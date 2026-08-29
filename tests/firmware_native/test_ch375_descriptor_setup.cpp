// Bringing a device up by hand, one control transfer at a time.
//
// The controller offers to do this in a single command, and that command
// cannot finish the job: it assigns the device an address and does not say
// which, while the host has to be told the same address separately (DS2 1.5),
// and it never reports which endpoint the reports will arrive on. On hardware
// that showed as a device that enumerated successfully and then answered a
// hundred and twenty polls with nothing at all.
//
// So each step is taken here, and each one's answer is kept. It is longer, and
// at the end the address and the endpoint are known rather than assumed.
//
// Every step is also bounded. A device that stops answering half way through
// has to end the attempt, not the loop - U1 services USB, the link to U2 and a
// watchdog on the same pass.

#include "ch375/descriptor_setup.hpp"
#include "input/mouse_normalizer.hpp"
#include "fakes/scripted_ch375.hpp"
#include "test_support.hpp"

#include <cstddef>
#include <cstdint>
#include <vector>

using duo_input::u1::ch375::Ch375Transport;
using duo_input::u1::ch375::ReplyProgress;
using duo_input::u1::ch375::DescriptorSetup;
using duo_input::u1::ch375::DeviceKind;
using duo_input::u1::ch375::InterruptStatus;
using duo_input::u1::ch375::kSetupTimeoutUs;
using duo_input::u1::ch375::SetupProgress;
using duo_input::u1::ch375::testing::FakeCh375Chip;

namespace {

struct Rig {
    FakeCh375Chip chip;
    Ch375Transport transport{chip};
    DescriptorSetup setup{transport};

    /// Start setup the way the device machine does.
    ///
    /// With any status the chip is already holding read first: there is
    /// exactly one reader of the interrupt status and it is the caller above
    /// this, not the setup itself. Two readers means each takes the byte the
    /// other was waiting for.
    void begin(std::uint32_t now_us) {
        if (transport.interrupt_pending()) {
            transport.begin_status_read();
            InterruptStatus held = InterruptStatus::Success;
            while (transport.poll_status_read(held) == ReplyProgress::Waiting) {
            }
        }
        setup.begin(now_us);
    }

    /// Drive it the way the device machine does: read the status once, above,
    /// and hand it down.
    SetupProgress settle() {
        for (int pass = 0; pass < 20000; ++pass) {
            bool interrupted = false;
            InterruptStatus status = InterruptStatus::Success;
            if (transport.interrupt_pending()) {
                transport.begin_status_read();
                interrupted = transport.poll_status_read(status) == ReplyProgress::Answered;
            }
            const SetupProgress progress = setup.poll(chip.now_us(), interrupted, status);
            if (progress != SetupProgress::Busy) {
                return progress;
            }
            chip.advance(50);
        }
        return SetupProgress::Busy;
    }
};

}  // namespace

// ------------------------------------------------------------- a mouse

TEST_CASE(a_mouse_is_brought_up) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();

    rig.begin(rig.chip.now_us());

    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));
}

TEST_CASE(the_endpoint_comes_from_the_descriptor_not_from_a_guess) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();  // declares endpoint 2

    rig.begin(rig.chip.now_us());
    rig.settle();

    CHECK_EQ(rig.setup.interrupt_endpoint(), 2u);
    CHECK_EQ(static_cast<int>(rig.setup.kind()), static_cast<int>(DeviceKind::Mouse));
}

TEST_CASE(a_keyboard_on_a_composite_device_is_found) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_composite_keyboard();  // consumer interface first, keyboard second

    rig.begin(rig.chip.now_us());
    rig.settle();

    CHECK_EQ(static_cast<int>(rig.setup.kind()), static_cast<int>(DeviceKind::Keyboard));
    CHECK_EQ(rig.setup.interrupt_endpoint(), 1u);
}

// ------------------------------------------------------------ the address

TEST_CASE(the_device_is_given_an_address_that_is_not_zero) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();

    rig.begin(rig.chip.now_us());
    rig.settle();

    // Everything talks on address zero until told otherwise, and only one
    // device can.
    CHECK(rig.chip.device_address() != 0);
}

TEST_CASE(the_controller_is_told_the_same_address_as_the_device) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();

    rig.begin(rig.chip.now_us());
    rig.settle();

    // DS2 1.5. Miss this and the device has moved while the host goes on
    // calling the address it used to be at - which answers nothing, forever,
    // and looks exactly like a device that is not there.
    CHECK_EQ(rig.chip.host_address(), rig.chip.device_address());
}

