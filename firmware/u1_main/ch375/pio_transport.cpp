#include "ch375/pio_transport.hpp"

#include "ch375_serial.pio.h"
#include "hardware/clocks.h"
#include "hardware/resets.h"
#include "hardware/gpio.h"
#include "pico/stdlib.h"

namespace duo_input::u1::ch375 {
namespace {

/// Both programs spend eight cycles on every bit, so the state machine runs
/// eight times faster than the line.
constexpr unsigned kCyclesPerBit = 8;

/// Nine data bits, not eight. The whole reason this port exists.
constexpr unsigned kFrameBits = 9;

/// Where each program ended up, per PIO block. Loaded once and shared: two
/// channels run the same instructions, and the block has room for one copy.
struct LoadedPrograms {
    bool loaded = false;
    unsigned tx_offset = 0;
    unsigned rx_offset = 0;
};

LoadedPrograms g_programs[2];

std::size_t block_index(PIO pio) {
    return pio == pio0 ? 0 : 1;
}

}  // namespace

bool load_ch375_programs(PIO pio) {
    LoadedPrograms& programs = g_programs[block_index(pio)];
    if (programs.loaded) {
        return true;
    }

    // Start from a block that is definitely idle.
    //
    // Reaching this code does not mean the chip just powered on: a firmware
    // update over USB restarts the processor and leaves every peripheral
    // exactly as it was. State machines from the previous run keep executing,
    // and loading this program on top puts new instructions under their
    // program counters. They then run whatever lands there and push the
    // results into their queues.
    //
    // Read back, that is indistinguishable from a noisy wire - and it was read
    // that way for an entire evening, against a line that turned out to be
    // silent. Which is the same lesson the SPI link taught: hardware survives
    // a soft restart, and anything not explicitly reset is whatever the last
    // run left behind.
    const uint32_t block = pio == pio0 ? RESETS_RESET_PIO0_BITS : RESETS_RESET_PIO1_BITS;
    reset_block(block);
    unreset_block_wait(block);
    if (!pio_can_add_program(pio, &ch375_tx_program) ||
        !pio_can_add_program(pio, &ch375_rx_program)) {
        return false;
    }
    programs.tx_offset = pio_add_program(pio, &ch375_tx_program);
    programs.rx_offset = pio_add_program(pio, &ch375_rx_program);
    programs.loaded = true;
    return true;
}

bool PioCh375Transport::begin(PIO pio, unsigned tx_pin, unsigned rx_pin, unsigned int_pin,
                              unsigned baud) {
    if (!load_ch375_programs(pio)) {
        return false;
    }
    const LoadedPrograms& programs = g_programs[block_index(pio)];

    const int tx_sm = pio_claim_unused_sm(pio, false);
    if (tx_sm < 0) {
        return false;
    }
    const int rx_sm = pio_claim_unused_sm(pio, false);
    if (rx_sm < 0) {
        pio_sm_unclaim(pio, static_cast<uint>(tx_sm));
        return false;
    }

    pio_ = pio;
    tx_sm_ = static_cast<unsigned>(tx_sm);
    rx_sm_ = static_cast<unsigned>(rx_sm);
    int_pin_ = int_pin;
    baud_ = baud;

    // --- transmit ---------------------------------------------------------
    //
    // The line is driven high before the state machine takes it, so the pad is
    // never briefly low: to a CH375 leaving reset, a low here selects the
    // parallel interface and there is no way back without another reset.
    pio_sm_set_pins_with_mask(pio, tx_sm_, 1u << tx_pin, 1u << tx_pin);
    pio_sm_set_pindirs_with_mask(pio, tx_sm_, 1u << tx_pin, 1u << tx_pin);
    pio_gpio_init(pio, tx_pin);

    pio_sm_config tx = ch375_tx_program_get_default_config(programs.tx_offset);
    // Explicit pull, so autopull stays off.
    //
    // With both, the PULL at the top of the loop becomes a no-op whenever
    // autopull has already refilled the output register - so the state machine
    // stops waiting for the next word and can run a frame together with the
    // one after it. One mechanism or the other, never both.
    sm_config_set_out_shift(&tx, true, false, 32);
    sm_config_set_out_pins(&tx, tx_pin, 1);
    sm_config_set_sideset_pins(&tx, tx_pin);
    sm_config_set_fifo_join(&tx, PIO_FIFO_JOIN_TX);
    pio_sm_init(pio, tx_sm_, programs.tx_offset, &tx);

    // --- receive ----------------------------------------------------------
    pio_gpio_init(pio, rx_pin);
    pio_sm_set_consecutive_pindirs(pio, rx_sm_, rx_pin, 1, false);
    // The chip drives this line, but it idles high through a pull-up at each
    // end. The internal one costs nothing and keeps a disconnected wire from
    // reading as an endless stream of start bits.
    gpio_pull_up(rx_pin);

    pio_sm_config rx = ch375_rx_program_get_default_config(programs.rx_offset);
    // Explicit push, so autopush stays off - and this one was doing damage.
    //
    // With autopush at nine bits, the ninth IN already pushed the word and
    // emptied the register; the PUSH after the stop bit then queued a second,
    // empty one. Every real frame arrived followed by a zero, and those zeros
    // were counted as frames from the wire. Half of the "unasked traffic" that
    // this port was built to measure was made by this line.
    sm_config_set_in_shift(&rx, true, false, 32);
    sm_config_set_in_pins(&rx, rx_pin);
    sm_config_set_jmp_pin(&rx, rx_pin);
    sm_config_set_fifo_join(&rx, PIO_FIFO_JOIN_RX);
    pio_sm_init(pio, rx_sm_, programs.rx_offset, &rx);

    gpio_init(int_pin);
    gpio_set_dir(int_pin, GPIO_IN);
    gpio_pull_up(int_pin);

    started_ = true;
    set_baud(baud);

    pio_sm_set_enabled(pio, tx_sm_, true);
    pio_sm_set_enabled(pio, rx_sm_, true);
    return true;
}

bool PioCh375Transport::set_baud(unsigned baud) {
    if (!started_ || baud == 0) {
        return false;
    }

    // The last byte of the command has to leave at the old rate before the
    // rate moves underneath it. Changing the divider with a frame still in the
    // transmit FIFO sends the tail of that frame at the new speed, which the
    // chip reads as a framing error and answers nothing at all.
    while (!pio_sm_is_tx_fifo_empty(pio_, tx_sm_)) {
    }
    sleep_us(kFrameTailUs);

    baud_ = baud;
    const float divider =
        static_cast<float>(clock_get_hz(clk_sys)) / static_cast<float>(baud * kCyclesPerBit);

    // Both state machines are stopped and put back to their first instruction,
    // not merely handed a new divider.
    //
    // A running state machine holds a program counter part way through a
    // frame, a shift counter part way through a byte and a clock divider part
    // way through a bit. Moving the divider underneath all of that leaves the
    // receiver counting the new rate's bits from the old rate's phase - it
    // never recovers, because nothing in the program resynchronises except
    // the start bit it is no longer looking for. The channel goes deaf, and
    // stays deaf through anything done to the chip at the other end, which is
    // the one symptom that says the fault is on this side of the wire.
    pio_sm_set_enabled(pio_, tx_sm_, false);
    pio_sm_set_enabled(pio_, rx_sm_, false);

    pio_sm_set_clkdiv(pio_, tx_sm_, divider);
    pio_sm_set_clkdiv(pio_, rx_sm_, divider);

    // Whatever either was part way through belongs to the old rate and is
    // nonsense at the new one.
    pio_sm_clear_fifos(pio_, tx_sm_);
    pio_sm_clear_fifos(pio_, rx_sm_);
    pio_sm_restart(pio_, tx_sm_);
    pio_sm_restart(pio_, rx_sm_);
    pio_sm_clkdiv_restart(pio_, tx_sm_);
    pio_sm_clkdiv_restart(pio_, rx_sm_);
    const LoadedPrograms& loaded = g_programs[block_index(pio_)];
    pio_sm_exec(pio_, tx_sm_, pio_encode_jmp(loaded.tx_offset));
    pio_sm_exec(pio_, rx_sm_, pio_encode_jmp(loaded.rx_offset));

    pio_sm_set_enabled(pio_, tx_sm_, true);
    pio_sm_set_enabled(pio_, rx_sm_, true);

    // DS1 5.2: about a millisecond, during which the chip answers at neither
    // rate. Reading before that is reading its silence as a refusal.
    sleep_us(kBaudChangeUs);
    return true;
}

void PioCh375Transport::set_rx_baud(unsigned baud) {
    if (!started_) {
        return;
    }
    const float divider =
        static_cast<float>(clock_get_hz(clk_sys)) / static_cast<float>(baud * kCyclesPerBit);
    pio_sm_set_clkdiv(pio_, rx_sm_, divider);
}

void PioCh375Transport::write_command(std::uint8_t command) {
    if (!started_) {
        return;
    }
    // Bit 8 set marks the other eight as a command rather than data.
    pio_sm_put_blocking(pio_, tx_sm_, static_cast<std::uint32_t>(command) | 0x100u);
}

void PioCh375Transport::write_data(std::uint8_t value) {
    if (!started_) {
        return;
    }
    pio_sm_put_blocking(pio_, tx_sm_, static_cast<std::uint32_t>(value));
}

bool PioCh375Transport::read_data(std::uint8_t& value) {
    if (!started_ || pio_sm_is_rx_fifo_empty(pio_, rx_sm_)) {
        return false;
    }
    // Nine bits shifted in from the top of a 32-bit register sit in bits
    // 31..23. Only the low eight are data; the ninth is the chip's own
    // command flag, which is always clear on the way back.
    const std::uint32_t word = pio_sm_get(pio_, rx_sm_);
    value = static_cast<std::uint8_t>((word >> 23) & 0xFFu);
    return true;
}

bool PioCh375Transport::int_asserted() const {
    // Active low: the chip pulls it down to ask for attention.
    return started_ && gpio_get(int_pin_) == 0;
}

std::uint32_t PioCh375Transport::now_us() const {
    return time_us_32();
}

std::uint32_t PioCh375Transport::framing_errors() const {
    if (!started_) {
        return 0;
    }
    // The receive program raises the flag with `irq 4 rel`, which for state
    // machine n is flag 4 + n, not flag n. Reading flag n instead reports no
    // framing errors no matter how many there are - and "no framing errors"
    // was being used here as evidence that a stream of frames was real data
    // rather than noise. It was evidence of nothing.
    const unsigned flag = 4u + (rx_sm_ & 3u);
    if (!pio_interrupt_get(pio_, flag)) {
        return 0;
    }
    pio_interrupt_clear(pio_, flag);
    return 1;
}

bool PioCh375Transport::read_word(std::uint16_t& word) {
    if (!started_ || pio_sm_is_rx_fifo_empty(pio_, rx_sm_)) {
        return false;
    }
    word = static_cast<std::uint16_t>((pio_sm_get(pio_, rx_sm_) >> 23) & 0x1FFu);
    return true;
}

void PioCh375Transport::drain() {
    if (!started_) {
        return;
    }
    while (!pio_sm_is_rx_fifo_empty(pio_, rx_sm_)) {
        (void)pio_sm_get(pio_, rx_sm_);
    }
}

}  // namespace duo_input::u1::ch375
