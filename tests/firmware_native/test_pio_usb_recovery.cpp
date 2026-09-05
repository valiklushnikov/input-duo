// Task 10: detach, stalls, hub resets and reconnects fail safe.
//
// The central constraint this file proves: on detach, reset, malformed
// state, queue overflow or host fault, every held key/button is released
// before the source is forgotten. A dropped release is a release-all fault,
// not a recoverable report loss - so every scenario below ends by checking
// the release actually reached its pipeline, not merely that a counter
// moved.

#include "crypto/sha256.hpp"
#include "fakes/spi_link.hpp"
#include "fakes/tinyusb_host.hpp"
#include "hid/state_manager.hpp"
#include "input/pipeline.hpp"
#include "pio_usb/backend.hpp"
#include "pio_usb/device_registry.hpp"
#include "spi_master.hpp"
#include "storage/ab_store.hpp"
#include "storage/flash_layout.hpp"
#include "test_support.hpp"

#include <cstdint>
#include <cstring>
#include <vector>

using duo::test::tinyusb_host::kProtocolKeyboard;
using duo::test::tinyusb_host::kProtocolMouse;
using duo_input::u1::input::DeviceKind;
using duo_input::u1::input::IInputHandler;
using duo_input::u1::input::InputEvent;
using duo_input::u1::input::InputEventKind;
using duo_input::u1::input::InputPipeline;
using duo_input::u1::input::SourceEvent;
using duo_input::u1::input::SourceEventKind;
using duo_input::u1::input::SourceIdentity;
using duo_input::hid::HidStateManager;
using duo_input::hid::Target;
using duo_input::storage::AbStore;
using duo_input::storage::FlashBackend;
using duo_input::storage::StoreError;
using duo_input::u1::SpiMaster;
using duo_input::u1::pio_usb::DeviceRegistry;
using duo_input::u1::pio_usb::LogicalRole;
using duo_input::u1::pio_usb::PioUsbBackend;

namespace {

constexpr std::uint8_t kKeyboardAddress = 2;
constexpr std::uint8_t kKeyboardInstance = 0;
constexpr std::uint16_t kVendorId = 0x1234;
constexpr std::uint16_t kProductId = 0x5678;

constexpr std::uint8_t kMouseAddress = 3;
constexpr std::uint8_t kMouseInstance = 0;
constexpr std::uint16_t kMouseProductId = 0x9ABC;

constexpr std::uint8_t kKeyA[] = {0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00};
constexpr std::uint8_t kNoKeys[] = {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00};
constexpr std::uint8_t kMouseButton1Down[] = {0x01, 0x00, 0x00};
constexpr std::uint8_t kMouseNoButtons[] = {0x00, 0x00, 0x00};

/// The Keychron M3 receiver, and the exact side-button trace
/// test_input_pipeline.cpp pinned. Its side button is emitted by a SECOND
/// interface on the same physical device as its mouse channel - the
/// LogicalRole::Auxiliary case - which is why it can only mount after that
/// mouse channel has (has_mouse_sibling requires it) and therefore always
/// carries the higher generation of the two sharing the mouse's role slot.
constexpr std::uint8_t kKeychronAddress = 4;
constexpr std::uint16_t kKeychronVendorId = 0x3434;
constexpr std::uint16_t kKeychronProductId = 0xD030;
constexpr std::uint8_t kKeychronSidePress[] = {0x01, 0x01, 0x00, 0x4F,
                                               0x00, 0x00, 0x00, 0x00, 0x03};

/// kArmRetryBackoffUs, written out rather than read from the registry.
///
/// DeviceRegistry keeps the table private, and asserting against a copy of
/// the same expression the code uses would pin nothing: a test that says
/// "the deadline is whatever the table says" survives the table being
/// zeroed. These are the microsecond waits the design actually commits to,
/// and the tests below fail if the code stops honouring them.
constexpr std::uint32_t kFirstBackoffUs = 1000;
constexpr std::uint32_t kSecondBackoffUs = 4000;
constexpr std::uint32_t kThirdBackoffUs = 16000;

struct Recorder final : IInputHandler {
    std::vector<InputEvent> events;

    void on_input(const InputEvent& event, std::uint32_t) override { events.push_back(event); }

    int of(InputEventKind kind, std::uint16_t code) const {
        int seen = 0;
        for (const InputEvent& event : events) {
            if (event.kind == kind && event.code == code) {
                ++seen;
            }
        }
        return seen;
    }

    int count(InputEventKind kind) const {
        int seen = 0;
        for (const InputEvent& event : events) {
            if (event.kind == kind) {
                ++seen;
            }
        }
        return seen;
    }
};

/// A keyboard AND a mouse behind one backend, each with its own
/// InputPipeline, routed by PioUsbBackend::logical_port exactly the way
/// main.cpp's Core 1 loop does - the same rig shape test_pio_usb_keyboard.cpp
/// and test_pio_usb_mouse.cpp already use, reproduced here because each test
/// file keeps its own rig rather than sharing one across translation units.
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

    void deliver(std::uint8_t address, std::uint8_t instance, const std::uint8_t* bytes,
                std::size_t size, std::uint32_t captured_us) {
        duo::test::tinyusb_host::set_now_us(captured_us);
        tuh_hid_report_received_cb(address, instance, bytes, static_cast<std::uint16_t>(size));
    }

    /// One Core 1 pass: task() only, at a caller-chosen ``now_us`` rather
    /// than the auto-incrementing default - needed so backoff/stall
    /// deadlines can be crossed deterministically.
    ///
    /// Sets the FAKE time_us_32() to the same value as well as passing it in.
    /// On hardware there is only one clock: main.cpp reads time_us_32() once
    /// per pass and hands that single reading to backend.task(). An earlier
    /// version of this rig fed two - time_us_32() stuck wherever the last
    /// deliver() left it (tens of microseconds) while task_at() passed tens of
    /// thousands - and because arm_if_needed() computed its deadline from the
    /// first while retry_pending_arms() compared against the second, EVERY
    /// deadline was already in the past the first time it was checked. The
    /// backoff mechanism was therefore never exercised at all: deleting the
    /// deadline comparison, or zeroing kArmRetryBackoffUs, left the whole
    /// suite green. The registry now takes the clock as a parameter
    /// everywhere, and this line keeps the rig honest even if some future
    /// code path reads time_us_32() directly again.
    void task_at(std::uint32_t at_us) {
        now_us = at_us;
        duo::test::tinyusb_host::set_now_us(at_us);
        backend.task(now_us);
    }

    void task() { task_at(now_us + 1000); }

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

TEST_CASE(device_unmount_mid_key_releases_the_held_key_exactly_once) {
    TwoSourceRig rig;

    rig.deliver(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA), 10);
    rig.pump();
    CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyDown, 0x04), 1);

    tuh_umount_cb(kKeyboardAddress);
    rig.pump();

    CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyUp, 0x04), 1);
    // Not doubled: a second unmount callback for the same, now-cleared
    // device must not synthesize a second release.
    tuh_umount_cb(kKeyboardAddress);
    rig.pump();
    CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyUp, 0x04), 1);
}

