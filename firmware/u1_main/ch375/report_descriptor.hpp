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
// What comes out is narrow on purpose: byte offsets and widths for the four
// fields the normalizer reads. A layout that cannot be said in those terms -
// an axis on a bit boundary, a twelve-bit field - is refused by name, and the
// caller keeps such a device on the path it already worked on.

#include <cstddef>
#include <cstdint>

#include "protocol/bytes.hpp"

namespace duo_input::u1::ch375 {

/// Where one field sits in a report, once the Report ID has been taken off.
struct ReportField {
    bool present = false;
    /// Bytes from the start of the report body, which is the byte after the
    /// Report ID on a device that sends one and the first byte on one that
    /// does not.
    std::uint8_t offset = 0;
    std::uint8_t bytes = 0;
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

enum class ReportDescriptorError : std::uint8_t {
    None,
    /// An item runs past the end of what arrived, or nothing arrived.
    Truncated,
    /// Nothing in here reports X and Y, so there is no mouse to be found.
    NoMouseReport,
    /// A mouse, but not one whose fields can be named as whole bytes.
    UnsupportedLayout,
};

/// Walk a HID report descriptor and find the mouse report inside it.
///
/// Returns ReportDescriptorError::None and fills ``out`` on success; on any
/// failure ``out`` is left exactly as the caller had it, so a refused device
/// cannot half-replace a layout that was working.
ReportDescriptorError parse_mouse_report_descriptor(protocol::ByteView descriptor,
                                                    MouseReportLayout& out);

}  // namespace duo_input::u1::ch375
