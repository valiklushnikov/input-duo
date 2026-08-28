// Talking to a CH375 without ever waiting forever.
//
// This layer knows the chip's command contract and nothing about wires. What
// it is given is a port that can send a command byte, send a data byte, take a
// byte if one has arrived, and say what time it is - so the same code drives a
// scripted fake here and real hardware later.
//
// Every wait is bounded. A USB host controller that stops answering must not
// take the keyboard down with it: this device routes someone's typing between
// two computers, and firmware spinning on a byte that will never come stops
// feeding the watchdog, drops the link to U2, and leaves whatever was held
// down held down.

#include "ch375/commands.hpp"
#include "ch375/transport.hpp"
#include "fakes/scripted_ch375.hpp"
#include "test_support.hpp"

using duo_input::u1::ch375::Ch375Command;
using duo_input::u1::ch375::BaudOption;
using duo_input::u1::ch375::Ch375Transport;
using duo_input::u1::ch375::CommandStatus;
using duo_input::u1::ch375::InterruptStatus;
using duo_input::u1::ch375::UsbMode;
using duo_input::u1::ch375::testing::expect_command;
using duo_input::u1::ch375::testing::expect_data;
using duo_input::u1::ch375::testing::reply;
using duo_input::u1::ch375::testing::ScriptedCh375;

// -------------------------------------------------------------- check_exist

TEST_CASE(check_exist_command_only_sends_no_parameter) {
    // A deliberately incomplete command is a diagnostic boundary: CH375 has
    // not received the probe byte yet, so it has no valid reply to send.
    ScriptedCh375 io({expect_command(Ch375Command::CheckExist)});
    Ch375Transport transport(io);

    transport.start_check_exist();

    CHECK(io.complete());
}

TEST_CASE(check_exist_requires_the_inverted_reply) {
    // CH375DS1 section 5.5: the chip answers with the bitwise inverse of
    // whatever byte it is given. It is the one command that proves the port
    // itself works, so nothing else should be attempted until it passes.
    ScriptedCh375 io({expect_command(Ch375Command::CheckExist), expect_data(0xA5), reply(0x5A)});
    Ch375Transport transport(io);

    CHECK(transport.check_exist(0xA5));
    CHECK(io.complete());
}

TEST_CASE(check_exist_fails_when_the_reply_is_not_the_inverse) {
    // A chip that answers, but wrongly, is worse than one that says nothing:
    // it looks alive. Anything that is not the exact inverse is a failure.
    ScriptedCh375 io({expect_command(Ch375Command::CheckExist), expect_data(0xA5), reply(0x5B)});
    Ch375Transport transport(io);

    CHECK(!transport.check_exist(0xA5));
}

TEST_CASE(check_exist_gives_up_when_nothing_answers) {
    ScriptedCh375 io({expect_command(Ch375Command::CheckExist), expect_data(0xA5)});
    Ch375Transport transport(io);

    // No reply is scripted. This must return, not spin.
    CHECK(!transport.check_exist(0xA5));
    CHECK(io.elapsed_us() >= transport.reply_timeout_us());
}

TEST_CASE(check_exist_probes_with_the_byte_it_was_given) {
    ScriptedCh375 io({expect_command(Ch375Command::CheckExist), expect_data(0x57), reply(0xA8)});
    Ch375Transport transport(io);

    // The datasheet's own example: 57H in, A8H out.
    CHECK(transport.check_exist(0x57));
    CHECK(io.complete());
}

// ------------------------------------------------------------- set_usb_mode

TEST_CASE(setting_the_usb_mode_reports_the_operation_status) {
    ScriptedCh375 io({expect_command(Ch375Command::SetUsbMode),
                      expect_data(static_cast<std::uint8_t>(UsbMode::HostWithSof)),
                      reply(static_cast<std::uint8_t>(CommandStatus::Success))});
    Ch375Transport transport(io);

    CHECK(transport.set_usb_mode(UsbMode::HostWithSof));
    CHECK(io.complete());
}

TEST_CASE(a_refused_usb_mode_is_a_failure_not_a_silence) {
    ScriptedCh375 io({expect_command(Ch375Command::SetUsbMode),
                      expect_data(static_cast<std::uint8_t>(UsbMode::HostReset)),
                      reply(static_cast<std::uint8_t>(CommandStatus::Abort))});
    Ch375Transport transport(io);

    CHECK(!transport.set_usb_mode(UsbMode::HostReset));
    CHECK(io.complete());
}

