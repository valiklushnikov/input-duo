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

void SpiMaster::bitbang_probe(std::uint8_t* rx, std::size_t count, unsigned half_period_us) {
    gpio_init(kPinSpiCs);
    gpio_set_dir(kPinSpiCs, GPIO_OUT);
    gpio_put(kPinSpiCs, 1);
    gpio_init(kPinSpiSck);
    gpio_set_dir(kPinSpiSck, GPIO_OUT);
    gpio_put(kPinSpiSck, 0);
    gpio_init(kPinSpiTx);
    gpio_set_dir(kPinSpiTx, GPIO_OUT);
    gpio_put(kPinSpiTx, 0);
    gpio_init(kPinSpiRx);
    gpio_set_dir(kPinSpiRx, GPIO_IN);
    sleep_ms(2);

    for (std::size_t index = 0; index < count; ++index) {
        // Chip select is raised between bytes because that is what the SPI
        // block does, and a slave in this mode expects each byte framed.
        const std::uint8_t outgoing = static_cast<std::uint8_t>(0xA0 | index);
        std::uint8_t incoming = 0;

        gpio_put(kPinSpiCs, 0);
        sleep_us(half_period_us + 1);
        for (int bit = 7; bit >= 0; --bit) {
            gpio_put(kPinSpiTx, (outgoing >> bit) & 1u);
            sleep_us(half_period_us);
            gpio_put(kPinSpiSck, 1);
            sleep_us(half_period_us);
            incoming = static_cast<std::uint8_t>((incoming << 1) | (gpio_get(kPinSpiRx) ? 1u : 0u));
            gpio_put(kPinSpiSck, 0);
            sleep_us(half_period_us);
        }
        gpio_put(kPinSpiCs, 1);
        sleep_us(half_period_us * 4 + 1);

        rx[index] = incoming;
    }
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

    // Chip select belongs to the SPI block, not to this code.
    //
    // Holding it down for a whole 64-byte transfer looks tidier - one
    // assertion, one frame - and it does not work. In this frame format the
    // slave takes that signal as the boundary of a single byte, so a select
    // that never rises means a slave that shifts one byte and then waits
    // forever for the next frame that never begins. Driven by hand this way,
    // the link was silent in both directions while every part of it was
    // working: U1's SPI block, U2's slave and all four wires each tested good
    // on their own.
    //
    // The frame boundary comes from elsewhere instead. U1 sends 64 bytes and
    // then idles for milliseconds, which is a gap U2 can see, and U2's resync
    // is written against exactly that.
    gpio_set_function(kPinSpiCs, GPIO_FUNC_SPI);
}

#ifdef DUO_INPUT_BACKEND_PIO_USB
void SpiMaster::refresh_baudrate() { spi_set_baudrate(kSpi, kSpiBaudRate); }
#endif

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

    spi_write_read_blocking(kSpi, tx_, rx_, kFrameSize);

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

    apply_reply(result.spi);
}

bool SpiMaster::send_release_all(std::uint32_t now_ms) {
    // Deliberately unconditional: "let go of everything" must not wait for a
    // change to be noticed, and it is the one message worth sending twice.
    keyboard_valid_ = false;
    last_buttons_ = 0;
    return send(protocol::SpiMessageType::CONTROL_RELEASE_ALL,
                protocol::ByteView{nullptr, 0}, now_ms);
}

}  // namespace duo_input::u1
