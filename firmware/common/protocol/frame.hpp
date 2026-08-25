#pragma once

#include "protocol/bytes.hpp"
#include "protocol/generated.hpp"

#include <cstddef>
#include <cstdint>

namespace duo_input::protocol {

enum class FrameError : std::uint8_t {
    NONE,
    INVALID_INPUT,
    INVALID_MAGIC,
    INCOMPATIBLE_MAJOR,
    INCOMPATIBLE_MINOR,
    INVALID_TYPE,
    INVALID_FLAGS,
    INVALID_LENGTH,
    INVALID_CRC,
    INVALID_PADDING,
    INSUFFICIENT_CAPACITY,
};

struct CdcFrame {
    std::uint8_t minor = PROTOCOL_VERSION_MINOR;
    CdcMessageType type = CdcMessageType::HELLO;
    std::uint8_t flags = 0;
    std::uint16_t sequence = 0;
    ByteView payload{nullptr, 0};
};

struct SpiFrame {
    std::uint8_t minor = PROTOCOL_VERSION_MINOR;
    SpiMessageType type = SpiMessageType::HANDSHAKE;
    std::uint8_t flags = 0;
    std::uint16_t sequence = 0;
    ByteView payload{nullptr, 0};
};

struct DecodeResult {
    bool success = false;
    FrameError error = FrameError::INVALID_INPUT;
    CdcFrame cdc{};
    SpiFrame spi{};
};

// A matching minor is always compatible. A different minor requires every
// required capability to be present in the locally supported capability mask.
bool is_minor_compatible(std::uint8_t minor, std::uint16_t required_capabilities = 0,
                         std::uint16_t supported_capabilities = 0);

// CDC encoding and decoding are allocation-free. The caller owns both output
// buffers. On successful CDC decode, result.cdc.payload points into scratch;
// it is valid only while scratch remains alive and unchanged.
bool encode_cdc_frame(const CdcFrame& frame, MutableByteView output, MutableByteView scratch,
                      std::size_t& output_size);
bool decode_cdc_frame(ByteView transport, MutableByteView scratch, DecodeResult& result,
                      std::uint16_t required_capabilities = 0,
                      std::uint16_t supported_capabilities = 0);

// SPI encoding is allocation-free. On successful SPI decode, result.spi.payload
// points into transport; it is valid only while transport remains alive and unchanged.
bool encode_spi_frame(const SpiFrame& frame, MutableByteView output, std::size_t& output_size);
bool decode_spi_frame(ByteView transport, DecodeResult& result,
                      std::uint16_t required_capabilities = 0,
                      std::uint16_t supported_capabilities = 0);

}  // namespace duo_input::protocol
