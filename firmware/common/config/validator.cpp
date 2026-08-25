#include "config/validator.hpp"

#include <limits>

namespace duo_input::config {
namespace {

constexpr std::size_t HEADER_CRC_OFFSET = 12U;
constexpr std::size_t PROFILE_TABLE_OFFSET = CONFIG_HEADER_SIZE;
constexpr std::uint8_t MAGIC[4] = {'D', 'U', 'O', 'C'};

bool has_region(protocol::ByteView bytes, std::size_t offset, std::size_t length) {
    return offset <= bytes.size && length <= bytes.size - offset;
}

bool checked_table(protocol::ByteView bytes, std::size_t offset, std::size_t count,
                   std::size_t record_size, std::size_t& end) {
    if (record_size != 0U && count > std::numeric_limits<std::size_t>::max() / record_size) {
        return false;
    }
    const std::size_t length = count * record_size;
    if (!has_region(bytes, offset, length)) {
        return false;
    }
    end = offset + length;
    return true;
}

bool align4(std::size_t value, std::size_t& aligned) {
    if (value > std::numeric_limits<std::size_t>::max() - 3U) {
        return false;
    }
    aligned = (value + 3U) & ~std::size_t{3U};
    return true;
}

std::uint16_t read_u16(protocol::ByteView bytes, std::size_t offset) {
    return static_cast<std::uint16_t>(bytes.data[offset]) |
           static_cast<std::uint16_t>(static_cast<std::uint16_t>(bytes.data[offset + 1U]) << 8U);
}

std::uint32_t read_u32(protocol::ByteView bytes, std::size_t offset) {
    return static_cast<std::uint32_t>(bytes.data[offset]) |
           (static_cast<std::uint32_t>(bytes.data[offset + 1U]) << 8U) |
           (static_cast<std::uint32_t>(bytes.data[offset + 2U]) << 16U) |
           (static_cast<std::uint32_t>(bytes.data[offset + 3U]) << 24U);
}

bool all_zero(protocol::ByteView bytes, std::size_t begin, std::size_t end) {
    if (begin > end || !has_region(bytes, begin, end - begin)) {
        return false;
    }
    for (std::size_t index = begin; index < end; ++index) {
        if (bytes.data[index] != 0U) {
            return false;
        }
    }
    return true;
}

std::uint32_t config_crc(protocol::ByteView bytes) {
    std::uint32_t crc = 0xFFFFFFFFU;
    for (std::size_t index = 0; index < bytes.size; ++index) {
        const std::uint8_t value = index >= HEADER_CRC_OFFSET && index < HEADER_CRC_OFFSET + 4U
                                       ? 0U
                                       : bytes.data[index];
        crc ^= value;
        for (int bit = 0; bit < 8; ++bit) {
            crc = (crc & 1U) != 0U ? (crc >> 1U) ^ 0xEDB88320U : crc >> 1U;
        }
    }
    return crc ^ 0xFFFFFFFFU;
}

bool valid_route(std::uint8_t value) {
    return value >= static_cast<std::uint8_t>(Route::U1) &&
           value <= static_cast<std::uint8_t>(Route::BOTH);
}

bool valid_layout(std::uint8_t value) {
    return value >= static_cast<std::uint8_t>(TextLayout::US) &&
           value <= static_cast<std::uint8_t>(TextLayout::DE);
}

bool valid_utf8_name(protocol::ByteView bytes, std::size_t offset, std::size_t length) {
    if (!has_region(bytes, offset, length)) {
        return false;
    }
    std::size_t index = offset;
    const std::size_t end = offset + length;
    std::size_t code_points = 0U;
    while (index < end) {
        if (++code_points > 48U) {
            return false;
        }
        const std::uint8_t first = bytes.data[index++];
        if (first == 0U) {
            return false;
        }
        std::size_t continuation_count = 0U;
        std::uint32_t value = 0U;
        std::uint32_t minimum = 0U;
        if (first <= 0x7FU) {
            continue;
        }
        if (first >= 0xC2U && first <= 0xDFU) {
            continuation_count = 1U;
            value = first & 0x1FU;
            minimum = 0x80U;
        } else if (first >= 0xE0U && first <= 0xEFU) {
            continuation_count = 2U;
            value = first & 0x0FU;
            minimum = 0x800U;
        } else if (first >= 0xF0U && first <= 0xF4U) {
            continuation_count = 3U;
            value = first & 0x07U;
            minimum = 0x10000U;
        } else {
            return false;
        }
        if (continuation_count > end - index) {
            return false;
        }
        for (std::size_t count = 0; count < continuation_count; ++count) {
            const std::uint8_t continuation = bytes.data[index++];
            if ((continuation & 0xC0U) != 0x80U) {
                return false;
            }
            value = (value << 6U) | (continuation & 0x3FU);
        }
        if (value < minimum || value > 0x10FFFFU || (value >= 0xD800U && value <= 0xDFFFU)) {
            return false;
        }
    }
    return true;
}

bool consume_name(protocol::ByteView bytes, std::size_t string_end, std::size_t offset,
                  std::size_t length, std::size_t& expected) {
    if (offset != expected || offset > string_end || !has_region(bytes, offset, length) ||
        length > string_end - offset ||
        !valid_utf8_name(bytes, offset, length)) {
        return false;
    }
    expected = offset + length;
    return true;
}

bool valid_macro_id(protocol::ByteView bytes, std::size_t macro_offset, std::size_t macro_count,
                    std::uint8_t id) {
    if (id == 0U) {
        return false;
    }
    for (std::size_t index = 0; index < macro_count; ++index) {
        if (bytes.data[macro_offset + index * MACRO_DESCRIPTOR_SIZE] == id) {
            return true;
        }
    }
    return false;
}

bool validate_step_payload(protocol::ByteView bytes, std::uint8_t type, std::size_t offset,
                           std::size_t length) {
    if (!has_region(bytes, offset, length)) {
        return false;
    }
    switch (static_cast<protocol::MacroStepType>(type)) {
        case protocol::MacroStepType::KEY_TAP:
        case protocol::MacroStepType::KEY_DOWN:
        case protocol::MacroStepType::KEY_UP:
            return length == 2U && bytes.data[offset + 1U] != 0U;
        case protocol::MacroStepType::CONSUMER_TAP:
            return length == 2U && read_u16(bytes, offset) != 0U;
        case protocol::MacroStepType::TEXT:
            if (length == 0U || (length & 1U) != 0U) {
                return false;
            }
            for (std::size_t index = 1U; index < length; index += 2U) {
                if (bytes.data[offset + index] == 0U) {
                    return false;
                }
            }
            return true;
        case protocol::MacroStepType::DELAY:
            return length == 4U && read_u16(bytes, offset) <= read_u16(bytes, offset + 2U) &&
                   read_u16(bytes, offset + 2U) <= protocol::ProtocolLimits::MAX_DELAY_MS;
        case protocol::MacroStepType::SET_KEYBOARD_ROUTE:
        case protocol::MacroStepType::SET_MOUSE_ROUTE:
            return length == 1U && valid_route(bytes.data[offset]);
        case protocol::MacroStepType::SET_PROFILE:
            return length == 1U && bytes.data[offset] >= 1U &&
                   bytes.data[offset] <= protocol::ProtocolLimits::PROFILES;
        default:
            return false;
    }
}

bool validate_binding(protocol::ByteView bytes, std::size_t offset, std::size_t macro_offset,
                      std::size_t macro_count) {
    const std::uint8_t kind = bytes.data[offset];
    const std::uint8_t code = bytes.data[offset + 1U];
    const std::uint8_t modifiers = bytes.data[offset + 2U];
    const std::uint8_t mode = bytes.data[offset + 3U];
    const std::uint8_t action = bytes.data[offset + 4U];
    const std::uint8_t argument = bytes.data[offset + 5U];
    if ((kind != static_cast<std::uint8_t>(TriggerKind::KEYBOARD_USAGE) &&
         kind != static_cast<std::uint8_t>(TriggerKind::MOUSE_BUTTON)) ||
        code == 0U ||
        (kind == static_cast<std::uint8_t>(TriggerKind::MOUSE_BUTTON) &&
         (code > 5U || modifiers != 0U)) ||
        (mode != static_cast<std::uint8_t>(BindingMode::REPLACE) &&
         mode != static_cast<std::uint8_t>(BindingMode::ADD)) ||
        !all_zero(bytes, offset + 6U, offset + BINDING_RECORD_SIZE)) {
        return false;
    }
    switch (static_cast<ActionKind>(action)) {
        case ActionKind::RUN_MACRO:
            return valid_macro_id(bytes, macro_offset, macro_count, argument);
        case ActionKind::TOGGLE_KEYBOARD_ROUTE:
        case ActionKind::TOGGLE_MOUSE_ROUTE:
            return argument == 0U;
        case ActionKind::SET_KEYBOARD_ROUTE:
        case ActionKind::SET_MOUSE_ROUTE:
            return valid_route(argument);
        case ActionKind::SET_PROFILE:
            return argument >= 1U && argument <= protocol::ProtocolLimits::PROFILES;
        default:
            return false;
    }
}

ValidationResult failure(ValidationError error) { return ValidationResult{error}; }

}  // namespace

ValidationResult validate_config(protocol::ByteView input) {
    if ((input.data == nullptr && input.size != 0U)) {
        return failure(ValidationError::INVALID_INPUT);
    }
    if (input.size < CONFIG_HEADER_SIZE ||
        input.size > protocol::ProtocolLimits::BINARY_CONFIG_MAX_BYTES) {
        return failure(ValidationError::INVALID_LENGTH);
    }
    if (input.data[0] != MAGIC[0] || input.data[1] != MAGIC[1] || input.data[2] != MAGIC[2] ||
        input.data[3] != MAGIC[3] || input.data[4] != protocol::SCHEMA_VERSION_MAJOR ||
        input.data[6] != 0U || input.data[7] != 0U || read_u32(input, 8U) != input.size) {
        return failure(ValidationError::INVALID_FORMAT);
    }
    if (read_u32(input, HEADER_CRC_OFFSET) != config_crc(input)) {
        return failure(ValidationError::INVALID_CRC);
    }
    if (input.data[16] != protocol::ProtocolLimits::PROFILES || input.data[17] < 1U ||
        input.data[17] > protocol::ProtocolLimits::PROFILES ||
        input.data[18] != PROFILE_DESCRIPTOR_SIZE || input.data[19] != 0U ||
        read_u32(input, 20U) != PROFILE_TABLE_OFFSET || !all_zero(input, 40U, 64U)) {
        return failure(ValidationError::INVALID_FORMAT);
    }

    std::size_t profile_end = 0U;
    if (!checked_table(input, PROFILE_TABLE_OFFSET, protocol::ProtocolLimits::PROFILES,
                       PROFILE_DESCRIPTOR_SIZE, profile_end)) {
        return failure(ValidationError::INVALID_LENGTH);
    }
    const std::size_t string_offset = read_u32(input, 24U);
    const std::size_t string_length = read_u32(input, 28U);
    const std::size_t data_offset = read_u32(input, 32U);
    const std::size_t data_length = read_u32(input, 36U);
    if (string_offset != profile_end || !has_region(input, string_offset, string_length)) {
        return failure(ValidationError::INVALID_FORMAT);
    }
    const std::size_t string_end = string_offset + string_length;
    std::size_t aligned_string_end = 0U;
    if (!align4(string_end, aligned_string_end) || data_offset != aligned_string_end ||
        (data_offset & 3U) != 0U || !all_zero(input, string_end, data_offset) ||
        !has_region(input, data_offset, data_length) || data_length != input.size - data_offset) {
        return failure(ValidationError::INVALID_FORMAT);
    }

    std::size_t expected_string = string_offset;
    for (std::size_t profile_index = 0; profile_index < protocol::ProtocolLimits::PROFILES;
         ++profile_index) {
        const std::size_t profile = PROFILE_TABLE_OFFSET + profile_index * PROFILE_DESCRIPTOR_SIZE;
        const std::size_t binding_count = read_u16(input, profile + 14U);
        const std::size_t macro_count = read_u16(input, profile + 22U);
        if (input.data[profile] != profile_index + 1U || !valid_route(input.data[profile + 1U]) ||
            !valid_route(input.data[profile + 2U]) || !valid_layout(input.data[profile + 3U]) ||
            input.data[profile + 7U] != 0U ||
            binding_count > protocol::ProtocolLimits::BINDINGS_PER_PROFILE ||
            read_u16(input, profile + 20U) != BINDING_RECORD_SIZE ||
            macro_count > protocol::ProtocolLimits::MACROS_PER_PROFILE ||
            read_u16(input, profile + 28U) != MACRO_DESCRIPTOR_SIZE ||
            read_u16(input, profile + 30U) != 0U || read_u32(input, profile + 32U) != 0U ||
            !consume_name(input, string_end, read_u32(input, profile + 8U),
                          read_u16(input, profile + 12U), expected_string)) {
            return failure(ValidationError::INVALID_FORMAT);
        }
    }

    std::size_t cursor = data_offset;
    for (std::size_t profile_index = 0; profile_index < protocol::ProtocolLimits::PROFILES;
         ++profile_index) {
        const std::size_t profile = PROFILE_TABLE_OFFSET + profile_index * PROFILE_DESCRIPTOR_SIZE;
        const std::size_t binding_count = read_u16(input, profile + 14U);
        const std::size_t binding_offset = read_u32(input, profile + 16U);
        const std::size_t macro_count = read_u16(input, profile + 22U);
        const std::size_t macro_offset = read_u32(input, profile + 24U);
        if (binding_offset != cursor || (binding_offset & 3U) != 0U ||
            !checked_table(input, binding_offset, binding_count, BINDING_RECORD_SIZE, cursor) ||
            macro_offset != cursor || (macro_offset & 3U) != 0U ||
            !checked_table(input, macro_offset, macro_count, MACRO_DESCRIPTOR_SIZE, cursor)) {
            return failure(ValidationError::INVALID_FORMAT);
        }

        for (std::size_t macro_index = 0; macro_index < macro_count; ++macro_index) {
            const std::size_t macro = macro_offset + macro_index * MACRO_DESCRIPTOR_SIZE;
            const std::uint8_t macro_id = input.data[macro];
            const std::size_t step_count = read_u16(input, macro + 10U);
            const std::size_t step_offset = read_u32(input, macro + 12U);
            if (macro_id == 0U || !valid_route(input.data[macro + 1U]) ||
                read_u16(input, macro + 2U) != 0U ||
                step_count > protocol::ProtocolLimits::MACRO_STEPS_PER_MACRO ||
                read_u16(input, macro + 16U) != STEP_DESCRIPTOR_SIZE ||
                read_u16(input, macro + 18U) != 0U || read_u32(input, macro + 20U) != 0U ||
                !consume_name(input, string_end, read_u32(input, macro + 4U),
                              read_u16(input, macro + 8U), expected_string) ||
                step_offset != cursor || (step_offset & 3U) != 0U ||
                !checked_table(input, step_offset, step_count, STEP_DESCRIPTOR_SIZE, cursor)) {
                return failure(ValidationError::INVALID_FORMAT);
            }
            for (std::size_t previous = 0; previous < macro_index; ++previous) {
                if (input.data[macro_offset + previous * MACRO_DESCRIPTOR_SIZE] == macro_id) {
                    return failure(ValidationError::INVALID_FORMAT);
                }
            }
            for (std::size_t step_index = 0; step_index < step_count; ++step_index) {
                const std::size_t step = step_offset + step_index * STEP_DESCRIPTOR_SIZE;
                const std::size_t payload_length = read_u16(input, step + 2U);
                const std::size_t payload_offset = read_u32(input, step + 4U);
                std::size_t aligned_cursor = 0U;
                if (input.data[step + 1U] != 0U || read_u32(input, step + 8U) != 0U ||
                    !align4(cursor, aligned_cursor) || payload_offset != aligned_cursor ||
                    !all_zero(input, cursor, aligned_cursor) ||
                    !has_region(input, payload_offset, payload_length) ||
                    !validate_step_payload(input, input.data[step], payload_offset, payload_length)) {
                    return failure(ValidationError::INVALID_FORMAT);
                }
                cursor = payload_offset + payload_length;
            }
            std::size_t aligned_cursor = 0U;
            if (!align4(cursor, aligned_cursor) || !all_zero(input, cursor, aligned_cursor)) {
                return failure(ValidationError::INVALID_FORMAT);
            }
            cursor = aligned_cursor;
        }

        for (std::size_t binding_index = 0; binding_index < binding_count; ++binding_index) {
            const std::size_t binding = binding_offset + binding_index * BINDING_RECORD_SIZE;
            if (!validate_binding(input, binding, macro_offset, macro_count)) {
                return failure(ValidationError::INVALID_FORMAT);
            }
            for (std::size_t previous = 0; previous < binding_index; ++previous) {
                const std::size_t earlier = binding_offset + previous * BINDING_RECORD_SIZE;
                if (input.data[earlier] == input.data[binding] &&
                    input.data[earlier + 1U] == input.data[binding + 1U] &&
                    input.data[earlier + 2U] == input.data[binding + 2U]) {
                    return failure(ValidationError::INVALID_FORMAT);
                }
            }
        }
    }
    if (expected_string != string_end || cursor != input.size) {
        return failure(ValidationError::INVALID_FORMAT);
    }
    return {ValidationError::NONE, ConfigView{input}};
}

std::uint8_t ConfigView::active_profile_id() const {
    return bytes_.data == nullptr ? 0U : bytes_.data[17U];
}
std::size_t ConfigView::profile_count() const {
    return bytes_.data == nullptr ? 0U : bytes_.data[16U];
}
bool ConfigView::profile_at(std::size_t index, ProfileView& output) const {
    if (index >= profile_count()) return false;
    output = ProfileView{bytes_, PROFILE_TABLE_OFFSET + index * PROFILE_DESCRIPTOR_SIZE};
    return true;
}

std::uint8_t ProfileView::id() const { return bytes_.data[offset_]; }
Route ProfileView::keyboard_route() const { return static_cast<Route>(bytes_.data[offset_ + 1U]); }
Route ProfileView::mouse_route() const { return static_cast<Route>(bytes_.data[offset_ + 2U]); }
TextLayout ProfileView::text_layout() const { return static_cast<TextLayout>(bytes_.data[offset_ + 3U]); }
protocol::ByteView ProfileView::name() const {
    return {bytes_.data + read_u32(bytes_, offset_ + 8U), read_u16(bytes_, offset_ + 12U)};
}
std::size_t ProfileView::binding_count() const { return read_u16(bytes_, offset_ + 14U); }
bool ProfileView::binding_at(std::size_t index, BindingView& output) const {
    if (index >= binding_count()) return false;
    output = BindingView{bytes_, read_u32(bytes_, offset_ + 16U) + index * BINDING_RECORD_SIZE};
    return true;
}
std::size_t ProfileView::macro_count() const { return read_u16(bytes_, offset_ + 22U); }
bool ProfileView::macro_at(std::size_t index, MacroView& output) const {
    if (index >= macro_count()) return false;
    output = MacroView{bytes_, read_u32(bytes_, offset_ + 24U) + index * MACRO_DESCRIPTOR_SIZE};
    return true;
}

TriggerKind BindingView::trigger_kind() const { return static_cast<TriggerKind>(bytes_.data[offset_]); }
std::uint8_t BindingView::trigger_code() const { return bytes_.data[offset_ + 1U]; }
std::uint8_t BindingView::trigger_modifiers() const { return bytes_.data[offset_ + 2U]; }
BindingMode BindingView::mode() const { return static_cast<BindingMode>(bytes_.data[offset_ + 3U]); }
ActionKind BindingView::action_kind() const { return static_cast<ActionKind>(bytes_.data[offset_ + 4U]); }
std::uint8_t BindingView::action_argument() const { return bytes_.data[offset_ + 5U]; }

std::uint8_t MacroView::id() const { return bytes_.data[offset_]; }
Route MacroView::target() const { return static_cast<Route>(bytes_.data[offset_ + 1U]); }
protocol::ByteView MacroView::name() const {
    return {bytes_.data + read_u32(bytes_, offset_ + 4U), read_u16(bytes_, offset_ + 8U)};
}
std::size_t MacroView::step_count() const { return read_u16(bytes_, offset_ + 10U); }
bool MacroView::step_at(std::size_t index, StepView& output) const {
    if (index >= step_count()) return false;
    output = StepView{bytes_, read_u32(bytes_, offset_ + 12U) + index * STEP_DESCRIPTOR_SIZE};
    return true;
}

protocol::MacroStepType StepView::type() const {
    return static_cast<protocol::MacroStepType>(bytes_.data[offset_]);
}
protocol::ByteView StepView::payload() const {
    return {bytes_.data + read_u32(bytes_, offset_ + 4U), read_u16(bytes_, offset_ + 2U)};
}

}  // namespace duo_input::config
