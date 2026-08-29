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

#include "ch375/descriptor_setup.hpp"
#include "ch375/device.hpp"
#include "fakes/scripted_ch375.hpp"
#include "test_support.hpp"

using duo_input::u1::ch375::Ch375Device;
using duo_input::u1::ch375::Ch375Event;
using duo_input::u1::ch375::Ch375EventKind;
using duo_input::u1::ch375::Ch375State;
using duo_input::u1::ch375::Ch375Transport;
using duo_input::u1::ch375::DescriptorSetup;
using duo_input::u1::ch375::kDeviceLostUs;
using duo_input::u1::ch375::kQuietRetriesBeforeTeardown;
using duo_input::u1::ch375::kRecoverDelayUs;
using duo_input::u1::ch375::kPresenceRecheckUs;
using duo_input::u1::ch375::kReportPollUs;
using duo_input::u1::ch375::PendingReply;
using duo_input::u1::ch375::testing::FakeCh375Chip;
using duo_input::u1::ch375::testing::FakeDeviceSetup;

namespace {

/// Run a rig for a while, letting time pass between ticks.
template <typename R>
void run_for(R& rig, std::uint32_t duration_us, std::uint32_t step_us) {
    const std::uint32_t until = rig.chip.now_us() + duration_us;
    while (static_cast<std::int32_t>(rig.chip.now_us() - until) < 0) {
        rig.device.tick(rig.chip.now_us());
        rig.chip.advance(step_us);
    }
    rig.device.tick(rig.chip.now_us());
}

/// The longest a single tick took, measured on the fake's own clock.
///
/// That clock moves only when the code under test polls a port with nothing on
/// it, so this measures the one thing that matters here: time spent waiting for
/// a chip, which Core 1 takes out of the other channel's poll window.
template <typename R>
std::uint32_t worst_tick_of(R& rig, int ticks, std::uint32_t step_us) {
    std::uint32_t worst = 0;
    for (int index = 0; index < ticks; ++index) {
        const std::uint32_t before = rig.chip.now_us();
        rig.device.tick(rig.chip.now_us());
        const std::uint32_t spent = rig.chip.now_us() - before;
        if (spent > worst) {
            worst = spent;
        }
        rig.chip.advance(step_us);
    }
    return worst;
}

/// A chip, a transport and a device, wired together the way firmware does.
struct Rig {
    FakeCh375Chip chip;
    FakeDeviceSetup setup;
    Ch375Transport transport{chip};
    Ch375Device device{transport, setup};

    /// Configuring a device reads blocks over the port, and whether those
    /// complete is what proves a rate. Wired here rather than per test, so
    /// every lifecycle test runs against a setup that actually uses the wire.
    Rig() { setup.reads_descriptors_through(transport); }

    /// Run the machine for a while, letting time pass between ticks.
    void run(std::uint32_t duration_us, std::uint32_t step_us = 100) {
        run_for(*this, duration_us, step_us);
    }

    /// The longest a single tick took, measured on the fake's own clock.
    std::uint32_t worst_tick(int ticks, std::uint32_t step_us = 1000) {
        return worst_tick_of(*this, ticks, step_us);
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

    rig.run(200000);

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
    rig.run(300000);

    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::RecoverWait));
}

TEST_CASE(the_wait_before_retrying_is_the_one_the_plan_names) {
    CHECK_EQ(kRecoverDelayUs, 1000000u);
}

TEST_CASE(a_retry_does_not_start_early) {
    Rig rig;
    rig.chip.answer_garbage(true);
    rig.chip.attach_device();
    rig.run(300000);
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
    rig.run(300000);
    // Counted by the probe, not by the mode command. A chip answering nonsense
    // never gets as far as a mode command now - that is the point of asking
    // first - so the mode count would say no attempt was made when one was.
    const std::uint32_t attempts = rig.chip.check_exist_count();

    rig.run(kRecoverDelayUs + 100000);

    CHECK(rig.chip.check_exist_count() > attempts);
}

