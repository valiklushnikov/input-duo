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
// What comes out is deliberately narrow: byte offsets for the four fields the
// normalizer reads. A layout that cannot be expressed that way is refused by
// name, and a refused layout is one the caller keeps out of report protocol.

#include "ch375/report_descriptor.hpp"
#include "test_support.hpp"

#include <cstdint>
#include <vector>

using duo_input::u1::ch375::boot_mouse_layout;
using duo_input::u1::ch375::MouseReportLayout;
using duo_input::u1::ch375::parse_mouse_report_descriptor;
using duo_input::u1::ch375::ReportDescriptorError;

namespace {

duo_input::protocol::ByteView view(const std::vector<std::uint8_t>& bytes) {
    return duo_input::protocol::ByteView{bytes.data(), bytes.size()};
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
}

TEST_CASE(a_field_that_does_not_start_on_a_byte_is_refused) {
    // Five button bits and then X immediately, with no padding: X starts at
    // bit five. The normalizer reads whole bytes, so this layout cannot be
    // expressed to it, and pretending otherwise shifts every axis.
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
             static_cast<int>(ReportDescriptorError::UnsupportedLayout));
}

TEST_CASE(an_axis_that_is_not_one_or_two_bytes_wide_is_refused) {
    // Twelve-bit axes. Real, and packed three to four bytes; nothing here can
    // read them, so the device keeps the layout it is already understood by.
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
             static_cast<int>(ReportDescriptorError::UnsupportedLayout));
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
