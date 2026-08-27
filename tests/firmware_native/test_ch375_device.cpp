// One CH375 and the device plugged into it, as a state machine.
//
// The chip is asynchronous and the firmware is not allowed to wait for it.
// Everything here therefore happens across ticks: each call does a bounded
// piece of work and returns, so a keyboard that stops answering costs one
// device its input and nothing else. Two of these run side by side and must
// not be able to interfere.
//
// The states are the honest ones. A device is Absent until the chip says
// otherwise; Resetting while the USB bus is held down; HostMode once the chip
// is generating frames; Enumerating while the device is being configured;
// Ready when reports can be read; RecoverWait after a failure, counting down
// to another attempt; Fault only when retrying has stopped being worth it.

#include "ch375/device.hpp"
#include "fakes/scripted_ch375.hpp"
#include "test_support.hpp"

using duo_input::u1::ch375::Ch375Device;
using duo_input::u1::ch375::Ch375Event;
using duo_input::u1::ch375::Ch375EventKind;
using duo_input::u1::ch375::Ch375State;
using duo_input::u1::ch375::Ch375Transport;
using duo_input::u1::ch375::kRecoverDelayUs;
using duo_input::u1::ch375::testing::FakeCh375Chip;
using duo_input::u1::ch375::testing::FakeDeviceSetup;

namespace {

/// A chip, a transport and a device, wired together the way firmware does.
struct Rig {
    FakeCh375Chip chip;
    FakeDeviceSetup setup;
    Ch375Transport transport{chip};
    Ch375Device device{transport, setup};

    /// Run the machine for a while, letting time pass between ticks.
    void run(std::uint32_t duration_us, std::uint32_t step_us = 100) {
        const std::uint32_t until = chip.now_us() + duration_us;
        while (static_cast<std::int32_t>(chip.now_us() - until) < 0) {
            device.tick(chip.now_us());
            chip.advance(step_us);
        }
        device.tick(chip.now_us());
    }

    /// Take every event the device has produced, keeping the last of a kind.
    int count(Ch375EventKind kind) {
        int seen = 0;
        Ch375Event event;
        while (device.take_event(event)) {
            if (event.kind == kind) {
                ++seen;
            }
        }
        return seen;
    }
};

/// Bring a device all the way up, which several tests need before they start.
void bring_up(Rig& rig) {
    rig.chip.attach_device();
    rig.run(200000);
}

}  // namespace

// ---------------------------------------------------------------- at rest

TEST_CASE(a_device_starts_absent) {
    Rig rig;

    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::Absent));
}

TEST_CASE(nothing_happens_while_nothing_is_plugged_in) {
    Rig rig;

    rig.run(500000);

    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::Absent));
    CHECK_EQ(rig.count(Ch375EventKind::Attached), 0);
}

TEST_CASE(the_chip_is_put_into_host_mode_before_anything_is_plugged_in) {
    Rig rig;

    rig.run(50000);

    // Mode 5 - enabled host, no frames - is where the datasheet says to wait.
    // Without it the chip never reports a device arriving and the firmware
    // would have to poll for one forever.
    CHECK_EQ(static_cast<int>(rig.chip.mode()),
             static_cast<int>(duo_input::u1::ch375::UsbMode::HostNoSof));
}

// ------------------------------------------------------------- plugging in

TEST_CASE(plugging_a_device_in_is_noticed) {
    Rig rig;
    rig.run(50000);

    rig.chip.attach_device();
    rig.run(50000);

    CHECK(rig.count(Ch375EventKind::Attached) >= 1);
}

TEST_CASE(a_device_that_is_plugged_in_reaches_ready) {
    Rig rig;

    bring_up(rig);

    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::Ready));
}

TEST_CASE(the_usb_bus_is_reset_before_the_device_is_configured) {
    Rig rig;

    bring_up(rig);

    // DS1 5.9 is explicit: enter mode 7 after the device is plugged, then
    // switch to mode 6. Skipping the reset leaves some devices in whatever
    // state they were unplugged in.
    CHECK(rig.chip.saw_bus_reset());
}

TEST_CASE(the_chip_generates_frames_once_a_device_is_up) {
    Rig rig;

    bring_up(rig);

    // Mode 6 - a device that is never sent a frame stops answering.
    CHECK_EQ(static_cast<int>(rig.chip.mode()),
             static_cast<int>(duo_input::u1::ch375::UsbMode::HostWithSof));
}

TEST_CASE(the_bus_reset_is_not_left_holding_the_bus_down) {
    Rig rig;

    bring_up(rig);

    // Mode 7 holds the bus in reset until the mode changes - DS1 5.9. Leaving
    // it there is indistinguishable from a dead port.
    CHECK(static_cast<int>(rig.chip.mode()) != static_cast<int>(duo_input::u1::ch375::UsbMode::HostReset));
}

