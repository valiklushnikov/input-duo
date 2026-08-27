#include "ch375/pio_transport.hpp"

#include "ch375_serial.pio.h"
#include "hardware/clocks.h"
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
    sm_config_set_out_shift(&tx, true, true, kFrameBits);
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
    sm_config_set_in_shift(&rx, true, true, kFrameBits);
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

void PioCh375Transport::set_baud(unsigned baud) {
    if (!started_) {
        return;
    }
    baud_ = baud;
    const float divider =
        static_cast<float>(clock_get_hz(clk_sys)) / static_cast<float>(baud * kCyclesPerBit);
    pio_sm_set_clkdiv(pio_, tx_sm_, divider);
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
    // The receive program raises a relative interrupt when a stop bit is not
    // where it belongs. It is sticky, so this reads and clears it - one or
    // more bad frames since the last look.
    const unsigned flag = rx_sm_;
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
