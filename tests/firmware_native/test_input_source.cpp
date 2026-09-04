// The backend-neutral boundary InputPipeline reads: SourceEvent and
// SourceIdentity, and InputPipeline::on_event across them.
//
// test_input_pipeline.cpp already proves the pipeline's behaviour in detail -
// captures, layouts, the Keychron shortcut, every release-on-detach case.
// This file proves something narrower and more load-bearing: that the same
// behaviour survives being told through the neutral shape rather than
// ch375::Ch375Event directly, so a second backend that never heard of CH375
// can produce the same events and get the same result.

#include "ch375/device.hpp"
#include "input/ch375_source_adapter.hpp"
#include "input/pipeline.hpp"
#include "input/source.hpp"
#include "test_support.hpp"

#include <cstring>
#include <vector>

using duo_input::protocol::ByteView;
using duo_input::u1::ch375::Ch375Event;
using duo_input::u1::ch375::Ch375EventKind;
using duo_input::u1::input::Ch375SourceAdapter;
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

constexpr std::uint16_t kKeychronVendorId = 0x3434;
constexpr std::uint16_t kKeychronProductId = 0xD030;

SourceIdentity keyboard_identity() {
    SourceIdentity identity;
    identity.kind = DeviceKind::Keyboard;
    identity.keyboard_layout = boot_keyboard_layout();
    identity.mouse_layout = boot_mouse_layout();
    return identity;
}

SourceIdentity mouse_identity(std::uint16_t vendor_id = 0, std::uint16_t product_id = 0) {
    SourceIdentity identity;
    identity.kind = DeviceKind::Mouse;
    identity.keyboard_layout = boot_keyboard_layout();
    identity.mouse_layout = boot_mouse_layout();
    identity.vendor_id = vendor_id;
    identity.product_id = product_id;
    return identity;
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

SourceEvent auxiliary_report_event(std::uint8_t endpoint, const std::uint8_t* bytes,
                                   std::size_t size) {
    SourceEvent event;
    event.kind = SourceEventKind::AuxiliaryReport;
    event.endpoint = endpoint;
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

SourceEvent fault_event() {
    SourceEvent event;
    event.kind = SourceEventKind::Fault;
    return event;
}

/// A boot keyboard report: modifiers, reserved, six usage slots.
constexpr std::uint8_t kKeyA[] = {0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00};

/// A boot mouse report: buttons, x, y.
constexpr std::uint8_t kMouseLeftAndRight[] = {0x01, 0x05, 0xFB};

// Captured from Keychron M3 receiver 3434:D030, interface 2 / endpoint 1.
constexpr std::uint8_t kKeychronSidePress[] = {0x01, 0x01, 0x00, 0x4F,
                                               0x00, 0x00, 0x00, 0x00, 0x03};
constexpr std::uint8_t kKeychronSideRelease[] = {0x01, 0x00, 0x00, 0x00,
                                                 0x00, 0x00, 0x00, 0x00, 0x03};

}  // namespace

TEST_CASE(a_neutral_ready_and_report_becomes_a_keypress) {
    Recorder recorder;
    InputPipeline pipeline(recorder);

    pipeline.on_event(ready_event(), keyboard_identity(), 1000);
    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)), keyboard_identity(), 1000);

    CHECK_EQ(recorder.of(InputEventKind::KeyDown, 0x04), 1);
}

TEST_CASE(a_neutral_report_becomes_mouse_motion_and_a_button) {
    Recorder recorder;
    InputPipeline pipeline(recorder);

    pipeline.on_event(ready_event(), mouse_identity(), 1000);
    pipeline.on_event(report_event(kMouseLeftAndRight, sizeof(kMouseLeftAndRight)),
                      mouse_identity(), 1000);

    CHECK_EQ(recorder.count(InputEventKind::MouseButtonDown), 1);
    CHECK_EQ(recorder.count(InputEventKind::MouseMove), 1);
}

TEST_CASE(a_neutral_auxiliary_report_carries_the_keychron_side_button_edge) {
    Recorder recorder;
    InputPipeline pipeline(recorder);

    pipeline.on_event(ready_event(), mouse_identity(kKeychronVendorId, kKeychronProductId), 1000);
    pipeline.on_event(
        auxiliary_report_event(1, kKeychronSidePress, sizeof(kKeychronSidePress)),
        mouse_identity(kKeychronVendorId, kKeychronProductId), 1010);
    pipeline.on_event(
        auxiliary_report_event(1, kKeychronSideRelease, sizeof(kKeychronSideRelease)),
        mouse_identity(kKeychronVendorId, kKeychronProductId), 1020);

    CHECK_EQ(recorder.of(InputEventKind::MouseButtonDown, 3), 1);
    CHECK_EQ(recorder.of(InputEventKind::MouseButtonUp, 3), 1);
}

