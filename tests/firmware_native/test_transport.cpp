#include "test_support.hpp"

#include "protocol/cobs.hpp"
#include "protocol/crc.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <fstream>
#include <iterator>
#include <limits>
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
    std::ifstream input(DUO_TRANSPORT_VECTORS_PATH);
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

std::string vector_value(const std::string& document, const char* name, const char* field) {
    std::string case_marker = std::string{"\"name\": \""} + name + "\"";
    if (std::string{name} == "crc") {
        case_marker = "\"crc\": {";
    }
    const std::size_t case_offset = document.find(case_marker);
    if (case_offset == std::string::npos) {
        return {};
    }

    const std::string field_marker = std::string{"\""} + field + "\": \"";
    const std::size_t value_offset = document.find(field_marker, case_offset);
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

TEST_CASE(cobs_encodes_and_decodes_empty_vector) {
    const std::string document = vector_document();
    const std::vector<std::uint8_t> raw = hex_bytes(vector_value(document, "empty", "raw"));
    const std::vector<std::uint8_t> expected =
        hex_bytes(vector_value(document, "empty", "encoded"));
    std::array<std::uint8_t, 1> encoded{};
    std::size_t encoded_size = 99;
    CHECK(duo_input::protocol::cobs_encode({raw.data(), raw.size()}, {encoded.data(), encoded.size()},
                                           encoded_size));
    CHECK_EQ(encoded_size, expected.size());
    CHECK(bytes_equal(encoded.data(), expected.data(), encoded_size));

    std::array<std::uint8_t, 1> decoded{};
    std::size_t decoded_size = 99;
    CHECK(duo_input::protocol::cobs_decode({encoded.data(), encoded_size},
                                           {decoded.data(), decoded.size()}, decoded_size));
    CHECK_EQ(decoded_size, raw.size());
}

TEST_CASE(cobs_encodes_and_decodes_embedded_zeroes_vector) {
    const std::string document = vector_document();
    const std::vector<std::uint8_t> raw =
        hex_bytes(vector_value(document, "embedded_zeroes", "raw"));
    const std::vector<std::uint8_t> expected =
        hex_bytes(vector_value(document, "embedded_zeroes", "encoded"));
    std::array<std::uint8_t, 6> encoded{};
    std::size_t encoded_size = 0;

    CHECK(duo_input::protocol::cobs_encode({raw.data(), raw.size()},
                                           {encoded.data(), encoded.size()}, encoded_size));
    CHECK_EQ(encoded_size, expected.size());
    CHECK(bytes_equal(encoded.data(), expected.data(), encoded_size));

    std::array<std::uint8_t, 5> decoded{};
    std::size_t decoded_size = 0;
    CHECK(duo_input::protocol::cobs_decode({encoded.data(), encoded_size},
                                           {decoded.data(), decoded.size()}, decoded_size));
    CHECK_EQ(decoded_size, raw.size());
    CHECK(bytes_equal(decoded.data(), raw.data(), raw.size()));
}

TEST_CASE(cobs_encodes_254_nonzero_bytes_vector) {
    const std::string document = vector_document();
    const std::vector<std::uint8_t> raw =
        hex_bytes(vector_value(document, "254_nonzero_bytes", "raw"));
    const std::vector<std::uint8_t> expected =
        hex_bytes(vector_value(document, "254_nonzero_bytes", "encoded"));
    std::array<std::uint8_t, 256> encoded{};
    std::size_t encoded_size = 0;

    CHECK_EQ(raw.size(), std::size_t{254});
    CHECK(duo_input::protocol::cobs_encode({raw.data(), raw.size()},
                                           {encoded.data(), encoded.size()}, encoded_size));
    CHECK_EQ(encoded_size, expected.size());
    CHECK(bytes_equal(encoded.data(), expected.data(), encoded_size));
}

TEST_CASE(cobs_rejects_malformed_vectors_without_changing_output_size) {
    const std::string document = vector_document();
    const std::vector<std::uint8_t> zero_code =
        hex_bytes(vector_value(document, "zero_code", "encoded"));
    const std::vector<std::uint8_t> truncated =
        hex_bytes(vector_value(document, "truncated_block", "encoded"));
    std::array<std::uint8_t, 4> output{};
    std::size_t output_size = 55;

    CHECK_FALSE(duo_input::protocol::cobs_decode({zero_code.data(), zero_code.size()},
                                                 {output.data(), output.size()}, output_size));
    CHECK_EQ(output_size, std::size_t{55});
    CHECK_FALSE(duo_input::protocol::cobs_decode({truncated.data(), truncated.size()},
                                                 {output.data(), output.size()}, output_size));
    CHECK_EQ(output_size, std::size_t{55});
}

TEST_CASE(cobs_rejects_insufficient_output_capacity_without_changing_output_size) {
    constexpr std::array<std::uint8_t, 2> raw{{0x11, 0x22}};
    std::array<std::uint8_t, 2> output{};
    std::size_t output_size = 55;

    CHECK_FALSE(duo_input::protocol::cobs_encode({raw.data(), raw.size()},
                                                 {output.data(), output.size()}, output_size));
    CHECK_EQ(output_size, std::size_t{55});
}

TEST_CASE(cobs_accepts_exact_capacity_for_zero_heavy_input) {
    std::array<std::uint8_t, 254> raw{};
    std::array<std::uint8_t, 255> output{};
    std::size_t output_size = 0;

    CHECK(duo_input::protocol::cobs_encode({raw.data(), raw.size()},
                                           {output.data(), output.size()}, output_size));
    CHECK_EQ(output_size, output.size());
}

TEST_CASE(cobs_refuses_overflowing_input_size_without_dereferencing_it) {
    constexpr std::size_t impossible_size =
        254U * (std::numeric_limits<std::size_t>::max() / 255U);
    std::uint8_t sentinel = 0;
    std::size_t output_size = 55;

    CHECK_FALSE(duo_input::protocol::cobs_encode({&sentinel, impossible_size},
                                                 {&sentinel, std::numeric_limits<std::size_t>::max()},
                                                 output_size));
    CHECK_EQ(output_size, std::size_t{55});
}

TEST_CASE(crc_matches_golden_vector) {
    const std::string document = vector_document();
    const std::vector<std::uint8_t> input = hex_bytes(vector_value(document, "crc", "input"));
    const std::vector<std::uint8_t> expected16 =
        hex_bytes(vector_value(document, "crc", "crc16_ccitt"));
    const std::vector<std::uint8_t> expected32 =
        hex_bytes(vector_value(document, "crc", "crc32_ieee"));
    const ByteView bytes{input.data(), input.size()};

    CHECK_EQ(expected16.size(), std::size_t{2});
    CHECK_EQ(expected32.size(), std::size_t{4});
    CHECK_EQ(duo_input::protocol::crc16_ccitt(bytes),
             static_cast<std::uint16_t>((expected16[0] << 8U) | expected16[1]));
    CHECK_EQ(duo_input::protocol::crc32_ieee(bytes),
             (static_cast<std::uint32_t>(expected32[0]) << 24U) |
                 (static_cast<std::uint32_t>(expected32[1]) << 16U) |
                 (static_cast<std::uint32_t>(expected32[2]) << 8U) | expected32[3]);
}
