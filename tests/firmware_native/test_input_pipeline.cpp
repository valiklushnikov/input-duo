// One peripheral, from a raw report to an input event.
//
// The case worth the most care here is the cable being pulled. A device
// unplugged mid-keystroke is holding keys on a computer that will keep holding
// them: the peripheral is gone, so no release will ever arrive from it, and
// the far machine has no way to find out. Somebody yanks a USB cable and their
// other computer types until it is rebooted.

#include "input/pipeline.hpp"
#include "test_support.hpp"

#include <vector>

using duo_input::protocol::ByteView;
using duo_input::u1::ch375::Ch375Event;
using duo_input::u1::ch375::Ch375EventKind;
using duo_input::u1::ch375::boot_mouse_layout;
using duo_input::u1::ch375::DeviceKind;
using duo_input::u1::ch375::MouseReportLayout;
using duo_input::u1::ch375::ReportField;
using duo_input::u1::input::IInputHandler;
using duo_input::u1::input::InputEvent;
using duo_input::u1::input::InputEventKind;
using duo_input::u1::input::InputPipeline;

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

Ch375Event ready_event() {
    Ch375Event event;
    event.kind = Ch375EventKind::Ready;
    return event;
}

Ch375Event report_event(const std::uint8_t* bytes, std::size_t size) {
    Ch375Event event;
    event.kind = Ch375EventKind::Report;
    for (std::size_t index = 0; index < size; ++index) {
        event.report[index] = bytes[index];
    }
    event.report_size = size;
    return event;
}

Ch375Event auxiliary_report_event(std::uint8_t endpoint, const std::uint8_t* bytes,
                                  std::size_t size) {
    Ch375Event event;
    event.kind = Ch375EventKind::AuxiliaryReport;
    event.endpoint = endpoint;
    for (std::size_t index = 0; index < size; ++index) {
        event.report[index] = bytes[index];
    }
    event.report_size = size;
    return event;
}

Ch375Event detached_event() {
    Ch375Event event;
    event.kind = Ch375EventKind::Detached;
    return event;
}

/// A boot keyboard report: modifiers, reserved, six usage slots.
constexpr std::uint8_t kKeyA[] = {0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00};
constexpr std::uint8_t kNoKeys[] = {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00};

/// A boot mouse report: buttons, x, y.
constexpr std::uint8_t kMouseLeftAndRight[] = {0x01, 0x05, 0xFB};

// Captured from Keychron M3 receiver 3434:D030, interface 2 / endpoint 1.
constexpr std::uint16_t kKeychronVendorId = 0x3434;
constexpr std::uint16_t kKeychronProductId = 0xD030;
constexpr std::uint8_t kKeychronSidePress[] = {0x01, 0x01, 0x00, 0x4F,
                                               0x00, 0x00, 0x00, 0x00, 0x03};
constexpr std::uint8_t kKeychronSideRelease[] = {0x01, 0x00, 0x00, 0x00,
                                                 0x00, 0x00, 0x00, 0x00, 0x03};
constexpr std::uint8_t kKeychronSideWithoutModifier[] = {
    0x01, 0x00, 0x00, 0x4F, 0x00, 0x00, 0x00, 0x00, 0x03};
constexpr std::uint8_t kKeychronSidePressZeroTail[] = {
    0x01, 0x01, 0x00, 0x4F, 0x00, 0x00, 0x00, 0x00, 0x00};
constexpr std::uint8_t kKeychronSideReleaseZeroTail[] = {
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00};
constexpr std::uint8_t kKeychronSideUnknownTail[] = {
    0x01, 0x01, 0x00, 0x4F, 0x00, 0x00, 0x00, 0x00, 0x04};
constexpr std::uint8_t kTruncatedKeychronSidePress[] = {
    0x01, 0x01, 0x00, 0x4F, 0x00, 0x00, 0x00, 0x00};

void ready_keychron(InputPipeline& pipeline) {
    pipeline.on_event(ready_event(), DeviceKind::Mouse, boot_mouse_layout(), 1000,
                      kKeychronVendorId, kKeychronProductId);
}

}  // namespace

