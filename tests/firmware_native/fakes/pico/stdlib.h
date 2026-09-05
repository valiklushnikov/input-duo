#pragma once

// Native stand-in for the one boot-only settling delay used by the PIO USB
// backend. Tests exercise the ordering contract from the linked firmware; the
// host process must not really sleep for every backend fixture.

#include <stdint.h>

static inline void sleep_ms(uint32_t ms) { (void)ms; }
