// The two buttons on the device, and the timing that makes them trustworthy.
//
// These are the controls someone reaches for when the software has already let
// them down: the mouse is on the wrong computer, or a macro is holding a key.
// So they have to behave the same way every time, and they must not fire on
// their own - a contact bouncing for a few milliseconds is not two presses,
// and a finger resting on a button is not a hundred toggles.
//
// The 5-second hold on SW2 confirms a factory reset. That one has the opposite
// requirement: it must be hard to do by accident, and it must not also perform
// the short action on the way past.

#include "buttons.hpp"
#include "test_support.hpp"

using duo_input::u1::ButtonEvent;
using duo_input::u1::Buttons;
using duo_input::u1::kDebounceMs;
using duo_input::u1::kFactoryHoldMs;

namespace {

/// Hold both buttons in a state for a while, returning the last event seen.
///
/// Events are collected rather than only the final one, because "did anything
/// fire during this" is usually the question.
struct Session {
    Buttons buttons;
    std::uint32_t now = 1000;
    int toggles = 0;
    int stops = 0;
    int factory = 0;

    void step(bool sw1, bool sw2, std::uint32_t duration_ms, std::uint32_t tick_ms = 1) {
        const std::uint32_t until = now + duration_ms;
        while (now <= until) {
            switch (buttons.update(now, sw1, sw2)) {
                case ButtonEvent::EmergencyMouseToggle:
                    ++toggles;
                    break;
                case ButtonEvent::StopReleaseAll:
                    ++stops;
                    break;
                case ButtonEvent::FactoryResetConfirmed:
                    ++factory;
                    break;
                case ButtonEvent::None:
                    break;
            }
            now += tick_ms;
        }
    }
};

}  // namespace

// ------------------------------------------------------------------ settling

TEST_CASE(nothing_happens_while_nothing_is_pressed) {
    Session session;

    session.step(false, false, 1000);

    CHECK_EQ(session.toggles, 0);
    CHECK_EQ(session.stops, 0);
    CHECK_EQ(session.factory, 0);
}

TEST_CASE(a_press_shorter_than_the_debounce_is_not_a_press) {
    Session session;
    session.step(false, false, 100);

    // A contact bouncing for a few milliseconds is not someone pressing a
    // button, and treating it as one would move the mouse to the other
    // computer while they were typing.
    session.step(true, false, kDebounceMs - 2);
    session.step(false, false, 200);

    CHECK_EQ(session.toggles, 0);
}

TEST_CASE(a_press_that_settles_is_a_press) {
    Session session;
    session.step(false, false, 100);

    session.step(true, false, kDebounceMs + 5);
    session.step(false, false, 100);

    CHECK_EQ(session.toggles, 1);
}

TEST_CASE(the_debounce_is_the_one_the_plan_names) {
    CHECK_EQ(kDebounceMs, 25u);
}

// ------------------------------------------------------------- sw1: one toggle

TEST_CASE(holding_sw1_gives_exactly_one_toggle) {
    Session session;
    session.step(false, false, 100);

    // A finger resting on the button is one intention, not a thousand.
    session.step(true, false, 3000);

    CHECK_EQ(session.toggles, 1);
}

TEST_CASE(sw1_toggles_again_only_after_it_is_let_go) {
    Session session;
    session.step(false, false, 100);
    session.step(true, false, 200);
    session.step(false, false, 200);

    session.step(true, false, 200);

    CHECK_EQ(session.toggles, 2);
}

TEST_CASE(bouncing_on_release_does_not_produce_a_second_toggle) {
    Session session;
    session.step(false, false, 100);
    session.step(true, false, 200);

    // Contacts bounce on the way up as well as the way down.
    session.step(false, false, 3);
    session.step(true, false, 3);
    session.step(false, false, 3);
    session.step(true, false, 3);
    session.step(false, false, 300);

    CHECK_EQ(session.toggles, 1);
}

TEST_CASE(sw1_fires_when_it_settles_not_when_it_is_released) {
    Session session;
    session.step(false, false, 100);

    session.step(true, false, kDebounceMs + 5);

    // The operator wants the mouse to move now, not when they lift a finger.
    CHECK_EQ(session.toggles, 1);
}

// --------------------------------------------------------- sw2: stop, or reset

TEST_CASE(a_short_sw2_press_stops_everything_when_it_is_released) {
    Session session;
    session.step(false, false, 100);

    session.step(false, true, 200);
    CHECK_EQ(session.stops, 0);

    session.step(false, false, 100);

    // Fired on release, because until then it might still become a five-second
    // hold, and a factory reset must not be preceded by a stop it did not ask
    // for.
    CHECK_EQ(session.stops, 1);
}

TEST_CASE(a_five_second_hold_confirms_a_factory_reset) {
    Session session;
    session.step(false, false, 100);

    session.step(false, true, kFactoryHoldMs + 50);

    CHECK_EQ(session.factory, 1);
}

TEST_CASE(the_hold_is_exactly_five_seconds) {
    CHECK_EQ(kFactoryHoldMs, 5000u);

    Session session;
    session.step(false, false, 100);
    // One tick short.
    session.step(false, true, kFactoryHoldMs - kDebounceMs - 2);

    CHECK_EQ(session.factory, 0);
}

