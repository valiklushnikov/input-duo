#include "ch375_probe.hpp"

#include "ch375/commands.hpp"
#include "pico/stdlib.h"

namespace duo_input::u1 {

using ch375::Ch375Command;
using ch375::CommandStatus;
using ch375::InterruptStatus;
using ch375::ReplyProgress;
using ch375::UsbMode;

bool driven_high_against_a_pull_down(unsigned pin) {
    gpio_init(pin);
    gpio_set_dir(pin, GPIO_IN);
    gpio_pull_down(pin);
    sleep_ms(5);
    const bool held_high = gpio_get(pin);
    gpio_disable_pulls(pin);
    gpio_pull_up(pin);
    return held_high;
}

PinActivity watch_bare_pin(unsigned pin, std::uint32_t for_ms) {
    PinActivity activity;

    gpio_init(pin);
    gpio_set_dir(pin, GPIO_IN);
    gpio_pull_up(pin);
    sleep_ms(2);

    const std::uint32_t started = to_ms_since_boot(get_absolute_time());
    std::uint32_t samples = 0;
    std::uint32_t low = 0;
    std::uint32_t run = 0;
    std::uint32_t shortest = 0xFFFFFFFF;
    bool level = gpio_get(pin);

    while (to_ms_since_boot(get_absolute_time()) - started < for_ms) {
        const bool now = gpio_get(pin);
        ++samples;
        ++run;
        if (!now) {
            ++low;
        }
        if (now != level) {
            level = now;
            if (activity.transitions < 0xFFFF) {
                ++activity.transitions;
            }
            // The first run is however long the loop happened to start into
            // it, so it says nothing.
            if (activity.transitions > 1 && run < shortest) {
                shortest = run;
            }
            run = 0;
        }
    }

    if (samples != 0) {
        activity.low_percent = static_cast<std::uint8_t>((low * 100u) / samples);
        activity.samples_per_ms = samples / (for_ms == 0 ? 1 : for_ms);
    }
    activity.shortest_run_samples =
        shortest == 0xFFFFFFFF ? 0 : static_cast<std::uint16_t>(shortest > 0xFFFF ? 0xFFFF : shortest);
    return activity;
}

std::uint16_t listen_without_sending(ch375::PioCh375Transport& port, std::uint32_t for_ms,
                                     std::uint16_t& bad_frames) {
    bad_frames = 0;
    port.drain();
    (void)port.framing_errors();  // clear whatever is already standing
    const std::uint32_t started = to_ms_since_boot(get_absolute_time());
    std::uint16_t frames = 0;
    std::uint16_t word = 0;
    while (to_ms_since_boot(get_absolute_time()) - started < for_ms) {
        if (port.read_word(word) && frames < 0xFFFF) {
            ++frames;
        }
        // Sampled as we go, because the flag is sticky: asked once at the end
        // it can only ever say "at least one", which is the same answer for a
        // single glitch and for a line that is nothing but glitches.
        if (port.framing_errors() != 0 && bad_frames < 0xFFFF) {
            ++bad_frames;
        }
    }
    return frames;
}

Ch375ProbeResult probe_ch375(ch375::PioCh375Transport& port, ch375::Ch375Transport& commands,
                             Ch375ProbeMode mode) {
    Ch375ProbeResult result;

    // Start from a chip that is definitely idle. A firmware update restarts
    // the processor and leaves the controller exactly as the last run left it,
    // and one of those states is "answers nothing at all" - which reads
    // identically to a chip that is not connected.
    commands.reset_all();
    sleep_ms(60);
    port.drain();

    // Listen first, saying nothing. Whatever arrives now was not asked for.
    sleep_ms(100);
    std::uint16_t stray = 0;
    while (port.read_word(stray)) {
        if (result.quiet_frames == 0) {
            result.quiet_first = stray;
        }
        if (result.quiet_frames < 255) {
            ++result.quiet_frames;
        }
    }
    port.drain();

    // DS1 5.5. The datasheet's own example is 57H in, A8H out - a value with
    // no symmetry to hide a stuck bit behind.
    //
    // Sent by hand rather than through the command layer, and everything that
    // comes back is kept. One frame is the answer; more than one means
    // something else is talking, and none means nothing is.
    commands.start_check_exist();
    if (mode == Ch375ProbeMode::CompleteCheckExist) {
        port.write_data(0x57);
    }
    sleep_ms(30);
    while (result.raw_count < Ch375ProbeResult::kRawWords &&
           port.read_word(result.raw[result.raw_count])) {
        ++result.raw_count;
    }

    result.answered = result.raw_count > 0;
    if (mode == Ch375ProbeMode::CommandOnly) {
        result.framing_errors = port.framing_errors();
        result.int_asserted = port.int_asserted();
        return result;
    }
    if (result.answered) {
        result.check_exist_reply = static_cast<std::uint8_t>(result.raw[0] & 0xFF);
        result.check_exist_ok = result.check_exist_reply == 0xA8;
    }

    if (!result.check_exist_ok) {
        result.framing_errors = port.framing_errors();
        result.int_asserted = port.int_asserted();
        return result;
    }

    // DS1 5.1.
    port.drain();
    port.write_command(static_cast<std::uint8_t>(Ch375Command::GetIcVersion));
    sleep_ms(5);
    result.ic_version_answered = port.read_data(result.ic_version);

    // DS1 5.9. Host mode, generating frames - a device that is never sent one
    // stops answering.
    // Asked and then waited for, deliberately. The transport's status commands
    // became tick-spanning so a stalled channel cannot hold Core 1's loop, but
    // this probe runs once before that loop exists and has no tick to be
    // spanned across, so it drives the same deferred exchange to completion
    // itself. The bound is the transport's own reply deadline.
    commands.begin_set_usb_mode(UsbMode::HostWithSof);
    ReplyProgress mode_progress = ReplyProgress::Waiting;
    while (mode_progress == ReplyProgress::Waiting) {
        mode_progress = commands.poll_command_status();
    }
    result.host_mode_ok = mode_progress == ReplyProgress::Answered &&
                          commands.command_status_succeeded();
    result.host_mode_reply =
        result.host_mode_ok ? static_cast<std::uint8_t>(CommandStatus::Success) : 0;
    sleep_ms(20);

    // DS1 5.10. Asking is better than waiting to be told: the connect
    // interrupt never arrives for a device that was already plugged in when
    // the power came on.
    if (result.host_mode_ok) {
        InterruptStatus status = InterruptStatus::Disconnect;
        result.connect_answered = commands.test_connect(status);
        result.connect_reply = static_cast<std::uint8_t>(status);
    }

    result.framing_errors = port.framing_errors();
    result.int_asserted = port.int_asserted();
    return result;
}

std::size_t scan_ch375_baud(ch375::PioCh375Transport& port,
                           diagnostics::Ch375BaudObservation* out,
                           std::size_t capacity) {
    static constexpr std::uint16_t kRates[] = {
        7200, 7600, 8000, 8400, 8800, 9200, 9600,
        10000, 10400, 10800, 11200, 11600, 12000,
    };
    const std::size_t count = capacity < (sizeof(kRates) / sizeof(kRates[0]))
                                  ? capacity
                                  : (sizeof(kRates) / sizeof(kRates[0]));

    for (std::size_t index = 0; index < count; ++index) {
        diagnostics::Ch375BaudObservation& observation = out[index];
        observation = {};
        observation.baud = kRates[index];

        port.set_baud(ch375::kCh375DefaultBaud);
        port.drain();
        (void)port.framing_errors();
        sleep_ms(5);

        port.write_command(static_cast<std::uint8_t>(Ch375Command::CheckExist));
        port.write_data(0x57);
        // The two transmitted frames take more than 2 ms, so the receiver is
        // already at the candidate rate before the chip can answer.
        port.set_rx_baud(kRates[index]);
        sleep_ms(30);

        std::uint16_t word = 0;
        while (port.read_word(word)) {
            if (observation.frame_count == 0) {
                observation.first_word = word;
            }
            if (observation.frame_count < 0xFF) {
                ++observation.frame_count;
            }
        }
        observation.framing_errors = static_cast<std::uint8_t>(port.framing_errors() != 0);
    }

    port.set_baud(ch375::kCh375DefaultBaud);
    port.drain();
    return count;
}

}  // namespace duo_input::u1
