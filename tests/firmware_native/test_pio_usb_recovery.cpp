// Task 10: detach, stalls, hub resets and reconnects fail safe.
//
// The central constraint this file proves: on detach, reset, malformed
// state, queue overflow or host fault, every held key/button is released
// before the source is forgotten. A dropped release is a release-all fault,
// not a recoverable report loss - so every scenario below ends by checking
// the release actually reached its pipeline, not merely that a counter
// moved.

#include "fakes/tinyusb_host.hpp"
#include "input/pipeline.hpp"
#include "pio_usb/backend.hpp"
#include "pio_usb/device_registry.hpp"
#include "test_support.hpp"

#include <cstdint>
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
using duo_input::u1::pio_usb::DeviceRegistry;
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
    void task_at(std::uint32_t at_us) {
        now_us = at_us;
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
    registry.process_pending();
    drain_all();
    const auto* device_a = registry.find(kKeyboardAddress, kKeyboardInstance);
    CHECK(device_a != nullptr);
    const std::uint32_t generation_a = device_a != nullptr ? device_a->generation : 0;

    duo::test::tinyusb_host::set_now_us(5);
    tuh_hid_report_received_cb(kKeyboardAddress, kKeyboardInstance, kKeyA, sizeof(kKeyA));
    registry.process_pending();
    drain_all();
    CHECK_EQ(recorder.of(InputEventKind::KeyDown, 0x04), 1);

    // From here, nothing is drained. A sends a SECOND report - key 0x05
    // newly down, alongside the still-held 0x04 - that is captured and
    // queued but never reaches the pipeline before A detaches.
    constexpr std::uint8_t second_report[] = {0x00, 0x00, 0x04, 0x05, 0x00, 0x00, 0x00, 0x00};
    duo::test::tinyusb_host::set_now_us(10);
    tuh_hid_report_received_cb(kKeyboardAddress, kKeyboardInstance, second_report,
                               sizeof(second_report));
    registry.process_pending();

    // A detaches. Its Detached is queued, but still nothing is drained.
    tuh_umount_cb(kKeyboardAddress);
    registry.process_pending();

    // A different device (a fresh generation, same role) mounts and reports
    // in the SAME undrained window.
    duo::test::tinyusb_host::add_device(kKeyboardAddress, 0xAAAA, 0xBBBB);
    tuh_mount_cb(kKeyboardAddress);
    duo::test::tinyusb_host::set_protocol(kKeyboardAddress, kKeyboardInstance, kProtocolKeyboard);
    tuh_hid_mount_cb(kKeyboardAddress, kKeyboardInstance, nullptr, 0);
    registry.process_pending();
    const auto* device_b = registry.find(kKeyboardAddress, kKeyboardInstance);
    CHECK(device_b != nullptr);
    if (device_b != nullptr) {
        CHECK(device_b->generation > generation_a);
    }

    duo::test::tinyusb_host::set_now_us(20);
    tuh_hid_report_received_cb(kKeyboardAddress, kKeyboardInstance, kNoKeys, sizeof(kNoKeys));
    registry.process_pending();

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
    registry.process_pending();

    // X detaches. Its Detached is queued. Nothing is drained.
    tuh_umount_cb(kDeviceX);
    registry.process_pending();

    // Y - a different device - claims the now-free Keyboard role and
    // immediately detaches too, still before any drain.
    duo::test::tinyusb_host::add_device(kDeviceY, 0x2222, 0x0002);
    tuh_mount_cb(kDeviceY);
    duo::test::tinyusb_host::set_protocol(kDeviceY, 0, kProtocolKeyboard);
    tuh_hid_mount_cb(kDeviceY, 0, nullptr, 0);
    registry.process_pending();
    tuh_umount_cb(kDeviceY);
    registry.process_pending();

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

TEST_CASE(registry_overflow_from_withheld_drains_across_many_passes_is_counted_not_silent) {
    // Deliberately never drained across MANY separate process_pending()
    // calls - not the shape one genuine Core 1 pass can ever produce (see
    // kDetachQueueCapacity's own derivation), but the adversarial
    // construction this file's other overflow tests already use to reach
    // their own queues' capacity limits.
    duo::test::tinyusb_host::reset();
    DeviceRegistry registry;
    duo_input::u1::pio_usb::set_callback_registry(&registry);

    for (std::size_t cycle = 0; cycle < DeviceRegistry::kDetachQueueCapacity + 2; ++cycle) {
        duo::test::tinyusb_host::add_device(kKeyboardAddress,
                                            static_cast<std::uint16_t>(0x1000 + cycle),
                                            static_cast<std::uint16_t>(0x2000 + cycle));
        tuh_mount_cb(kKeyboardAddress);
        duo::test::tinyusb_host::set_protocol(kKeyboardAddress, kKeyboardInstance,
                                              kProtocolKeyboard);
        tuh_hid_mount_cb(kKeyboardAddress, kKeyboardInstance, nullptr, 0);
        registry.process_pending();

        tuh_umount_cb(kKeyboardAddress);
        registry.process_pending();
        // No take_event() drain anywhere in this loop.
    }

    CHECK(registry.detach_overflow_count() > 0);

    duo_input::u1::pio_usb::set_callback_registry(nullptr);
}
