// What a keystroke turns into once the operator has had their say.
//
// Most input passes straight through. A few keys are bound to something, and
// the two ways of binding differ in exactly one respect: Replace swallows the
// key that triggered it, Add lets it through as well. Getting that backwards
// means either a key that does nothing visible or a key that does its job and
// types a character nobody wanted.
//
// The dangerous part is changing where input goes. Whatever is held down at
// that moment is held on the computer being left behind, and that computer
// will never hear about it again - so it has to be released before the switch,
// and the physical key that is still down must not arrive on the new computer
// as a fresh press. A modifier stuck on the machine you just left changes what
// every later keystroke there means, and you cannot fix it from here.

#include "mapping/engine.hpp"
#include "mapping/routes.hpp"
#include "input/source_table.hpp"
#include "test_support.hpp"

using duo_input::config::ActionKind;
using duo_input::config::BindingMode;
using duo_input::config::KeyboardRoute;
using duo_input::config::MouseRoute;
using duo_input::config::TriggerKind;
using duo_input::hid::Target;
using duo_input::u1::input::InputEvent;
using duo_input::u1::input::InputEventKind;
using duo_input::u1::mapping::ActionRequestKind;
using duo_input::u1::mapping::Binding;
using duo_input::u1::mapping::BindingEngine;
using duo_input::u1::mapping::Outcome;

TEST_CASE(equal_physical_inputs_on_two_sources_release_only_after_the_last_source) {
    for (auto down : {InputEventKind::KeyDown, InputEventKind::MouseButtonDown}) {
        const auto up = down == InputEventKind::KeyDown ? InputEventKind::KeyUp : InputEventKind::MouseButtonUp;
        for (std::uint8_t first : {0, 1}) {
            BindingEngine engine;
            InputEvent a; a.kind = down; a.code = 1; a.source_index = 0;
            InputEvent b = a; b.source_index = 1;
            CHECK(engine.handle(a).count == 1);
            CHECK(engine.handle(b).count == 0);
            a.kind = b.kind = up;
            CHECK(engine.handle(first == 0 ? a : b).count == 0);
            CHECK(engine.handle(first == 0 ? b : a).count == 1);
        }
    }
}

TEST_CASE(equal_modifiers_on_two_sources_remain_held_and_legacy_bindings_keep_first_match) {
    BindingEngine engine;
    Binding first; first.code = 0x4F; first.required_modifiers = 1;
    first.mode = BindingMode::REPLACE; first.action = ActionKind::RUN_MACRO; first.parameter = 11;
    Binding second = first; second.required_modifiers = 0; second.parameter = 22;
    engine.set_bindings({first, second});
    InputEvent ctrl; ctrl.kind = InputEventKind::KeyDown; ctrl.code = 0xE0;
    engine.handle(ctrl); ctrl.source_index = 1; engine.handle(ctrl);
    ctrl.kind = InputEventKind::KeyUp; ctrl.source_index = 0; engine.handle(ctrl);
    InputEvent arrow; arrow.kind = InputEventKind::KeyDown; arrow.code = 0x4F; arrow.source_index = 1;
    const auto outcome = engine.handle(arrow);
    CHECK(outcome.count == 1);
    CHECK(outcome.actions[0].kind == ActionRequestKind::RunMacro);
    CHECK(outcome.actions[0].parameter == 11);
}

namespace {

InputEvent key(InputEventKind kind, std::uint16_t usage) {
    InputEvent event;
    event.kind = kind;
    event.code = usage;
    return event;
}

InputEvent motion(std::int16_t x, std::int16_t y) {
    InputEvent event;
    event.kind = InputEventKind::MouseMove;
    event.x = x;
    event.y = y;
    return event;
}

Binding bound(std::uint16_t usage, BindingMode mode, ActionKind action,
              std::uint8_t parameter = 0, std::uint8_t modifiers = 0) {
    Binding binding;
    binding.trigger = TriggerKind::KEYBOARD_USAGE;
    binding.code = usage;
    binding.required_modifiers = modifiers;
    binding.mode = mode;
    binding.action = action;
    binding.parameter = parameter;
    return binding;
}

int count_of(const Outcome& outcome, ActionRequestKind kind) {
    int seen = 0;
    for (std::size_t index = 0; index < outcome.count; ++index) {
        if (outcome.actions[index].kind == kind) {
            ++seen;
        }
    }
    return seen;
}

bool sends_input(const Outcome& outcome) {
    return count_of(outcome, ActionRequestKind::SendInput) > 0;
}

struct SourceFixture : duo_input::u1::input::IInputHandler {
    duo_input::u1::input::SourceTable sources{*this};
    void on_input(const InputEvent&, std::uint32_t) override {}

    void attach(std::uint8_t id, std::uint16_t vid, std::uint16_t pid,
                std::uint8_t interface_number) {
        duo_input::u1::input::SourceEvent event;
        event.kind = duo_input::u1::input::SourceEventKind::Ready;
        event.source_id = id;
        duo_input::u1::input::SourceIdentity identity;
        identity.vendor_id = vid;
        identity.product_id = pid;
        identity.interface_number = interface_number;
        sources.on_event(event, identity, 0);
    }
};

Binding source_bound(std::uint8_t macro, std::uint16_t vid, std::uint16_t pid,
                     std::uint8_t interface_number) {
    Binding binding = bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, macro);
    binding.source = {vid, pid, interface_number};
    return binding;
}

