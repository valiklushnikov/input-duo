#pragma once

// When a half-arrived frame should be given up on.
//
// The slave has no way to ask for a retransmission, so it has to decide by
// itself that what it is holding will never be finished. Until it does, the
// bytes of the next frame land where the last one stopped and every frame
// after that is wrong.
//
// The obvious signal - the chip select line going idle - is not one. The SPI
// block raises it between every byte of a transfer, so a slave that reads it
// as a frame boundary abandons every frame after its first byte and receives
// nothing at all, which is exactly what this device did on the bench.
//
// Time says it without ambiguity. A 64-byte frame at 1 MHz crosses in about
// half a millisecond, and the master sends at least every 20 ms, so a gap in
// the middle of a frame is nothing else.

#include <cstdint>

namespace duo_input::u2 {

/// How long a frame may sit unchanged before it is abandoned.
inline constexpr std::uint32_t kResyncStallMs = 5;

class FrameResync {
public:
    /// Should the transfer be restarted?
    ///
    /// ``remaining`` is how many bytes the transfer still expects. Call this
    /// on every pass of the loop; it reports a stall once, not until it is
    /// cleared, because the caller restarts the transfer on being told.
    bool update(std::uint32_t now_ms, std::uint32_t remaining, std::uint32_t frame_size);

private:
    std::uint32_t last_remaining_ = 0;
    std::uint32_t last_change_ms_ = 0;
    bool watching_ = false;
};

}  // namespace duo_input::u2
