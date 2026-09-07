#pragma once

// The CDC side of U1: what the configurator talks to.
//
// One request in, one reply out, same sequence number. There is never more
// than one request in flight and the device never speaks first, except for
// capture events - so the whole thing is a function from a frame to a frame,
// plus the staging state a write transaction carries between them.
//
// Two rules shape everything here. A request whose sequence is not the next
// one is refused rather than acted on, because a configurator that lost a
// reply and a configurator that is confused look identical from this side.
// And a request byte-identical to the last one gets the last reply again,
// because that is what a lost reply looks like and re-erasing a slot for it
// would be worse than useless.

#include <cstddef>
#include <cstdint>

#include "diagnostics/latency.hpp"
#include "mapping/capture.hpp"
#include "protocol/frame.hpp"
#include "protocol/generated.hpp"
#include "runtime/output_command.hpp"
#include "storage/ab_store.hpp"

namespace duo_input::u1 {

/// What the host last asked the capture to do.
///
/// The capture itself runs on Core 1. This class only records what was asked
/// for and the main loop carries it across, because a USB callback writing the
/// other core's state is how a keyboard ends up swallowing keys nobody meant
/// it to.
enum class CaptureRequest : std::uint8_t {
    None,
    Begin,
    Cancel,
};

/// Errors the protocol defines.
///
/// The enum itself is generated from `protocol/schema.json`, which is the one
/// place a protocol identifier is allowed to exist; this alias only saves the
/// call sites here from spelling the namespace out. A firmware that named its
/// own numbers would drift from the host the moment either side edited one.
using CdcError = protocol::CdcError;

/// Counters the host can ask for.
struct CdcDiagnostics {
    std::uint32_t bad_crc = 0;
    std::uint32_t disconnect = 0;
    std::uint32_t timeout = 0;
    std::uint32_t bad_sequence = 0;
    std::uint32_t aborted_staging = 0;
    /// How many times the host has asked for everything to be let go of.
    ///
    /// GET_STATUS carries this, not aborted_staging: a host watching the
    /// safety command needs to know it landed, and the number of abandoned
    /// writes is a different question that GET_DIAGNOSTICS already answers.
    std::uint32_t release_all_count = 0;
};

/// Where a reply goes. The USB service supplies one.
class CdcSink {
public:
    virtual ~CdcSink() = default;
    virtual void write(const std::uint8_t* data, std::size_t size) = 0;
};

/// Makes a newly committed flash slot safe for the realtime runtime.
///
/// The A/B store may erase the old slot on the next write, so acknowledging a
/// commit before Core 1 has stopped reading that slot creates a dangling
/// pointer.  The hardware implementation performs the Core 0/Core 1 handoff;
/// tests provide an immediate recorder.
class IRuntimeConfig {
public:
    virtual ~IRuntimeConfig() = default;
    virtual bool activate(protocol::ByteView package) = 0;
    /// Stop using every flash-backed view before a factory reset erases both.
    virtual bool clear() = 0;
};

/// One of U1's own USB ports, as the host has to see it.
///
/// A peripheral plugged in here is invisible to both computers - it is on U1's
/// bus, not theirs - so this reply is the only place a compatibility matrix can
/// learn what device a row is about. "Keyboard 3" is not something anyone can
/// act on six months later; 046D:C31C with a descriptor hash is.
struct PeripheralPort {
    /// Something is on the port. Not the same as usable.
    bool attached = false;
    /// It was configured and its reports are being read.
    bool ready = false;
    /// What enumeration made of it: 0 unknown, 1 keyboard, 2 mouse. The same
    /// numbering as ch375::DeviceKind, which is where the value comes from.
    std::uint8_t kind = 0;
    std::uint16_t vendor_id = 0;
    std::uint16_t product_id = 0;
    /// How many buttons this mouse declared. Zero for a keyboard, and for a
    /// mouse that gave up no report descriptor to declare them in.
    std::uint8_t buttons = 0;
    /// How many bytes of report descriptor were read. Zero means none was.
    std::uint16_t report_descriptor_bytes = 0;
    /// SHA-256 of those bytes, or zeros when there were none.
    std::uint8_t descriptor_hash[32] = {};
};

/// Largest wire frame: header, maximum payload, CRC, COBS overhead, delimiter.
inline constexpr std::size_t kMaxWireFrame = 1100;

/// One port on the wire: attached, ready, kind, VID, PID, buttons, descriptor
/// length and its hash.
inline constexpr std::size_t kPeripheralPortBytes = 1 + 1 + 1 + 2 + 2 + 1 + 2 + 32;

/// How long a GET_DIAGNOSTICS reply is on the release build.
///
/// One error byte, five counters, the link state, the endpoint report, the
/// dropped-command count, the runtime fault - and then the latency block, which
/// carries its own bucket edges so that a host can never disagree with the
/// device about what a bucket means.
///
/// Everything is appended and nothing is ever moved, so a host that stops
/// reading at any earlier boundary still reads what it always read.
inline constexpr std::size_t kPeripheralBlockOffset =
    43 + 1 + 4 * (diagnostics::kLatencyBucketCount - 1) +
    2 * (8 + 4 * diagnostics::kLatencyBucketCount);

/// Which host stack read those ports, and what it counted while doing it.
///
/// U1's two input channels can be read by the CH375 pair or by the single
/// PIO USB host, and a diagnostic that does not say which one produced it
/// cannot be acted on months later - the two fail in entirely different ways.
///
/// Every counter here is a reason input did not arrive and has no other
/// outward sign. The order is the wire order and it is APPEND ONLY: a new
/// counter goes on the end, where a configurator that stops reading earlier
/// still reads what it always read. Reordering these would silently
/// re-label every reading an existing host takes.
struct BackendCounters {
    /// Every interface that ended up with no logical role, for any reason.
    std::uint32_t ignored_interfaces = 0;
    /// How many of those only because the role they wanted was already held.
    /// V1 accepts exactly one logical keyboard and one logical mouse, so a
    /// second keyboard lands here - which on a bench is a spare device, not
    /// a broken one. The remainder (ignored_interfaces minus this) is
    /// "nothing here could classify it", which is the broken one.
    std::uint32_t ignored_role_already_claimed = 0;
    std::uint32_t event_overflows = 0;
    std::uint32_t detach_overflows = 0;
    std::uint32_t stale_events_discarded = 0;
    std::uint32_t arm_failures = 0;
    std::uint32_t arm_escalations = 0;
    std::uint32_t stall_signals = 0;
    std::uint32_t duplicate_mounts = 0;
    std::uint32_t device_overflows = 0;
    std::uint32_t interface_overflows = 0;
    std::uint32_t callback_overflows = 0;
};

/// How many counters BackendCounters holds, and therefore how many the wire
/// carries. Tied to the struct rather than written down twice.
inline constexpr std::size_t kBackendCounterCount = 12;
static_assert(sizeof(BackendCounters) == 4 * kBackendCounterCount,
              "every BackendCounters field is one u32 on the wire, and the "
              "count above says how many - add a field, raise the count");

/// The appended block: the backend identifier, how many counters follow, and
/// then that many u32s.
///
/// The count byte is what lets a backend publish none of them. CH375 keeps no
/// host-stack counters, and sending twelve zeros for it would put twelve
/// readings in a report that nothing ever measured; it sends a count of zero
/// instead, and the host reports them as unknown rather than as zero.
inline constexpr std::size_t kBackendBlockBytes = 2 + 4 * kBackendCounterCount;

/// Where the appended backend block starts.
///
/// Everything before this offset is exactly what it was before the block
/// existed - same fields, same order, same numbering - so a configurator that
/// stops reading here reads what it always read.
inline constexpr std::size_t kBackendBlockOffset =
    kPeripheralBlockOffset + 2 * kPeripheralPortBytes;

/// What the host stack and its raw root port are doing, below every counter.
///
/// The twelve counters above are all above TinyUSB's device model: they are
/// reasons a device that enumerated was not read. None of them says anything
/// when nothing enumerates, and none distinguishes a host that never started
/// from a host that started and saw an empty bus - or from a Core 1 that
/// stopped before it could count anything. Task 14 cost a whole bench session
/// to that ambiguity. These fields resolve it, and they are the only fields in
/// this reply read from below the registry.
///
/// Fixed width, and append only for the same reason everything above is.
struct HostObservation {
    /// tuh_rhport_is_active(1) before Core 1 touched anything (bit 0), then
    /// tuh_configure's result (bit 1), tuh_init's result (bit 2) and
    /// tuh_inited() after both (bit 3). Bit 0 set means something initialised
    /// the host before Core 1 reached it, which makes bits 1-3 meaningless as
    /// evidence - they report success for calls that did nothing.
    std::uint8_t init_flags = 0;
    /// clk_sys when Core 1 began. The PIO build's main() has already selected
    /// and settled 120 MHz before any peripheral or Core 1 starts, so a
    /// correct reordered image reads 120 MHz here. The member's historical
    /// name is retained because the append-only wire field cannot be renamed.
    std::uint32_t clk_hz_at_begin = 0;
    /// clk_sys on Core 0. This is the divider clock whenever bit 0 of
    /// init_flags is clear, because Pico-PIO-USB computes every divider once
    /// during Core 1's later host bring-up.
    std::uint32_t clk_hz_now = 0;
    /// The root port's free-running SOF count. Zero and static means the bus
    /// is not being driven at all; climbing with every counter above still at
    /// zero means it is being driven and nothing on it answers.
    std::uint32_t sof_frame_count = 0;
    /// initialized (bit 0), connected (bit 1), suspended (bit 2) and
    /// is_fullspeed (bit 3), read straight off the root port.
    std::uint8_t root_port_state = 0;
    /// Disconnected-to-connected transitions since boot: whether U1 ever saw
    /// anything pull D+ up, independently of whether it could talk to it.
    ///
    /// A LOWER BOUND. Nothing below TinyUSB reports an attach edge, so Core 1
    /// polls the level once a pass; an attach and detach that both fall
    /// between two passes is not counted. Zero is strong evidence that nothing
    /// ever attached, not proof of it.
    std::uint16_t root_port_connects = 0;
    /// Passes of Core 1's loop. Unchanged across two reads twenty seconds
    /// apart is this project's established proof that Core 1 stopped.
    std::uint32_t core1_passes = 0;
    std::uint16_t mount_events = 0;
    std::uint16_t umount_events = 0;
    std::uint16_t hid_mount_events = 0;
    std::uint8_t ep_slots_opened = 0;
    std::uint8_t ep_max_failed_count = 0;
    std::uint32_t max_pass_gap_us = 0;
    std::uint16_t max_sof_gap = 0;
    std::uint16_t root_port_resets = 0;
    /// Configured-hub transitions, polled because TinyUSB excludes hubs from
    /// its application mount callback. A saturating lower bound.
    std::uint16_t hub_mount_events = 0;

