// When U1 has to tell U2 to let go, and how often.
//
// U2 releases everything by itself after 100 ms of silence - that fail-safe is
// what stops a severed cable leaving a key held down on PC2 for ever. U1 keeps
// no such clock; SpiMaster sends on change, so once U2 has released a key on
// its own the two ends disagree about what PC2 is holding and nothing that
// happens afterwards corrects it. A key still held by the operator across the
// outage produces no change, so no frame, so PC2 never gets it back.
//
// The correction is one CONTROL_RELEASE_ALL on the pass where the link starts
// answering again: it makes both ends agree on "nothing held", and it clears
// SpiMaster's own idea of what PC2 already knows, so the next state goes out
// whether or not it changed.
//
// Once per reconnection, and not once per pass. A release every pass is a
// keyboard that cannot hold a key down at all on PC2, which is a worse fault
// than the one being fixed.

#include "link_reconnect.hpp"
#include "test_support.hpp"

using duo_input::u1::reference::LinkReconnect;

TEST_CASE(a_link_that_has_never_answered_asks_for_nothing) {
    LinkReconnect reconnect;

    CHECK_FALSE(reconnect.should_release(false));
    CHECK_FALSE(reconnect.should_release(false));
}

TEST_CASE(the_first_answer_asks_for_one_release) {
    LinkReconnect reconnect;

    CHECK(reconnect.should_release(true));
}

TEST_CASE(a_link_that_keeps_answering_asks_only_once) {
    LinkReconnect reconnect;

    CHECK(reconnect.should_release(true));
    CHECK_FALSE(reconnect.should_release(true));
    CHECK_FALSE(reconnect.should_release(true));
}

TEST_CASE(a_link_that_came_back_asks_again) {
    LinkReconnect reconnect;

    CHECK(reconnect.should_release(true));
    CHECK_FALSE(reconnect.should_release(false));
    CHECK(reconnect.should_release(true));
}

TEST_CASE(a_long_outage_still_asks_exactly_once_on_return) {
    LinkReconnect reconnect;

    CHECK(reconnect.should_release(true));
    for (int pass = 0; pass < 50; ++pass) {
        CHECK_FALSE(reconnect.should_release(false));
    }

    CHECK(reconnect.should_release(true));
    for (int pass = 0; pass < 50; ++pass) {
        CHECK_FALSE(reconnect.should_release(true));
    }
}
