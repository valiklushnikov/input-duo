#pragma once

// Everything Core 1 does between a peripheral report and a queued command.
//
// The order is fixed and the reason for it is the whole design. Capture sees
// an event first, because while the configurator is asking "press the key you
// want to bind" that key belongs to the question and not to the computer.
// Then the bindings, which decide what the key means. Then the routes, which
// decide where the result goes. Only then does anything reach the queue.
//
// Nothing here blocks. Core 1 cannot wait for Core 0, which is waiting for the
// host; a queue that will not take a command produces a count, not a stall.
//
// The hardware is not here. This takes input events and produces commands, so
// the whole path from a keypress to a report can be exercised on a desktop -
// which matters, because the failure modes worth catching are the ones about
// what is held on a computer nobody is looking at.

#include <atomic>
#include <cstddef>
#include <cstdint>

#include "input/events.hpp"
#include "macros/scheduler.hpp"
#include "mapping/binding.hpp"
#include "mapping/capture.hpp"
#include "mapping/engine.hpp"
#include "runtime/output_command.hpp"

namespace duo_input::u1 {

/// The shortest gap between two things a macro emits.
///
/// One USB frame. A macro that types faster than the host polls is a macro
/// whose keystrokes are dropped by the endpoint rather than delivered, and a
/// human being does not type at a megahertz either.
inline constexpr std::uint32_t kMacroEventIntervalMs = 1;

/// Where commands go. Core 0's queue on hardware, a recorder in tests.
class ICommandSink {
public:
    virtual ~ICommandSink() = default;
    /// Returns false when the queue is full. Never blocks.
    virtual bool submit(const runtime::OutputCommand& command) = 0;

    /// How many submitted commands the consumer has not taken yet.
    ///
    /// Macro output is paced against this. Core 0 holds output *state*, not a
    /// queue of reports: a press and the release that follows it, applied in
    /// the same drain, leave the state exactly as it was and no report is sent
    /// at all - so a macro that outran the drain would type nothing. Waiting
    /// for the queue to empty puts each keystroke in its own drain, and
    /// therefore in its own report.
    ///
    /// Pure, and deliberately so. A default of nothing-pending would be right
    /// for a recorder and silently wrong for the one implementation that
    /// matters: a sink over the real queue that forgot to override it would
    /// pace macros against a constant and put the defect straight back.
    virtual std::size_t pending() const = 0;
};

/// Where a profile's bindings come from - stored configuration on hardware.
class IProfileSource {
public:
    virtual ~IProfileSource() = default;
    /// Writes up to ``kMaxBindings`` into ``out`` and returns how many.
    virtual std::size_t bindings_for(std::uint8_t profile, mapping::Binding* out) const = 0;

    /// The routes a profile starts in - spec section 11 stores both per
    /// profile, and a profile that becomes active starts in them.
    ///
    /// Returns false when the source has no such profile, which leaves the
    /// current routes alone. Pure rather than defaulted: a source that
    /// silently answered "no routes" would put back exactly the defect this
    /// exists to remove, and it would be invisible.
    virtual bool routes_for(std::uint8_t profile, config::KeyboardRoute& keyboard,
                            config::MouseRoute& mouse) const = 0;
};

class Core1Runtime {
public:
    Core1Runtime(ICommandSink& sink, IProfileSource& profiles);

    /// One event from a peripheral, already normalized.
    void handle_input(const input::InputEvent& event, std::uint32_t now_ms);

    /// Time passing: macro steps, capture timeouts, profile swaps.
    void tick(std::uint32_t now_ms);

    // --- capture, driven by the configurator over CDC

    void begin_capture(std::uint32_t now_ms);
    void cancel_capture();
    /// Post a capture command from Core 0. Core 1 applies it in tick().
    void request_capture_begin();
    void request_capture_cancel();

    /// Whether a capture is running, as Core 0 has to report it.
    ///
    /// Three states count as running, and the two extra ones are what stop
    /// Core 0 announcing the end of a capture it is about to be given the
    /// answer to. A begin Core 0 posted and this core has not reached yet is a
    /// capture that is going to run; a trigger sitting in the mailbox is a
    /// capture whose answer has not been sent, and the host is still waiting
    /// on it. Both would otherwise read as "no capture" for a pass, and
    /// ConfigService discards an answer arriving with no capture to answer.
    bool capture_active() const {
        if (capture_request_mailbox_.load(std::memory_order_acquire) ==
            kCaptureRequestBegin) {
            return true;
        }
        if ((capture_event_mailbox_.load(std::memory_order_acquire) &
             kCaptureMailboxOccupied) != 0) {
            return true;
        }
        return capture_active_published_.load(std::memory_order_acquire);
    }
    /// Take the captured trigger to send as CAPTURE_EVENT, if one is waiting.
    bool take_capture_event(mapping::CapturedTrigger& out);

