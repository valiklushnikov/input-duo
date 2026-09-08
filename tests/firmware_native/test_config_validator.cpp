#include "test_support.hpp"

#include "config/validator.hpp"
#include "protocol/crc.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <fstream>
#include <iterator>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

namespace {

using duo_input::config::ConfigView;
using duo_input::config::ValidationError;
using duo_input::config::ValidationResult;
using duo_input::config::validate_config;
using duo_input::protocol::ByteView;

static_assert(std::is_same_v<duo_input::config::KeyboardRoute,
                             duo_input::protocol::KeyboardRoute>);
static_assert(std::is_same_v<duo_input::config::MouseRoute,
                             duo_input::protocol::MouseRoute>);
static_assert(std::is_same_v<duo_input::config::TargetMode,
                             duo_input::protocol::TargetMode>);
static_assert(std::is_same_v<duo_input::config::MouseRouteCommand,
                             duo_input::protocol::MouseRouteCommand>);
static_assert(std::is_same_v<duo_input::config::TextLayout,
                             duo_input::protocol::TextLayout>);
static_assert(std::is_same_v<duo_input::config::TriggerKind,
                             duo_input::protocol::TriggerKind>);
static_assert(std::is_same_v<duo_input::config::BindingMode,
                             duo_input::protocol::BindingMode>);
static_assert(std::is_same_v<duo_input::config::ActionKind,
                             duo_input::protocol::ActionKind>);
static_assert(std::is_same_v<decltype(std::declval<const duo_input::config::BindingView&>().source()),
                             duo_input::config::TriggerSource>);
static_assert(duo_input::protocol::SCHEMA_VERSION_MINOR == 1U);
static_assert(!std::is_constructible_v<ValidationResult, ValidationError>);
static_assert(!std::is_constructible_v<ValidationResult, ValidationError, ConfigView>);
static_assert(std::is_same_v<decltype(std::declval<const ValidationResult&>().error()),
                             ValidationError>);
static_assert(std::is_same_v<decltype(std::declval<const ValidationResult&>().view()),
                             const ConfigView&>);
static_assert(!std::is_assignable_v<decltype(std::declval<const ValidationResult&>().error()),
                                    ValidationError>);
static_assert(!std::is_copy_assignable_v<ValidationResult>);

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

std::size_t first_macro_offset(const std::vector<std::uint8_t>& bytes) {
    return read_u32(bytes, 64U + 24U);
}

std::size_t step_descriptor(const std::vector<std::uint8_t>& bytes,
                            duo_input::protocol::MacroStepType type) {
    const std::size_t macro = first_macro_offset(bytes);
    const std::size_t step_count = read_u16(bytes, macro + 10U);
    const std::size_t steps = read_u32(bytes, macro + 12U);
    for (std::size_t index = 0; index < step_count; ++index) {
        const std::size_t descriptor = steps + index * 12U;
        if (bytes[descriptor] == static_cast<std::uint8_t>(type)) {
            return descriptor;
        }
    }
    return bytes.size();
}

std::vector<std::uint8_t> mutate_step_payload(const std::vector<std::uint8_t>& valid,
                                              duo_input::protocol::MacroStepType type,
                                              std::size_t payload_index,
                                              std::uint8_t value) {
    std::vector<std::uint8_t> bytes = valid;
    const std::size_t descriptor = step_descriptor(bytes, type);
    CHECK(descriptor < bytes.size());
    if (descriptor >= bytes.size()) {
        return valid;
    }
    const std::size_t payload = read_u32(bytes, descriptor + 4U);
    CHECK(payload_index < read_u16(bytes, descriptor + 2U));
    if (payload_index >= read_u16(bytes, descriptor + 2U)) {
        return valid;
    }
    bytes[payload + payload_index] = value;
    repair_crc(bytes);
    return bytes;
}

}  // namespace

