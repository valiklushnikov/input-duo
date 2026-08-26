#pragma once

// A stand-in for the peripherals that do not exist yet.
//
// Core 1's real job is to read the CH375B and turn what it sees into output
// commands. Until that exists there is nothing to read, and a device that
// cannot be made to emit anything cannot be measured - so under
// DUO_TEST_PATTERN=1, Core 1 emits a deterministic pattern instead: F13 tapped
// once a second, and the mouse tracing a square.
//
// F13 on purpose. It exists in the HID tables, no ordinary keyboard has it,
// and nothing on a normal desktop is bound to it, so a test build cannot type
// into the operator's documents or trigger a shortcut.
//
// This is compiled out entirely in a release build. A device that can generate
// its own input is exactly the thing that must not ship by accident.

#include <cstdint>

#include "output_runtime.hpp"

namespace duo_input::u1 {

/// HID usage for F13. Present in the tables, absent from real keyboards.
inline constexpr std::uint8_t kTestPatternUsage = 0x68;

/// How far the pointer travels along each side of the square, in report units.
inline constexpr std::int16_t kTestPatternStep = 10;

/// Where the generated input is sent. Fixed at compile time so a test build
/// cannot be talked into typing somewhere unexpected.
#ifndef DUO_TEST_PATTERN_ROUTE
#define DUO_TEST_PATTERN_ROUTE ::duo_input::runtime::Route::Pc1
#endif

/// Advance the pattern. Call from Core 1; ``now_ms`` is the current time.
///
/// Submits at most a few commands per call and never blocks: if the queue is
/// full, the runtime raises its own fault and this simply does not add to it.
void advance_test_pattern(OutputRuntime& runtime, std::uint32_t now_ms);

}  // namespace duo_input::u1