void check_macros(const Outcome& outcome, std::initializer_list<std::uint8_t> expected) {
    CHECK_EQ(outcome.count, expected.size());
    std::size_t index = 0;
    for (std::uint8_t macro : expected) {
        if (index >= outcome.count) break;
        CHECK(outcome.actions[index].kind == ActionRequestKind::RunMacro);
        CHECK_EQ(outcome.actions[index].parameter, macro);
        ++index;
    }
}

}  // namespace

TEST_CASE(legacy_unqualified_bindings_keep_first_match_precedence) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, 1),
                         bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, 3)});
    const Outcome outcome = engine.handle(key(InputEventKind::KeyDown, 0x3D));
    check_macros(outcome, {1});
}

TEST_CASE(consumer_binding_matches_the_consumer_page_and_full_u16_code) {
    BindingEngine engine;
    Binding binding = bound(0x1B1, BindingMode::REPLACE, ActionKind::RUN_MACRO, 3);
    binding.trigger = static_cast<TriggerKind>(3);
    engine.set_bindings({binding});
    CHECK(sends_input(engine.handle(key(InputEventKind::KeyDown, 0x1B1))));
    check_macros(engine.handle(key(InputEventKind::ConsumerDown, 0x1B1)), {3});
    CHECK(engine.handle(key(InputEventKind::ConsumerUp, 0x1B1)).count == 0);
    CHECK(sends_input(engine.handle(key(InputEventKind::ConsumerDown, 0xB1))));
}

TEST_CASE(source_a_fires_its_binding_and_any_source_but_never_source_b) {
    SourceFixture fixture;
    fixture.attach(41, 0x1234, 0x5678, 0);
    fixture.attach(42, 0x1234, 0x5678, 1);
    BindingEngine engine;
    engine.set_sources(fixture.sources);
    engine.set_bindings({source_bound(1, 0x1234, 0x5678, 0),
                         source_bound(2, 0x1234, 0x5678, 1),
                         source_bound(3, 0, 0, 0)});
    InputEvent event = key(InputEventKind::KeyDown, 0x3D);
    event.source_index = 0;
    check_macros(engine.handle(event), {1, 3});
    event.kind = InputEventKind::KeyUp;
    CHECK_EQ(engine.handle(event).count, 0u);
    event.kind = InputEventKind::KeyDown;
    event.source_index = 1;
    check_macros(engine.handle(event), {2, 3});
}

TEST_CASE(unresolved_sources_fire_only_the_any_source_binding) {
    SourceFixture fixture;
    fixture.attach(41, 0x1234, 0x5678, 0);
    for (std::uint8_t index : {std::uint8_t{1}, std::uint8_t{255}}) {
        BindingEngine engine;
        engine.set_sources(fixture.sources);
        engine.set_bindings({source_bound(1, 0x1234, 0x5678, 0),
                             source_bound(2, 0x1234, 0x5678, 1),
                             source_bound(3, 0, 0, 0)});
        InputEvent event = key(InputEventKind::KeyDown, 0x3D);
        event.source_index = index;
        check_macros(engine.handle(event), {3});
    }
    BindingEngine without_table;
    without_table.set_bindings({source_bound(1, 0x1234, 0x5678, 0),
                                source_bound(3, 0, 0, 0)});
    check_macros(without_table.handle(key(InputEventKind::KeyDown, 0x3D)), {3});
}

TEST_CASE(source_matching_checks_each_identity_field_and_only_all_zero_means_any) {
    SourceFixture fixture;
    fixture.attach(41, 0x1234, 0x5678, 0);
    BindingEngine engine;
    engine.set_sources(fixture.sources);
    engine.set_bindings({source_bound(1, 0x1235, 0x5678, 0),
                         source_bound(2, 0x1234, 0x5679, 0),
                         source_bound(3, 0x1234, 0x5678, 1),
                         source_bound(4, 0, 0, 1),
                         source_bound(5, 0, 0, 0)});
    InputEvent event = key(InputEventKind::KeyDown, 0x3D);
    event.source_index = 0;
    check_macros(engine.handle(event), {5});
}

TEST_CASE(multiple_add_bindings_send_one_physical_press_and_release) {
    SourceFixture fixture; fixture.attach(41, 0x1234, 0x5678, 0);
    BindingEngine engine;
    engine.set_sources(fixture.sources);
    Binding qualified = source_bound(2, 0x1234, 0x5678, 0);
    qualified.mode = BindingMode::ADD;
    engine.set_bindings({bound(0x3D, BindingMode::ADD, ActionKind::RUN_MACRO, 1),
                         qualified});
    const Outcome outcome = engine.handle(key(InputEventKind::KeyDown, 0x3D));
    CHECK_EQ(count_of(outcome, ActionRequestKind::RunMacro), 2);
    CHECK_EQ(count_of(outcome, ActionRequestKind::SendInput), 1);
    CHECK_EQ(engine.handle(key(InputEventKind::KeyDown, 0x3D)).count, 0u);
    CHECK_EQ(count_of(engine.handle(key(InputEventKind::KeyUp, 0x3D)),
                      ActionRequestKind::SendInput), 1);
}

