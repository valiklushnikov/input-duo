// Every HID interface owns a source slot and its normalizer state.

#include "input/source_table.hpp"
#include "fakes/multi_report_hid.hpp"
#include "input/product_names.hpp"
#include "pio_usb/hid_setup.hpp"
#include "test_support.hpp"

#include <type_traits>
#include <vector>

using duo_input::u1::input::DeviceKind;
using duo_input::u1::input::IInputHandler;
using duo_input::u1::input::InputEvent;
using duo_input::u1::input::InputEventKind;
using duo_input::u1::input::SourceEvent;
using duo_input::u1::input::SourceEventKind;
using duo_input::u1::input::SourceIdentity;
using duo_input::u1::input::SourceTable;
using duo_input::u1::input::hid::boot_keyboard_layout;
using duo_input::u1::input::hid::boot_mouse_layout;
using duo_input::u1::input::hid::ReportDescriptorError;
using duo_input::u1::input::hid::ReportRole;

static_assert(!std::is_copy_constructible<SourceTable>::value,
              "SourceTable handlers must not be copied away from their slots");
static_assert(!std::is_copy_assignable<SourceTable>::value,
              "SourceTable handlers must not be copied away from their slots");
static_assert(!std::is_move_constructible<SourceTable>::value,
              "SourceTable handlers must not move away from their slots");
static_assert(!std::is_move_assignable<SourceTable>::value,
              "SourceTable handlers must not move away from their slots");

namespace {

struct RecordingHandler final : IInputHandler {
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
};

SourceEvent ready_event(std::uint8_t source_id) {
    SourceEvent event;
    event.kind = SourceEventKind::Ready;
    event.source_id = source_id;
    return event;
}

SourceEvent report_event(std::uint8_t source_id, const std::uint8_t* bytes, std::size_t size) {
    SourceEvent event;
    event.kind = SourceEventKind::Report;
    event.source_id = source_id;
    for (std::size_t index = 0; index < size; ++index) {
        event.report[index] = bytes[index];
    }
    event.report_size = size;
    return event;
}

SourceEvent auxiliary_report_event(std::uint8_t source_id, const std::uint8_t* bytes,
                                   std::size_t size) {
    SourceEvent event = report_event(source_id, bytes, size);
    event.kind = SourceEventKind::AuxiliaryReport;
    return event;
}

SourceEvent detached_event(std::uint8_t source_id) {
    SourceEvent event;
    event.kind = SourceEventKind::Detached;
    event.source_id = source_id;
    return event;
}

SourceEvent fault_event(std::uint8_t source_id) {
    SourceEvent event;
    event.kind = SourceEventKind::Fault;
    event.source_id = source_id;
    return event;
}

SourceIdentity mouse_identity(std::uint8_t interface_number) {
    SourceIdentity identity{};
    identity.kind = DeviceKind::Mouse;
    identity.vendor_id = 0x1234;
    identity.product_id = 0x5678;
    identity.interface_number = interface_number;
    identity.mouse_layout = boot_mouse_layout();
    duo::test::multi_report_hid::set_single_report(identity);
    return identity;
}

}  // namespace

// Derive the public lookup value so this test runs against both the old full
// configuration copy and the small stable identity. A report set must never
// ride the synchronous binding/capture stack merely to match a source.
template <typename T>
T resolved_value_type(bool (SourceTable::*)(std::uint8_t, T&) const);
using ResolvedSource = decltype(resolved_value_type(&SourceTable::resolve));

TEST_CASE(source_lookup_has_a_small_owned_value_and_table_does_not_duplicate_report_sets) {
    CHECK(sizeof(ResolvedSource) <= 8u);
    CHECK(std::is_trivially_copyable<ResolvedSource>::value);
    CHECK(sizeof(SourceTable) <= 9000u);
}

