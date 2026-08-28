// UsbService's endpoint half, without TinyUSB.
//
// These are the four members firmware/u1_main/usb_service.cpp defines against
// the Pico SDK. Everything else about the class - publish, its change
// detection, when the mouse snapshot is consumed - is compiled from the
// firmware header exactly as it ships.

#include "fakes/usb_host.hpp"

#include "usb_service.hpp"

namespace duo::test {

UsbHost& usb_host() {
    static UsbHost host;
    return host;
}

}  // namespace duo::test

namespace duo_input::u1 {

void UsbService::begin() {}

void UsbService::task() {}

bool UsbService::mounted() const { return duo::test::usb_host().mounted; }

bool UsbService::suspended() const { return false; }

bool UsbService::send_keyboard(const hid::KeyboardSnapshot& keyboard) {
    duo::test::UsbHost& host = duo::test::usb_host();
    if (!host.keyboard_ready || host.keyboard_count >= duo::test::UsbHost::kMaxReports) {
        return false;
    }
    host.keyboard[host.keyboard_count++] = keyboard;
    return true;
}

bool UsbService::send_mouse(const hid::MouseSnapshot& mouse) {
    duo::test::UsbHost& host = duo::test::usb_host();
    if (!host.mouse_ready || host.mouse_count >= duo::test::UsbHost::kMaxReports) {
        return false;
    }
    host.mouse[host.mouse_count++] = mouse;
    return true;
}

}  // namespace duo_input::u1
