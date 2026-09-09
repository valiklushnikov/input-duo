// A TinyUSB keyboard's reports, from a fake host callback to a queued
// command - proving the PIO backend feeds the same InputPipeline the CH375
// adapter always has, and that nothing above the neutral source boundary can
// tell which backend produced an event.
//
// The oversized-report case is the one Task 4 flagged and Task 8 owns: a
// report longer than the boundary's 64 bytes must become a Fault - a
// release-all - never a trimmed-and-routed report. CH375's own report buffer
// is exactly 64 bytes, so that path is unreachable there; here it is real,
// and this file is where it is proven.

#include "core1_runtime.hpp"
#include "fakes/tinyusb_host.hpp"
#include "fakes/multi_report_hid.hpp"
#include "input/pipeline.hpp"
#include "pio_usb/backend.hpp"
#include "pio_usb/device_registry.hpp"
#include "test_support.hpp"

#include <array>
#include <cstdint>
#include <cstring>
#include <vector>

using duo::test::tinyusb_host::kProtocolKeyboard;
using duo::test::tinyusb_host::kProtocolMouse;
using duo::test::tinyusb_host::kProtocolNone;
using duo_input::config::KeyboardRoute;
using duo_input::config::MouseRoute;
using duo_input::runtime::CommandKind;
using duo_input::runtime::kPhysicalOwner;
using duo_input::runtime::OutputCommand;
using duo_input::u1::Core1Runtime;
using duo_input::u1::ICommandSink;
using duo_input::u1::IProfileSource;
using duo_input::u1::input::IInputHandler;
using duo_input::u1::input::DeviceKind;
using duo_input::u1::input::InputEvent;
using duo_input::u1::input::InputEventKind;
using duo_input::u1::input::InputPipeline;
using duo_input::u1::input::SourceEvent;
using duo_input::u1::input::SourceEventKind;
using duo_input::u1::input::SourceIdentity;
using duo_input::u1::mapping::Binding;
using duo_input::u1::pio_usb::DeviceRegistry;
using duo_input::u1::pio_usb::PioUsbBackend;

namespace {

constexpr std::uint8_t kKeyboardAddress = 2;
constexpr std::uint8_t kKeyboardInstance = 0;
constexpr std::uint16_t kVendorId = 0x1234;
constexpr std::uint16_t kProductId = 0x5678;

/// A second role-owned source on the same host. V1 accepts exactly two, and
/// several of the requirements below - the queue's Fault reservation, the
/// per-report capture timestamp - are only observable with both present.
constexpr std::uint8_t kMouseAddress = 3;
constexpr std::uint8_t kMouseInstance = 0;
constexpr std::uint16_t kMouseProductId = 0x9ABC;

/// Boot mouse: buttons byte, then X and Y. Button 0 is bit 0.
constexpr std::uint8_t kMouseButton1Down[] = {0x01, 0x00, 0x00};

/// A boot keyboard report: modifiers, reserved, six usage slots.
constexpr std::uint8_t kKeyA[] = {0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00};
constexpr std::uint8_t kNoKeys[] = {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00};

/// Report 2's descriptor: modifiers as one byte, then six one-byte usage
/// slots, exactly the layout test_pio_usb_hid_setup.cpp's own
/// report_id_keyboard() fixture already proves classify_hid reads correctly.
std::vector<std::uint8_t> report_id_keyboard_descriptor() {
    return {
        0x05, 0x0C, 0x85, 0x01,
        0x09, 0x01, 0x75, 0x08, 0x95, 0x04, 0x81, 0x02,
        0x05, 0x07, 0x85, 0x02,
        0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
        0x19, 0x00, 0x29, 0x65, 0x15, 0x00, 0x25, 0x65,
        0x75, 0x08, 0x95, 0x06, 0x81, 0x00,
    };
}
constexpr std::uint8_t kIdentifiedKeyA[] = {0x02, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00};
constexpr std::uint8_t kIdentifiedNoKeys[] = {0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00};

/// The NKRO bitmap descriptor test_report_descriptor.cpp's
/// an_nkro_bitmap_records_its_usage_range already proves parses to
/// KeyboardFieldKind::Bitmap, 112 usages from 0x04, 15-byte body.
std::vector<std::uint8_t> nkro_bitmap_descriptor() {
    return {
        0x05, 0x07,
        0x19, 0xE0, 0x29, 0xE7,
        0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
        0x19, 0x04, 0x29, 0x73,
        0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x70, 0x81, 0x02,
    };
}

/// A keyboard whose descriptor declares eight modifier bits and only THREE
/// one-byte key slots - a four-byte report - while the device goes on sending
/// eight-byte boot-shaped reports. This is the brief's "report larger than
/// the declared layout": longer than what the descriptor declared, but well
/// inside kMaxSourceReportBytes, so it is not the oversized-report Fault case.
std::vector<std::uint8_t> three_slot_keyboard_descriptor() {
    return {
        0x05, 0x07,
        0x19, 0xE0, 0x29, 0xE7,
        0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
        0x19, 0x00, 0x29, 0x65,
        0x15, 0x00, 0x25, 0x65,
        0x75, 0x08, 0x95, 0x03, 0x81, 0x00,
    };
}

/// Usage 0x04 ('a') pressed: bit 0 of the bitmap, byte 1 of the body.
constexpr std::uint8_t kNkroKeyA[15] = {0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                                        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00};
constexpr std::uint8_t kNkroNoKeys[15] = {};

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

/// A PioUsbBackend wired to the fake TinyUSB host, plus the real
/// InputPipeline and Recorder every drained event is fed through - the same
/// shape main.cpp's Core 1 loop drives it in.
struct KeyboardRig {
    PioUsbBackend backend;
    Recorder recorder;
    InputPipeline pipeline{recorder};
    std::uint32_t now_us = 1000;