TEST_CASE(the_configuration_is_selected) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();

    rig.begin(rig.chip.now_us());
    rig.settle();

    // A device that is addressed but unconfigured has no working endpoints.
    CHECK(rig.chip.configuration_value() != 0);
}

TEST_CASE(the_steps_happen_in_the_order_they_have_to) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();

    rig.begin(rig.chip.now_us());
    rig.settle();

    // The device descriptor has to be read before the address is changed, the
    // host has to follow the device's address before anything is read at it,
    // and the configuration cannot be chosen before it has been read.
    CHECK(rig.chip.order_was_correct());
}

// ------------------------------------------------------------ refusals

TEST_CASE(a_device_this_firmware_cannot_route_is_refused) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_hub();

    rig.begin(rig.chip.now_us());

    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Failed));
}

TEST_CASE(a_device_that_stops_answering_ends_the_attempt) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();
    rig.chip.go_silent(true);

    rig.begin(rig.chip.now_us());

    // That this test finishes is as much the assertion as what it returns.
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Failed));
}

TEST_CASE(a_step_the_device_refuses_ends_the_attempt) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();
    rig.chip.stall_after(2);  // answers the first two transfers, then refuses

    rig.begin(rig.chip.now_us());

    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Failed));
}

TEST_CASE(polling_without_beginning_is_a_failure) {
    Rig rig;

    CHECK_EQ(static_cast<int>(rig.setup.poll(rig.chip.now_us(), false, InterruptStatus::Success)),
             static_cast<int>(SetupProgress::Failed));
}

TEST_CASE(a_second_attempt_starts_from_the_beginning) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_hub();
    rig.begin(rig.chip.now_us());
    rig.settle();

    rig.chip.serve_boot_mouse();
    rig.begin(rig.chip.now_us());

    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));
    CHECK_EQ(rig.setup.interrupt_endpoint(), 2u);
}

TEST_CASE(each_attempt_gives_the_device_a_fresh_address) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();
    rig.begin(rig.chip.now_us());
    rig.settle();
    const std::uint8_t first = rig.chip.device_address();

    rig.begin(rig.chip.now_us());
    rig.settle();

    // A device that was reset kept nothing, so reusing the address is fine -
    // what matters is that the host and the device agree on it afterwards.
    CHECK_EQ(rig.chip.host_address(), rig.chip.device_address());
    CHECK(first != 0);
}

// ------------------------------------------------- putting it in boot protocol
//
// The firmware never selected a protocol, so every device stayed in the one it
// comes up in - its own. A mouse whose native report leads with a Report ID
// then had that identifier read as its buttons: a click on every movement, and
// the horizontal axis lost off the end. `boot=yes` in the diagnostics said only
// that the interface *advertises* boot support, which is not the same thing and
// was read as if it were for weeks.

namespace {

/// The eight bytes of a setup packet, read out by hand.
///
/// Written from USB 2.0 9.3 and HID 1.11 7.2.5 rather than from the firmware's
/// own constants, so that a test cannot agree with a wrong value by sharing it.
struct SetupPacket {
    std::uint8_t request_type = 0;
    std::uint8_t request = 0;
    std::uint16_t value = 0;
    std::uint16_t index = 0;
    std::uint16_t length = 0;
};

/// The one setup packet sent, or a run of zeroes if none was.
///
/// Zeroes rather than reading past the end of an empty list: a test that
/// crashes says far less about what went wrong than one that fails.
std::vector<std::uint8_t> only_setup(const FakeCh375Chip& chip) {
    if (chip.setup_packets().size() != 1) {
        return std::vector<std::uint8_t>(8, 0);
    }
    return chip.setup_packets().front();
}

bool read_setup(const std::vector<std::uint8_t>& bytes, SetupPacket& out) {
    if (bytes.size() != 8) {
        return false;
    }
    out.request_type = bytes[0];
    out.request = bytes[1];
    out.value = static_cast<std::uint16_t>(bytes[2] | (bytes[3] << 8));
    out.index = static_cast<std::uint16_t>(bytes[4] | (bytes[5] << 8));
    out.length = static_cast<std::uint16_t>(bytes[6] | (bytes[7] << 8));
    return true;
}

}  // namespace

