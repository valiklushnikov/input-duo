#pragma once

// The link to U2, as far as U1 can tell: the frames that actually went out.
//
// Only SpiMaster::send is replaced - the one member that touches the SPI
// block. Everything poll() decides is compiled from the firmware header, so a
// test here is asking what PC2 was told, which is the only question that
// distinguishes a pointer that moves from one that stutters.

#include <cstddef>
#include <cstdint>

#include "protocol/generated.hpp"

namespace duo::test {

struct SpiLink {
    // Large enough to hold every frame a millisecond-by-millisecond test can
    // produce: the address-exchange tests poll for a simulated second under
    // continuous motion, which is roughly one frame per millisecond.
    static constexpr std::size_t kMaxFrames = 1024;
    static constexpr std::size_t kMaxPayload = 64;

    duo_input::protocol::SpiMessageType type[kMaxFrames] = {};
    std::uint8_t payload[kMaxFrames][kMaxPayload] = {};
    std::size_t payload_size[kMaxFrames] = {};
    std::size_t count = 0;

    /// The transfer failed - a CRC error, a slave that did not answer.
    bool accept = true;

    void reset() {
        count = 0;
        accept = true;
    }

    std::size_t count_of(duo_input::protocol::SpiMessageType wanted) const {
        std::size_t seen = 0;
        for (std::size_t index = 0; index < count; ++index) {
            if (type[index] == wanted) {
                ++seen;
            }
        }
        return seen;
    }
};

SpiLink& spi_link();

}  // namespace duo::test
