// Reading a mouse's HID report descriptor, which is the only thing that says
// where its wheel is.
//
// The configuration descriptor says a device is a mouse and which endpoint its
// reports arrive on. It does not say what is inside a report. That is the
// report descriptor's job, and until it is read the only layout this firmware
// can be sure of is boot protocol's - buttons, dX, dY, and no wheel at all.
//
// These bytes come from a stranger too. Items carry their own sizes, a size
// of three means four bytes rather than three (HID 1.11 6.2.2.2), and a walk
// that believes what it is told walks out of the buffer. Every item here is
// measured against what is left.
//
// What comes out is deliberately narrow: bounded bit spans for the four fields
// the normalizer reads. A layout that cannot be expressed that way is refused
// by name, and a refused layout is one the caller keeps out of report protocol.

#include "crypto/sha256.hpp"
#include "input/hid/report_descriptor.hpp"
#include "test_support.hpp"

#include <cstring>
#include <cstdint>
#include <fstream>
#include <iterator>
#include <string>
#include <type_traits>
#include <vector>

using duo_input::u1::input::hid::boot_keyboard_layout;
using duo_input::u1::input::hid::boot_mouse_layout;
using duo_input::u1::input::hid::HidReportEntry;
using duo_input::u1::input::hid::HidReportSet;
using duo_input::u1::input::hid::KeyboardFieldKind;
using duo_input::u1::input::hid::KeyboardReportLayout;
using duo_input::u1::input::hid::MouseReportLayout;
using duo_input::u1::input::hid::parse_hid_report_set;
using duo_input::u1::input::hid::parse_keyboard_report_descriptor;
using duo_input::u1::input::hid::parse_mouse_report_descriptor;
using duo_input::u1::input::hid::RejectedReportEntry;
using duo_input::u1::input::hid::ReportDescriptorError;
using duo_input::u1::input::hid::ReportRole;
using duo_input::u1::input::hid::kMaxHidReportEntries;
using duo_input::u1::input::hid::kMaxRejectedReportEntries;

constexpr HidReportSet kEmptyReportSet{};
static_assert(!kEmptyReportSet.uses_report_ids);
static_assert(kEmptyReportSet.count == 0);
static_assert(kEmptyReportSet.rejected_count == 0);
static_assert(kEmptyReportSet.rejected_overflow == 0);
static_assert(std::extent<decltype(HidReportSet::entries)>::value ==
              kMaxHidReportEntries);
static_assert(std::extent<decltype(HidReportSet::rejected)>::value ==
              kMaxRejectedReportEntries);
static_assert(std::is_trivially_copyable<HidReportSet>::value,
              "a HID report set crosses cores only by value");

constexpr bool report_set_values_copy_without_aliasing() {
    HidReportSet original{};
    original.entries[0].report_id = 3;
    original.entries[0].mouse.x.offset = 1;
    original.rejected[0].report_id = 4;

    HidReportSet copy = original;
    copy.entries[0].report_id = 5;
    copy.entries[0].mouse.x.offset = 9;
    copy.rejected[0].report_id = 6;

    return original.entries[0].report_id == 3 &&
           original.entries[0].mouse.x.offset == 1 &&
           original.rejected[0].report_id == 4;
}
static_assert(report_set_values_copy_without_aliasing());

TEST_CASE(the_bounded_report_set_copies_values_without_aliasing) {
    HidReportSet original{};
    original.uses_report_ids = true;
    original.count = 1;
    original.entries[0].role = ReportRole::Mouse;
    original.entries[0].report_id = 3;
    original.entries[0].mouse.x.present = true;
    original.rejected_count = 1;
    original.rejected[0].role = ReportRole::Consumer;
    original.rejected[0].report_id = 4;
    original.rejected[0].reason = ReportDescriptorError::AmbiguousReportSet;

    HidReportSet copy = original;
    copy.entries[0].report_id = 5;
    copy.entries[0].mouse.x.offset = 9;
    copy.rejected[0].report_id = 6;
    copy.rejected[0].reason = ReportDescriptorError::UnsupportedLayout;

    CHECK_EQ(original.entries[0].report_id, std::uint8_t{3});
    CHECK_EQ(original.entries[0].mouse.x.offset, std::uint8_t{0});
    CHECK_EQ(original.rejected[0].report_id, std::uint8_t{4});
    CHECK_EQ(original.rejected[0].reason,
             ReportDescriptorError::AmbiguousReportSet);
}

namespace {

duo_input::protocol::ByteView view(const std::vector<std::uint8_t>& bytes) {
    return duo_input::protocol::ByteView{bytes.data(), bytes.size()};
}

void check_entry(const HidReportEntry& entry,
                 ReportRole role,
                 std::uint8_t report_id,
                 std::uint8_t minimum_body_bytes) {
    CHECK_EQ(entry.role, role);
    CHECK_EQ(entry.report_id, report_id);
    if (role == ReportRole::Mouse) {
        CHECK_EQ(entry.mouse.minimum_body_bytes, minimum_body_bytes);
    } else {
        CHECK_EQ(entry.keyboard.minimum_body_bytes, minimum_body_bytes);
    }
}

std::vector<std::uint8_t> read_strict_uppercase_hex(const std::string& path) {
    std::ifstream stream(path, std::ios::binary);
    const std::string text{std::istreambuf_iterator<char>{stream},
                           std::istreambuf_iterator<char>{}};
    if (!stream.is_open() || text.size() < 3 || text.back() != '\n' ||
        ((text.size() - 1) % 2) != 0) {
        return {};
    }

    auto nibble = [](char digit) -> int {
        if (digit >= '0' && digit <= '9') {
            return digit - '0';
        }
        if (digit >= 'A' && digit <= 'F') {
            return digit - 'A' + 10;
        }
        return -1;
    };

    std::vector<std::uint8_t> bytes;
    bytes.reserve((text.size() - 1) / 2);
    for (std::size_t at = 0; at + 1 < text.size() - 1; at += 2) {
        const int high = nibble(text[at]);
        const int low = nibble(text[at + 1]);
        if (high < 0 || low < 0) {
            return {};
        }
        bytes.push_back(static_cast<std::uint8_t>((high << 4) | low));
    }
    return bytes;
}

/// The report descriptor a plain three-button wheel mouse sends.
///
/// This is the layout HID 1.11 Appendix E.10 gives as the worked example and
/// that countless mice ship verbatim: five button bits, three bits of padding,
/// then X, Y and Wheel as signed bytes. It produces exactly the boot report
/// with a wheel byte after it, which is why boot protocol is a safe fallback
/// for a device shaped like this and a lossy one - the wheel is the loss.
std::vector<std::uint8_t> plain_wheel_mouse() {
    return {
        0x05, 0x01,        // Usage Page (Generic Desktop)
        0x09, 0x02,        // Usage (Mouse)
        0xA1, 0x01,        // Collection (Application)
        0x09, 0x01,        //   Usage (Pointer)
        0xA1, 0x00,        //   Collection (Physical)
        0x05, 0x09,        //     Usage Page (Button)
        0x19, 0x01,        //     Usage Minimum (Button 1)
        0x29, 0x05,        //     Usage Maximum (Button 5)
        0x15, 0x00,        //     Logical Minimum (0)
        0x25, 0x01,        //     Logical Maximum (1)
        0x95, 0x05,        //     Report Count (5)
        0x75, 0x01,        //     Report Size (1)
        0x81, 0x02,        //     Input (Data,Var,Abs)
        0x95, 0x01,        //     Report Count (1)
        0x75, 0x03,        //     Report Size (3)
        0x81, 0x03,        //     Input (Cnst,Var,Abs) - padding to a byte
        0x05, 0x01,        //     Usage Page (Generic Desktop)
        0x09, 0x30,        //     Usage (X)
        0x09, 0x31,        //     Usage (Y)
        0x09, 0x38,        //     Usage (Wheel)
        0x15, 0x81,        //     Logical Minimum (-127)
        0x25, 0x7F,        //     Logical Maximum (127)
        0x75, 0x08,        //     Report Size (8)
        0x95, 0x03,        //     Report Count (3)
        0x81, 0x06,        //     Input (Data,Var,Rel)
        0xC0,              //   End Collection
        0xC0,              // End Collection
    };
}

/// The report descriptor of a mouse that leads its reports with an identifier.
///
/// SYNTHETIC. It is written to the shape the bench's mouse must have - seven
/// byte packets, a Report ID, and a wheel - not copied off that device, which
/// has never been asked for its report descriptor. One identifier byte, one
/// button byte, sixteen-bit X and Y, one wheel byte: 1 + 1 + 2 + 2 + 1 = 7.
///
/// Sixteen-bit axes are what make this different from the one above rather
/// than a copy of it with an extra byte in front, and they are what a modern
/// high-resolution mouse actually sends.
std::vector<std::uint8_t> report_id_wheel_mouse() {
    return {
        0x05, 0x01,        // Usage Page (Generic Desktop)
        0x09, 0x02,        // Usage (Mouse)
        0xA1, 0x01,        // Collection (Application)
        0x85, 0x01,        //   Report ID (1)
        0x09, 0x01,        //   Usage (Pointer)
        0xA1, 0x00,        //   Collection (Physical)
        0x05, 0x09,        //     Usage Page (Button)
        0x19, 0x01,        //     Usage Minimum (Button 1)
        0x29, 0x05,        //     Usage Maximum (Button 5)
        0x15, 0x00,        //     Logical Minimum (0)
        0x25, 0x01,        //     Logical Maximum (1)
        0x75, 0x01,        //     Report Size (1)
        0x95, 0x05,        //     Report Count (5)
        0x81, 0x02,        //     Input (Data,Var,Abs)
        0x75, 0x03,        //     Report Size (3)
        0x95, 0x01,        //     Report Count (1)
        0x81, 0x01,        //     Input (Cnst) - padding to a byte
        0x05, 0x01,        //     Usage Page (Generic Desktop)
        0x09, 0x30,        //     Usage (X)
        0x09, 0x31,        //     Usage (Y)
        0x16, 0x01, 0xF8,  //     Logical Minimum (-2047)
        0x26, 0xFF, 0x07,  //     Logical Maximum (2047)
        0x75, 0x10,        //     Report Size (16)
        0x95, 0x02,        //     Report Count (2)
        0x81, 0x06,        //     Input (Data,Var,Rel)
        0x09, 0x38,        //     Usage (Wheel)
        0x15, 0x81,        //     Logical Minimum (-127)
        0x25, 0x7F,        //     Logical Maximum (127)
        0x75, 0x08,        //     Report Size (8)
        0x95, 0x01,        //     Report Count (1)
        0x81, 0x06,        //     Input (Data,Var,Rel)
        0xC0,              //   End Collection
        0xC0,              // End Collection
    };
}

}  // namespace

