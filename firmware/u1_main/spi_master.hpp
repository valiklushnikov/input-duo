#pragma once

// U1's half of the link to U2.
//
// U1 is the master: it decides when a transfer happens, so U2 never has to
// guess. Every transfer is exactly one 64-byte frame, which means the slave
// always knows how much to expect and a truncated transfer is visibly wrong
// rather than silently short.
//
// A frame is sent when the state changed, and a heartbeat goes out whenever
// nothing has been sent for a while. That second part is what keeps U2's
// watchdog alive during a pause in typing - without it, a quiet minute would
// look exactly like a severed cable.

#include <cstdint>

#include "hid/types.hpp"
#include "link/spi_protocol.hpp"
#include "protocol/generated.hpp"

namespace duo_input::u1 {

/// The four wires, as they are laid out on the board.
inline constexpr unsigned kPinSpiRx = 8;    // MISO, from U2
inline constexpr unsigned kPinSpiCs = 9;    // chip select, driven by U1
inline constexpr unsigned kPinSpiSck = 10;  // clock, driven by U1
inline constexpr unsigned kPinSpiTx = 11;   // MOSI, to U2

/// 1 MHz. A 64-byte frame therefore occupies 512 us of the bus, which is why
/// frames are sent on change rather than continuously.
inline constexpr unsigned kSpiBaudRate = 1'000'000;

/// Every transfer is this long, in both directions.
inline constexpr std::size_t kFrameSize = protocol::ProtocolLimits::SPI_FRAME_SIZE;

/// Send a heartbeat if nothing else has gone out for this long. Comfortably
/// inside U2's 100 ms timeout, with room for several to be lost in a row.
inline constexpr std::uint32_t kHeartbeatIntervalMs = 20;

/// What U2 said about itself in the last transfer.
struct EndpointStatus {
    bool answered = false;
    bool mounted = false;
    std::uint32_t crc_errors = 0;
};

class SpiMaster {
public:
    /// Claim SPI1 and the four pins. Call once.
    void begin();

    /// Send whatever PC2 needs to know, if anything.
    ///
    /// Returns whether a frame went out. Called every loop; it sends only when
    /// the state changed or the heartbeat is due, so an idle device is quiet.
    bool poll(std::uint32_t now_ms, const hid::TargetSnapshot& pc2);

    /// Tell U2 to let go of everything, now.
    ///
    /// Used on reset paths and by STOP AND RELEASE ALL. It is sent
    /// unconditionally rather than folded into the ordinary state frame,
    /// because "release everything" must not wait for a change to notice.
    bool send_release_all(std::uint32_t now_ms);

    const EndpointStatus& status() const { return status_; }

private:
    bool send(protocol::SpiMessageType type, protocol::ByteView payload,
              std::uint32_t now_ms);
    void consume_reply(const std::uint8_t* reply);

    std::uint16_t sequence_ = 0;
    std::uint32_t last_sent_ms_ = 0;
    bool ever_sent_ = false;

    hid::KeyboardSnapshot last_keyboard_{};
    std::uint8_t last_buttons_ = 0;
    bool keyboard_valid_ = false;

    EndpointStatus status_{};
    link::SequenceTracker replies_;

    std::uint8_t tx_[kFrameSize] = {};
    std::uint8_t rx_[kFrameSize] = {};
};

}  // namespace duo_input::u1
