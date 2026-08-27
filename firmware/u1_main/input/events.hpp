#pragma once

// What a report turned out to mean.
//
// A HID report says what is true now. These say what changed, which is what
// the far side needs: a computer receiving input is told about edges, not
// about a state it is expected to diff for itself.
//
// One flat struct rather than a variant, because every one of these crosses a
// 64-byte SPI frame and a fixed shape costs nothing to encode. The fields a
// given kind does not use are zero.

#include <cstddef>
#include <cstdint>

namespace duo_input::u1::input {

enum class InputEventKind : std::uint8_t {
    None,
    /// ``code`` is a HID keyboard usage. Modifiers arrive as usages too -
    /// 0xE0 to 0xE7 - because on the far side a modifier is a key like any
    /// other, and nothing will be shifted if it does not.
    KeyDown,
    KeyUp,
    /// ``code`` is a consumer-page usage: volume, play, and the rest.
    ConsumerDown,
    ConsumerUp,
    /// ``code`` is the button index, counting from zero.
    MouseButtonDown,
    MouseButtonUp,
    /// ``x`` and ``y``, both signed.
    MouseMove,
    /// ``wheel`` and ``pan``, both signed.
    Wheel,
    DeviceConnected,
    DeviceDisconnected,
};

struct InputEvent {
    InputEventKind kind = InputEventKind::None;
    std::uint16_t code = 0;
    std::int16_t x = 0;
    std::int16_t y = 0;
    std::int8_t wheel = 0;
    std::int8_t pan = 0;
};

/// The most a single report can produce.
///
/// A boot keyboard can change all eight modifiers and all six key slots at
/// once, which is fourteen presses and fourteen releases; a mouse cannot come
/// close. Fixed, because this runs on a chip with no allocator and a report
/// that produced more than expected must be truncated rather than trusted.
inline constexpr std::size_t kMaxEventsPerReport = 28;

}  // namespace duo_input::u1::input
