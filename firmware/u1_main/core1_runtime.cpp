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

/// Has ``deadline`` arrived? Subtraction, because the counter wraps at 49 days.
bool reached(std::uint32_t now_ms, std::uint32_t deadline_ms) {
    return static_cast<std::int32_t>(now_ms - deadline_ms) >= 0;
}

constexpr std::uint16_t kProfileMailboxOccupied = 0x100u;
constexpr std::uint16_t kProfileMailboxFromHost = 0x200u;

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
        dropped_.fetch_add(1, std::memory_order_relaxed);
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
                request_profile_from_core1(action.parameter);
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
        publish_capture_state();
        return;
    }

    apply(engine_.handle(event), now_ms);
}

void Core1Runtime::drain_macros(std::uint32_t now_ms) {
    // One macro event per pass of Core 0's loop, and never two inside the same
    // millisecond. Both halves matter, and neither alone is enough.
    //
    // Core 0 holds output *state*, not a queue of reports. A press and the
    // release that follows it, applied in the same drain, leave that state
    // identical to what was last sent and produce no report at all - so a
    // macro that emitted faster than Core 0 drained would type nothing on the
    // far computer, which is precisely what an unpaced TEXT step did. The two
    // cores do not loop at the same rate and never will: Core 1's pass is two
    // controller ticks, Core 0's is USB, the CDC service, a drain, a publish
    // and an SPI transaction. Counting events per Core 1 pass measures the
    // wrong loop.
    //
    // Waiting for the queue to empty is what makes the pass boundary
    // observable from here: it is empty only once Core 0 has drained what was
    // in it, so the next event cannot join the previous one in a drain. The
    // millisecond floor is what stops a fast Core 0 loop from driving the
    // macro faster than the host polls the endpoint. It is also why a long
    // TEXT step no longer fills the queue it shares with the other core.
    if (!reached(now_ms, next_macro_event_ms_)) {
        return;
    }
    if (sink_.pending() != 0) {
        return;
    }

    macros::MacroOutput output;
    if (macros_.tick(now_ms, output)) {
        next_macro_event_ms_ = now_ms + kMacroEventIntervalMs;
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
                request_profile_from_core1(output.parameter);
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
    // Read, act, publish, and only then clear - in that order.
    //
    // Core 0 reports a posted-but-not-yet-started capture as running, because
    // between posting it and this core reaching it there is otherwise a pass
    // in which nothing anywhere says a capture is coming. Clearing the mailbox
    // before publishing would put that same gap back, a few instructions wide:
    // the request gone, the state not yet stored, and Core 0 free to read
    // both and announce that no capture is running. It then refuses the
    // trigger the operator is about to press.
    //
    // Compare-exchange rather than a plain store, so a request Core 0 posted
    // while this was running is not silently dropped on the floor.
    const std::uint8_t capture_request =
        capture_request_mailbox_.load(std::memory_order_acquire);
    if (capture_request == kCaptureRequestBegin) {
        capture_.begin(now_ms);
    } else if (capture_request == kCaptureRequestCancel) {
        capture_.cancel();
    }

    capture_.tick(now_ms);
    publish_capture_state();

    if (capture_request != 0) {
        std::uint8_t consumed = capture_request;
        capture_request_mailbox_.compare_exchange_strong(consumed, 0,
                                                         std::memory_order_acq_rel,
                                                         std::memory_order_relaxed);
    }

    // First, and before the swap below: whatever else was asked for, the point
    // of this one is that it happens.
    if (release_all_requested_.exchange(false, std::memory_order_acq_rel)) {
        release_all();
    }

    const std::uint16_t requested =
        requested_profile_mailbox_.exchange(0, std::memory_order_acq_rel);
    if ((requested & kProfileMailboxOccupied) != 0) {
        swap_profile(static_cast<std::uint8_t>(requested), now_ms, true);
    } else if (local_profile_requested_) {
        local_profile_requested_ = false;
        swap_profile(local_requested_profile_, now_ms, false);
    }

    drain_macros(now_ms);
}

void Core1Runtime::begin_capture(std::uint32_t now_ms) {
    capture_.begin(now_ms);
    publish_capture_state();
}

void Core1Runtime::cancel_capture() {
    capture_.cancel();
    publish_capture_state();
}

void Core1Runtime::request_capture_begin() {
    capture_request_mailbox_.store(kCaptureRequestBegin, std::memory_order_release);
}

void Core1Runtime::request_capture_cancel() {
    capture_request_mailbox_.store(kCaptureRequestCancel, std::memory_order_release);
}

void Core1Runtime::publish_capture_state() {
    mapping::CapturedTrigger trigger;
    if (capture_.take(trigger)) {
        const std::uint32_t packed =
            kCaptureMailboxOccupied |
            (static_cast<std::uint32_t>(trigger.kind) << 16) |
            (static_cast<std::uint32_t>(trigger.code) << 8) |
            static_cast<std::uint32_t>(trigger.modifiers);
        capture_event_mailbox_.store(packed, std::memory_order_release);
    }
    // Event first, state second. Main reads in the same order.
    capture_active_published_.store(capture_.active(), std::memory_order_release);
}

bool Core1Runtime::take_capture_event(mapping::CapturedTrigger& out) {
    const std::uint32_t packed =
        capture_event_mailbox_.exchange(0, std::memory_order_acq_rel);
    if ((packed & kCaptureMailboxOccupied) == 0) {
        return false;
    }
    out.kind = static_cast<config::TriggerKind>((packed >> 16) & 0xFFu);
    out.code = static_cast<std::uint8_t>((packed >> 8) & 0xFFu);
    out.modifiers = static_cast<std::uint8_t>(packed & 0xFFu);
    return true;
}

void Core1Runtime::request_profile(std::uint8_t profile) {
    requested_profile_mailbox_.store(
        static_cast<std::uint16_t>(kProfileMailboxOccupied | profile),
        std::memory_order_release);
}

void Core1Runtime::request_profile_from_core1(std::uint8_t profile) {
    local_requested_profile_ = profile;
    local_profile_requested_ = true;
}

bool Core1Runtime::take_profile_ack(std::uint8_t& profile) {
    bool requested_by_host = false;
    return take_profile_ack(profile, requested_by_host);
}

bool Core1Runtime::take_profile_ack(std::uint8_t& profile, bool& requested_by_host) {
    const std::uint16_t acknowledged =
        profile_ack_mailbox_.exchange(0, std::memory_order_acq_rel);
    if ((acknowledged & kProfileMailboxOccupied) == 0) {
        return false;
    }
    profile = static_cast<std::uint8_t>(acknowledged);
    requested_by_host = (acknowledged & kProfileMailboxFromHost) != 0;
    return true;
}

void Core1Runtime::set_profile_now(std::uint8_t profile) {
    // Static, and measured rather than guessed at. A hundred and twenty-eight
    // bindings is a kilobyte, and Core 1's whole stack is two - with the swap
    // that calls this already carrying the macro drain and the release beneath
    // it. Nothing re-enters here: one core swaps profiles, one at a time.
    static mapping::Binding bindings[mapping::kMaxBindings];
    const std::size_t count = profiles_.bindings_for(profile, bindings);
    engine_.set_bindings(bindings, count);
    active_profile_.store(profile, std::memory_order_release);
}

void Core1Runtime::swap_profile(std::uint8_t profile, std::uint32_t now_ms,
                                bool requested_by_host) {
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

    profile_ack_mailbox_.store(
        static_cast<std::uint16_t>(kProfileMailboxOccupied |
                                   (requested_by_host ? kProfileMailboxFromHost : 0u) | profile),
        std::memory_order_release);
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

    // Abandoned rather than drained. ReleaseAll has already let go of
    // everything on both computers, so the releases would be noise on the link
    // - and, paced one event to a pass, noise that arrived milliseconds later,
    // after the step pool it came from had been rewritten underneath this.
    macros_.abandon();

    (void)engine_.release_everything();
    buttons_ = 0;
}

}  // namespace duo_input::u1
