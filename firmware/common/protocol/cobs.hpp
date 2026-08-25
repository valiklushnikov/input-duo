#pragma once

#include "protocol/bytes.hpp"

#include <cstddef>

namespace duo_input::protocol {

bool cobs_encode(ByteView input, MutableByteView output, std::size_t& output_size);
bool cobs_decode(ByteView input, MutableByteView output, std::size_t& output_size);

}  // namespace duo_input::protocol
