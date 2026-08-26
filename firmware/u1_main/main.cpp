// U1 entry point.
//
// At this stage U1 enumerates as its three HID interfaces plus CDC, drives the
// SPI link to U2, and holds nothing. There is no CH375B and no stored
// configuration yet, so nothing ever presses a key - but every later task adds
// input on top of this loop, and none of them get to revisit the invariant
// that a board with no input holds nothing.

#include "pico/multicore.h"
#include "pico/stdlib.h"

#include "hid/state_manager.hpp"
#include "output_runtime.hpp"
#include "spi_master.hpp"
#include "usb_service.hpp"

#if DUO_TEST_PATTERN
#include "test_pattern.hpp"
#endif

namespace {

// Core 0 owns this. Core 1 only ever submits commands to it, so there is
// exactly one writer and no locking between a keypress and a USB report.
duo_input::u1::OutputRuntime g_outputs;

#if DUO_TEST_PATTERN
// Core 1's stand-in until the CH375B exists. Compiled out of a release build:
// a device that can generate its own input must not ship by accident.
void core1_entry() {
    while (true) {
        duo_input::u1::advance_test_pattern(g_outputs,
                                            to_ms_since_boot(get_absolute_time()));
        sleep_ms(1);
    }
}
#endif

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

duo_input::runtime::OutputCommand release_pc1() {
    duo_input::runtime::OutputCommand command;
    command.kind = duo_input::runtime::CommandKind::ReleaseRoute;
    command.route = duo_input::runtime::Route::Pc1;
    return command;
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

    duo_input::u1::UsbService usb;
    duo_input::u1::SpiMaster link;
    usb.begin();
    link.begin();

#if DUO_TEST_PATTERN
    multicore_launch_core1(core1_entry);
#endif

    bool was_mounted = false;

    // The watchdog is not armed yet. It is fed only once USB, SPI and the
    // command queue have all been serviced, and the queue does not exist; a
    // watchdog armed now would reset a board behaving exactly as built.
    while (true) {
        usb.task();

        const bool mounted = usb.mounted();
        if (mounted != was_mounted) {
            // A host that has just enumerated knows nothing about the reports
            // sent before, and anything held while unplugged was never
            // released as far as it is concerned. Start from nothing.
            g_outputs.process(release_pc1());
            usb.forget_sent_state();
            was_mounted = mounted;
        }

        // Bounded, so a burst of input cannot starve the USB it is for.
        g_outputs.drain();

        usb.publish(g_outputs);

        // PC2's half of the state goes over the link. It sends on change and
        // otherwise heartbeats, so a quiet device does not saturate the bus
        // and does not look severed either.
        const std::uint32_t now_ms = to_ms_since_boot(get_absolute_time());
        link.poll(now_ms, g_outputs.take_snapshot(duo_input::hid::Target::Pc2));
    }
}
