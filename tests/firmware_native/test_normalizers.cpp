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

#include "input/hid/report_descriptor.hpp"
#include "input/keyboard_normalizer.hpp"
#include "input/mouse_normalizer.hpp"
#include "test_support.hpp"

#include <cstdint>
#include <vector>

using duo_input::u1::input::InputEvent;
using duo_input::u1::input::InputEventKind;
using duo_input::u1::input::KeyboardNormalizer;
using duo_input::u1::input::kMaxEventsPerReport;
using duo_input::u1::input::MouseNormalizer;
using duo_input::u1::input::hid::boot_keyboard_layout;
using duo_input::u1::input::hid::boot_mouse_layout;
using duo_input::u1::input::hid::KeyboardFieldKind;
using duo_input::u1::input::hid::KeyboardReportLayout;
using duo_input::u1::input::hid::MouseReportLayout;
using duo_input::u1::input::hid::parse_mouse_report_descriptor;
using duo_input::u1::input::hid::ReportDescriptorError;
using duo_input::u1::input::hid::ReportField;

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

/// The layout of a keyboard that leads every report with an identifier.
///
/// Report 2 of a device that also has a consumer-control report on the same
/// endpoint: one modifier byte and six slots behind the identifier, so the
/// packet is eight bytes and the body is seven. Every offset below is measured
/// from the byte after the identifier, which is the whole point of stripping
/// it before anything else is read.
KeyboardReportLayout report_id_keyboard_layout() {
    KeyboardReportLayout layout;
    for (std::uint16_t bit = 0; bit < 8; ++bit) {
        layout.modifier_bits[bit] = bit;
    }
    layout.report_id = true;
    layout.report_id_value = 2;
    layout.key_kind = KeyboardFieldKind::Array;
    layout.key_bit_offset = 8;
    layout.key_element_bits = 8;
    layout.key_element_count = 6;
    layout.key_usage_minimum = 0;
    layout.key_usage_maximum = 0x00FF;
    layout.minimum_body_bytes = 7;
    return layout;
}

/// What the Aula F75 actually declares, read off its captured descriptor.
///
/// Five key slots, not six, and a vendor byte in the eighth position where a
/// boot report's sixth slot would be. Boot protocol reads that vendor byte as
/// a key; the descriptor is the only thing that says it is not one.
KeyboardReportLayout aula_keyboard_layout() {
    KeyboardReportLayout layout = boot_keyboard_layout();
    layout.key_element_count = 5;
    layout.minimum_body_bytes = 7;
    return layout;
}

/// An NKRO keyboard: one bit per usage, 0x04 through 0x73.
///
/// No slots at all, so nothing about it is six of anything. A modifier byte,
/// then 112 bits - fifteen bytes of body.
KeyboardReportLayout nkro_keyboard_layout() {
    KeyboardReportLayout layout;
    for (std::uint16_t bit = 0; bit < 8; ++bit) {
        layout.modifier_bits[bit] = bit;
    }
    layout.key_kind = KeyboardFieldKind::Bitmap;
    layout.key_bit_offset = 8;
    layout.key_element_bits = 1;
    layout.key_element_count = 112;
    layout.key_usage_minimum = 0x04;
    layout.key_usage_maximum = 0x73;
    layout.minimum_body_bytes = 15;
    return layout;
}

/// A report from the keyboard above: identifier, modifiers, six slots.
std::vector<std::uint8_t> id_keys(std::uint8_t id, std::uint8_t modifiers,
                                  std::initializer_list<std::uint8_t> down) {
    std::vector<std::uint8_t> report{id, modifiers, 0, 0, 0, 0, 0, 0};
    std::size_t slot = 2;
    for (std::uint8_t usage : down) {
        if (slot < report.size()) {
            report[slot++] = usage;
        }
    }
    return report;
}

/// An Aula report: modifiers, a reserved byte, five slots, a vendor byte.
std::vector<std::uint8_t> aula_keys(std::uint8_t modifiers,
                                    std::initializer_list<std::uint8_t> down,
                                    std::uint8_t vendor = 0) {
    std::vector<std::uint8_t> report{modifiers, 0, 0, 0, 0, 0, 0, vendor};
    std::size_t slot = 2;
    for (std::uint8_t usage : down) {
        if (slot < 7) {
            report[slot++] = usage;
        }
    }
    return report;
}

