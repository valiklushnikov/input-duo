#pragma once

// U1's native USB host: one XL334P4 hub on RHPort 1, replacing both CH375
// channels. TinyUSB's device stack keeps RHPort 0 exactly as it always has -
// see tusb_config.h - and this never touches tud_task(), the CDC link to the
// configurator, or anything USB-descriptor-shaped on Core 0's side.
//
// main() sets clk_sys to 120 MHz before any peripheral or Core 1 starts, in
// the same order as Pico-PIO-USB's reference host. begin() still publishes a
// one-way readiness barrier when the host is initialized. The Core 0 link
// keeps using that barrier and refreshes its baud once: normally harmless,
// because SpiMaster::begin() now ran against the final clock, and still safe
// if startup ordering changes again later.
//
// This class is Core 1's only door into the host stack, the same shape
// Ch375SourceAdapter is Core 1's only door into a CH375 channel: begin()
// once, task() every pass, take_event() drained in a loop until it returns
// false. TinyUSB's own host callbacks - tinyusb_host_callbacks.cpp - run
// inside that task() call and copy bounded data into storage this class
// owns; nothing downstream ever sees a pointer into memory a callback still
// owns.
//
// The callback-facing registry is fixed-capacity. It assigns boot-protocol
// roles, owns one receive at a time per mounted HID interface, and surfaces
// Ready, Report, Detached and host/capture faults through take_event() in the
// shape Ch375SourceAdapter::convert() already produces from CH375's side.
//
// Recovery after a detach, a stall or a reconnect lives in that registry -
// bounded retry with backoff, generation counters, ordered teardown across a
// whole hub - but this file DRIVES it: task() hands the registry the pass's
// own clock reading and calls retry_pending_arms() every pass, right after
// process_pending(), so a backoff armed in one pass is retried in a later one
// without anything blocking in between. Nothing here decides what recovery
// means; it decides when the registry gets a chance to do it.

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <type_traits>

#include "input/source.hpp"
#include "pio_usb/device_registry.hpp"

