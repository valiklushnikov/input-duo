#include "ch375/device.hpp"

#include <cstring>

namespace duo_input::u1::ch375 {

void Ch375Device::tick(std::uint32_t now_us) {
    // The chip is put into host mode lazily rather than in a constructor: a
    // controller that is not powered yet would otherwise fail at construction
    // time, with nowhere to report it and no way to try again.
    if (!chip_ready_) {
        if (state_ == Ch375State::RecoverWait && now_us - entered_us_ < kRecoverDelayUs) {
            // Still counting down. Retrying flat out would hammer a controller
            // that is already unhappy, every tick, forever.
            return;
        }
        // Mode 5 is where DS1 5.9 says to wait: enabled, generating no frames,
        // watching for a device by itself.
        if (!transport_.set_usb_mode(UsbMode::HostNoSof)) {
            fail(now_us);
            return;
        }
        // The chip's own default is to retry a NAK forever (DS2 1.3). A device
        // that stops answering would then hold the firmware inside a single
        // command, and everything else on this loop stops with it.
        transport_.set_retry(kRetryReportNak);
        chip_ready_ = true;
        enter(Ch375State::Absent, now_us);
        last_connect_poll_us_ = now_us - kConnectPollUs;
    }

    InterruptStatus status = InterruptStatus::Success;
    const bool interrupted = poll_interrupt(status);

    // A disconnect outranks whatever was in progress. Anything being held on
    // the far side has to be let go, and no later step can succeed anyway.
    if (interrupted && status == InterruptStatus::Disconnect) {
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
                // DS1 5.9: mode 7 first, then mode 6. Mode 7 holds the bus in
                // reset and keeps holding it, so it is a step, not a state to
                // rest in.
                if (!transport_.set_usb_mode(UsbMode::HostReset)) {
                    fail(now_us);
                    return;
                }
                enter(Ch375State::Resetting, now_us);
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
            enter(Ch375State::HostMode, now_us);
            return;

        case Ch375State::HostMode:
            setup_.begin(now_us);
            enter(Ch375State::Enumerating, now_us);
            return;

        case Ch375State::Enumerating: {
            const SetupProgress progress = setup_.poll(now_us);
            if (progress == SetupProgress::Busy) {
                return;
            }
            if (progress == SetupProgress::Failed) {
                fail(now_us);
                return;
            }
            endpoint_ = setup_.interrupt_endpoint();
            enter(Ch375State::Ready, now_us);
            last_answer_us_ = now_us;
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
            } else if (now_us - last_answer_us_ >= kDeviceLostUs) {
                // The controller has gone quiet with a device configured. Its
                // reports cannot be read and its disconnection cannot be
                // noticed, so whatever it was holding would be held forever.
                // Treat it as gone: releasing a key nobody pressed is a
                // nuisance, and holding one nobody can release is not.
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
            }
            if (now_us - last_poll_us_ < kReportPollUs) {
                return;
            }
            last_poll_us_ = now_us;
            // Ask the device whether it has anything. The answer arrives as an
            // interrupt, which the next tick picks up - nothing waits here.
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

bool Ch375Device::poll_interrupt(InterruptStatus& status) {
    // Reading the status is what clears the chip's request, so this is only
    // done when the line is actually asserted. Asking otherwise would consume
    // a status that belongs to nothing.
    if (!transport_.interrupt_pending()) {
        return false;
    }
    return transport_.get_status(status);
}

void Ch375Device::handle_detach(std::uint32_t now_us) {
    const bool had_device = state_ != Ch375State::Absent;
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
