// Running a macro without stopping anything else.
//
// A macro is a little program that types. The hard requirement is that it must
// never hold the loop: a macro with a two-second pause in it cannot mean two
// seconds where the operator's own keyboard does nothing, because the person
// is still typing and their keystrokes are the ones that matter. So every step
// has an absolute deadline and the scheduler is asked, not told, what to do.
//
// The other half is ownership. Keys a macro pressed belong to the macro; keys
// a person is holding belong to the person. Stopping a macro releases what it
// pressed and nothing else - releasing the operator's held shift because a
// macro was cancelled would change what everything they type next means.

#include "macros/scheduler.hpp"
#include "test_support.hpp"

#include <vector>

using duo_input::config::MacroStepType;
using duo_input::hid::Target;
using duo_input::u1::input::InputEventKind;
using duo_input::u1::macros::kMacroQueueDepth;
using duo_input::u1::macros::MacroDefinition;
using duo_input::u1::macros::MacroOutput;
using duo_input::u1::macros::MacroOutputKind;
using duo_input::u1::macros::MacroScheduler;
using duo_input::u1::macros::MacroStep;
using duo_input::u1::macros::StopReason;

namespace {

MacroStep key_down(std::uint16_t usage) {
    MacroStep step;
    step.kind = MacroStepType::KEY_DOWN;
    step.code = usage;
    return step;
}

MacroStep key_up(std::uint16_t usage) {
    MacroStep step;
    step.kind = MacroStepType::KEY_UP;
    step.code = usage;
    return step;
}

MacroStep key_tap(std::uint16_t usage) {
    MacroStep step;
    step.kind = MacroStepType::KEY_TAP;
    step.code = usage;
    return step;
}

MacroStep delay(std::uint16_t fixed_ms, std::uint16_t jitter_ms = 0) {
    MacroStep step;
    step.kind = MacroStepType::DELAY;
    step.delay_ms = fixed_ms;
    step.jitter_ms = jitter_ms;
    return step;
}

MacroStep set_profile(std::uint8_t profile) {
    MacroStep step;
    step.kind = MacroStepType::SET_PROFILE;
    step.code = profile;
    return step;
}

/// A random source that answers whatever the test says it should.
struct ScriptedRandom final : duo_input::u1::macros::IRandom {
    std::uint32_t answer = 0;
    int asked = 0;

    std::uint32_t next(std::uint32_t bound) override {
        ++asked;
        return bound == 0 ? 0 : answer % bound;
    }
};

/// Everything the scheduler asked for, in order.
struct Recorder {
    std::vector<MacroOutput> outputs;

    void drain(MacroScheduler& scheduler, std::uint32_t now_ms) {
        MacroOutput output;
        while (scheduler.tick(now_ms, output)) {
            outputs.push_back(output);
        }
    }

    int count_of(MacroOutputKind kind) const {
        int seen = 0;
        for (const MacroOutput& output : outputs) {
            if (output.kind == kind) {
                ++seen;
            }
        }
        return seen;
    }

    int keys(InputEventKind kind, std::uint16_t code) const {
        int seen = 0;
        for (const MacroOutput& output : outputs) {
            if (output.kind == MacroOutputKind::SendInput && output.event.kind == kind &&
                output.event.code == code) {
                ++seen;
            }
        }
        return seen;
    }
};

}  // namespace

// -------------------------------------------------------------- one macro

TEST_CASE(a_macro_that_was_never_started_does_nothing) {
    MacroScheduler scheduler;
    Recorder recorder;

    recorder.drain(scheduler, 1000);

    CHECK_EQ(recorder.outputs.size(), 0u);
    CHECK(!scheduler.active());
}

TEST_CASE(a_tap_is_a_press_and_a_release) {
    const MacroStep steps[] = {key_tap(0x04)};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{steps, 1});
    Recorder recorder;

    scheduler.enqueue(0, Target::Pc1, 1000);
    recorder.drain(scheduler, 1000);

    CHECK_EQ(recorder.keys(InputEventKind::KeyDown, 0x04), 1);
    CHECK_EQ(recorder.keys(InputEventKind::KeyUp, 0x04), 1);
}

