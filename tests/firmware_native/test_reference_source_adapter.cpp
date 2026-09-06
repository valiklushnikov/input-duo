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
//: The wire-visible "no compared byte disagreed" sentinel, written here
//: as a literal rather than taken from the production constant. A
//: first-difference index that was never computed is precisely the answer
//: this measurement exists to test, so the test must not borrow the
//: implementation's own idea of it.
constexpr std::uint16_t kNoDifferenceSentinel = 0xFFFFu;
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

struct CdcWriter final : IReferenceCdcWriter {
    std::size_t space = 0;
    std::size_t write_limit = 0;
    std::size_t write_calls = 0;
    std::size_t flush_calls = 0;
    std::string output;

    std::size_t available() const override { return space; }

    std::size_t write(const char* data, std::size_t size) override {
        ++write_calls;
        const std::size_t count = std::min(size, write_limit);
        output.append(data, count);
        return count;
    }

    void flush() override { ++flush_calls; }
};

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
    CHECK(transfer.start(2, 0, 64u));
    CHECK(transfer.active());
    CHECK_FALSE(transfer.abandon(2, 1));
    CHECK(transfer.abandon(2, 0));
    CHECK_FALSE(transfer.active());

    CHECK(transfer.start(3, 0, 64u));
    const auto stale =
        transfer.complete(2, true, 77u, true, 256u, transfer.lifetime_token());
    CHECK(stale == duo_input::u1::reference::DescriptorTransferState::Completion::Ignored);
    CHECK(transfer.active());
    const auto current =
        transfer.complete(3, true, 77u, true, 256u, transfer.lifetime_token());
    CHECK(current == duo_input::u1::reference::DescriptorTransferState::Completion::Success);
    CHECK_FALSE(transfer.active());

    CHECK(transfer.start(2, 0, 64u));
    const std::uint32_t old_token = transfer.lifetime_token();
    CHECK(transfer.abandon(2, 0));
    CHECK(transfer.start(2, 0, 64u));
    CHECK(transfer.complete(2, true, 64u, true, 256u, old_token) ==
          duo_input::u1::reference::DescriptorTransferState::Completion::Ignored);
    CHECK(transfer.active());
}

TEST_CASE(descriptor_completion_rejects_failure_short_lifetime_and_overflow) {
    using State = duo_input::u1::reference::DescriptorTransferState;
    State transfer;
    CHECK(transfer.start(2, 0, 64u));
    CHECK(transfer.complete(2, false, 0u, true, 256u,
                            transfer.lifetime_token()) ==
          State::Completion::Failure);
    CHECK(transfer.start(2, 0, 64u));
    CHECK(transfer.complete(2, true, 77u, false, 256u,
                            transfer.lifetime_token()) ==
          State::Completion::Failure);
    CHECK(transfer.start(2, 0, 64u));
    CHECK(transfer.complete(2, true, 257u, true, 256u,
                            transfer.lifetime_token()) ==
          State::Completion::Failure);
    CHECK(transfer.start(2, 0, 64u));
    CHECK(transfer.complete(2, true, 0u, true, 256u,
                            transfer.lifetime_token()) ==
          State::Completion::Success);
}

