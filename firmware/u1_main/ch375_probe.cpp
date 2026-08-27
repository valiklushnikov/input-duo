#include "ch375_probe.hpp"

#include "ch375/commands.hpp"
#include "pico/stdlib.h"

namespace duo_input::u1 {

using ch375::Ch375Command;
using ch375::CommandStatus;
using ch375::InterruptStatus;
using ch375::UsbMode;

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

Ch375ProbeResult probe_ch375(ch375::PioCh375Transport& port, ch375::Ch375Transport& commands) {
    Ch375ProbeResult result;

    // Anything already waiting belongs to a question nobody asked. Reading it
    // as this answer is how a stale byte becomes a confident wrong result.
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
    port.write_command(static_cast<std::uint8_t>(Ch375Command::CheckExist));
    port.write_data(0x57);
    sleep_ms(30);
    while (result.raw_count < Ch375ProbeResult::kRawWords &&
           port.read_word(result.raw[result.raw_count])) {
        ++result.raw_count;
    }

    result.answered = result.raw_count > 0;
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
    result.host_mode_ok = commands.set_usb_mode(UsbMode::HostWithSof);
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

}  // namespace duo_input::u1