/// An NKRO report with the named usages' bits set.
std::vector<std::uint8_t> nkro_keys(std::uint8_t modifiers,
                                    std::initializer_list<std::uint8_t> down) {
    std::vector<std::uint8_t> report(15, 0);
    report[0] = modifiers;
    for (std::uint8_t usage : down) {
        const std::size_t bit = 8u + (usage - 0x04u);
        report[bit / 8] = static_cast<std::uint8_t>(report[bit / 8] | (1u << (bit % 8)));
    }
    return report;
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
TEST_CASE(one_error_slot_is_a_keyboard_holding_nothing_not_a_rollover) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.apply(view(keys(0, {0x0F})), out.events, kMaxEventsPerReport);

    // Captured from an Aula F75's 2.4 GHz receiver, which sends this in place
    // of an empty report about nine times in every hundred: 0x01 in the first
    // slot and nothing in the other five. HID 1.11 8.3 puts ErrorRollOver in
    // *every* array field, so one of them is not a keyboard saying it has lost
    // count of what is held - it is a keyboard saying nothing is.
    out.count = normalizer.apply(view(keys(0, {0x01})), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 1u);
    CHECK(out.has(InputEventKind::KeyUp, 0x0F));
}
TEST_CASE(an_error_slot_is_never_delivered_as_a_key) {
    KeyboardNormalizer normalizer;
    Collected out;

    // 0x01 is not a usage anybody can press. Reading it as one would put a key
    // nobody has on the far computer, and - worse - leave it held there, since
    // the report that clears it would look like a release of something real.
    out.count = normalizer.apply(view(keys(0, {0x01})), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 0u);
}
TEST_CASE(a_report_that_would_not_fit_is_not_half_applied) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.apply(view(keys(0, {0x04, 0x05, 0x06, 0x07, 0x08, 0x09})), out.events,
                     kMaxEventsPerReport);

    // Six keys let go and six pressed in one report is twelve events, into
    // room for three. Writing the three that fit and then recording the other
    // nine as delivered strands them: the far computer holds keys this no
    // longer believes are down, so nothing left will ever lift them.
    normalizer.apply(view(keys(0, {0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F})), out.events, 3);

    out.count = normalizer.apply(view(keys(0, {})), out.events, kMaxEventsPerReport);

    CHECK(out.has(InputEventKind::KeyUp, 0x07));
    CHECK(out.has(InputEventKind::KeyUp, 0x09));
}
TEST_CASE(a_report_that_would_not_fit_writes_nothing_at_all) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.apply(view(keys(0, {0x04, 0x05, 0x06, 0x07, 0x08, 0x09})), out.events,
                     kMaxEventsPerReport);

    // Nothing, rather than as much as fits. A caller handed three events out
    // of twelve has no way to learn that the other nine existed, and the three
    // it does get are a state no keyboard was ever in.
    out.count = normalizer.apply(view(keys(0, {0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F})),
                                 out.events, 3);

    CHECK_EQ(out.count, 0u);
}
TEST_CASE(a_release_all_that_would_not_fit_keeps_what_it_could_not_say) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.apply(view(keys(0x0F, {0x04, 0x05, 0x06, 0x07, 0x08, 0x09})), out.events,
                     kMaxEventsPerReport);

    // Six keys and four modifiers is ten releases, into room for two. This is
    // the path that exists so an unplugged keyboard cannot leave keys held on
    // a computer nobody is watching; it must not become the path that does it.
    normalizer.release_all(out.events, 2);

    out.count = normalizer.release_all(out.events, kMaxEventsPerReport);

    CHECK(out.has(InputEventKind::KeyUp, 0x09));
    CHECK(out.has(InputEventKind::KeyUp, 0xE3));
}
TEST_CASE(an_error_slot_does_not_leave_the_key_before_it_repeating) {
    KeyboardNormalizer normalizer;
    Collected out;

    // The sequence the bench recorded while a letter stuck for 453 ms: the
    // key, two of the receiver's one-slot error reports, then the next key.
    // With the error reports believed, 0x0F is released only here - and on a
    // computer that is a letter repeating until the next keystroke stops it.
    normalizer.apply(view(keys(0, {0x0F})), out.events, kMaxEventsPerReport);
    normalizer.apply(view(keys(0, {0x01})), out.events, kMaxEventsPerReport);
    normalizer.apply(view(keys(0, {0x01})), out.events, kMaxEventsPerReport);

    out.count = normalizer.apply(view(keys(0, {0x04})), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 1u);
    CHECK(out.has(InputEventKind::KeyDown, 0x04));
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

// ============================== the keyboard, in its own declared protocol

TEST_CASE(a_report_id_keyboard_presses_and_releases_behind_its_identifier) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.set_layout(report_id_keyboard_layout());

    out.count = normalizer.apply(view(id_keys(2, 0, {0x04})), out.events, kMaxEventsPerReport);
    CHECK_EQ(out.count, 1u);
    CHECK_EQ(static_cast<int>(out.events[0].kind), static_cast<int>(InputEventKind::KeyDown));
    CHECK_EQ(out.events[0].code, std::uint16_t{0x04});

    out.count = normalizer.apply(view(id_keys(2, 0, {})), out.events, kMaxEventsPerReport);
    CHECK_EQ(out.count, 1u);
    CHECK_EQ(static_cast<int>(out.events[0].kind), static_cast<int>(InputEventKind::KeyUp));
    CHECK_EQ(out.events[0].code, std::uint16_t{0x04});
}

