// SpiMaster's wire half, without the SPI block.
//
// This is the one member firmware/u1_main/spi_master.cpp defines against the
// Pico SDK that poll() needs. Everything poll() itself decides - what changed,
// what to encode, when the movement is consumed - is compiled from the
// firmware header exactly as it ships.

#include "fakes/spi_link.hpp"

#include <cstring>

#include "spi_master.hpp"

namespace duo::test {

SpiLink& spi_link() {
    static SpiLink link;
    return link;
}

}  // namespace duo::test

namespace duo_input::u1 {

bool SpiMaster::send(protocol::SpiMessageType type, protocol::ByteView payload,
                     std::uint32_t now_ms) {
    duo::test::SpiLink& link = duo::test::spi_link();
    if (!link.accept) {
        return false;
    }
    if (link.count < duo::test::SpiLink::kMaxFrames) {
        const std::size_t keep = payload.size < duo::test::SpiLink::kMaxPayload
                                     ? payload.size
                                     : duo::test::SpiLink::kMaxPayload;
        link.type[link.count] = type;
        link.payload_size[link.count] = keep;
        if (keep != 0 && payload.data != nullptr) {
            std::memcpy(link.payload[link.count], payload.data, keep);
        }
        ++link.count;
    }
    ++sequence_;
    ++frames_sent_;
    last_sent_ms_ = now_ms;
    ever_sent_ = true;
    return true;
}

}  // namespace duo_input::u1
