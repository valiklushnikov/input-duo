#include "spi_slave.hpp"

#include <cstring>

#include "hardware/gpio.h"
#include "hardware/spi.h"

namespace duo_input::u2 {
namespace {

spi_inst_t* const kSpi = spi1;

}  // namespace

void SpiSlave::begin() {
    spi_init(kSpi, kSpiBaudRate);
    spi_set_slave(kSpi, true);
    spi_set_format(kSpi, 8, SPI_CPOL_0, SPI_CPHA_0, SPI_MSB_FIRST);

    gpio_set_function(kPinSpiRx, GPIO_FUNC_SPI);
    gpio_set_function(kPinSpiCs, GPIO_FUNC_SPI);
    gpio_set_function(kPinSpiSck, GPIO_FUNC_SPI);
    gpio_set_function(kPinSpiTx, GPIO_FUNC_SPI);

    prime();
}

void SpiSlave::set_status(bool mounted) {
    mounted_ = mounted;
}

void SpiSlave::prime() {
    // What U2 will say during the next transfer, ready before it starts. A
    // slave that decides what to send once the clock is already running is a
    // slave that sends the previous buffer.
    protocol::SpiFrame frame;
    frame.type = protocol::SpiMessageType::ENDPOINT_STATUS;
    frame.sequence = reply_sequence_++;
    const std::uint8_t payload[1] = {static_cast<std::uint8_t>(mounted_ ? 1 : 0)};
    frame.payload = protocol::ByteView{payload, sizeof(payload)};

    std::size_t written = 0;
    std::memset(tx_, 0, sizeof(tx_));
    protocol::encode_spi_frame(frame, protocol::MutableByteView{tx_, sizeof(tx_)}, written);
}

bool SpiSlave::take_valid_frame(ValidFrame& frame) {
    if (!spi_is_readable(kSpi)) {
        return false;
    }

    // One whole frame or nothing. A partial transfer is not a short message,
    // it is a message that has not finished arriving.
    // One whole frame in both directions. spi_write_read_blocking waits for
    // the master's clock, which is what makes a slave a slave.
    spi_write_read_blocking(kSpi, tx_, rx_, kFrameSize);
    ++frames_;
    prime();

    if (!protocol::decode_spi_frame(protocol::ByteView{rx_, kFrameSize}, decoded_)) {
        // Damaged. It produces no report of any kind - a wrong keystroke on
        // someone's computer is worse than a missing one.
        ++crc_errors_;
        return false;
    }

    frame.type = decoded_.spi.type;
    frame.sequence = decoded_.spi.sequence;
    frame.payload = decoded_.spi.payload.data;
    frame.payload_size = decoded_.spi.payload.size;
    return true;
}

}  // namespace duo_input::u2