TEST_CASE(desc64_comparison_uses_actual_length_and_the_independent_golden_prefix) {
    using Result = duo_input::u1::reference::DescriptorDiagnosticResult;
    const auto golden = aula_keyboard_descriptor_vector();
    CHECK_EQ(golden.size(), 77u);

    const auto match = duo_input::u1::reference::descriptor_diagnostic_complete(
            true, 64u, golden.data(), golden.size(), 64u);
    CHECK(match.kind == Result::Kind::Match);
    CHECK_EQ(match.actual_len, 64u);
    CHECK_EQ(match.first_difference, kNoDifferenceSentinel);

    // Every compared byte agreed but the length was wrong. There is no first
    // difference to report, and reporting min(actual_len, 64) as one claims
    // those bytes were golden without having looked at a single one of them.
    const auto short_completion =
        duo_input::u1::reference::descriptor_diagnostic_complete(
            true, 63u, golden.data(), golden.size(), 64u);
    CHECK(short_completion.kind == Result::Kind::Mismatch);
    CHECK_EQ(short_completion.first_difference, kNoDifferenceSentinel);

    // A short completion that does disagree reports where it disagreed, not
    // how long it was.
    auto short_wrong = golden;
    short_wrong[3] ^= 0x01u;
    const auto short_mismatch =
        duo_input::u1::reference::descriptor_diagnostic_complete(
            true, 10u, short_wrong.data(), short_wrong.size(), 64u);
    CHECK(short_mismatch.kind == Result::Kind::Mismatch);
    CHECK_EQ(short_mismatch.first_difference, 3u);

    // The case the hardware actually produced: a successful transfer of zero
    // bytes. Nothing was compared, so nothing differed.
    const std::vector<std::uint8_t> poisoned(64u, 0xA5u);
    const auto empty = duo_input::u1::reference::descriptor_diagnostic_complete(
            true, 0u, poisoned.data(), poisoned.size(), 64u);
    CHECK(empty.kind == Result::Kind::Mismatch);
    CHECK_EQ(empty.actual_len, 0u);
    CHECK_EQ(empty.first_difference, kNoDifferenceSentinel);

    // Poison is not golden, so a full-length completion into a buffer nothing
    // wrote to is a mismatch at byte zero rather than a match.
    const auto poison_full =
        duo_input::u1::reference::descriptor_diagnostic_complete(
            true, 64u, poisoned.data(), poisoned.size(), 64u);
    CHECK(poison_full.kind == Result::Kind::Mismatch);
    CHECK_EQ(poison_full.first_difference, 0u);

    auto wrong = golden;
    wrong[17] ^= 0x01u;
    const auto mismatch = duo_input::u1::reference::descriptor_diagnostic_complete(
            true, 64u, wrong.data(), wrong.size(), 64u);
    CHECK(mismatch.kind == Result::Kind::Mismatch);
    CHECK_EQ(mismatch.first_difference, 17u);

    const auto failed = duo_input::u1::reference::descriptor_diagnostic_complete(
            false, 64u, golden.data(), golden.size(), 64u);
    CHECK(failed.kind == Result::Kind::Failure);

    // No buffer is an internal fault, not a device measurement.
    const auto no_buffer =
        duo_input::u1::reference::descriptor_diagnostic_complete(
            true, 64u, nullptr, 64u, 64u);
    CHECK(no_buffer.kind == Result::Kind::Failure);
}

TEST_CASE(a_descriptor_completion_without_the_live_token_is_ignored) {
    using State = duo_input::u1::reference::DescriptorTransferState;
    State transfer;
    CHECK(transfer.start(2, 0, 64u));
    // start() pre-increments from zero, so zero is never a live token: a
    // completion carrying it is one whose user_data was lost on the way, and
    // it must not be allowed to answer for the attempt that is in flight.
    CHECK(transfer.complete(2, true, 64u, true, 256u, 0u) ==
          State::Completion::Ignored);
    CHECK(transfer.active());
    CHECK(transfer.complete(2, true, 64u, true, 256u,
                            transfer.lifetime_token()) ==
          State::Completion::Success);
    CHECK_FALSE(transfer.active());
}

TEST_CASE(the_request_buffer_is_poisoned_so_a_stale_answer_cannot_read_as_golden) {
    using Result = duo_input::u1::reference::DescriptorDiagnosticResult;
    const auto golden = aula_keyboard_descriptor_vector();

    std::array<std::uint8_t, 64> buffer{};
    std::copy_n(golden.data(), buffer.size(), buffer.begin());

    // Left holding the previous attempt's bytes, a transfer that reports 64
    // bytes while writing none compares clean and prints MATCH - the exact
    // failure under investigation, reported as its own opposite.
    CHECK(duo_input::u1::reference::descriptor_diagnostic_complete(
            true, 64u, buffer.data(), buffer.size(), 64u)
              .kind == Result::Kind::Match);

    duo_input::u1::reference::poison_descriptor_buffer(buffer);
    for (const std::uint8_t value : buffer) {
        CHECK_EQ(value, 0xA5u);
    }
    // 0xA5 cannot begin a valid report descriptor and is not a golden byte,
    // so the same transfer is now visible as having written nothing.
    const auto after = duo_input::u1::reference::descriptor_diagnostic_complete(
            true, 64u, buffer.data(), buffer.size(), 64u);
    CHECK(after.kind == Result::Kind::Mismatch);
    CHECK_EQ(after.first_difference, 0u);
    CHECK(golden[0] != 0xA5u);
}