TEST_CASE(mouse_unmount_mid_button_releases_the_held_button_exactly_once) {
    TwoSourceRig rig;

    rig.deliver(kMouseAddress, kMouseInstance, kMouseButton1Down, sizeof(kMouseButton1Down), 10);
    rig.pump();
    CHECK_EQ(rig.mouse_recorder.of(InputEventKind::MouseButtonDown, 0), 1);

    tuh_umount_cb(kMouseAddress);
    rig.pump();

    CHECK_EQ(rig.mouse_recorder.of(InputEventKind::MouseButtonUp, 0), 1);
    CHECK_EQ(rig.mouse_recorder.count(InputEventKind::MouseButtonUp), 1);
}

TEST_CASE(whole_hub_unmount_releases_both_sources_exactly_once_each) {
    // Both downstream devices detach in the same callback burst - the shape
    // a hub loses power in, both tuh_umount_cb calls captured before either
    // is processed.
    TwoSourceRig rig;

    rig.deliver(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA), 10);
    rig.pump();
    rig.deliver(kMouseAddress, kMouseInstance, kMouseButton1Down, sizeof(kMouseButton1Down), 20);
    rig.pump();
    CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyDown, 0x04), 1);
    CHECK_EQ(rig.mouse_recorder.of(InputEventKind::MouseButtonDown, 0), 1);

    tuh_umount_cb(kKeyboardAddress);
    tuh_umount_cb(kMouseAddress);
    rig.pump();

    CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyUp, 0x04), 1);
    CHECK_EQ(rig.mouse_recorder.of(InputEventKind::MouseButtonUp, 0), 1);
    CHECK_EQ(rig.keyboard_recorder.count(InputEventKind::KeyUp), 1);
    CHECK_EQ(rig.mouse_recorder.count(InputEventKind::MouseButtonUp), 1);
}

TEST_CASE(a_persistent_receive_arm_refusal_escalates_to_a_release_all_and_stops_retrying) {
    // A synchronous tuh_hid_receive_report() refusal can only ever be
    // attempted while nothing is already in flight - report_in_flight is
    // TinyUSB's own claim on the endpoint, and arm_if_needed refuses to ask
    // again underneath it. So the realistic "device stops answering after a
    // held key" shape is: one report completes normally (the key goes
    // down), which is what frees the endpoint for its NEXT arm - and that
    // next arm is the one that starts failing, repeatedly, from here on.
    TwoSourceRig rig;

    rig.deliver(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA), 10);
    rig.pump();
    CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyDown, 0x04), 1);

    // One more callback uses up the currently-armed receive; the RE-ARM this
    // triggers, once processed, is what starts refusing.
    rig.deliver(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA), 15);
    duo::test::tinyusb_host::set_receive_result(false);

    const std::size_t receives_before =
        duo::test::tinyusb_host::receive_count(kKeyboardAddress, kKeyboardInstance);

    rig.pump();  // the report above lands fine; the re-arm after it fails (1 of kMaxArmRetries)

    // Cross each remaining backoff deadline in turn without ever delivering
    // another report - kMaxArmRetries total failures, then escalation.
    std::uint32_t at = rig.now_us;
    for (int attempt = 0; attempt < DeviceRegistry::kMaxArmRetries; ++attempt) {
        at += 20000;
        rig.task_at(at);
    }
    rig.drain();

    // Escalated: the held key was released, and the mouse - a completely
    // separate interface - was never touched by any of this.
    CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyUp, 0x04), 1);
    CHECK_EQ(rig.mouse_recorder.count(InputEventKind::MouseButtonDown), 0);
    CHECK_EQ(rig.mouse_recorder.count(InputEventKind::MouseButtonUp), 0);

    // No spin: once escalated, the interface is faulted and stops asking.
    const std::size_t receives_after_escalation =
        duo::test::tinyusb_host::receive_count(kKeyboardAddress, kKeyboardInstance);
    at += 100000;
    rig.task_at(at);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(kKeyboardAddress, kKeyboardInstance),
             receives_after_escalation);
    CHECK(receives_after_escalation > receives_before);
    CHECK(rig.backend.clock_settled());  // sanity: the backend itself is fine throughout
}

TEST_CASE(a_refusal_that_clears_before_retries_are_exhausted_recovers_without_a_fault) {
    // The companion case: bounded retry is not merely "give up after one" -
    // an endpoint that answers again inside the retry budget must recover
    // silently, with no release-all and no escalation at all.
    TwoSourceRig rig;

    rig.deliver(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA), 10);
    rig.pump();
    CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyDown, 0x04), 1);

    rig.deliver(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA), 15);
    duo::test::tinyusb_host::set_receive_result(false);
    rig.pump();  // the report lands; the re-arm right after it is refused once

    duo::test::tinyusb_host::set_receive_result(true);
    std::uint32_t at = rig.now_us;
    at += 20000;
    rig.task_at(at);  // the retry succeeds
    rig.drain();

    // No release-all: the key is still legitimately held, the interface is
    // not faulted, and it can still take a report.
    CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyUp, 0x04), 0);

    rig.deliver(kKeyboardAddress, kKeyboardInstance, kNoKeys, sizeof(kNoKeys), at + 1);
    rig.pump();
    CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyUp, 0x04), 1);
}

TEST_CASE(an_endpoint_that_stalls_is_distinguished_from_an_idle_device_and_recovers) {
    // Real TinyUSB's hidh_xfer_cb forwards xferred_bytes to
    // tuh_hid_report_received_cb regardless of xfer_result, so a
    // stalled/errored transfer completes - with (near) zero bytes, not
    // silence. A device that is simply idle (nothing pressed, nothing
    // moved) produces no callback at all, indefinitely, and must never be
    // treated as faulted for that - a timeout-based "no report in N ms"
    // detector would fault every keyboard nobody is currently typing on.
    TwoSourceRig rig;

    rig.deliver(kMouseAddress, kMouseInstance, kMouseButton1Down, sizeof(kMouseButton1Down), 10);
    rig.pump();
    CHECK_EQ(rig.mouse_recorder.of(InputEventKind::MouseButtonDown, 0), 1);

    // The keyboard sits idle throughout - no callback fires for it at all -
    // and must not be disturbed by the mouse's own stall/recovery, proving
    // the two sources recover independently. kMaxArmRetries stall signals
    // are tolerated (each one re-arms successfully); the (kMaxArmRetries+1)th
    // is what escalates.
    for (int index = 0; index < DeviceRegistry::kMaxArmRetries + 1; ++index) {
        rig.deliver(kMouseAddress, kMouseInstance, nullptr, 0,
                    20 + static_cast<std::uint32_t>(index));
        rig.pump();
    }

    CHECK_EQ(rig.mouse_recorder.of(InputEventKind::MouseButtonUp, 0), 1);
    CHECK_EQ(rig.keyboard_recorder.count(InputEventKind::KeyDown), 0);
    CHECK_EQ(rig.keyboard_recorder.count(InputEventKind::KeyUp), 0);
}

