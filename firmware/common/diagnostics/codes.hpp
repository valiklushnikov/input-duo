#pragma once

// Why the device last restarted.
//
// Worth knowing because the answers mean very different things. A power cycle
// is someone plugging the device in. A watchdog reset means the firmware
// stopped servicing its loop, which is a defect. A brown-out means the supply
// is inadequate, which is a cable or a hub. Reporting them as one number would
// hide the two that need fixing behind the one that does not.

#include <cstdint>

namespace duo_input::diagnostics {

enum class ResetReason : std::uint8_t {
    /// Not established. Better than guessing.
    Unknown = 0,
    /// Power was applied. The ordinary case.
    PowerOn,
    /// The loop stopped being serviced for longer than the watchdog allows.
    Watchdog,
    /// The host or a debugger asked for it.
    Requested,
    /// The supply sagged below what the part needs.
    BrownOut,
};

constexpr const char* name_of(ResetReason reason) {
    switch (reason) {
        case ResetReason::PowerOn:
            return "power_on";
        case ResetReason::Watchdog:
            return "watchdog";
        case ResetReason::Requested:
            return "requested";
        case ResetReason::BrownOut:
            return "brown_out";
        default:
            return "unknown";
    }
}

}  // namespace duo_input::diagnostics
