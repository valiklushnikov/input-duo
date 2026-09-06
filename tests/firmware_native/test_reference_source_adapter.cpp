// What a captured USB callback record turns into at the neutral boundary.
//
// The adapter is the only place in the reference target that decides what a
// device is and which of the two roles it may occupy, so these tests assert
// the SourceEvent and SourceIdentity values literally rather than through the
// adapter's own helpers - a wrong answer that both the code and the test agree
// on is exactly what this file has to be unable to produce.
//
// Real descriptors, not invented ones: the same fixtures the CH375 and current
// PIO backends are tested against, so a device that reads one way there cannot
// quietly read another way here.

#include <cstdint>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include "callback_queue.hpp"
#include "source_adapter.hpp"
#include "test_support.hpp"

using duo_input::u1::input::DeviceKind;
using duo_input::u1::input::SourceEvent;
using duo_input::u1::input::SourceEventKind;
using duo_input::u1::input::SourceIdentity;
using duo_input::u1::reference::ReferenceSourceAdapter;

namespace {

constexpr std::uint8_t kProtocolNone = 0;
constexpr std::uint8_t kProtocolKeyboard = 1;
constexpr std::uint8_t kProtocolMouse = 2;

std::vector<std::uint8_t> descriptor(const char* name) {
    std::ifstream stream(std::string{DUO_TEST_VECTOR_DIR} + "/hid_descriptors/" + name,
                         std::ios::binary);
    return {std::istreambuf_iterator<char>{stream}, std::istreambuf_iterator<char>{}};
}

ReferenceCallbackRecord mount(std::uint8_t dev_addr,
                              std::uint8_t instance,
                              std::uint8_t protocol,
                              std::uint16_t vid,
                              std::uint16_t pid,
                              const std::vector<std::uint8_t>& bytes) {
    return reference_make_mount(dev_addr, instance, protocol, vid, pid,
                                bytes.empty() ? nullptr : bytes.data(),
                                static_cast<std::uint16_t>(bytes.size()), 0);
}

ReferenceCallbackRecord report(std::uint8_t dev_addr,
                               std::uint8_t instance,
                               const std::vector<std::uint8_t>& bytes,
                               std::uint32_t now_us = 4242) {
    return reference_make_report(dev_addr, instance, bytes.data(),
                                 static_cast<std::uint16_t>(bytes.size()), now_us);
}

struct Taken {
    bool ok = false;
    SourceEvent event{};
    SourceIdentity identity{};
};

Taken take(ReferenceSourceAdapter& adapter) {
    Taken taken;
    taken.ok = adapter.take_event(taken.event, taken.identity);
    return taken;
}

}  // namespace

TEST_CASE(a_boot_mouse_mount_is_announced_as_a_ready_mouse) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(1, 0, kProtocolMouse, 0x1BCF, 0x0005,
                          descriptor("boot_mouse.bin")),
                    0);

    const Taken taken = take(adapter);
    CHECK(taken.ok);
    CHECK(taken.event.kind == SourceEventKind::Ready);
    CHECK(taken.identity.kind == DeviceKind::Mouse);
    CHECK_EQ(taken.identity.vendor_id, 0x1BCFu);
    CHECK_EQ(taken.identity.product_id, 0x0005u);
    CHECK_EQ(taken.event.source_id, adapter.logical_port(DeviceKind::Mouse));
    CHECK_EQ(taken.event.report_size, static_cast<std::size_t>(0));

    CHECK_FALSE(take(adapter).ok);
}

TEST_CASE(a_boot_keyboard_mount_is_announced_as_a_ready_keyboard) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(2, 0, kProtocolKeyboard, 0x3554, 0xFA09,
                          descriptor("boot_keyboard.bin")),
                    0);

    const Taken taken = take(adapter);
    CHECK(taken.ok);
    CHECK(taken.event.kind == SourceEventKind::Ready);
    CHECK(taken.identity.kind == DeviceKind::Keyboard);
    CHECK_EQ(taken.event.source_id, adapter.logical_port(DeviceKind::Keyboard));
    CHECK(adapter.logical_port(DeviceKind::Keyboard) !=
          adapter.logical_port(DeviceKind::Mouse));
}

