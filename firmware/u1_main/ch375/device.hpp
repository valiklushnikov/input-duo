#pragma once

// One CH375 and whatever is plugged into it.
//
// The chip is asynchronous and this firmware is not allowed to wait for it.
// Every step therefore happens across ticks: one call does a bounded piece of
// work and returns, so a keyboard whose controller has stopped answering costs
// that one device its input and nothing else. U1 still has to service USB, the
// link to U2 and the watchdog on the same loop, and a device that could hold
// the loop would take all three down with it.
//
// Two of these run side by side, one per CH375. They share no state at all,
// which is the point: the mouse dying must be invisible to the keyboard.
//
// Failure is expected rather than exceptional. Controllers answer nonsense,
// stop answering, and come back; devices are unplugged mid-transaction. All of
// that lands in RecoverWait, which counts down and tries again, and none of it
// is allowed to end with keys held down on the far side.

#include <cstddef>
#include <cstdint>

#include "ch375/commands.hpp"
#include "ch375/transport.hpp"

namespace duo_input::u1::ch375 {

enum class Ch375State : std::uint8_t {
    /// Nothing is plugged in, and the chip is watching for something to be.
    Absent,
    /// The USB bus is being held in reset.
    Resetting,
    /// The chip is in host mode and generating frames.
    HostMode,
    /// The device is being configured.
    Enumerating,
    /// Configured; its reports are being read.
    Ready,
    /// Something failed. Counting down to another attempt.
    RecoverWait,
    /// Retrying has stopped being worth it.
    Fault,
};

/// Where bringing the chip itself up has got to.
///
/// A sub-state machine rather than a straight line, because the one question
/// it asks - is the chip answering? - is asked of a chip that very often is
/// not, and waiting for that answer inside a single tick spends the other
/// channel's poll window. Each step here does a bounded piece and returns.
enum class ChipBringUp : std::uint8_t {
    /// Nothing started. The next tick sends RESET_ALL.
    Idle,
    /// RESET_ALL has gone, and a status the chip was holding has been asked
    /// for so that it can be thrown away rather than answer a later question.
    Draining,
    /// RESET_ALL has gone; the chip needs about 40 ms before it hears anything.
    Resetting,
    /// CHECK_EXIST has been asked at the home rate and not yet answered.
    Probing,
    /// Every rate is being walked looking for a chip that has moved.
    Searching,
    /// SET_USB_MODE has gone and its status byte has not come back.
    Moding,
};

/// A command whose one-byte status is on the wire, and who is waiting for it.
///
/// SET_USB_MODE is asked at five points of a device's life and its answer used
/// to be spun for. Asked and left, the question outlives the tick that put it
/// there, so the tick that collects the byte has to know what the machine
/// meant to do with it. One value per point, because they do different things
/// with the same byte.
enum class PendingCommand : std::uint8_t {
    /// Nothing is outstanding.
    None,
    /// GET_DEVICE_RATE, asked the moment a device is found attached.
    DeviceRate,
    /// Mode 7 or mode 6, sent when a device was found attached.
    AttachMode,
    /// Mode 6, ending the bus reset.
    ResetDone,
    /// Mode 7, starting the recovery cycle over.
    RecoverMode,
    /// Mode 5, on the way back to Absent after a device went away.
    ///
    /// Nothing depends on the answer. It is collected all the same, because a
    /// byte left in the receive FIFO is read as the answer to whatever is
    /// asked next, and then to the one after that, for ever.
    DetachMode,
};

/// How many probes at the home rate go unanswered before the chip is looked
/// for at the rates this code could have moved it to.
///
/// Not the first thing tried: the search writes to rates the chip may not be
/// using, and a chip that half-hears a byte swallows the next command as its
/// parameter. A channel that has been silent for this many recovery cycles is
/// already unreachable, and looking cannot make it more so.
inline constexpr std::uint16_t kProbesBeforeChipSearch = 3;

enum class Ch375EventKind : std::uint8_t {
    None,
    /// A device was plugged in. It is not usable yet.
    Attached,
    /// A device went away. Whatever it was holding must be released.
    Detached,
    /// A device is configured and its reports are coming.
    Ready,
    /// This controller has given up.
    Fault,
    /// A report arrived from the device.
    Report,
};

struct Ch375Event {
    Ch375EventKind kind = Ch375EventKind::None;
    std::uint8_t report[kMaxBlockSize] = {};
    std::size_t report_size = 0;
};

enum class SetupProgress : std::uint8_t {
    /// Still working. Call again next tick.
    Busy,
    /// The device is configured.
    Done,
    /// It cannot be configured. Recover and try again.
    Failed,
};

/// Configuring a freshly attached device.
///
/// Separated from the lifecycle because the two change for different reasons:
/// the lifecycle is about a controller and a cable, and this is about USB
/// descriptors. Task 3 of the plan implements the real one.
class IDeviceSetup {
public:
    virtual ~IDeviceSetup() = default;

