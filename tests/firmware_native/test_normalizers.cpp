// Turning HID reports into events, without inventing any.
//
// A report says what is true now, not what changed. Two reports with the same
// six keys in a different order mean nobody pressed anything - a keyboard is
// free to reshuffle its own array - and a normalizer that compares positions
// instead of contents releases and re-presses every key the user is holding.
// On a device that types onto somebody's computer, that is a stutter of
// duplicated characters they did not ask for.
//
// The other half is what happens when a device goes away. Whatever it was
// holding is still held on the far side, and nothing else will ever say
// otherwise, so a disconnect has to release everything the device was
// remembered to be holding. Releasing a key nobody pressed is a nuisance;
// holding one nobody can release is not.

#include "ch375/report_descriptor.hpp"
#include "input/keyboard_normalizer.hpp"
#include "input/mouse_normalizer.hpp"
#include "test_support.hpp"

#include <vector>

using duo_input::u1::input::InputEvent;
using duo_input::u1::input::InputEventKind;
using duo_input::u1::input::KeyboardNormalizer;
using duo_input::u1::input::kMaxEventsPerReport;
using duo_input::u1::input::MouseNormalizer;
using duo_input::u1::ch375::boot_mouse_layout;
using duo_input::u1::ch375::MouseReportLayout;
using duo_input::u1::ch375::ReportField;

namespace {

/// A boot keyboard report: modifiers, a reserved byte, then six key slots.
std::vector<std::uint8_t> keys(std::uint8_t modifiers, std::initializer_list<std::uint8_t> down) {
    std::vector<std::uint8_t> report{modifiers, 0, 0, 0, 0, 0, 0, 0};
    std::size_t slot = 2;
    for (std::uint8_t usage : down) {
        if (slot < report.size()) {
            report[slot++] = usage;
        }
    }
    return report;
}

/// A boot mouse report: buttons, then signed X and Y, then the wheel.
std::vector<std::uint8_t> mouse(std::uint8_t buttons, std::int8_t dx, std::int8_t dy,
                                std::int8_t wheel = 0) {
    return {buttons, static_cast<std::uint8_t>(dx), static_cast<std::uint8_t>(dy),
            static_cast<std::uint8_t>(wheel)};
}

duo_input::protocol::ByteView view(const std::vector<std::uint8_t>& bytes) {
    return duo_input::protocol::ByteView{bytes.data(), bytes.size()};
}

/// A layout shaped like a boot report with an identifier bolted on the front.
///
/// The shape the bench's mouse sends in its own protocol: identifier, buttons,
/// dX, dY, wheel. It stands in for what a descriptor would have said, so that
/// the two tests written against the old `set_report_id(true)` go on asking
/// the same question of the thing that replaced it.
MouseReportLayout identified_boot_shaped_layout() {
    MouseReportLayout layout = boot_mouse_layout();
    layout.report_id = true;
    layout.report_id_value = 1;
    return layout;
}

struct Collected {
    InputEvent events[kMaxEventsPerReport];
    std::size_t count = 0;

    int count_of(InputEventKind kind) const {
        int seen = 0;
        for (std::size_t index = 0; index < count; ++index) {
            if (events[index].kind == kind) {
                ++seen;
            }
        }
        return seen;
    }

    bool has(InputEventKind kind, std::uint16_t code) const {
        for (std::size_t index = 0; index < count; ++index) {
            if (events[index].kind == kind && events[index].code == code) {
                return true;
            }
        }
        return false;
    }
};

}  // namespace

// =========================================================== the keyboard

TEST_CASE(a_key_going_down_is_one_event) {
    KeyboardNormalizer normalizer;
    Collected out;
    const auto report = keys(0, {0x04});

    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 1u);
    CHECK(out.has(InputEventKind::KeyDown, 0x04));
}