    KeyboardRig() {
        duo::test::tinyusb_host::reset();
        backend.begin();
    }

    void mount(const std::uint8_t* descriptor, std::size_t descriptor_size,
              std::uint8_t protocol = kProtocolNone) {
        duo::test::tinyusb_host::add_device(kKeyboardAddress, kVendorId, kProductId);
        tuh_mount_cb(kKeyboardAddress);
        duo::test::tinyusb_host::set_protocol(kKeyboardAddress, kKeyboardInstance, protocol);
        tuh_hid_mount_cb(kKeyboardAddress, kKeyboardInstance, descriptor,
                        static_cast<std::uint16_t>(descriptor_size));
        pump();
    }

    void mount_boot_keyboard() { mount(nullptr, 0, kProtocolKeyboard); }

    /// Deliver one report and drain whatever it produces through the
    /// pipeline, exactly as main.cpp's while(take_event()) loop does.
    ///
    /// ``captured_us`` is what the fake host's clock reads at the moment the
    /// callback fires - the capture time the callback stamps onto the record,
    /// deliberately unrelated to the ``now_us`` pump() hands task().
    void report(const std::uint8_t* bytes, std::size_t size,
                std::uint32_t captured_us = 0) {
        duo::test::tinyusb_host::set_now_us(captured_us);
        tuh_hid_report_received_cb(kKeyboardAddress, kKeyboardInstance, bytes,
                                   static_cast<std::uint16_t>(size));
        pump();
    }

    /// task() plus a full drain, without delivering a report - used to
    /// process a mount or to observe a Fault with nothing else pending.
    void pump() {
        now_us += 1000;
        backend.task(now_us);
        SourceEvent event;
        SourceIdentity identity;
        while (backend.take_event(event, identity)) {
            pipeline.on_event(event, identity, now_us / 1000);
        }
    }

    std::size_t receive_count() const {
        return duo::test::tinyusb_host::receive_count(kKeyboardAddress, kKeyboardInstance);
    }
};

/// A keyboard AND a mouse behind one backend, each with its own
/// InputPipeline, routed by PioUsbBackend::logical_port exactly the way
/// main.cpp's Core 1 loop routes take_event()'s output to one of its two
/// pipelines. Both interfaces are mounted, Ready-drained and armed by the
/// time the constructor returns.
struct TwoSourceRig {
    PioUsbBackend backend;
    Recorder keyboard_recorder;
    Recorder mouse_recorder;
    InputPipeline keyboard_pipeline{keyboard_recorder};
    InputPipeline mouse_pipeline{mouse_recorder};
    std::uint32_t now_us = 1000;

