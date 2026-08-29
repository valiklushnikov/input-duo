// The queue between the two cores, and what happens to what comes out of it.
//
// Core 1 reads peripherals and decides what input means. Core 0 owns USB, SPI
// and flash, and must never wait: a Core 0 that blocks is a Core 0 that stops
// answering the host, and Windows removes devices that stop answering. So the
// two communicate through a bounded queue that neither side can block on, and
// a full queue is a fault to report rather than a place to wait.

#include "hid/types.hpp"
#include "output_runtime.hpp"
#include "runtime/output_command.hpp"
#include "runtime/spsc_queue.hpp"
#include "test_support.hpp"

using duo_input::hid::Target;
using duo_input::runtime::CommandKind;
using duo_input::runtime::kOutputQueueCapacity;
using duo_input::runtime::OutputCommand;
using duo_input::runtime::Route;
using duo_input::runtime::RuntimeFault;
using duo_input::runtime::SpscQueue;
using duo_input::u1::OutputRuntime;

namespace {

constexpr std::uint8_t kA = 0x04;
constexpr std::uint8_t kB = 0x05;

OutputCommand key(Route route, std::uint8_t usage, bool pressed) {
    OutputCommand command;
    command.kind = pressed ? CommandKind::KeyPress : CommandKind::KeyRelease;
    command.route = route;
    command.code = usage;
    return command;
}

OutputCommand movement(Route route, std::int32_t dx) {
    OutputCommand command;
    command.kind = CommandKind::MouseDelta;
    command.route = route;
    command.delta_x = dx;
    return command;
}

/// Both computers say they have the keyboard state now held.
///
/// On the board this is said by UsbService::publish and SpiMaster::poll, each
/// on the pass that told its own computer. Here it stands for a pass in which
/// both got through.
void both_computers_told(OutputRuntime& runtime) {
    runtime.keyboard_reported(Target::Pc1);
    runtime.keyboard_reported(Target::Pc2);
}

}  // namespace

// -------------------------------------------------------------------- queue

TEST_CASE(a_fresh_queue_is_empty) {
    SpscQueue<int, 8> queue;

    int value = 0;
    CHECK_FALSE(queue.pop(value));
    CHECK_EQ(queue.size(), 0u);
}

TEST_CASE(what_goes_in_first_comes_out_first) {
    SpscQueue<int, 8> queue;
    CHECK(queue.push(1));
    CHECK(queue.push(2));
    CHECK(queue.push(3));

    int first = 0, second = 0, third = 0;
    CHECK(queue.pop(first));
    CHECK(queue.pop(second));
    CHECK(queue.pop(third));

    CHECK_EQ(first, 1);
    CHECK_EQ(second, 2);
    CHECK_EQ(third, 3);
}

TEST_CASE(a_queue_holds_one_less_than_its_capacity) {
    SpscQueue<int, 8> queue;

    // One slot is always left empty: it is what distinguishes a full ring from
    // an empty one without a separate count that both cores would have to
    // agree on.
    for (int value = 0; value < 7; ++value) {
        CHECK(queue.push(value));
    }
    CHECK_FALSE(queue.push(99));
    CHECK_EQ(queue.size(), 7u);
}

TEST_CASE(a_full_queue_refuses_rather_than_overwriting) {
    SpscQueue<int, 4> queue;
    for (int value = 0; value < 3; ++value) {
        queue.push(value);
    }

    CHECK_FALSE(queue.push(99));

    // Overwriting would silently drop the oldest command, which might be the
    // key release that stops a key repeating forever.
    int first = 0;
    queue.pop(first);
    CHECK_EQ(first, 0);
}

TEST_CASE(the_ring_wraps_without_losing_anything) {
    SpscQueue<int, 4> queue;

    for (int round = 0; round < 100; ++round) {
        CHECK(queue.push(round));
        int value = -1;
        CHECK(queue.pop(value));
        CHECK_EQ(value, round);
    }
    CHECK_EQ(queue.size(), 0u);
}

TEST_CASE(draining_a_full_queue_makes_room_again) {
    SpscQueue<int, 4> queue;
    while (queue.push(0)) {
    }

    int value = 0;
    CHECK(queue.pop(value));

    CHECK(queue.push(7));
}

TEST_CASE(the_command_queue_is_the_size_the_plan_names) {
    CHECK_EQ(kOutputQueueCapacity, 128u);
}

// ------------------------------------------------------------------ routing