// ------------------------------------------------------- the boot fallback

TEST_CASE(the_boot_layout_is_the_one_the_normalizer_already_assumed) {
    const MouseReportLayout layout = boot_mouse_layout();

    // Buttons, dX, dY - and, for the devices that send a fourth byte, a wheel
    // where a boot report would put one. This is not a new decision; it is
    // what the normalizer has always read, written down so a descriptor path
    // can replace it rather than silently differ from it.
    CHECK_FALSE(layout.report_id);
    CHECK(layout.buttons.present);
    CHECK_EQ(layout.buttons.offset, std::uint8_t{0});
    CHECK_EQ(layout.buttons.bytes, std::uint8_t{1});
    CHECK(layout.x.present);
    CHECK_EQ(layout.x.offset, std::uint8_t{1});
    CHECK_EQ(layout.x.bytes, std::uint8_t{1});
    CHECK(layout.y.present);
    CHECK_EQ(layout.y.offset, std::uint8_t{2});
    CHECK(layout.wheel.present);
    CHECK_EQ(layout.wheel.offset, std::uint8_t{3});
    CHECK(layout.pan.present);
    CHECK_EQ(layout.pan.offset, std::uint8_t{4});
    // Three bytes is a whole boot report. The wheel and the pan are read only
    // when a device sends far enough to carry them.
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{3});
}

// ------------------------------------------------ a descriptor with no ID

TEST_CASE(a_mouse_without_a_report_id_lands_on_the_boot_offsets) {
    const std::vector<std::uint8_t> bytes = plain_wheel_mouse();
    MouseReportLayout layout;

    CHECK_EQ(static_cast<int>(parse_mouse_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::None));

    CHECK_FALSE(layout.report_id);
    CHECK_EQ(layout.buttons.offset, std::uint8_t{0});
    CHECK_EQ(layout.buttons.bytes, std::uint8_t{1});
    CHECK_EQ(layout.x.offset, std::uint8_t{1});
    CHECK_EQ(layout.x.bytes, std::uint8_t{1});
    CHECK_EQ(layout.y.offset, std::uint8_t{2});
    CHECK_EQ(layout.y.bytes, std::uint8_t{1});
    CHECK(layout.wheel.present);
    CHECK_EQ(layout.wheel.offset, std::uint8_t{3});
    CHECK_EQ(layout.wheel.bytes, std::uint8_t{1});
    CHECK_FALSE(layout.pan.present);
    // Buttons, X and Y and no further: the wheel is read when the report
    // reaches it, exactly as a boot report's fourth byte is.
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{3});
}

// --------------------------------------------- a descriptor with a Report ID

TEST_CASE(a_report_id_is_detected_and_the_fields_sit_behind_it) {
    const std::vector<std::uint8_t> bytes = report_id_wheel_mouse();
    MouseReportLayout layout;

    CHECK_EQ(static_cast<int>(parse_mouse_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::None));

    // The identifier itself, so a report carrying a different one - the
    // consumer collection's, say - can be told apart rather than parsed as a
    // mouse movement.
    CHECK(layout.report_id);
    CHECK_EQ(layout.report_id_value, std::uint8_t{1});

    // Every offset below is measured from the byte after the identifier.
    // Measured from the start of the report they would all be one late, which
    // is precisely the bug that made a click out of every movement.
    CHECK_EQ(layout.buttons.offset, std::uint8_t{0});
    CHECK_EQ(layout.buttons.bytes, std::uint8_t{1});
    CHECK_EQ(layout.x.offset, std::uint8_t{1});
    CHECK_EQ(layout.x.bytes, std::uint8_t{2});
    CHECK_EQ(layout.y.offset, std::uint8_t{3});
    CHECK_EQ(layout.y.bytes, std::uint8_t{2});
    CHECK(layout.wheel.present);
    CHECK_EQ(layout.wheel.offset, std::uint8_t{5});
    CHECK_EQ(layout.wheel.bytes, std::uint8_t{1});
    // One button byte and two sixteen-bit axes. The wheel sits past that and
    // is optional, the same as it is under boot.
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{5});
}

// ----------------------------------------------------- a horizontal wheel

TEST_CASE(a_horizontal_wheel_is_found_on_the_consumer_page) {
    // AC Pan is usage 0x0238 on the Consumer page, and it is how every mouse
    // with a tilting wheel reports sideways scrolling. It is a two-byte usage,
    // so a parser that only reads one-byte usage items misses it entirely.
    std::vector<std::uint8_t> bytes = plain_wheel_mouse();
    // Splice the pan in ahead of the closing collections: one more signed byte
    // after the wheel.
    const std::vector<std::uint8_t> pan = {
        0x05, 0x0C,        //     Usage Page (Consumer)
        0x0A, 0x38, 0x02,  //     Usage (AC Pan)
        0x15, 0x81,        //     Logical Minimum (-127)
        0x25, 0x7F,        //     Logical Maximum (127)
        0x75, 0x08,        //     Report Size (8)
        0x95, 0x01,        //     Report Count (1)
        0x81, 0x06,        //     Input (Data,Var,Rel)
    };
    bytes.insert(bytes.end() - 2, pan.begin(), pan.end());

    MouseReportLayout layout;
    CHECK_EQ(static_cast<int>(parse_mouse_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::None));
    CHECK(layout.pan.present);
    CHECK_EQ(layout.pan.offset, std::uint8_t{4});
    CHECK_EQ(layout.pan.bytes, std::uint8_t{1});
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{3});
}

// ------------------------------------------- more than one report in one file

TEST_CASE(the_mouse_report_is_picked_out_of_a_descriptor_that_has_two) {
    // A mouse with media keys on it: two application collections, two report
    // IDs, one endpoint. Reading the offsets out of the wrong one puts the
    // volume control's bits where the buttons should be.
    std::vector<std::uint8_t> bytes = {
        0x05, 0x0C,        // Usage Page (Consumer)
        0x09, 0x01,        // Usage (Consumer Control)
        0xA1, 0x01,        // Collection (Application)
        0x85, 0x02,        //   Report ID (2)
        0x19, 0x00,        //   Usage Minimum (0)
        0x2A, 0x3C, 0x02,  //   Usage Maximum (0x023C)
        0x15, 0x00,        //   Logical Minimum (0)
        0x26, 0x3C, 0x02,  //   Logical Maximum (0x023C)
        0x75, 0x10,        //   Report Size (16)
        0x95, 0x01,        //   Report Count (1)
        0x81, 0x00,        //   Input (Data,Arr,Abs)
        0xC0,              // End Collection
    };
    const std::vector<std::uint8_t> mouse = report_id_wheel_mouse();
    bytes.insert(bytes.end(), mouse.begin(), mouse.end());

    MouseReportLayout layout;
    CHECK_EQ(static_cast<int>(parse_mouse_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::None));

    // The mouse's identifier, not the consumer collection's.
    CHECK(layout.report_id);
    CHECK_EQ(layout.report_id_value, std::uint8_t{1});
    CHECK_EQ(layout.x.offset, std::uint8_t{1});
    CHECK_EQ(layout.x.bytes, std::uint8_t{2});
}

