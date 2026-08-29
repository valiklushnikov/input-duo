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

bool FakeCh375Chip::set_baud(unsigned baud) {
    if (baud == 0) {
        return false;
    }
    port_baud_ = baud;
    port_rx_baud_ = baud;
    return true;
}

bool FakeCh375Chip::set_rx_baud(unsigned baud) {
    if (baud == 0) {
        return false;
    }
    port_rx_baud_ = baud;
    return true;
}

void FakeCh375Chip::write_command(std::uint8_t command) {
    if (deaf_countdown_ >= 0) {
        // Counted at the command boundary, so a reply already begun is never
        // cut in half - a chip that has lost sync stops responding to
        // commands, it does not truncate a block it is part way through
        // sending.
        if (deaf_countdown_ == 0) {
            silent_ = true;
        } else {
            --deaf_countdown_;
        }
    }
    if (command == static_cast<std::uint8_t>(Ch375Command::CheckExist)) {
        ++check_exist_count_;
    }
    ++command_count_;
    command_bauds_.push_back(port_baud_);

    if (!chip_hears()) {
        // Counted above and then dropped: the command was written, which is
        // what a caller's own counters see, and the chip never received it.
        // Nothing is queued, so nothing comes back - which from this side is
        // indistinguishable from a chip that is not there, and is exactly the
        // fault a search across rates exists to tell apart.
        pending_command_ = 0;
        expecting_data_ = false;
        return;
    }

    if (nak_retry_in_progress_) {
        if (command == static_cast<std::uint8_t>(Ch375Command::AbortNak)) {
            nak_retry_in_progress_ = false;
            control_naks_ = 0;
            ++abort_nak_count_;
        }
        return;
    }

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
            if (hold_int_unanswered_) {
                // A chip that raised its interrupt and then stopped talking.
                // The request is not cleared, so the line stays asserted and
                // the status is asked for again on the very next tick.
                break;
            }
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
            if (block_reads_break_at_ != 0 && port_baud_ >= block_reads_break_at_) {
                // The chip sends the block; nothing readable comes back. The
                // caller sees the length byte never arrive, which is what the
                // bench saw at 115200 and 62500 while CHECK_EXIST answered
                // perfectly at both. What is waiting stays waiting: this side
                // could not decode it, and the chip does not know that.
                break;
            }
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

        case Ch375Command::WriteUsbData7:
            // A length, then that many bytes. Nothing is decided until the
            // token that carries the block goes out.
            outbound_block_.clear();
            block_remaining_ = -1;
            expecting_data_ = true;
            break;

        case Ch375Command::ResetAll:
            // DS1 5.2 and 5.4: the port goes back to the rate the chip comes
            // up at, whatever it was doing before. Everything else the chip
            // was in the middle of is out of scope here - what the tests are
            // about is that a chip found at an abandoned rate can be told to
            // come home, and does.
            chip_baud_ = kScriptedDefaultBaud;
            outgoing_.clear();
            outgoing_read_ = 0;
            retry_policy_ = kChipDefaultRetry;
            retry_prefix_seen_ = false;
            nak_retry_in_progress_ = false;
            break;

        case Ch375Command::AbortNak:
            ++abort_nak_count_;
            break;

        case Ch375Command::SetBaudRate:
            baud_coefficient_seen_ = false;
            expecting_data_ = true;
            break;

        case Ch375Command::GetDeviceRate:
        case Ch375Command::SetUsbSpeed:
        case Ch375Command::CheckExist:
        case Ch375Command::SetUsbMode:
        case Ch375Command::SetRetry:
        case Ch375Command::SetUsbAddress:
        case Ch375Command::SetEndpoint6:
        case Ch375Command::SetEndpoint7:
        case Ch375Command::IssueToken:
            expecting_data_ = true;
            break;

        default:
            break;
    }
}