TEST_CASE(a_chip_answering_nonsense_is_never_sent_a_mode_command) {
    // The command that wedges a controller for good is one it half-hears
    // while it is out of step: CH375 reads commands positionally, so a
    // swallowed byte takes the next command with it. A port that cannot pass
    // CHECK_EXIST has nothing to say worth risking that on.
    Rig rig;
    rig.chip.answer_garbage(true);
    rig.chip.attach_device();

    rig.run(500000);

    CHECK_EQ(rig.chip.mode_set_count(), 0u);
    CHECK(rig.chip.check_exist_count() > 0u);
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
    // A second of silence buys a re-arm, not a teardown - a polled endpoint
    // answers even when it has nothing to say, but one lost transaction is
    // commoner than a vanished device and costs four bytes to recover. Only
    // after kQuietRetriesBeforeTeardown of those is the device given up on,
    // so the window here is that many seconds and a margin.
    rig.run(kDeviceLostUs * (kQuietRetriesBeforeTeardown + 2));

    // This is the dangerous one. Nothing can be read from the device any more
    // and its disconnection can never be noticed, so a key held at that moment
    // would be held forever on the far side. It has to be declared gone.
    CHECK_EQ(rig.count(Ch375EventKind::Detached), 1);
    CHECK(static_cast<int>(rig.device.state()) != static_cast<int>(Ch375State::Ready));
}

TEST_CASE(a_moment_of_silence_re_arms_the_endpoint_instead_of_the_whole_bus) {
    // Tearing the chip and the bus down re-enumerates the peripheral: its
    // lights go out and come back, and everything held on it is released and
    // re-acquired. Doing that for one lost transaction is a fault the operator
    // can see, and on this bench it was the one they noticed.
    Rig rig;
    bring_up(rig);
    rig.count(Ch375EventKind::Ready);

    rig.chip.go_silent(true);
    rig.run(kDeviceLostUs + 200000);

    CHECK_EQ(rig.count(Ch375EventKind::Detached), 0);
    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::Ready));
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


// ------------------------------------------- letting the device wake up

TEST_CASE(a_device_is_given_time_to_recover_from_the_bus_reset) {
    // USB gives a device up to 10 ms after a reset before it has to answer
    // anything. Addressing it sooner is asking a question of something that is
    // still coming round, and the answer is silence - which the controller
    // reports as the device having gone, so the whole sequence starts again.
    Rig rig;
    rig.chip.attach_device();

    // Run only as far as the bus reset can have finished.
    rig.run(duo_input::u1::ch375::kBusResetHoldUs + 2000);

    CHECK(!rig.setup.was_begun());
}

TEST_CASE(the_recovery_pause_is_long_enough_to_be_one) {
    CHECK(duo_input::u1::ch375::kBusSettleUs >= 10000u);
}

TEST_CASE(the_device_still_comes_up_after_the_pause) {
    Rig rig;

    bring_up(rig);

    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::Ready));
}


// -------------------------------------------------- how fast the bus runs

TEST_CASE(a_low_speed_device_is_recognised) {
    // Nearly every wired mouse is a 1.5 Mbps device. Addressing one at the
    // 12 Mbps the chip defaults to gets no answer at all, and a controller
    // reports something that does not answer as gone - which on the bench was
    // a device connecting and disconnecting without end.
    Rig rig;
    rig.chip.set_low_speed(true);

    bring_up(rig);

    CHECK(rig.device.device_is_low_speed());
}

TEST_CASE(the_bus_is_set_to_the_speed_the_device_answered_at) {
    Rig rig;
    rig.chip.set_low_speed(true);

    bring_up(rig);

    CHECK_EQ(static_cast<int>(rig.chip.bus_speed()),
             static_cast<int>(duo_input::u1::ch375::UsbSpeed::Low1_5Mbps));
}

TEST_CASE(a_full_speed_device_is_left_at_full_speed) {
    Rig rig;
    rig.chip.set_low_speed(false);

    bring_up(rig);

    CHECK_EQ(static_cast<int>(rig.chip.bus_speed()),
             static_cast<int>(duo_input::u1::ch375::UsbSpeed::Full12Mbps));
}

TEST_CASE(the_speed_is_set_after_the_working_mode_not_before) {
    // Setting a working mode puts the bus back to 12 Mbps (DS2 1.1), so a
    // speed chosen before it is silently undone.
    Rig rig;
    rig.chip.set_low_speed(true);

    bring_up(rig);

    CHECK(rig.chip.speed_set_after_last_mode());
}