// ------------------------------------------------------------- refusals

TEST_CASE(a_descriptor_with_no_pointer_in_it_is_refused) {
    // The consumer collection on its own. Nothing here reports X and Y, so
    // there is no mouse layout to be had and guessing at one would put
    // arbitrary bytes through as movement.
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x0C,        // Usage Page (Consumer)
        0x09, 0x01,        // Usage (Consumer Control)
        0xA1, 0x01,        // Collection (Application)
        0x85, 0x02,        //   Report ID (2)
        0x75, 0x10,        //   Report Size (16)
        0x95, 0x01,        //   Report Count (1)
        0x81, 0x00,        //   Input (Data,Arr,Abs)
        0xC0,              // End Collection
    };
    MouseReportLayout layout;

    CHECK_EQ(static_cast<int>(parse_mouse_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::NoMouseReport));
}

TEST_CASE(an_item_that_runs_past_the_end_is_refused) {
    std::vector<std::uint8_t> bytes = plain_wheel_mouse();
    // A two-byte item with one byte behind it. Believed, the walk steps
    // outside the buffer and reads whatever is next in memory.
    bytes.push_back(0x16);
    bytes.push_back(0x01);

    MouseReportLayout layout;
    CHECK_EQ(static_cast<int>(parse_mouse_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::Truncated));
}

TEST_CASE(nothing_at_all_is_refused_rather_than_read) {
    MouseReportLayout layout;
    CHECK_EQ(static_cast<int>(
                 parse_mouse_report_descriptor(duo_input::protocol::ByteView{nullptr, 0}, layout)),
             static_cast<int>(ReportDescriptorError::Truncated));

    // A pointer that is not null and no bytes behind it. The two arrive by
    // different routes - a fetch nobody started, and a fetch that completed
    // with nothing in it - and both are a descriptor that was never read.
    // Only the first of them used to be checked here, so the length half of
    // the guard was never exercised at all.
    const std::uint8_t nothing = 0;
    CHECK_EQ(static_cast<int>(
                 parse_mouse_report_descriptor(duo_input::protocol::ByteView{&nothing, 0}, layout)),
             static_cast<int>(ReportDescriptorError::Truncated));
}

TEST_CASE(a_field_that_does_not_start_on_a_byte_is_recorded_in_bits) {
    // Five button bits and then X immediately, with no padding: X starts at
    // bit five and Y at bit thirteen. The byte span alone is ambiguous; the
    // bit offset keeps both values exact.
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x01,        // Usage Page (Generic Desktop)
        0x09, 0x02,        // Usage (Mouse)
        0xA1, 0x01,        // Collection (Application)
        0x05, 0x09,        //   Usage Page (Button)
        0x19, 0x01,        //   Usage Minimum (Button 1)
        0x29, 0x05,        //   Usage Maximum (Button 5)
        0x95, 0x05,        //   Report Count (5)
        0x75, 0x01,        //   Report Size (1)
        0x81, 0x02,        //   Input (Data,Var,Abs)
        0x05, 0x01,        //   Usage Page (Generic Desktop)
        0x09, 0x30,        //   Usage (X)
        0x09, 0x31,        //   Usage (Y)
        0x75, 0x08,        //   Report Size (8)
        0x95, 0x02,        //   Report Count (2)
        0x81, 0x06,        //   Input (Data,Var,Rel)
        0xC0,              // End Collection
    };
    MouseReportLayout layout;

    CHECK_EQ(static_cast<int>(parse_mouse_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::None));
    CHECK_EQ(layout.x.offset, std::uint8_t{0});
    CHECK_EQ(layout.x.bytes, std::uint8_t{2});
    CHECK_EQ(layout.x.bit_offset, std::uint8_t{5});
    CHECK_EQ(layout.x.bits, std::uint8_t{8});
    CHECK_EQ(layout.y.offset, std::uint8_t{1});
    CHECK_EQ(layout.y.bytes, std::uint8_t{2});
    CHECK_EQ(layout.y.bit_offset, std::uint8_t{5});
    CHECK_EQ(layout.y.bits, std::uint8_t{8});
}

TEST_CASE(packed_twelve_bit_axes_are_kept_exactly) {
    // The pair occupies three bytes: X starts at byte one and Y at the high
    // nibble of byte two. This is the shape the real bench mouse uses.
    std::vector<std::uint8_t> bytes = {
        0x05, 0x01,        // Usage Page (Generic Desktop)
        0x09, 0x02,        // Usage (Mouse)
        0xA1, 0x01,        // Collection (Application)
        0x05, 0x09,        //   Usage Page (Button)
        0x19, 0x01,        //   Usage Minimum (Button 1)
        0x29, 0x08,        //   Usage Maximum (Button 8)
        0x95, 0x08,        //   Report Count (8)
        0x75, 0x01,        //   Report Size (1)
        0x81, 0x02,        //   Input (Data,Var,Abs)
        0x05, 0x01,        //   Usage Page (Generic Desktop)
        0x09, 0x30,        //   Usage (X)
        0x09, 0x31,        //   Usage (Y)
        0x75, 0x0C,        //   Report Size (12)
        0x95, 0x02,        //   Report Count (2)
        0x81, 0x06,        //   Input (Data,Var,Rel)
        0xC0,              // End Collection
    };
    MouseReportLayout layout;

    CHECK_EQ(static_cast<int>(parse_mouse_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::None));
    CHECK_EQ(layout.x.offset, std::uint8_t{1});
    CHECK_EQ(layout.x.bytes, std::uint8_t{2});
    CHECK_EQ(layout.x.bit_offset, std::uint8_t{0});
    CHECK_EQ(layout.x.bits, std::uint8_t{12});
    CHECK_EQ(layout.y.offset, std::uint8_t{2});
    CHECK_EQ(layout.y.bytes, std::uint8_t{2});
    CHECK_EQ(layout.y.bit_offset, std::uint8_t{4});
    CHECK_EQ(layout.y.bits, std::uint8_t{12});
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{4});
}

TEST_CASE(the_bench_mouses_real_packed_twelve_bit_descriptor_is_accepted) {
    // Captured byte-for-byte from the attached mouse on 2026-08-30.  Its
    // seven-byte report is ID 1, buttons, two packed signed 12-bit axes,
    // wheel and AC Pan.  Boot protocol hides the last two fields, so this is
    // the vector the wheel repair has to understand rather than approximate.
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x85, 0x01, 0x09, 0x01,
        0xA1, 0x00, 0x05, 0x09, 0x19, 0x01, 0x29, 0x05, 0x15, 0x00,
        0x25, 0x01, 0x95, 0x05, 0x75, 0x01, 0x81, 0x02, 0x95, 0x01,
        0x75, 0x03, 0x81, 0x03, 0x05, 0x01, 0x16, 0x01, 0xF8, 0x26,
        0xFF, 0x07, 0x75, 0x0C, 0x95, 0x02, 0x09, 0x30, 0x09, 0x31,
        0x81, 0x06, 0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x01,
        0x09, 0x38, 0x81, 0x06, 0xC0, 0x05, 0x0C, 0x0A, 0x38, 0x02,
        0x95, 0x01, 0x81, 0x06, 0xC0,
    };
    MouseReportLayout layout;

    CHECK_EQ(static_cast<int>(parse_mouse_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::None));
    CHECK(layout.report_id);
    CHECK_EQ(layout.report_id_value, std::uint8_t{1});
    CHECK_EQ(layout.buttons.offset, std::uint8_t{0});
    CHECK_EQ(layout.x.offset, std::uint8_t{1});
    CHECK_EQ(layout.x.bytes, std::uint8_t{2});
    CHECK_EQ(layout.x.bits, std::uint8_t{12});
    CHECK_EQ(layout.y.offset, std::uint8_t{2});
    CHECK_EQ(layout.y.bytes, std::uint8_t{2});
    CHECK_EQ(layout.y.bit_offset, std::uint8_t{4});
    CHECK_EQ(layout.y.bits, std::uint8_t{12});
    CHECK_EQ(layout.wheel.offset, std::uint8_t{4});
    CHECK_EQ(layout.pan.offset, std::uint8_t{5});
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{4});
}