TEST_CASE(a_keyboard_report_becomes_a_keypress) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), DeviceKind::Keyboard, boot_mouse_layout(), 1000);

    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)), DeviceKind::Keyboard,
                      boot_mouse_layout(), 1000);

    CHECK_EQ(recorder.of(InputEventKind::KeyDown, 0x04), 1);
}

TEST_CASE(a_mouse_report_becomes_motion_and_a_button) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), DeviceKind::Mouse, boot_mouse_layout(), 1000);

    pipeline.on_event(report_event(kMouseLeftAndRight, sizeof(kMouseLeftAndRight)),
                      DeviceKind::Mouse, boot_mouse_layout(), 1000);

    CHECK_EQ(recorder.count(InputEventKind::MouseButtonDown), 1);
    CHECK_EQ(recorder.count(InputEventKind::MouseMove), 1);
}

TEST_CASE(the_keychron_side_shortcut_becomes_mouse_button_four) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    ready_keychron(pipeline);

    pipeline.on_event(auxiliary_report_event(1, kKeychronSidePress,
                                             sizeof(kKeychronSidePress)),
                      DeviceKind::Mouse, boot_mouse_layout(), 1010);
    pipeline.on_event(auxiliary_report_event(1, kKeychronSideRelease,
                                             sizeof(kKeychronSideRelease)),
                      DeviceKind::Mouse, boot_mouse_layout(), 1020);

    CHECK_EQ(recorder.of(InputEventKind::MouseButtonDown, 3), 1);
    CHECK_EQ(recorder.of(InputEventKind::MouseButtonUp, 3), 1);
}

TEST_CASE(an_unrelated_auxiliary_report_is_not_invented_as_a_mouse_button) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), DeviceKind::Mouse, boot_mouse_layout(), 1000);

    pipeline.on_event(auxiliary_report_event(4, kKeychronSidePress,
                                             sizeof(kKeychronSidePress)),
                      DeviceKind::Mouse, boot_mouse_layout(), 1010);

    CHECK_EQ(recorder.count(InputEventKind::MouseButtonDown), 0);
}

TEST_CASE(the_keychron_side_key_is_recognised_when_its_modifier_arrives_separately) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    ready_keychron(pipeline);

    pipeline.on_event(auxiliary_report_event(1, kKeychronSideWithoutModifier,
                                             sizeof(kKeychronSideWithoutModifier)),
                      DeviceKind::Mouse, boot_mouse_layout(), 1010);
    pipeline.on_event(auxiliary_report_event(1, kKeychronSideRelease,
                                             sizeof(kKeychronSideRelease)),
                      DeviceKind::Mouse, boot_mouse_layout(), 1020);

    CHECK_EQ(recorder.of(InputEventKind::MouseButtonDown, 3), 1);
    CHECK_EQ(recorder.of(InputEventKind::MouseButtonUp, 3), 1);
}

TEST_CASE(the_keychron_zero_tail_variant_remains_supported) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    ready_keychron(pipeline);

    pipeline.on_event(auxiliary_report_event(1, kKeychronSidePressZeroTail,
                                             sizeof(kKeychronSidePressZeroTail)),
                      DeviceKind::Mouse, boot_mouse_layout(), 1010);
    pipeline.on_event(auxiliary_report_event(1, kKeychronSideReleaseZeroTail,
                                             sizeof(kKeychronSideReleaseZeroTail)),
                      DeviceKind::Mouse, boot_mouse_layout(), 1020);

    CHECK_EQ(recorder.of(InputEventKind::MouseButtonDown, 3), 1);
    CHECK_EQ(recorder.of(InputEventKind::MouseButtonUp, 3), 1);
}

TEST_CASE(an_unknown_ninth_byte_is_not_invented_as_a_side_button) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    ready_keychron(pipeline);

    pipeline.on_event(auxiliary_report_event(1, kKeychronSideUnknownTail,
                                             sizeof(kKeychronSideUnknownTail)),
                      DeviceKind::Mouse, boot_mouse_layout(), 1010);

    CHECK_EQ(recorder.count(InputEventKind::MouseButtonDown), 0);
}