void FakeCh375Chip::write_data(std::uint8_t value) {
    if (!chip_hears() || !expecting_data_) {
        return;
    }

    switch (static_cast<Ch375Command>(pending_command_)) {
        case Ch375Command::CheckExist:
            expecting_data_ = false;
            if (missed_check_exists_ > 0) {
                // The command was heard and the answer never arrived. One byte
                // on a wire, not a chip that has gone.
                --missed_check_exists_;
                break;
            }
            queue(static_cast<std::uint8_t>(~value));
            break;

        case Ch375Command::GetDeviceRate:
            // DS2 1.2: bit 4 set means a 1.5 Mbps device.
            queue(static_cast<std::uint8_t>(low_speed_ ? 0x10 : 0x00));
            expecting_data_ = false;
            break;

        case Ch375Command::SetBaudRate: {
            // A coefficient and a constant (DS1 5.2). Only the rates this
            // firmware asks for are modelled; anything else is a divisor the
            // chip on this bench was never given, and answering it would let a
            // test pass on a rate that does not exist.
            if (!baud_coefficient_seen_) {
                baud_coefficient_ = value;
                baud_coefficient_seen_ = true;
                break;
            }
            baud_coefficient_seen_ = false;
            expecting_data_ = false;
            for (std::size_t index = 0; index < kBaudLadderSize; ++index) {
                if (kBaudLadder[index].coefficient != baud_coefficient_ ||
                    kBaudLadder[index].constant != value) {
                    continue;
                }
                // The chip moves first and answers at the new rate, which is
                // the whole difficulty of this command: a receiver still set
                // to the old one reads silence and calls a chip that moved a
                // chip that refused.
                chip_baud_ = kBaudLadder[index].baud;
                queue(static_cast<std::uint8_t>(CommandStatus::Success));
                break;
            }
            break;
        }

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
            retry_policy_ = kChipDefaultRetry;
            retry_after_mode_ = false;
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
            if (next_mode_reply_ == 1) {
                next_mode_reply_ = 0;
            } else if (next_mode_reply_ == 2) {
                next_mode_reply_ = 0;
                queue(static_cast<std::uint8_t>(CommandStatus::Abort));
            } else {
                queue(static_cast<std::uint8_t>(CommandStatus::Success));
            }
            expecting_data_ = false;
            break;
        }

        case Ch375Command::WriteUsbData7:
            if (block_remaining_ < 0) {
                block_remaining_ = static_cast<int>(value);
            } else if (block_remaining_ > 0) {
                outbound_block_.push_back(value);
                --block_remaining_;
            }
            if (block_remaining_ == 0) {
                block_remaining_ = -1;
                expecting_data_ = false;
            }
            break;

        case Ch375Command::SetEndpoint6:
            // The receiver's data toggle, set by hand (DS2 1.6). Acted on for
            // a control read and recorded otherwise: a data stage whose toggle
            // does not match what the device is sending gets nothing back at
            // all - no data, no error, no interrupt - which is the failure
            // that looked like a hundred and twenty empty polls on the bench.
            receive_toggle_ = value;
            expecting_data_ = false;
            break;

        case Ch375Command::SetEndpoint7:
            // The transmitter's (DS2 1.7). The status stage of a control read
            // is an empty DATA1, and a host that does not say so sends DATA0.
            transmit_toggle_ = value;
            expecting_data_ = false;
            break;

        case Ch375Command::IssueToken: {
            expecting_data_ = false;
            // DS2 1.15: the high nibble is the endpoint, the low nibble the
            // PID. Endpoint zero is where control transfers happen, and this
            // chip has no command for most of them.
            const std::uint8_t endpoint = static_cast<std::uint8_t>(value >> 4);
            const TokenPid pid = static_cast<TokenPid>(value & 0x0F);
            if (endpoint == 0 && pid == TokenPid::Setup) {
                begin_control_transfer();
                break;
            }
            // An IN at endpoint zero is either the data stage of a transfer
            // that reads, or the status stage of one that does not. Which it
            // is depends on whether a read is open and still has bytes left.
            if (endpoint == 0 && pid == TokenPid::In) {
                if (control_read_open_ && control_read_at_ < control_read_total_) {
                    serve_control_read_packet();
                } else {
                    finish_control_stage();
                }
                break;
            }
            // An OUT at endpoint zero is the status stage of a transfer that
            // read data: the host acknowledges with an empty packet rather
            // than asking for one (USB 2.0 8.5.3).
            if (endpoint == 0 && pid == TokenPid::Out) {
                if (control_read_open_) {
                    ++control_read_status_stages_;
                    control_read_open_ = false;
                }
                finish_control_stage();
                break;
            }
            ++tokens_issued_;
            if (!report_waiting_) {
                // A HID device sends a report when something changes and NAKs
                // every poll in between. What the chip does with that is the
                // retry policy's business (DS2 1.3): reported to the MCU as a
                // failure status, or retried on the bus until the device has
                // something - and under the second the transaction never
                // finishes, so no interrupt is ever raised.
                if (retries_naks()) {
                    break;
                }
                pending_status_ = idle_token_status_;
            } else {
                pending_status_ = static_cast<std::uint8_t>(InterruptStatus::Success);
            }
            // A real controller does not answer instantly.
            if (token_delay_us_ == 0) {
                int_asserted_ = !silent_;
            } else {
                token_pending_ = true;
                token_ready_us_ = now_us_ + token_delay_us_;
            }
            break;
        }

        case Ch375Command::SetRetry:
            // Two data bytes: the 25H prefix and the policy. DS2 1.3.
            if (!retry_prefix_seen_) {
                retry_prefix_seen_ = true;
                break;
            }
            retry_prefix_seen_ = false;
            expecting_data_ = false;
            if (drop_retry_policy_ && value == dropped_retry_policy_) {
                drop_retry_policy_ = false;
                break;
            }
            retry_policy_ = value;
            retry_after_mode_ = true;
            break;

        case Ch375Command::GetDescriptor: {
            expecting_data_ = false;
            if (control_transfer_naks()) {
                break;
            }
            if (value == 1) {
                // A device descriptor. Only its shape matters here.
                pending_read_ = {18,   0x01, 0x10, 0x01, 0, 0, 0, control_packet_,
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
            if (control_transfer_naks()) {
                break;
            }
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
            if (control_transfer_naks()) {
                break;
            }
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
    if (!chip_is_audible()) {
        // The chip may well be talking. Nothing readable comes of it while
        // this side is sampling at another rate, and time still passes -
        // without which every bounded wait here would run forever.
        now_us_ += 10;
        return false;
    }
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
    if (hold_int_unanswered_) {
        // Asserted and never cleared: only reading the status clears it, and
        // this chip does not answer that.
        return true;
    }
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

void FakeCh375Chip::power_cycle() {
    chip_baud_ = kScriptedDefaultBaud;
    mode_ = UsbMode::DeviceDisabled;
    outgoing_.clear();
    outgoing_read_ = 0;
    pending_status_ = 0;
    int_asserted_ = false;
    token_pending_ = false;
    expecting_data_ = false;
    pending_command_ = 0;
    nak_retry_in_progress_ = false;
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
    if (transport_ != nullptr) {
        std::uint8_t buffer[kMaxBlockSize];
        std::size_t size = 0;
        if (!transport_->read_block(buffer, sizeof(buffer), size)) {
            // A descriptor that cannot be read is a device that cannot be
            // configured, which is what the real setup reports as unreadable.
            // Tried again until the step's own deadline, because one lost
            // block is not the same as a rate that carries none.
            if (now_us - started_us_ >= kSetupTimeoutUs) {
                running_ = false;
                return SetupProgress::Failed;
            }
            return SetupProgress::Busy;
        }
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

/// HID 1.11 6.2.1. The record that says how long the report descriptor is,
/// and the only place that number exists.
std::vector<std::uint8_t> hid_record(std::uint16_t report_length) {
    return {9,
            0x21,
            0x11,
            0x01,
            0,
            1,
            0x22,
            static_cast<std::uint8_t>(report_length & 0xFF),
            static_cast<std::uint8_t>(report_length >> 8)};
}

std::vector<std::uint8_t> endpoint_record(std::uint8_t address, std::uint16_t max_packet) {
    return {7, 0x05, address, 0x03, static_cast<std::uint8_t>(max_packet & 0xFF),
            static_cast<std::uint8_t>(max_packet >> 8), 10};
}

void append(std::vector<std::uint8_t>& into, const std::vector<std::uint8_t>& more) {
    into.insert(into.end(), more.begin(), more.end());
}

/// A USB setup packet is eight bytes, always.
constexpr std::size_t kSetupPacketSize = 8;
/// Host to device, class request, addressed to an interface. USB 2.0 9.3.1.
constexpr std::uint8_t kRequestTypeInterfaceOut = 0x21;
/// HID 1.11 7.2.5. Written out here rather than taken from the firmware, so
/// that a test cannot agree with a wrong constant by sharing it.
constexpr std::uint8_t kRequestSetProtocol = 0x0B;
/// USB 2.0 9.3.1: device to host, standard request, to an interface.
constexpr std::uint8_t kRequestTypeInterfaceIn = 0x81;
/// USB 2.0 9.4.3 and HID 1.11 7.1.1: GET_DESCRIPTOR of a report descriptor,
/// whose type is the high byte of wValue.
constexpr std::uint8_t kRequestGetDescriptor = 0x06;
constexpr std::uint8_t kDescriptorReport = 0x22;

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

bool FakeCh375Chip::control_transfer_naks() {
    if (control_naks_ <= 0) {
        return false;
    }
    if (retries_naks()) {
        if (hold_control_nak_retry_) {
            nak_retry_in_progress_ = true;
            return true;
        }
        // The chip asks again itself, on the bus, without telling anyone - so
        // every NAK the device still had in it is spent inside this one
        // transaction, and what the MCU sees is a transfer that completed.
        control_naks_ = 0;
        return false;
    }
    --control_naks_;
    pending_read_.clear();
    // Bit 5 marks a failure and 1010 is a NAK (DS1 5.12). This is the byte the
    // mouse channel reported fifty-five times over.
    pending_status_ = static_cast<std::uint8_t>(0x20 | kResponseNak);
    int_asserted_ = !silent_;
    return true;
}

void FakeCh375Chip::begin_control_transfer() {
    setup_packets_.push_back(outbound_block_);
    control_pending_ = false;
    // A new setup packet ends whatever transfer was open. That is the real
    // rule (USB 2.0 8.5.3) and it is also what keeps an abandoned read from
    // leaking its remaining bytes into the next request's answer.
    control_read_open_ = false;
    control_read_at_ = 0;
    control_read_total_ = 0;

    const bool is_report_descriptor_read =
        outbound_block_.size() == kSetupPacketSize &&
        outbound_block_[0] == kRequestTypeInterfaceIn &&
        outbound_block_[1] == kRequestGetDescriptor && outbound_block_[3] == kDescriptorReport;
    if (is_report_descriptor_read) {
        ++report_descriptor_requests_;
        report_descriptor_asked_ = static_cast<std::uint16_t>(
            outbound_block_[6] | (static_cast<std::uint16_t>(outbound_block_[7]) << 8));
        if (ignore_report_descriptor_) {
            // No data, no interrupt, no clue. Only a deadline ends this.
            pending_read_.clear();
            return;
        }
        if (refuse_report_descriptor_) {
            pending_read_.clear();
            pending_status_ = 0x2E;
            int_asserted_ = !silent_;
            return;
        }
        control_read_total_ = report_descriptor_.size() < report_descriptor_asked_
                                  ? report_descriptor_.size()
                                  : report_descriptor_asked_;
        control_read_open_ = true;
        control_read_data1_ = true;
        // The setup packet's own interrupt carries no data with it; the bytes
        // come back one IN transaction at a time after this.
        pending_read_.clear();
        finish_transfer(false);
        return;
    }

    if (control_transfer_naks()) {
        return;
    }
    if (ignore_setup_) {
        // Nothing comes back at all. No data, no interrupt, no clue - which
        // is the case a bounded wait exists for.
        pending_read_.clear();
        return;
    }
    if (refuse_setup_) {
        pending_read_.clear();
        // Bit 5 marks a failure and 1110 is a STALL (DS1 5.12). A device that
        // does not implement a request refuses it exactly this way.
        pending_status_ = 0x2E;
        int_asserted_ = !silent_;
        return;
    }

    control_pending_ = outbound_block_.size() == kSetupPacketSize;
    finish_transfer(false);
}

void FakeCh375Chip::serve_control_read_packet() {
    // A control read's data stage starts at DATA1 and alternates (USB 2.0
    // 8.6). A host that asks with the wrong one is asking for a packet the
    // device is not sending, and the transaction simply never completes.
    const std::uint8_t expected = control_read_data1_ ? kToggleData1 : kToggleData0;
    if (receive_toggle_ != expected) {
        return;
    }
    control_read_data1_ = !control_read_data1_;
    const std::size_t left = control_read_total_ - control_read_at_;
    const std::size_t take = left < control_packet_ ? left : control_packet_;
    pending_read_.assign(report_descriptor_.begin() + static_cast<std::ptrdiff_t>(control_read_at_),
                         report_descriptor_.begin() +
                             static_cast<std::ptrdiff_t>(control_read_at_ + take));
    control_read_at_ += take;
    ++report_descriptor_packets_;
    pending_status_ = static_cast<std::uint8_t>(InterruptStatus::Success);
    int_asserted_ = !silent_;
}

void FakeCh375Chip::finish_control_stage() {
    ++control_status_stages_;
    if (control_pending_) {
        // A device acts on a request when the transfer completes, not when
        // the setup packet lands. Half a transfer changes nothing.
        const std::vector<std::uint8_t>& packet = setup_packets_.back();
        const std::uint16_t value = static_cast<std::uint16_t>(
            packet[2] | (static_cast<std::uint16_t>(packet[3]) << 8));
        if (packet[0] == kRequestTypeInterfaceOut && packet[1] == kRequestSetProtocol) {
            // wValue 0 is boot protocol, 1 is report protocol (HID 1.11 7.2.6).
            boot_protocol_ = value == 0;
        }
    }
    control_pending_ = false;
    // A request with no data stage has nothing to hand back.
    pending_read_.clear();
    pending_status_ = static_cast<std::uint8_t>(InterruptStatus::Success);
    int_asserted_ = !silent_;
}

std::vector<std::uint8_t> FakeCh375Chip::report_for(std::int8_t dx, std::int8_t dy) const {
    const std::uint8_t x = static_cast<std::uint8_t>(dx);
    const std::uint8_t y = static_cast<std::uint8_t>(dy);
    if (boot_protocol_) {
        // Buttons, X, Y, wheel. No identifier: that is what boot protocol is.
        return {0x00, x, y, 0x00};
    }
    // What the mouse on the bench sends when nobody asked it to switch: seven
    // bytes led by a Report ID, with the real buttons behind it.
    return {0x01, 0x00, x, y, 0x00, 0x00, 0x00};
}

void FakeCh375Chip::serve_boot_mouse() {
    std::vector<std::uint8_t> body;
    append(body, interface_record(0, 0x03, 0x01, 0x02, 1));
    append(body, endpoint_record(0x82, 4));
    configuration_ = configuration_header(9 + body.size(), 1);
    append(configuration_, body);
}

void FakeCh375Chip::serve_mouse_without_boot() {
    std::vector<std::uint8_t> body;
    // Subclass 0: the mouse protocol, but no boot report behind it.
    append(body, interface_record(0, 0x03, 0x00, 0x02, 1));
    append(body, endpoint_record(0x82, 4));
    configuration_ = configuration_header(9 + body.size(), 1);
    append(configuration_, body);
}

void FakeCh375Chip::serve_mouse_with_report_descriptor(
    const std::vector<std::uint8_t>& descriptor, bool boot_subclass) {
    report_descriptor_ = descriptor;
    std::vector<std::uint8_t> body;
    append(body, interface_record(0, 0x03, boot_subclass ? 0x01 : 0x00, 0x02, 1));
    append(body, hid_record(static_cast<std::uint16_t>(descriptor.size())));
    append(body, endpoint_record(0x82, 8));
    configuration_ = configuration_header(9 + body.size(), 1);
    append(configuration_, body);
}

void FakeCh375Chip::serve_composite_mouse_with_report_descriptor(
    const std::vector<std::uint8_t>& descriptor) {
    report_descriptor_ = descriptor;
    std::vector<std::uint8_t> body;
    // Consumer controls first, with a report descriptor of its own length, so
    // that a request sent to interface zero would look plausible rather than
    // failing outright.
    append(body, interface_record(0, 0x03, 0x00, 0x00, 1));
    append(body, hid_record(25));
    append(body, endpoint_record(0x83, 4));
    append(body, interface_record(1, 0x03, 0x00, 0x02, 1));
    append(body, hid_record(static_cast<std::uint16_t>(descriptor.size())));
    append(body, endpoint_record(0x82, 8));
    configuration_ = configuration_header(9 + body.size(), 2);
    append(configuration_, body);
}

void FakeCh375Chip::serve_keyboard_with_report_descriptor(
    const std::vector<std::uint8_t>& descriptor) {
    report_descriptor_ = descriptor;
    std::vector<std::uint8_t> body;
    append(body, interface_record(0, 0x03, 0x01, 0x01, 1));
    append(body, hid_record(static_cast<std::uint16_t>(descriptor.size())));
    append(body, endpoint_record(0x81, 8));
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
