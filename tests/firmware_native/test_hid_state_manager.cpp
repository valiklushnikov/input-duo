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

// -------------------------------------------------------------- publication
//
// This class holds a state and not a queue of reports, so a state replaced
// before anyone was told about it is gone: no report was built from it and
// nothing remembers it existed. That is how a macro loses letters, and how it
// loses a release - which strands a key on a computer nobody is watching. The
// flag below is what lets the drain pace itself against the senders, and it
// is owned here, so it is tested here.

TEST_CASE(a_manager_that_holds_nothing_owes_nobody_a_report) {
    HidStateManager manager;

    // Silence is a true account of an empty state. Starting owed would make
    // the first keystroke of every boot wait for a report of nothing.
    CHECK_FALSE(manager.keyboard_unreported(Target::Pc1));
    CHECK_FALSE(manager.keyboard_unreported(Target::Pc2));
}

TEST_CASE(a_press_leaves_its_computer_owed_the_new_state) {
    HidStateManager manager;

    manager.physical_key(Target::Pc1, kA, true);

    CHECK(manager.keyboard_unreported(Target::Pc1));
    // And only that computer. A key that went to PC1 says nothing about what
    // PC2 is holding, and marking PC2 owed would make it pace PC1's typing.
    CHECK_FALSE(manager.keyboard_unreported(Target::Pc2));
}

TEST_CASE(a_release_leaves_its_computer_owed_the_new_state) {
    HidStateManager manager;
    manager.physical_key(Target::Pc1, kA, true);
    manager.keyboard_reported(Target::Pc1);

    manager.physical_key(Target::Pc1, kA, false);

    // The dangerous direction. A press that is never published is a letter
    // nobody typed; a release that is never published is a key held down on a
    // computer the operator may not be looking at.
    CHECK(manager.keyboard_unreported(Target::Pc1));
}

TEST_CASE(a_modifier_is_a_keyboard_change_like_any_other) {
    HidStateManager manager;

    manager.physical_modifiers(Target::Pc2, 0x02, true);

    CHECK(manager.keyboard_unreported(Target::Pc2));
}

TEST_CASE(the_sender_saying_so_is_what_clears_the_debt) {
    HidStateManager manager;
    manager.physical_key(Target::Pc1, kA, true);

    manager.keyboard_reported(Target::Pc1);

    CHECK_FALSE(manager.keyboard_unreported(Target::Pc1));
}

TEST_CASE(one_computer_answering_does_not_answer_for_the_other) {
    HidStateManager manager;
    manager.physical_key(Target::Pc1, kA, true);
    manager.physical_key(Target::Pc2, kA, true);

    manager.keyboard_reported(Target::Pc1);

    // Two computers, two answers. Clearing both here is how a state moves on
    // while one of them has still never seen it.
    CHECK_FALSE(manager.keyboard_unreported(Target::Pc1));
    CHECK(manager.keyboard_unreported(Target::Pc2));
}

TEST_CASE(a_second_owner_joining_a_held_key_changes_no_report) {
    HidStateManager manager;
    manager.physical_key(Target::Pc1, kA, true);
    manager.keyboard_reported(Target::Pc1);

    // The key is already down; a macro taking a share of it costs no report
    // slot and the far computer would see nothing new. Marking it owed would
    // pause the drain for a report identical to the last one.
    manager.macro_key(1, Target::Pc1, kA, true);

    CHECK_FALSE(manager.keyboard_unreported(Target::Pc1));
}

TEST_CASE(a_key_the_report_had_no_room_for_leaves_nothing_owed) {
    HidStateManager manager;
    const std::uint8_t usages[] = {0x04, 0x05, 0x06, 0x07, 0x08, 0x09};
    for (std::uint8_t usage : usages) {
        manager.physical_key(Target::Pc1, usage, true);
    }
    manager.keyboard_reported(Target::Pc1);

    CHECK_EQ(manager.physical_key(Target::Pc1, 0x0A, true), HidResult::KeyCapacity);

    // Refused, so nothing was recorded and no report would differ. A debt
    // here would be a wait for a state that does not exist.
    CHECK_FALSE(manager.keyboard_unreported(Target::Pc1));
}

TEST_CASE(mouse_movement_owes_the_keyboard_nothing) {
    HidStateManager manager;

    manager.mouse_delta(Target::Pc1, 5, -3, 0, 0);
    manager.set_mouse_buttons(Target::Pc1, 0x01);

    // The pointer must never wait for typing, and it must not make typing
    // wait either. Neither says anything about which keys are down.
    CHECK_FALSE(manager.keyboard_unreported(Target::Pc1));
}

TEST_CASE(letting_go_of_everything_is_the_state_that_most_needs_publishing) {
    HidStateManager manager;
    manager.physical_key(Target::Pc1, kA, true);
    manager.physical_key(Target::Pc2, kB, true);
    manager.keyboard_reported(Target::Pc1);
    manager.keyboard_reported(Target::Pc2);

    manager.release_all();

    // STOP AND RELEASE ALL, a lost link, a reset. If this one is replaced
    // before it goes out, a key stays down on a computer with nothing left
    // driving it.
    CHECK(manager.keyboard_unreported(Target::Pc1));
    CHECK(manager.keyboard_unreported(Target::Pc2));
}

TEST_CASE(releasing_one_computer_leaves_the_other_answer_standing) {
    HidStateManager manager;
    manager.physical_key(Target::Pc1, kA, true);
    manager.physical_key(Target::Pc2, kB, true);
    manager.keyboard_reported(Target::Pc1);
    manager.keyboard_reported(Target::Pc2);

    manager.release_target(Target::Pc1);

    CHECK(manager.keyboard_unreported(Target::Pc1));
    CHECK_FALSE(manager.keyboard_unreported(Target::Pc2));
}

TEST_CASE(a_macro_ending_is_announced_even_if_it_was_holding_nothing) {
    HidStateManager manager;
    manager.physical_key(Target::Pc1, kA, true);
    manager.keyboard_reported(Target::Pc1);
    manager.keyboard_reported(Target::Pc2);

    manager.release_macro(7);

    // Announced rather than worked out. The sender answers a state it already
    // has by saying so, which costs one comparison; deciding here what
    // actually moved costs a scan of every usage on both computers.
    CHECK(manager.keyboard_unreported(Target::Pc1));
    CHECK(manager.keyboard_unreported(Target::Pc2));
}
