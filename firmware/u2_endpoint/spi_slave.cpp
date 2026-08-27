#include "spi_slave.hpp"

#include <cstring>

#include "hardware/dma.h"
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

    rx_channel_ = dma_claim_unused_channel(true);
    tx_channel_ = dma_claim_unused_channel(true);

    prime();
    arm();
}

void SpiSlave::set_status(const Status& status) {
    status_ = status;
}

void SpiSlave::prime() {
    // What U2 will say during the *next* transfer, built before that transfer
    // starts. A slave cannot answer within the frame that asks: whatever is
    // already in its transmit path is what goes out while the master's bytes
    // come in. Deciding what to send once the clock is running means sending
    // whatever happened to be there.
    protocol::SpiFrame frame;
    frame.type = protocol::SpiMessageType::ENDPOINT_STATUS;
    frame.sequence = reply_sequence_++;
    const std::uint8_t payload[4] = {
        static_cast<std::uint8_t>(status_.mounted ? 1 : 0),
        status_.drops,
        static_cast<std::uint8_t>(status_.last_release_ms & 0xFF),
        static_cast<std::uint8_t>(status_.last_release_ms >> 8),
    };
    frame.payload = protocol::ByteView{payload, sizeof(payload)};

    std::size_t written = 0;
    std::memset(tx_, 0, sizeof(tx_));
    protocol::encode_spi_frame(frame, protocol::MutableByteView{tx_, sizeof(tx_)}, written);

#if DUO_SPI_DEBUG
    // Bring-up only: a counting pattern instead of a frame. What the far end
    // reads then says exactly how many bytes crossed and in what order, which
    // separates a transport fault from a framing one. An encoded frame cannot
    // do that - it either decodes or it does not.
    for (std::size_t index = 0; index < sizeof(tx_); ++index) {
        tx_[index] = static_cast<std::uint8_t>(index);
    }
#endif
}

void SpiSlave::arm() {
    // Both directions are armed before the master clocks anything, and each
    // moves exactly one frame. That is what keeps the two ends in step: a
    // transfer completes when 64 bytes have moved and not before.
    dma_channel_config rx = dma_channel_get_default_config(rx_channel_);
    channel_config_set_transfer_data_size(&rx, DMA_SIZE_8);
    channel_config_set_dreq(&rx, spi_get_dreq(kSpi, false));
    channel_config_set_read_increment(&rx, false);
    channel_config_set_write_increment(&rx, true);
    dma_channel_configure(rx_channel_, &rx, rx_, &spi_get_hw(kSpi)->dr, kFrameSize, false);

    dma_channel_config tx = dma_channel_get_default_config(tx_channel_);
    channel_config_set_transfer_data_size(&tx, DMA_SIZE_8);
    channel_config_set_dreq(&tx, spi_get_dreq(kSpi, true));
    channel_config_set_read_increment(&tx, true);
    channel_config_set_write_increment(&tx, false);
    dma_channel_configure(tx_channel_, &tx, &spi_get_hw(kSpi)->dr, tx_, kFrameSize, false);

    dma_start_channel_mask((1u << rx_channel_) | (1u << tx_channel_));
}

void SpiSlave::rearm() {
    dma_channel_abort(rx_channel_);
    dma_channel_abort(tx_channel_);

    // The transmit queue has to be genuinely emptied, and only a reset of the
    // block does that.
    //
    // Bytes are handed to that queue ahead of the clock, so when a transfer
    // ends there are still some in it that never went out. They are not
    // discarded by aborting the transfer and not by disabling the block -
    // they simply wait, and then lead the next frame. That shifts it by
    // however many were left, which puts its checksum past the end of the
    // master's 64-byte window, so the frame can never verify - and because
    // each round leaves the same remainder, the shift repeats forever.
    //
    // On the bench this looked like a link that carried perfect data and
    // rejected all of it: U2's answer arrived complete and correct, three
    // bytes late, every single time.
    //
    // spi_init resets the block, which is the point of calling it here.
    spi_init(kSpi, kSpiBaudRate);
    spi_set_slave(kSpi, true);
    spi_set_format(kSpi, 8, SPI_CPOL_0, SPI_CPHA_0, SPI_MSB_FIRST);

    arm();
}

bool SpiSlave::take_valid_frame(std::uint32_t now_ms, ValidFrame& frame) {
    const std::uint32_t remaining = dma_channel_hw_addr(rx_channel_)->transfer_count;

    if (remaining != 0) {
        // Still arriving - or stopped part way, which only time can tell us.
        //
        // Not the chip select line: the SPI block raises that between every
        // byte of a transfer, so reading it as a frame boundary abandons every
        // frame after its first byte. That is what this did, and it received
        // nothing at all until the bench said so.
        if (resync_.update(now_ms, remaining, kFrameSize)) {
            ++partial_frames_;
            prime();
            rearm();
        }
        return false;
    }

    ++frames_;
    std::memcpy(decoded_bytes_, rx_, kFrameSize);
    prime();
    rearm();

    if (!protocol::decode_spi_frame(protocol::ByteView{decoded_bytes_, kFrameSize}, decoded_)) {
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
