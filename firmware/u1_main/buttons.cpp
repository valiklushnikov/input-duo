#include "buttons.hpp"

namespace duo_input::u1 {

bool Buttons::Pin::settle(std::uint32_t now_ms, bool reading) {
    if (!started) {
        started = true;
        raw = reading;
        stable = reading;
        changed_ms = now_ms;
        return false;
    }

    if (reading != raw) {
        // The reading moved; start the clock again. A contact that bounces
        // four times in ten milliseconds restarts it four times, which is
        // exactly right - none of those were the button settling.
        raw = reading;
        changed_ms = now_ms;
        return false;
    }

    if (raw == stable) {
        return false;
    }

    // Unsigned subtraction wraps the same way the millisecond counter does,
    // so this stays correct across the wrap at 49 days.
    if (now_ms - changed_ms < kDebounceMs) {
        return false;
    }
    stable = raw;
    return true;
}

ButtonEvent Buttons::update(std::uint32_t now_ms, bool sw1_down, bool sw2_down) {
    const bool sw1_changed = sw1_.settle(now_ms, sw1_down);
    const bool sw2_changed = sw2_.settle(now_ms, sw2_down);

    // SW1 acts the moment it settles, not when it is let go: the operator
    // wants the mouse to move now, and one press is one intention however
    // long the finger stays there.
    if (sw1_changed && sw1_.stable) {
        return ButtonEvent::EmergencyMouseToggle;
    }

    if (sw2_changed) {
        if (sw2_.stable) {
            sw2_held_ = true;
            sw2_confirmed_ = false;
            sw2_pressed_ms_ = now_ms;
            return ButtonEvent::None;
        }

        // Released. The short action fires here rather than on the way down,
        // because until this moment the press might still have become a
        // five-second hold - and a factory reset must not be preceded by a
        // stop nobody asked for.
        const bool was_confirmed = sw2_confirmed_;
        sw2_held_ = false;
        sw2_confirmed_ = false;
        return was_confirmed ? ButtonEvent::None : ButtonEvent::StopReleaseAll;
    }

    if (sw2_held_ && !sw2_confirmed_ && (now_ms - sw2_pressed_ms_) >= kFactoryHoldMs) {
        sw2_confirmed_ = true;
        return ButtonEvent::FactoryResetConfirmed;
    }

    return ButtonEvent::None;
}

}  // namespace duo_input::u1
