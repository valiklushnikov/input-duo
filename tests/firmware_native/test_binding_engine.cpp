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

}  // namespace

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