// --------------------------------------------- going back to wait properly

TEST_CASE(losing_a_device_returns_the_chip_to_the_waiting_mode) {
    // Mode 5 is where DS1 5.9 says to wait, and DS2 1.2 says it is the only
    // mode in which the chip can be asked how fast an attached device is. Left
    // in mode 6 after a device goes, that question comes back as nonsense - a
    // low-speed mouse read as full speed, addressed at eight times its rate,
    // and reported gone. Which is the loop this device sat in.
    Rig rig;
    bring_up(rig);

    rig.chip.detach_device();
    rig.run(50000);

    CHECK_EQ(static_cast<int>(rig.chip.mode()),
             static_cast<int>(duo_input::u1::ch375::UsbMode::HostNoSof));
}

TEST_CASE(a_device_that_comes_back_is_asked_its_speed_again) {
    Rig rig;
    rig.chip.set_low_speed(true);
    bring_up(rig);
    rig.chip.detach_device();
    rig.run(50000);

    rig.chip.attach_device();
    rig.run(300000);

    CHECK(rig.device.device_is_low_speed());
    CHECK_EQ(static_cast<int>(rig.chip.bus_speed()),
             static_cast<int>(duo_input::u1::ch375::UsbSpeed::Low1_5Mbps));
}


TEST_CASE(a_disconnect_while_the_bus_is_still_coming_up_is_not_an_unplug) {
    // The exemption covers the reset and the recovery pause after it, because
    // the chip's detection is not trustworthy until frames are flowing again -
    // on the bench a device was reported gone during that pause every single
    // time, and enumeration was never once reached.
    Rig rig;
    rig.chip.report_disconnect_on_reset(true);
    rig.chip.report_disconnect_while_settling(true);

    rig.chip.attach_device();
    rig.run(400000);

    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::Ready));
}

// -------------------------------------------------- one token at a time

TEST_CASE(a_second_token_waits_for_the_first_to_be_answered) {
    // A controller runs a real USB transaction when it is given a token and
    // raises its interrupt when that finishes. Firing the next one on a timer
    // regardless means talking over an answer that has not arrived - on the
    // bench, a hundred and twenty-five tokens produced seven interrupts.
    Rig rig;
    rig.chip.attach_device();
    rig.run(300000);
    rig.chip.answer_tokens_after(30000);
    const int before = rig.chip.tokens_issued();

    rig.run(300000);

    // Three hundred milliseconds at thirty per answer is about ten tokens.
    // On an eight-millisecond timer with no regard for the answer it is
    // nearer forty.
    const int issued = rig.chip.tokens_issued() - before;
    CHECK(issued <= 14);
}

TEST_CASE(polling_still_happens_when_it_is_paced_by_the_answers) {
    Rig rig;
    rig.chip.attach_device();
    rig.run(300000);
    rig.chip.answer_tokens_after(30000);
    const int before = rig.chip.tokens_issued();

    rig.run(300000);

    CHECK(rig.chip.tokens_issued() - before >= 4);
}

TEST_CASE(a_token_whose_answer_never_comes_does_not_stop_the_polling_forever) {
    // Waiting for an answer cannot mean waiting for one that was lost. A
    // controller that drops an interrupt would otherwise leave the device
    // unpolled for good, which reads as a mouse that simply stopped.
    Rig rig;
    rig.chip.attach_device();
    rig.run(300000);
    // Long enough that no answer is ever ready inside this test.
    rig.chip.answer_tokens_after(60000000);
    const int before = rig.chip.tokens_issued();

    rig.run(500000);

    CHECK(rig.chip.tokens_issued() > before);
}

// ------------------------------------------- the reply wait is a state

