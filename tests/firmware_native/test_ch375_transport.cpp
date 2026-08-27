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
using duo_input::u1::ch375::Ch375Transport;
using duo_input::u1::ch375::CommandStatus;
using duo_input::u1::ch375::InterruptStatus;
using duo_input::u1::ch375::UsbMode;
using duo_input::u1::ch375::testing::expect_command;
using duo_input::u1::ch375::testing::expect_data;
using duo_input::u1::ch375::testing::reply;
using duo_input::u1::ch375::testing::ScriptedCh375;

// -------------------------------------------------------------- check_exist

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