    // The six readings from inside the window where enumeration stops. Every
    // field above says whether the host started and whether anything attached;
    // by the time a board reaches this window both are yes.

    /// Whose endpoint sits in each of the first four host endpoint-pool slots,
    /// one byte per slot, slot 0 in the low byte: device address in bits 7-5,
    /// an OPEN bit in bit 4, direction (1 = IN) in bit 3 and endpoint number
    /// in bits 2-0. A byte of zero means the slot is closed.
    ///
    /// The open bit is load-bearing. The endpoint this field exists to find is
    /// address 0's control endpoint, whose address, direction and number are
    /// all zero; without that bit it would encode as zero and read as an empty
    /// slot. LIVE, not a high-water mark - ep_slots_opened above is the
    /// high-water count, and the pair says both how far enumeration ever got
    /// and where it stands now.
    std::uint32_t ep_slot_map = 0;
    /// Every event U1's host stack has queued since boot: accepted attaches in
    /// bits 0-7, removals in bits 8-15, completed transfers in bits 16-31,
    /// each saturating. Two attaches means a device behind the hub was seen as
    /// well as the hub itself. An event the host stack's own queue dropped is
    /// not counted here, by construction.
    std::uint32_t host_event_counts = 0;
    /// How far each device address got, sticky: for address a in 1..5, bit
    /// (a-1) says it reached the configured state and bit 8+(a-1) says its
    /// device descriptor was read. Addresses 1-4 are devices; 5 is the hub.
    /// All-zero for an address nothing was ever plugged into is NORMAL.
    std::uint32_t enum_progress_mask = 0;
    /// Input-core passes that blocked for more than 20 ms, saturating.
    ///
    /// NOT a fault reading. A healthy board produces several: the host stack
    /// blocks for 50+450 ms enumerating the root port and another 450 ms for
    /// each device behind a hub. Zero would mean no enumeration was ever
    /// attempted.
    std::uint32_t long_pass_count = 0;
    /// The total of those blocked passes in whole milliseconds, saturating.
    /// Roughly 500 ms per root enumeration and 450 ms per hub-side one, so a
    /// number near a second beside a count of two is what a board that started
    /// enumerating one device behind a hub is expected to show.
    std::uint32_t long_pass_total_ms = 0;
    /// The lowest stack pointer the input core was ever seen at while the host
    /// stack was queueing an event. ZERO MEANS NO SAMPLE - no host event has
    /// ever been queued - and is not a stack that reached address zero.
    std::uint32_t core1_min_sp = 0;
    /// Live Pico-PIO-USB transfer flags for pool slots 0-3. See the host
    /// backend's kEpXfer* constants; zero bytes are closed slots.
    std::uint32_t ep_transfer_flags = 0;
    /// Transfer-completion total captured when the latest attach was queued.
    /// Current host_event_counts completions minus this is the post-attach
    /// control-stage count (until the 16-bit total saturates).
    std::uint32_t xfer_completions_at_attach = 0;
    /// Address-0 duplicate-attach recovery requests submitted since boot,
    /// saturating. Zero is not a health verdict and this is not a successful-
    /// restart count.
    std::uint32_t enum_stall_recoveries = 0;
};

/// The observation's own bytes on the wire, without its leading length.
inline constexpr std::size_t kHostObservationBytes =
    1 + 4 + 4 + 4 + 1 + 2 + 4 + 2 + 2 + 2 + 1 + 1 + 4 + 2 + 2 + 2 + 4 + 4 + 4 +
    4 + 4 + 4 + 4 + 4 + 4;

/// The base reading alone, without its leading length: whether the host
/// started, on which clock, the free-running frame counter, the root port's
/// own four bits, and how many passes Core 1 has made. Everything behind it
/// is below TinyUSB's endpoint pool and the enumeration-progress bookkeeping
/// firmware/u1_main/pio_usb/device_registry.hpp owns - a build that reads
/// pio_usb's own root port and frame counter directly, and nothing else,
/// declares this many bytes rather than the full kHostObservationBytes, so a
/// configurator reads exactly the fields that build measured and nothing it
/// invented for the rest. Matches
/// configurator/src/duo_input/device/transactions.py's own
/// ``_HOST_OBSERVATION_BASE`` struct size.
inline constexpr std::size_t kHostObservationBaseBytes = 1 + 4 + 4 + 4 + 1 + 2 + 4;

/// The appended host block: one length byte, then that many bytes.
///
/// The length byte is what lets a build publish none of this. It is what the
/// backend block's count byte is for the counters, for the same reason: the
/// CH375 image has no host stack at all, and seven zeros from it would be
/// seven readings of something that does not exist. It is also what lets a
/// host that does not recognise a longer block still find its end.
inline constexpr std::size_t kHostBlockBytes = 1 + kHostObservationBytes;

/// The reference target's own diagnostics: the bounded callback queue's
/// overflow count, how many interfaces on U1's own bus earned no logical
/// role, and whether each of the two roles currently has an owner ready to
/// route.
///
/// Appended after every block above it, for the same append-only reason as
/// the backend and host blocks: a configurator that stops reading at the end
/// of the host block still reads exactly what it always read.
struct ReferenceCounters {
    std::uint32_t callback_overflows = 0;
    std::uint32_t ignored_interfaces = 0;
    bool keyboard_ready = false;
    bool mouse_ready = false;
};

/// The reference-counters block's own field bytes on the wire, without its
/// leading length: two u32 counters and two single-byte flags.
inline constexpr std::size_t kReferenceCounterFieldBytes = 4 + 4 + 1 + 1;

/// The appended reference-counters block: one length byte, then that many
/// bytes.
///
/// The length byte is what lets a build publish none of this - the same
/// reason the host block's own length byte exists, for the same failure this
/// would otherwise reproduce: CH375 and PIO_USB link this exact
/// ConfigService and never call set_reference_counters, and without a
/// presence marker their ten zero bytes would read as ten real measurements
/// on a board that never took them. A length of zero means "this image links
/// ConfigService and has not published these counters" - true of every
/// backend except the reference target - and is reported as
/// ReferenceCounters::state == "none" on the configurator side, the same way
/// CH375's empty host block reports HostObservation::state == "none".
inline constexpr std::size_t kReferenceCounterBlockBytes = 1 + kReferenceCounterFieldBytes;

/// The longest a GET_DIAGNOSTICS reply can be: a backend publishing every
/// counter, a host block with every field, and a reference-counters block
/// with its own fields. A backend publishing none sends 4 * kBackendCounterCount
/// fewer bytes, an image with no host stack sends kHostObservationBytes
/// fewer, and a build that never calls set_reference_counters sends
/// kReferenceCounterFieldBytes fewer, so this is a ceiling and not a length.
inline constexpr std::size_t kDiagnosticsPayloadSize =
    kBackendBlockOffset + kBackendBlockBytes + kHostBlockBytes +
    kReferenceCounterBlockBytes;

static_assert(kDiagnosticsPayloadSize <= protocol::ProtocolLimits::CDC_MAX_PAYLOAD,
              "the diagnostics reply has to fit in one frame");

/// What the link to U2 is doing, as the host needs to see it.
///
/// The counters beside this one all count failures, and a link that never
/// started produces none of them - it was possible for every reading this
/// device offered to be zero while the second board was not there at all.
/// These say what is happening rather than what went wrong.
struct LinkState {
    /// U2 replied to the last frame with something only U2 could have sent.
    bool answered = false;
    /// U2 says its own USB is up.
    bool mounted = false;
    std::uint32_t frames_sent = 0;
    std::uint32_t crc_errors = 0;
    /// Frames U1 received that only U1 could have sent - see spi_master.hpp.
    std::uint32_t echoed_frames = 0;
    /// How many times U2 has released everything because U1 went quiet.
    std::uint8_t endpoint_drops = 0;
    /// The silence that caused U2's most recent release, in milliseconds.
    std::uint16_t endpoint_release_ms = 0;
};

class ConfigService {
public:
    ConfigService(storage::AbStore& store, CdcSink& sink, IRuntimeConfig& runtime)
        : store_(store), sink_(sink), runtime_(runtime) {}

