#include "protocol/crc.hpp"

namespace duo_input::protocol {

std::uint16_t crc16_ccitt(ByteView input) {
    std::uint16_t crc = 0xFFFF;
    for (std::size_t index = 0; index < input.size; ++index) {
        crc ^= static_cast<std::uint16_t>(input.data[index]) << 8U;
        for (int bit = 0; bit < 8; ++bit) {
            crc = (crc & 0x8000U) != 0 ? static_cast<std::uint16_t>((crc << 1U) ^ 0x1021U)
                                       : static_cast<std::uint16_t>(crc << 1U);
        }
    }
    return crc;
}

std::uint32_t crc32_ieee(ByteView input) {
    std::uint32_t crc = 0xFFFFFFFFU;
    for (std::size_t index = 0; index < input.size; ++index) {
        crc ^= input.data[index];
        for (int bit = 0; bit < 8; ++bit) {
            crc = (crc & 1U) != 0 ? (crc >> 1U) ^ 0xEDB88320U : crc >> 1U;
        }
    }
    return crc ^ 0xFFFFFFFFU;
}

}  // namespace duo_input::protocol