    TwoSourceRig() {
        duo::test::tinyusb_host::reset();
        backend.begin();

        duo::test::tinyusb_host::add_device(kKeyboardAddress, kVendorId, kProductId);
        tuh_mount_cb(kKeyboardAddress);
        duo::test::tinyusb_host::set_protocol(kKeyboardAddress, kKeyboardInstance,
                                              kProtocolKeyboard);
        tuh_hid_mount_cb(kKeyboardAddress, kKeyboardInstance, nullptr, 0);

        duo::test::tinyusb_host::add_device(kMouseAddress, kVendorId, kMouseProductId);
        tuh_mount_cb(kMouseAddress);
        duo::test::tinyusb_host::set_protocol(kMouseAddress, kMouseInstance, kProtocolMouse);
        tuh_hid_mount_cb(kMouseAddress, kMouseInstance, nullptr, 0);

        pump();
    }

    ~TwoSourceRig() { duo_input::u1::pio_usb::set_callback_registry(nullptr); }

    /// One report callback, with the capture clock reading ``captured_us``.
    /// No task(), no drain: several of these can share one Core 1 pass.
    void deliver(std::uint8_t address, std::uint8_t instance, const std::uint8_t* bytes,
                 std::size_t size, std::uint32_t captured_us) {
        duo::test::tinyusb_host::set_now_us(captured_us);
        tuh_hid_report_received_cb(address, instance, bytes,
                                   static_cast<std::uint16_t>(size));
    }

    /// One Core 1 pass: task() only. Whatever it queued stays queued.
    void task() {
        now_us += 1000;
        backend.task(now_us);
    }

    /// Drain every queued event into the pipeline its identity belongs to -
    /// the loop main.cpp runs after each task().
    void drain() {
        SourceEvent event;
        SourceIdentity identity;
        while (backend.take_event(event, identity)) {
            switch (PioUsbBackend::logical_port(identity.kind)) {
                case 0:
                    keyboard_pipeline.on_event(event, identity, now_us / 1000);
                    break;
                case 1:
                    mouse_pipeline.on_event(event, identity, now_us / 1000);
                    break;
                default:
                    break;
            }
        }
    }

    void pump() {
        task();
        drain();
    }
};

}  // namespace

TEST_CASE(boot_6kro_press_and_release_become_keydown_and_keyup) {
    KeyboardRig rig;
    rig.mount_boot_keyboard();

    rig.report(kKeyA, sizeof(kKeyA));
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x04), 1);

    rig.report(kNoKeys, sizeof(kNoKeys));
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyUp, 0x04), 1);
}

TEST_CASE(pio_keychron_real_descriptor_delivers_exact_side_button_order_and_capture_identity) {
    using namespace duo::test::multi_report_hid;
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();
    duo_input::u1::mapping::CaptureController capture;
    struct CapturingHandler final : IInputHandler {
        duo_input::u1::mapping::CaptureController& capture;
        std::vector<InputEvent> events;
        explicit CapturingHandler(duo_input::u1::mapping::CaptureController& value) : capture(value) {}
        void on_input(const InputEvent& event, std::uint32_t) override {
            events.push_back(event);
            capture.handle(event);
        }
    } handler(capture);
    duo_input::u1::input::SourceTable sources(handler);
    capture.set_sources(sources);
    const auto bytes = descriptor();
    duo::test::tinyusb_host::add_device(4, 0x3434, 0xD030);
    tuh_mount_cb(4);
    duo::test::tinyusb_host::set_protocol(4, 2, kProtocolKeyboard);
    tuh_hid_mount_cb(4, 2, bytes.data(), static_cast<std::uint16_t>(bytes.size()));
    auto pump = [&](std::uint32_t now) {
        backend.task(now * 1000);
        SourceEvent event;
        SourceIdentity source;
        while (backend.take_event(event, source)) sources.on_event(event, source, now);
    };
    pump(1);
    capture.begin(1);
    tuh_hid_report_received_cb(4, 2, kSidePress, sizeof(kSidePress));
    pump(2);
    CHECK_EQ(handler.events.size(), 2u);
    duo_input::u1::mapping::CapturedTrigger trigger;
    CHECK(capture.take(trigger));
    CHECK_EQ(trigger.kind, duo_input::config::TriggerKind::KEYBOARD_USAGE);
    CHECK_EQ(trigger.code, 0x4F);
    CHECK_EQ(trigger.modifiers, 0x01);
    CHECK_EQ(trigger.vendor_id, 0x3434);
    CHECK_EQ(trigger.product_id, 0xD030);
    CHECK_EQ(trigger.interface_number, 2);
    tuh_hid_report_received_cb(4, 2, kSideRelease, sizeof(kSideRelease));
    pump(3);
    CHECK_EQ(handler.events.size(), 4u);
    if (handler.events.size() == 4) {
        CHECK_EQ(handler.events[0].kind, InputEventKind::KeyDown);
        CHECK_EQ(handler.events[0].code, 0xE0);
        CHECK_EQ(handler.events[1].kind, InputEventKind::KeyDown);
        CHECK_EQ(handler.events[1].code, 0x4F);
        CHECK_EQ(handler.events[2].kind, InputEventKind::KeyUp);
        CHECK_EQ(handler.events[2].code, 0x4F);
        CHECK_EQ(handler.events[3].kind, InputEventKind::KeyUp);
        CHECK_EQ(handler.events[3].code, 0xE0);
    }
    for (const auto& event : handler.events) {
        CHECK_EQ(event.source_index, 0);
        CHECK(event.code != 0x03);
    }
    duo_input::u1::pio_usb::set_callback_registry(nullptr);
}

