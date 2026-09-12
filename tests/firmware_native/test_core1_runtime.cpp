// Core 1's whole job, from a keypress to a queued command.
//
// Two things here are worth more care than the rest.
//
// Capture: the configurator asks "press the key you want to bind", and that
// keypress must not reach the far computer. If it did, asking somebody to
// bind Ctrl+W would close their browser tab. So a captured press is swallowed
// - and so is its release, because a computer that never saw a key go down
// must not be told it came up.
//
// Profile swap: the bindings change underneath keys that are physically held.
// Whatever was down stays down under somebody's finger, but the computer it
// was pressed on has already been told to let go. The key is dead until it is
// released and pressed again, which is the only reading that neither strands
// a key nor invents one.

#include "core1_runtime.hpp"
#include "input/source_table.hpp"
#include "test_support.hpp"

#include <vector>

using duo_input::config::ActionKind;
using duo_input::config::BindingMode;
using duo_input::config::MacroStepType;
using duo_input::config::TriggerKind;
using duo_input::runtime::CommandKind;
using duo_input::runtime::kPhysicalOwner;
using duo_input::runtime::OutputCommand;
using duo_input::u1::Core1Runtime;
using duo_input::u1::ICommandSink;
using duo_input::u1::IProfileSource;
using duo_input::u1::input::InputEvent;
using duo_input::u1::input::InputEventKind;
using duo_input::u1::input::SourceEvent;
using duo_input::u1::input::SourceEventKind;
using duo_input::u1::input::SourceIdentity;
using duo_input::u1::input::SourceTable;
using duo_input::u1::macros::MacroDefinition;
using duo_input::u1::macros::MacroStep;
using duo_input::u1::mapping::Binding;
using duo_input::u1::mapping::CapturedTrigger;
using duo_input::u1::mapping::kCaptureTimeoutMs;

namespace {

InputEvent key(InputEventKind kind, std::uint16_t usage) {
    InputEvent event;
    event.kind = kind;
    event.code = usage;
    return event;
}

InputEvent button(InputEventKind kind, std::uint16_t index) {
    InputEvent event;
    event.kind = kind;
    event.code = index;
    return event;
}

InputEvent motion(std::int16_t x, std::int16_t y) {
    InputEvent event;
    event.kind = InputEventKind::MouseMove;
    event.x = x;
    event.y = y;
    return event;
}

/// Payload storage for the taps a test builds. Static, because a step points
/// at its payload rather than copying it - on the device that payload lives in
/// the stored configuration, which outlives every macro that reads it.
std::uint8_t g_pairs[64][2];
std::size_t g_next_pair = 0;

MacroStep tap_step(std::uint16_t usage) {
    std::uint8_t* slot = g_pairs[g_next_pair++];
    slot[0] = 0;
    slot[1] = static_cast<std::uint8_t>(usage);
    MacroStep step;
    step.kind = MacroStepType::KEY_TAP;
    step.pairs = slot;
    step.pair_bytes = 2;
    return step;
}

struct RecordingSink final : ICommandSink {
    std::vector<OutputCommand> commands;

    /// Taken the instant it is given, so there is never anything waiting.
    std::size_t pending() const override { return 0; }

    bool accept = true;

    bool submit(const OutputCommand& command) override {
        if (!accept) {
            return false;
        }
        commands.push_back(command);
        return true;
    }

    int count_of(CommandKind kind) const {
        int seen = 0;
        for (const OutputCommand& command : commands) {
            if (command.kind == kind) {
                ++seen;
            }
        }
        return seen;
    }

    int keys(CommandKind kind, std::uint8_t code) const {
        int seen = 0;
        for (const OutputCommand& command : commands) {
            if (command.kind == kind && command.code == code) {
                ++seen;
            }
        }
        return seen;
    }
};

/// Two profiles, so a swap has somewhere to go.
struct TwoProfiles final : IProfileSource {
    std::vector<Binding> profile_zero;
    std::vector<Binding> profile_one;

    /// Spec section 11: each profile stores the routes it starts in. Left at
    /// PC1/PC1 - what a fresh Routes holds - unless a test says otherwise, so
    /// nothing here moves a route without asking for it.
    duo_input::config::KeyboardRoute keyboard_zero = duo_input::config::KeyboardRoute::PC1;
    duo_input::config::MouseRoute mouse_zero = duo_input::config::MouseRoute::PC1;
    duo_input::config::KeyboardRoute keyboard_one = duo_input::config::KeyboardRoute::PC1;
    duo_input::config::MouseRoute mouse_one = duo_input::config::MouseRoute::PC1;
    /// Whether this source has the profile at all.
    bool has_routes = true;

    std::size_t bindings_for(std::uint8_t profile, Binding* out) const override {
        const std::vector<Binding>& source = profile == 0 ? profile_zero : profile_one;
        for (std::size_t index = 0; index < source.size(); ++index) {
            out[index] = source[index];
        }
        return source.size();
    }

    bool routes_for(std::uint8_t profile, duo_input::config::KeyboardRoute& keyboard,
                    duo_input::config::MouseRoute& mouse) const override {
        if (!has_routes) {
            return false;
        }
        keyboard = profile == 0 ? keyboard_zero : keyboard_one;
        mouse = profile == 0 ? mouse_zero : mouse_one;
        return true;
    }
};

/// A one-source table, wired into a runtime's CaptureController the way
/// main.cpp wires the real one in - the same fixture test_capture.cpp and
/// test_binding_engine.cpp use for the same reason: only what
/// SourceTable::resolve hands back matters here, not how a backend fills it
/// in.
struct SourceFixture : duo_input::u1::input::IInputHandler {
    SourceTable sources{*this};
    void on_input(const InputEvent&, std::uint32_t) override {}

