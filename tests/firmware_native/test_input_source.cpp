// The backend-neutral boundary InputPipeline reads: SourceEvent and
// SourceIdentity, and InputPipeline::on_event across them.
//
// test_input_pipeline.cpp already proves the pipeline's behaviour in detail -
// captures, layouts, the Keychron shortcut, every release-on-detach case.
// This file proves something narrower and more load-bearing: that the same
// behaviour survives being told through the neutral shape rather than
// ch375::Ch375Event directly, so a second backend that never heard of CH375
// can produce the same events and get the same result.

#include "ch375/descriptor_setup.hpp"
#include "ch375/device.hpp"
#include "fakes/scripted_ch375.hpp"
#include "fakes/multi_report_hid.hpp"
#include "input/ch375_source_adapter.hpp"
#include "input/pipeline.hpp"
#include "input/source.hpp"
#include "test_support.hpp"

#include <cstring>
#include <vector>

using duo_input::protocol::ByteView;
using duo_input::u1::ch375::Ch375Event;
using duo_input::u1::ch375::Ch375EventKind;
using duo_input::u1::ch375::Ch375Transport;
using duo_input::u1::ch375::DescriptorSetup;
using duo_input::u1::ch375::InterruptStatus;
using duo_input::u1::ch375::ReplyProgress;
using duo_input::u1::ch375::SetupProgress;
using duo_input::u1::ch375::testing::FakeCh375Chip;
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
    duo::test::multi_report_hid::set_single_report(identity);
    return identity;
}

