#pragma once

// Two fake CH375s: one that recites, one that behaves.
//
// ScriptedCh375 exists only as a written-down conversation, and is what the
// transport tests need - they are about the exact bytes of each command, in
// order. Ch375Device cannot be tested that way: it decides what to ask next
// from the state it is in, so a fixed script would be asserting the state
// machine's shape rather than its behaviour. FakeCh375Chip answers the way a
// chip does, whatever order it is asked in, and can be told to misbehave.
//
// In both, the clock moves only when the code under test looks for a byte that
// has not arrived. That makes a timeout deterministic - it takes exactly as
// many polls as the deadline allows - and it makes a test that would otherwise
// spin forever finish and fail instead.
//
// ScriptedCh375 records anything sent that the script did not expect rather
// than ignoring it, because a port out of step with this chip is the failure
// that matters: the CH375 answers commands positionally, so one extra or
// missing byte turns every later reply into plausible nonsense.

#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <string>
#include <vector>

#include "ch375/commands.hpp"
#include "ch375/device.hpp"
#include "ch375/transport.hpp"

namespace duo_input::u1::ch375::testing {

struct Step {
    enum class Kind : std::uint8_t {
        ExpectCommand,
        ExpectData,
        Reply,
    };

    Kind kind = Kind::Reply;
    std::uint8_t value = 0;
};

inline Step expect_command(Ch375Command command) {
    return Step{Step::Kind::ExpectCommand, static_cast<std::uint8_t>(command)};
}

inline Step expect_data(std::uint8_t value) {
    return Step{Step::Kind::ExpectData, value};
}

inline Step reply(std::uint8_t value) {
    return Step{Step::Kind::Reply, value};
}

/// The rate a CH375 comes up at, and goes back to after a reset.
inline constexpr unsigned kScriptedDefaultBaud = 9600;

/// DS2 1.3: the retry policy a CH375 holds when nobody has set one.
///
/// Bit 7 set with bit 6 clear is "retry a NAK forever", so under it an
/// endpoint with nothing to say never finishes its transaction and never
/// raises the interrupt that would end it. This fake also puts the policy back
/// here on every working mode, the way DS2 1.1 says the bus speed goes back to
/// full speed - assumed of the retry policy rather than read in a datasheet,
/// and the assumption is why the firmware sets it again after each mode.
inline constexpr std::uint8_t kChipDefaultRetry = 0x85;

class ScriptedCh375 final : public ICh375Transport {
public:
    ScriptedCh375(std::initializer_list<Step> script) : script_(script) {}

    // --- the port the transport is written against -------------------------

    void write_command(std::uint8_t command) override;
    void write_data(std::uint8_t value) override;
    bool read_data(std::uint8_t& value) override;
    bool int_asserted() const override { return int_asserted_; }
    std::uint32_t now_us() const override { return now_us_; }

    bool set_baud(unsigned baud) override {
        if (refuse_baud_) {
            return false;
        }
        baud_ = baud;
        rx_baud_ = baud;
        ++baud_changes_;
        return true;
    }

    /// The rate this chip is answering at, once it has been told to move.
    ///
    /// Set by a test to model a chip that took SET_BAUDRATE. A reply is only
    /// readable when the receiver is at the rate the chip is speaking - which
    /// is the whole difficulty of that command and was, until this existed,
    /// the one thing none of its tests could see.
    void answers_at(unsigned baud) { chip_baud_ = baud; }

    /// Stand in for a port with a fixed rate.
    void refuse_baud_changes() { refuse_baud_ = true; }

    /// Accept anything and answer nothing - a chip that is simply not there.
    void allow_unscripted() { unscripted_ = true; }

    int data_bytes_written() const { return data_written_; }
    int commands_written() const { return commands_written_; }

    /// Was a chip reset sent while the port was at this rate?
    ///
    /// The question that matters when a rate is abandoned: a chip that moved
    /// has to be told to reset while it can still hear.
    bool saw_reset_at(unsigned baud) const {
        for (unsigned at : reset_baud_) {
            if (at == baud) {
                return true;
            }
        }
        return false;
    }

