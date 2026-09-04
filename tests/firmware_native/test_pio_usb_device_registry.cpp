#include "fakes/tinyusb_host.hpp"
#include "pio_usb/backend.hpp"
#include "pio_usb/device_registry.hpp"
#include "test_support.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>

using duo::test::tinyusb_host::kProtocolKeyboard;
using duo::test::tinyusb_host::kProtocolMouse;
using duo::test::tinyusb_host::kProtocolNone;
using duo_input::u1::input::DeviceKind;
using duo_input::u1::input::SourceEvent;
using duo_input::u1::input::SourceEventKind;
using duo_input::u1::input::SourceIdentity;
using duo_input::u1::pio_usb::DeviceRegistry;
using duo_input::u1::pio_usb::LogicalRole;
using duo_input::u1::pio_usb::PioUsbBackend;

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

    void hid(std::uint8_t address, std::uint8_t instance, std::uint8_t protocol) {
        duo::test::tinyusb_host::set_protocol(address, instance, protocol);
        tuh_hid_mount_cb(address, instance, kDescriptor, sizeof(kDescriptor));
    }
};

}  // namespace

TEST_CASE(hub_first_and_device_first_mount_orders_keep_the_same_keyboard_owner) {
    {
        RegistryRig rig;
        rig.device(1, 0x2109, 0x2817);
        rig.device(4, 0x3434, 0xD030);
        tuh_mount_cb(1);
        tuh_mount_cb(4);
        rig.hid(4, 0, kProtocolKeyboard);
        rig.registry.process_pending();

        const auto* keyboard = rig.registry.owner(DeviceKind::Keyboard);
        CHECK(keyboard != nullptr);
        CHECK_EQ(keyboard->dev_addr, 4u);
        CHECK_EQ(keyboard->instance, 0u);
    }

    {
        RegistryRig rig;
        rig.device(1, 0x2109, 0x2817);
        rig.device(4, 0x3434, 0xD030);
        rig.hid(4, 0, kProtocolKeyboard);
        tuh_mount_cb(4);
        tuh_mount_cb(1);
        rig.registry.process_pending();

        const auto* keyboard = rig.registry.owner(DeviceKind::Keyboard);
        CHECK(keyboard != nullptr);
        CHECK_EQ(keyboard->dev_addr, 4u);
        CHECK_EQ(rig.registry.device_count(), 2u);
    }
}

TEST_CASE(keyboard_and_mouse_ownership_follows_protocol_not_hub_address_order) {
    RegistryRig rig;
    rig.device(2, 0x1111, 0x0001);
    rig.device(5, 0x2222, 0x0002);
    rig.hid(5, 0, kProtocolKeyboard);
    rig.hid(2, 0, kProtocolMouse);
    rig.registry.process_pending();

    CHECK_EQ(rig.registry.owner(DeviceKind::Keyboard)->dev_addr, 5u);
    CHECK_EQ(rig.registry.owner(DeviceKind::Mouse)->dev_addr, 2u);
    CHECK_EQ(rig.registry.owner(DeviceKind::Keyboard)->role, LogicalRole::Keyboard);
    CHECK_EQ(rig.registry.owner(DeviceKind::Mouse)->role, LogicalRole::Mouse);
}

TEST_CASE(composite_receiver_instances_remain_distinct_registry_entries) {
    RegistryRig rig;
    rig.device(3, 0x3434, 0xD030);
    rig.hid(3, 0, kProtocolMouse);
    rig.hid(3, 1, kProtocolNone);
    rig.registry.process_pending();

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
    rig.registry.process_pending();

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
    rig.registry.process_pending();
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
    rig.registry.process_pending();

    CHECK(DeviceRegistry::kDeviceCapacity >= 5u);
    CHECK(DeviceRegistry::kInterfaceCapacity >= 8u);
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
    rig.registry.process_pending();
    CHECK_EQ(rig.registry.interface_count(), 2u);

    tuh_umount_cb(6);
    rig.registry.process_pending();

    CHECK(rig.registry.find(6, 0) == nullptr);
    CHECK(rig.registry.find(6, 1) == nullptr);
    CHECK_EQ(rig.registry.interface_count(), 0u);
    CHECK_EQ(rig.registry.device_count(), 0u);
}

TEST_CASE(repeated_unmount_by_device_address_is_safe) {
    RegistryRig rig;
    rig.device(6, 0x3434, 0xD030);
    rig.hid(6, 0, kProtocolMouse);
    rig.registry.process_pending();

    tuh_umount_cb(6);
    tuh_hid_umount_cb(6, 0);
    rig.registry.process_pending();

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
    rig.registry.process_pending();

    CHECK_EQ(rig.registry.owner(DeviceKind::Keyboard)->dev_addr, 2u);
    CHECK_EQ(rig.registry.find(3, 0)->role, LogicalRole::Ignored);
    CHECK_EQ(rig.registry.ignored_interface_count(), 1u);
}

TEST_CASE(accepted_report_record_is_processed_before_exactly_one_rearm) {
    RegistryRig rig;
    rig.device(2, 0x1234, 0x5678);
    rig.hid(2, 0, kProtocolKeyboard);
    rig.registry.process_pending();
    CHECK_EQ(duo::test::tinyusb_host::receive_count(2, 0), 1u);

    const std::uint8_t report[] = {0, 0, 4, 0, 0, 0, 0, 0};
    tuh_hid_report_received_cb(2, 0, report, sizeof(report));
    CHECK_FALSE(rig.registry.find(2, 0)->report_in_flight);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(2, 0), 1u);

    rig.registry.process_pending();
    CHECK(rig.registry.find(2, 0)->report_in_flight);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(2, 0), 2u);
    rig.registry.process_pending();
    CHECK_EQ(duo::test::tinyusb_host::receive_count(2, 0), 2u);
}

TEST_CASE(oversized_report_becomes_fault_without_truncation_or_rearm) {
    RegistryRig rig;
    rig.device(2, 0x1234, 0x5678);
    rig.hid(2, 0, kProtocolKeyboard);
    rig.registry.process_pending();

    std::array<std::uint8_t, 65> report{};
    tuh_hid_report_received_cb(2, 0, report.data(), report.size());
    rig.registry.process_pending();

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
    rig.registry.process_pending();

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

    rig.registry.process_pending();
    CHECK_EQ(duo::test::tinyusb_host::receive_count(2, 0), 1u);
}

TEST_CASE(failed_host_initialization_is_observable_as_a_fault_event) {
    for (const auto results : {std::array<bool, 2>{false, true},
                               std::array<bool, 2>{true, false}}) {
        DeviceRegistry registry;
        registry.record_host_initialization(results[0], results[1]);

        SourceEvent event;
        SourceIdentity identity;
        CHECK(registry.take_event(event, identity));
        CHECK_EQ(event.kind, SourceEventKind::Fault);
        CHECK_EQ(identity.kind, DeviceKind::Unknown);
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
        CHECK_EQ(identity.kind, DeviceKind::Unknown);
        CHECK(backend.clock_settled());
        CHECK_EQ(duo::test::tinyusb_host::system_clock_khz(), 120000u);
        CHECK_EQ(duo::test::tinyusb_host::configured_pin_dp(), 0u);

        backend.task(100u);
        CHECK_EQ(duo::test::tinyusb_host::host_task_count(), 0u);
    }
}

TEST_CASE(mount_processing_stores_vid_pid_protocol_descriptor_hash_and_neutral_layout) {
    RegistryRig rig;
    rig.device(8, 0xABCD, 0x0123);
    rig.hid(8, 4, kProtocolKeyboard);
    rig.registry.process_pending();

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
