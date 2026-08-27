#pragma once

// What a macro is made of.
//
// A step is one instruction: press this, wait that long, go to that computer.
// The vocabulary lives apart from the machine that runs it, because the editor
// on the host builds these and the scheduler only executes them.
//
// Text is not here as a step the firmware understands. The host compiles a
// string into key steps before it is ever sent, because turning characters
// into usages needs a keyboard layout, and the device does not know which one
// the operator is typing on.

#include <cstddef>
#include <cstdint>

#include "config/format.hpp"

namespace duo_input::u1::macros {

struct MacroStep {
    config::MacroStepType kind = config::MacroStepType::DELAY;
    /// A HID usage, a profile, or a route - whichever the step needs.
    std::uint16_t code = 0;
    std::uint16_t delay_ms = 0;
    /// How much longer the delay may randomly be. A macro that types at a
    /// perfectly even rhythm is the one thing that looks least like a person.
    std::uint16_t jitter_ms = 0;
};

struct MacroDefinition {
    const MacroStep* steps = nullptr;
    std::size_t count = 0;
};

}  // namespace duo_input::u1::macros