namespace duo_input::u1::pio_usb {

/// What begin() found and what the raw root port is doing, below TinyUSB.
///
/// Every counter the registry keeps starts one level too high to answer the
/// question that cost a whole bench session: a host that never initialised and
/// a host that initialised and saw nothing produce the same twelve zeros, and
/// so does a Core 1 that stopped inside begin(). None of these fields go
/// through the registry, the callbacks or TinyUSB's device model. They are
/// read straight off the host stack's own flags and Pico-PIO-USB's root port,
/// which is the only layer that still says something when everything above it
/// is silent.
///
/// All fixed width, all copied by value into the CDC reply on Core 0.
struct HostObservability {
    /// The four kHostInit* bits below.
    std::uint8_t init_flags = 0;
    /// clk_sys as begin() found it. main() has already selected and settled
    /// the 120 MHz reference clock, so a correct image reads 120 MHz here.
    /// The wire field keeps its historical name for append-only compatibility.
    std::uint32_t clk_hz_at_begin = 0;
    /// clk_sys on Core 0, sampled once per main-loop pass.
    ///
    /// THIS is the divider clock when kHostInitHostAlreadyActive is clear:
    /// Pico-PIO-USB computes every divider once inside pio_usb_host_init and
    /// never recomputes one, and begin() calls tuh_init after its own clock
    /// change - an ordering
    /// tests/firmware_native/test_pio_usb_device_registry.cpp guards rather
    /// than leaves to this comment. When that bit is SET the host came up
    /// somewhere else, on a clock this reply never saw, and no clock reading
    /// here is evidence about the dividers.
    std::uint32_t clk_hz_now = 0;
    /// pio_usb_host_get_frame_number(): the free-running SOF count.
    ///
    /// Below TinyUSB and below the registry. Read twice a second apart it
    /// says whether U1 is driving the bus at all, and at what rate. Sampled
    /// on Core 0 rather than cached by a Core 1 pass, deliberately: a frozen
    /// core1_passes beside a climbing frame count is a Core 1 that died under
    /// a host that did not.
    std::uint32_t sof_frame_count = 0;
    /// The four kRootPort* bits below, sampled on Core 0.
    std::uint8_t root_port_state = 0;
    /// How many times the root port has gone from disconnected to connected.
    ///
    /// Whether U1 ever saw the hub's D+ pull-up, independently of whether any
    /// transaction on it ever succeeded. Saturates rather than wrapping: a
    /// count that rolled over to zero would read as "never attached".
    ///
    /// A LOWER BOUND, not a total. Nothing below TinyUSB reports an attach
    /// edge, so task() polls the level once a pass; an attach and detach that
    /// both fall between two passes is not counted. Zero here is therefore
    /// strong evidence that nothing attached and not proof of it.
    std::uint16_t root_port_connects = 0;
    /// Passes of Core 1's loop. This project's established proof that Core 1
    /// stopped is this counter unchanged across two reads twenty seconds
    /// apart, so it must be readable that way and must not saturate low.
    std::uint32_t core1_passes = 0;
    /// Application device-mount callbacks since boot, regardless of whether a
    /// callback registry was available to accept the metadata. TinyUSB omits
    /// hubs from these callbacks; hub_mount_events below covers that case.
    std::uint16_t mount_events = 0;
    std::uint16_t umount_events = 0;
    std::uint16_t hid_mount_events = 0;
    /// Largest number of non-empty Pico-PIO-USB endpoint slots seen in a pass.
    std::uint8_t ep_slots_opened = 0;
    /// Largest endpoint failed_count seen in the pool.
    std::uint8_t ep_max_failed_count = 0;
    /// Largest unsigned interval between consecutive task() timestamps.
    std::uint32_t max_pass_gap_us = 0;
    /// Largest SOF-frame-number jump between consecutive task() passes.
    std::uint16_t max_sof_gap = 0;
    /// Completed connected suspended->running cycles observed by polling.
    std::uint16_t root_port_resets = 0;
    /// Configured-hub transitions observed through tuh_mounted().
    ///
    /// A separate lower-bound counter is necessary because TinyUSB
    /// deliberately does not call tuh_mount_cb for hub addresses.
    std::uint16_t hub_mount_events = 0;

    // The eight fields below read inside the window where enumeration stops.
    // Everything above them says whether the host started and whether anything
    // attached; by the time a board reaches this window both are yes, and none
    // of the counters above can say which statement stopped it.