TEST_CASE(an_axis_wider_than_the_bounded_reader_is_refused_without_half_a_layout) {
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x01,        // Usage Page (Generic Desktop)
        0x09, 0x02,        // Usage (Mouse)
        0xA1, 0x01,        // Collection (Application)
        0x05, 0x09,        //   Usage Page (Button)
        0x19, 0x01, 0x29, 0x08,
        0x75, 0x01, 0x95, 0x08,
        0x81, 0x02,        //   eight button bits
        0x05, 0x01,
        0x09, 0x30, 0x09, 0x31,
        0x75, 0x14,        //   Report Size (20)
        0x95, 0x02,
        0x81, 0x06,
        0xC0,
    };
    MouseReportLayout layout = boot_mouse_layout();

    CHECK_EQ(static_cast<int>(parse_mouse_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::UnsupportedLayout));
    CHECK_FALSE(layout.report_id);
    CHECK_EQ(layout.x.offset, std::uint8_t{1});
    CHECK_EQ(layout.x.bytes, std::uint8_t{1});
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{3});
}

TEST_CASE(a_refused_descriptor_leaves_the_caller_nothing_half_filled) {
    MouseReportLayout layout = boot_mouse_layout();
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01, 0x85, 0x02, 0xC0,
    };

    CHECK(parse_mouse_report_descriptor(view(bytes), layout) != ReportDescriptorError::None);

    // Untouched. A caller that reads the layout after a failure must find what
    // it put there, not half of a device it refused.
    CHECK_FALSE(layout.report_id);
    CHECK_EQ(layout.x.offset, std::uint8_t{1});
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{3});
}

// ------------------------------------------------ a button run in two pieces

namespace {

/// A five-button mouse that declares its buttons in two Input items.
///
/// SYNTHETIC, written to the shape the mouse on the bench must have. Its
/// measured report is `03 00 08 00 FC FF 00 00` - Report ID 3, one button
/// byte, sixteen-bit X and Y, a wheel byte - and buttons 1-3 work through the
/// device while 4 and 5 reach nothing at all.
///
/// Nothing in HID says a run of buttons has to arrive in one Input item.
/// `Usage Minimum 1 / Usage Maximum 3` followed by `Usage Minimum 4 /
/// Usage Maximum 5` describes exactly the same five bits as one run of five,
/// and a parser that keeps only the first item sees a three-button mouse.
std::vector<std::uint8_t> split_button_run_mouse() {
    return {
        0x05, 0x01,        // Usage Page (Generic Desktop)
        0x09, 0x02,        // Usage (Mouse)
        0xA1, 0x01,        // Collection (Application)
        0x85, 0x03,        //   Report ID (3)
        0x09, 0x01,        //   Usage (Pointer)
        0xA1, 0x00,        //   Collection (Physical)
        0x05, 0x09,        //     Usage Page (Button)
        0x15, 0x00,        //     Logical Minimum (0)
        0x25, 0x01,        //     Logical Maximum (1)
        0x75, 0x01,        //     Report Size (1)
        0x19, 0x01,        //     Usage Minimum (Button 1)
        0x29, 0x03,        //     Usage Maximum (Button 3)
        0x95, 0x03,        //     Report Count (3)
        0x81, 0x02,        //     Input (Data,Var,Abs) - bits 0-2
        0x19, 0x04,        //     Usage Minimum (Button 4)
        0x29, 0x05,        //     Usage Maximum (Button 5)
        0x95, 0x02,        //     Report Count (2)
        0x81, 0x02,        //     Input (Data,Var,Abs) - bits 3-4
        0x95, 0x03,        //     Report Count (3)
        0x81, 0x03,        //     Input (Cnst,Var,Abs) - padding to a byte
        0x05, 0x01,        //     Usage Page (Generic Desktop)
        0x09, 0x30,        //     Usage (X)
        0x09, 0x31,        //     Usage (Y)
        0x16, 0x00, 0x80,  //     Logical Minimum (-32768)
        0x26, 0xFF, 0x7F,  //     Logical Maximum (32767)
        0x75, 0x10,        //     Report Size (16)
        0x95, 0x02,        //     Report Count (2)
        0x81, 0x06,        //     Input (Data,Var,Rel)
        0x09, 0x38,        //     Usage (Wheel)
        0x15, 0x81,        //     Logical Minimum (-127)
        0x25, 0x7F,        //     Logical Maximum (127)
        0x75, 0x08,        //     Report Size (8)
        0x95, 0x01,        //     Report Count (1)
        0x81, 0x06,        //     Input (Data,Var,Rel)
        0xC0,              //   End Collection
        0xC0,              // End Collection
    };
}

}  // namespace

TEST_CASE(a_button_run_declared_in_two_input_items_is_one_field) {
    // The whole of the side-button defect is this number. Five bits declared
    // as 3 + 2 have to come out as one five-bit field, because the normalizer
    // masks the button byte to exactly this width: a three-bit field erases
    // buttons 4 and 5 on the way to both consumers, which is what the bench
    // reported.
    const std::vector<std::uint8_t> bytes = split_button_run_mouse();
    MouseReportLayout layout;

    CHECK_EQ(static_cast<int>(parse_mouse_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::None));
    CHECK(layout.report_id);
    CHECK_EQ(layout.report_id_value, std::uint8_t{3});
    CHECK(layout.buttons.present);
    CHECK_EQ(layout.buttons.offset, std::uint8_t{0});
    CHECK_EQ(layout.buttons.bytes, std::uint8_t{1});
    CHECK_EQ(layout.buttons.bit_offset, std::uint8_t{0});
    CHECK_EQ(layout.buttons.bits, std::uint8_t{5});

    // The second Input item widens the buttons without moving anything behind
    // them: the axes are counted from the report, not from the button field,
    // and they were never wrong.
    CHECK_EQ(layout.x.offset, std::uint8_t{1});
    CHECK_EQ(layout.x.bytes, std::uint8_t{2});
    CHECK_EQ(layout.x.bits, std::uint8_t{16});
    CHECK_EQ(layout.y.offset, std::uint8_t{3});
    CHECK_EQ(layout.y.bits, std::uint8_t{16});
    CHECK_EQ(layout.wheel.offset, std::uint8_t{5});
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{5});
}

TEST_CASE(a_second_button_declaration_somewhere_else_does_not_move_the_field) {
    // Only a piece that begins exactly where the previous one ended is the
    // same run. A Button item declared after the axes is a different field -
    // a device's extra keys, a second collection's worth - and taking it would
    // move the buttons to wherever that sits, losing the ones that do work.
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x01,        // Usage Page (Generic Desktop)
        0x09, 0x02,        // Usage (Mouse)
        0xA1, 0x01,        // Collection (Application)
        0x05, 0x09,        //   Usage Page (Button)
        0x19, 0x01,        //   Usage Minimum (Button 1)
        0x29, 0x03,        //   Usage Maximum (Button 3)
        0x75, 0x01,        //   Report Size (1)
        0x95, 0x03,        //   Report Count (3)
        0x81, 0x02,        //   Input (Data,Var,Abs) - bits 0-2
        0x95, 0x05,        //   Report Count (5)
        0x81, 0x03,        //   Input (Cnst,Var,Abs) - padding, bits 3-7
        0x05, 0x01,        //   Usage Page (Generic Desktop)
        0x09, 0x30,        //   Usage (X)
        0x09, 0x31,        //   Usage (Y)
        0x75, 0x08,        //   Report Size (8)
        0x95, 0x02,        //   Report Count (2)
        0x81, 0x06,        //   Input (Data,Var,Rel)
        0x05, 0x09,        //   Usage Page (Button)
        0x19, 0x04,        //   Usage Minimum (Button 4)
        0x29, 0x05,        //   Usage Maximum (Button 5)
        0x75, 0x01,        //   Report Size (1)
        0x95, 0x02,        //   Report Count (2)
        0x81, 0x02,        //   Input (Data,Var,Abs) - bits 24-25
        0xC0,              // End Collection
    };
    MouseReportLayout layout;

    CHECK_EQ(static_cast<int>(parse_mouse_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::None));
    CHECK_EQ(layout.buttons.offset, std::uint8_t{0});
    CHECK_EQ(layout.buttons.bits, std::uint8_t{3});
    CHECK_EQ(layout.x.offset, std::uint8_t{1});
    CHECK_EQ(layout.y.offset, std::uint8_t{2});
}

