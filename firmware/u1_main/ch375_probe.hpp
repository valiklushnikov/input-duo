#pragma once

// Bring-up only: does U1 reach the CH375s, and do they answer?
//
// Four questions, in order, each worthless until the one before it passes:
// does the chip answer at all, does it know its own version, will it enter USB
// host mode, and does it see a device. Stopping at the first failure is the
// point - a chip that cannot answer CHECK_EXIST has nothing useful to say
// about anything else, and asking anyway produces answers that look real.
//
// This runs through the same PIO port and the same command layer the firmware
// uses, so a pass here means those work rather than that a diagnostic does.
// The hand-timed version this replaces could send but never reliably receive,
// and an evening went into reading its own ringing as replies.

#include <cstdint>

#include "ch375/pio_transport.hpp"
#include "ch375/transport.hpp"

namespace duo_input::u1 {

struct Ch375ProbeResult {
    /// The chip answered the one command that needs nothing configured first:
    /// send a byte, get its bitwise inverse back (DS1 5.5).
    bool answered = false;
    bool check_exist_ok = false;
    std::uint8_t check_exist_reply = 0;

    /// Bit 7 set, bits 5-0 the version. A healthy CH375B says something like
    /// 0xB7. A second, independent answer, so one lucky reply cannot pass.
    bool ic_version_answered = false;
    std::uint8_t ic_version = 0;

    /// 0x51 means the chip accepted USB host mode.
    bool host_mode_ok = false;
    std::uint8_t host_mode_reply = 0;

    /// 0x15 a device is attached, 0x16 none is. Either means the chip's own
    /// USB side understood the question, which is what is being tested.
    bool connect_answered = false;
    std::uint8_t connect_reply = 0;

    /// Frames whose stop bit was in the wrong place. Any at all means the line
    /// is being read at the wrong rate, or is too noisy to trust.
    std::uint32_t framing_errors = 0;

    /// The chip's interrupt line at the end, active low.
    bool int_asserted = false;

    /// Whatever came back after CHECK_EXIST, whole frames, uninterpreted.
    ///
    /// Every attempt to decide on the spot what a reply meant has been wrong
    /// in a way that produced a believable byte. What the chip actually put on
    /// the wire is a fact; what it meant can be worked out afterwards.
    static constexpr std::size_t kRawWords = 4;
    std::uint16_t raw[kRawWords] = {};
    std::uint8_t raw_count = 0;

    /// Frames that arrived while nothing at all was being sent.
    ///
    /// A serial line at rest is silent. Anything here means the wire carries
    /// traffic of its own - a chip talking unbidden, or noise being read as
    /// frames - and until it is zero, no reply can be trusted to be a reply.
    std::uint8_t quiet_frames = 0;
    /// The first of them, so it can be recognised.
    std::uint16_t quiet_first = 0;
};

/// Listen on a port for a second without ever transmitting.
///
/// Separates a line that is noisy on its own from one this firmware disturbs.
/// Everything measured so far has been measured after U1 sent something, so
/// U1's own switching has never been ruled out as the source.
///
/// A serial line at rest is silent, so the honest expectation is zero.
std::uint16_t listen_without_sending(ch375::PioCh375Transport& port, std::uint32_t for_ms,
                                     std::uint16_t& bad_frames);

/// Ask one CH375 the four questions.
Ch375ProbeResult probe_ch375(ch375::PioCh375Transport& port, ch375::Ch375Transport& commands);

/// The pins as the board is built. See docs/hardware/ch375-wiring.md.
inline constexpr unsigned kPinKeyboardTx = 0;
inline constexpr unsigned kPinKeyboardRx = 1;
inline constexpr unsigned kPinKeyboardInt = 2;
inline constexpr unsigned kPinMouseTx = 4;
inline constexpr unsigned kPinMouseRx = 5;
inline constexpr unsigned kPinMouseInt = 6;

}  // namespace duo_input::u1
