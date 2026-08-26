#pragma once

// TinyUSB configuration for U2: three HID interfaces and nothing else.

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

// No CDC. U2 is not configurable, and a serial port on it would invite
// someone to try - the configurator would then be talking to the board that
// holds no configuration at all.
#define CFG_TUD_CDC 0
#define CFG_TUD_MSC 0
#define CFG_TUD_MIDI 0
#define CFG_TUD_VENDOR 0

#define CFG_TUD_HID_EP_BUFSIZE 8

#ifdef __cplusplus
}
#endif
