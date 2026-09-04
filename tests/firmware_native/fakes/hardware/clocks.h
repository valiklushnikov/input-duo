#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

bool set_sys_clock_khz(uint32_t requested_khz, bool required);

#ifdef __cplusplus
}
#endif
