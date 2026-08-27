#include "core1_runtime.hpp"

namespace duo_input::u1 {
namespace {

using input::InputEvent;
using input::InputEventKind;
using runtime::CommandKind;
using runtime::kPhysicalOwner;
using runtime::OutputCommand;
using runtime::Route;

/// Modifiers arrive as usages 0xE0 to 0xE7 and travel as a mask.
bool modifier_bit(std::uint16_t usage, std::uint8_t& mask) {
    if (usage < 0xE0 || usage > 0xE7) {
        return false;
    }
    mask = static_cast<std::uint8_t>(1u << (usage - 0xE0));
    return true;
}

Route route_of(config::KeyboardRoute route) {
    switch (route) {
        case config::KeyboardRoute::PC2:
            return Route::Pc2;
        case config::KeyboardRoute::BOTH:
            return Route::Both;
        default:
            return Route::Pc1;
    }
}

Route route_of(config::MouseRoute route) {
    // A mouse is never on both at once: one pointer cannot be in two places,
    // and sending the same motion to both computers moves two cursors the
    // operator can only watch one of.
    return route == config::MouseRoute::PC2 ? Route::Pc2 : Route::Pc1;
}

Route route_of(hid::Target target) {
    return target == hid::Target::Pc2 ? Route::Pc2 : Route::Pc1;
}

}  // namespace

Core1Runtime::Core1Runtime(ICommandSink& sink, IProfileSource& profiles)
    : sink_(sink), profiles_(profiles) {
    set_profile_now(0);
}

Route Core1Runtime::keyboard_route() const { return route_of(engine_.keyboard_route()); }

Route Core1Runtime::mouse_route() const { return route_of(engine_.mouse_route()); }

void Core1Runtime::submit(const OutputCommand& command) {
    if (!sink_.submit(command)) {
        // Core 1 cannot wait for Core 0, which is waiting for the host. What
        // it can do is record that the output state no longer matches what
        // actually happened, so somebody can be told.
        ++dropped_;
    }
}

void Core1Runtime::send_input(const InputEvent& event) {
    OutputCommand command;
    command.owner = kPhysicalOwner;

    std::uint8_t mask = 0;
    switch (event.kind) {
        case InputEventKind::KeyDown:
        case InputEventKind::KeyUp: {
            const bool pressed = event.kind == InputEventKind::KeyDown;
            command.route = keyboard_route();
            if (modifier_bit(event.code, mask)) {
                command.kind = pressed ? CommandKind::ModifiersPress : CommandKind::ModifiersRelease;
                command.code = mask;
            } else {
                command.kind = pressed ? CommandKind::KeyPress : CommandKind::KeyRelease;
                command.code = static_cast<std::uint8_t>(event.code);
            }
            break;
        }

        case InputEventKind::ConsumerDown:
            command.kind = CommandKind::ConsumerTap;
            command.route = keyboard_route();
            command.usage = event.code;
            break;

        case InputEventKind::ConsumerUp:
            // A consumer usage is sent as a tap and held by nobody, so the
            // release has nothing left to do.
            return;

        case InputEventKind::MouseButtonDown:
        case InputEventKind::MouseButtonUp: {
            const std::uint8_t bit = static_cast<std::uint8_t>(1u << event.code);
            if (event.kind == InputEventKind::MouseButtonDown) {
                buttons_ = static_cast<std::uint8_t>(buttons_ | bit);
            } else {
                buttons_ = static_cast<std::uint8_t>(buttons_ & ~bit);
            }
            // The report carries every button at once. Sending one index would
            // clear whatever else the operator is holding.
            command.kind = CommandKind::MouseButtons;
            command.route = mouse_route();
            command.code = buttons_;
            break;
        }

        case InputEventKind::MouseMove:
            command.kind = CommandKind::MouseDelta;
            command.route = mouse_route();
            command.delta_x = event.x;
            command.delta_y = event.y;
            break;

        case InputEventKind::Wheel:
            command.kind = CommandKind::MouseDelta;
            command.route = mouse_route();
            command.wheel = event.wheel;
            command.pan = event.pan;
            break;

        default:
            return;
    }

    submit(command);
}

void Core1Runtime::apply(const mapping::Outcome& outcome, std::uint32_t now_ms) {
    for (std::size_t index = 0; index < outcome.count; ++index) {
        const mapping::ActionRequest& action = outcome.actions[index];
        switch (action.kind) {
            case mapping::ActionRequestKind::SendInput:
                send_input(action.event);
                break;

            case mapping::ActionRequestKind::ReleaseTarget: {
                OutputCommand command;
                command.kind = CommandKind::ReleaseRoute;
                command.route = route_of(action.target);
                submit(command);
                // Whatever the operator was physically holding on that
                // computer is gone from it. Our idea of the button mask has to
                // go with it, or the next press resends buttons the far side
                // has already been told to let go of.
                buttons_ = 0;
                break;
            }

            case mapping::ActionRequestKind::RunMacro:
                run_macro(action.parameter, now_ms);
                break;

            case mapping::ActionRequestKind::SetProfile:
                // Through the same handshake as a request from the host, so
                // there is one path that swaps a profile and one place where
                // everything held is let go of first.
                request_profile(action.parameter);
                break;

            case mapping::ActionRequestKind::None:
                break;
        }
    }
}

void Core1Runtime::handle_input(const InputEvent& event, std::uint32_t now_ms) {
    // Capture first. While the configurator is asking which key to bind, that
    // key belongs to the question - running its current binding would act on
    // the old meaning of a key the operator is in the middle of replacing.
    if (capture_.handle(event) == mapping::CaptureDisposition::Swallow) {
        return;
    }

    apply(engine_.handle(event), now_ms);
}

void Core1Runtime::drain_macros(std::uint32_t now_ms) {
    macros::MacroOutput output;
    while (macros_.tick(now_ms, output)) {
        switch (output.kind) {
            case macros::MacroOutputKind::SendInput: {
                OutputCommand command;
                command.route = output.route;
                // Named as its own owner, so that letting go of the operator's
                // keys does not let go of a macro's, or the other way round.
                command.owner = output.owner;

                std::uint8_t mask = 0;
                const input::InputEvent& event = output.event;
                if (event.kind == InputEventKind::KeyDown ||
                    event.kind == InputEventKind::KeyUp) {
                    const bool pressed = event.kind == InputEventKind::KeyDown;
                    if (modifier_bit(event.code, mask)) {
                        command.kind =
                            pressed ? CommandKind::ModifiersPress : CommandKind::ModifiersRelease;
                        command.code = mask;
                    } else {
                        command.kind = pressed ? CommandKind::KeyPress : CommandKind::KeyRelease;
                        command.code = static_cast<std::uint8_t>(event.code);
                    }
                } else if (event.kind == InputEventKind::ConsumerDown) {
                    command.kind = CommandKind::ConsumerTap;
                    command.usage = event.code;
                } else {
                    break;
                }
                submit(command);
                break;
            }

            case macros::MacroOutputKind::SetProfile:
                request_profile(output.parameter);
                break;

            case macros::MacroOutputKind::SetKeyboardRoute:
                // The release actions matter as much as the move: whatever was
                // held is held on the computer being left behind.
                apply(engine_.set_keyboard_route(
                          static_cast<config::KeyboardRoute>(output.parameter)),
                      now_ms);
                break;

            case macros::MacroOutputKind::SetMouseRoute:
                apply(engine_.set_mouse_route(static_cast<config::MouseRoute>(output.parameter)),
                      now_ms);
                break;

            case macros::MacroOutputKind::None:
                break;
        }
    }
}

void Core1Runtime::tick(std::uint32_t now_ms) {
    capture_.tick(now_ms);

    if (profile_requested_) {
        profile_requested_ = false;
        swap_profile(requested_profile_, now_ms);
    }

    drain_macros(now_ms);
}

void Core1Runtime::begin_capture(std::uint32_t now_ms) { capture_.begin(now_ms); }

void Core1Runtime::cancel_capture() { capture_.cancel(); }

void Core1Runtime::request_profile(std::uint8_t profile) {
    requested_profile_ = profile;
    profile_requested_ = true;
}

bool Core1Runtime::take_profile_ack(std::uint8_t& profile) {
    if (!profile_acknowledged_) {
        return false;
    }
    profile_acknowledged_ = false;
    profile = acknowledged_profile_;
    return true;
}

void Core1Runtime::set_profile_now(std::uint8_t profile) {
    mapping::Binding bindings[mapping::kMaxBindings];
    const std::size_t count = profiles_.bindings_for(profile, bindings);
    engine_.set_bindings(bindings, count);
    active_profile_ = profile;
}

void Core1Runtime::swap_profile(std::uint8_t profile, std::uint32_t now_ms) {
    // A macro halfway through typing under the old profile's routing would
    // send the rest of its keystrokes wherever the new one happens to point.
    macros_.stop_all();
    drain_macros(now_ms);

    // Everything held was held under the old profile's meaning, and the
    // computer it was sent to will never hear about it again. What is still
    // under a finger is orphaned by this: dead until it is released and
    // pressed afresh, which is the only reading that neither strands a key on
    // one computer nor invents one on the other.
    apply(engine_.release_everything(), now_ms);
    buttons_ = 0;

    set_profile_now(profile);

    acknowledged_profile_ = profile;
    profile_acknowledged_ = true;
}

void Core1Runtime::define_macro(std::uint8_t macro_id,
                                const macros::MacroDefinition& definition) {
    macros_.define(macro_id, definition);
}

bool Core1Runtime::run_macro(std::uint8_t macro_id, std::uint32_t now_ms) {
    return macros_.enqueue(macro_id, keyboard_route(), now_ms);
}

void Core1Runtime::release_all() {
    OutputCommand command;
    command.kind = CommandKind::ReleaseAll;
    submit(command);

    macros_.stop_all();
    // Its releases are not sent: ReleaseAll has already let go of everything
    // on both computers, and repeating them would be noise on the link.
    macros::MacroOutput ignored;
    while (macros_.tick(0, ignored)) {
    }

    (void)engine_.release_everything();
    buttons_ = 0;
}

}  // namespace duo_input::u1