TEST_CASE(any_matching_replace_suppresses_the_physical_press_and_release) {
    SourceFixture fixture; fixture.attach(41, 0x1234, 0x5678, 0);
    BindingEngine engine;
    engine.set_sources(fixture.sources);
    engine.set_bindings({bound(0x3D, BindingMode::ADD, ActionKind::RUN_MACRO, 1),
                         source_bound(2, 0x1234, 0x5678, 0)});
    check_macros(engine.handle(key(InputEventKind::KeyDown, 0x3D)), {1, 2});
    CHECK_EQ(engine.handle(key(InputEventKind::KeyUp, 0x3D)).count, 0u);
}

TEST_CASE(full_outcomes_stop_before_a_route_change_that_cannot_release_both_targets) {
    BindingEngine engine;
    engine.set_keyboard_route(KeyboardRoute::BOTH);
    engine.set_bindings({bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, 1),
                         bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, 2),
                         bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, 3),
                         bound(0x3D, BindingMode::REPLACE, ActionKind::TOGGLE_KEYBOARD_ROUTE),
                         bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, 4)});
    check_macros(engine.handle(key(InputEventKind::KeyDown, 0x3D)), {1});
    CHECK(engine.keyboard_route() == KeyboardRoute::BOTH);
}

TEST_CASE(full_outcomes_do_not_partially_release_for_a_profile_change) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, 1),
                         bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, 2),
                         bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, 3),
                         bound(0x3D, BindingMode::REPLACE, ActionKind::SET_PROFILE, 2)});
    check_macros(engine.handle(key(InputEventKind::KeyDown, 0x3D)), {1});
}

TEST_CASE(multiple_matches_keep_the_existing_action_capacity) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, 1),
                         bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, 2),
                         bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, 3),
                         bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, 4),
                         bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, 5)});
    check_macros(engine.handle(key(InputEventKind::KeyDown, 0x3D)), {1});
}

// ------------------------------------------------------------ pass-through

TEST_CASE(an_unbound_key_goes_straight_through) {
    BindingEngine engine;

    const Outcome outcome = engine.handle(key(InputEventKind::KeyDown, 0x04));

    CHECK(sends_input(outcome));
    CHECK_EQ(count_of(outcome, ActionRequestKind::RunMacro), 0);
}

TEST_CASE(mouse_movement_goes_straight_through) {
    BindingEngine engine;

    const Outcome outcome = engine.handle(motion(3, -4));

    CHECK(sends_input(outcome));
}

// ------------------------------------------------------------- the two modes

TEST_CASE(replace_swallows_the_key_that_triggered_it) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, 2)});

    const Outcome outcome = engine.handle(key(InputEventKind::KeyDown, 0x3D));

    // The whole point of Replace: F4 runs the macro *instead of* typing F4.
    CHECK(!sends_input(outcome));
    CHECK_EQ(count_of(outcome, ActionRequestKind::RunMacro), 1);
}

TEST_CASE(add_lets_the_key_through_as_well) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3D, BindingMode::ADD, ActionKind::RUN_MACRO, 2)});

    const Outcome outcome = engine.handle(key(InputEventKind::KeyDown, 0x3D));

    CHECK(sends_input(outcome));
    CHECK_EQ(count_of(outcome, ActionRequestKind::RunMacro), 1);
}

TEST_CASE(the_release_of_a_replaced_key_is_swallowed_too) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, 2)});
    engine.handle(key(InputEventKind::KeyDown, 0x3D));

    const Outcome outcome = engine.handle(key(InputEventKind::KeyUp, 0x3D));

    // Letting the release through without the press would leave the far side
    // releasing a key it never saw pressed.
    CHECK(!sends_input(outcome));
}

TEST_CASE(a_held_key_fires_once) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, 2)});
    engine.handle(key(InputEventKind::KeyDown, 0x3D));

    const Outcome outcome = engine.handle(key(InputEventKind::KeyDown, 0x3D));

    // A keyboard resends its state constantly. A finger resting on a key is
    // one intention, not forty macros a second.
    CHECK_EQ(count_of(outcome, ActionRequestKind::RunMacro), 0);
}

TEST_CASE(a_key_pressed_again_after_release_fires_again) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, 2)});
    engine.handle(key(InputEventKind::KeyDown, 0x3D));
    engine.handle(key(InputEventKind::KeyUp, 0x3D));

    const Outcome outcome = engine.handle(key(InputEventKind::KeyDown, 0x3D));

    CHECK_EQ(count_of(outcome, ActionRequestKind::RunMacro), 1);
}

// -------------------------------------------------- modifiers as conditions

TEST_CASE(a_binding_that_wants_a_modifier_does_not_fire_without_it) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, 2, 0x02)});

    const Outcome outcome = engine.handle(key(InputEventKind::KeyDown, 0x3D));

    // Unbound in this state, so it types F4 as it normally would.
    CHECK(sends_input(outcome));
    CHECK_EQ(count_of(outcome, ActionRequestKind::RunMacro), 0);
}

TEST_CASE(a_binding_that_wants_a_modifier_fires_with_it) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3D, BindingMode::REPLACE, ActionKind::RUN_MACRO, 2, 0x02)});
    engine.handle(key(InputEventKind::KeyDown, 0xE1));  // left shift

    const Outcome outcome = engine.handle(key(InputEventKind::KeyDown, 0x3D));

    CHECK(!sends_input(outcome));
    CHECK_EQ(count_of(outcome, ActionRequestKind::RunMacro), 1);
}

// -------------------------------------------------------------- the routes

