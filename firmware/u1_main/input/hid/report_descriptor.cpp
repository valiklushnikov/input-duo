#include "input/hid/report_descriptor.hpp"

#include <new>

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
constexpr std::size_t kMaximumUsageReports = 16;
constexpr std::size_t kMaximumMouseReports = 8;
constexpr std::size_t kMaximumInputCursors = 16;
constexpr std::size_t kMaximumKeyboardGlobals = 4;
constexpr std::size_t kMaximumKeyboardUsages = 16;
constexpr std::size_t kNoDescriptorOffset = static_cast<std::size_t>(-1);

struct KeyboardGlobalState {
    std::uint16_t usage_page = 0;
    std::int32_t logical_minimum = 0;
    std::uint32_t logical_maximum_raw = 0;
    std::uint8_t logical_maximum_size = 0;
    std::uint32_t report_size = 0;
    std::uint32_t report_count = 0;
    std::uint8_t report_id = 0;
    bool report_id_valid = true;
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
    ReportRole role = ReportRole::Keyboard;
    std::uint8_t report_id = 0;
    bool report_id_valid = true;
    bool has_keys = false;
    bool invalid = false;
    std::size_t first_input_offset = kNoDescriptorOffset;
    KeyboardReportLayout layout;
    std::uint32_t required_bits = 0;
};

struct MouseReportState {
    WorkingReport report;
    bool report_id_valid = true;
    bool invalid = false;
    std::size_t first_input_offset = kNoDescriptorOffset;
};

struct InputCursor {
    std::uint8_t report_id = 0;
    bool report_id_valid = true;
    std::uint32_t input_bits = 0;
};

struct CompletedCandidate {
    std::size_t first_input_offset = kNoDescriptorOffset;
    ReportRole role = ReportRole::Keyboard;
    std::uint8_t source_index = 0;
    ReportDescriptorError error = ReportDescriptorError::None;
};

struct DroppedCandidateKeys {
    std::uint8_t valid_report_ids[32] = {};
    bool invalid_report_id = false;
};

struct ParserScratch {
    KeyboardGlobalState globals;
    KeyboardGlobalState global_stack[kMaximumKeyboardGlobals] = {};
    KeyboardLocalState locals;
    KeyboardReportState usage_reports[kMaximumUsageReports] = {};
    MouseReportState mouse_reports[kMaximumMouseReports] = {};
    InputCursor cursors[kMaximumInputCursors] = {};
    CompletedCandidate
        completed[kMaximumUsageReports + kMaximumMouseReports] = {};
    DroppedCandidateKeys usage_dropped[2] = {};
    DroppedCandidateKeys mouse_dropped;
    HidReportEntry entry;
    HidReportSet parsed;
};

static_assert(std::is_trivially_destructible<ParserScratch>::value,
              "Reusing parser storage must not skip a required destructor");
static_assert(std::is_trivially_destructible<HidReportSet>::value,
              "Reusing wrapper storage must not skip a required destructor");

// Descriptor discovery is serialized by the active backend (Core 1 for PIO
// USB; the alternative CH375 backend also calls synchronously). The
// legacy wrappers also finish one parse before starting another. Keeping the
// bounded candidate arena in BSS is what makes that ownership fit Core 1's
// 2 KiB stack; the flag turns accidental same-owner recursion into an atomic
// failure instead of corrupting an active parse.
alignas(ParserScratch)
std::uint8_t g_core1_parser_scratch_storage[sizeof(ParserScratch)] = {};
alignas(HidReportSet)
std::uint8_t g_core1_parser_result_storage[sizeof(HidReportSet)] = {};
bool g_core1_parser_scratch_active = false;

ParserScratch& construct_parser_scratch() {
    return *::new (static_cast<void*>(g_core1_parser_scratch_storage))
        ParserScratch{};
}

HidReportSet& construct_parser_result() {
    return *::new (static_cast<void*>(g_core1_parser_result_storage))
        HidReportSet{};
}

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

