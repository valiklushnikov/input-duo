#include "fakes/tinyusb_host.hpp"
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
        rig.registry.process_pending();

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
        rig.registry.process_pending();

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
    rig.registry.process_pending();

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
    rig.registry.process_pending();

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
    rig.registry.process_pending();
    CHECK_EQ(rig.registry.device_count(), 0u);
    CHECK_EQ(rig.registry.interface_count(), 0u);

    rig.device(kLastDownstreamAddress, 0xBEEF, 0x0002);
    tuh_mount_cb(kLastDownstreamAddress);
    rig.hid(kLastDownstreamAddress, 0, kProtocolKeyboard);
    rig.registry.process_pending();

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
    rig.registry.process_pending();
    CHECK_EQ(duo::test::tinyusb_host::receive_count(2, 0), 1u);

    rig.hid(2, 0, kProtocolKeyboard);
    std::array<std::uint8_t, 65> oversized{};
    tuh_hid_report_received_cb(2, 0, oversized.data(), oversized.size());
    rig.registry.process_pending();

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
    rig.registry.process_pending();

    const auto* keyboard = rig.registry.find(2, 0);
    CHECK(keyboard != nullptr);
    CHECK_FALSE(keyboard->report_in_flight);
    CHECK_EQ(rig.registry.arm_failure_count(), 1u);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(2, 0), 1u);

    rig.registry.process_pending();
    CHECK_EQ(rig.registry.arm_failure_count(), 1u);
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

TEST_CASE(hid_mount_callback_only_copies_before_core1_classifies_a_neutral_mouse) {
    RegistryRig rig;
    const auto descriptor = neutral_mouse_descriptor();
    rig.device(2, 0xCAFE, 0x0001);
    rig.hid(2, 0, kProtocolNone, descriptor.data(),
            static_cast<std::uint16_t>(descriptor.size()));

    CHECK(rig.registry.find(2, 0) == nullptr);
    CHECK_EQ(duo::test::tinyusb_host::receive_count(2, 0), 0u);

    rig.registry.process_pending();
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
    rig.registry.process_pending();

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
    rig.registry.process_pending();

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
    rig.registry.process_pending();
    CHECK_EQ(rig.registry.find(2, 0)->identity.kind, DeviceKind::Mouse);
    CHECK(rig.registry.find(2, 0)->descriptor_present);

    tuh_umount_cb(2);
    rig.registry.process_pending();
    rig.device(2, 0xAAAA, 0xBBBB);
    rig.hid(2, 0, kProtocolKeyboard, nullptr, 0);
    rig.registry.process_pending();

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
