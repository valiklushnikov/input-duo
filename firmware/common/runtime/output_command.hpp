#pragma once

// One thing that should happen to the output state, as a value.
//
// Everything Core 1 decides - a key was pressed, a macro let go, the mouse
// moved - becomes one of these and crosses to Core 0 through the queue. Being
// a plain, fixed-size value with no pointers is what makes that safe: nothing
// here refers to memory the other core might reuse.

#include <cstdint>

#include "hid/types.hpp"

namespace duo_input::runtime {

/// How many commands can be waiting at once.
///
/// A millisecond of the fastest plausible input is a handful of events, so 128
/// is far more headroom than ordinary use needs. It is a bound, not a target:
/// reaching it means something is wrong, and that is worth knowing.
inline constexpr std::size_t kOutputQueueCapacity = 128;

/// Which computers a command is for.
///
/// Distinct from hid::Target, which is one computer. A route can be both, and
/// the difference matters: a key held on Both and released on one must stay
/// held on the other.
enum class Route : std::uint8_t {
    Pc1 = 0,
    Pc2 = 1,
    Both = 2,
};

enum class CommandKind : std::uint8_t {
    None = 0,
    KeyPress,
    KeyRelease,
    ModifiersPress,
    ModifiersRelease,
    MouseButtons,
    MouseDelta,
    ConsumerTap,
    /// Let go of everything one macro was holding.
    ReleaseMacro,
    /// Let go of everything on the routed computers.
    ReleaseRoute,
    /// Let go of everything, everywhere, whatever the route says.
    ReleaseAll,
};

/// The owner index meaning "the operator's own hands" rather than a macro.
inline constexpr std::uint8_t kPhysicalOwner = 0xFF;

struct OutputCommand {
    CommandKind kind = CommandKind::None;
    Route route = Route::Pc1;

    /// Who is doing this: kPhysicalOwner, or a macro index.
    std::uint8_t owner = kPhysicalOwner;

    /// HID usage, modifier mask or button mask, depending on ``kind``.
    std::uint8_t code = 0;

    /// Consumer usage, for ConsumerTap.
    std::uint16_t usage = 0;

    std::int16_t delta_x = 0;
    std::int16_t delta_y = 0;
    std::int8_t wheel = 0;
    std::int8_t pan = 0;

    /// When the peripheral report behind this command arrived, in microseconds
    /// on the device's own clock, or zero when nothing arrived.
    ///
    /// This is the only reason the device can say anything about its own
    /// latency. Both ends of the interval - this stamp, taken on Core 1 as the
    /// controller hands over a report, and the moment Core 0 applies the
    /// command - are on the same board and the same clock, so the difference is
    /// a measurement rather than an estimate.
    ///
    /// Zero means "no report caused this": a macro step emitted on a schedule,
    /// a button on the case, a release the host asked for. Those are timed
    /// against nothing, because the delay in them is one somebody chose.
    std::uint32_t origin_us = 0;
};

/// Something that went wrong badly enough that the output state is suspect.
enum class RuntimeFault : std::uint8_t {
    None = 0,
    /// Commands were dropped, so what is held no longer matches what happened.
    OutputQueueFull,
};

}  // namespace duo_input::runtime
