#include "pio_usb/hid_setup.hpp"

#include "crypto/sha256.hpp"

namespace duo_input::u1::pio_usb {
namespace {

constexpr std::uint8_t kProtocolKeyboard = 1;
constexpr std::uint8_t kProtocolMouse = 2;

}  // namespace

HidLayoutSource classify_hid_layout(std::uint8_t protocol,
                                    const std::uint8_t* descriptor,
                                    std::size_t length,
                                    input::SourceIdentity& out) {
    out = {};

    if (descriptor != nullptr && length != 0) {
        crypto::sha256(descriptor, length, out.descriptor_hash);

        input::hid::KeyboardReportLayout keyboard;
        input::hid::MouseReportLayout mouse;
        const input::hid::ReportDescriptorRole role =
            input::hid::classify_report_descriptor(
                protocol::ByteView{descriptor, length}, keyboard, mouse);
        if (role == input::hid::ReportDescriptorRole::Ambiguous) {
            return HidLayoutSource::None;
        }
        if (role == input::hid::ReportDescriptorRole::Keyboard) {
            out.kind = input::DeviceKind::Keyboard;
            out.keyboard_layout = keyboard;
            return HidLayoutSource::ReportDescriptor;
        }
        if (role == input::hid::ReportDescriptorRole::Mouse) {
            out.kind = input::DeviceKind::Mouse;
            out.mouse_layout = mouse;
            return HidLayoutSource::ReportDescriptor;
        }
        if (input::hid::parse_consumer_report_descriptor({descriptor, length}, keyboard) ==
            input::hid::ReportDescriptorError::None) {
            out.kind = input::DeviceKind::Consumer;
            out.keyboard_layout = keyboard;
            return HidLayoutSource::ReportDescriptor;
        }
    }

    if (protocol == kProtocolKeyboard) {
        out.kind = input::DeviceKind::Keyboard;
        out.keyboard_layout = input::hid::boot_keyboard_layout();
        return HidLayoutSource::BootProtocol;
    }
    if (protocol == kProtocolMouse) {
        out.kind = input::DeviceKind::Mouse;
        out.mouse_layout = input::hid::boot_mouse_layout();
        return HidLayoutSource::BootProtocol;
    }
    return HidLayoutSource::None;
}

bool classify_hid(std::uint8_t protocol, const std::uint8_t* descriptor,
                  std::size_t length, input::SourceIdentity& out) {
    return classify_hid_layout(protocol, descriptor, length, out) !=
          HidLayoutSource::None;
}

}  // namespace duo_input::u1::pio_usb
