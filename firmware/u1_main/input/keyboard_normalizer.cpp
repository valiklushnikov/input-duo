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

    std::size_t used = 0;

    // --- the keys -----------------------------------------------------------
    //
    // Gathered first, because a rollover report has to be recognised before
    // anything is compared against it.
    std::uint8_t now[kKeySlots] = {};
    std::uint8_t now_count = 0;
    bool rollover = false;
    for (std::size_t slot = 0; slot < kKeySlots; ++slot) {
        const std::uint8_t usage = report.data[2 + slot];
        if (usage == kRollover) {
            // Every slot filled with this means the keyboard cannot say what
            // is held - too many keys at once for its matrix. It is not six
            // new keys. Believing it releases what is really down and presses
            // one that does not exist.
            rollover = true;
            break;
        }
        if (usage != 0 && !contains(now, now_count, usage)) {
            now[now_count++] = usage;
        }
    }

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
    if (out == nullptr) {
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
