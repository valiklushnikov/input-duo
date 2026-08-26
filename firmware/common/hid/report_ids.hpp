#pragma once

// How the two boards lay out their USB interfaces and endpoints.
//
// There are no report IDs here, and that is deliberate. The specification asks
// for a *boot* keyboard, and boot protocol reports carry no report ID at all -
// a BIOS or a UEFI setup screen reads the first eight bytes of the report and
// nothing else. Sharing one HID interface between keyboard, mouse and consumer
// would force report IDs onto all three and quietly cost boot compatibility,
// which is exactly the situation where a keyboard has to work.
//
// So each of the three gets its own interface with a single, unprefixed
// report. It costs two extra endpoints on a chip that has sixteen.

#include <cstdint>

namespace duo_input::hid {

/// Report IDs, spelled out so their absence is a decision rather than an
/// oversight.
inline constexpr std::uint8_t kNoReportId = 0;

/// Interfaces on U1, in descriptor order.
enum class U1Interface : std::uint8_t {
    Keyboard = 0,
    Mouse = 1,
    Consumer = 2,
    CdcControl = 3,
    CdcData = 4,
    Count = 5,
};

/// Interfaces on U2. The same HID set, and no CDC: U2 is not configurable, and
/// offering a serial port on it would invite someone to try.
enum class U2Interface : std::uint8_t {
    Keyboard = 0,
    Mouse = 1,
    Consumer = 2,
    Count = 3,
};

/// Endpoint addresses. The 0x80 bit means device-to-host.
inline constexpr std::uint8_t kEpKeyboardIn = 0x81;
inline constexpr std::uint8_t kEpMouseIn = 0x82;
inline constexpr std::uint8_t kEpConsumerIn = 0x83;
inline constexpr std::uint8_t kEpCdcNotifyIn = 0x84;
inline constexpr std::uint8_t kEpCdcDataOut = 0x05;
inline constexpr std::uint8_t kEpCdcDataIn = 0x85;

/// Poll intervals in milliseconds. Input is polled every frame; the consumer
/// controls and the CDC notification pipe do not need to be.
inline constexpr std::uint8_t kInputPollIntervalMs = 1;
inline constexpr std::uint8_t kConsumerPollIntervalMs = 10;
inline constexpr std::uint8_t kCdcNotifyIntervalMs = 16;

/// Sizes of the HID IN endpoints.
inline constexpr std::uint16_t kKeyboardReportSize = 8;
inline constexpr std::uint16_t kMouseReportSize = 8;
inline constexpr std::uint16_t kConsumerReportSize = 8;

}  // namespace duo_input::hid
