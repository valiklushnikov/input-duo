#pragma once

// A mouse report, turned into movement, buttons and wheel.
//
// Which bytes mean what is the device's business, not this one's. A layout
// says where the fields sit and whether an identifier leads every report; it
// comes from the device's own HID report descriptor, fetched during bring-up
// (ch375/descriptor_setup.hpp). Until one is set - and for every device whose
// descriptor could not be fetched or could not be parsed - this reads a boot
// report, which is buttons, dX and dY, and a wheel byte if a fourth arrives.
//
// Buttons are a state and arrive as edges; movement is not a state at all -
// each report carries however far the mouse went since the last one, so it is
// passed through rather than compared. The difference matters when a device
// disappears: buttons have to be released, and a pointer that jumps when a
// cable is pulled is worse than one that stops.

#include <cstddef>
#include <cstdint>

#include "input/events.hpp"
#include "input/hid/report_descriptor.hpp"
#include "protocol/bytes.hpp"

namespace duo_input::u1::input {

/// Buttons, X, Y - the whole of a boot report, and the shortest report this
/// firmware routes. The traces in tests/vectors are all this shape.
inline constexpr std::size_t kBootMouseReportSize = 3;

/// How many buttons are carried. Five is what a wired mouse offers, and the
/// side buttons are most of why somebody buys one.
inline constexpr std::size_t kMouseButtons = 5;

class MouseNormalizer {
public:
    /// Where this device keeps its fields, according to the device.
    ///
    /// Set once, when the device is declared ready, from whatever its report
    /// descriptor said. A device that would not give one up keeps the boot
    /// layout this starts on, which is the layout every mouse here has been
    /// read under so far.
    void set_layout(const hid::MouseReportLayout& layout) { layout_ = layout; }

    std::size_t apply(protocol::ByteView report, InputEvent* out, std::size_t capacity);

    /// Release whatever buttons are remembered as held. Movement is not undone.
    std::size_t release_all(InputEvent* out, std::size_t capacity);

private:
    hid::MouseReportLayout layout_ = hid::boot_mouse_layout();
    std::uint8_t buttons_ = 0;
};

}  // namespace duo_input::u1::input
