#pragma once

// U2's half of the link.
//
// U2 is the slave: it does not choose when a transfer happens. Both directions
// are therefore driven by DMA armed *before* the master clocks anything. That
// is not an optimisation - it is the only way a slave can work. Whatever is
// already in the transmit path is what goes out while the master's bytes come
// in, so deciding what to send once the clock is running means sending
// whatever happened to be there; and noticing that bytes have arrived and only
// then starting a 64-byte transfer means waiting for 64 more clocks that are
// never coming.
//
// The CRC is checked in loop context - not in an interrupt, where a slow check
// would delay the next transfer, and not on the far side, where a damaged
// frame would already have become a keystroke.
//
// A frame that fails its check produces nothing at all. Not a partial report,
// not a best guess: a wrong keystroke on someone's computer is worse than a
// missing one, and the operator can retype a missing one.

#include <cstdint>

#include "frame_resync.hpp"

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
    bool take_valid_frame(std::uint32_t now_ms, ValidFrame& frame);

    /// Publish what U2 will say in the next transfer.
    /// What U2 tells U1 about itself in every reply.
    struct Status {
        /// U2's own USB is up.
        bool mounted = false;
        /// How many times the link has died under U2, saturating at 255.
        std::uint8_t drops = 0;
        /// The silence that caused the most recent release, in milliseconds.
        ///
        /// This is the fail-safe reporting on itself. It cannot be observed as
        /// it happens - the link that would carry the news is the one that
        /// went quiet - so it is carried out afterwards instead.
        std::uint16_t last_release_ms = 0;
    };

    void set_status(const Status& status);

    std::uint32_t crc_errors() const { return crc_errors_; }
    std::uint32_t frames_received() const { return frames_; }

    /// Frames that stopped part way through and had to be resynchronised.
    std::uint32_t partial_frames() const { return partial_frames_; }

private:
    void prime();
    void arm();
    void rearm();

    std::uint8_t rx_[kFrameSize] = {};
    std::uint8_t tx_[kFrameSize] = {};
    /// The completed frame, copied out before the next transfer overwrites rx_.
    std::uint8_t decoded_bytes_[kFrameSize] = {};
    protocol::DecodeResult decoded_{};
    std::uint16_t reply_sequence_ = 0;
    std::uint32_t crc_errors_ = 0;
    std::uint32_t frames_ = 0;
    std::uint32_t partial_frames_ = 0;
    FrameResync resync_;
    int rx_channel_ = -1;
    int tx_channel_ = -1;
    Status status_{};
};

}  // namespace duo_input::u2
