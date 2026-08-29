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

/// Read one field out of a report body, or say it is not there.
///
/// A field is only readable if the device declared it *and* enough of the
/// report arrived to hold it. The second half is not a formality: a layout
/// says what the device promises to send, and what arrives is however many
/// bytes the controller actually collected. Reading past that is movement
/// nobody made, on somebody's other computer.
///
/// Values are signed. A movement byte read unsigned turns a small step left
/// into a leap across the screen, and the same is true of a wheel notch.
bool read_field(const ch375::ReportField& field, const std::uint8_t* body,
                std::size_t body_size, std::int16_t& out) {
    if (!field.present || field.bytes == 0) {
        return false;
    }
    const std::uint8_t width = field.bits == 0
                                   ? static_cast<std::uint8_t>(field.bytes * 8)
                                   : field.bits;
    if (width == 0 || width > 16 || field.bit_offset > 7) {
        return false;
    }
    const std::size_t bytes =
        (static_cast<std::size_t>(field.bit_offset) + width + 7) / 8;
    if (bytes > field.bytes || bytes > 3) {
        return false;
    }
    const std::size_t end = static_cast<std::size_t>(field.offset) + bytes;
    if (end > body_size) {
        return false;
    }

    // USB numbers are little-endian, but HID fields are bit streams and need
    // not start on a byte. Assemble only the bytes touched, shift the declared
    // field down, then sign-extend its own width rather than the container's.
    std::uint32_t container = 0;
    for (std::size_t byte = 0; byte < bytes; ++byte) {
        container |= static_cast<std::uint32_t>(body[field.offset + byte]) << (8 * byte);
    }
    const std::uint32_t mask = (std::uint32_t{1} << width) - 1;
    std::uint32_t raw = (container >> field.bit_offset) & mask;
    const std::uint32_t sign = std::uint32_t{1} << (width - 1);
    if ((raw & sign) != 0) {
        raw |= ~mask;
    }
    out = static_cast<std::int16_t>(static_cast<std::int32_t>(raw));
    return true;
}

/// A wheel is one byte's worth of notches however wide the field is.
std::int8_t clamped(std::int16_t value) {
    if (value > 127) {
        return 127;
    }
    if (value < -128) {
        return -128;
    }
    return static_cast<std::int8_t>(value);
}

}  // namespace

std::size_t MouseNormalizer::apply(protocol::ByteView report, InputEvent* out,
                                   std::size_t capacity) {
    if (report.data == nullptr || out == nullptr) {
        return 0;
    }

    // Some mice put an identifier in front of every report. Taking that byte
    // for the buttons puts a click on every movement.
    std::size_t offset = 0;
    if (layout_.report_id) {
        if (report.size < 1) {
            return 0;
        }
        if (report.data[0] != layout_.report_id_value) {
            // Another collection on the same endpoint - media keys, a battery
            // level. Read as movement it is a click and a jump across the
            // screen, so it is not read at all.
            return 0;
        }
        offset = 1;
    }
    if (report.size < offset + layout_.minimum_body_bytes) {
        return 0;
    }
    const std::uint8_t* body = report.data + offset;
    const std::size_t body_size = report.size - offset;

    std::size_t used = 0;

    // --- buttons ------------------------------------------------------------
    //
    // A state, so only the changes are worth sending. A button held across a
    // hundred reports is one press.
    std::int16_t button_bits = 0;
    if (read_field(layout_.buttons, body, body_size, button_bits)) {
        // read_field sign-extends movement fields, as it must. Buttons are an
        // unsigned bit set, so discard that sign fill above the width the HID
        // descriptor declared. Without this, button 3 of a three-button mouse
        // (0b100) becomes 0xFC and invents both side buttons too.
        const std::uint8_t declared_bits =
            layout_.buttons.bits == 0
                ? static_cast<std::uint8_t>(layout_.buttons.bytes * 8)
                : layout_.buttons.bits;
        const std::uint8_t button_mask =
            declared_bits >= 8
                ? std::uint8_t{0xFF}
                : static_cast<std::uint8_t>((std::uint16_t{1} << declared_bits) - 1);
        const std::uint8_t buttons =
            static_cast<std::uint8_t>(button_bits) & button_mask;
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
    }

    // --- movement -----------------------------------------------------------
    //
    // Not a state: each report carries how far the mouse went since the last
    // one, so it is passed through rather than compared. Nothing is sent when
    // it did not move, because a polled mouse says "nowhere" constantly.
    std::int16_t dx = 0;
    std::int16_t dy = 0;
    const bool have_x = read_field(layout_.x, body, body_size, dx);
    const bool have_y = read_field(layout_.y, body, body_size, dy);
    if ((have_x || have_y) && (dx != 0 || dy != 0)) {
        InputEvent event;
        event.kind = InputEventKind::MouseMove;
        event.x = dx;
        event.y = dy;
        emit(out, capacity, used, event);
    }

    // --- wheel --------------------------------------------------------------
    //
    // Read only when the report is long enough to hold it. A boot report is
    // three bytes and stops before the wheel, so under the boot layout this
    // branch is skipped exactly as it always was - which is what a device
    // whose descriptor could not be read must keep doing.
    std::int16_t wheel = 0;
    std::int16_t pan = 0;
    const bool have_wheel = read_field(layout_.wheel, body, body_size, wheel);
    const bool have_pan = read_field(layout_.pan, body, body_size, pan);
    if ((have_wheel || have_pan) && (wheel != 0 || pan != 0)) {
        InputEvent event;
        event.kind = InputEventKind::Wheel;
        event.wheel = clamped(wheel);
        event.pan = clamped(pan);
        emit(out, capacity, used, event);
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