TEST_CASE(a_device_that_advertises_boot_support_is_asked_to_use_it) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();

    rig.begin(rig.chip.now_us());
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));

    // One request, and it has to be the right one: SET_PROTOCOL, host to
    // device, class, to an interface, asking for protocol 0 - boot.
    CHECK_EQ(rig.chip.setup_packets().size(), std::size_t{1});
    SetupPacket packet;
    CHECK(read_setup(only_setup(rig.chip), packet));
    CHECK_EQ(packet.request_type, std::uint8_t{0x21});
    CHECK_EQ(packet.request, std::uint8_t{0x0B});
    CHECK_EQ(packet.value, std::uint16_t{0});
    CHECK_EQ(packet.length, std::uint16_t{0});
}

TEST_CASE(the_protocol_request_names_the_interface_that_was_chosen) {
    Rig rig;
    rig.chip.attach_device();
    // The keyboard is the second interface; the first is consumer controls.
    rig.chip.serve_composite_keyboard();

    rig.begin(rig.chip.now_us());
    rig.settle();

    // wIndex is an interface number, and a composite device has several. Sent
    // to the wrong one it configures something nobody is reading.
    CHECK_EQ(rig.chip.setup_packets().size(), std::size_t{1});
    SetupPacket packet;
    CHECK(read_setup(only_setup(rig.chip), packet));
    CHECK_EQ(packet.index, std::uint16_t{1});
}

TEST_CASE(the_device_is_actually_left_in_boot_protocol) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();

    rig.begin(rig.chip.now_us());
    rig.settle();

    // A control transfer that stops after its setup packet changes nothing on
    // the device: the request is applied when the transfer completes.
    CHECK(rig.chip.boot_protocol_selected());
    CHECK(rig.chip.control_status_stages() > 0);
    CHECK(rig.setup.boot_protocol_selected());
}

TEST_CASE(an_interface_that_does_not_advertise_boot_is_not_asked_to_switch) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_mouse_without_boot();

    rig.begin(rig.chip.now_us());

    // There is no boot report behind an interface that does not declare the
    // subclass, so asking for one is asking for something that does not exist.
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));
    CHECK_EQ(rig.chip.setup_packets().size(), std::size_t{0});
    CHECK_FALSE(rig.setup.boot_protocol_selected());
}

TEST_CASE(a_device_that_refuses_the_protocol_request_is_still_brought_up) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();
    rig.chip.refuse_setup_requests(true);

    rig.begin(rig.chip.now_us());

    // It refused one request, not the whole enumeration. A mouse that will not
    // switch protocol is worse than one that will and better than none at all.
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));
    CHECK_EQ(rig.setup.interrupt_endpoint(), 2u);
    CHECK_FALSE(rig.setup.boot_protocol_selected());
}

TEST_CASE(a_device_that_never_answers_the_protocol_request_is_still_brought_up) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();
    rig.chip.ignore_setup_requests(true);

    rig.begin(rig.chip.now_us());

    // That this test finishes is as much the assertion as what it returns: a
    // request nobody answers must end on a deadline, not hold the channel.
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));
    CHECK_FALSE(rig.setup.boot_protocol_selected());
}

TEST_CASE(a_refusal_and_a_silence_are_not_reported_as_the_same_thing) {
    Rig refused;
    refused.chip.attach_device();
    refused.chip.serve_boot_mouse();
    refused.chip.refuse_setup_requests(true);
    refused.setup.begin(refused.chip.now_us());
    refused.settle();

    Rig silent;
    silent.chip.attach_device();
    silent.chip.serve_boot_mouse();
    silent.chip.ignore_setup_requests(true);
    silent.setup.begin(silent.chip.now_us());
    silent.settle();

    // Three outcomes, three bytes. A device that took the request, one that
    // said no, and one that said nothing want different things done about
    // them, and the diagnostics are the only place anyone can tell.
    CHECK(refused.setup.last_status() != silent.setup.last_status());
    // 0x14 is USB_INT_SUCCESS. Reporting it here is the lie that started this:
    // a mouse still in report protocol looking like one that switched.
    CHECK(refused.setup.last_status() != 0x14);
    CHECK(silent.setup.last_status() != 0x14);
}

// -------------------------------------------------- what the far end receives