TEST_CASE(a_single_stall_signal_followed_by_a_real_report_does_not_escalate) {
    // The bounded-retry budget for stall signals is shared with ordinary
    // arm refusals and resets on real forward progress, the same as that
    // path - one isolated zero-length completion must not, by itself,
    // release a button that is still legitimately held.
    TwoSourceRig rig;

    rig.deliver(kMouseAddress, kMouseInstance, kMouseButton1Down, sizeof(kMouseButton1Down), 10);
    rig.pump();
    CHECK_EQ(rig.mouse_recorder.of(InputEventKind::MouseButtonDown, 0), 1);

    rig.deliver(kMouseAddress, kMouseInstance, nullptr, 0, 20);
    rig.pump();
    CHECK_EQ(rig.mouse_recorder.of(InputEventKind::MouseButtonUp, 0), 0);

    rig.deliver(kMouseAddress, kMouseInstance, kMouseNoButtons, sizeof(kMouseNoButtons), 30);
    rig.pump();
    CHECK_EQ(rig.mouse_recorder.of(InputEventKind::MouseButtonUp, 0), 1);
}

TEST_CASE(a_real_report_resets_the_retry_budget_so_occasional_stalls_never_accumulate) {
    // The companion mutation this file's own budget-sharing design invites:
    // if a real report merely avoided IMMEDIATE escalation without actually
    // resetting the count, an interface that stalls only occasionally -
    // recovering cleanly every time - would still creep towards escalation
    // over its working lifetime and eventually fault for no real reason.
    // More than kMaxArmRetries individual stall-then-recover cycles must
    // never escalate, because each real report in between is a clean bill
    // of health, not a partial one.
    TwoSourceRig rig;

    rig.deliver(kMouseAddress, kMouseInstance, kMouseButton1Down, sizeof(kMouseButton1Down), 1);
    rig.pump();
    CHECK_EQ(rig.mouse_recorder.of(InputEventKind::MouseButtonDown, 0), 1);

    for (int cycle = 0; cycle < DeviceRegistry::kMaxArmRetries + 2; ++cycle) {
        const std::uint32_t base = 10 + static_cast<std::uint32_t>(cycle) * 10;
        rig.deliver(kMouseAddress, kMouseInstance, nullptr, 0, base);
        rig.pump();
        rig.deliver(kMouseAddress, kMouseInstance, kMouseButton1Down, sizeof(kMouseButton1Down),
                    base + 1);
        rig.pump();
    }

    // Still held, never escalated, despite far more than kMaxArmRetries
    // total stall signals across this interface's lifetime.
    CHECK_EQ(rig.mouse_recorder.of(InputEventKind::MouseButtonUp, 0), 0);

    rig.deliver(kMouseAddress, kMouseInstance, kMouseNoButtons, sizeof(kMouseNoButtons), 900);
    rig.pump();
    CHECK_EQ(rig.mouse_recorder.of(InputEventKind::MouseButtonUp, 0), 1);
}

TEST_CASE(repeated_mount_and_unmount_never_accumulates_stale_role_ownership) {
    TwoSourceRig rig;

    for (int cycle = 0; cycle < 5; ++cycle) {
        const std::uint8_t address = kKeyboardAddress;
        tuh_umount_cb(address);
        rig.pump();

        duo::test::tinyusb_host::add_device(address, kVendorId, kProductId);
        tuh_mount_cb(address);
        duo::test::tinyusb_host::set_protocol(address, kKeyboardInstance, kProtocolKeyboard);
        tuh_hid_mount_cb(address, kKeyboardInstance, nullptr, 0);
        rig.pump();

        rig.deliver(address, kKeyboardInstance, kKeyA, sizeof(kKeyA),
                    10 + static_cast<std::uint32_t>(cycle) * 100);
        rig.pump();
        CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyDown, 0x04), cycle + 1);

        rig.deliver(address, kKeyboardInstance, kNoKeys, sizeof(kNoKeys),
                    50 + static_cast<std::uint32_t>(cycle) * 100);
        rig.pump();
        CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyUp, 0x04), cycle + 1);
    }

    // Every press had its own release - no cycle left a phantom key held
    // over into the next generation's reports.
    CHECK_EQ(rig.keyboard_recorder.count(InputEventKind::KeyDown), 5);
    CHECK_EQ(rig.keyboard_recorder.count(InputEventKind::KeyUp), 5);
}

TEST_CASE(a_stale_report_queued_behind_an_undrained_detach_is_discarded_not_misrouted) {
    // Constructed directly on the registry, not through the rig's pump(),
    // because the point is to accumulate MULTIPLE process_pending() passes
    // worth of callbacks - a second report from the OLD device, its
    // unmount, a new device's mount and report - all before take_event()
    // ever drains anything. A real Core 1 pass cannot do this across passes
    // (main.cpp drains completely after every task()), but it easily can
    // within the many callbacks one tuh_task() call can produce - the shape
    // this proves safe.
    duo::test::tinyusb_host::reset();
    DeviceRegistry registry;
    duo_input::u1::pio_usb::set_callback_registry(&registry);

    Recorder recorder;
    InputPipeline pipeline(recorder);
    const auto drain_all = [&] {
        SourceEvent event;
        SourceIdentity identity;
        while (registry.take_event(event, identity)) {
            pipeline.on_event(event, identity, 1);
        }
    };

    // Device A mounts, and a key genuinely goes down - delivered and held
    // through the pipeline NORMALLY, the same as any other pass.
    duo::test::tinyusb_host::add_device(kKeyboardAddress, kVendorId, kProductId);
    tuh_mount_cb(kKeyboardAddress);
    duo::test::tinyusb_host::set_protocol(kKeyboardAddress, kKeyboardInstance, kProtocolKeyboard);
    tuh_hid_mount_cb(kKeyboardAddress, kKeyboardInstance, nullptr, 0);
    registry.process_pending(0);
    drain_all();
    const auto* device_a = registry.find(kKeyboardAddress, kKeyboardInstance);
    CHECK(device_a != nullptr);
    const std::uint32_t generation_a = device_a != nullptr ? device_a->generation : 0;

    duo::test::tinyusb_host::set_now_us(5);
    tuh_hid_report_received_cb(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA));
    registry.process_pending(0);
    drain_all();
    CHECK_EQ(recorder.of(InputEventKind::KeyDown, 0x04), 1);

    // From here, nothing is drained. A sends a SECOND report - key 0x05
    // newly down, alongside the still-held 0x04 - that is captured and
    // queued but never reaches the pipeline before A detaches.
    constexpr std::uint8_t second_report[] = {0x00, 0x00, 0x04, 0x05, 0x00, 0x00, 0x00, 0x00};
    duo::test::tinyusb_host::set_now_us(10);
    tuh_hid_report_received_cb(kKeyboardAddress, kKeyboardInstance, second_report,
                               sizeof(second_report));
    registry.process_pending(0);

    // A detaches. Its Detached is queued, but still nothing is drained.
    tuh_umount_cb(kKeyboardAddress);
    registry.process_pending(0);

    // A different device (a fresh generation, same role) mounts and reports
    // in the SAME undrained window.
    duo::test::tinyusb_host::add_device(kKeyboardAddress, 0xAAAA, 0xBBBB);
    tuh_mount_cb(kKeyboardAddress);
    duo::test::tinyusb_host::set_protocol(kKeyboardAddress, kKeyboardInstance, kProtocolKeyboard);
    tuh_hid_mount_cb(kKeyboardAddress, kKeyboardInstance, nullptr, 0);
    registry.process_pending(0);
    const auto* device_b = registry.find(kKeyboardAddress, kKeyboardInstance);
    CHECK(device_b != nullptr);
    if (device_b != nullptr) {
        CHECK(device_b->generation > generation_a);
    }

    duo::test::tinyusb_host::set_now_us(20);
    tuh_hid_report_received_cb(kKeyboardAddress, kKeyboardInstance, kNoKeys, sizeof(kNoKeys));
    registry.process_pending(0);

    // Now drain everything at once, exactly as a Core 1 pass that had been
    // withheld this long finally would.
    drain_all();

    // A's own already-held key was released by its Detached - the release
    // mechanism still works even alongside this scenario - and its
    // never-delivered second report (0x05) was discarded rather than
    // delivered late as a phantom key, or misrouted into device B's own
    // fresh, correctly-delivered state.
    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x04), 1);
    CHECK_EQ(recorder.of(InputEventKind::KeyDown, 0x05), 0);
    CHECK_EQ(recorder.count(InputEventKind::KeyDown), 1);  // only the one from A, above
    CHECK(registry.stale_event_discard_count() > 0);

    duo_input::u1::pio_usb::set_callback_registry(nullptr);
}