TEST_CASE(a_status_byte_that_is_neither_success_nor_abort_is_a_failure) {
    // The chip is documented to answer 51H or 5FH here. Treating anything else
    // as success would accept a desynchronised port as a working one.
    ScriptedCh375 io({expect_command(Ch375Command::SetUsbMode),
                      expect_data(static_cast<std::uint8_t>(UsbMode::HostWithSof)), reply(0x00)});
    Ch375Transport transport(io);

    CHECK(!transport.set_usb_mode(UsbMode::HostWithSof));
}

// --------------------------------------------------------------- get_status

TEST_CASE(the_interrupt_status_is_read_back_verbatim) {
    ScriptedCh375 io({expect_command(Ch375Command::GetStatus),
                      reply(static_cast<std::uint8_t>(InterruptStatus::Connect))});
    Ch375Transport transport(io);

    InterruptStatus status = InterruptStatus::Success;
    CHECK(transport.get_status(status));
    CHECK_EQ(static_cast<std::uint8_t>(status),
             static_cast<std::uint8_t>(InterruptStatus::Connect));
    CHECK(io.complete());
}

TEST_CASE(an_unrecognised_interrupt_status_is_still_returned) {
    // The failure statuses are a range, not a list - 20H to 3FH encode which
    // PID the device answered with. Refusing to carry an unknown byte would
    // throw away the only evidence of why a transaction failed.
    ScriptedCh375 io({expect_command(Ch375Command::GetStatus), reply(0x2A)});
    Ch375Transport transport(io);

    InterruptStatus status = InterruptStatus::Success;
    CHECK(transport.get_status(status));
    CHECK_EQ(static_cast<std::uint8_t>(status), 0x2Au);
}

TEST_CASE(get_status_gives_up_when_nothing_answers) {
    ScriptedCh375 io({expect_command(Ch375Command::GetStatus)});
    Ch375Transport transport(io);

    InterruptStatus status = InterruptStatus::Success;
    CHECK(!transport.get_status(status));
}

// ------------------------------------------------------------------ reading

TEST_CASE(a_data_block_arrives_length_first) {
    // CH375DS1 section 5.13: the first byte out is the length, then that many
    // bytes follow.
    ScriptedCh375 io({expect_command(Ch375Command::ReadUsbData0), reply(3), reply(0x11),
                      reply(0x22), reply(0x33)});
    Ch375Transport transport(io);

    std::uint8_t buffer[8] = {};
    std::size_t size = 0;
    CHECK(transport.read_block(buffer, sizeof(buffer), size));
    CHECK_EQ(size, 3u);
    CHECK_EQ(buffer[0], 0x11u);
    CHECK_EQ(buffer[2], 0x33u);
    CHECK(io.complete());
}

TEST_CASE(an_empty_data_block_is_a_success_not_a_failure) {
    // A polled endpoint with nothing to say answers with a length of zero, and
    // that happens constantly. Calling it an error would make an idle keyboard
    // look like a broken one.
    ScriptedCh375 io({expect_command(Ch375Command::ReadUsbData0), reply(0)});
    Ch375Transport transport(io);

    std::uint8_t buffer[8] = {};
    std::size_t size = 1;
    CHECK(transport.read_block(buffer, sizeof(buffer), size));
    CHECK_EQ(size, 0u);
}

TEST_CASE(a_block_longer_than_the_buffer_is_refused_before_it_is_written) {
    // The chip's buffer is 64 bytes and a caller's may be smaller. Reading
    // what will not fit is how a device on the far end of a wire overwrites
    // this one's memory.
    ScriptedCh375 io({expect_command(Ch375Command::ReadUsbData0), reply(9)});
    Ch375Transport transport(io);

    std::uint8_t buffer[4] = {0xEE, 0xEE, 0xEE, 0xEE};
    std::size_t size = 0;
    CHECK(!transport.read_block(buffer, sizeof(buffer), size));
    CHECK_EQ(size, 0u);
    CHECK_EQ(buffer[0], 0xEEu);
}

TEST_CASE(a_block_longer_than_the_chip_can_hold_is_refused) {
    // 64 bytes is the documented maximum. A larger length means the port is
    // out of step with the chip, not that a longer packet arrived.
    ScriptedCh375 io({expect_command(Ch375Command::ReadUsbData0), reply(65)});
    Ch375Transport transport(io);

    std::uint8_t buffer[128] = {};
    std::size_t size = 0;
    CHECK(!transport.read_block(buffer, sizeof(buffer), size));
}