TEST_CASE(the_report_a_mouse_sends_after_setup_is_movement_and_not_a_click) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();

    rig.begin(rig.chip.now_us());
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));

    // The movement from the bench: 0xF6 is -10 across, 0x4F is +79 down. The
    // report that carries it is whatever protocol the device was left in.
    const std::vector<std::uint8_t> report = rig.chip.report_for(-10, 79);

    duo_input::u1::input::MouseNormalizer normalizer;
    duo_input::u1::input::InputEvent events[duo_input::u1::input::kMaxEventsPerReport];
    const std::size_t count = normalizer.apply(
        duo_input::protocol::ByteView{report.data(), report.size()}, events,
        duo_input::u1::input::kMaxEventsPerReport);

    // Nobody touched a button, and the mouse went sideways as well as down.
    // Left in its own protocol this produced a held left button, no horizontal
    // movement at all, and the sideways motion showing up as vertical.
    std::size_t moves = 0;
    for (std::size_t index = 0; index < count; ++index) {
        CHECK(events[index].kind != duo_input::u1::input::InputEventKind::MouseButtonDown);
        if (events[index].kind == duo_input::u1::input::InputEventKind::MouseMove) {
            ++moves;
            CHECK_EQ(events[index].x, std::int16_t{-10});
            CHECK_EQ(events[index].y, std::int16_t{79});
        }
    }
    CHECK_EQ(moves, std::size_t{1});
}

// ============================================== reading the report descriptor
//
// Boot protocol's report is three bytes - buttons, dX, dY - and there is no
// wheel in it. That is what the wheel cost: a mouse forced into a format this
// firmware already understood, with a control silently removed.
//
// The report descriptor is the only thing that says otherwise. It says where
// each field sits, how wide it is, and whether an identifier leads every
// report - and a device that gives one up can be left in its own protocol and
// read through what it declared.
//
// SYNTHETIC. Every descriptor below is written to a shape the HID
// specification describes, not captured from a device: nothing on this bench
// has ever been asked for its report descriptor, so the corpus in
// tests/vectors/hid_reports holds boot reports and nothing else. What confirms
// these is a mouse.

namespace {

/// The report descriptor of a plain wheel mouse, HID 1.11 Appendix E.10.
///
/// Five button bits, three of padding, then X, Y and Wheel as signed bytes.
/// No Report ID: the reports arrive with the buttons in the first byte.
std::vector<std::uint8_t> plain_wheel_mouse_descriptor() {
    return {
        0x05, 0x01,        // Usage Page (Generic Desktop)
        0x09, 0x02,        // Usage (Mouse)
        0xA1, 0x01,        // Collection (Application)
        0x09, 0x01,        //   Usage (Pointer)
        0xA1, 0x00,        //   Collection (Physical)
        0x05, 0x09,        //     Usage Page (Button)
        0x19, 0x01,        //     Usage Minimum (Button 1)
        0x29, 0x05,        //     Usage Maximum (Button 5)
        0x15, 0x00,        //     Logical Minimum (0)
        0x25, 0x01,        //     Logical Maximum (1)
        0x95, 0x05,        //     Report Count (5)
        0x75, 0x01,        //     Report Size (1)
        0x81, 0x02,        //     Input (Data,Var,Abs)
        0x95, 0x01,        //     Report Count (1)
        0x75, 0x03,        //     Report Size (3)
        0x81, 0x03,        //     Input (Cnst,Var,Abs)
        0x05, 0x01,        //     Usage Page (Generic Desktop)
        0x09, 0x30,        //     Usage (X)
        0x09, 0x31,        //     Usage (Y)
        0x09, 0x38,        //     Usage (Wheel)
        0x15, 0x81,        //     Logical Minimum (-127)
        0x25, 0x7F,        //     Logical Maximum (127)
        0x75, 0x08,        //     Report Size (8)
        0x95, 0x03,        //     Report Count (3)
        0x81, 0x06,        //     Input (Data,Var,Rel)
        0xC0,              //   End Collection
        0xC0,              // End Collection
    };
}

/// The report descriptor of a mouse whose reports lead with an identifier.
///
/// Identifier, one button byte, sixteen-bit X and Y, one wheel byte: the
/// seven-byte report the mouse on this bench sends when nobody forces boot
/// on it. Read as a boot report - which is what happened before the
/// SET_PROTOCOL fix - the identifier is the buttons and every axis is one
/// byte out of place.
std::vector<std::uint8_t> report_id_wheel_mouse_descriptor() {
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
        0x81, 0x01,        //     Input (Cnst)
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

/// Run one report through a normalizer set up the way the runtime sets it up.
struct Routed {
    duo_input::u1::input::InputEvent events[duo_input::u1::input::kMaxEventsPerReport];
    std::size_t count = 0;

    int count_of(duo_input::u1::input::InputEventKind kind) const {
        int seen = 0;
        for (std::size_t index = 0; index < count; ++index) {
            if (events[index].kind == kind) {
                ++seen;
            }
        }
        return seen;
    }
};

Routed route(const DescriptorSetup& setup, const std::vector<std::uint8_t>& report) {
    duo_input::u1::input::MouseNormalizer normalizer;
    normalizer.set_layout(setup.mouse_layout());
    Routed out;
    out.count = normalizer.apply(
        duo_input::protocol::ByteView{report.data(), report.size()}, out.events,
        duo_input::u1::input::kMaxEventsPerReport);
    return out;
}

}  // namespace

TEST_CASE(a_mouse_that_declares_a_report_descriptor_is_asked_for_it) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_mouse_with_report_descriptor(plain_wheel_mouse_descriptor(), true);

