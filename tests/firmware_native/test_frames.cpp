#include "test_support.hpp"

#include "protocol/frame.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

namespace {

using duo_input::protocol::ByteView;

bool bytes_equal(const std::uint8_t* actual, const std::uint8_t* expected, std::size_t size) {
    for (std::size_t index = 0; index < size; ++index) {
        if (actual[index] != expected[index]) {
            return false;
        }
    }
    return true;
}

std::string vector_document() {
    std::ifstream input(DUO_FRAME_VECTORS_PATH);
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

std::string vector_value(const std::string& document, const char* section, const char* field) {
    const std::string section_marker = std::string{"\""} + section + "\": {";
    const std::size_t section_offset = document.find(section_marker);
    if (section_offset == std::string::npos) {
        return {};
    }
    const std::string field_marker = std::string{"\""} + field + "\": \"";
    const std::size_t value_offset = document.find(field_marker, section_offset);
    if (value_offset == std::string::npos) {
        return {};
    }
    const std::size_t begin = value_offset + field_marker.size();
    const std::size_t end = document.find('"', begin);
    return end == std::string::npos ? std::string{} : document.substr(begin, end - begin);
}

std::uint8_t hex_digit(char value) {
    return value >= '0' && value <= '9' ? static_cast<std::uint8_t>(value - '0')
         : value >= 'a' && value <= 'f' ? static_cast<std::uint8_t>(value - 'a' + 10)
                                        : static_cast<std::uint8_t>(value - 'A' + 10);
}

std::vector<std::uint8_t> hex_bytes(const std::string& value) {
    std::vector<std::uint8_t> bytes;
    if ((value.size() % 2U) != 0) {
        return bytes;
    }
    bytes.reserve(value.size() / 2U);
    for (std::size_t index = 0; index < value.size(); index += 2U) {
        bytes.push_back(static_cast<std::uint8_t>((hex_digit(value[index]) << 4U) |
                                                  hex_digit(value[index + 1U])));
    }
    return bytes;
}

}  // namespace

TEST_CASE(cdc_frame_matches_shared_vector_and_round_trips) {
    const std::string document = vector_document();
    const std::vector<std::uint8_t> expected = hex_bytes(vector_value(document, "cdc", "transport"));
    const std::vector<std::uint8_t> payload = hex_bytes(vector_value(document, "cdc", "payload"));
    const duo_input::protocol::CdcFrame frame{
        duo_input::protocol::PROTOCOL_VERSION_MINOR,
        duo_input::protocol::CdcMessageType::PING,
        0,
        0x1234,
        {payload.data(), payload.size()},
    };
    std::array<std::uint8_t, 1038> scratch{};
    std::array<std::uint8_t, 1045> encoded{};
    std::size_t encoded_size = 0;

    CHECK(duo_input::protocol::encode_cdc_frame(frame, {encoded.data(), encoded.size()},
                                                {scratch.data(), scratch.size()}, encoded_size));
    CHECK_EQ(encoded_size, expected.size());
    CHECK(bytes_equal(encoded.data(), expected.data(), encoded_size));

    duo_input::protocol::DecodeResult decoded{};
    CHECK(duo_input::protocol::decode_cdc_frame({encoded.data(), encoded_size},
                                                {scratch.data(), scratch.size()}, decoded));
    CHECK_EQ(decoded.cdc.type, duo_input::protocol::CdcMessageType::PING);
    CHECK_EQ(decoded.cdc.sequence, 0x1234U);
    CHECK(bytes_equal(decoded.cdc.payload.data, payload.data(), payload.size()));
}

TEST_CASE(spi_frame_matches_shared_vector_is_64_bytes_and_round_trips) {
    const std::string document = vector_document();
    const std::vector<std::uint8_t> expected = hex_bytes(vector_value(document, "spi", "frame"));
    const std::vector<std::uint8_t> payload = hex_bytes(vector_value(document, "spi", "payload"));
    const duo_input::protocol::SpiFrame frame{
        duo_input::protocol::PROTOCOL_VERSION_MINOR,
        duo_input::protocol::SpiMessageType::HEARTBEAT,
        0,
        42,
        {payload.data(), payload.size()},
    };
    std::array<std::uint8_t, 64> encoded{};
    std::size_t encoded_size = 0;

    CHECK(duo_input::protocol::encode_spi_frame(frame, {encoded.data(), encoded.size()}, encoded_size));
    CHECK_EQ(encoded_size, 64U);
    CHECK(bytes_equal(encoded.data(), expected.data(), encoded_size));

    duo_input::protocol::DecodeResult decoded{};
    CHECK(duo_input::protocol::decode_spi_frame({encoded.data(), encoded_size}, decoded));
    CHECK_EQ(decoded.spi.type, duo_input::protocol::SpiMessageType::HEARTBEAT);
    CHECK_EQ(decoded.spi.sequence, 42U);
    CHECK(bytes_equal(decoded.spi.payload.data, payload.data(), payload.size()));
}

TEST_CASE(cdc_frame_rejects_every_transport_truncation_and_crc_damage) {
    const std::vector<std::uint8_t> transport =
        hex_bytes(vector_value(vector_document(), "cdc", "transport"));
    std::array<std::uint8_t, 1038> scratch{};
    duo_input::protocol::DecodeResult result{};

    for (std::size_t size = 0; size < transport.size(); ++size) {
        CHECK_FALSE(duo_input::protocol::decode_cdc_frame({transport.data(), size},
                                                          {scratch.data(), scratch.size()}, result));
    }
    std::vector<std::uint8_t> damaged = transport;
    damaged[damaged.size() - 2U] ^= 1U;
    CHECK_FALSE(duo_input::protocol::decode_cdc_frame({damaged.data(), damaged.size()},
                                                      {scratch.data(), scratch.size()}, result));
}

TEST_CASE(frame_rejects_invalid_headers_and_spi_padding) {
    std::vector<std::uint8_t> spi = hex_bytes(vector_value(vector_document(), "spi", "frame"));
    duo_input::protocol::DecodeResult result{};

    CHECK_FALSE(duo_input::protocol::decode_spi_frame({spi.data(), spi.size() - 1U}, result));
    spi[20] = 1;
    CHECK_FALSE(duo_input::protocol::decode_spi_frame({spi.data(), spi.size()}, result));
    CHECK_FALSE(duo_input::protocol::decode_spi_frame({spi.data(), spi.size()}, result,
                                                      0U, 0U));
}

TEST_CASE(minor_compatibility_uses_capability_intersection) {
    constexpr std::uint16_t keyboard = static_cast<std::uint16_t>(
        duo_input::protocol::Capability::KEYBOARD_HID);
    constexpr std::uint16_t capture = static_cast<std::uint16_t>(duo_input::protocol::Capability::CAPTURE);

    CHECK(duo_input::protocol::is_minor_compatible(9, keyboard, keyboard));
    CHECK_FALSE(duo_input::protocol::is_minor_compatible(9, capture, keyboard));
}
