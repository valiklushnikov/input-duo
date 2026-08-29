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
using duo_input::u1::ch375::kDefaultReplyTimeoutUs;
using duo_input::u1::ch375::ReplyProgress;
using duo_input::u1::ch375::SearchProgress;
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
    CHECK(io.elapsed_us() >= kDefaultReplyTimeoutUs);
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

    transport.begin_set_usb_mode(UsbMode::HostWithSof);

    CHECK_EQ(static_cast<int>(transport.poll_command_status()),
             static_cast<int>(ReplyProgress::Answered));
    CHECK(transport.command_status_succeeded());
    CHECK(io.complete());
}

TEST_CASE(a_refused_usb_mode_is_a_failure_not_a_silence) {
    ScriptedCh375 io({expect_command(Ch375Command::SetUsbMode),
                      expect_data(static_cast<std::uint8_t>(UsbMode::HostReset)),
                      reply(static_cast<std::uint8_t>(CommandStatus::Abort))});
    Ch375Transport transport(io);

    transport.begin_set_usb_mode(UsbMode::HostReset);

    // It answered, and what it answered was a refusal. The two are different
    // facts and only one of them can be acted on.
    CHECK_EQ(static_cast<int>(transport.poll_command_status()),
             static_cast<int>(ReplyProgress::Answered));
    CHECK(!transport.command_status_succeeded());
    CHECK_EQ(transport.last_status_reply(), static_cast<std::uint8_t>(CommandStatus::Abort));
    CHECK(transport.last_status_answered());
    CHECK(io.complete());
}

TEST_CASE(a_status_byte_that_is_neither_success_nor_abort_is_a_failure) {
    // The chip is documented to answer 51H or 5FH here. Treating anything else
    // as success would accept a desynchronised port as a working one.
    ScriptedCh375 io({expect_command(Ch375Command::SetUsbMode),
                      expect_data(static_cast<std::uint8_t>(UsbMode::HostWithSof)), reply(0x00)});
    Ch375Transport transport(io);

    transport.begin_set_usb_mode(UsbMode::HostWithSof);

    CHECK_EQ(static_cast<int>(transport.poll_command_status()),
             static_cast<int>(ReplyProgress::Answered));
    CHECK(!transport.command_status_succeeded());
}

