// One peripheral, from a raw report to an input event.
//
// The case worth the most care here is the cable being pulled. A device
// unplugged mid-keystroke is holding keys on a computer that will keep holding
// them: the peripheral is gone, so no release will ever arrive from it, and
// the far machine has no way to find out. Somebody yanks a USB cable and their
// other computer types until it is rebooted.

#include "input/pipeline.hpp"
#include "fakes/multi_report_hid.hpp"
#include "test_support.hpp"

#include <vector>

using duo_input::u1::input::DeviceKind;
using duo_input::u1::input::IInputHandler;
using duo_input::u1::input::InputEvent;
using duo_input::u1::input::InputEventKind;
using duo_input::u1::input::InputPipeline;
using duo_input::u1::input::SourceEvent;
using duo_input::u1::input::SourceEventKind;
using duo_input::u1::input::SourceIdentity;
using duo_input::u1::input::hid::boot_keyboard_layout;
using duo_input::u1::input::hid::boot_mouse_layout;
using duo_input::u1::input::hid::KeyboardFieldKind;
using duo_input::u1::input::hid::KeyboardReportLayout;
using duo_input::u1::input::hid::MouseReportLayout;
using duo_input::u1::input::hid::ReportField;

namespace {

struct Recorder final : IInputHandler {
    std::vector<InputEvent> events;

    void on_input(const InputEvent& event, std::uint32_t) override { events.push_back(event); }

    int count(InputEventKind kind) const {
        int seen = 0;
        for (const InputEvent& event : events) {
            if (event.kind == kind) {
                ++seen;
            }
        }
        return seen;
    }

    int of(InputEventKind kind, std::uint16_t code) const {
        int seen = 0;
        for (const InputEvent& event : events) {
            if (event.kind == kind && event.code == code) {
                ++seen;
            }
        }
        return seen;
    }
};

/// What Ready tells the pipeline. A test builds one of these wherever the old
/// suite passed the kind and two layouts as separate arguments.
SourceIdentity identity(DeviceKind kind, const KeyboardReportLayout& keyboard_layout,
                        const MouseReportLayout& mouse_layout, std::uint16_t vendor_id = 0,
                        std::uint16_t product_id = 0) {
    SourceIdentity out;
    out.kind = kind;
    out.keyboard_layout = keyboard_layout;
    out.mouse_layout = mouse_layout;
    out.vendor_id = vendor_id;
    out.product_id = product_id;
    duo::test::multi_report_hid::set_single_report(out);
    return out;
}

SourceEvent ready_event() {
    SourceEvent event;
    event.kind = SourceEventKind::Ready;
    return event;
}

SourceEvent report_event(const std::uint8_t* bytes, std::size_t size) {
    SourceEvent event;
    event.kind = SourceEventKind::Report;
    for (std::size_t index = 0; index < size; ++index) {
        event.report[index] = bytes[index];
    }
    event.report_size = size;
    return event;
}

SourceEvent detached_event() {
    SourceEvent event;
    event.kind = SourceEventKind::Detached;
    return event;
}

/// A boot keyboard report: modifiers, reserved, six usage slots.
constexpr std::uint8_t kKeyA[] = {0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00};
constexpr std::uint8_t kNoKeys[] = {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00};

/// A boot mouse report: buttons, x, y.
constexpr std::uint8_t kMouseLeftAndRight[] = {0x01, 0x05, 0xFB};

/// The layout of a keyboard that leads every report with an identifier.
///
/// Report 2, one modifier byte, six slots: an eight-byte packet whose body
/// starts at byte one. Read at boot offsets every field is a byte late, and
/// the identifier itself is read as the modifiers.
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

/// Report 2 of that keyboard, holding usage 0x04.
constexpr std::uint8_t kIdentifiedKeyA[] = {0x02, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00};
/// The same keyboard with nothing held.
constexpr std::uint8_t kIdentifiedNoKeys[] = {0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00};

}  // namespace

TEST_CASE(a_keyboard_report_becomes_a_keypress) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(),
                      identity(DeviceKind::Keyboard, boot_keyboard_layout(), boot_mouse_layout()),
                      1000);

    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)),
                      identity(DeviceKind::Keyboard, boot_keyboard_layout(), boot_mouse_layout()),
                      1000);

    CHECK_EQ(recorder.of(InputEventKind::KeyDown, 0x04), 1);
}