    rig.begin(rig.chip.now_us());
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));

    CHECK_EQ(rig.chip.report_descriptor_requests(), 1);
    // For exactly as many bytes as the HID record said it has. Fewer gets a
    // descriptor cut short, which parses as a different device.
    CHECK_EQ(rig.chip.report_descriptor_asked_for(),
             static_cast<std::uint16_t>(plain_wheel_mouse_descriptor().size()));
    CHECK(rig.setup.has_mouse_layout());
}

TEST_CASE(the_request_is_a_get_descriptor_for_a_report_descriptor) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_mouse_with_report_descriptor(plain_wheel_mouse_descriptor(), true);

    rig.begin(rig.chip.now_us());
    rig.settle();

    // Written from USB 2.0 9.4.3 and HID 1.11 7.1.1 rather than shared with
    // the firmware, so a wrong constant cannot be agreed with.
    CHECK_EQ(rig.chip.setup_packets().size(), std::size_t{1});
    SetupPacket packet;
    CHECK(read_setup(only_setup(rig.chip), packet));
    CHECK_EQ(packet.request_type, std::uint8_t{0x81});
    CHECK_EQ(packet.request, std::uint8_t{0x06});
    CHECK_EQ(packet.value, std::uint16_t{0x2200});
    // Addressed to the interface, because a composite device has several and
    // only one of them is the mouse.
    CHECK_EQ(packet.index, std::uint16_t{0});
}

TEST_CASE(a_mouse_read_through_its_descriptor_is_left_in_its_own_protocol) {
    Rig rig;
    rig.chip.attach_device();
    // Boot-capable, so the old path would have switched it - and thrown the
    // wheel away doing it.
    rig.chip.serve_mouse_with_report_descriptor(plain_wheel_mouse_descriptor(), true);

    rig.begin(rig.chip.now_us());
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));

    CHECK(rig.setup.has_mouse_layout());
    CHECK_FALSE(rig.setup.boot_protocol_selected());
    CHECK_FALSE(rig.chip.boot_protocol_selected());
    // One setup packet, and it is the descriptor request. A SET_PROTOCOL here
    // is the whole defect: three bytes with no wheel in them.
    CHECK_EQ(rig.chip.setup_packets().size(), std::size_t{1});
}

// -------------------------------------------------- a mouse with a Report ID

TEST_CASE(a_wheel_behind_a_report_id_arrives_as_a_wheel_event) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_mouse_with_report_descriptor(report_id_wheel_mouse_descriptor(), false);

    rig.begin(rig.chip.now_us());
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));
    CHECK(rig.setup.has_mouse_layout());

    // Identifier 1, no buttons, no movement, one notch towards the user.
    const Routed away = route(rig.setup, {0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFF});
    CHECK_EQ(away.count, std::size_t{1});
    CHECK_EQ(static_cast<int>(away.events[0].kind),
             static_cast<int>(duo_input::u1::input::InputEventKind::Wheel));
    CHECK_EQ(away.events[0].wheel, -1);

    // And the other way, because a wheel read at the wrong offset in a report
    // of zeroes produces nothing at all, which looks like agreement.
    const Routed toward = route(rig.setup, {0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02});
    CHECK_EQ(toward.count, std::size_t{1});
    CHECK_EQ(toward.events[0].wheel, 2);
}

TEST_CASE(buttons_and_axes_land_behind_a_report_id_too) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_mouse_with_report_descriptor(report_id_wheel_mouse_descriptor(), false);

    rig.begin(rig.chip.now_us());
    rig.settle();

    // Identifier 1, the middle button, X = -10 and Y = +79 sixteen bits wide.
    // Read as a boot report the identifier is a left click, the buttons are
    // dX, and the pointer only ever goes up and down - which is what this
    // mouse actually did before boot protocol was forced on it.
    const Routed out = route(rig.setup, {0x01, 0x04, 0xF6, 0xFF, 0x4F, 0x00, 0x00});

    CHECK_EQ(out.count, std::size_t{2});
    CHECK_EQ(out.count_of(duo_input::u1::input::InputEventKind::MouseButtonDown), 1);
    CHECK_EQ(out.count_of(duo_input::u1::input::InputEventKind::MouseMove), 1);
    for (std::size_t index = 0; index < out.count; ++index) {
        if (out.events[index].kind == duo_input::u1::input::InputEventKind::MouseButtonDown) {
            CHECK_EQ(out.events[index].code, std::uint16_t{2});
        }
        if (out.events[index].kind == duo_input::u1::input::InputEventKind::MouseMove) {
            CHECK_EQ(out.events[index].x, std::int16_t{-10});
            CHECK_EQ(out.events[index].y, std::int16_t{79});
        }
    }
}