TEST_CASE(a_report_carrying_another_identifier_is_not_this_keyboards_report) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.set_layout(report_id_keyboard_layout());
    normalizer.apply(view(id_keys(2, 0, {0x04})), out.events, kMaxEventsPerReport);

    // Report 1 on the same endpoint is the consumer collection's - a volume
    // key, not a keyboard state. Read as one it would release 0x04, and the
    // release that really comes would then say nothing at all.
    out.count = normalizer.apply(view(id_keys(1, 0, {})), out.events, kMaxEventsPerReport);
    CHECK_EQ(out.count, 0u);

    out.count = normalizer.apply(view(id_keys(2, 0, {})), out.events, kMaxEventsPerReport);
    CHECK_EQ(out.count, 1u);
    CHECK(out.has(InputEventKind::KeyUp, 0x04));
}

TEST_CASE(a_native_report_shorter_than_its_layout_leaves_the_held_keys_alone) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.set_layout(nkro_keyboard_layout());
    normalizer.apply(view(nkro_keys(0, {0x04})), out.events, kMaxEventsPerReport);

    std::vector<std::uint8_t> stub = nkro_keys(0, {});
    stub.resize(9);
    out.count = normalizer.apply(view(stub), out.events, kMaxEventsPerReport);
    CHECK_EQ(out.count, 0u);

    // Nine bytes of a fifteen-byte report cover only the first sixty-four
    // usages. Read as a whole report they say every key past that came up.
    out.count = normalizer.apply(view(nkro_keys(0, {})), out.events, kMaxEventsPerReport);
    CHECK_EQ(out.count, 1u);
    CHECK(out.has(InputEventKind::KeyUp, 0x04));
}

TEST_CASE(native_modifier_bits_are_read_where_the_descriptor_put_them) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.set_layout(report_id_keyboard_layout());

    out.count = normalizer.apply(view(id_keys(2, 0x05, {})), out.events, kMaxEventsPerReport);

    // Bits 0 and 2 of the byte after the identifier: left control and left
    // alt. Measured from the start of the packet they would be bits of the
    // identifier itself, which is 2 - and would deliver left shift instead.
    CHECK_EQ(out.count, 2u);
    CHECK_EQ(out.events[0].code, std::uint16_t{0xE0});
    CHECK_EQ(out.events[1].code, std::uint16_t{0xE2});
}

TEST_CASE(a_reordered_native_array_says_nobody_did_anything) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.set_layout(aula_keyboard_layout());
    normalizer.apply(view(aula_keys(0, {0x04, 0x05, 0x06, 0x07, 0x08})), out.events,
                     kMaxEventsPerReport);

    out.count = normalizer.apply(view(aula_keys(0, {0x08, 0x07, 0x06, 0x05, 0x04})), out.events,
                                 kMaxEventsPerReport);

    CHECK_EQ(out.count, 0u);
}