TEST_CASE(a_five_button_mouse_keeps_the_layout_its_descriptor_declared) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(1, 0, kProtocolMouse, 0x3434, 0xD031,
                          descriptor("mouse_5_button.bin")),
                    0);

    const Taken taken = take(adapter);
    CHECK(taken.ok);
    CHECK(taken.identity.kind == DeviceKind::Mouse);
    // The whole point of parsing the descriptor rather than assuming boot
    // protocol: a wheel this device declares and boot protocol cannot carry.
    CHECK(taken.identity.mouse_layout.wheel.present);
}

TEST_CASE(the_first_claimant_owns_a_role_and_a_second_is_ignored) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(1, 0, kProtocolKeyboard, 0x1111, 0x2222,
                          descriptor("boot_keyboard.bin")),
                    0);
    CHECK(take(adapter).ok);

    // A second keyboard-shaped interface, on a different device, must not
    // displace the one already routing.
    adapter.consume(mount(3, 0, kProtocolKeyboard, 0x3333, 0x4444,
                          descriptor("boot_keyboard.bin")),
                    0);
    CHECK_FALSE(take(adapter).ok);
}

TEST_CASE(reports_from_the_owning_interface_carry_their_bytes_unchanged) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(1, 0, kProtocolMouse, 0x1BCF, 0x0005,
                          descriptor("boot_mouse.bin")),
                    0);
    CHECK(take(adapter).ok);

    const std::vector<std::uint8_t> bytes{0x01, 0xF0, 0x0A};
    adapter.consume(report(1, 0, bytes, 4242), 0);

    const Taken taken = take(adapter);
    CHECK(taken.ok);
    CHECK(taken.event.kind == SourceEventKind::Report);
    CHECK_EQ(taken.event.source_id, adapter.logical_port(DeviceKind::Mouse));
    CHECK_EQ(taken.event.endpoint, 0u);
    CHECK_EQ(taken.event.received_us, 4242u);
    CHECK_EQ(taken.event.report_size, static_cast<std::size_t>(3));
    CHECK_EQ(taken.event.report[0], 0x01u);
    CHECK_EQ(taken.event.report[1], 0xF0u);
    CHECK_EQ(taken.event.report[2], 0x0Au);
}

TEST_CASE(a_report_from_an_interface_that_owns_nothing_produces_no_event) {
    ReferenceSourceAdapter adapter;

    const std::vector<std::uint8_t> bytes{0x01, 0x02, 0x03};
    adapter.consume(report(9, 4, bytes), 0);

    CHECK_FALSE(take(adapter).ok);
}

TEST_CASE(the_keychron_side_channel_is_auxiliary_and_never_the_keyboard) {
    ReferenceSourceAdapter adapter;

    // The receiver's mouse interface takes the Mouse role.
    adapter.consume(mount(1, 0, kProtocolMouse, 0x3434, 0xD030,
                          descriptor("boot_mouse.bin")),
                    0);
    CHECK(take(adapter).ok);

    // Its keyboard-shaped side-button interface must not become the Keyboard.
    adapter.consume(mount(1, 2, kProtocolKeyboard, 0x3434, 0xD030,
                          descriptor("boot_keyboard.bin")),
                    0);
    CHECK_FALSE(take(adapter).ok);

    // A real keyboard elsewhere can still claim the role afterwards.
    adapter.consume(mount(2, 0, kProtocolKeyboard, 0x3554, 0xFA09,
                          descriptor("boot_keyboard.bin")),
                    0);
    const Taken keyboard = take(adapter);
    CHECK(keyboard.ok);
    CHECK(keyboard.identity.kind == DeviceKind::Keyboard);

    // And the side channel's reports reach the mouse, as auxiliary.
    const std::vector<std::uint8_t> side{0x00, 0x00, 0x50, 0, 0, 0, 0, 0};
    adapter.consume(report(1, 2, side), 0);
    const Taken auxiliary = take(adapter);
    CHECK(auxiliary.ok);
    CHECK(auxiliary.event.kind == SourceEventKind::AuxiliaryReport);
    CHECK_EQ(auxiliary.event.source_id, adapter.logical_port(DeviceKind::Mouse));
    CHECK_EQ(auxiliary.event.endpoint, 2u);
}

