#pragma once

// TinyUSB configuration for U1: three HID interfaces and one CDC.

#ifdef __cplusplus
extern "C" {
#endif

#define CFG_TUSB_MCU OPT_MCU_RP2040
#define CFG_TUSB_OS OPT_OS_PICO
#define CFG_TUSB_RHPORT0_MODE (OPT_MODE_DEVICE | OPT_MODE_FULL_SPEED)

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