TEST_CASE(a_key_coming_up_is_one_event) {
    KeyboardNormalizer normalizer;
    Collected out;
    const auto pressed = keys(0, {0x04});
    normalizer.apply(view(pressed), out.events, kMaxEventsPerReport);

    const auto released = keys(0, {});
    out.count = normalizer.apply(view(released), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 1u);
    CHECK(out.has(InputEventKind::KeyUp, 0x04));
}

TEST_CASE(the_same_report_twice_says_nothing_happened) {
    KeyboardNormalizer normalizer;
    Collected out;
    const auto report = keys(0, {0x04, 0x05});
    normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    // Keyboards resend their state; a repeat is not a keystroke.
    CHECK_EQ(out.count, 0u);
}

TEST_CASE(six_keys_in_a_different_order_are_the_same_six_keys) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.apply(view(keys(0, {0x04, 0x05, 0x06, 0x07, 0x08, 0x09})), out.events,
                     kMaxEventsPerReport);

    out.count = normalizer.apply(view(keys(0, {0x09, 0x08, 0x07, 0x06, 0x05, 0x04})), out.events,
                                 kMaxEventsPerReport);

    // A keyboard may reshuffle its own array whenever it likes. Comparing
    // positions instead of contents releases and re-presses every key someone
    // is holding, which arrives as a stutter of characters nobody typed.
    CHECK_EQ(out.count, 0u);
}

TEST_CASE(one_key_changing_among_six_is_one_press_and_one_release) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.apply(view(keys(0, {0x04, 0x05, 0x06, 0x07, 0x08, 0x09})), out.events,
                     kMaxEventsPerReport);

    out.count = normalizer.apply(view(keys(0, {0x04, 0x05, 0x06, 0x07, 0x08, 0x0A})), out.events,
                                 kMaxEventsPerReport);

    CHECK_EQ(out.count, 2u);
    CHECK(out.has(InputEventKind::KeyUp, 0x09));
    CHECK(out.has(InputEventKind::KeyDown, 0x0A));
}

TEST_CASE(a_modifier_going_down_is_an_event_of_its_own) {
    KeyboardNormalizer normalizer;
    Collected out;

    out.count = normalizer.apply(view(keys(0x02, {})), out.events, kMaxEventsPerReport);

    // Bit 1 is left shift, usage 0xE1. A modifier is a key like any other on
    // the far side, and it has to arrive as one or nothing will be shifted.
    CHECK_EQ(out.count, 1u);
    CHECK(out.has(InputEventKind::KeyDown, 0xE1));
}

TEST_CASE(every_modifier_bit_maps_to_its_own_usage) {
    KeyboardNormalizer normalizer;
    Collected out;

    out.count = normalizer.apply(view(keys(0xFF, {})), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 8u);
    CHECK(out.has(InputEventKind::KeyDown, 0xE0));  // left control
    CHECK(out.has(InputEventKind::KeyDown, 0xE7));  // right meta
}

TEST_CASE(a_modifier_released_while_a_key_is_held_releases_only_the_modifier) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.apply(view(keys(0x02, {0x04})), out.events, kMaxEventsPerReport);

    out.count = normalizer.apply(view(keys(0x00, {0x04})), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 1u);
    CHECK(out.has(InputEventKind::KeyUp, 0xE1));
}

TEST_CASE(a_rollover_report_changes_nothing) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.apply(view(keys(0, {0x04, 0x05})), out.events, kMaxEventsPerReport);

    // 0x01 in every slot means the keyboard cannot say what is held, not that
    // six new keys were pressed. Believing it would release what is actually
    // down and press a key that does not exist.
    out.count = normalizer.apply(view(keys(0, {0x01, 0x01, 0x01, 0x01, 0x01, 0x01})), out.events,
                                 kMaxEventsPerReport);

    CHECK_EQ(out.count, 0u);
}

TEST_CASE(the_keys_held_before_a_rollover_are_still_held_after_it) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.apply(view(keys(0, {0x04})), out.events, kMaxEventsPerReport);
    normalizer.apply(view(keys(0, {0x01, 0x01, 0x01, 0x01, 0x01, 0x01})), out.events,
                     kMaxEventsPerReport);

    out.count = normalizer.apply(view(keys(0, {})), out.events, kMaxEventsPerReport);

    CHECK(out.has(InputEventKind::KeyUp, 0x04));
}

