#include "spi_master.hpp"

#include <cstddef>
#include <cstring>

#include "hardware/gpio.h"
#include "pico/stdlib.h"
#include "hardware/spi.h"

#include "link/spi_protocol.hpp"
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

#if DUO_SPI_DEBUG
void SpiMaster::internal_loopback(const std::uint8_t* tx, std::uint8_t* rx, std::size_t size) {
    // Loop back mode is a bit in the peripheral's own control register, so
    // this runs with nothing connected to anything. It answers the question
    // the wiring keeps getting blamed for: does this SPI block transfer bytes
    // at all, in the format we asked for?
    spi_init(kSpi, kSpiBaudRate);
    spi_set_format(kSpi, 8, SPI_CPOL_0, SPI_CPHA_0, SPI_MSB_FIRST);

    // The enable bit is cleared around the change: PL022 control bits are not
    // meant to be rewritten underneath a running peripheral.
    hw_clear_bits(&spi_get_hw(kSpi)->cr1, SPI_SSPCR1_SSE_BITS);
    hw_set_bits(&spi_get_hw(kSpi)->cr1, SPI_SSPCR1_LBM_BITS);
    hw_set_bits(&spi_get_hw(kSpi)->cr1, SPI_SSPCR1_SSE_BITS);

    spi_write_read_blocking(kSpi, tx, rx, size);

    hw_clear_bits(&spi_get_hw(kSpi)->cr1, SPI_SSPCR1_SSE_BITS);
    hw_clear_bits(&spi_get_hw(kSpi)->cr1, SPI_SSPCR1_LBM_BITS);
    hw_set_bits(&spi_get_hw(kSpi)->cr1, SPI_SSPCR1_SSE_BITS);
}

std::uint8_t SpiMaster::wire_walk() {
    // Every wire in one measurement, without touching any of them.
    //
    // U2 drives its outgoing line with the parity of the three it receives.
    // So U1 drives all eight combinations of its three outgoing lines and
    // reads back what should be their parity. A line that is broken stops
    // contributing, and which bits stop changing says which line it is - a
    // dead clock line and a dead chip-select line produce different answers,
    // where a single counter produces the same silence for both.
    const unsigned outputs[3] = {kPinSpiCs, kPinSpiSck, kPinSpiTx};
    for (unsigned pin : outputs) {
        gpio_init(pin);
        gpio_set_dir(pin, GPIO_OUT);
    }
    gpio_init(kPinSpiRx);
    gpio_set_dir(kPinSpiRx, GPIO_IN);
    // So that a wire nobody is driving reads zero every time rather than
    // whatever the air happens to induce. U2 drives push-pull and wins.
    gpio_pull_down(kPinSpiRx);

    std::uint8_t observed = 0;
    for (unsigned combination = 0; combination < 8; ++combination) {
        for (unsigned bit = 0; bit < 3; ++bit) {
            gpio_put(outputs[bit], (combination >> bit) & 1u);
        }
        // Long enough for U2's mirror loop to come round, which is immediate
        // by comparison, and for the lines to settle.
        sleep_us(500);
        if (gpio_get(kPinSpiRx)) {
            observed |= static_cast<std::uint8_t>(1u << combination);
        }
    }

    for (unsigned pin : outputs) {
        gpio_put(pin, 0);
    }
    gpio_disable_pulls(kPinSpiRx);
    return observed;
}

#endif

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
    ++frames_sent_;
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
    if (!link::is_endpoint_reply(result.spi.type)) {
        // A frame U1 could have sent is one U1 did send, returned by a fault
        // on the wires. It carries a CRC U1 computed itself, so the CRC cannot
        // catch it - only the type can. Counting it as an answer would report
        // a healthy link to a board that is not running.
        status_.answered = false;
        ++status_.echoed_frames;
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