TEST_CASE(a_button_run_wider_than_one_byte_keeps_the_byte_it_can_read) {
    // Sixteen buttons declared as 8 + 8. A merged sixteen-bit field is one the
    // packed reader refuses outright - to_bytes caps a packed field at eight
    // bits - and refusing takes the axes and the wheel down with it. The first
    // byte is the part this firmware routes, so that is what it keeps.
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x01,        // Usage Page (Generic Desktop)
        0x09, 0x02,        // Usage (Mouse)
        0xA1, 0x01,        // Collection (Application)
        0x05, 0x09,        //   Usage Page (Button)
        0x19, 0x01,        //   Usage Minimum (Button 1)
        0x29, 0x08,        //   Usage Maximum (Button 8)
        0x75, 0x01,        //   Report Size (1)
        0x95, 0x08,        //   Report Count (8)
        0x81, 0x02,        //   Input (Data,Var,Abs) - bits 0-7
        0x19, 0x09,        //   Usage Minimum (Button 9)
        0x29, 0x10,        //   Usage Maximum (Button 16)
        0x95, 0x08,        //   Report Count (8)
        0x81, 0x02,        //   Input (Data,Var,Abs) - bits 8-15
        0x05, 0x01,        //   Usage Page (Generic Desktop)
        0x09, 0x30,        //   Usage (X)
        0x09, 0x31,        //   Usage (Y)
        0x75, 0x08,        //   Report Size (8)
        0x95, 0x02,        //   Report Count (2)
        0x81, 0x06,        //   Input (Data,Var,Rel)
        0xC0,              // End Collection
    };
    MouseReportLayout layout;

    CHECK_EQ(static_cast<int>(parse_mouse_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::None));
    CHECK_EQ(layout.buttons.bits, std::uint8_t{8});
    CHECK_EQ(layout.buttons.bytes, std::uint8_t{1});
    CHECK_EQ(layout.x.offset, std::uint8_t{2});
    CHECK_EQ(layout.y.offset, std::uint8_t{3});
}

// --------------------------------------------------------------- bounding

TEST_CASE(a_descriptor_of_nothing_but_collection_openers_still_terminates) {
    // Two hundred and fifty opening collections and no closes. That this test
    // returns at all is the assertion: the walk is over bytes, not over a
    // nesting depth, so there is nothing here to recurse into.
    std::vector<std::uint8_t> bytes;
    for (int index = 0; index < 250; ++index) {
        bytes.push_back(0xA1);
        bytes.push_back(0x01);
    }
    MouseReportLayout layout;

    CHECK_EQ(static_cast<int>(parse_mouse_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::NoMouseReport));
}

// ------------------------------------------------- what the item sizes mean

TEST_CASE(a_four_byte_usage_item_carries_its_own_page) {
    // HID 1.11 6.2.2.2: a stored size of three means *four* data bytes. A walk
    // that takes it for three loses step at the first one and reads the rest
    // of the descriptor as garbage - and the four-byte Usage item is not an
    // exotic case, it is how a device names a usage on a page other than the
    // global one. Here AC Pan (Consumer page 0x0C, usage 0x0238) is declared
    // that way instead of by switching the global page.
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x01,                    // Usage Page (Generic Desktop)
        0x09, 0x02,                    // Usage (Mouse)
        0xA1, 0x01,                    // Collection (Application)
        0x05, 0x09,                    //   Usage Page (Button)
        0x19, 0x01,                    //   Usage Minimum (Button 1)
        0x29, 0x08,                    //   Usage Maximum (Button 8)
        0x95, 0x08,                    //   Report Count (8)
        0x75, 0x01,                    //   Report Size (1)
        0x81, 0x02,                    //   Input (Data,Var,Abs)
        0x05, 0x01,                    //   Usage Page (Generic Desktop)
        0x09, 0x30,                    //   Usage (X)
        0x09, 0x31,                    //   Usage (Y)
        0x09, 0x38,                    //   Usage (Wheel)
        0x0B, 0x38, 0x02, 0x0C, 0x00,  //   Usage (Consumer page, AC Pan) - four bytes
        0x75, 0x08,                    //   Report Size (8)
        0x95, 0x04,                    //   Report Count (4)
        0x81, 0x06,                    //   Input (Data,Var,Rel)
        0xC0,                          // End Collection
    };
    MouseReportLayout layout;

    CHECK_EQ(static_cast<int>(parse_mouse_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::None));
    CHECK_EQ(layout.x.offset, std::uint8_t{1});
    CHECK_EQ(layout.wheel.offset, std::uint8_t{3});
    CHECK(layout.pan.present);
    CHECK_EQ(layout.pan.offset, std::uint8_t{4});
}

namespace {

/// Axes twelve bits wide, both of them starting on a byte.
///
/// The existing twelve-bit test packs them back to back, which puts Y at bit
/// twenty - so it is refused for not starting on a byte, and the width check
/// behind it is never reached. Four bits of padding between them moves Y to
/// bit twenty-four, and then only the width can refuse this.
std::vector<std::uint8_t> byte_aligned_twelve_bit_axes() {
    return {
        0x05, 0x01,        // Usage Page (Generic Desktop)
        0x09, 0x02,        // Usage (Mouse)
        0xA1, 0x01,        // Collection (Application)
        0x05, 0x09,        //   Usage Page (Button)
        0x19, 0x01,        //   Usage Minimum (Button 1)
        0x29, 0x08,        //   Usage Maximum (Button 8)
        0x95, 0x08,        //   Report Count (8)
        0x75, 0x01,        //   Report Size (1)
        0x81, 0x02,        //   Input (Data,Var,Abs)   buttons, bits 0-7
        0x05, 0x01,        //   Usage Page (Generic Desktop)
        0x09, 0x30,        //   Usage (X)
        0x75, 0x0C,        //   Report Size (12)
        0x95, 0x01,        //   Report Count (1)
        0x81, 0x06,        //   Input (Data,Var,Rel)   X at bit 8
        0x75, 0x04,        //   Report Size (4)
        0x95, 0x01,        //   Report Count (1)
        0x81, 0x03,        //   Input (Cnst,Var,Abs)   padding, bits 20-23
        0x09, 0x31,        //   Usage (Y)
        0x75, 0x0C,        //   Report Size (12)
        0x95, 0x01,        //   Report Count (1)
        0x81, 0x06,        //   Input (Data,Var,Rel)   Y at bit 24
        0xC0,              // End Collection
    };
}

}  // namespace

TEST_CASE(a_twelve_bit_axis_on_a_byte_boundary_is_kept_twelve_bits_wide) {
    const std::vector<std::uint8_t> bytes = byte_aligned_twelve_bit_axes();
    MouseReportLayout layout;

    CHECK_EQ(static_cast<int>(parse_mouse_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::None));
    CHECK_EQ(layout.x.offset, std::uint8_t{1});
    CHECK_EQ(layout.x.bytes, std::uint8_t{2});
    CHECK_EQ(layout.x.bits, std::uint8_t{12});
    CHECK_EQ(layout.y.offset, std::uint8_t{3});
    CHECK_EQ(layout.y.bytes, std::uint8_t{2});
    CHECK_EQ(layout.y.bits, std::uint8_t{12});
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{5});
}

TEST_CASE(an_accepted_packed_layout_replaces_the_callers_fallback) {
    MouseReportLayout layout = boot_mouse_layout();
    const std::vector<std::uint8_t> bytes = byte_aligned_twelve_bit_axes();

    CHECK_EQ(static_cast<int>(parse_mouse_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::None));

    CHECK_FALSE(layout.report_id);
    CHECK_EQ(layout.buttons.offset, std::uint8_t{0});
    CHECK_EQ(layout.x.offset, std::uint8_t{1});
    CHECK_EQ(layout.y.offset, std::uint8_t{3});
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{5});
}

// ---------------------------------------------------------- keyboard layouts

TEST_CASE(the_fixed_boot_keyboard_layout_is_explicit) {
    const KeyboardReportLayout layout = boot_keyboard_layout();

    CHECK_FALSE(layout.report_id);
    CHECK_EQ(layout.report_id_value, std::uint8_t{0});
    for (std::uint16_t modifier = 0; modifier < 8; ++modifier) {
        CHECK_EQ(layout.modifier_bits[modifier], modifier);
    }
    CHECK_EQ(static_cast<int>(layout.key_kind),
             static_cast<int>(KeyboardFieldKind::Array));
    CHECK_EQ(layout.key_bit_offset, std::uint16_t{16});
    CHECK_EQ(layout.key_element_bits, std::uint8_t{8});
    CHECK_EQ(layout.key_element_count, std::uint8_t{6});
    CHECK_EQ(layout.key_usage_minimum, std::uint16_t{0});
    CHECK_EQ(layout.key_usage_maximum, std::uint16_t{0x00FF});
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{8});
}

