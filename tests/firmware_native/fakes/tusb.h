#pragma once

#include <stdbool.h>
#include <stdint.h>

#define TUH_CFGID_RPI_PIO_USB_CONFIGURATION 1u
#define CFG_TUH_DEVICE_MAX 4u
// The pinned firmware configuration (firmware/u1_main/tusb_config.h). Both
// numbers decide how many addresses backend.cpp walks and how many bits
// ep_slot_map and enum_progress_mask need, so the fake has to agree with it.
#define CFG_TUH_HUB 1u

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
// Real TinyUSB entry point (.deps/tinyusb/src/host/usbh.h). True only once the
// device is addressed AND its device descriptor has been read, which is what
// makes it the second half of enum_progress_mask.
bool tuh_vid_pid_get(uint8_t dev_addr, uint16_t* vendor_id, uint16_t* product_id);

#ifdef __cplusplus
}
#endif