TEST_CASE(a_macro_ends_and_stops_being_active) {
    const MacroStep steps[] = {key_tap(0x04)};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{steps, 1});
    Recorder recorder;
    scheduler.enqueue(0, Target::Pc1, 1000);

    recorder.drain(scheduler, 1000);

    CHECK(!scheduler.active());
}

TEST_CASE(every_step_carries_the_target_it_was_started_for) {
    const MacroStep steps[] = {key_tap(0x04)};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{steps, 1});
    Recorder recorder;

    scheduler.enqueue(0, Target::Pc2, 1000);
    recorder.drain(scheduler, 1000);

    // A macro typed onto the wrong computer is worse than one that does not
    // run: the person is looking at the other screen.
    CHECK(!recorder.outputs.empty());
    CHECK_EQ(static_cast<int>(recorder.outputs[0].target), static_cast<int>(Target::Pc2));
}

// ---------------------------------------------------------------- delays

TEST_CASE(a_delay_stops_the_macro_and_not_the_loop) {
    const MacroStep steps[] = {key_tap(0x04), delay(50), key_tap(0x05)};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{steps, 3});
    Recorder recorder;
    scheduler.enqueue(0, Target::Pc1, 1000);

    recorder.drain(scheduler, 1000);

    // The first tap happened; the second is waiting. Nothing here blocked:
    // the caller got its loop back, which is the whole point.
    CHECK_EQ(recorder.keys(InputEventKind::KeyDown, 0x04), 1);
    CHECK_EQ(recorder.keys(InputEventKind::KeyDown, 0x05), 0);
    CHECK(scheduler.active());
}

TEST_CASE(a_delay_ends_when_it_says_it_will) {
    const MacroStep steps[] = {delay(50), key_tap(0x05)};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{steps, 2});
    Recorder recorder;
    scheduler.enqueue(0, Target::Pc1, 1000);
    recorder.drain(scheduler, 1000);

    recorder.drain(scheduler, 1049);
    CHECK_EQ(recorder.keys(InputEventKind::KeyDown, 0x05), 0);

    recorder.drain(scheduler, 1050);
    CHECK_EQ(recorder.keys(InputEventKind::KeyDown, 0x05), 1);
}

TEST_CASE(a_random_delay_asks_the_source_it_was_given) {
    const MacroStep steps[] = {delay(10, 40), key_tap(0x05)};
    ScriptedRandom random;
    random.answer = 30;
    MacroScheduler scheduler(&random);
    scheduler.define(0, MacroDefinition{steps, 2});
    Recorder recorder;
    scheduler.enqueue(0, Target::Pc1, 1000);

    recorder.drain(scheduler, 1000);
    recorder.drain(scheduler, 1039);
    CHECK_EQ(recorder.keys(InputEventKind::KeyDown, 0x05), 0);

    recorder.drain(scheduler, 1040);
    CHECK_EQ(random.asked, 1);
    CHECK_EQ(recorder.keys(InputEventKind::KeyDown, 0x05), 1);
}

TEST_CASE(a_deadline_that_crosses_the_clock_wrapping_still_ends) {
    const MacroStep steps[] = {delay(100), key_tap(0x05)};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{steps, 2});
    Recorder recorder;

    // The millisecond counter wraps after 49 days. Compared directly rather
    // than subtracted, a deadline on the far side of that reads as either no
    // wait at all or a wait of seven weeks.
    const std::uint32_t near_the_end = 0xFFFFFFC0u;
    scheduler.enqueue(0, Target::Pc1, near_the_end);
    recorder.drain(scheduler, near_the_end);

    // Half way through, on the near side of the wrap. A plain comparison
    // fires here, a hundred milliseconds early.
    recorder.drain(scheduler, near_the_end + 50u);
    CHECK_EQ(recorder.keys(InputEventKind::KeyDown, 0x05), 0);

    recorder.drain(scheduler, near_the_end + 100u);

    CHECK_EQ(recorder.keys(InputEventKind::KeyDown, 0x05), 1);
}