TEST_CASE(
    two_different_devices_detaching_undrained_on_the_same_role_each_get_their_own_detached) {
    // The bug a PERSISTENT per-role-slot "already pending" guard produced:
    // device X detaches (queues a Detached, slot marked pending); before
    // that is ever drained, device Y - a completely different physical
    // device that has since claimed the same now-free role - ALSO
    // detaches, and a persistent guard would see the slot "still pending"
    // from X and silently drop Y's own Detached entirely. remove_device()'s
    // guard is local to each call precisely so this cannot happen: every
    // genuine teardown gets its own queued Detached, regardless of what
    // some earlier, unrelated device on the same role slot left undrained.
    duo::test::tinyusb_host::reset();
    DeviceRegistry registry;
    duo_input::u1::pio_usb::set_callback_registry(&registry);

    constexpr std::uint8_t kDeviceX = 2;
    constexpr std::uint8_t kDeviceY = 4;

    duo::test::tinyusb_host::add_device(kDeviceX, 0x1111, 0x0001);
    tuh_mount_cb(kDeviceX);
    duo::test::tinyusb_host::set_protocol(kDeviceX, 0, kProtocolKeyboard);
    tuh_hid_mount_cb(kDeviceX, 0, nullptr, 0);
    registry.process_pending(0);

    // X detaches. Its Detached is queued. Nothing is drained.
    tuh_umount_cb(kDeviceX);
    registry.process_pending(0);

    // Y - a different device - claims the now-free Keyboard role and
    // immediately detaches too, still before any drain.
    duo::test::tinyusb_host::add_device(kDeviceY, 0x2222, 0x0002);
    tuh_mount_cb(kDeviceY);
    duo::test::tinyusb_host::set_protocol(kDeviceY, 0, kProtocolKeyboard);
    tuh_hid_mount_cb(kDeviceY, 0, nullptr, 0);
    registry.process_pending(0);
    tuh_umount_cb(kDeviceY);
    registry.process_pending(0);

    int detached_count = 0;
    bool saw_x = false;
    bool saw_y = false;
    SourceEvent event;
    SourceIdentity identity;
    while (registry.take_event(event, identity)) {
        if (event.kind == SourceEventKind::Detached) {
            ++detached_count;
            saw_x = saw_x || event.source_id == kDeviceX;
            saw_y = saw_y || event.source_id == kDeviceY;
        }
    }

    // Both, not one silently dropped behind the other.
    CHECK_EQ(detached_count, 2);
    CHECK(saw_x);
    CHECK(saw_y);

    duo_input::u1::pio_usb::set_callback_registry(nullptr);
}

