#pragma once

// One thing the operator asked a key to do.
//
// A binding is a trigger and an action, plus the one distinction that decides
// what the far side sees: Replace swallows the key that triggered it, Add lets
// it through as well. Getting that backwards gives either a key that appears
// to do nothing or a key that does its job and types a character nobody
// wanted.

#include <cstdint>

#include "config/format.hpp"

namespace duo_input::u1::mapping {

struct Binding {
    config::TriggerKind trigger = config::TriggerKind::KEYBOARD_USAGE;
    /// A HID usage for a key, or a button index for a mouse button.
    std::uint16_t code = 0;
    /// Modifiers that must be held for this binding to apply at all. Zero
    /// means the binding does not care.
    std::uint8_t required_modifiers = 0;
    config::BindingMode mode = config::BindingMode::REPLACE;
    config::ActionKind action = config::ActionKind::RUN_MACRO;
    /// A macro index, a route, or a profile - whichever the action needs.
    std::uint8_t parameter = 0;
};

/// How many bindings one profile may carry.
///
/// Fixed, because this runs on a chip with no allocator, and a configuration
/// that wants more is rejected by the validator before it ever reaches here.
inline constexpr std::size_t kMaxBindings = 64;

}  // namespace duo_input::u1::mapping
