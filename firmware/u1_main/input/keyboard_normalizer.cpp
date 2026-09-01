#include "input/keyboard_normalizer.hpp"

namespace duo_input::u1::input {
namespace {

bool contains(const std::uint8_t* set, std::size_t count, std::uint8_t usage) {
    for (std::size_t index = 0; index < count; ++index) {
        if (set[index] == usage) {
            return true;
        }
    }
    return false;
}

bool emit(InputEvent* out, std::size_t capacity, std::size_t& used, InputEventKind kind,
          std::uint16_t code) {
    if (used >= capacity) {
        return false;
    }
    InputEvent event;
    event.kind = kind;
    event.code = code;
    out[used++] = event;
    return true;
}

}  // namespace

std::size_t KeyboardNormalizer::apply(protocol::ByteView report, InputEvent* out,
                                      std::size_t capacity) {
    if (report.data == nullptr || report.size < kBootKeyboardReportSize || out == nullptr) {
        // Half a report is not a report. Read as a whole one it would release
        // every key currently held.
        return 0;
    }
    if (capacity < kMaxKeyboardEventsPerReport) {
        // No room for everything this report could mean. The same rule as the
        // line above, for the same reason: what cannot be delivered whole is
        // not delivered in part.
        //
        // Writing what fits and moving the state on regardless is the worse
        // half of it. The events that did not fit are ones the far computer
        // never received, and a release recorded as delivered is a key nothing
        // left will ever lift - while the caller, handed only a count, has no
        // way to tell that from a quiet report. Nothing is touched here, so
        // the next call with room says the whole of it.
        return 0;
    }

    std::size_t used = 0;

    // --- the keys -----------------------------------------------------------
    //
    // Gathered first, because a rollover report has to be recognised before
    // anything is compared against it.
    std::uint8_t now[kKeySlots] = {};
    std::uint8_t now_count = 0;
    std::uint8_t error_slots = 0;
    for (std::size_t slot = 0; slot < kKeySlots; ++slot) {
        const std::uint8_t usage = report.data[2 + slot];
        if (usage == kRollover) {
            // Not a usage anybody can press, so it never joins the set. How
            // many there are is what decides the report: HID 1.11 8.3 has a
            // keyboard that has lost count put this in *every* array field,
            // and only that is the keyboard saying so.
            ++error_slots;
            continue;
        }
        if (usage != 0 && !contains(now, now_count, usage)) {
            now[now_count++] = usage;
        }
    }

    // Every slot: too many keys at once for the matrix, and the report says
    // nothing about what is held. It is not six new keys, and believing it
    // releases what is really down and presses one that does not exist.
    //
    // One slot is a different thing entirely, and treating it as this one is
    // what sent us looking. An Aula F75's 2.4 GHz receiver puts a lone 0x01
    // in an otherwise empty report about nine times in every hundred it
    // sends - where a wired keyboard sends an empty one - so freezing on it
    // threw away nine per cent of this keyboard's reports. A thrown-away
    // release leaves the key held, and the computer repeats it until the next
    // keystroke; a thrown-away press is a letter that never arrives at all.
    const bool rollover = error_slots == kKeySlots;

    if (!rollover) {
        for (std::size_t index = 0; index < held_count_; ++index) {
            if (!contains(now, now_count, held_[index])) {
                emit(out, capacity, used, InputEventKind::KeyUp, held_[index]);
            }
        }
        for (std::size_t index = 0; index < now_count; ++index) {
            if (!contains(held_, held_count_, now[index])) {
                emit(out, capacity, used, InputEventKind::KeyDown, now[index]);
            }
        }
        for (std::size_t index = 0; index < now_count; ++index) {
            held_[index] = now[index];
        }
        held_count_ = now_count;
    }

    // --- the modifiers ------------------------------------------------------
    //
    // Reported as a separate byte, delivered as ordinary keys. A modifier that
    // never arrives is a shift that never happens.
    const std::uint8_t modifiers = report.data[0];
    const std::uint8_t changed = static_cast<std::uint8_t>(modifiers ^ modifiers_);
    for (std::uint8_t bit = 0; bit < 8; ++bit) {
        const std::uint8_t mask = static_cast<std::uint8_t>(1u << bit);
        if ((changed & mask) == 0) {
            continue;
        }
        const std::uint16_t usage = static_cast<std::uint16_t>(kFirstModifierUsage + bit);
        emit(out, capacity, used,
             (modifiers & mask) != 0 ? InputEventKind::KeyDown : InputEventKind::KeyUp, usage);
    }
    modifiers_ = modifiers;

    return used;
}

std::size_t KeyboardNormalizer::release_all(InputEvent* out, std::size_t capacity) {
    if (out == nullptr || capacity < kMaxKeyboardEventsPerReport) {
        // Refused whole rather than done in part, and nothing is cleared, so a
        // caller with room can still do it.
        //
        // This is the one path that exists so an unplugged keyboard cannot
        // leave keys held on a computer the operator has no way to reach. A
        // partial release that then forgot what it had not said would be that
        // exact fault with the evidence taken away.
        return 0;
    }
    std::size_t used = 0;

    for (std::size_t index = 0; index < held_count_; ++index) {
        emit(out, capacity, used, InputEventKind::KeyUp, held_[index]);
    }
    held_count_ = 0;

    for (std::uint8_t bit = 0; bit < 8; ++bit) {
        const std::uint8_t mask = static_cast<std::uint8_t>(1u << bit);
        if ((modifiers_ & mask) != 0) {
            emit(out, capacity, used, InputEventKind::KeyUp,
                 static_cast<std::uint16_t>(kFirstModifierUsage + bit));
        }
    }
    modifiers_ = 0;

    return used;
}

}  // namespace duo_input::u1::input
