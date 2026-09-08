#pragma once

#include <cstddef>
#include <cstdint>

#include "input/source.hpp"

namespace duo_input::u1::pio_usb {

/// Where the layout in a SourceIdentity came from.
///
/// This matters because the two sources describe different wire formats and a
/// caller has to keep the interface in the matching protocol. TinyUSB puts
/// boot-capable interfaces in boot protocol by default, whose mouse report is
/// three bytes with no Report ID; a descriptor-derived layout may declare one.
/// Reading boot reports with a descriptor layout silently drops every report -
/// measured 2026-09-06, see the PIO USB hub record.
enum class HidLayoutSource : std::uint8_t {
    /// Nothing usable; the identity is left neutral.
    None,
    /// The device's own report descriptor. The interface has to be put in
    /// report protocol for this layout to describe what arrives.
    ReportDescriptor,
    /// Boot protocol's fixed layout, used because no usable descriptor was
    /// available. The interface must stay in boot protocol.
    BootProtocol,
};

/// Classify one TinyUSB HID interface without trusting its interface protocol
/// over a usable report descriptor, and say which of the two the layout came
/// from.
///
/// The output is cleared before any parsing. Descriptor bytes, when present,
/// are hashed even when their shape is unsupported, so diagnostics can still
/// distinguish exact devices. A None return leaves the kind and both layouts
/// neutral.
HidLayoutSource classify_hid_layout(std::uint8_t protocol,
                                    const std::uint8_t* descriptor,
                                    std::size_t length,
                                    input::SourceIdentity& out);

/// Whether the interface is one this firmware can read at all.
///
/// Kept for callers that only need the verdict. A caller that has to choose a
/// protocol needs classify_hid_layout above instead.
bool classify_hid(std::uint8_t protocol, const std::uint8_t* descriptor,
                  std::size_t length, input::SourceIdentity& out);

}  // namespace duo_input::u1::pio_usb