    // --- profiles, across the two cores

    /// Ask for a profile. Core 0 calls this; nothing changes yet.
    ///
    /// Core 0 must not write the binding table while Core 1 is reading it, so
    /// it asks and waits to be told the swap happened.
    void request_profile(std::uint8_t profile);

    /// Report a completed swap, once.
    bool take_profile_ack(std::uint8_t& profile);
    bool take_profile_ack(std::uint8_t& profile, bool& requested_by_host);

    std::uint8_t active_profile() const {
        return active_profile_.load(std::memory_order_acquire);
    }

    /// Install a profile directly, outside the handshake. Startup.
    ///
    /// Its bindings and the routes it is stored as starting in. ``now_ms`` is
    /// only used to timestamp anything the route change has to release, and
    /// every caller has already let go of everything, so the boot path's zero
    /// costs nothing.
    void set_profile_now(std::uint8_t profile, std::uint32_t now_ms = 0);

    // --- macros

    void define_macro(std::uint8_t macro_id, const macros::MacroDefinition& definition);
    /// Run a macro wherever the keyboard currently points.
    bool run_macro(std::uint8_t macro_id, std::uint32_t now_ms);
    bool macro_active() const { return macros_.active(); }

    /// Let go of everything, everywhere, and stop every macro.
    ///
    /// Called on Core 1. Core 0 asks for it instead - see below.
    void release_all();

    /// Ask for that from the other core. Acted on at the next tick.
    ///
    /// Not release_all() directly: it submits a command, and the queue has
    /// exactly one producer. Two cores pushing into it lose a command, and the
    /// one they lose may be the release that stops a key repeating forever -
    /// which would make the emergency stop the thing that stranded the key.
    void request_release_all() {
        release_all_requested_.store(true, std::memory_order_release);
    }

    /// How many commands the queue refused. Nonzero means what is held on a
    /// computer no longer matches what the operator did.
    std::uint32_t dropped_commands() const {
        return dropped_.load(std::memory_order_relaxed);
    }

    mapping::BindingEngine& engine() { return engine_; }

private:
    /// 1 begin, 2 cancel, 0 nothing asked for.
    static constexpr std::uint8_t kCaptureRequestBegin = 1;
    static constexpr std::uint8_t kCaptureRequestCancel = 2;
    /// Bit 31 of the packed trigger says the mailbox holds one.
    static constexpr std::uint32_t kCaptureMailboxOccupied = 0x80000000u;

    void submit(const runtime::OutputCommand& command);
    void apply(const mapping::Outcome& outcome, std::uint32_t now_ms);
    void send_input(const input::InputEvent& event);
    void drain_macros(std::uint32_t now_ms);
    void swap_profile(std::uint8_t profile, std::uint32_t now_ms, bool requested_by_host);
    void request_profile_from_core1(std::uint8_t profile);
    void publish_capture_state();

    runtime::Route keyboard_route() const;
    runtime::Route mouse_route() const;

    ICommandSink& sink_;
    IProfileSource& profiles_;

    mapping::CaptureController capture_;
    mapping::BindingEngine engine_;
    macros::MacroScheduler macros_;

    std::atomic<std::uint8_t> active_profile_{0};

    /// Core 0 is the only writer, Core 1 the only reader. Bit 8 means valid.
    std::atomic<std::uint16_t> requested_profile_mailbox_{0};
    /// A binding/macro on Core 1 may also request a profile, without becoming
    /// a second writer to the cross-core mailbox.
    std::uint8_t local_requested_profile_ = 0;
    bool local_profile_requested_ = false;
    /// Core 1 is the only writer, Core 0 the only reader. Bit 8 means valid.
    std::atomic<std::uint16_t> profile_ack_mailbox_{0};

    /// 0 none, 1 begin, 2 cancel. Written by Core 0, consumed by Core 1.
    std::atomic<std::uint8_t> capture_request_mailbox_{0};
    /// Published after any captured event, so Core 0 cannot observe the end
    /// and discard the answer that caused it.
    std::atomic<bool> capture_active_published_{false};
    /// Packed trigger, with bit 31 as the occupied flag. Core 1 -> Core 0.
    std::atomic<std::uint32_t> capture_event_mailbox_{0};

    /// A release Core 0 has asked for and Core 1 has not performed yet.
    std::atomic<bool> release_all_requested_{false};

    /// The earliest millisecond at which the next macro event may be emitted.
    ///
    /// Compared by subtraction: the millisecond counter wraps after 49 days.
    std::uint32_t next_macro_event_ms_ = 0;

    /// Which mouse buttons are held. The report carries them all at once, so
    /// every change resends the whole mask.
    std::uint8_t buttons_ = 0;

    std::atomic<std::uint32_t> dropped_{0};
};

}  // namespace duo_input::u1
