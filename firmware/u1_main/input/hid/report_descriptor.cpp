#include "input/hid/report_descriptor.hpp"

namespace duo_input::u1::input::hid {
namespace {

/// HID 1.11 6.2.2.3. A long item, which no HID device in the wild emits and
/// every parser still has to step over rather than read as a short one.
constexpr std::uint8_t kLongItemPrefix = 0xFE;

/// HID 1.11 6.2.2.1: bits 7-4 are the tag, 3-2 the type, 1-0 the size.
constexpr std::uint8_t kTypeMain = 0;
constexpr std::uint8_t kTypeGlobal = 1;
constexpr std::uint8_t kTypeLocal = 2;

constexpr std::uint8_t kTagInput = 0x8;
constexpr std::uint8_t kTagCollection = 0xA;
constexpr std::uint8_t kTagEndCollection = 0xC;

constexpr std::uint8_t kTagUsagePage = 0x0;
constexpr std::uint8_t kTagReportSize = 0x7;
constexpr std::uint8_t kTagReportId = 0x8;
constexpr std::uint8_t kTagReportCount = 0x9;

constexpr std::uint8_t kTagUsage = 0x0;

/// HID Usage Tables 1.12, the pages and usages a mouse is made of.
constexpr std::uint16_t kPageGenericDesktop = 0x01;
constexpr std::uint16_t kPageButton = 0x09;
constexpr std::uint16_t kPageConsumer = 0x0C;

constexpr std::uint16_t kUsageX = 0x30;
constexpr std::uint16_t kUsageY = 0x31;
constexpr std::uint16_t kUsageWheel = 0x38;
/// AC Pan, which is what a tilting wheel reports sideways scrolling as.
constexpr std::uint16_t kUsageAcPan = 0x0238;

/// HID 1.11 6.2.2.5: an Input item's data bit 0 is Constant, bit 1 Variable.
constexpr std::uint32_t kInputConstant = 0x01;
constexpr std::uint32_t kInputVariable = 0x02;

/// How many Usage items are kept for one Input item.
///
/// A mouse names three - X, Y and Wheel - and the count only ever matches the
/// usages behind it. More than this is a device doing something this firmware
/// is not going to route anyway, and the extras are dropped rather than
/// written past the end of an array.
constexpr std::size_t kMaxUsages = 8;

/// Where the bit cursor stops counting.
///
/// Offsets have to fit a byte count in the end, so anything past this is
/// already unusable; saturating rather than wrapping keeps a descriptor that
/// claims a four-billion-bit field from folding back round to a small offset
/// that looks legitimate.
constexpr std::uint32_t kBitCeiling = 0x00FFFFFF;

std::uint32_t saturating_add(std::uint32_t left, std::uint32_t right) {
    if (left >= kBitCeiling || right >= kBitCeiling - left) {
        return kBitCeiling;
    }
    return left + right;
}

/// A field while it is still measured in bits, which is how a descriptor
/// declares them and not how the normalizer can read them.
struct BitField {
    bool present = false;
    std::uint32_t bit_offset = 0;
    std::uint32_t bits = 0;
};

struct WorkingReport {
    std::uint8_t report_id = 0;
    BitField buttons;
    BitField x;
    BitField y;
    BitField wheel;
    BitField pan;