TEST_CASE(an_unmount_detaches_the_role_and_frees_it_for_the_next_device) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(1, 0, kProtocolKeyboard, 0x1111, 0x2222,
                          descriptor("boot_keyboard.bin")),
                    0);
    CHECK(take(adapter).ok);

    adapter.consume(reference_make_unmount(1, 0, 0), 0);
    const Taken detached = take(adapter);
    CHECK(detached.ok);
    CHECK(detached.event.kind == SourceEventKind::Detached);
    CHECK_EQ(detached.event.source_id, adapter.logical_port(DeviceKind::Keyboard));

    // Freed, so a replacement is announced rather than ignored.
    adapter.consume(mount(4, 0, kProtocolKeyboard, 0x5555, 0x6666,
                          descriptor("boot_keyboard.bin")),
                    0);
    const Taken replacement = take(adapter);
    CHECK(replacement.ok);
    CHECK(replacement.event.kind == SourceEventKind::Ready);
    CHECK_EQ(replacement.identity.vendor_id, 0x5555u);
}

TEST_CASE(an_unmount_of_an_interface_that_owns_nothing_says_nothing) {
    ReferenceSourceAdapter adapter;
    adapter.consume(reference_make_unmount(7, 3, 0), 0);
    CHECK_FALSE(take(adapter).ok);
}

TEST_CASE(an_overflow_faults_every_role_that_was_holding_something) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(1, 0, kProtocolKeyboard, 0x1111, 0x2222,
                          descriptor("boot_keyboard.bin")),
                    0);
    CHECK(take(adapter).ok);
    adapter.consume(mount(2, 0, kProtocolMouse, 0x1BCF, 0x0005,
                          descriptor("boot_mouse.bin")),
                    0);
    CHECK(take(adapter).ok);

    // Input was dropped, so nothing downstream can be trusted to know what is
    // still held. Both roles must be told, not just the first one.
    ReferenceCallbackRecord overflow{};
    overflow.kind = ReferenceCallbackKind::Overflow;
    adapter.consume(overflow, 0);

    bool keyboard_faulted = false;
    bool mouse_faulted = false;
    for (int index = 0; index < 2; ++index) {
        const Taken taken = take(adapter);
        CHECK(taken.ok);
        CHECK(taken.event.kind == SourceEventKind::Fault);
        if (taken.event.source_id == adapter.logical_port(DeviceKind::Keyboard)) {
            keyboard_faulted = true;
        }
        if (taken.event.source_id == adapter.logical_port(DeviceKind::Mouse)) {
            mouse_faulted = true;
        }
    }
    CHECK(keyboard_faulted);
    CHECK(mouse_faulted);
    CHECK_FALSE(take(adapter).ok);
}

TEST_CASE(an_overflow_with_nothing_attached_faults_nothing) {
    ReferenceSourceAdapter adapter;
    ReferenceCallbackRecord overflow{};
    overflow.kind = ReferenceCallbackKind::Overflow;
    adapter.consume(overflow, 0);
    CHECK_FALSE(take(adapter).ok);
}

TEST_CASE(a_descriptor_that_will_not_parse_does_not_take_a_role_on_its_shape) {
    ReferenceSourceAdapter adapter;

    // A truncated descriptor with no interface protocol to fall back on is a
    // device this firmware cannot read. Claiming a role for it would keep the
    // real device that follows from ever getting one.
    adapter.consume(mount(1, 0, kProtocolNone, 0x9999, 0x8888,
                          descriptor("truncated_item.bin")),
                    0);
    CHECK_FALSE(take(adapter).ok);

    adapter.consume(mount(2, 0, kProtocolMouse, 0x1BCF, 0x0005,
                          descriptor("boot_mouse.bin")),
                    0);
    const Taken taken = take(adapter);
    CHECK(taken.ok);
    CHECK(taken.identity.kind == DeviceKind::Mouse);
}

TEST_CASE(a_vendor_only_interface_is_ignored_rather_than_given_a_role) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(1, 1, kProtocolNone, 0x3434, 0xD030,
                          descriptor("vendor_only.bin")),
                    0);
    CHECK_FALSE(take(adapter).ok);
}

TEST_CASE(pending_events_are_visible_before_they_are_taken) {
    ReferenceSourceAdapter adapter;
    CHECK_FALSE(adapter.has_pending());

    adapter.consume(mount(1, 0, kProtocolMouse, 0x1BCF, 0x0005,
                          descriptor("boot_mouse.bin")),
                    0);
    CHECK(adapter.has_pending());
    CHECK(take(adapter).ok);
    CHECK_FALSE(adapter.has_pending());
}