TEST_CASE(a_completion_reports_the_buffer_prefix_whatever_the_outcome) {
    using Coordinator =
        duo_input::u1::reference::DescriptorDiagnosticCoordinator;
    using Completion =
        duo_input::u1::reference::DescriptorTransferState::Completion;

    reference_queue_reset();
    ReferenceSourceAdapter adapter;
    Coordinator coordinator(adapter);

    std::array<std::uint8_t, 64> poisoned{};
    duo_input::u1::reference::poison_descriptor_buffer(poisoned);

    // A successful transfer of zero bytes. Printing the buffer only when
    // actual_len is non-zero is what made the measured run unable to say
    // whether anything arrived at all.
    CHECK(coordinator.start(2, 0, 64u));
    CHECK(coordinator.complete(2, true, 0u, true, poisoned.data(),
                               poisoned.size(),
                               coordinator.lifetime_token()) ==
          Completion::Success);

    ReferenceDescriptorDiagnostic entry{};
    CHECK(reference_descriptor_diagnostic_take(entry));
    CHECK(entry.kind == ReferenceDescriptorDiagnosticKind::Mismatch);
    CHECK_EQ(entry.actual_len, 0u);
    CHECK_EQ(entry.first_difference, kNoDifferenceSentinel);
    CHECK_EQ(entry.prefix_size, 24u);
    for (std::size_t index = 0; index < entry.prefix.size(); ++index) {
        CHECK_EQ(entry.prefix[index], 0xA5u);
    }

    // And on a match, where the old image printed no bytes at all.
    const auto golden = aula_keyboard_descriptor_vector();
    CHECK(coordinator.start(2, 0, 64u));
    CHECK(coordinator.complete(2, true, 64u, true, golden.data(), golden.size(),
                               coordinator.lifetime_token()) ==
          Completion::Success);
    CHECK(reference_descriptor_diagnostic_take(entry));
    CHECK(entry.kind == ReferenceDescriptorDiagnosticKind::Match);
    CHECK_EQ(entry.prefix_size, 24u);
    CHECK_EQ(entry.prefix[0], 0x05u);
    CHECK_EQ(entry.prefix[7], 0x08u);
}

TEST_CASE(a_failed_completion_says_why_and_is_never_a_device_measurement) {
    using Coordinator =
        duo_input::u1::reference::DescriptorDiagnosticCoordinator;
    using Completion =
        duo_input::u1::reference::DescriptorTransferState::Completion;

    reference_queue_reset();
    ReferenceSourceAdapter adapter;
    Coordinator coordinator(adapter);
    std::array<std::uint8_t, 64> poisoned{};
    duo_input::u1::reference::poison_descriptor_buffer(poisoned);

    struct Expectation {
        bool transfer_succeeded;
        std::uint32_t actual_len;
        bool still_mounted;
        bool with_buffer;
        ReferenceDescriptorReason reason;
    };
    const Expectation expectations[] = {
        {false, 0u, true, true, ReferenceDescriptorReason::Transfer},
        {true, 64u, false, true, ReferenceDescriptorReason::Gone},
        {true, 65u, true, true, ReferenceDescriptorReason::TooLong},
        {true, 64u, true, false, ReferenceDescriptorReason::NoBuffer},
    };

    for (const Expectation& expectation : expectations) {
        CHECK(coordinator.start(2, 0, 64u));
        CHECK(coordinator.complete(
                  2, expectation.transfer_succeeded, expectation.actual_len,
                  expectation.still_mounted,
                  expectation.with_buffer ? poisoned.data() : nullptr,
                  poisoned.size(), coordinator.lifetime_token()) ==
              Completion::Failure);
        ReferenceDescriptorDiagnostic entry{};
        CHECK(reference_descriptor_diagnostic_take(entry));
        // An internal fault must not be dressed up as a mismatch: that is a
        // device measurement, and no device produced it.
        CHECK(entry.kind == ReferenceDescriptorDiagnosticKind::Failure);
        CHECK(entry.reason == expectation.reason);
    }
}

