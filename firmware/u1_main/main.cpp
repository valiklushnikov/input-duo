// U1 entry point.
//
// Deliberately almost empty. This exists so the build, the flash layout check
// and the two-image contract are all verifiable before a single byte of HID is
// emitted; the services arrive in the tasks that follow. What it does do
// matters: it comes up with nothing held and nothing driven.

#include "pico/stdlib.h"

namespace {

// Both switches are read as active-low with an internal pull-up. They are
// configured here, from the first instant, so a board that stops in this loop
// still has defined input pins rather than floating ones.
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
    // Off, not on: an LED that lights up before the firmware can do anything
    // tells the operator the device is ready when it is not.
    gpio_put(PICO_DEFAULT_LED_PIN, 0);
#endif
}

}  // namespace

int main() {
    configure_indicator();
    configure_button(kSw1EmergencyMouseTogglePin);
    configure_button(kSw2StopReleaseAllPin);

    // The watchdog is not started yet. It is fed only after USB, SPI and the
    // command queue have all been serviced, and none of those exist here; a
    // watchdog armed now would reset a board that is behaving exactly as it
    // was built to.
    while (true) {
        tight_loop_contents();
    }
}