    /// Start over on a device that has just been reset.
    virtual void begin(std::uint32_t now_us) = 0;

    /// Do a bounded piece of the work.
    ///
    /// The status is handed down rather than read here. Reading it is what
    /// clears the chip's request, so two readers means each takes the answer
    /// the other was waiting for - and the one that waits reports a timeout
    /// against a chip that answered immediately. There is one reader, above.
    virtual SetupProgress poll(std::uint32_t now_us, bool interrupted,
                               InterruptStatus status) = 0;

    /// Which endpoint the device's reports arrive on. Valid after Done.
    virtual std::uint8_t interrupt_endpoint() const = 0;

    /// How big this device's reports are, once its descriptors have said.
    ///
    /// Zero until then, and the port rate has to be decided while they are
    /// still being fetched - so a caller falls back to the largest boot report
    /// this firmware routes rather than treating zero as a small packet.
    virtual std::uint16_t max_packet() const { return 0; }
};

/// How long the USB bus is held in reset before the device is configured.
///
/// DS1 section 5.9 says to enter mode 7 after a device is plugged in and then
/// switch to mode 6. USB requires the reset to last at least 10 ms; this is
/// comfortably past that and still imperceptible.
inline constexpr std::uint32_t kBusResetHoldUs = 20000;

/// How long the chip needs after being told to reset itself.
///
/// Four times the datasheet's figure, because the datasheet's figure was not
/// enough for the module on this bench. The chip worked until the first
/// RESET_ALL and answered nothing ever after - not refusing, silent - which
/// is what talking to a controller still coming back looks like: it reads the
/// command as part of its own restart and every byte after that is out of
/// step. Waiting costs a quarter second on a path that already waits a full
/// one; getting it wrong costs a walk to the board.
///
/// DS1 5.4 gives about 40 ms, during which it answers nothing at all. Asking
/// it anything sooner reads as a chip that is not there.
inline constexpr std::uint32_t kChipResetUs = 60000;

/// How long a device is left alone after the bus reset before it is addressed.
///
/// USB allows a device up to 10 ms to recover from a reset before it has to
/// answer anything. Asking sooner is putting a question to something that is
/// still coming round, and the answer is silence - which the controller
/// reports as the device having gone, so the whole sequence begins again. That
/// is a loop the bench spent an evening in.
///
/// Half again over the specified maximum, and still imperceptible.
inline constexpr std::uint32_t kBusSettleUs = 15000;

/// How long to let one step of bringing a device up run before giving up.
///
/// A control transfer at full speed takes single-digit milliseconds. Long
/// enough not to abandon a slow device, short enough that a port with nothing
/// on it costs a fraction of a second rather than a pause somebody notices.
///
/// It lives here rather than beside either enumerator, because both of them
/// wait on the same chip for the same reason.
inline constexpr std::uint32_t kSetupTimeoutUs = 200000;

/// How long to wait after a failure before trying the whole sequence again.
///
/// Retrying flat out would hammer a controller that is already unhappy and
/// fill the loop with work that cannot succeed, while a longer wait would
/// leave someone staring at a keyboard that does nothing.
inline constexpr std::uint32_t kRecoverDelayUs = 1000000;

/// How often a configured device is asked whether it has anything to say.
///
/// A USB interrupt endpoint on a keyboard is polled about every 8 ms by a real
/// host, and faster gains nothing a person can feel.
inline constexpr std::uint32_t kReportPollUs = 8000;

/// A CH375 serial frame is eleven bits.
///
/// Eight data bits, a start bit, a stop bit, and the ninth data bit that marks
/// a command from a data byte (DS1 6.2.2). That ninth bit is why this port is
/// built out of PIO and not the RP2040's UART, and it is why a byte costs
/// eleven bit times rather than ten.
inline constexpr unsigned kSerialFrameBits = 11;

/// Frames spent collecting one report, besides the report itself.
///
/// GET_STATUS and its answer, RD_USB_DATA0 and its length byte, SET_ENDPOINT6
/// and its argument, ISSUE_TOKEN and its argument. Eight - which with a
/// seven-byte mouse report makes the fifteen bytes the transport's own note
/// names, and 17.2 ms of them at 9600.
inline constexpr std::size_t kReportOverheadFrames = 8;

/// The largest boot report this firmware routes: a keyboard's eight bytes.
///
/// Stood in for a device that has not said yet, because the port rate is
/// decided while the descriptors are still being fetched.
inline constexpr std::size_t kAssumedPacketBytes = 8;

/// The slowest port rate that can carry one report per poll interval.
///
/// Derived rather than chosen. At 9600 a seven-byte mouse report costs fifteen
/// frames of eleven bits - 165 bits, 17.2 ms - against the 8 ms a moving hand
/// produces one in, so the deficit never closes while the hand keeps moving
/// and the peripheral is torn down for a silence this side is causing. That
/// was measured, and so was the other end of it: block reads complete at 37500
/// and nowhere above.
constexpr unsigned report_rate_floor(std::size_t packet_bytes, std::uint32_t poll_us) {
    const std::uint64_t bits =
        static_cast<std::uint64_t>(packet_bytes + kReportOverheadFrames) * kSerialFrameBits;
    return static_cast<unsigned>(bits * 1000000u / poll_us);
}

/// How many block reads may fail in a row on one rate before the ladder steps
/// down.
///
/// A rate that cannot fetch a descriptor cannot run a device, and the ordinary
/// collapse path never sees it: that one only fires for a device that reached
/// Ready, which a rate this broken never does. Twelve is what the diagnostic
/// build on the bench used to walk 115200 and 62500 and settle on 37500.
inline constexpr std::uint16_t kBlockFailsBeforeStepDown = 12;

/// How long to wait for the answer to a token before assuming it was lost.
///
/// A USB interrupt transaction and the controller's work around it are well
/// under a millisecond; this is generous by two orders of magnitude, because
/// its only job is to stop a dropped interrupt from silencing the device for
/// good. It has to stay well below kDeviceLostUs, or polling would resume
/// only after the device had already been given up on.
inline constexpr std::uint32_t kTokenAnswerUs = 100000;

/// How many times a quiet endpoint is re-armed before the device is given up
/// on.
///
/// Each attempt costs a second of silence, so this is also how long a device
/// that really has gone stays held: three seconds. Longer strands a
/// peripheral's keys for longer; shorter re-enumerates a working mouse for a
/// hiccup, which is what the operator sees.
inline constexpr std::uint8_t kQuietRetriesBeforeTeardown = 3;

/// How many refused mode commands before asking whether this side is the one
/// at fault. High enough that a channel merely settling is not swept.
inline constexpr std::uint16_t kRxSweepAfterFailures = 6;

/// How long a working device may go without its controller answering.
///
/// A controller that stops answering while a device is up is the dangerous
/// case: nothing can be read from the device any more, and something on the
/// far side may be holding a key down with no way to learn otherwise. After
/// this long the device is declared gone and released, which is the same rule
/// U2 keeps about the link to U1 and for the same reason.
///
/// Long enough not to trip on jitter, short enough that nobody finishes a word
/// in it.
/// One second. A polled endpoint with nothing to say should still answer -
/// the chip is told to report a NAK rather than retry it forever - so silence
/// this long means the controller, not the device, has stopped talking. The
/// first value tried here was 250 ms, and it declared a working mouse gone a
/// quarter of a second after it finally came up.
inline constexpr std::uint32_t kDeviceLostUs = 1000000;

/// How often an idle channel asks its chip to prove it is still a chip.
///
/// Absent is not a resting place for the chip, only for the socket: a CH375
/// whose 5 V was cycled under a running U1 comes back at 9600 with no working
/// mode, and answers nothing at the rate this side raised it to. Asking about
/// a device instead of about the chip reads that as "no device", which is the
/// phantom Absent this project cured by reflashing all week.
///
/// One CHECK_EXIST a second: one command and one data byte, cheap enough that
/// an idle channel costs nothing and often enough that nobody watches a dead
/// socket for long.
inline constexpr std::uint32_t kPresenceRecheckUs = 1000000;

/// How many of those go unanswered in a row before the chip is declared lost.
///
/// One is a byte, not a chip. Declaring a loss re-runs the whole of chip
/// setup - RESET_ALL, sixty milliseconds of waiting, a probe, a mode command
/// and the climb back up the baud ladder - which is a second of a working
/// channel doing nothing, and the search for a lost chip is guarded by three
/// failures (kProbesBeforeChipSearch) for exactly that reason while this far
/// more expensive thing was guarded by none.
///
/// Two, not three: the retry is immediate rather than a second later, so the
/// cost of the second opinion is one reply timeout, and a chip that really has
/// stopped answering is still noticed inside about 20 ms of the first miss.
inline constexpr std::uint16_t kPresenceProbesBeforeLost = 2;

/// How often to ask whether something has been plugged in.
///
/// The chip announces arrivals by itself, so this is a backstop rather than
/// the main route: it catches a device that was already attached when the
/// power came on, and one whose announcement was lost while the chip was
/// unwell. A tenth of a second is imperceptible to whoever just plugged a
/// keyboard in.
inline constexpr std::uint32_t kConnectPollUs = 100000;

/// How many events can be held before the oldest is dropped.
inline constexpr std::size_t kEventQueueDepth = 8;

class Ch375Device {
public:
    Ch375Device(Ch375Transport& transport, IDeviceSetup& setup)
        : transport_(transport), setup_(setup) {}