TEST_CASE(a_block_that_stops_half_way_is_a_failure) {
    ScriptedCh375 io({expect_command(Ch375Command::ReadUsbData0), reply(4), reply(0x11),
                      reply(0x22)});
    Ch375Transport transport(io);

    std::uint8_t buffer[8] = {};
    std::size_t size = 0;
    CHECK(!transport.read_block(buffer, sizeof(buffer), size));
    CHECK_EQ(size, 0u);
}

// ------------------------------------------------------------------ writing

TEST_CASE(a_data_block_is_written_length_first) {
    ScriptedCh375 io({expect_command(Ch375Command::WriteUsbData7), expect_data(2),
                      expect_data(0xAB), expect_data(0xCD)});
    Ch375Transport transport(io);

    const std::uint8_t payload[2] = {0xAB, 0xCD};
    CHECK(transport.write_block(payload, sizeof(payload)));
    CHECK(io.complete());
}

TEST_CASE(writing_more_than_the_chip_can_hold_sends_nothing_at_all) {
    ScriptedCh375 io({});
    Ch375Transport transport(io);

    std::uint8_t payload[65] = {};
    CHECK(!transport.write_block(payload, sizeof(payload)));
    // Refused before the command byte, so the chip is still in step and the
    // next command will be understood.
    CHECK(io.complete());
}

TEST_CASE(an_empty_write_still_sends_the_length) {
    ScriptedCh375 io({expect_command(Ch375Command::WriteUsbData7), expect_data(0)});
    Ch375Transport transport(io);

    CHECK(transport.write_block(nullptr, 0));
    CHECK(io.complete());
}

// ---------------------------------------------------------- the port itself

TEST_CASE(an_unexpected_byte_on_the_wire_is_reported_by_the_script) {
    ScriptedCh375 io({expect_command(Ch375Command::CheckExist), expect_data(0xA5), reply(0x5A)});
    Ch375Transport transport(io);
    transport.check_exist(0xA5);

    // Anything more would be out of step with the chip, and the fake exists to
    // catch exactly that.
    io.write_data(0x99);

    CHECK(!io.complete());
}

TEST_CASE(a_script_left_unfinished_is_not_complete) {
    ScriptedCh375 io({expect_command(Ch375Command::CheckExist), expect_data(0xA5), reply(0x5A)});

    CHECK(!io.complete());
}

TEST_CASE(the_interrupt_line_is_readable_and_starts_idle) {
    ScriptedCh375 io({});

    CHECK(!io.int_asserted());
    io.assert_int(true);
    CHECK(io.int_asserted());
}

TEST_CASE(waiting_for_an_interrupt_gives_up_at_the_deadline) {
    ScriptedCh375 io({});
    Ch375Transport transport(io);

    CHECK(!transport.wait_for_interrupt(io.now_us() + 1000));
    CHECK(io.elapsed_us() >= 1000u);
}

TEST_CASE(waiting_for_an_interrupt_already_asserted_returns_at_once) {
    ScriptedCh375 io({});
    io.assert_int(true);
    Ch375Transport transport(io);

    CHECK(transport.wait_for_interrupt(io.now_us() + 1000));
    CHECK_EQ(io.elapsed_us(), 0u);
}

TEST_CASE(a_deadline_that_has_already_passed_does_not_wait_at_all) {
    // The caller computes deadlines from a clock that keeps running. One that
    // is already behind must not be read as an enormous wait, which is what
    // unsigned arithmetic does to a naive comparison.
    ScriptedCh375 io({});
    io.advance(5000);
    Ch375Transport transport(io);

    CHECK(!transport.wait_for_interrupt(io.now_us() - 1000));
}

// ------------------------------------------------------------- port speed

TEST_CASE(raising_the_port_speed_sends_the_divisor_the_datasheet_names) {
    ScriptedCh375 port({expect_command(Ch375Command::SetBaudRate), expect_data(0x03),
                        expect_data(0xCC), reply(0x51)});
    Ch375Transport transport(port);

    CHECK(transport.set_baud_rate(0x03, 0xCC, 115200));
    CHECK(port.complete());
}

