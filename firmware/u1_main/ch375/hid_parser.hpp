#pragma once

// Reading a USB configuration descriptor from a device nobody vouched for.
//
// The bytes arrive from whatever someone plugged in. Each record carries its
// own length, and those lengths are the only thing saying where the next one
// begins - so a parser that believes them walks wherever it is pointed, which
// in firmware means past the end of the buffer and into whatever is next.
//
// Every length here is checked against what is left rather than against what
// the descriptor claims to have, and a descriptor that does not add up is
// refused whole. Half a keyboard is not a keyboard: a device accepted with one
// field misread is one that types the wrong thing on somebody's computer, and
// that is worse than a device that says it is unsupported.
//
// What is looked for is deliberately narrow - one interface this firmware can
// actually route, and the endpoint its reports arrive on. Anything else is
// refused by name, so an unsupported device says so rather than half working.

#include <cstddef>
#include <cstdint>

#include "protocol/bytes.hpp"

namespace duo_input::u1::ch375 {

enum class DeviceKind : std::uint8_t {
    Unknown,
    Keyboard,
    Mouse,
};

enum class ParseError : std::uint8_t {
    None,
    /// A record runs past the end of what arrived, or the whole thing is
    /// shorter than it says. The chip's control buffer is 64 bytes, so a long
    /// descriptor genuinely does arrive cut short.
    Truncated,
    /// The first record is not a configuration descriptor.
    NotAConfiguration,
    /// Nothing in here is a keyboard or a mouse this firmware can route.
    NoUsableInterface,
    /// An endpoint promising more than the controller can read in one go.
    PacketTooLarge,
};

struct HidCapabilities {
    DeviceKind kind = DeviceKind::Unknown;
    /// Which interface was chosen - composite devices have several.
    std::uint8_t interface_number = 0;
    /// The endpoint number reports arrive on, without the direction bit.
    std::uint8_t endpoint = 0;
    std::uint16_t max_packet = 0;
    /// The device declares the boot subclass, so it can be put into a report
    /// format this firmware already understands without reading its report
    /// descriptor.
    bool boot_protocol = false;
    /// How many bytes the chosen interface's HID report descriptor is.
    ///
    /// From the HID class descriptor between the interface and its endpoints
    /// (HID 1.11 6.2.1), and the only place the number exists - a GET_DESCRIPTOR
    /// for type 0x22 has to say how much to ask for, and a device that is asked
    /// for less than it has answers with the descriptor cut short.
    ///
    /// Zero means the interface did not declare one, which is a device to leave
    /// on the path it already worked on rather than one to guess at.
    std::uint16_t report_descriptor_length = 0;
    /// Optional interrupt-IN endpoint belonging to another HID interface of
    /// the same composite receiver. Its packets are serviced and discarded so
    /// they cannot block the primary mouse/keyboard endpoint.
    std::uint8_t auxiliary_endpoint = 0;
    std::uint16_t auxiliary_max_packet = 0;
    /// A second non-routed interrupt-IN endpoint. Keychron's receiver has
    /// both a vendor channel and a boot-keyboard channel beside its mouse.
    std::uint8_t secondary_auxiliary_endpoint = 0;
    std::uint16_t secondary_auxiliary_max_packet = 0;
};

/// The largest packet the controller can read in one go (DS1 5.13).
inline constexpr std::uint16_t kMaxReadablePacket = 64;

/// Walk a configuration descriptor and pick the interface to use.
///
/// Returns ParseError::None and fills ``out`` on success; on any failure
/// ``out`` is left untouched, so a caller cannot half-use a refused device.
ParseError parse_configuration(protocol::ByteView descriptor, HidCapabilities& out);

}  // namespace duo_input::u1::ch375
