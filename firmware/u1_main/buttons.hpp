#pragma once

// The two buttons on the device.
//
// These are what someone reaches for when the software has already let them
// down: the mouse is on the wrong computer, or a macro is holding a key. So
// they must behave identically every time and must never fire on their own -
// a contact bouncing for a few milliseconds is not two presses, and a finger
// resting on a button is not a hundred toggles.
//
// The class is pure: it is given the time and the state of the two pins and
// returns what that means. Nothing here reads a GPIO, which is what lets the
// timing be tested exhaustively rather than by pressing a button and hoping.

#include <cstdint>

namespace duo_input::u1 {

/// GP14 and GP15, read active-low against internal pull-ups.
inline constexpr unsigned kPinSw1 = 14;
inline constexpr unsigned kPinSw2 = 15;

/// How long a pin must hold a state before it counts as that state.
inline constexpr std::uint32_t kDebounceMs = 25;

/// How long SW2 must be held to confirm a factory reset.
///
/// Long enough that nobody does it while reaching for something else, short
/// enough that someone who means it does not doubt whether it is working.
inline constexpr std::uint32_t kFactoryHoldMs = 5000;

enum class ButtonEvent : std::uint8_t {
    None = 0,
    /// SW1: send the mouse to the other computer, now.
    EmergencyMouseToggle,
    /// SW2, briefly: stop every macro and release every key.
    StopReleaseAll,
    /// SW2, held: the operator confirms erasing their configuration.
    FactoryResetConfirmed,
};

class Buttons {
public:
    /// Advance the state machine. Returns at most one event per call.
    ///
    /// ``sw1_down`` and ``sw2_down`` are the debounced-in-hardware sense of
    /// the pins - that is, true when the button is pressed, whatever the
    /// wiring polarity is.
    ButtonEvent update(std::uint32_t now_ms, bool sw1_down, bool sw2_down);

private:
    /// One pin's journey from bouncing to settled.
    struct Pin {
        bool raw = false;
        bool stable = false;
        bool started = false;
        std::uint32_t changed_ms = 0;

        /// Feed the current reading; returns true when ``stable`` just changed.
        bool settle(std::uint32_t now_ms, bool reading);
    };

    Pin sw1_;
    Pin sw2_;

    bool sw2_held_ = false;
    bool sw2_confirmed_ = false;
    std::uint32_t sw2_pressed_ms_ = 0;
};

}  // namespace duo_input::u1