TEST_CASE(a_pc1_command_leaves_pc2_untouched) {
    OutputRuntime runtime;

    runtime.process(key(Route::Pc1, kA, true));

    CHECK(runtime.snapshot(Target::Pc1).keyboard.contains(kA));
    // Nothing has been given to the link, because nothing about PC2 changed.
    CHECK_FALSE(runtime.snapshot(Target::Pc2).keyboard.contains(kA));
}

TEST_CASE(a_pc2_command_leaves_pc1_untouched) {
    OutputRuntime runtime;

    runtime.process(key(Route::Pc2, kA, true));

    CHECK(runtime.snapshot(Target::Pc2).keyboard.contains(kA));
    CHECK_FALSE(runtime.snapshot(Target::Pc1).keyboard.contains(kA));
}

TEST_CASE(a_both_command_reaches_both_computers) {
    OutputRuntime runtime;

    runtime.process(key(Route::Both, kA, true));

    CHECK(runtime.snapshot(Target::Pc1).keyboard.contains(kA));
    CHECK(runtime.snapshot(Target::Pc2).keyboard.contains(kA));
}

TEST_CASE(releasing_on_both_releases_on_both) {
    OutputRuntime runtime;
    runtime.process(key(Route::Both, kA, true));

    runtime.process(key(Route::Both, kA, false));

    CHECK_FALSE(runtime.snapshot(Target::Pc1).keyboard.contains(kA));
    CHECK_FALSE(runtime.snapshot(Target::Pc2).keyboard.contains(kA));
}

TEST_CASE(a_key_held_on_both_and_released_on_one_stays_on_the_other) {
    OutputRuntime runtime;
    runtime.process(key(Route::Both, kA, true));

    runtime.process(key(Route::Pc1, kA, false));

    CHECK_FALSE(runtime.snapshot(Target::Pc1).keyboard.contains(kA));
    CHECK(runtime.snapshot(Target::Pc2).keyboard.contains(kA));
}

// ----------------------------------------------------------------- commands

TEST_CASE(modifiers_route_the_same_way_keys_do) {
    OutputRuntime runtime;
    OutputCommand command;
    command.kind = CommandKind::ModifiersPress;
    command.route = Route::Pc2;
    command.code = 0x02;

    runtime.process(command);

    CHECK_EQ(runtime.snapshot(Target::Pc2).keyboard.modifiers, 0x02u);
    CHECK_EQ(runtime.snapshot(Target::Pc1).keyboard.modifiers, 0u);
}

TEST_CASE(mouse_buttons_are_set_on_the_routed_computer_only) {
    OutputRuntime runtime;
    OutputCommand command;
    command.kind = CommandKind::MouseButtons;
    command.route = Route::Pc1;
    command.code = 0b101;

    runtime.process(command);

    CHECK_EQ(runtime.snapshot(Target::Pc1).mouse.buttons, 0b101u);
    CHECK_EQ(runtime.snapshot(Target::Pc2).mouse.buttons, 0u);
}

TEST_CASE(mouse_movement_accumulates_on_the_routed_computer) {
    OutputRuntime runtime;
    OutputCommand command;
    command.kind = CommandKind::MouseDelta;
    command.route = Route::Pc2;
    command.delta_x = 5;
    command.delta_y = -3;

    runtime.process(command);
    runtime.process(command);

    CHECK_EQ(runtime.snapshot(Target::Pc2).mouse.delta_x, 10);
    CHECK_EQ(runtime.snapshot(Target::Pc2).mouse.delta_y, -6);
    CHECK_EQ(runtime.snapshot(Target::Pc1).mouse.delta_x, 0);
}

TEST_CASE(release_all_clears_both_computers_whatever_the_route_said) {
    OutputRuntime runtime;
    runtime.process(key(Route::Pc1, kA, true));
    runtime.process(key(Route::Pc2, kB, true));

    OutputCommand command;
    command.kind = CommandKind::ReleaseAll;
    command.route = Route::Pc1;
    runtime.process(command);

    // STOP AND RELEASE ALL means everything, everywhere. A route that narrowed
    // it would leave a key held on the computer the operator was not looking
    // at, which is the case it exists for.
    CHECK_EQ(runtime.snapshot(Target::Pc1).keyboard.key_count, 0u);
    CHECK_EQ(runtime.snapshot(Target::Pc2).keyboard.key_count, 0u);
}

TEST_CASE(a_macro_release_lets_go_of_only_that_macros_keys) {
    OutputRuntime runtime;
    OutputCommand press;
    press.kind = CommandKind::KeyPress;
    press.route = Route::Pc1;
    press.code = kA;
    press.owner = 3;
    runtime.process(press);
    runtime.process(key(Route::Pc1, kB, true));

    OutputCommand release;
    release.kind = CommandKind::ReleaseMacro;
    release.owner = 3;
    runtime.process(release);

    CHECK_FALSE(runtime.snapshot(Target::Pc1).keyboard.contains(kA));
    CHECK(runtime.snapshot(Target::Pc1).keyboard.contains(kB));
}

