// What a captured USB callback record turns into at the neutral boundary.
//
// The adapter is the only place in the reference target that decides what a
// device is and which of the two roles it may occupy, so these tests assert
// the SourceEvent and SourceIdentity values literally rather than through the
// adapter's own helpers - a wrong answer that both the code and the test agree
// on is exactly what this file has to be unable to produce.
//
// Two kinds of descriptor are involved and they are not interchangeable. The
// files under tests/vectors/hid_descriptors are *configuration* descriptors,
// which is what parse_configuration reads; a report descriptor is a different
// document, fetched with its own request. Feeding a configuration descriptor
// where a report descriptor belongs is not a parse failure to be shrugged at -
// it is how a test comes to pass for a reason its name does not claim. So the
// descriptor path is exercised with a real report descriptor, and the files are
// used only where the point is that a descriptor cannot be read at all.

#include <algorithm>
#include <cstdint>
#include <cctype>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include "callback_queue.hpp"
#include "host_control_state.hpp"
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

/// A report descriptor a mouse would actually send: Report ID 1, five buttons,
/// 16-bit axes and a wheel. Copied from test_report_descriptor.cpp so both
/// files describe the same device to the same parser.
std::vector<std::uint8_t> report_id_wheel_mouse() {
    return {
        0x05, 0x01,        // Usage Page (Generic Desktop)
        0x09, 0x02,        // Usage (Mouse)
        0xA1, 0x01,        // Collection (Application)
        0x85, 0x01,        //   Report ID (1)
        0x09, 0x01,        //   Usage (Pointer)
        0xA1, 0x00,        //   Collection (Physical)
        0x05, 0x09,        //     Usage Page (Button)
        0x19, 0x01,        //     Usage Minimum (Button 1)
        0x29, 0x05,        //     Usage Maximum (Button 5)
        0x15, 0x00,        //     Logical Minimum (0)
        0x25, 0x01,        //     Logical Maximum (1)
        0x75, 0x01,        //     Report Size (1)
        0x95, 0x05,        //     Report Count (5)
        0x81, 0x02,        //     Input (Data,Var,Abs)
        0x75, 0x03,        //     Report Size (3)
        0x95, 0x01,        //     Report Count (1)
        0x81, 0x01,        //     Input (Cnst) - padding to a byte
        0x05, 0x01,        //     Usage Page (Generic Desktop)
        0x09, 0x30,        //     Usage (X)
        0x09, 0x31,        //     Usage (Y)
        0x16, 0x01, 0xF8,  //     Logical Minimum (-2047)
        0x26, 0xFF, 0x07,  //     Logical Maximum (2047)
        0x75, 0x10,        //     Report Size (16)
        0x95, 0x02,        //     Report Count (2)
        0x81, 0x06,        //     Input (Data,Var,Rel)
        0x09, 0x38,        //     Usage (Wheel)
        0x15, 0x81,        //     Logical Minimum (-127)
        0x25, 0x7F,        //     Logical Maximum (127)
        0x75, 0x08,        //     Report Size (8)
        0x95, 0x01,        //     Report Count (1)
        0x81, 0x06,        //     Input (Data,Var,Rel)
        0xC0,              //   End Collection
        0xC0,              // End Collection
    };
}

std::vector<std::uint8_t> aula_keyboard_report() {
    return {
        0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x05, 0x08, 0x19, 0x01,
        0x29, 0x03, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x03,
        0x91, 0x02, 0x95, 0x05, 0x91, 0x01, 0x05, 0x07, 0x19, 0xE0,
        0x29, 0xE7, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x08,
        0x81, 0x02, 0x75, 0x08, 0x95, 0x01, 0x81, 0x01, 0x05, 0x07,
        0x19, 0x00, 0x2A, 0xFF, 0x00, 0x15, 0x00, 0x26, 0xFF, 0x00,
        0x75, 0x08, 0x95, 0x05, 0x81, 0x00, 0x05, 0xFF, 0x09, 0x03,
        0x75, 0x08, 0x95, 0x01, 0x81, 0x02, 0xC0,
    };
}

