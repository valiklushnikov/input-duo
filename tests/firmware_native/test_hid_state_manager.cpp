// The HID state manager decides what each computer is holding right now.
//
// Two things make it worth testing this hard. A key can be held by the
// operator's finger and by a running macro at the same time, and when the
// macro ends the finger must still be holding it - anything else drops a key
// out from under someone mid-word. And the six-key report has no room for a
// seventh: rejecting it must not disturb the six that are already there.

#include <initializer_list>

#include "hid/state_manager.hpp"
#include "test_support.hpp"

using duo_input::hid::HidResult;
using duo_input::hid::HidStateManager;
using duo_input::hid::KeyboardSnapshot;
using duo_input::hid::kMaxKeys;
using duo_input::hid::MouseButton;
using duo_input::hid::MouseSnapshot;
using duo_input::hid::Target;

namespace {

constexpr std::uint8_t kA = 0x04;
constexpr std::uint8_t kB = 0x05;
constexpr std::uint8_t kC = 0x06;

}  // namespace

// --------------------------------------------------------------- fresh state

TEST_CASE(a_new_manager_holds_nothing_on_either_computer) {
    HidStateManager manager;

    for (Target target : {Target::Pc1, Target::Pc2}) {
        const KeyboardSnapshot keyboard = manager.snapshot(target).keyboard;
        CHECK_EQ(keyboard.key_count, 0u);
        CHECK_EQ(keyboard.modifiers, 0u);
    }
}

TEST_CASE(a_snapshot_of_an_untouched_mouse_is_still) {
    HidStateManager manager;

    const MouseSnapshot mouse = manager.snapshot(Target::Pc1).mouse;

    CHECK_EQ(mouse.buttons, 0u);
    CHECK_EQ(mouse.delta_x, 0);
    CHECK_EQ(mouse.delta_y, 0);
    CHECK_EQ(mouse.wheel, 0);
    CHECK_EQ(mouse.pan, 0);
}

// ------------------------------------------------------------------ ownership

TEST_CASE(a_key_the_operator_holds_appears_in_the_report) {
    HidStateManager manager;

    CHECK_EQ(manager.physical_key(Target::Pc1, kA, true), HidResult::Ok);

    CHECK(manager.snapshot(Target::Pc1).keyboard.contains(kA));
}

TEST_CASE(a_key_pressed_on_one_computer_does_not_reach_the_other) {
    HidStateManager manager;

    manager.physical_key(Target::Pc1, kA, true);

    CHECK_FALSE(manager.snapshot(Target::Pc2).keyboard.contains(kA));
}

TEST_CASE(key_owned_by_physical_and_macro_survives_macro_release) {
    HidStateManager manager;
    manager.physical_key(Target::Pc1, kA, true);
    manager.macro_key(3, Target::Pc1, kA, true);

    manager.release_macro(3);

    // The finger is still down. Releasing it here would drop a key out from
    // under the operator in the middle of a word.
    CHECK(manager.snapshot(Target::Pc1).keyboard.contains(kA));
}

TEST_CASE(key_owned_by_macro_and_physical_survives_physical_release) {
    HidStateManager manager;
    manager.physical_key(Target::Pc1, kA, true);
    manager.macro_key(3, Target::Pc1, kA, true);

    manager.physical_key(Target::Pc1, kA, false);

    CHECK(manager.snapshot(Target::Pc1).keyboard.contains(kA));
}

TEST_CASE(a_key_lifts_only_when_its_last_owner_lets_go) {
    HidStateManager manager;
    manager.physical_key(Target::Pc1, kA, true);
    manager.macro_key(3, Target::Pc1, kA, true);

    manager.release_macro(3);
    manager.physical_key(Target::Pc1, kA, false);

    CHECK_FALSE(manager.snapshot(Target::Pc1).keyboard.contains(kA));
}

