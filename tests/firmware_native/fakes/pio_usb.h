#pragma once

// Stands in for .deps/pico-pio-usb/src/pio_usb.h in the native test build.
//
// Only what firmware/u1_main/pio_usb/backend.cpp actually uses: the
// configuration struct it fills in, the root-port record it reads its
// observability bits out of, and the SOF counter. The field names and the
// root-port count are the library's own - see usb_definitions.h:93-117 and
// pio_usb_configuration.h:50 in the pinned clone - so a rename upstream shows
// up as a compile error here rather than as a diagnostic field that quietly
// reports the wrong bit.

#include <stdint.h>

typedef struct pio_usb_configuration {
    uint8_t pin_dp;
} pio_usb_configuration_t;

#define PIO_USB_DEFAULT_CONFIG pio_usb_configuration_t{}

#define PIO_USB_ROOT_PORT_CNT 2
#define PIO_USB_EP_POOL_CNT 32

typedef struct {
    // dev_addr and ep_num are the library's own names and volatility for the
    // two fields ep_slot_map encodes; ep_num carries the direction bit,
    // because pio_usb_ll_configure_endpoint assigns it bEndpointAddress
    // verbatim (pio_usb.c:465).
    volatile uint8_t dev_addr;
    volatile uint8_t ep_num;
    volatile uint16_t size;
    uint8_t failed_count;
} endpoint_t;

typedef struct struct_root_port_t {
    volatile bool initialized;
    volatile bool is_fullspeed;
    volatile bool connected;
    volatile bool suspended;
} root_port_t;

#ifdef __cplusplus
extern "C" {
#endif

uint32_t pio_usb_host_get_frame_number(void);
extern endpoint_t pio_usb_ep_pool[PIO_USB_EP_POOL_CNT];

#ifdef __cplusplus
}
#endif
