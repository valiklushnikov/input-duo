#pragma once

// A boot mouse report, turned into movement, buttons and wheel.
//
// Buttons are a state and arrive as edges; movement is not a state at all -
// each report carries however far the mouse went since the last one, so it is
// passed through rather than compared. The difference matters when a device
// disappears: buttons have to be released, and a pointer that jumps when a
// cable is pulled is worse than one that stops.

#include <cstddef>
#include <cstdint>

#include "input/events.hpp"
#include "protocol/bytes.hpp"

namespace duo_input::u1::input {

/// Buttons, X, Y. A wheel byte usually follows.
inline constexpr std::size_t kBootMouseReportSize = 3;

/// How many buttons are carried. Five is what a wired mouse offers, and the
/// side buttons are most of why somebody buys one.
inline constexpr std::size_t kMouseButtons = 5;

class MouseNormalizer {
public:
    /// Some mice put an identifier in front of every report.
    ///
    /// Reading that byte as the buttons puts a click on every movement, which
    /// is exactly what the mouse on the bench did produce.
    ///
    /// Nothing in the firmware calls this. Devices are asked to use boot
    /// protocol while they are being brought up (see descriptor_setup.hpp),
    /// and a boot report has no identifier in it - so the way out of the
    /// identifier is not to read past it but to stop being sent it. This is
    /// here for the device that refuses, and would be set from its report
    /// descriptor, which is not fetched.
    void set_report_id(bool present) { report_id_ = present; }

    std::size_t apply(protocol::ByteView report, InputEvent* out, std::size_t capacity);

    /// Release whatever buttons are remembered as held. Movement is not undone.
    std::size_t release_all(InputEvent* out, std::size_t capacity);

private:
    bool report_id_ = false;
    std::uint8_t buttons_ = 0;
};

}  // namespace duo_input::u1::input