TEST_CASE(a_tick_on_a_chip_that_is_not_answering_costs_the_loop_almost_nothing) {
    // Core 1 ticks both channels in sequence, and a mouse's interrupt endpoint
    // wants polling about every 8 ms. So a channel that is waiting on a chip
    // which is never going to answer spends the *other* channel's poll window.
    //
    // Measured on hardware before this: 747 of one channel's 751 reply
    // timeouts were the recovery path's CHECK_EXIST, 20 ms each, and a single
    // tick reached 50 686 us. The question has to be asked and left, with the
    // answer collected on a later tick.
    Rig rig;
    rig.chip.go_silent(true);
    // Nothing plugged in: this is the recovery loop of a channel whose chip
    // has stopped talking, which is where the measured time went.
    rig.run(3 * kRecoverDelayUs, 1000);

    const std::uint32_t worst = rig.worst_tick(3000);

    // Measured with this test: 20 000 us before the change - one whole reply
    // timeout inside one tick - and 10 us after it.
    CHECK(worst < 500u);
}

TEST_CASE(a_chip_that_holds_its_interrupt_and_answers_nothing_costs_the_loop_nothing) {
    // Measured on hardware at 2026-08-29 11:31, with the four repairs on the
    // board: the worst pass round Core 1's loop was 20 168 us - one whole
    // kDefaultReplyTimeoutUs - once one channel had a device attached and
    // stuck before Ready, holding INT and answering nothing. poll_interrupt
    // called get_status, whose read_reply spun for 20 ms, and Core 1 ticks the
    // two channels in sequence: two and a half of the *other* channel's 8 ms
    // poll windows gone, every tick. The operator saw exactly that - "the
    // cursor works intermittently, with interruptions; the keyboard does not
    // type" - one channel's stuck enumeration starving the other through this
    // one wait.
    //
    // Reading the status is also the only thing that clears the chip's
    // request, so a chip that will not answer it holds the line asserted and
    // the wait is paid again on the very next tick. Nothing else in the
    // machine repeats a 20 ms wait that often.
    Rig rig;
    rig.run(300000);
    rig.chip.hold_interrupt_unanswered(true);

    const std::uint32_t worst = rig.worst_tick(3000);

    // Measured with this test: 20 000 us before the change, one poll of an
    // empty port after it.
    CHECK(worst < 500u);
}

/// A live rig: the real DescriptorSetup rather than the fake one.
///
/// The stretch measured below runs through enumeration, and what enumeration
/// puts on the wire is the whole question. FakeDeviceSetup reads a block on
/// every poll whether or not the chip has just answered an interrupt, which no
/// real setup does: DescriptorSetup reads one only when the status it was
/// handed says a transfer completed. Measuring the budget against the fake
/// would measure the fake.
struct LiveRig {
    FakeCh375Chip chip;
    Ch375Transport transport{chip};
    DescriptorSetup setup{transport};
    Ch375Device device{transport, setup};
};

/// How many commands of the bring-up the chip is allowed to answer before it
/// goes deaf, one scenario per value.
///
/// Zero is a chip that stops the instant the device arrives. Six carries it
/// through GET_DEVICE_RATE, both halves of the bus reset and into
/// enumeration, which is the whole of the stretch the hardware is stuck in.
/// It stops there because the next command that expects an answer is the
/// block read of the first descriptor, and a block read is the one path in
/// this transport still allowed to wait - see the link hardening report.
constexpr int kStallPoints = 6;

