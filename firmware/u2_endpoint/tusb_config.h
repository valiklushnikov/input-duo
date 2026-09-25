#pragma once

// TinyUSB configuration for U2: three HID interfaces plus one CDC pair used
// only for the address exchange.

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

// One CDC pair: U2 is not configurable, and this port carries only the
// address exchange - a program pointed at it for anything else is answered
// with UNSUPPORTED_CAPABILITY, not silence.
#define CFG_TUD_CDC 1
#define CFG_TUD_MSC 0
#define CFG_TUD_MIDI 0
#define CFG_TUD_VENDOR 0

#define CFG_TUD_HID_EP_BUFSIZE 8

// Address exchange only: requests and replies are well under 128 bytes.
#define CFG_TUD_CDC_RX_BUFSIZE 256
#define CFG_TUD_CDC_TX_BUFSIZE 256
#define CFG_TUD_CDC_EP_BUFSIZE 64

#ifdef __cplusplus
}
#endif
