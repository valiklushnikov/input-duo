// What U2 remembers about a link that died, so it can be told afterwards.
//
// The fail-safe itself is not observable while it happens: everything the host
// can see about U2 travels over the link that just went silent. So U2 records
// the drop and reports it once the link is back, which is the only way this
// device can be shown to release within 100 ms on real hardware rather than
// only in a unit test.

#include "link_drop_log.hpp"
#include "test_support.hpp"

using duo_input::u2::LinkDropLog;

TEST_CASE(a_board_that_has_never_lost_the_link_reports_nothing) {
    LinkDropLog log;

    CHECK_EQ(log.drops(), 0u);
    CHECK_EQ(log.last_release_ms(), 0u);
}

TEST_CASE(a_release_is_recorded_with_the_silence_that_caused_it) {
    LinkDropLog log;

    log.released(100);

    CHECK_EQ(log.drops(), 1u);
    CHECK_EQ(log.last_release_ms(), 100u);
}

TEST_CASE(one_silence_is_one_drop_however_long_it_lasts) {
    LinkDropLog log;

    // The loop calls this for as long as the link stays down. That is one
    // event, not one per pass, or the count would measure loop speed.
    log.released(100);
    log.released(200);
    log.released(5000);

    CHECK_EQ(log.drops(), 1u);
    // The first moment is the one that matters: it is when the keys were let
    // go. How long the link stayed down afterwards says nothing about the
    // fail-safe.
    CHECK_EQ(log.last_release_ms(), 100u);
}

TEST_CASE(the_link_coming_back_arms_the_next_drop) {
    LinkDropLog log;
    log.released(100);

    log.recovered();
    log.released(140);

    CHECK_EQ(log.drops(), 2u);
    CHECK_EQ(log.last_release_ms(), 140u);
}

TEST_CASE(recovering_without_a_drop_changes_nothing) {
    LinkDropLog log;

    log.recovered();
    log.recovered();

    CHECK_EQ(log.drops(), 0u);
}

TEST_CASE(the_count_stops_rather_than_wrapping) {
    LinkDropLog log;

    // It travels as a single byte. Wrapping would turn a link that drops
    // constantly into one that reports a handful of drops, which is the
    // opposite of what the number is for.
    for (int index = 0; index < 300; ++index) {
        log.recovered();
        log.released(100);
    }

    CHECK_EQ(log.drops(), 255u);
}

TEST_CASE(an_absurdly_long_silence_is_reported_as_the_largest_it_can_be) {
    LinkDropLog log;

    log.released(500000);

    // Two bytes on the wire. Saturating keeps it obviously large instead of
    // wrapping into a small, believable, wrong number.
    CHECK_EQ(log.last_release_ms(), 65535u);
}