TEST_CASE(a_detached_that_overflows_its_queue_escalates_instead_of_dropping_the_release) {
    // The arithmetic this test exists for. remove_device() queues one Detached
    // PER ROLE SLOT, not one per call, so a composite keyboard+mouse receiver -
    // this project's normal hardware - costs TWO Detached from a single
    // physical unplug. The first sizing of kDetachQueueCapacity assumed one,
    // and undersized the FIFO by a factor of two; the queue is now sized to
    // the whole callback budget, and this test drives the corrected
    // two-per-device cycle straight into the overflow path anyway, to prove
    // what happens there.
    //
    // Filling the FIFO first takes a shape that produces Detached WITHOUT
    // producing Ready, because granting the Keyboard or Mouse role always
    // pushes a Ready and event_queue_ offers ordinary traffic three fewer
    // slots than this FIFO has - so a queue of Ready-producing detaches would
    // saturate event_queue_ first. LogicalRole::Auxiliary is that shape: the
    // Keychron receiver's side-button channel is granted a role (so its
    // teardown owes a Detached) but is never announced with a Ready, because
    // it is not a source InputPipeline is told about on its own. Plugging and
    // unplugging that receiver while a DIFFERENT device already owns the Mouse
    // role fills the FIFO with Ready-free Detached.
    duo::test::tinyusb_host::reset();
    DeviceRegistry registry;
    duo_input::u1::pio_usb::set_callback_registry(&registry);

    Recorder keyboard_recorder;
    Recorder mouse_recorder;
    InputPipeline keyboard_pipeline(keyboard_recorder);
    InputPipeline mouse_pipeline(mouse_recorder);
    const auto drain_all = [&] {
        SourceEvent event;
        SourceIdentity identity;
        while (registry.take_event(event, identity)) {
            switch (PioUsbBackend::logical_port(identity.kind)) {
                case 0:
                    keyboard_pipeline.on_event(event, identity, 1);
                    break;
                case 1:
                    mouse_pipeline.on_event(event, identity, 1);
                    break;
                default:
                    break;
            }
        }
    };

    // One composite device carrying BOTH roles - the two-Detached-per-unplug
    // shape - with a key and a mouse button genuinely held on it.
    constexpr std::uint8_t kCompositeAddress = 2;
    duo::test::tinyusb_host::add_device(kCompositeAddress, kVendorId, kProductId);
    tuh_mount_cb(kCompositeAddress);
    duo::test::tinyusb_host::set_protocol(kCompositeAddress, 0, kProtocolKeyboard);
    tuh_hid_mount_cb(kCompositeAddress, 0, nullptr, 0);
    duo::test::tinyusb_host::set_protocol(kCompositeAddress, 1, kProtocolMouse);
    tuh_hid_mount_cb(kCompositeAddress, 1, nullptr, 0);
    registry.process_pending(0);
    drain_all();

    duo::test::tinyusb_host::set_now_us(5);
    tuh_hid_report_received_cb(kCompositeAddress, 0, kKeyA, sizeof(kKeyA));
    duo::test::tinyusb_host::set_now_us(6);
    tuh_hid_report_received_cb(kCompositeAddress, 1, kMouseButton1Down,
                               sizeof(kMouseButton1Down));
    registry.process_pending(0);
    drain_all();
    CHECK_EQ(keyboard_recorder.of(InputEventKind::KeyDown, 0x04), 1);
    CHECK_EQ(mouse_recorder.of(InputEventKind::MouseButtonDown, 0), 1);

    // From here nothing is drained at all. Fill the detach FIFO to the brim
    // with Ready-free Detached from a receiver whose own mouse channel is
    // refused the Mouse role (the composite above owns it) and whose auxiliary
    // channel therefore contributes the only role this device gets.
    for (std::size_t cycle = 0; cycle < DeviceRegistry::kDetachQueueCapacity; ++cycle) {
        duo::test::tinyusb_host::add_device(kKeychronAddress, kKeychronVendorId,
                                            kKeychronProductId);
        tuh_mount_cb(kKeychronAddress);
        duo::test::tinyusb_host::set_protocol(kKeychronAddress, 0, kProtocolMouse);
        tuh_hid_mount_cb(kKeychronAddress, 0, nullptr, 0);
        duo::test::tinyusb_host::set_protocol(kKeychronAddress, 1, kProtocolKeyboard);
        tuh_hid_mount_cb(kKeychronAddress, 1, nullptr, 0);
        registry.process_pending(0);

        tuh_umount_cb(kKeychronAddress);
        registry.process_pending(0);
    }
    CHECK_EQ(registry.detach_overflow_count(), 0u);

    // Now the composite device is unplugged. Both of its Detached - one per
    // role slot - find the FIFO full.
    tuh_umount_cb(kCompositeAddress);
    registry.process_pending(0);
    CHECK_EQ(registry.detach_overflow_count(), 2u);

    drain_all();

    // The releases were NOT lost. A dropped Detached is a dropped release-all,
    // so each one escalated through latch_fault() into a Fault carrying the
    // same identity, which reached the same pipeline and ran the same
    // release_all: nothing this device was holding survived its unplug.
    CHECK_EQ(keyboard_recorder.of(InputEventKind::KeyUp, 0x04), 1);
    CHECK_EQ(mouse_recorder.of(InputEventKind::MouseButtonUp, 0), 1);

    duo_input::u1::pio_usb::set_callback_registry(nullptr);
}

TEST_CASE(a_refused_receive_arm_is_not_retried_before_its_backoff_deadline) {
    // The half of "bounded backoff" that a counter cannot show. Bounded is
    // proved by the escalation tests above; BACKOFF means the retry does not
    // happen until the wait has actually elapsed, and it means each wait is
    // longer than the last. Neither was observable while the rig fed the
    // registry two different clocks - every deadline was already in the past
    // the first time it was checked, so the guard could be deleted and the
    // table zeroed with the whole suite still green. task_at() now moves one
    // clock, the same way main.cpp's single time_us_32() reading does.
    TwoSourceRig rig;

    // Use up the armed receive, so the RE-ARM that follows it is the one that
    // can be refused. (arm_if_needed never asks underneath an in-flight one.)
    rig.deliver(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA), 10);
    duo::test::tinyusb_host::set_receive_result(false);

    const std::uint32_t refused_at = 500000;
    rig.task_at(refused_at);
    rig.drain();
    CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyDown, 0x04), 1);

    std::size_t asked =
        duo::test::tinyusb_host::receive_count(kKeyboardAddress, kKeyboardInstance);

    // The endpoint would answer again immediately - but the first backoff has
    // to be waited out regardless. One microsecond early: nothing is asked.
    duo::test::tinyusb_host::set_receive_result(true);
    rig.task_at(refused_at + kFirstBackoffUs - 1);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(kKeyboardAddress, kKeyboardInstance),
             asked);

    // On the deadline, exactly one retry - and no second one from the same
    // arming, since the retry succeeded.
    duo::test::tinyusb_host::set_receive_result(false);
    rig.task_at(refused_at + kFirstBackoffUs);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(kKeyboardAddress, kKeyboardInstance),
             asked + 1);
    asked += 1;

    // That retry was refused too, so the SECOND wait applies - and it is
    // longer than the first. Waiting only the first backoff again asks
    // nothing; a table of three equal delays dies here.
    const std::uint32_t second_refusal_at = refused_at + kFirstBackoffUs;
    rig.task_at(second_refusal_at + kFirstBackoffUs);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(kKeyboardAddress, kKeyboardInstance),
             asked);
    rig.task_at(second_refusal_at + kSecondBackoffUs - 1);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(kKeyboardAddress, kKeyboardInstance),
             asked);
    rig.task_at(second_refusal_at + kSecondBackoffUs);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(kKeyboardAddress, kKeyboardInstance),
             asked + 1);
    asked += 1;

    // And the third is longer again. The key is still legitimately held
    // throughout: a backoff is not a fault.
    const std::uint32_t third_refusal_at = second_refusal_at + kSecondBackoffUs;
    rig.task_at(third_refusal_at + kSecondBackoffUs);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(kKeyboardAddress, kKeyboardInstance),
             asked);
    rig.task_at(third_refusal_at + kThirdBackoffUs - 1);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(kKeyboardAddress, kKeyboardInstance),
             asked);
    rig.drain();
    CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyUp, 0x04), 0);

    // The fourth failure is the one that exhausts the budget and releases
    // everything - reached only after all three waits, never before.
    rig.task_at(third_refusal_at + kThirdBackoffUs);
    rig.drain();
    CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyUp, 0x04), 1);
}