TEST_CASE(an_abandoned_in_flight_measurement_reports_a_skip) {
    using Coordinator =
        duo_input::u1::reference::DescriptorDiagnosticCoordinator;

    reference_queue_reset();
    ReferenceSourceAdapter adapter;
    Coordinator coordinator(adapter);

    CHECK(coordinator.start(2, 0, 64u));
    CHECK(coordinator.abandon_if_unmounted(false));
    ReferenceDescriptorDiagnostic entry{};
    CHECK(reference_descriptor_diagnostic_take(entry));
    CHECK(entry.kind == ReferenceDescriptorDiagnosticKind::Skip);
    CHECK(entry.reason == ReferenceDescriptorReason::Unmounted);

    // The same one line is what the host core emits for a give-up the adapter
    // reports: an experiment that ends in silence cannot be told apart from a
    // board that was never flashed.
    coordinator.skipped(ReferenceDescriptorReason::NoInterface, 2, 0, 64u);
    CHECK(reference_descriptor_diagnostic_take(entry));
    CHECK(entry.kind == ReferenceDescriptorDiagnosticKind::Skip);
    CHECK(entry.reason == ReferenceDescriptorReason::NoInterface);
}

TEST_CASE(a_drained_offer_budget_is_reported_rather_than_leaving_silence) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(2, 0, kProtocolKeyboard, 0x3554, 0xFA09, {}), 0u);
    CHECK(take(adapter).ok);

    ReferenceSourceAdapter::DescriptorRequest request{};
    std::uint32_t now = ReferenceSourceAdapter::kDescriptorQuietUs;
    std::uint32_t offers = 0;
    while (adapter.take_descriptor_request(now, request)) {
        ++offers;
        now += ReferenceSourceAdapter::kDescriptorOfferIntervalUs;
    }
    CHECK_EQ(offers, ReferenceSourceAdapter::kDescriptorMaxOffers);

    ReferenceDescriptorReason reason = ReferenceDescriptorReason::None;
    ReferenceSourceAdapter::DescriptorRequest abandoned{};
    CHECK(adapter.take_descriptor_giveup(reason, abandoned));
    CHECK(reason == ReferenceDescriptorReason::NoOffer);
    CHECK_EQ(abandoned.dev_addr, 2u);
    CHECK_EQ(abandoned.instance, 0u);
    // One line per attempt, not one per pass.
    CHECK_FALSE(adapter.take_descriptor_giveup(reason, abandoned));
}

TEST_CASE(an_unmount_before_the_wire_is_reported_rather_than_leaving_silence) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(2, 0, kProtocolKeyboard, 0x3554, 0xFA09, {}), 0u);
    CHECK(take(adapter).ok);
    adapter.consume(reference_make_unmount(2, 0, 1u), 1u);

    ReferenceDescriptorReason reason = ReferenceDescriptorReason::None;
    ReferenceSourceAdapter::DescriptorRequest abandoned{};
    CHECK(adapter.take_descriptor_giveup(reason, abandoned));
    CHECK(reason == ReferenceDescriptorReason::Unmounted);
    CHECK_FALSE(adapter.take_descriptor_giveup(reason, abandoned));
}

TEST_CASE(a_host_side_give_up_names_its_own_reason) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(2, 0, kProtocolKeyboard, 0x3554, 0xFA09, {}), 0u);
    CHECK(take(adapter).ok);

    ReferenceSourceAdapter::DescriptorRequest request{};
    CHECK(adapter.take_descriptor_request(
        ReferenceSourceAdapter::kDescriptorQuietUs, request));
    // tuh_hid_itf_get_info() failing is the give-up that used to consume an
    // offer and return with nothing printed.
    adapter.abandon_descriptor_request(ReferenceDescriptorReason::NoInterface,
                                       request);

    ReferenceSourceAdapter::DescriptorRequest again{};
    CHECK_FALSE(adapter.take_descriptor_request(
        ReferenceSourceAdapter::kDescriptorQuietUs +
            ReferenceSourceAdapter::kDescriptorOfferIntervalUs,
        again));

    ReferenceDescriptorReason reason = ReferenceDescriptorReason::None;
    ReferenceSourceAdapter::DescriptorRequest abandoned{};
    CHECK(adapter.take_descriptor_giveup(reason, abandoned));
    CHECK(reason == ReferenceDescriptorReason::NoInterface);
    CHECK_EQ(abandoned.dev_addr, 2u);
}

