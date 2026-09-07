#pragma once

// A keyboard report, turned into presses and releases.
//
// The report is a set, not a sequence: modifiers somewhere, then the keys that
// are currently down, in whatever order the keyboard feels like. Two reports
// with the same keys arranged differently mean nobody did anything - so this
// compares contents, never positions. Comparing positions releases and
// re-presses every key somebody is holding, which arrives on their computer as
// a stutter of characters they did not type.
//
// Where those fields sit is not a constant. Boot protocol's eight-byte report
// is one layout among several, and it is the only one a device can be forced
// into rather than asked about. A keyboard that leads its reports with an
// identifier, that declares five slots rather than six, or that sends one bit
// per key instead of an array of usages, is describing a different report -
// and reading it at the boot offsets puts a vendor byte where a key should be
// and drops the keys that were really struck. So the layout comes in from the
// device's own report descriptor, and boot's is what is assumed only until one
// arrives.

#include <cstddef>
#include <cstdint>

#include "ch375/report_descriptor.hpp"
#include "input/events.hpp"
#include "protocol/bytes.hpp"

namespace duo_input::u1::input {

/// Modifiers, a reserved byte, six key slots.
inline constexpr std::size_t kBootKeyboardReportSize = 8;

/// How many keys this firmware can carry at once.
///
/// Not a property of any keyboard - an NKRO one can hold far more - but of the
/// six-key report U2 sends onward. A report holding more than this cannot be
/// passed on whole, and passing on part of it is a state nobody's hands were
/// ever in.
inline constexpr std::size_t kKeySlots = 6;

/// The usage of the first modifier. Bit 0 of the modifier byte is 0xE0, bit 1
/// is 0xE1, and so on to 0xE7.
inline constexpr std::uint16_t kFirstModifierUsage = 0xE0;

/// What a keyboard puts in every slot when it cannot say what is held.
inline constexpr std::uint8_t kRollover = 0x01;

/// The widest array element the bounded reader will take.
inline constexpr std::uint8_t kMaxKeyElementBits = 16;

/// The most events one report can produce, and so the least room a caller may
/// offer either of the calls below.
///
/// Six keys let go and six pressed - more than six at once is refused whole,
/// so there are never more - with all eight modifiers changing alongside them.
/// Derived rather than chosen, because what lies below it is not a smaller
/// buffer: it is a report applied in part, which is a state no keyboard was
/// ever in.
///
/// One number for both calls rather than one each. release_all needs fourteen
/// and apply needs twenty, and a caller that sized its buffer for the smaller
/// and then made the other call would be exactly the defect this exists to
/// refuse.
inline constexpr std::size_t kMaxKeyboardEventsPerReport = kKeySlots * 2 + 8;

// The buffer every caller actually passes. A shared constant that drifted
// below what a keyboard can produce would not announce itself: it would
// truncate on the one report that needed the room and strand a key on
// somebody else's computer. So it stops at the compiler instead.
static_assert(kMaxEventsPerReport >= kMaxKeyboardEventsPerReport,
              "the shared event buffer must hold the worst keyboard report");

class KeyboardNormalizer {
public:
    /// Read reports at the offsets this device declared for them.
    ///
    /// Until this is called the boot layout is assumed, which is what every
    /// keyboard was read under before descriptors were fetched at all.
    void set_layout(const ch375::KeyboardReportLayout& layout);

    /// Read one report and write out what changed.
    ///
    /// Returns how many events were written. A report that is too short, that
    /// carries another report's identifier, or that holds more keys than can
    /// be passed on is ignored entirely: half a report read as a whole one
    /// releases every key the user is holding.
    std::size_t apply(protocol::ByteView report, InputEvent* out, std::size_t capacity);

    /// Release everything this keyboard is remembered to be holding.
    ///
    /// For when the device goes away. Nothing else will ever say these came
    /// up, and a held modifier changes what every later keystroke means on a
    /// computer the person cannot reach from here.
    std::size_t release_all(InputEvent* out, std::size_t capacity);

#if DUO_CH375_PROBE
    /// How many reports carried exactly N ErrorRollOver values in their key
    /// field, for N of 0 through 7.
    ///
    /// The question this exists to answer: HID 1.11 8.3 says a keyboard that
    /// has lost count puts ErrorRollOver in *every* array field, and the Aula
    /// declares five of them - so "every" is five here, not the six a boot
    /// report has. If index 5 is climbing while typing, reports that mean
    /// "I cannot say what is held" are being read as "nothing is held".
    std::uint16_t probe_error_slots(unsigned count) const {
        return count < 8 ? probe_error_slots_[count] : 0;
    }
    /// Reports refused whole because they held more keys than can be carried.
    std::uint16_t probe_refused() const { return probe_refused_; }
    /// Reports recognised as a rollover and frozen on.
    std::uint16_t probe_rollovers() const { return probe_rollovers_; }
    /// Reports refused because they were short, mislabelled, or unreadable.
    std::uint16_t probe_rejected() const { return probe_rejected_; }
    /// Key presses this normalizer actually emitted, modifiers counted apart.
    ///
    /// The number to compare against what arrived on the far computer. Equal
    /// means everything read here was handed on and the loss is further down
    /// the line; short means the keystroke never reached this code at all.
    std::uint16_t probe_key_downs() const { return probe_key_downs_; }
    std::uint16_t probe_key_ups() const { return probe_key_ups_; }
    std::uint16_t probe_modifier_downs() const { return probe_modifier_downs_; }
#endif

private:
    ch375::KeyboardReportLayout layout_ = ch375::boot_keyboard_layout();
    std::uint8_t modifiers_ = 0;
    std::uint16_t held_[kKeySlots] = {};
    std::uint8_t held_count_ = 0;
#if DUO_CH375_PROBE
    std::uint16_t probe_error_slots_[8] = {};
    std::uint16_t probe_refused_ = 0;
    std::uint16_t probe_rollovers_ = 0;
    std::uint16_t probe_rejected_ = 0;
    std::uint16_t probe_key_downs_ = 0;
    std::uint16_t probe_key_ups_ = 0;
    std::uint16_t probe_modifier_downs_ = 0;
#endif
};

}  // namespace duo_input::u1::input
