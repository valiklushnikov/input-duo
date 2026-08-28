#include "macros/scheduler.hpp"

namespace duo_input::u1::macros {
namespace {

static_assert(kMaxMacros == protocol::ProtocolLimits::MACROS_PER_PROFILE,
              "the runtime must expose every macro slot accepted by the config format");

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

    const bool is_modifier = usage >= 0xE0 && usage <= 0xE7;
    if (!is_modifier) {
        // Only the usages that need a key slot are counted against the six a
        // report can carry. Modifiers travel as a mask beside them.
        std::size_t keys = 0;
        for (std::size_t index = 0; index < held_count_; ++index) {
            if (held_[index] < 0xE0 || held_[index] > 0xE7) {
                ++keys;
            }
        }
        if (keys >= kMaxMacroKeys) {
            return false;
        }
    }

    if (held_count_ >= kMaxMacroHeld) {
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
    typing_ = false;
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

void MacroScheduler::abandon() {
    stop_all();
    // Everything stop_all() left owed. Nothing is emitted, so nothing may be
    // remembered either: a held_ entry that outlived the release it stands for
    // would be handed out later as a release of a key nobody is holding.
    releasing_ = 0;
    held_count_ = 0;
    consumer_pending_ = false;
    consumer_usage_ = 0;
    typing_ = false;
    // The pairs point into the step pool, which the caller is about to
    // rewrite.
    pairs_ = nullptr;
    pair_count_ = 0;
    pair_index_ = 0;
    phase_ = 0;
    mod_bit_ = 0;
}

bool MacroScheduler::typing_step(MacroOutput& output) {
    while (pair_index_ < pair_count_) {
        const std::uint8_t modifiers = pairs_[pair_index_ * 2u];
        const std::uint16_t usage = pairs_[pair_index_ * 2u + 1u];

        output.kind = MacroOutputKind::SendInput;
        output.route = route_;
        output.owner = current_;

        if (phase_ == 0 || phase_ == 3) {
            // Modifiers go down before the key and come up after it, one bit
            // at a time. A capital letter is a shift held across a keystroke,
            // and a shift that arrives with it or leaves before it is a
            // lower-case letter on somebody's screen.
            while (mod_bit_ < 8) {
                const std::uint8_t bit = static_cast<std::uint8_t>(1u << mod_bit_);
                const std::uint16_t modifier_usage = static_cast<std::uint16_t>(0xE0 + mod_bit_);
                ++mod_bit_;
                if ((modifiers & bit) == 0) {
                    continue;
                }
                const bool pressing = phase_ == 0;
                if (pressing) {
                    if (!press(modifier_usage)) {
                        return false;
                    }
                } else if (!release(modifier_usage)) {
                    continue;
                }
                output.event.kind = pressing ? InputEventKind::KeyDown : InputEventKind::KeyUp;
                output.event.code = modifier_usage;
                return true;
            }
            mod_bit_ = 0;
            if (phase_ == 0) {
                phase_ = 1;
            } else {
                phase_ = 0;
                ++pair_index_;
            }
            continue;
        }

        if (phase_ == 1) {
            if (!press(usage)) {
                return false;
            }
            phase_ = 2;
            output.event.kind = InputEventKind::KeyDown;
            output.event.code = usage;
            return true;
        }

        (void)release(usage);
        phase_ = 3;
        output.event.kind = InputEventKind::KeyUp;
        output.event.code = usage;
        return true;
    }

    typing_ = false;
    return false;
}

bool MacroScheduler::tick(std::uint32_t now_ms, MacroOutput& output) {
    output = MacroOutput{};

    if (emit_release(output)) {
        return true;
    }

    if (consumer_pending_) {
        consumer_pending_ = false;
        output.kind = MacroOutputKind::SendInput;
        output.route = route_;
        output.owner = current_;
        output.event.kind = InputEventKind::ConsumerUp;
        output.event.code = consumer_usage_;
        return true;
    }

    if (typing_ && typing_step(output)) {
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
        case config::MacroStepType::TEXT:
            // A tap is one modifier-and-usage pair and text is many. That is
            // the only difference between them, so they are the same thing to
            // run: pairs, one event at a time.
            pairs_ = step.pairs;
            pair_count_ = static_cast<std::uint16_t>(step.pair_bytes / 2u);
            pair_index_ = 0;
            phase_ = 0;
            mod_bit_ = 0;
            typing_ = pair_count_ > 0;
            if (typing_ && typing_step(output)) {
                return true;
            }
            typing_ = false;
            return tick(now_ms, output);

        case config::MacroStepType::CONSUMER_TAP:
            // Not counted against the six: a consumer usage travels in its own
            // report and does not take a keyboard slot. Held down, the volume
            // climbs until something releases it, so the release is owed.
            consumer_pending_ = true;
            consumer_usage_ = step.code;
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

    }

    return false;
}

}  // namespace duo_input::u1::macros