TEST_CASE(a_refused_capture_retires_the_measurement_and_says_so) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(2, 0, kProtocolKeyboard, 0x3554, 0xFA09, {}), 0u);
    CHECK(take(adapter).ok);

    // An overflow retires every role downstream, and this request with them.
    ReferenceCallbackRecord overflow{};
    overflow.kind = ReferenceCallbackKind::Overflow;
    adapter.consume(overflow, 1u);

    ReferenceSourceAdapter::DescriptorRequest request{};
    CHECK_FALSE(adapter.take_descriptor_request(
        ReferenceSourceAdapter::kDescriptorQuietUs, request));

    ReferenceDescriptorReason reason = ReferenceDescriptorReason::None;
    ReferenceSourceAdapter::DescriptorRequest abandoned{};
    CHECK(adapter.take_descriptor_giveup(reason, abandoned));
    CHECK(reason == ReferenceDescriptorReason::Overflow);
}

TEST_CASE(an_accepted_request_reports_no_give_up) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(2, 0, kProtocolKeyboard, 0x3554, 0xFA09, {}), 0u);
    CHECK(take(adapter).ok);

    ReferenceSourceAdapter::DescriptorRequest request{};
    CHECK(adapter.take_descriptor_request(
        ReferenceSourceAdapter::kDescriptorQuietUs, request));
    adapter.descriptor_request_accepted();

    ReferenceDescriptorReason reason = ReferenceDescriptorReason::None;
    ReferenceSourceAdapter::DescriptorRequest abandoned{};
    CHECK_FALSE(adapter.take_descriptor_giveup(reason, abandoned));
}

//: The first 24 golden bytes, which is what the widened prefix renders.
#define HEX_PREFIX "05010906A101050819012903150025017501950391029505"

TEST_CASE(descriptor_completion_owns_cdc_until_one_full_write) {
    using Coordinator =
        duo_input::u1::reference::DescriptorDiagnosticCoordinator;
    using Completion =
        duo_input::u1::reference::DescriptorTransferState::Completion;

    reference_queue_reset();
    ReferenceSourceAdapter adapter;
    Coordinator coordinator(adapter);
    const auto golden = aula_keyboard_descriptor_vector();

    ReferenceTraceEntry report_trace{};
    report_trace.kind = ReferenceCallbackKind::Report;
    report_trace.dev_addr = 7;
    report_trace.instance = 1;
    report_trace.length = 8;
    CHECK(reference_trace_push(report_trace));

    CHECK(coordinator.start(2, 0, 64u));
    const std::uint32_t token = coordinator.lifetime_token();
    CHECK(coordinator.complete(2, true, 64u, true, golden.data(),
                               golden.size(), token) == Completion::Success);

    CdcWriter writer;
    reference_service_cdc(writer);
    CHECK_EQ(writer.write_calls, 0u);
    CHECK(writer.output.empty());

    writer.space = 96;
    writer.write_limit = 2;
    reference_service_cdc(writer);
    CHECK_EQ(writer.write_calls, 1u);
    CHECK_EQ(writer.flush_calls, 0u);
    CHECK_EQ(writer.output, std::string{"DE"});

    writer.write_limit = 96;
    reference_service_cdc(writer);
    CHECK_EQ(writer.write_calls, 2u);
    CHECK_EQ(writer.flush_calls, 1u);
    CHECK_EQ(writer.output,
             std::string{"DESC64_MATCH actual=64 prefix=" HEX_PREFIX "\r\n"});

    reference_service_cdc(writer);
    CHECK_EQ(writer.write_calls, 3u);
    CHECK_EQ(writer.flush_calls, 2u);
    CHECK_EQ(writer.output,
             std::string{"DESC64_MATCH actual=64 prefix=" HEX_PREFIX "\r\n"
                         "REPORT a=7 i=1 len=8\r\n"});

    reference_service_cdc(writer);
    CHECK_EQ(writer.write_calls, 3u);
    CHECK_EQ(writer.flush_calls, 2u);
}

