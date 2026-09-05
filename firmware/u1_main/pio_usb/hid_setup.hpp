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

/// The Keychron M3 receiver, 3434:D030. Its side button is emitted by a
/// second interface shaped like a keyboard - boot protocol, or a descriptor
/// that reads as one - and never by a real keyboard.
inline constexpr std::uint16_t kKeychronAuxiliaryVendorId = 0x3434;
inline constexpr std::uint16_t kKeychronAuxiliaryProductId = 0xD030;

/// Whether an interface classify_hid has shaped as DeviceKind::Keyboard is
/// really this receiver's auxiliary side-button channel rather than a
/// genuine keyboard - the transport association the neutral boundary carries
/// instead of a TinyUSB type.
///
/// True only for the exact vendor/product this receiver reports. Every other
/// composite device's keyboard-shaped interface keeps the Keyboard role it
/// would otherwise earn; this decides which interface the receiver's side
/// channel is, nothing about what a given report on it means - that is
/// input/pipeline.cpp's report-shape check (keychron_side_state), unchanged
/// by this function and still the only thing that can turn a report from
/// here into mouse button 4.
bool is_keychron_auxiliary_interface(std::uint16_t vendor_id, std::uint16_t product_id,
                                     input::DeviceKind classified_kind);

}  // namespace duo_input::u1::pio_usb