    /// Feed bytes as they arrive from the CDC endpoint.
    ///
    /// Frames are delimited by a zero byte, so partial reads are normal and
    /// the leftover is kept until the rest turns up.
    void on_cdc_bytes(const std::uint8_t* data, std::size_t size);

    /// Forget any partial frame and any write in progress.
    ///
    /// Called when the host goes away. An abandoned staging slot has no header
    /// so it is already nothing, but the counter should say it happened.
    /// Also the moment conversation_active() below goes false again - see its
    /// own comment for why this is the one signal that means "gone" here.
    void on_disconnect();

    /// Whether a byte-clean CDC frame has actually been decoded since boot or
    /// since the last on_disconnect().
    ///
    /// A target that shares this CDC endpoint with a plain-text trace (the
    /// reference target does; see its main.cpp) must stop writing that trace
    /// the moment this is true: a COBS decoder finds its frame boundary at
    /// the next zero byte regardless of what is between them, so a single
    /// trace line landing inside a session turns the next reply into
    /// "malformed COBS frame" on the configurator's side - confirmed on real
    /// hardware, not by inference. True from the first frame that survives
    /// decode_cdc_frame's CRC check, which is the earliest point a stray
    /// noise byte can be told apart from a real client.
    ///
    /// Stays true until on_disconnect(), because nothing else this device can
    /// see is a reliable "the configurator process closed its port": DTR is
    /// not it - see CdcWriter's own comment in u1_main and u1_reference's
    /// main.cpp for why - so the USB mount transition on_disconnect() is
    /// already keyed to is the only "gone" this board can actually measure.
    bool conversation_active() const { return conversation_active_; }