TEST_CASE(a_native_report_releases_then_applies_modifiers_before_new_keys) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.set_layout(aula_keyboard_layout());
    normalizer.apply(view(aula_keys(0, {0x04})), out.events, kMaxEventsPerReport);

    out.count = normalizer.apply(view(aula_keys(0x01, {0x05})), out.events, kMaxEventsPerReport);

    // A key swapped for another under a modifier going down. The release has
    // to precede the press: a computer handed the press first sees both keys
    // held, and an autorepeat can start on the one that was already leaving.
    CHECK_EQ(out.count, 3u);
    CHECK_EQ(static_cast<int>(out.events[0].kind), static_cast<int>(InputEventKind::KeyUp));
    CHECK_EQ(out.events[0].code, std::uint16_t{0x04});
    CHECK_EQ(static_cast<int>(out.events[1].kind), static_cast<int>(InputEventKind::KeyDown));
    CHECK_EQ(out.events[1].code, std::uint16_t{0xE0});
    CHECK_EQ(static_cast<int>(out.events[2].kind), static_cast<int>(InputEventKind::KeyDown));
    CHECK_EQ(out.events[2].code, std::uint16_t{0x05});
}

TEST_CASE(native_modifiers_are_delivered_lowest_bit_first) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.set_layout(aula_keyboard_layout());

    out.count = normalizer.apply(view(aula_keys(0xFF, {})), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 8u);
    for (std::size_t index = 0; index < 8; ++index) {
        CHECK_EQ(static_cast<int>(out.events[index].kind),
                 static_cast<int>(InputEventKind::KeyDown));
        CHECK_EQ(out.events[index].code, static_cast<std::uint16_t>(0xE0 + index));
    }
}

TEST_CASE(the_vendor_byte_past_a_native_array_is_never_a_key) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.set_layout(aula_keyboard_layout());

    // The Aula declares five slots and puts a vendor byte in the eighth
    // position. Boot protocol reads that byte as a sixth slot, which is a key
    // nobody pressed - and one nothing will ever release.
    out.count = normalizer.apply(view(aula_keys(0, {0x04}, 0x1E)), out.events,
                                 kMaxEventsPerReport);

    CHECK_EQ(out.count, 1u);
    CHECK(out.has(InputEventKind::KeyDown, 0x04));
    CHECK_FALSE(out.has(InputEventKind::KeyDown, 0x1E));
}

TEST_CASE(zero_and_error_usages_in_a_native_array_never_become_keys) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.set_layout(aula_keyboard_layout());
    normalizer.apply(view(aula_keys(0, {0x0F})), out.events, kMaxEventsPerReport);

    // One 0x01 among five slots is the Aula's receiver saying nothing is held,
    // not that it has lost count: HID 1.11 8.3 puts ErrorRollOver in every
    // array field. Freezing on it strands the key that was down.
    out.count = normalizer.apply(view(aula_keys(0, {0x01})), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 1u);
    CHECK(out.has(InputEventKind::KeyUp, 0x0F));
}

TEST_CASE(six_error_slots_are_still_the_only_rollover) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.set_layout(report_id_keyboard_layout());
    normalizer.apply(view(id_keys(2, 0, {0x04})), out.events, kMaxEventsPerReport);

    out.count = normalizer.apply(view(id_keys(2, 0, {0x01, 0x01, 0x01, 0x01, 0x01, 0x01})),
                                 out.events, kMaxEventsPerReport);
    CHECK_EQ(out.count, 0u);

    out.count = normalizer.apply(view(id_keys(2, 0, {})), out.events, kMaxEventsPerReport);
    CHECK(out.has(InputEventKind::KeyUp, 0x04));
}

TEST_CASE(release_all_lets_go_of_keys_found_through_a_native_layout) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.set_layout(nkro_keyboard_layout());
    normalizer.apply(view(nkro_keys(0x01, {0x04, 0x50})), out.events, kMaxEventsPerReport);

    out.count = normalizer.release_all(out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 3u);
    CHECK(out.has(InputEventKind::KeyUp, 0x04));
    CHECK(out.has(InputEventKind::KeyUp, 0x50));
    CHECK(out.has(InputEventKind::KeyUp, 0xE0));
}

TEST_CASE(an_nkro_bitmap_delivers_the_bits_that_are_set_wherever_they_are) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.set_layout(nkro_keyboard_layout());

    // The lowest usage in the range, one in the middle of a byte, and the
    // highest. A scan that stops at a byte boundary or runs one usage short
    // loses the outer two and nobody notices until those keys stop working.
    out.count = normalizer.apply(view(nkro_keys(0, {0x04, 0x3A, 0x73})), out.events,
                                 kMaxEventsPerReport);

    CHECK_EQ(out.count, 3u);
    CHECK(out.has(InputEventKind::KeyDown, 0x04));
    CHECK(out.has(InputEventKind::KeyDown, 0x3A));
    CHECK(out.has(InputEventKind::KeyDown, 0x73));
}