std::vector<std::uint8_t> aula_keyboard_descriptor_vector() {
    std::ifstream stream(std::string{DUO_TEST_VECTOR_DIR} +
                         "/usb_descriptors/aula_f75_keyboard_report.hex");
    std::string hex{std::istreambuf_iterator<char>{stream},
                    std::istreambuf_iterator<char>{}};
    hex.erase(std::remove_if(hex.begin(), hex.end(), [](char value) {
                  return !std::isxdigit(static_cast<unsigned char>(value));
              }),
              hex.end());
    std::vector<std::uint8_t> bytes;
    for (std::size_t index = 0; index < hex.size(); index += 2) {
        bytes.push_back(static_cast<std::uint8_t>(
            std::stoul(hex.substr(index, 2), nullptr, 16)));
    }
    return bytes;
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

TEST_CASE(a_mouse_keeps_the_layout_its_report_descriptor_declared) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(1, 0, kProtocolMouse, 0x3434, 0xD031,
                          report_id_wheel_mouse()),
                    0);

    const Taken taken = take(adapter);
    CHECK(taken.ok);
    CHECK(taken.identity.kind == DeviceKind::Mouse);

    // Asserted on the Report ID rather than on the wheel: boot_mouse_layout()
    // also marks a wheel present, so a wheel assertion passes just as happily
    // when the descriptor was never read. Only a descriptor can declare an
    // identifier, and only 16-bit axes distinguish this from boot protocol.
    CHECK(taken.identity.mouse_layout.report_id);
    CHECK_EQ(taken.identity.mouse_layout.report_id_value, 1u);
    CHECK_EQ(taken.identity.mouse_layout.x.bytes, 2u);
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

    // Not a report descriptor at all - a configuration descriptor, offered
    // where a report descriptor belongs - and no interface protocol to fall
    // back on. A device this firmware cannot read. Claiming a role for it would
    // keep the real device that follows from ever getting one.
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

// --------------------------------------------------------------------------
// Protocol selection: the layout and the wire format have to agree.
// --------------------------------------------------------------------------

TEST_CASE(an_interface_read_by_descriptor_is_moved_to_report_protocol) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(1, 0, kProtocolMouse, 0x3434, 0xD031,
                          report_id_wheel_mouse()),
                    0);
    CHECK(take(adapter).ok);

    ReferenceSourceAdapter::ProtocolRequest request{};
    CHECK(adapter.take_protocol_request(request));
    CHECK_EQ(request.dev_addr, 1u);
    CHECK_EQ(request.instance, 0u);
    CHECK_EQ(request.protocol, ReferenceSourceAdapter::kHidProtocolReport);

    CHECK_FALSE(adapter.take_protocol_request(request));
}

TEST_CASE(an_interface_that_fell_back_to_boot_is_left_where_it_is) {
    ReferenceSourceAdapter adapter;

    // No descriptor at all: this is the receiver whose descriptor fetch fails.
    // Its layout is boot protocol's, so moving it to report protocol would make
    // it send a format nothing here can read.
    adapter.consume(mount(2, 0, kProtocolKeyboard, 0x3554, 0xFA09, {}), 0);
    const Taken taken = take(adapter);
    CHECK(taken.ok);
    CHECK(taken.identity.kind == DeviceKind::Keyboard);

    ReferenceSourceAdapter::ProtocolRequest request{};
    CHECK_FALSE(adapter.take_protocol_request(request));
}