TEST_CASE(a_key_held_by_a_macro_and_a_finger_survives_the_macro_ending) {
    OutputRuntime runtime;
    runtime.process(key(Route::Pc1, kA, true));
    OutputCommand macro_press;
    macro_press.kind = CommandKind::KeyPress;
    macro_press.route = Route::Pc1;
    macro_press.code = kA;
    macro_press.owner = 1;
    runtime.process(macro_press);

    OutputCommand release;
    release.kind = CommandKind::ReleaseMacro;
    release.owner = 1;
    runtime.process(release);

    CHECK(runtime.snapshot(Target::Pc1).keyboard.contains(kA));
}

// ------------------------------------------------------------------- faults

TEST_CASE(a_fresh_runtime_reports_no_fault) {
    OutputRuntime runtime;

    CHECK_EQ(runtime.fault(), RuntimeFault::None);
}

TEST_CASE(a_command_that_overflows_the_queue_is_a_fault_not_a_wait) {
    OutputRuntime runtime;

    for (std::size_t index = 0; index < kOutputQueueCapacity + 10; ++index) {
        runtime.submit(key(Route::Pc1, kA, true));
    }
    // The producer runs on Core 1 and writes nothing here but its refusal
    // count. The fault is Core 0's conclusion, reached in the drain.
    CHECK_EQ(runtime.fault(), RuntimeFault::None);
    // Ten over the constant, plus the one slot the ring always leaves empty
    // to tell full from empty.
    CHECK_EQ(runtime.refused_commands(), 11u);

    runtime.drain(0);

    // Core 1 must never block waiting for Core 0, and Core 0 must never be
    // made to wait for the host. The only honest answer to a full queue is to
    // say so.
    CHECK_EQ(runtime.fault(), RuntimeFault::OutputQueueFull);
}

TEST_CASE(a_full_queue_releases_everything_rather_than_typing_half_of_it) {
    OutputRuntime runtime;
    runtime.process(key(Route::Both, kA, true));

    for (std::size_t index = 0; index < kOutputQueueCapacity + 10; ++index) {
        runtime.submit(key(Route::Pc1, kB, true));
    }
    runtime.drain(0);

    // Commands were lost, so what is held no longer corresponds to anything
    // anyone did. Half a macro is worse than none of it.
    CHECK_EQ(runtime.snapshot(Target::Pc1).keyboard.key_count, 0u);
    CHECK_EQ(runtime.snapshot(Target::Pc2).keyboard.key_count, 0u);
}

TEST_CASE(a_runtime_that_overflowed_and_drained_accepts_input_again) {
    OutputRuntime runtime;
    for (std::size_t index = 0; index < kOutputQueueCapacity + 10; ++index) {
        runtime.submit(key(Route::Pc1, kA, true));
    }

    // The pass that notices the loss lets go of everything and applies
    // nothing. Nobody clears anything by hand: there is no operator inside
    // the loop, and a fault only a configurator could clear is a board whose
    // keyboard and mouse are dead on both computers until it is unplugged.
    CHECK_EQ(runtime.drain(0), 0u);
    CHECK_EQ(runtime.fault(), RuntimeFault::OutputQueueFull);

    // The burst is over - nothing was refused during that pass - so the next
    // one resumes. The pass that raised the fault let go of everything, and
    // that release is a keyboard state like any other: both computers hear it
    // before anything else is applied.
    both_computers_told(runtime);
    CHECK(runtime.submit(key(Route::Pc1, kB, true)));
    CHECK_EQ(runtime.drain(0), 1u);
    CHECK_EQ(runtime.fault(), RuntimeFault::None);
    CHECK(runtime.snapshot(Target::Pc1).keyboard.contains(kB));
}

TEST_CASE(a_fault_stands_while_the_queue_is_still_overflowing) {
    OutputRuntime runtime;
    for (std::size_t index = 0; index < kOutputQueueCapacity + 1; ++index) {
        runtime.submit(key(Route::Pc1, kA, true));
    }
    CHECK_EQ(runtime.drain(0), 0u);

    // Still overflowing. Recovery is one pass with nothing refused, not one
    // pass: a runtime that resumed here would apply half of the next burst.
    for (std::size_t index = 0; index < kOutputQueueCapacity + 1; ++index) {
        runtime.submit(key(Route::Pc1, kA, true));
    }
    CHECK_EQ(runtime.drain(0), 0u);
    CHECK_EQ(runtime.fault(), RuntimeFault::OutputQueueFull);
    CHECK_EQ(runtime.snapshot(Target::Pc1).keyboard.key_count, 0u);
}