TEST_CASE(clearing_one_nkro_bit_releases_only_that_key) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.set_layout(nkro_keyboard_layout());
    normalizer.apply(view(nkro_keys(0, {0x04, 0x3A, 0x73})), out.events, kMaxEventsPerReport);

    out.count = normalizer.apply(view(nkro_keys(0, {0x04, 0x73})), out.events,
                                 kMaxEventsPerReport);

    CHECK_EQ(out.count, 1u);
    CHECK(out.has(InputEventKind::KeyUp, 0x3A));
}

TEST_CASE(a_seventh_key_holds_the_previous_state_rather_than_being_truncated) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.set_layout(nkro_keyboard_layout());
    normalizer.apply(view(nkro_keys(0, {0x04, 0x05})), out.events, kMaxEventsPerReport);

    // An NKRO keyboard can hold more keys than this firmware's six-key output
    // contract can carry. Six of the seven is not a state anybody's hands were
    // in, and the seventh would be silently dropped for as long as it is held.
    // The modifier is refused with them: the report is applied whole or not.
    out.count = normalizer.apply(
        view(nkro_keys(0x01, {0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16})), out.events,
        kMaxEventsPerReport);
    CHECK_EQ(out.count, 0u);

    // Still exactly the two keys from before, so a release of them is still a
    // release of them.
    out.count = normalizer.apply(view(nkro_keys(0, {})), out.events, kMaxEventsPerReport);
    CHECK_EQ(out.count, 2u);
    CHECK(out.has(InputEventKind::KeyUp, 0x04));
    CHECK(out.has(InputEventKind::KeyUp, 0x05));
}

TEST_CASE(the_representable_report_after_an_overflow_is_applied_whole) {
    KeyboardNormalizer normalizer;
    Collected out;
    normalizer.set_layout(nkro_keyboard_layout());
    normalizer.apply(view(nkro_keys(0, {0x04, 0x05})), out.events, kMaxEventsPerReport);
    normalizer.apply(view(nkro_keys(0x01, {0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16})),
                     out.events, kMaxEventsPerReport);

    // The hand comes off one key. Six is representable again, and what arrives
    // is measured against the two that were really held - not against the
    // seven that were refused.
    out.count = normalizer.apply(view(nkro_keys(0x01, {0x10, 0x11, 0x12, 0x13, 0x14, 0x15})),
                                 out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 9u);
    CHECK(out.has(InputEventKind::KeyUp, 0x04));
    CHECK(out.has(InputEventKind::KeyUp, 0x05));
    CHECK(out.has(InputEventKind::KeyDown, 0x10));
    CHECK(out.has(InputEventKind::KeyDown, 0x15));
    CHECK(out.has(InputEventKind::KeyDown, 0xE0));
    CHECK_FALSE(out.has(InputEventKind::KeyDown, 0x16));
}

TEST_CASE(a_layout_that_declares_no_key_field_is_not_read_for_keys) {
    KeyboardNormalizer normalizer;
    Collected out;
    KeyboardReportLayout layout = boot_keyboard_layout();
    layout.key_kind = KeyboardFieldKind::None;
    normalizer.set_layout(layout);

    // Nothing says where the keys are. Reading the boot offsets anyway is a
    // guess, and the guess is wrong for exactly the devices this path exists
    // to serve.
    out.count = normalizer.apply(view(keys(0x01, {0x04})), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 0u);
}

TEST_CASE(the_same_usage_in_two_slots_is_one_key) {
    KeyboardNormalizer normalizer;
    Collected out;

    // Keyboards do repeat themselves across slots, and a set has no room for
    // the same member twice. Two entries for one key press it twice on the far
    // computer and then need two releases to lift it - so the second one stays
    // down when only one arrives.
    out.count = normalizer.apply(view(keys(0, {0x04, 0x04})), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 1u);
    CHECK(out.has(InputEventKind::KeyDown, 0x04));
}

TEST_CASE(a_layout_whose_keys_reach_past_its_declared_minimum_needs_the_longer_report) {
    KeyboardNormalizer normalizer;
    Collected out;
    KeyboardReportLayout layout = nkro_keyboard_layout();
    // Says eight bytes; its own bitmap runs to fifteen.
    layout.minimum_body_bytes = 8;
    normalizer.set_layout(layout);

    // A layout is a struct built out of a stranger's bytes, and its declared
    // length is one of those bytes. Believing it over the offsets beside it
    // reads the last seven bytes of this bitmap out of whatever follows the
    // report in memory.
    std::vector<std::uint8_t> report = nkro_keys(0, {0x04});
    report.resize(8);
    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 0u);
}