    /// Publish what the link is doing, for GET_DIAGNOSTICS to report.
    void set_link_state(const LinkState& state) { link_state_ = state; }

    const LinkState& link_state() const { return link_state_; }

    /// Publish how long the device itself has been taking.
    ///
    /// Owned by the output runtime and copied here each pass, for the same
    /// reason the link state is: this class must not reach into Core 0's
    /// output state, and the output runtime must not know what a CDC frame is.
    ///
    /// What these count is the interval inside U1 - a peripheral report
    /// reaching Core 1, against the command it produced being applied on Core
    /// 0. Not a keystroke's journey from a finger to a far screen: the device
    /// cannot see either end of that, and nothing that reads this may present
    /// it as if it could.
    void set_input_latency(const diagnostics::LatencyHistogram& keyboard,
                           const diagnostics::LatencyHistogram& mouse) {
        keyboard_latency_ = keyboard;
        mouse_latency_ = mouse;
    }

    /// Publish what is on the two peripheral ports.
    ///
    /// Gathered by the main loop from enumeration, for the same reason as
    /// everything else here: this class must not reach across to Core 1's
    /// controllers, and the controllers must not know what a CDC frame is.
    void set_peripherals(const PeripheralPort& keyboard, const PeripheralPort& mouse) {
        keyboard_port_ = keyboard;
        mouse_port_ = mouse;
    }

