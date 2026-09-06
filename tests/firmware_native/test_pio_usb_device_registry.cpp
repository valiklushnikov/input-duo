#include "fakes/tinyusb_host.hpp"
// For HCD_EVENT_DEVICE_ATTACH: the recovery has to queue that event id and no
// other, and the enumerator is the library's own (fakes/host/hcd.h mirrors
// .deps/tinyusb/src/host/hcd.h:56-63).
#include "host/hcd.h"
#include "pio_usb/backend.hpp"
#include "pio_usb/device_registry.hpp"
#include "test_support.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <vector>

using duo::test::tinyusb_host::kProtocolKeyboard;
using duo::test::tinyusb_host::kProtocolMouse;
using duo::test::tinyusb_host::kProtocolNone;
using duo_input::u1::input::DeviceKind;
using duo_input::u1::input::SourceEvent;
using duo_input::u1::input::SourceEventKind;
using duo_input::u1::input::SourceIdentity;
using duo_input::u1::pio_usb::DeviceRegistry;
using duo_input::u1::pio_usb::kEpXferAborted;
using duo_input::u1::pio_usb::kEpXferData1;
using duo_input::u1::pio_usb::kEpXferHasTransfer;
using duo_input::u1::pio_usb::kEpXferHostOut;
using duo_input::u1::pio_usb::kEpXferNeedPre;
using duo_input::u1::pio_usb::kEpXferOpen;
using duo_input::u1::pio_usb::kEpXferSetupStaged;
using duo_input::u1::pio_usb::kEpXferStalled;
using duo_input::u1::pio_usb::LogicalRole;
using duo_input::u1::pio_usb::PioUsbBackend;

static_assert(PioUsbBackend::observation_publication_is_always_lock_free(),
              "cross-core host observations must use lock-free publication");

namespace {

constexpr std::uint8_t kDescriptor[] = {'a', 'b', 'c'};
constexpr std::array<std::uint8_t, 32> kAbcSha256 = {
    0xba, 0x78, 0x16, 0xbf, 0x8f, 0x01, 0xcf, 0xea,
    0x41, 0x41, 0x40, 0xde, 0x5d, 0xae, 0x22, 0x23,
    0xb0, 0x03, 0x61, 0xa3, 0x96, 0x17, 0x7a, 0x9c,
    0xb4, 0x10, 0xff, 0x61, 0xf2, 0x00, 0x15, 0xad,
};

struct RegistryRig {
    DeviceRegistry registry;

    RegistryRig() {
        duo::test::tinyusb_host::reset();
        duo_input::u1::pio_usb::set_callback_registry(&registry);
    }

    ~RegistryRig() { duo_input::u1::pio_usb::set_callback_registry(nullptr); }

    void device(std::uint8_t address, std::uint16_t vendor_id,
                std::uint16_t product_id) {
        duo::test::tinyusb_host::add_device(address, vendor_id, product_id);
    }

    void hub(std::uint16_t vendor_id, std::uint16_t product_id) {
        duo::test::tinyusb_host::add_hub(vendor_id, product_id);
    }

    void hid(std::uint8_t address, std::uint8_t instance, std::uint8_t protocol) {
        hid(address, instance, protocol, kDescriptor, sizeof(kDescriptor));
    }

    void hid(std::uint8_t address, std::uint8_t instance, std::uint8_t protocol,
             const std::uint8_t* descriptor, std::uint16_t descriptor_size) {
        duo::test::tinyusb_host::set_protocol(address, instance, protocol);
        tuh_hid_mount_cb(address, instance, descriptor, descriptor_size);
    }

    /// Discard whatever take_event() has queued - a mount's own Ready among
    /// it, once a role is assigned. A real Core 1 pass always drains
    /// completely before its next tuh_task() call; a test that mounts, does
    /// not drain, then checks for exactly one LATER event (a Detached, a
    /// Fault) needs this first so that later event is not read behind a
    /// Ready this file's older tests were written before Task 8 existed.
    void drain_ready() {
        SourceEvent event;
        SourceIdentity identity;
        while (registry.take_event(event, identity)) {
        }
    }
};

std::vector<std::uint8_t> neutral_mouse_descriptor() {
    return {
        0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x09, 0x01, 0xA1, 0x00,
        0x05, 0x09, 0x19, 0x01, 0x29, 0x05, 0x15, 0x00, 0x25, 0x01,
        0x95, 0x05, 0x75, 0x01, 0x81, 0x02,
        0x95, 0x01, 0x75, 0x03, 0x81, 0x03,
        0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 0x09, 0x38,
        0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x03, 0x81, 0x06,
        0xC0, 0xC0,
    };
}

}  // namespace

TEST_CASE(hub_is_internal_and_downstream_mount_orders_keep_the_same_keyboard_owner) {
    {
        RegistryRig rig;
        rig.hub(0x2109, 0x2817);
        rig.device(duo::test::tinyusb_host::kFirstDownstreamAddress, 0x3434, 0xD030);
        tuh_mount_cb(duo::test::tinyusb_host::kFirstDownstreamAddress);
        rig.hid(duo::test::tinyusb_host::kFirstDownstreamAddress, 0, kProtocolKeyboard);
        rig.registry.process_pending(0);

        const auto* keyboard = rig.registry.owner(DeviceKind::Keyboard);
        CHECK(keyboard != nullptr);
        CHECK_EQ(keyboard->dev_addr, duo::test::tinyusb_host::kFirstDownstreamAddress);
        CHECK_EQ(keyboard->instance, 0u);
    }

    {
        RegistryRig rig;
        rig.hub(0x2109, 0x2817);
        rig.device(duo::test::tinyusb_host::kFirstDownstreamAddress, 0x3434, 0xD030);
        rig.hid(duo::test::tinyusb_host::kFirstDownstreamAddress, 0, kProtocolKeyboard);
        tuh_mount_cb(duo::test::tinyusb_host::kFirstDownstreamAddress);
        rig.registry.process_pending(0);

        const auto* keyboard = rig.registry.owner(DeviceKind::Keyboard);
        CHECK(keyboard != nullptr);
        CHECK_EQ(keyboard->dev_addr, duo::test::tinyusb_host::kFirstDownstreamAddress);
        CHECK_EQ(rig.registry.device_count(), 1u);
    }
}

TEST_CASE(keyboard_and_mouse_ownership_follows_protocol_not_hub_address_order) {
    RegistryRig rig;
    rig.device(duo::test::tinyusb_host::kFirstDownstreamAddress, 0x1111, 0x0001);
    rig.device(duo::test::tinyusb_host::kFirstDownstreamAddress + 1, 0x2222, 0x0002);
    rig.hid(duo::test::tinyusb_host::kFirstDownstreamAddress + 1, 0, kProtocolKeyboard);
    rig.hid(duo::test::tinyusb_host::kFirstDownstreamAddress, 0, kProtocolMouse);
    rig.registry.process_pending(0);

    CHECK_EQ(rig.registry.owner(DeviceKind::Keyboard)->dev_addr,
             duo::test::tinyusb_host::kFirstDownstreamAddress + 1);
    CHECK_EQ(rig.registry.owner(DeviceKind::Mouse)->dev_addr,
             duo::test::tinyusb_host::kFirstDownstreamAddress);
    CHECK_EQ(rig.registry.owner(DeviceKind::Keyboard)->role, LogicalRole::Keyboard);
    CHECK_EQ(rig.registry.owner(DeviceKind::Mouse)->role, LogicalRole::Mouse);
}

TEST_CASE(composite_receiver_instances_remain_distinct_registry_entries) {
    RegistryRig rig;
    rig.device(3, 0x3434, 0xD030);
    rig.hid(3, 0, kProtocolMouse);
    rig.hid(3, 1, kProtocolNone);
    rig.registry.process_pending(0);

    const auto* mouse = rig.registry.find(3, 0);
    const auto* auxiliary = rig.registry.find(3, 1);
    CHECK(mouse != nullptr);
    CHECK(auxiliary != nullptr);
    CHECK(mouse != auxiliary);
    CHECK_EQ(mouse->role, LogicalRole::Mouse);
    CHECK_EQ(auxiliary->role, LogicalRole::Ignored);
    CHECK_EQ(auxiliary->identity.vendor_id, 0x3434u);
    CHECK_EQ(auxiliary->identity.product_id, 0xD030u);
}

TEST_CASE(duplicate_mount_cannot_duplicate_the_entry_or_in_flight_receive) {
    RegistryRig rig;
    rig.device(7, 0x1234, 0x5678);
    rig.hid(7, 2, kProtocolKeyboard);
    rig.hid(7, 2, kProtocolKeyboard);
    rig.registry.process_pending(0);

    CHECK_EQ(rig.registry.interface_count(), 1u);
    CHECK_EQ(rig.registry.duplicate_mount_count(), 1u);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(7, 2), 1u);
    CHECK(rig.registry.find(7, 2)->report_in_flight);
}

TEST_CASE(mount_callback_does_not_arm_until_its_bounded_record_is_processed) {
    RegistryRig rig;
    rig.device(7, 0x1234, 0x5678);
    rig.hid(7, 2, kProtocolKeyboard);

    CHECK_EQ(duo::test::tinyusb_host::receive_count(7, 2), 0u);
    rig.registry.process_pending(0);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(7, 2), 1u);
    CHECK(rig.registry.find(7, 2)->report_in_flight);
}

