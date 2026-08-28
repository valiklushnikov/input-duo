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
    ++commands_written_;
    if (command == static_cast<std::uint8_t>(Ch375Command::ResetAll)) {
        reset_baud_.push_back(baud_);
    }
    if (unscripted_) {
        return;
    }
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
    ++data_written_;
    if (unscripted_) {
        return;
    }
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
    if (chip_baud_ != 0 && rx_baud_ != chip_baud_) {
        // The chip is speaking at one rate and this side is listening at
        // another. Nothing readable comes of that.
        now_us_ += 100;
        return false;
    }
    if (unscripted_) {
        // A chip that is not there never answers, and time still passes -
        // without which every wait for a reply here runs forever.
        now_us_ += 100;
        return false;
    }
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
    if (command == static_cast<std::uint8_t>(Ch375Command::CheckExist)) {
        ++check_exist_count_;
    }
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
            token_pending_ = false;
            pending_status_ = 0;
            break;

        case Ch375Command::GetDescriptor:
        case Ch375Command::SetAddress:
        case Ch375Command::SetConfiguration:
            expecting_data_ = true;
            break;

        case Ch375Command::ReadUsbData0:
        case Ch375Command::ReadUsbData:
            if (!pending_read_.empty()) {
                // A control transfer's answer, waiting to be collected.
                queue(static_cast<std::uint8_t>(pending_read_.size()));
                for (std::uint8_t byte : pending_read_) {
                    queue(byte);
                }
                pending_read_.clear();
            } else if (report_waiting_) {
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

        case Ch375Command::GetDeviceRate:
        case Ch375Command::SetUsbSpeed:
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

        case Ch375Command::GetDeviceRate:
            // DS2 1.2: bit 4 set means a 1.5 Mbps device.
            queue(static_cast<std::uint8_t>(low_speed_ ? 0x10 : 0x00));
            expecting_data_ = false;
            break;

        case Ch375Command::SetUsbSpeed:
            bus_speed_ = static_cast<UsbSpeed>(value);
            speed_after_mode_ = true;
            expecting_data_ = false;
            break;

        case Ch375Command::SetUsbMode: {
            const UsbMode mode = static_cast<UsbMode>(value);
            ++mode_set_count_;
            mode_ = mode;
            // The real chip puts the bus back to full speed here.
            bus_speed_ = UsbSpeed::Full12Mbps;
            speed_after_mode_ = false;
            if (mode == UsbMode::HostWithSof && attached_ && report_disconnect_settling_) {
                // Still catching up with the device after the reset.
                pending_status_ = static_cast<std::uint8_t>(InterruptStatus::Disconnect);
                int_asserted_ = !silent_;
            }
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
            // it had anything to say - but not instantly.
            ++tokens_issued_;
            pending_status_ = static_cast<std::uint8_t>(InterruptStatus::Success);
            expecting_data_ = false;
            if (token_delay_us_ == 0) {
                int_asserted_ = !silent_;
            } else {
                token_pending_ = true;
                token_ready_us_ = now_us_ + token_delay_us_;
            }
            break;

        case Ch375Command::SetRetry:
            // Two data bytes: the 25H prefix and the policy. DS2 1.3.
            break;

        case Ch375Command::GetDescriptor: {
            expecting_data_ = false;
            if (value == 1) {
                // A device descriptor. Only its shape matters here.
                pending_read_ = {18,   0x01, 0x10, 0x01, 0, 0, 0, 8,
                                 0x34, 0x12, 0x78, 0x56, 0, 1, 0, 0, 0, 1};
                read_device_descriptor_ = true;
            } else if (value == 2) {
                if (!read_device_descriptor_) {
                    // Asked for before the device descriptor, which is out of
                    // order - a real device is still on address zero here.
                    order_ok_ = false;
                }
                if (host_address_ != device_address_) {
                    // Read at an address the device no longer answers to.
                    order_ok_ = false;
                }
                pending_read_ = configuration_;
                read_configuration_ = true;
            }
            finish_transfer(false);
            break;
        }

        case Ch375Command::SetAddress:
            expecting_data_ = false;
            if (!read_device_descriptor_) {
                order_ok_ = false;
            }
            device_address_ = value;
            finish_transfer(false);
            break;

        case Ch375Command::SetUsbAddress:
            expecting_data_ = false;
            host_address_ = value;
            break;

        case Ch375Command::SetConfiguration:
            expecting_data_ = false;
            if (!read_configuration_) {
                order_ok_ = false;
            }
            configuration_value_ = value;
            finish_transfer(false);
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
    if (token_pending_) {
        // The transaction is still running on the bus.
        return !silent_ && static_cast<std::int32_t>(now_us_ - token_ready_us_) >= 0;
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
    begun_ = true;
}

SetupProgress FakeDeviceSetup::poll(std::uint32_t now_us, bool interrupted,
                                    InterruptStatus status) {
    (void)interrupted;
    (void)status;
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

// ---------------------------------------------------------------------------
// A device on the far side of the bus.
// ---------------------------------------------------------------------------

namespace duo_input::u1::ch375::testing {
namespace {

/// The descriptor records a real device sends, built the way a real one builds
/// them: length-prefixed and chained.
std::vector<std::uint8_t> configuration_header(std::size_t total, std::uint8_t interfaces) {
    return {9, 0x02, static_cast<std::uint8_t>(total & 0xFF),
            static_cast<std::uint8_t>(total >> 8), interfaces, 1, 0, 0x80, 50};
}

std::vector<std::uint8_t> interface_record(std::uint8_t number, std::uint8_t cls,
                                           std::uint8_t subclass, std::uint8_t protocol,
                                           std::uint8_t endpoints) {
    return {9, 0x04, number, 0, endpoints, cls, subclass, protocol, 0};
}

std::vector<std::uint8_t> endpoint_record(std::uint8_t address, std::uint16_t max_packet) {
    return {7, 0x05, address, 0x03, static_cast<std::uint8_t>(max_packet & 0xFF),
            static_cast<std::uint8_t>(max_packet >> 8), 10};
}

void append(std::vector<std::uint8_t>& into, const std::vector<std::uint8_t>& more) {
    into.insert(into.end(), more.begin(), more.end());
}

}  // namespace

void FakeCh375Chip::finish_transfer(bool stalled) {
    ++transfers_done_;
    const bool refuse = stalled || (stall_after_ >= 0 && transfers_done_ > stall_after_);
    if (refuse) {
        pending_read_.clear();
        // Bit 5 marks a failure, and 1110 in the low bits is a STALL - DS1
        // 5.12. A device that refuses a step is not the same as one that has
        // gone away, and the byte says which.
        pending_status_ = 0x2E;
    } else {
        pending_status_ = static_cast<std::uint8_t>(InterruptStatus::Success);
    }
    int_asserted_ = !silent_;
}

void FakeCh375Chip::serve_boot_mouse() {
    std::vector<std::uint8_t> body;
    append(body, interface_record(0, 0x03, 0x01, 0x02, 1));
    append(body, endpoint_record(0x82, 4));
    configuration_ = configuration_header(9 + body.size(), 1);
    append(configuration_, body);
}

void FakeCh375Chip::serve_composite_keyboard() {
    std::vector<std::uint8_t> body;
    append(body, interface_record(0, 0x03, 0x00, 0x00, 1));  // consumer controls
    append(body, endpoint_record(0x83, 4));
    append(body, interface_record(1, 0x03, 0x01, 0x01, 1));  // the keyboard
    append(body, endpoint_record(0x81, 8));
    configuration_ = configuration_header(9 + body.size(), 2);
    append(configuration_, body);
}

void FakeCh375Chip::serve_hub() {
    std::vector<std::uint8_t> body;
    append(body, interface_record(0, 0x09, 0x00, 0x00, 1));
    append(body, endpoint_record(0x81, 1));
    configuration_ = configuration_header(9 + body.size(), 1);
    append(configuration_, body);
}

}  // namespace duo_input::u1::ch375::testing