TEST_CASE(a_modifier_bit_past_the_end_of_the_body_refuses_the_report) {
    KeyboardNormalizer normalizer;
    Collected out;
    KeyboardReportLayout layout = boot_keyboard_layout();
    // Bit 200 is inside a 26-byte report and nowhere near an 8-byte one.
    layout.modifier_bits[0] = 200;
    normalizer.set_layout(layout);

    out.count = normalizer.apply(view(keys(0, {0x04})), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 0u);
}

TEST_CASE(modifiers_are_read_at_their_declared_bits_not_at_the_front) {
    KeyboardNormalizer normalizer;
    Collected out;
    KeyboardReportLayout layout;
    // Six slots first, then the modifier byte behind them: a seven-byte body
    // with nothing at all where boot protocol keeps its modifiers.
    for (std::uint16_t bit = 0; bit < 8; ++bit) {
        layout.modifier_bits[bit] = static_cast<std::uint16_t>(48 + bit);
    }
    layout.key_kind = KeyboardFieldKind::Array;
    layout.key_bit_offset = 0;
    layout.key_element_bits = 8;
    layout.key_element_count = 6;
    layout.key_usage_minimum = 0;
    layout.key_usage_maximum = 0x00FF;
    layout.minimum_body_bytes = 7;
    normalizer.set_layout(layout);

    // Byte 0 is a key, byte 6 is the modifiers. Reading bit 0 of the report
    // for left control instead reads the low bit of usage 0x04 - which is set,
    // so a keyboard nobody touched a modifier on shifts every letter typed.
    const std::vector<std::uint8_t> report{0x04, 0, 0, 0, 0, 0, 0x01};
    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 2u);
    CHECK(out.has(InputEventKind::KeyDown, 0x04));
    CHECK(out.has(InputEventKind::KeyDown, 0xE0));
    CHECK_FALSE(out.has(InputEventKind::KeyDown, 0xE2));
}

TEST_CASE(a_bitmap_counts_its_keys_up_from_its_own_declared_usage_minimum) {
    KeyboardNormalizer normalizer;
    Collected out;
    KeyboardReportLayout layout;
    for (std::uint16_t bit = 0; bit < 8; ++bit) {
        layout.modifier_bits[bit] = bit;
    }
    // Eight bits covering usages 0x50 through 0x57 - the arrow-key end of the
    // page, not the 0x04 an NKRO bitmap usually starts at.
    layout.key_kind = KeyboardFieldKind::Bitmap;
    layout.key_bit_offset = 8;
    layout.key_element_bits = 1;
    layout.key_element_count = 8;
    layout.key_usage_minimum = 0x50;
    layout.key_usage_maximum = 0x57;
    layout.minimum_body_bytes = 2;
    normalizer.set_layout(layout);

    // Bits 0 and 2 of the second byte. Counting up from a minimum this layout
    // did not declare delivers two keys from the other end of the keyboard.
    const std::vector<std::uint8_t> report{0x00, 0x05};
    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 2u);
    CHECK(out.has(InputEventKind::KeyDown, 0x50));
    CHECK(out.has(InputEventKind::KeyDown, 0x52));
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


// ============================== the side buttons, from descriptor to events
//
// The two halves of the field defect reported on 2026-09-07 meet here. The
// parser reads a button run that the device declared in two Input items, and
// the normalizer masks the button byte to whatever width the parser produced.
// Either half alone looks correct; together they decided that buttons 4 and 5
// did not exist, and every consumer behind this point - the PC and the capture
// dialog both - was told the truth as this layout knew it.

