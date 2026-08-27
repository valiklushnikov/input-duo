#include "fakes/scripted_ch375.hpp"

namespace duo_input::u1::ch375::testing {
namespace {

/// How far the clock moves when the code under test polls an empty port.
///
/// Small enough that a deadline is not overshot by much, large enough that a
/// wait of milliseconds does not take millions of iterations to expire.
constexpr std::uint32_t kPollCostUs = 10;

std::string hex(std::uint8_t value) {
    static const char* digits = "0123456789ABCDEF";
    std::string out = "0x";
    out += digits[value >> 4];
    out += digits[value & 0x0F];
    return out;
}

}  // namespace

void ScriptedCh375::note(const char* what, std::uint8_t value) {
    if (!violations_.empty()) {
        violations_ += "; ";
    }
    violations_ += what;
    violations_ += " ";
    violations_ += hex(value);
}

void ScriptedCh375::write_command(std::uint8_t command) {
    if (next_ >= script_.size()) {
        note("unexpected command past the end of the script:", command);
        return;
    }
    const Step& step = script_[next_];
    if (step.kind != Step::Kind::ExpectCommand || step.value != command) {
        note("unexpected command:", command);
        return;
    }
    ++next_;
}

void ScriptedCh375::write_data(std::uint8_t value) {
    if (next_ >= script_.size()) {
        note("unexpected data past the end of the script:", value);
        return;
    }
    const Step& step = script_[next_];
    if (step.kind != Step::Kind::ExpectData || step.value != value) {
        note("unexpected data:", value);
        return;
    }
    ++next_;
}

bool ScriptedCh375::read_data(std::uint8_t& value) {
    if (next_ < script_.size() && script_[next_].kind == Step::Kind::Reply) {
        value = script_[next_].value;
        ++next_;
        return true;
    }
    // Nothing to read. Time passes, which is what lets a bounded wait end.
    now_us_ += kPollCostUs;
    return false;
}

}  // namespace duo_input::u1::ch375::testing

// ---------------------------------------------------------------------------
// A CH375 that behaves like one.
// ---------------------------------------------------------------------------