TEST_CASE(the_captured_aula_keyboard_descriptor_has_one_usable_layout) {
    const std::vector<std::uint8_t> bytes = read_strict_uppercase_hex(
        std::string{DUO_USB_DESCRIPTORS_PATH} + "/aula_f75_keyboard_report.hex");
    CHECK_EQ(bytes.size(), std::size_t{77});
    KeyboardReportLayout layout;

    CHECK_EQ(static_cast<int>(parse_keyboard_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::None));
    CHECK_FALSE(layout.report_id);
    for (std::uint16_t modifier = 0; modifier < 8; ++modifier) {
        CHECK_EQ(layout.modifier_bits[modifier], modifier);
    }
    CHECK_EQ(static_cast<int>(layout.key_kind),
             static_cast<int>(KeyboardFieldKind::Array));
    CHECK_EQ(layout.key_bit_offset, std::uint16_t{16});
    CHECK_EQ(layout.key_element_bits, std::uint8_t{8});
    CHECK_EQ(layout.key_element_count, std::uint8_t{5});
    CHECK_EQ(layout.key_usage_minimum, std::uint16_t{0});
    CHECK_EQ(layout.key_usage_maximum, std::uint16_t{0x00FF});
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{7});
}

TEST_CASE(a_report_id_keyboard_keeps_offsets_inside_its_own_report) {
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x0C, 0x85, 0x01,              // Consumer report ID 1
        0x09, 0x01, 0x75, 0x08, 0x95, 0x04,
        0x81, 0x02,                          // four bytes in report 1
        0x05, 0x07, 0x85, 0x02,              // Keyboard report ID 2
        0x19, 0xE0, 0x29, 0xE7,
        0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
        0x19, 0x00, 0x29, 0x65,
        0x15, 0x00, 0x25, 0x65,
        0x75, 0x08, 0x95, 0x06, 0x81, 0x00,
    };
    KeyboardReportLayout layout;

    CHECK_EQ(static_cast<int>(parse_keyboard_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::None));
    CHECK(layout.report_id);
    CHECK_EQ(layout.report_id_value, std::uint8_t{2});
    CHECK_EQ(layout.modifier_bits[0], std::uint16_t{0});
    CHECK_EQ(layout.modifier_bits[7], std::uint16_t{7});
    CHECK_EQ(layout.key_bit_offset, std::uint16_t{8});
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{7});
}

TEST_CASE(an_eight_byte_array_keyboard_records_modifiers_and_six_slots) {
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x07,
        0x19, 0xE0, 0x29, 0xE7,
        0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
        0x75, 0x08, 0x95, 0x01, 0x81, 0x01,
        0x19, 0x00, 0x29, 0x65,
        0x15, 0x00, 0x25, 0x65,
        0x75, 0x08, 0x95, 0x06, 0x81, 0x00,
    };
    KeyboardReportLayout layout;

    CHECK_EQ(static_cast<int>(parse_keyboard_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::None));
    CHECK_EQ(layout.modifier_bits[0], std::uint16_t{0});
    CHECK_EQ(layout.modifier_bits[7], std::uint16_t{7});
    CHECK_EQ(static_cast<int>(layout.key_kind),
             static_cast<int>(KeyboardFieldKind::Array));
    CHECK_EQ(layout.key_bit_offset, std::uint16_t{16});
    CHECK_EQ(layout.key_element_bits, std::uint8_t{8});
    CHECK_EQ(layout.key_element_count, std::uint8_t{6});
    CHECK_EQ(layout.key_usage_minimum, std::uint16_t{0});
    CHECK_EQ(layout.key_usage_maximum, std::uint16_t{0x65});
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{8});
}

TEST_CASE(an_nkro_bitmap_records_its_usage_range) {
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x07,
        0x19, 0xE0, 0x29, 0xE7,
        0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
        0x19, 0x04, 0x29, 0x73,
        0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x70, 0x81, 0x02,
    };
    KeyboardReportLayout layout;

    CHECK_EQ(static_cast<int>(parse_keyboard_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::None));
    CHECK_EQ(static_cast<int>(layout.key_kind),
             static_cast<int>(KeyboardFieldKind::Bitmap));
    CHECK_EQ(layout.key_bit_offset, std::uint16_t{8});
    CHECK_EQ(layout.key_element_bits, std::uint8_t{1});
    CHECK_EQ(layout.key_element_count, std::uint8_t{0x70});
    CHECK_EQ(layout.key_usage_minimum, std::uint16_t{0x04});
    CHECK_EQ(layout.key_usage_maximum, std::uint16_t{0x73});
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{15});
}

TEST_CASE(global_push_and_pop_restore_keyboard_report_size_and_count) {
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x07,
        0x19, 0xE0, 0x29, 0xE7,
        0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
        0x75, 0x08, 0x95, 0x06,
        0xA4,                                // Push globals
        0x75, 0x01, 0x95, 0x01,
        0xB4,                                // Pop: size 8, count 6
        0x19, 0x00, 0x29, 0x65,
        0x15, 0x00, 0x25, 0x65,
        0x81, 0x00,
    };
    KeyboardReportLayout layout;

    CHECK_EQ(static_cast<int>(parse_keyboard_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::None));
    CHECK_EQ(layout.key_bit_offset, std::uint16_t{8});
    CHECK_EQ(layout.key_element_bits, std::uint8_t{8});
    CHECK_EQ(layout.key_element_count, std::uint8_t{6});
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{7});
}

TEST_CASE(output_feature_and_long_items_do_not_move_the_keyboard_input_cursor) {
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x07,
        0x19, 0xE0, 0x29, 0xE7,
        0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
        0x75, 0x08, 0x95, 0x04,
        0x19, 0x01, 0x29, 0x04, 0x91, 0x02,
        0x19, 0x01, 0x29, 0x04, 0xB1, 0x02,
        0xFE, 0x03, 0x99, 0xDE, 0xAD, 0xBE,
        0x19, 0x00, 0x29, 0x65,
        0x15, 0x00, 0x25, 0x65,
        0x75, 0x08, 0x95, 0x06, 0x81, 0x00,
    };
    KeyboardReportLayout layout;

    CHECK_EQ(static_cast<int>(parse_keyboard_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::None));
    CHECK_EQ(layout.key_bit_offset, std::uint16_t{8});
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{7});
}

TEST_CASE(two_competing_keyboard_input_reports_are_refused) {
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x07,
        0x85, 0x01,
        0x19, 0x00, 0x29, 0x65,
        0x15, 0x00, 0x25, 0x65,
        0x75, 0x08, 0x95, 0x06, 0x81, 0x00,
        0x85, 0x02,
        0x19, 0x04, 0x29, 0x73,
        0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x70, 0x81, 0x02,
    };
    KeyboardReportLayout layout = boot_keyboard_layout();

    CHECK_EQ(static_cast<int>(parse_keyboard_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::AmbiguousKeyboardReport));
    CHECK_FALSE(layout.report_id);
    CHECK_EQ(layout.key_bit_offset, std::uint16_t{16});
    CHECK_EQ(layout.key_element_count, std::uint8_t{6});
}

TEST_CASE(a_truncated_keyboard_item_leaves_the_callers_layout_unchanged) {
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x07,
        0x19, 0x00, 0x29, 0x65,
        0x15, 0x00, 0x25, 0x65,
        0x75, 0x08, 0x95, 0x06,
        0x82, 0x00,                          // two-byte Input, one byte present
    };
    KeyboardReportLayout layout = boot_keyboard_layout();

    CHECK_EQ(static_cast<int>(parse_keyboard_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::Truncated));
    CHECK_EQ(layout.modifier_bits[0], std::uint16_t{0});
    CHECK_EQ(static_cast<int>(layout.key_kind),
             static_cast<int>(KeyboardFieldKind::Array));
    CHECK_EQ(layout.key_bit_offset, std::uint16_t{16});
    CHECK_EQ(layout.key_element_count, std::uint8_t{6});
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{8});
}