TEST_CASE(fixed_device_and_interface_capacity_counts_and_ignores_overflow) {
    RegistryRig rig;
    for (std::uint8_t address = 1; address <= DeviceRegistry::kDeviceCapacity; ++address) {
        rig.device(address, 0x1000u + address, 0x2000u + address);
        tuh_mount_cb(address);
    }
    rig.device(9, 0x9999, 0x9999);
    tuh_mount_cb(9);

    std::uint8_t address = 1;
    std::uint8_t instance = 0;
    for (std::size_t index = 0; index < DeviceRegistry::kInterfaceCapacity; ++index) {
        rig.hid(address, instance++, kProtocolNone);
        if (instance == 2) {
            instance = 0;
            ++address;
        }
    }
    rig.hid(5, 7, kProtocolNone);
    rig.registry.process_pending(0);

    CHECK(DeviceRegistry::kDeviceCapacity >= 5u);
    CHECK(DeviceRegistry::kInterfaceCapacity >= 8u);
    CHECK_EQ(DeviceRegistry::kInterfaceCapacity,
             DeviceRegistry::kDownstreamDeviceCapacity *
                 DeviceRegistry::kInterfacesPerDownstreamDevice);
    CHECK(DeviceRegistry::kCallbackQueueCapacity >=
          (2u * DeviceRegistry::kInterfaceCapacity) +
              DeviceRegistry::kDownstreamDeviceCapacity);
    CHECK_EQ(rig.registry.device_count(), DeviceRegistry::kDeviceCapacity);
    CHECK_EQ(rig.registry.interface_count(), DeviceRegistry::kInterfaceCapacity);
    CHECK_EQ(rig.registry.device_overflow_count(), 1u);
    CHECK_EQ(rig.registry.interface_overflow_count(), 1u);
    CHECK(rig.registry.find(5, 7) == nullptr);
}

TEST_CASE(unmount_by_device_address_removes_every_composite_instance) {
    RegistryRig rig;
    rig.device(6, 0x3434, 0xD030);
    rig.hid(6, 0, kProtocolMouse);
    rig.hid(6, 1, kProtocolNone);
    rig.registry.process_pending(0);
    CHECK_EQ(rig.registry.interface_count(), 2u);

    tuh_umount_cb(6);
    rig.registry.process_pending(0);

    CHECK(rig.registry.find(6, 0) == nullptr);
    CHECK(rig.registry.find(6, 1) == nullptr);
    CHECK_EQ(rig.registry.interface_count(), 0u);
    CHECK_EQ(rig.registry.device_count(), 0u);
}

TEST_CASE(repeated_unmount_by_device_address_is_safe) {
    RegistryRig rig;
    rig.device(6, 0x3434, 0xD030);
    rig.hid(6, 0, kProtocolMouse);
    rig.registry.process_pending(0);
    rig.drain_ready();

    tuh_umount_cb(6);
    tuh_hid_umount_cb(6, 0);
    rig.registry.process_pending(0);

    SourceEvent event;
    SourceIdentity identity;
    CHECK(rig.registry.take_event(event, identity));
    CHECK_EQ(event.kind, SourceEventKind::Detached);
    CHECK_EQ(identity.kind, DeviceKind::Mouse);
    CHECK_FALSE(rig.registry.take_event(event, identity));
    CHECK_EQ(rig.registry.interface_count(), 0u);
}

TEST_CASE(first_usable_role_owner_is_stable_and_extra_interfaces_are_diagnosed) {
    RegistryRig rig;
    rig.device(2, 0x1000, 0x0001);
    rig.device(3, 0x1000, 0x0002);
    rig.hid(2, 0, kProtocolKeyboard);
    rig.hid(3, 0, kProtocolKeyboard);
    rig.registry.process_pending(0);

    CHECK_EQ(rig.registry.owner(DeviceKind::Keyboard)->dev_addr, 2u);
    CHECK_EQ(rig.registry.find(3, 0)->role, LogicalRole::Ignored);
    CHECK_EQ(rig.registry.ignored_interface_count(), 1u);
    // V1 accepts one logical keyboard and one logical mouse, so the second
    // keyboard is ignored deterministically - and the reason is kept apart
    // from "this firmware could not classify it at all", because on a bench
    // those two are a spare keyboard and a broken one.
    CHECK_EQ(rig.registry.ignored_role_taken_count(), 1u);
}

// The other reason an interface ends up with no role: nothing here could
// classify it. The Keychron receiver's middle interface is one of these in
// real life, and reporting it as "the role was already taken" would send
// somebody looking for a second mouse that does not exist.
TEST_CASE(an_unclassifiable_interface_is_not_counted_as_a_role_collision) {
    RegistryRig rig;
    rig.device(2, 0x1000, 0x0001);
    rig.hid(2, 0, kProtocolNone, nullptr, 0);
    rig.registry.process_pending(0);

    CHECK_EQ(rig.registry.find(2, 0)->role, LogicalRole::Ignored);
    CHECK_EQ(rig.registry.ignored_interface_count(), 1u);
    CHECK_EQ(rig.registry.ignored_role_taken_count(), 0u);
}

// A mounted interface's descriptor length is what Task 11's diagnostics put on
// the wire beside its hash, so it has to be kept rather than recomputed from a
// buffer the callback has already handed back.
TEST_CASE(a_mounted_interface_remembers_how_many_descriptor_bytes_it_read) {
    RegistryRig rig;
    rig.device(2, 0x1000, 0x0001);
    rig.hid(2, 0, kProtocolKeyboard);
    rig.registry.process_pending(0);

    CHECK(rig.registry.find(2, 0)->descriptor_present);
    CHECK_EQ(rig.registry.find(2, 0)->descriptor_bytes, sizeof(kDescriptor));
}

TEST_CASE(an_interface_that_gave_up_no_descriptor_reports_no_descriptor_bytes) {
    RegistryRig rig;
    rig.device(2, 0x1000, 0x0001);
    rig.hid(2, 0, kProtocolKeyboard, nullptr, 0);
    rig.registry.process_pending(0);

    CHECK_FALSE(rig.registry.find(2, 0)->descriptor_present);
    CHECK_EQ(rig.registry.find(2, 0)->descriptor_bytes, 0u);
}

TEST_CASE(accepted_report_record_is_processed_before_exactly_one_rearm) {
    RegistryRig rig;
    rig.device(2, 0x1234, 0x5678);
    rig.hid(2, 0, kProtocolKeyboard);
    rig.registry.process_pending(0);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(2, 0), 1u);

    const std::uint8_t report[] = {0, 0, 4, 0, 0, 0, 0, 0};
    tuh_hid_report_received_cb(2, 0, report, sizeof(report));
    CHECK_FALSE(rig.registry.find(2, 0)->report_in_flight);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(2, 0), 1u);

    rig.registry.process_pending(0);
    CHECK(rig.registry.find(2, 0)->report_in_flight);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(2, 0), 2u);
    rig.registry.process_pending(0);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(2, 0), 2u);
}

TEST_CASE(oversized_report_becomes_fault_without_truncation_or_rearm) {
    RegistryRig rig;
    rig.device(2, 0x1234, 0x5678);
    rig.hid(2, 0, kProtocolKeyboard);
    rig.registry.process_pending(0);
    rig.drain_ready();

    std::array<std::uint8_t, 65> report{};
    tuh_hid_report_received_cb(2, 0, report.data(), report.size());
    rig.registry.process_pending(0);

    SourceEvent event;
    SourceIdentity identity;
    CHECK(rig.registry.take_event(event, identity));
    CHECK_EQ(event.kind, SourceEventKind::Fault);
    CHECK_EQ(event.report_size, 0u);
    CHECK_EQ(identity.kind, DeviceKind::Keyboard);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(2, 0), 1u);
}

TEST_CASE(full_callback_queue_latches_owed_fault_and_does_not_rearm) {
    RegistryRig rig;
    rig.device(2, 0x1234, 0x5678);
    rig.hid(2, 0, kProtocolKeyboard);
    rig.registry.process_pending(0);
    rig.drain_ready();

    rig.device(1, 0x2109, 0x2817);
    for (std::size_t index = 0; index < DeviceRegistry::kCallbackQueueCapacity; ++index) {
        tuh_mount_cb(1);
    }
    const std::uint8_t release[] = {0, 0, 0, 0, 0, 0, 0, 0};
    tuh_hid_report_received_cb(2, 0, release, sizeof(release));

    SourceEvent event;
    SourceIdentity identity;
    CHECK(rig.registry.take_event(event, identity));
    CHECK_EQ(event.kind, SourceEventKind::Fault);
    CHECK_EQ(identity.kind, DeviceKind::Keyboard);
    CHECK_EQ(rig.registry.callback_overflow_count(), 1u);
    CHECK(rig.registry.find(2, 0)->report_in_flight);

    rig.registry.process_pending(0);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(2, 0), 1u);
}

