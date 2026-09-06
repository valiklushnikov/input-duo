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
#define USB_PID_SETUP 0x2d

typedef struct {
    // These are the library's own field names and volatility for everything
    // backend.cpp samples.  Physical layout is deliberately irrelevant in a
    // native build; the linked-image contract exercises the real endpoint_t.
    volatile uint8_t dev_addr;
    volatile uint8_t ep_num;
    volatile uint16_t size;
    bool need_pre;
    bool is_tx;
    volatile uint8_t data_id;
    volatile bool stalled;
    volatile bool has_transfer;
    volatile bool transfer_aborted;
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