    /// Whose endpoint is in each of the first four Pico-PIO-USB pool slots,
    /// one byte per slot, slot 0 in the low byte. See the kEpSlot* constants.
    ///
    /// LIVE, not a high-water mark: it follows the pool down when an endpoint
    /// closes. ep_slots_opened above is the high-water count, so the pair says
    /// both how far enumeration ever got and where it is now.
    std::uint32_t ep_slot_map = 0;
    /// Every event the host stack has queued, packed: attach in bits 0-7,
    /// remove in bits 8-15, transfer-completion in bits 16-31, each saturating.
    ///
    /// Read through TinyUSB's weak tuh_event_hook_cb, which queue_event()
    /// calls for every event, so this counts what the host stack itself saw
    /// rather than what any callback above it was told. An event DROPPED by a
    /// full queue is not counted: queue_event calls the hook only after
    /// osal_queue_send succeeds.
    std::uint32_t host_event_counts = 0;
    /// How far each device address got, sticky. For address a in 1..5, bit
    /// (a-1) is tuh_mounted(a) and bit 8+(a-1) is tuh_vid_pid_get(a) - which
    /// is true only once the address is assigned AND the device descriptor has
    /// been read. Addresses 1-4 are devices; 5 is the hub.
    std::uint32_t enum_progress_mask = 0;
    /// Core 1 passes whose gap exceeded kLongPassThresholdUs, saturating.
    ///
    /// A healthy board produces some of these: TinyUSB's enumeration blocks
    /// inside tuh_task() for 50+450 ms on the root port and 450 ms behind a
    /// hub. Zero here would mean no enumeration was ever attempted.
    std::uint32_t long_pass_count = 0;
    /// The sum of those gaps in whole milliseconds, saturating. Together with
    /// long_pass_count this is the blocking budget measured rather than
    /// inferred from an assumed-constant pass rate.
    std::uint32_t long_pass_total_ms = 0;
    /// The lowest __get_MSP() any host event ever saw, or 0 if no host event
    /// has been queued yet. Core 1's stack runs from __StackOneBottom to
    /// __StackOneTop; a reading below the bottom is an overflow, and 0 is NOT
    /// one - it means the sample was never taken.
    std::uint32_t core1_min_sp = 0;
    /// Live transfer state for the same four pool slots as ep_slot_map, one
    /// byte per slot with slot 0 in the low byte. See kEpXfer* below.
    ///
    /// Unlike ep_slots_opened this is not sticky: an active bit clears as soon
    /// as Pico-PIO-USB completes the transfer. That is what separates an open
    /// but idle address-0 endpoint from a SETUP or later control stage that is
    /// still being retried.
    std::uint32_t ep_transfer_flags = 0;
    /// The saturated transfer-completion total when the latest attach event
    /// was queued. Subtract it from host_event_counts' current completion
    /// total to count stages completed after that attach without knowing how
    /// many hub-control transfers preceded it.
    std::uint32_t xfer_completions_at_attach = 0;
};

struct HostCallbackObservability {
    std::uint16_t mount_events = 0;
    std::uint16_t umount_events = 0;
    std::uint16_t hid_mount_events = 0;
    /// HostObservability::host_event_counts, already packed and saturated.
    std::uint32_t host_event_counts = 0;
    /// HostObservability::core1_min_sp; 0 means no host event has run the hook.
    std::uint32_t core1_min_sp = 0;
    /// HostObservability::xfer_completions_at_attach.
    std::uint32_t xfer_completions_at_attach = 0;
    /// The two halves of the attach total, before they are added.
    ///
    /// Exposed because the split is the correctness argument, not a detail:
    /// TinyUSB queues attach events from BOTH of Core 1's contexts - hub.c
    /// with in_isr false from inside tuh_task(), hcd_pio_usb.c with in_isr
    /// true from the SOF alarm - and RP2040 has no atomic read-modify-write,
    /// so one shared counter would silently lose an increment whenever the ISR
    /// landed inside the other context's load-modify-store. Losing one is the
    /// difference between one attach and two, which is the whole reading.
    std::uint32_t attach_events_from_isr = 0;
    std::uint32_t attach_events_from_task = 0;
};

/// Fixed-width callback counters. The reset happens once, before host init;
/// the accessor is read-only and used by Core 0's diagnostics snapshot.
void reset_host_callback_observability() noexcept;
HostCallbackObservability host_callback_observability() noexcept;

/// tuh_rhport_is_active(1) as begin() found it, before it changed anything.
///
/// The smoking gun. Set means something initialised the host stack before
/// Core 1 reached begin() - which is what usb_service.cpp's argument-less
/// tusb_init() used to do on Core 0, at the wrong clock, before
/// tuh_configure(). It must read clear on a correct image.
inline constexpr std::uint8_t kHostInitHostAlreadyActive = 1u << 0;
/// What tuh_configure() returned.
inline constexpr std::uint8_t kHostInitConfigured = 1u << 1;
/// What tuh_init() returned. True on its own proves nothing: tuh_init on an
/// already-active rhport returns true without doing anything, which is why
/// kHostInitHostAlreadyActive is read first and reported beside it.
inline constexpr std::uint8_t kHostInitInitialized = 1u << 2;
/// tuh_inited() after both calls.
inline constexpr std::uint8_t kHostInitInited = 1u << 3;

/// ep_slot_map's byte, per Pico-PIO-USB endpoint-pool slot.
///
/// The open bit is not decoration. The endpoint this field exists to find is
/// device address 0's control endpoint, opened by ENUM_ADDR0_DEVICE_DESC: its
/// address, its direction and its endpoint number are all zero, so without a
/// separate open bit it would encode to 0x00 and read as an empty slot - the
/// exact reading it has to contradict. Three address bits are enough because
/// the pinned configuration allots addresses 0 through CFG_TUH_DEVICE_MAX +
/// CFG_TUH_HUB, and backend.cpp static-asserts that this stays true.
inline constexpr std::uint8_t kEpSlotEndpointMask = 0x07u;
inline constexpr std::uint8_t kEpSlotDirectionIn = 1u << 3;
inline constexpr std::uint8_t kEpSlotOpen = 1u << 4;
inline constexpr int kEpSlotAddressShift = 5;
inline constexpr std::uint8_t kEpSlotAddressMask = 0x07u;
/// How many pool slots ep_slot_map has room for, one byte each.
inline constexpr std::size_t kEpSlotMapSlots = 4;

/// ep_transfer_flags' byte, per Pico-PIO-USB endpoint-pool slot.
///
/// Open is independent of active so address 0's all-zero endpoint identity
/// cannot disappear into the closed value. Direction and DATA/SETUP describe
/// a transfer only while kEpXferHasTransfer is set; PRE is an endpoint route
/// property, while stalled/aborted are the library's live flags.
inline constexpr std::uint8_t kEpXferOpen = 1u << 0;
inline constexpr std::uint8_t kEpXferHasTransfer = 1u << 1;
inline constexpr std::uint8_t kEpXferHostOut = 1u << 2;
inline constexpr std::uint8_t kEpXferData1 = 1u << 3;
inline constexpr std::uint8_t kEpXferNeedPre = 1u << 4;
inline constexpr std::uint8_t kEpXferSetupStaged = 1u << 5;
inline constexpr std::uint8_t kEpXferStalled = 1u << 6;
inline constexpr std::uint8_t kEpXferAborted = 1u << 7;

/// host_event_counts' three packed fields.
inline constexpr int kHostEventRemoveShift = 8;
inline constexpr int kHostEventXferShift = 16;
inline constexpr std::uint32_t kHostEventAttachLimit = 0xFFu;
inline constexpr std::uint32_t kHostEventRemoveLimit = 0xFFu;
inline constexpr std::uint32_t kHostEventXferLimit = 0xFFFFu;

/// enum_progress_mask: bit (a-1) is configured, bit 8+(a-1) is descriptor-read.
inline constexpr int kEnumDescriptorShift = 8;

/// What counts as a blocked Core 1 pass, in microseconds.
///
/// 20 ms is far above this loop's ordinary tens of microseconds and far below
/// every blocking wait TinyUSB's enumeration performs (the shortest is 50 ms),
/// so nothing ordinary is counted and nothing that matters is missed.
inline constexpr std::uint32_t kLongPassThresholdUs = 20000u;

/// PIO_USB_ROOT_PORT(0)->initialized.
inline constexpr std::uint8_t kRootPortInitialized = 1u << 0;
/// PIO_USB_ROOT_PORT(0)->connected.
inline constexpr std::uint8_t kRootPortConnected = 1u << 1;
/// PIO_USB_ROOT_PORT(0)->suspended.
inline constexpr std::uint8_t kRootPortSuspended = 1u << 2;
/// PIO_USB_ROOT_PORT(0)->is_fullspeed.
inline constexpr std::uint8_t kRootPortFullSpeed = 1u << 3;

/// One-way Core 1 -> Core 0 publication for host readiness on the settled clock.
class ClockChangeBarrier {
public:
    void publish_settled() noexcept {
        settled_.store(1u, std::memory_order_release);
    }