TEST_CASE(the_keychron_shortcut_is_not_enabled_for_another_device_identity) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), DeviceKind::Mouse, boot_mouse_layout(), 1000,
                      0x1234, 0x5678);

    pipeline.on_event(auxiliary_report_event(1, kKeychronSidePress,
                                             sizeof(kKeychronSidePress)),
                      DeviceKind::Mouse, boot_mouse_layout(), 1010);

    CHECK_EQ(recorder.count(InputEventKind::MouseButtonDown), 0);
}

TEST_CASE(an_eight_byte_prefix_is_not_accepted_as_a_keychron_side_button) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    ready_keychron(pipeline);

    pipeline.on_event(auxiliary_report_event(1, kTruncatedKeychronSidePress,
                                             sizeof(kTruncatedKeychronSidePress)),
                      DeviceKind::Mouse, boot_mouse_layout(), 1010);

    CHECK_EQ(recorder.count(InputEventKind::MouseButtonDown), 0);
}

TEST_CASE(a_report_from_a_device_nobody_identified_is_dropped) {
    Recorder recorder;
    InputPipeline pipeline(recorder);

    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)), DeviceKind::Unknown,
                      boot_mouse_layout(), 1000);

    // Guessing at the layout would put arbitrary keystrokes on somebody's
    // computer. Counted, so that a device this firmware cannot read is
    // visible rather than merely quiet.
    CHECK_EQ(recorder.events.size(), 0u);
    CHECK_EQ(pipeline.unclaimed_reports(), 1u);
}

TEST_CASE(pulling_the_cable_releases_the_key_that_was_held) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), DeviceKind::Keyboard, boot_mouse_layout(), 1000);
    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)), DeviceKind::Keyboard,
                      boot_mouse_layout(), 1000);

    pipeline.on_event(detached_event(), DeviceKind::Keyboard, boot_mouse_layout(), 1100);

    // No release will ever arrive from a device that is gone, and the far
    // computer has no way to work that out for itself.
    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x04), 1);
}

TEST_CASE(pulling_the_cable_releases_the_button_that_was_held) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), DeviceKind::Mouse, boot_mouse_layout(), 1000);
    pipeline.on_event(report_event(kMouseLeftAndRight, sizeof(kMouseLeftAndRight)),
                      DeviceKind::Mouse, boot_mouse_layout(), 1000);

    pipeline.on_event(detached_event(), DeviceKind::Mouse, boot_mouse_layout(), 1100);

    CHECK_EQ(recorder.count(InputEventKind::MouseButtonUp), 1);
}

TEST_CASE(a_key_already_released_is_not_released_again_by_a_disconnect) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), DeviceKind::Keyboard, boot_mouse_layout(), 1000);
    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)), DeviceKind::Keyboard,
                      boot_mouse_layout(), 1000);
    pipeline.on_event(report_event(kNoKeys, sizeof(kNoKeys)), DeviceKind::Keyboard,
                      boot_mouse_layout(), 1010);

    pipeline.on_event(detached_event(), DeviceKind::Keyboard, boot_mouse_layout(), 1100);

    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x04), 1);
}

TEST_CASE(a_controller_fault_releases_what_was_held_too) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), DeviceKind::Keyboard, boot_mouse_layout(), 1000);
    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)), DeviceKind::Keyboard,
                      boot_mouse_layout(), 1000);

    Ch375Event fault;
    fault.kind = Ch375EventKind::Fault;
    pipeline.on_event(fault, DeviceKind::Keyboard, boot_mouse_layout(), 1100);

    // A controller that gave up leaves the same keys held as a cable pulled
    // out of the socket, and the far computer cannot tell the two apart.
    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x04), 1);
}

