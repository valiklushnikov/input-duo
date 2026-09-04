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

    pio_usb_configuration_t config = PIO_USB_DEFAULT_CONFIG;
    // D+ on GP0. Pico-PIO-USB requires D- to be the next pin up and derives
    // it from this one rather than taking it as a separate field - GP1 is
    // that adjacent pin, and nothing here names it.
    config.pin_dp = 0;
    tuh_configure(1, TUH_CFGID_RPI_PIO_USB_CONFIGURATION, &config);
    tuh_init(1);

    // Last: the release store publishes every preceding clock/host write to
    // Core 0. Its acquire read is the point after which SPI may be touched.
    clock_change_.publish_settled();
}

bool PioUsbBackend::clock_settled() const { return clock_change_.settled(); }

void PioUsbBackend::task(std::uint32_t now_us) {
    (void)now_us;
    tuh_task();
}

bool PioUsbBackend::take_event(input::SourceEvent& event, input::SourceIdentity& identity) {
    (void)event;
    (void)identity;
    // Nothing is queued yet: tinyusb_host_callbacks.cpp does not fill any
    // storage this task, so there is never anything to hand back. Tasks 6-8
    // add that storage and make this answer true.
    return false;
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
