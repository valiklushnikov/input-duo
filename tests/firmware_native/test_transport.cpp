#include "test_support.hpp"

#include "protocol/cobs.hpp"
#include "protocol/crc.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace {

using duo_input::protocol::ByteView;
using duo_input::protocol::MutableByteView;

bool bytes_equal(const std::uint8_t* actual, const std::uint8_t* expected, std::size_t size) {
    for (std::size_t index = 0; index < size; ++index) {
        if (actual[index] != expected[index]) {
            return false;
        }
    }
    return true;
}

}  // namespace

TEST_CASE(cobs_encodes_and_decodes_empty_vector) {
    std::array<std::uint8_t, 1> encoded{};
    std::size_t encoded_size = 99;
    CHECK(duo_input::protocol::cobs_encode({nullptr, 0}, {encoded.data(), encoded.size()},
                                           encoded_size));
    CHECK_EQ(encoded_size, std::size_t{1});
    CHECK_EQ(encoded[0], std::uint8_t{1});

    std::array<std::uint8_t, 1> decoded{};
    std::size_t decoded_size = 99;
    CHECK(duo_input::protocol::cobs_decode({encoded.data(), encoded_size},
                                           {decoded.data(), decoded.size()}, decoded_size));
    CHECK_EQ(decoded_size, std::size_t{0});
}

TEST_CASE(cobs_encodes_and_decodes_embedded_zeroes_vector) {
    constexpr std::array<std::uint8_t, 5> raw{{0x11, 0x00, 0x22, 0x33, 0x00}};
    constexpr std::array<std::uint8_t, 6> expected{{0x02, 0x11, 0x03, 0x22, 0x33, 0x01}};
    std::array<std::uint8_t, 6> encoded{};
    std::size_t encoded_size = 0;

    CHECK(duo_input::protocol::cobs_encode({raw.data(), raw.size()},
                                           {encoded.data(), encoded.size()}, encoded_size));
    CHECK_EQ(encoded_size, std::size_t{6});
    CHECK(bytes_equal(encoded.data(), expected.data(), encoded_size));

    std::array<std::uint8_t, 5> decoded{};
    std::size_t decoded_size = 0;
    CHECK(duo_input::protocol::cobs_decode({encoded.data(), encoded_size},
                                           {decoded.data(), decoded.size()}, decoded_size));
    CHECK_EQ(decoded_size, raw.size());
    CHECK(bytes_equal(decoded.data(), raw.data(), raw.size()));
}

TEST_CASE(cobs_encodes_254_nonzero_bytes_vector) {
    std::array<std::uint8_t, 254> raw{};
    raw.fill(0xAA);
    std::array<std::uint8_t, 256> encoded{};
    std::size_t encoded_size = 0;

    CHECK(duo_input::protocol::cobs_encode({raw.data(), raw.size()},
                                           {encoded.data(), encoded.size()}, encoded_size));
    CHECK_EQ(encoded_size, std::size_t{256});
    CHECK_EQ(encoded[0], std::uint8_t{0xFF});
    CHECK(bytes_equal(encoded.data() + 1, raw.data(), raw.size()));
    CHECK_EQ(encoded[255], std::uint8_t{0x01});
}

TEST_CASE(cobs_rejects_malformed_vectors_without_changing_output_size) {
    constexpr std::array<std::uint8_t, 1> zero_code{{0x00}};
    constexpr std::array<std::uint8_t, 2> truncated_block{{0x03, 0x11}};
    std::array<std::uint8_t, 4> output{};
    std::size_t output_size = 55;

    CHECK_FALSE(duo_input::protocol::cobs_decode({zero_code.data(), zero_code.size()},
                                                 {output.data(), output.size()}, output_size));
    CHECK_EQ(output_size, std::size_t{55});
    CHECK_FALSE(duo_input::protocol::cobs_decode({truncated_block.data(), truncated_block.size()},
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

TEST_CASE(crc_matches_golden_vector) {
    constexpr std::array<std::uint8_t, 9> input{{'1', '2', '3', '4', '5', '6', '7', '8', '9'}};
    const ByteView bytes{input.data(), input.size()};

    CHECK_EQ(duo_input::protocol::crc16_ccitt(bytes), std::uint16_t{0x29B1});
    CHECK_EQ(duo_input::protocol::crc32_ieee(bytes), std::uint32_t{0xCBF43926});
}