TEST_CASE(the_keyboard_starts_on_the_first_computer) {
    BindingEngine engine;

    CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(KeyboardRoute::PC1));
    CHECK_EQ(static_cast<int>(engine.mouse_route()), static_cast<int>(MouseRoute::PC1));
}

TEST_CASE(a_binding_can_move_the_keyboard) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3E, BindingMode::REPLACE, ActionKind::SET_KEYBOARD_ROUTE,
                               static_cast<std::uint8_t>(KeyboardRoute::PC2))});

    engine.handle(key(InputEventKind::KeyDown, 0x3E));

    CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(KeyboardRoute::PC2));
}

TEST_CASE(the_keyboard_can_go_to_both_computers) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3E, BindingMode::REPLACE, ActionKind::SET_KEYBOARD_ROUTE,
                               static_cast<std::uint8_t>(KeyboardRoute::BOTH))});

    engine.handle(key(InputEventKind::KeyDown, 0x3E));

    CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(KeyboardRoute::BOTH));
}

TEST_CASE(the_mouse_can_never_go_to_both) {
    BindingEngine engine;
    // BOTH is not a mouse route. A pointer on two computers at once follows
    // neither, and the operator has no way to tell which one they are aiming.
    engine.set_bindings({bound(0x3F, BindingMode::REPLACE, ActionKind::SET_MOUSE_ROUTE,
                               static_cast<std::uint8_t>(KeyboardRoute::BOTH))});

    engine.handle(key(InputEventKind::KeyDown, 0x3F));

    CHECK_EQ(static_cast<int>(engine.mouse_route()), static_cast<int>(MouseRoute::PC1));
}

TEST_CASE(toggling_moves_the_keyboard_and_toggling_again_moves_it_back) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3E, BindingMode::REPLACE, ActionKind::TOGGLE_KEYBOARD_ROUTE)});

    engine.handle(key(InputEventKind::KeyDown, 0x3E));
    const auto moved = engine.keyboard_route();
    engine.handle(key(InputEventKind::KeyUp, 0x3E));
    engine.handle(key(InputEventKind::KeyDown, 0x3E));

    CHECK_EQ(static_cast<int>(moved), static_cast<int>(KeyboardRoute::PC2));
    CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(KeyboardRoute::PC1));
}

TEST_CASE(toggling_out_of_both_lands_where_the_pointer_is) {
    // The cursor is the only thing telling the operator which computer they
    // are working on. A keyboard leaving BOTH for anywhere else lands on the
    // machine they are not looking at.
    for (auto mouse : {MouseRoute::PC1, MouseRoute::PC2}) {
        BindingEngine engine;
        engine.set_bindings({bound(0x3E, BindingMode::REPLACE, ActionKind::SET_KEYBOARD_ROUTE,
                                   static_cast<std::uint8_t>(KeyboardRoute::BOTH)),
                             bound(0x3F, BindingMode::REPLACE, ActionKind::SET_MOUSE_ROUTE,
                                   static_cast<std::uint8_t>(mouse)),
                             bound(0x40, BindingMode::REPLACE, ActionKind::TOGGLE_KEYBOARD_ROUTE)});

        engine.handle(key(InputEventKind::KeyDown, 0x3F));
        engine.handle(key(InputEventKind::KeyDown, 0x3E));
        CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(KeyboardRoute::BOTH));

        engine.handle(key(InputEventKind::KeyDown, 0x40));

        // The old unconditional flip out of BOTH also landed on PC1, so the
        // PC1 iteration passes under either rule and proves nothing by
        // itself - it is here to state the rule in full, not to verify it.
        // PC2 is the iteration that actually distinguishes "follows the
        // pointer" from "always PC1".
        const KeyboardRoute expected =
            mouse == MouseRoute::PC1 ? KeyboardRoute::PC1 : KeyboardRoute::PC2;
        CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(expected));
    }
}

TEST_CASE(routes_translate_between_the_two_devices_on_one_computer) {
    using duo_input::u1::mapping::Routes;
    CHECK_EQ(static_cast<int>(Routes::mouse_beside(KeyboardRoute::PC1)),
             static_cast<int>(MouseRoute::PC1));
    CHECK_EQ(static_cast<int>(Routes::mouse_beside(KeyboardRoute::PC2)),
             static_cast<int>(MouseRoute::PC2));
    CHECK_EQ(static_cast<int>(Routes::keyboard_beside(MouseRoute::PC1)),
             static_cast<int>(KeyboardRoute::PC1));
    CHECK_EQ(static_cast<int>(Routes::keyboard_beside(MouseRoute::PC2)),
             static_cast<int>(KeyboardRoute::PC2));
}

// ------------------------------------------- what is held when the route moves

TEST_CASE(moving_the_keyboard_releases_what_the_old_computer_is_holding) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3E, BindingMode::REPLACE, ActionKind::TOGGLE_KEYBOARD_ROUTE)});
    engine.handle(key(InputEventKind::KeyDown, 0x04));

    const Outcome outcome = engine.handle(key(InputEventKind::KeyDown, 0x3E));

    // The computer being left behind will never hear about that key again.
    CHECK_EQ(count_of(outcome, ActionRequestKind::ReleaseTarget), 1);
}

