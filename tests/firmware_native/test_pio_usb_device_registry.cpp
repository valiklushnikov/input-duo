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
    duo::test::tinyusb_host::set_sof_frame_count(105u);
    backend.task(451000u);

    auto observed = backend.observe();
    CHECK_EQ(observed.ep_slots_opened, 2u);
    CHECK_EQ(observed.ep_max_failed_count, 3u);
    CHECK_EQ(observed.max_pass_gap_us, 450000u);
    CHECK_EQ(observed.max_sof_gap, 5u);

    duo::test::tinyusb_host::set_endpoint(0, 0u, 0u);
    duo::test::tinyusb_host::set_endpoint(4, 0u, 0u);
    duo::test::tinyusb_host::set_sof_frame_count(106u);
    backend.task(452000u);
    observed = backend.observe();
    CHECK_EQ(observed.ep_slots_opened, 2u);
    CHECK_EQ(observed.ep_max_failed_count, 3u);
    CHECK_EQ(observed.max_pass_gap_us, 450000u);
    CHECK_EQ(observed.max_sof_gap, 5u);
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