TEST_CASE(a_backoff_deadline_that_spans_the_clock_wrap_is_still_waited_out) {
    // time_us_32() wraps every ~71.6 minutes, and a deadline computed across
    // that wrap is smaller than the "now" that precedes it. A plain
    // `now_us >= deadline` therefore fires the retry IMMEDIATELY, skipping the
    // backoff entirely - or, on the other side of the same wrap, defers it by
    // up to 71 minutes, leaving the interface un-armed, never escalated, and
    // its held keys never released. This tree's convention elsewhere
    // (main.cpp's `time_us_32() - started < for_us`) is wrap-safe subtraction,
    // and so is retry_pending_arms.
    TwoSourceRig rig;

    rig.deliver(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA), 10);
    duo::test::tinyusb_host::set_receive_result(false);

    // 100 us before the wrap. The first backoff is 1000 us, so the deadline
    // lands 900 us AFTER it: 0xFFFFFF9C + 1000 == 0x384, wrapped.
    const std::uint32_t refused_at = 0xFFFFFF9Cu;
    const std::uint32_t deadline = refused_at + kFirstBackoffUs;  // == 0x384
    rig.task_at(refused_at);
    rig.drain();
    const std::size_t asked =
        duo::test::tinyusb_host::receive_count(kKeyboardAddress, kKeyboardInstance);

    duo::test::tinyusb_host::set_receive_result(true);

    // Still 850 us to wait, on the far side of the wrap from the deadline.
    // An unsigned `>=` reads this as "long past due" and retries at once.
    rig.task_at(0xFFFFFFCEu);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(kKeyboardAddress, kKeyboardInstance),
             asked);

    // One microsecond early, now past the wrap.
    rig.task_at(deadline - 1);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(kKeyboardAddress, kKeyboardInstance),
             asked);

    // On the deadline: exactly one retry, and the key was never released -
    // the interface recovered rather than being abandoned across the wrap.
    rig.task_at(deadline);
    rig.drain();
    CHECK_EQ(duo::test::tinyusb_host::receive_count(kKeyboardAddress, kKeyboardInstance),
             asked + 1);
    CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyUp, 0x04), 0);
}

TEST_CASE(an_escalated_interface_gives_its_role_back_so_a_replacement_can_claim_it) {
    // The brief said escalation goes to "logical Fault AND re-enumeration".
    // The binding spec (2026-09-02-pio-usb-hub-v1-design.md:239-254) never
    // mentions re-enumeration; what it requires is that held buttons are
    // released "before the source is forgotten". An escalated interface is
    // never forgotten - it stays mounted && faulted - and while it holds a
    // role, role_is_owned() refuses that role to every replacement device
    // until the user physically unplugs the dead one. That is the real gap,
    // and this is the test for it. (A bus reset would not have helped: this
    // project's own history records that the Keychron receiver's wedge
    // SURVIVES re-enumeration, and a reset would drop the sibling role too.)
    duo::test::tinyusb_host::reset();
    DeviceRegistry registry;
    duo_input::u1::pio_usb::set_callback_registry(&registry);

    Recorder recorder;
    InputPipeline pipeline(recorder);
    const auto drain_all = [&] {
        SourceEvent event;
        SourceIdentity identity;
        while (registry.take_event(event, identity)) {
            if (PioUsbBackend::logical_port(identity.kind) == 0) {
                pipeline.on_event(event, identity, 1);
            }
        }
    };

    duo::test::tinyusb_host::add_device(kKeyboardAddress, kVendorId, kProductId);
    tuh_mount_cb(kKeyboardAddress);
    duo::test::tinyusb_host::set_protocol(kKeyboardAddress, kKeyboardInstance, kProtocolKeyboard);
    tuh_hid_mount_cb(kKeyboardAddress, kKeyboardInstance, nullptr, 0);
    registry.process_pending(0);
    drain_all();
    duo::test::tinyusb_host::set_now_us(5);
    tuh_hid_report_received_cb(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA));
    registry.process_pending(1000);
    drain_all();
    CHECK_EQ(recorder.of(InputEventKind::KeyDown, 0x04), 1);
    CHECK(registry.owner(DeviceKind::Keyboard) != nullptr);

    // It stops answering. Four consecutive refusals exhaust kMaxArmRetries and
    // escalate; each retry is driven at a time past its own backoff deadline.
    duo::test::tinyusb_host::set_receive_result(false);
    duo::test::tinyusb_host::set_now_us(6);
    tuh_hid_report_received_cb(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA));
    registry.process_pending(1000000);
    registry.retry_pending_arms(2000000);
    registry.retry_pending_arms(3000000);
    registry.retry_pending_arms(4000000);
    drain_all();

    CHECK_EQ(registry.arm_escalation_count(), 1u);
    CHECK_EQ(recorder.of(InputEventKind::KeyUp, 0x04), 1);

    // Not forgotten - still mounted, still occupying its interface slot - but
    // no longer holding the Keyboard role.
    const auto* escalated = registry.find(kKeyboardAddress, kKeyboardInstance);
    CHECK(escalated != nullptr);
    if (escalated != nullptr) {
        CHECK_EQ(escalated->role, LogicalRole::Ignored);
    }
    CHECK(registry.owner(DeviceKind::Keyboard) == nullptr);

    // The obvious trap: a still-mounted faulted interface must not re-claim
    // the role it just released and re-fault in a loop. TinyUSB re-announcing
    // the same interface takes the duplicate-mount branch, which never re-runs
    // role assignment, and arm_if_needed() refuses a faulted interface.
    const std::uint32_t escalations_after_fault = registry.arm_escalation_count();
    tuh_hid_mount_cb(kKeyboardAddress, kKeyboardInstance, nullptr, 0);
    registry.process_pending(5000000);
    registry.retry_pending_arms(6000000);
    drain_all();
    CHECK(registry.duplicate_mount_count() > 0);
    CHECK_EQ(registry.arm_escalation_count(), escalations_after_fault);
    CHECK(registry.owner(DeviceKind::Keyboard) == nullptr);
    const auto* still_faulted = registry.find(kKeyboardAddress, kKeyboardInstance);
    CHECK(still_faulted != nullptr);
    if (still_faulted != nullptr) {
        CHECK_EQ(still_faulted->role, LogicalRole::Ignored);
    }

    // A REPLACEMENT keyboard, plugged in beside the dead one, gets the role -
    // and its keys reach the pipeline. Before this fix it was ignored for
    // ever, because the escalated interface never let the role go.
    duo::test::tinyusb_host::set_receive_result(true);
    constexpr std::uint8_t kReplacementAddress = 3;
    duo::test::tinyusb_host::add_device(kReplacementAddress, 0xAAAA, 0xBBBB);
    tuh_mount_cb(kReplacementAddress);
    duo::test::tinyusb_host::set_protocol(kReplacementAddress, 0, kProtocolKeyboard);
    tuh_hid_mount_cb(kReplacementAddress, 0, nullptr, 0);
    registry.process_pending(7000000);
    drain_all();

    const auto* replacement = registry.owner(DeviceKind::Keyboard);
    CHECK(replacement != nullptr);
    if (replacement != nullptr) {
        CHECK_EQ(replacement->dev_addr, kReplacementAddress);
    }

    constexpr std::uint8_t kKeyB[] = {0x00, 0x00, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00};
    duo::test::tinyusb_host::set_now_us(20);
    tuh_hid_report_received_cb(kReplacementAddress, 0, kKeyB, sizeof(kKeyB));
    registry.process_pending(8000000);
    drain_all();
    CHECK_EQ(recorder.of(InputEventKind::KeyDown, 0x05), 1);

    duo_input::u1::pio_usb::set_callback_registry(nullptr);
}

