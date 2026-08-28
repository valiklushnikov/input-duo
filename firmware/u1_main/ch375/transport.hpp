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

    /// Change the port's own speed, if it can.
    ///
    /// Returns false when the port has a fixed rate. Answering true without
    /// changing anything would leave the chip talking at a speed nothing on
    /// this side is listening at, which is silence that looks like a dead
    /// chip.
    virtual bool set_baud(unsigned) { return false; }
};

/// How long to wait for a byte the chip owes us.
///
/// The chip's own processing is documented in microseconds, but the byte still
/// has to cross the wire: eleven bits at 9600 bps is 1.15 ms, so a timeout of
/// one millisecond gives up before a perfectly good answer has finished
/// arriving - and then reads the tail of it as the start of the next one.
///
/// That happened. Twenty milliseconds is far past any real answer and still
/// far below anything a person would notice.
inline constexpr std::uint32_t kDefaultReplyTimeoutUs = 20000;

class Ch375Transport {
public:
    explicit Ch375Transport(ICh375Transport& io) : io_(io) {}

    std::uint32_t reply_timeout_us() const { return reply_timeout_us_; }
    void set_reply_timeout_us(std::uint32_t micros) { reply_timeout_us_ = micros; }

    /// Start CHECK_EXIST without supplying its data byte.
    ///
    /// During bring-up this separates a real controller response from the
    /// transmit frame coupling into the receive path. Until the data byte
    /// arrives, the command is incomplete and the chip has no valid reply.
    void start_check_exist() {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::CheckExist));
    }

    /// DS1 5.5. Send a byte, expect its bitwise inverse.
    ///
    /// A chip that answers wrongly is worse than one that says nothing - it
    /// looks alive - so only the exact inverse counts.
    bool check_exist(std::uint8_t probe) {
        start_check_exist();
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

    /// DS2 1.6. Tell the receiver which data packet to expect next.
    ///
    /// The data toggle is not tracked by the chip - it is set by hand, and it
    /// has to be set before the first IN transaction of a new device. Without
    /// it a token is issued and nothing happens at all: no data, no error, and
    /// no interrupt, so the transaction simply never completes.
    ///
    /// kToggleData0 expects DATA0, kToggleData1 expects DATA1.
    void set_receive_toggle(std::uint8_t mode) {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::SetEndpoint6));
        io_.write_data(mode);
    }

    /// DS2 1.15. Issue a token; the interrupt that follows carries the result.
    bool issue_token(std::uint8_t endpoint, TokenPid pid) {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::IssueToken));
        io_.write_data(transaction(endpoint, pid));
        return true;
    }

    /// DS1 5.2. Raise the port speed, and the chip's with it.
    ///
    /// At 9600 the port is the slowest thing in the system by a wide margin.
    /// A frame is eleven bits - the ninth data bit is the command flag - so a
    /// byte costs 1.15 ms, and reading one mouse report takes fifteen of them
    /// between the status, the length, the data and the next token. Seventeen
    /// milliseconds to collect a report a moving mouse produces every eight is
    /// a deficit that never closes while the hand keeps moving.
    ///
    /// The chip replies at the *new* rate (DS1 5.2), so the port has to be
    /// switched between sending this and reading the answer. It also needs
    /// about a millisecond to make the change, during which it says nothing.
    ///
    /// Re-establish this after every chip reset: RESET_ALL returns the port to
    /// 9600, and a host still talking at the old speed to a chip that has gone
    /// back to the default is the same silence as a chip that is not there.
    bool set_baud_rate(std::uint8_t coefficient, std::uint8_t constant, unsigned baud) {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::SetBaudRate));
        io_.write_data(coefficient);
        io_.write_data(constant);

        if (!io_.set_baud(baud)) {
            return false;
        }

        std::uint8_t answer = 0;
        if (!read_reply(answer)) {
            return false;
        }
        return answer == static_cast<std::uint8_t>(CommandStatus::Success);
    }

    /// Put this side back to the rate a chip comes up at.
    ///
    /// RESET_ALL returns the chip to 9600 whatever it was doing before, so a
    /// port left at the raised rate is talking to something that is no longer
    /// listening at it.
    bool reset_port_speed(unsigned default_baud) { return io_.set_baud(default_baud); }

    /// Raise both ends, and find out where they are if that fails.
    ///
    /// A chip that did not answer at the new rate may never have left the old
    /// one - the command may not have arrived at all. Going back and asking is
    /// the only way to know; leaving both ends guessing gives a chip that
    /// looks dead and is not.
    ///
    /// Returns false when the port stayed at ``slow``, which is a working
    /// system and a slow one rather than a broken one.
    bool raise_speed(std::uint8_t coefficient, std::uint8_t constant, unsigned fast,
                     unsigned slow) {
        if (set_baud_rate(coefficient, constant, fast)) {
            return true;
        }

        // No answer at the new rate has two opposite causes. The chip may
        // never have heard the command, in which case it is still at the old
        // rate; or it may have changed and had its one-byte answer lost, in
        // which case it is at the new one. Guessing picks the wrong one half
        // the time and leaves a chip that looks dead and is not - which costs
        // somebody a trip to the board to pull its power.
        //
        // So ask. CHECK_EXIST is the one command that proves the port itself
        // works, and it is safe to repeat.
        if (check_exist(kPortProbeByte)) {
            return true;
        }
        io_.set_baud(slow);
        (void)check_exist(kPortProbeByte);
        return false;
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

    /// DS1 5.4. Reset the chip and everything it was in the middle of.
    ///
    /// It answers nothing and takes about 40 ms, which the caller has to wait
    /// out. Worth it at the start of every attempt: reaching this code does
    /// not mean the chip just powered on - a firmware update restarts the
    /// processor and leaves the controller exactly as the last run left it,
    /// including states it will not come out of by itself.
    void reset_all() {
        // Data bytes first, then the command.
        //
        // The chip reads commands positionally: a command byte, then however
        // many data bytes that command takes. Interrupt the sequence - which
        // is what restarting U1 in the middle of one does - and it is left
        // waiting for data, so the next *command* byte is swallowed as the
        // parameter it was waiting for and every byte after that is out of
        // step. From the outside that is a chip which has stopped answering,
        // and no amount of resetting helps if the reset command is eaten too.
        //
        // Four is more than any command here takes, so whatever it was waiting
        // for is satisfied before the reset is sent.
        for (int filler = 0; filler < 4; ++filler) {
            io_.write_data(0x00);
        }
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::ResetAll));
    }

    /// DS2 1.2. Is the attached device a low-speed one?
    ///
    /// Takes the byte 07H and answers a rate type; bit 4 set means 1.5 Mbps.
    /// Only valid in host mode 5, before frames are being generated.
    bool get_device_rate(bool& low_speed) {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::GetDeviceRate));
        io_.write_data(kGetDeviceRatePrefix);

        std::uint8_t answer = 0;
        if (!read_reply(answer)) {
            return false;
        }
        low_speed = (answer & 0x10) != 0;
        return true;
    }

    /// DS2 1.1. Set the bus speed.
    ///
    /// It returns to 12 Mbps whenever the working mode is set, so this has to
    /// come after that, not before. A low-speed device addressed at full speed
    /// does not answer, and a controller reports something that does not
    /// answer as gone.
    void set_usb_speed(UsbSpeed speed) {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::SetUsbSpeed));
        io_.write_data(static_cast<std::uint8_t>(speed));
    }

    /// DS2 1.11. Fetch a descriptor. The interrupt that follows says whether
    /// it worked; the bytes are then collected with read_block.
    void get_descriptor(DescriptorType type) {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::GetDescriptor));
        io_.write_data(static_cast<std::uint8_t>(type));
    }

    /// DS2 1.10. Ask the *device* to take a new address.
    ///
    /// Not the same as set_usb_address, which tells the controller where the
    /// device now is. Both are needed, in that order; doing only one leaves a
    /// device that has moved and a host still calling where it used to be.
    void set_address(std::uint8_t address) {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::SetAddress));
        io_.write_data(address);
    }

    /// DS2 1.12. Choose a configuration, which is what makes the endpoints
    /// work. An addressed but unconfigured device answers nothing.
    void set_configuration(std::uint8_t value) {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::SetConfiguration));
        io_.write_data(value);
    }

    /// DS2 1.13. Ask the chip to configure the attached device by itself.
    ///
    /// It answers with an interrupt, so there is nothing to read here.
    void auto_setup() {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::AutoSetup));
    }

    /// Read and discard a status the chip is already holding.
    ///
    /// Left standing it would be delivered as the answer to the next question
    /// asked, which is how a device that has already gone gets configured.
    void drain_pending_status() {
        if (!io_.int_asserted()) {
            return;
        }
        InterruptStatus discarded = InterruptStatus::Success;
        (void)get_status(discarded);
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