TEST_CASE(a_mouse_report_becomes_motion_and_a_button) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(),
                      identity(DeviceKind::Mouse, boot_keyboard_layout(), boot_mouse_layout()),
                      1000);

    pipeline.on_event(report_event(kMouseLeftAndRight, sizeof(kMouseLeftAndRight)),
                      identity(DeviceKind::Mouse, boot_keyboard_layout(), boot_mouse_layout()),
                      1000);

    CHECK_EQ(recorder.count(InputEventKind::MouseButtonDown), 1);
    CHECK_EQ(recorder.count(InputEventKind::MouseMove), 1);
}

TEST_CASE(a_report_from_a_device_nobody_identified_is_dropped) {
    Recorder recorder;
    InputPipeline pipeline(recorder);

    pipeline.on_event(
        report_event(kKeyA, sizeof(kKeyA)),
        identity(DeviceKind::Unknown, boot_keyboard_layout(), boot_mouse_layout()), 1000);

    // Guessing at the layout would put arbitrary keystrokes on somebody's
    // computer. Counted, so that a device this firmware cannot read is
    // visible rather than merely quiet.
    CHECK_EQ(recorder.events.size(), 0u);
    CHECK_EQ(pipeline.unclaimed_reports(), 1u);
}

TEST_CASE(pulling_the_cable_releases_the_key_that_was_held) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(),
                      identity(DeviceKind::Keyboard, boot_keyboard_layout(), boot_mouse_layout()),
                      1000);
    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)),
                      identity(DeviceKind::Keyboard, boot_keyboard_layout(), boot_mouse_layout()),
                      1000);

    pipeline.on_event(detached_event(),
                      identity(DeviceKind::Keyboard, boot_keyboard_layout(), boot_mouse_layout()),
                      1100);

    // No release will ever arrive from a device that is gone, and the far
    // computer has no way to work that out for itself.
    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x04), 1);
}

TEST_CASE(pulling_the_cable_releases_the_button_that_was_held) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(),
                      identity(DeviceKind::Mouse, boot_keyboard_layout(), boot_mouse_layout()),
                      1000);
    pipeline.on_event(report_event(kMouseLeftAndRight, sizeof(kMouseLeftAndRight)),
                      identity(DeviceKind::Mouse, boot_keyboard_layout(), boot_mouse_layout()),
                      1000);

    pipeline.on_event(detached_event(),
                      identity(DeviceKind::Mouse, boot_keyboard_layout(), boot_mouse_layout()),
                      1100);

    CHECK_EQ(recorder.count(InputEventKind::MouseButtonUp), 1);
}

TEST_CASE(a_key_already_released_is_not_released_again_by_a_disconnect) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(),
                      identity(DeviceKind::Keyboard, boot_keyboard_layout(), boot_mouse_layout()),
                      1000);
    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)),
                      identity(DeviceKind::Keyboard, boot_keyboard_layout(), boot_mouse_layout()),
                      1000);
    pipeline.on_event(report_event(kNoKeys, sizeof(kNoKeys)),
                      identity(DeviceKind::Keyboard, boot_keyboard_layout(), boot_mouse_layout()),
                      1010);

    pipeline.on_event(detached_event(),
                      identity(DeviceKind::Keyboard, boot_keyboard_layout(), boot_mouse_layout()),
                      1100);

    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x04), 1);
}

TEST_CASE(a_controller_fault_releases_what_was_held_too) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(),
                      identity(DeviceKind::Keyboard, boot_keyboard_layout(), boot_mouse_layout()),
                      1000);
    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)),
                      identity(DeviceKind::Keyboard, boot_keyboard_layout(), boot_mouse_layout()),
                      1000);

    SourceEvent fault;
    fault.kind = SourceEventKind::Fault;
    pipeline.on_event(fault,
                      identity(DeviceKind::Keyboard, boot_keyboard_layout(), boot_mouse_layout()),
                      1100);

    // A controller that gave up leaves the same keys held as a cable pulled
    // out of the socket, and the far computer cannot tell the two apart.
    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x04), 1);
}

