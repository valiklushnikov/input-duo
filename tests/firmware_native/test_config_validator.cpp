#include "test_support.hpp"

#include "config/validator.hpp"
#include "protocol/crc.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

namespace {

using duo_input::config::ConfigView;
using duo_input::config::validate_config;
using duo_input::protocol::ByteView;

std::vector<std::uint8_t> read_vector(const char* name) {
    const std::string path = std::string{DUO_CONFIG_VECTORS_PATH} + "/" + name;
    std::ifstream input(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

void write_u16(std::vector<std::uint8_t>& bytes, std::size_t offset, std::uint16_t value) {
    bytes[offset] = static_cast<std::uint8_t>(value);
    bytes[offset + 1U] = static_cast<std::uint8_t>(value >> 8U);
}

void write_u32(std::vector<std::uint8_t>& bytes, std::size_t offset, std::uint32_t value) {
    bytes[offset] = static_cast<std::uint8_t>(value);
    bytes[offset + 1U] = static_cast<std::uint8_t>(value >> 8U);
    bytes[offset + 2U] = static_cast<std::uint8_t>(value >> 16U);
    bytes[offset + 3U] = static_cast<std::uint8_t>(value >> 24U);
}

std::uint16_t read_u16(const std::vector<std::uint8_t>& bytes, std::size_t offset) {
    return static_cast<std::uint16_t>(bytes[offset]) |
           static_cast<std::uint16_t>(static_cast<std::uint16_t>(bytes[offset + 1U]) << 8U);
}

std::uint32_t read_u32(const std::vector<std::uint8_t>& bytes, std::size_t offset) {
    return static_cast<std::uint32_t>(bytes[offset]) |
           (static_cast<std::uint32_t>(bytes[offset + 1U]) << 8U) |
           (static_cast<std::uint32_t>(bytes[offset + 2U]) << 16U) |
           (static_cast<std::uint32_t>(bytes[offset + 3U]) << 24U);
}

void repair_crc(std::vector<std::uint8_t>& bytes) {
    write_u32(bytes, 12U, 0U);
    write_u32(bytes, 12U, duo_input::protocol::crc32_ieee({bytes.data(), bytes.size()}));
}

bool rejects(const std::vector<std::uint8_t>& bytes) {
    return !validate_config({bytes.data(), bytes.size()});
}

}  // namespace

TEST_CASE(config_validator_accepts_python_vectors_and_exposes_bounded_views) {
    const std::vector<std::uint8_t> minimal = read_vector("valid_minimal.bin");
    const std::vector<std::uint8_t> full = read_vector("valid_full.bin");
    const auto minimal_result = validate_config({minimal.data(), minimal.size()});
    const auto full_result = validate_config({full.data(), full.size()});

    CHECK(minimal_result);
    CHECK(full_result);
    CHECK_EQ(minimal_result.view.active_profile_id(), 1U);
    CHECK_EQ(full_result.view.active_profile_id(), 8U);
    CHECK_EQ(full_result.view.profile_count(), 8U);

    duo_input::config::ProfileView profile{};
    CHECK(full_result.view.profile_at(0U, profile));
    CHECK_EQ(profile.id(), 1U);
    CHECK_EQ(profile.binding_count(), 4U);
    CHECK_EQ(profile.macro_count(), 2U);
    CHECK_FALSE(full_result.view.profile_at(8U, profile));

    duo_input::config::BindingView binding{};
    CHECK(profile.binding_at(0U, binding));
    CHECK_EQ(binding.action_argument(), 255U);
    CHECK_FALSE(profile.binding_at(4U, binding));

    duo_input::config::MacroView macro{};
    CHECK(profile.macro_at(0U, macro));
    CHECK_EQ(macro.id(), 1U);
    CHECK_EQ(macro.step_count(), 9U);
    CHECK_FALSE(profile.macro_at(2U, macro));

    duo_input::config::StepView step{};
    CHECK(macro.step_at(4U, step));
    CHECK_EQ(step.payload().size, 4U);
    CHECK_FALSE(macro.step_at(9U, step));
}

TEST_CASE(config_validator_rejects_crc_damage_and_representative_truncations) {
    const std::vector<std::uint8_t> valid = read_vector("valid_full.bin");
    std::vector<std::uint8_t> damaged = valid;
    damaged.back() ^= 1U;
    CHECK(rejects(damaged));
    const auto damaged_result = validate_config({damaged.data(), damaged.size()});
    CHECK_EQ(damaged_result.view.profile_count(), 0U);
    duo_input::config::ProfileView absent{};
    CHECK_FALSE(damaged_result.view.profile_at(0U, absent));

    const std::uint32_t string_offset = read_u32(valid, 24U);
    const std::uint32_t data_offset = read_u32(valid, 32U);
    const std::array<std::size_t, 7> boundaries{{
        0U, 63U, 64U, 64U + 7U * 36U, string_offset, data_offset, valid.size() - 1U,
    }};
    for (std::size_t boundary : boundaries) {
        CHECK(rejects(std::vector<std::uint8_t>(valid.begin(), valid.begin() + boundary)));
    }
}

TEST_CASE(config_validator_rejects_each_repaired_header_length_and_offset_field) {
    const std::vector<std::uint8_t> valid = read_vector("valid_full.bin");
    struct Mutation { std::size_t offset; std::uint32_t value; std::size_t width; };
    const std::array<Mutation, 10> mutations{{
        {8U, 64U, 4U},       // total length
        {16U, 7U, 1U},       // profile count
        {18U, 0U, 1U},       // profile descriptor size
        {20U, 65U, 4U},      // profile table offset
        {24U, 321U, 4U},     // string offset
        {28U, 1U, 4U},       // string length
        {32U, 65U, 4U},      // data offset
        {36U, 1U, 4U},       // data length
        {6U, 1U, 1U},        // flags
        {40U, 1U, 1U},       // reserved tail
    }};
    for (const Mutation& mutation : mutations) {
        std::vector<std::uint8_t> bytes = valid;
        if (mutation.width == 1U) bytes[mutation.offset] = static_cast<std::uint8_t>(mutation.value);
        else write_u32(bytes, mutation.offset, mutation.value);
        repair_crc(bytes);
        CHECK(rejects(bytes));
    }
}

TEST_CASE(config_validator_rejects_each_repaired_profile_table_length_and_offset_field) {
    const std::vector<std::uint8_t> valid = read_vector("valid_full.bin");
    struct Mutation { std::size_t relative; std::uint32_t value; std::size_t width; };
    const std::array<Mutation, 8> mutations{{
        {8U, 0U, 4U},       // name offset
        {12U, 0xFFFFU, 2U}, // name length
        {14U, 129U, 2U},    // binding count
        {16U, 1U, 4U},      // binding offset
        {20U, 0U, 2U},      // binding record size
        {22U, 33U, 2U},     // macro count
        {24U, 1U, 4U},      // macro offset
        {28U, 0U, 2U},      // macro record size
    }};
    for (const Mutation& mutation : mutations) {
        std::vector<std::uint8_t> bytes = valid;
        const std::size_t offset = 64U + mutation.relative;
        if (mutation.width == 2U) write_u16(bytes, offset, static_cast<std::uint16_t>(mutation.value));
        else write_u32(bytes, offset, mutation.value);
        repair_crc(bytes);
        CHECK(rejects(bytes));
    }
}

TEST_CASE(config_validator_rejects_each_repaired_macro_and_step_length_or_offset_field) {
    const std::vector<std::uint8_t> valid = read_vector("valid_full.bin");
    const std::size_t macro_offset = read_u32(valid, 64U + 24U);
    const std::size_t step_offset = read_u32(valid, macro_offset + 12U);
    struct Mutation { std::size_t offset; std::uint32_t value; std::size_t width; };
    const std::array<Mutation, 7> mutations{{
        {macro_offset + 4U, 0U, 4U},       // macro name offset
        {macro_offset + 8U, 0xFFFFU, 2U},  // macro name length
        {macro_offset + 10U, 65U, 2U},     // step count
        {macro_offset + 12U, 1U, 4U},      // step offset
        {macro_offset + 16U, 0U, 2U},      // step record size
        {step_offset + 2U, 0xFFFFU, 2U},   // payload length
        {step_offset + 4U, 1U, 4U},        // payload offset
    }};
    for (const Mutation& mutation : mutations) {
        std::vector<std::uint8_t> bytes = valid;
        if (mutation.width == 2U) write_u16(bytes, mutation.offset, static_cast<std::uint16_t>(mutation.value));
        else write_u32(bytes, mutation.offset, mutation.value);
        repair_crc(bytes);
        CHECK(rejects(bytes));
    }
}

TEST_CASE(config_validator_rejects_repaired_unknown_enums_duplicates_and_invalid_payloads) {
    const std::vector<std::uint8_t> valid = read_vector("valid_full.bin");
    const std::size_t binding_offset = read_u32(valid, 64U + 16U);
    const std::size_t macro_offset = read_u32(valid, 64U + 24U);
    const std::size_t step_offset = read_u32(valid, macro_offset + 12U);
    const std::array<std::size_t, 8> enum_offsets{{
        64U + 1U, 64U + 2U, 64U + 3U, binding_offset, binding_offset + 3U,
        binding_offset + 4U, macro_offset + 1U, step_offset,
    }};
    for (std::size_t offset : enum_offsets) {
        std::vector<std::uint8_t> bytes = valid;
        bytes[offset] = 0xFFU;
        repair_crc(bytes);
        CHECK(rejects(bytes));
    }
    std::vector<std::uint8_t> duplicate_trigger = valid;
    for (std::size_t index = 0; index < 3U; ++index) {
        duplicate_trigger[binding_offset + 12U + index] = duplicate_trigger[binding_offset + index];
    }
    repair_crc(duplicate_trigger);
    CHECK(rejects(duplicate_trigger));

    std::vector<std::uint8_t> duplicate_macro = valid;
    duplicate_macro[macro_offset + 24U] = duplicate_macro[macro_offset];
    repair_crc(duplicate_macro);
    CHECK(rejects(duplicate_macro));

    std::vector<std::uint8_t> invalid_key = valid;
    const std::size_t first_payload = read_u32(valid, step_offset + 4U);
    invalid_key[first_payload + 1U] = 0U;
    repair_crc(invalid_key);
    CHECK(rejects(invalid_key));

    std::vector<std::uint8_t> invalid_utf8 = valid;
    const std::size_t name_offset = read_u32(valid, 64U + 8U);
    invalid_utf8[name_offset] = 0xFFU;
    repair_crc(invalid_utf8);
    CHECK(rejects(invalid_utf8));
}