TEST_CASE(maximum_downstream_burst_keeps_every_unmount_and_accepts_address_reuse) {
    RegistryRig rig;
    constexpr std::uint8_t kFirstDownstreamAddress =
        duo::test::tinyusb_host::kFirstDownstreamAddress;
    constexpr std::uint8_t kLastDownstreamAddress =
        duo::test::tinyusb_host::kLastDownstreamAddress;
    constexpr std::uint8_t kDownstreamCount =
        (kLastDownstreamAddress - kFirstDownstreamAddress) + 1;
    const std::uint8_t report[] = {0, 0, 0, 0, 0, 0, 0, 0};

    rig.hub(0x2109, 0x2817);
    for (std::uint8_t offset = 0; offset < kDownstreamCount; ++offset) {
        const std::uint8_t address = kFirstDownstreamAddress + offset;
        rig.device(address, static_cast<std::uint16_t>(0x1000u + address), 0x0001);
        tuh_mount_cb(address);
        rig.hid(address, 0, address == kLastDownstreamAddress ? kProtocolKeyboard
                                                               : kProtocolNone);
        rig.hid(address, 1, address == kFirstDownstreamAddress ? kProtocolMouse
                                                                : kProtocolNone);
    }
    rig.registry.process_pending(0);

    for (std::uint8_t offset = 0; offset < kDownstreamCount; ++offset) {
        const std::uint8_t address = kFirstDownstreamAddress + offset;
        tuh_hid_report_received_cb(address, 0, report, sizeof(report));
        tuh_hid_report_received_cb(address, 1, report, sizeof(report));
    }
    // Vendored TinyUSB reports each device removal before hidh_close() emits
    // that same device's HID unmount callbacks.
    for (std::uint8_t offset = 0; offset < kDownstreamCount; ++offset) {
        const std::uint8_t address = kFirstDownstreamAddress + offset;
        tuh_umount_cb(address);
        tuh_hid_umount_cb(address, 0);
        tuh_hid_umount_cb(address, 1);
    }

    CHECK_EQ(rig.registry.callback_overflow_count(), 0u);
    rig.registry.process_pending(0);
    CHECK_EQ(rig.registry.device_count(), 0u);
    CHECK_EQ(rig.registry.interface_count(), 0u);

    rig.device(kLastDownstreamAddress, 0xBEEF, 0x0002);
    tuh_mount_cb(kLastDownstreamAddress);
    rig.hid(kLastDownstreamAddress, 0, kProtocolKeyboard);
    rig.registry.process_pending(0);

    const auto* keyboard = rig.registry.owner(DeviceKind::Keyboard);
    CHECK(keyboard != nullptr);
    CHECK_EQ(keyboard->dev_addr, kLastDownstreamAddress);
    CHECK_EQ(keyboard->identity.vendor_id, 0xBEEFu);
    CHECK(keyboard->report_in_flight);
}

TEST_CASE(pending_fatal_report_blocks_duplicate_mount_from_rearming) {
    RegistryRig rig;
    rig.device(2, 0x1234, 0x5678);
    rig.hid(2, 0, kProtocolKeyboard);
    rig.registry.process_pending(0);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(2, 0), 1u);
    rig.drain_ready();

    rig.hid(2, 0, kProtocolKeyboard);
    std::array<std::uint8_t, 65> oversized{};
    tuh_hid_report_received_cb(2, 0, oversized.data(), oversized.size());
    rig.registry.process_pending(0);

    SourceEvent event;
    SourceIdentity identity;
    CHECK(rig.registry.take_event(event, identity));
    CHECK_EQ(event.kind, SourceEventKind::Fault);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(2, 0), 1u);
}

TEST_CASE(receive_arm_refusal_is_counted_once_without_in_flight_or_spin) {
    RegistryRig rig;
    duo::test::tinyusb_host::set_receive_result(false);
    rig.device(2, 0x1234, 0x5678);
    rig.hid(2, 0, kProtocolKeyboard);
    rig.registry.process_pending(0);

    const auto* keyboard = rig.registry.find(2, 0);
    CHECK(keyboard != nullptr);
    CHECK_FALSE(keyboard->report_in_flight);
    CHECK_EQ(rig.registry.arm_failure_count(), 1u);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(2, 0), 1u);

    rig.registry.process_pending(0);
    CHECK_EQ(rig.registry.arm_failure_count(), 1u);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(2, 0), 1u);
}

TEST_CASE(failed_host_initialization_is_observable_as_a_fault_event) {
    // Task 10 fix: a whole-host Fault has no interface of its own, so
    // delivering it with identity.kind left Unknown routed to neither
    // pipeline (PioUsbBackend::logical_port(Unknown) is -1) and released
    // nothing - harmless only as long as this could fire solely from
    // begin(), before any device could be mounted. It is now delivered as
    // two properly-identified Fault events, one per role slot, so both
    // pipelines actually see it.
    for (const auto results : {std::array<bool, 2>{false, true},
                               std::array<bool, 2>{true, false}}) {
        DeviceRegistry registry;
        registry.record_host_initialization(results[0], results[1]);

        SourceEvent event;
        SourceIdentity identity;
        CHECK(registry.take_event(event, identity));
        CHECK_EQ(event.kind, SourceEventKind::Fault);
        CHECK_EQ(identity.kind, DeviceKind::Keyboard);

        CHECK(registry.take_event(event, identity));
        CHECK_EQ(event.kind, SourceEventKind::Fault);
        CHECK_EQ(identity.kind, DeviceKind::Mouse);

        CHECK_FALSE(registry.take_event(event, identity));
    }
}

TEST_CASE(backend_reports_failed_host_initialization_without_servicing_a_dead_host) {
    for (const auto results : {std::array<bool, 2>{false, true},
                               std::array<bool, 2>{true, false}}) {
        duo::test::tinyusb_host::reset();
        duo::test::tinyusb_host::set_host_initialization_result(results[0], results[1]);
        PioUsbBackend backend;

        backend.begin();

        SourceEvent event;
        SourceIdentity identity;
        CHECK(backend.take_event(event, identity));
        CHECK_EQ(event.kind, SourceEventKind::Fault);
        CHECK_EQ(identity.kind, DeviceKind::Keyboard);
        CHECK(backend.take_event(event, identity));
        CHECK_EQ(event.kind, SourceEventKind::Fault);
        CHECK_EQ(identity.kind, DeviceKind::Mouse);
        CHECK_FALSE(backend.take_event(event, identity));
        CHECK(backend.clock_settled());
        CHECK_EQ(duo::test::tinyusb_host::system_clock_khz(), 0u);
        CHECK_EQ(duo::test::tinyusb_host::configured_pin_dp(), 0u);

        backend.task(100u);
        CHECK_EQ(duo::test::tinyusb_host::host_task_count(), 0u);
    }
}

// ------------------------------------------- what the host stack is doing

// The reading that was missing when the board enumerated nothing.
//
// begin() must sample tuh_rhport_is_active BEFORE it calls anything, because
// that flag is the only thing that distinguishes a
// host this backend started from one something else started first - after
// which tuh_configure and tuh_init are no-ops that still return true, which is
// exactly what they did.
TEST_CASE(begin_reports_a_host_that_was_already_active_before_core1_reached_it) {
    duo::test::tinyusb_host::reset();
    duo::test::tinyusb_host::set_system_clock_hz(120000000u);
    duo::test::tinyusb_host::set_host_already_active(true);
    duo::test::tinyusb_host::set_host_inited(true);
    PioUsbBackend backend;

    backend.begin();
    const auto observed = backend.observe();

    CHECK((observed.init_flags & duo_input::u1::pio_usb::kHostInitHostAlreadyActive) != 0);
    // And the other three still read "healthy", which is the whole problem:
    // this is what a silently no-op host bring-up looks like from above.
    CHECK((observed.init_flags & duo_input::u1::pio_usb::kHostInitConfigured) != 0);
    CHECK((observed.init_flags & duo_input::u1::pio_usb::kHostInitInitialized) != 0);
    CHECK((observed.init_flags & duo_input::u1::pio_usb::kHostInitInited) != 0);
    // The clock pair is already final on both cores. Bit 0 remains the
    // discriminator for a host somebody started before this backend.
    CHECK_EQ(observed.clk_hz_at_begin, 120000000u);
    CHECK_EQ(observed.clk_hz_now, 120000000u);
}

// The ordering the whole smoking gun rests on: the flag must be sampled BEFORE
// this backend brings the host up, not after.
//
// The fake's tuh_init sets the active flag the way usbh.c:415 does, so a sample
// moved to after the bring-up reads set here and fails this case. Sampling
// after would make bit 0 read 1 on a CORRECT image, which is worse than not
// reporting it at all: it sends an operator chasing a regression that is not
// there.
TEST_CASE(begin_reports_no_already_active_host_on_a_correct_image) {
    duo::test::tinyusb_host::reset();
    duo::test::tinyusb_host::set_system_clock_hz(120000000u);
    duo::test::tinyusb_host::set_host_already_active(false);
    duo::test::tinyusb_host::set_host_inited(true);
    PioUsbBackend backend;

    backend.begin();
    const auto observed = backend.observe();

    CHECK_EQ(observed.init_flags & duo_input::u1::pio_usb::kHostInitHostAlreadyActive, 0u);
    CHECK((observed.init_flags & duo_input::u1::pio_usb::kHostInitInited) != 0);
    // The bring-up really did happen, so "not already active" is a reading
    // taken before it rather than a reading of a backend that did nothing.
    CHECK(duo::test::tinyusb_host::host_already_active_now());
}

// Why clk_hz_now is the divider clock, guarded rather than asserted.
//
// Pico-PIO-USB computes every PIO divider from clock_get_hz(clk_sys) inside
// pio_usb_host_init and never recomputes one. main() has already selected the
// final clock before this fixture reaches begin(), so the begin and configure
// readings must agree.
TEST_CASE(the_host_stack_is_brought_up_on_the_clock_main_already_selected) {
    duo::test::tinyusb_host::reset();
    duo::test::tinyusb_host::set_system_clock_hz(120000000u);
    PioUsbBackend backend;

    backend.begin();

    CHECK_EQ(duo::test::tinyusb_host::clock_hz_at_configure(), 120000000u);
    CHECK_EQ(backend.observe().clk_hz_now, 120000000u);
    CHECK_EQ(backend.observe().clk_hz_at_begin, 120000000u);
}