    unsigned baud() const { return baud_; }

    bool set_rx_baud(unsigned baud) override {
        rx_baud_ = baud;
        return true;
    }

    unsigned rx_baud() const { return rx_baud_; }
    int baud_changes() const { return baud_changes_; }

    // --- what the test drives and asks ------------------------------------

    /// Every step consumed, in order, with nothing unexpected on the wire.
    bool complete() const { return next_ == script_.size() && violations_.empty(); }

    /// What went wrong, for a failing test to be readable.
    const std::string& violations() const { return violations_; }

    void assert_int(bool asserted) { int_asserted_ = asserted; }

    /// Move the clock without anyone polling, for setting a scene.
    void advance(std::uint32_t micros) { now_us_ += micros; }

    std::uint32_t elapsed_us() const { return now_us_ - start_us_; }

private:
    void note(const char* what, std::uint8_t value);

    std::vector<Step> script_;
    std::size_t next_ = 0;
    std::string violations_;
    unsigned baud_ = kScriptedDefaultBaud;
    unsigned rx_baud_ = kScriptedDefaultBaud;
    unsigned chip_baud_ = 0;
    bool refuse_baud_ = false;
    bool unscripted_ = false;
    int data_written_ = 0;
    int commands_written_ = 0;
    std::vector<unsigned> reset_baud_;
    int baud_changes_ = 0;
    bool int_asserted_ = false;
    std::uint32_t start_us_ = 1000;
    std::uint32_t now_us_ = 1000;
};

/// A CH375 that behaves like one, for testing a state machine against.
///
/// It models what the lifecycle depends on and nothing else: the interrupt
/// line, the pending status byte, the working mode, a device that can be
/// plugged in and pulled out, and reports waiting to be read. It can also be
/// told to answer nonsense or to stop answering, because those are the cases
/// the firmware exists to survive.
class FakeCh375Chip final : public ICh375Transport {
public:
    // --- the port ----------------------------------------------------------

    void write_command(std::uint8_t command) override;
    void write_data(std::uint8_t value) override;
    bool read_data(std::uint8_t& value) override;
    bool int_asserted() const override;
    std::uint32_t now_us() const override { return now_us_; }

    // --- the scene ---------------------------------------------------------

    void attach_device();
    void detach_device();

    /// Give the device something to say the next time it is polled.
    void queue_report(const std::uint8_t* data, std::size_t size);

    /// Was the chip asked to configure the device by itself?
    bool saw_auto_setup() const { return saw_auto_setup_; }

    // --- a device on the far side of the bus -------------------------------
    //
    // Enough of one to be enumerated: it answers descriptor requests, takes an
    // address, and remembers which configuration was chosen. What the tests
    // are about is the order those happen in and whether the host keeps up
    // with the address it handed out.

    /// Serve the descriptors of an ordinary boot mouse on endpoint 2.
    void serve_boot_mouse();
    /// A composite whose first HID interface is consumer controls.
    void serve_composite_keyboard();
    /// A hub, which this firmware does not support.
    void serve_hub();
    /// A mouse that declares the mouse protocol but not the boot subclass.
    ///
    /// Most mice sold today are this. There is no boot report behind the
    /// interface, so asking it to switch to one is asking for something that
    /// does not exist.
    void serve_mouse_without_boot();

    /// A mouse that declares a HID report descriptor and will hand it over.
    ///
    /// The other serve_ helpers deliberately declare none: they were written
    /// before anything fetched one, and a device that names no report
    /// descriptor is exactly the fallback case. This one names it, sizes the
    /// HID record to it, and answers the request for it.
    void serve_mouse_with_report_descriptor(const std::vector<std::uint8_t>& descriptor,
                                            bool boot_subclass);

    /// A composite whose first HID interface is consumer controls and whose
    /// second is the mouse, both declaring report descriptors.
    ///
    /// wIndex is an interface number and a composite has several. Sent to the
    /// wrong one, the request fetches somebody else's descriptor - or nothing.
    void serve_composite_mouse_with_report_descriptor(
        const std::vector<std::uint8_t>& descriptor);