TEST_CASE(a_report_after_a_disconnect_is_not_read_as_the_old_device) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(),
                      identity(DeviceKind::Keyboard, boot_keyboard_layout(), boot_mouse_layout()),
                      1000);
    pipeline.on_event(detached_event(),
                      identity(DeviceKind::Keyboard, boot_keyboard_layout(), boot_mouse_layout()),
                      1100);
    recorder.events.clear();

    // A report the old device's layout would have accepted: if the pipeline
    // still believed a keyboard were attached, this would type.
    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)),
                      identity(DeviceKind::Keyboard, boot_keyboard_layout(), boot_mouse_layout()),
                      1200);

    // Whatever turns up next is not necessarily what was there before, and
    // one device's report read with another's layout is arbitrary input.
    CHECK_EQ(recorder.events.size(), 0u);
    CHECK_EQ(pipeline.unclaimed_reports(), 1u);
}

// "A device attaching is not input" moved out of this file: SourceEventKind
// has no Attached case at all - not usable yet is no longer a state the
// pipeline can be told about, so there is nothing for InputPipeline to prove
// here any more. Ch375SourceAdapter's refusal to produce an event for
// ch375::Ch375EventKind::Attached is covered in test_input_source.cpp,
// against the type that still has to represent it.

// ============================================= the layout the device declared
//
// A mouse read through its own report descriptor sends a report that is not a
// boot report: an identifier in front, sixteen-bit axes, a wheel. The layout
// arrives with Ready, which is the moment the device has been identified and
// the moment before its first report.
//
// SYNTHETIC. Nothing on this bench has ever been asked for its report
// descriptor, so these layouts are written to the shape one declares.

namespace {

/// Identifier, buttons, sixteen-bit X and Y, wheel: a seven-byte report.
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

/// Buttons, X, Y, wheel - the seven-byte report above without its identifier.
constexpr std::uint8_t kIdentifiedWheelUp[] = {0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01};
constexpr std::uint8_t kIdentifiedMove[] = {0x01, 0x00, 0xF6, 0xFF, 0x4F, 0x00, 0x00};

}  // namespace

TEST_CASE(a_mouse_is_read_through_the_layout_it_was_declared_with) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(
        ready_event(),
        identity(DeviceKind::Mouse, boot_keyboard_layout(), report_id_wheel_layout()), 1000);

    pipeline.on_event(
        report_event(kIdentifiedWheelUp, sizeof(kIdentifiedWheelUp)),
        identity(DeviceKind::Mouse, boot_keyboard_layout(), report_id_wheel_layout()), 1000);

    // Under the boot layout this report is a left click, movement of zero, and
    // no wheel at all - which is the pointer this firmware shipped.
    CHECK_EQ(recorder.count(InputEventKind::Wheel), 1);
    CHECK_EQ(recorder.count(InputEventKind::MouseButtonDown), 0);
    if (!recorder.events.empty()) CHECK_EQ(recorder.events.front().wheel, 1);
}

TEST_CASE(movement_in_a_declared_layout_reaches_the_handler_intact) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(
        ready_event(),
        identity(DeviceKind::Mouse, boot_keyboard_layout(), report_id_wheel_layout()), 1000);

    pipeline.on_event(
        report_event(kIdentifiedMove, sizeof(kIdentifiedMove)),
        identity(DeviceKind::Mouse, boot_keyboard_layout(), report_id_wheel_layout()), 1000);

    CHECK_EQ(recorder.count(InputEventKind::MouseMove), 1);
    CHECK_EQ(recorder.count(InputEventKind::MouseButtonDown), 0);
    if (!recorder.events.empty()) {
        CHECK_EQ(recorder.events.front().x, static_cast<std::int16_t>(-10));
        CHECK_EQ(recorder.events.front().y, static_cast<std::int16_t>(79));
    }
}

TEST_CASE(a_layout_that_never_arrived_leaves_the_boot_reader_in_place) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    // What a device whose descriptor could not be fetched comes up as.
    pipeline.on_event(ready_event(),
                      identity(DeviceKind::Mouse, boot_keyboard_layout(), boot_mouse_layout()),
                      1000);

    pipeline.on_event(report_event(kMouseLeftAndRight, sizeof(kMouseLeftAndRight)),
                      identity(DeviceKind::Mouse, boot_keyboard_layout(), boot_mouse_layout()),
                      1000);

    CHECK_EQ(recorder.count(InputEventKind::MouseButtonDown), 1);
    CHECK_EQ(recorder.count(InputEventKind::MouseMove), 1);
}

