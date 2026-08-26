#include "usb_service.hpp"

#include <cstring>

#include "tusb.h"

#include "hid/report_ids.hpp"

namespace duo_input::u1 {
namespace {

constexpr std::uint8_t kKeyboard = static_cast<std::uint8_t>(hid::U1Interface::Keyboard);
constexpr std::uint8_t kMouse = static_cast<std::uint8_t>(hid::U1Interface::Mouse);

bool same_keyboard(const hid::KeyboardSnapshot& left, const hid::KeyboardSnapshot& right) {
    if (left.modifiers != right.modifiers || left.key_count != right.key_count) {
        return false;
    }
    for (std::uint8_t index = 0; index < left.key_count; ++index) {
        if (left.keys[index] != right.keys[index]) {
            return false;
        }
    }
    return true;
}

bool moved(const hid::MouseSnapshot& mouse) {
    return mouse.delta_x != 0 || mouse.delta_y != 0 || mouse.wheel != 0 || mouse.pan != 0;
}

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

bool UsbService::publish(hid::HidStateManager& manager) {
    if (!mounted()) {
        return false;
    }

    bool sent = false;
    const hid::TargetSnapshot current = manager.snapshot(hid::Target::Pc1);

    // Keys are an absolute state: resend only when it differs from what the
    // host was last told, so a held key does not flood the bus.
    if (!keyboard_valid_ || !same_keyboard(current.keyboard, last_keyboard_)) {
        if (send_keyboard(current.keyboard)) {
            last_keyboard_ = current.keyboard;
            keyboard_valid_ = true;
            sent = true;
        }
    }

    // Movement is a delta, so "unchanged" is not a reason to stay quiet.
    // Buttons are absolute and travel in the same report.
    if (moved(current.mouse) || current.mouse.buttons != last_buttons_) {
        if (send_mouse(current.mouse)) {
            last_buttons_ = current.mouse.buttons;
            // Consume only now: an endpoint that was busy has cost the pointer
            // a millisecond, not a movement.
            manager.take_snapshot(hid::Target::Pc1);
            sent = true;
        }
    }

    return sent;
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