    bool settled() const noexcept {
        return settled_.load(std::memory_order_acquire) != 0;
    }

private:
    std::atomic<std::uint32_t> settled_{0};
};

/// Keeps Core 0 non-blocking while ordering baud restore before any transfer.
class LinkStartupGate {
public:
    template <typename RestoreBaud, typename Transfer>
    bool run_if_ready(bool clock_settled, RestoreBaud&& restore_baud, Transfer&& transfer) {
        if (!clock_settled) {
            return false;
        }
        if (!baud_restored_) {
            restore_baud();
            baud_restored_ = true;
        }
        transfer();
        return true;
    }

private:
    bool baud_restored_ = false;
};

class PioUsbBackend {
public:
    using PublishedObservationWord = std::atomic<std::uint32_t>;

    /// Every Core-1 diagnostic word is published through this lock-free word.
    /// RP2040 cannot afford a hidden library lock in the host service loop.
    static constexpr bool observation_publication_is_always_lock_free() noexcept {
        return PublishedObservationWord::is_always_lock_free &&
               std::is_same_v<decltype(host_init_flags_), PublishedObservationWord> &&
               std::is_same_v<decltype(clk_hz_at_begin_), PublishedObservationWord> &&
               std::is_same_v<decltype(root_port_connects_), PublishedObservationWord> &&
               std::is_same_v<decltype(root_port_resets_), PublishedObservationWord> &&
               std::is_same_v<decltype(hub_mount_events_), PublishedObservationWord> &&
               std::is_same_v<decltype(ep_slot_map_), PublishedObservationWord> &&
               std::is_same_v<decltype(ep_transfer_flags_), PublishedObservationWord> &&
               std::is_same_v<decltype(enum_progress_mask_), PublishedObservationWord> &&
               std::is_same_v<decltype(long_pass_count_), PublishedObservationWord> &&
               std::is_same_v<decltype(long_pass_total_ms_), PublishedObservationWord> &&
               std::is_same_v<decltype(ep_slots_opened_), PublishedObservationWord> &&
               std::is_same_v<decltype(ep_max_failed_count_), PublishedObservationWord> &&
               std::is_same_v<decltype(max_pass_gap_us_), PublishedObservationWord> &&
               std::is_same_v<decltype(max_sof_gap_), PublishedObservationWord> &&
               std::is_same_v<decltype(core1_passes_), PublishedObservationWord>;
    }

