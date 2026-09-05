#pragma once

#include <stdbool.h>
#include <stdint.h>

#define TUH_CFGID_RPI_PIO_USB_CONFIGURATION 1u
#define CFG_TUH_DEVICE_MAX 4u

#ifdef __cplusplus
extern "C" {
#endif

bool tuh_configure(uint8_t rhport, uint8_t cfg_id, const void* config);
bool tuh_init(uint8_t rhport);
void tuh_task(void);

// Both are real TinyUSB entry points in the pinned tree
// (.deps/tinyusb/src/host/usbh.h:141,168). tuh_rhport_is_active is the one
// PioUsbBackend::begin() reads first, before it changes anything: it is the
// only reading that separates a host this backend started from a host
// something else had already started.
bool tuh_inited(void);
bool tuh_rhport_is_active(uint8_t rhport);
bool tuh_mounted(uint8_t dev_addr);

#ifdef __cplusplus
}
#endif
