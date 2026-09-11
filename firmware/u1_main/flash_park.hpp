#pragma once

#include <atomic>
#include <cstdint>

namespace duo_input::u1 {

/// Two-core ownership protocol for one flash operation.
///
/// Core 0 owns request()/release(); Core 1 owns begin_park()/finish_park().
/// The state itself is the acknowledgement in both directions, so neither
/// core can start a second operation while the other is still leaving the
/// first one's RAM-only window.
class FlashPark {
public:
    bool request() {
        if (state_.load(std::memory_order_relaxed) != State::Idle) {
            return false;
        }
        state_.store(State::Requested, std::memory_order_release);
        return true;
    }

    bool requested() const {
        return state_.load(std::memory_order_acquire) == State::Requested;
    }

    bool begin_park() {
        if (state_.load(std::memory_order_relaxed) != State::Requested) {
            return false;
        }
        state_.store(State::Parked, std::memory_order_release);
        return true;
    }

    bool parked() const {
        return state_.load(std::memory_order_acquire) == State::Parked;
    }

    bool release() {
        State expected = State::Parked;
        return state_.compare_exchange_strong(
            expected, State::ReleaseRequested, std::memory_order_release, std::memory_order_relaxed);
    }

    bool release_requested() const {
        return state_.load(std::memory_order_acquire) == State::ReleaseRequested;
    }

    void finish_park() { state_.store(State::Idle, std::memory_order_release); }

    bool idle() const { return state_.load(std::memory_order_acquire) == State::Idle; }

private:
    enum class State : std::uint8_t {
        Idle,
        Requested,
        Parked,
        ReleaseRequested,
    };

    std::atomic<State> state_{State::Idle};
};

/// Wrap-safe 1 kHz schedule for the RAM-only USB keepalive window.
///
/// At most one frame becomes due per observation. If Core 1 was delayed, the
/// missed slots are discarded instead of being emitted as a catch-up burst.
class FlashSofCadence {
public:
    FlashSofCadence() = default;

    bool due(std::uint32_t now_us) {
        // The first flash window may itself be shorter than one frame. Emit
        // immediately, then retain this deadline across later windows so a
        // train of sub-millisecond page programs cannot keep restarting the
        // clock and starve the bus indefinitely.
        if (!started_) {
            started_ = true;
            next_us_ = now_us + kPeriodUs;
            return true;
        }
        if (static_cast<std::int32_t>(now_us - next_us_) < 0) {
            return false;
        }
        next_us_ += kPeriodUs;
        if (static_cast<std::int32_t>(now_us - next_us_) >= 0) {
            next_us_ = now_us + kPeriodUs;
        }
        return true;
    }

private:
    static constexpr std::uint32_t kPeriodUs = 1000u;
    std::uint32_t next_us_ = 0;
    bool started_ = false;
};

}  // namespace duo_input::u1