    /// Bring the host stack up. Call once, from Core 1, before task().
    ///
    /// Waits the reference sequence's second 10 ms settling interval, then
    /// configures Pico-PIO-USB's D+ pin as GP0 (D- is adjacent GP1) and calls
    /// tuh_init on RHPort 1. main() has already selected and settled 120 MHz
    /// before any peripheral started. clock_settled() reads true once this
    /// host bring-up returns.
    void begin();

    /// Whether begin() has finished bringing the host up on the settled clock.
    ///
    /// Core 0 must not run a real transfer over the SPI link to U2 before
    /// this is true; see the file comment above and
    /// SpiMaster::refresh_baudrate(). The refresh is normally a no-op now that
    /// SpiMaster::begin() sees the final clock; retaining it and the gate keeps
    /// future startup-order changes safe. The acquire pairs with begin()'s
    /// release publication.
    bool clock_settled() const;

    /// Service the host stack for one pass. Called every time round Core 1's
    /// loop, unconditionally, the same as Ch375Device::tick.
    void task(std::uint32_t now_us);

    /// Take one queued event out of backend-owned storage, in the shape
    /// Ch375SourceAdapter::convert() already produces from CH375's side.
    /// Returns false once nothing is queued; ``event`` and ``identity`` are
    /// unchanged when it does.
    bool take_event(input::SourceEvent& event, input::SourceIdentity& identity);

    /// Which of this board's two logical ports a resolved DeviceKind
    /// belongs on: 0 for the keyboard, 1 for the mouse. -1 for Unknown,
    /// which nothing routes - the same thing CH375's own per-channel
    /// adapters already do by construction.
    static int logical_port(input::DeviceKind kind);

