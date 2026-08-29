#include "output_runtime.hpp"

#include <initializer_list>

namespace duo_input::u1 {
namespace {

using runtime::CommandKind;
using runtime::kPhysicalOwner;
using runtime::OutputCommand;
using runtime::Route;

bool reaches(Route route, hid::Target target) {
    if (route == Route::Both) {
        return true;
    }
    return (route == Route::Pc1) == (target == hid::Target::Pc1);
}

}  // namespace

bool OutputRuntime::submit(const OutputCommand& command) {
    if (queue_.push(command)) {
        return true;
    }
    // Commands are being dropped, so what is held will no longer correspond to
    // what anyone did. This runs on Core 1, which owns nothing here but this
    // counter; drain() reads it on Core 0 and decides what to do about it.
    refused_.fetch_add(1, std::memory_order_release);
    return false;
}

bool OutputRuntime::touches_keyboard(const OutputCommand& command) {
    switch (command.kind) {
        case CommandKind::KeyPress:
        case CommandKind::KeyRelease:
        case CommandKind::ModifiersPress:
        case CommandKind::ModifiersRelease:
        case CommandKind::ReleaseMacro:
        case CommandKind::ReleaseRoute:
        case CommandKind::ReleaseAll:
            return true;
        default:
            // Movement, buttons and a consumer tap say nothing about which
            // keys are down, so nothing here has to wait for a keyboard
            // report - and the pointer must never wait for typing.
            return false;
    }
}

bool OutputRuntime::may_change_keyboard(std::uint32_t now_ms) {
    bool owed = false;
    for (std::size_t index = 0; index < hid::kTargetCount; ++index) {
        if (outputs_.keyboard_unreported(static_cast<hid::Target>(index)) &&
            !stalled_[index]) {
            owed = true;
        }
    }
    if (!owed) {
        waiting_ = false;
        return true;
    }

    if (!waiting_) {
        waiting_ = true;
        waiting_since_ms_ = now_ms;
        return false;
    }
    if (static_cast<std::int32_t>(now_ms - waiting_since_ms_) <
        static_cast<std::int32_t>(kPublishGraceMs)) {
        return false;
    }

    // Long enough. Whoever has still not answered is not listening - an
    // unplugged host, a severed link - and a computer that is receiving
    // nothing must not be able to stop the one that is. Set it aside until it
    // answers again, so this costs one pause and not one per keystroke.
    for (std::size_t index = 0; index < hid::kTargetCount; ++index) {
        if (outputs_.keyboard_unreported(static_cast<hid::Target>(index))) {
            stalled_[index] = true;
        }
    }
    waiting_ = false;
    return true;
}

std::size_t OutputRuntime::drain(std::uint32_t now_ms, std::size_t budget) {
    const std::uint32_t refused = refused_.load(std::memory_order_acquire);
    if (refused != seen_refusals_) {
        // Something was dropped since the last pass. Half a macro is worse
        // than none of it, and a key whose release was the command that got
        // dropped would repeat forever - so let go of everything and apply
        // nothing this pass.
        seen_refusals_ = refused;
        fault_ = runtime::RuntimeFault::OutputQueueFull;
        queue_.clear();
        outputs_.release_all();
        return 0;
    }

    if (fault_ == runtime::RuntimeFault::OutputQueueFull) {
        // A whole pass with nothing refused: the burst is over. Everything was
        // released and the queue was emptied when the fault was raised, so
        // there is no half-typed state left to be wrong about, and going on
        // refusing input would leave both computers deaf to the operator's
        // keyboard until the board was unplugged.
        fault_ = runtime::RuntimeFault::None;
    }

    std::size_t applied = 0;
    OutputCommand command;
    while (applied < budget && queue_.peek(command)) {
        if (touches_keyboard(command) && !may_change_keyboard(now_ms)) {
            // The state now held has not reached both computers yet, and
            // replacing it would be replacing something nobody ever saw. It
            // waits in the queue - which is also what stops the other core
            // running ahead, since it emits only into an empty queue.
            break;
        }
        queue_.pop(command);
        process(command);
        ++applied;
    }
    return applied;
}

void OutputRuntime::apply_to(hid::Target target, const OutputCommand& command) {
    const bool physical = command.owner == kPhysicalOwner;

    switch (command.kind) {
        case CommandKind::KeyPress:
        case CommandKind::KeyRelease: {
            const bool pressed = command.kind == CommandKind::KeyPress;
            if (physical) {
                outputs_.physical_key(target, command.code, pressed);
            } else {
                outputs_.macro_key(command.owner, target, command.code, pressed);
            }
            return;
        }
        case CommandKind::ModifiersPress:
        case CommandKind::ModifiersRelease: {
            const bool pressed = command.kind == CommandKind::ModifiersPress;
            if (physical) {
                outputs_.physical_modifiers(target, command.code, pressed);
            } else {
                outputs_.macro_modifiers(command.owner, target, command.code, pressed);
            }
            return;
        }
        case CommandKind::MouseButtons:
            outputs_.set_mouse_buttons(target, command.code);
            return;
        case CommandKind::MouseDelta:
            outputs_.mouse_delta(target, command.delta_x, command.delta_y, command.wheel,
                                 command.pan);
            return;
        case CommandKind::ConsumerTap:
            // Consumer usages are not held, so there is no state to keep here;
            // the USB service sends them as they pass.
            return;
        case CommandKind::ReleaseRoute:
            outputs_.release_target(target);
            return;
        default:
            return;
    }
}

void OutputRuntime::process(const OutputCommand& command) {
    switch (command.kind) {
        case CommandKind::ReleaseAll:
            // Everything, everywhere, whatever the route said. A route that
            // narrowed this would leave a key held on the computer the
            // operator is not looking at, which is the case it exists for.
            outputs_.release_all();
            return;
        case CommandKind::ReleaseMacro:
            // A macro's keys go on both computers at once: it may have typed
            // on either, and it is no longer around to say which.
            outputs_.release_macro(command.owner);
            return;
        default:
            break;
    }

    for (const hid::Target target : {hid::Target::Pc1, hid::Target::Pc2}) {
        if (reaches(command.route, target)) {
            apply_to(target, command);
        }
    }
}

hid::TargetSnapshot OutputRuntime::snapshot(hid::Target target) const {
    return outputs_.snapshot(target);
}

hid::TargetSnapshot OutputRuntime::take_snapshot(hid::Target target) {
    return outputs_.take_snapshot(target);
}

void OutputRuntime::keyboard_reported(hid::Target target) {
    outputs_.keyboard_reported(target);
    // It answered, so it is listening after all.
    stalled_[static_cast<std::size_t>(target)] = false;
}

void OutputRuntime::release_all() {
    outputs_.release_all();
}

}  // namespace duo_input::u1
