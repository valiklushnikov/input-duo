#include "input/mouse_normalizer.hpp"

namespace duo_input::u1::input {
namespace {

bool emit(InputEvent* out, std::size_t capacity, std::size_t& used, const InputEvent& event) {
    if (used >= capacity) {
        return false;
    }
    out[used++] = event;
    return true;
}

/// A movement byte is signed. Read unsigned, a small step left becomes a leap
/// across the screen.
std::int16_t signed_byte(std::uint8_t value) {
    return static_cast<std::int16_t>(static_cast<std::int8_t>(value));
}

}  // namespace

std::size_t MouseNormalizer::apply(protocol::ByteView report, InputEvent* out,
                                   std::size_t capacity) {
    if (report.data == nullptr || out == nullptr) {
        return 0;
    }

    // Some mice put an identifier in front of every report. Taking that byte
    // for the buttons puts a click on every movement.
    const std::size_t offset = report_id_ ? 1 : 0;
    if (report.size < offset + kBootMouseReportSize) {
        return 0;
    }
    const std::uint8_t* body = report.data + offset;
    const std::size_t body_size = report.size - offset;

    std::size_t used = 0;

    // --- buttons ------------------------------------------------------------
    //
    // A state, so only the changes are worth sending. A button held across a
    // hundred reports is one press.
    const std::uint8_t buttons = body[0];
    const std::uint8_t changed = static_cast<std::uint8_t>(buttons ^ buttons_);
    for (std::size_t index = 0; index < kMouseButtons; ++index) {
        const std::uint8_t mask = static_cast<std::uint8_t>(1u << index);
        if ((changed & mask) == 0) {
            continue;
        }
        InputEvent event;
        event.kind = (buttons & mask) != 0 ? InputEventKind::MouseButtonDown
                                           : InputEventKind::MouseButtonUp;
        event.code = static_cast<std::uint16_t>(index);
        emit(out, capacity, used, event);
    }
    buttons_ = buttons;

    // --- movement -----------------------------------------------------------
    //
    // Not a state: each report carries how far the mouse went since the last
    // one, so it is passed through rather than compared. Nothing is sent when
    // it did not move, because a polled mouse says "nowhere" constantly.
    const std::int16_t dx = signed_byte(body[1]);
    const std::int16_t dy = signed_byte(body[2]);
    if (dx != 0 || dy != 0) {
        InputEvent event;
        event.kind = InputEventKind::MouseMove;
        event.x = dx;
        event.y = dy;
        emit(out, capacity, used, event);
    }

    // --- wheel --------------------------------------------------------------
    if (body_size >= 4) {
        const std::int16_t wheel = signed_byte(body[3]);
        const std::int16_t pan = body_size >= 5 ? signed_byte(body[4]) : 0;
        if (wheel != 0 || pan != 0) {
            InputEvent event;
            event.kind = InputEventKind::Wheel;
            event.wheel = static_cast<std::int8_t>(wheel);
            event.pan = static_cast<std::int8_t>(pan);
            emit(out, capacity, used, event);
        }
    }

    return used;
}

std::size_t MouseNormalizer::release_all(InputEvent* out, std::size_t capacity) {
    if (out == nullptr) {
        return 0;
    }
    std::size_t used = 0;

    for (std::size_t index = 0; index < kMouseButtons; ++index) {
        const std::uint8_t mask = static_cast<std::uint8_t>(1u << index);
        if ((buttons_ & mask) == 0) {
            continue;
        }
        InputEvent event;
        event.kind = InputEventKind::MouseButtonUp;
        event.code = static_cast<std::uint16_t>(index);
        emit(out, capacity, used, event);
    }
    buttons_ = 0;

    // Movement is not a state that can be left held, so there is nothing to
    // undo - and a pointer that jumps when a cable is pulled is worse than one
    // that stops where it was.
    return used;
}

}  // namespace duo_input::u1::input