    /// Occupies slot 0, since it is the first and only attach.
    void attach(std::uint16_t vid, std::uint16_t pid, std::uint8_t interface_number) {
        SourceEvent event;
        event.kind = SourceEventKind::Ready;
        event.source_id = 1;
        SourceIdentity identity;
        identity.vendor_id = vid;
        identity.product_id = pid;
        identity.interface_number = interface_number;
        sources.on_event(event, identity, 0);
    }
};

Binding run_macro_on(std::uint16_t usage, std::uint8_t macro_id) {
    Binding binding;
    binding.trigger = TriggerKind::KEYBOARD_USAGE;
    binding.code = usage;
    binding.mode = BindingMode::REPLACE;
    binding.action = ActionKind::RUN_MACRO;
    binding.parameter = macro_id;
    return binding;
}

}  // namespace

// ------------------------------------------------------------ pass-through

TEST_CASE(an_unbound_key_reaches_the_computer_the_route_points_at) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);

    runtime.handle_input(key(InputEventKind::KeyDown, 0x04), 1000);

    CHECK_EQ(sink.keys(CommandKind::KeyPress, 0x04), 1);
    CHECK_EQ(static_cast<int>(sink.commands[0].owner), static_cast<int>(kPhysicalOwner));
}

TEST_CASE(a_modifier_travels_as_a_mask_and_not_as_a_usage) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);

    runtime.handle_input(key(InputEventKind::KeyDown, 0xE1), 1000);  // left shift

    // The far side holds modifiers as a mask. Sending 0xE1 as a key usage
    // would put a meaningless key in the report and no shift at all.
    CHECK_EQ(sink.count_of(CommandKind::ModifiersPress), 1);
    CHECK_EQ(static_cast<int>(sink.commands[0].code), 0x02);
}

TEST_CASE(mouse_buttons_travel_as_the_whole_mask) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);

    runtime.handle_input(button(InputEventKind::MouseButtonDown, 0), 1000);
    runtime.handle_input(button(InputEventKind::MouseButtonDown, 2), 1000);

    // The report carries every button at once, so pressing a second one means
    // sending the first again. An index here would clear whatever else is down.
    CHECK_EQ(sink.count_of(CommandKind::MouseButtons), 2);
    CHECK_EQ(static_cast<int>(sink.commands[1].code), 0x05);
}

TEST_CASE(letting_go_of_one_button_leaves_the_others_held) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.handle_input(button(InputEventKind::MouseButtonDown, 0), 1000);
    runtime.handle_input(button(InputEventKind::MouseButtonDown, 2), 1000);

    runtime.handle_input(button(InputEventKind::MouseButtonUp, 0), 1000);

    CHECK_EQ(static_cast<int>(sink.commands[2].code), 0x04);
}

TEST_CASE(motion_becomes_a_delta) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);

    runtime.handle_input(motion(7, -3), 1000);

    CHECK_EQ(sink.count_of(CommandKind::MouseDelta), 1);
    CHECK_EQ(static_cast<int>(sink.commands[0].delta_x), 7);
    CHECK_EQ(static_cast<int>(sink.commands[0].delta_y), -3);
}

TEST_CASE(a_full_queue_is_recorded_rather_than_waited_on) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    sink.accept = false;

    runtime.handle_input(key(InputEventKind::KeyDown, 0x04), 1000);

    // Core 1 cannot wait for Core 0, which is waiting for the host. What it
    // can do is say that the output state no longer matches what happened.
    CHECK(runtime.dropped_commands() > 0);
}

// ---------------------------------------------------------------- capture

TEST_CASE(capture_is_not_running_until_it_is_asked_for) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);

    CHECK(!runtime.capture_active());
}

TEST_CASE(the_first_key_pressed_is_the_one_captured) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.begin_capture(1000);

    runtime.handle_input(key(InputEventKind::KeyDown, 0x1A), 1000);

    CapturedTrigger trigger;
    CHECK(runtime.take_capture_event(trigger));
    CHECK_EQ(static_cast<int>(trigger.kind), static_cast<int>(TriggerKind::KEYBOARD_USAGE));
    CHECK_EQ(static_cast<int>(trigger.code), 0x1A);
}

TEST_CASE(a_captured_trigger_s_source_crosses_from_core_1_to_core_0) {
    // CaptureController resolves the source on Core 1; take_capture_event is
    // what hands the trigger to Core 0 across the mailbox. A mailbox that
    // narrowed the copy back to kind/code/modifiers would pass every other
    // test in this file - none of them look at vendor_id - while silently
    // dropping the one thing this test exists to catch.
    SourceFixture fixture;
    fixture.attach(0x3434, 0xD030, 1);

    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.capture().set_sources(fixture.sources);
    runtime.begin_capture(1000);

    InputEvent press = key(InputEventKind::KeyDown, 0x1A);
    press.source_index = 0;  // the slot attach() just filled
    runtime.handle_input(press, 1000);

    CapturedTrigger trigger;
    CHECK(runtime.take_capture_event(trigger));
    CHECK_EQ(trigger.vendor_id, 0x3434u);
    CHECK_EQ(trigger.product_id, 0xD030u);
    CHECK_EQ(static_cast<int>(trigger.interface_number), 1);
}