    bool describes_a_pointer() const { return x.present && y.present; }
};

void record(BitField& field, std::uint32_t bit_offset, std::uint32_t bits) {
    if (field.present) {
        // A descriptor is allowed to name the same usage twice. The first
        // one is the one the device fills in for a normal report; taking the
        // later one moves the axis to wherever a second declaration sits.
        return;
    }
    field.present = true;
    field.bit_offset = bit_offset;
    field.bits = bits;
}

/// Take one more Input item's worth of buttons into the run already found.
///
/// A run of buttons does not have to arrive in one Input item. `Usage Minimum
/// 1 / Usage Maximum 3` followed by `Usage Minimum 4 / Usage Maximum 5`
/// describes the same five bits as one run of five, and mice ship it: the
/// device on the bench does, which is why buttons 4 and 5 reached neither the
/// PC nor the capture dialog while 1-3 worked. Treated as a redeclaration the
/// second item is discarded and the field stays three bits wide, and the
/// normalizer masks the button byte to exactly that width.
///
/// Only a piece that begins where the previous one ended is the same run.
/// Buttons declared somewhere else in the report - after the axes, in another
/// collection - are a different field, and moving the buttons there would lose
/// the ones that do work; the first declaration still wins for those.
void extend_buttons(BitField& field, std::uint32_t bit_offset, std::uint32_t bits) {
    if (!field.present) {
        record(field, bit_offset, bits);
        return;
    }
    if (bits == 0 || bit_offset != field.bit_offset + field.bits) {
        return;
    }
    const std::uint32_t grown = field.bits + bits;
    if (grown > 8) {
        // More buttons than fit the byte the normalizer reads. Growing past
        // eight would make to_bytes refuse the field outright and take the
        // axes and the wheel down with it, so the run keeps the byte it can
        // actually route.
        return;
    }
    field.bits = grown;
}

/// Turn a run of bits into the span the normalizer can read, or refuse it.
///
/// RP2040 can cheaply extract at most sixteen signed bits from the three bytes
/// such a field can touch.  This includes the bench mouse's two consecutive
/// 12-bit axes: X is byte-aligned and Y begins in the high nibble of the next
/// byte.  Recording the bit offset is essential; rounding Y down would turn
/// movement into unrelated values and put the wheel at the wrong byte again.
bool to_bytes(const BitField& field, bool packed, ReportField& out) {
    if (!field.present) {
        out = ReportField{};
        return true;
    }
    if (field.bits == 0 || field.bits > 16) {
        return false;
    }
    if (packed) {
        if (field.bits > 8) {
            return false;
        }
    }
    const std::uint32_t bit_inside_byte = field.bit_offset % 8;
    const std::uint32_t bytes = (bit_inside_byte + field.bits + 7) / 8;
    const std::uint32_t offset = field.bit_offset / 8;
    if (offset + bytes > 0xFF) {
        return false;
    }
    out.present = true;
    out.offset = static_cast<std::uint8_t>(offset);
    out.bytes = static_cast<std::uint8_t>(bytes);
    out.bit_offset = static_cast<std::uint8_t>(bit_inside_byte);
    out.bits = static_cast<std::uint8_t>(field.bits);
    return true;
}

std::uint8_t end_of(const ReportField& field) {
    if (!field.present) {
        return 0;
    }
    return static_cast<std::uint8_t>(field.offset + field.bytes);
}

}  // namespace

MouseReportLayout boot_mouse_layout() {
    MouseReportLayout layout;
    layout.buttons = ReportField{true, 0, 1};
    layout.x = ReportField{true, 1, 1};
    layout.y = ReportField{true, 2, 1};
    // The boot report is three bytes and stops at Y. These two are read only
    // when a device sends far enough to carry them, which is the behaviour
    // that was already here before any descriptor was fetched.
    layout.wheel = ReportField{true, 3, 1};
    layout.pan = ReportField{true, 4, 1};
    layout.minimum_body_bytes = 3;
    return layout;
}

ReportDescriptorError parse_mouse_report_descriptor(protocol::ByteView descriptor,
                                                    MouseReportLayout& out) {
    if (descriptor.data == nullptr || descriptor.size == 0) {
        return ReportDescriptorError::Truncated;
    }

    std::uint16_t usage_page = 0;
    std::uint32_t report_size = 0;
    std::uint32_t report_count = 0;
    bool any_report_id = false;

    // Input, Output and Feature are three separate report spaces (HID 1.11
    // 5.6), so only Input items move this. Counting an Output item here would
    // push every axis along by the length of a report nobody reads.
    std::uint32_t input_bits = 0;

    std::uint32_t usages[kMaxUsages] = {};
    std::uint8_t usage_sizes[kMaxUsages] = {};
    std::size_t usage_count = 0;

    WorkingReport current;
    WorkingReport found;
    bool have_found = false;

    std::size_t at = 0;
    while (at < descriptor.size) {
        const std::uint8_t prefix = descriptor.data[at];
        if (prefix == kLongItemPrefix) {
            // bDataSize, bLongItemTag, then the data. Nothing here reads one;
            // the point is to step over exactly as far as it claims and not a
            // byte further.
            if (descriptor.size - at < 3) {
                return ReportDescriptorError::Truncated;
            }
            const std::size_t length = descriptor.data[at + 1];
            if (descriptor.size - at - 3 < length) {
                return ReportDescriptorError::Truncated;
            }
            at += 3 + length;
            continue;
        }

        // A stored size of 3 means four bytes, not three (HID 1.11 6.2.2.2).
        const std::uint8_t stored = static_cast<std::uint8_t>(prefix & 0x03);
        const std::size_t length = stored == 3 ? 4 : stored;
        if (descriptor.size - at - 1 < length) {
            return ReportDescriptorError::Truncated;
        }

        std::uint32_t data = 0;
        for (std::size_t index = 0; index < length; ++index) {
            data |= static_cast<std::uint32_t>(descriptor.data[at + 1 + index])
                    << (8 * index);
        }
        const std::uint8_t type = static_cast<std::uint8_t>((prefix >> 2) & 0x03);
        const std::uint8_t tag = static_cast<std::uint8_t>((prefix >> 4) & 0x0F);
        at += 1 + length;

        if (type == kTypeGlobal) {
            switch (tag) {
                case kTagUsagePage:
                    usage_page = static_cast<std::uint16_t>(data & 0xFFFF);
                    break;
                case kTagReportSize:
                    report_size = data;
                    break;
                case kTagReportCount:
                    report_count = data;
                    break;
                case kTagReportId:
                    // A new identifier begins a new report, so the offsets
                    // start again from zero. Whatever was being measured is
                    // finished here or it is never finished at all.
                    if (!have_found && current.describes_a_pointer()) {
                        found = current;
                        have_found = true;
                    }
                    current = WorkingReport{};
                    current.report_id = static_cast<std::uint8_t>(data & 0xFF);
                    input_bits = 0;
                    any_report_id = true;
                    break;
                default:
                    break;
            }
            continue;
        }

        if (type == kTypeLocal) {
            if (tag == kTagUsage && usage_count < kMaxUsages) {
                usages[usage_count] = data;
                usage_sizes[usage_count] = static_cast<std::uint8_t>(length);
                ++usage_count;
            }
            // Usage Minimum and Usage Maximum name a run rather than a list.
            // The only run a mouse declares is its buttons, and those are
            // recognised by their page, so the bounds themselves are not read.
            continue;
        }

        if (type != kTypeMain) {
            continue;
        }

        if (tag == kTagInput) {
            const std::uint32_t bits =
                report_size > 32 || report_count > 0xFF
                    ? kBitCeiling
                    : saturating_add(0, report_size * report_count);

            if ((data & kInputConstant) == 0 && (data & kInputVariable) != 0) {
                if (usage_page == kPageButton) {
                    // Every button in one field, however many Input items
                    // the device took to declare them. Which button is which
                    // is the bit position inside it, which is what the
                    // normalizer already walks.
                    extend_buttons(current.buttons, input_bits, bits);
                } else {
                    for (std::uint32_t index = 0; index < report_count && index < kMaxUsages;
                         ++index) {
                        const std::size_t pick =
                            index < usage_count ? static_cast<std::size_t>(index)
                                                : (usage_count == 0 ? kMaxUsages : usage_count - 1);
                        if (pick >= kMaxUsages) {
                            break;
                        }
                        // A four-byte Usage item carries its own page in the
                        // top half; a shorter one takes the global page.
                        const std::uint16_t page =
                            usage_sizes[pick] == 4
                                ? static_cast<std::uint16_t>(usages[pick] >> 16)
                                : usage_page;
                        const std::uint16_t usage =
                            static_cast<std::uint16_t>(usages[pick] & 0xFFFF);
                        const std::uint32_t offset =
                            saturating_add(input_bits, index * report_size);

                        if (page == kPageGenericDesktop && usage == kUsageX) {
                            record(current.x, offset, report_size);
                        } else if (page == kPageGenericDesktop && usage == kUsageY) {
                            record(current.y, offset, report_size);
                        } else if (page == kPageGenericDesktop && usage == kUsageWheel) {
                            record(current.wheel, offset, report_size);
                        } else if (page == kPageConsumer && usage == kUsageAcPan) {
                            record(current.pan, offset, report_size);
                        }
                    }
                }
            }
            input_bits = saturating_add(input_bits, bits);
        }

        if (tag == kTagCollection || tag == kTagEndCollection) {
            // Nesting is not tracked. The walk is over bytes from start to
            // end, so a descriptor of nothing but openers ends where the bytes
            // do rather than wherever a depth counter gave up.
        }

        // HID 1.11 6.2.2.8: every main item clears the local state behind it.
        usage_count = 0;
    }

    if (!have_found && current.describes_a_pointer()) {
        found = current;
        have_found = true;
    }
    if (!have_found) {
        return ReportDescriptorError::NoMouseReport;
    }

    MouseReportLayout layout;
    layout.report_id = any_report_id;
    layout.report_id_value = found.report_id;
    if (!to_bytes(found.buttons, true, layout.buttons) ||
        !to_bytes(found.x, false, layout.x) ||
        !to_bytes(found.y, false, layout.y) ||
        !to_bytes(found.wheel, false, layout.wheel) ||
        !to_bytes(found.pan, false, layout.pan)) {
        return ReportDescriptorError::UnsupportedLayout;
    }
    if (!layout.buttons.present) {
        // A pointer with no buttons at all. Something is being described here
        // that is not the mouse this firmware knows how to route.
        return ReportDescriptorError::UnsupportedLayout;
    }

    std::uint8_t minimum = end_of(layout.buttons);
    if (end_of(layout.x) > minimum) {
        minimum = end_of(layout.x);
    }
    if (end_of(layout.y) > minimum) {
        minimum = end_of(layout.y);
    }
    layout.minimum_body_bytes = minimum;

    out = layout;
    return ReportDescriptorError::None;
}

namespace {

constexpr std::uint8_t kKeyboardTagLogicalMinimum = 0x1;
constexpr std::uint8_t kKeyboardTagLogicalMaximum = 0x2;
constexpr std::uint8_t kKeyboardTagPush = 0xA;
constexpr std::uint8_t kKeyboardTagPop = 0xB;
constexpr std::uint8_t kKeyboardTagUsageMinimum = 0x1;
constexpr std::uint8_t kKeyboardTagUsageMaximum = 0x2;

constexpr std::uint16_t kPageKeyboard = 0x07;
constexpr std::uint16_t kModifierMinimum = 0xE0;
constexpr std::uint16_t kModifierMaximum = 0xE7;
constexpr std::uint32_t kMaximumKeyboardReportBits = 64 * 8;
constexpr std::size_t kMaximumKeyboardReports = 8;
constexpr std::size_t kMaximumKeyboardGlobals = 4;
constexpr std::size_t kMaximumKeyboardUsages = 16;

struct KeyboardGlobalState {
    std::uint16_t usage_page = 0;
    std::int32_t logical_minimum = 0;
    std::uint32_t logical_maximum_raw = 0;
    std::uint8_t logical_maximum_size = 0;
    std::uint32_t report_size = 0;
    std::uint32_t report_count = 0;
    std::uint8_t report_id = 0;
};

struct KeyboardUsage {
    std::uint16_t page = 0;
    std::uint16_t value = 0;
};

struct KeyboardLocalState {
    KeyboardUsage usages[kMaximumKeyboardUsages] = {};
    std::size_t usage_count = 0;
    bool have_minimum = false;
    bool have_maximum = false;
    KeyboardUsage minimum;
    KeyboardUsage maximum;
};

struct KeyboardReportState {
    std::uint8_t report_id = 0;
    std::uint32_t input_bits = 0;
    bool has_keys = false;
    KeyboardReportLayout layout;
    std::uint32_t required_bits = 0;
};

std::int32_t signed_item(std::uint32_t data, std::size_t length) {
    if (length == 1) {
        return static_cast<std::int8_t>(data);
    }
    if (length == 2) {
        return static_cast<std::int16_t>(data);
    }
    if (length == 4) {
        return static_cast<std::int32_t>(data);
    }
    return 0;
}

std::int64_t logical_maximum(const KeyboardGlobalState& globals) {
    if (globals.logical_minimum < 0) {
        return signed_item(globals.logical_maximum_raw, globals.logical_maximum_size);
    }
    return globals.logical_maximum_raw;
}

bool logical_range_is_valid(const KeyboardGlobalState& globals) {
    return static_cast<std::int64_t>(globals.logical_minimum) <= logical_maximum(globals);
}

KeyboardUsage usage_from_item(std::uint32_t data,
                              std::size_t length,
                              std::uint16_t global_page) {
    KeyboardUsage usage;
    usage.page = length == 4 ? static_cast<std::uint16_t>(data >> 16) : global_page;
    usage.value = static_cast<std::uint16_t>(data & 0xFFFF);
    return usage;
}

bool local_range_is_valid(const KeyboardLocalState& locals) {
    return locals.have_minimum == locals.have_maximum &&
           (!locals.have_minimum ||
            (locals.minimum.page == locals.maximum.page &&
             locals.minimum.value <= locals.maximum.value));
}

bool is_modifier(std::uint16_t usage) {
    return usage >= kModifierMinimum && usage <= kModifierMaximum;
}

void keep_required_bit(KeyboardReportState& report, std::uint32_t bit_after_field) {
    if (bit_after_field > report.required_bits) {
        report.required_bits = bit_after_field;
    }
}

bool record_modifier(KeyboardReportState& report,
                     std::uint16_t usage,
                     std::uint32_t bit_offset) {
    if (!is_modifier(usage) || bit_offset >= kMaximumKeyboardReportBits) {
        return bit_offset < kMaximumKeyboardReportBits;
    }
    const std::size_t index = usage - kModifierMinimum;
    if (report.layout.modifier_bits[index] == kNoKeyboardBit) {
        report.layout.modifier_bits[index] = static_cast<std::uint16_t>(bit_offset);
        keep_required_bit(report, bit_offset + 1);
    }
    return true;
}

KeyboardReportState* keyboard_report(KeyboardReportState* reports,
                                     std::size_t& report_count,
                                     std::uint8_t report_id) {
    for (std::size_t index = 0; index < report_count; ++index) {
        if (reports[index].report_id == report_id) {
            return &reports[index];
        }
    }
    if (report_count == kMaximumKeyboardReports) {
        return nullptr;
    }
    KeyboardReportState& report = reports[report_count++];
    report.report_id = report_id;
    report.layout.report_id_value = report_id;
    return &report;
}

bool keyboard_range(const KeyboardLocalState& locals,
                    std::uint16_t& minimum,
                    std::uint16_t& maximum) {
    if (!locals.have_minimum || !locals.have_maximum ||
        locals.minimum.page != kPageKeyboard ||
        locals.maximum.page != kPageKeyboard) {
        return false;
    }
    minimum = locals.minimum.value;
    maximum = locals.maximum.value;
    return true;
}

bool record_keyboard_modifiers(KeyboardReportState& report,
                               const KeyboardLocalState& locals,
                               std::uint32_t report_size,
                               std::uint32_t report_count,
                               std::uint32_t first_bit) {
    if (report_size != 1) {
        return true;
    }

    if (locals.have_minimum && locals.minimum.page == kPageKeyboard) {
        for (std::uint32_t index = 0; index < report_count; ++index) {
            const std::uint32_t usage =
                static_cast<std::uint32_t>(locals.minimum.value) + index;
            if (usage > locals.maximum.value) {
                break;
            }
            if (!record_modifier(report,
                                 static_cast<std::uint16_t>(usage),
                                 first_bit + index)) {
                return false;
            }
        }
        return true;
    }

    for (std::uint32_t index = 0;
         index < report_count && index < locals.usage_count;
         ++index) {
        if (locals.usages[index].page == kPageKeyboard &&
            !record_modifier(report,
                             locals.usages[index].value,
                             first_bit + index)) {
            return false;
        }
    }
    return true;
}

bool record_keyboard_keys(KeyboardReportState& report,
                          KeyboardFieldKind kind,
                          std::uint32_t first_bit,
                          std::uint32_t element_bits,
                          std::uint32_t element_count,
                          std::uint16_t usage_minimum,
                          std::uint16_t usage_maximum) {
    if (report.has_keys || first_bit >= kMaximumKeyboardReportBits ||
        element_bits == 0 || element_bits > 0xFF ||
        element_count == 0 || element_count > 0xFF) {
        return false;
    }
    report.has_keys = true;
    report.layout.key_kind = kind;
    report.layout.key_bit_offset = static_cast<std::uint16_t>(first_bit);
    report.layout.key_element_bits = static_cast<std::uint8_t>(element_bits);
    report.layout.key_element_count = static_cast<std::uint8_t>(element_count);
    report.layout.key_usage_minimum = usage_minimum;
    report.layout.key_usage_maximum = usage_maximum;
    keep_required_bit(report, first_bit + element_bits * element_count);
    return true;
}

}  // namespace

KeyboardReportLayout boot_keyboard_layout() {
    KeyboardReportLayout layout;
    for (std::uint16_t modifier = 0; modifier < 8; ++modifier) {
        layout.modifier_bits[modifier] = modifier;
    }
    layout.key_kind = KeyboardFieldKind::Array;
    layout.key_bit_offset = 16;
    layout.key_element_bits = 8;
    layout.key_element_count = 6;
    layout.key_usage_minimum = 0;
    layout.key_usage_maximum = 0x00FF;
    layout.minimum_body_bytes = 8;
    return layout;
}

ReportDescriptorError parse_keyboard_report_descriptor(protocol::ByteView descriptor,
                                                       KeyboardReportLayout& out) {
    if (descriptor.data == nullptr || descriptor.size == 0) {
        return ReportDescriptorError::Truncated;
    }

    KeyboardGlobalState globals;
    KeyboardGlobalState global_stack[kMaximumKeyboardGlobals] = {};
    std::size_t global_depth = 0;
    KeyboardLocalState locals;
    KeyboardReportState reports[kMaximumKeyboardReports] = {};
    std::size_t report_count = 0;
    bool any_report_id = false;

    std::size_t at = 0;
    while (at < descriptor.size) {
        const std::uint8_t prefix = descriptor.data[at];
        if (prefix == kLongItemPrefix) {
            if (descriptor.size - at < 3) {
                return ReportDescriptorError::Truncated;
            }
            const std::size_t length = descriptor.data[at + 1];
            if (descriptor.size - at - 3 < length) {
                return ReportDescriptorError::Truncated;
            }
            at += 3 + length;
            continue;
        }

        const std::uint8_t stored = static_cast<std::uint8_t>(prefix & 0x03);
        const std::size_t length = stored == 3 ? 4 : stored;
        if (descriptor.size - at - 1 < length) {
            return ReportDescriptorError::Truncated;
        }

        std::uint32_t data = 0;
        for (std::size_t index = 0; index < length; ++index) {
            data |= static_cast<std::uint32_t>(descriptor.data[at + 1 + index])
                    << (8 * index);
        }
        const std::uint8_t type = static_cast<std::uint8_t>((prefix >> 2) & 0x03);
        const std::uint8_t tag = static_cast<std::uint8_t>((prefix >> 4) & 0x0F);
        at += 1 + length;

        if (type == kTypeGlobal) {
            switch (tag) {
                case kTagUsagePage:
                    globals.usage_page = static_cast<std::uint16_t>(data & 0xFFFF);
                    break;
                case kKeyboardTagLogicalMinimum:
                    globals.logical_minimum = signed_item(data, length);
                    break;
                case kKeyboardTagLogicalMaximum:
                    globals.logical_maximum_raw = data;
                    globals.logical_maximum_size = static_cast<std::uint8_t>(length);
                    break;
                case kTagReportSize:
                    globals.report_size = data;
                    break;
                case kTagReportId:
                    if (data == 0 || data > 0xFF) {
                        return ReportDescriptorError::UnsupportedLayout;
                    }
                    globals.report_id = static_cast<std::uint8_t>(data);
                    any_report_id = true;
                    if (keyboard_report(reports, report_count, globals.report_id) == nullptr) {
                        return ReportDescriptorError::UnsupportedLayout;
                    }
                    break;
                case kTagReportCount:
                    globals.report_count = data;
                    break;
                case kKeyboardTagPush:
                    if (global_depth == kMaximumKeyboardGlobals) {
                        return ReportDescriptorError::MalformedGlobalState;
                    }
                    global_stack[global_depth++] = globals;
                    break;
                case kKeyboardTagPop:
                    if (global_depth == 0) {
                        return ReportDescriptorError::MalformedGlobalState;
                    }
                    globals = global_stack[--global_depth];
                    break;
                default:
                    break;
            }
            continue;
        }

        if (type == kTypeLocal) {
            const KeyboardUsage usage = usage_from_item(data, length, globals.usage_page);
            if (tag == kTagUsage && locals.usage_count < kMaximumKeyboardUsages) {
                locals.usages[locals.usage_count++] = usage;
            } else if (tag == kKeyboardTagUsageMinimum) {
                locals.have_minimum = true;
                locals.minimum = usage;
            } else if (tag == kKeyboardTagUsageMaximum) {
                locals.have_maximum = true;
                locals.maximum = usage;
            }
            continue;
        }

        if (type != kTypeMain) {
            continue;
        }

        if (tag == kTagInput) {
            if (globals.report_size == 0 || globals.report_count == 0 ||
                !logical_range_is_valid(globals) || !local_range_is_valid(locals)) {
                return ReportDescriptorError::UnsupportedLayout;
            }
            KeyboardReportState* report =
                keyboard_report(reports, report_count, globals.report_id);
            if (report == nullptr) {
                return ReportDescriptorError::UnsupportedLayout;
            }

            const std::uint64_t field_bits =
                static_cast<std::uint64_t>(globals.report_size) * globals.report_count;
            const std::uint64_t bit_after_field =
                static_cast<std::uint64_t>(report->input_bits) + field_bits;
            if (field_bits > kMaximumKeyboardReportBits ||
                bit_after_field > kMaximumKeyboardReportBits) {
                return ReportDescriptorError::UnsupportedLayout;
            }

            const bool constant = (data & kInputConstant) != 0;
            const bool variable = (data & kInputVariable) != 0;
            if (!constant) {
                std::uint16_t usage_minimum = 0;
                std::uint16_t usage_maximum = 0;
                const bool have_keyboard_range =
                    keyboard_range(locals, usage_minimum, usage_maximum);

                if (variable) {
                    if (!record_keyboard_modifiers(*report,
                                                   locals,
                                                   globals.report_size,
                                                   globals.report_count,
                                                   report->input_bits)) {
                        return ReportDescriptorError::UnsupportedLayout;
                    }
                    const bool modifier_range =
                        have_keyboard_range &&
                        usage_minimum >= kModifierMinimum &&
                        usage_maximum <= kModifierMaximum;
                    if (have_keyboard_range && !modifier_range) {
                        const std::uint32_t usage_count =
                            static_cast<std::uint32_t>(usage_maximum) - usage_minimum + 1;
                        if (globals.report_size != 1 ||
                            globals.report_count != usage_count ||
                            !record_keyboard_keys(*report,
                                                  KeyboardFieldKind::Bitmap,
                                                  report->input_bits,
                                                  globals.report_size,
                                                  globals.report_count,
                                                  usage_minimum,
                                                  usage_maximum)) {
                            return ReportDescriptorError::UnsupportedLayout;
                        }
                    }
                } else {
                    if (!have_keyboard_range && globals.usage_page == kPageKeyboard) {
                        const std::int64_t maximum = logical_maximum(globals);
                        if (globals.logical_minimum < 0 || maximum > 0xFFFF) {
                            return ReportDescriptorError::UnsupportedLayout;
                        }
                        usage_minimum = static_cast<std::uint16_t>(globals.logical_minimum);
                        usage_maximum = static_cast<std::uint16_t>(maximum);
                    }
                    if (have_keyboard_range || globals.usage_page == kPageKeyboard) {
                        if (globals.report_size > 16 ||
                            !record_keyboard_keys(*report,
                                                  KeyboardFieldKind::Array,
                                                  report->input_bits,
                                                  globals.report_size,
                                                  globals.report_count,
                                                  usage_minimum,
                                                  usage_maximum)) {
                            return ReportDescriptorError::UnsupportedLayout;
                        }
                    }
                }
            }
            report->input_bits = static_cast<std::uint32_t>(bit_after_field);
        }

        // HID local items apply to one Main item, including Output, Feature,
        // Collection, and End Collection. Only Input advances an Input cursor.
        locals = KeyboardLocalState{};
    }

    KeyboardReportState* found = nullptr;
    for (std::size_t index = 0; index < report_count; ++index) {
        if (!reports[index].has_keys) {
            continue;
        }
        if (found != nullptr && found->report_id != reports[index].report_id) {
            return ReportDescriptorError::AmbiguousKeyboardReport;
        }
        found = &reports[index];
    }
    if (found == nullptr) {
        return ReportDescriptorError::NoKeyboardReport;
    }
    if (any_report_id && found->report_id == 0) {
        return ReportDescriptorError::UnsupportedLayout;
    }

    found->layout.report_id = any_report_id;
    found->layout.minimum_body_bytes =
        static_cast<std::uint8_t>((found->required_bits + 7) / 8);
    out = found->layout;
    return ReportDescriptorError::None;
}

ReportDescriptorRole classify_report_descriptor(protocol::ByteView descriptor,
                                                KeyboardReportLayout& keyboard,
                                                MouseReportLayout& mouse) {
    KeyboardReportLayout parsed_keyboard;
    MouseReportLayout parsed_mouse;
    const bool has_keyboard =
        parse_keyboard_report_descriptor(descriptor, parsed_keyboard) ==
        ReportDescriptorError::None;
    const bool has_mouse = parse_mouse_report_descriptor(descriptor, parsed_mouse) ==
                           ReportDescriptorError::None;

    if (has_keyboard && has_mouse) {
        return ReportDescriptorRole::Ambiguous;
    }
    if (has_keyboard) {
        keyboard = parsed_keyboard;
        return ReportDescriptorRole::Keyboard;
    }
    if (has_mouse) {
        mouse = parsed_mouse;
        return ReportDescriptorRole::Mouse;
    }
    return ReportDescriptorRole::None;
}

}  // namespace duo_input::u1::input::hid