TEST_CASE(config_validator_accepts_python_vectors_and_exposes_bounded_views) {
    const std::vector<std::uint8_t> minimal = read_vector("valid_minimal.bin");
    const std::vector<std::uint8_t> full = read_vector("valid_full.bin");
    const auto minimal_result = validate_config({minimal.data(), minimal.size()});
    const auto full_result = validate_config({full.data(), full.size()});

    CHECK(minimal_result);
    CHECK(full_result);
    CHECK_EQ(minimal_result.error(), ValidationError::NONE);
    CHECK_EQ(minimal_result.view().active_profile_id(), 1U);
    CHECK_EQ(full_result.view().active_profile_id(), 8U);
    CHECK_EQ(full_result.view().profile_count(), 8U);

    duo_input::config::ProfileView profile{};
    CHECK(full_result.view().profile_at(0U, profile));
    CHECK_EQ(profile.id(), 1U);
    CHECK_EQ(profile.binding_count(), 4U);
    CHECK_EQ(profile.macro_count(), 2U);
    CHECK_FALSE(full_result.view().profile_at(8U, profile));

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

TEST_CASE(config_validator_accepts_same_trigger_from_distinct_sources) {
    std::vector<std::uint8_t> bytes = read_vector("valid_full.bin");
    const std::size_t binding_offset = read_u32(bytes, 64U + 16U);
    const std::size_t second = binding_offset + 12U;
    for (std::size_t index = 0; index < 6U; ++index) {
        bytes[second + index] = bytes[binding_offset + index];
    }
    write_u16(bytes, binding_offset + 6U, 0x3434U);
    write_u16(bytes, binding_offset + 8U, 0xD030U);
    bytes[binding_offset + 10U] = 1U;
    write_u16(bytes, second + 6U, 0x3434U);
    write_u16(bytes, second + 8U, 0xD030U);
    bytes[second + 10U] = 2U;
    repair_crc(bytes);

    CHECK(validate_config({bytes.data(), bytes.size()}));
}

TEST_CASE(config_validator_rejects_exact_trigger_source_duplicate) {
    std::vector<std::uint8_t> bytes = read_vector("valid_full.bin");
    const std::size_t binding_offset = read_u32(bytes, 64U + 16U);
    const std::size_t second = binding_offset + 12U;
    for (std::size_t index = 0; index < 11U; ++index) {
        bytes[second + index] = bytes[binding_offset + index];
    }
    write_u16(bytes, binding_offset + 6U, 0x3434U);
    write_u16(bytes, binding_offset + 8U, 0xD030U);
    bytes[binding_offset + 10U] = 1U;
    write_u16(bytes, second + 6U, 0x3434U);
    write_u16(bytes, second + 8U, 0xD030U);
    bytes[second + 10U] = 1U;
    repair_crc(bytes);

    CHECK(rejects(bytes));
}

TEST_CASE(config_validator_rejects_every_partially_zero_source) {
    const std::array<duo_input::config::TriggerSource, 5> invalid_sources{{
        {0x3434U, 0U, 0U},
        {0x3434U, 0U, 1U},
        {0U, 0xD030U, 0U},
        {0U, 0xD030U, 1U},
        {0U, 0U, 1U},
    }};

    for (const auto source : invalid_sources) {
        std::vector<std::uint8_t> bytes = read_vector("valid_full.bin");
        const std::size_t binding_offset = read_u32(bytes, 64U + 16U);
        write_u16(bytes, binding_offset + 6U, source.vendor_id);
        write_u16(bytes, binding_offset + 8U, source.product_id);
        bytes[binding_offset + 10U] = source.interface_number;
        repair_crc(bytes);

        CHECK(rejects(bytes));
    }
}

TEST_CASE(config_validator_accepts_nonzero_ids_with_interface_zero) {
    std::vector<std::uint8_t> bytes = read_vector("valid_full.bin");
    const std::size_t binding_offset = read_u32(bytes, 64U + 16U);
    write_u16(bytes, binding_offset + 6U, 0x3434U);
    write_u16(bytes, binding_offset + 8U, 0xD030U);
    bytes[binding_offset + 10U] = 0U;
    repair_crc(bytes);

    const auto result = validate_config({bytes.data(), bytes.size()});
    CHECK(result);
    if (!result) {
        return;
    }
    duo_input::config::ProfileView profile{};
    duo_input::config::BindingView binding{};
    CHECK(result.view().profile_at(0U, profile));
    CHECK(profile.binding_at(0U, binding));
    CHECK_EQ(binding.source().vendor_id, 0x3434U);
    CHECK_EQ(binding.source().product_id, 0xD030U);
    CHECK_EQ(binding.source().interface_number, 0U);
}

TEST_CASE(config_binding_view_exposes_source_and_legacy_any_source) {
    std::vector<std::uint8_t> bytes = read_vector("valid_full.bin");
    const auto legacy_result = validate_config({bytes.data(), bytes.size()});
    CHECK(legacy_result);
    duo_input::config::ProfileView profile{};
    duo_input::config::BindingView binding{};
    CHECK(legacy_result.view().profile_at(0U, profile));
    CHECK(profile.binding_at(0U, binding));
    CHECK_EQ(binding.source().vendor_id, 0U);
    CHECK_EQ(binding.source().product_id, 0U);
    CHECK_EQ(binding.source().interface_number, 0U);

    const std::size_t binding_offset = read_u32(bytes, 64U + 16U);
    write_u16(bytes, binding_offset + 6U, 0x3434U);
    write_u16(bytes, binding_offset + 8U, 0xD030U);
    bytes[binding_offset + 10U] = 1U;
    repair_crc(bytes);
    const auto qualified_result = validate_config({bytes.data(), bytes.size()});
    CHECK(qualified_result);
    CHECK(qualified_result.view().profile_at(0U, profile));
    CHECK(profile.binding_at(0U, binding));
    CHECK_EQ(binding.source().vendor_id, 0x3434U);
    CHECK_EQ(binding.source().product_id, 0xD030U);
    CHECK_EQ(binding.source().interface_number, 1U);
}

TEST_CASE(config_validator_accepts_ru_and_ua_layouts_from_python_vector) {
    const std::vector<std::uint8_t> full = read_vector("valid_full.bin");
    const auto result = validate_config({full.data(), full.size()});
    CHECK(result);
    duo_input::config::ProfileView ru{};
    duo_input::config::ProfileView ua{};
    CHECK(result.view().profile_at(0U, ru));
    CHECK(result.view().profile_at(1U, ua));
    CHECK_EQ(ru.text_layout(), duo_input::config::TextLayout::RU);
    CHECK_EQ(ua.text_layout(), duo_input::config::TextLayout::UA);
}

TEST_CASE(config_validator_accepts_target_inherit_and_mouse_step_toggle_from_python_vector) {
    const std::vector<std::uint8_t> full = read_vector("valid_full.bin");
    const auto result = validate_config({full.data(), full.size()});
    CHECK(result);
    duo_input::config::ProfileView profile{};
    duo_input::config::MacroView macro{};
    CHECK(result.view().profile_at(0U, profile));
    CHECK(profile.macro_at(0U, macro));
    CHECK_EQ(macro.target(), duo_input::config::TargetMode::INHERIT);
    const std::size_t descriptor = step_descriptor(
        full, duo_input::protocol::MacroStepType::SET_MOUSE_ROUTE);
    CHECK(descriptor < full.size());
    CHECK_EQ(read_u16(full, descriptor + 2U), 1U);
    CHECK_EQ(full[read_u32(full, descriptor + 4U)],
             static_cast<std::uint8_t>(duo_input::config::MouseRouteCommand::TOGGLE));
}

TEST_CASE(config_validator_rejects_mouse_profile_both) {
    std::vector<std::uint8_t> bytes = read_vector("valid_full.bin");
    bytes[64U + 2U] = static_cast<std::uint8_t>(duo_input::config::KeyboardRoute::BOTH);
    repair_crc(bytes);
    CHECK(rejects(bytes));
}

TEST_CASE(config_validator_rejects_binding_set_mouse_route_toggle_value) {
    std::vector<std::uint8_t> bytes = read_vector("valid_full.bin");
    const std::size_t binding_offset = read_u32(bytes, 64U + 16U);
    const std::size_t set_mouse_binding = binding_offset + 3U * 12U;
    CHECK_EQ(bytes[set_mouse_binding + 4U],
             static_cast<std::uint8_t>(duo_input::config::ActionKind::SET_MOUSE_ROUTE));
    bytes[set_mouse_binding + 5U] =
        static_cast<std::uint8_t>(duo_input::config::MouseRouteCommand::TOGGLE);
    repair_crc(bytes);
    CHECK(rejects(bytes));
}

TEST_CASE(config_validator_rejects_unknown_text_layout) {
    std::vector<std::uint8_t> bytes = read_vector("valid_full.bin");
    bytes[64U + 3U] = 4U;
    repair_crc(bytes);
    CHECK(rejects(bytes));
}

TEST_CASE(config_validator_accepts_key_tap_modifier_and_nonzero_usage_shape) {
    const std::vector<std::uint8_t> bytes = read_vector("valid_full.bin");
    const std::size_t descriptor = step_descriptor(bytes, duo_input::protocol::MacroStepType::KEY_TAP);
    CHECK_EQ(read_u16(bytes, descriptor + 2U), 2U);
    const std::size_t payload = read_u32(bytes, descriptor + 4U);
    CHECK_EQ(bytes[payload], 2U);
    CHECK_EQ(bytes[payload + 1U], 4U);
    CHECK(validate_config({bytes.data(), bytes.size()}));
}

TEST_CASE(config_validator_rejects_key_tap_one_byte_or_zero_usage_shape) {
    const std::vector<std::uint8_t> valid = read_vector("valid_full.bin");
    std::vector<std::uint8_t> one_byte = valid;
    one_byte[step_descriptor(one_byte, duo_input::protocol::MacroStepType::KEY_DOWN)] =
        static_cast<std::uint8_t>(duo_input::protocol::MacroStepType::KEY_TAP);
    repair_crc(one_byte);
    CHECK(rejects(one_byte));
    CHECK(rejects(mutate_step_payload(valid, duo_input::protocol::MacroStepType::KEY_TAP, 1U, 0U)));
}

TEST_CASE(config_validator_accepts_key_down_single_nonzero_usage_shape) {
    const std::vector<std::uint8_t> bytes = read_vector("valid_full.bin");
    const std::size_t descriptor = step_descriptor(bytes, duo_input::protocol::MacroStepType::KEY_DOWN);
    CHECK_EQ(read_u16(bytes, descriptor + 2U), 1U);
    CHECK(bytes[read_u32(bytes, descriptor + 4U)] != 0U);
    CHECK(validate_config({bytes.data(), bytes.size()}));
}

TEST_CASE(config_validator_rejects_key_down_two_bytes_or_zero_usage_shape) {
    const std::vector<std::uint8_t> valid = read_vector("valid_full.bin");
    std::vector<std::uint8_t> two_bytes = valid;
    two_bytes[step_descriptor(two_bytes, duo_input::protocol::MacroStepType::KEY_TAP)] =
        static_cast<std::uint8_t>(duo_input::protocol::MacroStepType::KEY_DOWN);
    repair_crc(two_bytes);
    CHECK(rejects(two_bytes));
    CHECK(rejects(mutate_step_payload(valid, duo_input::protocol::MacroStepType::KEY_DOWN, 0U, 0U)));
}

TEST_CASE(config_validator_accepts_key_up_single_nonzero_usage_shape) {
    const std::vector<std::uint8_t> bytes = read_vector("valid_full.bin");
    const std::size_t descriptor = step_descriptor(bytes, duo_input::protocol::MacroStepType::KEY_UP);
    CHECK_EQ(read_u16(bytes, descriptor + 2U), 1U);
    CHECK(bytes[read_u32(bytes, descriptor + 4U)] != 0U);
    CHECK(validate_config({bytes.data(), bytes.size()}));
}

TEST_CASE(config_validator_rejects_key_up_two_bytes_or_zero_usage_shape) {
    const std::vector<std::uint8_t> valid = read_vector("valid_full.bin");
    std::vector<std::uint8_t> two_bytes = valid;
    two_bytes[step_descriptor(two_bytes, duo_input::protocol::MacroStepType::KEY_TAP)] =
        static_cast<std::uint8_t>(duo_input::protocol::MacroStepType::KEY_UP);
    repair_crc(two_bytes);
    CHECK(rejects(two_bytes));
    CHECK(rejects(mutate_step_payload(valid, duo_input::protocol::MacroStepType::KEY_UP, 0U, 0U)));
}

TEST_CASE(config_validator_rejects_crc_damage_and_representative_truncations) {
    const std::vector<std::uint8_t> valid = read_vector("valid_full.bin");
    std::vector<std::uint8_t> damaged = valid;
    damaged.back() ^= 1U;
    CHECK(rejects(damaged));
    const auto damaged_result = validate_config({damaged.data(), damaged.size()});
    CHECK_EQ(damaged_result.error(), ValidationError::INVALID_CRC);
    CHECK_EQ(damaged_result.view().profile_count(), 0U);
    duo_input::config::ProfileView absent{};
    CHECK_FALSE(damaged_result.view().profile_at(0U, absent));

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

TEST_CASE(config_validator_rejects_independent_missing_action_references) {
    const std::vector<std::uint8_t> valid = read_vector("valid_full.bin");
    const std::size_t binding_offset = read_u32(valid, 64U + 16U);

    std::vector<std::uint8_t> missing_macro = valid;
    missing_macro[binding_offset + 5U] = 254U;
    repair_crc(missing_macro);
    CHECK(rejects(missing_macro));

    std::vector<std::uint8_t> missing_profile = valid;
    missing_profile[binding_offset + 12U + 5U] = 9U;
    repair_crc(missing_profile);
    CHECK(rejects(missing_profile));
}

TEST_CASE(config_validator_rejects_each_independent_malformed_generated_step_payload) {
    using duo_input::protocol::MacroStepType;
    const std::vector<std::uint8_t> valid = read_vector("valid_full.bin");

    CHECK(rejects(mutate_step_payload(valid, MacroStepType::CONSUMER_TAP, 0U, 0U)));
    CHECK(rejects(mutate_step_payload(valid, MacroStepType::TEXT, 1U, 0U)));
    CHECK(rejects(mutate_step_payload(valid, MacroStepType::DELAY, 2U, 0xFFU)));
    CHECK(rejects(mutate_step_payload(valid, MacroStepType::DELAY, 3U, 0xFFU)));
    CHECK(rejects(mutate_step_payload(valid, MacroStepType::SET_KEYBOARD_ROUTE, 0U, 0U)));
    CHECK(rejects(mutate_step_payload(valid, MacroStepType::SET_MOUSE_ROUTE, 0U, 0U)));
    CHECK(rejects(mutate_step_payload(valid, MacroStepType::SET_MOUSE_ROUTE, 0U, 4U)));
    CHECK(rejects(mutate_step_payload(valid, MacroStepType::SET_PROFILE, 0U, 9U)));
}
