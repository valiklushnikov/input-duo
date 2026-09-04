#pragma once

#include <stdbool.h>
#include <stdint.h>

#define TUH_CFGID_RPI_PIO_USB_CONFIGURATION 1u

#ifdef __cplusplus
extern "C" {
#endif

bool tuh_configure(uint8_t rhport, uint8_t cfg_id, const void* config);
bool tuh_init(uint8_t rhport);
void tuh_task(void);

#ifdef __cplusplus
}
#endif
