#pragma once

// Turning a snapshot of held keys into USB reports, and nothing more.
//
// The state manager decides what is held. This decides when to tell the
// computer, and it sends a report only when the state actually changed -
// except for mouse movement, which is a delta and is sent whenever there is
// any, because "no change" and "did not move" are the same thing there.
//
// Nothing here blocks. If an endpoint is busy the report waits for the next
// call; a USB stack that is made to wait is a USB stack that stops answering
// the host, and Windows removes devices that stop answering.

#include <cstdint>

#include "hid/state_manager.hpp"
#include "hid/types.hpp"

namespace duo_input::u1 {

class UsbService {
public:
    /// Bring up the USB device stack. Call once, before task().
    void begin();

    /// Service the stack. Call every loop, unconditionally.
    void task();

    /// Send whatever changed for PC1 since the last call.
    ///
    /// Returns whether anything was sent. Movement is consumed only when it
    /// is actually handed to an endpoint, so a busy endpoint delays the
    /// pointer rather than losing it.
    /// ``source`` is anything that answers ``snapshot`` and ``take_snapshot``:
    /// a bare state manager, or the command runtime that owns one. Templated
    /// rather than fixed so neither board has to know the other's internals.
    template <typename StateSource>
    bool publish(StateSource& source) {
        if (!mounted()) {
            return false;
        }

        bool sent = false;
        const hid::TargetSnapshot current = source.snapshot(hid::Target::Pc1);

        // Keys are an absolute state: resend only when it differs from what
        // the host was last told, so a held key does not flood the bus.
        if (!keyboard_valid_ || !same_as_last_keyboard(current.keyboard)) {
            if (send_keyboard(current.keyboard)) {
                last_keyboard_ = current.keyboard;
                keyboard_valid_ = true;
                sent = true;
            }
        }

        // Movement is a delta, so "unchanged" is not a reason to stay quiet.
        // Buttons are absolute and travel in the same report.
        if (has_movement(current.mouse) || current.mouse.buttons != last_buttons_) {
            if (send_mouse(current.mouse)) {
                last_buttons_ = current.mouse.buttons;
                // Consume only now: an endpoint that was busy has cost the
                // pointer a millisecond, not a movement.
                source.take_snapshot(hid::Target::Pc1);
                sent = true;
            }
        }

        return sent;
    }

    /// Has the host configured us?
    bool mounted() const;

    /// Is the bus suspended?
    bool suspended() const;

    /// Forget what was last sent, so the next publish resends everything.
    ///
    /// A host that has just re-enumerated knows nothing about the reports we
    /// sent before; without this, a key released while unplugged would never
    /// be reported as released.
    void forget_sent_state();

private:
    bool send_keyboard(const hid::KeyboardSnapshot& keyboard);
    bool send_mouse(const hid::MouseSnapshot& mouse);
    bool same_as_last_keyboard(const hid::KeyboardSnapshot& keyboard) const;
    static bool has_movement(const hid::MouseSnapshot& mouse);

    hid::KeyboardSnapshot last_keyboard_{};
    std::uint8_t last_buttons_ = 0;
    bool keyboard_valid_ = false;
};

}  // namespace duo_input::u1