TEST_CASE(a_report_that_is_too_short_is_ignored) {
    KeyboardNormalizer normalizer;
    Collected out;
    const std::vector<std::uint8_t> stub{0x00, 0x00};

    out.count = normalizer.apply(view(stub), out.events, kMaxEventsPerReport);

    // Half a report is not a report. Reading the slots that did arrive would
    // release every key the user is holding.
    CHECK_EQ(out.count, 0u);
}

TEST_CASE(a_disconnected_keyboard_releases_what_it_was_holding) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.apply(view(keys(0x01, {0x04, 0x05})), out.events, kMaxEventsPerReport);

    out.count = normalizer.release_all(out.events, kMaxEventsPerReport);

    // Nothing else will ever say these came up. A held modifier changes what
    // every later keystroke means, and the person cannot fix it from the
    // computer receiving it.
    CHECK_EQ(out.count, 3u);
    CHECK(out.has(InputEventKind::KeyUp, 0x04));
    CHECK(out.has(InputEventKind::KeyUp, 0x05));
    CHECK(out.has(InputEventKind::KeyUp, 0xE0));
}

TEST_CASE(releasing_everything_twice_releases_it_once) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.apply(view(keys(0, {0x04})), out.events, kMaxEventsPerReport);
    normalizer.release_all(out.events, kMaxEventsPerReport);

    out.count = normalizer.release_all(out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 0u);
}

// ============================================================== the mouse

TEST_CASE(movement_is_one_event_carrying_both_axes) {
    MouseNormalizer normalizer;
    Collected out;

    out.count = normalizer.apply(view(mouse(0, 5, -3)), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 1u);
    CHECK_EQ(out.events[0].kind, InputEventKind::MouseMove);
    CHECK_EQ(out.events[0].x, 5);
    CHECK_EQ(out.events[0].y, -3);
}

TEST_CASE(movement_is_signed) {
    MouseNormalizer normalizer;
    Collected out;

    // 0xFF is minus one, not two hundred and fifty five. Read unsigned, a
    // small movement left becomes a leap across the screen.
    out.count = normalizer.apply(view(mouse(0, -1, -128)), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.events[0].x, -1);
    CHECK_EQ(out.events[0].y, -128);
}

TEST_CASE(a_report_with_no_movement_and_no_change_says_nothing) {
    MouseNormalizer normalizer;
    Collected out;
    normalizer.apply(view(mouse(0, 0, 0)), out.events, kMaxEventsPerReport);

    out.count = normalizer.apply(view(mouse(0, 0, 0)), out.events, kMaxEventsPerReport);

    // A mouse polled every few milliseconds sends these constantly.
    CHECK_EQ(out.count, 0u);
}

TEST_CASE(a_button_going_down_is_an_edge) {
    MouseNormalizer normalizer;
    Collected out;

    out.count = normalizer.apply(view(mouse(0x01, 0, 0)), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 1u);
    CHECK(out.has(InputEventKind::MouseButtonDown, 0));
}

TEST_CASE(a_button_held_across_reports_is_one_edge_not_many) {
    MouseNormalizer normalizer;
    Collected out;
    normalizer.apply(view(mouse(0x01, 0, 0)), out.events, kMaxEventsPerReport);

    out.count = normalizer.apply(view(mouse(0x01, 0, 0)), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 0u);
}

TEST_CASE(the_fourth_and_fifth_buttons_are_carried) {
    MouseNormalizer normalizer;
    Collected out;

    out.count = normalizer.apply(view(mouse(0x18, 0, 0)), out.events, kMaxEventsPerReport);

    // Bits 3 and 4. Side buttons are most of why somebody buys a five-button
    // mouse, and dropping them silently is worse than refusing the device.
    CHECK_EQ(out.count, 2u);
    CHECK(out.has(InputEventKind::MouseButtonDown, 3));
    CHECK(out.has(InputEventKind::MouseButtonDown, 4));
}