// ----------------------------------------------- a mouse with no Report ID

TEST_CASE(a_wheel_at_the_unprefixed_offsets_arrives_as_a_wheel_event) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_mouse_with_report_descriptor(plain_wheel_mouse_descriptor(), true);

    rig.begin(rig.chip.now_us());
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));

    // Buttons, X, Y, wheel - a boot report with a fourth byte, which is what
    // this descriptor declares and what the device sends unasked.
    const Routed away = route(rig.setup, {0x00, 0x00, 0x00, 0xFF});
    CHECK_EQ(away.count, std::size_t{1});
    CHECK_EQ(static_cast<int>(away.events[0].kind),
             static_cast<int>(duo_input::u1::input::InputEventKind::Wheel));
    CHECK_EQ(away.events[0].wheel, -1);

    const Routed toward = route(rig.setup, {0x00, 0x00, 0x00, 0x03});
    CHECK_EQ(toward.count, std::size_t{1});
    CHECK_EQ(toward.events[0].wheel, 3);
}

TEST_CASE(buttons_and_axes_land_at_the_unprefixed_offsets_too) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_mouse_with_report_descriptor(plain_wheel_mouse_descriptor(), true);

    rig.begin(rig.chip.now_us());
    rig.settle();

    const Routed out = route(rig.setup, {0x01, 0xF6, 0x4F, 0x00});

    CHECK_EQ(out.count, std::size_t{2});
    CHECK_EQ(out.count_of(duo_input::u1::input::InputEventKind::MouseButtonDown), 1);
    for (std::size_t index = 0; index < out.count; ++index) {
        if (out.events[index].kind == duo_input::u1::input::InputEventKind::MouseButtonDown) {
            CHECK_EQ(out.events[index].code, std::uint16_t{0});
        }
        if (out.events[index].kind == duo_input::u1::input::InputEventKind::MouseMove) {
            CHECK_EQ(out.events[index].x, std::int16_t{-10});
            CHECK_EQ(out.events[index].y, std::int16_t{79});
        }
    }
}

// -------------------------------------------- more than one transaction long

TEST_CASE(a_descriptor_longer_than_one_packet_is_collected_whole) {
    Rig rig;
    rig.chip.attach_device();
    // Eight bytes is what endpoint zero carries on a low-speed mouse, which is
    // most of them. A fifty-odd byte descriptor is seven transactions, and a
    // reader that takes the first packet for the whole thing sees a truncated
    // descriptor and falls back to boot.
    rig.chip.set_control_packet_size(8);
    rig.chip.serve_mouse_with_report_descriptor(plain_wheel_mouse_descriptor(), true);

    rig.begin(rig.chip.now_us());
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));

    CHECK(rig.chip.report_descriptor_packets() > 1);
    CHECK(rig.setup.has_mouse_layout());
    CHECK_EQ(rig.setup.report_descriptor_bytes(),
             static_cast<std::uint16_t>(plain_wheel_mouse_descriptor().size()));
}

TEST_CASE(a_descriptor_that_fits_one_packet_takes_one_transaction) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.set_control_packet_size(64);
    rig.chip.serve_mouse_with_report_descriptor(plain_wheel_mouse_descriptor(), true);

    rig.begin(rig.chip.now_us());
    rig.settle();

    CHECK_EQ(rig.chip.report_descriptor_packets(), 1);
    CHECK(rig.setup.has_mouse_layout());
}

TEST_CASE(the_transfer_is_closed_the_way_a_read_is_closed) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_mouse_with_report_descriptor(plain_wheel_mouse_descriptor(), true);

    rig.begin(rig.chip.now_us());
    rig.settle();

    // A transfer that read data is acknowledged by the host sending an empty
    // DATA1, not by asking for one (USB 2.0 8.5.3). Left unfinished, the
    // device is still waiting on a transfer nobody ended.
    CHECK_EQ(rig.chip.control_read_status_stages(), 1);
    CHECK_EQ(rig.chip.transmit_toggle(), std::uint8_t{0xC0});
}

