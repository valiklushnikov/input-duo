#pragma once

// Stands in for the Pico SDK's hardware/clocks.h in the native test build,
// for the same reason fakes/hardware/timer.h stands in for its timer header:
// the firmware source calls these unconditionally, and only the build decides
// whether it reaches the real clock hardware or a value a test set.

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// clk_sys is enumerator 5 of clock_num_t in the real SDK
// (.deps/pico-sdk/src/rp2040/hardware_structs/include/hardware/structs/clocks.h:35).
// The value is carried over so a test reading this fake and a person reading a
// register dump are talking about the same clock.
enum { clk_sys = 5 };

bool set_sys_clock_khz(uint32_t requested_khz, bool required);
uint32_t clock_get_hz(uint32_t clock);

#ifdef __cplusplus
}
#endif