// ----------------------------------------------------------------- queue

TEST_CASE(a_macro_started_while_one_is_running_waits_its_turn) {
    const MacroStep first[] = {delay(50), key_tap(0x04)};
    const MacroStep second[] = {key_tap(0x05)};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{first, 2});
    scheduler.define(1, MacroDefinition{second, 1});
    Recorder recorder;

    scheduler.enqueue(0, Target::Pc1, 1000);
    scheduler.enqueue(1, Target::Pc1, 1000);
    recorder.drain(scheduler, 1000);

    // Two macros typing at once would interleave their keystrokes into
    // something neither of them meant.
    CHECK_EQ(recorder.keys(InputEventKind::KeyDown, 0x05), 0);
    CHECK_EQ(scheduler.queued_count(), 1u);
}

TEST_CASE(the_waiting_macro_runs_when_the_first_finishes) {
    const MacroStep first[] = {delay(50), key_tap(0x04)};
    const MacroStep second[] = {key_tap(0x05)};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{first, 2});
    scheduler.define(1, MacroDefinition{second, 1});
    Recorder recorder;
    scheduler.enqueue(0, Target::Pc1, 1000);
    scheduler.enqueue(1, Target::Pc1, 1000);

    recorder.drain(scheduler, 1000);
    recorder.drain(scheduler, 1050);

    CHECK_EQ(recorder.keys(InputEventKind::KeyDown, 0x04), 1);
    CHECK_EQ(recorder.keys(InputEventKind::KeyDown, 0x05), 1);
}

TEST_CASE(four_can_wait_and_the_fifth_is_refused) {
    const MacroStep steps[] = {delay(1000)};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{steps, 1});
    MacroOutput ignored;
    scheduler.enqueue(0, Target::Pc1, 1000);
    scheduler.tick(1000, ignored);

    for (std::size_t index = 0; index < kMacroQueueDepth; ++index) {
        CHECK(scheduler.enqueue(0, Target::Pc1, 1000));
    }

    // Refused rather than dropped silently or run late: somebody leaning on a
    // bound key should not queue up a minute of typing that arrives after
    // they have moved on.
    CHECK(!scheduler.enqueue(0, Target::Pc1, 1000));
    CHECK_EQ(scheduler.queued_count(), kMacroQueueDepth);
}

TEST_CASE(the_queue_depth_is_the_one_the_plan_names) {
    CHECK_EQ(kMacroQueueDepth, 4u);
}

// ------------------------------------------------------------- ownership

TEST_CASE(stopping_releases_what_the_macro_is_holding) {
    const MacroStep steps[] = {key_down(0x04), key_down(0x05), delay(1000)};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{steps, 3});
    Recorder recorder;
    scheduler.enqueue(0, Target::Pc1, 1000);
    recorder.drain(scheduler, 1000);
    recorder.outputs.clear();

    scheduler.stop_all();
    recorder.drain(scheduler, 1000);

    // A macro cancelled part way through must not leave its keys down. It is
    // the only thing that knows they are.
    CHECK_EQ(recorder.keys(InputEventKind::KeyUp, 0x04), 1);
    CHECK_EQ(recorder.keys(InputEventKind::KeyUp, 0x05), 1);
}

TEST_CASE(stopping_clears_the_queue_as_well) {
    const MacroStep steps[] = {delay(1000)};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{steps, 1});
    MacroOutput ignored;
    scheduler.enqueue(0, Target::Pc1, 1000);
    scheduler.tick(1000, ignored);
    scheduler.enqueue(0, Target::Pc1, 1000);

    scheduler.stop_all();

    // Stop means stop. A queue that empties itself afterwards is a macro that
    // starts typing after the person reached for the emergency control.
    CHECK_EQ(scheduler.queued_count(), 0u);
    CHECK(!scheduler.active());
}