TEST_CASE(no_tick_between_a_device_attaching_and_it_working_stalls_the_other_channel) {
    // Measured on hardware at 2026-08-29 12:06, with the deferred status read
    // on the board: the worst pass round Core 1's loop was still 20 170 us -
    // one whole kDefaultReplyTimeoutUs - on a channel sitting at attached=1,
    // ready=0. That is the stretch between a device arriving and it being
    // usable, and it is exactly the stretch whose commands were left
    // blocking: GET_DEVICE_RATE on the attach, and SET_USB_MODE at each step
    // of the bus reset and of the recovery that follows a failed one.
    //
    // Neither existing budget test can reach it. One drives a chip that is
    // deaf from the start, which never gets a device as far as attaching; the
    // other holds INT and answers no status, which stops the tick above these
    // states entirely. So a 20 ms path survived both.
    //
    // The chip here answers its own bring-up, answers a device arriving, and
    // then stops - at each command boundary in turn, because which command it
    // stops at decides which of these paths is the one left waiting.
    std::uint32_t worst = 0;
    bool saw_resetting = false;
    bool saw_host_mode = false;
    bool saw_enumerating = false;

    for (int answered = 0; answered <= kStallPoints; ++answered) {
        LiveRig rig;
        rig.chip.serve_boot_mouse();
        run_for(rig, 200000, 100);
        rig.chip.attach_device();
        rig.chip.go_silent_after(answered);

        // Long enough to cover enumeration's own deadline and a full
        // kRecoverDelayUs after it, so the recovery path's mode command is
        // measured too.
        for (int pass = 0; pass < 2500; ++pass) {
            const std::uint32_t before = rig.chip.now_us();
            rig.device.tick(rig.chip.now_us());
            const std::uint32_t spent = rig.chip.now_us() - before;
            if (spent > worst) {
                worst = spent;
            }
            saw_resetting = saw_resetting || rig.device.state() == Ch375State::Resetting;
            saw_host_mode = saw_host_mode || rig.device.state() == Ch375State::HostMode;
            saw_enumerating = saw_enumerating || rig.device.state() == Ch375State::Enumerating;
            rig.chip.advance(1000);
        }
    }

    // The walk actually happened. Without these the budget could be met by a
    // machine that never left Absent.
    CHECK(saw_resetting);
    CHECK(saw_host_mode);
    CHECK(saw_enumerating);
    // Measured with this test before the conversion: 20 000 us, one whole
    // reply timeout inside one tick.
    CHECK(worst < 500u);
}

TEST_CASE(a_status_read_never_takes_the_answer_to_a_question_already_on_the_wire) {
    // The interrupt status is read at the top of every tick, before the idle
    // channel gets to poll the probe it put on the wire a tick ago. Both read
    // the same port, and the chip answers commands in the order they arrive -
    // so GET_STATUS written while a CHECK_EXIST is outstanding is answered
    // after the probe's byte, and each reader takes the other's.
    //
    // A chip that holds its interrupt asserted and answers no status is the
    // case that repeats it: the request is never cleared, so the line is still
    // asserted on the next tick, and on the one after that. Every probe this
    // channel puts on the wire is taken. The chip here answers CHECK_EXIST
    // perfectly - nothing about it is a lost chip - so a channel that reports
    // one, and puts a working chip through the whole of setup again to cure
    // it, is reporting the collision and not the chip.
    Rig rig;
    rig.run(300000);
    const std::uint32_t modes_before = rig.chip.mode_set_count();
    rig.chip.hold_interrupt_unanswered(true);

    rig.run(6 * kPresenceRecheckUs, 1000);

    CHECK_EQ(rig.device.presence_lost(), 0u);
    CHECK_EQ(rig.chip.mode_set_count(), modes_before);
}

TEST_CASE(a_device_plugged_in_while_the_chip_is_being_re_proved_is_not_read_as_a_loss) {
    // The reviewer's sequence, exactly. The channel is idle in Absent and has
    // just put its once-a-second CHECK_EXIST on the wire. Between that tick
    // and the next, somebody plugs a device in: the chip raises INT, and its
    // answer to the probe is already sitting in the receive FIFO. The next
    // tick reads the status first.
    //
    // What that used to produce: the probe's 0x5A read as the interrupt
    // status, the real Connect left queued and then read as the probe's
    // answer, a mismatch, and a healthy chip put through the whole of setup
    // again - with presence_lost counting plug events, which is the one
    // counter that repair 4 exists to report.
    Rig rig;
    rig.run(300000);
    for (int index = 0;
         index < 200000 && rig.transport.pending_reply() != PendingReply::Presence; ++index) {
        rig.device.tick(rig.chip.now_us());
        rig.chip.advance(100);
    }
    CHECK_EQ(static_cast<int>(rig.transport.pending_reply()),
             static_cast<int>(PendingReply::Presence));

    rig.chip.attach_device();
    rig.run(400000);

    CHECK_EQ(rig.device.presence_lost(), 0u);
    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::Ready));
}

// ------------------------------------------------ finding a lost chip