void saturating_increment(std::uint8_t& value);

void note_dropped_candidate(DroppedCandidateKeys& dropped,
                            std::uint8_t report_id,
                            bool report_id_valid,
                            std::uint8_t& rejected_overflow) {
    const std::uint8_t mask =
        static_cast<std::uint8_t>(1U << (report_id % 8));
    const bool known = report_id_valid
                           ? (dropped.valid_report_ids[report_id / 8] & mask) != 0
                           : dropped.invalid_report_id;
    if (known) {
        return;
    }
    if (report_id_valid) {
        dropped.valid_report_ids[report_id / 8] |= mask;
    } else {
        dropped.invalid_report_id = true;
    }
    saturating_increment(rejected_overflow);
}

KeyboardReportState* usage_report(KeyboardReportState* reports,
                                  std::size_t& report_count,
                                  ReportRole role,
                                  std::uint8_t report_id,
                                  bool report_id_valid,
                                  DroppedCandidateKeys& dropped,
                                  std::uint8_t& rejected_overflow,
                                  std::size_t first_input_offset) {
    for (std::size_t index = 0; index < report_count; ++index) {
        if (reports[index].role == role && reports[index].report_id == report_id &&
            reports[index].report_id_valid == report_id_valid) {
            return &reports[index];
        }
    }
    if (report_count == kMaximumUsageReports) {
        note_dropped_candidate(dropped, report_id, report_id_valid,
                               rejected_overflow);
        return nullptr;
    }
    KeyboardReportState& report = reports[report_count++];
    report.role = role;
    report.report_id = report_id;
    report.report_id_valid = report_id_valid;
    report.first_input_offset = first_input_offset;
    report.layout.consumer = role == ReportRole::Consumer;
    report.layout.report_id_value = report_id;
    return &report;
}

MouseReportState* mouse_report(MouseReportState* reports,
                               std::size_t& report_count,
                               std::uint8_t report_id,
                               bool report_id_valid,
                               DroppedCandidateKeys& dropped,
                               std::uint8_t& rejected_overflow,
                               std::size_t first_input_offset) {
    for (std::size_t index = 0; index < report_count; ++index) {
        if (reports[index].report.report_id == report_id &&
            reports[index].report_id_valid == report_id_valid) {
            return &reports[index];
        }
    }
    if (report_count == kMaximumMouseReports) {
        note_dropped_candidate(dropped, report_id, report_id_valid,
                               rejected_overflow);
        return nullptr;
    }
    MouseReportState& report = reports[report_count++];
    report.report.report_id = report_id;
    report.report_id_valid = report_id_valid;
    report.first_input_offset = first_input_offset;
    return &report;
}

InputCursor* input_cursor(InputCursor* cursors,
                          std::size_t& cursor_count,
                          std::uint8_t report_id,
                          bool report_id_valid) {
    for (std::size_t index = 0; index < cursor_count; ++index) {
        if (cursors[index].report_id == report_id &&
            cursors[index].report_id_valid == report_id_valid) {
            return &cursors[index];
        }
    }
    if (cursor_count == kMaximumInputCursors) {
        return nullptr;
    }
    InputCursor& cursor = cursors[cursor_count++];
    cursor.report_id = report_id;
    cursor.report_id_valid = report_id_valid;
    return &cursor;
}

bool input_mentions_page(const KeyboardLocalState& locals,
                         std::uint16_t global_page,
                         std::uint16_t page) {
    if (global_page == page ||
        (locals.have_minimum && locals.minimum.page == page) ||
        (locals.have_maximum && locals.maximum.page == page)) {
        return true;
    }
    for (std::size_t index = 0; index < locals.usage_count; ++index) {
        if (locals.usages[index].page == page) {
            return true;
        }
    }
    return false;
}

void invalidate_usage_reports(KeyboardReportState* reports,
                              std::size_t report_count,
                              std::uint8_t report_id,
                              bool report_id_valid) {
    for (std::size_t index = 0; index < report_count; ++index) {
        if (reports[index].report_id == report_id &&
            reports[index].report_id_valid == report_id_valid) {
            reports[index].invalid = true;
        }
    }
}