TEST_CASE(inventory_copies_bounded_report_set_decisions_without_persisting_them_in_source_keys) {
    RecordingHandler handler;
    SourceTable table(handler);
    auto identity = duo::test::multi_report_hid::identity();
    identity.report_set.rejected_count = 2;
    identity.report_set.rejected[0] = {
        ReportRole::Mouse, 7, ReportDescriptorError::UnsupportedLayout};
    identity.report_set.rejected[1] = {
        ReportRole::Keyboard, 9, ReportDescriptorError::AmbiguousKeyboardReport};
    identity.report_set.rejected_overflow = 3;

    table.on_event(ready_event(42), identity, 0);
    duo_input::u1::input::SourceInventory inventory{};
    table.inventory(inventory);

    CHECK_EQ(inventory.count, 1u);
    const auto& source = inventory.sources[0];
    CHECK_EQ(source.accepted_count, 3u);
    CHECK_EQ(source.accepted[0].role, static_cast<std::uint8_t>(ReportRole::Keyboard));
    CHECK_EQ(source.accepted[0].report_id, 1u);
    CHECK_EQ(source.accepted[0].minimum_body_bytes, 8u);
    CHECK_EQ(source.accepted[1].role, static_cast<std::uint8_t>(ReportRole::Consumer));
    CHECK_EQ(source.accepted[1].report_id, 2u);
    CHECK_EQ(source.accepted[1].minimum_body_bytes, 2u);
    CHECK_EQ(source.accepted[2].role, static_cast<std::uint8_t>(ReportRole::Keyboard));
    CHECK_EQ(source.accepted[2].report_id, 12u);
    CHECK_EQ(source.accepted[2].minimum_body_bytes, 20u);
    CHECK_EQ(source.rejected_count, 2u);
    CHECK_EQ(source.rejected[0].role, static_cast<std::uint8_t>(ReportRole::Mouse));
    CHECK_EQ(source.rejected[0].report_id, 7u);
    CHECK_EQ(source.rejected[0].reason,
             static_cast<std::uint8_t>(ReportDescriptorError::UnsupportedLayout));
    CHECK_EQ(source.rejected_overflow, 3u);
    CHECK(sizeof(ResolvedSource) <= 8u);
}

TEST_CASE(resolved_source_survives_slot_reuse_and_configuration_buffer_reuse) {
    RecordingHandler handler;
    SourceTable table(handler);
    auto configuration = duo::test::multi_report_hid::identity();
    table.on_event(ready_event(42), configuration, 0);
    ResolvedSource saved{};
    CHECK(table.resolve(0, saved));
    configuration = mouse_identity(7); // backend overwrites its event scratch
    table.on_event(detached_event(42), configuration, 1);
    CHECK_FALSE(table.resolve(0, saved));
    CHECK_FALSE(table.resolve(255, saved));
    table.on_event(ready_event(5), configuration, 2);
    ResolvedSource current{};
    CHECK(table.resolve(0, current));
    CHECK_EQ(saved.vendor_id, 0x3434);
    CHECK_EQ(saved.product_id, 0xD030);
    CHECK_EQ(saved.interface_number, 2);
    CHECK_EQ(current.vendor_id, 0x1234);
    CHECK_EQ(current.product_id, 0x5678);
    CHECK_EQ(current.interface_number, 7);
}

TEST_CASE(a_composite_device_gets_one_slot_per_interface) {
    RecordingHandler handler;
    SourceTable table(handler);

    const SourceIdentity mouse = mouse_identity(0);

    SourceIdentity keyboard{};
    keyboard.kind = DeviceKind::Keyboard;
    keyboard.vendor_id = 0x1234;
    keyboard.product_id = 0x5678;
    keyboard.interface_number = 1;
    keyboard.keyboard_layout = boot_keyboard_layout();
    duo::test::multi_report_hid::set_single_report(keyboard);

    const SourceIdentity second_mouse = mouse_identity(2);

    table.on_event(ready_event(0), mouse, 0);
    table.on_event(ready_event(1), keyboard, 0);
    table.on_event(ready_event(2), second_mouse, 0);

    const std::uint8_t click[3] = {0x01, 0x00, 0x00};
    table.on_event(report_event(0, click, sizeof(click)), mouse, 1);

    const std::uint8_t shortcut[8] = {0x00, 0x00, 0x4F, 0, 0, 0, 0, 0};
    table.on_event(report_event(1, shortcut, sizeof(shortcut)), keyboard, 2);

    // The third HID interface is serviced as itself even when its report was
    // collected through the backend's auxiliary channel.
    const std::uint8_t right_click[3] = {0x02, 0x00, 0x00};
    table.on_event(auxiliary_report_event(2, right_click, sizeof(right_click)), second_mouse, 3);

    CHECK_EQ(handler.events.size(), 3u);
    if (handler.events.size() == 3) {
        CHECK(handler.events[0].kind == InputEventKind::MouseButtonDown);
        CHECK(handler.events[0].code == 0);
        CHECK(handler.events[0].source_index == 0);
        CHECK(handler.events[1].kind == InputEventKind::KeyDown);
        CHECK(handler.events[1].code == 0x4F);
        CHECK(handler.events[1].source_index == 1);
        CHECK(handler.events[2].kind == InputEventKind::MouseButtonDown);
        CHECK(handler.events[2].code == 1);
        CHECK(handler.events[2].source_index == 2);
    }

    ResolvedSource resolved{};
    if (table.resolve(1, resolved)) {
        CHECK(resolved.interface_number == 1);
        CHECK(resolved.vendor_id == 0x1234);
        CHECK(resolved.product_id == 0x5678);
    } else {
        CHECK(false);
    }
}