TEST_CASE(a_chip_left_at_a_rate_this_side_abandoned_is_found_and_brought_home) {
    // The state a reflash of U1 leaves behind: the processor restarts talking
    // at 9600 and the controller is still wherever the last run put it. This
    // board has no reset line to a CH375 (docs/hardware/ch375-wiring.md gives
    // RXD, TXD and INT and nothing else), so the serial port is the only lever
    // there is - and the manufacturer says the same thing from the other side
    // (CH375 datasheet, serial interface section: reset it through an MCU pin
    // to bring the baud rate home). Until this, the cure was a person walking
    // to the board and pulling the module's 5 V.
    Rig rig;
    rig.chip.strand_at(115200);

    rig.run(6 * kRecoverDelayUs, 1000);

    CHECK(rig.device.chip_found_elsewhere() > 0u);
    // Where it was found, not merely that it was: the rate says whether this
    // side abandoned a chip that was holding a rung, or the chip is somewhere
    // neither end chose.
    CHECK_EQ(rig.device.chip_found_at(), 115200u);
    // Revived without anybody touching it, and both ends agree about the rate
    // they are speaking at - which is what being brought home means.
    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::Absent));
    CHECK_EQ(rig.chip.chip_baud(), rig.chip.port_baud());
}

TEST_CASE(a_chip_that_answers_nowhere_is_not_hunted_for_every_second) {
    // The search writes to rates the chip may not be using, and a chip that
    // half-hears a byte swallows the next command as its parameter. So it is
    // not the first thing tried - only what is left after several probes at
    // the rate the chip should have come back to have gone unanswered.
    //
    // This is the only remaining guard on that constraint: the two recover_from
    // tests that used to hold it went with recover_from. It has to be a chip
    // the search would find, and it has to look at the wire. Against a chip
    // that answers nowhere - which is what this used to use - the search finds
    // nothing at any rate, so the counters read zero whether the guard exists
    // or not, and the test held either way.
    Rig rig;
    rig.chip.strand_at(115200);

    rig.run(2 * kRecoverDelayUs, 1000);

    // Two recovery cycles against a guard of three, and nothing has been
    // written anywhere but the rate the chip is supposed to have come back to.
    CHECK(!rig.chip.wrote_commands_away_from(9600u));
    CHECK_EQ(rig.device.chip_found_elsewhere(), 0u);
    CHECK_EQ(rig.device.chip_found_at(), 0u);

    // And a delay is all it is: given the third cycle, the search runs and
    // finds the chip where this side left it.
    rig.run(6 * kRecoverDelayUs, 1000);

    CHECK_EQ(rig.device.chip_found_at(), 115200u);
}

TEST_CASE(searching_every_rate_for_a_lost_chip_still_fits_inside_a_tick) {
    // The prototype wired this search into the blocking reply wait and the
    // worst tick went from 50 686 us to 154 056 us - measured, and felt as a
    // lag while typing, because Core 1 ticks the other channel afterwards.
    // Four rates and two probes each is a state machine's worth of work, not
    // one tick's.
    Rig rig;
    rig.chip.go_silent(true);
    rig.run(6 * kRecoverDelayUs, 1000);

    const std::uint32_t worst = rig.worst_tick(20000);

    CHECK(worst < 500u);
}

// ----------------------------------------------- what proves a rate

TEST_CASE(a_rate_that_cannot_finish_a_block_read_is_stepped_down_from) {
    // Measured on the bench, keyed on the port's real rate:
    //
    //     byRate(9600/37500/62500/115200):  ok=0/1/0/0   fail=0/0/24/24
    //
    // 115200 and 62500 answer CHECK_EXIST perfectly and fail every block read,
    // framing errors accumulating; 37500 carries them and the mouse enumerated
    // and reported there. Two CHECK_EXIST replies prove a divider, not a link.
    //
    // And the trap closed from the other side: the ladder only ever stepped
    // down from a device that had reached Ready, which a rate too broken to
    // fetch a descriptor never does. So the channel sat at 115200 for ever.
    Rig rig;
    rig.chip.break_block_reads_at_or_above(62500);
    rig.chip.attach_device();

    rig.run(60 * kRecoverDelayUs, 1000);

    // Measured with this test: the channel walks 115200 -> 62500 -> 37500 in
    // four failed enumerations and comes up there. Before the step-down
    // existed it stayed at 115200 for the whole run.
    CHECK_EQ(rig.chip.port_baud(), 37500u);
    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::Ready));
}