    /// Publish which backend read those ports, and its own counters.
    ///
    /// Two overloads rather than a defaulted argument, because "publishes no
    /// counters" and "publishes twelve zeros" are different claims and the
    /// call site has to make one of them on purpose. CH375 uses the first.
    void set_backend(protocol::InputBackend backend) {
        backend_ = backend;
        backend_counters_ = {};
        backend_publishes_counters_ = false;
    }
    void set_backend(protocol::InputBackend backend, const BackendCounters& counters) {
        backend_ = backend;
        backend_counters_ = counters;
        backend_publishes_counters_ = true;
    }

    /// Publish what the host stack and its root port are doing.
    ///
    /// Three shapes for three different claims about the device, and the call
    /// site has to make one of them on purpose:
    ///
    /// - No block at all: this image has no host stack to observe. The CH375
    ///   image uses this - it has no host stack, no root port and no Core 1
    ///   backend loop, so every field would be an invented reading.
    /// - The full block: every field this struct carries is a real reading.
    ///   The shipping PIO USB backend uses this - its DeviceRegistry keeps
    ///   the endpoint-pool and enumeration-progress counters the extension
    ///   fields report.
    /// - The base block only (set_host_observation_base): a build that reads
    ///   pio_usb's own root port and frame counter directly and has none of
    ///   that further instrumentation. Declaring kHostObservationBaseBytes
    ///   rather than the full width is what keeps the sixteen fields it never
    ///   measured out of the reply, rather than sixteen invented zeros behind
    ///   the seven it actually knows.
    void set_host_observation() {
        host_observation_ = {};
        host_publishes_observation_ = false;
        host_observation_base_only_ = false;
    }
    void set_host_observation(const HostObservation& observation) {
        host_observation_ = observation;
        host_publishes_observation_ = true;
        host_observation_base_only_ = false;
    }
    void set_host_observation_base(const HostObservation& observation) {
        host_observation_ = observation;
        host_publishes_observation_ = true;
        host_observation_base_only_ = true;
    }