TEST_CASE(a_report_id_keyboard_is_read_through_its_own_descriptor_layout) {
    KeyboardRig rig;
    const auto descriptor = report_id_keyboard_descriptor();
    rig.mount(descriptor.data(), descriptor.size(), kProtocolNone);

    rig.report(kIdentifiedKeyA, sizeof(kIdentifiedKeyA));
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x04), 1);

    rig.report(kIdentifiedNoKeys, sizeof(kIdentifiedNoKeys));
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyUp, 0x04), 1);

    // Boot offsets would read this report's identifier byte (0x02) as
    // modifiers and byte 2 (0x04) as the reserved field - neither usage 0x04
    // arriving through the boot reading would be this report's own key.
    CHECK_EQ(rig.recorder.count(InputEventKind::KeyDown), 1);
}

TEST_CASE(an_nkro_bitmap_keyboard_reads_a_set_bit_as_a_keydown) {
    KeyboardRig rig;
    const auto descriptor = nkro_bitmap_descriptor();
    rig.mount(descriptor.data(), descriptor.size(), kProtocolNone);

    rig.report(kNkroKeyA, sizeof(kNkroKeyA));
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x04), 1);

    rig.report(kNkroNoKeys, sizeof(kNkroNoKeys));
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyUp, 0x04), 1);
}

TEST_CASE(modifiers_travel_as_keydown_and_keyup_like_any_other_key) {
    KeyboardRig rig;
    rig.mount_boot_keyboard();

    const std::uint8_t left_ctrl_down[] = {0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00};
    rig.report(left_ctrl_down, sizeof(left_ctrl_down));
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0xE0), 1);

    rig.report(kNoKeys, sizeof(kNoKeys));
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyUp, 0xE0), 1);
}

TEST_CASE(fn_dependent_f9_through_f12_usages_round_trip_like_any_boot_key) {
    // A compact keyboard's Fn layer resolves to ordinary HID usages before the
    // report ever reaches USB - HID 1.12 Sec 10 usages 0x42-0x45 - so nothing
    // downstream of the report needs to know Fn was involved at all.
    KeyboardRig rig;
    rig.mount_boot_keyboard();

    const std::uint8_t f9_through_f12[] = {0x00, 0x00, 0x42, 0x43, 0x44, 0x45, 0x00, 0x00};
    rig.report(f9_through_f12, sizeof(f9_through_f12));
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x42), 1);
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x43), 1);
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x44), 1);
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x45), 1);

    rig.report(kNoKeys, sizeof(kNoKeys));
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyUp, 0x42), 1);
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyUp, 0x43), 1);
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyUp, 0x44), 1);
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyUp, 0x45), 1);
}

