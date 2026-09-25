#include "test_support.hpp"

#include "protocol/cobs.hpp"
#include "protocol/crc.hpp"
#include "protocol/frame.hpp"

#include "link/spi_protocol.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <fstream>
#include <iterator>
#include <string>
#include <type_traits>
#include <vector>

namespace {

using duo_input::protocol::ByteView;

static_assert(std::is_same_v<std::underlying_type_t<duo_input::protocol::Capability>,
                             std::uint32_t>);

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

std::vector<std::uint8_t> decode_cdc_transport(const std::vector<std::uint8_t>& transport) {
    std::vector<std::uint8_t> raw(transport.size());
    std::size_t raw_size = 0;
    if (transport.empty() || !duo_input::protocol::cobs_decode(
                                 {transport.data(), transport.size() - 1U},
                                 {raw.data(), raw.size()}, raw_size)) {
        return {};
    }
    raw.resize(raw_size);
    return raw;
}

std::vector<std::uint8_t> encode_cdc_raw_with_crc(std::vector<std::uint8_t> raw) {
    if (raw.size() < 14U) {
        return {};
    }
    write_u32(raw.data() + raw.size() - 4U,
              duo_input::protocol::crc32_ieee({raw.data(), raw.size() - 4U}));
    std::vector<std::uint8_t> transport(raw.size() + raw.size() / 254U + 2U);
    std::size_t transport_size = 0;
    if (!duo_input::protocol::cobs_encode({raw.data(), raw.size()},
                                          {transport.data(), transport.size()}, transport_size)) {
        return {};
    }
    transport.resize(transport_size);
    transport.push_back(0);
    return transport;
}

std::vector<std::uint8_t> repair_spi_crc(std::vector<std::uint8_t> frame) {
    if (frame.size() != 64U) {
        return {};
    }
    write_u16(frame.data() + 62U, duo_input::protocol::crc16_ccitt({frame.data(), 62U}));
    return frame;
}

void check_cdc_error(const std::vector<std::uint8_t>& transport,
                     duo_input::protocol::FrameError expected) {
    std::array<std::uint8_t, 1038> scratch{};
    duo_input::protocol::DecodeResult result{};
    CHECK_FALSE(duo_input::protocol::decode_cdc_frame({transport.data(), transport.size()},
                                                      {scratch.data(), scratch.size()}, result));
    CHECK_EQ(result.error, expected);
}

void check_spi_error(const std::vector<std::uint8_t>& transport,
                     duo_input::protocol::FrameError expected) {
    duo_input::protocol::DecodeResult result{};
    CHECK_FALSE(duo_input::protocol::decode_spi_frame({transport.data(), transport.size()}, result));
    CHECK_EQ(result.error, expected);
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

TEST_CASE(address_frames_are_known_in_both_directions) {
    using duo_input::protocol::SpiFrame;
    using duo_input::protocol::SpiMessageType;
    for (SpiMessageType type :
         {SpiMessageType::HOST_ADDRESSES, SpiMessageType::ENDPOINT_ADDRESSES}) {
        const std::uint8_t payload[5] = {1, 192, 168, 1, 7};
        SpiFrame frame;
        frame.type = type;
        frame.payload = duo_input::protocol::ByteView{payload, sizeof(payload)};
        std::uint8_t wire[64] = {};
        std::size_t written = 0;
        CHECK(duo_input::protocol::encode_spi_frame(
            frame, duo_input::protocol::MutableByteView{wire, sizeof(wire)}, written));
        duo_input::protocol::DecodeResult result;
        CHECK(duo_input::protocol::decode_spi_frame(
            duo_input::protocol::ByteView{wire, sizeof(wire)}, result));
        CHECK(result.spi.type == type);
    }
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

TEST_CASE(cdc_frame_distinguishes_malformed_cobs_from_insufficient_scratch) {
    const std::vector<std::uint8_t> transport =
        hex_bytes(vector_value(vector_document(), "cdc", "transport"));
    std::array<std::uint8_t, 16> small_scratch{};
    duo_input::protocol::DecodeResult result{};

    CHECK_FALSE(duo_input::protocol::decode_cdc_frame({transport.data(), transport.size()},
                                                      {small_scratch.data(), small_scratch.size()}, result));
    CHECK_EQ(result.error, duo_input::protocol::FrameError::INSUFFICIENT_CAPACITY);
    check_cdc_error({2U, 0U}, duo_input::protocol::FrameError::INVALID_INPUT);
}

TEST_CASE(cdc_frame_reaches_each_repaired_header_validation) {
    const std::vector<std::uint8_t> raw = decode_cdc_transport(
        hex_bytes(vector_value(vector_document(), "cdc", "transport")));
    struct Mutation {
        std::size_t offset;
        std::uint8_t value;
        duo_input::protocol::FrameError error;
    };
    const std::array<Mutation, 5> mutations{{
        {0U, 0U, duo_input::protocol::FrameError::INVALID_MAGIC},
        {2U, 2U, duo_input::protocol::FrameError::INCOMPATIBLE_MAJOR},
        {5U, 1U, duo_input::protocol::FrameError::INVALID_FLAGS},
        {4U, 0xFFU, duo_input::protocol::FrameError::INVALID_TYPE},
        {8U, 4U, duo_input::protocol::FrameError::INVALID_LENGTH},
    }};

    for (const Mutation& mutation : mutations) {
        std::vector<std::uint8_t> mutated = raw;
        mutated[mutation.offset] = mutation.value;
        check_cdc_error(encode_cdc_raw_with_crc(mutated), mutation.error);
    }
    check_cdc_error({2U, 0U}, duo_input::protocol::FrameError::INVALID_INPUT);
}

TEST_CASE(spi_frame_reaches_each_repaired_header_padding_and_crc_validation) {
    const std::vector<std::uint8_t> spi = hex_bytes(vector_value(vector_document(), "spi", "frame"));
    struct Mutation {
        std::size_t offset;
        std::uint8_t value;
        duo_input::protocol::FrameError error;
    };
    const std::array<Mutation, 5> mutations{{
        {0U, 0U, duo_input::protocol::FrameError::INVALID_MAGIC},
        {2U, 2U, duo_input::protocol::FrameError::INCOMPATIBLE_MAJOR},
        {5U, 1U, duo_input::protocol::FrameError::INVALID_FLAGS},
        {4U, 0xFFU, duo_input::protocol::FrameError::INVALID_TYPE},
        {8U, 53U, duo_input::protocol::FrameError::INVALID_LENGTH},
    }};

    check_spi_error(std::vector<std::uint8_t>(spi.begin(), spi.end() - 1U),
                    duo_input::protocol::FrameError::INVALID_INPUT);
    for (const Mutation& mutation : mutations) {
        std::vector<std::uint8_t> mutated = spi;
        mutated[mutation.offset] = mutation.value;
        check_spi_error(repair_spi_crc(mutated), mutation.error);
    }
    std::vector<std::uint8_t> nonzero_padding = spi;
    nonzero_padding[20U] = 1U;
    check_spi_error(repair_spi_crc(nonzero_padding), duo_input::protocol::FrameError::INVALID_PADDING);
    std::vector<std::uint8_t> damaged_crc = spi;
    damaged_crc[63U] ^= 1U;
    check_spi_error(damaged_crc, duo_input::protocol::FrameError::INVALID_CRC);
}

TEST_CASE(frame_codecs_accept_and_reject_different_minors_by_capability) {
    constexpr std::uint32_t keyboard = static_cast<std::uint32_t>(
        duo_input::protocol::Capability::KEYBOARD_HID);
    constexpr std::uint32_t capture = static_cast<std::uint32_t>(duo_input::protocol::Capability::CAPTURE);

    std::vector<std::uint8_t> cdc_raw = decode_cdc_transport(
        hex_bytes(vector_value(vector_document(), "cdc", "transport")));
    cdc_raw[3] = 9U;
    const std::vector<std::uint8_t> cdc = encode_cdc_raw_with_crc(cdc_raw);
    std::array<std::uint8_t, 1038> scratch{};
    duo_input::protocol::DecodeResult cdc_result{};
    CHECK(duo_input::protocol::decode_cdc_frame({cdc.data(), cdc.size()},
                                                {scratch.data(), scratch.size()}, cdc_result,
                                                keyboard, keyboard));
    CHECK_EQ(cdc_result.cdc.minor, 9U);
    CHECK_FALSE(duo_input::protocol::decode_cdc_frame({cdc.data(), cdc.size()},
                                                      {scratch.data(), scratch.size()}, cdc_result,
                                                      capture, keyboard));
    CHECK_EQ(cdc_result.error, duo_input::protocol::FrameError::INCOMPATIBLE_MINOR);

    std::vector<std::uint8_t> spi = hex_bytes(vector_value(vector_document(), "spi", "frame"));
    spi[3] = 9U;
    spi = repair_spi_crc(spi);
    duo_input::protocol::DecodeResult spi_result{};
    CHECK(duo_input::protocol::decode_spi_frame({spi.data(), spi.size()}, spi_result, keyboard,
                                                keyboard));
    CHECK_EQ(spi_result.spi.minor, 9U);
    CHECK_FALSE(duo_input::protocol::decode_spi_frame({spi.data(), spi.size()}, spi_result, capture,
                                                      keyboard));
    CHECK_EQ(spi_result.error, duo_input::protocol::FrameError::INCOMPATIBLE_MINOR);
}

TEST_CASE(frame_codecs_preserve_bit_31_in_capability_compatibility_masks) {
    constexpr std::uint32_t high_bit = UINT32_C(1) << 31U;
    CHECK(duo_input::protocol::is_minor_compatible(9U, high_bit, high_bit));
    CHECK_FALSE(duo_input::protocol::is_minor_compatible(9U, high_bit, 0U));

    std::vector<std::uint8_t> cdc_raw = decode_cdc_transport(
        hex_bytes(vector_value(vector_document(), "cdc", "transport")));
    cdc_raw[3] = 9U;
    const std::vector<std::uint8_t> cdc = encode_cdc_raw_with_crc(cdc_raw);
    std::array<std::uint8_t, 1038> scratch{};
    duo_input::protocol::DecodeResult cdc_result{};
    CHECK(duo_input::protocol::decode_cdc_frame({cdc.data(), cdc.size()},
                                                {scratch.data(), scratch.size()}, cdc_result,
                                                high_bit, high_bit));
    CHECK_FALSE(duo_input::protocol::decode_cdc_frame({cdc.data(), cdc.size()},
                                                      {scratch.data(), scratch.size()}, cdc_result,
                                                      high_bit, 0U));
    CHECK_EQ(cdc_result.error, duo_input::protocol::FrameError::INCOMPATIBLE_MINOR);

    std::vector<std::uint8_t> spi = hex_bytes(vector_value(vector_document(), "spi", "frame"));
    spi[3] = 9U;
    spi = repair_spi_crc(spi);
    duo_input::protocol::DecodeResult spi_result{};
    CHECK(duo_input::protocol::decode_spi_frame({spi.data(), spi.size()}, spi_result, high_bit,
                                                high_bit));
    CHECK_FALSE(duo_input::protocol::decode_spi_frame({spi.data(), spi.size()}, spi_result, high_bit,
                                                      0U));
    CHECK_EQ(spi_result.error, duo_input::protocol::FrameError::INCOMPATIBLE_MINOR);
}

// ------------------------------------------------- who could have sent this
//
// Found on real hardware. A fault that returns U1's outgoing line to its own
// incoming one hands U1 back its own frame - intact, with a valid CRC, because
// it is a frame U1 built itself. U1 accepted it as U2's reply and reported the
// link healthy while U2 was not running at all.
//
// That is the worst possible failure for this device. The whole point of the
// link watchdog is that U2 releases every key when U1 stops talking; a U1 that
// believes a dead link is alive keeps sending, and whatever U2 was holding
// when it died stays held on someone's computer.

TEST_CASE(the_endpoint_status_message_is_an_answer) {
    CHECK(duo_input::link::is_endpoint_reply(duo_input::protocol::SpiMessageType::ENDPOINT_STATUS));
}

TEST_CASE(a_frame_carrying_one_of_u1s_own_messages_is_not_an_answer) {
    using duo_input::protocol::SpiMessageType;

    // Everything U1 sends. None of it can arrive from U2, so a frame carrying
    // any of it is U1's own transmission coming back.
    const SpiMessageType from_u1[] = {
        SpiMessageType::HANDSHAKE,   SpiMessageType::KBD_STATE,
        SpiMessageType::CONSUMER_STATE, SpiMessageType::MOUSE_DELTA,
        SpiMessageType::CONTROL_RELEASE_ALL, SpiMessageType::HEARTBEAT,
    };

    for (SpiMessageType type : from_u1) {
        CHECK(!duo_input::link::is_endpoint_reply(type));
    }
}

TEST_CASE(a_message_type_that_does_not_exist_is_not_an_answer) {
    // Noise on an undriven line decodes into a valid frame vanishingly rarely,
    // but "vanishingly rarely" over days of uptime is not never.
    CHECK(!duo_input::link::is_endpoint_reply(
        static_cast<duo_input::protocol::SpiMessageType>(0x00)));
    CHECK(!duo_input::link::is_endpoint_reply(
        static_cast<duo_input::protocol::SpiMessageType>(0xFF)));
}

TEST_CASE(hid_descriptor_capture_is_a_known_cdc_message_and_round_trips) {
    const duo_input::protocol::CdcFrame frame{
        duo_input::protocol::PROTOCOL_VERSION_MINOR,
        duo_input::protocol::CdcMessageType::GET_HID_DESCRIPTOR_CAPTURE,
        0,
        23,
        {nullptr, 0},
    };
    std::array<std::uint8_t, 1038> scratch{};
    std::array<std::uint8_t, 1045> encoded{};
    std::size_t encoded_size = 0;

    CHECK(duo_input::protocol::encode_cdc_frame(frame, {encoded.data(), encoded.size()},
                                                {scratch.data(), scratch.size()}, encoded_size));
    duo_input::protocol::DecodeResult decoded{};
    CHECK(duo_input::protocol::decode_cdc_frame({encoded.data(), encoded_size},
                                                {scratch.data(), scratch.size()}, decoded));
    CHECK_EQ(decoded.cdc.type,
             duo_input::protocol::CdcMessageType::GET_HID_DESCRIPTOR_CAPTURE);
    CHECK_EQ(decoded.cdc.sequence, 23u);
    CHECK_EQ(decoded.cdc.payload.size, 0u);
}