TEST_CASE(global_stack_underflow_and_overflow_are_refused) {
    const std::vector<std::uint8_t> underflow = {0xB4};
    const std::vector<std::uint8_t> overflow = {0xA4, 0xA4, 0xA4, 0xA4, 0xA4};
    KeyboardReportLayout layout = boot_keyboard_layout();

    CHECK_EQ(static_cast<int>(parse_keyboard_report_descriptor(view(underflow), layout)),
             static_cast<int>(ReportDescriptorError::MalformedGlobalState));
    CHECK_EQ(static_cast<int>(parse_keyboard_report_descriptor(view(overflow), layout)),
             static_cast<int>(ReportDescriptorError::MalformedGlobalState));
    CHECK_EQ(layout.key_bit_offset, std::uint16_t{16});
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{8});
}

TEST_CASE(a_non_keyboard_descriptor_is_refused_by_name) {
    const std::vector<std::uint8_t> bytes = plain_wheel_mouse();
    KeyboardReportLayout layout = boot_keyboard_layout();

    CHECK_EQ(static_cast<int>(parse_keyboard_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::NoKeyboardReport));
    CHECK_EQ(layout.modifier_bits[0], std::uint16_t{0});
    CHECK_EQ(layout.key_bit_offset, std::uint16_t{16});
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{8});
}

TEST_CASE(unsupported_keyboard_layouts_are_refused_whole) {
    const std::vector<std::vector<std::uint8_t>> descriptors = {
        // Report ID zero is reserved.
        {0x85, 0x00},
        // Array elements wider than the bounded reader.
        {0x05, 0x07, 0x19, 0x00, 0x29, 0x65, 0x15, 0x00, 0x25, 0x65,
         0x75, 0x11, 0x95, 0x01, 0x81, 0x00},
        // Reversed local usage range.
        {0x05, 0x07, 0x19, 0x65, 0x29, 0x04, 0x15, 0x00, 0x25, 0x65,
         0x75, 0x08, 0x95, 0x06, 0x81, 0x00},
        // Reversed global logical range.
        {0x05, 0x07, 0x19, 0x00, 0x29, 0x65, 0x15, 0x01, 0x25, 0x00,
         0x75, 0x08, 0x95, 0x06, 0x81, 0x00},
        // One bit past the 64-byte report ceiling.
        {0x75, 0x08, 0x96, 0x40, 0x00, 0x81, 0x01,
         0x05, 0x07, 0x19, 0x04, 0x29, 0x04, 0x15, 0x00, 0x25, 0x01,
         0x75, 0x01, 0x95, 0x01, 0x81, 0x02},
    };

    for (const std::vector<std::uint8_t>& bytes : descriptors) {
        KeyboardReportLayout layout = boot_keyboard_layout();
        CHECK_EQ(static_cast<int>(parse_keyboard_report_descriptor(view(bytes), layout)),
                 static_cast<int>(ReportDescriptorError::UnsupportedLayout));
        CHECK_EQ(layout.key_bit_offset, std::uint16_t{16});
        CHECK_EQ(layout.key_element_count, std::uint8_t{6});
    }
}

// A descriptor that names Report IDs at all names them for every report it
// declares. A keyboard left in the unnamed report 0 alongside identified ones
// cannot be read: the device prefixes every packet with an identifier, so
// offsets measured without one are all a byte late and no packet ever matches
// identifier 0. Boot protocol is the only honest answer.
TEST_CASE(a_keyboard_report_without_an_id_beside_identified_reports_is_refused) {
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x07,
        0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x08, 0x81, 0x02,  // modifiers, in no named report
        0x19, 0x00, 0x29, 0x65, 0x15, 0x00, 0x25, 0x65,
        0x75, 0x08, 0x95, 0x06, 0x81, 0x00,  // keys, in no named report
        0x85, 0x02,                          // Report ID (2), too late
        0x05, 0x0C, 0x09, 0x01, 0x75, 0x08, 0x95, 0x02, 0x81, 0x02,
    };
    KeyboardReportLayout layout = boot_keyboard_layout();

    CHECK_EQ(static_cast<int>(parse_keyboard_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::UnsupportedLayout));
    CHECK_FALSE(layout.report_id);
    CHECK_EQ(layout.key_bit_offset, std::uint16_t{16});
    CHECK_EQ(layout.key_element_count, std::uint8_t{6});
}

// Two key fields inside one report leave no way to say which one a pressed key
// arrives in. Taking the last one silently discards the first, which is how a
// whole half of a keyboard goes quiet; refusing sends the device to boot.
TEST_CASE(a_second_key_field_in_one_report_is_refused) {
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x07,
        0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
        0x19, 0x00, 0x29, 0x65, 0x15, 0x00, 0x25, 0x65,
        0x75, 0x08, 0x95, 0x06, 0x81, 0x00,  // six slots at bit 8
        0x19, 0x00, 0x29, 0x65, 0x15, 0x00, 0x25, 0x65,
        0x75, 0x08, 0x95, 0x06, 0x81, 0x00,  // six more at bit 56
    };
    KeyboardReportLayout layout = boot_keyboard_layout();

    CHECK_EQ(static_cast<int>(parse_keyboard_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::UnsupportedLayout));
    CHECK_EQ(layout.key_bit_offset, std::uint16_t{16});
}

// The bit ceiling is a property of the whole report, not of the fields read out
// of it. A padding field declared after the keys can push the report past what
// the bounded reader will ever be handed, and the keys found before it are no
// reason to accept the rest.
TEST_CASE(a_report_that_outgrows_sixty_four_bytes_after_its_keys_is_refused) {
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x07,
        0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
        0x19, 0x00, 0x29, 0x65, 0x15, 0x00, 0x25, 0x65,
        0x75, 0x08, 0x95, 0x06, 0x81, 0x00,  // keys end at bit 56
        0x75, 0x08, 0x96, 0x41, 0x00, 0x81, 0x01,  // 65 constant bytes after
    };
    KeyboardReportLayout layout = boot_keyboard_layout();

    CHECK_EQ(static_cast<int>(parse_keyboard_report_descriptor(view(bytes), layout)),
             static_cast<int>(ReportDescriptorError::UnsupportedLayout));
    CHECK_EQ(layout.key_bit_offset, std::uint16_t{16});
    CHECK_EQ(layout.minimum_body_bytes, std::uint8_t{8});
}

// An NKRO bitmap is read by counting bits off its usage minimum, so the field
// has to hold exactly one bit per usage in the declared range. A field that
// holds fewer bits, or wider ones, maps every key past the first onto the wrong
// usage - a descriptor that reports the letter next to the one that was struck.
TEST_CASE(a_bitmap_whose_bit_count_disagrees_with_its_usage_range_is_refused) {
    const std::vector<std::uint8_t> modifiers = {
        0x05, 0x07,
        0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
    };
    const std::vector<std::vector<std::uint8_t>> tails = {
        // 112 usages declared, 96 bits sent.
        {0x19, 0x04, 0x29, 0x73, 0x15, 0x00, 0x25, 0x01,
         0x75, 0x01, 0x95, 0x60, 0x81, 0x02},
        // Eight usages, eight elements - but a byte each, not a bit each.
        {0x19, 0x04, 0x29, 0x0B, 0x15, 0x00, 0x25, 0x01,
         0x75, 0x08, 0x95, 0x08, 0x81, 0x02},
    };

    for (const std::vector<std::uint8_t>& tail : tails) {
        std::vector<std::uint8_t> bytes = modifiers;
        bytes.insert(bytes.end(), tail.begin(), tail.end());
        KeyboardReportLayout layout = boot_keyboard_layout();

        CHECK_EQ(static_cast<int>(parse_keyboard_report_descriptor(view(bytes), layout)),
                 static_cast<int>(ReportDescriptorError::UnsupportedLayout));
        CHECK_EQ(static_cast<int>(layout.key_kind),
                 static_cast<int>(KeyboardFieldKind::Array));
        CHECK_EQ(layout.key_element_bits, std::uint8_t{8});
        CHECK_EQ(layout.key_element_count, std::uint8_t{6});
    }
}

// ------------------------------------------------------- bounded report sets