TEST_CASE(a_rate_that_carries_its_block_reads_is_left_alone) {
    // The step-down is on repeated failure, not on any failure: a working
    // channel must keep the fastest rung it has.
    Rig rig;
    rig.chip.attach_device();

    rig.run(20 * kRecoverDelayUs, 1000);

    CHECK_EQ(rig.chip.port_baud(), 115200u);
    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::Ready));
}

// ------------------------------------------------- the ladder has a floor

TEST_CASE(the_floor_is_derived_from_what_one_report_costs_on_the_wire) {
    // Collecting one report costs the packet plus eight frames: GET_STATUS and
    // its answer, RD_USB_DATA0 and its length byte, SET_ENDPOINT6 and its
    // argument, ISSUE_TOKEN and its argument. A CH375 serial frame is eleven
    // bits, the ninth data bit marking a command from a data byte (DS1 6.2.2).
    // So a seven-byte mouse report is fifteen frames, 165 bits - the fifteen
    // bytes the transport's own note names, and 17.2 ms at 9600 against the
    // 8 ms a moving hand produces one in.
    CHECK_EQ(duo_input::u1::ch375::report_rate_floor(7, kReportPollUs), 20625u);
    // 9600 is below it and 37500 is the slowest rung above it, which is the
    // rung the bench measured block reads completing on.
    CHECK(9600u < duo_input::u1::ch375::report_rate_floor(7, kReportPollUs));
    CHECK(37500u > duo_input::u1::ch375::report_rate_floor(8, kReportPollUs));
}

TEST_CASE(a_channel_with_a_device_up_never_rests_below_that_floor) {
    // The ladder used to step down on every collapse and, at the bottom, set
    // baud_exhausted_ and park the port at 9600 for good. On the bench that is
    // exactly what happened - col=3, then baud=9600 - and at 9600 the channel
    // provably cannot carry a moving mouse, so it collapsed harder, which
    // stepped the ladder down again. Self-reinforcing, and only a restart of
    // U1 ever cleared it.
    Rig rig;
    rig.chip.attach_device();
    rig.run(300000);
    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::Ready));

    // The device stops answering its endpoint. Every rung now collapses.
    rig.chip.answer_tokens_after(60000000);

    unsigned slowest_while_up = 0xFFFFFFFFu;
    for (int index = 0; index < 60000; ++index) {
        rig.device.tick(rig.chip.now_us());
        if (rig.device.state() == Ch375State::Ready &&
            rig.chip.port_baud() < slowest_while_up) {
            slowest_while_up = rig.chip.port_baud();
        }
        rig.chip.advance(1000);
    }

    CHECK(rig.device.collapses_while_raised() >= 3u);
    CHECK(slowest_while_up >= 37500u);
}

// ------------------------------ one rate, not two ideas about one rate

TEST_CASE(the_port_and_the_rate_this_side_believes_in_never_disagree) {
    // reset_port_speed drops the port back to 9600 on every chip re-setup, and
    // it used to do that without moving the device's own copy of the rate. So
    // the device could believe it was talking at 37500 while the receiver was
    // clocked for 9600 - which is precisely the state the transport's own note
    // warns about: a byte half-heard at the wrong rate is swallowed as some
    // command's parameter, and every byte after it is out of step. That is the
    // mechanism behind "the chip stopped answering".
    //
    // There is now one place that knows, so the two cannot come apart.
    Rig rig;
    rig.chip.attach_device();

    bool ever_disagreed = false;
    for (int index = 0; index < 40000; ++index) {
        rig.device.tick(rig.chip.now_us());
        if (rig.transport.port_baud() != rig.chip.port_baud()) {
            ever_disagreed = true;
        }
        rig.chip.advance(500);
    }

    CHECK(!ever_disagreed);
    // And the run really did move the rate around, so agreeing is a finding
    // rather than the two never having been asked to differ.
    CHECK(rig.chip.port_baud() > 9600u);
}

// ------------------------------------- an idle channel re-proves its chip

