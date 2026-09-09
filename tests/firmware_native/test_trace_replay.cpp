// Replaying reports captured off real devices, and asserting what they mean.
//
// Every other normalizer test writes its own reports. Those prove the code
// does what its author believed a device does; they cannot prove that belief.
// This one replays `tests/vectors/hid_reports/`, which was recorded off a real
// keyboard and a real mouse through the probe build's diagnostics, and pins
// the exact event stream each corpus produces - kinds, codes, and for the
// mouse the signed deltas.
//
// Two things the corpus's own metadata forces on this test:
//
//   - **Only the first four bytes of each report were observed.** The
//     diagnostics carry a packet's size and its first four bytes, nothing
//     more. A boot mouse report is three bytes, so those are whole. A boot
//     keyboard report is eight, so what was seen is the modifier byte, the
//     reserved byte, and the first two of six key slots - and nothing at all
//     about slots three to six.
//
//     Keyboard reports are therefore PADDED to eight bytes with zeros in the
//     unobserved slots, and mouse reports are TRUNCATED to the three bytes
//     the device actually sent, discarding the capture record's fourth byte,
//     which is the record's own padding and not something the mouse
//     transmitted. The consequence is stated plainly: this test asserts the
//     event stream the captured prefix implies, not the stream the device
//     produced. If the operator had six keys down at once, the corpus cannot
//     say so and neither can this.
//
//   - The corpus is keyed on the device kind read from its own descriptor,
//     not on which channel it arrived over, because the two channel names are
//     crossed on this bench.
//
// The corpus is a set of distinct reports in the order each was first seen,
// so replaying it in file order through one normalizer is a legitimate
// sequence: no report repeats immediately, and every transition between
// neighbours is one the device really made at some point.

#include "input/keyboard_normalizer.hpp"
#include "input/mouse_normalizer.hpp"
#include "test_support.hpp"

#include <cstddef>
#include <cstdint>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

using duo_input::u1::input::InputEvent;
using duo_input::u1::input::InputEventKind;
using duo_input::u1::input::kBootKeyboardReportSize;
using duo_input::u1::input::kBootMouseReportSize;
using duo_input::u1::input::KeyboardNormalizer;
using duo_input::u1::input::kMaxEventsPerReport;
using duo_input::u1::input::MouseNormalizer;

