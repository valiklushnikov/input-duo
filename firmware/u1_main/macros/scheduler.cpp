#include "macros/scheduler.hpp"

namespace duo_input::u1::macros {
namespace {

using input::InputEvent;
using input::InputEventKind;

/// Has ``deadline`` arrived?
///
/// Subtraction, not comparison. The millisecond counter wraps after 49 days,
/// and a deadline on the far side of that reads as either no wait at all or a
/// wait of seven weeks, depending which way the comparison went.
bool reached(std::uint32_t now_ms, std::uint32_t deadline_ms) {
    return static_cast<std::int32_t>(now_ms - deadline_ms) >= 0;
}

}  // namespace

void MacroScheduler::define(std::uint8_t macro_id, const MacroDefinition& definition) {
    if (macro_id < kMaxMacros) {
        macros_[macro_id] = definition;
    }
}

bool MacroScheduler::press(std::uint16_t usage) {
    for (std::size_t index = 0; index < held_count_; ++index) {
        if (held_[index] == usage) {
            return true;
        }
    }
    if (held_count_ >= kMaxMacroKeys) {
        return false;
    }
    held_[held_count_++] = usage;
    return true;
}

bool MacroScheduler::release(std::uint16_t usage) {
    for (std::size_t index = 0; index < held_count_; ++index) {
        if (held_[index] != usage) {
            continue;
        }
        held_[index] = held_[held_count_ - 1];
        --held_count_;
        return true;
    }
    return false;
}

bool MacroScheduler::enqueue(std::uint8_t macro_id, runtime::Route route,
                             std::uint32_t now_ms) {
    if (macro_id >= kMaxMacros || macros_[macro_id].steps == nullptr ||
        macros_[macro_id].count == 0) {
        // Nothing to run. Saying so beats starting something empty and
        // reporting that a macro fired.
        return false;
    }

    if (!running_ && releasing_ == 0) {
        current_ = macro_id;
        route_ = route;
        cursor_ = 0;
        waiting_ = false;
        running_ = true;
        last_stop_ = StopReason::None;
        (void)now_ms;
        return true;
    }

    if (queued_ >= kMacroQueueDepth) {
        return false;
    }
    queue_[queued_].macro_id = macro_id;
    queue_[queued_].route = route;
    ++queued_;
    return true;
}

bool MacroScheduler::start_next(std::uint32_t now_ms) {
    if (queued_ == 0) {
        return false;
    }
    current_ = queue_[0].macro_id;
    route_ = queue_[0].route;
    for (std::size_t index = 1; index < queued_; ++index) {
        queue_[index - 1] = queue_[index];
    }
    --queued_;

    cursor_ = 0;
    waiting_ = false;
    running_ = true;
    last_stop_ = StopReason::None;
    (void)now_ms;
    return true;
}

void MacroScheduler::finish(StopReason reason) {
    if (tap_pending_) {
        // Its release is already owed and goes out on the next pass. Leaving
        // it in the held set as well would release the same key twice.
        (void)release(tap_usage_);
    }
    running_ = false;
    waiting_ = false;
    cursor_ = 0;
    last_stop_ = reason;
    // Whatever is still held goes out as releases before anything else runs.
    // A macro that pressed without releasing has left a key down on somebody
    // else's computer, and this is the only thing that knows about it.
    releasing_ = held_count_;
}

bool MacroScheduler::emit_release(MacroOutput& output) {
    if (releasing_ == 0) {
        return false;
    }
    --releasing_;
    const std::uint16_t usage = held_[releasing_];
    --held_count_;

    output.kind = MacroOutputKind::SendInput;
    output.route = route_;
    output.owner = current_;
    output.event = InputEvent{};
    output.event.kind = InputEventKind::KeyUp;
    output.event.code = usage;
    return true;
}

void MacroScheduler::stop_all() {
    // Stop means stop. A queue that empties itself afterwards is a macro that
    // starts typing just after somebody reached for the emergency control.
    queued_ = 0;
    if (running_ || held_count_ > 0) {
        finish(StopReason::Stopped);
    }
}

bool MacroScheduler::tick(std::uint32_t now_ms, MacroOutput& output) {
    output = MacroOutput{};

    if (emit_release(output)) {
        return true;
    }

    if (tap_pending_) {
        tap_pending_ = false;
        (void)release(tap_usage_);
        output.kind = MacroOutputKind::SendInput;
        output.route = route_;
    output.owner = current_;
        output.event.kind = tap_release_kind_;
        output.event.code = tap_usage_;
        return true;
    }

    if (!running_) {
        if (!start_next(now_ms)) {
            return false;
        }
    }

    if (waiting_) {
        if (!reached(now_ms, resume_at_ms_)) {
            // Still waiting. The caller gets its loop back, which is the whole
            // point of asking rather than being told.
            return false;
        }
        waiting_ = false;
    }

    const MacroDefinition& macro = macros_[current_];
    if (cursor_ >= macro.count) {
        finish(StopReason::Finished);
        if (emit_release(output)) {
            return true;
        }
        // Whatever was queued behind it starts now rather than on some later
        // pass: returning false here means "nothing to do", and there is.
        return tick(now_ms, output);
    }

    const MacroStep& step = macro.steps[cursor_];
    ++cursor_;

    switch (step.kind) {
        case config::MacroStepType::DELAY: {
            std::uint32_t wait = step.delay_ms;
            if (step.jitter_ms != 0 && random_ != nullptr) {
                wait += random_->next(static_cast<std::uint32_t>(step.jitter_ms) + 1u);
            }
            resume_at_ms_ = now_ms + wait;
            waiting_ = true;
            return false;
        }

        case config::MacroStepType::KEY_DOWN:
            if (!press(step.code)) {
                // A report carries six usages and no more. Delivering six of
                // the seven this macro wants would type something the operator
                // did not write, so it is abandoned instead.
                finish(StopReason::TooManyKeys);
                return emit_release(output);
            }
            output.kind = MacroOutputKind::SendInput;
            output.route = route_;
    output.owner = current_;
            output.event.kind = InputEventKind::KeyDown;
            output.event.code = step.code;
            return true;

        case config::MacroStepType::KEY_UP:
            if (!release(step.code)) {
                // It was not down. Sending the release anyway would let go of
                // a key the operator might be holding themselves.
                return tick(now_ms, output);
            }
            output.kind = MacroOutputKind::SendInput;
            output.route = route_;
    output.owner = current_;
            output.event.kind = InputEventKind::KeyUp;
            output.event.code = step.code;
            return true;

        case config::MacroStepType::KEY_TAP:
            // The press now, the release on the next pass. A tap is two
            // events, and squeezing both into one hides the second from
            // anything counting them - including the far side, which needs a
            // gap between them to register a keystroke at all.
            if (!press(step.code)) {
                finish(StopReason::TooManyKeys);
                return emit_release(output);
            }
            tap_pending_ = true;
            tap_usage_ = step.code;
            tap_release_kind_ = InputEventKind::KeyUp;
            output.kind = MacroOutputKind::SendInput;
            output.route = route_;
    output.owner = current_;
            output.event.kind = InputEventKind::KeyDown;
            output.event.code = step.code;
            return true;

        case config::MacroStepType::CONSUMER_TAP:
            // Not counted against the six: a consumer usage travels in its own
            // report and does not take a keyboard slot.
            tap_pending_ = true;
            tap_usage_ = step.code;
            tap_release_kind_ = InputEventKind::ConsumerUp;
            output.kind = MacroOutputKind::SendInput;
            output.route = route_;
    output.owner = current_;
            output.event.kind = InputEventKind::ConsumerDown;
            output.event.code = step.code;
            return true;

        case config::MacroStepType::SET_PROFILE:
            output.kind = MacroOutputKind::SetProfile;
            output.parameter = static_cast<std::uint8_t>(step.code);
            return true;

        case config::MacroStepType::SET_KEYBOARD_ROUTE:
            output.kind = MacroOutputKind::SetKeyboardRoute;
            output.parameter = static_cast<std::uint8_t>(step.code);
            return true;

        case config::MacroStepType::SET_MOUSE_ROUTE:
            output.kind = MacroOutputKind::SetMouseRoute;
            output.parameter = static_cast<std::uint8_t>(step.code);
            return true;

        case config::MacroStepType::TEXT:
            // Text is compiled into key steps by the application before it
            // ever reaches the device - the firmware executes HID steps and
            // knows nothing about layouts.
            return tick(now_ms, output);
    }

    return false;
}

}  // namespace duo_input::u1::macros
