/* This is an additional assertion script; the Pico SDK owns the memory map.
 * .stack1_dummy contains the SDK's real core1_stack allocation, not a guessed
 * address range or the size of the whole scratch bank. */
ASSERT(SIZEOF(.stack1_dummy) >= 4096,
       "PIO USB Core 1 needs at least 4096 bytes for discovery plus nested IRQs")
