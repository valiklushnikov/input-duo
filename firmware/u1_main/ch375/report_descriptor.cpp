#include "ch375/report_descriptor.hpp"

namespace duo_input::u1::ch375 {
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

/// Turn a run of bits into the bytes the normalizer can read, or refuse it.
///
/// ``packed`` is true only for the buttons, where a run of bits inside a
/// single byte is the ordinary way to declare them. An axis has to be a whole
/// byte or a whole pair of them; a twelve-bit axis is real and nothing here
/// can read it, so it is refused rather than rounded.
bool to_bytes(const BitField& field, bool packed, ReportField& out) {
    if (!field.present) {
        out = ReportField{};
        return true;
    }
    if ((field.bit_offset % 8) != 0) {
        // The normalizer reads whole bytes. A field starting inside one cannot
        // be handed to it, and rounding down shifts every value it carries.
        return false;
    }
    std::uint32_t bytes = 0;
    if (packed) {
        if (field.bits == 0 || field.bits > 8) {
            return false;
        }
        bytes = 1;
    } else if (field.bits == 8 || field.bits == 16) {
        bytes = field.bits / 8;
    } else {
        return false;
    }
    const std::uint32_t offset = field.bit_offset / 8;
    if (offset + bytes > 0xFF) {
        return false;
    }
    out.present = true;
    out.offset = static_cast<std::uint8_t>(offset);
    out.bytes = static_cast<std::uint8_t>(bytes);
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
                    // Every button in one field. Which button is which is the
                    // bit position inside it, which is what the normalizer
                    // already walks.
                    record(current.buttons, input_bits, bits);
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

}  // namespace duo_input::u1::ch375
