#include "input/keyboard_normalizer.hpp"

namespace duo_input::u1::input {
namespace {

/// One more than can be carried, so a seventh key is counted rather than lost.
///
/// Stopping the scan at six would make an overflowing report indistinguishable
/// from a full one, and the difference is the whole decision: six keys are
/// delivered, seven are refused so the six that were really held stay held.
constexpr std::size_t kGatherSlots = kKeySlots + 1;

bool contains(const std::uint16_t* set, std::size_t count, std::uint16_t usage) {
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

/// Read `count` bits starting at `first`, lowest bit of the field first.
///
/// HID packs fields from bit 0 of byte 0 upward, so a sixteen-bit value that
/// straddles a byte boundary is little-endian across it. The caller has
/// already proved the span is inside the report.
std::uint32_t read_bits(const std::uint8_t* body, std::uint32_t first, std::uint8_t count) {
    std::uint32_t value = 0;
    for (std::uint8_t index = 0; index < count; ++index) {
        const std::uint32_t bit = first + index;
        const std::uint32_t set = (body[bit / 8] >> (bit % 8)) & 1u;
        value |= set << index;
    }
    return value;
}

/// The shortest body this layout can be read out of, in bits.
///
/// Measured from the fields themselves rather than from the layout's declared
/// `minimum_body_bytes`, which is not consulted here at all. A layout is a
/// plain struct built out of bytes a stranger sent, and a declared length that
/// disagrees with the offsets beside it is exactly the case that matters: a
/// field reaching past that length is read out of memory this report does not
/// own. Where the two agree - which is every layout the parser builds - this
/// is the same number.
std::uint32_t needed_body_bits(const ch375::KeyboardReportLayout& layout) {
    std::uint32_t needed = 0;

    const std::uint32_t keys_end =
        static_cast<std::uint32_t>(layout.key_bit_offset) +
        static_cast<std::uint32_t>(layout.key_element_bits) *
            static_cast<std::uint32_t>(layout.key_element_count);
    if (keys_end > needed) {
        needed = keys_end;
    }

    for (std::uint8_t bit = 0; bit < 8; ++bit) {
        const std::uint16_t at = layout.modifier_bits[bit];
        if (at != ch375::kNoKeyboardBit && static_cast<std::uint32_t>(at) + 1u > needed) {
            needed = static_cast<std::uint32_t>(at) + 1u;
        }
    }
    return needed;
}

}  // namespace

void KeyboardNormalizer::set_layout(const ch375::KeyboardReportLayout& layout) {
    layout_ = layout;
}

std::size_t KeyboardNormalizer::apply(protocol::ByteView report, InputEvent* out,
                                      std::size_t capacity) {
    if (report.data == nullptr || out == nullptr) {
        return 0;
    }
    if (capacity < kMaxKeyboardEventsPerReport) {
        // No room for everything this report could mean. What cannot be
        // delivered whole is not delivered in part.
        //
        // Writing what fits and moving the state on regardless is the worse
        // half of it. The events that did not fit are ones the far computer
        // never received, and a release recorded as delivered is a key nothing
        // left will ever lift - while the caller, handed only a count, has no
        // way to tell that from a quiet report. Nothing is touched here, so
        // the next call with room says the whole of it.
        return 0;
    }

    // --- the layout ---------------------------------------------------------
    //
    // A layout with no key field says nothing about where the keys are.
    // Reading the boot offsets anyway is a guess, and it is wrong for exactly
    // the devices the descriptor path exists to serve.
    if (layout_.key_kind == ch375::KeyboardFieldKind::None ||
        layout_.key_element_bits == 0 || layout_.key_element_count == 0 ||
        layout_.key_element_bits > kMaxKeyElementBits) {
        return 0;
    }

    // --- the identifier -----------------------------------------------------
    //
    // A device with media keys sends those down the same endpoint under a
    // different identifier. Read as a keyboard state they release everything
    // held, and the release that really comes then says nothing at all.
    const std::uint8_t* body = report.data;
    std::size_t body_bytes = report.size;
    if (layout_.report_id) {
        if (body_bytes == 0 || body[0] != layout_.report_id_value) {
            return 0;
        }
        ++body;
        --body_bytes;
    }
    if (static_cast<std::uint64_t>(body_bytes) * 8u < needed_body_bits(layout_)) {
        // Half a report is not a report. Read as a whole one it would release
        // every key the user is holding - and the fields past its end would be
        // read out of whatever happens to follow it in memory.
        return 0;
    }

    // --- the keys -----------------------------------------------------------
    //
    // Gathered and counted before anything is emitted, because whether this
    // report can be delivered at all is not known until the last one is read.
    std::uint16_t now[kGatherSlots] = {};
    std::size_t now_count = 0;
    std::size_t error_slots = 0;
    bool overflowed = false;

    const std::uint32_t first_bit = layout_.key_bit_offset;
    const std::uint32_t element_bits = layout_.key_element_bits;
    const std::uint32_t element_count = layout_.key_element_count;

    for (std::uint32_t index = 0; index < element_count; ++index) {
        std::uint16_t usage = 0;
        if (layout_.key_kind == ch375::KeyboardFieldKind::Array) {
            usage = static_cast<std::uint16_t>(
                read_bits(body, first_bit + index * element_bits,
                          layout_.key_element_bits));
            if (usage == kRollover) {
                // Not a usage anybody can press, so it never joins the set.
                // How many there are is what decides the report: HID 1.11 8.3
                // has a keyboard that has lost count put this in *every* array
                // field, and only that is the keyboard saying so.
                ++error_slots;
                continue;
            }
        } else {
            if (read_bits(body, first_bit + index, 1) == 0) {
                continue;
            }
            usage = static_cast<std::uint16_t>(layout_.key_usage_minimum + index);
        }
        if (usage == 0 || contains(now, now_count, usage)) {
            continue;
        }
        if (now_count == kGatherSlots) {
            overflowed = true;
            break;
        }
        now[now_count++] = usage;
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

    // More keys than the six-key report onward can carry. Six of the seven is
    // a state nobody's hands were in, and the seventh would stay dropped for
    // as long as it is held. The whole report waits instead - including its
    // modifiers, because a report is applied whole or not at all.
    if (!rollover && (overflowed || now_count > kKeySlots)) {
        return 0;
    }

    // --- the modifiers ------------------------------------------------------
    //
    // Reported as separate bits, delivered as ordinary keys. A modifier that
    // never arrives is a shift that never happens. Read before anything is
    // emitted so an out-of-range bit refuses the report rather than half of it.
    std::uint8_t modifiers = 0;
    for (std::uint8_t bit = 0; bit < 8; ++bit) {
        const std::uint16_t at = layout_.modifier_bits[bit];
        if (at == ch375::kNoKeyboardBit) {
            continue;
        }
        if (read_bits(body, at, 1) != 0) {
            modifiers = static_cast<std::uint8_t>(modifiers | (1u << bit));
        }
    }

    std::size_t used = 0;

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
        held_count_ = static_cast<std::uint8_t>(now_count);
    }

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