TEST_CASE(consumer_descriptors_decode_array_and_bitmap_sources_and_release_on_detach) {
    const std::uint8_t array[] = {0x05,0x0C,0x09,0x01,0xA1,0x01,0x85,0x02,0x15,0x00,0x26,0xFF,0x03,
        0x19,0x00,0x2A,0xFF,0x03,0x75,0x10,0x95,0x01,0x81,0x00,0xC0};
    const std::uint8_t bitmap[] = {0x05,0x0C,0x09,0x01,0xA1,0x01,0x15,0x00,0x25,0x01,
        0x09,0xE9,0x09,0xEA,0x09,0xCD,0x75,0x01,0x95,0x03,0x81,0x02,0xC0};
    RecordingHandler handler; SourceTable table(handler);
    SourceIdentity a, b;
    CHECK(duo_input::u1::pio_usb::classify_hid_layout(0, array, sizeof(array), a) !=
          duo_input::u1::pio_usb::HidLayoutSource::None);
    CHECK(duo_input::u1::pio_usb::classify_hid_layout(0, bitmap, sizeof(bitmap), b) !=
          duo_input::u1::pio_usb::HidLayoutSource::None);
    table.on_event(ready_event(1), a, 0); table.on_event(ready_event(2), b, 0);
    const std::uint8_t down_a[] = {2, 0xB1, 1}; const std::uint8_t down_b[] = {5};
    table.on_event(report_event(1, down_a, sizeof(down_a)), a, 1);
    table.on_event(report_event(2, down_b, sizeof(down_b)), b, 1);
    CHECK(handler.events.size() == 3);
    if (handler.events.size() == 3) {
        CHECK(handler.events[0].kind == InputEventKind::ConsumerDown); CHECK(handler.events[0].code == 0x1B1);
        CHECK(handler.events[1].code == 0xE9); CHECK(handler.events[2].code == 0xCD);
        CHECK(handler.events[0].source_index == 0); CHECK(handler.events[2].source_index == 1);
    }
    table.on_event(detached_event(1), a, 2);
    CHECK(handler.count(InputEventKind::ConsumerUp) == 1);
}

TEST_CASE(detaching_a_source_releases_held_input_and_frees_its_slot) {
    RecordingHandler handler;
    SourceTable table(handler);
    const SourceIdentity mouse = mouse_identity(0);
    table.on_event(ready_event(0), mouse, 0);

    const std::uint8_t held[3] = {0x01, 0x00, 0x00};
    table.on_event(report_event(0, held, sizeof(held)), mouse, 1);
    table.on_event(detached_event(0), mouse, 2);

    CHECK_EQ(handler.events.size(), 2u);
    if (handler.events.size() == 2) {
        CHECK(handler.events[0].kind == InputEventKind::MouseButtonDown);
        CHECK(handler.events[0].source_index == 0);
        CHECK(handler.events[1].kind == InputEventKind::MouseButtonUp);
        CHECK(handler.events[1].source_index == 0);
    }

    ResolvedSource resolved{};
    CHECK_FALSE(table.resolve(0, resolved));

    const SourceIdentity replacement = mouse_identity(2);
    table.on_event(ready_event(2), replacement, 3);
    CHECK(table.resolve(0, resolved));
    if (table.resolve(0, resolved)) {
        CHECK(resolved.interface_number == 2);
    }
}