TEST_CASE(the_wheel_is_its_own_event) {
    MouseNormalizer normalizer;
    Collected out;

    out.count = normalizer.apply(view(mouse(0, 0, 0, -2)), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 1u);
    CHECK_EQ(out.events[0].kind, InputEventKind::Wheel);
    CHECK_EQ(out.events[0].wheel, -2);
}

TEST_CASE(moving_and_clicking_at_once_produces_both) {
    MouseNormalizer normalizer;
    Collected out;

    out.count = normalizer.apply(view(mouse(0x02, 4, 4)), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 2u);
    CHECK(out.has(InputEventKind::MouseButtonDown, 1));
    CHECK_EQ(out.count_of(InputEventKind::MouseMove), 1);
}

TEST_CASE(a_report_that_leads_with_an_identifier_is_read_past_it) {
    MouseNormalizer normalizer;
    normalizer.set_layout(identified_boot_shaped_layout());
    Collected out;

    // What the mouse on the bench actually sends: seven bytes beginning with
    // an identifier. Taking that first byte for the buttons puts a click on
    // every single movement.
    const std::vector<std::uint8_t> report{0x01, 0x00, 0x05, 0x00, 0xFD, 0xFF, 0x00};
    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count_of(InputEventKind::MouseButtonDown), 0);
    CHECK_EQ(out.count_of(InputEventKind::MouseMove), 1);
}

TEST_CASE(the_movement_in_an_identified_report_is_taken_from_behind_the_buttons) {
    MouseNormalizer normalizer;
    normalizer.set_layout(identified_boot_shaped_layout());
    Collected out;

    // Seven bytes from the bench: identifier, buttons, then the movement.
    // Counting the events is not enough - a report read one byte further out
    // still produces exactly one movement, of the wrong thing.
    const std::vector<std::uint8_t> report{0x01, 0x00, 0xF6, 0x4F, 0x00, 0x00, 0x00};
    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 1u);
    CHECK_EQ(out.events[0].kind, InputEventKind::MouseMove);
    CHECK_EQ(out.events[0].x, static_cast<std::int16_t>(-10));
    CHECK_EQ(out.events[0].y, static_cast<std::int16_t>(79));
}

TEST_CASE(a_boot_report_is_read_from_its_first_byte) {
    MouseNormalizer normalizer;
    Collected out;

    // The same movement as a boot mouse sends it: buttons, X, Y, wheel, with
    // nothing in front. Skipping a byte here loses the buttons and shifts the
    // movement one axis over, which is the same fault from the other side.
    const std::vector<std::uint8_t> report{0x00, 0xF6, 0x4F, 0x00};
    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 1u);
    CHECK_EQ(out.events[0].kind, InputEventKind::MouseMove);
    CHECK_EQ(out.events[0].x, static_cast<std::int16_t>(-10));
    CHECK_EQ(out.events[0].y, static_cast<std::int16_t>(79));
}

TEST_CASE(a_mouse_report_that_is_too_short_is_ignored) {
    MouseNormalizer normalizer;
    Collected out;
    const std::vector<std::uint8_t> stub{0x01};

    out.count = normalizer.apply(view(stub), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 0u);
}

TEST_CASE(a_disconnected_mouse_releases_its_buttons) {
    MouseNormalizer normalizer;
    Collected out;
    normalizer.apply(view(mouse(0x05, 0, 0)), out.events, kMaxEventsPerReport);

    out.count = normalizer.release_all(out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 2u);
    CHECK(out.has(InputEventKind::MouseButtonUp, 0));
    CHECK(out.has(InputEventKind::MouseButtonUp, 2));
}

