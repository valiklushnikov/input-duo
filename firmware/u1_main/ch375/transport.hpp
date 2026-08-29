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

    /// Change only the rate this side listens at, leaving transmission alone.
    ///
    /// Asking the same known question at the documented rate and sampling the
    /// answer at a different one separates a chip that is silent from a
    /// receiver that is looking in the wrong place - two faults that are
    /// identical from outside and want completely different repairs.
    virtual bool set_rx_baud(unsigned) { return false; }
};

/// How long to wait for a byte the chip owes us.
///
/// The chip's own processing is documented in microseconds, but the byte still
/// has to cross the wire: eleven bits at 9600 bps is 1.15 ms, so a timeout of
/// one millisecond gives up before a perfectly good answer has finished
/// arriving - and then reads the tail of it as the start of the next one.
///
/// That happened. Twenty milliseconds is far past any real answer.
///
/// It is not, however, "far below anything a person would notice", which this
/// note used to claim: that measures human perception, and the cost that
/// matters is that Core 1 ticks both channels in sequence and the other one's
/// interrupt endpoint wants polling every 8 ms. Twenty milliseconds is two and
/// a half of its poll windows. So this figure is right for a chip that is
/// going to answer and ruinous for one that is not.
///
/// It is used two ways. As a deadline on a question that was asked and left -
/// the presence probe, the interrupt status, the connect poll, the search
/// across rates - it costs nothing but the delay before the caller gives up,
/// which is what ReplyProgress below is for. As the bound on read_reply it is
/// a spin, and the whole of it is taken out of the other channel.
///
/// Read as a spin by, at the time of writing: check_exist; set_baud_rate's
/// acknowledgement and both port_answers probes inside try_speed; the
/// check_exist in the mode-refused branch; and read_block with the drain_port
/// behind it. A successful try_speed therefore contains three such waits plus
/// the physical port's rate-change sleeps: about 67 ms measured in one device
/// tick, not less than the other channel's 8 ms poll period. These are bounded
/// recovery/attach costs, not latency-safe steady-state operations.
inline constexpr std::uint32_t kDefaultReplyTimeoutUs = 20000;

/// What has become of a reply that is being waited for across ticks.
///
/// The blocking wait below is right for a chip that answers and ruinous for
/// one that does not: 20 ms is two and a half times the 8 ms an interrupt
/// endpoint on the *other* channel wants, and Core 1 ticks the two in
/// sequence. Measured on hardware, 747 of one channel's 751 reply timeouts
/// were a single command in the recovery path, and one tick reached 50 686 us.
/// A caller that can come back later asks and leaves instead.
enum class ReplyProgress : std::uint8_t {
    /// Nothing has arrived and the deadline has not passed.
    Waiting,
    /// A byte came back. Whether it is the right byte is a separate question.
    Answered,
    /// The deadline passed with nothing on the wire.
    TimedOut,
};

/// Which question is on the wire waiting for its one-byte answer.
///
/// One slot, not one per caller, and that is the point. The chip answers
/// commands in the order they are written, so two questions outstanding at
/// once means each poller can take the other's byte - which is not
/// hypothetical: a CHECK_EXIST answer sitting in the receive FIFO was read as
/// an interrupt status, and the Connect it displaced was then read as the
/// probe's answer, so a healthy chip whose device had just been plugged in was
/// declared lost and set up again from scratch. With one slot the question is
/// "whose byte is this" and it has exactly one answer.
enum class PendingReply : std::uint8_t {
    /// Nothing is outstanding; the port is free for whoever asks next.
    None,
    /// CHECK_EXIST - the presence probe.
    Presence,
    /// GET_STATUS - the interrupt status.
    Status,
    /// TEST_CONNECT - the idle channel's connect poll.
    Connect,
    /// A command that answers 51H or 5FH - SET_USB_MODE and its kind.
    Command,
    /// GET_DEVICE_RATE - how fast the attached device is.
    DeviceRate,
};

/// How far a search for a chip that has stopped answering has got.
enum class SearchProgress : std::uint8_t {
    /// Still walking the rates. Call again next tick.
    Waiting,
    /// It answered somewhere, and the port is back at the home rate.
    Found,
    /// It answered at no rate at all, and the port is back at the home rate.
    NotFound,
};