TEST_CASE(a_report_after_a_disconnect_is_not_read_as_the_old_device) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), DeviceKind::Keyboard, boot_mouse_layout(), 1000);
    pipeline.on_event(detached_event(), DeviceKind::Keyboard, boot_mouse_layout(), 1100);
    recorder.events.clear();

    // A report the old device's layout would have accepted: if the pipeline
    // still believed a keyboard were attached, this would type.
    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)), DeviceKind::Keyboard,
                      boot_mouse_layout(), 1200);

    // Whatever turns up next is not necessarily what was there before, and
    // one device's report read with another's layout is arbitrary input.
    CHECK_EQ(recorder.events.size(), 0u);
    CHECK_EQ(pipeline.unclaimed_reports(), 1u);
}

TEST_CASE(a_device_attaching_is_not_input) {
    Recorder recorder;
    InputPipeline pipeline(recorder);

    Ch375Event attached;
    attached.kind = Ch375EventKind::Attached;
    pipeline.on_event(attached, DeviceKind::Unknown, boot_mouse_layout(), 1000);

    CHECK_EQ(recorder.events.size(), 0u);
}

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
    pipeline.on_event(ready_event(), DeviceKind::Mouse, report_id_wheel_layout(), 1000);

    pipeline.on_event(report_event(kIdentifiedWheelUp, sizeof(kIdentifiedWheelUp)),
                      DeviceKind::Mouse, report_id_wheel_layout(), 1000);

    // Under the boot layout this report is a left click, movement of zero, and
    // no wheel at all - which is the pointer this firmware shipped.
    CHECK_EQ(recorder.count(InputEventKind::Wheel), 1);
    CHECK_EQ(recorder.count(InputEventKind::MouseButtonDown), 0);
    CHECK_EQ(recorder.events.front().wheel, 1);
}

TEST_CASE(movement_in_a_declared_layout_reaches_the_handler_intact) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), DeviceKind::Mouse, report_id_wheel_layout(), 1000);

    pipeline.on_event(report_event(kIdentifiedMove, sizeof(kIdentifiedMove)),
                      DeviceKind::Mouse, report_id_wheel_layout(), 1000);

    CHECK_EQ(recorder.count(InputEventKind::MouseMove), 1);
    CHECK_EQ(recorder.count(InputEventKind::MouseButtonDown), 0);
    CHECK_EQ(recorder.events.front().x, static_cast<std::int16_t>(-10));
    CHECK_EQ(recorder.events.front().y, static_cast<std::int16_t>(79));
}

TEST_CASE(a_layout_that_never_arrived_leaves_the_boot_reader_in_place) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    // What a device whose descriptor could not be fetched comes up as.
    pipeline.on_event(ready_event(), DeviceKind::Mouse, boot_mouse_layout(), 1000);

    pipeline.on_event(report_event(kMouseLeftAndRight, sizeof(kMouseLeftAndRight)),
                      DeviceKind::Mouse, boot_mouse_layout(), 1000);

    CHECK_EQ(recorder.count(InputEventKind::MouseButtonDown), 1);
    CHECK_EQ(recorder.count(InputEventKind::MouseMove), 1);
}

TEST_CASE(a_second_device_is_read_through_its_own_layout) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), DeviceKind::Mouse, report_id_wheel_layout(), 1000);
    pipeline.on_event(detached_event(), DeviceKind::Mouse, report_id_wheel_layout(), 1100);

    // A boot mouse plugged in after one that described itself. Kept, the first
    // layout drops every report whose leading byte is not 1 - which is all of
    // this one's.
    pipeline.on_event(ready_event(), DeviceKind::Mouse, boot_mouse_layout(), 1200);
    pipeline.on_event(report_event(kMouseLeftAndRight, sizeof(kMouseLeftAndRight)),
                      DeviceKind::Mouse, boot_mouse_layout(), 1200);

    CHECK_EQ(recorder.count(InputEventKind::MouseMove), 1);
}

TEST_CASE(a_keyboard_is_not_touched_by_a_mouse_layout) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    // The layout travels with every event whatever the device is, because one
    // pipeline serves either kind. A keyboard must not notice.
    pipeline.on_event(ready_event(), DeviceKind::Keyboard, report_id_wheel_layout(), 1000);

    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)), DeviceKind::Keyboard,
                      report_id_wheel_layout(), 1000);

    CHECK_EQ(recorder.of(InputEventKind::KeyDown, 0x04), 1);
}