TEST_CASE(reaching_ready_is_announced_once) {
    Rig rig;

    bring_up(rig);

    CHECK_EQ(rig.count(Ch375EventKind::Ready), 1);
}

// ------------------------------------------------------------ unplugging

TEST_CASE(unplugging_a_device_returns_to_absent) {
    Rig rig;
    bring_up(rig);

    rig.chip.detach_device();
    rig.run(50000);

    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::Absent));
}

TEST_CASE(unplugging_is_announced) {
    Rig rig;
    bring_up(rig);
    rig.count(Ch375EventKind::Ready);  // drain

    rig.chip.detach_device();
    rig.run(50000);

    // Whoever is holding keys down on the far side has to be told, and told
    // once. This is the event that releases them.
    CHECK_EQ(rig.count(Ch375EventKind::Detached), 1);
}

TEST_CASE(a_device_plugged_back_in_comes_up_again) {
    Rig rig;
    bring_up(rig);
    rig.chip.detach_device();
    rig.run(50000);

    rig.chip.attach_device();
    rig.run(200000);

    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::Ready));
}

// ------------------------------------------------------------- misbehaving

TEST_CASE(a_chip_that_answers_nonsense_does_not_reach_ready) {
    Rig rig;
    rig.chip.answer_garbage(true);

    rig.chip.attach_device();
    rig.run(200000);

    CHECK(static_cast<int>(rig.device.state()) != static_cast<int>(Ch375State::Ready));
}

TEST_CASE(a_chip_that_stops_answering_does_not_hang_the_tick) {
    Rig rig;
    rig.chip.go_silent(true);

    // If any transition waited for a reply that is not coming, this test would
    // not finish. That it finishes is the assertion.
    rig.chip.attach_device();
    rig.run(200000);

    CHECK(static_cast<int>(rig.device.state()) != static_cast<int>(Ch375State::Ready));
}

TEST_CASE(a_failure_waits_before_trying_again) {
    Rig rig;
    rig.chip.answer_garbage(true);
    rig.chip.attach_device();
    rig.run(50000);

    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::RecoverWait));
}

TEST_CASE(the_wait_before_retrying_is_the_one_the_plan_names) {
    CHECK_EQ(kRecoverDelayUs, 1000000u);
}

TEST_CASE(a_retry_does_not_start_early) {
    Rig rig;
    rig.chip.answer_garbage(true);
    rig.chip.attach_device();
    rig.run(50000);
    const std::uint32_t attempts = rig.chip.mode_set_count();

    rig.run(kRecoverDelayUs / 2);

    // Retrying flat out would hammer a chip that is already unhappy and fill
    // the loop with work that cannot succeed.
    CHECK_EQ(rig.chip.mode_set_count(), attempts);
}

TEST_CASE(a_retry_does_start_once_the_wait_is_over) {
    Rig rig;
    rig.chip.answer_garbage(true);
    rig.chip.attach_device();
    rig.run(50000);
    const std::uint32_t attempts = rig.chip.mode_set_count();

    rig.run(kRecoverDelayUs + 100000);

    CHECK(rig.chip.mode_set_count() > attempts);
}

TEST_CASE(a_chip_that_recovers_reaches_ready_after_the_wait) {
    Rig rig;
    rig.chip.answer_garbage(true);
    rig.chip.attach_device();
    rig.run(50000);

    rig.chip.answer_garbage(false);
    rig.run(kRecoverDelayUs + 300000);

    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::Ready));
}

TEST_CASE(a_controller_that_goes_quiet_with_a_device_up_releases_it) {
    Rig rig;
    bring_up(rig);
    rig.count(Ch375EventKind::Ready);

    rig.chip.go_silent(true);
    rig.run(500000);

    // This is the dangerous one. Nothing can be read from the device any more
    // and its disconnection can never be noticed, so a key held at that moment
    // would be held forever on the far side. It has to be declared gone.
    CHECK_EQ(rig.count(Ch375EventKind::Detached), 1);
    CHECK(static_cast<int>(rig.device.state()) != static_cast<int>(Ch375State::Ready));
}

TEST_CASE(a_device_unplugged_while_its_controller_recovers_leaves_nothing_claimed) {
    Rig rig;
    rig.chip.answer_garbage(true);
    rig.chip.attach_device();
    rig.run(50000);

    rig.chip.detach_device();
    rig.run(kRecoverDelayUs + 100000);

    // The controller never answered, so nothing was ever announced as attached
    // and there is nothing to release. What must not happen is the device
    // being reported ready on the strength of a chip that has said nothing.
    CHECK(static_cast<int>(rig.device.state()) != static_cast<int>(Ch375State::Ready));
    CHECK_EQ(rig.count(Ch375EventKind::Ready), 0);
}