TEST_CASE(a_macro_that_ends_normally_releases_what_it_pressed) {
    const MacroStep steps[] = {key_down(0x04)};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{steps, 1});
    Recorder recorder;

    scheduler.enqueue(0, Target::Pc1, 1000);
    recorder.drain(scheduler, 1000);

    // A macro that presses without releasing has written a bug into somebody
    // else's computer. The scheduler cleans up after it either way.
    CHECK_EQ(recorder.keys(InputEventKind::KeyUp, 0x04), 1);
}

TEST_CASE(a_key_the_macro_already_released_is_not_released_twice) {
    const MacroStep steps[] = {key_down(0x04), key_up(0x04)};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{steps, 2});
    Recorder recorder;

    scheduler.enqueue(0, Target::Pc1, 1000);
    recorder.drain(scheduler, 1000);

    CHECK_EQ(recorder.keys(InputEventKind::KeyUp, 0x04), 1);
}

// ------------------------------------------------------------ the limit

TEST_CASE(a_macro_holding_more_keys_than_a_keyboard_can_report_is_abandoned) {
    // A boot keyboard report carries six usages. A macro that tries to hold a
    // seventh cannot be delivered, and delivering six of the seven would type
    // something the operator did not write.
    const MacroStep steps[] = {key_down(0x04), key_down(0x05), key_down(0x06), key_down(0x07),
                               key_down(0x08), key_down(0x09), key_down(0x0A)};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{steps, 7});
    Recorder recorder;

    scheduler.enqueue(0, Target::Pc1, 1000);
    recorder.drain(scheduler, 1000);

    CHECK_EQ(static_cast<int>(scheduler.last_stop_reason()),
             static_cast<int>(StopReason::TooManyKeys));
    CHECK(!scheduler.active());
}

TEST_CASE(abandoning_a_macro_still_releases_what_it_had_pressed) {
    const MacroStep steps[] = {key_down(0x04), key_down(0x05), key_down(0x06), key_down(0x07),
                               key_down(0x08), key_down(0x09), key_down(0x0A)};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{steps, 7});
    Recorder recorder;

    scheduler.enqueue(0, Target::Pc1, 1000);
    recorder.drain(scheduler, 1000);

    CHECK_EQ(recorder.keys(InputEventKind::KeyUp, 0x04), 1);
    CHECK_EQ(recorder.keys(InputEventKind::KeyUp, 0x09), 1);
}

// ------------------------------------------------------- other step kinds

TEST_CASE(a_profile_step_asks_for_the_profile) {
    const MacroStep steps[] = {set_profile(3)};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{steps, 1});
    Recorder recorder;

    scheduler.enqueue(0, Target::Pc1, 1000);
    recorder.drain(scheduler, 1000);

    CHECK_EQ(recorder.count_of(MacroOutputKind::SetProfile), 1);
}

TEST_CASE(a_route_step_asks_for_the_route) {
    MacroStep step;
    step.kind = MacroStepType::SET_MOUSE_ROUTE;
    step.code = 2;
    const MacroStep steps[] = {step};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{steps, 1});
    Recorder recorder;

    scheduler.enqueue(0, Target::Pc1, 1000);
    recorder.drain(scheduler, 1000);

    CHECK_EQ(recorder.count_of(MacroOutputKind::SetMouseRoute), 1);
    CHECK_EQ(static_cast<int>(recorder.outputs[0].parameter), 2);
}