TEST_CASE(rapid_reports_across_several_core1_passes_stay_in_order) {
    KeyboardRig rig;
    rig.mount_boot_keyboard();

    const std::uint8_t key_a[] = {0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00};
    const std::uint8_t key_b[] = {0x00, 0x00, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00};
    const std::uint8_t key_c[] = {0x00, 0x00, 0x06, 0x00, 0x00, 0x00, 0x00, 0x00};

    rig.report(key_a, sizeof(key_a));
    rig.report(key_b, sizeof(key_b));
    rig.report(key_c, sizeof(key_c));
    rig.report(kNoKeys, sizeof(kNoKeys));

    // One press and one release per key, each pass drained before the next
    // report was ever delivered - never a merged or reordered transition.
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x04), 1);
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x05), 1);
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x06), 1);
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyUp, 0x04), 1);
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyUp, 0x05), 1);
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyUp, 0x06), 1);
    CHECK_EQ(rig.recorder.count(InputEventKind::KeyDown), 3);
    CHECK_EQ(rig.recorder.count(InputEventKind::KeyUp), 3);
}

TEST_CASE(a_rollover_report_freezes_state_instead_of_releasing_the_held_key) {
    KeyboardRig rig;
    rig.mount_boot_keyboard();

    rig.report(kKeyA, sizeof(kKeyA));
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x04), 1);

    // HID 1.11 Sec 8.3: every array slot holding ErrorRollOver (0x01) means
    // "I cannot say what is held", not "nothing is held".
    const std::uint8_t rollover[] = {0x00, 0x00, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01};
    rig.report(rollover, sizeof(rollover));
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyUp, 0x04), 0);

    // The real release, once the keyboard can say what is held again.
    rig.report(kNoKeys, sizeof(kNoKeys));
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyUp, 0x04), 1);
}

TEST_CASE(a_report_past_the_boundary_becomes_a_release_all_fault_never_a_trimmed_report) {
    KeyboardRig rig;
    rig.mount_boot_keyboard();

    rig.report(kKeyA, sizeof(kKeyA));
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x04), 1);
    const std::size_t receives_before = rig.receive_count();

    // One byte past the 64-byte boundary source.hpp fixes for every backend.
    // Trimmed and read at the boot offsets this would be all zero bytes - a
    // quiet report, not the fault it must become.
    std::array<std::uint8_t, 65> oversized{};
    oversized[2] = 0x05;  // What a trim-then-route bug would read as key 0x05.
    rig.report(oversized.data(), oversized.size());

    // The held key is released - the fault's whole job - and nothing reads
    // the oversized bytes as a new key.
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyUp, 0x04), 1);
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x05), 0);
    CHECK_EQ(rig.recorder.count(InputEventKind::KeyDown), 1);

    // The interface is given up on, not asked for another report: a fault is
    // terminal, and re-arming a source whose last report was unaccounted for
    // is exactly the "recoverable report loss" the requirement forbids.
    CHECK_EQ(rig.receive_count(), receives_before);
    rig.report(kKeyA, sizeof(kKeyA));
    CHECK_EQ(rig.receive_count(), receives_before);
    CHECK_EQ(rig.recorder.count(InputEventKind::KeyDown), 1);
}