// ---------------------------------------------------------------- reports

TEST_CASE(a_report_from_the_device_is_handed_over) {
    Rig rig;
    bring_up(rig);
    rig.count(Ch375EventKind::Ready);

    const std::uint8_t report[4] = {0x02, 0x00, 0x04, 0x00};
    rig.chip.queue_report(report, sizeof(report));
    rig.run(50000);

    Ch375Event event;
    bool found = false;
    while (rig.device.take_event(event)) {
        if (event.kind == Ch375EventKind::Report) {
            found = true;
            CHECK_EQ(event.report_size, 4u);
            CHECK_EQ(event.report[0], 0x02u);
            CHECK_EQ(event.report[2], 0x04u);
        }
    }
    CHECK(found);
}

TEST_CASE(no_report_is_invented_when_the_device_has_nothing_to_say) {
    Rig rig;
    bring_up(rig);
    rig.count(Ch375EventKind::Ready);

    rig.run(200000);

    CHECK_EQ(rig.count(Ch375EventKind::Report), 0);
}

TEST_CASE(reports_are_not_read_before_the_device_is_ready) {
    Rig rig;
    const std::uint8_t report[2] = {0x11, 0x22};
    rig.chip.queue_report(report, sizeof(report));

    rig.run(20000);

    // Nothing is configured yet, so anything read from the endpoint belongs to
    // no device and could be any bytes at all.
    CHECK_EQ(rig.count(Ch375EventKind::Report), 0);
}

// --------------------------------------------------------- asked to redo it

TEST_CASE(re_enumeration_can_be_requested) {
    Rig rig;
    bring_up(rig);
    const std::uint32_t resets = rig.chip.reset_count();

    rig.device.request_reenumeration();
    rig.run(300000);

    CHECK(rig.chip.reset_count() > resets);
    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::Ready));
}

TEST_CASE(re_enumeration_when_nothing_is_plugged_in_changes_nothing) {
    Rig rig;
    rig.run(50000);

    rig.device.request_reenumeration();
    rig.run(50000);

    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::Absent));
}

// ------------------------------------------------------------ independence

TEST_CASE(two_devices_do_not_interfere) {
    Rig keyboard;
    Rig mouse;

    keyboard.chip.attach_device();
    keyboard.run(200000);
    mouse.chip.go_silent(true);
    mouse.chip.attach_device();
    mouse.run(200000);

    // The mouse's chip is dead. The keyboard must not care - this is the whole
    // reason the two are separate state machines rather than one.
    CHECK_EQ(static_cast<int>(keyboard.device.state()), static_cast<int>(Ch375State::Ready));
    CHECK(static_cast<int>(mouse.device.state()) != static_cast<int>(Ch375State::Ready));
}

// --------------------------------------------------------- bounded per tick

TEST_CASE(one_tick_does_a_bounded_amount_of_work) {
    Rig rig;
    rig.chip.attach_device();

    // However much there is to do, a single tick must not run away with the
    // loop: U1 also has to service USB, the link to U2 and the watchdog.
    for (int index = 0; index < 50; ++index) {
        rig.chip.reset_command_count();
        rig.device.tick(rig.chip.now_us());
        CHECK(rig.chip.command_count() <= 8u);
        rig.chip.advance(1000);
    }
}


// ------------------------------------- a disconnect this firmware caused

TEST_CASE(a_disconnect_reported_during_our_own_bus_reset_is_not_an_unplug) {
    // Holding the bus in reset is how a device is brought up (DS1 5.9), and
    // an attached device looks gone to the chip while that lasts. Taking that
    // at face value means announcing a detach, going back to Absent, seeing
    // the device again, resetting the bus again - which on the bench was a
    // device attaching and detaching six times over and never coming up.
    Rig rig;
    rig.chip.report_disconnect_on_reset(true);

    rig.chip.attach_device();
    rig.run(300000);

    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::Ready));
}

TEST_CASE(a_real_unplug_after_the_reset_is_still_an_unplug) {
    // The exemption is narrow on purpose: only while this code is the one
    // holding the bus down. Any later, and it is somebody pulling a cable.
    Rig rig;
    rig.chip.report_disconnect_on_reset(true);
    bring_up(rig);
    rig.count(Ch375EventKind::Ready);

    rig.chip.detach_device();
    rig.run(50000);

    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::Absent));
    CHECK_EQ(rig.count(Ch375EventKind::Detached), 1);
}