TEST_CASE(a_mode_command_that_is_not_answered_is_never_read_as_taken) {
    // The whole reason this became a deferred question: the chip most likely
    // to go unanswered is the one being brought back from a fault, and the
    // blocking form spent a whole reply timeout finding that out. Silence must
    // not read as agreement - a chip that never heard the mode is still in
    // whatever mode it was in.
    ScriptedCh375 io({expect_command(Ch375Command::SetUsbMode),
                      expect_data(static_cast<std::uint8_t>(UsbMode::HostNoSof))});
    Ch375Transport transport(io);

    transport.begin_set_usb_mode(UsbMode::HostNoSof);

    // Nothing has arrived and the deadline has not passed, so the caller is
    // told to come back - it is not kept here.
    CHECK_EQ(static_cast<int>(transport.poll_command_status()),
             static_cast<int>(ReplyProgress::Waiting));
    CHECK(io.elapsed_us() < kDefaultReplyTimeoutUs);

    ReplyProgress progress = ReplyProgress::Waiting;
    for (int pass = 0; pass < 100000 && progress == ReplyProgress::Waiting; ++pass) {
        progress = transport.poll_command_status();
    }
    CHECK_EQ(static_cast<int>(progress), static_cast<int>(ReplyProgress::TimedOut));
    CHECK(!transport.command_status_succeeded());
    CHECK(!transport.last_status_answered());
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

TEST_CASE(the_chips_acknowledgement_is_read_at_the_rate_it_is_sent_at) {
    // The command that moves the chip is answered by a chip that has already
    // moved: 51H arrives within microseconds of the last stop bit, at the new
    // rate. A receiver still set to the old one cannot read it, so the change
    // reports failure on a chip that took it - and everything sent afterwards
    // goes out at a rate it is no longer listening at, where half-heard bytes
    // are parsed as opcodes.
    ScriptedCh375 port({expect_command(Ch375Command::SetBaudRate), expect_data(0x03),
                        expect_data(0xCC), reply(0x51),
                        expect_command(Ch375Command::CheckExist), expect_data(0xA5), reply(0x5A),
                        expect_command(Ch375Command::CheckExist), expect_data(0x5A), reply(0xA5)});
    port.answers_at(115200);
    Ch375Transport transport(port);
    const BaudOption rung{0x03, 0xCC, 115200};

    CHECK_EQ(transport.try_speed(rung, 9600), 115200u);
    CHECK(port.complete());
}

TEST_CASE(the_transmitter_moves_only_once_the_answer_is_in) {
    ScriptedCh375 port({expect_command(Ch375Command::SetBaudRate), expect_data(0x03),
                        expect_data(0xCC), reply(0x51)});
    port.answers_at(115200);
    Ch375Transport transport(port);

    CHECK(transport.set_baud_rate(0x03, 0xCC, 115200));

    // Both ends end up together. The last frame of the command itself was sent
    // at the old rate and had to finish at it.
    CHECK_EQ(port.baud(), 115200u);
    CHECK_EQ(port.rx_baud(), 115200u);
}

TEST_CASE(one_good_answer_is_not_enough_to_keep_a_rate) {
    // A marginal rate answers sometimes. Accepting it on one byte is how a
    // link that half works gets chosen over one that works.
    ScriptedCh375 port({expect_command(Ch375Command::SetBaudRate), expect_data(0x03),
                        expect_data(0xCC), reply(0x51),
                        expect_command(Ch375Command::CheckExist), expect_data(0xA5), reply(0x5A),
                        expect_command(Ch375Command::CheckExist), expect_data(0x5A), reply(0x00)});
    port.answers_at(115200);
    Ch375Transport transport(port);
    const BaudOption rung{0x03, 0xCC, 115200};

    CHECK_EQ(transport.try_speed(rung, 9600), 9600u);
    CHECK_EQ(port.baud(), 9600u);
}

TEST_CASE(a_failed_rate_change_writes_nothing_to_find_out_why) {
    // Writing at a rate the chip may not be using is the one operation that
    // turns a controller which was about to come good into one that answers
    // nothing at all. Not knowing why is cheaper than that.
    ScriptedCh375 port({expect_command(Ch375Command::SetBaudRate), expect_data(0x03),
                        expect_data(0xCC)});
    Ch375Transport transport(port);
    const BaudOption rung{0x03, 0xCC, 115200};

    CHECK_EQ(transport.try_speed(rung, 9600), 9600u);
    CHECK(!port.saw_reset_at(115200));
    CHECK(!port.saw_reset_at(9600));
    CHECK(port.complete());
}

TEST_CASE(a_refused_change_puts_both_ends_back_together) {
    ScriptedCh375 port({expect_command(Ch375Command::SetBaudRate), expect_data(0x03),
                        expect_data(0xCC)});
    Ch375Transport transport(port);

    CHECK(!transport.set_baud_rate(0x03, 0xCC, 115200));

    // A receiver left at the new rate while the transmitter is at the old one
    // is a port that can talk and not listen.
    CHECK_EQ(port.baud(), 9600u);
    CHECK_EQ(port.rx_baud(), 9600u);
}

// --------------------------------------------------- finding a lost chip

namespace {

/// Drive the search the way Ch375Device does: one step per tick, never
/// waiting inside one.
///
/// The bound is not a timeout - it is an assertion that the search terminates.
/// Four rates, two probes each and a 20 ms deadline per probe, against a fake
/// whose clock moves 10 us per empty read, is comfortably inside this.
SearchProgress settle_search(Ch375Transport& transport, unsigned home) {
    transport.begin_chip_search(home);
    for (int step = 0; step < 200000; ++step) {
        const SearchProgress progress = transport.poll_chip_search();
        if (progress != SearchProgress::Waiting) {
            return progress;
        }
    }
    return SearchProgress::Waiting;
}

}  // namespace


TEST_CASE(a_chip_at_the_default_rate_is_found_without_touching_anything) {
    ScriptedCh375 port({expect_command(Ch375Command::CheckExist), expect_data(0xA5), reply(0x5A),
                        expect_command(Ch375Command::CheckExist), expect_data(0x5A), reply(0xA5)});
    Ch375Transport transport(port);

    CHECK_EQ(static_cast<int>(settle_search(transport, 9600)),
             static_cast<int>(SearchProgress::Found));
    CHECK_EQ(transport.chip_search_found_at(), 9600u);
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

    CHECK_EQ(static_cast<int>(settle_search(transport, 9600)),
             static_cast<int>(SearchProgress::Found));
    CHECK_EQ(transport.chip_search_found_at(), 115200u);
    CHECK(port.saw_reset_at(115200));
    CHECK_EQ(port.baud(), 9600u);
}

TEST_CASE(a_chip_that_answers_nowhere_leaves_the_port_where_it_belongs) {
    ScriptedCh375 port({});
    port.allow_unscripted();
    Ch375Transport transport(port);

    CHECK_EQ(static_cast<int>(settle_search(transport, 9600)),
             static_cast<int>(SearchProgress::NotFound));
    CHECK_EQ(transport.chip_search_found_at(), 0u);
    CHECK_EQ(port.baud(), 9600u);
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

// -------------------------------------- bringing a stranded chip home

TEST_CASE(a_chip_left_at_the_raised_rate_is_found_there_and_reset) {
    // When a raised link degrades, the reset meant to bring the chip home goes
    // out at the rate it can no longer hold, so it never arrives. The chip
    // stays where it was put while this side knocks on an empty door.
    //
    // The rate this code raised it to is one of the ladder's rungs, so the
    // search reaches it: the home rate first, then every rung in order.
    ScriptedCh375 port({// Nothing at the rate it should be at.
                        expect_command(Ch375Command::CheckExist), expect_data(0xA5),
                        expect_data(0x00), expect_data(0x00), expect_data(0x00), expect_data(0x00),
                        // Nor at the top rung.
                        expect_command(Ch375Command::CheckExist), expect_data(0xA5),
                        expect_data(0x00), expect_data(0x00), expect_data(0x00), expect_data(0x00),
                        // There it is, at 62500.
                        expect_command(Ch375Command::CheckExist), expect_data(0xA5), reply(0x5A),
                        expect_command(Ch375Command::CheckExist), expect_data(0x5A), reply(0xA5),
                        expect_data(0x00), expect_data(0x00), expect_data(0x00), expect_data(0x00),
                        expect_command(Ch375Command::ResetAll)});
    port.answers_at(62500);
    Ch375Transport transport(port);

    CHECK_EQ(static_cast<int>(settle_search(transport, 9600)),
             static_cast<int>(SearchProgress::Found));
    CHECK_EQ(transport.chip_search_found_at(), 62500u);
    CHECK(port.saw_reset_at(62500));
    CHECK_EQ(port.baud(), 9600u);
    CHECK(port.complete());
}