/// The eight bytes of a USB setup packet, before they are laid out.
///
/// USB 2.0 section 9.3. The controller has commands of its own for three
/// requests - SET_ADDRESS, SET_CONFIGURATION and GET_DESCRIPTOR - and nothing
/// at all for the rest, so anything else is assembled here and issued as a
/// transfer by hand.
struct ControlRequest {
    std::uint8_t request_type = 0;
    std::uint8_t request = 0;
    std::uint16_t value = 0;
    std::uint16_t index = 0;
    std::uint16_t length = 0;
};

class Ch375Transport {
public:
    explicit Ch375Transport(ICh375Transport& io) : io_(io) {}

    /// Start CHECK_EXIST without supplying its data byte.
    ///
    /// During bring-up this separates a real controller response from the
    /// transmit frame coupling into the receive path. Until the data byte
    /// arrives, the command is incomplete and the chip has no valid reply.
    void start_check_exist() {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::CheckExist));
    }

    /// Ask CHECK_EXIST and leave, collecting the answer on a later tick.
    ///
    /// The blocking form spins for up to reply_timeout_us_, and the recovery
    /// path asks it of a chip that by definition is not answering. Measured on
    /// hardware: 747 of one channel's 751 reply timeouts were this command, at
    /// 20 ms each, on a core that owes the other channel a poll every 8 ms.
    /// Asking costs two frames into the port's queue and nothing else.
    ///
    /// Anything already waiting is thrown away first, so a byte left over from
    /// an abandoned exchange is very unlikely to be read as this one's answer.
    /// Not "cannot": drain_arrived stops at the first read that finds nothing,
    /// so a byte still crossing the wire at that instant survives it. What
    /// makes it unlikely is the deadline - a question is only abandoned after
    /// 20 ms of silence, and no answer this firmware waits for takes anywhere
    /// near that long to arrive.
    void begin_presence_probe(std::uint8_t probe) {
        presence_expected_ = static_cast<std::uint8_t>(~probe);
        presence_matched_ = false;
        begin_reply(PendingReply::Presence);
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::CheckExist));
        io_.write_data(probe);
    }

    /// Look once for that answer. Never waits.
    ///
    /// TimedOut without a probe outstanding, because a caller that asks about
    /// a question it never put has not been kept waiting by anything - and
    /// that includes one some later command abandoned on its behalf.
    ReplyProgress poll_presence_probe() {
        std::uint8_t answer = 0;
        const ReplyProgress progress = poll_reply(PendingReply::Presence, answer);
        if (progress == ReplyProgress::Answered) {
            presence_matched_ = answer == presence_expected_;
        }
        return progress;
    }

    /// Was the byte that came back the exact inverse it should have been?
    ///
    /// A chip that answers wrongly is worse than one that says nothing - it
    /// looks alive - so only the exact inverse counts (DS1 5.5).
    bool presence_probe_matched() const { return presence_matched_; }

    /// DS1 5.12. Ask for the interrupt status and leave.
    ///
    /// The one call the first pass left blocking, and the one that mattered
    /// most. Measured on hardware at 2026-08-29 11:31: a channel whose device
    /// was attached and stuck before Ready held INT asserted and answered no
    /// status, so every tick spent one whole reply timeout inside get_status -
    /// 20 168 us of worst pass against 7296 us on an idle pair - and Core 1
    /// charged it to the other channel as two and a half missed 8 ms poll
    /// windows. Reading the status is also what clears the chip's request, so
    /// a chip that will not answer holds the line asserted and the wait is
    /// paid again on the very next tick.
    ///
    /// Only the caller who asked may collect it: this is the byte that says a
    /// device arrived, a transaction finished, or a cable was pulled.
    void begin_status_read() {
        begin_reply(PendingReply::Status);
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::GetStatus));
    }

    /// Look once for that status. Never waits.
    ///
    /// Any byte is carried through, including ones this code does not
    /// recognise: the failure statuses are a 32-value range that encodes which
    /// PID the device answered with, and that byte is the only evidence of why
    /// a transaction failed.
    ReplyProgress poll_status_read(InterruptStatus& status) {
        std::uint8_t answer = 0;
        const ReplyProgress progress = poll_reply(PendingReply::Status, answer);
        if (progress == ReplyProgress::Answered) {
            status = static_cast<InterruptStatus>(answer);
        }
        return progress;
    }

    /// DS1 5.10. Ask whether a device is attached, and leave.
    ///
    /// The idle channel's backstop poll, which runs every 100 ms against a
    /// chip that may well be deaf - one whose 5 V was cycled under a running
    /// U1 hears nothing at the rate this side raised it to. Blocking, that was
    /// 20 ms out of every 100 on a dead socket, all of it taken out of the
    /// other channel's poll windows.
    void begin_connect_probe() {
        begin_reply(PendingReply::Connect);
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::TestConnect));
    }

    /// Look once for that answer. Never waits.
    ReplyProgress poll_connect_probe(InterruptStatus& status) {
        std::uint8_t answer = 0;
        const ReplyProgress progress = poll_reply(PendingReply::Connect, answer);
        if (progress == ReplyProgress::Answered) {
            status = static_cast<InterruptStatus>(answer);
        }
        return progress;
    }

    /// Which question is on the wire, if any.
    ///
    /// A caller about to ask a different one has to know: two outstanding at
    /// once is how one poller comes to take the other's byte.
    PendingReply pending_reply() const { return pending_; }
    bool reply_outstanding() const { return pending_ != PendingReply::None; }

    /// Give up on the question that is on the wire.
    ///
    /// For a caller about to do something a pending answer cannot survive -
    /// tearing a device down, going back through chip setup. What has already
    /// arrived is thrown away rather than left to be read as the answer to
    /// whatever is asked next, and the poller that asked is told TimedOut
    /// rather than left waiting for a byte nobody will deliver.
    void abandon_reply() {
        if (pending_ == PendingReply::None) {
            return;
        }
        pending_ = PendingReply::None;
        drain_arrived();
    }

    /// Take whatever has already arrived and throw it away, without waiting.
    ///
    /// Not drain_port: that one waits a full reply timeout to discover an
    /// empty port, which is the cost this whole mechanism exists to avoid.
    /// This asks only for bytes that are there now, and stops at the first
    /// read that finds nothing - which is what the end of the traffic looks
    /// like.
    void drain_arrived() {
        for (std::size_t index = 0; index < kMaxBlockSize + 2; ++index) {
            std::uint8_t discarded = 0;
            if (!io_.read_data(discarded)) {
                return;
            }
        }
    }

    /// DS1 5.5. Send a byte, expect its bitwise inverse.
    ///
    /// A chip that answers wrongly is worse than one that says nothing - it
    /// looks alive - so only the exact inverse counts.
    bool check_exist(std::uint8_t probe) {
        abandon_reply();
        start_check_exist();
        io_.write_data(probe);

        std::uint8_t answer = 0;
        if (!read_reply(answer)) {
            return false;
        }
        return answer == static_cast<std::uint8_t>(~probe);
    }

    /// Ask a command that answers a status byte, and leave.
    ///
    /// The blocking form spun for a whole reply timeout, and its biggest
    /// caller is SET_USB_MODE, which runs at five points of a device's life -
    /// the attach, both halves of the bus reset, the recovery cycle and the
    /// detach. Measured on hardware at 2026-08-29 12:06: a channel sitting at
    /// attached=1, ready=0 - between the attach and Ready, which is precisely
    /// the stretch these run in - took 20 170 us round Core 1's loop, and the
    /// other channel lost two and a half of its 8 ms poll windows to it.
    ///
    /// The answer is collected by poll_command_status on a later tick. Until
    /// it is, nothing else may be written: the chip answers in the order it
    /// was asked, so a second question would be answered after this byte and
    /// each reader would take the other's.
    void begin_command_status(Ch375Command command, std::uint8_t argument) {
        last_status_reply_ = 0;
        last_status_answered_ = false;
        command_status_ok_ = false;
        begin_reply(PendingReply::Command);
        io_.write_command(static_cast<std::uint8_t>(command));
        io_.write_data(argument);
    }

    /// Look once for that status byte. Never waits.
    ///
    /// The byte is kept as well as judged, because "it refused" and "it said
    /// 5FH" are different facts and only one of them can be acted on: a chip
    /// that says 5FH is refusing, one that says nothing is not listening, and
    /// one that says some third byte has a port out of step.
    ReplyProgress poll_command_status() {
        std::uint8_t answer = 0;
        const ReplyProgress progress = poll_reply(PendingReply::Command, answer);
        if (progress == ReplyProgress::Answered) {
            last_status_reply_ = answer;
            last_status_answered_ = true;
            // 51H and 5FH are the whole documented set. Anything else means
            // the port has lost step, and calling that success would let a
            // desynchronised chip pass for a working one.
            command_status_ok_ = answer == static_cast<std::uint8_t>(CommandStatus::Success);
        }
        return progress;
    }

    /// Did the last collected status say the command was taken?
    ///
    /// Only meaningful once poll_command_status has answered; a question that
    /// timed out leaves this false, which is the safe direction.
    bool command_status_succeeded() const { return command_status_ok_; }

    /// DS1 5.9. Set the USB working mode, and leave.
    void begin_set_usb_mode(UsbMode mode) {
        begin_command_status(Ch375Command::SetUsbMode, static_cast<std::uint8_t>(mode));
    }

    /// What the chip actually answered the last status-bearing command with,
    /// and whether it answered at all.
    ///
    /// A chip that says 5FH is refusing; one that says nothing is not
    /// listening; one that says some third byte has a port out of step. All
    /// three arrive as false, and they want three different repairs.
    std::uint8_t last_status_reply() const { return last_status_reply_; }
    bool last_status_answered() const { return last_status_answered_; }

    /// DS2 1.3. Decide what happens when a device answers NAK.
    bool set_retry(std::uint8_t policy) {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::SetRetry));
        io_.write_data(kSetRetryPrefix);
        io_.write_data(policy);
        return true;  // DS2 1.3 documents no reply.
    }

    /// DS1 5.11. Stop a USB transaction the chip is still retrying.
    ///
    /// The bounded NAK policy can outlive the firmware's setup deadline. A
    /// recovery command sent into that interval is not a replacement for the
    /// token still on the bus; ABORT_NAK is the command that makes it one.
    void abort_nak() {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::AbortNak));
    }

    /// DS2 1.5. Tell the chip which device address it is talking to.
    bool set_usb_address(std::uint8_t address) {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::SetUsbAddress));
        io_.write_data(address);
        return true;  // DS2 1.5 documents no reply.
    }

    /// How many length-prefixed block reads have completed, and how many have
    /// failed.
    ///
    /// The ladder is driven by these, because two CHECK_EXIST replies prove a
    /// divider and not a link. Measured by the port's real rate:
    ///
    ///     byRate(9600/37500/62500/115200):  ok=0/1/0/0   fail=0/0/24/24
    ///
    /// CHECK_EXIST answered at every one of those rates. A rung is only good
    /// once a block read has finished on it.
    std::uint32_t block_reads_ok() const { return block_reads_ok_; }
    std::uint32_t block_reads_failed() const { return block_reads_failed_; }

    /// DS2 1.8. Read a length-prefixed block from the endpoint buffer.
    ///
    /// A length of zero is a success: a polled endpoint with nothing to say
    /// answers that constantly, and treating it as an error would make an idle
    /// keyboard look like a broken one.
    bool read_block(std::uint8_t* out, std::size_t capacity, std::size_t& size) {
        size = 0;
        abandon_reply();
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::ReadUsbData0));

        std::uint8_t length = 0;
        if (!read_reply(length)) {
            // Not even a length. Whatever the chip is doing, the next thing it
            // sends is not an answer to anything asked yet.
            ++block_reads_failed_;
            drain_port();
            return false;
        }
        if (length > kMaxBlockSize || length > capacity) {
            // A length past the chip's own maximum means the port is out of
            // step rather than that a longer packet arrived, and a length past
            // the caller's buffer is how a device on the far end of a wire
            // writes into this one. Either way the bytes are refused - but the
            // command has already gone, so the chip is going to send them
            // regardless of what is decided here. Walking away does not stop
            // them arriving; it makes them arrive later, as the answers to
            // whatever is asked next, for ever.
            ++block_reads_failed_;
            drain_port();
            return false;
        }

        for (std::size_t index = 0; index < length; ++index) {
            std::uint8_t value = 0;
            if (!read_reply(value)) {
                // The rest of the packet is still coming.
                size = 0;
                ++block_reads_failed_;
                drain_port();
                return false;
            }
            out[index] = value;
        }
        size = length;
        // A length of zero counts: the exchange completed, which is the thing
        // a broken rate cannot do. An idle endpoint answers that constantly,
        // and calling it unproven would make a working link look like a rate
        // that never carried anything.
        ++block_reads_ok_;
        return true;
    }

    /// Read and throw away whatever is still on its way.
    ///
    /// Called when a read is abandoned part way through. The chip has already
    /// been told to send something; that does not stop because this side
    /// changed its mind. Left alone, those bytes are read as the reply to the
    /// next command, and the one after that, and every one after that - a port
    /// permanently one answer behind, which from outside is a controller that
    /// has stopped making sense and only a power cycle appears to fix.
    ///
    /// Bounded twice over: by a byte count, and by the first read that finds
    /// nothing, which is what the end of the traffic looks like.
    void drain_port() {
        for (std::size_t index = 0; index < kMaxBlockSize + 2; ++index) {
            std::uint8_t discarded = 0;
            if (!read_reply(discarded)) {
                return;
            }
        }
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
        abandon_reply();
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::SetBaudRate));
        io_.write_data(coefficient);
        io_.write_data(constant);

        // The receiver moves now; the transmitter waits until the answer is in.
        //
        // They are separate state machines with separate dividers, and that
        // separation is the only thing that makes this command readable at
        // all. The chip answers 51H within microseconds of the last stop bit,
        // at the new rate. Moving both ends together means waiting for the
        // transmitter's tail before touching the divider - by which time the
        // answer has already come and gone against a receiver still set to the
        // old rate. Every rate change then reports failure on a chip that
        // moved, and the next command goes out at a rate it is no longer
        // listening at: bytes it half-hears are parsed as opcodes, and the
        // reachable ones include this very command and ENTER_SLEEP. That is
        // how asking for more speed produced a controller only a power cycle
        // could revive.
        //
        // The transmitter is still shifting its last frame at the old rate
        // while this happens, which is correct: that frame was sent at the old
        // rate and must finish at it.
        if (!io_.set_rx_baud(baud)) {
            return false;
        }

        std::uint8_t answer = 0;
        if (!read_reply(answer) || answer != static_cast<std::uint8_t>(CommandStatus::Success)) {
            // It did not take, or was not heard. Put the receiver back where
            // the transmitter still is, so both ends agree again.
            io_.set_rx_baud(kCh375DefaultBaud);
            return false;
        }

        // Confirmed. Now the transmitter follows.
        return set_port_baud(baud);
    }

    /// The rate this side is transmitting at.
    ///
    /// One place knows, and everything that changes the rate goes through the
    /// one setter below. A second copy kept elsewhere is exactly what drifted:
    /// the device believed it was talking at 37500 while reset_port_speed had
    /// put the port back to 9600, which is the state this file's own note
    /// warns about - a byte half-heard at the wrong rate is swallowed as some
    /// command's parameter, and every byte after it is out of step.
    unsigned port_baud() const { return port_baud_; }

    /// Put this side back to the rate a chip comes up at.
    ///
    /// RESET_ALL returns the chip to 9600 whatever it was doing before, so a
    /// port left at the raised rate is talking to something that is no longer
    /// listening at it.
    bool reset_port_speed(unsigned default_baud) { return set_port_baud(default_baud); }

    /// Does the port work well enough to be trusted at this rate?
    ///
    /// Asked more than once, with a different byte each time. A marginal rate
    /// answers sometimes, and accepting it on one byte is how a link that half
    /// works gets chosen over one that works.
    bool port_answers(int rounds) {
        std::uint8_t probe = kPortProbeByte;
        for (int round = 0; round < rounds; ++round) {
            if (!check_exist(probe)) {
                return false;
            }
            probe = static_cast<std::uint8_t>(~probe);
        }
        return true;
    }

    /// Try one rung of the speed ladder.
    ///
    /// One rung per call, not the whole ladder, because giving up on a rate
    /// can mean resetting the chip and a chip takes about 40 ms to come back
    /// (DS1 5.4). Waiting for that here would block the loop everything else
    /// on this board shares; the caller already has a reset cycle with the
    /// wait built into it.
    ///
    /// Nothing is ever written at a rate this has not just proved. The chip
    /// reads commands positionally - a command byte, then the data bytes that
    /// command takes - so a byte it half-hears at the wrong rate is swallowed
    /// as somebody's parameter and every byte after it is out of step. From
    /// outside that is a chip which has stopped answering, and the only cure
    /// is someone walking over to pull its power. Blindly resetting at a rate
    /// the chip might not be using does exactly that, a hundred times an hour.
    ///
    /// Returns the rate settled on, which is ``slow`` if this rung did not
    /// hold.
    unsigned try_speed(const BaudOption& option, unsigned slow) {
        if (set_baud_rate(option.coefficient, option.constant, option.baud) &&
            port_answers(kPortProofRounds)) {
            return option.baud;
        }

        // Nothing is written to find out what went wrong.
        //
        // The old version probed at two rates and could send RESET_ALL at one
        // of them, which meant writing at a rate the chip might not be using -
        // the single operation that turns a controller which was about to come
        // good into one that answers nothing at all. It also sent that reset
        // with no wait, and the caller then wrote thirty more frames into the
        // chip's forty-millisecond restart.
        //
        // Both ends go back to the rate they started at and this side stays
        // quiet. If the chip really did move and its answer was lost, the next
        // recovery cycle finds it silent and waits, which is recoverable. A
        // barrage of half-heard opcodes is not.
        set_port_baud(slow);
        return slow;
    }

    /// Look for a chip that has stopped answering, one step per tick.
    ///
    /// A chip left at a rate this side abandoned answers nothing where it is
    /// expected, and until this ran the only cure was somebody walking to the
    /// board and pulling its power - which is what this project did for weeks.
    /// It is reachable; nobody was asking in the right place. Wired in on the
    /// bench it recovered a stranded chip with no power cycle, first time all
    /// week.
    ///
    /// There is no way to reach a CH375 on this board except the serial port:
    /// docs/hardware/ch375-wiring.md gives RXD, TXD and INT per channel and no
    /// reset line. The manufacturer recommends the hardware this board does
    /// not have - CH375 datasheet, serial interface section, "mends
    /// communication baud-rate dynamically, one suggest is that controlling
    /// RSTI of CH375 through MCU I/O point in order to reset CH375 to default
    /// baud-rate" - so this is the software equivalent and the only lever.
    ///
    /// Only CHECK_EXIST is used to look, because it is one command and one
    /// data byte and it proves the port by construction (DS1 5.5). After a
    /// rate that did not answer, four filler bytes go out to satisfy whatever
    /// half-heard command the chip may be sitting on: it reads commands
    /// positionally, so a swallowed parameter puts every later byte out of
    /// step - which is how it got lost in the first place.
    ///
    /// Split across ticks, not run to completion. Done in one call it walks
    /// four rates with two probes each, all inside the blocking reply wait: on
    /// the bench that took the worst tick to 154 056 us, which Core 1 charges
    /// to the other channel as a lag in somebody's typing.
    void begin_chip_search(unsigned home) {
        search_home_ = home;
        search_index_ = 0;
        search_round_ = 0;
        search_found_at_ = 0;
        search_step_ = SearchStep::Rate;
    }

    /// Do one step of that search.
    ///
    /// Never waits for a reply: each step either moves the port to the next
    /// rate or takes one look for an answer already asked for. It is not free,
    /// though, and the note here used to say it was. A step that changes the
    /// rate calls set_port_baud, and on hardware PioCh375Transport::set_baud
    /// spends about 3.5 ms in two mandated sleeps - kFrameTailUs = 1500 us so
    /// the last frame leaves at the old rate, and kBaudChangeUs = 2000 us
    /// because DS1 5.2 says the chip answers at neither rate for about a
    /// millisecond after the change. At most one rate changes per step, so
    /// 3.5 ms is the worst a search step can cost, against 154 056 us for the
    /// same search done inside one tick.
    SearchProgress poll_chip_search() {
        switch (search_step_) {
            case SearchStep::Rate: {
                if (search_index_ >= 1 + kBaudLadderSize) {
                    set_port_baud(search_home_);
                    search_step_ = SearchStep::Done;
                    return SearchProgress::NotFound;
                }
                if (!set_port_baud(search_rate(search_index_))) {
                    // A port with a fixed rate cannot be asked at another one.
                    ++search_index_;
                    return SearchProgress::Waiting;
                }
                search_round_ = 0;
                search_probe_ = kPortProbeByte;
                begin_presence_probe(search_probe_);
                search_step_ = SearchStep::Probe;
                return SearchProgress::Waiting;
            }

            case SearchStep::Probe: {
                const ReplyProgress progress = poll_presence_probe();
                if (progress == ReplyProgress::Waiting) {
                    return SearchProgress::Waiting;
                }
                if (progress == ReplyProgress::Answered && presence_matched_) {
                    if (++search_round_ < kPortProofRounds) {
                        // A marginal rate answers sometimes, and accepting it
                        // on one byte is how a link that half works gets
                        // chosen over one that works.
                        search_probe_ = static_cast<std::uint8_t>(~search_probe_);
                        begin_presence_probe(search_probe_);
                        return SearchProgress::Waiting;
                    }
                    search_found_at_ = search_rate(search_index_);
                    if (search_found_at_ != search_home_) {
                        // Found somewhere it should not be. Tell it to reset
                        // while it can still hear, then come home with it.
                        reset_all();
                        set_port_baud(search_home_);
                    }
                    search_step_ = SearchStep::Done;
                    return SearchProgress::Found;
                }
                for (int filler = 0; filler < 4; ++filler) {
                    io_.write_data(0x00);
                }
                ++search_index_;
                search_step_ = SearchStep::Rate;
                return SearchProgress::Waiting;
            }

            case SearchStep::Done:
            default:
                return search_found_at_ != 0 ? SearchProgress::Found
                                             : SearchProgress::NotFound;
        }
    }

    /// The rate the chip answered at, or zero if it answered nowhere.
    ///
    /// Kept apart from "it was found" because the two say different things: a
    /// chip found at the home rate was merely slow to come back, and one found
    /// at a rung is one this code stranded there and can strand again.
    unsigned chip_search_found_at() const { return search_found_at_; }

    /// DS1 5.10. Ask whether a device is attached, rather than waiting to be
    /// told.
    ///
    /// The connect interrupt can be missed - it is cleared by reading it, and
    /// a chip that was answering nonsense at the time consumes it for nothing.
    /// It also never arrives for a device that was already plugged in when the
    /// power came on. Asking covers both.
    bool test_connect(InterruptStatus& status) {
        abandon_reply();
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

    /// DS2 1.2. Ask how fast the attached device is, and leave.
    ///
    /// Takes the byte 07H and answers a rate type; bit 4 set means 1.5 Mbps.
    /// Only valid in host mode 5, before frames are being generated.
    ///
    /// Asked once on every attach, which is the first thing that happens to a
    /// channel after a device arrives - and the channel measured on hardware
    /// at 2026-08-29 12:06 was stuck at attached=1, ready=0, so this was one
    /// of the two commands its 20 170 us pass was being spent inside.
    void begin_device_rate() {
        begin_reply(PendingReply::DeviceRate);
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::GetDeviceRate));
        io_.write_data(kGetDeviceRatePrefix);
    }

    /// Look once for that answer. Never waits.
    ///
    /// ``low_speed`` is written only when a byte actually came back. A caller
    /// that folded "no answer" into "not low speed" would address a low-speed
    /// mouse at eight times its rate, get nothing back, and report a device
    /// that is sitting right there as gone.
    ReplyProgress poll_device_rate(bool& low_speed) {
        std::uint8_t answer = 0;
        const ReplyProgress progress = poll_reply(PendingReply::DeviceRate, answer);
        if (progress == ReplyProgress::Answered) {
            low_speed = (answer & 0x10) != 0;
        }
        return progress;
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

    /// DS2 1.15. Begin a control transfer the chip has no command for.
    ///
    /// The setup packet goes into the endpoint buffer and then a SETUP token
    /// carries it to endpoint zero. That order is the datasheet's own (DS2
    /// 1.15: write the payload first, then issue the token); reversed, the
    /// token goes out over whatever the buffer happened to hold last, which
    /// on a device is a request nobody made.
    ///
    /// Nothing is read here. The chip answers with an interrupt, which the
    /// caller is already reading once per pass, so this costs the shared loop
    /// ten data bytes and no waiting at all.
    ///
    /// False means the packet was refused before the command byte went out,
    /// so the chip is still in step and no token was issued over a stale
    /// buffer.
    bool begin_control_request(const ControlRequest& request) {
        const std::uint8_t setup[] = {
            request.request_type,
            request.request,
            static_cast<std::uint8_t>(request.value & 0xFF),
            static_cast<std::uint8_t>(request.value >> 8),
            static_cast<std::uint8_t>(request.index & 0xFF),
            static_cast<std::uint8_t>(request.index >> 8),
            static_cast<std::uint8_t>(request.length & 0xFF),
            static_cast<std::uint8_t>(request.length >> 8),
        };
        if (!write_block(setup, sizeof(setup))) {
            return false;
        }
        return issue_token(kControlEndpoint, TokenPid::Setup);
    }

    /// The status stage of a control transfer that carries no data.
    ///
    /// A request with no data stage still has one: the host asks for a packet
    /// and the device answers an empty DATA1 (USB 2.0 8.5.3). It is not a
    /// formality - a device applies the request when the transfer completes,
    /// so a transfer left after its setup packet changes nothing and leaves
    /// the device waiting to be finished.
    ///
    /// The toggle is always DATA1 for a status stage, and the chip does not
    /// track it (DS2 1.6). Whatever the interrupt endpoint is expecting is
    /// set again from scratch when the device is declared ready, so borrowing
    /// the receiver here costs it nothing.
    ///
    /// Nothing is read: the answer is empty by definition, and the interrupt
    /// that says it arrived is read by the caller.
    void finish_control_request() {
        set_receive_toggle(kToggleData1);
        (void)issue_token(kControlEndpoint, TokenPid::In);
    }

    /// DS2 1.13. Ask the chip to configure the attached device by itself.
    ///
    /// It answers with an interrupt, so there is nothing to read here.
    void auto_setup() {
        io_.write_command(static_cast<std::uint8_t>(Ch375Command::AutoSetup));
    }

    /// Ask for a status the chip is already holding, so it can be thrown away.
    ///
    /// Left standing it would be delivered as the answer to the next question
    /// asked, which is how a device that has already gone gets configured.
    ///
    /// Asked and left, like the rest. The blocking form ran once per bring-up
    /// and spent a whole reply timeout whenever the chip was holding its
    /// interrupt line down and not answering - which is the state a chip is in
    /// when a bring-up is what the channel is doing, and a sick channel brings
    /// up once a second.
    ///
    /// False when the line is not asserted and nothing was asked: reading a
    /// status the chip is not offering consumes one that belongs to nothing.
    /// True means a question is on the wire and its answer - which the caller
    /// throws away - has to be collected with poll_status_read.
    bool begin_status_drain() {
        if (!io_.int_asserted()) {
            return false;
        }
        begin_status_read();
        return true;
    }

    /// Is the chip asking for attention right now?
    ///
    /// Reading the status is what clears the request, so a caller should ask
    /// this first rather than reading a status that belongs to nothing.
    bool interrupt_pending() const { return io_.int_asserted(); }

    std::uint32_t now_us() const { return io_.now_us(); }

private:
    /// Has ``deadline_us`` passed?
    ///
    /// Subtraction, not comparison. The clock wraps every 71 minutes, and a
    /// deadline compared directly would look like an enormous wait for the
    /// hour on one side of the wrap and no wait at all on the other.
    bool expired(std::uint32_t deadline_us) const {
        return static_cast<std::int32_t>(io_.now_us() - deadline_us) >= 0;
    }

    /// Put one question on the wire and start its deadline.
    ///
    /// Anything already waiting goes first: it belongs to a question nobody is
    /// collecting any more, and the chip answers in order, so leaving it there
    /// would hand it to this caller.
    void begin_reply(PendingReply who) {
        drain_arrived();
        pending_ = who;
        pending_deadline_us_ = io_.now_us() + reply_timeout_us_;
    }

    /// One look for the answer to the question ``who`` asked.
    ///
    /// TimedOut when some other question is on the wire, or none at all: a
    /// caller whose question was abandoned has not been kept waiting by
    /// anything, and must not be left polling for a byte that is not coming.
    ReplyProgress poll_reply(PendingReply who, std::uint8_t& answer) {
        if (pending_ != who) {
            return ReplyProgress::TimedOut;
        }
        if (io_.read_data(answer)) {
            pending_ = PendingReply::None;
            return ReplyProgress::Answered;
        }
        if (expired(pending_deadline_us_)) {
            pending_ = PendingReply::None;
            return ReplyProgress::TimedOut;
        }
        return ReplyProgress::Waiting;
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

    std::uint8_t last_status_reply_ = 0;
    bool last_status_answered_ = false;
    bool command_status_ok_ = false;

    /// Move this side's transmit rate, and remember where it went.
    ///
    /// The only route to io_.set_baud. A rate change that skipped this would
    /// leave port_baud() describing a port that has moved on without it.
    bool set_port_baud(unsigned baud) {
        if (!io_.set_baud(baud)) {
            return false;
        }
        port_baud_ = baud;
        return true;
    }

    /// Where a search across the rates has got to.
    enum class SearchStep : std::uint8_t { Rate, Probe, Done };

    /// The rates a lost chip can be at: the one it comes up at, and every one
    /// this code is capable of having moved it to.
    unsigned search_rate(std::size_t index) const {
        return index == 0 ? search_home_ : kBaudLadder[index - 1].baud;
    }

    SearchStep search_step_ = SearchStep::Done;
    unsigned search_home_ = kCh375DefaultBaud;
    std::size_t search_index_ = 0;
    int search_round_ = 0;
    std::uint8_t search_probe_ = kPortProbeByte;
    unsigned search_found_at_ = 0;

    std::uint32_t block_reads_ok_ = 0;
    std::uint32_t block_reads_failed_ = 0;
    unsigned port_baud_ = kCh375DefaultBaud;

    /// The one question that may be on the wire, and when it gives up.
    PendingReply pending_ = PendingReply::None;
    std::uint32_t pending_deadline_us_ = 0;
    std::uint8_t presence_expected_ = 0;
    bool presence_matched_ = false;

    ICh375Transport& io_;
    std::uint32_t reply_timeout_us_ = kDefaultReplyTimeoutUs;
};

}  // namespace duo_input::u1::ch375