TEST_CASE(a_source_fault_releases_held_input_and_frees_its_slot) {
    RecordingHandler handler;
    SourceTable table(handler);

    SourceIdentity keyboard{};
    keyboard.kind = DeviceKind::Keyboard;
    keyboard.interface_number = 1;
    keyboard.keyboard_layout = boot_keyboard_layout();
    duo::test::multi_report_hid::set_single_report(keyboard);
    table.on_event(ready_event(1), keyboard, 0);

    const std::uint8_t held[8] = {0x00, 0x00, 0x4F, 0, 0, 0, 0, 0};
    table.on_event(report_event(1, held, sizeof(held)), keyboard, 1);
    table.on_event(fault_event(1), keyboard, 2);

    CHECK_EQ(handler.events.size(), 2u);
    if (handler.events.size() == 2) {
        CHECK(handler.events[0].kind == InputEventKind::KeyDown);
        CHECK(handler.events[0].code == 0x4F);
        CHECK(handler.events[0].source_index == 0);
        CHECK(handler.events[1].kind == InputEventKind::KeyUp);
        CHECK(handler.events[1].code == 0x4F);
        CHECK(handler.events[1].source_index == 0);
    }

    ResolvedSource resolved{};
    CHECK_FALSE(table.resolve(0, resolved));
}

TEST_CASE(mouse_interfaces_do_not_share_normalizer_state) {
    RecordingHandler handler;
    SourceTable table(handler);
    const SourceIdentity first = mouse_identity(0);
    const SourceIdentity second = mouse_identity(1);
    table.on_event(ready_event(0), first, 0);
    table.on_event(ready_event(1), second, 0);

    const std::uint8_t held[3] = {0x01, 0x00, 0x00};
    const std::uint8_t none[3] = {0x00, 0x00, 0x00};
    table.on_event(report_event(0, held, sizeof(held)), first, 1);
    table.on_event(report_event(1, none, sizeof(none)), second, 2);

    CHECK(handler.count(InputEventKind::MouseButtonDown) == 1);
    CHECK(handler.count(InputEventKind::MouseButtonUp) == 0);
}

TEST_CASE(a_full_source_table_refuses_the_ninth_interface_without_displacing_a_slot) {
    RecordingHandler handler;
    SourceTable table(handler);

    for (std::uint8_t source_id = 0; source_id < SourceTable::kMaxSources; ++source_id) {
        table.on_event(ready_event(source_id), mouse_identity(source_id), 0);
    }
    table.on_event(ready_event(SourceTable::kMaxSources),
                   mouse_identity(SourceTable::kMaxSources), 0);

    const std::uint8_t left_click[3] = {0x01, 0x00, 0x00};
    const std::uint8_t right_click[3] = {0x02, 0x00, 0x00};
    table.on_event(report_event(0, left_click, sizeof(left_click)), mouse_identity(0), 1);
    table.on_event(report_event(SourceTable::kMaxSources, right_click, sizeof(right_click)),
                   mouse_identity(SourceTable::kMaxSources), 2);

    CHECK(table.unclaimed_interfaces() == 1);
    duo_input::u1::input::SourceInventory inventory{};
    table.inventory(inventory, 2);
    CHECK_EQ(inventory.count, 8);
    CHECK_EQ(inventory.rejected_interfaces, 3u);
    CHECK_EQ(handler.events.size(), 1u);
    if (handler.events.size() == 1) {
        CHECK(handler.events[0].source_index == 0);
        CHECK(handler.events[0].code == 0);
    }

    for (std::uint8_t slot = 0; slot < SourceTable::kMaxSources; ++slot) {
        ResolvedSource resolved{};
        CHECK(table.resolve(slot, resolved));
        if (table.resolve(slot, resolved)) {
            CHECK(resolved.interface_number == slot);
        }
    }
}

TEST_CASE(whole_host_fault_releases_every_source_including_additional_interfaces) {
    RecordingHandler handler;
    SourceTable table(handler);
    const std::uint8_t held[3] = {1, 0, 0};
    for (std::uint8_t i = 0; i < 3; ++i) {
        table.on_event(ready_event(i), mouse_identity(i), 0);
        table.on_event(report_event(i, held, sizeof(held)), mouse_identity(i), 1);
    }
    SourceEvent fault{}; fault.kind = SourceEventKind::Fault; fault.source_id = 0xFF;
    table.on_event(fault, {}, 2);
    CHECK_EQ(handler.count(InputEventKind::MouseButtonUp), 3);
    duo_input::u1::input::SourceInventory inventory{};
    table.inventory(inventory);
    CHECK_EQ(inventory.count, 0);
}

