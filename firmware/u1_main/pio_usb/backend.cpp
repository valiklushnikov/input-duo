#include "pio_usb/backend.hpp"

#include "hardware/clocks.h"
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

namespace duo_input::u1::pio_usb {
namespace {

constexpr std::uint8_t kHostRhPort = 1;

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
    // And the clock the PIO dividers would be computed from if the host were
    // brought up now, captured before the call below moves it. Pico-PIO-USB
    // computes its dividers once and never recomputes them, so this number and
    // the one Core 0 samples at reply time are what say whether the bus is
    // being driven at the rate its dividers were built for.
    clk_hz_at_begin_ = clock_get_hz(clk_sys);

    // Pico-PIO-USB bit-bangs both directions of full-speed USB out of PIO
    // state machines clocked from clk_sys, and 120 MHz is the rate its own
    // timing is written against. RHPort 0's device side does not move: it
    // runs off the RP2040's dedicated 48 MHz USB PLL, which this call does
    // not touch.
    //
    // clk_peri does move, though, and this class does not own it: on this
    // SDK (PICO_CLOCK_ADJUST_PERI_CLOCK_WITH_SYS_CLOCK is 0 here, so
    // set_sys_clock_pll takes the branch that does not keep clk_peri tied to
    // clk_sys) this call reparents clk_peri onto PLL_USB at 48 MHz as a side
    // effect. clock_settled() is what tells Core 0's SPI code that side
    // effect has already happened - see backend.hpp and
    // SpiMaster::refresh_baudrate().
    set_sys_clock_khz(120000, true);

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

    // The attach edge. Nothing below TinyUSB reports one, so it is polled
    // here: four volatile reads a pass, no allocation and no wait. It counts
    // whether U1 ever saw the hub's D+ pull-up at all, which is a different
    // question from whether anything on the bus ever answered - and on this
    // defect the two had different answers.
    const bool connected = pio_usb_root_port[0].connected;
    if (connected && !root_port_was_connected_ && root_port_connects_ != 0xFFFFu) {
        ++root_port_connects_;
    }
    root_port_was_connected_ = connected;

    if (!host_ready_) {
        return;
    }
    tuh_task();
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
