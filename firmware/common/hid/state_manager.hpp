#pragma once

// What each computer is holding, and who is holding it.
//
// A key can be down because the operator is pressing it and because a macro is
// holding it, at the same time. If either one lets go, the key must stay down
// until the other does too - otherwise a macro ending mid-word lifts a key out
// from under someone's finger. So this class does not store "is this key
// down"; it stores *who* is holding it, and a key is down while anyone is.
//
// Ownership lives in a fixed bitmask per usage: one bit for the physical
// owner, one per macro. No allocation, no growth, and a bounded amount of work
// per call - this runs on Core 0 between USB polls.

#include <cstdint>

#include "hid/types.hpp"

namespace duo_input::hid {

class HidStateManager {
public:
    HidStateManager() = default;

    // --- the operator's own hands -------------------------------------------

    /// Press or release ``usage`` on ``target`` on the operator's behalf.
    HidResult physical_key(Target target, std::uint8_t usage, bool pressed);

    /// Hold or release modifier bits on the operator's behalf.
    HidResult physical_modifiers(Target target, std::uint8_t modifiers, bool pressed);

    // --- macros --------------------------------------------------------------

    /// Press or release ``usage`` on behalf of macro ``owner``.
    HidResult macro_key(std::uint8_t owner, Target target, std::uint8_t usage, bool pressed);

    /// Hold or release modifier bits on behalf of macro ``owner``.
    HidResult macro_modifiers(std::uint8_t owner, Target target, std::uint8_t modifiers,
                              bool pressed);

    /// Let go of everything macro ``owner`` was holding, on both computers.
    HidResult release_macro(std::uint8_t owner);

    // --- mouse ---------------------------------------------------------------

    /// Set the absolute button state of one computer's mouse.
    HidResult set_mouse_buttons(Target target, std::uint8_t buttons);

    /// Add movement that has not been reported yet. Saturates; never wraps.
    void mouse_delta(Target target, std::int32_t dx, std::int32_t dy, std::int32_t wheel,
                     std::int32_t pan);

    // --- releasing -----------------------------------------------------------

    /// Let go of everything on one computer.
    void release_target(Target target);

    /// Let go of everything, everywhere, and forget every macro.
    ///
    /// This is what a reset, a lost link and STOP AND RELEASE ALL all reach
    /// for, so it must leave no ownership behind: a macro whose ownership
    /// survived would later release a key it never pressed.
    void release_all();

    // --- reading -------------------------------------------------------------

    /// Look at what ``target`` is holding. Changes nothing.
    TargetSnapshot snapshot(Target target) const;

    /// Take the report to send, consuming accumulated movement exactly once.
    ///
    /// Held keys and buttons are absolute and are *not* consumed; movement is
    /// a delta and is. Calling this twice without new movement reports no
    /// movement the second time, which is correct.
    TargetSnapshot take_snapshot(Target target);

private:
    /// One bit per owner: bit 0 is the operator, bits 1..32 are macros.
    using OwnerMask = std::uint64_t;

    static constexpr OwnerMask kPhysicalBit = 1;

    struct TargetState {
        OwnerMask key_owners[256] = {};
        OwnerMask modifier_owners[8] = {};
        std::uint8_t buttons = 0;
        std::int32_t delta_x = 0;
        std::int32_t delta_y = 0;
        std::int32_t wheel = 0;
        std::int32_t pan = 0;
    };

    static bool valid_usage(std::uint8_t usage);
    static bool valid_owner(std::uint8_t owner);
    static OwnerMask macro_bit(std::uint8_t owner);

    TargetState& state(Target target);
    const TargetState& state(Target target) const;

    HidResult hold_key(Target target, std::uint8_t usage, OwnerMask owner, bool pressed);
    void hold_modifiers(Target target, std::uint8_t modifiers, OwnerMask owner, bool pressed);
    KeyboardSnapshot keyboard_of(const TargetState& target) const;
    MouseSnapshot mouse_of(const TargetState& target) const;

    TargetState targets_[kTargetCount];
};

}  // namespace duo_input::hid