TEST_CASE(the_port_switches_before_the_answer_is_read) {
    ScriptedCh375 port({expect_command(Ch375Command::SetBaudRate), expect_data(0x03),
                        expect_data(0xCC), reply(0x51)});
    Ch375Transport transport(port);

    transport.set_baud_rate(0x03, 0xCC, 115200);

    // The chip answers at the new rate, not the old one. A port still set to
    // 9600 reads that answer as noise, and then reads every byte after it as
    // noise too.
    CHECK_EQ(port.baud(), 115200u);
    CHECK_EQ(port.baud_changes(), 1);
}

TEST_CASE(a_port_that_cannot_change_speed_says_so) {
    ScriptedCh375 port({expect_command(Ch375Command::SetBaudRate), expect_data(0x03),
                        expect_data(0xCC)});
    Ch375Transport transport(port);
    port.refuse_baud_changes();

    // Reporting success without changing anything leaves the chip talking at a
    // speed nothing here is listening at - silence that looks like a dead chip.
    CHECK(!transport.set_baud_rate(0x03, 0xCC, 115200));
}

TEST_CASE(a_chip_that_refuses_the_new_speed_is_not_treated_as_agreeing) {
    ScriptedCh375 port({expect_command(Ch375Command::SetBaudRate), expect_data(0x03),
                        expect_data(0xCC), reply(0x00)});
    Ch375Transport transport(port);

    CHECK(!transport.set_baud_rate(0x03, 0xCC, 115200));
}


// ------------------------------------------------------- the rate ladder

TEST_CASE(a_rate_that_answers_twice_is_kept) {
    ScriptedCh375 port({expect_command(Ch375Command::SetBaudRate), expect_data(0x03),
                        expect_data(0xCC), reply(0x51),
                        expect_command(Ch375Command::CheckExist), expect_data(0xA5), reply(0x5A),
                        expect_command(Ch375Command::CheckExist), expect_data(0x5A), reply(0xA5)});
    Ch375Transport transport(port);
    const BaudOption rung{0x03, 0xCC, 115200};

    CHECK_EQ(transport.try_speed(rung, 9600), 115200u);
    CHECK(port.complete());
}

TEST_CASE(one_good_answer_is_not_enough_to_keep_a_rate) {
    // A marginal rate answers sometimes. Accepting it on one byte is how a
    // link that half works gets chosen over one that works.
    ScriptedCh375 port({expect_command(Ch375Command::SetBaudRate), expect_data(0x03),
                        expect_data(0xCC), reply(0x51),
                        expect_command(Ch375Command::CheckExist), expect_data(0xA5), reply(0x5A),
                        expect_command(Ch375Command::CheckExist), expect_data(0x5A), reply(0x00),
                        // Back down, and it is there after all.
                        expect_command(Ch375Command::CheckExist), expect_data(0xA5), reply(0x5A)});
    Ch375Transport transport(port);
    const BaudOption rung{0x03, 0xCC, 115200};

    CHECK_EQ(transport.try_speed(rung, 9600), 9600u);
    CHECK_EQ(port.baud(), 9600u);
    CHECK(port.complete());
}

TEST_CASE(a_chip_that_never_moved_is_not_reset_for_it) {
    // The command did not take, which is the ordinary failure. The chip is
    // exactly where it was and there is nothing to undo - and a reset sent to
    // find that out is the one thing that could break it.
    ScriptedCh375 port({expect_command(Ch375Command::SetBaudRate), expect_data(0x03),
                        expect_data(0xCC),
                        // Back at the rate it started at, and there it is.
                        expect_command(Ch375Command::CheckExist), expect_data(0xA5), reply(0x5A)});
    Ch375Transport transport(port);
    const BaudOption rung{0x03, 0xCC, 115200};

    CHECK_EQ(transport.try_speed(rung, 9600), 9600u);
    CHECK(!port.saw_reset_at(115200));
    CHECK(!port.saw_reset_at(9600));
    CHECK(port.complete());
}

TEST_CASE(a_chip_that_did_move_is_reset_where_it_can_hear_it) {
    // Silent at the old rate and answering at the new one: it really did move,
    // and this is the only case where a reset is both needed and safe.
    ScriptedCh375 port({expect_command(Ch375Command::SetBaudRate), expect_data(0x03),
                        expect_data(0xCC), reply(0x51),
                        expect_command(Ch375Command::CheckExist), expect_data(0xA5), reply(0x5A),
                        expect_command(Ch375Command::CheckExist), expect_data(0x5A), reply(0x00),
                        // Nothing at the old rate.
                        expect_command(Ch375Command::CheckExist), expect_data(0xA5),
                        // But it is up there.
                        expect_command(Ch375Command::CheckExist), expect_data(0xA5), reply(0x5A),
                        expect_data(0x00), expect_data(0x00), expect_data(0x00), expect_data(0x00),
                        expect_command(Ch375Command::ResetAll)});
    Ch375Transport transport(port);
    const BaudOption rung{0x03, 0xCC, 115200};

    transport.try_speed(rung, 9600);

    CHECK(port.saw_reset_at(115200));
    CHECK_EQ(port.baud(), 9600u);
}