TEST_CASE(a_neutral_detach_releases_the_key_that_was_held) {
    Recorder recorder;
    InputPipeline pipeline(recorder);

    pipeline.on_event(ready_event(), keyboard_identity(), 1000);
    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)), keyboard_identity(), 1000);
    pipeline.on_event(detached_event(), keyboard_identity(), 1100);

    // No release will ever arrive from a source that is gone, and the far
    // computer has no way to work that out for itself.
    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x04), 1);
}

TEST_CASE(a_neutral_fault_releases_the_key_that_was_held) {
    Recorder recorder;
    InputPipeline pipeline(recorder);

    pipeline.on_event(ready_event(), keyboard_identity(), 1000);
    pipeline.on_event(report_event(kKeyA, sizeof(kKeyA)), keyboard_identity(), 1000);
    pipeline.on_event(fault_event(), keyboard_identity(), 1100);

    // A backend that gave up leaves the same keys held as a cable pulled out
    // of the socket, and the far computer cannot tell the two apart.
    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x04), 1);
}

// ==================================================== Ch375SourceAdapter
//
// The mechanical conversion CH375's own events go through before they cross
// this boundary. Not a second copy of pipeline behaviour - what belongs here
// is only what the adapter itself does: which Ch375EventKind values become a
// SourceEvent, and that the fields it copies land unchanged.

TEST_CASE(the_adapter_produces_no_event_for_a_device_that_has_only_attached) {
    Ch375SourceAdapter adapter(0);
    Ch375Event attached;
    attached.kind = Ch375EventKind::Attached;
    SourceEvent out;

    // Not usable yet. The old CH375-specific on_event ignored this kind
    // outright; the adapter is where that refusal lives now, since the
    // neutral SourceEventKind has no case to hold it in at all.
    CHECK(!adapter.convert(attached, out));
}

TEST_CASE(the_adapter_produces_no_event_for_an_empty_ch375_event) {
    Ch375SourceAdapter adapter(0);
    Ch375Event none;  // Ch375EventKind::None, the default.
    SourceEvent out;

    CHECK(!adapter.convert(none, out));
}

TEST_CASE(the_adapter_carries_the_report_bytes_endpoint_and_timestamp_unchanged) {
    Ch375SourceAdapter adapter(7);
    Ch375Event report;
    report.kind = Ch375EventKind::AuxiliaryReport;
    report.endpoint = 1;
    report.received_us = 123456;
    report.report[0] = 0x01;
    report.report[1] = 0x02;
    report.report[2] = 0x4F;
    report.report_size = 3;
    SourceEvent out;

    CHECK(adapter.convert(report, out));
    CHECK(out.kind == SourceEventKind::AuxiliaryReport);
    CHECK_EQ(out.source_id, 7);
    CHECK_EQ(out.endpoint, 1);
    CHECK_EQ(out.received_us, 123456u);
    CHECK_EQ(out.report_size, 3u);
    CHECK(std::memcmp(out.report, report.report, 3) == 0);
}

TEST_CASE(the_adapter_maps_every_routed_ch375_event_kind_to_its_neutral_counterpart) {
    Ch375SourceAdapter adapter(0);
    SourceEvent out;

    Ch375Event ready;
    ready.kind = Ch375EventKind::Ready;
    CHECK(adapter.convert(ready, out));
    CHECK(out.kind == SourceEventKind::Ready);

    Ch375Event report;
    report.kind = Ch375EventKind::Report;
    CHECK(adapter.convert(report, out));
    CHECK(out.kind == SourceEventKind::Report);

    Ch375Event detached;
    detached.kind = Ch375EventKind::Detached;
    CHECK(adapter.convert(detached, out));
    CHECK(out.kind == SourceEventKind::Detached);

    Ch375Event fault;
    fault.kind = Ch375EventKind::Fault;
    CHECK(adapter.convert(fault, out));
    CHECK(out.kind == SourceEventKind::Fault);
}
