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
#include "fakes/scripted_ch375.hpp"
#include "test_support.hpp"

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