TEST_CASE(the_captured_key_never_reaches_the_computer) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.begin_capture(1000);

    runtime.handle_input(key(InputEventKind::KeyDown, 0x1A), 1000);

    // Asking somebody to bind Ctrl+W must not close their browser tab.
    CHECK_EQ(sink.count_of(CommandKind::KeyPress), 0);
}

TEST_CASE(the_release_of_a_captured_key_is_swallowed_too) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.begin_capture(1000);
    runtime.handle_input(key(InputEventKind::KeyDown, 0x1A), 1000);

    runtime.handle_input(key(InputEventKind::KeyUp, 0x1A), 1100);

    // Capture ended on the press, but the key is still under a finger. A
    // computer that never saw it go down must not be told it came up.
    CHECK_EQ(sink.count_of(CommandKind::KeyRelease), 0);
}

TEST_CASE(capture_ends_at_the_first_press) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.begin_capture(1000);

    runtime.handle_input(key(InputEventKind::KeyDown, 0x1A), 1000);

    // The answer is still in the mailbox here, and until Core 0 has taken it
    // the capture is not over as far as the host is concerned - see the test
    // below. Once it is taken, nothing is running.
    CapturedTrigger trigger;
    CHECK(runtime.take_capture_event(trigger));
    CHECK(!runtime.capture_active());
}

TEST_CASE(a_capture_that_has_been_answered_is_running_until_the_answer_is_taken) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.begin_capture(1000);

    runtime.handle_input(key(InputEventKind::KeyDown, 0x1A), 1000);

    // Core 0 reports this to the host and gates the answer on it. If the
    // moment the trigger lands reads as "no capture", the pass that takes the
    // trigger has already been told there is nothing to answer, and it throws
    // the operator's keypress away.
    CHECK(runtime.capture_active());
}

TEST_CASE(a_second_key_after_capture_ends_goes_through_normally) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.begin_capture(1000);
    runtime.handle_input(key(InputEventKind::KeyDown, 0x1A), 1000);

    runtime.handle_input(key(InputEventKind::KeyDown, 0x04), 1100);

    CHECK_EQ(sink.keys(CommandKind::KeyPress, 0x04), 1);
}

TEST_CASE(the_mouse_still_moves_while_a_capture_waits) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.begin_capture(1000);

    runtime.handle_input(motion(5, 5), 1000);

    // The operator has to reach the configurator window to read what it is
    // asking them, and a frozen pointer while capture waits looks like a
    // crash.
    CHECK_EQ(sink.count_of(CommandKind::MouseDelta), 1);
    CHECK(runtime.capture_active());
}

TEST_CASE(a_modifier_is_recorded_against_the_key_and_does_not_end_capture) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.begin_capture(1000);

    runtime.handle_input(key(InputEventKind::KeyDown, 0xE0), 1000);  // left control
    CHECK(runtime.capture_active());
    runtime.handle_input(key(InputEventKind::KeyDown, 0x1A), 1010);

    // Capturing the control key the instant it goes down would make Ctrl+W
    // impossible to bind at all.
    CapturedTrigger trigger;
    CHECK(runtime.take_capture_event(trigger));
    CHECK_EQ(static_cast<int>(trigger.code), 0x1A);
    CHECK_EQ(static_cast<int>(trigger.modifiers), 0x01);
}

TEST_CASE(a_captured_mouse_button_counts_from_one) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.begin_capture(1000);

    runtime.handle_input(button(InputEventKind::MouseButtonDown, 3), 1000);

    // The host numbers buttons from one and rejects a zero. Internally they
    // count from zero, and the two must not be confused.
    CapturedTrigger trigger;
    CHECK(runtime.take_capture_event(trigger));
    CHECK_EQ(static_cast<int>(trigger.kind), static_cast<int>(TriggerKind::MOUSE_BUTTON));
    CHECK_EQ(static_cast<int>(trigger.code), 4);
}

TEST_CASE(a_captured_mouse_button_carries_no_modifiers) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.begin_capture(1000);
    runtime.handle_input(key(InputEventKind::KeyDown, 0xE0), 1000);

    runtime.handle_input(button(InputEventKind::MouseButtonDown, 0), 1010);

    // The host refuses a mouse trigger that carries modifiers, and a refused
    // payload is a capture the operator has to repeat for no reason.
    CapturedTrigger trigger;
    CHECK(runtime.take_capture_event(trigger));
    CHECK_EQ(static_cast<int>(trigger.modifiers), 0);
}

TEST_CASE(pending_capture_cannot_be_overwritten_by_rearm_and_cancel) {
    RecordingSink sink; TwoProfiles profiles; Core1Runtime runtime(sink, profiles);
    runtime.request_capture_begin(); runtime.tick(100);
    runtime.handle_input(key(InputEventKind::KeyDown, 0x04), 101);
    runtime.request_capture_begin(); runtime.tick(102);
    runtime.handle_input(key(InputEventKind::KeyDown, 0x05), 103);
    CapturedTrigger trigger;
    CHECK(runtime.take_capture_event(trigger)); CHECK(trigger.code == 0x04);
    CHECK(!runtime.take_capture_event(trigger));
    runtime.tick(104);
    runtime.handle_input(key(InputEventKind::KeyDown, 0x06), 105);
    runtime.request_capture_cancel(); runtime.tick(106);
    CHECK(runtime.take_capture_event(trigger)); CHECK(trigger.code == 0x06);
    CHECK(!runtime.take_capture_event(trigger)); CHECK(!runtime.capture_active());
}

