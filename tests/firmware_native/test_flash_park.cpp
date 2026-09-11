// The two-core handshake that makes flash writes safe without silencing USB SOF.

#include "flash_park.hpp"
#include "test_support.hpp"

using duo_input::u1::FlashPark;
using duo_input::u1::FlashSofCadence;

TEST_CASE(core0_and_core1_complete_one_flash_park_handshake) {
    FlashPark park;

    CHECK(park.idle());
    CHECK(park.request());
    CHECK(park.requested());
    CHECK_FALSE(park.parked());

    CHECK(park.begin_park());
    CHECK(park.parked());
    CHECK_FALSE(park.release_requested());

    CHECK(park.release());
    CHECK(park.release_requested());
    CHECK_FALSE(park.idle());

    park.finish_park();
    CHECK(park.idle());
}

TEST_CASE(a_second_flash_operation_cannot_overlap_the_first) {
    FlashPark park;

    CHECK(park.request());
    CHECK_FALSE(park.request());
    CHECK(park.begin_park());
    CHECK_FALSE(park.begin_park());
    CHECK(park.release());
    park.finish_park();

    CHECK(park.request());
}

TEST_CASE(release_without_a_park_is_refused) {
    FlashPark park;

    CHECK_FALSE(park.release());
    CHECK(park.idle());
}

TEST_CASE(flash_sof_cadence_emits_once_per_millisecond) {
    FlashSofCadence cadence;

    CHECK(cadence.due(100u));
    CHECK_FALSE(cadence.due(1099u));
    CHECK(cadence.due(1100u));
    CHECK_FALSE(cadence.due(1100u));
    CHECK_FALSE(cadence.due(2099u));
    CHECK(cadence.due(2100u));
}

TEST_CASE(flash_sof_cadence_skips_missed_frames_without_a_catch_up_burst) {
    FlashSofCadence cadence;

    CHECK(cadence.due(100u));
    CHECK(cadence.due(15100u));
    CHECK_FALSE(cadence.due(15100u));
    CHECK_FALSE(cadence.due(16099u));
    CHECK(cadence.due(16100u));
}

TEST_CASE(flash_sof_cadence_handles_the_32_bit_timer_wrapping) {
    FlashSofCadence cadence;

    CHECK(cadence.due(0xFFFFFF00u));
    CHECK_FALSE(cadence.due(743u));
    CHECK(cadence.due(744u));
    CHECK_FALSE(cadence.due(744u));
}

TEST_CASE(flash_sof_cadence_survives_consecutive_short_flash_windows) {
    FlashSofCadence cadence;

    // Each observation represents a separate page-program park. None lasts
    // for 1 ms by itself, but their combined wall time does.
    CHECK(cadence.due(100u));
    CHECK_FALSE(cadence.due(450u));
    CHECK_FALSE(cadence.due(800u));
    CHECK(cadence.due(1150u));
    CHECK_FALSE(cadence.due(1500u));
    CHECK_FALSE(cadence.due(1850u));
    CHECK(cadence.due(2200u));
}
