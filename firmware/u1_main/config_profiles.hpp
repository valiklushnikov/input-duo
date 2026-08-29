#pragma once

// The stored configuration, in the shapes the runtime works in.
//
// One translation happens here and nowhere else. The format numbers macros up
// to 255, but the output side keeps a macro's keys apart from the operator's
// by an owner index and there are thirty-two of those. So a binding's macro
// argument is rewritten to a slot on the way in, where the macro table and the
// binding table are both in view. Doing it later means either an index off the
// end of the owner table or two numbering schemes travelling together, and the
// second is how the wrong macro runs and looks like a hardware fault.
//
// Step payloads are pointed at, not copied. A text step may hold a thousand
// characters and this chip has no memory to duplicate that per macro; the
// configuration lives in flash and outlasts everything that reads it.

#include <cstddef>
#include <cstdint>

#include "config/validator.hpp"
#include "core1_runtime.hpp"
#include "macros/steps.hpp"
#include "mapping/binding.hpp"
#include "protocol/bytes.hpp"

namespace duo_input::u1 {

/// How many macros one profile may carry, and how many owners the output side
/// can tell apart. The format's limit and the report's limit agree at 32.
inline constexpr std::size_t kMaxProfileMacros = protocol::ProtocolLimits::MACROS_PER_PROFILE;

/// Every step of every macro in one profile, at the format's own limits.
inline constexpr std::size_t kMaxProfileSteps =
    kMaxProfileMacros * protocol::ProtocolLimits::MACRO_STEPS_PER_MACRO;

class StoredProfiles final : public IProfileSource {
public:
    /// Point at a stored configuration.
    ///
    /// Returns false when it does not validate, and leaves nothing behind when
    /// it does not: a caller that ignores the result gets an empty
    /// configuration rather than fragments of one nobody checked.
    ///
    /// The bytes must outlive this object. On the device they are the flash
    /// the configuration was written to.
    bool load(protocol::ByteView blob);

    bool loaded() const { return loaded_; }

    /// Which profile the configuration says to run.
    std::uint8_t active_profile_id() const { return active_profile_id_; }

    /// Bindings for a profile, keyed by the format's own profile id.
    ///
    /// Nothing is renumbered: the ids the configurator writes are the ids a
    /// SET_PROFILE action carries and the ids this answers to, so there is one
    /// numbering from the editor to the far side.
    std::size_t bindings_for(std::uint8_t profile_id, mapping::Binding* out) const override;

    /// The routes the profile is stored as starting in.
    ///
    /// Read straight out of the descriptor; the validator has already refused
    /// any value that is not a route, so whatever is here is one.
    bool routes_for(std::uint8_t profile_id, config::KeyboardRoute& keyboard,
                    config::MouseRoute& mouse) const override;

    /// Compile a profile's macros, indexed by slot.
    ///
    /// The slot is what a binding's RUN_MACRO parameter names after loading.
    /// Writes ``capacity`` definitions at most and returns how many the
    /// profile has; empty slots are left with a count of zero.
    std::size_t macros_for(std::uint8_t profile_id, macros::MacroDefinition* out,
                           std::size_t capacity);

    /// How many steps were dropped because the step pool was full.
    ///
    /// The pool is sized to the format's own limits, so a nonzero count means
    /// a configuration the validator accepted did not fit - which is a defect
    /// here, not a bad configuration, and must not be silent.
    std::size_t dropped_steps() const { return dropped_steps_; }

private:
    bool find_profile(std::uint8_t profile_id, config::ProfileView& out) const;
    /// Which slot a wire macro id was given, or kNoSlot.
    std::uint8_t slot_of(const config::ProfileView& profile, std::uint8_t macro_id) const;

    static constexpr std::uint8_t kNoSlot = 0xFF;

    /// The bytes, not a view of them.
    ///
    /// A ConfigView can only be had from a successful validation, which is the
    /// point of it - there is no way to hold one that was never checked. So
    /// the blob is kept and re-validated when a profile is looked up, which
    /// happens on a profile change and not on the path an input event takes.
    protocol::ByteView blob_{nullptr, 0};
    bool loaded_ = false;
    std::uint8_t active_profile_id_ = 0;

    macros::MacroStep steps_[kMaxProfileSteps];
    std::size_t used_steps_ = 0;
    std::size_t dropped_steps_ = 0;
};

}  // namespace duo_input::u1