TEST_CASE(a_refusal_cannot_republish_a_stale_event) {
    // Task 4's review: a drain loop that leaves its output buffers untouched
    // on a refusal path can re-deliver the PREVIOUS event under a dropped
    // continue. DeviceRegistry::take_event() must fully overwrite ``event``
    // and ``identity`` on every true return - proven here by seeding both
    // with a stale report before asking for a fresh, unrelated one.
    duo::test::tinyusb_host::reset();
    DeviceRegistry registry;
    duo_input::u1::pio_usb::set_callback_registry(&registry);

    // Instance 3, not 0, on purpose: ``endpoint`` is asserted below, and an
    // interface whose expected endpoint is zero cannot tell "copied from the
    // record" apart from "left at the struct's default".
    duo::test::tinyusb_host::add_device(9, 0xBEEF, 0x0001);
    tuh_mount_cb(9);
    duo::test::tinyusb_host::set_protocol(9, 3, kProtocolKeyboard);
    tuh_hid_mount_cb(9, 3, nullptr, 0);
    registry.process_pending(0);

    SourceEvent drain;
    SourceIdentity drain_identity;
    while (registry.take_event(drain, drain_identity)) {
    }

    const std::uint8_t stale_report[] = {0xFF, 0xFF, 0x7A, 0x7A, 0x7A, 0x7A, 0x7A, 0x7A};
    constexpr std::uint32_t kCaptureUs = 0x0BADF00Du;
    duo::test::tinyusb_host::set_now_us(kCaptureUs);
    tuh_hid_report_received_cb(9, 3, stale_report, sizeof(stale_report));
    registry.process_pending(0);

    // Seeded with sentinel content take_event() must not leave standing.
    SourceEvent event;
    event.kind = SourceEventKind::Fault;
    event.source_id = 0xAB;
    event.endpoint = 0xAB;
    event.received_us = 0xAAAAAAAAu;
    std::memset(event.report, 0xEE, sizeof(event.report));
    event.report_size = sizeof(event.report);
    SourceIdentity identity;
    identity.vendor_id = 0xDEAD;

    CHECK(registry.take_event(event, identity));
    CHECK_EQ(static_cast<int>(event.kind), static_cast<int>(SourceEventKind::Report));
    CHECK_EQ(event.source_id, std::uint8_t{0});
    // The two fields the original assertion block never looked at. endpoint
    // must be the interface's own instance, and received_us the clock read
    // at capture - neither the 0xAB/0xAAAAAAAA sentinel nor a zero.
    CHECK_EQ(event.endpoint, std::uint8_t{3});
    CHECK_EQ(event.received_us, kCaptureUs);
    CHECK_EQ(event.report_size, sizeof(stale_report));
    CHECK(std::memcmp(event.report, stale_report, sizeof(stale_report)) == 0);
    // Every byte past what this report carried came from the fresh copy's
    // own zero-initialization, not from the 0xEE sentinel this was seeded
    // with.
    for (std::size_t index = sizeof(stale_report); index < sizeof(event.report); ++index) {
        CHECK_EQ(event.report[index], std::uint8_t{0});
    }
    CHECK_EQ(identity.vendor_id, std::uint16_t{0xBEEF});

    duo_input::u1::pio_usb::set_callback_registry(nullptr);
}

TEST_CASE(a_queue_overflow_synthesizes_a_fault_for_every_role_owned_source) {
    // Deliberately never drained, the same way Task 6's own callback-queue
    // overflow test withholds processing to reach kCallbackQueueCapacity.
    // This is not a shape a real Core 1 pass produces - main.cpp drains
    // completely after every task() call - it exists to prove the queue's
    // own overflow path is safe when something else someday fails to drain.
    //
    // Both of V1's role-owned sources are present, because one is not enough
    // to see the requirement. A queue that holds ONE slot back for Fault
    // covers whichever source overflows first and then has nothing left for
    // the second: that second Fault is a release-all that never reaches its
    // InputPipeline, and the interface is faulted afterwards so nothing will
    // ever produce it again. The reservation is one slot per role.
    TwoSourceRig rig;

    // Something genuinely held on each side, delivered and drained normally,
    // so each pipeline really does owe a release.
    rig.deliver(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA), 10);
    rig.pump();
    CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyDown, 0x04), 1);

    rig.deliver(kMouseAddress, kMouseInstance, kMouseButton1Down, sizeof(kMouseButton1Down), 20);
    rig.pump();
    CHECK_EQ(rig.mouse_recorder.of(InputEventKind::MouseButtonDown, 0), 1);

    // From here nothing drains. Each pass still processes its callback into
    // the event queue; the queue only grows.
    for (std::size_t index = 0; index < DeviceRegistry::kEventQueueCapacity + 2; ++index) {
        rig.deliver(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA),
                    static_cast<std::uint32_t>(100 + index));
        rig.task();
    }

    // The keyboard has overflowed and taken one reserved slot for its Fault.
    // The mouse now overflows too - the second source, the one a single
    // reserved slot loses.
    rig.deliver(kMouseAddress, kMouseInstance, kMouseButton1Down, sizeof(kMouseButton1Down), 900);
    rig.task();

    rig.drain();

    // Both release-alls arrived. Neither the key nor the button is left held
    // on a computer that has no other way to find out.
    CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyUp, 0x04), 1);
    CHECK_EQ(rig.mouse_recorder.of(InputEventKind::MouseButtonUp, 0), 1);
}