TEST_CASE(a_second_device_is_read_through_its_own_layout) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(
        ready_event(),
        identity(DeviceKind::Mouse, boot_keyboard_layout(), report_id_wheel_layout()), 1000);
    pipeline.on_event(
        detached_event(),
        identity(DeviceKind::Mouse, boot_keyboard_layout(), report_id_wheel_layout()), 1100);

    // A boot mouse plugged in after one that described itself. Kept, the first
    // layout drops every report whose leading byte is not 1 - which is all of
    // this one's.
    pipeline.on_event(ready_event(),
                      identity(DeviceKind::Mouse, boot_keyboard_layout(), boot_mouse_layout()),
                      1200);
    pipeline.on_event(report_event(kMouseLeftAndRight, sizeof(kMouseLeftAndRight)),
                      identity(DeviceKind::Mouse, boot_keyboard_layout(), boot_mouse_layout()),
                      1200);

    CHECK_EQ(recorder.count(InputEventKind::MouseMove), 1);
}

TEST_CASE(a_keyboard_is_not_touched_by_a_mouse_layout) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    // The layout travels with every event whatever the device is, because one
    // pipeline serves either kind. A keyboard must not notice.
    pipeline.on_event(
        ready_event(),
        identity(DeviceKind::Keyboard, boot_keyboard_layout(), report_id_wheel_layout()), 1000);

    pipeline.on_event(
        report_event(kKeyA, sizeof(kKeyA)),
        identity(DeviceKind::Keyboard, boot_keyboard_layout(), report_id_wheel_layout()), 1000);

    CHECK_EQ(recorder.of(InputEventKind::KeyDown, 0x04), 1);
}

TEST_CASE(a_report_id_keyboard_is_read_through_the_layout_it_declared) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(
        ready_event(),
        identity(DeviceKind::Keyboard, report_id_keyboard_layout(), boot_mouse_layout()), 1000);

    pipeline.on_event(
        report_event(kIdentifiedKeyA, sizeof(kIdentifiedKeyA)),
        identity(DeviceKind::Keyboard, report_id_keyboard_layout(), boot_mouse_layout()), 1010);
    pipeline.on_event(
        report_event(kIdentifiedNoKeys, sizeof(kIdentifiedNoKeys)),
        identity(DeviceKind::Keyboard, report_id_keyboard_layout(), boot_mouse_layout()), 1020);

    // At boot offsets the identifier is the modifier byte - 2 is left shift -
    // and the key lands a slot early. What arrives on the far computer is a
    // shifted letter nobody typed and a shift that is never released.
    CHECK_EQ(recorder.of(InputEventKind::KeyDown, 0x04), 1);
    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x04), 1);
    CHECK_EQ(recorder.count(InputEventKind::KeyDown), 1);
    CHECK_EQ(recorder.of(InputEventKind::KeyDown, 0xE1), 0);
}

TEST_CASE(a_keyboard_layout_does_not_outlive_the_keyboard_that_declared_it) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(
        ready_event(),
        identity(DeviceKind::Keyboard, report_id_keyboard_layout(), boot_mouse_layout()), 1000);
    pipeline.on_event(
        detached_event(),
        identity(DeviceKind::Keyboard, report_id_keyboard_layout(), boot_mouse_layout()), 1100);

    // A boot keyboard is plugged in next. Kept, the first one's layout would
    // drop every report of it - none of them carries identifier 2.
    pipeline.on_event(ready_event(),
                      identity(DeviceKind::Keyboard, boot_keyboard_layout(), boot_mouse_layout()),
                      1200);
    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)),
                      identity(DeviceKind::Keyboard, boot_keyboard_layout(), boot_mouse_layout()),
                      1210);

    CHECK_EQ(recorder.of(InputEventKind::KeyDown, 0x04), 1);
}

TEST_CASE(keychron_side_button_uses_declared_id_and_preserves_exact_shortcut_order) {
    using namespace duo::test::multi_report_hid;
    Recorder recorder;
    InputPipeline pipeline(recorder);
    const auto source = identity();
    pipeline.on_event(ready_event(), source, 0);
    pipeline.on_report({kSidePress, sizeof(kSidePress)}, 1);
    CHECK_EQ(recorder.events.size(), 2u);
    pipeline.on_report({kSideRelease, sizeof(kSideRelease)}, 2);
    CHECK_EQ(recorder.events.size(), 4u);
    if (recorder.events.size() == 4) {
        CHECK_EQ(recorder.events[0].kind, InputEventKind::KeyDown);
        CHECK_EQ(recorder.events[0].code, 0xE0);
        CHECK_EQ(recorder.events[1].kind, InputEventKind::KeyDown);
        CHECK_EQ(recorder.events[1].code, 0x4F);
        CHECK_EQ(recorder.events[2].kind, InputEventKind::KeyUp);
        CHECK_EQ(recorder.events[2].code, 0x4F);
        CHECK_EQ(recorder.events[3].kind, InputEventKind::KeyUp);
        CHECK_EQ(recorder.events[3].code, 0xE0);
    }
    CHECK_EQ(recorder.of(InputEventKind::KeyDown, 0x03), 0);
    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x03), 0);
}

