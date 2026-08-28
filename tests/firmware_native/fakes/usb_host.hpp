#pragma once

// The far computer, as far as U1 can tell: the reports that actually left the
// keyboard and mouse endpoints.
//
// Everything above the endpoint - when to send, what "changed" means, which
// snapshot to consume - is UsbService's own code, compiled from the firmware
// header. Only the two calls that reach TinyUSB are replaced, because a
// desktop has no TinyUSB. So a test written against this is asking what the
// far computer received, which is the only question that distinguishes a macro
// that types from one that does not.

#include <cstddef>

#include "hid/types.hpp"

namespace duo::test {

struct UsbHost {
    static constexpr std::size_t kMaxReports = 512;

    duo_input::hid::KeyboardSnapshot keyboard[kMaxReports];
    std::size_t keyboard_count = 0;
    duo_input::hid::MouseSnapshot mouse[kMaxReports];
    std::size_t mouse_count = 0;

    /// Has the host configured us?
    bool mounted = true;
    /// The endpoint has not been polled yet, so a report cannot be handed to
    /// it. TinyUSB says so with tud_hid_n_ready.
    bool keyboard_ready = true;
    bool mouse_ready = true;

    void reset() {
        keyboard_count = 0;
        mouse_count = 0;
        mounted = true;
        keyboard_ready = true;
        mouse_ready = true;
    }

    /// Does any report hold ``usage``?
    bool typed(std::uint8_t usage) const {
        for (std::size_t index = 0; index < keyboard_count; ++index) {
            if (keyboard[index].contains(usage)) {
                return true;
            }
        }
        return false;
    }
};

/// The one host the linked-in UsbService stub reports to.
UsbHost& usb_host();

}  // namespace duo::test