TEST_CASE(the_highest_generation_sharing_a_role_slot_is_what_retires_it) {
    // The Keychron receiver's auxiliary channel shares the mouse's role slot
    // and can only mount AFTER the mouse (has_mouse_sibling requires the
    // mouse already mounted), so it always carries the HIGHER generation of
    // the two. remove_device() used to retire the FIRST interface it found
    // for the slot - the mouse's, the lower one - which let every
    // AuxiliaryReport the auxiliary channel had queued escape pop_event()'s
    // stale filter entirely.
    //
    // It is harmless TODAY only because a Detached is delivered ahead of the
    // event queue and pipeline.cpp's on_auxiliary_report then finds the state
    // it gates on already cleared - exactly the "coincidentally harmless"
    // argument this file refuses to accept for the keyboard case. So the
    // assertion is at the registry boundary, where the difference is real: a
    // stale AuxiliaryReport is either discarded or handed out.
    duo::test::tinyusb_host::reset();
    DeviceRegistry registry;
    duo_input::u1::pio_usb::set_callback_registry(&registry);

    Recorder recorder;
    InputPipeline pipeline(recorder);
    int auxiliary_reports_delivered = 0;
    const auto drain_all = [&] {
        SourceEvent event;
        SourceIdentity identity;
        while (registry.take_event(event, identity)) {
            if (event.kind == SourceEventKind::AuxiliaryReport) {
                ++auxiliary_reports_delivered;
            }
            if (PioUsbBackend::logical_port(identity.kind) == 1) {
                pipeline.on_event(event, identity, 1);
            }
        }
    };

    duo::test::tinyusb_host::add_device(kKeychronAddress, kKeychronVendorId, kKeychronProductId);
    tuh_mount_cb(kKeychronAddress);
    duo::test::tinyusb_host::set_protocol(kKeychronAddress, 0, kProtocolMouse);
    tuh_hid_mount_cb(kKeychronAddress, 0, nullptr, 0);
    duo::test::tinyusb_host::set_protocol(kKeychronAddress, 1, kProtocolKeyboard);
    tuh_hid_mount_cb(kKeychronAddress, 1, nullptr, 0);
    registry.process_pending(0);
    drain_all();

    const auto* mouse = registry.find(kKeychronAddress, 0);
    const auto* auxiliary = registry.find(kKeychronAddress, 1);
    CHECK(mouse != nullptr);
    CHECK(auxiliary != nullptr);
    if (mouse != nullptr && auxiliary != nullptr) {
        CHECK_EQ(mouse->role, LogicalRole::Mouse);
        CHECK_EQ(auxiliary->role, LogicalRole::Auxiliary);
        // The whole reason the "first one wins" bug was invisible: the
        // auxiliary channel's generation is always the higher of the pair.
        CHECK(auxiliary->generation > mouse->generation);
    }

    // A side-button press is queued and NOT drained...
    duo::test::tinyusb_host::set_now_us(10);
    tuh_hid_report_received_cb(kKeychronAddress, 1, kKeychronSidePress,
                               sizeof(kKeychronSidePress));
    registry.process_pending(0);
    CHECK_EQ(auxiliary_reports_delivered, 0);

    // ...and the receiver is unplugged before anything drains it.
    tuh_umount_cb(kKeychronAddress);
    registry.process_pending(0);

    drain_all();

    // The Detached that jumped ahead of it retired the AUXILIARY channel's own
    // generation, so the report behind it is stale and was discarded rather
    // than handed out after its source had already been released.
    CHECK_EQ(auxiliary_reports_delivered, 0);
    CHECK(registry.stale_event_discard_count() > 0);
    CHECK_EQ(recorder.count(InputEventKind::MouseButtonDown), 0);

    duo_input::u1::pio_usb::set_callback_registry(nullptr);
}

TEST_CASE(a_dead_link_to_u2_never_stops_pc1_input) {
    // The brief asks this to be ASSERTED, not argued from a diff. PC1's input
    // path is Core 1's: PIO USB host -> DeviceRegistry -> InputPipeline -> the
    // HID state this firmware publishes on its own device port. PC2's is Core
    // 0's SPI link to U2. The two share nothing, and this is the test that
    // says so out loud - every transfer to U2 is refused for the whole run,
    // and PC1 keeps receiving keystrokes and button presses throughout.
    TwoSourceRig rig;
    HidStateManager outputs;
    SpiMaster link;
    duo::test::spi_link().reset();
    duo::test::spi_link().accept = false;  // U2 is gone.

    // Control, so the test cannot pass vacuously: the link really is dead.
    CHECK_FALSE(link.poll(1, outputs));
    CHECK_EQ(duo::test::spi_link().count, 0u);

    // PC1's own input, delivered while that is true. Routed exactly the way
    // main.cpp routes it - through the pipelines, then onto PC1's HID state.
    rig.deliver(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA), 10);
    rig.pump();
    rig.deliver(kMouseAddress, kMouseInstance, kMouseButton1Down, sizeof(kMouseButton1Down), 20);
    rig.pump();
    CHECK_FALSE(link.poll(2, outputs));

    for (const InputEvent& event : rig.keyboard_recorder.events) {
        if (event.kind == InputEventKind::KeyDown) {
            outputs.physical_key(Target::Pc1, static_cast<std::uint8_t>(event.code), true);
        } else if (event.kind == InputEventKind::KeyUp) {
            outputs.physical_key(Target::Pc1, static_cast<std::uint8_t>(event.code), false);
        }
    }
    std::uint8_t buttons = 0;
    for (const InputEvent& event : rig.mouse_recorder.events) {
        if (event.kind == InputEventKind::MouseButtonDown) {
            buttons |= static_cast<std::uint8_t>(1u << event.code);
        } else if (event.kind == InputEventKind::MouseButtonUp) {
            buttons &= static_cast<std::uint8_t>(~(1u << event.code));
        }
    }
    outputs.set_mouse_buttons(Target::Pc1, buttons);

    CHECK(outputs.snapshot(Target::Pc1).keyboard.contains(0x04));
    CHECK_EQ(outputs.snapshot(Target::Pc1).mouse.buttons, 0x01);
    // PC2 was told nothing, because there is nobody there to tell.
    CHECK_EQ(duo::test::spi_link().count, 0u);
    CHECK_FALSE(outputs.snapshot(Target::Pc2).keyboard.contains(0x04));

    // And it keeps working: a release arrives on PC1 with the link still dead.
    rig.deliver(kKeyboardAddress, kKeyboardInstance, kNoKeys, sizeof(kNoKeys), 30);
    rig.pump();
    CHECK_FALSE(link.poll(3, outputs));
    CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyUp, 0x04), 1);
    outputs.physical_key(Target::Pc1, 0x04, false);
    CHECK_FALSE(outputs.snapshot(Target::Pc1).keyboard.contains(0x04));

    duo::test::spi_link().reset();
}

