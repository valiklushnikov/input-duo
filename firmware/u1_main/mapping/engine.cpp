#include "mapping/engine.hpp"

namespace duo_input::u1::mapping {
namespace {

using input::InputEvent;
using input::InputEventKind;

bool is_down(InputEventKind kind) {
    return kind == InputEventKind::KeyDown || kind == InputEventKind::MouseButtonDown ||
           kind == InputEventKind::ConsumerDown;
}

bool is_up(InputEventKind kind) {
    return kind == InputEventKind::KeyUp || kind == InputEventKind::MouseButtonUp ||
           kind == InputEventKind::ConsumerUp;
}

InputEventKind matching_down(InputEventKind up) {
    switch (up) {
        case InputEventKind::KeyUp:
            return InputEventKind::KeyDown;
        case InputEventKind::MouseButtonUp:
            return InputEventKind::MouseButtonDown;
        case InputEventKind::ConsumerUp:
            return InputEventKind::ConsumerDown;
        default:
            return up;
    }
}

/// Bit 0 is left control at usage 0xE0, through to bit 7 at 0xE7.
bool modifier_bit(std::uint16_t usage, std::uint8_t& mask) {
    if (usage < 0xE0 || usage > 0xE7) {
        return false;
    }
    mask = static_cast<std::uint8_t>(1u << (usage - 0xE0));
    return true;
}

config::TriggerKind trigger_of(InputEventKind kind) {
    return (kind == InputEventKind::MouseButtonDown || kind == InputEventKind::MouseButtonUp)
               ? config::TriggerKind::MOUSE_BUTTON
               : config::TriggerKind::KEYBOARD_USAGE;
}

}  // namespace

void BindingEngine::set_bindings(std::initializer_list<Binding> bindings) {
    set_bindings(bindings.begin(), bindings.size());
}

void BindingEngine::set_bindings(const Binding* bindings, std::size_t count) {
    binding_count_ = count < kMaxBindings ? count : kMaxBindings;
    for (std::size_t index = 0; index < binding_count_; ++index) {
        bindings_[index] = bindings[index];
    }
}

void BindingEngine::add(Outcome& outcome, const ActionRequest& request) const {
    if (outcome.count < kMaxActionsPerEvent) {
        outcome.actions[outcome.count++] = request;
    }
}

void BindingEngine::release_both(Outcome& outcome) const {
    for (std::size_t index = 0; index < hid::kTargetCount; ++index) {
        ActionRequest request;
        request.kind = ActionRequestKind::ReleaseTarget;
        request.target = static_cast<hid::Target>(index);
        add(outcome, request);
    }
}

void BindingEngine::release_reached(Outcome& outcome, bool keyboard, bool mouse) const {
    // Only the computers this device actually reaches, and only while
    // "actually" still means the route about to be left. Releasing a machine
    // that was receiving nothing is noise on the link for no reason.
    for (std::size_t index = 0; index < hid::kTargetCount; ++index) {
        const hid::Target target = static_cast<hid::Target>(index);
        const bool reached = (keyboard && routes_.keyboard_reaches(target)) ||
                             (mouse && routes_.mouse_reaches(target));
        if (!reached) {
            continue;
        }
        ActionRequest request;
        request.kind = ActionRequestKind::ReleaseTarget;
        request.target = target;
        add(outcome, request);
    }
}

const Binding* BindingEngine::find_binding(const InputEvent& event) const {
    const config::TriggerKind kind = trigger_of(event.kind);
    for (std::size_t index = 0; index < binding_count_; ++index) {
        const Binding& binding = bindings_[index];
        if (binding.trigger != kind || binding.code != event.code) {
            continue;
        }
        // A binding that asks for modifiers applies only while they are held.
        // Without them the key is unbound and does what it always did, which
        // is what somebody pressing it expects.
        if ((modifiers_ & binding.required_modifiers) != binding.required_modifiers) {
            continue;
        }
        return &binding;
    }
    return nullptr;
}

BindingEngine::Held* BindingEngine::find_held(InputEventKind kind, std::uint16_t code) {
    for (std::size_t index = 0; index < held_count_; ++index) {
        if (held_[index].kind == kind && held_[index].code == code) {
            return &held_[index];
        }
    }
    return nullptr;
}

bool BindingEngine::remember(const InputEvent& event, bool suppressed) {
    if (find_held(event.kind, event.code) != nullptr) {
        return false;
    }
    if (held_count_ >= kMaxHeld) {
        return false;
    }
    held_[held_count_].kind = event.kind;
    held_[held_count_].code = event.code;
    held_[held_count_].orphaned = false;
    held_[held_count_].suppressed = suppressed;
    ++held_count_;
    return true;
}

bool BindingEngine::forget(const InputEvent& event) {
    const InputEventKind down = matching_down(event.kind);
    for (std::size_t index = 0; index < held_count_; ++index) {
        if (held_[index].kind != down || held_[index].code != event.code) {
            continue;
        }
        // Swallowed on the way down, or left behind by a route change: either
        // way the far side never saw this go down, so it must not see it come
        // up. Releasing a key that was never pressed is not harmless - it
        // clears a modifier the person is still holding.
        const bool hidden = held_[index].orphaned || held_[index].suppressed;
        held_[index] = held_[held_count_ - 1];
        --held_count_;
        return !hidden;
    }
    // Never seen going down at all.
    return false;
}

void BindingEngine::orphan(bool keys, bool buttons) {
    // Everything under a finger right now belongs to the computer it was
    // pressed on. It must not arrive on the new one as a fresh press, and its
    // release must not arrive there either.
    for (std::size_t index = 0; index < held_count_; ++index) {
        const bool is_button = held_[index].kind == InputEventKind::MouseButtonDown;
        if ((is_button && buttons) || (!is_button && keys)) {
            held_[index].orphaned = true;
        }
    }
}

Outcome BindingEngine::handle(const InputEvent& event) {
    Outcome outcome;

    // Modifiers are tracked before anything is matched, so a binding that asks
    // for shift sees the shift that arrived a moment earlier.
    std::uint8_t mask = 0;
    if (event.kind == InputEventKind::KeyDown && modifier_bit(event.code, mask)) {
        modifiers_ = static_cast<std::uint8_t>(modifiers_ | mask);
    } else if (event.kind == InputEventKind::KeyUp && modifier_bit(event.code, mask)) {
        modifiers_ = static_cast<std::uint8_t>(modifiers_ & ~mask);
    }

    if (is_up(event.kind)) {
        if (forget(event)) {
            ActionRequest request;
            request.kind = ActionRequestKind::SendInput;
            request.event = event;
            add(outcome, request);
        }
        return outcome;
    }

    if (!is_down(event.kind)) {
        // Movement and wheel: no state, nothing to bind to, straight through.
        ActionRequest request;
        request.kind = ActionRequestKind::SendInput;
        request.event = event;
        add(outcome, request);
        return outcome;
    }

    if (find_held(event.kind, event.code) != nullptr) {
        // Already down. A keyboard resends its state constantly: a finger
        // resting on a key is one intention, not forty macros a second - and
        // if the route moved underneath it, this input belongs to the computer
        // it was pressed on and goes nowhere at all.
        return outcome;
    }

    const Binding* binding = find_binding(event);
    const bool swallowed = binding != nullptr && binding->mode == config::BindingMode::REPLACE;
    remember(event, swallowed);

    if (binding == nullptr) {
        ActionRequest request;
        request.kind = ActionRequestKind::SendInput;
        request.event = event;
        add(outcome, request);
        return outcome;
    }

    // Add lets the key through as well; Replace swallows it. That is the whole
    // difference between the two modes, and getting it backwards gives either
    // a key that seems to do nothing or one that does its job and types a
    // character nobody wanted.
    if (binding->mode == config::BindingMode::ADD) {
        ActionRequest request;
        request.kind = ActionRequestKind::SendInput;
        request.event = event;
        add(outcome, request);
    }

    switch (binding->action) {
        case config::ActionKind::RUN_MACRO: {
            ActionRequest request;
            request.kind = ActionRequestKind::RunMacro;
            request.parameter = binding->parameter;
            add(outcome, request);
            break;
        }

        case config::ActionKind::SET_KEYBOARD_ROUTE:
        case config::ActionKind::TOGGLE_KEYBOARD_ROUTE:
        case config::ActionKind::SET_MOUSE_ROUTE:
        case config::ActionKind::TOGGLE_MOUSE_ROUTE: {
            const bool keyboard = binding->action == config::ActionKind::SET_KEYBOARD_ROUTE ||
                                  binding->action == config::ActionKind::TOGGLE_KEYBOARD_ROUTE;
            const bool toggle = binding->action == config::ActionKind::TOGGLE_KEYBOARD_ROUTE ||
                                binding->action == config::ActionKind::TOGGLE_MOUSE_ROUTE;
            move_route(outcome, keyboard, toggle, binding->parameter);
            break;
        }

        case config::ActionKind::SET_PROFILE: {
            // The new profile may bind a held key to something else entirely,
            // and the far side is holding it under the old meaning.
            release_reached(outcome, true, true);
            orphan(true, true);

            ActionRequest request;
            request.kind = ActionRequestKind::SetProfile;
            request.parameter = binding->parameter;
            add(outcome, request);
            break;
        }
    }

    return outcome;
}

bool BindingEngine::move_route(Outcome& outcome, bool keyboard, bool toggle,
                               std::uint8_t parameter) {
    // Whether the move is allowed at all is settled first. A refused route
    // must release nothing: letting go of a computer's keys because somebody
    // asked for an impossible route would be a fault of its own.
    const bool allowed =
        toggle ? true
               : (keyboard
                      ? Routes::keyboard_route_is_valid(
                            static_cast<config::KeyboardRoute>(parameter))
                      : Routes::mouse_route_is_valid(static_cast<config::MouseRoute>(parameter)));
    if (!allowed) {
        return false;
    }

    // Released before the route moves, while "where this reaches" still means
    // the computer being left behind. That machine will never hear about these
    // keys again.
    release_reached(outcome, keyboard, !keyboard);
    orphan(keyboard, !keyboard);

    if (keyboard) {
        if (toggle) {
            routes_.toggle_keyboard();
        } else {
            routes_.set_keyboard(static_cast<config::KeyboardRoute>(parameter));
        }
    } else {
        if (toggle) {
            routes_.toggle_mouse();
        } else {
            routes_.set_mouse(static_cast<config::MouseRoute>(parameter));
        }
    }
    return true;
}

Outcome BindingEngine::set_keyboard_route(config::KeyboardRoute route) {
    Outcome outcome;
    move_route(outcome, true, false, static_cast<std::uint8_t>(route));
    return outcome;
}

Outcome BindingEngine::set_mouse_route(config::MouseRoute route) {
    Outcome outcome;
    move_route(outcome, false, false, static_cast<std::uint8_t>(route));
    return outcome;
}

Outcome BindingEngine::release_everything() {
    Outcome outcome;
    // Both, unconditionally: this is the emergency control, and whoever
    // reaches for it cannot see which computer is holding what.
    release_both(outcome);
    orphan(true, true);
    return outcome;
}

}  // namespace duo_input::u1::mapping
