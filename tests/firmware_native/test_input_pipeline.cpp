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
using duo_input::u1::ch375::DeviceKind;
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

}  // namespace

TEST_CASE(a_keyboard_report_becomes_a_keypress) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), DeviceKind::Keyboard, 1000);

    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)), DeviceKind::Keyboard, 1000);

    CHECK_EQ(recorder.of(InputEventKind::KeyDown, 0x04), 1);
}

TEST_CASE(a_mouse_report_becomes_motion_and_a_button) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), DeviceKind::Mouse, 1000);

    pipeline.on_event(report_event(kMouseLeftAndRight, sizeof(kMouseLeftAndRight)),
                      DeviceKind::Mouse, 1000);

    CHECK_EQ(recorder.count(InputEventKind::MouseButtonDown), 1);
    CHECK_EQ(recorder.count(InputEventKind::MouseMove), 1);
}

TEST_CASE(a_report_from_a_device_nobody_identified_is_dropped) {
    Recorder recorder;
    InputPipeline pipeline(recorder);

    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)), DeviceKind::Unknown, 1000);

    // Guessing at the layout would put arbitrary keystrokes on somebody's
    // computer. Counted, so that a device this firmware cannot read is
    // visible rather than merely quiet.
    CHECK_EQ(recorder.events.size(), 0u);
    CHECK_EQ(pipeline.unclaimed_reports(), 1u);
}

TEST_CASE(pulling_the_cable_releases_the_key_that_was_held) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), DeviceKind::Keyboard, 1000);
    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)), DeviceKind::Keyboard, 1000);

    pipeline.on_event(detached_event(), DeviceKind::Keyboard, 1100);

    // No release will ever arrive from a device that is gone, and the far
    // computer has no way to work that out for itself.
    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x04), 1);
}

TEST_CASE(pulling_the_cable_releases_the_button_that_was_held) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), DeviceKind::Mouse, 1000);
    pipeline.on_event(report_event(kMouseLeftAndRight, sizeof(kMouseLeftAndRight)),
                      DeviceKind::Mouse, 1000);

    pipeline.on_event(detached_event(), DeviceKind::Mouse, 1100);

    CHECK_EQ(recorder.count(InputEventKind::MouseButtonUp), 1);
}

TEST_CASE(a_key_already_released_is_not_released_again_by_a_disconnect) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), DeviceKind::Keyboard, 1000);
    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)), DeviceKind::Keyboard, 1000);
    pipeline.on_event(report_event(kNoKeys, sizeof(kNoKeys)), DeviceKind::Keyboard, 1010);

    pipeline.on_event(detached_event(), DeviceKind::Keyboard, 1100);

    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x04), 1);
}

TEST_CASE(a_controller_fault_releases_what_was_held_too) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), DeviceKind::Keyboard, 1000);
    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)), DeviceKind::Keyboard, 1000);

    Ch375Event fault;
    fault.kind = Ch375EventKind::Fault;
    pipeline.on_event(fault, DeviceKind::Keyboard, 1100);

    // A controller that gave up leaves the same keys held as a cable pulled
    // out of the socket, and the far computer cannot tell the two apart.
    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x04), 1);
}

TEST_CASE(a_report_after_a_disconnect_is_not_read_as_the_old_device) {
    Recorder recorder;
    InputPipeline pipeline(recorder);
    pipeline.on_event(ready_event(), DeviceKind::Keyboard, 1000);
    pipeline.on_event(detached_event(), DeviceKind::Keyboard, 1100);
    recorder.events.clear();

    // A report the old device's layout would have accepted: if the pipeline
    // still believed a keyboard were attached, this would type.
    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)), DeviceKind::Keyboard, 1200);

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
    pipeline.on_event(attached, DeviceKind::Unknown, 1000);

    CHECK_EQ(recorder.events.size(), 0u);
}
