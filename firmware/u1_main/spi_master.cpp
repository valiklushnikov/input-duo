#include "spi_master.hpp"

#include <cstring>

#include "hardware/gpio.h"
#include "hardware/spi.h"

#include "protocol/frame.hpp"

namespace duo_input::u1 {
namespace {

spi_inst_t* const kSpi = spi1;

bool same_keyboard(const hid::KeyboardSnapshot& left, const hid::KeyboardSnapshot& right) {
    if (left.modifiers != right.modifiers || left.key_count != right.key_count) {
        return false;
    }
    for (std::uint8_t index = 0; index < left.key_count; ++index) {
        if (left.keys[index] != right.keys[index]) {
            return false;
        }
    }
    return true;
}

bool moved(const hid::MouseSnapshot& mouse) {
    return mouse.delta_x != 0 || mouse.delta_y != 0 || mouse.wheel != 0 || mouse.pan != 0;
}

}  // namespace

void SpiMaster::begin() {
    spi_init(kSpi, kSpiBaudRate);
    spi_set_format(kSpi, 8, SPI_CPOL_0, SPI_CPHA_0, SPI_MSB_FIRST);

    gpio_set_function(kPinSpiRx, GPIO_FUNC_SPI);
    gpio_set_function(kPinSpiSck, GPIO_FUNC_SPI);
    gpio_set_function(kPinSpiTx, GPIO_FUNC_SPI);

    // CS is driven by hand rather than by the SPI block, so one assertion
    // frames exactly one 64-byte transfer. The hardware would otherwise
    // toggle it per byte, and the slave could not tell where a frame began.
    gpio_init(kPinSpiCs);
    gpio_set_dir(kPinSpiCs, GPIO_OUT);
    gpio_put(kPinSpiCs, 1);
}

bool SpiMaster::send(protocol::SpiMessageType type, protocol::ByteView payload,
                     std::uint32_t now_ms) {
    protocol::SpiFrame frame;
    frame.type = type;
    frame.sequence = sequence_;
    frame.payload = payload;

    std::size_t written = 0;
    std::memset(tx_, 0, sizeof(tx_));
    if (!protocol::encode_spi_frame(frame, protocol::MutableByteView{tx_, sizeof(tx_)},
                                    written)) {
        // A frame this side cannot encode is a bug here, not a link fault.
        // Sending the buffer anyway would put arbitrary bytes on the wire.
        return false;
    }

    gpio_put(kPinSpiCs, 0);
    spi_write_read_blocking(kSpi, tx_, rx_, kFrameSize);
    gpio_put(kPinSpiCs, 1);

    ++sequence_;
    last_sent_ms_ = now_ms;
    ever_sent_ = true;
    consume_reply(rx_);
    return true;
}

void SpiMaster::consume_reply(const std::uint8_t* reply) {
    protocol::DecodeResult result;
    if (!protocol::decode_spi_frame(protocol::ByteView{reply, kFrameSize}, result)) {
        // U2 either said nothing or said something damaged. Either way there
        // is nothing to believe, and nothing here acts on a guess.
        status_.answered = false;
        ++status_.crc_errors;
        return;
    }
    replies_.observe(result.spi.sequence);
    status_.answered = true;
    if (result.spi.type == protocol::SpiMessageType::ENDPOINT_STATUS &&
        result.spi.payload.size >= 1) {
        status_.mounted = result.spi.payload.data[0] != 0;
    }
}

bool SpiMaster::send_release_all(std::uint32_t now_ms) {
    // Deliberately unconditional: "let go of everything" must not wait for a
    // change to be noticed, and it is the one message worth sending twice.
    keyboard_valid_ = false;
    last_buttons_ = 0;
    return send(protocol::SpiMessageType::CONTROL_RELEASE_ALL,
                protocol::ByteView{nullptr, 0}, now_ms);
}

bool SpiMaster::poll(std::uint32_t now_ms, const hid::TargetSnapshot& pc2) {
    std::uint8_t payload[link::kKeyboardStateSize > link::kMouseDeltaSize
                             ? link::kKeyboardStateSize
                             : link::kMouseDeltaSize] = {};
    std::size_t written = 0;

    if (!keyboard_valid_ || !same_keyboard(pc2.keyboard, last_keyboard_)) {
        if (link::encode_keyboard_state(pc2.keyboard,
                                        protocol::MutableByteView{payload, sizeof(payload)},
                                        written) &&
            send(protocol::SpiMessageType::KBD_STATE,
                 protocol::ByteView{payload, written}, now_ms)) {
            last_keyboard_ = pc2.keyboard;
            keyboard_valid_ = true;
            return true;
        }
    }

    if (moved(pc2.mouse) || pc2.mouse.buttons != last_buttons_) {
        if (link::encode_mouse_delta(pc2.mouse,
                                     protocol::MutableByteView{payload, sizeof(payload)},
                                     written) &&
            send(protocol::SpiMessageType::MOUSE_DELTA,
                 protocol::ByteView{payload, written}, now_ms)) {
            last_buttons_ = pc2.mouse.buttons;
            return true;
        }
    }

    // Silence and a severed cable look identical from the far end, so a quiet
    // link still has to say something.
    const bool heartbeat_due =
        !ever_sent_ || (now_ms - last_sent_ms_) >= kHeartbeatIntervalMs;
    if (heartbeat_due) {
        return send(protocol::SpiMessageType::HEARTBEAT, protocol::ByteView{nullptr, 0},
                    now_ms);
    }
    return false;
}

}  // namespace duo_input::u1