TEST_CASE(a_key_still_held_across_a_route_change_does_not_press_on_the_new_computer) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3E, BindingMode::REPLACE, ActionKind::TOGGLE_KEYBOARD_ROUTE)});
    engine.handle(key(InputEventKind::KeyDown, 0x04));
    engine.handle(key(InputEventKind::KeyDown, 0x3E));

    // The keyboard keeps reporting the key as down, because a finger is on it.
    const Outcome outcome = engine.handle(key(InputEventKind::KeyDown, 0x04));

    // It belongs to the computer it was pressed on. Transferring it types a
    // character on the new one that nobody asked for.
    CHECK(!sends_input(outcome));
}

TEST_CASE(the_release_of_a_transferred_key_is_swallowed) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3E, BindingMode::REPLACE, ActionKind::TOGGLE_KEYBOARD_ROUTE)});
    engine.handle(key(InputEventKind::KeyDown, 0x04));
    engine.handle(key(InputEventKind::KeyDown, 0x3E));

    const Outcome outcome = engine.handle(key(InputEventKind::KeyUp, 0x04));

    // The new computer never saw it pressed, so it must not see it released.
    CHECK(!sends_input(outcome));
}

TEST_CASE(a_key_pressed_after_the_route_change_works_normally) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3E, BindingMode::REPLACE, ActionKind::TOGGLE_KEYBOARD_ROUTE)});
    engine.handle(key(InputEventKind::KeyDown, 0x04));
    engine.handle(key(InputEventKind::KeyDown, 0x3E));
    engine.handle(key(InputEventKind::KeyUp, 0x04));

    const Outcome outcome = engine.handle(key(InputEventKind::KeyDown, 0x04));

    CHECK(sends_input(outcome));
}

TEST_CASE(moving_the_mouse_route_releases_its_buttons_on_the_old_computer) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3F, BindingMode::REPLACE, ActionKind::TOGGLE_MOUSE_ROUTE)});
    InputEvent button;
    button.kind = InputEventKind::MouseButtonDown;
    button.code = 0;
    engine.handle(button);

    const Outcome outcome = engine.handle(key(InputEventKind::KeyDown, 0x3F));

    // A mouse button left down on the computer you walked away from selects
    // everything the pointer passes over on the one you went to.
    CHECK_EQ(count_of(outcome, ActionRequestKind::ReleaseTarget), 1);
}

TEST_CASE(the_two_routes_move_independently) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3E, BindingMode::REPLACE, ActionKind::TOGGLE_KEYBOARD_ROUTE)});

    engine.handle(key(InputEventKind::KeyDown, 0x3E));

    // Typing on one computer while pointing at the other is the whole feature.
    CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(KeyboardRoute::PC2));
    CHECK_EQ(static_cast<int>(engine.mouse_route()), static_cast<int>(MouseRoute::PC1));
}

// ----------------------------------------------------------------- profiles

TEST_CASE(a_binding_can_change_the_profile) {
    BindingEngine engine;
    engine.set_bindings({bound(0x40, BindingMode::REPLACE, ActionKind::SET_PROFILE, 3)});

    const Outcome outcome = engine.handle(key(InputEventKind::KeyDown, 0x40));

    CHECK_EQ(count_of(outcome, ActionRequestKind::SetProfile), 1);
}

TEST_CASE(changing_profile_also_releases_what_is_held) {
    BindingEngine engine;
    engine.set_bindings({bound(0x40, BindingMode::REPLACE, ActionKind::SET_PROFILE, 3)});
    engine.handle(key(InputEventKind::KeyDown, 0x04));

    const Outcome outcome = engine.handle(key(InputEventKind::KeyDown, 0x40));

    // The new profile may bind that key to something else entirely, and the
    // far side is still holding it under the old meaning.
    CHECK_EQ(count_of(outcome, ActionRequestKind::ReleaseTarget), 1);
}

// -------------------------------------------------------------- everything off

TEST_CASE(releasing_everything_clears_both_computers) {
    BindingEngine engine;
    engine.handle(key(InputEventKind::KeyDown, 0x04));

    const Outcome outcome = engine.release_everything();

    // What the emergency control does. It has to reach both, because the
    // operator cannot see which one is holding what.
    CHECK_EQ(count_of(outcome, ActionRequestKind::ReleaseTarget), 2);
}

TEST_CASE(nothing_held_after_releasing_everything_blocks_a_fresh_press) {
    BindingEngine engine;
    engine.handle(key(InputEventKind::KeyDown, 0x04));
    engine.release_everything();
    engine.handle(key(InputEventKind::KeyUp, 0x04));

    const Outcome outcome = engine.handle(key(InputEventKind::KeyDown, 0x04));

    CHECK(sends_input(outcome));
}

// ------------------------------------------------- a thousand switches

