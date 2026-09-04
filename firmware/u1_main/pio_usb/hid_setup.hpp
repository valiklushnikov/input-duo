#pragma once

#include <cstddef>
#include <cstdint>

#include "input/source.hpp"

namespace duo_input::u1::pio_usb {

/// Classify one TinyUSB HID interface without trusting its interface protocol
/// over a usable report descriptor.
///
/// The output is cleared before any parsing. Descriptor bytes, when present,
/// are hashed even when their shape is unsupported, so diagnostics can still
/// distinguish exact devices. A false return leaves the kind and both layouts
/// neutral.
bool classify_hid(std::uint8_t protocol, const std::uint8_t* descriptor,
                  std::size_t length, input::SourceIdentity& out);

}  // namespace duo_input::u1::pio_usb