    /// Publish the reference target's own counters: the callback queue's
    /// overflow count, how many interfaces earned no role, and whether each
    /// of the two roles currently has an owner.
    ///
    /// Only the reference target calls this. CH375 and PIO_USB never do, and
    /// their reply must say so rather than send zeros for counters they never
    /// measured - the same reason set_backend and set_host_observation each
    /// have a "publishes none" shape.
    void set_reference_counters(const ReferenceCounters& counters) {
        reference_counters_ = counters;
        reference_publishes_counters_ = true;
    }

    /// Which profile the device is running.
    ///
    /// Set by the main loop when Core 1 confirms a swap, not when the host
    /// asks for one. Reporting a profile before the bindings behind it changed
    /// would have the configurator show one thing while the keyboard does
    /// another for as long as the swap takes.
    std::uint8_t active_profile() const { return active_profile_; }
    /// Startup publication, before Core 1 is launched.
    void set_initial_active_profile(std::uint8_t profile) { active_profile_ = profile; }
    /// Accept an acknowledgement only for the outstanding host request.
    bool confirm_profile_applied(std::uint8_t profile);
    /// Publish a profile selected by a binding or macro on Core 1.
    void publish_local_profile(std::uint8_t profile) { active_profile_ = profile; }

    /// How many commands Core 1's queue refused, as Core 1 last reported it.
    ///
    /// Published by the bridge each pass, for the same reason the link state
    /// is: the counter lives on the other core and this class must not reach
    /// across for it. Nonzero means a keypress, a release or a macro step
    /// never reached the far computer - what is held there no longer matches
    /// what the operator did - so it is reported rather than merely counted.
    void set_dropped_commands(std::uint32_t dropped) { dropped_commands_ = dropped; }
    std::uint32_t dropped_commands() const { return dropped_commands_; }

