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
    runtime.drain();

    // Commands were lost, so what is held no longer corresponds to anything
    // anyone did. Half a macro is worse than none of it.
    CHECK_EQ(runtime.snapshot(Target::Pc1).keyboard.key_count, 0u);
    CHECK_EQ(runtime.snapshot(Target::Pc2).keyboard.key_count, 0u);
}

TEST_CASE(clearing_the_fault_lets_the_runtime_carry_on) {
    OutputRuntime runtime;
    for (std::size_t index = 0; index < kOutputQueueCapacity + 10; ++index) {
        runtime.submit(key(Route::Pc1, kA, true));
    }
    runtime.drain();

    runtime.clear_fault();
    CHECK_EQ(runtime.fault(), RuntimeFault::None);

    CHECK(runtime.submit(key(Route::Pc1, kB, true)));
    runtime.drain();
    CHECK(runtime.snapshot(Target::Pc1).keyboard.contains(kB));
}

// -------------------------------------------------------------------- drain

TEST_CASE(draining_applies_everything_that_was_submitted_in_order) {
    OutputRuntime runtime;
    runtime.submit(key(Route::Pc1, kA, true));
    runtime.submit(key(Route::Pc1, kB, true));
    runtime.submit(key(Route::Pc1, kA, false));

    const std::size_t applied = runtime.drain();

    CHECK_EQ(applied, 3u);
    CHECK_FALSE(runtime.snapshot(Target::Pc1).keyboard.contains(kA));
    CHECK(runtime.snapshot(Target::Pc1).keyboard.contains(kB));
}

TEST_CASE(draining_an_empty_queue_does_nothing) {
    OutputRuntime runtime;

    CHECK_EQ(runtime.drain(), 0u);
    CHECK_EQ(runtime.fault(), RuntimeFault::None);
}

TEST_CASE(a_drain_is_bounded_so_core_zero_still_reaches_usb) {
    OutputRuntime runtime;
    for (std::size_t index = 0; index < 100; ++index) {
        runtime.submit(key(Route::Pc1, kA, index % 2 == 0));
    }

    const std::size_t applied = runtime.drain(16);

    // Core 0 has a millisecond of USB to service. Draining without a bound
    // would let a burst of input starve the thing the input is for.
    CHECK_EQ(applied, 16u);
}