namespace {

/// One line of the corpus: the size the device sent, and the four bytes seen.
struct CapturedReport {
    std::size_t size = 0;
    std::uint8_t observed[4] = {};
};

/// What one replayed report is expected to produce.
///
/// `report` is the index into the corpus, carried so a failure names the
/// report that broke rather than an offset into a flat stream.
struct ExpectedEvent {
    std::size_t report;
    InputEventKind kind;
    std::uint16_t code;
    std::int16_t x;
    std::int16_t y;
};

std::string read_document(const char* path) {
    std::ifstream input(path);
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

/// Read a decimal integer at or after `offset`, stepping over separators.
bool read_number(const std::string& document, std::size_t& offset, long& value) {
    while (offset < document.size() &&
           (document[offset] == ' ' || document[offset] == '\n' || document[offset] == '\r' ||
            document[offset] == '\t' || document[offset] == ',')) {
        ++offset;
    }
    if (offset >= document.size() || document[offset] < '0' || document[offset] > '9') {
        return false;
    }
    value = 0;
    while (offset < document.size() && document[offset] >= '0' && document[offset] <= '9') {
        value = value * 10 + (document[offset] - '0');
        ++offset;
    }
    return true;
}

/// Pull every size/bytes pair out of the corpus, in file order.
///
/// Deliberately not a general JSON reader: this file is committed data with a
/// fixed shape, and a parser with opinions would be more code than the test.
std::vector<CapturedReport> parse_corpus(const std::string& document) {
    std::vector<CapturedReport> reports;
    std::size_t offset = 0;
    while (true) {
        const std::size_t size_at = document.find("\"size\":", offset);
        if (size_at == std::string::npos) {
            break;
        }
        std::size_t cursor = size_at + 7;
        long size = 0;
        if (!read_number(document, cursor, size)) {
            break;
        }

        const std::size_t bytes_at = document.find("\"bytes\":", cursor);
        if (bytes_at == std::string::npos) {
            break;
        }
        cursor = document.find('[', bytes_at);
        if (cursor == std::string::npos) {
            break;
        }
        ++cursor;

        CapturedReport report;
        report.size = static_cast<std::size_t>(size);
        bool complete = true;
        for (std::size_t index = 0; index < 4; ++index) {
            long value = 0;
            if (!read_number(document, cursor, value)) {
                complete = false;
                break;
            }
            report.observed[index] = static_cast<std::uint8_t>(value);
        }
        if (!complete) {
            break;
        }
        reports.push_back(report);
        offset = cursor;
    }
    return reports;
}

/// One event stream, gathered across a whole corpus.
struct Replayed {
    std::vector<ExpectedEvent> events;
};

void collect(Replayed& into, std::size_t report_index, const InputEvent* events,
             std::size_t count) {
    for (std::size_t index = 0; index < count; ++index) {
        into.events.push_back(ExpectedEvent{report_index, events[index].kind, events[index].code,
                                            events[index].x, events[index].y});
    }
}

/// Compare a replayed stream against the expected one, event by event.
void check_stream(const Replayed& actual, const ExpectedEvent* expected,
                  std::size_t expected_count) {
    CHECK_EQ(actual.events.size(), expected_count);
    const std::size_t common =
        actual.events.size() < expected_count ? actual.events.size() : expected_count;
    for (std::size_t index = 0; index < common; ++index) {
        CHECK_EQ(actual.events[index].report, expected[index].report);
        CHECK(actual.events[index].kind == expected[index].kind);
        CHECK_EQ(actual.events[index].code, expected[index].code);
        CHECK_EQ(actual.events[index].x, expected[index].x);
        CHECK_EQ(actual.events[index].y, expected[index].y);
    }
}

int count_of(const Replayed& stream, InputEventKind kind) {
    int seen = 0;
    for (std::size_t index = 0; index < stream.events.size(); ++index) {
        if (stream.events[index].kind == kind) {
            ++seen;
        }
    }
    return seen;
}

/// Replay the keyboard corpus, padding each report to its full eight bytes.
Replayed replay_keyboard(const std::vector<CapturedReport>& reports) {
    Replayed stream;
    KeyboardNormalizer normalizer;
    for (std::size_t index = 0; index < reports.size(); ++index) {
        // PADDED, not truncated: the normalizer refuses a short report - and
        // rightly, since half a report read as a whole one releases everything
        // held - so the four observed bytes are placed at the front of an
        // eight-byte report and slots three to six are left at zero. Those
        // four zeros are this test's assumption, not the device's data.
        std::uint8_t report[kBootKeyboardReportSize] = {};
        for (std::size_t byte = 0; byte < 4; ++byte) {
            report[byte] = reports[index].observed[byte];
        }
        InputEvent events[kMaxEventsPerReport];
        const std::size_t count =
            normalizer.apply(duo_input::protocol::ByteView{report, kBootKeyboardReportSize}, events,
                             kMaxEventsPerReport);
        collect(stream, index, events, count);
    }
    return stream;
}

/// Replay the mouse corpus, truncated to the three bytes the device sent.
Replayed replay_mouse(const std::vector<CapturedReport>& reports) {
    Replayed stream;
    MouseNormalizer normalizer;
    for (std::size_t index = 0; index < reports.size(); ++index) {
        // TRUNCATED, not padded: every captured mouse report says size 3, so
        // the record's fourth byte belongs to the capture format and not to
        // the mouse. Handing it to the normalizer as a fourth report byte
        // would invent a wheel this device never sent.
        InputEvent events[kMaxEventsPerReport];
        const std::size_t count = normalizer.apply(
            duo_input::protocol::ByteView{reports[index].observed, kBootMouseReportSize}, events,
            kMaxEventsPerReport);
        collect(stream, index, events, count);
    }
    return stream;
}

std::vector<CapturedReport> keyboard_corpus() {
    return parse_corpus(read_document(DUO_KEYBOARD_TRACE_PATH));
}

std::vector<CapturedReport> mouse_corpus() {
    return parse_corpus(read_document(DUO_MOUSE_TRACE_PATH));
}

// The whole keyboard stream. Derived from what a boot keyboard report means -
// a set of usages, compared by contents - and not from reading the normalizer.
//
// Landmarks visible in it: report 9 is a modifier alone (02 00 00 00, left
// shift down, no key), report 12 is that modifier with a key (02 00 11 00),
// report 25 holds two usages at once (1C 17), and report 0 is all-released.
const ExpectedEvent kKeyboardExpected[] = {
    {1, InputEventKind::KeyDown, 0x44, 0, 0},
    {2, InputEventKind::KeyUp, 0x44, 0, 0},
    {2, InputEventKind::KeyDown, 0x28, 0, 0},
    {3, InputEventKind::KeyUp, 0x28, 0, 0},
    {3, InputEventKind::KeyDown, 0x0A, 0, 0},
    {4, InputEventKind::KeyUp, 0x0A, 0, 0},
    {4, InputEventKind::KeyDown, 0x0B, 0, 0},
    {5, InputEventKind::KeyUp, 0x0B, 0, 0},
    {5, InputEventKind::KeyDown, 0x05, 0, 0},
    {6, InputEventKind::KeyUp, 0x05, 0, 0},
    {6, InputEventKind::KeyDown, 0x07, 0, 0},
    {7, InputEventKind::KeyUp, 0x07, 0, 0},
    {7, InputEventKind::KeyDown, 0x17, 0, 0},
    {8, InputEventKind::KeyUp, 0x17, 0, 0},
    {8, InputEventKind::KeyDown, 0x11, 0, 0},
    {9, InputEventKind::KeyUp, 0x11, 0, 0},
    {9, InputEventKind::KeyDown, 0xE1, 0, 0},
    {10, InputEventKind::KeyUp, 0xE1, 0, 0},
    {10, InputEventKind::KeyDown, 0x15, 0, 0},
    {11, InputEventKind::KeyUp, 0x15, 0, 0},
    {11, InputEventKind::KeyDown, 0x2C, 0, 0},
    {12, InputEventKind::KeyUp, 0x2C, 0, 0},
    {12, InputEventKind::KeyDown, 0xE1, 0, 0},
    {12, InputEventKind::KeyDown, 0x11, 0, 0},
    {13, InputEventKind::KeyUp, 0x11, 0, 0},
    {13, InputEventKind::KeyUp, 0xE1, 0, 0},
    {13, InputEventKind::KeyDown, 0x1E, 0, 0},
    {14, InputEventKind::KeyUp, 0x1E, 0, 0},
    {14, InputEventKind::KeyDown, 0x20, 0, 0},
    {15, InputEventKind::KeyUp, 0x20, 0, 0},
    {15, InputEventKind::KeyDown, 0x22, 0, 0},
    {16, InputEventKind::KeyUp, 0x22, 0, 0},
    {16, InputEventKind::KeyDown, 0x23, 0, 0},
    {17, InputEventKind::KeyUp, 0x23, 0, 0},
    {17, InputEventKind::KeyDown, 0x24, 0, 0},
    {18, InputEventKind::KeyUp, 0x24, 0, 0},
    {18, InputEventKind::KeyDown, 0x42, 0, 0},
    {19, InputEventKind::KeyUp, 0x42, 0, 0},
    {19, InputEventKind::KeyDown, 0x1F, 0, 0},
    {20, InputEventKind::KeyUp, 0x1F, 0, 0},
    {20, InputEventKind::KeyDown, 0x43, 0, 0},
    {21, InputEventKind::KeyUp, 0x43, 0, 0},
    {21, InputEventKind::KeyDown, 0x09, 0, 0},
    {22, InputEventKind::KeyUp, 0x09, 0, 0},
    {22, InputEventKind::KeyDown, 0x25, 0, 0},
    {23, InputEventKind::KeyUp, 0x25, 0, 0},
    {23, InputEventKind::KeyDown, 0xE1, 0, 0},
    {23, InputEventKind::KeyDown, 0x05, 0, 0},
    {24, InputEventKind::KeyUp, 0x05, 0, 0},
    {24, InputEventKind::KeyUp, 0xE1, 0, 0},
    {24, InputEventKind::KeyDown, 0x2A, 0, 0},
    {25, InputEventKind::KeyUp, 0x2A, 0, 0},
    {25, InputEventKind::KeyDown, 0x1C, 0, 0},
    {25, InputEventKind::KeyDown, 0x17, 0, 0},
};

// The whole mouse stream: button edges, then the signed deltas of each report.
//
// Landmarks: report 12 is a left click with no movement (01 00 00), report 19
// is a report that moved nowhere and pressed nothing and therefore produces no
// event at all, report 36 presses button index 4 while moving, and both axes
// appear in both directions (report 3 is -46/+16, report 5 is +29/+44).
const ExpectedEvent kMouseExpected[] = {
    {0, InputEventKind::MouseMove, 0, -3, -1},
    {1, InputEventKind::MouseMove, 0, 1, -2},
    {2, InputEventKind::MouseMove, 0, -2, 0},
    {3, InputEventKind::MouseMove, 0, -46, 16},
    {4, InputEventKind::MouseMove, 0, -1, -14},
    {5, InputEventKind::MouseMove, 0, 29, 44},
    {6, InputEventKind::MouseMove, 0, -27, 7},
    {7, InputEventKind::MouseMove, 0, 0, -4},
    {8, InputEventKind::MouseMove, 0, 0, -1},
    {9, InputEventKind::MouseMove, 0, -2, 5},
    {10, InputEventKind::MouseMove, 0, 25, -9},
    {11, InputEventKind::MouseMove, 0, 6, -4},
    {12, InputEventKind::MouseButtonDown, 0, 0, 0},
    {13, InputEventKind::MouseButtonUp, 0, 0, 0},
    {13, InputEventKind::MouseMove, 0, -4, 10},
    {14, InputEventKind::MouseMove, 0, -1, 2},
    {15, InputEventKind::MouseMove, 0, -2, -2},
    {16, InputEventKind::MouseMove, 0, 0, -2},
    {17, InputEventKind::MouseMove, 0, 1, -1},
    {18, InputEventKind::MouseMove, 0, 3, 0},
    {20, InputEventKind::MouseMove, 0, -18, -9},
    {21, InputEventKind::MouseMove, 0, -3, -7},
    {22, InputEventKind::MouseMove, 0, -18, 24},
    {23, InputEventKind::MouseMove, 0, 14, -2},
    {24, InputEventKind::MouseMove, 0, 1, 2},
    {25, InputEventKind::MouseMove, 0, 0, -3},
    {26, InputEventKind::MouseMove, 0, -1, -2},
    {27, InputEventKind::MouseMove, 0, -7, -3},
    {28, InputEventKind::MouseButtonDown, 1, 0, 0},
    {29, InputEventKind::MouseButtonUp, 1, 0, 0},
    {29, InputEventKind::MouseMove, 0, -2, 1},
    {30, InputEventKind::MouseMove, 0, -1, -3},
    {31, InputEventKind::MouseMove, 0, 4, -2},
    {32, InputEventKind::MouseMove, 0, 2, 0},
    {33, InputEventKind::MouseMove, 0, -4, 3},
    {34, InputEventKind::MouseMove, 0, -1, 0},
    {35, InputEventKind::MouseMove, 0, 1, 0},
    {36, InputEventKind::MouseButtonDown, 4, 0, 0},
    {36, InputEventKind::MouseMove, 0, 2, 1},
    {37, InputEventKind::MouseButtonUp, 4, 0, 0},
    {37, InputEventKind::MouseMove, 0, 13, -16},
    {38, InputEventKind::MouseMove, 0, 4, 12},
    {39, InputEventKind::MouseMove, 0, -30, -21},
    {40, InputEventKind::MouseMove, 0, 21, 7},
    {41, InputEventKind::MouseMove, 0, -27, 15},
    {42, InputEventKind::MouseMove, 0, -41, 2},
    {43, InputEventKind::MouseMove, 0, -6, -7},
    {44, InputEventKind::MouseMove, 0, 26, 15},
    {45, InputEventKind::MouseMove, 0, -3, -9},
    {46, InputEventKind::MouseMove, 0, -9, 4},
    {47, InputEventKind::MouseMove, 0, 21, -13},
    {48, InputEventKind::MouseMove, 0, 2, -2},
    {49, InputEventKind::MouseMove, 0, -10, -1},
    {50, InputEventKind::MouseMove, 0, 2, -5},
    {51, InputEventKind::MouseMove, 0, 3, -4},
    {52, InputEventKind::MouseMove, 0, 3, -8},
    {53, InputEventKind::MouseMove, 0, 2, -1},
    {54, InputEventKind::MouseMove, 0, -20, 1},
    {55, InputEventKind::MouseMove, 0, -5, 0},
    {56, InputEventKind::MouseMove, 0, -12, 2},
    {57, InputEventKind::MouseMove, 0, -7, 1},
    {58, InputEventKind::MouseMove, 0, 16, -7},
};

}  // namespace

TEST_CASE(captured_keyboard_corpus_is_the_one_that_was_committed) {
    // If the corpus is ever regenerated, the expected stream below is stale
    // and every assertion after this one is meaningless. Fail here instead.
    const std::vector<CapturedReport> reports = keyboard_corpus();
    CHECK_EQ(reports.size(), static_cast<std::size_t>(26));
    for (std::size_t index = 0; index < reports.size(); ++index) {
        CHECK_EQ(reports[index].size, kBootKeyboardReportSize);
    }
}

TEST_CASE(captured_mouse_corpus_is_the_one_that_was_committed) {
    const std::vector<CapturedReport> reports = mouse_corpus();
    CHECK_EQ(reports.size(), static_cast<std::size_t>(59));
    for (std::size_t index = 0; index < reports.size(); ++index) {
        // Three bytes, every one of them. This is the wheel's absence stated
        // as data: a boot mouse report is buttons, dX, dY and nothing else.
        CHECK_EQ(reports[index].size, kBootMouseReportSize);
    }
}

TEST_CASE(captured_keyboard_reports_normalize_to_the_expected_events) {
    const Replayed stream = replay_keyboard(keyboard_corpus());
    check_stream(stream, kKeyboardExpected,
                 sizeof(kKeyboardExpected) / sizeof(kKeyboardExpected[0]));
}

TEST_CASE(captured_mouse_reports_normalize_to_the_expected_events) {
    const Replayed stream = replay_mouse(mouse_corpus());
    check_stream(stream, kMouseExpected, sizeof(kMouseExpected) / sizeof(kMouseExpected[0]));
}

TEST_CASE(captured_keyboard_modifier_alone_presses_only_the_modifier) {
    // Report 9 is 02 00 00 00: left shift down, no key. A normalizer that read
    // the modifier byte as a usage, or ignored it, would show here. Exactly
    // two events: the key report 8 held comes up, and left shift goes down.
    const Replayed stream = replay_keyboard(keyboard_corpus());
    int modifier_down = 0;
    int key_up = 0;
    int events = 0;
    for (std::size_t index = 0; index < stream.events.size(); ++index) {
        if (stream.events[index].report != 9) {
            continue;
        }
        ++events;
        if (stream.events[index].kind == InputEventKind::KeyDown &&
            stream.events[index].code == 0xE1) {
            ++modifier_down;
        }
        if (stream.events[index].kind == InputEventKind::KeyUp &&
            stream.events[index].code == 0x11) {
            ++key_up;
        }
    }
    CHECK_EQ(modifier_down, 1);
    CHECK_EQ(key_up, 1);
    CHECK_EQ(events, 2);
}

TEST_CASE(captured_keyboard_modifier_with_key_presses_both) {
    // Report 12 is 02 00 11 00: left shift and a key in the same report. Both
    // have to arrive, because a shift that never arrives types the wrong
    // character on somebody's computer.
    const Replayed stream = replay_keyboard(keyboard_corpus());
    bool key = false;
    bool modifier = false;
    for (std::size_t index = 0; index < stream.events.size(); ++index) {
        if (stream.events[index].report != 12 ||
            stream.events[index].kind != InputEventKind::KeyDown) {
            continue;
        }
        key = key || stream.events[index].code == 0x11;
        modifier = modifier || stream.events[index].code == 0xE1;
    }
    CHECK(key);
    CHECK(modifier);
}

TEST_CASE(captured_keyboard_all_released_report_says_nothing) {
    // Report 0 is 00 00 00 00 arriving first, with nothing held. A normalizer
    // that emitted releases for keys it never saw pressed would show here.
    const Replayed stream = replay_keyboard(keyboard_corpus());
    for (std::size_t index = 0; index < stream.events.size(); ++index) {
        CHECK(stream.events[index].report != 0);
    }
}

TEST_CASE(captured_mouse_deltas_are_read_as_signed) {
    // Report 3 is 00 D2 10: -46 and +16. Read unsigned, that -46 becomes 210
    // and a small step left becomes a leap across the screen.
    const Replayed stream = replay_mouse(mouse_corpus());
    bool seen = false;
    for (std::size_t index = 0; index < stream.events.size(); ++index) {
        if (stream.events[index].report == 3 &&
            stream.events[index].kind == InputEventKind::MouseMove) {
            CHECK_EQ(stream.events[index].x, static_cast<std::int16_t>(-46));
            CHECK_EQ(stream.events[index].y, static_cast<std::int16_t>(16));
            seen = true;
        }
    }
    CHECK(seen);
}

TEST_CASE(captured_mouse_button_without_movement_produces_one_edge) {
    // Report 12 is 01 00 00: a click, standing still. It must be one button
    // edge and no movement event, since a polled mouse reports "nowhere"
    // constantly and a movement event for each would flood the link.
    const Replayed stream = replay_mouse(mouse_corpus());
    int events = 0;
    for (std::size_t index = 0; index < stream.events.size(); ++index) {
        if (stream.events[index].report != 12) {
            continue;
        }
        ++events;
        CHECK(stream.events[index].kind == InputEventKind::MouseButtonDown);
        CHECK_EQ(stream.events[index].code, static_cast<std::uint16_t>(0));
    }
    CHECK_EQ(events, 1);
}

TEST_CASE(captured_mouse_reports_carry_no_wheel) {
    // Not an omission in the test: the boot-protocol report the device was
    // asked for is three bytes and has no wheel byte at all. This is the limit
    // recorded in docs/hardware/ch375-compatibility.md, asserted against the
    // capture that established it.
    const Replayed stream = replay_mouse(mouse_corpus());
    CHECK_EQ(count_of(stream, InputEventKind::Wheel), 0);
}
