#include "ch375/hid_parser.hpp"

namespace duo_input::u1::ch375 {
namespace {

constexpr std::uint8_t kDescriptorConfiguration = 0x02;
constexpr std::uint8_t kDescriptorInterface = 0x04;
constexpr std::uint8_t kDescriptorEndpoint = 0x05;
/// HID 1.11 6.2.1, the class descriptor that sits between an interface and
/// its endpoints, and 7.1.1's type for the report descriptor it names.
constexpr std::uint8_t kDescriptorHid = 0x21;
constexpr std::uint8_t kDescriptorReport = 0x22;
/// bLength, bDescriptorType, bcdHID (2), bCountryCode, bNumDescriptors.
constexpr std::size_t kHidHeaderBytes = 6;
/// Each subordinate descriptor it names: a type and a two-byte length.
constexpr std::size_t kHidSubordinateBytes = 3;

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
    bool interface_open = false;
    DeviceKind open_kind = DeviceKind::Unknown;
    std::uint8_t open_number = 0;
    bool open_boot = false;
    std::uint16_t open_report_length = 0;
    HidCapabilities selected;
    bool have_selected = false;
    std::uint8_t auxiliary_endpoint = 0;
    std::uint16_t auxiliary_max_packet = 0;
    std::uint8_t secondary_auxiliary_endpoint = 0;
    std::uint16_t secondary_auxiliary_max_packet = 0;

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
            // Belongs to this interface and not to the one before it. A
            // composite device declares one HID record per interface, and
            // carrying the previous interface's length forward asks the device
            // for the wrong number of bytes - which comes back as a descriptor
            // cut short or as one with somebody else's bytes on the end.
            open_report_length = 0;
            // An interface is only interesting until its endpoints have been
            // seen. A composite device has several, and the first HID one is
            // often consumer controls - stopping there picks an interface
            // this firmware cannot route and calls the keyboard behind it
            // unsupported.
            interface_open = device_class == kClassHid;
        } else if (type == kDescriptorHid && interface_open) {
            // No test can tell this check from the one below it: both refuse
            // the same records with the same error, because a record shorter
            // than its header can never hold the entries it counts either.
            // What it does is stop the count itself being read - the sixth
            // byte of a two-byte record is not in the record, and at the end
            // of a descriptor it is not in the buffer.
            if (length < kHidHeaderBytes) {
                return ParseError::Truncated;
            }
            const std::size_t named = descriptor.data[at + 5];
            // Every entry has to fit inside the record's own length. One that
            // does not is read out of whatever record follows this one, and
            // bLength - which is what the walk steps by - would never notice.
            if (kHidHeaderBytes + named * kHidSubordinateBytes > length) {
                return ParseError::Truncated;
            }
            for (std::size_t index = 0; index < named; ++index) {
                const std::size_t entry = at + kHidHeaderBytes + index * kHidSubordinateBytes;
                if (descriptor.data[entry] != kDescriptorReport) {
                    // A physical descriptor, or something this firmware has no
                    // use for. Only the report descriptor says where a wheel is.
                    continue;
                }
                open_report_length =
                    static_cast<std::uint16_t>(descriptor.data[entry + 1]) |
                    static_cast<std::uint16_t>(
                        static_cast<std::uint16_t>(descriptor.data[entry + 2]) << 8);
                break;
            }
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
                    if (open_kind != DeviceKind::Unknown) {
                        return ParseError::PacketTooLarge;
                    }
                    at += length;
                    continue;
                }
                const std::uint8_t endpoint = static_cast<std::uint8_t>(address & 0x0F);
                if (open_kind != DeviceKind::Unknown && !have_selected) {
                    selected.kind = open_kind;
                    selected.interface_number = open_number;
                    selected.endpoint = endpoint;
                    selected.max_packet = max_packet;
                    selected.boot_protocol = open_boot;
                    selected.report_descriptor_length = open_report_length;
                    have_selected = true;
                } else {
                    // Every interrupt-IN endpoint other than the routed one
                    // still belongs to the same composite receiver. Leaving
                    // either the vendor channel or Keychron's later keyboard
                    // channel unpolled can leave a notification queued in the
                    // receiver while its mouse endpoint only NAKs.
                    if (auxiliary_endpoint == 0) {
                        auxiliary_endpoint = endpoint;
                        auxiliary_max_packet = max_packet;
                    } else if (secondary_auxiliary_endpoint == 0) {
                        secondary_auxiliary_endpoint = endpoint;
                        secondary_auxiliary_max_packet = max_packet;
                    }
                }
            }
        }

        at += length;
    }

    if (!have_selected) {
        return ParseError::NoUsableInterface;
    }
    selected.auxiliary_endpoint = auxiliary_endpoint;
    selected.auxiliary_max_packet = auxiliary_max_packet;
    selected.secondary_auxiliary_endpoint = secondary_auxiliary_endpoint;
    selected.secondary_auxiliary_max_packet = secondary_auxiliary_max_packet;
    // The diagnostic build used to select the *unknown* interface here, so
    // that DescriptorSetup would fetch and print its report descriptor. It did
    // its job - the Keychron receiver's vendor descriptor is captured in
    // docs/hardware/ch375-compatibility.md - and it has to go, because it made
    // the two builds route different endpoints.
    //
    // A diagnostic that changes what is being diagnosed is worse than none.
    // It left the probe image reading vendor packets as boot mouse reports:
    // 54 E2 01 02 arrives as buttons 0x54 held down and the pointer dragged
    // thirty counts left, on a build somebody is using to decide whether the
    // mouse works.
    out = selected;
    return ParseError::None;
}

}  // namespace duo_input::u1::ch375