TEST_CASE(the_captured_keychron_descriptor_keeps_all_supported_reports_in_order) {
    const std::vector<std::uint8_t> bytes = read_strict_uppercase_hex(
        std::string{DUO_USB_DESCRIPTORS_PATH} +
        "/keychron_3434_d030_interface_2_report.hex");
    CHECK_EQ(bytes.size(), std::size_t{164});

    const std::uint8_t expected_digest[duo_input::crypto::kSha256DigestSize] = {
        0x3E, 0x7A, 0x52, 0x26, 0x17, 0x3A, 0x4F, 0xBE,
        0x03, 0xC9, 0x89, 0x34, 0xC6, 0x68, 0x6D, 0x8C,
        0xCD, 0x0D, 0x3A, 0xE3, 0x21, 0xB5, 0xB1, 0xA2,
        0x66, 0xB2, 0x4C, 0x4B, 0x18, 0xCD, 0xDB, 0x28,
    };
    std::uint8_t digest[duo_input::crypto::kSha256DigestSize] = {};
    duo_input::crypto::sha256(bytes.data(), bytes.size(), digest);
    for (std::size_t index = 0; index < duo_input::crypto::kSha256DigestSize;
         ++index) {
        CHECK_EQ(digest[index], expected_digest[index]);
    }

    HidReportSet set;
    CHECK_EQ(parse_hid_report_set(view(bytes), set), ReportDescriptorError::None);
    CHECK(set.uses_report_ids);
    CHECK_EQ(set.count, std::uint8_t{3});
    check_entry(set.entries[0], ReportRole::Keyboard, 1, 8);
    check_entry(set.entries[1], ReportRole::Consumer, 2, 2);
    check_entry(set.entries[2], ReportRole::Keyboard, 12, 20);

    const KeyboardReportLayout& id_1 = set.entries[0].keyboard;
    for (std::uint16_t modifier = 0; modifier < 8; ++modifier) {
        CHECK_EQ(id_1.modifier_bits[modifier], modifier);
    }
    CHECK_EQ(id_1.key_kind, KeyboardFieldKind::Array);
    CHECK_EQ(id_1.key_bit_offset, std::uint16_t{16});
    CHECK_EQ(id_1.key_element_bits, std::uint8_t{8});
    CHECK_EQ(id_1.key_element_count, std::uint8_t{6});
    CHECK_EQ(id_1.key_usage_minimum, std::uint16_t{0});
    CHECK_EQ(id_1.key_usage_maximum, std::uint16_t{0xF1});

    const KeyboardReportLayout& id_12 = set.entries[2].keyboard;
    CHECK_EQ(id_12.key_kind, KeyboardFieldKind::Bitmap);
    CHECK_EQ(id_12.key_bit_offset, std::uint16_t{8});
    CHECK_EQ(id_12.key_element_count, std::uint8_t{0x98});
    CHECK_EQ(id_12.key_usage_minimum, std::uint16_t{0});
    CHECK_EQ(id_12.key_usage_maximum, std::uint16_t{0x98});
}

TEST_CASE(an_unsupported_keyboard_candidate_does_not_hide_a_later_consumer) {
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x07, 0x85, 0x01,
        0x19, 0x00, 0x29, 0x65, 0x15, 0x00, 0x25, 0x65,
        0x75, 0x11, 0x95, 0x01, 0x81, 0x00,
        0x05, 0x0C, 0x85, 0x02,
        0x19, 0x01, 0x29, 0x02, 0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x02, 0x81, 0x02,
    };
    HidReportSet set;

    CHECK_EQ(parse_hid_report_set(view(bytes), set), ReportDescriptorError::None);
    CHECK_EQ(set.count, std::uint8_t{1});
    check_entry(set.entries[0], ReportRole::Consumer, 2, 1);
    CHECK_EQ(set.rejected_count, std::uint8_t{1});
    CHECK_EQ(set.rejected[0].role, ReportRole::Keyboard);
    CHECK_EQ(set.rejected[0].report_id, std::uint8_t{1});
    CHECK_EQ(set.rejected[0].reason, ReportDescriptorError::UnsupportedLayout);
}

TEST_CASE(valid_report_entries_follow_descriptor_order_across_roles) {
    const std::vector<std::uint8_t> bytes = {
        // Consumer ID 9.
        0x05, 0x0C, 0x85, 0x09, 0x19, 0x01, 0x29, 0x02,
        0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x02, 0x81, 0x02,
        // Mouse ID 3.
        0x05, 0x09, 0x85, 0x03, 0x19, 0x01, 0x29, 0x03,
        0x75, 0x01, 0x95, 0x03, 0x81, 0x02,
        0x75, 0x05, 0x95, 0x01, 0x81, 0x01,
        0x05, 0x01, 0x09, 0x30, 0x09, 0x31,
        0x75, 0x08, 0x95, 0x02, 0x81, 0x06,
        // Keyboard ID 7.
        0x05, 0x07, 0x85, 0x07, 0x19, 0x00, 0x29, 0x65,
        0x15, 0x00, 0x25, 0x65, 0x75, 0x08, 0x95, 0x06, 0x81, 0x00,
    };
    HidReportSet set;

    CHECK_EQ(parse_hid_report_set(view(bytes), set), ReportDescriptorError::None);
    CHECK_EQ(set.count, std::uint8_t{3});
    check_entry(set.entries[0], ReportRole::Consumer, 9, 1);
    check_entry(set.entries[1], ReportRole::Mouse, 3, 3);
    check_entry(set.entries[2], ReportRole::Keyboard, 7, 6);
}

TEST_CASE(the_ninth_valid_candidate_never_displaces_the_first_eight) {
    std::vector<std::uint8_t> bytes;
    for (std::uint8_t report_id = 1; report_id <= 8; ++report_id) {
        const std::uint8_t report[] = {
            0x05, 0x0C, 0x85, report_id, 0x19, 0x01, 0x29, 0x01,
            0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x01, 0x81, 0x02,
        };
        bytes.insert(bytes.end(), std::begin(report), std::end(report));
    }
    const std::uint8_t ninth_mouse[] = {
        0x05, 0x09, 0x85, 0x09, 0x19, 0x01, 0x29, 0x03,
        0x75, 0x01, 0x95, 0x03, 0x81, 0x02,
        0x75, 0x05, 0x95, 0x01, 0x81, 0x01,
        0x05, 0x01, 0x09, 0x30, 0x09, 0x31,
        0x75, 0x08, 0x95, 0x02, 0x81, 0x06,
    };
    bytes.insert(bytes.end(), std::begin(ninth_mouse), std::end(ninth_mouse));
    HidReportSet set;

    CHECK_EQ(parse_hid_report_set(view(bytes), set), ReportDescriptorError::None);
    CHECK_EQ(set.count, std::uint8_t{8});
    for (std::uint8_t index = 0; index < 8; ++index) {
        check_entry(set.entries[index], ReportRole::Consumer,
                    static_cast<std::uint8_t>(index + 1), 1);
    }
    CHECK_EQ(set.rejected_count, std::uint8_t{0});
    CHECK_EQ(set.rejected_overflow, std::uint8_t{1});
}

TEST_CASE(two_supported_unnumbered_roles_are_explicitly_ambiguous) {
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x0C, 0x19, 0x01, 0x29, 0x02,
        0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x02, 0x81, 0x02,
        0x05, 0x07, 0x19, 0x00, 0x29, 0x65,
        0x15, 0x00, 0x25, 0x65, 0x75, 0x08, 0x95, 0x06, 0x81, 0x00,
    };
    HidReportSet set;

    CHECK_EQ(parse_hid_report_set(view(bytes), set), ReportDescriptorError::None);
    CHECK_FALSE(set.uses_report_ids);
    CHECK_EQ(set.count, std::uint8_t{0});
    CHECK_EQ(set.rejected_count, std::uint8_t{2});
    CHECK_EQ(set.rejected[0].role, ReportRole::Consumer);
    CHECK_EQ(set.rejected[0].report_id, std::uint8_t{0});
    CHECK_EQ(set.rejected[0].reason, ReportDescriptorError::AmbiguousReportSet);
    CHECK_EQ(set.rejected[1].role, ReportRole::Keyboard);
    CHECK_EQ(set.rejected[1].report_id, std::uint8_t{0});
    CHECK_EQ(set.rejected[1].reason, ReportDescriptorError::AmbiguousReportSet);
}

TEST_CASE(a_fatal_truncated_item_leaves_the_report_set_unchanged) {
    const std::vector<std::uint8_t> bytes = {
        0x05, 0x0C, 0x85, 0x02, 0x19, 0x01, 0x29, 0x02,
        0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x02, 0x81, 0x02,
        0x82, 0x00,
    };
    HidReportSet set{};
    set.uses_report_ids = true;
    set.count = 1;
    set.entries[0].role = ReportRole::Mouse;
    set.entries[0].report_id = 77;
    set.entries[0].mouse = boot_mouse_layout();
    set.rejected_count = 1;
    set.rejected[0] = RejectedReportEntry{
        ReportRole::Keyboard, 55, ReportDescriptorError::AmbiguousReportSet};
    set.rejected_overflow = 9;
    const HidReportSet sentinel = set;

    CHECK_EQ(parse_hid_report_set(view(bytes), set), ReportDescriptorError::Truncated);
    CHECK_EQ(std::memcmp(&set, &sentinel, sizeof(set)), 0);
}
