#pragma once

// Core 0's side of the queue: take commands, apply them, hold the result.
//
// This owns the only HidStateManager on U1. Core 1 never touches it - it only
// submits commands - so there is exactly one writer and no locking anywhere in
// the path between a keypress and a USB report.
//
// Draining is bounded on purpose. Core 0 has a millisecond of USB to service
// and a link to feed; letting a burst of input drain without limit would starve
// the very thing the input is for.

#include <cstddef>

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
    /// Never blocks. A full queue raises OutputQueueFull rather than making
    /// Core 1 wait for Core 0, which would be waiting for the host.
    bool submit(const runtime::OutputCommand& command);

    /// Apply up to ``budget`` queued commands. Returns how many were applied.
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

    runtime::RuntimeFault fault() const { return fault_; }
    void clear_fault() { fault_ = runtime::RuntimeFault::None; }

    /// How many commands are waiting.
    std::size_t pending() const { return queue_.size(); }

private:
    void apply_to(hid::Target target, const runtime::OutputCommand& command);

    runtime::SpscQueue<runtime::OutputCommand, runtime::kOutputQueueCapacity> queue_;
    hid::HidStateManager outputs_;
    runtime::RuntimeFault fault_ = runtime::RuntimeFault::None;
};

}  // namespace duo_input::u1
