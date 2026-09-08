#include "config_profiles.hpp"

namespace duo_input::u1 {
namespace {

std::uint16_t read_u16(protocol::ByteView bytes, std::size_t offset) {
    return static_cast<std::uint16_t>(bytes.data[offset]) |
           static_cast<std::uint16_t>(static_cast<std::uint16_t>(bytes.data[offset + 1]) << 8);
}

}  // namespace

bool StoredProfiles::load(protocol::ByteView blob) {
    loaded_ = false;
    used_steps_ = 0;
    dropped_steps_ = 0;
    active_profile_id_ = 0;
    blob_ = protocol::ByteView{nullptr, 0};

    if (blob.data == nullptr || blob.size == 0) {
        return false;
    }

    const config::ValidationResult result = config::validate_config(blob);
    if (!result) {
        // Nothing is kept. A half-loaded configuration is worse than none: it
        // runs, and what it runs is whatever survived the part that failed.
        return false;
    }

    blob_ = blob;
    active_profile_id_ = result.view().active_profile_id();
    loaded_ = true;
    return true;
}

bool StoredProfiles::find_profile(std::uint8_t profile_id, config::ProfileView& out) const {
    if (!loaded_) {
        return false;
    }
    const config::ValidationResult result = config::validate_config(blob_);
    if (!result) {
        return false;
    }
    const config::ConfigView& view = result.view();
    for (std::size_t index = 0; index < view.profile_count(); ++index) {
        config::ProfileView profile;
        if (view.profile_at(index, profile) && profile.id() == profile_id) {
            out = profile;
            return true;
        }
    }
    return false;
}

std::uint8_t StoredProfiles::slot_of(const config::ProfileView& profile,
                                     std::uint8_t macro_id) const {
    const std::size_t count = profile.macro_count();
    for (std::size_t index = 0; index < count && index < kMaxProfileMacros; ++index) {
        config::MacroView macro;
        if (profile.macro_at(index, macro) && macro.id() == macro_id) {
            return static_cast<std::uint8_t>(index);
        }
    }
    return kNoSlot;
}

std::size_t StoredProfiles::bindings_for(std::uint8_t profile_id, mapping::Binding* out) const {
    config::ProfileView profile;
    if (!find_profile(profile_id, profile)) {
        return 0;
    }

    std::size_t written = 0;
    const std::size_t count = profile.binding_count();
    for (std::size_t index = 0; index < count && written < mapping::kMaxBindings; ++index) {
        config::BindingView binding;
        if (!profile.binding_at(index, binding)) {
            continue;
        }

        mapping::Binding& slot = out[written];
        slot.trigger = binding.trigger_kind();
        slot.code = binding.trigger_code();
        if (slot.trigger == config::TriggerKind::MOUSE_BUTTON) {
            // The format numbers buttons from one, because zero means "no
            // button" to the validator. The input pipeline numbers them from
            // zero, because they are bit positions in the mouse report, and
            // the engine matches a binding against an event straight. Without
            // this the binding answers the next button along - and the one on
            // button 5 answers a bit no mouse ever sets. The capture path
            // makes the same translation in the other direction.
            slot.code = static_cast<std::uint16_t>(slot.code - 1);
        }
        slot.required_modifiers = binding.trigger_modifiers();
        slot.source = binding.source();
        slot.mode = binding.mode();
        slot.action = binding.action_kind();
        slot.parameter = binding.action_argument();

        if (slot.action == config::ActionKind::RUN_MACRO) {
            // The one translation. The format numbers macros up to 255 and the
            // output side tells owners apart by an index with thirty-two of
            // them, so the wire's number cannot travel any further than here.
            const std::uint8_t slot_index = slot_of(profile, slot.parameter);
            if (slot_index == kNoSlot) {
                // The validator rejects a binding naming a macro the profile
                // does not have, so this cannot happen against a configuration
                // that loaded - and if it ever does, a binding that runs macro
                // zero is worse than one that does nothing.
                continue;
            }
            slot.parameter = slot_index;
        }

        ++written;
    }
    return written;
}

bool StoredProfiles::routes_for(std::uint8_t profile_id, config::KeyboardRoute& keyboard,
                                config::MouseRoute& mouse) const {
    config::ProfileView profile;
    if (!find_profile(profile_id, profile)) {
        return false;
    }
    keyboard = profile.keyboard_route();
    mouse = profile.mouse_route();
    return true;
}

std::size_t StoredProfiles::macros_for(std::uint8_t profile_id, macros::MacroDefinition* out,
                                       std::size_t capacity) {
    for (std::size_t index = 0; index < capacity; ++index) {
        out[index] = macros::MacroDefinition{};
    }

    config::ProfileView profile;
    if (!find_profile(profile_id, profile)) {
        return 0;
    }

    used_steps_ = 0;
    dropped_steps_ = 0;

    const std::size_t count = profile.macro_count();
    std::size_t written = 0;
    for (std::size_t index = 0; index < count && index < capacity; ++index) {
        config::MacroView macro;
        if (!profile.macro_at(index, macro)) {
            continue;
        }

        macros::MacroStep* first = steps_ + used_steps_;
        std::size_t here = 0;

        const std::size_t step_count = macro.step_count();
        for (std::size_t step_index = 0; step_index < step_count; ++step_index) {
            config::StepView step;
            if (!profile.macro_at(index, macro) || !macro.step_at(step_index, step)) {
                continue;
            }
            if (used_steps_ >= kMaxProfileSteps) {
                // Sized to the format's own limits, so this means a
                // configuration the validator accepted did not fit here.
                // Counted rather than silently truncated: a macro that stops
                // half way is a key left down on somebody's computer.
                ++dropped_steps_;
                continue;
            }

            const protocol::ByteView payload = step.payload();
            macros::MacroStep& target = steps_[used_steps_];
            target = macros::MacroStep{};
            target.kind = step.type();

            switch (target.kind) {
                case config::MacroStepType::KEY_DOWN:
                case config::MacroStepType::KEY_UP:
                    if (payload.size >= 1) {
                        target.code = payload.data[0];
                    }
                    break;

                case config::MacroStepType::CONSUMER_TAP:
                    if (payload.size >= 2) {
                        target.code = read_u16(payload, 0);
                    }
                    break;

                case config::MacroStepType::KEY_TAP:
                case config::MacroStepType::TEXT:
                    // Both are runs of modifier-and-usage pairs; a tap is one
                    // pair and text is many. Pointed at where they lie.
                    target.pairs = payload.data;
                    target.pair_bytes = static_cast<std::uint16_t>(payload.size);
                    break;

                case config::MacroStepType::DELAY:
                    if (payload.size >= 4) {
                        // Stored as a minimum and a maximum; the scheduler
                        // wants a wait and how much longer it may randomly be.
                        const std::uint16_t low = read_u16(payload, 0);
                        const std::uint16_t high = read_u16(payload, 2);
                        target.delay_ms = low;
                        target.jitter_ms = static_cast<std::uint16_t>(high - low);
                    }
                    break;

                case config::MacroStepType::SET_KEYBOARD_ROUTE:
                case config::MacroStepType::SET_MOUSE_ROUTE:
                case config::MacroStepType::SET_PROFILE:
                    if (payload.size >= 1) {
                        target.code = payload.data[0];
                    }
                    break;
            }

            ++used_steps_;
            ++here;
        }

        out[index].steps = here > 0 ? first : nullptr;
        out[index].count = here;
        ++written;
    }

    return written;
}

}  // namespace duo_input::u1