TEST_CASE(consumer_id_2_never_releases_ctrl_right_held_by_keyboard_id_1) {
    using namespace duo::test::multi_report_hid;
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), identity(), 0);
    pipeline.on_report({kSidePress, sizeof(kSidePress)}, 1);
    CHECK_EQ(recorder.count(InputEventKind::KeyDown), 2);
    recorder.events.clear();
    // Padding is legal; a broken broadcast can read a complete keyboard body.
    const std::uint8_t consumer_press[21] = {2, 0xE9};
    const std::uint8_t consumer_release[21] = {2};
    pipeline.on_report({consumer_press, sizeof(consumer_press)}, 2);
    pipeline.on_report({consumer_release, sizeof(consumer_release)}, 3);
    CHECK_EQ(recorder.events.size(), 2u);
    CHECK_EQ(recorder.of(InputEventKind::ConsumerDown, 0xE9), 1);
    CHECK_EQ(recorder.of(InputEventKind::ConsumerUp, 0xE9), 1);
    CHECK_EQ(recorder.count(InputEventKind::KeyUp), 0);
    CHECK_EQ(recorder.count(InputEventKind::KeyDown), 0);
    pipeline.on_report({kSideRelease, sizeof(kSideRelease)}, 4);
    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x4F), 1);
    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0xE0), 1);
}

TEST_CASE(keyboard_id_12_keeps_its_keys_and_modifiers_independent_of_id_1) {
    using namespace duo::test::multi_report_hid;
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), identity(), 0);
    pipeline.on_report({kSidePress, sizeof(kSidePress)}, 1);
    pipeline.on_report({kBitmapPress, sizeof(kBitmapPress)}, 2);
    CHECK_EQ(recorder.count(InputEventKind::KeyDown), 4);
    CHECK_EQ(recorder.count(InputEventKind::KeyUp), 0);
    recorder.events.clear();
    pipeline.on_report({kSideRelease, sizeof(kSideRelease)}, 3);
    CHECK_EQ(recorder.events.size(), 2u);
    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x04), 0);
    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0xE1), 0);
    pipeline.on_report({kBitmapRelease, sizeof(kBitmapRelease)}, 4);
    CHECK_EQ(recorder.events.size(), 4u);
    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x04), 1);
    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0xE1), 1);
}

TEST_CASE(detach_and_fault_release_every_report_slot_and_reconnect_starts_empty) {
    using namespace duo::test::multi_report_hid;
    for (const auto terminal : {SourceEventKind::Detached, SourceEventKind::Fault}) {
        Recorder recorder;
        InputPipeline pipeline(recorder);
        const auto source = identity();
        pipeline.on_event(ready_event(), source, 0);
        pipeline.on_report({kSidePress, sizeof(kSidePress)}, 1);
        pipeline.on_report({kConsumerPress, sizeof(kConsumerPress)}, 2);
        pipeline.on_report({kBitmapPress, sizeof(kBitmapPress)}, 3);
        recorder.events.clear();
        auto event = detached_event();
        event.kind = terminal;
        pipeline.on_event(event, source, 4);
        CHECK_EQ(recorder.events.size(), 5u);
        CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x4F), 1);
        CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0xE0), 1);
        CHECK_EQ(recorder.of(InputEventKind::ConsumerUp, 0xE9), 1);
        CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x04), 1);
        CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0xE1), 1);
        recorder.events.clear();
        pipeline.on_event(ready_event(), source, 5);
        pipeline.on_report({kSideRelease, sizeof(kSideRelease)}, 6);
        pipeline.on_report({kConsumerRelease, sizeof(kConsumerRelease)}, 7);
        pipeline.on_report({kBitmapRelease, sizeof(kBitmapRelease)}, 8);
        CHECK(recorder.events.empty());
        pipeline.on_report({kSidePress, sizeof(kSidePress)}, 9);
        pipeline.on_report({kConsumerPress, sizeof(kConsumerPress)}, 10);
        pipeline.on_report({kBitmapPress, sizeof(kBitmapPress)}, 11);
        CHECK_EQ(recorder.events.size(), 5u);
    }
}

