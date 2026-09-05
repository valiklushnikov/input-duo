#pragma once

// Stands in for the Pico SDK's hardware/timer.h in the native test build.
//
// The real header declares time_us_32() as a `static inline` register read,
// so the extern-declaration trick this tree uses for genuine SDK/TinyUSB
// entry points (see device_registry.cpp's tuh_hid_receive_report) cannot be
// used for it: an extern declaration would leave the Pico link with an
// undefined symbol. Shadowing the header instead - exactly what
// fakes/hardware/clocks.h already does for set_sys_clock_khz - keeps the
// firmware source calling time_us_32() unconditionally, with the real inline
// on hardware and a test-settable function here.

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

uint32_t time_us_32(void);

#ifdef __cplusplus
}
#endif
