#include "link/spi_protocol.hpp"

#include <cstring>

namespace duo_input::link {
namespace {

std::uint8_t to_byte(std::int8_t value) {
    return static_cast<std::uint8_t>(value);
}

std::int8_t from_byte(std::uint8_t value) {
    return static_cast<std::int8_t>(value);
}

}  // namespace

// -------------------------------------------------------------- sequences

SequenceVerdict SequenceTracker::observe(std::uint16_t sequence) {
    if (!started_) {
        started_ = true;
        last_ = sequence;
        return SequenceVerdict::Fresh;
    }
    if (sequence == last_) {
        ++duplicates_;
        return SequenceVerdict::Duplicate;
    }

    // Unsigned 16-bit subtraction wraps the same way the counter does, so the
    // frame after 0xFFFF is 0x0000 and the difference is 1 - not 65535.
    const std::uint16_t advance = static_cast<std::uint16_t>(sequence - last_);
    last_ = sequence;
    if (advance == 1) {
        return SequenceVerdict::Fresh;
    }
    ++gaps_;
    return SequenceVerdict::Gap;
}

void SequenceTracker::reset() {
    started_ = false;
    last_ = 0;
}

// --------------------------------------------------------------- keyboard

bool encode_keyboard_state(const hid::KeyboardSnapshot& keyboard,
                           protocol::MutableByteView output, std::size_t& written) {
    if (output.data == nullptr || output.size < kKeyboardStateSize) {
        return false;
    }
    if (keyboard.key_count > hid::kMaxKeys) {
        return false;
    }

    output.data[0] = keyboard.modifiers;
    output.data[1] = keyboard.key_count;
    for (std::size_t index = 0; index < hid::kMaxKeys; ++index) {
        // Slots past the count are zero, which is what "no key" means in a HID
        // report; sending whatever was left in the struct would be a key.
        output.data[2 + index] = index < keyboard.key_count ? keyboard.keys[index] : 0;
    }
    written = kKeyboardStateSize;
    return true;
}

bool decode_keyboard_state(protocol::ByteView payload, hid::KeyboardSnapshot& keyboard) {
    if (payload.data == nullptr || payload.size != kKeyboardStateSize) {
        return false;
    }
    const std::uint8_t count = payload.data[1];
    if (count > hid::kMaxKeys) {
        // A count this frame cannot carry means the frame is not what it says
        // it is. Emitting the keys anyway would be inventing input.
        return false;
    }

    hid::KeyboardSnapshot decoded;
    decoded.modifiers = payload.data[0];
    decoded.key_count = count;
    for (std::size_t index = 0; index < count; ++index) {
        const std::uint8_t usage = payload.data[2 + index];
        if (usage == 0) {
            // A hole inside the count is a malformed frame, not an empty slot.
            return false;
        }
        decoded.keys[index] = usage;
    }
    keyboard = decoded;
    return true;
}

// ------------------------------------------------------------------ mouse

bool encode_mouse_delta(const hid::MouseSnapshot& mouse, protocol::MutableByteView output,
                        std::size_t& written) {
    if (output.data == nullptr || output.size < kMouseDeltaSize) {
        return false;
    }
    output.data[0] = mouse.buttons;
    output.data[1] = static_cast<std::uint8_t>(mouse.delta_x & 0xFF);
    output.data[2] = static_cast<std::uint8_t>((mouse.delta_x >> 8) & 0xFF);
    output.data[3] = static_cast<std::uint8_t>(mouse.delta_y & 0xFF);
    output.data[4] = static_cast<std::uint8_t>((mouse.delta_y >> 8) & 0xFF);
    output.data[5] = to_byte(mouse.wheel);
    output.data[6] = to_byte(mouse.pan);
    written = kMouseDeltaSize;
    return true;
}

bool decode_mouse_delta(protocol::ByteView payload, hid::MouseSnapshot& mouse) {
    if (payload.data == nullptr || payload.size != kMouseDeltaSize) {
        return false;
    }
    if ((payload.data[0] & ~static_cast<std::uint8_t>(hid::MouseButton::All)) != 0) {
        // A button this mouse does not have. The frame is not trustworthy.
        return false;
    }

    hid::MouseSnapshot decoded;
    decoded.buttons = payload.data[0];
    decoded.delta_x = static_cast<std::int16_t>(
        static_cast<std::uint16_t>(payload.data[1]) |
        (static_cast<std::uint16_t>(payload.data[2]) << 8));
    decoded.delta_y = static_cast<std::int16_t>(
        static_cast<std::uint16_t>(payload.data[3]) |
        (static_cast<std::uint16_t>(payload.data[4]) << 8));
    decoded.wheel = from_byte(payload.data[5]);
    decoded.pan = from_byte(payload.data[6]);
    mouse = decoded;
    return true;
}

// --------------------------------------------------------------- consumer

bool encode_consumer_state(std::uint16_t usage, protocol::MutableByteView output,
                           std::size_t& written) {
    if (output.data == nullptr || output.size < kConsumerStateSize) {
        return false;
    }
    output.data[0] = static_cast<std::uint8_t>(usage & 0xFF);
    output.data[1] = static_cast<std::uint8_t>((usage >> 8) & 0xFF);
    written = kConsumerStateSize;
    return true;
}

bool decode_consumer_state(protocol::ByteView payload, std::uint16_t& usage) {
    if (payload.data == nullptr || payload.size != kConsumerStateSize) {
        return false;
    }
    usage = static_cast<std::uint16_t>(payload.data[0] |
                                       (static_cast<std::uint16_t>(payload.data[1]) << 8));
    return true;
}

bool is_endpoint_reply(protocol::SpiMessageType type) {
    return type == protocol::SpiMessageType::ENDPOINT_STATUS ||
           type == protocol::SpiMessageType::ENDPOINT_ADDRESSES;
}

}  // namespace duo_input::link