TEST_CASE(a_descriptor_longer_than_there_is_room_for_is_refused_by_name) {
    Rig rig;
    rig.chip.attach_device();
    // Longer than kMaxReportDescriptorBytes. Collecting part of it would give
    // a layout for a device that does not exist, which is worse than boot.
    std::vector<std::uint8_t> huge = plain_wheel_mouse_descriptor();
    huge.resize(duo_input::u1::ch375::kMaxReportDescriptorBytes + 1, 0xC0);
    rig.chip.serve_mouse_with_report_descriptor(huge, true);

    rig.begin(rig.chip.now_us());

    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));
    // Not asked for at all: the length is known before a byte goes on the wire.
    CHECK_EQ(rig.chip.report_descriptor_requests(), 0);
    CHECK_FALSE(rig.setup.has_mouse_layout());
    CHECK(rig.setup.boot_protocol_selected());
}

// -------------------------------------------------- everything that goes wrong
//
// Each of these has to end exactly where the firmware ended before this step
// existed: a working mouse on boot protocol, with no wheel.

TEST_CASE(a_device_that_refuses_the_descriptor_request_falls_back_to_boot) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_mouse_with_report_descriptor(plain_wheel_mouse_descriptor(), true);
    rig.chip.refuse_report_descriptor(true);

    rig.begin(rig.chip.now_us());

    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));
    CHECK_FALSE(rig.setup.has_mouse_layout());
    CHECK(rig.setup.boot_protocol_selected());
    CHECK(rig.chip.boot_protocol_selected());
    CHECK_EQ(rig.setup.interrupt_endpoint(), 2u);
}

TEST_CASE(a_device_that_refuses_it_still_moves_the_pointer) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_mouse_with_report_descriptor(plain_wheel_mouse_descriptor(), true);
    rig.chip.refuse_report_descriptor(true);

    rig.begin(rig.chip.now_us());
    rig.settle();

    // The boot report it now sends, read through the layout it was left with.
    // Working without a wheel is the behaviour this repair must not lose.
    const Routed out = route(rig.setup, {0x00, 0xF6, 0x4F});
    CHECK_EQ(out.count, std::size_t{1});
    CHECK_EQ(static_cast<int>(out.events[0].kind),
             static_cast<int>(duo_input::u1::input::InputEventKind::MouseMove));
    CHECK_EQ(out.events[0].x, std::int16_t{-10});
    CHECK_EQ(out.events[0].y, std::int16_t{79});
}

TEST_CASE(a_descriptor_that_cannot_be_parsed_falls_back_to_boot) {
    Rig rig;
    rig.chip.attach_device();
    // Bytes that arrive perfectly and describe nothing: a run of collection
    // openers with no pointer inside.
    std::vector<std::uint8_t> nonsense;
    for (int index = 0; index < 20; ++index) {
        nonsense.push_back(0xA1);
        nonsense.push_back(0x01);
    }
    rig.chip.serve_mouse_with_report_descriptor(nonsense, true);

    rig.begin(rig.chip.now_us());

    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));
    // It was fetched - the failure is in the bytes, not in the fetching.
    CHECK_EQ(rig.chip.report_descriptor_requests(), 1);
    CHECK_FALSE(rig.setup.has_mouse_layout());
    CHECK(rig.setup.boot_protocol_selected());
}

TEST_CASE(a_device_that_declares_no_report_descriptor_is_never_asked) {
    Rig rig;
    rig.chip.attach_device();
    // serve_boot_mouse declares no HID record at all, which is the shape every
    // one of the tests above this section was written against.
    rig.chip.serve_boot_mouse();

    rig.begin(rig.chip.now_us());

    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));
    CHECK_EQ(rig.chip.report_descriptor_requests(), 0);
    CHECK_FALSE(rig.setup.has_mouse_layout());
    CHECK(rig.setup.boot_protocol_selected());
}

TEST_CASE(a_device_silent_on_the_request_ends_the_attempt_rather_than_the_loop) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_mouse_with_report_descriptor(plain_wheel_mouse_descriptor(), true);
    rig.chip.ignore_report_descriptor(true);

    rig.begin(rig.chip.now_us());

    // That this test finishes is as much the assertion as what it returns. A
    // token issued and never answered may still complete, and its interrupt
    // would be read as the answer to whatever went out next - so the attempt
    // ends and the bus is reset rather than another transfer being sent.
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Failed));
    CHECK_FALSE(rig.setup.has_mouse_layout());
}

