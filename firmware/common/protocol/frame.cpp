#include "protocol/frame.hpp"

#include "protocol/cobs.hpp"
#include "protocol/crc.hpp"

#include <cstddef>
#include <cstdint>
#include <limits>

namespace duo_input::protocol {

namespace {

constexpr std::size_t CDC_HEADER_SIZE = 10;
constexpr std::size_t CDC_CRC_SIZE = 4;
constexpr std::size_t CDC_MIN_RAW_SIZE = CDC_HEADER_SIZE + CDC_CRC_SIZE;
constexpr std::size_t SPI_HEADER_SIZE = 10;
constexpr std::size_t SPI_CRC_OFFSET = ProtocolLimits::SPI_FRAME_SIZE - 2;
constexpr std::size_t SPI_PAYLOAD_MAX = SPI_CRC_OFFSET - SPI_HEADER_SIZE;

bool has_valid_data(ByteView view) {
    return view.size == 0 || view.data != nullptr;
}

bool has_valid_data(MutableByteView view) {
    return view.size == 0 || view.data != nullptr;
}

bool cobs_decoded_size(ByteView input, std::size_t& decoded_size) {
    if (!has_valid_data(input) || input.size == 0) {
        return false;
    }

    std::size_t read_index = 0;
    decoded_size = 0;
    while (read_index < input.size) {
        const std::uint8_t code = input.data[read_index++];
        if (code == 0) {
            return false;
        }

        const std::size_t block_size = static_cast<std::size_t>(code - 1U);
        if (block_size > input.size - read_index ||
            decoded_size > std::numeric_limits<std::size_t>::max() - block_size) {
            return false;
        }
        read_index += block_size;
        decoded_size += block_size;
        if (read_index < input.size && code != 0xFFU) {
            if (decoded_size == std::numeric_limits<std::size_t>::max()) {
                return false;
            }
            ++decoded_size;
        }
    }
    return true;
}

bool fail(DecodeResult& result, FrameError error) {
    result.success = false;
    result.error = error;
    return false;
}

std::uint16_t read_u16(const std::uint8_t* bytes) {
    return static_cast<std::uint16_t>(bytes[0]) |
           static_cast<std::uint16_t>(static_cast<std::uint16_t>(bytes[1]) << 8U);
}

std::uint32_t read_u32(const std::uint8_t* bytes) {
    return static_cast<std::uint32_t>(bytes[0]) |
           (static_cast<std::uint32_t>(bytes[1]) << 8U) |
           (static_cast<std::uint32_t>(bytes[2]) << 16U) |
           (static_cast<std::uint32_t>(bytes[3]) << 24U);
}

void write_u16(std::uint8_t* bytes, std::uint16_t value) {
    bytes[0] = static_cast<std::uint8_t>(value & 0xFFU);
    bytes[1] = static_cast<std::uint8_t>(value >> 8U);
}

void write_u32(std::uint8_t* bytes, std::uint32_t value) {
    bytes[0] = static_cast<std::uint8_t>(value & 0xFFU);
    bytes[1] = static_cast<std::uint8_t>((value >> 8U) & 0xFFU);
    bytes[2] = static_cast<std::uint8_t>((value >> 16U) & 0xFFU);
    bytes[3] = static_cast<std::uint8_t>((value >> 24U) & 0xFFU);
}

bool is_known(CdcMessageType type) {
    switch (type) {
        case CdcMessageType::CAPTURE_BEGIN:
        case CdcMessageType::CAPTURE_END:
        case CdcMessageType::CAPTURE_EVENT:
        case CdcMessageType::DEVICE_INFO:
        case CdcMessageType::FACTORY_RESET_ARM:
        case CdcMessageType::FACTORY_RESET_COMMIT:
        case CdcMessageType::GET_ACTIVE_CONFIG_INFO:
        case CdcMessageType::GET_DIAGNOSTICS:
        case CdcMessageType::GET_HID_DESCRIPTOR_CAPTURE:
        case CdcMessageType::GET_STATUS:
        case CdcMessageType::HELLO:
        case CdcMessageType::PING:
        case CdcMessageType::READ_CONFIG_BEGIN:
        case CdcMessageType::READ_CONFIG_CHUNK:
        case CdcMessageType::SET_ACTIVE_PROFILE:
        case CdcMessageType::STOP_AND_RELEASE_ALL:
        case CdcMessageType::TEST_MACRO:
        case CdcMessageType::WRITE_ABORT:
        case CdcMessageType::WRITE_BEGIN:
        case CdcMessageType::WRITE_CHUNK:
        case CdcMessageType::WRITE_COMMIT:
        case CdcMessageType::WRITE_VERIFY:
            return true;
    }
    return false;
}

bool is_known(SpiMessageType type) {
    switch (type) {
        case SpiMessageType::CONSUMER_STATE:
        case SpiMessageType::CONTROL_RELEASE_ALL:
        case SpiMessageType::ENDPOINT_STATUS:
        case SpiMessageType::HANDSHAKE:
        case SpiMessageType::HEARTBEAT:
        case SpiMessageType::KBD_STATE:
        case SpiMessageType::MOUSE_DELTA:
            return true;
    }
    return false;
}

bool validate_cdc_frame(const CdcFrame& frame) {
    return has_valid_data(frame.payload) && frame.flags == 0 && is_known(frame.type) &&
           frame.payload.size <= ProtocolLimits::CDC_MAX_PAYLOAD;
}

bool validate_spi_frame(const SpiFrame& frame) {
    return has_valid_data(frame.payload) && frame.flags == 0 && is_known(frame.type) &&
           frame.payload.size <= SPI_PAYLOAD_MAX;
}

}  // namespace

bool is_minor_compatible(std::uint8_t minor, std::uint32_t required_capabilities,
                         std::uint32_t supported_capabilities) {
    return minor == PROTOCOL_VERSION_MINOR ||
           (required_capabilities & ~supported_capabilities) == 0;
}

bool encode_cdc_frame(const CdcFrame& frame, MutableByteView output, MutableByteView scratch,
                      std::size_t& output_size) {
    if (!validate_cdc_frame(frame) || !has_valid_data(output) || !has_valid_data(scratch)) {
        return false;
    }

    const std::size_t raw_size = CDC_MIN_RAW_SIZE + frame.payload.size;
    if (scratch.size < raw_size || output.size == 0) {
        return false;
    }

    scratch.data[0] = 'D';
    scratch.data[1] = 'I';
    scratch.data[2] = PROTOCOL_VERSION_MAJOR;
    scratch.data[3] = frame.minor;
    scratch.data[4] = static_cast<std::uint8_t>(frame.type);
    scratch.data[5] = frame.flags;
    write_u16(scratch.data + 6, frame.sequence);
    write_u16(scratch.data + 8, static_cast<std::uint16_t>(frame.payload.size));
    for (std::size_t index = 0; index < frame.payload.size; ++index) {
        scratch.data[CDC_HEADER_SIZE + index] = frame.payload.data[index];
    }
    write_u32(scratch.data + CDC_HEADER_SIZE + frame.payload.size,
              crc32_ieee({scratch.data, CDC_HEADER_SIZE + frame.payload.size}));

    std::size_t cobs_size = 0;
    if (!cobs_encode({scratch.data, raw_size}, {output.data, output.size - 1U}, cobs_size)) {
        return false;
    }
    output.data[cobs_size] = 0;
    output_size = cobs_size + 1U;
    return true;
}

bool decode_cdc_frame(ByteView transport, MutableByteView scratch, DecodeResult& result,
                      std::uint32_t required_capabilities, std::uint32_t supported_capabilities) {
    if (!has_valid_data(transport) || !has_valid_data(scratch) || transport.size == 0 ||
        transport.data[transport.size - 1U] != 0) {
        return fail(result, FrameError::INVALID_INPUT);
    }
    for (std::size_t index = 0; index + 1U < transport.size; ++index) {
        if (transport.data[index] == 0) {
            return fail(result, FrameError::INVALID_INPUT);
        }
    }

    const ByteView encoded{transport.data, transport.size - 1U};
    std::size_t raw_size = 0;
    if (!cobs_decoded_size(encoded, raw_size)) {
        return fail(result, FrameError::INVALID_INPUT);
    }
    if (scratch.size < raw_size) {
        return fail(result, FrameError::INSUFFICIENT_CAPACITY);
    }
    if (!cobs_decode(encoded, scratch, raw_size)) {
        return fail(result, FrameError::INVALID_INPUT);
    }
    if (raw_size < CDC_MIN_RAW_SIZE) {
        return fail(result, FrameError::INVALID_LENGTH);
    }
    if (scratch.data[0] != 'D' || scratch.data[1] != 'I') {
        return fail(result, FrameError::INVALID_MAGIC);
    }
    if (scratch.data[2] != PROTOCOL_VERSION_MAJOR) {
        return fail(result, FrameError::INCOMPATIBLE_MAJOR);
    }
    if (!is_minor_compatible(scratch.data[3], required_capabilities, supported_capabilities)) {
        return fail(result, FrameError::INCOMPATIBLE_MINOR);
    }
    const auto type = static_cast<CdcMessageType>(scratch.data[4]);
    if (!is_known(type)) {
        return fail(result, FrameError::INVALID_TYPE);
    }
    if (scratch.data[5] != 0) {
        return fail(result, FrameError::INVALID_FLAGS);
    }
    const std::size_t payload_size = read_u16(scratch.data + 8);
    if (payload_size > ProtocolLimits::CDC_MAX_PAYLOAD ||
        raw_size != CDC_MIN_RAW_SIZE + payload_size) {
        return fail(result, FrameError::INVALID_LENGTH);
    }
    if (read_u32(scratch.data + CDC_HEADER_SIZE + payload_size) !=
        crc32_ieee({scratch.data, CDC_HEADER_SIZE + payload_size})) {
        return fail(result, FrameError::INVALID_CRC);
    }

    result.success = true;
    result.error = FrameError::NONE;
    result.cdc = {scratch.data[3], type, scratch.data[5], read_u16(scratch.data + 6),
                  {scratch.data + CDC_HEADER_SIZE, payload_size}};
    return true;
}

bool encode_spi_frame(const SpiFrame& frame, MutableByteView output, std::size_t& output_size) {
    if (!validate_spi_frame(frame) || !has_valid_data(output) ||
        output.size < ProtocolLimits::SPI_FRAME_SIZE) {
        return false;
    }

    output.data[0] = 'D';
    output.data[1] = 'S';
    output.data[2] = PROTOCOL_VERSION_MAJOR;
    output.data[3] = frame.minor;
    output.data[4] = static_cast<std::uint8_t>(frame.type);
    output.data[5] = frame.flags;
    write_u16(output.data + 6, frame.sequence);
    write_u16(output.data + 8, static_cast<std::uint16_t>(frame.payload.size));
    for (std::size_t index = 0; index < frame.payload.size; ++index) {
        output.data[SPI_HEADER_SIZE + index] = frame.payload.data[index];
    }
    for (std::size_t index = SPI_HEADER_SIZE + frame.payload.size; index < SPI_CRC_OFFSET; ++index) {
        output.data[index] = 0;
    }
    write_u16(output.data + SPI_CRC_OFFSET, crc16_ccitt({output.data, SPI_CRC_OFFSET}));
    output_size = ProtocolLimits::SPI_FRAME_SIZE;
    return true;
}

bool decode_spi_frame(ByteView transport, DecodeResult& result, std::uint32_t required_capabilities,
                      std::uint32_t supported_capabilities) {
    if (!has_valid_data(transport) || transport.size != ProtocolLimits::SPI_FRAME_SIZE) {
        return fail(result, FrameError::INVALID_INPUT);
    }
    if (transport.data[0] != 'D' || transport.data[1] != 'S') {
        return fail(result, FrameError::INVALID_MAGIC);
    }
    if (transport.data[2] != PROTOCOL_VERSION_MAJOR) {
        return fail(result, FrameError::INCOMPATIBLE_MAJOR);
    }
    if (!is_minor_compatible(transport.data[3], required_capabilities, supported_capabilities)) {
        return fail(result, FrameError::INCOMPATIBLE_MINOR);
    }
    const auto type = static_cast<SpiMessageType>(transport.data[4]);
    if (!is_known(type)) {
        return fail(result, FrameError::INVALID_TYPE);
    }
    if (transport.data[5] != 0) {
        return fail(result, FrameError::INVALID_FLAGS);
    }
    const std::size_t payload_size = read_u16(transport.data + 8);
    if (payload_size > SPI_PAYLOAD_MAX) {
        return fail(result, FrameError::INVALID_LENGTH);
    }
    for (std::size_t index = SPI_HEADER_SIZE + payload_size; index < SPI_CRC_OFFSET; ++index) {
        if (transport.data[index] != 0) {
            return fail(result, FrameError::INVALID_PADDING);
        }
    }
    if (read_u16(transport.data + SPI_CRC_OFFSET) != crc16_ccitt({transport.data, SPI_CRC_OFFSET})) {
        return fail(result, FrameError::INVALID_CRC);
    }

    result.success = true;
    result.error = FrameError::NONE;
    result.spi = {transport.data[3], type, transport.data[5], read_u16(transport.data + 6),
                  {transport.data + SPI_HEADER_SIZE, payload_size}};
    return true;
}

}  // namespace duo_input::protocol
