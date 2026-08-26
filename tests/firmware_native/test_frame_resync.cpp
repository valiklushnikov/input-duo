// When a half-arrived frame should be abandoned.
//
// The slave cannot ask the master to start over, so it has to decide on its
// own when what it is holding will never be completed. Getting this wrong in
// either direction is bad: abandon too eagerly and no frame ever completes,
// abandon too late and one interrupted frame desynchronises every frame after
// it, because the bytes of the next one land where the last one left off.
//
// The first version judged this by the chip select line and abandoned every
// frame after its first byte, so nothing ever arrived. Time is the honest
// measure here: a frame takes half a millisecond to cross, and the master
// idles for twenty between them, so a gap in the middle of one is not
// ambiguous.

#include "frame_resync.hpp"
#include "test_support.hpp"

using duo_input::u2::FrameResync;
using duo_input::u2::kResyncStallMs;

namespace {
constexpr std::uint32_t kFrame = 64;
}

TEST_CASE(a_frame_that_has_not_started_is_never_stalled) {
    FrameResync resync;

    // Nothing has arrived. There is nothing to abandon, however long it lasts.
    for (std::uint32_t now = 1000; now < 1000 + kResyncStallMs * 10; ++now) {
        CHECK(!resync.update(now, kFrame, kFrame));
    }
}

TEST_CASE(a_complete_frame_is_not_stalled) {
    FrameResync resync;

    CHECK(!resync.update(1000, 0, kFrame));
    CHECK(!resync.update(1000 + kResyncStallMs * 4, 0, kFrame));
}

TEST_CASE(a_frame_still_arriving_is_not_stalled) {
    FrameResync resync;

    // One byte every poll. Slower than the wire really is, and still fine.
    std::uint32_t now = 1000;
    for (std::uint32_t remaining = kFrame - 1; remaining > 0; --remaining) {
        now += 1;
        CHECK(!resync.update(now, remaining, kFrame));
    }
}

TEST_CASE(a_frame_that_stops_part_way_is_abandoned_once_the_gap_is_long_enough) {
    FrameResync resync;
    resync.update(1000, 40, kFrame);

    CHECK(!resync.update(1000 + kResyncStallMs - 1, 40, kFrame));
    CHECK(resync.update(1000 + kResyncStallMs, 40, kFrame));
}

TEST_CASE(the_gap_is_measured_from_the_last_byte_not_the_first) {
    FrameResync resync;
    resync.update(1000, 40, kFrame);

    // A byte arrives late but it does arrive: the frame is alive again, and
    // the clock for giving up starts over.
    resync.update(1000 + kResyncStallMs - 1, 39, kFrame);

    CHECK(!resync.update(1000 + kResyncStallMs + 1, 39, kFrame));
    CHECK(resync.update(1000 + kResyncStallMs - 1 + kResyncStallMs, 39, kFrame));
}

TEST_CASE(a_stall_is_reported_once_not_on_every_poll) {
    FrameResync resync;
    resync.update(1000, 40, kFrame);
    CHECK(resync.update(1000 + kResyncStallMs, 40, kFrame));

    // The caller has already restarted the transfer. Reporting again would
    // restart it a second time and lose the frame that had begun arriving.
    CHECK(!resync.update(1000 + kResyncStallMs + 1, 40, kFrame));
}

TEST_CASE(the_millisecond_counter_wrapping_does_not_invent_a_stall) {
    FrameResync resync;
    const std::uint32_t near_the_end = 0xFFFFFFF0;

    resync.update(near_the_end, 40, kFrame);
    // Unsigned arithmetic measures the real gap. Comparing the raw values
    // would see the counter leap backwards and abandon a healthy frame.
    for (std::uint32_t offset = 1; offset < kResyncStallMs; ++offset) {
        CHECK(!resync.update(near_the_end + offset, 40, kFrame));
    }
    CHECK(resync.update(near_the_end + kResyncStallMs, 40, kFrame));
}

TEST_CASE(the_stall_is_longer_than_a_frame_and_shorter_than_the_heartbeat) {
    // 64 bytes at 1 MHz is about half a millisecond, and the master sends at
    // least every 20 ms. A threshold outside that range would either abandon
    // frames that are simply crossing the wire or leave the link desynchronised
    // for longer than the watchdog allows.
    CHECK(kResyncStallMs > 1u);
    CHECK(kResyncStallMs < 20u);
}
