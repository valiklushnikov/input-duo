// What U1 decides to tell PC2 on any one pass, and nothing about the wires.
//
// Separate from spi_master.cpp because nothing here touches the SPI block, and
// this is the part that has to be right: poll sends one frame per pass and
// returns after the first thing it has to say, so which branch it takes
// decides whether a pointer moves or stutters. In its own translation unit it
// can be exercised on a desktop against a recorded link, with only send()
// replaced.

#include "spi_master.hpp"

#include <cstddef>

#include "link/spi_protocol.hpp"

namespace duo_input::u1 {
namespace {

bool same_keyboard(const hid::KeyboardSnapshot& left, const hid::KeyboardSnapshot& right) {
    if (left.modifiers != right.modifiers || left.key_count != right.key_count) {
        return false;
    }
    for (std::uint8_t index = 0; index < left.key_count; ++index) {
        if (left.keys[index] != right.keys[index]) {
            return false;
        }
    }
    return true;
}

bool moved(const hid::MouseSnapshot& mouse) {
    return mouse.delta_x != 0 || mouse.delta_y != 0 || mouse.wheel != 0 || mouse.pan != 0;
}

}  // namespace

bool SpiMaster::poll_snapshot(std::uint32_t now_ms, const hid::TargetSnapshot& pc2,
                              bool& consumed_mouse, bool& told_keyboard) {
    consumed_mouse = false;
    told_keyboard = false;

    std::uint8_t payload[link::kKeyboardStateSize > link::kMouseDeltaSize
                             ? link::kKeyboardStateSize
                             : link::kMouseDeltaSize] = {};
    std::size_t written = 0;

    if (!keyboard_valid_ || !same_keyboard(pc2.keyboard, last_keyboard_)) {
        if (link::encode_keyboard_state(pc2.keyboard,
                                        protocol::MutableByteView{payload, sizeof(payload)},
                                        written) &&
            send(protocol::SpiMessageType::KBD_STATE,
                 protocol::ByteView{payload, written}, now_ms)) {
            last_keyboard_ = pc2.keyboard;
            keyboard_valid_ = true;
            told_keyboard = true;
            // Returning here is what makes the caller's timing matter: this
            // pass has said nothing about the movement, so the movement must
            // still be there on the next one.
            return true;
        }
        // The transfer did not go out. PC2 still does not know, and saying
        // nothing here is what makes the caller hold the state until it does.
    } else {
        // PC2 already has this one, which is the other way of being told.
        told_keyboard = true;
    }

    if (moved(pc2.mouse) || pc2.mouse.buttons != last_buttons_) {
        if (link::encode_mouse_delta(pc2.mouse,
                                     protocol::MutableByteView{payload, sizeof(payload)},
                                     written) &&
            send(protocol::SpiMessageType::MOUSE_DELTA,
                 protocol::ByteView{payload, written}, now_ms)) {
            last_buttons_ = pc2.mouse.buttons;
            // PC2 has been told where the pointer went. Only now has anyone
            // earned the right to consume it.
            consumed_mouse = true;
            return true;
        }
    }

    // Silence and a severed cable look identical from the far end, so a quiet
    // link still has to say something.
    const bool heartbeat_due =
        !ever_sent_ || (now_ms - last_sent_ms_) >= kHeartbeatIntervalMs;
    if (heartbeat_due) {
        return send(protocol::SpiMessageType::HEARTBEAT, protocol::ByteView{nullptr, 0},
                    now_ms);
    }
    return false;
}

}  // namespace duo_input::u1
