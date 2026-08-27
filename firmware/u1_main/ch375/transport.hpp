#pragma once

// The CH375 command layer, with every wait bounded.
//
// It is given a port that can send a command byte, send a data byte, take a
// byte if one has arrived, read the interrupt line and say what time it is.
// Nothing here knows about UARTs, pins or PIO, so the same code runs against a
// written-down conversation in the tests and against hardware later.
//
// The two write operations are separate because the chip's serial framing
// makes them separate: DS1 section 6.2.2 gives the format as nine data bits,
// where the ninth says whether the other eight are a command or data. A port
// implementation carries that bit however it can.
//
// Nothing in here blocks indefinitely. This device routes someone's typing
// between two computers; firmware spinning on a byte that will never come
// stops feeding the watchdog, drops the link to U2, and leaves whatever was
// held down held down. A USB host controller that has stopped answering is a
// problem to report, not a reason to stop.

#include <cstddef>
#include <cstdint>

#include "ch375/commands.hpp"

namespace duo_input::u1::ch375 {

/// The byte-level port. Implemented by the hardware and by the test fake.
class ICh375Transport {
public:
    virtual ~ICh375Transport() = default;

    /// Send one byte marked as a command.
    virtual void write_command(std::uint8_t command) = 0;

    /// Send one byte marked as data.
    virtual void write_data(std::uint8_t value) = 0;

    /// Take one byte if one has arrived. Never blocks; false means nothing yet.
    virtual bool read_data(std::uint8_t& value) = 0;

    /// Is the chip's interrupt line asserted?
    virtual bool int_asserted() const = 0;

    virtual std::uint32_t now_us() const = 0;
};

/// How long to wait for a byte the chip owes us.
///
/// Every documented command answers within tens of microseconds; a millisecond
/// is far past "slow" and well short of anything a person would notice.
inline constexpr std::uint32_t kDefaultReplyTimeoutUs = 1000;

class Ch375Transport {
public:
    explicit Ch375Transport(ICh375Transport& io) : io_(io) {}

    std::uint32_t reply_timeout_us() const { return reply_timeout_us_; }
    void set_reply_timeout_us(std::uint32_t micros) { reply_timeout_us_ = micros; }

    /// DS1 5.5. Send a byte, expect its bitwise inverse.
    ///
    /// A chip that answers wrongly is worse than one that says nothing - it
    /// looks alive - so only the exact inverse counts.
    bool check_exist(std::uint8_t probe) {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::CheckExist));
        io_.write_data(probe);

