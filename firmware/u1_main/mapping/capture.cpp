#include "mapping/capture.hpp"

namespace duo_input::u1::mapping {
namespace {

using input::InputEvent;
using input::InputEventKind;

/// Modifiers arrive as usages 0xE0 to 0xE7, in mask order.
bool is_modifier(std::uint16_t usage) {
    return usage >= 0xE0 && usage <= 0xE7;
}

std::uint8_t modifier_bit(std::uint16_t usage) {
    return static_cast<std::uint8_t>(1u << (usage - 0xE0));
}

bool reached(std::uint32_t now_ms, std::uint32_t deadline_ms) {
    // Subtraction, not comparison: the millisecond counter wraps after 49
    // days, and a deadline on the far side of that reads as either no wait at
    // all or a wait of seven weeks.
    return static_cast<std::int32_t>(now_ms - deadline_ms) >= 0;
}

}  // namespace

void CaptureController::begin(std::uint32_t now_ms, std::uint32_t timeout_ms) {
    active_ = true;
    deadline_ms_ = now_ms + timeout_ms;
    have_trigger_ = false;
    modifiers_ = 0;
}

void CaptureController::cancel() {
    active_ = false;
    have_trigger_ = false;
    modifiers_ = 0;
    // What is still held stays in the swallowed set. Those keys went down
    // without the far side hearing, and their releases must not either.
}

void CaptureController::tick(std::uint32_t now_ms) {
    if (active_ && reached(now_ms, deadline_ms_)) {
        cancel();
    }
}

bool CaptureController::remember(const InputEvent& event) {
    if (swallowed_count_ >= kMaxSwallowed) {
        return false;
    }
    swallowed_[swallowed_count_++] = event;
    return true;
}

bool CaptureController::forget(const InputEvent& event) {
    for (std::size_t index = 0; index < swallowed_count_; ++index) {
        const bool same_key = swallowed_[index].kind == InputEventKind::KeyDown &&
                              event.kind == InputEventKind::KeyUp;
        const bool same_button = swallowed_[index].kind == InputEventKind::MouseButtonDown &&
                                 event.kind == InputEventKind::MouseButtonUp;
        if ((same_key || same_button) && swallowed_[index].code == event.code) {
            swallowed_[index] = swallowed_[swallowed_count_ - 1];
            --swallowed_count_;
            return true;
        }
    }
    return false;
}

void CaptureController::fill_source(const InputEvent& event) {
    input::SourceIdentity identity;
    if (sources_ != nullptr && sources_->resolve(event.source_index, identity)) {
        trigger_.vendor_id = identity.vendor_id;
        trigger_.product_id = identity.product_id;
        trigger_.interface_number = identity.interface_number;
    } else {
        trigger_.vendor_id = 0;
        trigger_.product_id = 0;
        trigger_.interface_number = 0;
    }
}

bool CaptureController::take(CapturedTrigger& out) {
    if (!have_trigger_) {
        return false;
    }
    out = trigger_;
    have_trigger_ = false;
    return true;
}

CaptureDisposition CaptureController::handle(const InputEvent& event) {
    // A release of something swallowed on the way down is swallowed whether or
    // not a capture is still running - the press happened during one, and the
    // far side was never told about it.
    if (event.kind == InputEventKind::KeyUp || event.kind == InputEventKind::MouseButtonUp) {
        if (forget(event)) {
            if (event.kind == InputEventKind::KeyUp && is_modifier(event.code)) {
                modifiers_ = static_cast<std::uint8_t>(modifiers_ & ~modifier_bit(event.code));
            }
            return CaptureDisposition::Swallow;
        }
    }

    if (!active_) {
        return CaptureDisposition::Pass;
    }

    switch (event.kind) {
        case InputEventKind::KeyDown:
            if (is_modifier(event.code)) {
                // Not an answer to the question. It qualifies the next one.
                modifiers_ = static_cast<std::uint8_t>(modifiers_ | modifier_bit(event.code));
                remember(event);
                return CaptureDisposition::Swallow;
            }
            trigger_.kind = config::TriggerKind::KEYBOARD_USAGE;
            trigger_.code = static_cast<std::uint8_t>(event.code);
            trigger_.modifiers = modifiers_;
            fill_source(event);
            have_trigger_ = true;
            remember(event);
            active_ = false;
            return CaptureDisposition::Swallow;

        case InputEventKind::MouseButtonDown:
            trigger_.kind = config::TriggerKind::MOUSE_BUTTON;
            // The host numbers buttons from one and refuses a zero.
            trigger_.code = static_cast<std::uint8_t>(event.code + 1);
            // And refuses a mouse trigger carrying modifiers, whatever is
            // actually held at the time.
            trigger_.modifiers = 0;
            fill_source(event);
            have_trigger_ = true;
            remember(event);
            active_ = false;
            return CaptureDisposition::Swallow;

        case InputEventKind::ConsumerDown:
        case InputEventKind::ConsumerUp:
            // Not a trigger the configuration can express, and not something
            // to send to a computer that is waiting to be told about a key.
            return CaptureDisposition::Swallow;

        default:
            // Motion, wheel, connections. The operator has to be able to see
            // and reach the window that is asking them the question.
            return CaptureDisposition::Pass;
    }
}

}  // namespace duo_input::u1::mapping
