#include "diagnostics/ch375_baud_scan.hpp"

namespace duo_input::diagnostics {

namespace {

constexpr std::size_t kHeaderSize = 2;
constexpr std::size_t kObservationSize = 6;
constexpr std::size_t kSingleProbeSize = 21;

void put_u16(std::uint8_t* out, std::uint16_t value) {
    out[0] = static_cast<std::uint8_t>(value & 0xFFu);
    out[1] = static_cast<std::uint8_t>(value >> 8);
}

}  // namespace

std::size_t pack_ch375_baud_scan(const Ch375BaudObservation* observations, std::size_t count,
                                 std::uint8_t* out, std::size_t capacity) {
    if (out == nullptr || capacity < kHeaderSize) {
        return 0;
    }

    const std::size_t fits = (capacity - kHeaderSize) / kObservationSize;
    const std::size_t packed = count < fits ? count : fits;
    out[0] = 0xB5;
    out[1] = static_cast<std::uint8_t>(packed);

    for (std::size_t index = 0; index < packed; ++index) {
        std::uint8_t* at = out + kHeaderSize + index * kObservationSize;
        put_u16(at, observations[index].baud);
        put_u16(at + 2, observations[index].first_word);
        at[4] = observations[index].frame_count;
        at[5] = observations[index].framing_errors;
    }
    return kHeaderSize + packed * kObservationSize;
}

std::size_t pack_ch375_single_probe(const Ch375SingleProbeObservation& observation,
                                    std::uint8_t* out, std::size_t capacity) {
    if (out == nullptr || capacity < kSingleProbeSize) {
        return 0;
    }

    out[0] = 0xB6;
    out[1] = observation.pad_low_percent;
    put_u16(out + 2, observation.pad_transitions);
    put_u16(out + 4, observation.quiet_frames);
    put_u16(out + 6, observation.quiet_bad_frames);
    out[8] = observation.probe_quiet_frames;
    put_u16(out + 9, observation.probe_quiet_first);
    out[11] = observation.raw_count;
    for (std::size_t index = 0; index < 4; ++index) {
        put_u16(out + 12 + index * 2, observation.raw[index]);
    }
    out[20] = observation.framing_errors;
    return kSingleProbeSize;
}

}  // namespace duo_input::diagnostics
