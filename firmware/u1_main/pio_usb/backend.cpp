#include "pio_usb/backend.hpp"

#include "hardware/clocks.h"
#include "pio_usb.h"
#include "tusb.h"

namespace duo_input::u1::pio_usb {

void PioUsbBackend::begin() {
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
    if (!host_ready_) {
        return;
    }
    tuh_task();
    registry_.process_pending(now_us);
    registry_.retry_pending_arms(now_us);
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