TEST_CASE(a_host_that_never_initialized_reports_it_in_the_flags) {
    duo::test::tinyusb_host::reset();
    duo::test::tinyusb_host::set_host_initialization_result(true, false);
    duo::test::tinyusb_host::set_host_inited(false);
    PioUsbBackend backend;

    backend.begin();
    const auto observed = backend.observe();

    CHECK((observed.init_flags & duo_input::u1::pio_usb::kHostInitConfigured) != 0);
    CHECK_EQ(observed.init_flags & duo_input::u1::pio_usb::kHostInitInitialized, 0u);
    CHECK_EQ(observed.init_flags & duo_input::u1::pio_usb::kHostInitInited, 0u);
}

// Frozen across two reads twenty seconds apart is this project's established
// proof that Core 1 stopped, so the counter has to move on every pass -
// including the passes where the backend refuses to service a dead host, which
// is precisely when somebody is asking whether Core 1 is alive.
TEST_CASE(core1_passes_counts_every_pass_including_ones_a_dead_host_short_circuits) {
    duo::test::tinyusb_host::reset();
    duo::test::tinyusb_host::set_host_initialization_result(false, false);
    PioUsbBackend backend;
    backend.begin();

    CHECK_EQ(backend.observe().core1_passes, 0u);
    for (std::uint32_t pass = 1; pass <= 5; ++pass) {
        backend.task(pass * 1000u);
        CHECK_EQ(backend.observe().core1_passes, pass);
    }
    // The host really is not being serviced - the counter above is evidence
    // about the core, not about the host.
    CHECK_EQ(duo::test::tinyusb_host::host_task_count(), 0u);
}

// Whether U1 ever saw anything pull D+ up at all, which is a different
// question from whether any transaction on it succeeded. Edges, not levels: a
// device that attached and detached and attached again has been seen twice.
TEST_CASE(root_port_attach_edges_are_counted_and_its_flags_are_reported_live) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    backend.task(1000u);
    CHECK_EQ(backend.observe().root_port_connects, 0u);

    duo::test::tinyusb_host::set_root_port(true, true, false, true);
    backend.task(2000u);
    backend.task(3000u);
    CHECK_EQ(backend.observe().root_port_connects, 1u);
    CHECK_EQ(backend.observe().root_port_state,
             static_cast<std::uint8_t>(duo_input::u1::pio_usb::kRootPortInitialized |
                                       duo_input::u1::pio_usb::kRootPortConnected |
                                       duo_input::u1::pio_usb::kRootPortFullSpeed));

    duo::test::tinyusb_host::set_root_port(true, false, true, true);
    backend.task(4000u);
    CHECK_EQ(backend.observe().root_port_connects, 1u);
    CHECK_EQ(backend.observe().root_port_state,
             static_cast<std::uint8_t>(duo_input::u1::pio_usb::kRootPortInitialized |
                                       duo_input::u1::pio_usb::kRootPortSuspended |
                                       duo_input::u1::pio_usb::kRootPortFullSpeed));

    duo::test::tinyusb_host::set_root_port(true, true, false, true);
    backend.task(5000u);
    CHECK_EQ(backend.observe().root_port_connects, 2u);
}

// Raw root-port activity, below TinyUSB and below the registry. Sampled when
// the reply is built rather than cached by task(), so it still answers when
// Core 1 has stopped - a frozen core1_passes beside a climbing frame count is
// a dead core under a live host, and nothing else in this reply can say that.
TEST_CASE(the_sof_frame_count_is_read_live_rather_than_cached_by_a_pass) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    duo::test::tinyusb_host::set_sof_frame_count(1000u);
    CHECK_EQ(backend.observe().sof_frame_count, 1000u);
    duo::test::tinyusb_host::set_sof_frame_count(2000u);
    CHECK_EQ(backend.observe().sof_frame_count, 2000u);
    // No task() call in between: the count moved without a pass, which is the
    // case this field exists to be able to report.
    CHECK_EQ(backend.observe().core1_passes, 0u);
}

TEST_CASE(host_callbacks_count_device_mount_unmount_and_hid_mount_even_without_a_registry) {
    duo::test::tinyusb_host::reset();
    duo_input::u1::pio_usb::reset_host_callback_observability();
    duo_input::u1::pio_usb::set_callback_registry(nullptr);

    tuh_mount_cb(7);
    tuh_umount_cb(7);
    tuh_hid_mount_cb(7, 0, nullptr, 0);

    const auto observed = duo_input::u1::pio_usb::host_callback_observability();
    CHECK_EQ(observed.mount_events, 1u);
    CHECK_EQ(observed.umount_events, 1u);
    CHECK_EQ(observed.hid_mount_events, 1u);
}

TEST_CASE(backend_observation_surfaces_saturating_callback_counts) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();
    duo_input::u1::pio_usb::set_callback_registry(nullptr);

    for (std::uint32_t count = 0; count < 0x10001u; ++count) {
        tuh_mount_cb(7);
    }
    tuh_umount_cb(7);
    tuh_hid_mount_cb(7, 0, nullptr, 0);

    const auto observed = backend.observe();
    CHECK_EQ(observed.mount_events, 0xFFFFu);
    CHECK_EQ(observed.umount_events, 1u);
    CHECK_EQ(observed.hid_mount_events, 1u);
}

TEST_CASE(endpoint_pool_and_pass_timing_high_waters_preserve_the_worst_reading) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    duo::test::tinyusb_host::set_sof_frame_count(100u);
    backend.task(1000u);
    duo::test::tinyusb_host::set_endpoint(0, 8u, 3u);
    duo::test::tinyusb_host::set_endpoint(4, 64u, 1u);
    // tuh_task() can block during ordinary enumeration while the SOF ISR
    // keeps advancing. A 450 ms service pause therefore produces roughly 450
    // frames, not a tiny value and not evidence that SOF itself was starved.
    duo::test::tinyusb_host::set_sof_frame_count(550u);
    backend.task(451000u);

    auto observed = backend.observe();
    CHECK_EQ(observed.ep_slots_opened, 2u);
    CHECK_EQ(observed.ep_max_failed_count, 3u);
    CHECK_EQ(observed.max_pass_gap_us, 450000u);
    CHECK_EQ(observed.max_sof_gap, 450u);

    duo::test::tinyusb_host::set_endpoint(0, 0u, 0u);
    duo::test::tinyusb_host::set_endpoint(4, 0u, 0u);
    duo::test::tinyusb_host::set_sof_frame_count(551u);
    backend.task(452000u);
    observed = backend.observe();
    CHECK_EQ(observed.ep_slots_opened, 2u);
    CHECK_EQ(observed.ep_max_failed_count, 3u);
    CHECK_EQ(observed.max_pass_gap_us, 450000u);
    CHECK_EQ(observed.max_sof_gap, 450u);
}

TEST_CASE(endpoint_pool_is_sampled_on_core1_passes_then_published_to_core0) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    duo::test::tinyusb_host::set_endpoint(0, 8u, 3u);
    backend.task(1000u);
    CHECK_EQ(backend.observe().ep_max_failed_count, 3u);

    // Model TinyUSB mutating its pool after the pass. observe() must read the
    // published Core-1 high-water, not race this external mutable storage.
    duo::test::tinyusb_host::set_endpoint(0, 8u, 9u);
    CHECK_EQ(backend.observe().ep_max_failed_count, 3u);
    backend.task(2000u);
    CHECK_EQ(backend.observe().ep_max_failed_count, 9u);
}

TEST_CASE(root_port_resets_count_only_connected_suspended_cycles_and_saturate) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    duo::test::tinyusb_host::set_root_port(true, false, true, true);
    backend.task(1000u);
    duo::test::tinyusb_host::set_root_port(true, true, true, true);
    backend.task(2000u);
    duo::test::tinyusb_host::set_root_port(true, true, false, true);
    backend.task(3000u);
    CHECK_EQ(backend.observe().root_port_resets, 1u);

    backend.task(4000u);
    CHECK_EQ(backend.observe().root_port_resets, 1u);
    duo::test::tinyusb_host::set_root_port(true, true, true, true);
    backend.task(5000u);
    duo::test::tinyusb_host::set_root_port(true, true, false, true);
    backend.task(6000u);
    CHECK_EQ(backend.observe().root_port_resets, 2u);

    for (std::uint32_t count = 2u; count < 0xFFFFu; ++count) {
        duo::test::tinyusb_host::set_root_port(true, true, true, true);
        backend.task(6001u + count * 2u);
        duo::test::tinyusb_host::set_root_port(true, true, false, true);
        backend.task(6002u + count * 2u);
    }
    CHECK_EQ(backend.observe().root_port_resets, 0xFFFFu);
    duo::test::tinyusb_host::set_root_port(true, true, true, true);
    backend.task(200000u);
    duo::test::tinyusb_host::set_root_port(true, true, false, true);
    backend.task(200001u);
    CHECK_EQ(backend.observe().root_port_resets, 0xFFFFu);
}

TEST_CASE(sof_gap_uses_unsigned_wrap_arithmetic_and_clamps_to_the_wire_width) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    duo::test::tinyusb_host::set_sof_frame_count(0xFFFFFFF0u);
    backend.task(1000u);
    duo::test::tinyusb_host::set_sof_frame_count(20u);
    backend.task(2000u);
    CHECK_EQ(backend.observe().max_sof_gap, 36u);

    duo::test::tinyusb_host::set_sof_frame_count(70020u);
    backend.task(3000u);
    CHECK_EQ(backend.observe().max_sof_gap, 0xFFFFu);
}