TEST_CASE(a_capture_nobody_answers_gives_up_after_ten_seconds) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.begin_capture(1000);

    runtime.tick(1000 + kCaptureTimeoutMs);

    // Otherwise a configurator that crashed mid-capture leaves the keyboard
    // permanently swallowing its own input, with nothing on screen to say so.
    CHECK(!runtime.capture_active());
    CapturedTrigger trigger;
    CHECK(!runtime.take_capture_event(trigger));
}

TEST_CASE(a_key_pressed_a_moment_before_the_timeout_is_still_captured) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.begin_capture(1000);

    runtime.tick(1000 + kCaptureTimeoutMs - 1);
    runtime.handle_input(key(InputEventKind::KeyDown, 0x1A), 1000 + kCaptureTimeoutMs - 1);

    CapturedTrigger trigger;
    CHECK(runtime.take_capture_event(trigger));
}

TEST_CASE(a_captured_key_does_not_run_the_binding_it_is_bound_to) {
    RecordingSink sink;
    TwoProfiles profiles;
    profiles.profile_zero.push_back(run_macro_on(0x1A, 0));
    const MacroStep steps[] = {tap_step(0x07)};
    Core1Runtime runtime(sink, profiles);
    runtime.define_macro(0, MacroDefinition{steps, 1});
    runtime.set_profile_now(0);
    runtime.begin_capture(1000);

    runtime.handle_input(key(InputEventKind::KeyDown, 0x1A), 1000);
    runtime.tick(1000);

    // The operator is telling the configurator which key to rebind. Running
    // its current binding at the same time acts on the old meaning of a key
    // they are in the middle of giving a new one.
    CHECK_EQ(sink.keys(CommandKind::KeyPress, 0x07), 0);
}

TEST_CASE(a_cancelled_capture_stops_swallowing) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.begin_capture(1000);

    runtime.cancel_capture();
    runtime.handle_input(key(InputEventKind::KeyDown, 0x1A), 1000);

    CHECK_EQ(sink.keys(CommandKind::KeyPress, 0x1A), 1);
}

TEST_CASE(a_capture_request_crosses_to_core_one_before_it_swallows_anything) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);

    runtime.request_capture_begin();

    // Posted, not performed. Core 0 must not start a capture inside a USB
    // callback while this core is halfway through an event, so the key that
    // arrives before the tick still belongs to the computer.
    runtime.handle_input(key(InputEventKind::KeyDown, 0x1A), 1000);
    CHECK_EQ(sink.keys(CommandKind::KeyPress, 0x1A), 1);

    runtime.tick(1000);

    runtime.handle_input(key(InputEventKind::KeyDown, 0x04), 1010);
    CHECK_EQ(sink.keys(CommandKind::KeyPress, 0x04), 0);
}

TEST_CASE(a_capture_asked_for_and_not_yet_started_already_counts_as_running) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);

    runtime.request_capture_begin();

    // Between the request and the tick there would otherwise be a pass in
    // which nothing anywhere says a capture is coming. Core 0 publishes what
    // this answers, and publishing "no capture" there is what makes the
    // operator's answer arrive with nothing to answer.
    CHECK(runtime.capture_active());
}

TEST_CASE(a_captured_trigger_is_published_before_inactive_state) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.request_capture_begin();
    runtime.tick(1000);

    runtime.handle_input(key(InputEventKind::KeyDown, 0x1A), 1010);

    CapturedTrigger trigger;
    CHECK(runtime.take_capture_event(trigger));
    CHECK_EQ(static_cast<int>(trigger.code), 0x1A);
    CHECK(!runtime.capture_active());
}

TEST_CASE(a_capture_cancel_request_is_applied_only_by_core_one) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.begin_capture(1000);

    runtime.request_capture_cancel();

    CHECK(runtime.capture_active());
    runtime.tick(1010);
    CHECK(!runtime.capture_active());
}

// ----------------------------------------------------------- profile swap

TEST_CASE(a_profile_request_is_not_applied_until_core_one_sees_it) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);

    runtime.request_profile(1);

    // Core 0 must not reach into the binding table while Core 1 is reading
    // it. It asks, and waits to be told.
    CHECK_EQ(static_cast<int>(runtime.active_profile()), 0);
}

TEST_CASE(the_swap_happens_on_the_next_tick_and_is_acknowledged) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.request_profile(1);

    runtime.tick(1000);

    CHECK_EQ(static_cast<int>(runtime.active_profile()), 1);
    std::uint8_t acknowledged = 0;
    bool requested_by_host = false;
    CHECK(runtime.take_profile_ack(acknowledged, requested_by_host));
    CHECK_EQ(static_cast<int>(acknowledged), 1);
    CHECK(requested_by_host);
}