TEST_CASE(refused_unmount_capture_retires_descriptor_state_before_replug) {
    using Coordinator =
        duo_input::u1::reference::DescriptorDiagnosticCoordinator;
    using Completion =
        duo_input::u1::reference::DescriptorTransferState::Completion;

    reference_queue_reset();
    ReferenceSourceAdapter adapter;
    Coordinator coordinator(adapter);
    adapter.consume(mount(2, 0, kProtocolKeyboard, 0x3554, 0xFA09, {}), 0u);
    CHECK(take(adapter).ok);

    ReferenceSourceAdapter::DescriptorRequest old_request{};
    CHECK(adapter.take_descriptor_request(
        ReferenceSourceAdapter::kDescriptorQuietUs, old_request));
    CHECK(coordinator.start(old_request.dev_addr, old_request.instance, 64u));
    const std::uint32_t old_token = coordinator.lifetime_token();

    const std::uint8_t byte = 0xA5;
    for (std::size_t index = 0; index < kReferenceQueueCapacity; ++index) {
        CHECK(reference_capture(reference_make_report(
            static_cast<std::uint8_t>(10 + index), 0, &byte, 1, 1u)));
    }
    CHECK_FALSE(coordinator.capture_unmount(2, 0, 2u));
    CHECK_EQ(reference_overflows(), 1u);
    CHECK_FALSE(coordinator.active());
    CHECK_FALSE(adapter.take_descriptor_request(
        ReferenceSourceAdapter::kDescriptorQuietUs +
            ReferenceSourceAdapter::kDescriptorOfferIntervalUs,
        old_request));
    const Taken detached = take(adapter);
    CHECK(detached.ok);
    CHECK(detached.event.kind == SourceEventKind::Detached);

    ReferenceCallbackRecord queued{};
    while (reference_take(queued)) {
    }
    CHECK(reference_capture(
        mount(2, 0, kProtocolKeyboard, 0x3554, 0xFA09, {})));
    CHECK(reference_take(queued));
    adapter.consume(queued, 10u);
    CHECK(take(adapter).ok);

    ReferenceSourceAdapter::DescriptorRequest fresh_request{};
    CHECK(adapter.take_descriptor_request(
        10u + ReferenceSourceAdapter::kDescriptorQuietUs, fresh_request));
    CHECK(coordinator.start(fresh_request.dev_addr, fresh_request.instance, 64u));
    coordinator.request_accepted();
    const std::uint32_t fresh_token = coordinator.lifetime_token();
    CHECK(fresh_token != old_token);

    const auto golden = aula_keyboard_descriptor_vector();
    CHECK(coordinator.complete(2, true, 64u, true, golden.data(),
                               golden.size(), old_token) == Completion::Ignored);
    CHECK(coordinator.active());
    CHECK(coordinator.complete(2, true, 64u, true, golden.data(),
                               golden.size(), fresh_token) == Completion::Success);
    CHECK_FALSE(coordinator.active());
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

// --------------------------------------------------------------------------
// The 77-byte follow-up
// --------------------------------------------------------------------------
//
// One boot has to yield both measurements: the one-packet read whose answer
// was zero bytes, and the full-document read whose answer arrived rotated by
// 64. Two boots would compare two different device states.

TEST_CASE(the_comparison_covers_the_whole_golden_document_not_only_one_packet) {
    using Result = duo_input::u1::reference::DescriptorDiagnosticResult;
    const auto golden = aula_keyboard_descriptor_vector();
    CHECK_EQ(golden.size(), 77u);

    // A 77-byte answer that agrees everywhere is a match, and saying so means
    // having compared bytes 64..76 rather than assuming them.
    const auto full = duo_input::u1::reference::descriptor_diagnostic_complete(
        true, 77u, golden.data(), golden.size(), 77u);
    CHECK(full.kind == Result::Kind::Match);
    CHECK_EQ(full.first_difference, kNoDifferenceSentinel);

    auto late = golden;
    late[70] ^= 0x01u;
    const auto late_mismatch =
        duo_input::u1::reference::descriptor_diagnostic_complete(
            true, 77u, late.data(), late.size(), 77u);
    CHECK(late_mismatch.kind == Result::Kind::Mismatch);
    CHECK_EQ(late_mismatch.first_difference, 70u);

    // The rotation the hardware produced: golden bytes 64..76 followed by
    // bytes 0..63. It disagrees at byte zero, and nothing about it is golden.
    std::vector<std::uint8_t> rotated(golden.begin() + 64, golden.end());
    rotated.insert(rotated.end(), golden.begin(), golden.begin() + 64);
    CHECK_EQ(rotated.size(), 77u);
    const auto rotated_result =
        duo_input::u1::reference::descriptor_diagnostic_complete(
            true, 77u, rotated.data(), rotated.size(), 77u);
    CHECK(rotated_result.kind == Result::Kind::Mismatch);
    CHECK_EQ(rotated_result.first_difference, 0u);

    // A 77-byte answer to a 64-byte request is not a match however golden it
    // is: the length is part of what is being measured.
    const auto wrong_length =
        duo_input::u1::reference::descriptor_diagnostic_complete(
            true, 77u, golden.data(), golden.size(), 64u);
    CHECK(wrong_length.kind == Result::Kind::Mismatch);
    CHECK_EQ(wrong_length.first_difference, kNoDifferenceSentinel);
}

TEST_CASE(a_completed_measurement_arms_the_seventy_seven_byte_follow_up_once) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(2, 0, kProtocolKeyboard, 0x3554, 0xFA09, {}), 0u);
    CHECK(take(adapter).ok);

    ReferenceSourceAdapter::DescriptorRequest first{};
    CHECK(adapter.take_descriptor_request(
        ReferenceSourceAdapter::kDescriptorQuietUs, first));
    CHECK_EQ(first.length, 64u);
    adapter.descriptor_request_accepted();

    // Nothing more is offered while that attempt is the only one there has
    // been: exactly one on-wire request per experiment.
    ReferenceSourceAdapter::DescriptorRequest none{};
    CHECK_FALSE(adapter.take_descriptor_request(
        2u * ReferenceSourceAdapter::kDescriptorQuietUs, none));

    adapter.schedule_descriptor_followup(
        2u * ReferenceSourceAdapter::kDescriptorQuietUs);

    ReferenceSourceAdapter::DescriptorRequest second{};
    CHECK(adapter.take_descriptor_request(
        2u * ReferenceSourceAdapter::kDescriptorQuietUs, second));
    CHECK_EQ(second.length, 77u);
    CHECK_EQ(second.dev_addr, first.dev_addr);
    CHECK_EQ(second.instance, first.instance);
    adapter.descriptor_request_accepted();

    // And there is no third experiment: arming again must do nothing.
    adapter.schedule_descriptor_followup(
        3u * ReferenceSourceAdapter::kDescriptorQuietUs);
    CHECK_FALSE(adapter.take_descriptor_request(
        3u * ReferenceSourceAdapter::kDescriptorQuietUs, none));
}