    /// A boot keyboard that declares a report descriptor and will hand it
    /// over. Nothing should ever ask it for one.
    void serve_keyboard_with_report_descriptor(const std::vector<std::uint8_t>& descriptor);

    /// What endpoint zero carries in one packet (USB 2.0 9.6.1).
    ///
    /// Eight on a low-speed mouse, which is most of them - so a fifty-byte
    /// report descriptor arrives in seven transactions and not one. The
    /// default here is eight for that reason.
    void set_control_packet_size(std::uint8_t bytes) { control_packet_ = bytes; }

    /// Refuse the report-descriptor request with a STALL, as a device that
    /// does not implement it does. Every other request is still answered.
    void refuse_report_descriptor(bool refusing) { refuse_report_descriptor_ = refusing; }

    /// Answer the report-descriptor request with nothing at all.
    ///
    /// No data and no interrupt: a token issued and never completed, which is
    /// the one failure that cannot be recovered from without resetting the bus.
    void ignore_report_descriptor(bool ignoring) { ignore_report_descriptor_ = ignoring; }

    /// How many times the report descriptor has been asked for, and how many
    /// bytes the last request asked for.
    int report_descriptor_requests() const { return report_descriptor_requests_; }
    std::uint16_t report_descriptor_asked_for() const { return report_descriptor_asked_; }
    /// How many IN transactions its data stage took.
    int report_descriptor_packets() const { return report_descriptor_packets_; }
    /// Was the transfer closed with an empty packet the host sent?
    int control_read_status_stages() const { return control_read_status_stages_; }
    /// The transmitter's data toggle, as it was last set (DS2 1.7).
    std::uint8_t transmit_toggle() const { return transmit_toggle_; }

    // --- control transfers the chip has no dedicated command for ------------
    //
    // SET_ADDRESS, SET_CONFIG and GET_DESCR are commands of their own; every
    // other request has to be assembled by hand - the eight bytes of a setup
    // packet into the endpoint buffer, then a SETUP token at endpoint zero,
    // then the status stage. This models all three parts, because a transfer
    // left half done is a device that never received the request.

    /// Every setup packet the device was sent, in order, eight bytes each.
    const std::vector<std::vector<std::uint8_t>>& setup_packets() const {
        return setup_packets_;
    }

    /// Is the device in boot protocol?
    ///
    /// True only once a SET_PROTOCOL asking for it has completed its status
    /// stage, because that is when a real device applies the request.
    bool boot_protocol_selected() const { return boot_protocol_; }

    /// How many control transfers were finished off with a status stage.
    int control_status_stages() const { return control_status_stages_; }

    /// Refuse every setup packet with a STALL, as a device that does not
    /// support the request does.
    void refuse_setup_requests(bool refusing) { refuse_setup_ = refusing; }

    /// Answer setup packets with nothing whatsoever - no data, no interrupt.
    ///
    /// The worst case for anything that waits: only a deadline ends it.
    void ignore_setup_requests(bool ignoring) { ignore_setup_ = ignoring; }

    /// What this mouse would send for a movement, in the protocol it is in.
    ///
    /// In report protocol it leads with its Report ID, which is what the
    /// mouse on the bench does: seven bytes, `01 00 dx dy 00 00 00`. Put into
    /// boot protocol it sends the fixed report and no identifier at all.
    std::vector<std::uint8_t> report_for(std::int8_t dx, std::int8_t dy) const;

    /// Refuse everything after this many control transfers have succeeded.
    void stall_after(int transfers) { stall_after_ = transfers; }

    /// Make the device answer NAK this many times before it is ready to talk.
    ///
    /// A NAK to a control transfer is a device saying "busy, ask again". Who
    /// asks again is the retry policy's business (DS2 1.3): a chip told to
    /// retry asks on the bus, so every one of these is consumed inside the one
    /// transaction the MCU asked for and the MCU sees a transfer that took a
    /// little longer. A chip told to report hands the first one to the MCU,
    /// and the transfer it belonged to is over.
    void nak_control_transfers(int transfers) { control_naks_ = transfers; }