TEST_CASE(hub_mount_edges_are_counted_even_though_tinyusb_suppresses_its_mount_callback) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    backend.task(1000u);
    duo::test::tinyusb_host::set_hub_mounted(true);
    backend.task(2000u);
    backend.task(3000u);
    CHECK_EQ(backend.observe().hub_mount_events, 1u);

    duo::test::tinyusb_host::set_hub_mounted(false);
    backend.task(4000u);
    duo::test::tinyusb_host::set_hub_mounted(true);
    backend.task(5000u);
    CHECK_EQ(backend.observe().hub_mount_events, 2u);

    for (std::uint32_t count = 2u; count < 0xFFFFu; ++count) {
        duo::test::tinyusb_host::set_hub_mounted(false);
        backend.task(5001u + count * 2u);
        duo::test::tinyusb_host::set_hub_mounted(true);
        backend.task(5002u + count * 2u);
    }
    CHECK_EQ(backend.observe().hub_mount_events, 0xFFFFu);
    duo::test::tinyusb_host::set_hub_mounted(false);
    backend.task(200000u);
    duo::test::tinyusb_host::set_hub_mounted(true);
    backend.task(200001u);
    CHECK_EQ(backend.observe().hub_mount_events, 0xFFFFu);
}

// ------------------------------- inside the five-statement enumeration window
//
// Everything above this line reports whether the host stack started and
// whether anything ever attached. The board this round was built for is past
// both: the hub configures, a downstream attach is accepted, and TinyUSB then
// stops inside process_enumeration without a retry, without a failed
// transaction and without a log line. The fields below are the readings that
// say WHICH of the ranked causes that is. None of them steers anything.

// F1. Whose endpoints are open, slot by slot.
//
// ep_slots_opened is a COUNT and a high-water mark: it cannot say whose
// endpoints they are, and "a configured hub occupies exactly two pool slots,
// so a third slot is a downstream device" was a source derivation and never a
// reading. This is the reading.
//
// One byte per pool slot 0-3, slot 0 in the low byte, and the open bit is what
// keeps a closed slot distinguishable: the endpoint this field exists to find
// is device address 0's control endpoint, whose address, direction and
// endpoint number are all zero. Without the open bit it would encode to 0x00
// and read as an empty slot - the exact reading it has to contradict.
TEST_CASE(ep_slot_map_names_the_device_and_endpoint_behind_each_of_the_first_four_slots) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    // A configured hub: its control endpoint, then its interrupt-IN status
    // pipe. Then the downstream device's address-0 control endpoint.
    duo::test::tinyusb_host::set_endpoint_identity(0, 8u, 5u, 0x00u);
    duo::test::tinyusb_host::set_endpoint_identity(1, 1u, 5u, 0x81u);
    duo::test::tinyusb_host::set_endpoint_identity(2, 8u, 0u, 0x00u);
    backend.task(1000u);

    const std::uint32_t map = backend.observe().ep_slot_map;
    CHECK_EQ(map & 0xFFu, 0xB0u);          // address 5, open, OUT, endpoint 0
    CHECK_EQ((map >> 8) & 0xFFu, 0xB9u);   // address 5, open, IN, endpoint 1
    // The whole point: an open slot for address 0 endpoint 0 is NOT zero.
    CHECK_EQ((map >> 16) & 0xFFu, 0x10u);
    CHECK_EQ((map >> 24) & 0xFFu, 0x00u);  // slot 3 closed
}

TEST_CASE(a_closed_endpoint_slot_reads_as_zero_and_the_map_is_live_not_a_high_water) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    duo::test::tinyusb_host::set_endpoint_identity(0, 8u, 5u, 0x00u);
    duo::test::tinyusb_host::set_endpoint_identity(1, 64u, 2u, 0x81u);
    backend.task(1000u);
    CHECK_EQ(backend.observe().ep_slot_map & 0xFFFFu, 0x59B0u);

    // The slot count above it is a high-water mark and stays at two; the map
    // is a live reading and follows the pool down. Both facts are needed: the
    // count says how far enumeration ever got, the map says where it is now.
    duo::test::tinyusb_host::set_endpoint_identity(1, 0u, 0u, 0x00u);
    backend.task(2000u);
    const auto observed = backend.observe();
    CHECK_EQ(observed.ep_slot_map & 0xFFFFu, 0x00B0u);
    CHECK_EQ(observed.ep_slots_opened, 2u);
}

// F2. Every event the host stack ever queued, counted where TinyUSB queues it.
//
// tuh_event_hook_cb is weak in usbh.c and queue_event() calls it for every
// event. Two attaches prove a downstream device was seen; one attach beside a
// configured hub says the hub never raised a port change. The completion count
// says how many control stages finished, which is how far along the chain the
// last successful transfer was.
TEST_CASE(host_event_counts_pack_attach_remove_and_transfer_completions) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    tuh_event_hook_cb(1, 0u, true);   // root attach, from the SOF ISR
    tuh_event_hook_cb(1, 2u, true);   // a control stage completed
    tuh_event_hook_cb(1, 2u, true);
    tuh_event_hook_cb(1, 0u, false);  // the downstream attach, from hub.c
    tuh_event_hook_cb(1, 1u, false);
    tuh_event_hook_cb(1, 3u, false);  // USBH_EVENT_FUNC_CALL: not an HCD event

    const std::uint32_t counts = backend.observe().host_event_counts;
    CHECK_EQ(counts & 0xFFu, 2u);
    CHECK_EQ((counts >> 8) & 0xFFu, 1u);
    CHECK_EQ((counts >> 16) & 0xFFFFu, 2u);
}

// The hook is called from BOTH contexts on Core 1 - hub.c queues its port
// events with in_isr false from inside tuh_task(), hcd_pio_usb.c queues
// attach, remove and completion with in_isr true from the SOF alarm - and
// RP2040 has no atomic read-modify-write. A single shared counter would lose
// an increment whenever the ISR landed inside the other context's
// load-modify-store, and losing one is the difference between "one attach" and
// "two attaches", which is the whole reading. Each context therefore owns its
// own word and observe() adds them.
TEST_CASE(host_event_counts_keep_a_separate_word_per_calling_context) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    for (std::uint32_t count = 0; count < 200u; ++count) {
        tuh_event_hook_cb(1, 0u, false);
    }
    for (std::uint32_t count = 0; count < 200u; ++count) {
        tuh_event_hook_cb(1, 0u, true);
    }

    // 400 attaches. One shared 0xFF-saturating word would have stopped at 255
    // too, so the reading that proves the split is the pair below it: each
    // context reached 200 on its own before the sum clamped.
    CHECK_EQ(backend.observe().host_event_counts & 0xFFu, 0xFFu);
    CHECK_EQ(duo_input::u1::pio_usb::host_callback_observability().attach_events_from_isr,
             200u);
    CHECK_EQ(duo_input::u1::pio_usb::host_callback_observability().attach_events_from_task,
             200u);
}

TEST_CASE(host_event_counts_saturate_rather_than_wrapping_back_through_zero) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    for (std::uint32_t count = 0; count < 0x10001u; ++count) {
        tuh_event_hook_cb(1, 2u, true);
    }
    for (std::uint32_t count = 0; count < 0x101u; ++count) {
        tuh_event_hook_cb(1, 1u, true);
    }

    const std::uint32_t counts = backend.observe().host_event_counts;
    CHECK_EQ((counts >> 16) & 0xFFFFu, 0xFFFFu);
    CHECK_EQ((counts >> 8) & 0xFFu, 0xFFu);
    CHECK_EQ(counts & 0xFFu, 0u);
}

// F3. How far each address got, through TinyUSB's public API only.
//
// Bit (a-1) is tuh_mounted(a) - SET_CONFIGURATION was ACKed. Bit 8+(a-1) is
// tuh_vid_pid_get(a), which is true only once the device is addressed AND its
// full device descriptor has been read. Never addressed and addressed-but-not
// configured are different failures, and this is what tells them apart.
TEST_CASE(enum_progress_mask_separates_a_configured_address_from_an_addressed_one) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    duo::test::tinyusb_host::set_hub_mounted(true);
    duo::test::tinyusb_host::add_hub(0x2109, 0x2817);
    duo::test::tinyusb_host::add_device(2, 0x046D, 0xC077);
    backend.task(1000u);

    const std::uint32_t mask = backend.observe().enum_progress_mask;
    CHECK_EQ(mask & (1u << 4), 1u << 4);        // address 5 configured
    CHECK_EQ(mask & (1u << 12), 1u << 12);      // address 5 descriptor read
    CHECK_EQ(mask & (1u << 1), 0u);             // address 2 never configured
    CHECK_EQ(mask & (1u << 9), 1u << 9);        // address 2 descriptor read
    CHECK_EQ(mask & (1u << 0), 0u);
    CHECK_EQ(mask & (1u << 8), 0u);
}

TEST_CASE(enum_progress_mask_is_sticky_so_a_transient_address_is_never_lost) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    duo::test::tinyusb_host::add_device(3, 0x1234, 0x5678);
    duo::test::tinyusb_host::set_device_mounted(3, true);
    backend.task(1000u);
    CHECK_EQ(backend.observe().enum_progress_mask & 0x0404u, 0x0404u);

    // TinyUSB forgets the device. The mask must not: an address that was
    // configured once is a fact about the run, and a reading taken a pass
    // later would have missed it.
    duo::test::tinyusb_host::forget_devices();
    backend.task(2000u);
    CHECK_EQ(backend.observe().enum_progress_mask & 0x0404u, 0x0404u);
}

