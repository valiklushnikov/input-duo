#pragma once

// The CH375's serial port, built out of PIO.
//
// The plan called this a UART, and it cannot be one: the chip's frame carries
// nine data bits, the ninth marking a command from a data byte (DS1 6.2.2),
// and an RP2040's hardware UART does at most eight. Two state machines per
// chip - one sending, one receiving - do what the peripheral cannot.
//
// Two of these run side by side, one per CH375, and they share nothing but the
// PIO block their programs are loaded into. Four state machines of the eight
// available; the block is chosen by the caller so the other one stays free.
//
// The interrupt line is an ordinary input. It is active low (DS1 section 4),
// so idle is high, and it is only ever read - the chip is asked for its status
// rather than being allowed to interrupt this firmware, because reading that
// status is what clears the request and there is exactly one right moment for
// it.

#include <cstdint>

#include "ch375/transport.hpp"
#include "hardware/pio.h"

namespace duo_input::u1::ch375 {

/// How long the chip takes to change rate, and how long a frame's tail needs
/// to finish leaving before the rate moves underneath it.
inline constexpr std::uint32_t kBaudChangeUs = 2000;
inline constexpr std::uint32_t kFrameTailUs = 1500;

/// Load both programs into a PIO block. Call once before any port is opened.
///
/// Returns false when the block has no room, which would mean something else
/// has claimed it - worth knowing rather than silently running one channel.
bool load_ch375_programs(PIO pio);

class PioCh375Transport final : public ICh375Transport {
public:
    /// Claim two state machines and drive these pins with them.
    ///
    /// Returns false if no state machine is free.
    bool begin(PIO pio, unsigned tx_pin, unsigned rx_pin, unsigned int_pin,
               unsigned baud = kCh375DefaultBaud);

    /// Change the line rate.
    ///
    /// The chip answers the command that changes its own rate *at the new
    /// rate* (DS1 5.2), so this must be called immediately after sending it,
    /// and anything still in flight is lost. It also needs about a millisecond
    /// to make the change, during which it says nothing at either rate.
    bool set_baud(unsigned baud) override;

    /// What rate this side is set to. Reported so that a port and a chip
    /// that have drifted apart can be seen rather than guessed at.
    unsigned baud() const { return baud_; }

    /// Change only the receiver rate while transmission remains at 9600.
    ///
    /// Bring-up uses this to ask the same known question at the documented
    /// rate and sample the answer across nearby rates. It distinguishes a
    /// timing mismatch from level-shifter corruption without moving a wire.
    bool set_rx_baud(unsigned baud) override;

    // --- the port ----------------------------------------------------------

    void write_command(std::uint8_t command) override;
    void write_data(std::uint8_t value) override;
    bool read_data(std::uint8_t& value) override;
    bool int_asserted() const override;
    std::uint32_t now_us() const override;

    /// Frames whose stop bit was not where it should have been.
    ///
    /// These are dropped rather than delivered. A byte this port could not
    /// read must not reach the caller looking like one it could: a wrong
    /// command to a USB host controller is acted on exactly like a right one.
    std::uint32_t framing_errors() const;

    /// Take one whole nine-bit frame, ninth bit and all.
    ///
    /// read_data throws that bit away, which is right for a driver - the chip
    /// never sets it on the way back - and wrong for a bring-up, where it is
    /// the difference between "the chip answered" and "something is on the
    /// wire that this port does not understand".
    bool read_word(std::uint16_t& word);

    /// Throw away anything waiting to be read.
    ///
    /// Used before asking a question, so the answer cannot be confused with a
    /// leftover from the last one.
    void drain();

private:
    PIO pio_ = nullptr;
    unsigned tx_sm_ = 0;
    unsigned rx_sm_ = 0;
    unsigned int_pin_ = 0;
    unsigned baud_ = kCh375DefaultBaud;
    bool started_ = false;
};

}  // namespace duo_input::u1::ch375