    /// Do a bounded piece of work.
    void tick(std::uint32_t now_us);

    Ch375State state() const { return state_; }

    /// Bring-up: which state the last detach interrupted, and the last status
    /// byte the chip reported.
    ///
    /// Not for the product to act on - it is here so that "the device keeps
    /// attaching and detaching" can be answered with where and what, instead
    /// of another guess about which step is at fault.
    Ch375State state_at_last_detach() const { return detach_state_; }
    std::uint8_t last_status() const { return last_status_; }

    /// How many times each kind of status has been read, for bring-up.
    ///
    /// Counted rather than kept as "the last one", because the last one is
    /// whatever happened most recently and says nothing about what the chip
    /// has been doing. The bands come from DS1 5.12.
    std::uint16_t status_connect() const { return status_connect_; }
    std::uint16_t status_disconnect() const { return status_disconnect_; }
    std::uint16_t status_success() const { return status_success_; }
    std::uint16_t status_failure() const { return status_failure_; }
    /// Statuses a chip in host mode cannot produce - 00 to 0F is device mode.
    std::uint16_t status_impossible() const { return status_impossible_; }

    /// How often the interrupt line was found asserted, and how often reading
    /// the status that goes with it actually produced a byte.
    ///
    /// Two numbers rather than one, because "no statuses were read" has two
    /// completely different causes and the same appearance: a line that never
    /// asserts, and a read that never completes.
    std::uint16_t interrupts_seen() const { return interrupts_seen_; }
    std::uint16_t status_reads_failed() const { return status_reads_failed_; }