// F4/F5. The blocking budget, measured instead of inferred.
//
// The round-3 diagnosis derived "one 500 ms root enumeration plus one 450 ms
// hub-branch debounce" from a pass rate assumed constant. These two fields
// measure it: (2, 950) is that pair, (4, 800) is a root enumeration plus three
// 100 ms retries, (1, 500) says enumeration never left the root port.
TEST_CASE(long_passes_count_only_gaps_over_twenty_milliseconds_and_sum_them) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    backend.task(0u);
    backend.task(20000u);           // exactly 20 ms: not over the threshold
    backend.task(40001u);           // 20.001 ms: over it
    backend.task(540709u);          // 500.708 ms
    backend.task(990209u);          // 449.5 ms

    const auto observed = backend.observe();
    CHECK_EQ(observed.long_pass_count, 3u);
    // 20 + 500 + 449 ms, each truncated to whole milliseconds.
    CHECK_EQ(observed.long_pass_total_ms, 20u + 500u + 449u);
    CHECK_EQ(observed.max_pass_gap_us, 500708u);
}

TEST_CASE(long_pass_totals_saturate_rather_than_wrapping) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    std::uint32_t now = 0;
    backend.task(now);
    // Each pass adds a little over four million milliseconds; 1024 of them
    // cannot fit in a u32 and must clamp rather than roll over to a small
    // number that reads as a healthy board.
    for (std::uint32_t pass = 0; pass < 1024u; ++pass) {
        now += 0xFFFFFFFFu;
        backend.task(now);
    }
    const auto observed = backend.observe();
    CHECK_EQ(observed.long_pass_count, 1024u);
    CHECK_EQ(observed.long_pass_total_ms, 0xFFFFFFFFu);
}

// F6. How close Core 1's 2 KB stack came to overflowing while TinyUSB's whole
// enumeration ran on it. Sampled inside the event hook because that is the
// deepest point in tuh_task()'s call chain this firmware can reach without
// patching TinyUSB.
TEST_CASE(core1_min_sp_keeps_the_deepest_stack_pointer_any_host_event_saw) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    // Zero is not a stack pointer. It is this field's "no host event has been
    // queued yet", and it must not read as an overflow.
    CHECK_EQ(backend.observe().core1_min_sp, 0u);

    duo::test::tinyusb_host::set_stack_pointer(0x20040F00u);
    tuh_event_hook_cb(1, 2u, true);
    CHECK_EQ(backend.observe().core1_min_sp, 0x20040F00u);

    duo::test::tinyusb_host::set_stack_pointer(0x20040A40u);
    tuh_event_hook_cb(1, 0u, false);
    CHECK_EQ(backend.observe().core1_min_sp, 0x20040A40u);

    // A later, shallower sample must not raise it: the minimum is the whole
    // reading, and a raised one would hide the overflow it exists to find.
    duo::test::tinyusb_host::set_stack_pointer(0x20040FF0u);
    tuh_event_hook_cb(1, 2u, true);
    CHECK_EQ(backend.observe().core1_min_sp, 0x20040A40u);
}

TEST_CASE(core1_min_sp_takes_the_deeper_of_the_two_calling_contexts) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    // Thread context goes deepest, then the ISR reports a shallower one. A
    // single shared word could lose the deeper sample to an interrupted
    // load-modify-store, and losing it reads as more headroom than existed.
    duo::test::tinyusb_host::set_stack_pointer(0x20040820u);
    tuh_event_hook_cb(1, 0u, false);
    duo::test::tinyusb_host::set_stack_pointer(0x20040EE0u);
    tuh_event_hook_cb(1, 2u, true);

    CHECK_EQ(backend.observe().core1_min_sp, 0x20040820u);
}

// F7. Whether a transfer is OUTSTANDING on each of the first four pool slots.
//
// ep_slot_map says WHOSE endpoint is in a slot; it cannot say whether anything
// was ever placed on the wire through it. Those are the two readings the round-5
// analysis has to tell apart at address 0: an endpoint that TinyUSB opened and
// then never sent a SETUP through looks identical, in every field shipped so
// far, to one carrying a control transfer that the device stopped answering.
// Pico-PIO-USB retries a NAK for ever without ever touching failed_count, so
// ep_max_failed_count stays 0 in both cases too.
TEST_CASE(ep_transfer_flags_say_whether_a_transfer_is_outstanding_in_each_slot) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    // A configured hub, both of whose endpoints are idle, and behind it the
    // address-0 control endpoint carrying the zero-length OUT status stage of
    // a control transfer that never completed.
    duo::test::tinyusb_host::set_endpoint_identity(0, 8u, 5u, 0x00u);
    duo::test::tinyusb_host::set_endpoint_identity(1, 1u, 5u, 0x81u);
    duo::test::tinyusb_host::set_endpoint_identity(2, 8u, 0u, 0x00u);

    duo::test::tinyusb_host::EndpointTransfer status_out;
    status_out.has_transfer = true;
    status_out.is_tx = true;
    status_out.data_id = 1u;  // data and status stages always carry DATA1
    status_out.need_pre = true;  // a low-speed device behind the hub
    duo::test::tinyusb_host::set_endpoint_transfer(2, status_out);
    backend.task(1000u);

    const std::uint32_t flags = backend.observe().ep_transfer_flags;
    CHECK_EQ(flags & 0xFFu, kEpXferOpen);
    CHECK_EQ((flags >> 8) & 0xFFu, kEpXferOpen);
    CHECK_EQ((flags >> 16) & 0xFFu,
             static_cast<std::uint8_t>(kEpXferOpen | kEpXferHasTransfer |
                                       kEpXferHostOut | kEpXferData1 |
                                       kEpXferNeedPre));
    CHECK_EQ((flags >> 24) & 0xFFu, 0u);  // slot 3 closed
}

// The other half of the same question, and the reading that says the SETUP was
// never placed on the wire: an open address-0 control endpoint with NO transfer
// on it. Without the open bit this byte would be zero and indistinguishable
// from a closed slot - the same trap ep_slot_map's open bit exists for.
TEST_CASE(an_open_endpoint_with_no_transfer_is_not_the_same_reading_as_a_closed_one) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    duo::test::tinyusb_host::set_endpoint_identity(2, 8u, 0u, 0x00u);
    backend.task(1000u);
    CHECK_EQ((backend.observe().ep_transfer_flags >> 16) & 0xFFu, kEpXferOpen);
    CHECK_EQ((backend.observe().ep_transfer_flags >> 24) & 0xFFu, 0u);
}

TEST_CASE(ep_transfer_flags_stage_a_setup_packet_distinctly_from_a_data_toggle) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    duo::test::tinyusb_host::set_endpoint_identity(0, 8u, 0u, 0x00u);
    duo::test::tinyusb_host::EndpointTransfer setup;
    setup.has_transfer = true;
    setup.is_tx = true;
    // pio_usb_host_send_setup stores the SETUP PID in data_id rather than a
    // toggle, so a staged SETUP must not read as DATA0.
    setup.data_id = USB_PID_SETUP;
    duo::test::tinyusb_host::set_endpoint_transfer(0, setup);
    backend.task(1000u);

    CHECK_EQ(backend.observe().ep_transfer_flags & 0xFFu,
             static_cast<std::uint8_t>(kEpXferOpen | kEpXferHasTransfer |
                                       kEpXferHostOut | kEpXferSetupStaged));
}

TEST_CASE(ep_transfer_flags_are_live_and_follow_a_transfer_that_completes) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    duo::test::tinyusb_host::set_endpoint_identity(0, 8u, 5u, 0x00u);
    duo::test::tinyusb_host::EndpointTransfer running;
    running.has_transfer = true;
    running.is_tx = true;
    running.data_id = 1u;
    duo::test::tinyusb_host::set_endpoint_transfer(0, running);
    backend.task(1000u);
    CHECK_EQ(backend.observe().ep_transfer_flags & 0xFFu,
             static_cast<std::uint8_t>(kEpXferOpen | kEpXferHasTransfer |
                                       kEpXferHostOut | kEpXferData1));

    // The transfer completes. Pico-PIO-USB clears has_transfer and LEAVES
    // is_tx and data_id holding the stage that just finished, so those two are
    // reported only while a transfer is outstanding - otherwise an idle
    // endpoint would read as one still carrying its last stage, which is the
    // exact confusion this field exists to remove.
    //
    // A high-water reading would say "a transfer was outstanding once", which
    // every healthy board would also say. Only a live one can report a WEDGE.
    running.has_transfer = false;
    duo::test::tinyusb_host::set_endpoint_transfer(0, running);
    backend.task(2000u);
    CHECK_EQ(backend.observe().ep_transfer_flags & 0xFFu, kEpXferOpen);
}

TEST_CASE(ep_transfer_flags_preserve_live_pre_stall_and_abort_state) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    duo::test::tinyusb_host::set_endpoint_identity(0, 8u, 0u, 0x00u);
    duo::test::tinyusb_host::EndpointTransfer state;
    state.need_pre = true;
    state.stalled = true;
    state.transfer_aborted = true;
    duo::test::tinyusb_host::set_endpoint_transfer(0, state);
    backend.task(1000u);

    CHECK_EQ(backend.observe().ep_transfer_flags & 0xFFu,
             static_cast<std::uint8_t>(kEpXferOpen | kEpXferNeedPre |
                                       kEpXferStalled | kEpXferAborted));
}

