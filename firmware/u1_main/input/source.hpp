#pragma once

// The boundary InputPipeline is fed across, whatever is plugged in and
// whatever reads it.
//
// A controller - CH375 today, a PIO-USB/TinyUSB host next - says a device
// attached, was configured, sent this many bytes, went away. Everything above
// this line reads that through one shape, so a second backend does not
// duplicate InputPipeline's state machine and cannot change what an event
// above it means.
//
// The shape is fixed-capacity on purpose. This runs on a chip with no
// allocator: a report is copied into bounded storage once, by whichever
// backend produced it, and nothing downstream holds a pointer into memory a
// USB callback owns. A report that would not fit is a device this firmware
// cannot read, not a reason to grow the buffer.

#include <cstddef>
#include <cstdint>
#include <type_traits>

#include "input/hid/report_descriptor.hpp"
#include "input/source_inventory.hpp"

namespace duo_input::u1::input {

/// What a source turned out to be. Independent of how it was found -
/// CH375's descriptor walk and a PIO-USB host both resolve to one of these.
enum class DeviceKind : std::uint8_t {
    Unknown,
    Keyboard,
    Mouse,
    Consumer,
};

/// The most a source event's report can carry.
///
/// CH375's own command buffer is 64 bytes and nothing this firmware routes -
/// boot protocol, or a report descriptor's own layout - is read past that. A
/// backend that produced more would be a device this firmware cannot read,
/// not a reason to widen the boundary.
inline constexpr std::size_t kMaxSourceReportBytes = 64;
/// A Fault with this source ID invalidates the whole host, including every interface.
inline constexpr std::uint8_t kWholeHostSource = 0xFF;

enum class SourceEventKind : std::uint8_t {
    /// The source has been identified and configured; its reports are coming.
    Ready,
    /// A report arrived on the primary endpoint/instance.
    Report,
    /// A report arrived on a secondary endpoint/instance of the same source -
    /// serviced so it cannot block the primary one. SourceTable reads it
    /// through the layout of the interface identified by source_id.
    AuxiliaryReport,
    /// The source went away. Whatever it was holding must be released.
    Detached,
    /// The backend gave up on this source. Same obligation as Detached: what
    /// it was holding must be released, because nothing will tell the far
    /// side otherwise.
    Fault,
};

/// One thing a source did, copied out of backend-owned memory before it
/// crosses this boundary.
///
/// Defaults to Fault: a SourceEvent nobody finished filling in must read as
/// the safe case - release what is held - rather than as a report that was
/// never actually read.
struct SourceEvent {
    SourceEventKind kind = SourceEventKind::Fault;
    /// Which source this came from. A backend's own choice of numbering; the
    /// pipeline never interprets it, only carries it.
    std::uint8_t source_id = 0;
    /// The endpoint a report arrived on (CH375), or the analogous instance
    /// number a later backend uses to tell one interrupt-IN pipe from
    /// another. Zero for Ready, Detached and Fault.
    std::uint8_t endpoint = 0;
    /// When this report was read out of the controller, in microseconds on
    /// the device's own clock. Zero for everything that is not a report.
    std::uint32_t received_us = 0;
    std::uint8_t report[kMaxSourceReportBytes] = {};
    std::size_t report_size = 0;
};

/// What Ready says the source is, told once when it has been configured.
///
/// Carries everything InputPipeline needs to read that source's reports and
/// everything it needs to tell devices apart by more than a USB address: the
/// VID/PID a compatibility matrix is written against, the layout the source's
/// own report descriptor declared (or boot protocol's, for one that would not
/// give it up), and the descriptor's hash - the one thing that tells two
/// devices sharing a VID and PID apart.
struct SourceIdentity {
    std::uint8_t device_address = 0;
    char product_name[kProductNameBytes] = {};
    DeviceKind kind = DeviceKind::Unknown;
    std::uint16_t vendor_id = 0;
    std::uint16_t product_id = 0;
    /// This interface's number within its device's configuration. Together
    /// with the VID and PID it is what a saved binding matches on.
    std::uint8_t interface_number = 0;
    hid::KeyboardReportLayout keyboard_layout{};
    hid::MouseReportLayout mouse_layout{};
    /// SHA-256 of the report descriptor the source gave up, or all zeros if
    /// none was read - a keyboard, or a mouse that declined.
    std::uint8_t descriptor_hash[32] = {};
};

static_assert(sizeof(SourceEvent::report) == kMaxSourceReportBytes,
             "a source report must not exceed the fixed boundary size");
static_assert(kMaxSourceReportBytes <= 64, "the boundary report is at most 64 bytes");

// Every field is a value - no pointer, reference or virtual function - so a
// SourceEvent or SourceIdentity can only ever carry a copy of what a backend
// read, never a handle back into memory the backend still owns. A type with
// hidden ownership (a destructor, a pointer member with custom copy
// semantics) would fail this trivially-copyable check.
static_assert(std::is_trivially_copyable<SourceEvent>::value,
             "SourceEvent must cross the boundary by value, never by reference");
static_assert(std::is_trivially_copyable<SourceIdentity>::value,
             "SourceIdentity must cross the boundary by value, never by reference");

}  // namespace duo_input::u1::input