TEST_CASE(side_buttons_declared_in_a_second_input_item_reach_the_events) {
    // The descriptor shape: `Usage Minimum 1 / Usage Maximum 3` in one Input
    // item and `4 / 5` in the next, then sixteen-bit axes behind a Report ID.
    const std::vector<std::uint8_t> descriptor = {
        0x05, 0x01,        // Usage Page (Generic Desktop)
        0x09, 0x02,        // Usage (Mouse)
        0xA1, 0x01,        // Collection (Application)
        0x85, 0x03,        //   Report ID (3)
        0x09, 0x01,        //   Usage (Pointer)
        0xA1, 0x00,        //   Collection (Physical)
        0x05, 0x09,        //     Usage Page (Button)
        0x15, 0x00,        //     Logical Minimum (0)
        0x25, 0x01,        //     Logical Maximum (1)
        0x75, 0x01,        //     Report Size (1)
        0x19, 0x01,        //     Usage Minimum (Button 1)
        0x29, 0x03,        //     Usage Maximum (Button 3)
        0x95, 0x03,        //     Report Count (3)
        0x81, 0x02,        //     Input (Data,Var,Abs) - bits 0-2
        0x19, 0x04,        //     Usage Minimum (Button 4)
        0x29, 0x05,        //     Usage Maximum (Button 5)
        0x95, 0x02,        //     Report Count (2)
        0x81, 0x02,        //     Input (Data,Var,Abs) - bits 3-4
        0x95, 0x03,        //     Report Count (3)
        0x81, 0x03,        //     Input (Cnst,Var,Abs) - padding to a byte
        0x05, 0x01,        //     Usage Page (Generic Desktop)
        0x09, 0x30,        //     Usage (X)
        0x09, 0x31,        //     Usage (Y)
        0x16, 0x00, 0x80,  //     Logical Minimum (-32768)
        0x26, 0xFF, 0x7F,  //     Logical Maximum (32767)
        0x75, 0x10,        //     Report Size (16)
        0x95, 0x02,        //     Report Count (2)
        0x81, 0x06,        //     Input (Data,Var,Rel)
        0x09, 0x38,        //     Usage (Wheel)
        0x15, 0x81,        //     Logical Minimum (-127)
        0x25, 0x7F,        //     Logical Maximum (127)
        0x75, 0x08,        //     Report Size (8)
        0x95, 0x01,        //     Report Count (1)
        0x81, 0x06,        //     Input (Data,Var,Rel)
        0xC0,              //   End Collection
        0xC0,              // End Collection
    };
    MouseReportLayout layout;
    CHECK_EQ(static_cast<int>(parse_mouse_report_descriptor(view(descriptor), layout)),
             static_cast<int>(ReportDescriptorError::None));

    MouseNormalizer normalizer;
    normalizer.set_layout(layout);
    Collected out;

    // Report ID 3, buttons 4 and 5 down (bits 3 and 4), no movement, no wheel.
    // The same eight-byte shape the bench measured.
    const std::vector<std::uint8_t> report{0x03, 0x18, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00};
    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 2u);
    CHECK(out.has(InputEventKind::MouseButtonDown, 3));
    CHECK(out.has(InputEventKind::MouseButtonDown, 4));
}

TEST_CASE(the_first_three_buttons_of_a_split_run_still_work) {
    // The half that was never broken, kept honest: a wider button field must
    // not move or reinterpret the buttons that already reached the PC.
    const std::vector<std::uint8_t> descriptor = {
        0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x85, 0x03, 0x09, 0x01,
        0xA1, 0x00, 0x05, 0x09, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01,
        0x19, 0x01, 0x29, 0x03, 0x95, 0x03, 0x81, 0x02,
        0x19, 0x04, 0x29, 0x05, 0x95, 0x02, 0x81, 0x02,
        0x95, 0x03, 0x81, 0x03,
        0x05, 0x01, 0x09, 0x30, 0x09, 0x31,
        0x16, 0x00, 0x80, 0x26, 0xFF, 0x7F, 0x75, 0x10, 0x95, 0x02, 0x81, 0x06,
        0x09, 0x38, 0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x01, 0x81, 0x06,
        0xC0, 0xC0,
    };
    MouseReportLayout layout;
    CHECK_EQ(static_cast<int>(parse_mouse_report_descriptor(view(descriptor), layout)),
             static_cast<int>(ReportDescriptorError::None));

    MouseNormalizer normalizer;
    normalizer.set_layout(layout);
    Collected out;

    // Button 3 alone. Three bits wide it sign-extends to 0xFC and invents both
    // side buttons; five bits wide it is one event.
    const std::vector<std::uint8_t> report{0x03, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00};
    out.count = normalizer.apply(view(report), out.events, kMaxEventsPerReport);

    CHECK_EQ(out.count, 1u);
    CHECK(out.has(InputEventKind::MouseButtonDown, 2));
}
