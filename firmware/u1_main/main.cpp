// U1 entry point.
//
// At this stage U1 enumerates as its three HID interfaces plus CDC and holds
// nothing. There is no CH375B, no SPI and no configuration yet; what there is
// must be right, because every later task adds input on top of this loop and
// none of them get to revisit the invariant that a fresh board holds no keys.

#include "pico/stdlib.h"

#include "hid/state_manager.hpp"
#include "usb_service.hpp"

namespace {

// Both switches are read as active-low with an internal pull-up, from the
// first instant, so a board that stops early still has defined input pins
// rather than floating ones.
constexpr uint kSw1EmergencyMouseTogglePin = 14;
constexpr uint kSw2StopReleaseAllPin = 15;

void configure_button(uint pin) {
    gpio_init(pin);
    gpio_set_dir(pin, GPIO_IN);
    gpio_pull_up(pin);
}

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
    configure_button(kSw1EmergencyMouseTogglePin);
    configure_button(kSw2StopReleaseAllPin);

    duo_input::hid::HidStateManager outputs;
    duo_input::u1::UsbService usb;
    usb.begin();

    bool was_mounted = false;

    // The watchdog is not armed yet. It is fed only once USB, SPI and the
    // command queue have all been serviced, and two of those do not exist; a
    // watchdog armed now would reset a board behaving exactly as built.
    while (true) {
        usb.task();

        const bool mounted = usb.mounted();
        if (mounted != was_mounted) {
            // A host that has just enumerated knows nothing about the reports
            // sent before, and anything held while unplugged was never
            // released as far as it is concerned. Start from nothing.
            outputs.release_target(duo_input::hid::Target::Pc1);
            usb.forget_sent_state();
            was_mounted = mounted;
        }

        usb.publish(outputs);
    }
}
