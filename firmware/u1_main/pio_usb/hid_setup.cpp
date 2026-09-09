#include "pio_usb/hid_setup.hpp"

#include "crypto/sha256.hpp"

namespace duo_input::u1::pio_usb {
namespace {

constexpr std::uint8_t kProtocolKeyboard = 1;
constexpr std::uint8_t kProtocolMouse = 2;

input::hid::HidReportSet boot_report_set(input::DeviceKind kind) {
    input::hid::HidReportSet set;
    set.count = 1;
    set.entries[0].role = kind == input::DeviceKind::Mouse
                              ? input::hid::ReportRole::Mouse
                              : input::hid::ReportRole::Keyboard;
    set.entries[0].keyboard = input::hid::boot_keyboard_layout();
    set.entries[0].mouse = input::hid::boot_mouse_layout();
    return set;
}

input::hid::ReportDescriptorError report_error(
    const input::hid::HidReportSet& set,
    input::hid::ReportRole role,
    input::hid::ReportDescriptorError missing) {
    std::size_t accepted = 0;
    for (std::size_t index = 0; index < set.count; ++index) {
        if (set.entries[index].role == role) {
            ++accepted;
        }
    }
    if (accepted == 1) {
        return input::hid::ReportDescriptorError::None;
    }
    if (accepted > 1) {
        return input::hid::ReportDescriptorError::AmbiguousKeyboardReport;
    }
    for (std::size_t index = 0; index < set.rejected_count; ++index) {
        if (set.rejected[index].role == role) {
            return set.rejected[index].reason ==
                           input::hid::ReportDescriptorError::AmbiguousReportSet
                       ? input::hid::ReportDescriptorError::AmbiguousKeyboardReport
                       : set.rejected[index].reason;
        }
    }
    return missing;
}

void mirror_first_report(input::SourceIdentity& out) {
    const input::hid::HidReportEntry& first = out.report_set.entries[0];
    switch (first.role) {
        case input::hid::ReportRole::Keyboard:
            out.kind = input::DeviceKind::Keyboard;
            out.keyboard_layout = first.keyboard;
            break;
        case input::hid::ReportRole::Consumer:
            out.kind = input::DeviceKind::Consumer;
            out.keyboard_layout = first.keyboard;
            break;
        case input::hid::ReportRole::Mouse:
            out.kind = input::DeviceKind::Mouse;
            out.mouse_layout = first.mouse;
            break;
    }
}

}  // namespace

HidLayoutSource classify_hid_layout(std::uint8_t protocol,
                                    const std::uint8_t* descriptor,
                                    std::size_t length,
                                    input::SourceIdentity& out) {
    out = {};

    if (descriptor != nullptr && length != 0) {
        crypto::sha256(descriptor, length, out.descriptor_hash);

        const input::hid::ReportDescriptorError error =
            input::hid::parse_hid_report_set(
                protocol::ByteView{descriptor, length}, out.report_set);
        if (error == input::hid::ReportDescriptorError::None) {
            out.keyboard_error = static_cast<std::uint8_t>(report_error(
                out.report_set, input::hid::ReportRole::Keyboard,
                input::hid::ReportDescriptorError::NoKeyboardReport));
            out.consumer_error = static_cast<std::uint8_t>(report_error(
                out.report_set, input::hid::ReportRole::Consumer,
                input::hid::ReportDescriptorError::NoKeyboardReport));
        } else {
            out.keyboard_error = static_cast<std::uint8_t>(error);
            out.consumer_error = static_cast<std::uint8_t>(error);
        }
        if (out.report_set.count != 0) {
            mirror_first_report(out);
            out.layout_source = 1;
            return HidLayoutSource::ReportDescriptor;
        }
    }

    if (protocol == kProtocolKeyboard) {
        out.kind = input::DeviceKind::Keyboard;
        out.report_set = boot_report_set(out.kind);
        out.keyboard_layout = out.report_set.entries[0].keyboard;
        out.layout_source = 2;
        return HidLayoutSource::BootProtocol;
    }
    if (protocol == kProtocolMouse) {
        out.kind = input::DeviceKind::Mouse;
        out.report_set = boot_report_set(out.kind);
        out.mouse_layout = out.report_set.entries[0].mouse;
        out.layout_source = 2;
        return HidLayoutSource::BootProtocol;
    }
    return HidLayoutSource::None;
}

}  // namespace duo_input::u1::pio_usb
