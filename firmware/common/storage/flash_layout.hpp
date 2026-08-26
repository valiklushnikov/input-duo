#pragma once

// Where everything lives in U1's 2 MiB of flash.
//
// These offsets are not adjustable. A build that moves them turns every
// existing device's stored configuration into noise at the next write, and
// there is no way for the device to notice that has happened - the bytes at
// the new offset are simply whatever was there before.
//
// The two configuration slots exist so that a write never destroys the only
// copy. The device runs from one and writes to the other, and the new slot
// only becomes authoritative once its header lands, which is the last thing
// written.

#include <cstddef>
#include <cstdint>

#include "protocol/generated.hpp"

namespace duo_input::storage {

/// The whole part.
inline constexpr std::uint32_t kFlashSize = 2 * 1024 * 1024;

/// Erase granularity on an RP2040's flash.
inline constexpr std::uint32_t kSectorSize = 4096;

/// Program granularity.
inline constexpr std::uint32_t kPageSize = 256;

/// Firmware owns the first megabyte.
inline constexpr std::uint32_t kFirmwareOffset = 0x000000;
inline constexpr std::uint32_t kFirmwareSize = 0x100000;

/// Each configuration slot is 384 KiB.
inline constexpr std::uint32_t kSlotSize = 0x60000;

inline constexpr std::uint32_t kConfigAOffset = 0x100000;
inline constexpr std::uint32_t kConfigBOffset = 0x160000;

/// Whatever a later version needs: logs, a recovery image, counters.
inline constexpr std::uint32_t kServiceOffset = 0x1C0000;
inline constexpr std::uint32_t kServiceSize = 0x40000;

/// The header occupies one whole page at the start of a slot.
///
/// A page, not fewer bytes, because a page is the smallest thing flash can be
/// told to program: the header has to be written by itself, after the payload,
/// and it cannot share a page with data that was written earlier.
inline constexpr std::uint32_t kSlotHeaderSize = kPageSize;

/// How much configuration one slot can actually hold.
inline constexpr std::uint32_t kSlotPayloadCapacity = kSlotSize - kSlotHeaderSize;

static_assert(kFirmwareOffset + kFirmwareSize == kConfigAOffset,
              "config slot A must begin where the firmware ends");
static_assert(kConfigAOffset + kSlotSize == kConfigBOffset,
              "config slot B must begin where slot A ends");
static_assert(kConfigBOffset + kSlotSize == kServiceOffset,
              "the service area must begin where slot B ends");
static_assert(kServiceOffset + kServiceSize == kFlashSize,
              "the layout must account for the whole part");
static_assert(kSlotSize % kSectorSize == 0, "a slot must be a whole number of sectors");
static_assert(kSlotPayloadCapacity >= protocol::ProtocolLimits::BINARY_CONFIG_MAX_BYTES,
              "a slot must hold the largest configuration the protocol allows");

/// Which of the two slots.
enum class Slot : std::uint8_t {
    A = 0,
    B = 1,
};

inline constexpr std::uint32_t slot_offset(Slot slot) {
    return slot == Slot::A ? kConfigAOffset : kConfigBOffset;
}

inline constexpr Slot other_slot(Slot slot) {
    return slot == Slot::A ? Slot::B : Slot::A;
}

}  // namespace duo_input::storage
