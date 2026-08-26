#pragma once

// The shapes of a HID report, and nothing else.
//
// These types are shared by U1 and U2 and by the host tests, so they contain
// no TinyUSB, no Pico SDK and no allocation. A report is a fixed number of
// bytes; everything here is sized to match it exactly.

#include <cstddef>
#include <cstdint>

namespace duo_input::hid {

/// Which computer a piece of input is meant for.
enum class Target : std::uint8_t {
    Pc1 = 0,
    Pc2 = 1,
};

/// How many targets exist. Two, and the code says so once.
inline constexpr std::size_t kTargetCount = 2;

/// A boot-compatible keyboard report carries six usages and no more.
inline constexpr std::uint8_t kMaxKeys = 6;

/// HID usages this firmware will hold. 0 means "no key" in a report, so it is
/// not a usage anything can press.
inline constexpr std::uint8_t kMinUsage = 1;
inline constexpr std::uint8_t kMaxUsage = 0xFF;

/// Macro owners are identified by a small index, not a pointer.
inline constexpr std::uint8_t kMaxMacroOwners = 32;

/// The five buttons the mouse report describes.
enum class MouseButton : std::uint8_t {
    Left = 0x01,
    Right = 0x02,
    Middle = 0x04,
    Backward = 0x08,
    Forward = 0x10,
    All = 0x1F,
};

/// What went wrong, if anything. Every mutating call answers with one of these
/// rather than silently doing nothing.
enum class HidResult : std::uint8_t {
    Ok = 0,
    /// The sixth key is already held; a seventh cannot be reported.
    KeyCapacity,
    /// A usage outside the range a report can carry.
    BadUsage,
    /// A macro owner index outside the table.
    BadOwner,
    /// A button bit outside the five the report describes.
    BadButton,
};

/// The keyboard half of one computer's state.
struct KeyboardSnapshot {
    std::uint8_t modifiers = 0;
    std::uint8_t key_count = 0;
    std::uint8_t keys[kMaxKeys] = {};

    /// Is ``usage`` among the keys currently held?
    constexpr bool contains(std::uint8_t usage) const {
        for (std::uint8_t index = 0; index < key_count; ++index) {
            if (keys[index] == usage) {
                return true;
            }
        }
        return false;
    }
};

/// The mouse half of one computer's state.
///
/// Buttons are absolute - they are whatever is held right now. Movement is a
/// delta that accumulates until it is sent, because a mouse report says how
/// far the pointer moved since the last one.
struct MouseSnapshot {
    std::uint8_t buttons = 0;
    std::int16_t delta_x = 0;
    std::int16_t delta_y = 0;
    std::int8_t wheel = 0;
    std::int8_t pan = 0;
};

/// Everything one computer is being told at this instant.
struct TargetSnapshot {
    KeyboardSnapshot keyboard;
    MouseSnapshot mouse;
};

}  // namespace duo_input::hid
