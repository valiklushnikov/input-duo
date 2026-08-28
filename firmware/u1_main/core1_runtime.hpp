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

#include <cstddef>
#include <cstdint>

#include "input/events.hpp"
#include "macros/scheduler.hpp"
#include "mapping/binding.hpp"
#include "mapping/capture.hpp"
#include "mapping/engine.hpp"
#include "runtime/output_command.hpp"

namespace duo_input::u1 {

/// Where commands go. Core 0's queue on hardware, a recorder in tests.
class ICommandSink {
public:
    virtual ~ICommandSink() = default;
    /// Returns false when the queue is full. Never blocks.
    virtual bool submit(const runtime::OutputCommand& command) = 0;
};

/// Where a profile's bindings come from - stored configuration on hardware.
class IProfileSource {
public:
    virtual ~IProfileSource() = default;
    /// Writes up to ``kMaxBindings`` into ``out`` and returns how many.
    virtual std::size_t bindings_for(std::uint8_t profile, mapping::Binding* out) const = 0;
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
    bool capture_active() const { return capture_.active(); }
    /// Take the captured trigger to send as CAPTURE_EVENT, if one is waiting.
    bool take_capture_event(mapping::CapturedTrigger& out) { return capture_.take(out); }

    // --- profiles, across the two cores

    /// Ask for a profile. Core 0 calls this; nothing changes yet.
    ///
    /// Core 0 must not write the binding table while Core 1 is reading it, so
    /// it asks and waits to be told the swap happened.
    void request_profile(std::uint8_t profile);

    /// Report a completed swap, once.
    bool take_profile_ack(std::uint8_t& profile);

    std::uint8_t active_profile() const { return active_profile_; }

    /// Load a profile's bindings directly, outside the handshake. Startup.
    void set_profile_now(std::uint8_t profile);

    // --- macros

    void define_macro(std::uint8_t macro_id, const macros::MacroDefinition& definition);
    /// Run a macro wherever the keyboard currently points.
    bool run_macro(std::uint8_t macro_id, std::uint32_t now_ms);

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
    void request_release_all() { release_all_requested_ = true; }

    /// How many commands the queue refused. Nonzero means what is held on a
    /// computer no longer matches what the operator did.
    std::uint32_t dropped_commands() const { return dropped_; }

    mapping::BindingEngine& engine() { return engine_; }

private:
    void submit(const runtime::OutputCommand& command);
    void apply(const mapping::Outcome& outcome, std::uint32_t now_ms);
    void send_input(const input::InputEvent& event);
    void drain_macros(std::uint32_t now_ms);
    void swap_profile(std::uint8_t profile, std::uint32_t now_ms);

    runtime::Route keyboard_route() const;
    runtime::Route mouse_route() const;

    ICommandSink& sink_;
    IProfileSource& profiles_;

    mapping::CaptureController capture_;
    mapping::BindingEngine engine_;
    macros::MacroScheduler macros_;

    std::uint8_t active_profile_ = 0;
    /// A profile Core 0 has asked for and Core 1 has not applied yet.
    std::uint8_t requested_profile_ = 0;
    bool profile_requested_ = false;
    bool profile_acknowledged_ = false;
    std::uint8_t acknowledged_profile_ = 0;

    /// A release Core 0 has asked for and Core 1 has not performed yet.
    bool release_all_requested_ = false;

    /// Which mouse buttons are held. The report carries them all at once, so
    /// every change resends the whole mask.
    std::uint8_t buttons_ = 0;

    std::uint32_t dropped_ = 0;
};

}  // namespace duo_input::u1
