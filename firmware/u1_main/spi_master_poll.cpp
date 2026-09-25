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

#include "link/host_addresses.hpp"
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

    // The computer's own addresses, repeated on their interval. Checked first
    // because a link that is never idle - a mouse that never stops - would
    // otherwise never reach a slot for them. Returning here holds the
    // keyboard state and the movement exactly as a busy transfer does; they
    // go out on the next pass.
    if (addresses_ != nullptr && addresses_->local_due(now_ms)) {
        std::uint8_t list[link::kHostAddressesMaxSize];
        std::size_t size = 0;
        if (link::encode_host_addresses(addresses_->local(),
                                        protocol::MutableByteView{list, sizeof(list)}, size) &&
            send(protocol::SpiMessageType::HOST_ADDRESSES, protocol::ByteView{list, size},
                 now_ms)) {
            addresses_->mark_local_sent(now_ms);
            return true;
        }
    }

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

void SpiMaster::apply_reply(const protocol::SpiFrame& frame) {
    replies_.observe(frame.sequence);
    status_.answered = true;
    if (frame.type == protocol::SpiMessageType::ENDPOINT_ADDRESSES) {
        if (addresses_ != nullptr) {
            addresses_->accept_peer(frame.payload);
        }
        return;
    }
    if (frame.type == protocol::SpiMessageType::ENDPOINT_STATUS && frame.payload.size >= 1) {
        status_.mounted = frame.payload.data[0] != 0;
    }
    if (frame.type == protocol::SpiMessageType::ENDPOINT_STATUS && frame.payload.size >= 4) {
        // Read separately from the mount flag, so a U2 that reports only the
        // flag stays readable instead of being rejected over a field it never
        // claimed to send.
        status_.endpoint_drops = frame.payload.data[1];
        status_.endpoint_release_ms = static_cast<std::uint16_t>(
            frame.payload.data[2] | (frame.payload.data[3] << 8));
    }
}

}  // namespace duo_input::u1
