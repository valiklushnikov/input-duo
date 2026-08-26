// U2 entry point.
//
// U2 has no configuration, no CDC and no peripherals of its own. Everything it
// emits arrives over SPI from U1, which means the only thing it must get right
// unconditionally is this: when it has nothing valid to say, it says nothing
// and holds nothing.

#include "pico/stdlib.h"

namespace {

void configure_indicator() {
#ifdef PICO_DEFAULT_LED_PIN
    gpio_init(PICO_DEFAULT_LED_PIN);
    gpio_set_dir(PICO_DEFAULT_LED_PIN, GPIO_OUT);
    gpio_put(PICO_DEFAULT_LED_PIN, 0);
#endif
}

}  // namespace

int main() {
    configure_indicator();

    // No link yet, so nothing is held. That is the whole invariant this board
    // exists to preserve, and it is true from the first instruction.
    while (true) {
        tight_loop_contents();
    }
}