    /// Keep a retried control NAK in flight until ABORT_NAK is issued.
    ///
    /// This models the upper end of DS2 1.3's retry window: the firmware's
    /// setup deadline can expire before the chip finishes retrying, and only
    /// ABORT_NAK makes the command port available for recovery traffic.
    void hold_control_nak_retry(bool holding) { hold_control_nak_retry_ = holding; }
    bool nak_retry_in_progress() const { return nak_retry_in_progress_; }
    std::uint32_t abort_nak_count() const { return abort_nak_count_; }

    /// How long this chip takes to answer a token.
    ///
    /// A real controller runs a USB transaction and raises its interrupt when
    /// it is done; it is not instant, and a host that assumes it is will issue
    /// the next token into a chip still working on the last one.
    void answer_tokens_after(std::uint32_t micros) { token_delay_us_ = micros; }

    /// What an idle interrupt-IN token reports to the MCU.
    ///
    /// The ordinary answer is 0x2A (NAK). STALL (0x2E) and timeout (0x20)
    /// exercise the path that must eventually release anything held by a
    /// device which is no longer usable but whose Disconnect was lost.
    void answer_idle_tokens_with(std::uint8_t status) { idle_token_status_ = status; }

    /// How many IN tokens have been issued to this chip.
    int tokens_issued() const { return tokens_issued_; }

    /// The retry policy this chip is holding (DS2 1.3).
    std::uint8_t retry_policy() const { return retry_policy_; }

    /// Was the retry policy set after the most recent working mode?
    ///
    /// The question speed_set_after_last_mode already asks, about the other
    /// per-mode setting.
    bool retry_set_after_last_mode() const { return retry_after_mode_; }

    /// Lose one unacknowledged SET_RETRY policy write.
    void drop_next_retry_policy(std::uint8_t policy) {
        dropped_retry_policy_ = policy;
        drop_retry_policy_ = true;
    }

    /// The next SET_USB_MODE is heard but its status reply is lost/refused.
    void ignore_next_mode_reply() { next_mode_reply_ = 1; }
    void refuse_next_mode_reply() { next_mode_reply_ = 2; }

    std::uint8_t device_address() const { return device_address_; }
    std::uint8_t host_address() const { return host_address_; }
    std::uint8_t configuration_value() const { return configuration_value_; }

    /// Was every step taken before the step that depends on it?
    bool order_was_correct() const { return order_ok_; }

    /// Make the attached device answer as a 1.5 Mbps one.
    void set_low_speed(bool low) { low_speed_ = low; }

    /// What the bus was last set to.
    UsbSpeed bus_speed() const { return bus_speed_; }

    /// Was the speed set after the most recent working mode?
    ///
    /// Setting a mode puts the bus back to full speed (DS2 1.1), so a speed
    /// chosen before it is quietly undone.
    bool speed_set_after_last_mode() const { return speed_after_mode_; }

    /// Report a disconnect the moment the USB bus is held in reset, which is
    /// what the real chip does with a device attached.
    void report_disconnect_on_reset(bool reporting) { report_disconnect_on_reset_ = reporting; }

    /// Keep reporting it once frames are switched on, which is what the chip
    /// does while a device is still coming back after the reset.
    void report_disconnect_while_settling(bool reporting) {
        report_disconnect_settling_ = reporting;
    }

    /// Refuse to configure, as a device the chip cannot talk to would cause.
    void fail_auto_setup(bool failing) { fail_auto_setup_ = failing; }

    // --- the serial port's own rate ----------------------------------------
    //
    // The chip and this side each have one, and the two coming apart is the
    // fault the recovery path exists for. There is no reset line to a CH375 on
    // this board (docs/hardware/ch375-wiring.md gives RXD, TXD and INT and
    // nothing else), so a chip left at a rate this side abandoned hears
    // nothing, answers nothing, and cannot be told anything either.

    bool set_baud(unsigned baud) override;
    bool set_rx_baud(unsigned baud) override;