TEST_CASE(no_follow_up_is_armed_when_nothing_ever_reached_the_wire) {
    ReferenceSourceAdapter adapter;
    adapter.consume(mount(2, 0, kProtocolKeyboard, 0x3554, 0xFA09, {}), 0u);
    CHECK(take(adapter).ok);

    // The first attempt was never accepted by the host stack, so there is no
    // completion to follow and nothing to follow it up with.
    adapter.schedule_descriptor_followup(
        ReferenceSourceAdapter::kDescriptorQuietUs);
    ReferenceSourceAdapter::DescriptorRequest request{};
    CHECK(adapter.take_descriptor_request(
        ReferenceSourceAdapter::kDescriptorQuietUs, request));
    CHECK_EQ(request.length, 64u);
}

TEST_CASE(a_completion_hands_the_host_core_exactly_one_follow_up_trigger) {
    using Coordinator =
        duo_input::u1::reference::DescriptorDiagnosticCoordinator;
    using Completion =
        duo_input::u1::reference::DescriptorTransferState::Completion;

    reference_queue_reset();
    ReferenceSourceAdapter adapter;
    Coordinator coordinator(adapter);
    const auto golden = aula_keyboard_descriptor_vector();

    CHECK_FALSE(coordinator.take_completed());

    CHECK(coordinator.start(2, 0, 64u));
    CHECK(coordinator.complete(2, true, 0u, true, golden.data(), golden.size(),
                               coordinator.lifetime_token()) ==
          Completion::Success);
    CHECK(coordinator.take_completed());
    CHECK_FALSE(coordinator.take_completed());

    // A failed completion still reached the wire, so it still arms the
    // follow-up: the second measurement is what tells the two apart.
    CHECK(coordinator.start(2, 0, 64u));
    CHECK(coordinator.complete(2, false, 0u, true, golden.data(), golden.size(),
                               coordinator.lifetime_token()) ==
          Completion::Failure);
    CHECK(coordinator.take_completed());

    // A stale completion answers for nothing and must arm nothing.
    CHECK(coordinator.start(2, 0, 64u));
    CHECK(coordinator.complete(2, true, 0u, true, golden.data(), golden.size(),
                               0u) == Completion::Ignored);
    CHECK_FALSE(coordinator.take_completed());
}