TEST_CASE(the_aula_keyboard_gets_one_delayed_post_mount_descriptor_request) {
    CHECK_EQ(ReferenceSourceAdapter::kDescriptorQuietUs, 1000000u);
    CHECK_EQ(ReferenceSourceAdapter::kDescriptorOfferIntervalUs, 10000u);
    CHECK_EQ(ReferenceSourceAdapter::kDescriptorMaxOffers, 100u);

    ReferenceSourceAdapter adapter;
    adapter.consume(mount(2, 0, kProtocolKeyboard, 0x3554, 0xFA09, {}), 100u);
    CHECK(take(adapter).ok);

    ReferenceSourceAdapter::DescriptorRequest request{};
    CHECK_FALSE(adapter.take_descriptor_request(
        1000099u, request));
    CHECK(adapter.take_descriptor_request(1000100u, request));
    CHECK_EQ(request.dev_addr, 2u);
    CHECK_EQ(request.instance, 0u);
    CHECK_EQ(request.length, 64u);

    adapter.descriptor_request_accepted();
    CHECK_FALSE(adapter.take_descriptor_request(
        100u + ReferenceSourceAdapter::kDescriptorQuietUs +
            ReferenceSourceAdapter::kDescriptorOfferIntervalUs,
        request));
}

TEST_CASE(a_busy_control_slot_is_reoffered_at_a_bounded_rate_and_count) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(2, 0, kProtocolKeyboard, 0x3554, 0xFA09, {}), 0u);
    CHECK(take(adapter).ok);

    ReferenceSourceAdapter::DescriptorRequest request{};
    std::uint32_t now = ReferenceSourceAdapter::kDescriptorQuietUs;
    for (std::uint32_t offer = 0;
         offer < ReferenceSourceAdapter::kDescriptorMaxOffers; ++offer) {
        CHECK(adapter.take_descriptor_request(now, request));
        CHECK_FALSE(adapter.take_descriptor_request(now, request));
        now += ReferenceSourceAdapter::kDescriptorOfferIntervalUs;
    }
    CHECK_FALSE(adapter.take_descriptor_request(now, request));
    CHECK_FALSE(adapter.take_descriptor_request(now + 1000000u, request));
}

TEST_CASE(unmount_cancels_a_descriptor_request_that_has_not_started) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(2, 0, kProtocolKeyboard, 0x3554, 0xFA09, {}), 0u);
    CHECK(take(adapter).ok);
    adapter.consume(reference_make_unmount(2, 0, 1u), 1u);
    CHECK(take(adapter).ok);

    ReferenceSourceAdapter::DescriptorRequest request{};
    CHECK_FALSE(adapter.take_descriptor_request(
        ReferenceSourceAdapter::kDescriptorQuietUs, request));
}

TEST_CASE(the_post_mount_experiment_is_scoped_to_the_aula_keyboard) {
    ReferenceSourceAdapter adapter;
    ReferenceSourceAdapter::DescriptorRequest request{};

    adapter.consume(mount(1, 0, kProtocolKeyboard, 0x1234, 0x5678, {}), 0u);
    CHECK(take(adapter).ok);
    CHECK_FALSE(adapter.take_descriptor_request(
        ReferenceSourceAdapter::kDescriptorQuietUs, request));

    ReferenceSourceAdapter aula_mouse;
    aula_mouse.consume(mount(2, 1, kProtocolMouse, 0x3554, 0xFA09, {}), 0u);
    CHECK(take(aula_mouse).ok);
    CHECK_FALSE(aula_mouse.take_descriptor_request(
        ReferenceSourceAdapter::kDescriptorQuietUs, request));
}