TEST_CASE(a_profile_selected_by_a_macro_is_acknowledged_as_local) {
    RecordingSink sink;
    TwoProfiles profiles;
    MacroStep select;
    select.kind = MacroStepType::SET_PROFILE;
    select.code = 1;
    const MacroStep steps[] = {select};
    Core1Runtime runtime(sink, profiles);
    runtime.define_macro(0, MacroDefinition{steps, 1});
    runtime.run_macro(0, 1000);

    runtime.tick(1000);  // macro publishes the local request
    runtime.tick(1001);  // Core 1 applies it

    std::uint8_t acknowledged = 0;
    bool requested_by_host = true;
    CHECK(runtime.take_profile_ack(acknowledged, requested_by_host));
    CHECK_EQ(static_cast<int>(acknowledged), 1);
    CHECK_FALSE(requested_by_host);
}

TEST_CASE(an_acknowledgement_is_reported_once) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.request_profile(1);
    runtime.tick(1000);
    std::uint8_t acknowledged = 0;
    runtime.take_profile_ack(acknowledged);

    CHECK(!runtime.take_profile_ack(acknowledged));
}

TEST_CASE(a_profile_starts_in_the_routes_it_was_stored_with) {
    RecordingSink sink;
    TwoProfiles profiles;
    profiles.keyboard_one = duo_input::config::KeyboardRoute::PC2;
    profiles.mouse_one = duo_input::config::MouseRoute::PC2;
    Core1Runtime runtime(sink, profiles);
    runtime.request_profile(1);

    runtime.tick(1000);

    // Spec section 11 stores a starting KeyboardRoute and a starting
    // MouseRoute in every profile. A profile that becomes active and leaves
    // the routes wherever the last one happened to put them is not the profile
    // the operator configured - and there is no other way to reach those two
    // fields, so a hand-edited project would carry them and nothing would ever
    // read them.
    sink.commands.clear();
    runtime.handle_input(key(InputEventKind::KeyDown, 0x04), 1001);
    runtime.handle_input(motion(3, 0), 1001);

    CHECK_EQ(sink.count_of(CommandKind::KeyPress), 1);
    CHECK_EQ(sink.count_of(CommandKind::MouseDelta), 1);
    if (sink.commands.size() < 2) {
        return;
    }
    CHECK_EQ(static_cast<int>(sink.commands[0].route),
             static_cast<int>(duo_input::runtime::Route::Pc2));
    CHECK_EQ(static_cast<int>(sink.commands[1].route),
             static_cast<int>(duo_input::runtime::Route::Pc2));
}

TEST_CASE(the_profile_loaded_at_startup_starts_in_its_routes_too) {
    RecordingSink sink;
    TwoProfiles profiles;
    profiles.keyboard_one = duo_input::config::KeyboardRoute::BOTH;
    Core1Runtime runtime(sink, profiles);

    // The boot path: main reads the configuration's own active profile and
    // installs it directly, outside the handshake. A device that powers up in
    // profile 3 has to power up in profile 3's routes as well, which is the
    // case the acceptance exercised across a power cycle.
    runtime.set_profile_now(1);

    sink.commands.clear();
    runtime.handle_input(key(InputEventKind::KeyDown, 0x04), 1001);

    CHECK_EQ(sink.count_of(CommandKind::KeyPress), 1);
    if (sink.commands.empty()) {
        return;
    }
    CHECK_EQ(static_cast<int>(sink.commands[0].route),
             static_cast<int>(duo_input::runtime::Route::Both));
}

TEST_CASE(a_profile_with_parted_routes_is_brought_together_under_synchronised_control) {
    RecordingSink sink;
    TwoProfiles profiles;
    // Stored apart: the configurator does not forbid it, so the device is what
    // settles it - and it settles it before either route is applied.
    profiles.keyboard_one = duo_input::config::KeyboardRoute::PC2;
    profiles.mouse_one = duo_input::config::MouseRoute::PC1;
    Core1Runtime runtime(sink, profiles);
    runtime.set_synchronised_control(true);

    runtime.set_profile_now(1);

    CHECK(runtime.engine().keyboard_route() == duo_input::config::KeyboardRoute::PC2);
    CHECK(runtime.engine().mouse_route() == duo_input::config::MouseRoute::PC2);
}

TEST_CASE(a_profile_on_both_keeps_its_own_mouse_route_under_synchronised_control) {
    RecordingSink sink;
    TwoProfiles profiles;
    profiles.keyboard_one = duo_input::config::KeyboardRoute::BOTH;
    profiles.mouse_one = duo_input::config::MouseRoute::PC2;
    Core1Runtime runtime(sink, profiles);
    runtime.set_synchronised_control(true);

    runtime.set_profile_now(1);

    CHECK(runtime.engine().keyboard_route() == duo_input::config::KeyboardRoute::BOTH);
    CHECK(runtime.engine().mouse_route() == duo_input::config::MouseRoute::PC2);
}

TEST_CASE(a_profile_keeps_parted_routes_when_synchronised_control_is_off) {
    RecordingSink sink;
    TwoProfiles profiles;
    profiles.keyboard_one = duo_input::config::KeyboardRoute::PC2;
    profiles.mouse_one = duo_input::config::MouseRoute::PC1;
    Core1Runtime runtime(sink, profiles);

    runtime.set_profile_now(1);

    CHECK(runtime.engine().keyboard_route() == duo_input::config::KeyboardRoute::PC2);
    CHECK(runtime.engine().mouse_route() == duo_input::config::MouseRoute::PC1);
}

