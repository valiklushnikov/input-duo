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
    if (kind == InputEventKind::ConsumerDown || kind == InputEventKind::ConsumerUp)
        return config::TriggerKind::CONSUMER_USAGE;
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

bool BindingEngine::matches(const Binding& binding, const InputEvent& event,
                            const input::SourceIdentity* source) const {
    if (binding.trigger != trigger_of(event.kind) || binding.code != event.code ||
        (modifiers_ & binding.required_modifiers) != binding.required_modifiers) {
        return false;
    }
    const auto& required = binding.source;
    const bool any_source = required.vendor_id == 0 && required.product_id == 0 &&
                            required.interface_number == 0;
    return any_source ||
           (source != nullptr && required.vendor_id == source->vendor_id &&
            required.product_id == source->product_id &&
            required.interface_number == source->interface_number);
}

BindingEngine::Held* BindingEngine::find_held(InputEventKind kind, std::uint16_t code,
                                             std::uint8_t source_index) {
    for (std::size_t index = 0; index < held_count_; ++index) {
        if (held_[index].kind == kind && held_[index].code == code &&
            held_[index].source_index == source_index) {
            return &held_[index];
        }
    }
    return nullptr;
}

bool BindingEngine::remember(const InputEvent& event, bool suppressed) {
    if (find_held(event.kind, event.code, event.source_index) != nullptr) {
        return false;
    }
    if (held_count_ >= kMaxHeld) {
        return false;
    }
    held_[held_count_].kind = event.kind;
    held_[held_count_].code = event.code;
    held_[held_count_].source_index = event.source_index;
    held_[held_count_].orphaned = false;
    held_[held_count_].suppressed = suppressed;
    ++held_count_;
    return true;
}

bool BindingEngine::forget(const InputEvent& event) {
    const InputEventKind down = matching_down(event.kind);
    for (std::size_t index = 0; index < held_count_; ++index) {
        if (held_[index].kind != down || held_[index].code != event.code ||
            held_[index].source_index != event.source_index) {
            continue;
        }
        // Swallowed on the way down, or left behind by a route change: either
        // way the far side never saw this go down, so it must not see it come
        // up. Releasing a key that was never pressed is not harmless - it
        // clears a modifier the person is still holding.
        const bool hidden = held_[index].orphaned || held_[index].suppressed;
        held_[index] = held_[held_count_ - 1];
        --held_count_;
        return !hidden && !forwarded(down, event.code);
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

bool BindingEngine::forwarded(InputEventKind kind, std::uint16_t code) const {
    for (std::size_t i = 0; i < held_count_; ++i) {
        const auto& held = held_[i];
        if (held.kind == kind && held.code == code && !held.suppressed && !held.orphaned)
            return true;
    }
    return false;
}

std::uint8_t BindingEngine::held_modifiers() const {
    std::uint8_t modifiers = 0;
    for (std::size_t i = 0; i < held_count_; ++i) {
        std::uint8_t mask = 0;
        if (held_[i].kind == InputEventKind::KeyDown && modifier_bit(held_[i].code, mask))
            modifiers = static_cast<std::uint8_t>(modifiers | mask);
    }
    return modifiers;
}

Outcome BindingEngine::handle(const InputEvent& event) {
    Outcome outcome;
    input::SourceIdentity identity;
    const input::SourceIdentity* source =
        sources_ != nullptr && sources_->resolve(event.source_index, identity) ? &identity : nullptr;

    // Modifiers are tracked before anything is matched, so a binding that asks
    // for shift sees the shift that arrived a moment earlier.
    std::uint8_t mask = 0;
    if (event.kind == InputEventKind::KeyDown && modifier_bit(event.code, mask)) {
        modifiers_ = static_cast<std::uint8_t>(modifiers_ | mask);
    }

    if (is_up(event.kind)) {
        const bool released = forget(event);
        modifiers_ = held_modifiers();
        if (released) {
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

    if (find_held(event.kind, event.code, event.source_index) != nullptr) {
        // Already down. A keyboard resends its state constantly: a finger
        // resting on a key is one intention, not forty macros a second - and
        // if the route moved underneath it, this input belongs to the computer
        // it was pressed on and goes nowhere at all.
        return outcome;
    }

    bool matched[kMaxBindings] = {};
    bool swallowed = false;
    bool matched_any = false;
    bool matched_qualified = false;
    for (std::size_t index = 0; index < binding_count_; ++index) {
        const auto& required = bindings_[index].source;
        const bool qualified = required.vendor_id != 0 || required.product_id != 0 || required.interface_number != 0;
        bool& group_matched = qualified ? matched_qualified : matched_any;
        matched[index] = !group_matched && matches(bindings_[index], event, source);
        group_matched = group_matched || matched[index];
        swallowed = swallowed ||
                    (matched[index] && bindings_[index].mode == config::BindingMode::REPLACE);
    }
    const bool already_forwarded = forwarded(event.kind, event.code);
    if (!remember(event, swallowed)) return outcome;

    // All Add matches share one physical press; any Replace suppresses it and
    // its eventual release, even if that binding is beyond the output budget.
    if (!swallowed && !already_forwarded) {
        ActionRequest request;
        request.kind = ActionRequestKind::SendInput;
        request.event = event;
        add(outcome, request);
    }

    for (std::size_t index = 0; index < binding_count_; ++index) {
        if (matched[index] && !apply_binding(outcome, bindings_[index])) {
            break;
        }
    }
    return outcome;
}

bool BindingEngine::apply_binding(Outcome& outcome, const Binding& binding) {
    switch (binding.action) {
        case config::ActionKind::RUN_MACRO: {
            if (outcome.count == kMaxActionsPerEvent) return false;
            ActionRequest request;
            request.kind = ActionRequestKind::RunMacro;
            request.parameter = binding.parameter;
            request.macro_route = routes_.keyboard();
            add(outcome, request);
            break;
        }

        case config::ActionKind::SET_KEYBOARD_ROUTE:
        case config::ActionKind::TOGGLE_KEYBOARD_ROUTE:
        case config::ActionKind::SET_MOUSE_ROUTE:
        case config::ActionKind::TOGGLE_MOUSE_ROUTE: {
            const bool keyboard = binding.action == config::ActionKind::SET_KEYBOARD_ROUTE ||
                                  binding.action == config::ActionKind::TOGGLE_KEYBOARD_ROUTE;
            const bool toggle = binding.action == config::ActionKind::TOGGLE_KEYBOARD_ROUTE ||
                                binding.action == config::ActionKind::TOGGLE_MOUSE_ROUTE;
            if (!toggle &&
                !(keyboard ? Routes::keyboard_route_is_valid(
                                 static_cast<config::KeyboardRoute>(binding.parameter))
                           : Routes::mouse_route_is_valid(
                                 static_cast<config::MouseRoute>(binding.parameter)))) {
                break;
            }
            if (!move_route(outcome, keyboard, toggle, binding.parameter)) return false;
            break;
        }

        case config::ActionKind::SET_PROFILE: {
            Outcome releases;
            release_reached(releases, true, true);
            if (outcome.count + releases.count + 1 > kMaxActionsPerEvent) return false;
            // The new profile may bind a held key to something else entirely,
            // and the far side is holding it under the old meaning.
            release_reached(outcome, true, true);
            orphan(true, true);

            ActionRequest request;
            request.kind = ActionRequestKind::SetProfile;
            request.parameter = binding.parameter;
            add(outcome, request);
            break;
        }
    }

    return true;
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

    Outcome releases;
    release_reached(releases, keyboard, !keyboard);
    if (outcome.count + releases.count > kMaxActionsPerEvent) return false;

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
