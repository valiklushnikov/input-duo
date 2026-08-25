#pragma once

#include <cstddef>
#include <cstdint>

namespace duo_input::protocol {

struct ByteView {
    const std::uint8_t* data;
    std::size_t size;
};

struct MutableByteView {
    std::uint8_t* data;
    std::size_t size;
};

}  // namespace duo_input::protocol