TEST_CASE(a_thousand_route_changes_leave_nothing_held_anywhere) {
    // The failure this is looking for does not show up in ones and twos. A
    // key that transfers to the wrong computer, or a release that goes
    // missing, needs the route to move while a particular combination happens
    // to be down - and somebody using this device switches all day.
    BindingEngine engine;
    engine.set_bindings({bound(0x3E, BindingMode::REPLACE, ActionKind::TOGGLE_KEYBOARD_ROUTE),
                         bound(0x3F, BindingMode::REPLACE, ActionKind::TOGGLE_MOUSE_ROUTE)});

    // A deterministic stand-in for randomness, so a failure can be repeated.
    std::uint32_t seed = 0x1234567u;
    auto next = [&seed]() {
        seed = seed * 1664525u + 1013904223u;
        return seed >> 16;
    };

    int sends = 0;
    int releases = 0;
    for (int round = 0; round < 1000; ++round) {
        const std::uint16_t usage = static_cast<std::uint16_t>(0x04 + (next() % 20));
        engine.handle(key(InputEventKind::KeyDown, usage));

        InputEvent button;
        button.kind = InputEventKind::MouseButtonDown;
        button.code = static_cast<std::uint16_t>(next() % 5);
        engine.handle(button);

        const Outcome moved =
            engine.handle(key(InputEventKind::KeyDown, (next() % 2) == 0 ? 0x3E : 0x3F));
        releases += count_of(moved, ActionRequestKind::ReleaseTarget);

        // Everything goes up again, the way fingers eventually do.
        engine.handle(key(InputEventKind::KeyUp, usage));
        InputEvent up = button;
        up.kind = InputEventKind::MouseButtonUp;
        sends += count_of(engine.handle(up), ActionRequestKind::SendInput);
        engine.handle(key(InputEventKind::KeyUp, 0x3E));
        engine.handle(key(InputEventKind::KeyUp, 0x3F));
    }

    // Every switch released the computer being left; nothing accumulated.
    CHECK(releases >= 1000);

    const Outcome after = engine.release_everything();
    CHECK_EQ(count_of(after, ActionRequestKind::ReleaseTarget), 2);

    // And the engine still works normally afterwards.
    engine.handle(key(InputEventKind::KeyUp, 0x04));
    CHECK(sends_input(engine.handle(key(InputEventKind::KeyDown, 0x04))));
}

TEST_CASE(the_mouse_is_never_on_both_however_much_it_is_switched) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3F, BindingMode::REPLACE, ActionKind::TOGGLE_MOUSE_ROUTE),
                         bound(0x40, BindingMode::REPLACE, ActionKind::SET_MOUSE_ROUTE,
                               static_cast<std::uint8_t>(KeyboardRoute::BOTH))});

    for (int round = 0; round < 1000; ++round) {
        engine.handle(key(InputEventKind::KeyDown, (round % 2) == 0 ? 0x3F : 0x40));
        engine.handle(key(InputEventKind::KeyUp, (round % 2) == 0 ? 0x3F : 0x40));

        const auto route = engine.mouse_route();
        CHECK(route == MouseRoute::PC1 || route == MouseRoute::PC2);
    }
}

TEST_CASE(a_key_is_never_pressed_twice_on_the_far_side_without_a_release) {
    // The failure that would be silently wrong: a key transferred across a
    // route change arrives as a second press on a computer that already thinks
    // it is down, and starts repeating the character.
    //
    // What clears a press is not always its own release. A route change sends
    // ReleaseTarget, which lets go of everything at once, and the individual
    // release is then deliberately swallowed - so the invariant is about
    // either of those having happened, not about counting ups against downs.
    BindingEngine engine;
    engine.set_bindings({bound(0x3E, BindingMode::REPLACE, ActionKind::TOGGLE_KEYBOARD_ROUTE)});

    bool down_on_far_side = false;
    auto walk = [&down_on_far_side](const Outcome& outcome) {
        for (std::size_t index = 0; index < outcome.count; ++index) {
            const auto& action = outcome.actions[index];
            if (action.kind == ActionRequestKind::ReleaseTarget) {
                down_on_far_side = false;
            } else if (action.kind == ActionRequestKind::SendInput) {
                if (action.event.kind == InputEventKind::KeyDown && action.event.code == 0x04) {
                    CHECK(!down_on_far_side);
                    down_on_far_side = true;
                } else if (action.event.kind == InputEventKind::KeyUp &&
                           action.event.code == 0x04) {
                    down_on_far_side = false;
                }
            }
        }
    };

    for (int round = 0; round < 500; ++round) {
        walk(engine.handle(key(InputEventKind::KeyDown, 0x04)));
        walk(engine.handle(key(InputEventKind::KeyDown, 0x3E)));
        walk(engine.handle(key(InputEventKind::KeyUp, 0x3E)));
        walk(engine.handle(key(InputEventKind::KeyUp, 0x04)));
    }

    // And after the last release nothing is left pressed anywhere.
    walk(engine.release_everything());
    CHECK(!down_on_far_side);
}

namespace {

/// Drive an engine to a given pair of routes, then apply one switch.
///
/// The bindings are fixed: 0x3E sets the keyboard, 0x3F sets the mouse, 0x40
/// toggles the keyboard and 0x41 toggles the mouse.
BindingEngine synchronised_engine(KeyboardRoute keyboard, MouseRoute mouse) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3E, BindingMode::REPLACE, ActionKind::SET_KEYBOARD_ROUTE,
                               static_cast<std::uint8_t>(keyboard)),
                         bound(0x3F, BindingMode::REPLACE, ActionKind::SET_MOUSE_ROUTE,
                               static_cast<std::uint8_t>(mouse)),
                         bound(0x40, BindingMode::REPLACE, ActionKind::TOGGLE_KEYBOARD_ROUTE),
                         bound(0x41, BindingMode::REPLACE, ActionKind::TOGGLE_MOUSE_ROUTE)});
    // Placed while synchronisation is off, so the starting pair is exactly
    // what the table asks for - including the two diverged rows, which is the
    // state switching the mode on over parted routes leaves behind.
    engine.set_synchronised_control(false);
    engine.handle(key(InputEventKind::KeyDown, 0x3F));
    engine.handle(key(InputEventKind::KeyUp, 0x3F));
    engine.handle(key(InputEventKind::KeyDown, 0x3E));
    engine.handle(key(InputEventKind::KeyUp, 0x3E));
    engine.set_synchronised_control(true);
    return engine;
}

}  // namespace

