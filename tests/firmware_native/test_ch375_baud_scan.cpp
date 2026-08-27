#include "diagnostics/ch375_baud_scan.hpp"
#include "test_support.hpp"

#include <cstdint>

using duo_input::diagnostics::Ch375BaudObservation;
using duo_input::diagnostics::Ch375SingleProbeObservation;
using duo_input::diagnostics::pack_ch375_baud_scan;
using duo_input::diagnostics::pack_ch375_single_probe;

TEST_CASE(ch375_baud_scan_report_keeps_each_rate_with_its_observation) {
    const Ch375BaudObservation observations[] = {
        {8800, 0x01A8, 1, 0},
        {9600, 0x0039, 4, 1},
    };
    std::uint8_t report[14] = {};

    const std::size_t size = pack_ch375_baud_scan(observations, 2, report, sizeof(report));

    const std::uint8_t expected[] = {
        0xB5, 0x02,
        0x60, 0x22, 0xA8, 0x01, 0x01, 0x00,
        0x80, 0x25, 0x39, 0x00, 0x04, 0x01,
    };
    CHECK_EQ(size, sizeof(expected));
    for (std::size_t index = 0; index < sizeof(expected); ++index) {
        CHECK_EQ(report[index], expected[index]);
    }
}

TEST_CASE(ch375_baud_scan_report_never_writes_a_partial_observation) {
    const Ch375BaudObservation observation = {9600, 0x01A8, 1, 0};
    std::uint8_t report[7] = {0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC};

    const std::size_t size = pack_ch375_baud_scan(&observation, 1, report, sizeof(report));

    CHECK_EQ(size, 2u);
    CHECK_EQ(report[0], 0xB5u);
    CHECK_EQ(report[1], 0u);
    CHECK_EQ(report[2], 0xCCu);
}

TEST_CASE(ch375_single_probe_report_preserves_boot_quiet_and_raw_frames) {
    Ch375SingleProbeObservation observation;
    observation.pad_low_percent = 7;
    observation.pad_transitions = 0x1234;
    observation.quiet_frames = 0x2345;
    observation.quiet_bad_frames = 0x3456;
    observation.probe_quiet_frames = 2;
    observation.probe_quiet_first = 0x01AB;
    observation.raw_count = 3;
    observation.raw[0] = 0x01A8;
    observation.raw[1] = 0x0055;
    observation.raw[2] = 0x0106;
    observation.raw[3] = 0x0000;
    observation.framing_errors = 1;
    std::uint8_t report[21] = {};

    const std::size_t size = pack_ch375_single_probe(observation, report, sizeof(report));

    const std::uint8_t expected[] = {
        0xB6,
        0x07,
        0x34, 0x12,
        0x45, 0x23,
        0x56, 0x34,
        0x02,
        0xAB, 0x01,
        0x03,
        0xA8, 0x01,
        0x55, 0x00,
        0x06, 0x01,
        0x00, 0x00,
        0x01,
    };
    CHECK_EQ(size, sizeof(expected));
    for (std::size_t index = 0; index < sizeof(expected); ++index) {
        CHECK_EQ(report[index], expected[index]);
    }
}

TEST_CASE(ch375_single_probe_report_does_not_partially_write) {
    const Ch375SingleProbeObservation observation = {};
    std::uint8_t report[20] = {};

    const std::size_t size = pack_ch375_single_probe(observation, report, sizeof(report));

    CHECK_EQ(size, 0u);
}