TEST_CASE(ready_resets_all_decoder_states_without_inventing_releases) {
    using namespace duo::test::multi_report_hid;
    Recorder recorder;
    InputPipeline pipeline(recorder);
    const auto source = identity();
    pipeline.on_event(ready_event(), source, 0);
    pipeline.on_report({kSidePress, sizeof(kSidePress)}, 1);
    pipeline.on_report({kConsumerPress, sizeof(kConsumerPress)}, 2);
    pipeline.on_report({kBitmapPress, sizeof(kBitmapPress)}, 3);
    recorder.events.clear();
    pipeline.on_event(ready_event(), source, 4);
    CHECK(recorder.events.empty());
    pipeline.on_report({kSidePress, sizeof(kSidePress)}, 5);
    pipeline.on_report({kConsumerPress, sizeof(kConsumerPress)}, 6);
    pipeline.on_report({kBitmapPress, sizeof(kBitmapPress)}, 7);
    CHECK_EQ(recorder.events.size(), 5u);
    CHECK_EQ(recorder.count(InputEventKind::KeyUp), 0);
}

TEST_CASE(auxiliary_and_primary_reports_use_the_same_report_id_dispatch) {
    using namespace duo::test::multi_report_hid;
    Recorder recorder;
    InputPipeline pipeline(recorder);
    const auto source = identity();
    pipeline.on_event(ready_event(), source, 0);
    auto press = report_event(kSidePress, sizeof(kSidePress));
    press.kind = SourceEventKind::AuxiliaryReport;
    pipeline.on_event(press, source, 1);
    pipeline.on_event(report_event(kSideRelease, sizeof(kSideRelease)), source, 2);
    CHECK_EQ(recorder.events.size(), 4u);
    CHECK_EQ(recorder.of(InputEventKind::KeyDown, 0x4F), 1);
    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x4F), 1);
}

TEST_CASE(unnumbered_boot_and_descriptor_mouse_reports_dispatch_motion_exactly_once) {
    const std::uint8_t descriptor[] = {0x05,0x01,0x09,0x02,0xA1,0x01,
        0x05,0x09,0x19,0x01,0x29,0x08,0x15,0x00,0x25,0x01,
        0x75,0x01,0x95,0x08,0x81,0x02,0x05,0x01,
        0x09,0x30,0x09,0x31,0x15,0x81,0x25,0x7F,0x75,0x08,0x95,0x02,0x81,0x06,0xC0};
    SourceIdentity declared;
    CHECK_EQ(duo_input::u1::input::hid::parse_hid_report_set(
                 {descriptor, sizeof(descriptor)}, declared.report_set),
             duo_input::u1::input::hid::ReportDescriptorError::None);
    CHECK_EQ(declared.report_set.count, 1);
    declared.kind = DeviceKind::Mouse;
    declared.mouse_layout = declared.report_set.entries[0].mouse;
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), declared, 0);
    const std::uint8_t move[] = {0, 5, 6};
    pipeline.on_report({move, sizeof(move)}, 1);
    CHECK_EQ(recorder.count(InputEventKind::MouseMove), 1);
    pipeline.on_event(detached_event(), declared, 2);
    pipeline.set_kind(DeviceKind::Mouse, boot_keyboard_layout(), boot_mouse_layout());
    const std::uint8_t boot_move[] = {0, 5, 6};
    pipeline.on_report({boot_move, sizeof(boot_move)}, 3);
    CHECK_EQ(recorder.count(InputEventKind::MouseMove), 2);
}

TEST_CASE(empty_and_unknown_numbered_packets_never_fall_back_to_boot_offsets) {
    using namespace duo::test::multi_report_hid;
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), identity(), 0);
    const std::uint8_t unknown[] = {0x7F, 0x01, 0x00, 0x4F, 0, 0, 0, 0, 0};
    pipeline.on_report({nullptr, 0}, 1);
    pipeline.on_report({unknown, 0}, 2);
    pipeline.on_report({unknown, sizeof(unknown)}, 3);
    CHECK(recorder.events.empty());
}