TEST_CASE(a_disconnect_does_not_invent_a_final_movement) {
    MouseNormalizer normalizer;
    Collected out;
    normalizer.apply(view(mouse(0, 10, 10)), out.events, kMaxEventsPerReport);

    out.count = normalizer.release_all(out.events, kMaxEventsPerReport);

    // Movement is not a state that can be left held, so there is nothing to
    // undo - and a pointer that jumps when a cable is pulled is worse than one
    // that stops.
    CHECK_EQ(out.count, 0u);
}

// ================================================= a layout from a descriptor
//
// SYNTHETIC, all of it. The corpus in tests/vectors/hid_reports holds boot
// reports only, because boot protocol is the only thing any device on this
// bench has ever been asked for. These layouts are written to the shape a
// descriptor declares; only a mouse can confirm them.

namespace {

/// What a high-resolution mouse's descriptor declares, once parsed.
///
/// Identifier, one button byte, sixteen-bit X and Y, one wheel byte: the
/// seven-byte report the bench's mouse sends when nobody forces boot on it.
MouseReportLayout report_id_wheel_layout() {
    MouseReportLayout layout;
    layout.report_id = true;
    layout.report_id_value = 1;
    layout.buttons = ReportField{true, 0, 1};
    layout.x = ReportField{true, 1, 2};
    layout.y = ReportField{true, 3, 2};
    layout.wheel = ReportField{true, 5, 1};
    layout.minimum_body_bytes = 5;
    return layout;
}

/// The same mouse without an identifier: buttons, X, Y, wheel, pan.
MouseReportLayout unprefixed_wheel_layout() {
    MouseReportLayout layout;
    layout.buttons = ReportField{true, 0, 1};
    layout.x = ReportField{true, 1, 1};
    layout.y = ReportField{true, 2, 1};
    layout.wheel = ReportField{true, 3, 1};
    layout.pan = ReportField{true, 4, 1};
    layout.minimum_body_bytes = 3;
    return layout;
}

}  // namespace

TEST_CASE(a_wheel_behind_a_report_id_turns_the_right_way) {
    MouseNormalizer normalizer;
    normalizer.set_layout(report_id_wheel_layout());
    Collected out;

    // Identifier, no buttons, no movement, one notch away from the user.
    // 0xFF is minus one; read unsigned it scrolls two hundred and fifty five.
    const std::vector<std::uint8_t> report{0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFF};
    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 1u);
    CHECK_EQ(out.events[0].kind, InputEventKind::Wheel);
    CHECK_EQ(out.events[0].wheel, -1);
}

TEST_CASE(a_wheel_behind_a_report_id_turns_the_other_way_too) {
    MouseNormalizer normalizer;
    normalizer.set_layout(report_id_wheel_layout());
    Collected out;

    const std::vector<std::uint8_t> report{0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01};
    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    // Both signs, because a wheel read at the wrong offset in a report that is
    // otherwise zeroes produces nothing at all, and nothing looks like
    // agreement.
    CHECK_EQ(out.count, 1u);
    CHECK_EQ(out.events[0].kind, InputEventKind::Wheel);
    CHECK_EQ(out.events[0].wheel, 1);
}

TEST_CASE(buttons_and_axes_still_land_when_the_layout_has_a_report_id) {
    MouseNormalizer normalizer;
    normalizer.set_layout(report_id_wheel_layout());
    Collected out;

    // Identifier 1, the middle button down, X = -10 and Y = +79 as sixteen-bit
    // little-endian values, and no wheel. Read one byte out, or read the axes
    // a byte wide, and every one of these lands somewhere else.
    const std::vector<std::uint8_t> report{0x01, 0x04, 0xF6, 0xFF, 0x4F, 0x00, 0x00};
    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 2u);
    CHECK(out.has(InputEventKind::MouseButtonDown, 2));
    CHECK_EQ(out.count_of(InputEventKind::MouseMove), 1);
    CHECK_EQ(out.count_of(InputEventKind::Wheel), 0);
    for (std::size_t index = 0; index < out.count; ++index) {
        if (out.events[index].kind == InputEventKind::MouseMove) {
            CHECK_EQ(out.events[index].x, static_cast<std::int16_t>(-10));
            CHECK_EQ(out.events[index].y, static_cast<std::int16_t>(79));
        }
    }
}

