#include "ch375_probe.hpp"

#include "ch375/commands.hpp"
#include "hardware/gpio.h"
#include "pico/stdlib.h"

namespace duo_input::u1 {
namespace {

using ch375::Ch375Command;
using ch375::InterruptStatus;

/// 9600 bps, which is where a CH375 starts after reset (DS1 5.2).
///
/// Deliberately the slowest rate the chip offers. Nothing here is in a hurry,
/// and a slow line is forgiving of a level shifter, a long wire and a hand
/// timing loop all at once - none of which is what is being tested.
constexpr std::uint32_t kBitUs = 104;

/// Half a bit, for sampling in the middle of one rather than at its edge.
constexpr std::uint32_t kHalfBitUs = 52;

/// How long to wait for the chip to begin answering.
///
/// Every command here is documented to complete in microseconds. A tenth of a
/// second is far past slow and short enough that two dead channels do not
/// noticeably delay start-up.
constexpr std::uint32_t kReplyTimeoutUs = 20000;

/// Send one frame: a start bit, nine data bits, a stop bit.
///
/// The ninth bit is what distinguishes a command from data (DS1 6.2.2), which
/// is the whole reason this cannot be an ordinary eight-bit UART.
void send_frame(unsigned tx_pin, std::uint8_t value, bool is_command,
                std::uint32_t bit_us = kBitUs) {
    gpio_put(tx_pin, 0);  // start
    busy_wait_us_32(bit_us);

    for (int bit = 0; bit < 8; ++bit) {
        gpio_put(tx_pin, (value >> bit) & 1u);
        busy_wait_us_32(bit_us);
    }

    gpio_put(tx_pin, is_command ? 1 : 0);  // the ninth bit
    busy_wait_us_32(bit_us);

    gpio_put(tx_pin, 1);  // stop, and back to idle
    busy_wait_us_32(bit_us);
    // Nothing more. The chip answers within microseconds of the stop bit, so
    // any pause taken here is a pause during which its reply begins - and the
    // start bit that is being waited for further down has already gone by.
    // That is not a slow answer, it is a missed one, and it reads as a byte
    // from the middle of the frame.
}

/// Receive one frame, or give up. Returns the low eight bits.
bool receive_frame(unsigned rx_pin, std::uint8_t& value) {
    const absolute_time_t deadline = make_timeout_time_us(kReplyTimeoutUs);
    while (gpio_get(rx_pin)) {
        if (time_reached(deadline)) {
            return false;
        }
    }
    // The falling edge has just been seen. Everything below is measured from
    // here, so the polling loop above has to be tight - a late detection
    // shifts every sample that follows it.

    // Sample in the middle of each bit rather than at its edge, so a little
    // drift either way still reads the right level.
    busy_wait_us_32(kBitUs + kHalfBitUs);

    std::uint8_t received = 0;
    for (int bit = 0; bit < 8; ++bit) {
        if (gpio_get(rx_pin)) {
            received |= static_cast<std::uint8_t>(1u << bit);
        }
        busy_wait_us_32(kBitUs);
    }
    // The ninth bit and the stop bit are read past, not read.
    busy_wait_us_32(kBitUs);

    value = received;
    return true;
}

/// Drive a pin high with the feeblest output there is, and see who wins.
///
/// Brief on purpose. If something really is driving the line the other way
/// this is a contention, and the weakest setting keeps that harmless - which
/// is also exactly what makes the answer meaningful.
bool push_and_read(unsigned pin) {
    gpio_init(pin);
    gpio_set_dir(pin, GPIO_OUT);
    gpio_set_drive_strength(pin, GPIO_DRIVE_STRENGTH_2MA);
    gpio_put(pin, 1);
    busy_wait_us_32(200);
    const bool won = gpio_get(pin);

    gpio_set_dir(pin, GPIO_IN);
    gpio_disable_pulls(pin);
    busy_wait_us_32(200);
    return won;
}

/// Bit times to try, in microseconds: 4800, 9600, 19200, 38400, 57600 bps.
constexpr std::uint32_t kSweepBitUs[Ch375ProbeResult::kSweepCount] = {208, 104, 52, 26, 17};

/// Wait until the line has been quiet and high for a while.
///
/// A reply left over from an earlier command, or an unsolicited interrupt from
/// a chip that now has a device attached, would otherwise be read as the
/// beginning of the next answer.
void wait_for_idle(unsigned rx_pin) {
    const absolute_time_t give_up = make_timeout_time_us(20000);
    std::uint32_t quiet_since = time_us_32();
    while (!time_reached(give_up)) {
        if (!gpio_get(rx_pin)) {
            quiet_since = time_us_32();
            continue;
        }
        if (time_us_32() - quiet_since >= 2000) {
            return;
        }
    }
}

/// Send a command and record every edge of whatever comes back.
void capture_reply(unsigned tx_pin, unsigned rx_pin, Ch375Command command, std::uint8_t data,
                   bool has_data, Ch375ProbeResult& result) {
    wait_for_idle(rx_pin);

    send_frame(tx_pin, static_cast<std::uint8_t>(command), true);
    if (has_data) {
        send_frame(tx_pin, data, false);
    }

    // Only a level that holds counts as an edge.
    //
    // The raw line rings for microseconds after every transition - the first
    // capture filled its whole buffer with twelve edges inside ten
    // microseconds, which is a hundredth of a single bit. Recording that is
    // recording the level shifter arguing with itself, and it crowds out the
    // frame underneath. A settling time well below one bit and well above the
    // ringing separates the two.
    constexpr std::uint32_t kSettleUs = 20;

    const std::uint32_t started = time_us_32();
    bool level = gpio_get(rx_pin);
    result.level_before_first_edge = level;
    result.edge_count = 0;

    bool candidate = level;
    std::uint32_t candidate_since = started;

    while (time_us_32() - started < 6000) {
        const bool now = gpio_get(rx_pin);
        if (now != candidate) {
            candidate = now;
            candidate_since = time_us_32();
            continue;
        }
        if (candidate == level || time_us_32() - candidate_since < kSettleUs) {
            continue;
        }
        level = candidate;
        if (result.edge_count < Ch375ProbeResult::kMaxEdges) {
            // Timed from when the level first changed, not from when it was
            // believed - otherwise every edge is reported a settle-time late.
            result.edge_us[result.edge_count] =
                static_cast<std::uint16_t>(candidate_since - started);
            ++result.edge_count;
        }
    }
}

/// Watch a reply go past and time it, without trying to decode it.
///
/// Samples as fast as the loop allows and keeps the shortest gap between two
/// edges. That gap is one bit, whatever the rate happens to be.
void measure_reply(unsigned rx_pin, std::uint16_t& shortest_us, std::uint16_t& edges) {
    const std::uint32_t started = time_us_32();
    bool level = gpio_get(rx_pin);
    std::uint32_t last_edge = started;
    std::uint32_t shortest = 0xFFFFFFFF;
    std::uint16_t count = 0;

    while (time_us_32() - started < 4000) {
        const bool now = gpio_get(rx_pin);
        if (now == level) {
            continue;
        }
        const std::uint32_t at = time_us_32();
        level = now;
        ++count;
        if (count > 1) {
            const std::uint32_t gap = at - last_edge;
            if (gap < shortest) {
                shortest = gap;
            }
        }
        last_edge = at;
    }

    edges = count;
    shortest_us = shortest == 0xFFFFFFFF ? 0 : static_cast<std::uint16_t>(shortest);
}

/// Receive one frame at a given bit time, ignoring pulses too short to be one.
bool receive_frame_at(unsigned rx_pin, std::uint8_t& value, std::uint32_t bit_us,
                      std::uint8_t& settled_edges) {
    const std::uint32_t glitch_us = bit_us / 4;
    const absolute_time_t deadline = make_timeout_time_us(kReplyTimeoutUs);

    // A start bit is a low that stays low. The line rings for microseconds
    // after every transition, and a ring has been mistaken for a start bit
    // here before - which turns silence into a plausible byte.
    while (true) {
        if (time_reached(deadline)) {
            settled_edges = 0;
            return false;
        }
        if (gpio_get(rx_pin)) {
            continue;
        }
        const std::uint32_t low_since = time_us_32();
        bool still_low = true;
        while (time_us_32() - low_since < glitch_us) {
            if (gpio_get(rx_pin)) {
                still_low = false;
                break;
            }
        }
        if (still_low) {
            busy_wait_us_32(bit_us - glitch_us + bit_us / 2);
            break;
        }
    }

    std::uint8_t received = 0;
    std::uint8_t edges = 1;
    bool previous = false;
    for (int bit = 0; bit < 8; ++bit) {
        const bool level = gpio_get(rx_pin);
        if (level != previous) {
            ++edges;
            previous = level;
        }
        if (level) {
            received |= static_cast<std::uint8_t>(1u << bit);
        }
        busy_wait_us_32(bit_us);
    }

    value = received;
    settled_edges = edges;
    return true;
}

bool ask(unsigned tx_pin, unsigned rx_pin, Ch375Command command, std::uint8_t& reply) {
    send_frame(tx_pin, static_cast<std::uint8_t>(command), true);
    return receive_frame(rx_pin, reply);
}

}  // namespace

Ch375ProbeResult probe_ch375(unsigned tx_pin, unsigned rx_pin, unsigned int_pin) {
    Ch375ProbeResult result;

    gpio_init(int_pin);
    gpio_set_dir(int_pin, GPIO_IN);
    gpio_disable_pulls(int_pin);
    gpio_init(rx_pin);
    gpio_set_dir(rx_pin, GPIO_IN);
    gpio_disable_pulls(rx_pin);

    // Every line's resting level is read first, before anything here touches
    // it. Reading it after a pull test measures how long this code's own
    // pull-down takes to fade, which is a fact about this code.
    //
    // That mistake was made here, and only on this line: the transmit side was
    // read first and the receive side last, so the two disagreed and the
    // disagreement looked like a finding.
    sleep_ms(10);
    result.rx_idle_high = gpio_get(rx_pin);
    result.int_high = gpio_get(int_pin);

    // Now the pull test, which separates a wire nobody drives from one with a
    // powered chip on the end of it.
    gpio_pull_up(rx_pin);
    sleep_ms(2);
    const bool high_when_pulled_up = gpio_get(rx_pin);
    gpio_pull_down(rx_pin);
    sleep_ms(2);
    const bool low_when_pulled_down = !gpio_get(rx_pin);
    gpio_disable_pulls(rx_pin);
    sleep_ms(10);

    result.rx_floating = high_when_pulled_up && low_when_pulled_down;

    // Read the transmit line before driving it. See the comment on
    // tx_idle_high: this is the one measurement that speaks about the level
    // shifter alone, because nothing at either end of that wire drives it.
    gpio_init(tx_pin);
    gpio_set_dir(tx_pin, GPIO_IN);
    gpio_disable_pulls(tx_pin);
    sleep_ms(2);
    result.tx_idle_high = gpio_get(tx_pin);

    gpio_pull_up(tx_pin);
    sleep_ms(2);
    const bool tx_high_pulled_up = gpio_get(tx_pin);
    gpio_pull_down(tx_pin);
    sleep_ms(2);
    const bool tx_low_pulled_down = !gpio_get(tx_pin);
    gpio_disable_pulls(tx_pin);
    result.tx_floating = tx_high_pulled_up && tx_low_pulled_down;

    result.tx_wins_when_pushed = push_and_read(tx_pin);
    result.rx_wins_when_pushed = push_and_read(rx_pin);
    sleep_ms(10);

    // Read again now that everything has been let go of. If this disagrees
    // with the reading taken before the tests, the line is holding whatever it
    // was last pushed to - which is worth knowing and easy to mistake for the
    // far end doing something.
    result.rx_settles_high = gpio_get(rx_pin);

    gpio_init(tx_pin);
    gpio_set_dir(tx_pin, GPIO_OUT);
    gpio_set_drive_strength(tx_pin, GPIO_DRIVE_STRENGTH_12MA);
    gpio_put(tx_pin, 1);  // a serial line idles high
    sleep_ms(5);

    // DS1 5.5. Any byte in, its inverse out. The datasheet's own example is
    // 57H answering A8H, so that is what is sent - a value with no symmetry
    // to hide a stuck bit behind.
    send_frame(tx_pin, static_cast<std::uint8_t>(Ch375Command::CheckExist), true);
    send_frame(tx_pin, 0x57, false);
    result.check_exist_answered = receive_frame(rx_pin, result.check_exist_reply);
    result.check_exist_ok = result.check_exist_answered && result.check_exist_reply == 0xA8;

    // DS1 5.1. Bit 7 set, bits 5-0 the version. A second, independent answer,
    // so one lucky reply cannot pass for a working port.
    result.ic_version_answered =
        ask(tx_pin, rx_pin, Ch375Command::GetIcVersion, result.ic_version);

    // And once more, recording the edges instead of interpreting them.
    sleep_ms(2);
    capture_reply(tx_pin, rx_pin, Ch375Command::CheckExist, 0x57, true, result);

    // Then ask the same question at every plausible speed. Whichever one the
    // chip is actually listening at is the one that answers; the rest get
    // silence, which is a clean signal rather than a confusing one.
    for (std::size_t index = 0; index < Ch375ProbeResult::kSweepCount; ++index) {
        const std::uint32_t bit_us = kSweepBitUs[index];
        wait_for_idle(rx_pin);
        send_frame(tx_pin, static_cast<std::uint8_t>(Ch375Command::CheckExist), true, bit_us);
        send_frame(tx_pin, 0x57, false, bit_us);

        std::uint8_t settled_edges = 0;
        std::uint8_t received = 0;
        if (receive_frame_at(rx_pin, received, bit_us, settled_edges)) {
            result.sweep_reply[index] = received;
        }
        result.sweep_edges[index] = settled_edges;
        sleep_ms(3);
    }

    // One layer deeper: put the chip into host mode and ask whether anything
    // is plugged into it. This exercises the CH375's own USB side rather than
    // just the wires to it, and needs no device to be attached - "nothing
    // here" is as good an answer as "something is", because both mean the
    // chip understood the question.
    if (result.check_exist_ok) {
        send_frame(tx_pin, static_cast<std::uint8_t>(Ch375Command::SetUsbMode), true);
        send_frame(tx_pin, static_cast<std::uint8_t>(ch375::UsbMode::HostWithSof), false);
        result.host_mode_ok = receive_frame(rx_pin, result.host_mode_reply) &&
                              result.host_mode_reply ==
                                  static_cast<std::uint8_t>(ch375::CommandStatus::Success);
        sleep_ms(10);

        if (result.host_mode_ok) {
            result.connect_answered =
                ask(tx_pin, rx_pin, Ch375Command::TestConnect, result.connect_reply);
        }
    }

    gpio_set_dir(tx_pin, GPIO_IN);
    return result;
}


void hold_serial_mode_selected() {
    const unsigned lines[2] = {kPinKeyboardRx, kPinMouseRx};
    for (unsigned pin : lines) {
        gpio_init(pin);
        gpio_set_dir(pin, GPIO_OUT);
        gpio_set_drive_strength(pin, GPIO_DRIVE_STRENGTH_12MA);
        gpio_put(pin, 1);
    }
}

void hold_serial_mode_selected(std::uint32_t duration_ms) {
    const unsigned lines[2] = {kPinKeyboardRx, kPinMouseRx};
    for (unsigned pin : lines) {
        gpio_init(pin);
        gpio_set_dir(pin, GPIO_OUT);
        gpio_set_drive_strength(pin, GPIO_DRIVE_STRENGTH_12MA);
        gpio_put(pin, 1);
    }

    sleep_ms(duration_ms);

    for (unsigned pin : lines) {
        gpio_set_dir(pin, GPIO_IN);
        gpio_disable_pulls(pin);
    }
    sleep_ms(50);
}

}  // namespace duo_input::u1