// -------------------------------------------------------------------- drain

TEST_CASE(draining_applies_everything_that_was_submitted_in_order) {
    OutputRuntime runtime;
    runtime.submit(key(Route::Pc1, kA, true));
    runtime.submit(key(Route::Pc1, kB, true));
    runtime.submit(key(Route::Pc1, kA, false));

    // One keyboard state to a pass, because this holds a state and not a queue
    // of reports: a second change applied before the first was published would
    // erase a state nobody was ever told about. So the queue comes out in
    // order, one pass at a time, as each state is acknowledged.
    std::size_t applied = 0;
    for (int pass = 0; pass < 3; ++pass) {
        applied += runtime.drain(static_cast<std::uint32_t>(pass));
        both_computers_told(runtime);
    }

    CHECK_EQ(applied, 3u);
    CHECK_FALSE(runtime.snapshot(Target::Pc1).keyboard.contains(kA));
    CHECK(runtime.snapshot(Target::Pc1).keyboard.contains(kB));
}

TEST_CASE(draining_an_empty_queue_does_nothing) {
    OutputRuntime runtime;

    CHECK_EQ(runtime.drain(0), 0u);
    CHECK_EQ(runtime.fault(), RuntimeFault::None);
}

TEST_CASE(a_drain_is_bounded_so_core_zero_still_reaches_usb) {
    OutputRuntime runtime;
    // Movement, so what stops this is the bound and not the publication
    // interlock: the pointer is a delta and is never held back for a report.
    for (std::size_t index = 0; index < 100; ++index) {
        runtime.submit(movement(Route::Pc1, 1));
    }

    const std::size_t applied = runtime.drain(0, 16);

    // Core 0 has a millisecond of USB to service. Draining without a bound
    // would let a burst of input starve the thing the input is for.
    CHECK_EQ(applied, 16u);
}

// -------------------------------------------------------------- publication
//
// This runtime holds a state, not a queue of reports. A state replaced before
// anyone was told about it is a keystroke that never happened - or a release
// that never happened, which leaves a key down on a computer the operator may
// not be watching. So the keyboard state moves on only when every computer has
// said it has the one now held.

TEST_CASE(a_second_keyboard_state_waits_until_both_computers_have_the_first) {
    OutputRuntime runtime;
    runtime.submit(key(Route::Both, kA, true));
    runtime.submit(key(Route::Both, kA, false));

    CHECK_EQ(runtime.drain(0), 1u);
    CHECK(runtime.snapshot(Target::Pc1).keyboard.contains(kA));

    // Neither computer has been told. Applying the release now would leave the
    // state exactly as it was before the press, and the keystroke would never
    // have existed as far as either host is concerned.
    CHECK_EQ(runtime.drain(0), 0u);
    CHECK(runtime.snapshot(Target::Pc1).keyboard.contains(kA));

    // One of them is not enough - the other is still owed the press.
    runtime.keyboard_reported(Target::Pc1);
    CHECK_EQ(runtime.drain(0), 0u);
    CHECK(runtime.snapshot(Target::Pc2).keyboard.contains(kA));

    runtime.keyboard_reported(Target::Pc2);
    CHECK_EQ(runtime.drain(0), 1u);
    CHECK_FALSE(runtime.snapshot(Target::Pc1).keyboard.contains(kA));
    CHECK_FALSE(runtime.snapshot(Target::Pc2).keyboard.contains(kA));
}

TEST_CASE(the_pointer_is_never_held_back_by_an_unpublished_keyboard_state) {
    OutputRuntime runtime;
    runtime.submit(key(Route::Pc1, kA, true));
    CHECK_EQ(runtime.drain(0), 1u);

    // Nobody has acknowledged the press, and the mouse does not care: movement
    // says nothing about which keys are down, and a pointer that stuttered
    // every time somebody typed would be the worse fault by far.
    runtime.submit(movement(Route::Pc1, 5));
    CHECK_EQ(runtime.drain(0), 1u);
    CHECK_EQ(runtime.snapshot(Target::Pc1).mouse.delta_x, 5);
}

