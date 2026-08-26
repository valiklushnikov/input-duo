// U2 entry point.
//
// U2 holds no configuration and has no peripherals of its own. At this stage
// it enumerates as the same three HID interfaces U1 offers and holds nothing;
// once the SPI link exists, everything it emits will arrive from U1, and the
// invariant established here - a board with no valid input holds no keys - is
// the one that must survive every later change.

#include "pico/stdlib.h"

#include "hid/state_manager.hpp"
#include "link/spi_protocol.hpp"
#include "link_watchdog.hpp"
#include "spi_slave.hpp"
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

void apply(const duo_input::u2::ValidFrame& frame,
           duo_input::hid::HidStateManager& outputs) {
    namespace hid = duo_input::hid;
    namespace link = duo_input::link;
    const auto payload = duo_input::protocol::ByteView{frame.payload, frame.payload_size};

    switch (frame.type) {
        case duo_input::protocol::SpiMessageType::KBD_STATE: {
            hid::KeyboardSnapshot keyboard;
            if (!link::decode_keyboard_state(payload, keyboard)) {
                // A frame that passed its CRC but cannot mean what it claims
                // is still not input. Say nothing.
                return;
            }
            outputs.release_target(hid::Target::Pc2);
            outputs.physical_modifiers(hid::Target::Pc2, keyboard.modifiers, true);
            for (std::uint8_t index = 0; index < keyboard.key_count; ++index) {
                outputs.physical_key(hid::Target::Pc2, keyboard.keys[index], true);
            }
            return;
        }
        case duo_input::protocol::SpiMessageType::MOUSE_DELTA: {
            hid::MouseSnapshot mouse;
            if (!link::decode_mouse_delta(payload, mouse)) {
                return;
            }
            outputs.set_mouse_buttons(hid::Target::Pc2, mouse.buttons);
            outputs.mouse_delta(hid::Target::Pc2, mouse.delta_x, mouse.delta_y, mouse.wheel,
                                mouse.pan);
            return;
        }
        case duo_input::protocol::SpiMessageType::CONTROL_RELEASE_ALL:
            outputs.release_target(hid::Target::Pc2);
            return;
        default:
            // HEARTBEAT and HANDSHAKE carry no state; arriving intact is the
            // whole message.
            return;
    }
}

}  // namespace

int main() {
    configure_indicator();

    duo_input::hid::HidStateManager outputs;
    duo_input::u2::UsbService usb;
    duo_input::u2::SpiSlave link;
    duo_input::u2::LinkWatchdog watchdog;
    usb.begin();
    link.begin();

    bool was_mounted = false;
    bool released_for_silence = false;

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

        link.set_status(mounted);

        const std::uint32_t now_ms = to_ms_since_boot(get_absolute_time());
        duo_input::u2::ValidFrame frame;
        while (link.take_valid_frame(frame)) {
            watchdog.observe_valid(now_ms);
            released_for_silence = false;
            apply(frame, outputs);
        }

        // No valid frame for 100 ms. U1 cannot tell us to let go, because the
        // thing that would tell us is what went away - so let go here. A stuck
        // modifier changes what every later key means, and the operator cannot
        // fix it from the computer receiving it.
        if (watchdog.expired(now_ms) && !released_for_silence) {
            outputs.release_target(duo_input::hid::Target::Pc2);
            released_for_silence = true;
        }

        usb.publish(outputs);
    }
}
