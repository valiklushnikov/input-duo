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
#include "input/pipeline.hpp"
#include "pio_usb/backend.hpp"
#include "pio_usb/device_registry.hpp"
#include "test_support.hpp"

#include <array>
#include <cstdint>
#include <cstring>
#include <vector>

using duo::test::tinyusb_host::kProtocolKeyboard;
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
    void report(const std::uint8_t* bytes, std::size_t size) {
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

}  // namespace

TEST_CASE(boot_6kro_press_and_release_become_keydown_and_keyup) {
    KeyboardRig rig;
    rig.mount_boot_keyboard();

    rig.report(kKeyA, sizeof(kKeyA));
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyDown, 0x04), 1);

    rig.report(kNoKeys, sizeof(kNoKeys));
    CHECK_EQ(rig.recorder.of(InputEventKind::KeyUp, 0x04), 1);
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

    duo::test::tinyusb_host::add_device(9, 0xBEEF, 0x0001);
    tuh_mount_cb(9);
    duo::test::tinyusb_host::set_protocol(9, 0, kProtocolKeyboard);
    tuh_hid_mount_cb(9, 0, nullptr, 0);
    registry.process_pending();

    SourceEvent drain;
    SourceIdentity drain_identity;
    while (registry.take_event(drain, drain_identity)) {
    }

    const std::uint8_t stale_report[] = {0xFF, 0xFF, 0x7A, 0x7A, 0x7A, 0x7A, 0x7A, 0x7A};
    tuh_hid_report_received_cb(9, 0, stale_report, sizeof(stale_report));
    registry.process_pending();

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
    CHECK_EQ(event.source_id, std::uint8_t{9});
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

TEST_CASE(a_queue_overflow_synthesizes_a_fault_and_counts_rather_than_losing_a_report) {
    // Deliberately never drained, the same way Task 6's own callback-queue
    // overflow test withholds processing to reach kCallbackQueueCapacity.
    // This is not a shape a real Core 1 pass produces - main.cpp drains
    // completely after every task() call - it exists to prove the queue's
    // own overflow path is safe when something else someday fails to drain.
    duo::test::tinyusb_host::reset();
    DeviceRegistry registry;
    duo_input::u1::pio_usb::set_callback_registry(&registry);

    duo::test::tinyusb_host::add_device(kKeyboardAddress, kVendorId, kProductId);
    tuh_mount_cb(kKeyboardAddress);
    duo::test::tinyusb_host::set_protocol(kKeyboardAddress, kKeyboardInstance, kProtocolKeyboard);
    tuh_hid_mount_cb(kKeyboardAddress, kKeyboardInstance, nullptr, 0);
    registry.process_pending();

    SourceEvent drain;
    SourceIdentity drain_identity;
    CHECK(registry.take_event(drain, drain_identity));  // the mount's Ready
    CHECK_FALSE(registry.take_event(drain, drain_identity));

    for (std::size_t index = 0; index < DeviceRegistry::kEventQueueCapacity + 1; ++index) {
        tuh_hid_report_received_cb(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA));
        registry.process_pending();
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
