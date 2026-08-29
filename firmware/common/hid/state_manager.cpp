#include "hid/state_manager.hpp"

namespace duo_input::hid {
namespace {

/// Clamp to what a report field can carry, rather than letting it wrap.
///
/// A wrapped delta sends the pointer the other way across the screen. A
/// clamped one is merely slower than the operator's hand, which is what a
/// dropped report looks like anyway.
template <typename Narrow>
Narrow saturate(std::int32_t value) {
    constexpr std::int32_t lowest = static_cast<std::int32_t>(
        Narrow(-1) < Narrow(0) ? -(std::int32_t(1) << (sizeof(Narrow) * 8 - 1)) : 0);
    constexpr std::int32_t highest =
        (std::int32_t(1) << (sizeof(Narrow) * 8 - 1)) - 1;
    if (value < lowest) {
        return static_cast<Narrow>(lowest);
    }
    if (value > highest) {
        return static_cast<Narrow>(highest);
    }
    return static_cast<Narrow>(value);
}

/// Add without overflowing the accumulator itself.
std::int32_t accumulate(std::int32_t total, std::int32_t added) {
    constexpr std::int32_t kCeiling = 1 << 20;
    const std::int64_t sum = static_cast<std::int64_t>(total) + added;
    if (sum > kCeiling) {
        return kCeiling;
    }
    if (sum < -kCeiling) {
        return -kCeiling;
    }
    return static_cast<std::int32_t>(sum);
}

}  // namespace

bool HidStateManager::valid_usage(std::uint8_t usage) {
    return usage >= kMinUsage;
}

bool HidStateManager::valid_owner(std::uint8_t owner) {
    return owner < kMaxMacroOwners;
}

HidStateManager::OwnerMask HidStateManager::macro_bit(std::uint8_t owner) {
    return OwnerMask(1) << (owner + 1);
}

HidStateManager::TargetState& HidStateManager::state(Target target) {
    return targets_[static_cast<std::size_t>(target)];
}

const HidStateManager::TargetState& HidStateManager::state(Target target) const {
    return targets_[static_cast<std::size_t>(target)];
}

// ------------------------------------------------------------------ ownership

HidResult HidStateManager::hold_key(Target target, std::uint8_t usage, OwnerMask owner,
                                    bool pressed) {
    if (!valid_usage(usage)) {
        return HidResult::BadUsage;
    }
    TargetState& current = state(target);
    OwnerMask& owners = current.key_owners[usage];

    if (!pressed) {
        const bool was_down = owners != 0;
        owners &= ~owner;
        if (was_down && owners == 0) {
            keyboard_changed(target);
        }
        return HidResult::Ok;
    }
    if (owners != 0) {
        // Already down. A second owner joining costs no report slot, so the
        // six-key limit does not apply here - and changes nothing the far
        // computer would see, so nobody has to be told about it.
        owners |= owner;
        return HidResult::Ok;
    }

    std::uint8_t held = 0;
    for (std::size_t candidate = kMinUsage; candidate <= kMaxUsage; ++candidate) {
        if (current.key_owners[candidate] != 0) {
            ++held;
        }
    }
    if (held >= kMaxKeys) {
        // The report has no seventh slot. Refuse, and - importantly - record
        // no ownership: a key held in the model but absent from every report
        // would be released later by something that never appeared to press it.
        return HidResult::KeyCapacity;
    }

    owners |= owner;
    keyboard_changed(target);
    return HidResult::Ok;
}

void HidStateManager::hold_modifiers(Target target, std::uint8_t modifiers, OwnerMask owner,
                                     bool pressed) {
    TargetState& current = state(target);
    for (std::uint8_t bit = 0; bit < 8; ++bit) {
        if ((modifiers & (1u << bit)) == 0) {
            continue;
        }
        const bool was_held = current.modifier_owners[bit] != 0;
        if (pressed) {
            current.modifier_owners[bit] |= owner;
        } else {
            current.modifier_owners[bit] &= ~owner;
        }
        if (was_held != (current.modifier_owners[bit] != 0)) {
            keyboard_changed(target);
        }
    }
}

HidResult HidStateManager::physical_key(Target target, std::uint8_t usage, bool pressed) {
    return hold_key(target, usage, kPhysicalBit, pressed);
}

HidResult HidStateManager::physical_modifiers(Target target, std::uint8_t modifiers,
                                              bool pressed) {
    hold_modifiers(target, modifiers, kPhysicalBit, pressed);
    return HidResult::Ok;
}

HidResult HidStateManager::macro_key(std::uint8_t owner, Target target, std::uint8_t usage,
                                     bool pressed) {
    if (!valid_owner(owner)) {
        return HidResult::BadOwner;
    }
    return hold_key(target, usage, macro_bit(owner), pressed);
}

HidResult HidStateManager::macro_modifiers(std::uint8_t owner, Target target,
                                           std::uint8_t modifiers, bool pressed) {
    if (!valid_owner(owner)) {
        return HidResult::BadOwner;
    }
    hold_modifiers(target, modifiers, macro_bit(owner), pressed);
    return HidResult::Ok;
}

HidResult HidStateManager::release_macro(std::uint8_t owner) {
    if (!valid_owner(owner)) {
        return HidResult::BadOwner;
    }
    const OwnerMask bit = macro_bit(owner);
    for (std::size_t index = 0; index < kTargetCount; ++index) {
        TargetState& current = targets_[index];
        for (std::size_t usage = 0; usage <= kMaxUsage; ++usage) {
            current.key_owners[usage] &= ~bit;
        }
        for (OwnerMask& owners : current.modifier_owners) {
            owners &= ~bit;
        }
        // Announced even when the macro held nothing: the sender answers a
        // state it already knows by saying so, which costs one comparison and
        // spares this from having to work out what actually moved.
        keyboard_changed(static_cast<Target>(index));
    }
    return HidResult::Ok;
}

// ---------------------------------------------------------------------- mouse

HidResult HidStateManager::set_mouse_buttons(Target target, std::uint8_t buttons) {
    if ((buttons & ~static_cast<std::uint8_t>(MouseButton::All)) != 0) {
        return HidResult::BadButton;
    }
    state(target).buttons = buttons;
    return HidResult::Ok;
}

void HidStateManager::mouse_delta(Target target, std::int32_t dx, std::int32_t dy,
                                  std::int32_t wheel, std::int32_t pan) {
    TargetState& current = state(target);
    current.delta_x = accumulate(current.delta_x, dx);
    current.delta_y = accumulate(current.delta_y, dy);
    current.wheel = accumulate(current.wheel, wheel);
    current.pan = accumulate(current.pan, pan);
}

// ------------------------------------------------------------------ releasing

void HidStateManager::release_target(Target target) {
    state(target) = TargetState{};
    keyboard_changed(target);
}

void HidStateManager::release_all() {
    for (std::size_t index = 0; index < kTargetCount; ++index) {
        targets_[index] = TargetState{};
        keyboard_changed(static_cast<Target>(index));
    }
}

// -------------------------------------------------------------------- reading

KeyboardSnapshot HidStateManager::keyboard_of(const TargetState& target) const {
    KeyboardSnapshot keyboard;
    for (std::uint8_t bit = 0; bit < 8; ++bit) {
        if (target.modifier_owners[bit] != 0) {
            keyboard.modifiers |= static_cast<std::uint8_t>(1u << bit);
        }
    }
    // Ascending usage order, so an unchanged set of held keys always produces
    // identical report bytes and never looks like a change to the host.
    for (std::size_t usage = kMinUsage;
         usage <= kMaxUsage && keyboard.key_count < kMaxKeys; ++usage) {
        if (target.key_owners[usage] != 0) {
            keyboard.keys[keyboard.key_count++] = static_cast<std::uint8_t>(usage);
        }
    }
    return keyboard;
}

MouseSnapshot HidStateManager::mouse_of(const TargetState& target) const {
    MouseSnapshot mouse;
    mouse.buttons = target.buttons;
    mouse.delta_x = saturate<std::int16_t>(target.delta_x);
    mouse.delta_y = saturate<std::int16_t>(target.delta_y);
    mouse.wheel = saturate<std::int8_t>(target.wheel);
    mouse.pan = saturate<std::int8_t>(target.pan);
    return mouse;
}

TargetSnapshot HidStateManager::snapshot(Target target) const {
    const TargetState& current = state(target);
    return TargetSnapshot{keyboard_of(current), mouse_of(current)};
}

TargetSnapshot HidStateManager::take_snapshot(Target target) {
    TargetState& current = state(target);
    const TargetSnapshot taken{keyboard_of(current), mouse_of(current)};
    // Movement is a delta and is consumed; keys and buttons are absolute and
    // are not. Anything beyond what one report can carry is dropped rather
    // than carried forward: it is motion the operator made long ago, and
    // replaying it later would move the pointer on its own.
    current.delta_x = 0;
    current.delta_y = 0;
    current.wheel = 0;
    current.pan = 0;
    return taken;
}

}  // namespace duo_input::hid