    /// What this side is transmitting at, and what it is listening at.
    unsigned port_baud() const { return port_baud_; }
    unsigned port_rx_baud() const { return port_rx_baud_; }

    /// What the chip itself is speaking and listening at.
    ///
    /// It comes up at 9600 and returns there after RESET_ALL (DS1 5.2), and
    /// SET_BAUDRATE is the only other thing that moves it.
    unsigned chip_baud() const { return chip_baud_; }

    /// Fail every block read while the port is at or above this rate.
    ///
    /// The bench's own reading: at 115200 and 62500 CHECK_EXIST answers fine
    /// and every multi-byte read fails with framing errors, while 37500
    /// carries them. Nothing decodable arrives, which is what a receiver
    /// sampling a rate the wiring cannot hold produces.
    void break_block_reads_at_or_above(unsigned baud) { block_reads_break_at_ = baud; }

    /// Cycle the module's 5 V under a running U1.
    ///
    /// The chip comes back at 9600 with no working mode and nothing pending,
    /// while this side is still talking at whatever rate it raised the chip
    /// to. That is the state every power cycle on this bench produced, and
    /// there is no reset line to notice it with.
    void power_cycle();

    /// Leave the chip at a rate this side is not using.
    ///
    /// The state a reflash of U1 leaves behind: the processor restarts at
    /// 9600 and the controller is still wherever the last run put it.
    void strand_at(unsigned baud) { chip_baud_ = baud; }

    /// Answer every command with a byte that means nothing.
    void answer_garbage(bool broken) { garbage_ = broken; }

    /// Hold the interrupt line asserted and never answer GET_STATUS.
    ///
    /// The state the keyboard's channel was measured in on hardware at
    /// 2026-08-29 11:31: attached, stuck before Ready, INT held, and every
    /// status read going unanswered. It is the one case where reading the
    /// status blocks on every single tick, because the line is still asserted
    /// on the next one.
    void hold_interrupt_unanswered(bool holding) { hold_int_unanswered_ = holding; }

    /// Stop answering at all, as a chip with a broken port would.
    void go_silent(bool silent) { silent_ = silent; }

    /// Answer this many more commands and then stop answering at all.
    ///
    /// The state the hardware was measured in at 2026-08-29 12:06: a channel
    /// that attached and never reached Ready, so the chip was answering when
    /// the device arrived and had stopped by the time the bring-up sequence
    /// was through. A chip that is deaf from the start never gets that far,
    /// which is why go_silent alone cannot reach the stretch between attach
    /// and Ready.
    ///
    /// It goes deaf at a command boundary, never in the middle of a reply
    /// already begun: a controller that has lost sync stops responding to
    /// commands, and a reply cut in half is a framing error, which is a
    /// different fault with a different knob (break_block_reads_at_or_above).
    void go_silent_after(int commands) { deaf_countdown_ = commands; }

    /// Drop the answer to the next few CHECK_EXISTs, and nothing else.
    ///
    /// One byte lost on a wire, rather than a chip that has stopped being a
    /// chip. The two are the same from outside - a probe that goes unanswered
    /// - and they want completely different responses, because taking the
    /// second for the first re-runs the whole of chip setup on a healthy
    /// channel.
    void miss_next_check_exists(int count) { missed_check_exists_ = count; }

    void advance(std::uint32_t micros) { now_us_ += micros; }

    UsbMode mode() const { return mode_; }
    bool saw_bus_reset() const { return saw_bus_reset_; }
    /// How many times the USB bus has been reset - one per setup attempt.
    std::uint32_t reset_count() const { return reset_count_; }

    /// How many times a working mode has been asked for.
    ///
    /// This is what counts attempts when the chip is misbehaving: a controller
    /// that refuses host mode never gets as far as resetting the bus, so
    /// reset_count would sit at zero however many times it was retried.
    std::uint32_t mode_set_count() const { return mode_set_count_; }