    /// Which of the two ways out of a working device was taken.
    ///
    /// Both need an interrupt, and none has been seen - so one of these
    /// numbers disagrees with that, and the disagreement is the bug.
    std::uint16_t detach_from_disconnect() const { return detach_from_disconnect_; }
    std::uint16_t detach_from_lost() const { return detach_from_lost_; }
    std::uint16_t enumerate_failures() const { return enumerate_failures_; }

    /// How often the two mode commands were refused, and how often the chip
    /// turned out to be alive at some other rate.
    ///
    /// Both mode failures used to be silent. A channel could spin between them
    /// for hours with every reading frozen, which is indistinguishable from a
    /// board that has stopped running, and was read as one.
    std::uint16_t setup_mode_failures() const { return setup_mode_failures_; }
    std::uint16_t recover_mode_failures() const { return recover_mode_failures_; }
    std::uint16_t chip_found_elsewhere() const { return chip_found_elsewhere_; }

    /// How often the chip answered CHECK_EXIST while refusing a mode command.
    ///
    /// A deaf chip and a chip that will not change mode look identical from
    /// outside - both are a channel that does nothing - and they want
    /// completely different repairs.
    std::uint16_t alive_but_refusing() const { return alive_but_refusing_; }

    /// The sampling rate a dead channel's reply read correctly at, or zero.
    ///
    /// Zero after a sweep means the chip really is saying nothing. Anything
    /// else means it was talking the whole time and this side was listening
    /// at the wrong speed, which is a fault in here and not on the bench.
    unsigned rx_sweep_hit() const { return rx_sweep_hit_; }

    /// The byte the chip answered the refused mode command with, and whether
    /// it answered at all. Three different faults arrive as one refusal.
    /// How often the chip had not finished resetting when it was asked.
    ///
    /// A number that climbs while the channel eventually works means the wait
    /// is merely optimistic. One that climbs while nothing works means the
    /// chip is not coming back at all, which is a different fault.
    std::uint16_t chip_not_back_yet() const { return chip_not_back_yet_; }

