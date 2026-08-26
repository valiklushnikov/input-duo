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
    // what anyone did. Recording the fault is how drain() knows to let go of
    // everything rather than type half of it.
    fault_ = runtime::RuntimeFault::OutputQueueFull;
    return false;
}

std::size_t OutputRuntime::drain(std::size_t budget) {
    if (fault_ == runtime::RuntimeFault::OutputQueueFull) {
        // Half a macro is worse than none of it, and a key whose release was
        // the command that got dropped would repeat forever.
        queue_.clear();
        outputs_.release_all();
        return 0;
    }

    std::size_t applied = 0;
    OutputCommand command;
    while (applied < budget && queue_.pop(command)) {
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

void OutputRuntime::release_all() {
    outputs_.release_all();
}

}  // namespace duo_input::u1