TEST_CASE(every_diagnostic_names_the_length_its_attempt_requested) {
    using Coordinator =
        duo_input::u1::reference::DescriptorDiagnosticCoordinator;
    using Completion =
        duo_input::u1::reference::DescriptorTransferState::Completion;

    reference_queue_reset();
    ReferenceSourceAdapter adapter;
    Coordinator coordinator(adapter);
    const auto golden = aula_keyboard_descriptor_vector();

    CHECK(coordinator.start(2, 0, 77u));
    coordinator.request_accepted();

    ReferenceDescriptorDiagnostic started{};
    CHECK(reference_descriptor_diagnostic_take(started));
    CHECK(started.kind == ReferenceDescriptorDiagnosticKind::Start);
    CHECK_EQ(started.requested, 77u);

    CHECK(coordinator.complete(2, true, 77u, true, golden.data(), golden.size(),
                               coordinator.lifetime_token()) ==
          Completion::Success);
    ReferenceDescriptorDiagnostic done{};
    CHECK(reference_descriptor_diagnostic_take(done));
    CHECK(done.kind == ReferenceDescriptorDiagnosticKind::Match);
    CHECK_EQ(done.requested, 77u);
    CHECK_EQ(done.actual_len, 77u);

    // A give-up names the length of the attempt that gave up, so DESC64_SKIP
    // and DESC77_SKIP cannot be confused.
    coordinator.skipped(ReferenceDescriptorReason::NoOffer, 2, 0, 77u);
    ReferenceDescriptorDiagnostic skipped{};
    CHECK(reference_descriptor_diagnostic_take(skipped));
    CHECK_EQ(skipped.requested, 77u);

    // An in-flight attempt that is abandoned names its own requested length
    // rather than a default.
    CHECK(coordinator.start(2, 0, 77u));
    CHECK(coordinator.abandon_if_unmounted(false));
    ReferenceDescriptorDiagnostic abandoned{};
    CHECK(reference_descriptor_diagnostic_take(abandoned));
    CHECK(abandoned.kind == ReferenceDescriptorDiagnosticKind::Skip);
    CHECK_EQ(abandoned.requested, 77u);
}

TEST_CASE(a_follow_up_completion_reports_the_widened_buffer_prefix) {
    using Coordinator =
        duo_input::u1::reference::DescriptorDiagnosticCoordinator;
    using Completion =
        duo_input::u1::reference::DescriptorTransferState::Completion;

    reference_queue_reset();
    ReferenceSourceAdapter adapter;
    Coordinator coordinator(adapter);

    // The rotation prediction: with the buffer poisoned, bytes 13..20 decide
    // whether the 77-byte answer was the golden document rotated by 64 or a
    // 13-byte second packet landing at offset zero.
    const auto golden = aula_keyboard_descriptor_vector();
    std::array<std::uint8_t, 128> buffer{};
    duo_input::u1::reference::poison_descriptor_buffer(buffer);
    std::copy(golden.begin() + 64, golden.end(), buffer.begin());

    CHECK(coordinator.start(2, 0, 77u));
    CHECK(coordinator.complete(2, true, 13u, true, buffer.data(), buffer.size(),
                               coordinator.lifetime_token()) ==
          Completion::Success);

    ReferenceDescriptorDiagnostic entry{};
    CHECK(reference_descriptor_diagnostic_take(entry));
    CHECK_EQ(entry.prefix_size, 24u);
    // A 13-byte second packet that landed at offset zero leaves the poison
    // visible from byte 13 onwards; the golden document rotated by 64 would
    // show 05 01 09 06 there instead.
    CHECK_EQ(entry.prefix[0], 0x81u);
    CHECK_EQ(entry.prefix[12], 0xC0u);
    CHECK_EQ(entry.prefix[13], 0xA5u);
    CHECK_EQ(entry.prefix[23], 0xA5u);
}
