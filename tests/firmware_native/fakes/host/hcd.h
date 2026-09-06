#pragma once

// Stands in for .deps/tinyusb/src/host/hcd.h in the native test build.
//
// Only the part backend.cpp uses: the event record a host controller hands to
// the host stack, and the two entry points the stack publishes for a
// controller to call. The names, the field order and the enumerator order are
// TinyUSB's own (host/hcd.h:56-98,185-193), so a rename or a reorder upstream
// shows up as a compile error here rather than as a recovery that queues the
// wrong event.
//
// This tree does NOT own these two functions; usbh.c defines them. They are
// the documented direction of travel for a controller reporting a connection
// change, and hub.c:466-501 calls hcd_event_handler for exactly that reason.

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    HCD_EVENT_DEVICE_ATTACH,
    HCD_EVENT_DEVICE_REMOVE,
    HCD_EVENT_XFER_COMPLETE,

    USBH_EVENT_FUNC_CALL,  // Not an HCD event
    HCD_EVENT_COUNT
} hcd_eventid_t;

typedef struct {
    uint8_t rhport;
    uint8_t event_id;
    uint8_t dev_addr;

    union {
        struct {
            uint8_t hub_addr;
            uint8_t hub_port;
            uint8_t speed;
        } connection;

        struct {
            uint8_t ep_addr;
            uint8_t result;
            uint32_t len;
        } xfer_complete;

        struct {
            void (*func)(void*);
            void* param;
        } func_call;
    };
} hcd_event_t;

typedef struct {
    uint8_t rhport;
    uint8_t hub_addr;
    uint8_t hub_port;
    uint8_t speed;
} hcd_devtree_info_t;

#ifdef __cplusplus
extern "C" {
#endif

extern void hcd_devtree_get_info(uint8_t dev_addr, hcd_devtree_info_t* devtree_info);
extern void hcd_event_handler(hcd_event_t const* event, bool in_isr);

#ifdef __cplusplus
}
#endif