TEST_CASE(a_computer_that_stops_answering_does_not_stop_the_other_one) {
    OutputRuntime runtime;
    runtime.submit(key(Route::Both, kA, true));
    runtime.submit(key(Route::Both, kA, false));
    CHECK_EQ(runtime.drain(1000), 1u);

    // PC1 keeps up. PC2 says nothing at all - a severed link, or a slave that
    // has stopped answering - and the whole grace goes by.
    runtime.keyboard_reported(Target::Pc1);
    CHECK_EQ(runtime.drain(1000), 0u);
    CHECK_EQ(runtime.drain(1000 + duo_input::u1::kPublishGraceMs - 1), 0u);

    // Long enough. PC1 is receiving input and must not be made deaf by PC2.
    CHECK_EQ(runtime.drain(1000 + duo_input::u1::kPublishGraceMs), 1u);
}

TEST_CASE(a_computer_set_aside_for_silence_is_not_waited_for_again) {
    OutputRuntime runtime;
    runtime.submit(key(Route::Both, kA, true));
    CHECK_EQ(runtime.drain(1000), 1u);

    // PC1 keeps up, PC2 says nothing, and the grace runs out once.
    runtime.keyboard_reported(Target::Pc1);
    runtime.submit(key(Route::Both, kA, false));
    CHECK_EQ(runtime.drain(1000), 0u);
    CHECK_EQ(runtime.drain(1000 + duo_input::u1::kPublishGraceMs), 1u);

    // PC2 has been set aside. From here PC1 alone paces the drain, with no
    // second pause: a dead link costs one, not one per keystroke.
    runtime.keyboard_reported(Target::Pc1);
    runtime.submit(key(Route::Both, kB, true));
    CHECK_EQ(runtime.drain(1000 + duo_input::u1::kPublishGraceMs), 1u);
    CHECK(runtime.snapshot(Target::Pc1).keyboard.contains(kB));
}

TEST_CASE(the_pass_that_let_go_of_everything_does_not_leave_a_wait_running) {
    OutputRuntime runtime;
    runtime.submit(key(Route::Both, kA, true));
    runtime.submit(key(Route::Both, kB, true));

    // One state to a pass. The second command starts a wait at t=1000 that
    // nobody answers.
    CHECK_EQ(runtime.drain(1000), 1u);

    // Core 1 floods the queue. The pass that notices lets go of everything and
    // applies nothing - and the release it just made is a keyboard state that
    // neither computer has been told about either.
    for (std::size_t index = 0; index < kOutputQueueCapacity + 10; ++index) {
        runtime.submit(key(Route::Pc1, kA, true));
    }
    CHECK_EQ(runtime.drain(1000), 0u);
    CHECK_EQ(runtime.fault(), RuntimeFault::OutputQueueFull);

    // The burst is over and the operator types again. The wait that was
    // running before the fault belonged to a state that no longer exists, so
    // this is a fresh one: nobody has acknowledged the release, and the grace
    // has to be measured from here.
    //
    // Carried over, the old wait is already expired the moment it is looked
    // at - so both computers are set aside as silent when neither has been
    // silent for a millisecond, and everything queued applies in one unpaced
    // drain. Presses collapse against their own releases and the keystrokes
    // are never seen by either host.
    runtime.submit(key(Route::Both, kA, true));
    runtime.submit(key(Route::Both, kA, false));
    CHECK_EQ(runtime.drain(1000 + duo_input::u1::kPublishGraceMs), 0u);

    // And the pacing is intact rather than merely delayed: once both computers
    // acknowledge, the press applies and the release that follows it waits its
    // own turn. Under the carried-over wait both were already applied above,
    // so the press and its release have cancelled and nothing is held.
    both_computers_told(runtime);
    CHECK_EQ(runtime.drain(1000 + duo_input::u1::kPublishGraceMs), 1u);
    CHECK(runtime.snapshot(Target::Pc1).keyboard.contains(kA));
}

TEST_CASE(a_computer_that_answers_again_is_waited_for_again) {
    OutputRuntime runtime;
    runtime.submit(key(Route::Both, kA, true));
    CHECK_EQ(runtime.drain(1000), 1u);
    runtime.keyboard_reported(Target::Pc1);
    runtime.submit(key(Route::Both, kA, false));
    CHECK_EQ(runtime.drain(1000), 0u);
    CHECK_EQ(runtime.drain(1000 + duo_input::u1::kPublishGraceMs), 1u);

    // PC2 comes back. Being set aside is not permanent - a link that recovers
    // is a computer somebody is looking at again - so it is owed the next
    // state like anyone else, and the one after it waits for it.
    both_computers_told(runtime);
    runtime.submit(key(Route::Both, kB, true));
    runtime.submit(key(Route::Both, kB, false));
    CHECK_EQ(runtime.drain(2000), 1u);
    CHECK_EQ(runtime.drain(2000), 0u);
    CHECK(runtime.snapshot(Target::Pc2).keyboard.contains(kB));
}