TEST_CASE(an_idle_channel_notices_its_chip_stopped_being_a_configured_chip) {
    // Ch375State::Absent asks whether a device is attached and reads silence
    // as "no device". It has no way to notice that its chip stopped being a
    // configured chip at all - and a CH375 whose 5 V was cycled under a
    // running U1 is back at 9600 with no host mode, deaf at the rate this side
    // raised it to. The channel then sits in Absent for ever. That is the
    // state this project cured by reflashing U1, all week.
    Rig rig;
    rig.run(300000);
    CHECK_EQ(static_cast<int>(rig.device.state()), static_cast<int>(Ch375State::Absent));
    CHECK(rig.chip.port_baud() > 9600u);
    const std::uint32_t modes_before = rig.chip.mode_set_count();

    rig.chip.power_cycle();
    rig.run(6 * kRecoverDelayUs, 1000);

    // Chip setup ran again, unaided, and the chip is back in the mode DS1 5.9
    // says to wait in.
    CHECK(rig.device.presence_lost() > 0u);
    CHECK(rig.chip.mode_set_count() > modes_before);
    CHECK_EQ(static_cast<int>(rig.chip.mode()),
             static_cast<int>(duo_input::u1::ch375::UsbMode::HostNoSof));
}

TEST_CASE(a_channel_whose_chip_is_well_is_not_set_up_again_for_nothing) {
    // The re-check must not become a reason to tear a healthy channel down:
    // chip setup resets the chip, drops the port to 9600 and climbs the ladder
    // again, which is a second of a working channel doing nothing.
    Rig rig;
    rig.run(300000);
    const std::uint32_t modes_before = rig.chip.mode_set_count();

    rig.run(10 * kRecoverDelayUs, 1000);

    CHECK_EQ(rig.device.presence_lost(), 0u);
    CHECK_EQ(rig.chip.mode_set_count(), modes_before);
}

TEST_CASE(one_missed_probe_byte_is_not_a_lost_chip) {
    // Declaring the chip lost re-runs the whole of chip setup: RESET_ALL, sixty
    // milliseconds of waiting, a probe, a mode command and the climb back up
    // the baud ladder - about a second of a working channel doing nothing, and
    // a peripheral that goes dark and comes back if one was attached. The far
    // cheaper search for a chip that has moved is guarded by three failures
    // (kProbesBeforeChipSearch); this was guarded by none, so a single dropped
    // byte bought all of it.
    Rig rig;
    rig.run(300000);
    const std::uint32_t modes_before = rig.chip.mode_set_count();
    rig.chip.miss_next_check_exists(1);

    rig.run(4 * kPresenceRecheckUs, 1000);

    CHECK_EQ(rig.device.presence_lost(), 0u);
    CHECK_EQ(rig.chip.mode_set_count(), modes_before);
}

TEST_CASE(two_missed_probe_bytes_in_a_row_are_a_chip_that_has_gone) {
    // The other side of it: a second opinion, not a second chance. A chip that
    // really has stopped answering must still be noticed, and quickly - the
    // retry is immediate rather than a second later, so it costs one reply
    // timeout to be sure.
    Rig rig;
    rig.run(300000);
    const std::uint32_t modes_before = rig.chip.mode_set_count();
    rig.chip.miss_next_check_exists(2);

    rig.run(4 * kPresenceRecheckUs, 1000);

    CHECK(rig.device.presence_lost() > 0u);
    CHECK(rig.chip.mode_set_count() > modes_before);
}

TEST_CASE(re_proving_the_chip_does_not_cost_the_other_channel_its_poll_window) {
    // Against a healthy chip this measured nothing: the re-check is answered
    // immediately, so asking and waiting costs the same as asking and leaving,
    // and the test held whichever the code did.
    //
    // The chip that makes the difference visible is the one the re-check
    // exists for - a module whose 5 V was cycled under a running U1, back at
    // 9600 and deaf at the rate this side raised it to. Every question put to
    // that chip costs a whole reply timeout if it is waited for, and Core 1
    // takes that out of the other channel's 8 ms poll window.
    Rig rig;
    rig.run(300000);
    CHECK(rig.chip.port_baud() > 9600u);
    rig.chip.power_cycle();

    const std::uint32_t worst = rig.worst_tick(20000);

    // Measured with this test: 20 000 us with the blocking check_exist, and
    // one poll of an empty port with the deferred probe.
    CHECK(worst < 500u);
}
