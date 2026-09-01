#pragma once

// A boot keyboard report, turned into presses and releases.
//
// The report is a set, not a sequence: modifiers in the first byte, then six
// slots holding whatever is currently down, in whatever order the keyboard
// feels like. Two reports with the same six keys arranged differently mean
// nobody did anything - so this compares contents, never positions. Comparing
// positions releases and re-presses every key somebody is holding, which
// arrives on their computer as a stutter of characters they did not type.

#include <cstddef>
#include <cstdint>

#include "input/events.hpp"
#include "protocol/bytes.hpp"

namespace duo_input::u1::input {

/// Modifiers, a reserved byte, six key slots.
inline constexpr std::size_t kBootKeyboardReportSize = 8;
inline constexpr std::size_t kKeySlots = 6;

/// The usage of the first modifier. Bit 0 of the modifier byte is 0xE0, bit 1
/// is 0xE1, and so on to 0xE7.
inline constexpr std::uint16_t kFirstModifierUsage = 0xE0;

/// What a keyboard puts in every slot when it cannot say what is held.
inline constexpr std::uint8_t kRollover = 0x01;

/// The most events one report can produce, and so the least room a caller may
/// offer either of the calls below.
///
/// Six keys let go and six pressed - the report has no more slots - with all
/// eight modifiers changing alongside them. Derived rather than chosen,
/// because what lies below it is not a smaller buffer: it is a report applied
/// in part, which is a state no keyboard was ever in.
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
    /// Read one report and write out what changed.
    ///
    /// Returns how many events were written. A report that is too short is
    /// ignored entirely: half a report read as a whole one releases every key
    /// the user is holding.
    std::size_t apply(protocol::ByteView report, InputEvent* out, std::size_t capacity);

    /// Release everything this keyboard is remembered to be holding.
    ///
    /// For when the device goes away. Nothing else will ever say these came
    /// up, and a held modifier changes what every later keystroke means on a
    /// computer the person cannot reach from here.
    std::size_t release_all(InputEvent* out, std::size_t capacity);

private:
    std::uint8_t modifiers_ = 0;
    std::uint8_t held_[kKeySlots] = {};
    std::uint8_t held_count_ = 0;
};

}  // namespace duo_input::u1::input