    /// The rate the search last found the chip answering at, or zero.
    ///
    /// The rate, not a tally, because the rate is the fact: it says whether
    /// the chip was where this code had put it - so this side abandoned a rate
    /// the chip was holding - or somewhere neither end chose.
    ///
    /// Zero is three cases, not the two this note used to name. The search has
    /// never run; the search ran and found nothing anywhere; or the search ran
    /// and found the chip at the home rate, which is written nowhere because
    /// only a chip found away from home is recorded here. chip_found_elsewhere
    /// counts the third case out - it moves only when the chip was somewhere
    /// else - and chip_not_back_yet says whether the search was ever provoked
    /// at all.
    unsigned chip_found_at() const { return chip_found_at_; }

    /// How often an idle channel found its chip no longer answering.
    ///
    /// Nonzero means a chip was power-cycled, or otherwise stopped being a
    /// configured chip, under a running U1 - and that the channel noticed by
    /// itself instead of sitting in Absent until somebody reflashed.
    std::uint16_t presence_lost() const { return presence_lost_; }

    /// How often a quiet endpoint was re-armed instead of torn down.
    ///
    /// Each teardown avoided is a peripheral that did not go dark and come
    /// back, and a set of held keys that was not released and re-acquired.
    std::uint16_t quiet_rearms() const { return quiet_rearms_; }

    /// How often a raised link ran and then collapsed.
    ///
    /// Each one costs a rung. A rate that answers twice and then drops the
    /// link after two thousand transactions was never this channel's rate;
    /// only running at it finds that out.
    std::uint16_t collapses_while_raised() const { return collapses_while_raised_; }

    std::uint8_t mode_reply() const { return mode_reply_; }
    bool mode_answered() const { return mode_answered_; }
    bool rx_swept() const { return rx_swept_; }

    /// How often the chip would not move to the faster port rate.
    ///
    /// Nonzero means the link is running at 9600, where collecting one mouse
    /// report costs more time than a moving hand takes to produce the next -
    /// so the pointer will be slow and behind, and it will not be obvious why.
    std::uint16_t baud_change_failures() const { return baud_change_failures_; }
    std::uint16_t mode_failures() const { return mode_failures_; }

    /// How many times the device's endpoint has been polled, and how many
    /// reports came back from it.
    std::uint16_t polls_issued() const { return polls_issued_; }

    /// Whether the device that is attached answered as a low-speed one.
    ///
    /// Only meaningful when device_rate_known() is true. The chip is asked
    /// once per attach and the answer decides how the bus runs from then on,
    /// so "it did not answer" and "full speed" have to stay apart: they used
    /// to be folded into one boolean, and a low-speed mouse addressed at eight
    /// times its rate says nothing and is reported gone.
    bool device_is_low_speed() const { return device_is_low_speed_; }

    /// Did the chip actually answer GET_DEVICE_RATE for the device now up?
    bool device_rate_known() const { return device_rate_known_; }

    /// Take the oldest event, if there is one.
    bool take_event(Ch375Event& event);

    /// Bring-up only: skip the USB bus reset when bringing a device up.
    ///
    /// The datasheet's sequence is mode 7 then mode 6 (DS1 5.9), and that is
    /// what this does by default. But on the bench a device is reported gone
    /// exactly once per reset and never comes back, so being able to leave the
    /// reset out is what separates "the reset is killing it" from "it was
    /// leaving anyway".
    void skip_bus_reset(bool skipping) { skip_bus_reset_ = skipping; }

    /// Configure the attached device again from scratch.
    ///
    /// Does nothing when there is no device: there would be nothing to
    /// configure, and pretending otherwise would reset a bus with nothing on
    /// it every time the configurator asked.
    void request_reenumeration();

private:
    /// Get the chip itself answering, a bounded piece per tick.
    ///
    /// True only on the tick that finishes the job, which is the tick the rest
    /// of the state machine may run in.
    bool bring_chip_up(std::uint32_t now_us);

    /// Down a rung, but never below a rate that can carry this device.
    void step_ladder_down();

    /// The slowest rung this channel's device can actually be run at.
    std::size_t slowest_usable_rung() const;

    void enter(Ch375State state, std::uint32_t now_us);
    void publish(Ch375EventKind kind);
    void publish_report(const std::uint8_t* data, std::size_t size);
    void fail(std::uint32_t now_us);
    void handle_detach(std::uint32_t now_us);

