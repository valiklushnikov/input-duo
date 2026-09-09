#pragma once

// Where a mouse keeps its wheel, according to the mouse.
//
// The configuration descriptor says a device is a mouse and which endpoint it
// speaks on. It does not say what is inside a report. Only the HID report
// descriptor does - a separate descriptor, fetched with its own request (HID
// 1.11 7.1.1, type 0x22), and the only place a Report ID or a wheel byte is
// ever declared.
//
// Without it the firmware has one layout it can be sure of: boot protocol's,
// which is buttons, dX and dY and nothing else. Forcing every mouse into that
// is what made the pointer work and the wheel dead.
//
// These bytes come from a stranger. Each item carries its own size in the low
// two bits of its prefix, where a stored 3 means four bytes rather than three
// (HID 1.11 6.2.2.2), and a walk that trusts those sizes walks out of the
// buffer. Every step here is measured against what is left, and a descriptor
// that does not add up is refused whole rather than partly believed.
//
// What comes out is narrow on purpose: bit spans for the four fields the
// normalizer reads. Fields up to sixteen bits may cross a byte boundary; a
// wider value or a report that cannot be named safely is refused whole, and
// the caller keeps such a device on the path it already worked on.

#include <cstddef>
#include <cstdint>
#include <type_traits>

#include "protocol/bytes.hpp"

namespace duo_input::u1::input::hid {

/// Where one field sits in a report, once the Report ID has been taken off.
struct ReportField {
    bool present = false;
    /// First byte touched, from the start of the report body.
    std::uint8_t offset = 0;
    /// Number of bytes touched. A packed 12-bit field can touch two bytes.
    std::uint8_t bytes = 0;
    /// First bit inside ``offset``. Zero for every byte-aligned field.
    std::uint8_t bit_offset = 0;
    /// Declared width. Zero preserves byte-wide layouts built by old callers.
    std::uint8_t bits = 0;
};

/// The four fields a mouse report has to give up before it can be routed.
struct MouseReportLayout {
    /// Every report from this device is prefixed by an identifier byte.
    bool report_id = false;
    /// Which identifier the mouse's own reports carry. A device with media
    /// keys on it sends other reports down the same endpoint, and they are not
    /// movement.
    std::uint8_t report_id_value = 0;

    ReportField buttons;
    ReportField x;
    ReportField y;
    ReportField wheel;
    ReportField pan;

    /// The shortest body that still carries buttons, X and Y.
    ///
    /// The wheel and the pan are deliberately not counted: a boot report is
    /// three bytes and a device that sends a fourth is read for a wheel, which
    /// is the behaviour that was already here and must not change under a
    /// device that declines to describe itself.
    std::uint8_t minimum_body_bytes = 0;
};

/// The layout of a boot-protocol mouse report, and the assumption every mouse
/// has been read under so far.
MouseReportLayout boot_mouse_layout();

enum class KeyboardFieldKind : std::uint8_t { None, Array, Bitmap };
inline constexpr std::uint16_t kNoKeyboardBit = 0xFFFF;
struct KeyboardReportLayout {
    bool consumer = false;
    /// Explicit bitmap usages; zero count means the contiguous range below.
    std::uint8_t explicit_usage_count = 0;
    std::uint16_t explicit_usages[16] = {};
    bool report_id = false;
    std::uint8_t report_id_value = 0;
    std::uint16_t modifier_bits[8] = {
        kNoKeyboardBit, kNoKeyboardBit, kNoKeyboardBit, kNoKeyboardBit,
        kNoKeyboardBit, kNoKeyboardBit, kNoKeyboardBit, kNoKeyboardBit,
    };
    KeyboardFieldKind key_kind = KeyboardFieldKind::None;
    std::uint16_t key_bit_offset = 0;
    std::uint8_t key_element_bits = 0;
    std::uint8_t key_element_count = 0;
    std::uint16_t key_usage_minimum = 0;
    std::uint16_t key_usage_maximum = 0;
    std::uint8_t minimum_body_bytes = 0;
};

/// The explicit eight-byte boot-protocol keyboard report layout.
KeyboardReportLayout boot_keyboard_layout();

enum class ReportDescriptorError : std::uint8_t {
    None,
    /// An item runs past the end of what arrived, or nothing arrived.
    Truncated,
    /// Nothing in here reports X and Y, so there is no mouse to be found.
    NoMouseReport,
    /// A mouse, but not one whose fields fit the bounded bit reader.
    UnsupportedLayout,
    NoKeyboardReport,
    AmbiguousKeyboardReport,
    MalformedGlobalState,
    AmbiguousReportSet,
};

enum class ReportRole : std::uint8_t { Keyboard = 1, Consumer = 2, Mouse = 3 };

struct HidReportEntry {
    ReportRole role = ReportRole::Keyboard;
    std::uint8_t report_id = 0;
    KeyboardReportLayout keyboard{};
    MouseReportLayout mouse{};
};

struct RejectedReportEntry {
    ReportRole role = ReportRole::Keyboard;
    std::uint8_t report_id = 0;
    ReportDescriptorError reason = ReportDescriptorError::UnsupportedLayout;
};

inline constexpr std::size_t kMaxHidReportEntries = 8;
inline constexpr std::size_t kMaxRejectedReportEntries = 8;

struct HidReportSet {
    bool uses_report_ids = false;
    std::uint8_t count = 0;
    HidReportEntry entries[kMaxHidReportEntries] = {};
    std::uint8_t rejected_count = 0;
    RejectedReportEntry rejected[kMaxRejectedReportEntries] = {};
    std::uint8_t rejected_overflow = 0;
};

static_assert(std::is_trivially_copyable<HidReportSet>::value,
              "a HID report set crosses cores only by value");

/// Walk one HID report descriptor and retain every independently decodable
/// input layout in descriptor order. Candidate failures are recorded without
/// discarding other Report IDs. Structural failures leave ``out`` unchanged.
ReportDescriptorError parse_hid_report_set(protocol::ByteView descriptor,
                                           HidReportSet& out);

/// The one supported report role exposed by a descriptor, if there is one.
enum class ReportDescriptorRole : std::uint8_t {
    None,
    Keyboard,
    Mouse,
    Ambiguous,
};

/// Walk a HID report descriptor and find the mouse report inside it.
///
/// Returns ReportDescriptorError::None and fills ``out`` on success; on any
/// failure ``out`` is left exactly as the caller had it, so a refused device
/// cannot half-replace a layout that was working.
ReportDescriptorError parse_mouse_report_descriptor(protocol::ByteView descriptor,
                                                    MouseReportLayout& out);

/// Walk a HID report descriptor and find one bounded keyboard input report.
///
/// A failure never changes ``out``.
ReportDescriptorError parse_keyboard_report_descriptor(protocol::ByteView descriptor,
                                                       KeyboardReportLayout& out);
ReportDescriptorError parse_consumer_report_descriptor(protocol::ByteView descriptor,
                                                       KeyboardReportLayout& out);

/// Try both bounded parsers and retain a layout only when exactly one role is
/// supported. This is the neutral composition shared by USB host backends;
/// neither parser's implementation is copied or specialized by transport.
ReportDescriptorRole classify_report_descriptor(protocol::ByteView descriptor,
                                                KeyboardReportLayout& keyboard,
                                                MouseReportLayout& mouse);

}  // namespace duo_input::u1::input::hid
