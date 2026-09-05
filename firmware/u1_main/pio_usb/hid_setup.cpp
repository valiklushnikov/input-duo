#include "pio_usb/hid_setup.hpp"

#include "crypto/sha256.hpp"

namespace duo_input::u1::pio_usb {
namespace {

constexpr std::uint8_t kProtocolKeyboard = 1;
constexpr std::uint8_t kProtocolMouse = 2;

}  // namespace

bool classify_hid(std::uint8_t protocol, const std::uint8_t* descriptor,
                  std::size_t length, input::SourceIdentity& out) {
    out = {};

    if (descriptor != nullptr && length != 0) {
        crypto::sha256(descriptor, length, out.descriptor_hash);

        input::hid::KeyboardReportLayout keyboard;
        input::hid::MouseReportLayout mouse;
        const input::hid::ReportDescriptorRole role =
            input::hid::classify_report_descriptor(
                protocol::ByteView{descriptor, length}, keyboard, mouse);
        if (role == input::hid::ReportDescriptorRole::Ambiguous) {
            return false;
        }
        if (role == input::hid::ReportDescriptorRole::Keyboard) {
            out.kind = input::DeviceKind::Keyboard;
            out.keyboard_layout = keyboard;
            return true;
        }
        if (role == input::hid::ReportDescriptorRole::Mouse) {
            out.kind = input::DeviceKind::Mouse;
            out.mouse_layout = mouse;
            return true;
        }
    }

    if (protocol == kProtocolKeyboard) {
        out.kind = input::DeviceKind::Keyboard;
        out.keyboard_layout = input::hid::boot_keyboard_layout();
        return true;
    }
    if (protocol == kProtocolMouse) {
        out.kind = input::DeviceKind::Mouse;
        out.mouse_layout = input::hid::boot_mouse_layout();
        return true;
    }
    return false;
}

bool is_keychron_auxiliary_interface(std::uint16_t vendor_id, std::uint16_t product_id,
                                     input::DeviceKind classified_kind) {
    return vendor_id == kKeychronAuxiliaryVendorId && product_id == kKeychronAuxiliaryProductId &&
          classified_kind == input::DeviceKind::Keyboard;
}

}  // namespace duo_input::u1::pio_usb