// F8. The transfer-completion total at the moment the host stack queued its
// most recent attach.
//
// Subtracting it from the completion count in host_event_counts gives how many
// control stages finished AFTER that attach - which is the one measurement that
// needs to know nothing about the hub in front of it. Every count before the
// attach, including the SET_FEATURE(PORT_POWER) per port, cancels out.
TEST_CASE(xfer_completions_at_attach_snapshot_the_total_when_an_attach_is_queued) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    // Nothing has attached yet, so there is nothing to subtract from.
    CHECK_EQ(backend.observe().xfer_completions_at_attach, 0u);

    tuh_event_hook_cb(1, 0u, true);   // the root attach: the hub
    for (int stage = 0; stage < 16; ++stage) {
        tuh_event_hook_cb(1, 2u, true);
    }
    CHECK_EQ(backend.observe().xfer_completions_at_attach, 0u);

    tuh_event_hook_cb(1, 0u, false);  // the downstream attach, queued by hub.c
    CHECK_EQ(backend.observe().xfer_completions_at_attach, 16u);

    // Five more completions carry the chain from that attach to the first
    // request the downstream device itself ever sees.
    for (int stage = 0; stage < 5; ++stage) {
        tuh_event_hook_cb(1, 2u, false);
    }
    const auto observed = backend.observe();
    CHECK_EQ((observed.host_event_counts >> 16) & 0xFFFFu, 21u);
    CHECK_EQ(observed.xfer_completions_at_attach, 16u);
}

TEST_CASE(xfer_completions_at_attach_count_both_contexts_and_follow_the_latest_attach) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    // One completion from each of Core 1's two contexts before the attach: the
    // snapshot has to be the sum, not one context's word, or it would read
    // low and make the enumeration look further along than it is.
    tuh_event_hook_cb(1, 2u, true);
    tuh_event_hook_cb(1, 2u, false);
    tuh_event_hook_cb(1, 0u, true);
    CHECK_EQ(backend.observe().xfer_completions_at_attach, 2u);

    tuh_event_hook_cb(1, 2u, true);
    tuh_event_hook_cb(1, 0u, false);
    CHECK_EQ(backend.observe().xfer_completions_at_attach, 3u);
}

// The round-6 recovery. A control transfer to address 0 that neither completes
// nor fails leaves TinyUSB's enumeration wedged for ever: _dev0.enumerating
// stays 1, the hub's status pipe is never re-armed, and not even unplugging the
// hub clears it, because a root-port disconnect carries hub_addr 0 and does not
// match _dev0 (usbh.c:981-993). Only a reboot recovers the board.
//
// The stack already contains the cure. tuh_task's attach branch treats an
// attach that matches the device it is already enumerating as a duplicate,
// aborts the outstanding control transfer and starts the enumeration over
// (usbh.c:498-508). Nothing on this board will ever raise that event, so the
// backend raises it - through the two entry points a host controller is
// published to call, and only after a transfer to address 0 has been
// outstanding long enough that no legitimate one could be.
TEST_CASE(a_control_transfer_to_address_zero_that_never_completes_is_restarted) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    // The wedge exactly as the board reported it: the hub's two endpoints
    // idle, and address 0's control endpoint holding the zero-length status
    // stage that never completes.
    duo::test::tinyusb_host::set_endpoint_identity(0, 8u, 5u, 0x00u);
    duo::test::tinyusb_host::set_endpoint_identity(1, 1u, 5u, 0x81u);
    duo::test::tinyusb_host::set_endpoint_identity(2, 8u, 0u, 0x00u);
    duo::test::tinyusb_host::EndpointTransfer status_out;
    status_out.has_transfer = true;
    status_out.is_tx = true;
    status_out.data_id = 1u;
    duo::test::tinyusb_host::set_endpoint_transfer(2, status_out);
    duo::test::tinyusb_host::set_device_zero_topology(1u, 5u, 3u);

    backend.task(0u);
    CHECK_EQ(duo::test::tinyusb_host::host_events().size(), std::size_t{0});
    CHECK_EQ(backend.observe().enum_stall_recoveries, 0u);

    // These are independently derived literal wall-clock readings, not the
    // firmware constant under test. A recovery one microsecond early would
    // tear down a healthy enumeration.
    backend.task(1'999'999u);
    CHECK_EQ(duo::test::tinyusb_host::host_events().size(), std::size_t{0});

    backend.task(2'000'000u);
    const auto& events = duo::test::tinyusb_host::host_events();
    CHECK_EQ(events.size(), std::size_t{1});
    CHECK_EQ(events[0].event_id, static_cast<std::uint8_t>(HCD_EVENT_DEVICE_ATTACH));
    // Addressed at the port the stack is already enumerating, which is the
    // only value tuh_task treats as a duplicate rather than deferring for ever.
    CHECK_EQ(events[0].rhport, 1u);
    CHECK_EQ(events[0].hub_addr, 5u);
    CHECK_EQ(events[0].hub_port, 3u);
    CHECK_FALSE(events[0].in_isr);
    CHECK_EQ(backend.observe().enum_stall_recoveries, 1u);
}

TEST_CASE(a_transfer_that_completes_inside_the_window_never_triggers_a_recovery) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    duo::test::tinyusb_host::set_endpoint_identity(2, 8u, 0u, 0x00u);
    duo::test::tinyusb_host::EndpointTransfer running;
    running.has_transfer = true;
    duo::test::tinyusb_host::set_endpoint_transfer(2, running);
    backend.task(1'000u);

    // The stage completes. The next one starts the clock again from scratch,
    // so a chain of ordinary stages never adds up to a false recovery.
    duo::test::tinyusb_host::set_endpoint_transfer(
        2, duo::test::tinyusb_host::EndpointTransfer{});
    backend.task(2'001'000u);
    duo::test::tinyusb_host::set_endpoint_transfer(2, running);
    backend.task(2'001'001u);
    backend.task(4'001'000u);

    CHECK_EQ(duo::test::tinyusb_host::host_events().size(), std::size_t{0});
    CHECK_EQ(backend.observe().enum_stall_recoveries, 0u);
}

TEST_CASE(an_outstanding_transfer_on_an_addressed_device_is_not_an_enumeration_stall) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    // Address 5's own endpoints. A hub interrupt pipe waits for a port change
    // indefinitely by design, and tearing an enumeration down over it would
    // break the one thing on this board that works.
    duo::test::tinyusb_host::set_endpoint_identity(0, 8u, 5u, 0x00u);
    duo::test::tinyusb_host::set_endpoint_identity(1, 1u, 5u, 0x81u);
    duo::test::tinyusb_host::EndpointTransfer armed;
    armed.has_transfer = true;
    duo::test::tinyusb_host::set_endpoint_transfer(1, armed);

    backend.task(1'000u);
    backend.task(8'001'000u);

    CHECK_EQ(duo::test::tinyusb_host::host_events().size(), std::size_t{0});
    CHECK_EQ(backend.observe().enum_stall_recoveries, 0u);
}

TEST_CASE(a_wedge_that_survives_its_own_recovery_is_retried_twice_then_left_quiet) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    duo::test::tinyusb_host::set_endpoint_identity(2, 8u, 0u, 0x00u);
    duo::test::tinyusb_host::EndpointTransfer status_out;
    status_out.has_transfer = true;
    status_out.is_tx = true;
    status_out.data_id = 1u;
    duo::test::tinyusb_host::set_endpoint_transfer(2, status_out);
    duo::test::tinyusb_host::set_device_zero_topology(1u, 5u, 3u);

    backend.task(0u);
    backend.task(2'000'000u);
    CHECK_EQ(duo::test::tinyusb_host::host_events().size(), std::size_t{1});

    // ONE PER WINDOW, NOT ONE PER PASS. These passes are all inside the
    // second window and must produce nothing.
    for (std::uint32_t pass = 1; pass <= 64u; ++pass) {
        backend.task(2'000'000u + pass);
    }
    CHECK_EQ(duo::test::tinyusb_host::host_events().size(), std::size_t{1});

    // A second full, literal 2 s observation produces the last bench retry.
    // At 3,999,999 us the back-off is still in effect; exactly at 4,000,000
    // us the second and final duplicate-attach event is queued.
    backend.task(3'999'999u);
    CHECK_EQ(duo::test::tinyusb_host::host_events().size(), std::size_t{1});
    backend.task(4'000'000u);
    CHECK_EQ(duo::test::tinyusb_host::host_events().size(), std::size_t{2});

    // A third 2 s window proves the cap is inclusive of exactly two queued
    // recovery events, not "two retries plus the original". Removing the cap
    // or changing its comparison to <= creates a third event here.
    backend.task(6'000'000u);
    CHECK_EQ(duo::test::tinyusb_host::host_events().size(), std::size_t{2});
    CHECK_EQ(backend.observe().enum_stall_recoveries, 2u);
}