namespace {

/// Flash that records every operation and never lies about what it holds.
///
/// The point is the counters: no code under firmware/u1_main/pio_usb has any
/// business erasing or programming anything, and this is what says so.
class CountingFlash final : public FlashBackend {
public:
    CountingFlash() : bytes_(duo_input::storage::kFlashSize, 0xFF) {}

    bool erase(std::uint32_t offset, std::size_t size) override {
        ++operations_;
        if (offset + size > bytes_.size()) {
            return false;
        }
        std::memset(&bytes_[offset], 0xFF, size);
        return true;
    }

    bool program(std::uint32_t offset, const std::uint8_t* data, std::size_t size) override {
        ++operations_;
        if (offset + size > bytes_.size()) {
            return false;
        }
        std::memcpy(&bytes_[offset], data, size);
        return true;
    }

    bool read(std::uint32_t offset, std::uint8_t* data, std::size_t size) const override {
        if (offset + size > bytes_.size()) {
            return false;
        }
        std::memcpy(data, &bytes_[offset], size);
        return true;
    }

    const std::uint8_t* direct(std::uint32_t offset) const override {
        return offset < bytes_.size() ? &bytes_[offset] : nullptr;
    }

    std::size_t operations() const { return operations_; }

    /// A cheap fingerprint of everything stored, so "unchanged" means the
    /// bytes, not merely the operation count.
    std::uint64_t fingerprint() const {
        std::uint64_t hash = 1469598103934665603ull;
        for (std::uint8_t byte : bytes_) {
            hash = (hash ^ byte) * 1099511628211ull;
        }
        return hash;
    }

private:
    std::vector<std::uint8_t> bytes_;
    std::size_t operations_ = 0;
};

}  // namespace

TEST_CASE(no_backend_failure_touches_flash_or_profile_state) {
    // The other assertion the brief asks for and an earlier round substituted
    // a grep for. A grep says "no identifier from storage/ appears in
    // pio_usb/*.cpp today"; it cannot say "and none of these failure paths
    // reaches flash", which is the claim. So: a real AbStore over a flash
    // backend that counts every erase and every program, a stored
    // configuration written through it, and then every failure this task's
    // code can produce - run against the same process.
    duo::test::tinyusb_host::reset();
    CountingFlash flash;
    AbStore store(flash);

    // Vacuity control: the counter is live and the store really writes.
    constexpr std::uint8_t kProfile[] = {0xDE, 0xAD, 0xBE, 0xEF, 0x01, 0x02, 0x03, 0x04};
    std::uint8_t digest[duo_input::crypto::kSha256DigestSize] = {};
    duo_input::crypto::sha256(kProfile, sizeof(kProfile), digest);
    CHECK_EQ(store.begin(static_cast<std::uint32_t>(sizeof(kProfile)), digest), StoreError::None);
    CHECK_EQ(store.write_chunk(0, kProfile, sizeof(kProfile)), StoreError::None);
    CHECK_EQ(store.verify(), StoreError::None);
    CHECK_EQ(store.commit(), StoreError::None);
    CHECK(flash.operations() > 0);
    CHECK(store.scan().has_active);

    const std::size_t operations_before = flash.operations();
    const std::uint64_t fingerprint_before = flash.fingerprint();

    {
        // 1. A host that never came up: the whole-host Fault, both stages.
        duo::test::tinyusb_host::set_host_initialization_result(false, false);
        TwoSourceRig rig;
        rig.pump();

        // 2. Detach mid-key and mid-button.
        duo::test::tinyusb_host::set_host_initialization_result(true, true);
    }
    {
        TwoSourceRig rig;
        rig.deliver(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA), 10);
        rig.pump();
        rig.deliver(kMouseAddress, kMouseInstance, kMouseButton1Down,
                    sizeof(kMouseButton1Down), 20);
        rig.pump();
        tuh_umount_cb(kKeyboardAddress);
        tuh_umount_cb(kMouseAddress);
        rig.pump();
        CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyUp, 0x04), 1);
    }
    {
        // 3. A persistent receive-arm refusal, escalated to a release-all.
        TwoSourceRig rig;
        rig.deliver(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA), 10);
        rig.pump();
        rig.deliver(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA), 15);
        duo::test::tinyusb_host::set_receive_result(false);
        rig.pump();
        std::uint32_t at = rig.now_us;
        for (int attempt = 0; attempt < DeviceRegistry::kMaxArmRetries + 1; ++attempt) {
            at += 100000;
            rig.task_at(at);
        }
        rig.drain();
        CHECK_EQ(rig.keyboard_recorder.of(InputEventKind::KeyUp, 0x04), 1);
        duo::test::tinyusb_host::set_receive_result(true);
    }
    {
        // 4. A run of zero-length completions - the stall signal - escalated.
        TwoSourceRig rig;
        rig.deliver(kMouseAddress, kMouseInstance, kMouseButton1Down,
                    sizeof(kMouseButton1Down), 10);
        rig.pump();
        for (int index = 0; index < DeviceRegistry::kMaxArmRetries + 1; ++index) {
            rig.deliver(kMouseAddress, kMouseInstance, nullptr, 0,
                        20 + static_cast<std::uint32_t>(index));
            rig.pump();
        }
        CHECK_EQ(rig.mouse_recorder.of(InputEventKind::MouseButtonUp, 0), 1);
    }
    {
        // 5. Queue overflow, on both queues, from withheld drains.
        duo::test::tinyusb_host::reset();
        DeviceRegistry registry;
        duo_input::u1::pio_usb::set_callback_registry(&registry);
        for (std::size_t cycle = 0; cycle < DeviceRegistry::kEventQueueCapacity + 4; ++cycle) {
            duo::test::tinyusb_host::add_device(kKeyboardAddress,
                                                static_cast<std::uint16_t>(0x1000 + cycle),
                                                static_cast<std::uint16_t>(0x2000 + cycle));
            tuh_mount_cb(kKeyboardAddress);
            duo::test::tinyusb_host::set_protocol(kKeyboardAddress, kKeyboardInstance,
                                                  kProtocolKeyboard);
            tuh_hid_mount_cb(kKeyboardAddress, kKeyboardInstance, nullptr, 0);
            registry.process_pending(0);
            tuh_umount_cb(kKeyboardAddress);
            registry.process_pending(0);
        }
        CHECK(registry.event_overflow_count() > 0);
        duo_input::u1::pio_usb::set_callback_registry(nullptr);
    }

    // Not one erase, not one program, and not one byte different.
    CHECK_EQ(flash.operations(), operations_before);
    CHECK_EQ(flash.fingerprint(), fingerprint_before);
    CHECK(store.scan().has_active);
}