TEST_CASE(a_source_that_has_no_such_profile_leaves_the_routes_where_they_are) {
    RecordingSink sink;
    TwoProfiles profiles;
    profiles.keyboard_one = duo_input::config::KeyboardRoute::PC2;
    profiles.has_routes = false;
    Core1Runtime runtime(sink, profiles);

    runtime.request_profile(1);
    runtime.tick(1000);

    // Nothing stored means nothing to apply. Moving to a made-up route here
    // would send the operator's keys to a computer no configuration named.
    sink.commands.clear();
    runtime.handle_input(key(InputEventKind::KeyDown, 0x04), 1001);
    CHECK_EQ(sink.count_of(CommandKind::KeyPress), 1);
    if (sink.commands.empty()) {
        return;
    }
    CHECK_EQ(static_cast<int>(sink.commands[0].route),
             static_cast<int>(duo_input::runtime::Route::Pc1));
}

TEST_CASE(swapping_profiles_releases_both_computers) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.handle_input(key(InputEventKind::KeyDown, 0x04), 1000);
    runtime.request_profile(1);

    runtime.tick(1000);

    // Whatever was held was held under the old profile's routing, and the
    // computer it was sent to will never hear about it again.
    CHECK(sink.count_of(CommandKind::ReleaseRoute) > 0);
}

TEST_CASE(a_key_held_across_a_swap_is_not_sent_again) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.handle_input(key(InputEventKind::KeyDown, 0x04), 1000);
    runtime.request_profile(1);
    runtime.tick(1000);
    const int before = sink.keys(CommandKind::KeyPress, 0x04);

    runtime.handle_input(key(InputEventKind::KeyUp, 0x04), 1100);

    // The key is still under a finger, but the new profile never saw it go
    // down. It is dead until it is released and pressed again.
    CHECK_EQ(sink.keys(CommandKind::KeyPress, 0x04), before);
    CHECK_EQ(sink.keys(CommandKind::KeyRelease, 0x04), 0);
}

TEST_CASE(the_same_key_pressed_again_after_a_swap_works) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.handle_input(key(InputEventKind::KeyDown, 0x04), 1000);
    runtime.request_profile(1);
    runtime.tick(1000);
    runtime.handle_input(key(InputEventKind::KeyUp, 0x04), 1100);

    runtime.handle_input(key(InputEventKind::KeyDown, 0x04), 1200);

    CHECK_EQ(sink.keys(CommandKind::KeyPress, 0x04), 2);
}

TEST_CASE(the_new_profiles_bindings_are_the_ones_that_apply) {
    RecordingSink sink;
    TwoProfiles profiles;
    profiles.profile_one.push_back(run_macro_on(0x04, 0));
    const MacroStep steps[] = {tap_step(0x09)};
    Core1Runtime runtime(sink, profiles);
    runtime.define_macro(0, MacroDefinition{steps, 1});
    runtime.request_profile(1);
    runtime.tick(1000);

    runtime.handle_input(key(InputEventKind::KeyDown, 0x04), 1100);
    runtime.tick(1100);

    // Unbound under profile zero, bound under profile one.
    CHECK_EQ(sink.keys(CommandKind::KeyPress, 0x09), 1);
    CHECK_EQ(sink.keys(CommandKind::KeyPress, 0x04), 0);
}

TEST_CASE(a_running_macro_is_stopped_by_a_profile_swap) {
    RecordingSink sink;
    TwoProfiles profiles;
    MacroStep down;
    down.kind = MacroStepType::KEY_DOWN;
    down.code = 0x09;
    MacroStep wait;
    wait.kind = MacroStepType::DELAY;
    wait.delay_ms = 5000;
    const MacroStep steps[] = {down, wait};
    Core1Runtime runtime(sink, profiles);
    runtime.define_macro(0, MacroDefinition{steps, 2});
    runtime.run_macro(0, 1000);
    runtime.tick(1000);

    runtime.request_profile(1);
    runtime.tick(1010);

    // A macro still typing after the swap sends its remaining keystrokes to
    // whichever computer the new profile happens to point at.
    CHECK_EQ(sink.keys(CommandKind::KeyRelease, 0x09), 1);
}

// ----------------------------------------------------------------- macros

TEST_CASE(matching_macro_and_route_bindings_use_the_route_at_the_macros_position) {
    using duo_input::config::KeyboardRoute;
    using duo_input::runtime::Route;
    struct Scenario {
        bool route_first;
        Route macro_route;
    };
    for (const Scenario scenario : {Scenario{false, Route::Pc1}, Scenario{true, Route::Pc2}}) {
        RecordingSink sink;
        TwoProfiles profiles;
        const Binding macro = run_macro_on(0x3D, 3);
        Binding move = macro;
        move.source = {0x1234, 0x5678, 0};
        move.action = ActionKind::SET_KEYBOARD_ROUTE;
        move.parameter = static_cast<std::uint8_t>(KeyboardRoute::PC2);
        profiles.profile_zero = scenario.route_first ? std::vector<Binding>{move, macro}
                                                     : std::vector<Binding>{macro, move};
        Core1Runtime runtime(sink, profiles);
        SourceFixture sources; sources.attach(0x1234, 0x5678, 0);
        runtime.engine().set_sources(sources.sources);
        MacroStep steps[] = {tap_step(0x05)};
        runtime.define_macro(3, MacroDefinition{steps, 1});
        runtime.handle_input(key(InputEventKind::KeyDown, 0x06), 999);
        sink.commands.clear();

        runtime.handle_input(key(InputEventKind::KeyDown, 0x3D), 1000);
        CHECK(runtime.engine().keyboard_route() == KeyboardRoute::PC2);
        CHECK_EQ(sink.commands.size(), 1u);
        CHECK(sink.commands[0].kind == CommandKind::ReleaseRoute);
        CHECK(sink.commands[0].route == Route::Pc1);
        // The old physical input remains orphaned across either binding order.
        runtime.handle_input(key(InputEventKind::KeyUp, 0x06), 1000);
        CHECK_EQ(sink.commands.size(), 1u);

        runtime.tick(1000);
        runtime.tick(1001);
        CHECK_EQ(sink.keys(CommandKind::KeyPress, 0x05), 1);
        CHECK_EQ(sink.keys(CommandKind::KeyRelease, 0x05), 1);
        for (const OutputCommand& command : sink.commands) {
            if (command.code == 0x05 &&
                (command.kind == CommandKind::KeyPress || command.kind == CommandKind::KeyRelease)) {
                CHECK_EQ(command.owner, 3);
                CHECK(command.route == scenario.macro_route);
            }
        }
    }
}