TEST_CASE(a_queue_overflow_counts_and_leaves_the_source_faulted_without_rearming) {
    // The bookkeeping half of the same path, on the registry directly: the
    // overflow is counted, the interface is faulted, and no receive is left
    // in flight for a report nothing accounted for.
    duo::test::tinyusb_host::reset();
    DeviceRegistry registry;
    duo_input::u1::pio_usb::set_callback_registry(&registry);

    duo::test::tinyusb_host::add_device(kKeyboardAddress, kVendorId, kProductId);
    tuh_mount_cb(kKeyboardAddress);
    duo::test::tinyusb_host::set_protocol(kKeyboardAddress, kKeyboardInstance, kProtocolKeyboard);
    tuh_hid_mount_cb(kKeyboardAddress, kKeyboardInstance, nullptr, 0);
    registry.process_pending(0);

    SourceEvent drain;
    SourceIdentity drain_identity;
    CHECK(registry.take_event(drain, drain_identity));  // the mount's Ready
    CHECK_FALSE(registry.take_event(drain, drain_identity));

    for (std::size_t index = 0; index < DeviceRegistry::kEventQueueCapacity + 1; ++index) {
        tuh_hid_report_received_cb(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA));
        registry.process_pending(0);
    }

    CHECK(registry.event_overflow_count() > 0);

    bool saw_fault = false;
    SourceEvent event;
    SourceIdentity identity;
    while (registry.take_event(event, identity)) {
        if (event.kind == SourceEventKind::Fault) {
            saw_fault = true;
        }
    }
    CHECK(saw_fault);

    const auto* interface = registry.find(kKeyboardAddress, kKeyboardInstance);
    CHECK(interface != nullptr);
    if (interface != nullptr) {
        CHECK(interface->faulted);
        CHECK_FALSE(interface->report_in_flight);
    }

    duo_input::u1::pio_usb::set_callback_registry(nullptr);
}

TEST_CASE(two_reports_captured_in_one_core1_pass_carry_their_own_timestamps) {
    // The point of the brief's "callback capture timestamp". A pass-level
    // clock read - one time_us_32() before tuh_task(), stamped onto every
    // report the pass then processes - gives both of these reports the same
    // number, and a number earlier than either report actually arrived at.
    // Reading the clock inside tuh_hid_report_received_cb gives each report
    // the moment it was really handed over.
    TwoSourceRig rig;

    constexpr std::uint32_t kKeyboardCaptureUs = 0x11112222u;
    constexpr std::uint32_t kMouseCaptureUs = 0x33334444u;
    constexpr std::uint32_t kPassUs = 0x55556666u;

    // Both callbacks fire before task() - exactly what happens inside one
    // tuh_task() call when two devices report in the same window.
    rig.deliver(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA), kKeyboardCaptureUs);
    rig.deliver(kMouseAddress, kMouseInstance, kMouseButton1Down, sizeof(kMouseButton1Down),
                kMouseCaptureUs);

    // One pass, one now_us, two reports.
    rig.backend.task(kPassUs);

    bool saw_keyboard = false;
    bool saw_mouse = false;
    SourceEvent event;
    SourceIdentity identity;
    while (rig.backend.take_event(event, identity)) {
        if (event.kind != SourceEventKind::Report) {
            continue;
        }
        if (identity.kind == DeviceKind::Keyboard) {
            saw_keyboard = true;
            CHECK_EQ(event.received_us, kKeyboardCaptureUs);
        } else if (identity.kind == DeviceKind::Mouse) {
            saw_mouse = true;
            CHECK_EQ(event.received_us, kMouseCaptureUs);
        }
    }
    CHECK(saw_keyboard);
    CHECK(saw_mouse);
}

