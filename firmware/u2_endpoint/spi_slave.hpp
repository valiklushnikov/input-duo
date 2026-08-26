#pragma once

// U2's half of the link.
//
// U2 is the slave: it does not choose when a transfer happens, so it keeps a
// frame-sized buffer permanently ready and looks at it between transfers. The
// CRC is checked here, in loop context - not in an interrupt, where a slow
// check would delay the next transfer, and not on the far side, where a
// damaged frame would already have become a keystroke.
//
// A frame that fails its check produces nothing at all. Not a partial report,
// not a best guess: a wrong keystroke on someone's computer is worse than a
// missing one, and the operator can retype a missing one.

#include <cstdint>

#include "hid/types.hpp"
#include "link/spi_protocol.hpp"
#include "protocol/frame.hpp"
#include "protocol/generated.hpp"

namespace duo_input::u2 {

/// The four wires, mirroring U1.
inline constexpr unsigned kPinSpiRx = 8;
inline constexpr unsigned kPinSpiCs = 9;
inline constexpr unsigned kPinSpiSck = 10;
inline constexpr unsigned kPinSpiTx = 11;

inline constexpr unsigned kSpiBaudRate = 1'000'000;
inline constexpr std::size_t kFrameSize = protocol::ProtocolLimits::SPI_FRAME_SIZE;

/// One message that arrived intact.
struct ValidFrame {
    protocol::SpiMessageType type = protocol::SpiMessageType::HEARTBEAT;
    std::uint16_t sequence = 0;
    const std::uint8_t* payload = nullptr;
    std::size_t payload_size = 0;
};

class SpiSlave {
public:
    /// Claim SPI1 as a slave and prime the first buffer.
    void begin();

    /// Is there a frame that arrived and passed its CRC?
    ///
    /// Consumes it: calling twice without a new transfer returns false the
    /// second time.
    bool take_valid_frame(ValidFrame& frame);

    /// Publish what U2 will say in the next transfer.
    void set_status(bool mounted);

    std::uint32_t crc_errors() const { return crc_errors_; }
    std::uint32_t frames_received() const { return frames_; }

private:
    void prime();

    std::uint8_t rx_[kFrameSize] = {};
    std::uint8_t tx_[kFrameSize] = {};
    protocol::DecodeResult decoded_{};
    std::uint16_t reply_sequence_ = 0;
    std::uint32_t crc_errors_ = 0;
    std::uint32_t frames_ = 0;
    bool mounted_ = false;
};

}  // namespace duo_input::u2
