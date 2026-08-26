#pragma once

// How the two boards identify themselves on USB.
//
// ---------------------------------------------------------------------------
// PROTOTYPE PLACEHOLDER - NOT A LAWFUL USB VENDOR ALLOCATION.
//
// 0x1209 is the pid.codes vendor ID, and these product IDs are not allocated
// to this project. They exist so the configurator's whitelist has something
// concrete to match during development. They MUST be replaced with real
// allocated identifiers before any commercial release.
//
// These values are mirrored in configurator/src/duo_input/device/discovery.py
// and tests/build/test_usb_descriptors.py asserts the two agree, so the
// firmware and the program that looks for it cannot drift apart.
// ---------------------------------------------------------------------------

#include <cstdint>

namespace duo_input::usb {

inline constexpr std::uint16_t kVendorId = 0x1209;

inline constexpr std::uint16_t kU1ProductId = 0xD101;
inline constexpr std::uint16_t kU2ProductId = 0xD102;

inline constexpr const char* kManufacturer = "Duo Input";
inline constexpr const char* kU1Product = "Duo Input U1";
inline constexpr const char* kU2Product = "Duo Input U2";

/// The serial number is this prefix followed by the RP2040's unique chip ID,
/// so two units are distinguishable and the same unit is recognisable across
/// reconnects.
inline constexpr const char* kU1SerialPrefix = "DIU1-";
inline constexpr const char* kU2SerialPrefix = "DIU2-";

/// USB device release, as BCD. Independent of the firmware version: it changes
/// only when the descriptors themselves change, because Windows caches driver
/// bindings against it.
inline constexpr std::uint16_t kDeviceReleaseBcd = 0x0100;

}  // namespace duo_input::usb