TEST_CASE(a_report_longer_than_the_declared_layout_reads_only_the_declared_fields) {
    // Not the oversized-report case above: this report is well inside
    // kMaxSourceReportBytes and is routed as an ordinary Report. What it is
    // longer than is the DESCRIPTOR's declared layout - three key slots in a
    // four-byte report - and the surplus bytes carry usages that a
    // boot-offset reading would happily press.
    //
    // The assertion is on the normalizer's existing behaviour, which reads
    // exactly the fields the layout declares and never looks past them.
    KeyboardRig rig;
    const auto descriptor = three_slot_keyboard_descriptor();
    rig.mount(descriptor.data(), descriptor.size(), kProtocolNone);

    // Bytes 1-3 are the three declared slots. Bytes 4-7 are surplus: read at
    // the boot layout's own offsets they would be four more pressed keys.
    const std::uint8_t long_report[] = {0x00, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A};
    rig.report(long_report, sizeof(long_report));

    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x04), 1);
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x05), 1);
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x06), 1);
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x07), 0);
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x08), 0);
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x09), 0);
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x0A), 0);
    CHECK_EQ(rig.recorder.count(InputEventKind::KeyDown), 3);

    // And the declared fields still transition normally afterwards - the
    // surplus is ignored, not treated as a reason to refuse the report.
    const std::uint8_t long_release[] = {0x00, 0x00, 0x00, 0x00, 0x07, 0x08, 0x09, 0x0A};
    rig.report(long_release, sizeof(long_release));
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyUp, 0x04), 1);
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyUp, 0x05), 1);
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyUp, 0x06), 1);
    CHECK_EQ(rig.recorder.count(InputEventKind::KeyUp), 3);
}

namespace {

/// No bindings, default PC1/PC1 routes: an unbound key just reaches the
/// computer the route points at, exactly what test_core1_runtime.cpp's own
/// pass-through case already proves for any InputEvent regardless of source.
struct NoBindings final : IProfileSource {
    std::size_t bindings_for(std::uint8_t, Binding*) const override { return 0; }
    bool routes_for(std::uint8_t, KeyboardRoute& keyboard, MouseRoute& mouse) const override {
        keyboard = KeyboardRoute::PC1;
        mouse = MouseRoute::PC1;
        return true;
    }
};

struct RecordingSink final : ICommandSink {
    std::vector<OutputCommand> commands;
    std::size_t pending() const override { return 0; }
    bool submit(const OutputCommand& command) override {
        commands.push_back(command);
        return true;
    }
};

}  // namespace

TEST_CASE(a_pio_keypress_reaches_core1_runtime_through_the_existing_command_path) {
    // Proves the checklist's own claim: bindings, capture, macros and routes
    // consume the InputEvent this backend produces exactly as they already
    // do for CH375's, because RuntimeInput::on_input in main.cpp calls
    // Core1Runtime::handle_input the same way regardless of which backend's
    // InputPipeline produced the event, and ICommandSink::submit is the same
    // interface the real SPI-bound queue implements on hardware.
    RecordingSink sink;
    NoBindings profiles;
    Core1Runtime runtime(sink, profiles);

    struct RuntimeInput final : IInputHandler {
        Core1Runtime& runtime;
        explicit RuntimeInput(Core1Runtime& target) : runtime(target) {}
        void on_input(const InputEvent& event, std::uint32_t now_ms) override {
            runtime.handle_input(event, now_ms);
        }
    } handler{runtime};

    InputPipeline pipeline(handler);
    PioUsbBackend backend;
    duo::test::tinyusb_host::reset();
    backend.begin();

    duo::test::tinyusb_host::add_device(kKeyboardAddress, kVendorId, kProductId);
    tuh_mount_cb(kKeyboardAddress);
    duo::test::tinyusb_host::set_protocol(kKeyboardAddress, kKeyboardInstance, kProtocolKeyboard);
    tuh_hid_mount_cb(kKeyboardAddress, kKeyboardInstance, nullptr, 0);
    backend.task(1000);

    SourceEvent event;
    SourceIdentity identity;
    while (backend.take_event(event, identity)) {
        pipeline.on_event(event, identity, 1);
    }

    tuh_hid_report_received_cb(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA));
    backend.task(2000);
    while (backend.take_event(event, identity)) {
        pipeline.on_event(event, identity, 2);
    }

    int key_presses = 0;
    for (const OutputCommand& command : sink.commands) {
        if (command.kind == CommandKind::KeyPress && command.code == 0x04) {
            ++key_presses;
            CHECK_EQ(static_cast<int>(command.owner), static_cast<int>(kPhysicalOwner));
        }
    }
    CHECK_EQ(key_presses, 1);
}
