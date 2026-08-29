#include "ch375/device.hpp"

#include <cstring>

namespace duo_input::u1::ch375 {

void Ch375Device::tick(std::uint32_t now_us) {
    // The chip is put into host mode lazily rather than in a constructor: a
    // controller that is not powered yet would otherwise fail at construction
    // time, with nowhere to report it and no way to try again.
    if (!chip_ready_ && !bring_chip_up(now_us)) {
        return;
    }

    InterruptStatus status = InterruptStatus::Success;
    const bool interrupted = poll_interrupt(status);
    if (interrupted) {
        last_status_ = static_cast<std::uint8_t>(status);
        const std::uint8_t raw = last_status_;
        if (raw < 0x10) {
            // DS1 5.12: 00-0F is the device-mode band. A chip in host mode
            // cannot produce one, so this is the port out of step rather than
            // the chip saying something.
            ++status_impossible_;
        } else if (status == InterruptStatus::Connect) {
            ++status_connect_;
        } else if (status == InterruptStatus::Disconnect) {
            ++status_disconnect_;
        } else if (status == InterruptStatus::Success) {
            ++status_success_;
        } else if (is_failure(status)) {
            ++status_failure_;
        }
    }

    // A disconnect outranks whatever was in progress. Anything being held on
    // the far side has to be let go, and no later step can succeed anyway.
    //
    // Except while this code is the one holding the bus down. Bringing a
    // device up means putting the bus into reset (DS1 5.9), and an attached
    // device looks gone to the chip's own detection for as long as that lasts.
    // Believing it means announcing a detach, returning to Absent, finding the
    // device again and resetting the bus again - which on the bench was a
    // mouse attaching and detaching six times over without ever coming up.
    //
    // The exemption covers the reset and the pause after it, and stops there.
    // The chip's detection is not trustworthy until frames are flowing again -
    // on the bench a device was reported gone during that pause every time,
    // and configuring it was never once reached. Past that point a disconnect
    // is somebody pulling a cable, and a missed one leaves keys held down on
    // the far side.
    const bool bringing_up =
        state_ == Ch375State::Resetting || state_ == Ch375State::HostMode;
    if (interrupted && status == InterruptStatus::Disconnect && !bringing_up) {
        ++detach_from_disconnect_;
        handle_detach(now_us);
        return;
    }

    switch (state_) {
        case Ch375State::Absent: {
            bool connected = interrupted && status == InterruptStatus::Connect;
            if (!connected && now_us - last_connect_poll_us_ >= kConnectPollUs) {
                // Ask, rather than only waiting to be told. The announcement
                // never comes for a device that was plugged in before the
                // power, and it is consumed for nothing if the chip was
                // answering nonsense when it arrived.
                last_connect_poll_us_ = now_us;
                InterruptStatus probed = InterruptStatus::Disconnect;
                connected = transport_.test_connect(probed) && probed == InterruptStatus::Connect;
            }
            if (connected) {
                publish(Ch375EventKind::Attached);
                // Asked here, in mode 5, because DS2 1.2 says that is the only
                // mode the question is valid in - and the answer decides how
                // the bus has to run from now on.
                bool low_speed = false;
                device_is_low_speed_ = transport_.get_device_rate(low_speed) && low_speed;
                // DS1 5.9: mode 7 first, then mode 6. Mode 7 holds the bus in
                // reset and keeps holding it, so it is a step, not a state to
                // rest in.
                const UsbMode next =
                    skip_bus_reset_ ? UsbMode::HostWithSof : UsbMode::HostReset;
                if (!transport_.set_usb_mode(next)) {
                    ++mode_failures_;
                    fail(now_us);
                    return;
                }
                if (skip_bus_reset_) {
                    transport_.set_usb_speed(device_is_low_speed_ ? UsbSpeed::Low1_5Mbps
                                                                  : UsbSpeed::Full12Mbps);
                }
                enter(skip_bus_reset_ ? Ch375State::HostMode : Ch375State::Resetting, now_us);
            }
            return;
        }

        case Ch375State::Resetting:
            if (now_us - entered_us_ < kBusResetHoldUs) {
                return;
            }
            // Leaving mode 7 in place would hold the bus down forever, which
            // is indistinguishable from a dead port.
            if (!transport_.set_usb_mode(UsbMode::HostWithSof)) {
                fail(now_us);
                return;
            }
            // After the mode, never before: setting a working mode puts the
            // bus back to 12 Mbps (DS2 1.1). A low-speed device addressed at
            // full speed says nothing, and a controller reports something that
            // says nothing as gone.
            transport_.set_usb_speed(device_is_low_speed_ ? UsbSpeed::Low1_5Mbps
                                                          : UsbSpeed::Full12Mbps);
            enter(Ch375State::HostMode, now_us);
            return;

        case Ch375State::HostMode:
            if (now_us - entered_us_ < kBusSettleUs) {
                // Still coming round from the reset. See kBusSettleUs.
                return;
            }
            setup_.begin(now_us);
            enter(Ch375State::Enumerating, now_us);
            return;

        case Ch375State::Enumerating: {
            const SetupProgress progress = setup_.poll(now_us, interrupted, status);
            if (progress == SetupProgress::Busy) {
                return;
            }
            if (progress == SetupProgress::Failed) {
                ++enumerate_failures_;
                fail(now_us);
                return;
            }
            endpoint_ = setup_.interrupt_endpoint();
            // A new device starts its data toggle at DATA0, and the chip has
            // to be told - it does not track this itself (DS2 1.6). Without
            // it the first IN transaction never completes: no data, no error,
            // and no interrupt, which on the bench was 120 polls in a row
            // producing nothing at all.
            expect_data1_ = false;
            transport_.set_receive_toggle(kToggleData0);
            enter(Ch375State::Ready, now_us);
            last_answer_us_ = now_us;
            token_outstanding_ = false;
            relights_ = 0;
            if (!announced_ready_) {
                // Once per device, not once per attempt: a controller that
                // needed three tries still produced one working keyboard, and
                // saying so three times would be three keyboards.
                publish(Ch375EventKind::Ready);
                announced_ready_ = true;
            }
            last_poll_us_ = now_us;
            return;
        }

        case Ch375State::Ready: {
            if (interrupted) {
                last_answer_us_ = now_us;
                // The transaction this token started has finished, whatever it
                // came to.
                token_outstanding_ = false;
            } else if (now_us - last_answer_us_ >= kDeviceLostUs) {
                // Quiet for a second with a device configured. That is not the
                // same as the device being gone - the controller has said
                // nothing about a disconnection - and the two have been
                // treated alike, at a cost the operator can see.
                //
                // Tearing the chip and the bus down re-enumerates the
                // peripheral: its lights go out and come back, and every key
                // or button held on it is released and re-acquired. Doing that
                // for a transient silence is a fault of its own, and on this
                // bench it is the fault the operator actually notices.
                //
                // So the endpoint is re-armed first. Setting the receive
                // toggle back to DATA0 and polling again costs four bytes and
                // recovers the case that is actually common: one transaction
                // lost, and the two ends one packet out of step over it.
                if (relights_ < kQuietRetriesBeforeTeardown) {
                    ++relights_;
                    ++quiet_rearms_;
                    expect_data1_ = false;
                    transport_.set_receive_toggle(kToggleData0);
                    token_outstanding_ = false;
                    last_answer_us_ = now_us;
                    return;
                }

                // It has had its chances. Whatever it was holding would be
                // held forever otherwise: releasing a key nobody pressed is a
                // nuisance, and holding one nobody can release is not.
                ++detach_from_lost_;

                // A rate that shook hands and then dropped the link is not a
                // rate this channel has. Two CHECK_EXIST replies prove the
                // divider; they say nothing about whether the wire holds up
                // under seventeen hundred transactions, and on this bench it
                // did not - it ran, then fell over, and every fall costs the
                // operator a peripheral that goes dark and comes back.
                //
                // So the ladder is walked by what actually survives, not by
                // what answers once. Down a rung on every collapse, and off
                // the ladder entirely at the bottom: a link that keeps its
                // speed and keeps dropping is worse than a slower one that
                // does not.
                if (raised_baud_ != kCh375DefaultBaud) {
                    ++collapses_while_raised_;
                    if (baud_rung_ + 1 < kBaudLadderSize) {
                        ++baud_rung_;
                    } else {
                        baud_exhausted_ = true;
                    }
                    // raised_baud_ is deliberately left alone. The chip may be
                    // stranded at it right now - that is what a collapse looks
                    // like from here - and this is the only record of where it
                    // was put. Clearing it here threw away the one thing the
                    // recovery needs, and left the chip unreachable.
                }

                handle_detach(now_us);
                fail(now_us);
                chip_ready_ = false;
                return;
            }
            if (interrupted && status == InterruptStatus::Success) {
                std::uint8_t buffer[kMaxBlockSize];
                std::size_t size = 0;
                if (transport_.read_block(buffer, sizeof(buffer), size) && size > 0) {
                    publish_report(buffer, size);
                }
                // One transaction succeeded, so the device will send the other
                // packet type next. Told only after a success: a transaction
                // that failed did not consume anything, and moving the toggle
                // anyway would leave the two ends permanently one apart.
                expect_data1_ = !expect_data1_;
                transport_.set_receive_toggle(expect_data1_ ? kToggleData1 : kToggleData0);
            }
            if (now_us - last_poll_us_ < kReportPollUs) {
                return;
            }
            // One token at a time. Giving the controller another before it has
            // answered the last is talking over it: it is running a real USB
            // transaction and raises its interrupt when that finishes, so a
            // token fired on a timer regardless lands in the middle of one.
            // On the bench that was a hundred and twenty-five tokens producing
            // seven interrupts, and then a device declared lost for the
            // silence they were causing.
            //
            // Unless the answer is simply not coming. Waiting for a lost
            // interrupt for ever would leave the device unpolled for good,
            // which looks like a mouse that stopped rather than a link that
            // dropped one reply.
            if (token_outstanding_ && now_us - token_at_us_ < kTokenAnswerUs) {
                return;
            }
            last_poll_us_ = now_us;
            token_outstanding_ = true;
            token_at_us_ = now_us;
            // Ask the device whether it has anything. The answer arrives as an
            // interrupt, which the next tick picks up - nothing waits here.
            ++polls_issued_;
            transport_.issue_token(endpoint_, TokenPid::In);
            return;
        }

        case Ch375State::RecoverWait:
            if (now_us - entered_us_ < kRecoverDelayUs) {
                return;
            }
            // Start the whole sequence again from the bus reset. Picking up
            // where it left off would carry the broken state along with it.
            if (!transport_.set_usb_mode(UsbMode::HostReset)) {
                // Still unhappy. Wait out another delay rather than spinning,
                // and go back through chip setup: a controller this broken may
                // have lost its mode entirely.
                ++recover_mode_failures_;
                chip_ready_ = false;
                entered_us_ = now_us;
                return;
            }
            enter(Ch375State::Resetting, now_us);
            return;

        case Ch375State::Fault:
            return;
    }
}

bool Ch375Device::bring_chip_up(std::uint32_t now_us) {
    switch (bring_up_) {
        case ChipBringUp::Idle:
            if (state_ == Ch375State::RecoverWait && now_us - entered_us_ < kRecoverDelayUs) {
                // Still counting down. Retrying flat out would hammer a
                // controller that is already unhappy, every tick, forever.
                return false;
            }
            // Start from a chip that is definitely idle. Whatever state the
            // last run left it in - including ones it does not leave by itself
            // - is gone after this, and the wait is the price.
            transport_.reset_all();
            transport_.drain_pending_status();
            bring_up_ = ChipBringUp::Resetting;
            chip_reset_at_us_ = now_us;
            return false;

        case ChipBringUp::Resetting:
            if (now_us - chip_reset_at_us_ < kChipResetUs) {
                return false;
            }
            // The chip has just come back from RESET_ALL, which returns its
            // port to 9600 whatever it was doing before. This side has to go
            // back with it before anything can be said at all.
            transport_.reset_port_speed(kCh375DefaultBaud);

            // Nothing is said to the chip until it has said something first.
            //
            // CHECK_EXIST is one command and one data byte and it proves the
            // port by construction (DS1 5.5), so it is the cheapest thing that
            // can be wrong. A mode command sent to a controller that is still
            // coming back from its reset is read as part of that restart, and
            // then every byte after it is out of step - a chip that worked a
            // second ago and now answers nothing at all.
            //
            // Asked and left. The answer is picked up on a later tick, because
            // a chip that is not coming back would otherwise hold this core
            // for 20 ms while the other channel's endpoint goes unpolled.
            transport_.begin_presence_probe(kPortProbeByte);
            bring_up_ = ChipBringUp::Probing;
            return false;

        case ChipBringUp::Probing: {
            const ReplyProgress progress = transport_.poll_presence_probe();
            if (progress == ReplyProgress::Waiting) {
                return false;
            }
            if (progress == ReplyProgress::Answered && transport_.presence_probe_matched()) {
                break;
            }
            ++chip_not_back_yet_;
            bring_up_ = ChipBringUp::Idle;

            // Silent at the rate it should have come back to. If this code had
            // raised it, that is where it may still be - the reset was sent at
            // a rate it had stopped holding, so it never heard it. One known
            // rate is worth asking; anything else is guessing at its expense.
            if (raised_baud_ != kCh375DefaultBaud &&
                transport_.recover_from(raised_baud_, kCh375DefaultBaud)) {
                ++chip_recovered_from_raised_;
                raised_baud_ = kCh375DefaultBaud;
                // It has just been reset, so it needs the same wait as any
                // other reset before anything else is said to it.
                bring_up_ = ChipBringUp::Resetting;
                chip_reset_at_us_ = now_us;
                return false;
            }
            fail(now_us);
            return false;
        }
    }

    bring_up_ = ChipBringUp::Idle;

    // Mode 5 is where DS1 5.9 says to wait: enabled, generating no frames,
    // watching for a device by itself.
    if (!transport_.set_usb_mode(UsbMode::HostNoSof)) {
        // Counted. Without this the channel can spin here forever with every
        // reading frozen, which looks from outside exactly like a board that
        // has stopped running - and cost an evening of being read as one.
        ++setup_mode_failures_;

        // Does it answer anything at all? A chip that takes CHECK_EXIST and
        // refuses a mode is a different fault from one that is deaf, and the
        // two have looked identical from out here all along: both are just a
        // channel that does nothing.
        // What it answered, not merely that it refused.
        mode_reply_ = transport_.last_status_reply();
        mode_answered_ = transport_.last_status_answered();

        if (transport_.check_exist(kPortProbeByte)) {
            ++alive_but_refusing_;
        }

        // Nothing else is sent. Searching for the chip at other rates,
        // flushing it with filler and sweeping the receiver all write bytes at
        // rates it may not be using, and each of those can wedge a chip that
        // was about to come good on its own.
        fail(now_us);
        return false;
    }
    // The chip's own default is to retry a NAK forever (DS2 1.3). A device
    // that stops answering would then hold the firmware inside a single
    // command, and everything else on this loop stops with it.
    transport_.set_retry(kRetryReportNak);

    // Only now is the rate raised - after the chip has proved it is alive by
    // taking a mode command.
    //
    // At 9600 one mouse report costs fifteen bytes of eleven bits each:
    // seventeen milliseconds for something a moving hand produces every eight.
    // The deficit never closes while the hand keeps moving, and the peripheral
    // is reset and re-enumerated for a silence this side is causing. That is
    // the fault this exists to fix.
    //
    // But asking costs a write at a rate the chip may not be using, and a chip
    // that half-hears one stops answering entirely. Asked before the chip had
    // answered anything, that barrage met every dead channel once a second -
    // including the freshly powered one somebody had just walked over to
    // revive, wedged again before they got back to their chair. A chip that
    // has just accepted a mode command is not in that state, and is the only
    // kind worth asking.
    if (!baud_exhausted_) {
        port_baud_ = transport_.try_speed(kBaudLadder[baud_rung_], kCh375DefaultBaud);
        // Remembered, because it is the only other place the chip can be.
        raised_baud_ = port_baud_;
        if (port_baud_ == kCh375DefaultBaud) {
            ++baud_change_failures_;
            if (baud_rung_ + 1 < kBaudLadderSize) {
                ++baud_rung_;
            } else {
                baud_exhausted_ = true;
            }
        }
    }
    chip_ready_ = true;
    enter(Ch375State::Absent, now_us);
    last_connect_poll_us_ = now_us - kConnectPollUs;
    return true;
}

bool Ch375Device::poll_interrupt(InterruptStatus& status) {
    // Reading the status is what clears the chip's request, so this is only
    // done when the line is actually asserted. Asking otherwise would consume
    // a status that belongs to nothing.
    if (!transport_.interrupt_pending()) {
        return false;
    }
    ++interrupts_seen_;
    if (transport_.get_status(status)) {
        return true;
    }
    ++status_reads_failed_;
    return false;
}

void Ch375Device::handle_detach(std::uint32_t now_us) {
    const bool had_device = state_ != Ch375State::Absent;
    detach_state_ = state_;

    // Back to the mode this waits in. Mode 5 is where DS1 5.9 says to sit with
    // nothing attached, and DS2 1.2 says it is the only mode in which the chip
    // can be asked how fast a device is. Left in mode 6, that question returns
    // nonsense - and a low-speed mouse read as full speed is addressed at
    // eight times its rate, answers nothing, and is reported gone.
    transport_.set_usb_mode(UsbMode::HostNoSof);
    device_is_low_speed_ = false;
    endpoint_ = 0;
    announced_ready_ = false;
    enter(Ch375State::Absent, now_us);
    if (had_device) {
        publish(Ch375EventKind::Detached);
    }
}

void Ch375Device::fail(std::uint32_t now_us) {
    enter(Ch375State::RecoverWait, now_us);
}

void Ch375Device::enter(Ch375State state, std::uint32_t now_us) {
    state_ = state;
    entered_us_ = now_us;
}

void Ch375Device::request_reenumeration() {
    if (state_ == Ch375State::Absent || state_ == Ch375State::Fault) {
        // Nothing is attached, so there is nothing to configure. Resetting a
        // bus with nothing on it every time the configurator asked would be
        // work that cannot succeed.
        return;
    }
    announced_ready_ = false;
    // Straight to the recovery path with no delay left to run: it already
    // does exactly this sequence, and having one route to it means one place
    // where it can be wrong.
    state_ = Ch375State::RecoverWait;
    entered_us_ -= kRecoverDelayUs;
}

void Ch375Device::publish(Ch375EventKind kind) {
    Ch375Event event;
    event.kind = kind;
    event.report_size = 0;

    if (count_ == kEventQueueDepth) {
        // The queue is full, so the oldest goes. Dropping the newest instead
        // would mean a detach could be lost behind a backlog of reports, and
        // that is the one event that must not be: it is what releases keys.
        head_ = (head_ + 1) % kEventQueueDepth;
        --count_;
    }
    events_[(head_ + count_) % kEventQueueDepth] = event;
    ++count_;
}

void Ch375Device::publish_report(const std::uint8_t* data, std::size_t size) {
    Ch375Event event;
    event.kind = Ch375EventKind::Report;
    event.report_size = size > kMaxBlockSize ? kMaxBlockSize : size;
    std::memcpy(event.report, data, event.report_size);

    if (count_ == kEventQueueDepth) {
        head_ = (head_ + 1) % kEventQueueDepth;
        --count_;
    }
    events_[(head_ + count_) % kEventQueueDepth] = event;
    ++count_;
}

bool Ch375Device::take_event(Ch375Event& event) {
    if (count_ == 0) {
        return false;
    }
    event = events_[head_];
    head_ = (head_ + 1) % kEventQueueDepth;
    --count_;
    return true;
}

}  // namespace duo_input::u1::ch375
