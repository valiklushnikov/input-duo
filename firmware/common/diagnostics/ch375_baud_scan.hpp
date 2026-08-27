#pragma once

#include <cstddef>
#include <cstdint>

namespace duo_input::diagnostics {

struct Ch375BaudObservation {
    std::uint16_t baud = 0;
    std::uint16_t first_word = 0;
    std::uint8_t frame_count = 0;
    std::uint8_t framing_errors = 0;
};

struct Ch375SingleProbeObservation {
    std::uint8_t pad_low_percent = 0;
    std::uint16_t pad_transitions = 0;
    std::uint16_t quiet_frames = 0;
    std::uint16_t quiet_bad_frames = 0;
    std::uint8_t probe_quiet_frames = 0;
    std::uint16_t probe_quiet_first = 0;
    std::uint8_t raw_count = 0;
    std::uint16_t raw[4] = {};
    std::uint8_t framing_errors = 0;
};

/// Diagnostic-only wire format:
/// magic, count, then six bytes per observation:
/// baud LE, first 9-bit word LE, frame count, framing-error count.
std::size_t pack_ch375_baud_scan(const Ch375BaudObservation* observations,
                                 std::size_t count, std::uint8_t* out,
                                 std::size_t capacity);

/// Diagnostic-only one-shot report. It preserves what the receive pin did
/// before PIO, what arrived while U1 was silent, and every raw frame produced
/// by the first and only CHECK_EXIST command after boot.
std::size_t pack_ch375_single_probe(const Ch375SingleProbeObservation& observation,
                                    std::uint8_t* out, std::size_t capacity);

}  // namespace duo_input::diagnostics