    /// What Core 0's output runtime last concluded about its queue.
    ///
    /// Published by the main loop for the same reason as the counter above.
    /// The runtime clears the fault itself once the burst that caused it has
    /// passed, so a host that sees this set is looking at a device that is
    /// dropping input right now, not at a latch left over from boot.
    void set_runtime_fault(runtime::RuntimeFault fault) { runtime_fault_ = fault; }
    runtime::RuntimeFault runtime_fault() const { return runtime_fault_; }

    /// Whether a capture is running, as Core 1 last reported it.
    ///
    /// Published by the main loop rather than owned here, for the same reason
    /// the link state is: a capture ends on its own after ten seconds and this
    /// class would otherwise go on telling the host one is running.
    bool capture_active() const { return capture_active_; }
    void set_capture_active(bool active) { capture_active_ = active; }

    const CdcDiagnostics& diagnostics() const { return diagnostics_; }

#if DUO_SPI_DEBUG || DUO_CH375_PROBE
    /// Bytes reported in place of the ordinary diagnostics, for bring-up only.
    ///
    /// This replaces a frozen protocol reply with something the configurator
    /// cannot parse, which is why it exists only behind a build flag and never
    /// ships. It is here because the alternative was guessing at a silent
    /// four-wire link.
    void set_link_debug(const std::uint8_t* bytes, std::size_t size);
#endif

    /// Record that someone at the device held SW2 for five seconds.
    ///
    /// The confirmation authorises exactly one factory reset and is spent by
    /// it. A standing confirmation would let a program erase the operator's
    /// work repeatedly on the strength of one button press, and a new session
    /// forgets it entirely: the person who pressed the button and the program
    /// now connected are not necessarily the same person.
    void confirm_factory_reset() { factory_confirmed_ = true; }

    /// Set when the host asked for everything to be released.
    ///
    /// Read and cleared by the main loop: this class must not reach into the
    /// output state, which belongs to Core 0's runtime.
    bool take_release_all_request();

    /// What the host last asked of the capture, taken once.
    ///
    /// Read twice, a Begin would start a second capture nobody asked for and
    /// the operator's next keystroke would vanish into it.
    CaptureRequest take_capture_request();

    /// The profile the host asked for, if it asked. Taken once.
    bool take_profile_request(std::uint8_t& profile);

    /// Send the trigger the operator pressed, unprompted.
    ///
    /// The host is waiting on this and nothing else, so it goes out on the
    /// sequence the next request would have used and moves the count on - a
    /// configurator that carried on from its own last request would be refused
    /// from here to the end of the session. The capture ends with it: one
    /// question, one answer.
    ///
    /// Ignored when no capture is running. Speaking out of turn costs the host
    /// its sequence over a trigger it never asked for.
    void emit_capture_event(const mapping::CapturedTrigger& trigger);

private:
    void handle_frame(const std::uint8_t* wire, std::size_t size);
    void dispatch(const protocol::CdcFrame& frame);
    void reply(protocol::CdcMessageType type, std::uint16_t sequence,
               const std::uint8_t* payload, std::size_t size);
    void reply_error(const protocol::CdcFrame& frame, CdcError error);

    std::size_t device_info_payload(CdcError error, std::uint32_t capabilities,
                                    std::uint8_t* out) const;
    std::size_t status_payload(CdcError error, std::uint8_t* out) const;
    std::size_t config_info_payload(CdcError error, std::uint8_t* out);
    std::size_t diagnostics_payload(CdcError error, std::uint8_t* out) const;
    /// The appended host block, written at ``out``. Returns its length, which
    /// is one byte when this image publishes no observation.
    std::size_t write_host_observation(std::uint8_t* out) const;
    /// The appended reference-counters block, written at ``out``. Returns
    /// its length, which is one byte when this image never published
    /// reference counters.
    std::size_t write_reference_counters(std::uint8_t* out) const;

    storage::AbStore& store_;
    CdcSink& sink_;
    IRuntimeConfig& runtime_;