TEST_CASE(two_macros_holding_one_key_each_release_independently) {
    HidStateManager manager;
    manager.macro_key(1, Target::Pc1, kA, true);
    manager.macro_key(2, Target::Pc1, kA, true);

    manager.release_macro(1);
    CHECK(manager.snapshot(Target::Pc1).keyboard.contains(kA));

    manager.release_macro(2);
    CHECK_FALSE(manager.snapshot(Target::Pc1).keyboard.contains(kA));
}

TEST_CASE(pressing_a_key_twice_from_one_owner_is_not_two_holds) {
    HidStateManager manager;
    manager.physical_key(Target::Pc1, kA, true);
    manager.physical_key(Target::Pc1, kA, true);

    manager.physical_key(Target::Pc1, kA, false);

    CHECK_FALSE(manager.snapshot(Target::Pc1).keyboard.contains(kA));
}

TEST_CASE(releasing_a_key_nobody_holds_is_harmless) {
    HidStateManager manager;

    CHECK_EQ(manager.physical_key(Target::Pc1, kA, false), HidResult::Ok);

    CHECK_EQ(manager.snapshot(Target::Pc1).keyboard.key_count, 0u);
}

// ------------------------------------------------------------------ capacity

TEST_CASE(seventh_key_is_rejected_without_corrupting_six) {
    HidStateManager manager;
    for (std::uint8_t usage = kA; usage < kA + kMaxKeys; ++usage) {
        CHECK_EQ(manager.physical_key(Target::Pc1, usage, true), HidResult::Ok);
    }

    CHECK_EQ(manager.physical_key(Target::Pc1, kA + kMaxKeys, true),
             HidResult::KeyCapacity);

    const KeyboardSnapshot keyboard = manager.snapshot(Target::Pc1).keyboard;
    CHECK_EQ(keyboard.key_count, kMaxKeys);
    for (std::uint8_t usage = kA; usage < kA + kMaxKeys; ++usage) {
        CHECK(keyboard.contains(usage));
    }
    CHECK_FALSE(keyboard.contains(kA + kMaxKeys));
}

TEST_CASE(a_rejected_key_is_not_silently_held_for_later) {
    HidStateManager manager;
    for (std::uint8_t usage = kA; usage < kA + kMaxKeys; ++usage) {
        manager.physical_key(Target::Pc1, usage, true);
    }
    manager.physical_key(Target::Pc1, kA + kMaxKeys, true);

    manager.physical_key(Target::Pc1, kA, false);

    CHECK_FALSE(manager.snapshot(Target::Pc1).keyboard.contains(kA + kMaxKeys));
}

TEST_CASE(a_full_keyboard_on_one_computer_leaves_the_other_free) {
    HidStateManager manager;
    for (std::uint8_t usage = kA; usage < kA + kMaxKeys; ++usage) {
        manager.physical_key(Target::Pc1, usage, true);
    }

    CHECK_EQ(manager.physical_key(Target::Pc2, kA, true), HidResult::Ok);
}

TEST_CASE(the_report_lists_its_keys_in_a_stable_order) {
    HidStateManager manager;
    manager.physical_key(Target::Pc1, kC, true);
    manager.physical_key(Target::Pc1, kA, true);
    manager.physical_key(Target::Pc1, kB, true);

    const KeyboardSnapshot keyboard = manager.snapshot(Target::Pc1).keyboard;

    // Sorted, so an unchanged set of held keys always produces identical
    // report bytes and never looks like a change to the host.
    CHECK_EQ(keyboard.keys[0], kA);
    CHECK_EQ(keyboard.keys[1], kB);
    CHECK_EQ(keyboard.keys[2], kC);
}

// ----------------------------------------------------------------- modifiers

TEST_CASE(modifiers_are_held_and_released_by_owner_too) {
    HidStateManager manager;
    constexpr std::uint8_t kLeftShift = 0x02;

    manager.physical_modifiers(Target::Pc1, kLeftShift, true);
    manager.macro_modifiers(1, Target::Pc1, kLeftShift, true);
    manager.physical_modifiers(Target::Pc1, kLeftShift, false);

    CHECK_EQ(manager.snapshot(Target::Pc1).keyboard.modifiers, kLeftShift);

    manager.release_macro(1);

    CHECK_EQ(manager.snapshot(Target::Pc1).keyboard.modifiers, 0u);
}

