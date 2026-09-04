#pragma once

// HID report layouts, forwarded from input::hid.
//
// The parser and layout types that used to live here moved to
// input/hid/report_descriptor.hpp, so a second USB-host backend can classify
// its own devices without depending on CH375. These are temporary aliases,
// kept for the rest of this branch so every CH375 call site keeps compiling
// unchanged; there is exactly one implementation, in input::hid, never a
// second copy under this name.

#include "input/hid/report_descriptor.hpp"

namespace duo_input::u1::ch375 {

using duo_input::u1::input::hid::ReportField;
using duo_input::u1::input::hid::MouseReportLayout;
using duo_input::u1::input::hid::boot_mouse_layout;
using duo_input::u1::input::hid::KeyboardFieldKind;
using duo_input::u1::input::hid::kNoKeyboardBit;
using duo_input::u1::input::hid::KeyboardReportLayout;
using duo_input::u1::input::hid::boot_keyboard_layout;
using duo_input::u1::input::hid::ReportDescriptorError;
using duo_input::u1::input::hid::parse_mouse_report_descriptor;
using duo_input::u1::input::hid::parse_keyboard_report_descriptor;

}  // namespace duo_input::u1::ch375