TEST_CASE(a_consumer_tap_is_pressed_and_released) {
    MacroStep step;
    step.kind = MacroStepType::CONSUMER_TAP;
    step.code = 0x00E9;  // volume up
    const MacroStep steps[] = {step};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{steps, 1});
    Recorder recorder;

    scheduler.enqueue(0, Target::Pc1, 1000);
    recorder.drain(scheduler, 1000);

    // Held down, the volume climbs until something releases it.
    CHECK_EQ(recorder.keys(InputEventKind::ConsumerDown, 0x00E9), 1);
    CHECK_EQ(recorder.keys(InputEventKind::ConsumerUp, 0x00E9), 1);
}

TEST_CASE(a_consumer_tap_cut_short_is_still_released) {
    MacroStep tap;
    tap.kind = MacroStepType::CONSUMER_TAP;
    tap.code = 0x00E9;
    const MacroStep steps[] = {tap};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{steps, 1});
    Recorder recorder;
    scheduler.enqueue(0, Target::Pc1, 1000);
    MacroOutput first;
    scheduler.tick(1000, first);  // the press, and nothing more

    scheduler.stop_all();
    recorder.drain(scheduler, 1000);

    CHECK_EQ(recorder.keys(InputEventKind::ConsumerUp, 0x00E9), 1);
}

TEST_CASE(text_is_compiled_by_the_host_and_skipped_here) {
    MacroStep text;
    text.kind = MacroStepType::TEXT;
    const MacroStep steps[] = {text, key_tap(0x04)};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{steps, 2});
    Recorder recorder;

    scheduler.enqueue(0, Target::Pc1, 1000);
    recorder.drain(scheduler, 1000);

    // Turning characters into usages needs a keyboard layout, and the device
    // does not know which one the operator is typing on. The step is inert
    // rather than fatal: the macro after it still runs.
    CHECK_EQ(recorder.keys(InputEventKind::KeyDown, 0x04), 1);
}

TEST_CASE(an_undefined_macro_is_refused_rather_than_run_empty) {
    MacroScheduler scheduler;

    CHECK(!scheduler.enqueue(7, Target::Pc1, 1000));
    CHECK(!scheduler.active());
}

// ------------------------------------------------- ten thousand of them

TEST_CASE(ten_thousand_starts_and_stops_leave_nothing_held) {
    // The fault this looks for is a macro cancelled at exactly the wrong step,
    // leaving a key down on a computer that will never be told otherwise. It
    // needs the stop to land between a press and its release, which happens
    // rarely and then constantly.
    const MacroStep steps[] = {key_down(0x04), delay(5), key_up(0x04), key_tap(0x05), delay(3)};
    MacroScheduler scheduler;
    scheduler.define(0, MacroDefinition{steps, 5});

    std::uint32_t seed = 0x2468ACEu;
    auto next = [&seed]() {
        seed = seed * 1103515245u + 12345u;
        return seed >> 16;
    };

    int down = 0;
    int up = 0;
    std::uint32_t now = 1000;
    for (int round = 0; round < 10000; ++round) {
        scheduler.enqueue(0, Target::Pc1, now);
        for (int step = 0; step < 4; ++step) {
            MacroOutput output;
            while (scheduler.tick(now, output)) {
                if (output.kind == MacroOutputKind::SendInput) {
                    if (output.event.kind == InputEventKind::KeyDown) {
                        ++down;
                    } else if (output.event.kind == InputEventKind::KeyUp) {
                        ++up;
                    }
                }
            }
            now += 1 + (next() % 4);
        }
        if ((next() % 3) == 0) {
            MacroOutput output;
            scheduler.stop_all();
            while (scheduler.tick(now, output)) {
                if (output.kind == MacroOutputKind::SendInput &&
                    output.event.kind == InputEventKind::KeyUp) {
                    ++up;
                }
            }
        }
    }

    MacroOutput output;
    scheduler.stop_all();
    while (scheduler.tick(now, output)) {
        if (output.kind == MacroOutputKind::SendInput &&
            output.event.kind == InputEventKind::KeyUp) {
            ++up;
        }
    }

    // Every press a macro made was matched by a release, however it ended.
    CHECK_EQ(down, up);
    CHECK(!scheduler.active());
}