TEST_CASE(separate_modifier_bits_do_not_release_each_other) {
    HidStateManager manager;
    constexpr std::uint8_t kLeftCtrl = 0x01;
    constexpr std::uint8_t kLeftShift = 0x02;

    manager.physical_modifiers(Target::Pc1, kLeftCtrl | kLeftShift, true);
    manager.physical_modifiers(Target::Pc1, kLeftShift, false);

    CHECK_EQ(manager.snapshot(Target::Pc1).keyboard.modifiers, kLeftCtrl);
}

// --------------------------------------------------------------------- mouse

TEST_CASE(mouse_buttons_are_an_absolute_state) {
    HidStateManager manager;

    manager.set_mouse_buttons(Target::Pc2, 0b00000101);

    CHECK_EQ(manager.snapshot(Target::Pc2).mouse.buttons, 0b00000101u);
}

TEST_CASE(mouse_deltas_accumulate_until_they_are_taken) {
    HidStateManager manager;

    manager.mouse_delta(Target::Pc1, 3, -4, 1, 0);
    manager.mouse_delta(Target::Pc1, 5, 2, 0, -1);

    const MouseSnapshot mouse = manager.snapshot(Target::Pc1).mouse;
    CHECK_EQ(mouse.delta_x, 8);
    CHECK_EQ(mouse.delta_y, -2);
    CHECK_EQ(mouse.wheel, 1);
    CHECK_EQ(mouse.pan, -1);
}

TEST_CASE(taking_a_snapshot_does_not_consume_the_movement) {
    HidStateManager manager;
    manager.mouse_delta(Target::Pc1, 3, 0, 0, 0);

    manager.snapshot(Target::Pc1);

    // snapshot() is a look; take_snapshot() is a send. Confusing the two loses
    // motion the moment anything else inspects the state.
    CHECK_EQ(manager.snapshot(Target::Pc1).mouse.delta_x, 3);
}

TEST_CASE(taking_the_report_consumes_the_movement_exactly_once) {
    HidStateManager manager;
    manager.mouse_delta(Target::Pc1, 3, -4, 0, 0);

    const MouseSnapshot sent = manager.take_snapshot(Target::Pc1).mouse;

    CHECK_EQ(sent.delta_x, 3);
    CHECK_EQ(sent.delta_y, -4);
    CHECK_EQ(manager.snapshot(Target::Pc1).mouse.delta_x, 0);
    CHECK_EQ(manager.snapshot(Target::Pc1).mouse.delta_y, 0);
}

TEST_CASE(taking_the_report_leaves_the_held_keys_alone) {
    HidStateManager manager;
    manager.physical_key(Target::Pc1, kA, true);

    manager.take_snapshot(Target::Pc1);

    CHECK(manager.snapshot(Target::Pc1).keyboard.contains(kA));
}

TEST_CASE(movement_saturates_rather_than_wrapping_round) {
    HidStateManager manager;

    for (int step = 0; step < 100; ++step) {
        manager.mouse_delta(Target::Pc1, 1000, -1000, 0, 0);
    }

    const MouseSnapshot mouse = manager.snapshot(Target::Pc1).mouse;
    // A wrap would send the pointer the other way across the screen.
    CHECK_EQ(mouse.delta_x, 32767);
    CHECK_EQ(mouse.delta_y, -32768);
}

TEST_CASE(movement_on_one_computer_does_not_move_the_other_pointer) {
    HidStateManager manager;

    manager.mouse_delta(Target::Pc1, 10, 10, 0, 0);

    CHECK_EQ(manager.snapshot(Target::Pc2).mouse.delta_x, 0);
}

// ------------------------------------------------------------------ releasing

