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

namespace duo_input::u2 {

class UsbService {
public:
    /// Bring up the USB device stack. Call once, before task().
    void begin();

    /// Service the stack. Call every loop, unconditionally.
    void task();

    /// Send whatever changed for PC2 since the last call.
    ///
    /// Returns whether anything was sent. Movement is consumed from
    /// ``manager`` only when it is actually handed to an endpoint, so a busy
    /// endpoint delays the pointer rather than losing it.
    bool publish(hid::HidStateManager& manager);

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

    hid::KeyboardSnapshot last_keyboard_{};
    std::uint8_t last_buttons_ = 0;
    bool keyboard_valid_ = false;
};

}  // namespace duo_input::u2