TEST_CASE(a_chip_silent_at_both_rates_is_written_to_no_further) {
    // Nothing answers anywhere. Whatever is wrong, sending more commands into
    // it cannot help and a half-heard one can leave it worse.
    ScriptedCh375 port({expect_command(Ch375Command::SetBaudRate), expect_data(0x03),
                        expect_data(0xCC),
                        expect_command(Ch375Command::CheckExist), expect_data(0xA5),
                        expect_command(Ch375Command::CheckExist), expect_data(0xA5)});
    Ch375Transport transport(port);
    const BaudOption rung{0x03, 0xCC, 115200};

    CHECK_EQ(transport.try_speed(rung, 9600), 9600u);
    CHECK(!port.saw_reset_at(115200));
    CHECK(port.complete());
}

TEST_CASE(the_ladder_ends_at_a_rate_worth_having) {
    // The bottom rung still has to be fast enough to matter: at 9600 one
    // mouse report costs seventeen milliseconds and a moving hand produces
    // one every eight.
    CHECK(duo_input::u1::ch375::kBaudLadderSize > 0u);
    const BaudOption& last =
        duo_input::u1::ch375::kBaudLadder[duo_input::u1::ch375::kBaudLadderSize - 1];
    CHECK(last.baud >= 20000u);
}

// --------------------------------------------------- finding a lost chip

TEST_CASE(a_chip_at_the_default_rate_is_found_without_touching_anything) {
    ScriptedCh375 port({expect_command(Ch375Command::CheckExist), expect_data(0xA5), reply(0x5A),
                        expect_command(Ch375Command::CheckExist), expect_data(0x5A), reply(0xA5)});
    Ch375Transport transport(port);

    CHECK(transport.find_chip(9600));
    CHECK_EQ(port.baud(), 9600u);
    CHECK(!port.saw_reset_at(9600));
    CHECK(port.complete());
}

TEST_CASE(a_chip_stranded_at_a_raised_rate_is_found_and_brought_back) {
    // The whole point. A chip left at a rate this side abandoned answers
    // nothing at the default, and today the only cure is somebody walking to
    // the board to pull its power.
    ScriptedCh375 port({// Nothing at the rate it should be at.
                        expect_command(Ch375Command::CheckExist), expect_data(0xA5),
                        // Satisfy whatever it half-heard before moving on.
                        expect_data(0x00), expect_data(0x00), expect_data(0x00), expect_data(0x00),
                        // There it is.
                        expect_command(Ch375Command::CheckExist), expect_data(0xA5), reply(0x5A),
                        expect_command(Ch375Command::CheckExist), expect_data(0x5A), reply(0xA5),
                        // Reset it where it can hear, then come home.
                        expect_data(0x00), expect_data(0x00), expect_data(0x00), expect_data(0x00),
                        expect_command(Ch375Command::ResetAll)});
    Ch375Transport transport(port);

    CHECK(transport.find_chip(9600));
    CHECK(port.saw_reset_at(115200));
    CHECK_EQ(port.baud(), 9600u);
}

TEST_CASE(a_chip_that_answers_nowhere_leaves_the_port_where_it_belongs) {
    ScriptedCh375 port({});
    port.allow_unscripted();
    Ch375Transport transport(port);

    CHECK(!transport.find_chip(9600));
    CHECK_EQ(port.baud(), 9600u);
}

// ------------------------------------------------ waking a wedged chip

TEST_CASE(a_wedged_chip_is_flushed_with_more_than_any_command_can_want) {
    ScriptedCh375 port({});
    port.allow_unscripted();
    Ch375Transport transport(port);

    transport.flush_command_state();

    // While U1 is being reflashed its pins go high impedance and the chip's
    // receive line floats. Noise on a floating line is start bits, and the
    // chip reads bytes out of it - positionally, so it ends up part way
    // through a command, waiting for parameters that never come. Every
    // command sent afterwards is swallowed as one of them, including the
    // reset meant to fix it.
    //
    // Four filler bytes is what an ordinary reset sends, and it is enough for
    // any command this firmware issues. It is not enough for a chip that has
    // read noise: nobody knows what it thinks it is waiting for.
    CHECK(port.data_bytes_written() >= 64);
}

