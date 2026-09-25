// U2 entry point.
//
// U2 holds no configuration and has no peripherals of its own. At this stage
// it enumerates as the same three HID interfaces U1 offers and holds nothing;
// once the SPI link exists, everything it emits will arrive from U1, and the
// invariant established here - a board with no valid input holds no keys - is
// the one that must survive every later change.

#include "pico/stdlib.h"

#include "hardware/gpio.h"

#include "tusb.h"

#include "address_service.hpp"
#include "hid/state_manager.hpp"
#include "link/host_addresses.hpp"
#include "link/spi_protocol.hpp"
#include "link_drop_log.hpp"
#include "link_watchdog.hpp"
#include "spi_slave.hpp"
#include "usb_service.hpp"

namespace {

/// The LED reports whether the link is alive.
///
/// It goes out at the same moment U2 releases everything, which makes the
/// 100 ms fail-safe something an operator can see rather than something they
/// have to take on trust.
void show_link(bool healthy) {
#ifdef PICO_DEFAULT_LED_PIN
    gpio_put(PICO_DEFAULT_LED_PIN, healthy ? 1 : 0);
#endif
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

void apply(const duo_input::u2::ValidFrame& frame,
           duo_input::hid::HidStateManager& outputs,
           duo_input::link::AddressBook& addresses) {
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
        case duo_input::protocol::SpiMessageType::HOST_ADDRESSES:
            // PC1's addresses, for PC2's program to read over CDC.
            addresses.accept_peer(payload);
            return;
        default:
            // HEARTBEAT and HANDSHAKE carry no state; arriving intact is the
            // whole message.
            return;
    }
}

/// Sends the address service's replies back down the CDC pipe.
///
/// Mirrors U1's own static_assert beside its CdcWriter (main.cpp:660): the
/// TinyUSB TX FIFO has to hold whatever a single write() hands it, or a reply
/// longer than the FIFO would be split - or dropped - underneath the class
/// that has no idea that happened.
static_assert(CFG_TUD_CDC_TX_BUFSIZE >= duo_input::u2::AddressService::kMaxReplyWire,
              "TinyUSB TX FIFO must hold one complete encoded reply");

class CdcWriter : public duo_input::u2::ByteSink {
public:
    void write(const std::uint8_t* data, std::size_t size) override {
        // Same rule as U1: written whether or not DTR is up, because
        // QSerialPort does not raise it on open.
        tud_cdc_write(data, static_cast<std::uint32_t>(size));
        tud_cdc_write_flush();
    }
};

}  // namespace

int main() {
#if DUO_WIRE_WALK
    // Bring-up only: U2 becomes a mirror and nothing else. No SPI, no USB.
    //
    // It drives its outgoing line with the parity of the three lines U1 drives
    // it with. U1 can then walk all eight combinations and read back a single
    // byte whose value names which wire is broken - a dead clock and a dead
    // chip select produce different bytes, where a link counter produces the
    // same zero for both. Inputs are pulled down so a wire nobody drives reads
    // as a definite zero instead of as noise.
    {
        const unsigned inputs[3] = {duo_input::u2::kPinSpiCs, duo_input::u2::kPinSpiSck,
                                    duo_input::u2::kPinSpiRx};
        for (unsigned pin : inputs) {
            gpio_init(pin);
            gpio_set_dir(pin, GPIO_IN);
            gpio_pull_down(pin);
        }
        gpio_init(duo_input::u2::kPinSpiTx);
        gpio_set_dir(duo_input::u2::kPinSpiTx, GPIO_OUT);
        while (true) {
            const bool parity = gpio_get(inputs[0]) ^ gpio_get(inputs[1]) ^ gpio_get(inputs[2]);
            gpio_put(duo_input::u2::kPinSpiTx, parity);
        }
    }
#endif

    configure_indicator();

    duo_input::hid::HidStateManager outputs;
    duo_input::u2::UsbService usb;
    duo_input::u2::SpiSlave link;
    duo_input::u2::LinkWatchdog watchdog;
    duo_input::u2::LinkDropLog drops;
    usb.begin();
    link.begin();

    static duo_input::link::AddressBook addresses;
    static CdcWriter cdc_writer;
    static duo_input::u2::AddressService address_service(addresses, cdc_writer);
    link.set_address_book(&addresses);

    bool was_mounted = false;
    bool released_for_silence = false;

    // The watchdog is not armed yet: it is fed only once USB and the SPI link
    // have both been serviced, and the link does not exist. The link watchdog
    // that releases every key after 100 ms without a valid frame arrives with
    // it, in the next task.
    while (true) {
        usb.task();

        if (tud_cdc_available()) {
            std::uint8_t incoming[64];
            const std::uint32_t read = tud_cdc_read(incoming, sizeof(incoming));
            address_service.on_cdc_bytes(incoming, read);
        }

        const bool mounted = usb.mounted();
        if (mounted != was_mounted) {
            // A host that has just enumerated knows nothing about the reports
            // sent before, and anything held while unplugged was never
            // released as far as it is concerned. Start from nothing.
            outputs.release_target(duo_input::hid::Target::Pc2);
            usb.forget_sent_state();
            if (!mounted) {
                address_service.on_disconnect();
            }
            was_mounted = mounted;
        }

        duo_input::u2::SpiSlave::Status status;
        status.mounted = mounted;
        status.drops = drops.drops();
        status.last_release_ms = drops.last_release_ms();
        link.set_status(status);

        const std::uint32_t now_ms = to_ms_since_boot(get_absolute_time());
        duo_input::u2::ValidFrame frame;
        while (link.take_valid_frame(now_ms, frame)) {
            watchdog.observe_valid(now_ms);
            drops.recovered();
            released_for_silence = false;
            apply(frame, outputs, addresses);
        }

        // No valid frame for 100 ms. U1 cannot tell us to let go, because the
        // thing that would tell us is what went away - so let go here. A stuck
        // modifier changes what every later key means, and the operator cannot
        // fix it from the computer receiving it.
        const bool link_alive = !watchdog.expired(now_ms);
        if (!link_alive && !released_for_silence) {
            outputs.release_target(duo_input::hid::Target::Pc2);
            // Recorded at the moment of release, so U1 can be told afterwards
            // how long the silence had lasted when the keys were let go.
            drops.released(watchdog.silence_ms(now_ms));
            released_for_silence = true;
        }
        show_link(link_alive);

        usb.publish(outputs);
    }
}