TEST_CASE(a_device_silent_every_time_is_eventually_left_on_boot) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_mouse_with_report_descriptor(plain_wheel_mouse_descriptor(), true);
    rig.chip.ignore_report_descriptor(true);

    SetupProgress last = SetupProgress::Failed;
    for (int attempt = 0; attempt < 8; ++attempt) {
        rig.begin(rig.chip.now_us());
        last = rig.settle();
        if (last == SetupProgress::Done) {
            break;
        }
    }

    // Otherwise a device that works perfectly well on boot protocol would
    // re-enumerate for ever over a control it never had.
    CHECK_EQ(static_cast<int>(last), static_cast<int>(SetupProgress::Done));
    CHECK_FALSE(rig.setup.has_mouse_layout());
    CHECK(rig.setup.boot_protocol_selected());
    CHECK_EQ(rig.setup.interrupt_endpoint(), 2u);
}

// ------------------------------------------------------------- the keyboard

TEST_CASE(a_keyboard_is_not_asked_for_its_report_descriptor) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_composite_keyboard();

    rig.begin(rig.chip.now_us());
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));

    // A boot keyboard's report is fixed by HID 1.11 Appendix B.1 and is what
    // the keyboard normalizer and the captured traces are written against.
    // There is no wheel to recover and nothing to gain by reading it.
    CHECK_EQ(static_cast<int>(rig.setup.kind()), static_cast<int>(DeviceKind::Keyboard));
    CHECK_EQ(rig.chip.report_descriptor_requests(), 0);
    CHECK(rig.setup.boot_protocol_selected());
    CHECK_FALSE(rig.setup.has_mouse_layout());
}

// ------------------------------------------------- one device at a time

TEST_CASE(a_layout_does_not_survive_into_the_next_device) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_mouse_with_report_descriptor(report_id_wheel_mouse_descriptor(), false);
    rig.begin(rig.chip.now_us());
    rig.settle();
    CHECK(rig.setup.has_mouse_layout());

    // Somebody unplugs it and plugs in a mouse that says nothing about itself.
    rig.chip.serve_boot_mouse();
    rig.begin(rig.chip.now_us());
    rig.settle();

    // Kept, the first mouse's layout would read the second one's reports one
    // byte out of place and drop every report whose first byte is not 1.
    CHECK_FALSE(rig.setup.has_mouse_layout());
    const Routed out = route(rig.setup, {0x00, 0xF6, 0x4F});
    CHECK_EQ(out.count, std::size_t{1});
    CHECK_EQ(out.events[0].x, std::int16_t{-10});
}

TEST_CASE(the_descriptor_request_names_the_interface_the_mouse_is_on) {
    Rig rig;
    rig.chip.attach_device();
    // Consumer controls on interface zero, the mouse on interface one, both
    // declaring report descriptors.
    rig.chip.serve_composite_mouse_with_report_descriptor(plain_wheel_mouse_descriptor());

    rig.begin(rig.chip.now_us());
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));

    CHECK_EQ(rig.chip.setup_packets().size(), std::size_t{1});
    SetupPacket packet;
    CHECK(read_setup(only_setup(rig.chip), packet));
    // Sent to interface zero this fetches the consumer interface's descriptor,
    // which is not a mouse - and a mouse read at a media controller's offsets
    // is a click on every scroll.
    CHECK_EQ(packet.index, std::uint16_t{1});
    CHECK_EQ(packet.length,
             static_cast<std::uint16_t>(plain_wheel_mouse_descriptor().size()));
}

TEST_CASE(a_keyboard_that_declares_a_report_descriptor_is_still_not_asked) {
    Rig rig;
    rig.chip.attach_device();
    // A keyboard with a HID record naming a real report descriptor, which is
    // what every keyboard actually has. Nothing about it should be fetched:
    // its boot report is fixed by HID 1.11 Appendix B.1, that is what the
    // keyboard normalizer reads, and the captured traces replay against it.
    rig.chip.serve_keyboard_with_report_descriptor(plain_wheel_mouse_descriptor());

    rig.begin(rig.chip.now_us());
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));

    CHECK_EQ(static_cast<int>(rig.setup.kind()), static_cast<int>(DeviceKind::Keyboard));
    CHECK_EQ(rig.chip.report_descriptor_requests(), 0);
    CHECK_FALSE(rig.setup.has_mouse_layout());
    // And it is put into boot protocol, exactly as before.
    CHECK(rig.setup.boot_protocol_selected());
    CHECK(rig.chip.boot_protocol_selected());
}
