#pragma once

// Core 0's side of the queue: take commands, apply them, hold the result.
//
// This owns the only HidStateManager on U1. Core 1 never touches it - it only
// pushes into the queue - so there is exactly one writer and no locking
// anywhere in the path between a keypress and a USB report. The one thing the
// producer records is an atomic count of what the queue refused; everything
// derived from it, the fault included, is written here on Core 0.
//
// Draining is bounded on purpose. Core 0 has a millisecond of USB to service
// and a link to feed; letting a burst of input drain without limit would starve
// the very thing the input is for.
//
// A full queue is a fault that heals. It has to be: the fault suppresses every
// command, so a latch nothing clears is a board whose keyboard and mouse are
// dead on both computers until it is unplugged. The burst is what is wrong, not
// the device, and one pass with nothing refused is the end of the burst.

#include <atomic>
#include <cstddef>
#include <cstdint>

#include "hid/state_manager.hpp"
#include "hid/types.hpp"
#include "runtime/output_command.hpp"
#include "runtime/spsc_queue.hpp"

namespace duo_input::u1 {

/// How many commands one drain applies before returning to USB.
inline constexpr std::size_t kDefaultDrainBudget = 32;

class OutputRuntime {
public:
    /// Queue one command from Core 1. Returns false when the queue is full.
    ///
    /// Never blocks. A refusal is counted rather than acted on: this runs on
    /// Core 1, and the count is the only thing about the output state that the
    /// producer is allowed to write. drain() turns it into the fault.
    bool submit(const runtime::OutputCommand& command);

    /// Apply up to ``budget`` queued commands. Returns how many were applied.
    ///
    /// Also the only place the fault is raised and the only place it is
    /// cleared, both on Core 0. A pass that sees a new refusal releases
    /// everything and applies nothing; the pass after one that sees none
    /// resumes, because by then the queue was emptied and nothing is held.
    std::size_t drain(std::size_t budget = kDefaultDrainBudget);

    /// Apply one command immediately, without the queue.
    ///
    /// This is Core 0's own path - a button on the device, a reset - where
    /// there is no other core involved and no reason to defer.
    void process(const runtime::OutputCommand& command);

    /// What one computer is currently being told.
    hid::TargetSnapshot snapshot(hid::Target target) const;

    /// The report to send, consuming accumulated movement.
    hid::TargetSnapshot take_snapshot(hid::Target target);

    /// Let go of everything, everywhere.
    void release_all();

    /// What the last drain concluded about the queue. Core 0 only.
    runtime::RuntimeFault fault() const { return fault_; }

    /// How many commands the queue has refused since boot.
    std::uint32_t refused_commands() const {
        return refused_.load(std::memory_order_relaxed);
    }

    /// How many commands are waiting.
    std::size_t pending() const { return queue_.size(); }

private:
    void apply_to(hid::Target target, const runtime::OutputCommand& command);

    runtime::SpscQueue<runtime::OutputCommand, runtime::kOutputQueueCapacity> queue_;
    hid::HidStateManager outputs_;

    /// Written by the producer, read by the consumer. The only cross-core
    /// field here, and atomic because of it - which is what makes the
    /// single-writer claim above true rather than merely intended.
    std::atomic<std::uint32_t> refused_{0};

    /// What the previous drain had already accounted for. Core 0 only.
    std::uint32_t seen_refusals_ = 0;

    /// Core 0 only: raised and cleared inside drain(), read by diagnostics.
    runtime::RuntimeFault fault_ = runtime::RuntimeFault::None;
};

}  // namespace duo_input::u1
