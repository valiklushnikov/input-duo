#pragma once

// How long the device took, counted into buckets rather than averaged.
//
// The specification bounds a percentile - "keyboard and mouse p95 <= 20 ms" -
// and a percentile cannot be recovered from a mean and a maximum. Keeping every
// sample is not an option either: a device with 264 KB of RAM and no filesystem
// cannot hold a day of timestamps, and streaming them out would change the very
// latency being measured.
//
// A histogram is what is left, and it answers the question the specification
// actually asks exactly rather than approximately. "p95 <= 20 ms" is true if and
// only if at least 95% of the samples were at or below 20 ms, so as long as
// 20 ms is a bucket edge, the answer is a count and not an estimate. The same
// holds for the 50 ms stall threshold, which is why that is an edge too.
//
// What a histogram cannot do is name the p95 to the microsecond. It brackets it
// between two edges, and every reader of this data must report the bracket. A
// single number invented from the middle of a bucket would be an estimate
// wearing a measurement's clothes, which is the one thing this whole acceptance
// path exists to prevent.

#include <cstddef>
#include <cstdint>

namespace duo_input::diagnostics {

/// How many buckets, including the overflow one at the top.
inline constexpr std::size_t kLatencyBucketCount = 9;

/// The inclusive upper bound of every bucket but the last, in microseconds.
///
/// A sample belongs to the first bucket whose edge it does not exceed;
/// everything past the final edge belongs to the overflow bucket. 20000 and
/// 50000 are here because the specification names 20 ms and 50 ms and both
/// answers have to be exact. The rest are spaced to make the fast end legible:
/// a device answering in 300 us and one answering in 4 ms both pass, and only
/// one of them is still passing after the next change.
inline constexpr std::uint32_t kLatencyBucketEdgesUs[kLatencyBucketCount - 1] = {
    250, 500, 1000, 2000, 5000, 10000, 20000, 50000,
};

/// A gap this long cannot be a latency, so it is a clock read that raced.
///
/// Core 0 reads the microsecond clock once, then drains the queue the other
/// core is writing. A command queued between the read and the drain has a
/// timestamp later than the read, and the unsigned difference wraps to about
/// thirty-five minutes. The watchdog restarts the board after two seconds, so
/// no genuine measurement can exceed this; anything that does is the race, and
/// the command in question was queued a few instructions ago.
inline constexpr std::uint32_t kImplausibleLatencyUs = 2000000;

/// Counted latencies for one stream of input.
///
/// Trivially copyable on purpose: Core 0 owns one of these per stream and hands
/// a copy to the CDC service each pass round its loop. Nothing here allocates,
/// blocks or reads a clock - the caller supplies the elapsed time, which is what
/// lets the arithmetic be tested on a desktop.
class LatencyHistogram {
public:
    /// Add one measurement.
    ///
    /// A difference above ``kImplausibleLatencyUs`` is recorded as zero rather
    /// than dropped: the event did happen, and its true latency is at the
    /// bottom of the scale. Dropping it would lose a real event; recording the
    /// wrapped figure would put a thirty-five minute stall in the maximum of
    /// every report the device ever produced.
    void record(std::uint32_t elapsed_us) {
        if (elapsed_us > kImplausibleLatencyUs) {
            elapsed_us = 0;
        }
        std::size_t index = kLatencyBucketCount - 1;
        for (std::size_t candidate = 0; candidate + 1 < kLatencyBucketCount; ++candidate) {
            if (elapsed_us <= kLatencyBucketEdgesUs[candidate]) {
                index = candidate;
                break;
            }
        }
        // Saturating rather than wrapping. A wrapped count reports fewer events
        // than happened, and every ratio taken from it - the p95 included - is
        // then wrong in the direction that flatters the device.
        if (buckets_[index] != 0xFFFFFFFFu) {
            ++buckets_[index];
        }
        if (count_ != 0xFFFFFFFFu) {
            ++count_;
        }
        if (elapsed_us > max_us_) {
            max_us_ = elapsed_us;
        }
    }

    std::uint32_t count() const { return count_; }
    std::uint32_t max_us() const { return max_us_; }

    /// One bucket's count. An index past the end reads as zero.
    std::uint32_t bucket(std::size_t index) const {
        return index < kLatencyBucketCount ? buckets_[index] : 0;
    }

    /// How many samples were at or below ``bound_us``.
    ///
    /// Answers only for a bucket edge, and says so by returning false for
    /// anything else. A bound inside a bucket has no exact answer here, and
    /// producing one would mean assuming a distribution the device never
    /// reported.
    bool at_or_below(std::uint32_t bound_us, std::uint32_t& out) const {
        std::uint32_t total = 0;
        for (std::size_t index = 0; index + 1 < kLatencyBucketCount; ++index) {
            total += buckets_[index];
            if (kLatencyBucketEdgesUs[index] == bound_us) {
                out = total;
                return true;
            }
        }
        return false;
    }

    void reset() { *this = LatencyHistogram{}; }

    /// Put the counters at an arbitrary value. Tests only.
    ///
    /// Saturation is otherwise unreachable: it takes 2^32 samples, which at any
    /// rate a USB peripheral can produce is longer than the part will live. An
    /// untested saturation branch is a wrap waiting to happen.
    void seed_for_test(std::uint32_t count, std::uint32_t first_bucket) {
        count_ = count;
        buckets_[0] = first_bucket;
    }

private:
    std::uint32_t buckets_[kLatencyBucketCount] = {};
    std::uint32_t count_ = 0;
    std::uint32_t max_us_ = 0;
};

}  // namespace duo_input::diagnostics