        std::uint8_t answer = 0;
        if (!read_reply(answer)) {
            return false;
        }
        return answer == static_cast<std::uint8_t>(~probe);
    }

    /// DS1 5.9. Set the USB working mode.
    bool set_usb_mode(UsbMode mode) {
        return command_with_status(Ch375Command::SetUsbMode, static_cast<std::uint8_t>(mode));
    }

    /// DS2 1.3. Decide what happens when a device answers NAK.
    bool set_retry(std::uint8_t policy) {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::SetRetry));
        io_.write_data(kSetRetryPrefix);
        io_.write_data(policy);
        return true;  // DS2 1.3 documents no reply.
    }

    /// DS2 1.5. Tell the chip which device address it is talking to.
    bool set_usb_address(std::uint8_t address) {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::SetUsbAddress));
        io_.write_data(address);
        return true;  // DS2 1.5 documents no reply.
    }

    /// DS1 5.12. Read the interrupt status and clear the request.
    ///
    /// Any byte is carried through, including ones this code does not
    /// recognise: the failure statuses are a 32-value range that encodes which
    /// PID the device answered with, and that byte is the only evidence of why
    /// a transaction failed.
    bool get_status(InterruptStatus& status) {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::GetStatus));

        std::uint8_t answer = 0;
        if (!read_reply(answer)) {
            return false;
        }
        status = static_cast<InterruptStatus>(answer);
        return true;
    }

    /// DS2 1.8. Read a length-prefixed block from the endpoint buffer.
    ///
    /// A length of zero is a success: a polled endpoint with nothing to say
    /// answers that constantly, and treating it as an error would make an idle
    /// keyboard look like a broken one.
    bool read_block(std::uint8_t* out, std::size_t capacity, std::size_t& size) {
        size = 0;
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::ReadUsbData0));

        std::uint8_t length = 0;
        if (!read_reply(length)) {
            return false;
        }
        if (length > kMaxBlockSize || length > capacity) {
            // Refused before a single byte is stored. A length past the chip's
            // own maximum means the port is out of step rather than that a
            // longer packet arrived, and a length past the caller's buffer is
            // how a device on the far end of a wire writes into this one.
            return false;
        }

        for (std::size_t index = 0; index < length; ++index) {
            std::uint8_t value = 0;
            if (!read_reply(value)) {
                size = 0;
                return false;
            }
            out[index] = value;
        }
        size = length;
        return true;
    }

    /// DS1 5.14. Write a length-prefixed block to the host endpoint buffer.
    bool write_block(const std::uint8_t* data, std::size_t size) {
        if (size > kMaxBlockSize) {
            // Refused before the command byte, so the chip is still in step
            // and the next command will be understood.
            return false;
        }
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::WriteUsbData7));
        io_.write_data(static_cast<std::uint8_t>(size));
        for (std::size_t index = 0; index < size; ++index) {
            io_.write_data(data[index]);
        }
        return true;
    }

    /// DS2 1.15. Issue a token; the interrupt that follows carries the result.
    bool issue_token(std::uint8_t endpoint, TokenPid pid) {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::IssueToken));
        io_.write_data(transaction(endpoint, pid));
        return true;
    }

    /// DS1 5.10. Ask whether a device is attached, rather than waiting to be
    /// told.
    ///
    /// The connect interrupt can be missed - it is cleared by reading it, and
    /// a chip that was answering nonsense at the time consumes it for nothing.
    /// It also never arrives for a device that was already plugged in when the
    /// power came on. Asking covers both.
    bool test_connect(InterruptStatus& status) {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::TestConnect));

        std::uint8_t answer = 0;
        if (!read_reply(answer)) {
            return false;
        }
        status = static_cast<InterruptStatus>(answer);
        return true;
    }

    /// Is the chip asking for attention right now?
    ///
    /// Reading the status is what clears the request, so a caller should ask
    /// this first rather than reading a status that belongs to nothing.
    bool interrupt_pending() const { return io_.int_asserted(); }

    std::uint32_t now_us() const { return io_.now_us(); }

    /// Wait for the chip's interrupt line, but not past ``deadline_us``.
    bool wait_for_interrupt(std::uint32_t deadline_us) {
        while (true) {
            if (io_.int_asserted()) {
                return true;
            }
            if (expired(deadline_us)) {
                return false;
            }
            // Reading drives the fake's clock and drains anything stale on
            // real hardware. The value is deliberately discarded: an interrupt
            // is what is being waited for, not a byte.
            std::uint8_t discarded = 0;
            (void)io_.read_data(discarded);
        }
    }

private:
    /// Has ``deadline_us`` passed?
    ///
    /// Subtraction, not comparison. The clock wraps every 71 minutes, and a
    /// deadline compared directly would look like an enormous wait for the
    /// hour on one side of the wrap and no wait at all on the other.
    bool expired(std::uint32_t deadline_us) const {
        return static_cast<std::int32_t>(io_.now_us() - deadline_us) >= 0;
    }

    bool read_reply(std::uint8_t& value) {
        const std::uint32_t deadline = io_.now_us() + reply_timeout_us_;
        while (true) {
            if (io_.read_data(value)) {
                return true;
            }
            if (expired(deadline)) {
                return false;
            }
        }
    }

    bool command_with_status(Ch375Command command, std::uint8_t argument) {
        io_.write_command(static_cast<std::uint8_t>(command));
        io_.write_data(argument);

        std::uint8_t answer = 0;
        if (!read_reply(answer)) {
            return false;
        }
        // 51H and 5FH are the whole documented set. Anything else means the
        // port has lost step, and calling that success would let a
        // desynchronised chip pass for a working one.
        return answer == static_cast<std::uint8_t>(CommandStatus::Success);
    }

    ICh375Transport& io_;
    std::uint32_t reply_timeout_us_ = kDefaultReplyTimeoutUs;
};

}  // namespace duo_input::u1::ch375