    // The working buffers, kept here rather than on the stack.
    //
    // Core 0's stack region is two kilobytes, and the region immediately below
    // it is Core 1's live stack rather than a guard page. A rejected or
    // malformed frame walks handle_frame -> dispatch -> reply_error -> reply,
    // and each of those used to declare a payload-sized array of its own: the
    // four together measured 4824 bytes of live frame on the release image and
    // 5400 on the probe image, both of which reach past the bottom of Core 0's
    // region into Core 1's. reply_error is on the path of every malformed or
    // refused CDC frame, so that was not an exotic path.
    //
    // There is exactly one ConfigService, exactly one core calling into it,
    // and none of these calls re-enter, so a member is as good as a local and
    // costs the stack nothing.
    //
    // Four separate buffers rather than one shared scratch, deliberately:
    // handle_frame's decoded payload is what frame.payload points at for the
    // whole of dispatch, and reply's encoder reads the payload its caller has
    // just finished filling in.
    std::uint8_t decoded_[protocol::ProtocolLimits::CDC_MAX_PAYLOAD] = {};
    std::uint8_t dispatch_payload_[protocol::ProtocolLimits::CDC_MAX_PAYLOAD] = {};
    std::uint8_t error_payload_[protocol::ProtocolLimits::CDC_MAX_PAYLOAD] = {};
    std::uint8_t encode_scratch_[protocol::ProtocolLimits::CDC_MAX_PAYLOAD + 32] = {};

    // Assembly of an incoming frame.
    std::uint8_t pending_[kMaxWireFrame] = {};
    std::size_t pending_size_ = 0;

    /// See conversation_active() above.
    bool conversation_active_ = false;

    // The last exchange, kept so a repeated request gets a repeated reply.
    std::uint8_t last_request_[kMaxWireFrame] = {};
    std::size_t last_request_size_ = 0;
    std::uint8_t last_response_[kMaxWireFrame] = {};
    std::size_t last_response_size_ = 0;
    std::uint16_t last_sequence_ = 0;
    bool have_sequence_ = false;

    std::uint32_t negotiated_capabilities_ = 0;
    bool negotiated_ = false;

    /// Where the next chunk of a write must start. The protocol requires
    /// strictly ascending chunks so both ends agree on what has arrived.
    std::uint32_t expected_offset_ = 0;

    std::uint8_t active_profile_ = 1;
    std::uint32_t dropped_commands_ = 0;
    runtime::RuntimeFault runtime_fault_ = runtime::RuntimeFault::None;
    bool capture_active_ = false;
    CaptureRequest capture_request_ = CaptureRequest::None;
    std::uint8_t requested_profile_ = 0;
    bool profile_requested_ = false;
    std::uint8_t pending_profile_ = 0;
    bool profile_confirmation_pending_ = false;
    bool release_all_requested_ = false;
    bool factory_confirmed_ = false;
    bool factory_armed_ = false;

    CdcDiagnostics diagnostics_{};
    LinkState link_state_{};
    diagnostics::LatencyHistogram keyboard_latency_{};
    diagnostics::LatencyHistogram mouse_latency_{};
    PeripheralPort keyboard_port_{};
    PeripheralPort mouse_port_{};
    /// Unknown until the main loop says otherwise. Defaulting this to CH375
    /// would have a PIO USB build report the wrong backend for as long as it
    /// took the first pass to run, and a wrong answer here is worse than none.
    protocol::InputBackend backend_ = protocol::InputBackend::UNKNOWN;
    BackendCounters backend_counters_{};
    bool backend_publishes_counters_ = false;
    HostObservation host_observation_{};
    /// False until the main loop publishes one. An image with no host stack
    /// never does, and the block then carries a length of zero rather than
    /// seven zeroed readings of hardware it does not have.
    bool host_publishes_observation_ = false;
    /// True only after set_host_observation_base: the block declares
    /// kHostObservationBaseBytes instead of the full width, so the fields
    /// this build never measured are left off the wire rather than sent as
    /// invented zeros. Meaningless while host_publishes_observation_ is
    /// false.
    bool host_observation_base_only_ = false;
    ReferenceCounters reference_counters_{};
    /// False until the main loop publishes some. CH375 and PIO_USB never do,
    /// and the block then carries a length of zero rather than four readings
    /// of a bounded callback queue neither of them has.
    bool reference_publishes_counters_ = false;

#if DUO_SPI_DEBUG || DUO_CH375_PROBE
    // One byte short of what a CDC reply can carry, because the payload leads
    // with an error code (diagnostics_payload). The probe build's report is
    // two devices' worth of text and 900 was not enough for both once the
    // report-descriptor line joined it - and a report that runs out of room
    // stops mid-device, which is a diagnostic that lies by omission.
    std::uint8_t link_debug_[protocol::ProtocolLimits::CDC_MAX_PAYLOAD - 1] = {};
    std::size_t link_debug_size_ = 0;
#endif
};

}  // namespace duo_input::u1