TEST_CASE(a_macros_keystrokes_are_owned_by_the_macro) {
    RecordingSink sink;
    TwoProfiles profiles;
    const MacroStep steps[] = {tap_step(0x09)};
    Core1Runtime runtime(sink, profiles);
    runtime.define_macro(3, MacroDefinition{steps, 1});

    runtime.run_macro(3, 1000);
    runtime.tick(1000);

    // Core 0 keeps macro keys apart from the operator's, so that letting go of
    // one does not let go of the other.
    CHECK_EQ(sink.keys(CommandKind::KeyPress, 0x09), 1);
    CHECK_EQ(static_cast<int>(sink.commands[0].owner), 3);
}

TEST_CASE(a_macro_delay_does_not_stop_the_operators_own_typing) {
    RecordingSink sink;
    TwoProfiles profiles;
    MacroStep wait;
    wait.kind = MacroStepType::DELAY;
    wait.delay_ms = 5000;
    const MacroStep steps[] = {wait};
    Core1Runtime runtime(sink, profiles);
    runtime.define_macro(0, MacroDefinition{steps, 1});
    runtime.run_macro(0, 1000);
    runtime.tick(1000);

    runtime.handle_input(key(InputEventKind::KeyDown, 0x04), 1010);

    CHECK_EQ(sink.keys(CommandKind::KeyPress, 0x04), 1);
}

TEST_CASE(a_macro_emits_one_event_per_pass_however_often_core_one_goes_round) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);

    // One hundred letters owe two hundred output commands. Core 1 goes round
    // many times for each pass Core 0 makes, so a per-pass count of anything
    // above one measures the wrong loop: Core 0 holds output state and not a
    // queue of reports, and a press and its release inside one drain leave
    // that state unchanged and produce no report at all.
    std::uint8_t pairs[200] = {};
    for (std::size_t index = 0; index < sizeof(pairs); index += 2) {
        pairs[index + 1] = 0x04;
    }
    MacroStep text;
    text.kind = MacroStepType::TEXT;
    text.pairs = pairs;
    text.pair_bytes = sizeof(pairs);
    const MacroStep steps[] = {text};
    runtime.define_macro(0, MacroDefinition{steps, 1});
    CHECK(runtime.run_macro(0, 1000));

    for (int index = 0; index < 20; ++index) {
        runtime.tick(1000);
    }

    CHECK_EQ(sink.commands.size(), 1u);
    CHECK(runtime.macro_active());

    // And the millisecond after, one more - not twenty.
    for (int index = 0; index < 20; ++index) {
        runtime.tick(1001);
    }
    CHECK_EQ(sink.commands.size(), 2u);
}

TEST_CASE(a_macro_waits_for_the_other_core_to_take_what_it_already_sent) {
    // A sink that keeps everything, so nothing is ever consumed. The macro has
    // to stop after one event: a second one queued behind the first would be
    // applied in the same drain, and the far computer would see neither.
    struct HoldingSink final : ICommandSink {
        std::size_t held = 0;
        bool submit(const OutputCommand& command) override {
            (void)command;
            ++held;
            return true;
        }
        std::size_t pending() const override { return held; }
    };

    HoldingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    std::uint8_t pairs[8] = {0, 0x04, 0, 0x05, 0, 0x06, 0, 0x07};
    MacroStep text;
    text.kind = MacroStepType::TEXT;
    text.pairs = pairs;
    text.pair_bytes = sizeof(pairs);
    const MacroStep steps[] = {text};
    runtime.define_macro(0, MacroDefinition{steps, 1});
    CHECK(runtime.run_macro(0, 1000));

    for (std::uint32_t now = 1000; now < 1050; ++now) {
        runtime.tick(now);
    }

    CHECK_EQ(sink.held, 1u);

    // Taken, and the macro carries on.
    sink.held = 0;
    runtime.tick(1050);
    CHECK_EQ(sink.held, 1u);
}

TEST_CASE(the_last_protocol_macro_slot_is_reachable) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    const MacroStep steps[] = {tap_step(0x09)};

    runtime.define_macro(31, MacroDefinition{steps, 1});

    CHECK(runtime.run_macro(31, 1000));
    runtime.tick(1000);
    CHECK_EQ(sink.keys(CommandKind::KeyPress, 0x09), 1);
}


// --------------------------------------------------- releasing from Core 0

