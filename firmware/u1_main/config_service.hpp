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

/// Largest wire frame: header, maximum payload, CRC, COBS overhead, delimiter.
inline constexpr std::size_t kMaxWireFrame = 1100;

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
    void on_disconnect();

    /// Publish what the link is doing, for GET_DIAGNOSTICS to report.
    void set_link_state(const LinkState& state) { link_state_ = state; }

    const LinkState& link_state() const { return link_state_; }

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