TEST_CASE(a_long_hold_does_not_also_stop_everything) {
    Session session;
    session.step(false, false, 100);
    session.step(false, true, kFactoryHoldMs + 50);

    session.step(false, false, 200);

    // The short action is suppressed once the hold has been recognised.
    // Otherwise confirming a factory reset would release every key on the way
    // past, which is not what the operator asked for.
    CHECK_EQ(session.stops, 0);
    CHECK_EQ(session.factory, 1);
}

TEST_CASE(a_hold_confirms_once_however_long_it_lasts) {
    Session session;
    session.step(false, false, 100);

    session.step(false, true, kFactoryHoldMs * 3);

    CHECK_EQ(session.factory, 1);
}

TEST_CASE(letting_go_and_holding_again_can_confirm_again) {
    Session session;
    session.step(false, false, 100);
    session.step(false, true, kFactoryHoldMs + 50);
    session.step(false, false, 200);

    session.step(false, true, kFactoryHoldMs + 50);

    CHECK_EQ(session.factory, 2);
}

// -------------------------------------------------------------- independence

TEST_CASE(the_two_buttons_do_not_interfere) {
    Session session;
    session.step(false, false, 100);

    session.step(true, true, 200);
    session.step(false, false, 200);

    CHECK_EQ(session.toggles, 1);
    CHECK_EQ(session.stops, 1);
}

TEST_CASE(pressing_sw1_during_an_sw2_hold_does_not_cancel_the_hold) {
    Session session;
    session.step(false, false, 100);

    session.step(false, true, 1000);
    session.step(true, true, 1000);
    session.step(false, true, kFactoryHoldMs);

    CHECK_EQ(session.factory, 1);
    CHECK_EQ(session.toggles, 1);
}

// ------------------------------------------------------------------ the clock

TEST_CASE(the_millisecond_counter_wrapping_does_not_invent_a_hold) {
    Buttons buttons;
    const std::uint32_t near_the_end = 0xFFFFFFF0;

    // Settle the press just before the wrap.
    for (std::uint32_t offset = 0; offset < kDebounceMs + 5; ++offset) {
        buttons.update(near_the_end + offset, false, true);
    }
    // Then keep holding through it. Unsigned arithmetic measures the real
    // elapsed time; comparing the raw values would see five seconds pass in
    // an instant and erase the operator's configuration.
    int factory = 0;
    for (std::uint32_t offset = kDebounceMs + 5; offset < 1000; ++offset) {
        if (buttons.update(near_the_end + offset, false, true) ==
            ButtonEvent::FactoryResetConfirmed) {
            ++factory;
        }
    }

    CHECK_EQ(factory, 0);
}

TEST_CASE(a_hold_across_the_wrap_still_confirms_at_five_seconds) {
    Buttons buttons;
    const std::uint32_t start = 0xFFFFF000;
    int factory = 0;

    // Released first: the press has to happen while the device is running,
    // and the wrap is the only thing under test here.
    for (std::uint32_t offset = 0; offset < 100; ++offset) {
        buttons.update(start + offset, false, false);
    }
    for (std::uint32_t offset = 100; offset < kFactoryHoldMs + 400; ++offset) {
        if (buttons.update(start + offset, false, true) ==
            ButtonEvent::FactoryResetConfirmed) {
            ++factory;
        }
    }

    CHECK_EQ(factory, 1);
}

// -------------------------------------------------------------- at power-on

TEST_CASE(a_button_already_down_at_the_first_reading_is_not_a_press) {
    Buttons buttons;
    int factory = 0;
    int stops = 0;

    // Someone powering the device with a finger on SW2 - or a shorted pin -
    // must not have their configuration erased five seconds later. Whatever
    // is true at the first reading is the baseline, not an action.
    for (std::uint32_t offset = 0; offset < kFactoryHoldMs + 500; ++offset) {
        switch (buttons.update(1000 + offset, true, true)) {
            case ButtonEvent::FactoryResetConfirmed:
                ++factory;
                break;
            case ButtonEvent::StopReleaseAll:
                ++stops;
                break;
            default:
                break;
        }
    }

    CHECK_EQ(factory, 0);
    CHECK_EQ(stops, 0);
}

TEST_CASE(a_button_held_at_power_on_works_normally_once_it_is_let_go) {
    Buttons buttons;
    for (std::uint32_t offset = 0; offset < 100; ++offset) {
        buttons.update(1000 + offset, false, true);
    }
    for (std::uint32_t offset = 100; offset < 300; ++offset) {
        buttons.update(1000 + offset, false, false);
    }

    int stops = 0;
    for (std::uint32_t offset = 300; offset < 600; ++offset) {
        if (buttons.update(1000 + offset, false, true) == ButtonEvent::StopReleaseAll) {
            ++stops;
        }
    }
    for (std::uint32_t offset = 600; offset < 800; ++offset) {
        if (buttons.update(1000 + offset, false, false) == ButtonEvent::StopReleaseAll) {
            ++stops;
        }
    }

    CHECK_EQ(stops, 1);
}