namespace duo_input::u1::ch375::testing {

void FakeCh375Chip::queue(std::uint8_t value) {
    if (silent_) {
        // A chip whose port has died says nothing at all, which is the case
        // the firmware most has to survive: every wait has to end by itself.
        return;
    }
    outgoing_.push_back(garbage_ ? 0x00 : value);
}

void FakeCh375Chip::write_command(std::uint8_t command) {
    ++command_count_;
    pending_command_ = command;
    expecting_data_ = false;

    switch (static_cast<Ch375Command>(command)) {
        case Ch375Command::AutoSetup:
            // Several control transfers, not an instant reply. A caller that
            // only worked when this finished in one pass would not survive a
            // real device.
            saw_auto_setup_ = true;
            auto_setup_running_ = true;
            auto_setup_at_us_ = now_us_;
            pending_status_ = static_cast<std::uint8_t>(
                fail_auto_setup_ ? InterruptStatus::BufferOver : InterruptStatus::Success);
            int_asserted_ = !silent_;
            break;

        case Ch375Command::TestConnect:
            // DS1 5.10: answers Connect, Disconnect or USB_READY. Unlike
            // GetStatus this does not consume the pending interrupt.
            queue(static_cast<std::uint8_t>(attached_ ? InterruptStatus::Connect
                                                      : InterruptStatus::Disconnect));
            break;

        case Ch375Command::GetStatus:
            auto_setup_running_ = false;
            queue(pending_status_);
            // Reading the status is what clears the request - DS1 5.12.
            int_asserted_ = false;
            pending_status_ = 0;
            break;

        case Ch375Command::ReadUsbData0:
        case Ch375Command::ReadUsbData:
            if (report_waiting_) {
                queue(static_cast<std::uint8_t>(report_.size()));
                for (std::uint8_t byte : report_) {
                    queue(byte);
                }
                report_waiting_ = false;
                report_.clear();
            } else {
                queue(0);
            }
            break;

        case Ch375Command::CheckExist:
        case Ch375Command::SetUsbMode:
        case Ch375Command::SetRetry:
        case Ch375Command::SetUsbAddress:
        case Ch375Command::IssueToken:
            expecting_data_ = true;
            break;

        default:
            break;
    }
}

void FakeCh375Chip::write_data(std::uint8_t value) {
    if (!expecting_data_) {
        return;
    }

    switch (static_cast<Ch375Command>(pending_command_)) {
        case Ch375Command::CheckExist:
            queue(static_cast<std::uint8_t>(~value));
            expecting_data_ = false;
            break;

        case Ch375Command::SetUsbMode: {
            const UsbMode mode = static_cast<UsbMode>(value);
            ++mode_set_count_;
            mode_ = mode;
            if (mode == UsbMode::HostReset) {
                saw_bus_reset_ = true;
                ++reset_count_;
                if (attached_ && report_disconnect_on_reset_) {
                    // Holding the bus in reset makes an attached device look
                    // gone to the chip's own detection, so it says so. On the
                    // bench that arrived as a disconnect the instant the reset
                    // began, over and over.
                    pending_status_ = static_cast<std::uint8_t>(InterruptStatus::Disconnect);
                    int_asserted_ = !silent_;
                }
            }
            queue(static_cast<std::uint8_t>(CommandStatus::Success));
            expecting_data_ = false;
            break;
        }

        case Ch375Command::IssueToken:
            // A real device answers a poll with an interrupt, whether or not
            // it had anything to say.
            pending_status_ = static_cast<std::uint8_t>(InterruptStatus::Success);
            int_asserted_ = !silent_;
            expecting_data_ = false;
            break;

        case Ch375Command::SetRetry:
            // Two data bytes: the 25H prefix and the policy. DS2 1.3.
            break;

        default:
            expecting_data_ = false;
            break;
    }
}

bool FakeCh375Chip::read_data(std::uint8_t& value) {
    if (outgoing_read_ < outgoing_.size()) {
        value = outgoing_[outgoing_read_++];
        if (outgoing_read_ == outgoing_.size()) {
            outgoing_.clear();
            outgoing_read_ = 0;
        }
        return true;
    }
    // Nothing to read. Time passes, which is what lets a bounded wait end.
    now_us_ += 10;
    return false;
}

bool FakeCh375Chip::int_asserted() const {
    // A real AUTO_SETUP is several control transfers over the USB bus, and a
    // caller that only worked when it finished within one pass of the loop
    // would not survive meeting a device.
    constexpr std::uint32_t kAutoSetupUs = 3000;
    if (auto_setup_running_ && now_us_ - auto_setup_at_us_ < kAutoSetupUs) {
        return false;
    }
    return int_asserted_;
}

void FakeCh375Chip::attach_device() {
    attached_ = true;
    pending_status_ = static_cast<std::uint8_t>(InterruptStatus::Connect);
    int_asserted_ = true;
}

void FakeCh375Chip::detach_device() {
    attached_ = false;
    report_waiting_ = false;
    report_.clear();
    pending_status_ = static_cast<std::uint8_t>(InterruptStatus::Disconnect);
    int_asserted_ = true;
}

void FakeCh375Chip::queue_report(const std::uint8_t* data, std::size_t size) {
    report_.assign(data, data + size);
    report_waiting_ = true;
}

// ---------------------------------------------------------------------------

void FakeDeviceSetup::begin(std::uint32_t now_us) {
    started_us_ = now_us;
    running_ = true;
}

SetupProgress FakeDeviceSetup::poll(std::uint32_t now_us) {
    if (!running_) {
        return SetupProgress::Failed;
    }
    if (failing_) {
        running_ = false;
        return SetupProgress::Failed;
    }
    // Deliberately not instant. Real enumeration is several control transfers,
    // and a lifecycle that only works when configuration finishes in one tick
    // would not survive meeting one.
    if (now_us - started_us_ < 5000) {
        return SetupProgress::Busy;
    }
    running_ = false;
    return SetupProgress::Done;
}

}  // namespace duo_input::u1::ch375::testing