TEST_CASE(optional_usb_product_name_is_carried_to_every_sibling_and_stale_completion_is_discarded) {
    RecordingHandler handler; SourceTable table(handler);
    auto identity = mouse_identity(0); identity.device_address = 2;
    table.on_event(ready_event(0), identity, 0);
    identity.interface_number = 1;
    table.on_event(ready_event(1), identity, 0);
    duo_input::u1::input::ProductNames names;
    bool offered = false;
    names.poll(table, [&](std::uint8_t address, std::uint8_t* buffer, std::size_t capacity) {
        offered = true; CHECK_EQ(address, 2); CHECK(capacity >= 6);
        buffer[0] = 6; buffer[1] = 3; buffer[2] = 'M'; buffer[3] = 0; buffer[4] = '3'; buffer[5] = 0;
        return true;
    });
    CHECK(offered);
    names.complete(true, 6);
    duo_input::u1::input::SourceInventory inventory{}; table.inventory(inventory);
    CHECK_EQ(inventory.sources[0].product_name[0], 'M');
    CHECK_EQ(inventory.sources[1].product_name[1], '3');
    SourceEvent fault{}; fault.kind = SourceEventKind::Fault; fault.source_id = 0xFF;
    table.on_event(fault, {}, 1);
    table.on_event(ready_event(0), identity, 2);
    names.poll(table, [&](std::uint8_t, std::uint8_t* buffer, std::size_t) {
        buffer[0] = 4; buffer[1] = 3; buffer[2] = 'X'; buffer[3] = 0; return true;
    });
    table.on_event(fault, {}, 3);
    table.on_event(ready_event(0), identity, 4);
    names.complete(true, 4);
    table.inventory(inventory);
    CHECK_EQ(inventory.sources[0].product_name[0], 0);
}

TEST_CASE(every_keychron_report_keeps_one_source_index_and_unknown_ids_are_counted) {
    using namespace duo::test::multi_report_hid;
    RecordingHandler handler;
    SourceTable table(handler);
    table.on_event(ready_event(7), mouse_identity(0), 0);
    const auto source = identity();
    table.on_event(ready_event(42), source, 0);
    table.on_event(report_event(42, kSidePress, sizeof(kSidePress)), source, 1);
    table.on_event(auxiliary_report_event(42, kConsumerPress, sizeof(kConsumerPress)), source, 2);
    table.on_event(report_event(42, kBitmapPress, sizeof(kBitmapPress)), source, 3);
    CHECK_EQ(handler.events.size(), 5u);
    const std::uint8_t unknown[21] = {0x7F, 1, 0, 0x4F};
    table.on_event(auxiliary_report_event(42, unknown, sizeof(unknown)), source, 4);
    CHECK_EQ(handler.events.size(), 5u);
    duo_input::u1::input::SourceInventory inventory;
    table.inventory(inventory);
    CHECK_EQ(inventory.count, 2);
    CHECK_EQ(inventory.sources[1].reports, 4u);
    CHECK_EQ(inventory.sources[1].last_report[0], 0x7F);
    table.on_event(report_event(42, kSideRelease, sizeof(kSideRelease)), source, 5);
    CHECK_EQ(handler.events.size(), 7u);
    if (handler.events.size() == 7) {
        CHECK_EQ(handler.events[0].code, 0xE0);
        CHECK_EQ(handler.events[1].code, 0x4F);
        CHECK_EQ(handler.events[5].kind, InputEventKind::KeyUp);
        CHECK_EQ(handler.events[5].code, 0x4F);
        CHECK_EQ(handler.events[6].kind, InputEventKind::KeyUp);
        CHECK_EQ(handler.events[6].code, 0xE0);
    }
    table.on_event(detached_event(42), source, 6);
    CHECK_EQ(handler.events.size(), 10u);
    for (const auto& event : handler.events) {
        CHECK_EQ(event.source_index, 1);
        CHECK(event.code != 0x03);
    }
    table.on_event(ready_event(42), source, 7);
    ResolvedSource resolved;
    CHECK(table.resolve(1, resolved));
    CHECK_EQ(resolved.vendor_id, 0x3434);
    CHECK_EQ(resolved.product_id, 0xD030);
    CHECK_EQ(resolved.interface_number, 2);
}

TEST_CASE(ready_with_no_accepted_reports_does_not_guess_from_legacy_kind_or_layout) {
    RecordingHandler handler;
    SourceTable table(handler);
    auto source = mouse_identity(0);
    source.report_set = {};
    table.on_event(ready_event(0), source, 0);
    const std::uint8_t move[] = {1, 5, 6};
    table.on_event(report_event(0, move, sizeof(move)), source, 1);
    CHECK(handler.events.empty());
}