TEST_CASE(synchronised_switching_puts_both_devices_on_one_computer) {
    struct Row {
        KeyboardRoute keyboard;
        MouseRoute mouse;
        std::uint16_t press;
        KeyboardRoute expect_keyboard;
        MouseRoute expect_mouse;
    };
    // Seven of the design's eight rows, row for row. The eighth
    // (K=PC1 M=PC1 + set keyboard BOTH -> K=BOTH M=PC1) is covered by
    // synchronised_control_leaves_the_mouse_alone_when_the_keyboard_goes_to_both
    // below, as its own dedicated test rather than a row here.
    const Row rows[] = {
        {KeyboardRoute::PC1, MouseRoute::PC1, 0x40, KeyboardRoute::PC2, MouseRoute::PC2},
        {KeyboardRoute::PC1, MouseRoute::PC1, 0x41, KeyboardRoute::PC2, MouseRoute::PC2},
        {KeyboardRoute::PC1, MouseRoute::PC2, 0x40, KeyboardRoute::PC2, MouseRoute::PC2},
        {KeyboardRoute::PC2, MouseRoute::PC1, 0x41, KeyboardRoute::PC2, MouseRoute::PC2},
        {KeyboardRoute::BOTH, MouseRoute::PC1, 0x40, KeyboardRoute::PC1, MouseRoute::PC1},
        {KeyboardRoute::BOTH, MouseRoute::PC2, 0x40, KeyboardRoute::PC2, MouseRoute::PC2},
        {KeyboardRoute::BOTH, MouseRoute::PC1, 0x41, KeyboardRoute::PC2, MouseRoute::PC2},
    };

    for (const Row& row : rows) {
        BindingEngine engine = synchronised_engine(row.keyboard, row.mouse);
        engine.handle(key(InputEventKind::KeyDown, row.press));

        CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(row.expect_keyboard));
        CHECK_EQ(static_cast<int>(engine.mouse_route()), static_cast<int>(row.expect_mouse));
    }
}

TEST_CASE(synchronised_control_leaves_the_mouse_alone_when_the_keyboard_goes_to_both) {
    // BOTH is the one pause. There is no mouse route that could follow the
    // keyboard there, and inventing one would put the pointer on two computers
    // where it follows neither.
    BindingEngine engine = synchronised_engine(KeyboardRoute::PC1, MouseRoute::PC1);
    engine.set_bindings({bound(0x42, BindingMode::REPLACE, ActionKind::SET_KEYBOARD_ROUTE,
                               static_cast<std::uint8_t>(KeyboardRoute::BOTH))});

    engine.handle(key(InputEventKind::KeyDown, 0x42));

    CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(KeyboardRoute::BOTH));
    CHECK_EQ(static_cast<int>(engine.mouse_route()), static_cast<int>(MouseRoute::PC1));
}

TEST_CASE(synchronised_control_leaves_a_diverged_mouse_alone_when_the_keyboard_goes_to_both) {
    // The case above cannot see this guard: it starts with the mouse on PC1,
    // and mouse_beside(BOTH) answers PC1, so a build with the guard deleted
    // writes the value that was already there. With the pointer on the far
    // computer the difference is visible - the keyboard reaches BOTH either
    // way, and only an unguarded build drags the mouse back to PC1.
    BindingEngine engine = synchronised_engine(KeyboardRoute::PC1, MouseRoute::PC2);
    engine.set_bindings({bound(0x42, BindingMode::REPLACE, ActionKind::SET_KEYBOARD_ROUTE,
                               static_cast<std::uint8_t>(KeyboardRoute::BOTH))});

    engine.handle(key(InputEventKind::KeyDown, 0x42));

    CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(KeyboardRoute::BOTH));
    CHECK_EQ(static_cast<int>(engine.mouse_route()), static_cast<int>(MouseRoute::PC2));
}

TEST_CASE(a_held_mouse_button_is_released_when_the_keyboard_switch_takes_the_mouse_along) {
    // The whole reason this lives in the engine and not in Routes. A button
    // still under a finger when the pointer moves would stay down on the
    // computer being left, and that computer never hears about it again.
    BindingEngine engine = synchronised_engine(KeyboardRoute::PC1, MouseRoute::PC1);

    InputEvent button;
    button.kind = InputEventKind::MouseButtonDown;
    button.code = 1;
    button.source_index = 0;
    CHECK(engine.handle(button).count == 1);

    const Outcome outcome = engine.handle(key(InputEventKind::KeyDown, 0x40));

    CHECK(count_of(outcome, ActionRequestKind::ReleaseTarget) >= 1);
    CHECK_EQ(static_cast<int>(engine.mouse_route()), static_cast<int>(MouseRoute::PC2));

    // The release of a button the far side never saw must not arrive there.
    InputEvent release = button;
    release.kind = InputEventKind::MouseButtonUp;
    CHECK_EQ(count_of(engine.handle(release), ActionRequestKind::SendInput), 0);
}