SourceIdentity mouse_identity(std::uint16_t vendor_id = 0, std::uint16_t product_id = 0) {
    SourceIdentity identity;
    identity.kind = DeviceKind::Mouse;
    identity.keyboard_layout = boot_keyboard_layout();
    identity.mouse_layout = boot_mouse_layout();
    identity.vendor_id = vendor_id;
    identity.product_id = product_id;
    duo::test::multi_report_hid::set_single_report(identity);
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

/// A minimal mouse report descriptor: buttons, X, Y, wheel. Enough for
/// DescriptorSetup to read a real, non-empty layout and hash out of a
/// device that is not merely on boot protocol - which is the case that
/// distinguishes ``identity()`` actually reading the setup from it handing
/// back an empty default.
std::vector<std::uint8_t> wheel_mouse_descriptor() {
    return {
        0x05, 0x01,  // Usage Page (Generic Desktop)
        0x09, 0x02,  // Usage (Mouse)
        0xA1, 0x01,  // Collection (Application)
        0x09, 0x01,  //   Usage (Pointer)
        0xA1, 0x00,  //   Collection (Physical)
        0x05, 0x09,  //     Usage Page (Button)
        0x19, 0x01,  //     Usage Minimum (Button 1)
        0x29, 0x05,  //     Usage Maximum (Button 5)
        0x15, 0x00,  //     Logical Minimum (0)
        0x25, 0x01,  //     Logical Maximum (1)
        0x95, 0x05,  //     Report Count (5)
        0x75, 0x01,  //     Report Size (1)
        0x81, 0x02,  //     Input (Data,Var,Abs)
        0x95, 0x01,  //     Report Count (1)
        0x75, 0x03,  //     Report Size (3)
        0x81, 0x03,  //     Input (Cnst,Var,Abs)
        0x05, 0x01,  //     Usage Page (Generic Desktop)
        0x09, 0x30,  //     Usage (X)
        0x09, 0x31,  //     Usage (Y)
        0x09, 0x38,  //     Usage (Wheel)
        0x15, 0x81,  //     Logical Minimum (-127)
        0x25, 0x7F,  //     Logical Maximum (127)
        0x75, 0x08,  //     Report Size (8)
        0x95, 0x03,  //     Report Count (3)
        0x81, 0x06,  //     Input (Data,Var,Rel)
        0xC0,        //   End Collection
        0xC0,        // End Collection
    };
}

std::vector<std::uint8_t> consumer_descriptor() {
    return {
        0x05, 0x0C, 0x85, 0x02,
        0x19, 0x01, 0x29, 0x02, 0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x02, 0x81, 0x02,
    };
}

/// Drives a DescriptorSetup exactly the way Ch375Device does, against a
/// FakeCh375Chip standing in for the controller. Copied from the same rig
/// test_ch375_descriptor_setup.cpp uses - this file is not about the setup
/// state machine, only about what Ch375SourceAdapter::identity() reads back
/// out of one that has already reached Done.
struct Rig {
    FakeCh375Chip chip;
    Ch375Transport transport{chip};
    DescriptorSetup setup{transport};

    void begin(std::uint32_t now_us) {
        if (transport.interrupt_pending()) {
            transport.begin_status_read();
            InterruptStatus held = InterruptStatus::Success;
            while (transport.poll_status_read(held) == ReplyProgress::Waiting) {
            }
        }
        setup.begin(now_us);
    }

    SetupProgress settle() {
        for (int pass = 0; pass < 20000; ++pass) {
            bool interrupted = false;
            InterruptStatus status = InterruptStatus::Success;
            if (transport.interrupt_pending()) {
                transport.begin_status_read();
                interrupted = transport.poll_status_read(status) == ReplyProgress::Answered;
            }
            const SetupProgress progress = setup.poll(chip.now_us(), interrupted, status);
            if (progress != SetupProgress::Busy) {
                return progress;
            }
            chip.advance(50);
        }
        return SetupProgress::Busy;
    }
};

bool all_zero(const std::uint8_t* bytes, std::size_t size) {
    for (std::size_t index = 0; index < size; ++index) {
        if (bytes[index] != 0) {
            return false;
        }
    }
    return true;
}

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

// ============================== Ch375SourceAdapter::identity(DescriptorSetup)
//
// Everything above this line hand-builds a SourceIdentity - the pipeline
// tests do not care where one came from. This is the one place that proves
// identity() itself reads a real, settled DescriptorSetup correctly: what it
// is, whose product this is, and the layout and hash it declared. A
// DescriptorSetup is driven to Done against a FakeCh375Chip exactly the way
// Ch375Device drives one on hardware.

TEST_CASE(identity_reads_kind_layout_and_hash_from_a_settled_mouse_setup) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_mouse_with_report_descriptor(wheel_mouse_descriptor(), true);
    rig.begin(rig.chip.now_us());
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));
    CHECK(rig.setup.has_mouse_layout());

    Ch375SourceAdapter adapter(0);
    const SourceIdentity identity = adapter.identity(rig.setup);

    // Read through its own report descriptor, not left on the boot
    // fallback - the whole reason this device has a wheel field at all.
    CHECK(identity.kind == DeviceKind::Mouse);
    CHECK(identity.mouse_layout.wheel.present);
    CHECK(std::memcmp(&identity.mouse_layout, &rig.setup.mouse_layout(),
                      sizeof(identity.mouse_layout)) == 0);
    CHECK_EQ(identity.report_set.count, 1u);
    CHECK_EQ(identity.report_set.entries[0].role,
             duo_input::u1::input::hid::ReportRole::Mouse);
    CHECK(std::memcmp(&identity.report_set, &rig.setup.report_set(),
                      sizeof(identity.report_set)) == 0);

    // A real SHA-256 of a non-empty descriptor is not all zero. If it were
    // copied wrong - or not copied at all - this and the memcmp below could
    // not both hold.
    CHECK(!all_zero(identity.descriptor_hash, sizeof(identity.descriptor_hash)));
    CHECK(std::memcmp(identity.descriptor_hash, rig.setup.report_descriptor_hash(),
                      sizeof(identity.descriptor_hash)) == 0);
}

TEST_CASE(identity_uses_the_first_accepted_report_instead_of_the_configuration_hint) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_report_keyboard(consumer_descriptor());
    rig.begin(rig.chip.now_us());
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));

    Ch375SourceAdapter adapter(0);
    const SourceIdentity identity = adapter.identity(rig.setup);

    CHECK(identity.kind == DeviceKind::Consumer);
    CHECK(identity.keyboard_layout.consumer);
    CHECK_EQ(identity.report_set.count, 1u);
    CHECK_EQ(identity.report_set.entries[0].role,
             duo_input::u1::input::hid::ReportRole::Consumer);
    CHECK_EQ(identity.report_set.entries[0].report_id, 2u);
}

TEST_CASE(identity_preserves_vendor_and_product_ids_from_setup) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.set_device_ids(0x1234, 0x5678);
    rig.chip.serve_boot_mouse();
    rig.begin(rig.chip.now_us());
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));

    Ch375SourceAdapter adapter(0);
    const SourceIdentity identity = adapter.identity(rig.setup);

    // A transposed assignment still compiles and would cause bindings to
    // resolve against the wrong physical source.
    CHECK_EQ(identity.vendor_id, 0x1234);
    CHECK_EQ(identity.product_id, 0x5678);
}
