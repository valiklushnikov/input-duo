// The two-core handshake that makes flash writes safe without silencing USB SOF.

#include "flash_park.hpp"
#include "pio_usb_sof_scheduler.h"
#include "test_support.hpp"

using duo_input::u1::FlashPark;

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

TEST_CASE(pio_usb_scheduler_emits_once_per_millisecond) {
    pio_usb_sof_scheduler_t scheduler{};

    CHECK(pio_usb_sof_scheduler_due(&scheduler, 100u));
    CHECK_FALSE(pio_usb_sof_scheduler_due(&scheduler, 1099u));
    CHECK(pio_usb_sof_scheduler_due(&scheduler, 1100u));
    CHECK_FALSE(pio_usb_sof_scheduler_due(&scheduler, 1100u));
    CHECK_FALSE(pio_usb_sof_scheduler_due(&scheduler, 2099u));
    CHECK(pio_usb_sof_scheduler_due(&scheduler, 2100u));
}

TEST_CASE(pio_usb_scheduler_skips_missed_frames_without_a_catch_up_burst) {
    pio_usb_sof_scheduler_t scheduler{};

    CHECK(pio_usb_sof_scheduler_due(&scheduler, 100u));
    CHECK(pio_usb_sof_scheduler_due(&scheduler, 15100u));
    CHECK_FALSE(pio_usb_sof_scheduler_due(&scheduler, 15100u));
    CHECK_FALSE(pio_usb_sof_scheduler_due(&scheduler, 16099u));
    CHECK(pio_usb_sof_scheduler_due(&scheduler, 16100u));
}

TEST_CASE(pio_usb_scheduler_handles_the_32_bit_timer_wrapping) {
    pio_usb_sof_scheduler_t scheduler{};

    CHECK(pio_usb_sof_scheduler_due(&scheduler, 0xFFFFFF00u));
    CHECK_FALSE(pio_usb_sof_scheduler_due(&scheduler, 743u));
    CHECK(pio_usb_sof_scheduler_due(&scheduler, 744u));
    CHECK_FALSE(pio_usb_sof_scheduler_due(&scheduler, 744u));
}

TEST_CASE(pio_usb_scheduler_survives_consecutive_short_flash_windows) {
    pio_usb_sof_scheduler_t scheduler{};

    // Each observation represents a separate page-program park. None lasts
    // for 1 ms by itself, but their combined wall time does.
    CHECK(pio_usb_sof_scheduler_due(&scheduler, 100u));
    CHECK_FALSE(pio_usb_sof_scheduler_due(&scheduler, 450u));
    CHECK_FALSE(pio_usb_sof_scheduler_due(&scheduler, 800u));
    CHECK(pio_usb_sof_scheduler_due(&scheduler, 1150u));
    CHECK_FALSE(pio_usb_sof_scheduler_due(&scheduler, 1500u));
    CHECK_FALSE(pio_usb_sof_scheduler_due(&scheduler, 1850u));
    CHECK(pio_usb_sof_scheduler_due(&scheduler, 2200u));
}

TEST_CASE(pio_usb_scheduler_reports_intervals_only_after_two_frames) {
    pio_usb_sof_scheduler_t scheduler{};

    CHECK(pio_usb_sof_scheduler_interval_min_us(&scheduler) == 0u);
    CHECK(pio_usb_sof_scheduler_interval_max_us(&scheduler) == 0u);
    CHECK(pio_usb_sof_scheduler_due(&scheduler, 100u));
    CHECK(pio_usb_sof_scheduler_interval_min_us(&scheduler) == 0u);
    CHECK(pio_usb_sof_scheduler_interval_max_us(&scheduler) == 0u);

    CHECK(pio_usb_sof_scheduler_due(&scheduler, 1100u));
    CHECK(pio_usb_sof_scheduler_interval_min_us(&scheduler) == 1000u);
    CHECK(pio_usb_sof_scheduler_interval_max_us(&scheduler) == 1000u);
    CHECK(pio_usb_sof_scheduler_due(&scheduler, 2600u));
    CHECK(pio_usb_sof_scheduler_interval_min_us(&scheduler) == 1000u);
    CHECK(pio_usb_sof_scheduler_interval_max_us(&scheduler) == 1500u);
}