TEST_CASE(a_report_carrying_another_collections_identifier_is_not_movement) {
    MouseNormalizer normalizer;
    normalizer.set_layout(report_id_wheel_layout());
    Collected out;

    // A device with media keys on it sends those down the same endpoint under
    // an identifier of their own. Read as a mouse report they are a click and
    // a jump across the screen.
    const std::vector<std::uint8_t> report{0x02, 0x01, 0x20, 0x00, 0x20, 0x00, 0x01};
    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 0u);
}

TEST_CASE(a_wheel_at_the_unprefixed_offsets_turns_the_right_way) {
    MouseNormalizer normalizer;
    normalizer.set_layout(unprefixed_wheel_layout());
    Collected out;

    const std::vector<std::uint8_t> report{0x00, 0x00, 0x00, 0xFF, 0x00};
    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 1u);
    CHECK_EQ(out.events[0].kind, InputEventKind::Wheel);
    CHECK_EQ(out.events[0].wheel, -1);
    CHECK_EQ(out.events[0].pan, 0);
}

TEST_CASE(buttons_axes_and_pan_land_when_the_layout_has_no_report_id) {
    MouseNormalizer normalizer;
    normalizer.set_layout(unprefixed_wheel_layout());
    Collected out;

    // Button 1 down, -10 across, +79 down, no wheel, tilted right.
    const std::vector<std::uint8_t> report{0x01, 0xF6, 0x4F, 0x00, 0x01};
    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 3u);
    CHECK(out.has(InputEventKind::MouseButtonDown, 0));
    for (std::size_t index = 0; index < out.count; ++index) {
        if (out.events[index].kind == InputEventKind::MouseMove) {
            CHECK_EQ(out.events[index].x, static_cast<std::int16_t>(-10));
            CHECK_EQ(out.events[index].y, static_cast<std::int16_t>(79));
        }
        if (out.events[index].kind == InputEventKind::Wheel) {
            CHECK_EQ(out.events[index].wheel, 0);
            CHECK_EQ(out.events[index].pan, 1);
        }
    }
}

TEST_CASE(a_normalizer_nobody_configured_reads_a_boot_report) {
    MouseNormalizer normalizer;
    Collected out;

    // The fallback, and the only layout this firmware could be sure of before
    // any descriptor was fetched. A device whose descriptor cannot be read
    // arrives here and has to go on working exactly as it did.
    out.count =
        normalizer.apply(view(mouse(0x01, -10, 79, -2)), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 3u);
    CHECK(out.has(InputEventKind::MouseButtonDown, 0));
    CHECK_EQ(out.count_of(InputEventKind::MouseMove), 1);
    CHECK_EQ(out.count_of(InputEventKind::Wheel), 1);
}

TEST_CASE(a_report_shorter_than_the_layout_needs_is_ignored) {
    MouseNormalizer normalizer;
    normalizer.set_layout(report_id_wheel_layout());
    Collected out;

    // Four bytes of a seven-byte report. The axes are sixteen bits and only
    // half of X arrived; reading what did arrive is movement nobody made.
    const std::vector<std::uint8_t> report{0x01, 0x00, 0xF6, 0xFF};
    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 0u);
}

TEST_CASE(a_sixteen_bit_axis_carries_more_than_a_byte_could) {
    MouseNormalizer normalizer;
    normalizer.set_layout(report_id_wheel_layout());
    Collected out;

    // X = +300 and Y = -300, which is what a high-resolution mouse produces
    // from an ordinary flick of the wrist and what sixteen-bit axes exist for.
    // Read a byte wide they are +44 and -44: still movement, still the right
    // sign, and a third of the distance. Every axis value in the other tests
    // fits in a byte, so only this one can tell the two apart.
    const std::vector<std::uint8_t> report{0x01, 0x00, 0x2C, 0x01, 0xD4, 0xFE, 0x00};
    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 1u);
    CHECK_EQ(out.events[0].kind, InputEventKind::MouseMove);
    CHECK_EQ(out.events[0].x, static_cast<std::int16_t>(300));
    CHECK_EQ(out.events[0].y, static_cast<std::int16_t>(-300));
}