TEST_CASE(a_late_aula_descriptor_releases_boot_state_before_changing_layout) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(2, 0, kProtocolKeyboard, 0x3554, 0xFA09, {}), 0u);
    const Taken boot = take(adapter);
    CHECK(boot.ok);
    CHECK_FALSE(boot.identity.keyboard_layout.report_id);

    // A key may still be held when the quiet-period read completes. The
    // adapter need not understand held state; Detached is the pipeline's
    // existing exact instruction to release it before the new layout arrives.
    adapter.consume(report(2, 0, {0, 0, 0x04, 0, 0, 0, 0, 0}), 1u);
    CHECK(take(adapter).ok);

    const auto descriptor_bytes = aula_keyboard_report();
    adapter.consume(mount(2, 0, kProtocolKeyboard, 0x3554, 0xFA09,
                          descriptor_bytes),
                    ReferenceSourceAdapter::kDescriptorQuietUs + 100u);
    const Taken released = take(adapter);
    CHECK(released.ok);
    CHECK(released.event.kind == SourceEventKind::Detached);
    CHECK_EQ(released.event.source_id,
             adapter.logical_port(DeviceKind::Keyboard));

    const Taken upgraded = take(adapter);
    CHECK(upgraded.ok);
    CHECK(upgraded.event.kind == SourceEventKind::Ready);
    CHECK(upgraded.identity.kind == DeviceKind::Keyboard);
    CHECK_EQ(upgraded.identity.keyboard_layout.key_element_count, 5u);

    ReferenceSourceAdapter::ProtocolRequest protocol{};
    CHECK(adapter.take_protocol_request(protocol));
    CHECK_EQ(protocol.dev_addr, 2u);
    CHECK_EQ(protocol.instance, 0u);
    CHECK_EQ(protocol.protocol, ReferenceSourceAdapter::kHidProtocolReport);
}

TEST_CASE(unmount_removes_a_queued_late_protocol_change) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(2, 0, kProtocolKeyboard, 0x3554, 0xFA09, {}), 0u);
    CHECK(take(adapter).ok);
    adapter.consume(mount(2, 0, kProtocolKeyboard, 0x3554, 0xFA09,
                          aula_keyboard_report()),
                    ReferenceSourceAdapter::kDescriptorQuietUs);
    CHECK(take(adapter).ok);
    CHECK(take(adapter).ok);

    adapter.consume(reference_make_unmount(2, 0, 0u), 0u);
    CHECK(take(adapter).ok);
    ReferenceSourceAdapter::ProtocolRequest request{};
    CHECK_FALSE(adapter.take_protocol_request(request));
}

TEST_CASE(a_held_protocol_change_is_dropped_when_its_interface_unmounts) {
    duo_input::u1::reference::ProtocolRequestHold held;
    ReferenceSourceAdapter::ProtocolRequest request{2, 0,
        ReferenceSourceAdapter::kHidProtocolReport};
    CHECK(held.hold(request));
    CHECK(held.action(false) ==
          duo_input::u1::reference::ProtocolRequestHold::Action::Dropped);
    CHECK_FALSE(held.active());
}

TEST_CASE(a_canceled_descriptor_transfer_does_not_block_a_replug) {
    duo_input::u1::reference::DescriptorTransferState transfer;
    CHECK(transfer.start(2, 0));
    CHECK(transfer.active());
    CHECK_FALSE(transfer.abandon(2, 1));
    CHECK(transfer.abandon(2, 0));
    CHECK_FALSE(transfer.active());

    CHECK(transfer.start(3, 0));
    const auto stale = transfer.complete(2, true, 77u, true, 256u);
    CHECK(stale == duo_input::u1::reference::DescriptorTransferState::Completion::Ignored);
    CHECK(transfer.active());
    const auto current = transfer.complete(3, true, 77u, true, 256u);
    CHECK(current == duo_input::u1::reference::DescriptorTransferState::Completion::Success);
    CHECK_FALSE(transfer.active());

    CHECK(transfer.start(2, 0));
    const std::uint32_t old_token = transfer.lifetime_token();
    CHECK(transfer.abandon(2, 0));
    CHECK(transfer.start(2, 0));
    CHECK(transfer.complete(2, true, 64u, true, 256u, old_token) ==
          duo_input::u1::reference::DescriptorTransferState::Completion::Ignored);
    CHECK(transfer.active());
}