TEST_CASE(release_target_clears_one_computer_completely) {
    HidStateManager manager;
    manager.physical_key(Target::Pc1, kA, true);
    manager.macro_key(1, Target::Pc1, kB, true);
    manager.physical_modifiers(Target::Pc1, 0x02, true);
    manager.set_mouse_buttons(Target::Pc1, 0b111);
    manager.physical_key(Target::Pc2, kC, true);

    manager.release_target(Target::Pc1);

    CHECK_EQ(manager.snapshot(Target::Pc1).keyboard.key_count, 0u);
    CHECK_EQ(manager.snapshot(Target::Pc1).keyboard.modifiers, 0u);
    CHECK_EQ(manager.snapshot(Target::Pc1).mouse.buttons, 0u);
    CHECK(manager.snapshot(Target::Pc2).keyboard.contains(kC));
}

TEST_CASE(release_all_clears_both_computers) {
    HidStateManager manager;
    manager.physical_key(Target::Pc1, kA, true);
    manager.physical_key(Target::Pc2, kB, true);
    manager.set_mouse_buttons(Target::Pc2, 0b1);

    manager.release_all();

    CHECK_EQ(manager.snapshot(Target::Pc1).keyboard.key_count, 0u);
    CHECK_EQ(manager.snapshot(Target::Pc2).keyboard.key_count, 0u);
    CHECK_EQ(manager.snapshot(Target::Pc2).mouse.buttons, 0u);
}

TEST_CASE(release_all_also_forgets_every_macro_that_was_holding_something) {
    HidStateManager manager;
    manager.macro_key(1, Target::Pc1, kA, true);

    manager.release_all();
    // The macro is gone. If its ownership survived, the next thing that
    // pressed this key would be released by a macro that ended long ago.
    manager.physical_key(Target::Pc1, kA, true);
    manager.release_macro(1);

    CHECK(manager.snapshot(Target::Pc1).keyboard.contains(kA));
}

TEST_CASE(release_all_discards_movement_that_was_never_sent) {
    HidStateManager manager;
    manager.mouse_delta(Target::Pc1, 500, 500, 0, 0);

    manager.release_all();

    CHECK_EQ(manager.snapshot(Target::Pc1).mouse.delta_x, 0);
}

TEST_CASE(releasing_a_macro_that_never_ran_changes_nothing) {
    HidStateManager manager;
    manager.physical_key(Target::Pc1, kA, true);

    manager.release_macro(31);

    CHECK(manager.snapshot(Target::Pc1).keyboard.contains(kA));
}

TEST_CASE(a_macro_owner_out_of_range_is_refused_not_wrapped) {
    HidStateManager manager;

    // Wrapping a bad owner index onto a valid one would let one macro release
    // another macro's keys.
    CHECK_EQ(manager.macro_key(255, Target::Pc1, kA, true), HidResult::BadOwner);
    CHECK_EQ(manager.snapshot(Target::Pc1).keyboard.key_count, 0u);
}

TEST_CASE(a_usage_outside_the_report_range_is_refused) {
    HidStateManager manager;

    CHECK_EQ(manager.physical_key(Target::Pc1, 0, true), HidResult::BadUsage);
    CHECK_EQ(manager.snapshot(Target::Pc1).keyboard.key_count, 0u);
}

TEST_CASE(a_button_mask_beyond_the_five_buttons_is_refused) {
    HidStateManager manager;

    CHECK_EQ(manager.set_mouse_buttons(Target::Pc1, 0b11100000), HidResult::BadButton);
    CHECK_EQ(manager.snapshot(Target::Pc1).mouse.buttons, 0u);
}

TEST_CASE(all_five_buttons_are_allowed) {
    HidStateManager manager;

    CHECK_EQ(manager.set_mouse_buttons(Target::Pc1,
                                       static_cast<std::uint8_t>(MouseButton::All)),
             HidResult::Ok);

    CHECK_EQ(manager.snapshot(Target::Pc1).mouse.buttons, 0b00011111u);
}
