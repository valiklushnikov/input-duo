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

MacroStep tap_step(std::uint16_t usage) {
    MacroStep step;
    step.kind = MacroStepType::KEY_TAP;
    step.code = usage;
    return step;
}

struct RecordingSink final : ICommandSink {
    std::vector<OutputCommand> commands;
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

    std::size_t bindings_for(std::uint8_t profile, Binding* out) const override {
        const std::vector<Binding>& source = profile == 0 ? profile_zero : profile_one;
        for (std::size_t index = 0; index < source.size(); ++index) {
            out[index] = source[index];
        }
        return source.size();
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

    CHECK(!runtime.capture_active());
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
    CHECK(runtime.take_profile_ack(acknowledged));
    CHECK_EQ(static_cast<int>(acknowledged), 1);
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