    /// Was a command byte ever written at a rate other than this one?
    ///
    /// The standing constraint this whole recovery path lives under: never
    /// write a command at a port rate that has not just been proved. The chip
    /// reads commands positionally, so a byte it half-hears at the wrong rate
    /// is swallowed as somebody's parameter and every byte after it is out of
    /// step - which is a chip that has stopped answering, and on this board
    /// there is no reset line to bring it back.
    ///
    /// Counters about what a search found cannot hold that constraint: a chip
    /// that answers nowhere makes them read zero whether the guard exists or
    /// not. This looks at the wire instead.
    bool wrote_commands_away_from(unsigned baud) const {
        for (unsigned at : command_bauds_) {
            if (at != baud) {
                return true;
            }
        }
        return false;
    }

    /// How many times the port has been probed for existence.
    ///
    /// The first thing a recovery attempt does, and the only thing it does
    /// when the chip is answering nonsense - so it, not the mode command,
    /// is what says an attempt happened at all.
    std::uint32_t check_exist_count() const { return check_exist_count_; }

    std::uint32_t command_count() const { return command_count_; }
    void reset_command_count() { command_count_ = 0; }

private:
    void queue(std::uint8_t value);

    std::uint8_t pending_command_ = 0;
    bool expecting_data_ = false;

    std::vector<std::uint8_t> outgoing_;
    std::size_t outgoing_read_ = 0;

    std::vector<std::uint8_t> report_;
    bool report_waiting_ = false;

    UsbMode mode_ = UsbMode::DeviceDisabled;
    bool attached_ = false;
    bool int_asserted_ = false;
    std::uint8_t pending_status_ = 0;

    /// Can the chip hear this side, and can this side hear the chip?
    ///
    /// Both ends have to be at the same rate. Half-hearing is not modelled as
    /// plausible nonsense - the chip simply misses the frame - which is the
    /// safe direction: a test cannot pass by accident on a byte that only
    /// looked right.
    bool chip_hears() const { return port_baud_ == chip_baud_; }
    bool chip_is_audible() const { return port_rx_baud_ == chip_baud_; }

    /// At or above this rate no block read produces anything readable.
    unsigned block_reads_break_at_ = 0;

    unsigned chip_baud_ = kScriptedDefaultBaud;
    unsigned port_baud_ = kScriptedDefaultBaud;
    unsigned port_rx_baud_ = kScriptedDefaultBaud;
    /// The two divisor bytes of SET_BAUDRATE, as they arrive.
    std::uint8_t baud_coefficient_ = 0;
    bool baud_coefficient_seen_ = false;

