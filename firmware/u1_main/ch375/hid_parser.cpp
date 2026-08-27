#include "ch375/hid_parser.hpp"

namespace duo_input::u1::ch375 {
namespace {

constexpr std::uint8_t kDescriptorConfiguration = 0x02;
constexpr std::uint8_t kDescriptorInterface = 0x04;
constexpr std::uint8_t kDescriptorEndpoint = 0x05;

constexpr std::uint8_t kClassHid = 0x03;
constexpr std::uint8_t kSubclassBoot = 0x01;
constexpr std::uint8_t kProtocolKeyboard = 0x01;
constexpr std::uint8_t kProtocolMouse = 0x02;

/// Bits 0-1 of an endpoint's attributes. Interrupt is 3.
constexpr std::uint8_t kTransferInterrupt = 0x03;
/// Bit 7 of an endpoint address: set means IN, towards the host.
constexpr std::uint8_t kEndpointDirectionIn = 0x80;

/// The shortest a record can be: a length and a type.
constexpr std::size_t kMinimumRecord = 2;

DeviceKind kind_of(std::uint8_t device_class, std::uint8_t protocol) {
    if (device_class != kClassHid) {
        return DeviceKind::Unknown;
    }
    if (protocol == kProtocolKeyboard) {
        return DeviceKind::Keyboard;
    }
    if (protocol == kProtocolMouse) {
        return DeviceKind::Mouse;
    }
    // A HID interface that is neither - consumer controls, a touchpad's
    // vendor interface, a gamepad. This firmware has nothing to route it to.
    return DeviceKind::Unknown;
}

}  // namespace

ParseError parse_configuration(protocol::ByteView descriptor, HidCapabilities& out) {
    if (descriptor.data == nullptr || descriptor.size < kMinimumRecord) {
        return ParseError::Truncated;
    }
    // The type is checked before the length. Both would refuse a device
    // descriptor offered here, but only one of them says why - and "this is
    // the wrong descriptor" and "this descriptor was cut short" want
    // different things done about them.
    if (descriptor.data[1] != kDescriptorConfiguration) {
        return ParseError::NotAConfiguration;
    }
    if (descriptor.size < 9) {
        return ParseError::Truncated;
    }

    // What the header says the whole thing weighs. If more than arrived, the
    // rest was cut off in transit - the controller's control buffer is 64
    // bytes - and the records that are missing might be the ones that matter.
    const std::size_t claimed =
        static_cast<std::size_t>(descriptor.data[2]) |
        (static_cast<std::size_t>(descriptor.data[3]) << 8);
    if (claimed > descriptor.size) {
        return ParseError::Truncated;
    }
    const std::size_t total = claimed < descriptor.size ? claimed : descriptor.size;

    // Filled in only when an interface is both usable and complete. A caller
    // that gets a failure must not find half of a device in here.
    HidCapabilities candidate;
    bool interface_open = false;
    DeviceKind open_kind = DeviceKind::Unknown;
    std::uint8_t open_number = 0;
    bool open_boot = false;

    std::size_t at = descriptor.data[0];
    if (at < kMinimumRecord || at > total) {
        return ParseError::Truncated;
    }

    while (at < total) {
        const std::size_t remaining = total - at;
        if (remaining < kMinimumRecord) {
            return ParseError::Truncated;
        }
        const std::size_t length = descriptor.data[at];
        // A record shorter than its own header, or longer than what is left,
        // is the end of anything trustworthy. Zero is the dangerous one: it
        // would leave this walk standing still, reading the same bytes until
        // the power goes off.
        if (length < kMinimumRecord || length > remaining) {
            return ParseError::Truncated;
        }

        const std::uint8_t type = descriptor.data[at + 1];
        if (type == kDescriptorInterface) {
            if (length < 9) {
                return ParseError::Truncated;
            }
            const std::uint8_t number = descriptor.data[at + 2];
            const std::uint8_t device_class = descriptor.data[at + 5];
            const std::uint8_t subclass = descriptor.data[at + 6];
            const std::uint8_t protocol = descriptor.data[at + 7];

            open_kind = kind_of(device_class, protocol);
            open_number = number;
            open_boot = subclass == kSubclassBoot;
            // An interface is only interesting until its endpoints have been
            // seen. A composite device has several, and the first HID one is
            // often consumer controls - stopping there picks an interface
            // this firmware cannot route and calls the keyboard behind it
            // unsupported.
            interface_open = open_kind != DeviceKind::Unknown;
        } else if (type == kDescriptorEndpoint && interface_open) {
            if (length < 7) {
                return ParseError::Truncated;
            }
            const std::uint8_t address = descriptor.data[at + 2];
            const std::uint8_t attributes = descriptor.data[at + 3];
            const std::uint16_t max_packet =
                static_cast<std::uint16_t>(descriptor.data[at + 4]) |
                static_cast<std::uint16_t>(static_cast<std::uint16_t>(descriptor.data[at + 5])
                                           << 8);

            const bool interrupt_in = (attributes & 0x03) == kTransferInterrupt &&
                                      (address & kEndpointDirectionIn) != 0;
            if (interrupt_in) {
                if (max_packet == 0 || max_packet > kMaxReadablePacket) {
                    // Reports would arrive cut in half, which is worse than
                    // refusing the device: a truncated report is a keystroke
                    // that is not the one somebody made.
                    return ParseError::PacketTooLarge;
                }
                candidate.kind = open_kind;
                candidate.interface_number = open_number;
                candidate.endpoint = static_cast<std::uint8_t>(address & 0x0F);
                candidate.max_packet = max_packet;
                candidate.boot_protocol = open_boot;

                // A keyboard is what this device is mainly for, so it wins
                // outright. A mouse is kept and the walk continues, in case a
                // keyboard interface follows it on the same device.
                if (candidate.kind == DeviceKind::Keyboard) {
                    out = candidate;
                    return ParseError::None;
                }
                interface_open = false;
            }
        }

        at += length;
    }

    if (candidate.kind == DeviceKind::Unknown) {
        return ParseError::NoUsableInterface;
    }
    out = candidate;
    return ParseError::None;
}

}  // namespace duo_input::u1::ch375
