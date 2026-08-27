// Getting a freshly attached device ready to talk.
//
// The CH375 will do the standard opening moves by itself: AUTO_SETUP is
// GET_DESCR, SET_ADDRESS and SET_CONFIGURATION in one command (DS2 1.13). That
// is enough for the ordinary wired keyboard and mouse this device promises,
// and it is deliberately all that is done here - reading descriptors properly,
// so an endpoint is known rather than assumed, is the next task in the plan.
//
// What matters at this layer is that it cannot hang. The chip answers by
// raising an interrupt, and an interrupt that never comes has to end the
// attempt rather than the loop: U1 services USB, the link to U2 and a watchdog
// on the same pass, and none of them can wait for a device that is not there.

#include "ch375/enumerator.hpp"
#include "fakes/scripted_ch375.hpp"
#include "test_support.hpp"

using duo_input::u1::ch375::AutoSetupEnumerator;
using duo_input::u1::ch375::Ch375Transport;
using duo_input::u1::ch375::kSetupTimeoutUs;
using duo_input::u1::ch375::SetupProgress;
using duo_input::u1::ch375::testing::FakeCh375Chip;

namespace {

struct Rig {
    FakeCh375Chip chip;
    Ch375Transport transport{chip};
    AutoSetupEnumerator enumerator{transport};

    /// Poll until it stops saying Busy, or until far past any real deadline.
    SetupProgress settle() {
        for (int pass = 0; pass < 10000; ++pass) {
            const SetupProgress progress = enumerator.poll(chip.now_us());
            if (progress != SetupProgress::Busy) {
                return progress;
            }
            chip.advance(100);
        }
        return SetupProgress::Busy;
    }
};

}  // namespace

TEST_CASE(a_device_that_configures_is_reported_done) {
    Rig rig;
    rig.chip.attach_device();

    rig.enumerator.begin(rig.chip.now_us());

    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));
}

TEST_CASE(the_chip_is_asked_to_configure_the_device) {
    Rig rig;
    rig.chip.attach_device();

    rig.enumerator.begin(rig.chip.now_us());
    rig.settle();

    CHECK(rig.chip.saw_auto_setup());
}

TEST_CASE(configuring_takes_more_than_one_pass) {
    Rig rig;
    rig.chip.attach_device();
    rig.enumerator.begin(rig.chip.now_us());

    // Several control transfers happen inside that one command. A caller that
    // only worked when it finished instantly would not survive meeting a real
    // device.
    CHECK_EQ(static_cast<int>(rig.enumerator.poll(rig.chip.now_us())),
             static_cast<int>(SetupProgress::Busy));
}

TEST_CASE(a_chip_that_refuses_is_a_failure_not_a_wait) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.fail_auto_setup(true);

    rig.enumerator.begin(rig.chip.now_us());

    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Failed));
}

TEST_CASE(a_chip_that_never_answers_gives_up) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.go_silent(true);

    rig.enumerator.begin(rig.chip.now_us());

    // The assertion is as much that this test finishes as that it fails.
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Failed));
}

TEST_CASE(giving_up_takes_the_time_it_says_it_does) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.go_silent(true);
    const std::uint32_t started = rig.chip.now_us();

    rig.enumerator.begin(started);
    rig.settle();

    CHECK(rig.chip.now_us() - started >= kSetupTimeoutUs);
}

TEST_CASE(the_timeout_leaves_room_for_a_real_device) {
    // Several control transfers at full speed take single-digit milliseconds.
    // Long enough not to abandon a slow one, short enough that a dead port
    // costs a fraction of a second rather than a noticeable pause.
    CHECK(kSetupTimeoutUs >= 50000u);
    CHECK(kSetupTimeoutUs <= 500000u);
}

TEST_CASE(polling_without_beginning_is_a_failure) {
    Rig rig;

    // Not Busy: a caller that never started would otherwise wait forever on
    // something that was never going to happen.
    CHECK_EQ(static_cast<int>(rig.enumerator.poll(rig.chip.now_us())),
             static_cast<int>(SetupProgress::Failed));
}

TEST_CASE(a_second_attempt_starts_over) {
    Rig rig;
    rig.chip.attach_device();
    rig.chip.fail_auto_setup(true);
    rig.enumerator.begin(rig.chip.now_us());
    rig.settle();

    rig.chip.fail_auto_setup(false);
    rig.enumerator.begin(rig.chip.now_us());

    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));
}

TEST_CASE(the_endpoint_is_the_one_ordinary_devices_use) {
    Rig rig;
    rig.chip.attach_device();
    rig.enumerator.begin(rig.chip.now_us());
    rig.settle();

    // Assumed, not discovered - AUTO_SETUP does not report it. Nearly every
    // wired keyboard and mouse puts its interrupt IN endpoint here, and the
    // next task in the plan reads the descriptors instead of assuming.
    CHECK_EQ(rig.enumerator.interrupt_endpoint(), 1u);
}