TEST_CASE(a_release_asked_for_by_the_other_core_waits_for_the_tick) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);

    runtime.request_release_all();

    // Nothing yet. The command queue has exactly one producer, and the core
    // asking is not it: two cores pushing into it lose a command, and the one
    // they lose may be the release that stops a key repeating forever.
    CHECK_EQ(sink.count_of(CommandKind::ReleaseAll), 0);
}

TEST_CASE(a_release_asked_for_by_the_other_core_happens_on_the_next_tick) {
    RecordingSink sink;
    TwoProfiles profiles;
    const MacroStep steps[] = {tap_step(0x09)};
    Core1Runtime runtime(sink, profiles);
    runtime.define_macro(2, MacroDefinition{steps, 1});
    runtime.run_macro(2, 1000);
    runtime.handle_input(key(InputEventKind::KeyDown, 0x04), 1000);
    sink.commands.clear();

    runtime.request_release_all();
    runtime.tick(1001);

    CHECK_EQ(sink.count_of(CommandKind::ReleaseAll), 1);
    // And the macro is stopped rather than left to carry on typing into a
    // computer that was just told to let go of everything.
    runtime.tick(1002);
    CHECK_EQ(sink.keys(CommandKind::KeyPress, 0x09), 0);
}

TEST_CASE(a_release_all_leaves_the_scheduler_owing_nothing) {
    RecordingSink sink;
    TwoProfiles profiles;
    MacroStep down;
    down.kind = MacroStepType::KEY_DOWN;
    down.code = 0x09;
    MacroStep wait;
    wait.kind = MacroStepType::DELAY;
    wait.delay_ms = 5000;
    const MacroStep steps[] = {down, wait};
    Core1Runtime runtime(sink, profiles);
    runtime.define_macro(0, MacroDefinition{steps, 2});
    runtime.run_macro(0, 1000);
    runtime.tick(1000);
    CHECK_EQ(sink.keys(CommandKind::KeyPress, 0x09), 1);

    sink.commands.clear();
    runtime.request_release_all();
    runtime.tick(1001);

    // ReleaseAll already let go of everything on both computers. A release the
    // scheduler still owed would be handed out afterwards - one event to a
    // pass, over the following milliseconds, after the step pool it came from
    // may have been rewritten - and would say a key came up that nothing is
    // holding.
    CHECK_EQ(sink.commands.size(), 1u);
    CHECK_EQ(sink.count_of(CommandKind::ReleaseAll), 1);

    for (std::uint32_t now = 1002; now < 1030; ++now) {
        runtime.tick(now);
    }
    CHECK_EQ(sink.commands.size(), 1u);
    CHECK_FALSE(runtime.macro_active());
}

TEST_CASE(a_release_is_asked_for_once_and_not_repeated_every_tick) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);

    runtime.request_release_all();
    runtime.tick(1000);
    runtime.tick(1001);
    runtime.tick(1002);

    CHECK_EQ(sink.count_of(CommandKind::ReleaseAll), 1);
}

// ------------------------------------------------------- timing the input
//
// The device cannot see a finger or a far screen, so it cannot measure the
// journey between them. What it can measure is its own part: the microsecond
// the controller handed over a report, carried on every command that report
// produced, so Core 0 can subtract it from the microsecond it applied the
// command. The stamp is set by Core 1's loop from the event it is about to
// feed in, and cleared afterwards, so that only what a peripheral caused
// carries one.

TEST_CASE(a_command_carries_the_stamp_of_the_report_that_caused_it) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.set_profile_now(0);

    runtime.set_event_origin_us(4321);
    runtime.handle_input(key(InputEventKind::KeyDown, 0x04), 10);

    CHECK_EQ(sink.commands.size(), 1u);
    CHECK_EQ(sink.commands[0].origin_us, 4321u);
}

TEST_CASE(a_macro_step_emitted_on_a_schedule_carries_no_stamp) {
    RecordingSink sink;
    TwoProfiles profiles;
    MacroStep steps[1] = {tap_step(0x05)};
    profiles.profile_zero.push_back(run_macro_on(0x04, 1));

    Core1Runtime runtime(sink, profiles);
    runtime.set_profile_now(0);
    runtime.define_macro(1, MacroDefinition{steps, 1});

    // The binding replaces the key, so the press itself reaches nothing; every
    // command below is the scheduler's own output, emitted after the stamp was
    // cleared. The delay in those is one the macro asked for, and counting it
    // as latency would let a slow macro make the input path look slow.
    runtime.set_event_origin_us(900);
    runtime.handle_input(key(InputEventKind::KeyDown, 0x04), 10);
    runtime.set_event_origin_us(0);
    for (std::uint32_t now = 11; now < 40; ++now) {
        runtime.tick(now);
    }

    CHECK(sink.commands.size() > 0u);
    for (const OutputCommand& command : sink.commands) {
        CHECK_EQ(command.origin_us, 0u);
    }
}

// A stamp left standing would be attached to whatever the device did next, and
// the further from the report that happened, the worse the reading. Clearing
// it is the loop's job; this proves the runtime honours the clearing.
TEST_CASE(a_cleared_stamp_stays_cleared) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    runtime.set_profile_now(0);

    runtime.set_event_origin_us(4321);
    runtime.set_event_origin_us(0);
    runtime.handle_input(key(InputEventKind::KeyDown, 0x04), 10);

    CHECK_EQ(sink.commands.size(), 1u);
    CHECK_EQ(sink.commands[0].origin_us, 0u);
}
