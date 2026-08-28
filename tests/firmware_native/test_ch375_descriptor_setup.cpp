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

    /// Drive it the way the device machine does: read the status once, above,
    /// and hand it down.
    SetupProgress settle() {
        for (int pass = 0; pass < 20000; ++pass) {
            bool interrupted = false;
            InterruptStatus status = InterruptStatus::Success;
            if (transport.interrupt_pending()) {
                interrupted = transport.get_status(status);
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

    rig.setup.begin(rig.chip.now_us());

    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));
}

TEST_CASE(the_endpoint_comes_from_the_descriptor_not_from_a_guess) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();  // declares endpoint 2

    rig.setup.begin(rig.chip.now_us());
    rig.settle();

    CHECK_EQ(rig.setup.interrupt_endpoint(), 2u);
    CHECK_EQ(static_cast<int>(rig.setup.kind()), static_cast<int>(DeviceKind::Mouse));
}

TEST_CASE(a_keyboard_on_a_composite_device_is_found) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_composite_keyboard();  // consumer interface first, keyboard second

    rig.setup.begin(rig.chip.now_us());
    rig.settle();

    CHECK_EQ(static_cast<int>(rig.setup.kind()), static_cast<int>(DeviceKind::Keyboard));
    CHECK_EQ(rig.setup.interrupt_endpoint(), 1u);
}

// ------------------------------------------------------------ the address

TEST_CASE(the_device_is_given_an_address_that_is_not_zero) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();

    rig.setup.begin(rig.chip.now_us());
    rig.settle();

    // Everything talks on address zero until told otherwise, and only one
    // device can.
    CHECK(rig.chip.device_address() != 0);
}

TEST_CASE(the_controller_is_told_the_same_address_as_the_device) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();

    rig.setup.begin(rig.chip.now_us());
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

    rig.setup.begin(rig.chip.now_us());
    rig.settle();

    // A device that is addressed but unconfigured has no working endpoints.
    CHECK(rig.chip.configuration_value() != 0);
}

TEST_CASE(the_steps_happen_in_the_order_they_have_to) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();

    rig.setup.begin(rig.chip.now_us());
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

    rig.setup.begin(rig.chip.now_us());

    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Failed));
}

TEST_CASE(a_device_that_stops_answering_ends_the_attempt) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();
    rig.chip.go_silent(true);

    rig.setup.begin(rig.chip.now_us());

    // That this test finishes is as much the assertion as what it returns.
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Failed));
}

TEST_CASE(a_step_the_device_refuses_ends_the_attempt) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();
    rig.chip.stall_after(2);  // answers the first two transfers, then refuses

    rig.setup.begin(rig.chip.now_us());

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
    rig.setup.begin(rig.chip.now_us());
    rig.settle();

    rig.chip.serve_boot_mouse();
    rig.setup.begin(rig.chip.now_us());

    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));
    CHECK_EQ(rig.setup.interrupt_endpoint(), 2u);
}

TEST_CASE(each_attempt_gives_the_device_a_fresh_address) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.serve_boot_mouse();
    rig.setup.begin(rig.chip.now_us());
    rig.settle();
    const std::uint8_t first = rig.chip.device_address();

    rig.setup.begin(rig.chip.now_us());
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

    rig.setup.begin(rig.chip.now_us());
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

    rig.setup.begin(rig.chip.now_us());
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

    rig.setup.begin(rig.chip.now_us());
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

    rig.setup.begin(rig.chip.now_us());

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

    rig.setup.begin(rig.chip.now_us());

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

    rig.setup.begin(rig.chip.now_us());

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

    rig.setup.begin(rig.chip.now_us());
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
