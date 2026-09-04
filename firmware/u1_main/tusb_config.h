#pragma once

// TinyUSB configuration for U1: three HID interfaces and one CDC on RHPort 0,
// device mode, in every build. The PIO USB backend adds a host stack on
// RHPort 1 - see docs/superpowers/specs/2026-09-02-pio-usb-hub-v1-design.md.

#ifdef __cplusplus
extern "C" {
#endif

#define CFG_TUSB_MCU OPT_MCU_RP2040
#define CFG_TUSB_OS OPT_OS_PICO
#define CFG_TUSB_RHPORT0_MODE (OPT_MODE_DEVICE | OPT_MODE_FULL_SPEED)

#ifdef DUO_INPUT_BACKEND_PIO_USB
// RHPort 1 is Pico-PIO-USB's host controller, dual-role alongside the device
// port above; tusb_option.h resolves TUD to whichever RHPORTx_MODE carries
// OPT_MODE_DEVICE and TUH to whichever carries OPT_MODE_HOST, so the two
// stacks land on the ports this migration fixed them to without either one
// naming a port number itself.
#define CFG_TUSB_RHPORT1_MODE (OPT_MODE_HOST | OPT_MODE_FULL_SPEED)
#define CFG_TUH_RPI_PIO_USB 1
#define CFG_TUH_ENABLED 1

#define CFG_TUH_ENUMERATION_BUFSIZE 256

// One hub plus at least four downstream addresses is the fixed registry
// contract. CFG_TUH_DEVICE_MAX excludes the hub itself (TinyUSB's own
// convention); CFG_TUH_HID leaves room for two HID interfaces per downstream
// device, so composite receivers do not consume unbounded host state.
#define CFG_TUH_HUB 1
#define CFG_TUH_DEVICE_MAX 4
#define CFG_TUH_HID (2 * CFG_TUH_DEVICE_MAX)
#endif

// Debug output would go somewhere this board has no room to send it.
#define CFG_TUSB_DEBUG 0

#define CFG_TUSB_MEM_SECTION
#define CFG_TUSB_MEM_ALIGN __attribute__((aligned(4)))

#define CFG_TUD_ENDPOINT0_SIZE 64

// Keyboard, mouse and consumer each get their own interface so the keyboard
// can be a real boot keyboard; see firmware/common/hid/report_ids.hpp.
#define CFG_TUD_HID 3
#define CFG_TUD_CDC 1
#define CFG_TUD_MSC 0
#define CFG_TUD_MIDI 0
#define CFG_TUD_VENDOR 0

#define CFG_TUD_HID_EP_BUFSIZE 8

// The protocol permits a 1024-byte payload, but CDC carries the complete COBS
// wire frame: header, CRC, COBS overhead and delimiter make that frame larger
// than 1024 bytes.  tud_cdc_write() accepts only what fits in this FIFO and
// returns a short count for the rest, so a 1024-byte FIFO silently cut the
// diagnostic reply before its delimiter and made it undecodable.
#define CFG_TUD_CDC_RX_BUFSIZE 1024
#define CFG_TUD_CDC_TX_BUFSIZE 2048
#define CFG_TUD_CDC_EP_BUFSIZE 64

#ifdef __cplusplus
}
#endif