TEST_CASE(the_bench_mouses_packed_twelve_bit_axes_leave_the_wheel_at_its_declared_byte) {
    MouseNormalizer normalizer;
    MouseReportLayout layout;
    layout.report_id = true;
    layout.report_id_value = 1;
    layout.buttons = ReportField{true, 0, 1, 0, 5};
    layout.x = ReportField{true, 1, 2, 0, 12};
    layout.y = ReportField{true, 2, 2, 4, 12};
    layout.wheel = ReportField{true, 4, 1, 0, 8};
    layout.pan = ReportField{true, 5, 1, 0, 8};
    layout.minimum_body_bytes = 4;
    normalizer.set_layout(layout);
    Collected out;

    // Body bits: X=0x123, Y=-0x123 (0xEDD as signed 12-bit), then wheel -1
    // and pan +1.  Reading whole little-endian words yields -12099 and -2915;
    // reading boot offsets reports movement as scrolling.
    const std::vector<std::uint8_t> report{0x01, 0x00, 0x23, 0xD1, 0xED, 0xFF, 0x01};
    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 2u);
    CHECK_EQ(out.count_of(InputEventKind::MouseMove), 1);
    CHECK_EQ(out.count_of(InputEventKind::Wheel), 1);
    for (std::size_t index = 0; index < out.count; ++index) {
        if (out.events[index].kind == InputEventKind::MouseMove) {
            CHECK_EQ(out.events[index].x, static_cast<std::int16_t>(0x123));
            CHECK_EQ(out.events[index].y, static_cast<std::int16_t>(-0x123));
        }
        if (out.events[index].kind == InputEventKind::Wheel) {
            CHECK_EQ(out.events[index].wheel, -1);
            CHECK_EQ(out.events[index].pan, 1);
        }
    }
}

TEST_CASE(the_buttons_are_read_where_the_layout_puts_them) {
    MouseNormalizer normalizer;
    // A descriptor is free to declare the axes first and the buttons after
    // them, and the parser reports whatever it found. Every other layout here
    // puts the buttons at offset zero, so nothing else would notice a reader
    // that assumed it.
    MouseReportLayout layout;
    layout.x = ReportField{true, 0, 1};
    layout.y = ReportField{true, 1, 1};
    layout.buttons = ReportField{true, 2, 1};
    layout.wheel = ReportField{true, 3, 1};
    layout.minimum_body_bytes = 3;
    normalizer.set_layout(layout);
    Collected out;

    // No movement, the right button down at the third byte. Read at offset
    // zero the buttons are zero and the movement is a click's worth of jump.
    const std::vector<std::uint8_t> report{0x00, 0x00, 0x02, 0x00};
    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 1u);
    CHECK(out.has(InputEventKind::MouseButtonDown, 1));
}

TEST_CASE(a_three_button_field_does_not_invent_two_side_buttons_when_button_three_is_down) {
    MouseNormalizer normalizer;
    MouseReportLayout layout;
    layout.buttons = ReportField{true, 0, 1, 0, 3};
    layout.x = ReportField{true, 1, 1, 0, 8};
    layout.y = ReportField{true, 2, 1, 0, 8};
    layout.minimum_body_bytes = 3;
    normalizer.set_layout(layout);
    Collected out;

    // In a signed three-bit number 0b100 is -4. Buttons are not signed: it is
    // only button three. Sign-extending it to 0xFC also sets bits 3 and 4,
    // which are the two side buttons and may be bound to destination changes.
    const std::vector<std::uint8_t> report{0x04, 0x00, 0x00};
    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 1u);
    CHECK_EQ(out.events[0].kind, InputEventKind::MouseButtonDown);
    CHECK_EQ(out.events[0].code, std::uint16_t{2});
}
