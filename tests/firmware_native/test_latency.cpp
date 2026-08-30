#include "diagnostics/latency.hpp"

#include <cstdint>

#include "test_support.hpp"

using duo_input::diagnostics::kImplausibleLatencyUs;
using duo_input::diagnostics::kLatencyBucketCount;
using duo_input::diagnostics::kLatencyBucketEdgesUs;
using duo_input::diagnostics::LatencyHistogram;

namespace {

// The edges are on the wire and the two figures the specification names have
// to be answerable exactly, not by interpolation between buckets.
TEST_CASE(the_budget_and_the_stall_threshold_are_both_bucket_edges) {
    bool has_twenty_ms = false;
    bool has_fifty_ms = false;
    for (std::size_t index = 0; index + 1 < kLatencyBucketCount; ++index) {
        has_twenty_ms = has_twenty_ms || kLatencyBucketEdgesUs[index] == 20000;
        has_fifty_ms = has_fifty_ms || kLatencyBucketEdgesUs[index] == 50000;
    }
    CHECK(has_twenty_ms);
    CHECK(has_fifty_ms);
}

TEST_CASE(the_edges_ascend) {
    for (std::size_t index = 1; index + 1 < kLatencyBucketCount; ++index) {
        CHECK(kLatencyBucketEdgesUs[index] > kLatencyBucketEdgesUs[index - 1]);
    }
}

TEST_CASE(a_fresh_histogram_has_measured_nothing) {
    LatencyHistogram histogram;

    CHECK_EQ(histogram.count(), 0u);
    CHECK_EQ(histogram.max_us(), 0u);
    for (std::size_t index = 0; index < kLatencyBucketCount; ++index) {
        CHECK_EQ(histogram.bucket(index), 0u);
    }
}

TEST_CASE(a_sample_lands_in_the_first_bucket_whose_edge_it_does_not_exceed) {
    LatencyHistogram histogram;

    histogram.record(0);
    histogram.record(kLatencyBucketEdgesUs[0]);
    histogram.record(kLatencyBucketEdgesUs[0] + 1);

    CHECK_EQ(histogram.bucket(0), 2u);
    CHECK_EQ(histogram.bucket(1), 1u);
    CHECK_EQ(histogram.count(), 3u);
}

TEST_CASE(a_sample_past_the_last_edge_lands_in_the_overflow_bucket) {
    LatencyHistogram histogram;
    const std::uint32_t last_edge = kLatencyBucketEdgesUs[kLatencyBucketCount - 2];

    histogram.record(last_edge + 1);

    CHECK_EQ(histogram.bucket(kLatencyBucketCount - 1), 1u);
    CHECK_EQ(histogram.max_us(), last_edge + 1);
}

TEST_CASE(the_maximum_is_the_largest_sample_and_not_the_last_one) {
    LatencyHistogram histogram;

    histogram.record(9000);
    histogram.record(12);

    CHECK_EQ(histogram.max_us(), 9000u);
}

// The whole point of the bucket edges: "p95 <= 20 ms" is a question about how
// many samples were at or below 20 ms, and that has an exact answer as long as
// 20 ms is an edge. Asking about anything else does not, and must not be
// answered with a guess.
TEST_CASE(the_count_at_or_below_an_edge_is_exact) {
    LatencyHistogram histogram;
    for (int index = 0; index < 96; ++index) {
        histogram.record(1500);
    }
    for (int index = 0; index < 4; ++index) {
        histogram.record(30000);
    }

    std::uint32_t within = 0;
    CHECK(histogram.at_or_below(20000, within));
    CHECK_EQ(within, 96u);

    std::uint32_t over = 0;
    CHECK(histogram.at_or_below(50000, over));
    CHECK_EQ(over, 100u);
}

TEST_CASE(a_bound_that_is_not_a_bucket_edge_is_refused_rather_than_estimated) {
    LatencyHistogram histogram;
    histogram.record(1500);

    std::uint32_t answer = 12345;
    CHECK_FALSE(histogram.at_or_below(17500, answer));
    CHECK_EQ(answer, 12345u);
}

// The clock is read once per pass on Core 0 and the other core may queue a
// command immediately after that read. The unsigned difference then wraps to
// most of an hour, and a device whose watchdog fires after two seconds cannot
// have produced that. The command was queued a few instructions ago, so its
// latency is at the bottom of the scale - which is what gets recorded, rather
// than a fabricated stall that would dominate every maximum in the report.
TEST_CASE(a_difference_that_could_only_be_a_race_is_recorded_as_the_smallest) {
    LatencyHistogram histogram;

    histogram.record(kImplausibleLatencyUs + 1);
    histogram.record(0xFFFFFFFFu);

    CHECK_EQ(histogram.count(), 2u);
    CHECK_EQ(histogram.bucket(0), 2u);
    CHECK_EQ(histogram.max_us(), 0u);
}

TEST_CASE(a_stall_just_under_the_watchdog_is_still_a_real_measurement) {
    LatencyHistogram histogram;

    histogram.record(kImplausibleLatencyUs - 1);

    CHECK_EQ(histogram.bucket(kLatencyBucketCount - 1), 1u);
    CHECK_EQ(histogram.max_us(), kImplausibleLatencyUs - 1);
}

// A counter that wraps reports fewer events than happened, and every ratio
// derived from it is then wrong in the flattering direction.
TEST_CASE(a_saturated_counter_stops_rather_than_wrapping) {
    LatencyHistogram histogram;
    histogram.seed_for_test(0xFFFFFFFFu, 0xFFFFFFFFu);

    histogram.record(100);

    CHECK_EQ(histogram.count(), 0xFFFFFFFFu);
    CHECK_EQ(histogram.bucket(0), 0xFFFFFFFFu);
}

TEST_CASE(an_out_of_range_bucket_reads_as_nothing_rather_than_off_the_end) {
    LatencyHistogram histogram;
    histogram.record(100);

    CHECK_EQ(histogram.bucket(kLatencyBucketCount), 0u);
    CHECK_EQ(histogram.bucket(9999), 0u);
}

}  // namespace