    /// Read-only view of what the registry knows, for diagnostics.
    ///
    /// Const on purpose: the only caller is Core 0's main loop building a
    /// GET_DIAGNOSTICS reply, and it must be able to read what is mounted
    /// without being able to arm, fault or forget anything. The translation
    /// into the wire's PeripheralPort shape stays in main.cpp, exactly where
    /// the CH375 path already does it - the registry does not know what a CDC
    /// frame is and the CDC service does not know what a TinyUSB interface is.
    const DeviceRegistry& registry() const { return registry_; }

    /// What the host stack and the raw root port are doing right now.
    ///
    /// Called from Core 0's main loop, once a pass, for the same reason
    /// registry() is read there: the reply must be able to read this core's
    /// state without being able to change any of it. The result is handed to
    /// ConfigService and held until a GET_DIAGNOSTICS arrives, so a reading
    /// can be up to one Core 0 pass old - which is far below the second the
    /// "read it twice" procedures need, and does not affect either of them.
    ///
    /// The live halves (clk_sys, the SOF count, the root port's own flags) are
    /// sampled HERE, on Core 0, rather than cached by Core 1's task(), so they
    /// still answer when Core 1 has stopped - which is precisely the case they
    /// exist to tell apart.
    ///
    /// Bounded, non-allocating and non-blocking: four volatile root-port reads,
    /// one clock/frame register read apiece, and three relaxed atomic counter
    /// loads. It takes no lock, so a root-port flag can change under it;
    /// nothing here is a decision, only a reading.
    HostObservability observe() const;

private:
    ClockChangeBarrier clock_change_;
    DeviceRegistry registry_;
    bool host_ready_ = false;

    // Core 1 publishes these words and Core 0 samples them independently.
    // Relaxed atomics are sufficient for observations: no field controls host
    // behavior and no cross-field snapshot is claimed. Readiness has its own
    // release/acquire barrier above.
    PublishedObservationWord host_init_flags_{0};
    PublishedObservationWord clk_hz_at_begin_{0};

    // The attach edge has to be polled - nothing below TinyUSB reports one -
    // so task() samples it every pass. Both are Core 1's alone; Core 0 only
    // ever reads the counter.
    PublishedObservationWord root_port_connects_{0};
    bool root_port_was_connected_ = false;
    PublishedObservationWord root_port_resets_{0};
    bool root_port_reset_in_progress_ = false;
    PublishedObservationWord hub_mount_events_{0};
    bool hub_was_mounted_ = false;

    // Live per-slot identity beside the high-water count. Both come from the
    // one pool scan task() already performs; neither reads the pool from
    // Core 0, which must never touch library-owned mutable storage.
    PublishedObservationWord ep_slot_map_{0};
    PublishedObservationWord ep_transfer_flags_{0};
    // Sticky, so an address that reached a state for one pass and lost it
    // before the next GET_DIAGNOSTICS is still a fact about the run.
    PublishedObservationWord enum_progress_mask_{0};
    PublishedObservationWord long_pass_count_{0};
    PublishedObservationWord long_pass_total_ms_{0};
    PublishedObservationWord ep_slots_opened_{0};
    PublishedObservationWord ep_max_failed_count_{0};
    PublishedObservationWord max_pass_gap_us_{0};
    std::uint32_t previous_pass_us_ = 0;
    bool have_previous_pass_ = false;
    PublishedObservationWord max_sof_gap_{0};
    std::uint32_t previous_sof_frame_ = 0;
    bool have_previous_sof_frame_ = false;

    // Incremented once per task() call, and task() is called exactly once per
    // pass of core1_entry's loop, unconditionally - ahead of the host_ready_
    // early return, so a backend that refused to start still proves the core
    // itself is turning.
    PublishedObservationWord core1_passes_{0};
};

}  // namespace duo_input::u1::pio_usb
