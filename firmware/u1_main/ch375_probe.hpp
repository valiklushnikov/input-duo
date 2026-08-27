#pragma once

// Bring-up only: does U1 actually reach the CH375s?
//
// Before any of the host-controller logic is worth running, one question has
// to be answered on its own: are these two chips wired up correctly and
// powered. The chip has a command for exactly that - send it any byte and it
// answers with the bitwise inverse (DS1 section 5.5) - and nothing else needs
// to work for that to succeed.
//
// The serial lines are driven by hand here rather than through a real port.
// CH375 talks 9600 bps after reset and its frame is nine data bits, the ninth
// marking command from data (DS1 6.2.2). Bit-banging that costs nothing at
// 104 µs per bit and needs no PIO program to be written and debugged first, so
// a soldering mistake can be found before any of that exists.
//
// This is a diagnostic, not a driver. It is compiled only under
// DUO_CH375_PROBE and runs once, before anything else claims the pins.

#include <cstdint>

namespace duo_input::u1 {

struct Ch375ProbeResult {
    /// The receive line at rest. A serial line idles high; low means either
    /// something is holding it down or the level shifter is unpowered.
    bool rx_idle_high = false;

    /// The receive line follows a pull-up and then a pull-down, so nothing is
    /// driving it. Almost always an unconnected wire or an unpowered chip.
    bool rx_floating = false;

    /// The interrupt line at rest. It is active low, so high is idle.
    bool int_high = false;

    /// The transmit line at rest, read before anything drives it.
    ///
    /// This one says something the others cannot. It ends at the CH375's RXD,
    /// which is an input with nothing but a weak pull-up behind it, so no chip
    /// is driving this wire from either end - only the level shifter's own
    /// 10 kOhm pull-up to its A-side supply. High means that supply is there;
    /// low means the same resistor is pulling to nothing, and the shifter is
    /// unpowered or disabled. Either way the answer is about the shifter, not
    /// about the CH375.
    bool tx_idle_high = false;
    bool tx_floating = false;

    /// How hard each line is held down, measured by fighting it.
    ///
    /// The weakest output this chip has is 2 mA, which walks over a 10 kOhm
    /// pull-down without noticing - a third of a milliamp - but loses to a
    /// short circuit and to a real driver. So a line that comes up when pushed
    /// is being held by a resistor, and one that does not is being held by
    /// copper or by something driving it on purpose. The two look identical
    /// from a distance and want opposite repairs.
    bool tx_wins_when_pushed = false;
    bool rx_wins_when_pushed = false;

    /// The receive line once every test has let go of it.
    ///
    /// Compared against rx_idle_high, which was read before any of them. The
    /// two disagreeing means the line holds whatever it was last pushed to,
    /// and that nothing at the far end is deciding its level.
    bool rx_settles_high = false;

    /// The shortest pulse seen in a reply, in microseconds.
    ///
    /// One bit lasts exactly that long, so this is the line's real speed
    /// measured rather than assumed: about 104 us is 9600 bps, 52 us is 19200,
    /// 208 us is 4800. Guessing this wrong turns a working chip into one that
    /// answers plausible nonsense, which is indistinguishable from a broken
    /// one until it is measured.
    std::uint16_t shortest_pulse_us = 0;

    /// How many edges the reply contained. A nine-bit frame has at most ten.
    std::uint16_t edges_seen = 0;

    /// When the line changed level, in microseconds from the end of the send.
    ///
    /// Captured rather than decoded. Every attempt to read this reply on the
    /// spot has had to assume a bit time and a starting phase, and both
    /// assumptions have been wrong in ways that produce a plausible byte -
    /// which is worse than no byte, because it looks like an answer. The edges
    /// are what actually happened; the meaning can be worked out later, by
    /// something that can be looked at.
    static constexpr std::size_t kMaxEdges = 12;
    std::uint16_t edge_us[kMaxEdges] = {};
    std::uint8_t edge_count = 0;
    /// The level before the first edge, so the trace can be reconstructed.
    bool level_before_first_edge = true;

    /// One entry per candidate bit time, in the order kSweepBitUs lists them.
    ///
    /// 9600 bps is what the datasheet says a CH375 uses after reset, and that
    /// has been assumed here from the start without ever being checked. If the
    /// module's crystal is not the 12 MHz the chip expects, every rate scales
    /// with it and nothing will ever answer at the rate the book gives.
    static constexpr std::size_t kSweepCount = 5;
    std::uint8_t sweep_reply[kSweepCount] = {};
    std::uint8_t sweep_edges[kSweepCount] = {};

    /// What came back from CHECK_EXIST, and whether it was the right answer.
    std::uint8_t check_exist_reply = 0;
    bool check_exist_answered = false;
    bool check_exist_ok = false;

    /// What came back from GET_IC_VER. Bit 7 is set and bits 5-0 are the
    /// version, so a healthy CH375B answers something like 0xB7.
    std::uint8_t ic_version = 0;
    bool ic_version_answered = false;

    /// What the chip said when asked to enter USB host mode. 0x51 is success.
    std::uint8_t host_mode_reply = 0;
    bool host_mode_ok = false;

    /// What TEST_CONNECT reported: 0x15 means a device is attached, 0x16 that
    /// none is. This is the chip's own USB side answering, one layer deeper
    /// than the serial port.
    std::uint8_t connect_reply = 0;
    bool connect_answered = false;
};

/// Probe one CH375 over its three lines.
///
/// ``tx_pin`` is what U1 drives, ``rx_pin`` what it listens to, ``int_pin``
/// the chip's interrupt output. All three are left as plain inputs afterwards.
Ch375ProbeResult probe_ch375(unsigned tx_pin, unsigned rx_pin, unsigned int_pin);

/// Hold both TXD lines high, so a CH375 reset while this runs picks serial.
///
/// DS1 section 6.2: the level on TXD when the chip comes out of reset decides
/// whether it speaks serial or parallel, and it keeps that choice until the
/// next reset. A module built for the parallel port holds that pin down, so
/// every power-on locks the chip away from us.
///
/// This drives the line the other way and keeps driving it, which gives whoever
/// is at the bench a window to power-cycle the controllers into serial mode.
void hold_serial_mode_selected(std::uint32_t duration_ms);

/// Drive both TXD lines high and leave them that way.
///
/// Non-blocking counterpart to the above, for firmware that has a USB port to
/// service while it waits.
void hold_serial_mode_selected();

/// The pins as the board is built. See docs/hardware/ch375-wiring.md.
inline constexpr unsigned kPinKeyboardTx = 0;
inline constexpr unsigned kPinKeyboardRx = 1;
inline constexpr unsigned kPinKeyboardInt = 2;
inline constexpr unsigned kPinMouseTx = 4;
inline constexpr unsigned kPinMouseRx = 5;
inline constexpr unsigned kPinMouseInt = 6;

}  // namespace duo_input::u1
