#pragma once

// What has gone wrong since the device started, counted rather than logged.
//
// A count is the right shape for this. The device has no room to keep a log
// and no way to send one unprompted, and the questions an operator or a
// support conversation actually asks are of the form "is this happening a lot"
// - which a number answers and a message does not.
//
// Everything here is monotonic within one power cycle. Nothing is persisted:
// writing a counter to flash on every event would wear the part out to record
// events nobody reads, and the RP2040's watchdog scratch registers already
// carry across a reset the one number that has to.

#include <cstdint>

#include "diagnostics/codes.hpp"

namespace duo_input::diagnostics {

struct Counters {
    /// Frames that arrived damaged, on either link.
    std::uint32_t bad_crc = 0;
    /// Times the host went away.
    std::uint32_t disconnect = 0;
    /// Requests that were never answered in time.
    std::uint32_t timeout = 0;
    /// Frames whose sequence was not the one expected.
    std::uint32_t bad_sequence = 0;
    /// Configuration writes abandoned before they were committed.
    std::uint32_t aborted_staging = 0;
    /// Frames the SPI link dropped because they did not verify.
    std::uint32_t spi_crc_errors = 0;
    /// Times U2 released everything because the link went quiet.
    std::uint32_t link_timeouts = 0;
};

/// What survives a reset, and therefore what the device can report about one.
struct ResetRecord {
    ResetReason reason = ResetReason::Unknown;
    /// How many watchdog resets this device has had since it was last
    /// unplugged. A number that climbs is a defect worth chasing.
    std::uint32_t watchdog_count = 0;
};

}  // namespace duo_input::diagnostics
