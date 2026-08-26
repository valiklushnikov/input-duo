#include "usb_service.hpp"

#include <cstring>

#include "pico/bootrom.h"
#include "tusb.h"

#include "hid/report_ids.hpp"

namespace duo_input::u1 {
namespace {

constexpr std::uint8_t kKeyboard = static_cast<std::uint8_t>(hid::U1Interface::Keyboard);
constexpr std::uint8_t kMouse = static_cast<std::uint8_t>(hid::U1Interface::Mouse);

}  // namespace

void UsbService::begin() {
    // The RP2040 has exactly one device root-hub port, and tusb_init picks it
    // from CFG_TUSB_RHPORT0_MODE rather than making every caller name it.
    tusb_init();
}

void UsbService::task() {
    tud_task();
}

bool UsbService::mounted() const {
    return tud_mounted();
}

bool UsbService::suspended() const {
    return tud_suspended();
}

void UsbService::forget_sent_state() {
    keyboard_valid_ = false;
    last_buttons_ = 0;
}

bool UsbService::send_keyboard(const hid::KeyboardSnapshot& keyboard) {
    if (!tud_hid_n_ready(kKeyboard)) {
        return false;
    }
    // A boot keyboard report is six usages and a modifier byte. The unused
    // slots are zero, which is what "no key" means in this report.
    std::uint8_t usages[hid::kMaxKeys] = {};
    std::memcpy(usages, keyboard.keys, keyboard.key_count);
    return tud_hid_n_keyboard_report(kKeyboard, hid::kNoReportId, keyboard.modifiers, usages);
}

bool UsbService::send_mouse(const hid::MouseSnapshot& mouse) {
    if (!tud_hid_n_ready(kMouse)) {
        return false;
    }
    // The report carries signed bytes for movement; the snapshot has already
    // saturated to int16, so this clamps once more to what actually fits.
    const auto clamp = [](std::int16_t value) -> std::int8_t {
        if (value > 127) {
            return 127;
        }
        if (value < -127) {
            return -127;
        }
        return static_cast<std::int8_t>(value);
    };
    return tud_hid_n_mouse_report(kMouse, hid::kNoReportId, mouse.buttons,
                                  clamp(mouse.delta_x), clamp(mouse.delta_y), mouse.wheel,
                                  mouse.pan);
}

bool UsbService::same_as_last_keyboard(const hid::KeyboardSnapshot& keyboard) const {
    if (keyboard.modifiers != last_keyboard_.modifiers ||
        keyboard.key_count != last_keyboard_.key_count) {
        return false;
    }
    for (std::uint8_t index = 0; index < keyboard.key_count; ++index) {
        if (keyboard.keys[index] != last_keyboard_.keys[index]) {
            return false;
        }
    }
    return true;
}

bool UsbService::has_movement(const hid::MouseSnapshot& mouse) {
    return mouse.delta_x != 0 || mouse.delta_y != 0 || mouse.wheel != 0 || mouse.pan != 0;
}

}  // namespace duo_input::u1

// ------------------------------------------------------------ TinyUSB hooks

extern "C" {

// The host can ask for a report; there is nothing to volunteer beyond what
// publish() already sends, so this answers with nothing rather than with
// stale bytes.
std::uint16_t tud_hid_get_report_cb(std::uint8_t instance, std::uint8_t report_id,
                                    hid_report_type_t report_type, std::uint8_t* buffer,
                                    std::uint16_t reqlen) {
    (void)instance;
    (void)report_id;
    (void)report_type;
    (void)buffer;
    (void)reqlen;
    return 0;
}

// Opening the port at 1200 baud and dropping DTR asks the board to restart
// into its bootloader. This is the convention every Arduino and Pico board
// follows, and the configurator and the update instructions both rely on it:
// without it, every firmware update needs someone physically present to hold
// BOOTSEL, which is a poor thing to require for a fix.
//
// Nothing opens a port at 1200 baud by accident - it is a rate no modern
// device uses - and the worst case is a reboot into a bootloader the operator
// can leave by unplugging the board.
void tud_cdc_line_state_cb(std::uint8_t instance, bool dtr, bool rts) {
    (void)instance;
    (void)rts;
    if (dtr) {
        return;
    }
    cdc_line_coding_t coding;
    tud_cdc_get_line_coding(&coding);
    if (coding.bit_rate == 1200) {
        reset_usb_boot(0, 0);
    }
}

// Output reports carry keyboard LED state. Duo Input has no LEDs to drive on
// the operator's behalf, so this is accepted and ignored rather than refused:
// a stalled endpoint here upsets some hosts.
void tud_hid_set_report_cb(std::uint8_t instance, std::uint8_t report_id,
                           hid_report_type_t report_type, const std::uint8_t* buffer,
                           std::uint16_t bufsize) {
    (void)instance;
    (void)report_id;
    (void)report_type;
    (void)buffer;
    (void)bufsize;
}

}  // extern "C"