TEST_CASE(a_held_mouse_button_on_the_far_computer_is_released_there_too) {
    // Parted routes are reachable: the setting can be switched on after they
    // have. The button is down on PC2 and the keyboard switch moves the pair,
    // so PC2 must be told to let go - orphaning alone leaves it stuck there.
    BindingEngine engine = synchronised_engine(KeyboardRoute::PC1, MouseRoute::PC2);
    InputEvent button;
    button.kind = InputEventKind::MouseButtonDown;
    button.code = 1;
    button.source_index = 0;
    CHECK(engine.handle(button).count == 1);

    const Outcome outcome = engine.handle(key(InputEventKind::KeyDown, 0x40));

    bool released_pc2 = false;
    for (std::size_t i = 0; i < outcome.count; ++i) {
        if (outcome.actions[i].kind == ActionRequestKind::ReleaseTarget &&
            outcome.actions[i].target == Target::Pc2) {
            released_pc2 = true;
        }
    }
    CHECK(released_pc2);
}

TEST_CASE(a_refused_route_moves_neither_device) {
    // Validity is settled before anything is released. A refused route that
    // released the old computer first would let go of keys for a switch that
    // never happened.
    //
    // The two route values alone do not prove that: Routes::set_mouse guards
    // its own validity independently, and the follower placement here
    // computes keyboard_beside(PC1) == PC1, reproducing the starting value -
    // so deleting the gate at the top of move_route would still leave both
    // routes looking right. What the gate actually prevents is
    // release_reached/orphan running for a switch that never happened, so
    // this checks that directly, with a key held before the refused switch.
    //
    // This exercises the binding path, and apply_binding runs this exact
    // same validity check before it ever calls move_route - so a binding
    // never reaches move_route with a bad parameter in the first place, and
    // this case cannot see move_route's own gate deleted. It documents the
    // binding path's refusal, nothing more; see
    // a_refused_route_from_set_mouse_route_moves_neither_device_and_releases_nothing
    // below for the gate move_route itself owns.
    BindingEngine engine = synchronised_engine(KeyboardRoute::PC1, MouseRoute::PC1);
    engine.set_bindings({bound(0x43, BindingMode::REPLACE, ActionKind::SET_MOUSE_ROUTE,
                               static_cast<std::uint8_t>(KeyboardRoute::BOTH))});

    const Outcome held = engine.handle(key(InputEventKind::KeyDown, 0x04));
    CHECK(sends_input(held));

    const Outcome outcome = engine.handle(key(InputEventKind::KeyDown, 0x43));

    CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(KeyboardRoute::PC1));
    CHECK_EQ(static_cast<int>(engine.mouse_route()), static_cast<int>(MouseRoute::PC1));
    CHECK_EQ(count_of(outcome, ActionRequestKind::ReleaseTarget), 0);

    // The held key must still be live: its release still reaches the far
    // side. A key wrongly orphaned by the refused switch would swallow it.
    CHECK_EQ(count_of(engine.handle(key(InputEventKind::KeyUp, 0x04)), ActionRequestKind::SendInput), 1);
}

TEST_CASE(a_refused_route_from_set_mouse_route_moves_neither_device_and_releases_nothing) {
    // a_refused_route_moves_neither_device above goes through a binding, and
    // apply_binding runs this exact same validity check before it ever calls
    // move_route - so that path can never see move_route's own gate deleted.
    // set_mouse_route and set_keyboard_route are what a macro step, or the
    // host, call directly: they skip apply_binding entirely, so this is the
    // path move_route's own gate is actually guarding.
    BindingEngine engine = synchronised_engine(KeyboardRoute::PC1, MouseRoute::PC1);

    const Outcome held = engine.handle(key(InputEventKind::KeyDown, 0x04));
    CHECK(sends_input(held));

    const Outcome outcome =
        engine.set_mouse_route(static_cast<MouseRoute>(KeyboardRoute::BOTH));

    CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(KeyboardRoute::PC1));
    CHECK_EQ(static_cast<int>(engine.mouse_route()), static_cast<int>(MouseRoute::PC1));
    CHECK_EQ(count_of(outcome, ActionRequestKind::ReleaseTarget), 0);

    // The held key must still be live: its release still reaches the far
    // side. A key wrongly orphaned by the refused switch would swallow it.
    CHECK_EQ(count_of(engine.handle(key(InputEventKind::KeyUp, 0x04)), ActionRequestKind::SendInput), 1);
}

TEST_CASE(a_macro_step_moves_both_devices_under_synchronised_control) {
    // Macro steps reach move_route through set_keyboard_route, so one rule
    // covers the operator's buttons and the scripts alike.
    BindingEngine engine = synchronised_engine(KeyboardRoute::PC1, MouseRoute::PC1);

    engine.set_keyboard_route(KeyboardRoute::PC2);

    CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(KeyboardRoute::PC2));
    CHECK_EQ(static_cast<int>(engine.mouse_route()), static_cast<int>(MouseRoute::PC2));
}

TEST_CASE(switching_is_independent_again_when_synchronised_control_is_off) {
    BindingEngine engine = synchronised_engine(KeyboardRoute::PC1, MouseRoute::PC1);
    engine.set_synchronised_control(false);

    engine.handle(key(InputEventKind::KeyDown, 0x40));

    CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(KeyboardRoute::PC2));
    CHECK_EQ(static_cast<int>(engine.mouse_route()), static_cast<int>(MouseRoute::PC1));
}
