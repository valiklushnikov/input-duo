#pragma once

#include <cstddef>
#include <cstdint>

namespace duo_input::config {

inline constexpr std::size_t CONFIG_HEADER_SIZE = 64U;
inline constexpr std::size_t PROFILE_DESCRIPTOR_SIZE = 36U;
inline constexpr std::size_t BINDING_RECORD_SIZE = 12U;
inline constexpr std::size_t MACRO_DESCRIPTOR_SIZE = 24U;
inline constexpr std::size_t STEP_DESCRIPTOR_SIZE = 12U;

enum class Route : std::uint8_t { U1 = 1, U2 = 2, BOTH = 3 };
enum class TextLayout : std::uint8_t { US = 1, UK = 2, DE = 3 };
enum class TriggerKind : std::uint8_t { KEYBOARD_USAGE = 1, MOUSE_BUTTON = 2 };
enum class BindingMode : std::uint8_t { REPLACE = 1, ADD = 2 };
enum class ActionKind : std::uint8_t {
    RUN_MACRO = 1,
    TOGGLE_KEYBOARD_ROUTE = 2,
    SET_KEYBOARD_ROUTE = 3,
    TOGGLE_MOUSE_ROUTE = 4,
    SET_MOUSE_ROUTE = 5,
    SET_PROFILE = 6,
};

}  // namespace duo_input::config
