#include "pio_usb/backend.hpp"

#include "hardware/clocks.h"
#include "pico/stdlib.h"
#include "pio_usb.h"
#include "tusb.h"

// Pico-PIO-USB's own root-port table. Declared here rather than reached
// through pio_usb_ll.h, which drags in hardware/pio.h and both generated
// .pio.h programs for four volatile bools; this is the same extern-declaration
// trick the rest of this tree uses for genuine library entry points (see
// device_registry.cpp's tuh_hid_receive_report and the note in
// tests/firmware_native/fakes/hardware/timer.h). root_port_t and
// PIO_USB_ROOT_PORT_CNT come from pio_usb.h above, so the type is the library's
// own and not a second copy of it that could drift. At file scope rather than
// in an unnamed namespace: a C-linkage name cannot also have internal linkage.
extern "C" root_port_t pio_usb_root_port[PIO_USB_ROOT_PORT_CNT];
extern "C" endpoint_t pio_usb_ep_pool[PIO_USB_EP_POOL_CNT];

namespace duo_input::u1::pio_usb {
namespace {

constexpr std::uint8_t kHostRhPort = 1;
// TinyUSB allocates hub addresses immediately above its ordinary device
// range. CFG_TUH_HUB is one in this image, so this is the sole root hub's
// address and remains tied to the pinned stack's public configuration.
constexpr std::uint8_t kHubAddress = CFG_TUH_DEVICE_MAX + 1;
// The highest address TinyUSB's pinned configuration can assign. ep_slot_map
// keeps only three bits for an address, and enum_progress_mask only eight bits
// per half, so both would start lying if this configuration grew. A compile
// error is the right way to find that out.
constexpr std::uint8_t kHighestAddress = CFG_TUH_DEVICE_MAX + CFG_TUH_HUB;
static_assert(kHighestAddress <= kEpSlotAddressMask,
              "ep_slot_map keeps three bits for a device address - raising "
              "CFG_TUH_DEVICE_MAX or CFG_TUH_HUB needs a wider field, not a "
              "wider mask");
static_assert(kHighestAddress <= kEnumDescriptorShift,
              "enum_progress_mask keeps one bit per address in each half - "
              "raising CFG_TUH_DEVICE_MAX or CFG_TUH_HUB needs a wider field");

void publish_max(std::atomic<std::uint32_t>& destination, std::uint32_t value) noexcept {
    const std::uint32_t previous = destination.load(std::memory_order_relaxed);
    if (value > previous) {
        destination.store(value, std::memory_order_relaxed);
    }
}

void increment_saturating(std::atomic<std::uint32_t>& destination,
                          std::uint32_t limit) noexcept {
    const std::uint32_t previous = destination.load(std::memory_order_relaxed);
    if (previous < limit) {
        destination.store(previous + 1u, std::memory_order_relaxed);
    }
}

void publish_flag(std::atomic<std::uint32_t>& destination, std::uint32_t flag) noexcept {
    destination.store(destination.load(std::memory_order_relaxed) | flag,
                      std::memory_order_relaxed);
}

void add_saturating(std::atomic<std::uint32_t>& destination,
                    std::uint32_t addend) noexcept {
    const std::uint32_t previous = destination.load(std::memory_order_relaxed);
    // Clamps rather than wrapping, for the reason every other counter here
    // does: a total that rolled over to a small number would read as a board
    // that barely blocked at all, which is the opposite of what it measured.
    const std::uint32_t remaining = 0xFFFFFFFFu - previous;
    destination.store(addend > remaining ? 0xFFFFFFFFu : previous + addend,
                      std::memory_order_relaxed);
}

/// One ep_slot_map byte for one pool entry. Zero means the slot is closed.
std::uint8_t encode_endpoint_slot(const endpoint_t& endpoint) noexcept {
    if (endpoint.size == 0) {
        // Pico-PIO-USB uses size as its validity flag; see
        // pio_usb_host_endpoint_open and pio_usb_host_close_device.
        return 0;
    }
    const std::uint8_t address = static_cast<std::uint8_t>(endpoint.dev_addr);
    const std::uint8_t number = static_cast<std::uint8_t>(endpoint.ep_num);
    std::uint8_t encoded = kEpSlotOpen;
    encoded = static_cast<std::uint8_t>(
        encoded | ((address & kEpSlotAddressMask) << kEpSlotAddressShift));
    if ((number & 0x80u) != 0) {
        encoded = static_cast<std::uint8_t>(encoded | kEpSlotDirectionIn);
    }
    return static_cast<std::uint8_t>(encoded | (number & kEpSlotEndpointMask));
}

/// One ep_transfer_flags byte for one pool entry. Zero means closed.
std::uint8_t encode_endpoint_transfer(const endpoint_t& endpoint) noexcept {
    if (endpoint.size == 0) {
        return 0;
    }

    std::uint8_t encoded = kEpXferOpen;
    if (endpoint.need_pre) {
        encoded = static_cast<std::uint8_t>(encoded | kEpXferNeedPre);
    }
    if (endpoint.stalled) {
        encoded = static_cast<std::uint8_t>(encoded | kEpXferStalled);
    }
    if (endpoint.transfer_aborted) {
        encoded = static_cast<std::uint8_t>(encoded | kEpXferAborted);
    }
    if (!endpoint.has_transfer) {
        // is_tx and data_id retain the preceding stage after completion; they
        // are not a live direction or PID while the endpoint is idle.
        return encoded;
    }

    encoded = static_cast<std::uint8_t>(encoded | kEpXferHasTransfer);
    if (endpoint.is_tx) {
        encoded = static_cast<std::uint8_t>(encoded | kEpXferHostOut);
    }
    if (endpoint.data_id == USB_PID_SETUP) {
        encoded = static_cast<std::uint8_t>(encoded | kEpXferSetupStaged);
    } else if (endpoint.data_id == 1u) {
        encoded = static_cast<std::uint8_t>(encoded | kEpXferData1);
    }
    return encoded;
}

}  // namespace

void PioUsbBackend::begin() {
    // First, before anything here changes a clock or touches the host stack:
    // whether the host was ALREADY up when Core 1 got here. That is the one
    // reading that separates "this backend started the host" from "something
    // else started it first and every call below is a no-op that still returns
    // true" - which is exactly what usb_service.cpp's argument-less
    // tusb_init() did from Core 0, and what made a dead board indistinguishable
    // from an idle one for a whole bench session.
    if (tuh_rhport_is_active(kHostRhPort)) {
        publish_flag(host_init_flags_, kHostInitHostAlreadyActive);
    }
    // main() selected and settled 120 MHz before any peripheral or Core 1
    // started. This reading is therefore both a proof that the reorder took
    // effect (120 MHz, not the former 125 MHz) and the clock from which the
    // host below computes its dividers.
    clk_hz_at_begin_.store(clock_get_hz(clk_sys), std::memory_order_relaxed);

    // The reference host also waits on the core that owns tuh_init. This runs
    // once at boot, before any service loop; it is not a service-path wait.
    sleep_ms(10);

    reset_host_callback_observability();
    set_callback_registry(&registry_);

    pio_usb_configuration_t config = PIO_USB_DEFAULT_CONFIG;
    // D+ on GP0. Pico-PIO-USB requires D- to be the next pin up and derives
    // it from this one rather than taking it as a separate field - GP1 is
    // that adjacent pin, and nothing here names it.
    config.pin_dp = 0;
    const bool configured = tuh_configure(1, TUH_CFGID_RPI_PIO_USB_CONFIGURATION, &config);
    const bool initialized = tuh_init(1);
    host_ready_ = configured && initialized;
    registry_.record_host_initialization(configured, initialized);

    // Recorded whatever they said, including "true" - see the comment above
    // the first flag: a true from tuh_init on an rhport somebody else already
    // activated is not evidence that this call did anything.
    if (configured) {
        publish_flag(host_init_flags_, kHostInitConfigured);
    }
    if (initialized) {
        publish_flag(host_init_flags_, kHostInitInitialized);
    }
    if (tuh_inited()) {
        publish_flag(host_init_flags_, kHostInitInited);
    }

    // Last: the release store publishes every preceding clock/host write to
    // Core 0. Its acquire read is the point after which SPI may be touched.
    clock_change_.publish_settled();
}

bool PioUsbBackend::clock_settled() const { return clock_change_.settled(); }

void PioUsbBackend::task(std::uint32_t now_us) {
    // Not read for a Report's own received_us: that is the TinyUSB
    // callback's own capture time (tinyusb_host_callbacks.cpp reads it
    // directly, inside tuh_task() below), not the moment this particular
    // pass got around to draining it. now_us is the single clock reading
    // Task 10's bounded receive-arm retry/backoff judges every interface's
    // deadline against - and it is handed to BOTH halves of that mechanism:
    // process_pending(), where a refused arm computes its next deadline, and
    // retry_pending_arms(), which decides whether one has elapsed. There is
    // no stall TIMEOUT to feed: a stall is recognised from a zero-length
    // completion, not from silence, because silence is what a healthy idle
    // keyboard produces - see DeviceRegistry::retry_pending_arms.
    // Ahead of every early return below, and ahead of tuh_task(): this counter
    // is the proof that Core 1 is turning at all, and a backend that refused
    // to start is one of the cases it has to be able to report. task() is
    // called exactly once per pass of core1_entry's loop, unconditionally, so
    // one increment here is one pass - which is what makes "unchanged across
    // two reads twenty seconds apart" mean Core 1 stopped.
    //
    // Saturating rather than wrapping, for the same reason the registry's
    // counters are: at roughly the rate this loop turns, a u32 would wrap in
    // weeks, and a counter that came back to a value it already showed would
    // read as a stopped core to exactly the procedure that exists to detect
    // one.
    increment_saturating(core1_passes_, 0xFFFFFFFFu);

    if (have_previous_pass_) {
        const std::uint32_t gap = now_us - previous_pass_us_;
        publish_max(max_pass_gap_us_, gap);
        // The blocking budget, measured. max_pass_gap_us above keeps only the
        // single worst pass, which cannot tell one 500 ms root enumeration
        // from a 500 ms root enumeration followed by a 450 ms hub-branch
        // debounce - and separating exactly those two was the soft step in the
        // analysis this image exists to settle.
        if (gap > kLongPassThresholdUs) {
            increment_saturating(long_pass_count_, 0xFFFFFFFFu);
            add_saturating(long_pass_total_ms_, gap / 1000u);
        }
    }
    previous_pass_us_ = now_us;
    have_previous_pass_ = true;

    const std::uint32_t sof_frame = pio_usb_host_get_frame_number();
    if (have_previous_sof_frame_) {
        const std::uint32_t gap = sof_frame - previous_sof_frame_;
        const std::uint16_t bounded_gap =
            gap > 0xFFFFu ? 0xFFFFu : static_cast<std::uint16_t>(gap);
        publish_max(max_sof_gap_, bounded_gap);
    }
    previous_sof_frame_ = sof_frame;
    have_previous_sof_frame_ = true;

    std::uint8_t slots_opened = 0;
    std::uint32_t slot_map = 0;
    std::uint32_t transfer_flags = 0;
    for (std::size_t index = 0; index < PIO_USB_EP_POOL_CNT; ++index) {
        const endpoint_t& endpoint = pio_usb_ep_pool[index];
        if (endpoint.size != 0 && slots_opened != 0xFFu) {
            ++slots_opened;
        }
        // The same scan, one more reading out of it: WHOSE endpoint each of
        // the first four slots holds. The count above says how many; only this
        // says whether the third one belongs to a device behind the hub, which
        // is the claim the whole enumeration analysis rests on.
        if (index < kEpSlotMapSlots) {
            slot_map |= static_cast<std::uint32_t>(encode_endpoint_slot(endpoint))
                        << (8u * index);
            transfer_flags |=
                static_cast<std::uint32_t>(encode_endpoint_transfer(endpoint))
                << (8u * index);
        }
        publish_max(ep_max_failed_count_, endpoint.failed_count);
    }
    publish_max(ep_slots_opened_, slots_opened);
    // Stored, not maxed: this one is a live map and has to be able to go back
    // down when an endpoint closes. ep_slots_opened beside it stays the
    // high-water count, so the pair says both how far it went and where it is.
    ep_slot_map_.store(slot_map, std::memory_order_relaxed);
    // Live like ep_slot_map: Core 1 is the only pool sampler and publishes one
    // already-packed word for Core 0, which never touches library storage.
    ep_transfer_flags_.store(transfer_flags, std::memory_order_relaxed);

    // The attach edge. Nothing below TinyUSB reports one, so it is polled
    // here: four volatile reads a pass, no allocation and no wait. It counts
    // whether U1 ever saw the hub's D+ pull-up at all, which is a different
    // question from whether anything on the bus ever answered - and on this
    // defect the two had different answers.
    //
    // Polled, so the count is a LOWER BOUND: an attach and detach that both
    // fall between two passes leaves no trace. This loop turns far faster than
    // USB debounce, so it is a small bound - but it is a bound, and the wire
    // documentation and the configurator both say so where a reader will see
    // it rather than only here.
    const bool connected = pio_usb_root_port[0].connected;
    if (connected && !root_port_was_connected_) {
        increment_saturating(root_port_connects_, 0xFFFFu);
    }
    root_port_was_connected_ = connected;

    const bool suspended = pio_usb_root_port[0].suspended;
    if (!connected) {
        root_port_reset_in_progress_ = false;
    } else if (suspended) {
        root_port_reset_in_progress_ = true;
    } else if (root_port_reset_in_progress_) {
        increment_saturating(root_port_resets_, 0xFFFFu);
        root_port_reset_in_progress_ = false;
    }

    if (!host_ready_) {
        return;
    }
    tuh_task();

    // TinyUSB intentionally suppresses tuh_mount_cb for hub addresses, so
    // that callback counter cannot answer whether the hub itself configured.
    // Poll the public mounted state and count rising edges separately. Like
    // the root-port edge counters this is a saturating lower bound: a whole
    // mount/unmount cycle between two passes is not observable.
    const bool hub_mounted = tuh_mounted(kHubAddress);
    if (hub_mounted && !hub_was_mounted_) {
        increment_saturating(hub_mount_events_, 0xFFFFu);
    }
    hub_was_mounted_ = hub_mounted;

    // How far each address got, through TinyUSB's public API only and without
    // reading one byte of its internal device table from this core or any
    // other. tuh_mounted() is dev->configured; tuh_vid_pid_get() is true only
    // once the address is assigned AND the device descriptor has been read, so
    // the two halves separate "never addressed" from "addressed, then stopped
    // in the configuration phase" - which is precisely the distinction the
    // ranked causes turn on. Neither call mutates anything.
    std::uint32_t progress = 0;
    for (std::uint8_t address = 1; address <= kHighestAddress; ++address) {
        const std::uint8_t bit = static_cast<std::uint8_t>(address - 1);
        if (tuh_mounted(address)) {
            progress |= 1u << bit;
        }
        std::uint16_t vendor_id = 0;
        std::uint16_t product_id = 0;
        if (tuh_vid_pid_get(address, &vendor_id, &product_id)) {
            progress |= 1u << (bit + kEnumDescriptorShift);
        }
    }
    // OR-accumulated: an address that reached a state for one pass and lost it
    // before the next GET_DIAGNOSTICS still happened.
    publish_flag(enum_progress_mask_, progress);

    registry_.process_pending(now_us);
    registry_.retry_pending_arms(now_us);
}

HostObservability PioUsbBackend::observe() const {
    HostObservability out;
    out.init_flags = static_cast<std::uint8_t>(host_init_flags_.load(std::memory_order_relaxed));
    out.clk_hz_at_begin = clk_hz_at_begin_.load(std::memory_order_relaxed);
    out.clk_hz_now = clock_get_hz(clk_sys);
    out.sof_frame_count = pio_usb_host_get_frame_number();
    out.root_port_connects =
        static_cast<std::uint16_t>(root_port_connects_.load(std::memory_order_relaxed));
    out.core1_passes = core1_passes_.load(std::memory_order_relaxed);
    const HostCallbackObservability callbacks = host_callback_observability();
    out.mount_events = callbacks.mount_events;
    out.umount_events = callbacks.umount_events;
    out.hid_mount_events = callbacks.hid_mount_events;
    out.ep_slots_opened =
        static_cast<std::uint8_t>(ep_slots_opened_.load(std::memory_order_relaxed));
    out.ep_max_failed_count =
        static_cast<std::uint8_t>(ep_max_failed_count_.load(std::memory_order_relaxed));
    out.max_pass_gap_us = max_pass_gap_us_.load(std::memory_order_relaxed);
    out.max_sof_gap =
        static_cast<std::uint16_t>(max_sof_gap_.load(std::memory_order_relaxed));
    out.root_port_resets =
        static_cast<std::uint16_t>(root_port_resets_.load(std::memory_order_relaxed));
    out.hub_mount_events =
        static_cast<std::uint16_t>(hub_mount_events_.load(std::memory_order_relaxed));
    out.ep_slot_map = ep_slot_map_.load(std::memory_order_relaxed);
    out.ep_transfer_flags = ep_transfer_flags_.load(std::memory_order_relaxed);
    out.host_event_counts = callbacks.host_event_counts;
    out.enum_progress_mask = enum_progress_mask_.load(std::memory_order_relaxed);
    out.long_pass_count = long_pass_count_.load(std::memory_order_relaxed);
    out.long_pass_total_ms = long_pass_total_ms_.load(std::memory_order_relaxed);
    out.core1_min_sp = callbacks.core1_min_sp;
    out.xfer_completions_at_attach = callbacks.xfer_completions_at_attach;

    const root_port_t& root = pio_usb_root_port[0];
    std::uint8_t state = 0;
    if (root.initialized) {
        state |= kRootPortInitialized;
    }
    if (root.connected) {
        state |= kRootPortConnected;
    }
    if (root.suspended) {
        state |= kRootPortSuspended;
    }
    if (root.is_fullspeed) {
        state |= kRootPortFullSpeed;
    }
    out.root_port_state = state;
    return out;
}

bool PioUsbBackend::take_event(input::SourceEvent& event, input::SourceIdentity& identity) {
    return registry_.take_event(event, identity);
}

int PioUsbBackend::logical_port(input::DeviceKind kind) {
    switch (kind) {
        case input::DeviceKind::Keyboard:
            return 0;
        case input::DeviceKind::Mouse:
            return 1;
        case input::DeviceKind::Unknown:
        default:
            return -1;
    }
}

}  // namespace duo_input::u1::pio_usb
