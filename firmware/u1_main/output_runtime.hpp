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

#include "diagnostics/latency.hpp"
#include "hid/state_manager.hpp"
#include "hid/types.hpp"
#include "runtime/output_command.hpp"
#include "runtime/spsc_queue.hpp"

namespace duo_input::u1 {

/// How many commands one drain applies before returning to USB.
inline constexpr std::size_t kDefaultDrainBudget = 32;

/// How long a computer may leave the current keyboard state unacknowledged
/// before the drain stops waiting for it.
///
/// This is not a pacing figure - the ordinary wait is one pass, and usually
/// less. It is the answer to "what if a computer never answers again": a link
/// that has gone down, or a host that has stopped collecting the endpoint.
/// Waiting for that one forever would stop the *other* computer receiving
/// anything, which is a far worse fault than the one being prevented. Twenty
/// milliseconds is many times the longest honest publication - a USB frame,
/// an SPI frame and a pass round the loop - and short enough that a dead link
/// costs one pause and not one per keystroke, because a computer that misses
/// this deadline is set aside until it answers again.
inline constexpr std::uint32_t kPublishGraceMs = 20;

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
    ///
    /// And the only place the keyboard state is allowed to move on. It holds
    /// what it holds until both computers have been told - see
    /// ``may_change_keyboard`` - because this class keeps a state and not a
    /// queue of reports, so a state replaced before it was published is a
    /// letter nobody typed, or a release nobody made.
    /// ``now_us`` is the same instant as ``now_ms``, read from the microsecond
    /// clock, and is what every command applied in this pass is timed against.
    /// Milliseconds are too coarse for a budget of twenty of them.
    std::size_t drain(std::uint32_t now_ms, std::uint32_t now_us,
                      std::size_t budget = kDefaultDrainBudget);

    /// Apply one command immediately, without the queue.
    ///
    /// This is Core 0's own path - a button on the device, a reset - where
    /// there is no other core involved and no reason to defer.
    void process(const runtime::OutputCommand& command);

    /// What one computer is currently being told.
    hid::TargetSnapshot snapshot(hid::Target target) const;

    /// The report to send, consuming accumulated movement.
    hid::TargetSnapshot take_snapshot(hid::Target target);

    /// Record that ``target`` now knows the keyboard state held here.
    ///
    /// Said by whoever sends to that computer - UsbService::publish for PC1,
    /// SpiMaster::poll for PC2 - on the two occasions that make it true: a
    /// report that actually went out, and a state the far side already had.
    /// Until both have said it, the next keyboard command waits.
    void keyboard_reported(hid::Target target);

    /// Is ``target`` still owed the keyboard state now held?
    ///
    /// The condition the drain waits on, readable from outside: a computer
    /// that is behind is a computer whose keys are not what this holds.
    /// How many times the publish grace ran out and pacing was abandoned.
    ///
    /// Each one is a drain that then applied its whole budget at once, which
    /// is where a press can collapse against its own release and a letter
    /// never reach the computer at all.
    std::uint16_t keyboard_unpaced() const { return keyboard_unpaced_; }

    bool keyboard_unreported(hid::Target target) const {
        return outputs_.keyboard_unreported(target);
    }

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

    /// How long the device itself took, counted since boot.
    ///
    /// From the microsecond a peripheral report reached Core 1 to the
    /// microsecond Core 0 applied the command it produced. That is the whole
    /// of U1's own contribution and none of anybody else's: not the peripheral
    /// polling interval ahead of it, not USB or the SPI link behind it, and
    /// certainly not the far computer's compositor. A report quoting these as
    /// end-to-end latency would be quoting them as something they are not.
    const diagnostics::LatencyHistogram& keyboard_latency() const {
        return keyboard_latency_;
    }
    const diagnostics::LatencyHistogram& mouse_latency() const { return mouse_latency_; }

private:
    void apply_to(hid::Target target, const runtime::OutputCommand& command);

    /// Would this command change what a keyboard report says?
    static bool touches_keyboard(const runtime::OutputCommand& command);

    /// Count one applied command against the stream it came from.
    ///
    /// Only the commands a peripheral report caused, and only the kinds that
    /// belong to one stream or the other: a ReleaseAll is a safety action that
    /// touches both, so counting it as either would be arbitrary.
    void record_latency(const runtime::OutputCommand& command, std::uint32_t now_us);

    /// May the keyboard state move on yet?
    ///
    /// True once every computer has been told the state now held. False while
    /// one still owes an acknowledgement - and true again, for that computer
    /// alone, once it has owed one for longer than the grace: a computer that
    /// has stopped answering must not be able to stop the other one.
    bool may_change_keyboard(std::uint32_t now_ms);

    runtime::SpscQueue<runtime::OutputCommand, runtime::kOutputQueueCapacity> queue_;
    hid::HidStateManager outputs_;

    /// Core 0 only: written in the drain, copied out for the host to read.
    diagnostics::LatencyHistogram keyboard_latency_;
    diagnostics::LatencyHistogram mouse_latency_;

    /// Written by the producer, read by the consumer. The only cross-core
    /// field here, and atomic because of it - which is what makes the
    /// single-writer claim above true rather than merely intended.
    std::atomic<std::uint32_t> refused_{0};

    /// What the previous drain had already accounted for. Core 0 only.
    std::uint32_t seen_refusals_ = 0;

    /// Core 0 only: raised and cleared inside drain(), read by diagnostics.
    runtime::RuntimeFault fault_ = runtime::RuntimeFault::None;

    /// A computer that missed the grace and is no longer waited for. Cleared
    /// the moment it acknowledges anything again.
    bool stalled_[hid::kTargetCount] = {};

    /// When the current wait for an acknowledgement began. Meaningful only
    /// while ``waiting_`` is set.
    std::uint32_t waiting_since_ms_ = 0;
    bool waiting_ = false;
    std::uint16_t keyboard_unpaced_ = 0;
};

}  // namespace duo_input::u1