    bool garbage_ = false;
    bool silent_ = false;
    /// Commands left to answer before this chip goes deaf. Negative is never.
    int deaf_countdown_ = -1;
    bool hold_int_unanswered_ = false;
    int missed_check_exists_ = 0;
    bool saw_bus_reset_ = false;
    bool saw_auto_setup_ = false;
    bool fail_auto_setup_ = false;
    bool report_disconnect_on_reset_ = false;
    bool report_disconnect_settling_ = false;
    bool low_speed_ = false;
    std::vector<std::uint8_t> configuration_;
    bool read_device_descriptor_ = false;
    bool read_configuration_ = false;
    std::uint8_t device_address_ = 0;
    std::uint8_t host_address_ = 0;
    std::uint8_t configuration_value_ = 0;
    bool order_ok_ = true;
    int stall_after_ = -1;
    std::uint32_t token_delay_us_ = 0;
    std::uint32_t token_ready_us_ = 0;
    bool token_pending_ = false;
    int tokens_issued_ = 0;
    std::uint8_t idle_token_status_ = static_cast<std::uint8_t>(0x20 | kResponseNak);
    std::uint8_t retry_policy_ = kChipDefaultRetry;
    bool retry_after_mode_ = false;
    bool retry_prefix_seen_ = false;
    bool drop_retry_policy_ = false;
    std::uint8_t dropped_retry_policy_ = 0;
    /// 0=normal, 1=silent, 2=explicit CommandStatus::Abort.
    std::uint8_t next_mode_reply_ = 0;
    int transfers_done_ = 0;
    /// What the next RD_USB_DATA0 will hand back.
    std::vector<std::uint8_t> pending_read_;
    void finish_transfer(bool stalled);
    /// The block WR_USB_DATA7 is filling, and how much of it is still to come.
    std::vector<std::uint8_t> outbound_block_;
    int block_remaining_ = -1;
    std::vector<std::vector<std::uint8_t>> setup_packets_;
    /// A setup packet has been accepted and is waiting for its status stage.
    bool control_pending_ = false;
    int control_status_stages_ = 0;
    bool refuse_setup_ = false;
    bool ignore_setup_ = false;
    std::vector<std::uint8_t> report_descriptor_;
    /// What is left of the report descriptor's data stage, and where it is up
    /// to. A control read is one SETUP and then as many INs as it takes.
    std::size_t control_read_at_ = 0;
    std::size_t control_read_total_ = 0;
    bool control_read_open_ = false;
    /// Which packet the device is sending next in a control read's data stage.
    bool control_read_data1_ = true;
    std::uint8_t receive_toggle_ = 0;
    std::uint8_t control_packet_ = 8;
    bool refuse_report_descriptor_ = false;
    bool ignore_report_descriptor_ = false;
    int report_descriptor_requests_ = 0;
    std::uint16_t report_descriptor_asked_ = 0;
    int report_descriptor_packets_ = 0;
    int control_read_status_stages_ = 0;
    std::uint8_t transmit_toggle_ = 0;
    bool boot_protocol_ = false;
    int control_naks_ = 0;
    bool hold_control_nak_retry_ = false;
    bool nak_retry_in_progress_ = false;
    std::uint32_t abort_nak_count_ = 0;
    void begin_control_transfer();
    void serve_control_read_packet();
    void finish_control_stage();
    /// DS2 1.3: bit 7 is what chooses between retrying a NAK on the bus and
    /// handing it to the MCU as a failure status.
    bool retries_naks() const { return (retry_policy_ & 0x80) != 0; }
    /// Take one control transfer's worth of the device's settling NAKs.
    ///
    /// True when the MCU has to be told about it, which is when the transfer
    /// it belonged to is over.
    bool control_transfer_naks();
    UsbSpeed bus_speed_ = UsbSpeed::Full12Mbps;
    bool speed_after_mode_ = false;
    /// AUTO_SETUP is several control transfers, so its answer is not instant.
    bool auto_setup_running_ = false;
    std::uint32_t auto_setup_at_us_ = 0;
    std::uint32_t reset_count_ = 0;
    std::uint32_t mode_set_count_ = 0;
    std::uint32_t check_exist_count_ = 0;
    std::uint32_t command_count_ = 0;
    /// The port rate every command byte was written at, in order.
    std::vector<unsigned> command_bauds_;
    std::uint32_t now_us_ = 1000;
};

/// Configuring the attached device, faked.
///
/// Task 3 replaces this with real enumeration and descriptor parsing. What the
/// lifecycle needs from it now is only that it takes more than one tick, can
/// fail, and eventually names an endpoint to poll.
class FakeDeviceSetup final : public IDeviceSetup {
public:
    void begin(std::uint32_t now_us) override;
    SetupProgress poll(std::uint32_t now_us, bool interrupted, InterruptStatus status) override;
    std::uint8_t interrupt_endpoint() const override { return 1; }

    void always_fail(bool failing) { failing_ = failing; }

    /// Fetch a block the way real setup fetches descriptors.
    ///
    /// Configuring a device is control transfers whose answers come back as
    /// length-prefixed blocks, and whether those complete is the only thing
    /// that proves a port rate: on the bench CHECK_EXIST answered at 115200
    /// and 62500 while every block read there failed. A fake that never read
    /// one would let a rate that cannot carry a descriptor look like a rate
    /// that works, which is the defect being modelled.
    void reads_descriptors_through(Ch375Transport& transport) { transport_ = &transport; }

    /// Has anyone asked this to start yet?
    bool was_begun() const { return begun_; }

private:
    Ch375Transport* transport_ = nullptr;
    std::uint32_t started_us_ = 0;
    bool running_ = false;
    bool failing_ = false;
    bool begun_ = false;
};

}  // namespace duo_input::u1::ch375::testing
