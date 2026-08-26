// U2 entry point.
//
// U2 holds no configuration and has no peripherals of its own. At this stage
// it enumerates as the same three HID interfaces U1 offers and holds nothing;
// once the SPI link exists, everything it emits will arrive from U1, and the
// invariant established here - a board with no valid input holds no keys - is
// the one that must survive every later change.

#include "pico/stdlib.h"

#include "hid/state_manager.hpp"
#include "usb_service.hpp"

namespace {

void configure_indicator() {
#ifdef PICO_DEFAULT_LED_PIN
    gpio_init(PICO_DEFAULT_LED_PIN);
    gpio_set_dir(PICO_DEFAULT_LED_PIN, GPIO_OUT);
    // Off, not on: an LED lit before the firmware can do anything tells the
    // operator the device is ready when it is not.
    gpio_put(PICO_DEFAULT_LED_PIN, 0);
#endif
}

}  // namespace

int main() {
    configure_indicator();

    duo_input::hid::HidStateManager outputs;
    duo_input::u2::UsbService usb;
    usb.begin();

    bool was_mounted = false;

    // The watchdog is not armed yet: it is fed only once USB and the SPI link
    // have both been serviced, and the link does not exist. The link watchdog
    // that releases every key after 100 ms without a valid frame arrives with
    // it, in the next task.
    while (true) {
        usb.task();

        const bool mounted = usb.mounted();
        if (mounted != was_mounted) {
            // A host that has just enumerated knows nothing about the reports
            // sent before, and anything held while unplugged was never
            // released as far as it is concerned. Start from nothing.
            outputs.release_target(duo_input::hid::Target::Pc2);
            usb.forget_sent_state();
            was_mounted = mounted;
        }

        usb.publish(outputs);
    }
}
