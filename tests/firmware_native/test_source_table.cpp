// Every HID interface owns a source slot and its normalizer state.

#include "input/source_table.hpp"
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
    return identity;
}

}  // namespace

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

    SourceIdentity resolved{};
    if (table.resolve(1, resolved)) {
        CHECK(resolved.interface_number == 1);
        CHECK(resolved.vendor_id == 0x1234);
        CHECK(resolved.product_id == 0x5678);
    } else {
        CHECK(false);
    }
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

    SourceIdentity resolved{};
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

    SourceIdentity resolved{};
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
    CHECK_EQ(handler.events.size(), 1u);
    if (handler.events.size() == 1) {
        CHECK(handler.events[0].source_index == 0);
        CHECK(handler.events[0].code == 0);
    }

    for (std::uint8_t slot = 0; slot < SourceTable::kMaxSources; ++slot) {
        SourceIdentity resolved{};
        CHECK(table.resolve(slot, resolved));
        if (table.resolve(slot, resolved)) {
            CHECK(resolved.interface_number == slot);
        }
    }
}