TEST_CASE(flushing_writes_only_data_and_never_a_command) {
    ScriptedCh375 port({});
    port.allow_unscripted();
    Ch375Transport transport(port);

    transport.flush_command_state();

    // A command byte here would be read as one of the parameters being waited
    // for, which is the very thing being cleared.
    CHECK_EQ(port.commands_written(), 0);
}

// ------------------------------------- abandoning a read part way through

TEST_CASE(a_block_with_an_impossible_length_leaves_the_port_in_step) {
    // The command has already been sent, so the chip is going to send those
    // bytes whatever this side decides about the length. Walking away from
    // them does not make them not arrive - it makes them arrive later, as the
    // answers to whatever is asked next, for ever.
    ScriptedCh375 port({expect_command(Ch375Command::ReadUsbData0), reply(0xFF),
                        reply(0x11), reply(0x22), reply(0x33),
                        expect_command(Ch375Command::CheckExist), expect_data(0xA5),
                        reply(0x5A)});
    Ch375Transport transport(port);

    std::uint8_t buffer[64] = {};
    std::size_t size = 0;
    CHECK(!transport.read_block(buffer, sizeof(buffer), size));

    // The next command must get its own answer, not the tail of the last one.
    CHECK(transport.check_exist(0xA5));
}

TEST_CASE(a_block_that_stops_half_way_leaves_the_port_in_step) {
    // Four bytes promised, one delivered before the reply timed out. The other
    // three are still coming.
    ScriptedCh375 port({expect_command(Ch375Command::ReadUsbData0), reply(0x04),
                        reply(0xAA),
                        // Late, but they arrive.
                        reply(0xBB), reply(0xCC), reply(0xDD),
                        expect_command(Ch375Command::CheckExist), expect_data(0xA5),
                        reply(0x5A)});
    Ch375Transport transport(port);

    std::uint8_t buffer[2] = {};
    std::size_t size = 0;
    CHECK(!transport.read_block(buffer, sizeof(buffer), size));

    CHECK(transport.check_exist(0xA5));
}

TEST_CASE(a_good_block_is_not_drained_of_anything) {
    ScriptedCh375 port({expect_command(Ch375Command::ReadUsbData0), reply(0x02),
                        reply(0xAA), reply(0xBB),
                        expect_command(Ch375Command::CheckExist), expect_data(0xA5),
                        reply(0x5A)});
    Ch375Transport transport(port);

    std::uint8_t buffer[64] = {};
    std::size_t size = 0;
    CHECK(transport.read_block(buffer, sizeof(buffer), size));
    CHECK_EQ(size, 2u);
    CHECK(transport.check_exist(0xA5));
    CHECK(port.complete());
}

// -------------------------------------------- looking in the wrong place

TEST_CASE(a_reply_that_only_reads_at_another_rate_names_that_rate) {
    // A silent chip and a receiver sampling at the wrong rate are the same
    // thing from outside: a channel that answers nothing. They want completely
    // different repairs - one is a wire or a module, the other is this side's
    // own timing - and nothing until now could tell them apart.
    ScriptedCh375 port({expect_command(Ch375Command::CheckExist), expect_data(0xA5),
                        expect_command(Ch375Command::CheckExist), expect_data(0xA5),
                        reply(0x5A)});
    Ch375Transport transport(port);
    const unsigned rates[] = {9600, 10400};

    CHECK_EQ(transport.sweep_rx(rates, 2, 9600), 10400u);
}

TEST_CASE(a_chip_that_answers_at_no_sampling_rate_names_none) {
    ScriptedCh375 port({});
    port.allow_unscripted();
    Ch375Transport transport(port);
    const unsigned rates[] = {9600, 10400, 8800};

    // Nothing anywhere. The receiver is not the problem, so the answer is
    // zero rather than a rate somebody might act on.
    CHECK_EQ(transport.sweep_rx(rates, 3, 9600), 0u);
}

TEST_CASE(a_sweep_leaves_the_receiver_where_it_started) {
    ScriptedCh375 port({});
    port.allow_unscripted();
    Ch375Transport transport(port);
    const unsigned rates[] = {10400, 8800};

    transport.sweep_rx(rates, 2, 9600);

    CHECK_EQ(port.rx_baud(), 9600u);
}