    /// Read the chip's interrupt status if it is asking. False if it is not.
    bool poll_interrupt(InterruptStatus& status);

    /// Collect the status byte of a command asked on an earlier tick, and do
    /// whatever the state that asked it meant to do with the answer.
    void finish_pending_command(std::uint32_t now_us);

    /// The last steps of chip setup, once the chip has taken a working mode.
    ///
    /// True, like bring_chip_up itself, only on the tick that finishes.
    bool finish_chip_setup(std::uint32_t now_us);

    Ch375Transport& transport_;
    IDeviceSetup& setup_;

    Ch375State state_ = Ch375State::Absent;
    bool chip_ready_ = false;
    ChipBringUp bring_up_ = ChipBringUp::Idle;
    PendingCommand pending_command_ = PendingCommand::None;
    std::uint32_t chip_reset_at_us_ = 0;
    bool skip_bus_reset_ = false;
    /// What the attached device turned out to be, asked while still in the
    /// mode where the question is valid.
    bool device_is_low_speed_ = false;
    /// Whether that came from an answer rather than from a default.
    bool device_rate_known_ = false;
    bool announced_ready_ = false;
    std::uint32_t entered_us_ = 0;
    std::uint32_t last_poll_us_ = 0;
    /// A token has been issued and its interrupt has not arrived yet.
    bool token_outstanding_ = false;
    std::uint32_t token_at_us_ = 0;
    std::uint32_t last_connect_poll_us_ = 0;
    /// The idle channel's own CHECK_EXIST, and when it last answered.
    ///
    /// Kept here as well as in the transport's one pending-reply slot, because
    /// the two say different things: the slot says which question is on the
    /// wire, and this says that this state is waiting for an answer. A probe
    /// whose slot was taken by another reader is then noticed as unanswered
    /// rather than silently forgotten.
    bool presence_probe_running_ = false;
    /// Consecutive idle re-checks that went unanswered.
    std::uint16_t unanswered_presence_ = 0;
    std::uint32_t last_presence_us_ = 0;
    /// The idle channel's own TEST_CONNECT, and what it answered.
    bool connect_probe_running_ = false;
    InterruptStatus probed_connect_ = InterruptStatus::Disconnect;
    std::uint32_t last_answer_us_ = 0;
    std::uint8_t endpoint_ = 0;
    Ch375State detach_state_ = Ch375State::Absent;
    std::uint8_t last_status_ = 0;
    std::uint16_t status_connect_ = 0;
    std::uint16_t status_disconnect_ = 0;
    std::uint16_t status_success_ = 0;
    std::uint16_t status_failure_ = 0;
    std::uint16_t status_impossible_ = 0;
    std::uint16_t interrupts_seen_ = 0;
    std::uint16_t status_reads_failed_ = 0;
    std::uint16_t detach_from_disconnect_ = 0;
    std::uint16_t detach_from_lost_ = 0;
    std::uint16_t enumerate_failures_ = 0;
    std::uint16_t baud_change_failures_ = 0;
    std::uint16_t setup_mode_failures_ = 0;
    std::uint16_t recover_mode_failures_ = 0;
    std::uint16_t chip_found_elsewhere_ = 0;
    std::uint16_t alive_but_refusing_ = 0;
    bool rx_swept_ = false;
    unsigned rx_sweep_hit_ = 0;
    std::uint16_t chip_not_back_yet_ = 0;
    /// Consecutive probes at the home rate that went unanswered.
    std::uint16_t unanswered_probes_ = 0;
    unsigned chip_found_at_ = 0;
    std::uint16_t presence_lost_ = 0;
    std::uint16_t quiet_rearms_ = 0;
    std::uint16_t collapses_while_raised_ = 0;
    std::uint8_t relights_ = 0;
    unsigned raised_baud_ = kCh375DefaultBaud;
    std::uint8_t mode_reply_ = 0;
    bool mode_answered_ = false;
    std::size_t baud_rung_ = 0;
    bool baud_exhausted_ = false;
    std::uint16_t mode_failures_ = 0;
    std::uint16_t polls_issued_ = 0;
    /// Block reads that have failed in a row on the rate now in use.
    std::uint16_t block_read_failures_ = 0;
    /// Which data packet the next IN transaction should expect. Alternates on
    /// every one that succeeds; the chip does not track it.
    bool expect_data1_ = false;

    Ch375Event events_[kEventQueueDepth];
    std::size_t head_ = 0;
    std::size_t count_ = 0;
};

}  // namespace duo_input::u1::ch375