bool keyboard_range(const KeyboardLocalState& locals,
                    std::uint16_t& minimum,
                    std::uint16_t& maximum, std::uint16_t page) {
    if (!locals.have_minimum || !locals.have_maximum ||
        locals.minimum.page != page ||
        locals.maximum.page != page) {
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

namespace {

void saturating_increment(std::uint8_t& value) {
    if (value != 0xFF) {
        ++value;
    }
}

bool append_accepted(HidReportSet& out, const HidReportEntry& entry) {
    if (out.count == kMaxHidReportEntries) {
        saturating_increment(out.rejected_overflow);
        return false;
    }
    out.entries[out.count++] = entry;
    return true;
}

void append_rejected(HidReportSet& out,
                     ReportRole role,
                     std::uint8_t report_id,
                     ReportDescriptorError reason) {
    if (out.rejected_count == kMaxRejectedReportEntries) {
        saturating_increment(out.rejected_overflow);
        return;
    }
    out.rejected[out.rejected_count++] = RejectedReportEntry{role, report_id, reason};
}

std::uint32_t bounded_field_bits(std::uint32_t report_size,
                                 std::uint32_t report_count) {
    if (report_size > 32 || report_count > 0xFF) {
        return kBitCeiling;
    }
    return saturating_add(0, report_size * report_count);
}

void record_mouse_inputs(MouseReportState* mouse_reports,
                         std::size_t& mouse_report_count,
                         DroppedCandidateKeys& dropped,
                         std::uint8_t& rejected_overflow,
                         const KeyboardGlobalState& globals,
                         const KeyboardLocalState& locals,
                         std::size_t item_offset,
                         std::uint32_t first_bit,
                         std::uint32_t data) {
    if ((data & kInputConstant) != 0 || (data & kInputVariable) == 0) {
        return;
    }

    const std::uint32_t bits = bounded_field_bits(globals.report_size,
                                                  globals.report_count);
    if (globals.usage_page == kPageButton) {
        MouseReportState* candidate = mouse_report(
            mouse_reports, mouse_report_count, globals.report_id,
            globals.report_id_valid, dropped, rejected_overflow, item_offset);
        if (candidate != nullptr) {
            candidate->invalid = candidate->invalid || !globals.report_id_valid;
            extend_buttons(candidate->report.buttons, first_bit, bits);
        }
        return;
    }

    MouseReportState* candidate = nullptr;
    for (std::uint32_t index = 0;
         index < globals.report_count && index < kMaximumKeyboardUsages;
         ++index) {
        const std::size_t pick =
            index < locals.usage_count
                ? static_cast<std::size_t>(index)
                : (locals.usage_count == 0 ? kMaximumKeyboardUsages
                                           : locals.usage_count - 1);
        if (pick >= kMaximumKeyboardUsages) {
            break;
        }
        const KeyboardUsage usage = locals.usages[pick];
        const bool is_mouse_usage =
            (usage.page == kPageGenericDesktop &&
             (usage.value == kUsageX || usage.value == kUsageY ||
              usage.value == kUsageWheel)) ||
            (usage.page == kPageConsumer && usage.value == kUsageAcPan);
        if (!is_mouse_usage) {
            continue;
        }
        if (candidate == nullptr) {
            candidate = mouse_report(mouse_reports, mouse_report_count,
                                     globals.report_id, globals.report_id_valid,
                                     dropped, rejected_overflow, item_offset);
            if (candidate == nullptr) {
                return;
            }
            candidate->invalid = candidate->invalid || !globals.report_id_valid;
        }

        const std::uint64_t raw_offset =
            static_cast<std::uint64_t>(first_bit) +
            static_cast<std::uint64_t>(index) * globals.report_size;
        const std::uint32_t offset =
            raw_offset >= kBitCeiling ? kBitCeiling
                                      : static_cast<std::uint32_t>(raw_offset);
        if (usage.page == kPageGenericDesktop && usage.value == kUsageX) {
            record(candidate->report.x, offset, globals.report_size);
        } else if (usage.page == kPageGenericDesktop && usage.value == kUsageY) {
            record(candidate->report.y, offset, globals.report_size);
        } else if (usage.page == kPageGenericDesktop &&
                   usage.value == kUsageWheel) {
            record(candidate->report.wheel, offset, globals.report_size);
        } else {
            record(candidate->report.pan, offset, globals.report_size);
        }
    }
}

void record_usage_input(KeyboardReportState* reports,
                        std::size_t& report_count,
                        ReportRole role,
                        DroppedCandidateKeys& dropped,
                        std::uint8_t& rejected_overflow,
                        const KeyboardGlobalState& globals,
                        const KeyboardLocalState& locals,
                        std::size_t item_offset,
                        std::uint32_t first_bit,
                        bool cursor_is_bounded,
                        std::uint32_t data) {
    const std::uint16_t page =
        role == ReportRole::Keyboard ? kPageKeyboard : kPageConsumer;
    const bool constant = (data & kInputConstant) != 0;
    if (constant || !input_mentions_page(locals, globals.usage_page, page) ||
        (role == ReportRole::Consumer && (data & 4U) != 0)) {
        return;
    }

    KeyboardReportState* report = usage_report(
        reports, report_count, role, globals.report_id,
        globals.report_id_valid, dropped, rejected_overflow, item_offset);
    if (report == nullptr) {
        return;
    }
    if (!globals.report_id_valid || globals.report_size == 0 ||
        globals.report_count == 0 || !cursor_is_bounded ||
        !logical_range_is_valid(globals) || !local_range_is_valid(locals)) {
        report->invalid = true;
        return;
    }

    const bool variable = (data & kInputVariable) != 0;
    std::uint16_t usage_minimum = 0;
    std::uint16_t usage_maximum = 0;
    const bool have_range =
        keyboard_range(locals, usage_minimum, usage_maximum, page);

    if (variable) {
        if (role == ReportRole::Keyboard &&
            !record_keyboard_modifiers(*report, locals, globals.report_size,
                                       globals.report_count, first_bit)) {
            report->invalid = true;
            return;
        }
        const bool modifier_range =
            role == ReportRole::Keyboard && have_range &&
            usage_minimum >= kModifierMinimum && usage_maximum <= kModifierMaximum;
        if (have_range && !modifier_range) {
            const std::uint32_t usage_count =
                static_cast<std::uint32_t>(usage_maximum) - usage_minimum + 1;
            const bool count_matches_range =
                globals.report_count == usage_count ||
                (usage_minimum == 0 && globals.report_count + 1 == usage_count);
            if (globals.report_size != 1 || !count_matches_range ||
                !record_keyboard_keys(*report, KeyboardFieldKind::Bitmap,
                                      first_bit, globals.report_size,
                                      globals.report_count, usage_minimum,
                                      usage_maximum)) {
                report->invalid = true;
            }
            return;
        }
        if (role == ReportRole::Consumer && !have_range &&
            locals.usage_count != 0 && locals.usages[0].page == page) {
            if (globals.report_size != 1 ||
                globals.report_count != locals.usage_count ||
                !record_keyboard_keys(*report, KeyboardFieldKind::Bitmap,
                                      first_bit, 1, globals.report_count, 0, 0)) {
                report->invalid = true;
                return;
            }
            report->layout.explicit_usage_count =
                static_cast<std::uint8_t>(locals.usage_count);
            for (std::size_t index = 0; index < locals.usage_count; ++index) {
                if (locals.usages[index].page != page) {
                    report->invalid = true;
                    return;
                }
                report->layout.explicit_usages[index] = locals.usages[index].value;
            }
        }
        return;
    }

    if (!have_range && globals.usage_page == page) {
        const std::int64_t maximum = logical_maximum(globals);
        if (globals.logical_minimum < 0 || maximum > 0xFFFF) {
            report->invalid = true;
            return;
        }
        usage_minimum = static_cast<std::uint16_t>(globals.logical_minimum);
        usage_maximum = static_cast<std::uint16_t>(maximum);
    }
    if (have_range || globals.usage_page == page) {
        // Keyboard array usages 0x01..0x03 are HID rollover/error indicators,
        // not key presses. Preserve the advertised range here so the bounded
        // normalizer can suppress those values without changing field offsets.
        if (globals.report_size > 16 ||
            !record_keyboard_keys(*report, KeyboardFieldKind::Array,
                                  first_bit, globals.report_size,
                                  globals.report_count, usage_minimum,
                                  usage_maximum)) {
            report->invalid = true;
        }
    }
}

bool complete_mouse_candidate(const MouseReportState& candidate,
                              bool uses_report_ids,
                              MouseReportLayout& layout,
                              ReportDescriptorError& error) {
    if (!candidate.report.describes_a_pointer()) {
        return false;
    }

    layout.report_id = uses_report_ids;
    layout.report_id_value = candidate.report.report_id;
    if (candidate.invalid || (uses_report_ids && candidate.report.report_id == 0) ||
        !to_bytes(candidate.report.buttons, true, layout.buttons) ||
        !to_bytes(candidate.report.x, false, layout.x) ||
        !to_bytes(candidate.report.y, false, layout.y) ||
        !to_bytes(candidate.report.wheel, false, layout.wheel) ||
        !to_bytes(candidate.report.pan, false, layout.pan) ||
        !layout.buttons.present) {
        error = ReportDescriptorError::UnsupportedLayout;
        return true;
    }

    std::uint8_t minimum = end_of(layout.buttons);
    if (end_of(layout.x) > minimum) {
        minimum = end_of(layout.x);
    }
    if (end_of(layout.y) > minimum) {
        minimum = end_of(layout.y);
    }
    layout.minimum_body_bytes = minimum;
    return true;
}

void sort_completed_candidates(CompletedCandidate* candidates, std::size_t count) {
    for (std::size_t index = 1; index < count; ++index) {
        std::size_t insert = index;
        while (insert > 0 &&
               candidates[insert].first_input_offset <
                   candidates[insert - 1].first_input_offset) {
            const CompletedCandidate swap = candidates[insert - 1];
            candidates[insert - 1] = candidates[insert];
            candidates[insert] = swap;
            --insert;
        }
    }
}

bool descriptor_has_invalid_report_id(protocol::ByteView descriptor) {
    std::size_t at = 0;
    while (at < descriptor.size) {
        const std::uint8_t prefix = descriptor.data[at];
        if (prefix == kLongItemPrefix) {
            const std::size_t length = descriptor.data[at + 1];
            at += 3 + length;
            continue;
        }
        const std::uint8_t stored = static_cast<std::uint8_t>(prefix & 0x03);
        const std::size_t length = stored == 3 ? 4 : stored;
        std::uint32_t data = 0;
        for (std::size_t index = 0; index < length; ++index) {
            data |= static_cast<std::uint32_t>(descriptor.data[at + 1 + index])
                    << (8 * index);
        }
        const std::uint8_t type = static_cast<std::uint8_t>((prefix >> 2) & 0x03);
        const std::uint8_t tag = static_cast<std::uint8_t>((prefix >> 4) & 0x0F);
        if (type == kTypeGlobal && tag == kTagReportId &&
            (data == 0 || data > 0xFF)) {
            return true;
        }
        at += 1 + length;
    }
    return false;
}

}  // namespace

ReportDescriptorError parse_hid_report_set(protocol::ByteView descriptor,
                                           HidReportSet& out) {
    if (descriptor.data == nullptr || descriptor.size == 0) {
        return ReportDescriptorError::Truncated;
    }
    if (g_core1_parser_scratch_active) {
        return ReportDescriptorError::MalformedGlobalState;
    }
    g_core1_parser_scratch_active = true;
    struct ScratchRelease {
        ~ScratchRelease() { g_core1_parser_scratch_active = false; }
    } scratch_release;

    ParserScratch& scratch = construct_parser_scratch();

    KeyboardGlobalState& globals = scratch.globals;
    KeyboardGlobalState* const global_stack = scratch.global_stack;
    std::size_t global_depth = 0;
    KeyboardLocalState& locals = scratch.locals;
    KeyboardReportState* const usage_reports = scratch.usage_reports;
    std::size_t usage_report_count = 0;
    MouseReportState* const mouse_reports = scratch.mouse_reports;
    std::size_t mouse_report_count = 0;
    InputCursor* const cursors = scratch.cursors;
    std::size_t cursor_count = 0;
    bool any_report_id = false;

    std::size_t at = 0;
    while (at < descriptor.size) {
        const std::size_t item_offset = at;
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
                    any_report_id = true;
                    globals.report_id_valid = data != 0 && data <= 0xFF;
                    globals.report_id = globals.report_id_valid
                                            ? static_cast<std::uint8_t>(data)
                                            : std::uint8_t{0};
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
            const std::uint64_t field_bits =
                static_cast<std::uint64_t>(globals.report_size) * globals.report_count;
            InputCursor* cursor = input_cursor(cursors, cursor_count,
                                               globals.report_id,
                                               globals.report_id_valid);
            const std::uint32_t first_bit =
                cursor == nullptr ? kBitCeiling : cursor->input_bits;
            const std::uint64_t bit_after_field =
                static_cast<std::uint64_t>(first_bit) + field_bits;
            const bool cursor_is_bounded =
                cursor != nullptr && field_bits <= kMaximumKeyboardReportBits &&
                bit_after_field <= kMaximumKeyboardReportBits;
            if (!cursor_is_bounded) {
                invalidate_usage_reports(usage_reports, usage_report_count,
                                         globals.report_id,
                                         globals.report_id_valid);
            }

            record_usage_input(usage_reports, usage_report_count,
                               ReportRole::Keyboard, scratch.usage_dropped[0],
                               scratch.parsed.rejected_overflow, globals, locals,
                               item_offset, first_bit, cursor_is_bounded, data);
            record_usage_input(usage_reports, usage_report_count,
                               ReportRole::Consumer, scratch.usage_dropped[1],
                               scratch.parsed.rejected_overflow, globals, locals,
                               item_offset, first_bit, cursor_is_bounded, data);
            record_mouse_inputs(mouse_reports, mouse_report_count,
                                scratch.mouse_dropped,
                                scratch.parsed.rejected_overflow, globals, locals,
                                item_offset, first_bit, data);

            if (cursor != nullptr) {
                const std::uint32_t increment =
                    field_bits >= kBitCeiling
                        ? kBitCeiling
                        : static_cast<std::uint32_t>(field_bits);
                cursor->input_bits = saturating_add(cursor->input_bits, increment);
            }
        }

        // HID local items apply to one Main item, including Output, Feature,
        // Collection, and End Collection. Only Input advances an Input cursor.
        ::new (static_cast<void*>(&locals)) KeyboardLocalState{};
    }

    CompletedCandidate* const completed = scratch.completed;
    std::size_t completed_count = 0;
    for (std::size_t index = 0; index < usage_report_count; ++index) {
        KeyboardReportState& report = usage_reports[index];
        if (!report.has_keys && !report.invalid) {
            continue;
        }
        CompletedCandidate& candidate = completed[completed_count++];
        candidate.first_input_offset = report.first_input_offset;
        candidate.role = report.role;
        candidate.source_index = static_cast<std::uint8_t>(index);
        if (report.invalid || !report.report_id_valid ||
            (any_report_id && report.report_id == 0)) {
            candidate.error = ReportDescriptorError::UnsupportedLayout;
        }
    }
    for (std::size_t index = 0; index < mouse_report_count; ++index) {
        ReportDescriptorError error = ReportDescriptorError::None;
        if (complete_mouse_candidate(mouse_reports[index], any_report_id,
                                     scratch.entry.mouse, error)) {
            CompletedCandidate& candidate = completed[completed_count++];
            candidate.first_input_offset = mouse_reports[index].first_input_offset;
            candidate.role = ReportRole::Mouse;
            candidate.source_index = static_cast<std::uint8_t>(index);
            candidate.error = error;
        }
    }
    sort_completed_candidates(completed, completed_count);

    HidReportSet& parsed = scratch.parsed;
    parsed.uses_report_ids = any_report_id;
    std::size_t accepted_count = 0;
    for (std::size_t index = 0; index < completed_count; ++index) {
        if (completed[index].error == ReportDescriptorError::None) {
            ++accepted_count;
        }
    }
    const bool ambiguous_unnumbered = !any_report_id && accepted_count > 1;
    for (std::size_t index = 0; index < completed_count; ++index) {
        const CompletedCandidate& candidate = completed[index];
        const std::uint8_t report_id =
            candidate.role == ReportRole::Mouse
                ? mouse_reports[candidate.source_index].report.report_id
                : usage_reports[candidate.source_index].report_id;
        if (candidate.error != ReportDescriptorError::None) {
            append_rejected(parsed, candidate.role, report_id, candidate.error);
        } else if (ambiguous_unnumbered) {
            append_rejected(parsed, candidate.role, report_id,
                            ReportDescriptorError::AmbiguousReportSet);
        } else {
            HidReportEntry& entry =
                *::new (static_cast<void*>(&scratch.entry)) HidReportEntry{};
            entry.role = candidate.role;
            entry.report_id = report_id;
            if (candidate.role == ReportRole::Mouse) {
                ReportDescriptorError ignored = ReportDescriptorError::None;
                complete_mouse_candidate(mouse_reports[candidate.source_index],
                                         any_report_id, entry.mouse, ignored);
            } else {
                const KeyboardReportState& report =
                    usage_reports[candidate.source_index];
                entry.keyboard = report.layout;
                entry.keyboard.report_id = any_report_id;
                entry.keyboard.minimum_body_bytes =
                    static_cast<std::uint8_t>((report.required_bits + 7) / 8);
            }
            append_accepted(parsed, entry);
        }
    }

    out = parsed;
    return ReportDescriptorError::None;
}

ReportDescriptorError parse_keyboard_report_descriptor(protocol::ByteView descriptor,
                                                       KeyboardReportLayout& out) {
    HidReportSet& set = construct_parser_result();
    const ReportDescriptorError error = parse_hid_report_set(descriptor, set);
    if (error != ReportDescriptorError::None) {
        return error;
    }
    const HidReportEntry* found = nullptr;
    for (std::size_t index = 0; index < set.count; ++index) {
        if (set.entries[index].role != ReportRole::Keyboard) {
            continue;
        }
        if (found != nullptr) {
            return ReportDescriptorError::AmbiguousKeyboardReport;
        }
        found = &set.entries[index];
    }
    if (found != nullptr) {
        out = found->keyboard;
        return ReportDescriptorError::None;
    }
    for (std::size_t index = 0; index < set.rejected_count; ++index) {
        if (set.rejected[index].role == ReportRole::Keyboard) {
            return set.rejected[index].reason == ReportDescriptorError::AmbiguousReportSet
                       ? ReportDescriptorError::AmbiguousKeyboardReport
                       : set.rejected[index].reason;
        }
    }
    return descriptor_has_invalid_report_id(descriptor)
               ? ReportDescriptorError::UnsupportedLayout
               : ReportDescriptorError::NoKeyboardReport;
}

ReportDescriptorError parse_consumer_report_descriptor(protocol::ByteView descriptor,
                                                       KeyboardReportLayout& out) {
    HidReportSet& set = construct_parser_result();
    const ReportDescriptorError error = parse_hid_report_set(descriptor, set);
    if (error != ReportDescriptorError::None) {
        return error;
    }
    const HidReportEntry* found = nullptr;
    for (std::size_t index = 0; index < set.count; ++index) {
        if (set.entries[index].role != ReportRole::Consumer) {
            continue;
        }
        if (found != nullptr) {
            return ReportDescriptorError::AmbiguousKeyboardReport;
        }
        found = &set.entries[index];
    }
    if (found != nullptr) {
        out = found->keyboard;
        return ReportDescriptorError::None;
    }
    for (std::size_t index = 0; index < set.rejected_count; ++index) {
        if (set.rejected[index].role == ReportRole::Consumer) {
            return set.rejected[index].reason == ReportDescriptorError::AmbiguousReportSet
                       ? ReportDescriptorError::AmbiguousKeyboardReport
                       : set.rejected[index].reason;
        }
    }
    return descriptor_has_invalid_report_id(descriptor)
               ? ReportDescriptorError::UnsupportedLayout
               : ReportDescriptorError::NoKeyboardReport;
}

ReportDescriptorError parse_mouse_report_descriptor(protocol::ByteView descriptor,
                                                    MouseReportLayout& out) {
    HidReportSet& set = construct_parser_result();
    const ReportDescriptorError error = parse_hid_report_set(descriptor, set);
    if (error != ReportDescriptorError::None) {
        return error;
    }
    const HidReportEntry* found = nullptr;
    for (std::size_t index = 0; index < set.count; ++index) {
        if (set.entries[index].role != ReportRole::Mouse) {
            continue;
        }
        if (found != nullptr) {
            return ReportDescriptorError::UnsupportedLayout;
        }
        found = &set.entries[index];
    }
    if (found != nullptr) {
        out = found->mouse;
        return ReportDescriptorError::None;
    }
    for (std::size_t index = 0; index < set.rejected_count; ++index) {
        if (set.rejected[index].role == ReportRole::Mouse) {
            return ReportDescriptorError::UnsupportedLayout;
        }
    }
    return ReportDescriptorError::NoMouseReport;
}

ReportDescriptorRole classify_report_descriptor(protocol::ByteView descriptor,
                                                 KeyboardReportLayout& keyboard,
                                                 MouseReportLayout& mouse) {
    HidReportSet& set = construct_parser_result();
    if (parse_hid_report_set(descriptor, set) != ReportDescriptorError::None) {
        return ReportDescriptorRole::None;
    }
    for (std::size_t index = 0; index < set.rejected_count; ++index) {
        if (set.rejected[index].reason == ReportDescriptorError::AmbiguousReportSet) {
            return ReportDescriptorRole::Ambiguous;
        }
    }

    // Match the legacy wrappers' exactly-one-per-role rule directly against
    // the completed set. Re-parsing through those wrappers kept extra layout
    // copies and nested parser frames on the small USB discovery stack.
    const HidReportEntry* keyboard_entry = nullptr;
    const HidReportEntry* mouse_entry = nullptr;
    std::size_t keyboard_count = 0;
    std::size_t mouse_count = 0;
    for (std::size_t index = 0; index < set.count; ++index) {
        const HidReportEntry& entry = set.entries[index];
        if (entry.role == ReportRole::Keyboard) {
            keyboard_entry = &entry;
            ++keyboard_count;
        } else if (entry.role == ReportRole::Mouse) {
            mouse_entry = &entry;
            ++mouse_count;
        }
    }
    const bool has_keyboard = keyboard_count == 1;
    const bool has_mouse = mouse_count == 1;

    if (has_keyboard && has_mouse) {
        return ReportDescriptorRole::Ambiguous;
    }
    if (has_keyboard) {
        keyboard = keyboard_entry->keyboard;
        return ReportDescriptorRole::Keyboard;
    }
    if (has_mouse) {
        mouse = mouse_entry->mouse;
        return ReportDescriptorRole::Mouse;
    }
    return ReportDescriptorRole::None;
}

}  // namespace duo_input::u1::input::hid