TEST_CASE(a_mounted_downstream_child_suppresses_and_rearms_address_zero_recovery) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    duo::test::tinyusb_host::set_endpoint_identity(2, 8u, 0u, 0x00u);
    duo::test::tinyusb_host::EndpointTransfer status_out;
    status_out.has_transfer = true;
    status_out.is_tx = true;
    status_out.data_id = 1u;
    duo::test::tinyusb_host::set_endpoint_transfer(2, status_out);
    duo::test::tinyusb_host::set_device_zero_topology(1u, 5u, 3u);

    // Address 4 exercises the last possible downstream address, rather than
    // only the first child. A recovery must never sacrifice any working HID
    // device to restart a different address-0 enumeration.
    duo::test::tinyusb_host::set_device_mounted(4u, true);
    backend.task(0u);
    backend.task(2'000'000u);
    CHECK_EQ(duo::test::tinyusb_host::host_events().size(), std::size_t{0});
    CHECK_EQ(backend.observe().enum_stall_recoveries, 0u);

    // Once the child is gone, use a complete new observation window. A
    // recovery immediately at removal would still charge time during the
    // protected interval to the next enumeration.
    duo::test::tinyusb_host::set_device_mounted(4u, false);
    backend.task(3'999'999u);
    CHECK_EQ(duo::test::tinyusb_host::host_events().size(), std::size_t{0});
    backend.task(4'000'000u);
    CHECK_EQ(duo::test::tinyusb_host::host_events().size(), std::size_t{1});
    CHECK_EQ(backend.observe().enum_stall_recoveries, 1u);
}

TEST_CASE(a_root_port_topology_never_turns_an_address_zero_watchdog_into_a_reset) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    duo::test::tinyusb_host::set_endpoint_identity(2, 8u, 0u, 0x00u);
    duo::test::tinyusb_host::EndpointTransfer status_out;
    status_out.has_transfer = true;
    status_out.is_tx = true;
    status_out.data_id = 1u;
    duo::test::tinyusb_host::set_endpoint_transfer(2, status_out);

    // The backend cannot read TinyUSB's private _dev0.enumerating flag. Its
    // published HCD boundary does expose the topology it would put in the
    // synthetic attach. hub_addr == 0 is the only value that sends
    // enum_new_device() through its root-port reset branch, so it is rejected.
    duo::test::tinyusb_host::set_device_zero_topology(1u, 0u, 0u);
    backend.task(0u);
    backend.task(2'000'000u);
    CHECK_EQ(duo::test::tinyusb_host::host_events().size(), std::size_t{0});
    CHECK_EQ(backend.observe().enum_stall_recoveries, 0u);

    // A safe, hub-backed topology gets its own new 2 s window after the
    // rejected sample; it is not charged against the root-port interval.
    duo::test::tinyusb_host::set_device_zero_topology(1u, 5u, 3u);
    backend.task(3'999'999u);
    CHECK_EQ(duo::test::tinyusb_host::host_events().size(), std::size_t{0});
    backend.task(4'000'000u);
    CHECK_EQ(duo::test::tinyusb_host::host_events().size(), std::size_t{1});
}

TEST_CASE(the_watchdog_samples_the_pool_before_tuh_task_can_start_a_transfer) {
    duo::test::tinyusb_host::reset();
    PioUsbBackend backend;
    backend.begin();

    // The fake starts address-0 transfer only from inside the first service
    // call. Sampling before service must defer the watchdog's start to the
    // next pass. Moving tuh_task() above the pool scan starts it at 0 and
    // makes the 2,000,000 us pass queue a false recovery.
    duo::test::tinyusb_host::set_device_zero_topology(1u, 5u, 3u);
    duo::test::tinyusb_host::set_tuh_task_effect(
        duo::test::tinyusb_host::TuhTaskEffect::StartAddressZeroTransferOnce);
    backend.task(0u);
    backend.task(2'000'000u);

    CHECK_EQ(duo::test::tinyusb_host::host_events().size(), std::size_t{0});
    CHECK_EQ(backend.observe().enum_stall_recoveries, 0u);
}

TEST_CASE(mount_processing_stores_vid_pid_protocol_descriptor_hash_and_neutral_layout) {
    RegistryRig rig;
    rig.device(8, 0xABCD, 0x0123);
    rig.hid(8, 4, kProtocolKeyboard);
    rig.registry.process_pending(0);

    const auto* entry = rig.registry.find(8, 4);
    CHECK(entry != nullptr);
    CHECK_EQ(entry->identity.vendor_id, 0xABCDu);
    CHECK_EQ(entry->identity.product_id, 0x0123u);
    CHECK_EQ(entry->interface_protocol, kProtocolKeyboard);
    CHECK(entry->descriptor_present);
    CHECK(std::memcmp(entry->identity.descriptor_hash, kAbcSha256.data(),
                      kAbcSha256.size()) == 0);
    CHECK(entry->identity.keyboard_layout.key_kind !=
          duo_input::u1::input::hid::KeyboardFieldKind::None);
}

TEST_CASE(hid_mount_callback_only_copies_before_core1_classifies_a_neutral_mouse) {
    RegistryRig rig;
    const auto descriptor = neutral_mouse_descriptor();
    rig.device(2, 0xCAFE, 0x0001);
    rig.hid(2, 0, kProtocolNone, descriptor.data(),
            static_cast<std::uint16_t>(descriptor.size()));

    CHECK(rig.registry.find(2, 0) == nullptr);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(2, 0), 0u);

    rig.registry.process_pending(0);
    const auto* mouse = rig.registry.owner(DeviceKind::Mouse);
    CHECK(mouse != nullptr);
    if (mouse != nullptr) {
        CHECK_EQ(mouse->identity.mouse_layout.buttons.bits, std::uint8_t{5});
        CHECK_EQ(mouse->identity.mouse_layout.wheel.offset, std::uint8_t{3});
        CHECK_EQ(mouse->identity.mouse_layout.minimum_body_bytes, std::uint8_t{3});
    }
}

TEST_CASE(absent_descriptor_is_not_the_sha256_of_empty_and_keeps_boot_fallback_explicit) {
    RegistryRig rig;
    rig.device(2, 0xCAFE, 0x0002);
    rig.hid(2, 0, kProtocolMouse, nullptr, 0);
    rig.registry.process_pending(0);

    const auto* mouse = rig.registry.find(2, 0);
    CHECK(mouse != nullptr);
    CHECK_FALSE(mouse->descriptor_present);
    CHECK_EQ(mouse->identity.kind, DeviceKind::Mouse);
    CHECK_EQ(mouse->identity.mouse_layout.minimum_body_bytes, std::uint8_t{3});
    CHECK(std::memcmp(mouse->identity.descriptor_hash, std::array<std::uint8_t, 32>{}.data(),
                      sizeof(mouse->identity.descriptor_hash)) == 0);
}

TEST_CASE(prefix_equal_descriptor_lengths_keep_their_exact_registry_hashes) {
    constexpr std::uint8_t short_descriptor[] = {'a', 'b', 'c'};
    constexpr std::uint8_t long_descriptor[] = {'a', 'b', 'c', 'd'};
    constexpr std::array<std::uint8_t, 32> kAbcdSha256 = {
        0x88, 0xd4, 0x26, 0x6f, 0xd4, 0xe6, 0x33, 0x8d,
        0x13, 0xb8, 0x45, 0xfc, 0xf2, 0x89, 0x57, 0x9d,
        0x20, 0x9c, 0x89, 0x78, 0x23, 0xb9, 0x21, 0x7d,
        0xa3, 0xe1, 0x61, 0x93, 0x6f, 0x03, 0x15, 0x89,
    };
    RegistryRig rig;
    rig.device(2, 0xCAFE, 0x0003);
    rig.device(3, 0xCAFE, 0x0004);
    rig.hid(2, 0, kProtocolNone, short_descriptor, sizeof(short_descriptor));
    rig.hid(3, 0, kProtocolNone, long_descriptor, sizeof(long_descriptor));
    rig.registry.process_pending(0);

    const auto* short_entry = rig.registry.find(2, 0);
    const auto* long_entry = rig.registry.find(3, 0);
    CHECK(short_entry != nullptr);
    CHECK(long_entry != nullptr);
    CHECK(short_entry->descriptor_present);
    CHECK(long_entry->descriptor_present);
    CHECK(std::memcmp(short_entry->identity.descriptor_hash, kAbcSha256.data(),
                      kAbcSha256.size()) == 0);
    CHECK(std::memcmp(long_entry->identity.descriptor_hash, kAbcdSha256.data(),
                      kAbcdSha256.size()) == 0);
}

TEST_CASE(reconnect_clears_old_layout_vid_pid_hash_and_presence_before_reclassification) {
    RegistryRig rig;
    const auto descriptor = neutral_mouse_descriptor();
    rig.device(2, 0x1111, 0x2222);
    rig.hid(2, 0, kProtocolNone, descriptor.data(),
            static_cast<std::uint16_t>(descriptor.size()));
    rig.registry.process_pending(0);
    CHECK_EQ(rig.registry.find(2, 0)->identity.kind, DeviceKind::Mouse);
    CHECK(rig.registry.find(2, 0)->descriptor_present);

    tuh_umount_cb(2);
    rig.registry.process_pending(0);
    rig.device(2, 0xAAAA, 0xBBBB);
    rig.hid(2, 0, kProtocolKeyboard, nullptr, 0);
    rig.registry.process_pending(0);

    const auto* keyboard = rig.registry.find(2, 0);
    CHECK(keyboard != nullptr);
    if (keyboard != nullptr) {
        CHECK_EQ(keyboard->identity.kind, DeviceKind::Keyboard);
        CHECK_EQ(keyboard->identity.vendor_id, std::uint16_t{0xAAAA});
        CHECK_EQ(keyboard->identity.product_id, std::uint16_t{0xBBBB});
        CHECK_FALSE(keyboard->descriptor_present);
        CHECK_EQ(keyboard->identity.mouse_layout.minimum_body_bytes, std::uint8_t{0});
        CHECK(std::memcmp(keyboard->identity.descriptor_hash,
                          std::array<std::uint8_t, 32>{}.data(),
                          sizeof(keyboard->identity.descriptor_hash)) == 0);
    }
}