TEST_CASE(descriptor_completion_rejects_failure_short_lifetime_and_overflow) {
    using State = duo_input::u1::reference::DescriptorTransferState;
    State transfer;
    CHECK(transfer.start(2, 0));
    CHECK(transfer.complete(2, false, 0u, true, 256u) ==
          State::Completion::Failure);
    CHECK(transfer.start(2, 0));
    CHECK(transfer.complete(2, true, 77u, false, 256u) ==
          State::Completion::Failure);
    CHECK(transfer.start(2, 0));
    CHECK(transfer.complete(2, true, 257u, true, 256u) ==
          State::Completion::Failure);
    CHECK(transfer.start(2, 0));
    CHECK(transfer.complete(2, true, 0u, true, 256u) ==
          State::Completion::Success);
}

TEST_CASE(synchronous_descriptor_cancellation_prevents_a_reused_address_offer) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(2, 0, kProtocolKeyboard, 0x3554, 0xFA09, {}), 0u);
    CHECK(take(adapter).ok);
    adapter.cancel_descriptor_request(2, 0);
    ReferenceSourceAdapter::DescriptorRequest request{};
    CHECK_FALSE(adapter.take_descriptor_request(
        ReferenceSourceAdapter::kDescriptorQuietUs, request));
}

TEST_CASE(desc64_comparison_uses_actual_length_and_the_independent_golden_prefix) {
    using Result = duo_input::u1::reference::DescriptorDiagnosticResult;
    const auto golden = aula_keyboard_descriptor_vector();
    CHECK_EQ(golden.size(), 77u);

    const auto match = duo_input::u1::reference::descriptor_diagnostic_complete(
        true, 64u, golden.data(), golden.size());
    CHECK(match.kind == Result::Kind::Match);
    CHECK_EQ(match.actual_len, 64u);

    const auto short_completion =
        duo_input::u1::reference::descriptor_diagnostic_complete(
            true, 63u, golden.data(), golden.size());
    CHECK(short_completion.kind == Result::Kind::Mismatch);
    CHECK_EQ(short_completion.first_difference, 63u);

    auto wrong = golden;
    wrong[17] ^= 0x01u;
    const auto mismatch = duo_input::u1::reference::descriptor_diagnostic_complete(
        true, 64u, wrong.data(), wrong.size());
    CHECK(mismatch.kind == Result::Kind::Mismatch);
    CHECK_EQ(mismatch.first_difference, 17u);

    const auto failed = duo_input::u1::reference::descriptor_diagnostic_complete(
        false, 64u, golden.data(), golden.size());
    CHECK(failed.kind == Result::Kind::Failure);
}

TEST_CASE(an_ignored_interface_asks_for_no_protocol_change) {
    ReferenceSourceAdapter adapter;

    // Second claimant for a role already taken.
    adapter.consume(mount(1, 0, kProtocolMouse, 0x1BCF, 0x0005,
                          descriptor("boot_mouse.bin")),
                    0);
    CHECK(take(adapter).ok);
    ReferenceSourceAdapter::ProtocolRequest first{};
    adapter.take_protocol_request(first);

    adapter.consume(mount(3, 0, kProtocolMouse, 0x2222, 0x3333,
                          report_id_wheel_mouse()),
                    0);
    CHECK_FALSE(take(adapter).ok);

    ReferenceSourceAdapter::ProtocolRequest request{};
    CHECK_FALSE(adapter.take_protocol_request(request));
}

TEST_CASE(the_keychron_side_channel_stays_in_boot_protocol) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(1, 0, kProtocolMouse, 0x3434, 0xD030,
                          descriptor("boot_mouse.bin")),
                    0);
    CHECK(take(adapter).ok);
    ReferenceSourceAdapter::ProtocolRequest request{};
    while (adapter.take_protocol_request(request)) {
    }

    // Its reports are matched by shape, and that shape is boot protocol's.
    adapter.consume(mount(1, 2, kProtocolKeyboard, 0x3434, 0xD030,
                          descriptor("boot_keyboard.bin")),
                    0);
    CHECK_FALSE(adapter.take_protocol_request(request));
}
