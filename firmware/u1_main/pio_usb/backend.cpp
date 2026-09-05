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
        host_init_flags_ |= kHostInitHostAlreadyActive;
    }
    // main() selected and settled 120 MHz before any peripheral or Core 1
    // started. This reading is therefore both a proof that the reorder took
    // effect (120 MHz, not the former 125 MHz) and the clock from which the
    // host below computes its dividers.
    clk_hz_at_begin_ = clock_get_hz(clk_sys);

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
        host_init_flags_ |= kHostInitConfigured;
    }
    if (initialized) {
        host_init_flags_ |= kHostInitInitialized;
    }
    if (tuh_inited()) {
        host_init_flags_ |= kHostInitInited;
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
    if (core1_passes_ != 0xFFFFFFFFu) {
        ++core1_passes_;
    }

    if (have_previous_pass_) {
        const std::uint32_t gap = now_us - previous_pass_us_;
        if (gap > max_pass_gap_us_) {
            max_pass_gap_us_ = gap;
        }
    }
    previous_pass_us_ = now_us;
    have_previous_pass_ = true;

    const std::uint32_t sof_frame = pio_usb_host_get_frame_number();
    if (have_previous_sof_frame_) {
        const std::uint32_t gap = sof_frame - previous_sof_frame_;
        const std::uint16_t bounded_gap =
            gap > 0xFFFFu ? 0xFFFFu : static_cast<std::uint16_t>(gap);
        if (bounded_gap > max_sof_gap_) {
            max_sof_gap_ = bounded_gap;
        }
    }
    previous_sof_frame_ = sof_frame;
    have_previous_sof_frame_ = true;

    std::uint8_t slots_opened = 0;
    for (std::size_t index = 0; index < PIO_USB_EP_POOL_CNT; ++index) {
        const endpoint_t& endpoint = pio_usb_ep_pool[index];
        if (endpoint.size != 0 && slots_opened != 0xFFu) {
            ++slots_opened;
        }
        if (endpoint.failed_count > ep_max_failed_count_) {
            ep_max_failed_count_ = endpoint.failed_count;
        }
    }
    if (slots_opened > ep_slots_opened_) {
        ep_slots_opened_ = slots_opened;
    }

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
    if (connected && !root_port_was_connected_ && root_port_connects_ != 0xFFFFu) {
        ++root_port_connects_;
    }
    root_port_was_connected_ = connected;

    const bool suspended = pio_usb_root_port[0].suspended;
    if (!connected) {
        root_port_reset_in_progress_ = false;
    } else if (suspended) {
        root_port_reset_in_progress_ = true;
    } else if (root_port_reset_in_progress_) {
        if (root_port_resets_ != 0xFFFFu) {
            ++root_port_resets_;
        }
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
    if (hub_mounted && !hub_was_mounted_ && hub_mount_events_ != 0xFFFFu) {
        ++hub_mount_events_;
    }
    hub_was_mounted_ = hub_mounted;

    registry_.process_pending(now_us);
    registry_.retry_pending_arms(now_us);
}

HostObservability PioUsbBackend::observe() const {
    HostObservability out;
    out.init_flags = host_init_flags_;
    out.clk_hz_at_begin = clk_hz_at_begin_;
    out.clk_hz_now = clock_get_hz(clk_sys);
    out.sof_frame_count = pio_usb_host_get_frame_number();
    out.root_port_connects = root_port_connects_;
    out.core1_passes = core1_passes_;
    const HostCallbackObservability callbacks = host_callback_observability();
    out.mount_events = callbacks.mount_events;
    out.umount_events = callbacks.umount_events;
    out.hid_mount_events = callbacks.hid_mount_events;
    out.ep_slots_opened = ep_slots_opened_;
    out.ep_max_failed_count = ep_max_failed_count_;
    out.max_pass_gap_us = max_pass_gap_us_;
    out.max_sof_gap = max_sof_gap_;
    out.root_port_resets = root_port_resets_;
    out.hub_mount_events = hub_mount_events_;

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
