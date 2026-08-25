#pragma once

#include "protocol/bytes.hpp"

#include <cstdint>

namespace duo_input::protocol {

std::uint16_t crc16_ccitt(ByteView input);
std::uint32_t crc32_ieee(ByteView input);

}  // namespace duo_input::protocol
