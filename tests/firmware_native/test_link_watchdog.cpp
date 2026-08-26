// The rule U2 exists to keep: no valid frame, nothing held.
//
// If the link to U1 goes away while a key is down, the computer on the other
// side keeps receiving that key until something says otherwise - and nothing
// will, because the thing that would say so is the link that just died. So U2
// releases everything on its own after 100 ms of silence, and this file is
// where that 100 ms is pinned down.

#include "link/spi_protocol.hpp"
#include "link_watchdog.hpp"
#include "test_support.hpp"

using duo_input::link::SequenceTracker;
using duo_input::link::SequenceVerdict;
using duo_input::u2::LinkWatchdog;

// ------------------------------------------------------------------ watchdog

TEST_CASE(a_link_that_has_never_spoken_is_already_expired) {
    LinkWatchdog watchdog(100);

    // Holding nothing and reporting "no link" is honest. Reporting "link fine"
    // before a single frame has arrived is not.
    CHECK(watchdog.expired(0));
    CHECK(watchdog.expired(5000));
}

TEST_CASE(endpoint_releases_at_100ms_not_99ms) {
    LinkWatchdog watchdog(100);
    watchdog.observe_valid(1000);

    CHECK_FALSE(watchdog.expired(1099));
    CHECK(watchdog.expired(1100));
}

TEST_CASE(a_valid_frame_pushes_the_deadline_out) {
    LinkWatchdog watchdog(100);
    watchdog.observe_valid(1000);
    watchdog.observe_valid(1050);

    CHECK_FALSE(watchdog.expired(1149));
    CHECK(watchdog.expired(1150));
}

TEST_CASE(a_heartbeat_counts_as_life_even_with_no_input) {
    LinkWatchdog watchdog(100);
    watchdog.observe_valid(1000);

    // Nothing is being typed, but the link is alive; the heartbeat is what
    // says so, and it must be enough on its own.
    for (std::uint32_t now = 1020; now < 2000; now += 20) {
        watchdog.observe_valid(now);
        CHECK_FALSE(watchdog.expired(now));
    }
}

TEST_CASE(the_clock_wrapping_round_does_not_look_like_silence) {
    LinkWatchdog watchdog(100);
    const std::uint32_t near_the_end = 0xFFFFFFF0;
    watchdog.observe_valid(near_the_end);

    // A millisecond counter wraps after 49 days. Subtracting through the wrap
    // in unsigned arithmetic still gives the real elapsed time; treating it as
    // an enormous gap would release every key on a device that was fine.
    CHECK_FALSE(watchdog.expired(near_the_end + 50));
    CHECK(watchdog.expired(near_the_end + 100));
}

TEST_CASE(a_reset_puts_the_link_back_to_silent) {
    LinkWatchdog watchdog(100);
    watchdog.observe_valid(1000);
    CHECK_FALSE(watchdog.expired(1050));

    watchdog.reset();

    CHECK(watchdog.expired(1050));
}

TEST_CASE(the_timeout_is_the_one_the_specification_names) {
    CHECK_EQ(duo_input::u2::kLinkTimeoutMs, 100u);
}

// ----------------------------------------------------------------- sequences

TEST_CASE(the_first_frame_is_always_fresh) {
    SequenceTracker tracker;

    CHECK_EQ(tracker.observe(7), SequenceVerdict::Fresh);
}

TEST_CASE(the_next_sequence_is_fresh) {
    SequenceTracker tracker;
    tracker.observe(7);

    CHECK_EQ(tracker.observe(8), SequenceVerdict::Fresh);
}

TEST_CASE(the_same_sequence_twice_is_a_duplicate) {
    SequenceTracker tracker;
    tracker.observe(7);

    // Every state this link carries is absolute, so applying a duplicate
    // changes nothing. It is reported so it can be counted, not so it can be
    // refused.
    CHECK_EQ(tracker.observe(7), SequenceVerdict::Duplicate);
}

TEST_CASE(a_duplicate_does_not_move_the_expectation_backwards) {
    SequenceTracker tracker;
    tracker.observe(7);
    tracker.observe(7);

    CHECK_EQ(tracker.observe(8), SequenceVerdict::Fresh);
}

TEST_CASE(a_skipped_sequence_is_a_gap) {
    SequenceTracker tracker;
    tracker.observe(7);

    CHECK_EQ(tracker.observe(10), SequenceVerdict::Gap);
}

TEST_CASE(a_gap_still_becomes_the_new_expectation) {
    SequenceTracker tracker;
    tracker.observe(7);
    tracker.observe(10);

    // The frame was accepted - the state it carried is current - so the next
    // one in order is fresh rather than another gap.
    CHECK_EQ(tracker.observe(11), SequenceVerdict::Fresh);
}

TEST_CASE(a_sequence_that_goes_backwards_is_a_gap_not_a_duplicate) {
    SequenceTracker tracker;
    tracker.observe(100);

    // U1 restarting begins at zero again. That is a gap in this link's terms:
    // something was missed, and the counter should say so.
    CHECK_EQ(tracker.observe(3), SequenceVerdict::Gap);
}

TEST_CASE(the_sequence_wrapping_round_is_not_a_gap) {
    SequenceTracker tracker;
    tracker.observe(0xFFFF);

    // 16 bits wrap after 65536 frames, which at one frame per millisecond is
    // about a minute. Counting that as a gap would flag a fault every minute
    // on a link that never dropped anything.
    CHECK_EQ(tracker.observe(0), SequenceVerdict::Fresh);
}

TEST_CASE(a_reset_forgets_what_was_expected) {
    SequenceTracker tracker;
    tracker.observe(100);

    tracker.reset();

    CHECK_EQ(tracker.observe(3), SequenceVerdict::Fresh);
}

TEST_CASE(gaps_are_counted_so_a_flaky_link_is_visible) {
    SequenceTracker tracker;
    tracker.observe(1);
    tracker.observe(5);
    tracker.observe(6);
    tracker.observe(20);

    CHECK_EQ(tracker.gap_count(), 2u);
    CHECK_EQ(tracker.duplicate_count(), 0u);
}

TEST_CASE(duplicates_are_counted_separately_from_gaps) {
    SequenceTracker tracker;
    tracker.observe(1);
    tracker.observe(1);
    tracker.observe(1);

    CHECK_EQ(tracker.duplicate_count(), 2u);
    CHECK_EQ(tracker.gap_count(), 0u);
}
