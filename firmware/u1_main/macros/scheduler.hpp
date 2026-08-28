#pragma once

// Running a macro without stopping anything else.
//
// A macro is a little program that types, and the hard requirement is that it
// must never hold the loop. A macro with a two-second pause in it cannot mean
// two seconds where the operator's own keyboard does nothing: the person is
// still typing, and their keystrokes are the ones that matter. So every step
// carries an absolute deadline and this is asked what to do rather than told
// to do it - tick returns, always, immediately.
//
// The other half is ownership. Keys a macro pressed belong to the macro; keys
// a person is holding belong to the person. Stopping a macro releases what it
// pressed and nothing else, because releasing somebody's held shift when a
// macro was cancelled changes what everything they type next means.
//
// One macro runs at a time. Two typing at once would interleave into something
// neither of them meant.

#include <cstddef>
#include <cstdint>

#include "config/format.hpp"
#include "input/events.hpp"
#include "runtime/output_command.hpp"
#include "macros/steps.hpp"

namespace duo_input::u1::macros {

/// Where a random delay's spread comes from. Injected so a test can pin it:
/// a delay that varies is untestable otherwise, and a macro that types at a
/// perfectly even rhythm is the one thing that looks least like a person.
class IRandom {
public:
    virtual ~IRandom() = default;
    /// A value below ``bound``. Zero when the bound is zero.
    virtual std::uint32_t next(std::uint32_t bound) = 0;
};

enum class MacroOutputKind : std::uint8_t {
    None,
    SendInput,
    SetProfile,
    SetKeyboardRoute,
    SetMouseRoute,
};

struct MacroOutput {
    MacroOutputKind kind = MacroOutputKind::None;
    input::InputEvent event{};
    /// Where this goes - which can be both computers at once, so this is a
    /// route and not a single target.
    runtime::Route route = runtime::Route::Pc1;
    /// Which macro this came from. Core 0 keeps a macro's keys apart from the
    /// operator's, so letting go of one does not let go of the other.
    std::uint8_t owner = 0;
    std::uint8_t parameter = 0;
};

enum class StopReason : std::uint8_t {
    None,
    Finished,
    /// Somebody asked for everything to stop.
    Stopped,
    /// The macro tried to hold more keys than a report can carry.
    TooManyKeys,
};

/// How many macros may wait behind the running one.
inline constexpr std::size_t kMacroQueueDepth = 4;

/// How many macros can be defined at once.
inline constexpr std::size_t kMaxMacros = protocol::ProtocolLimits::MACROS_PER_PROFILE;

/// A boot keyboard report carries six usages, and a macro cannot hold more.
///
/// Modifiers do not count against it: they travel as a mask and take no key
/// slot, so refusing a seventh because shift was held would refuse something
/// the report has room for.
inline constexpr std::size_t kMaxMacroKeys = 6;

/// Six keys plus the eight modifiers that can be held alongside them.
inline constexpr std::size_t kMaxMacroHeld = kMaxMacroKeys + 8;

class MacroScheduler {
public:
    MacroScheduler() = default;
    explicit MacroScheduler(IRandom* random) : random_(random) {}

    void define(std::uint8_t macro_id, const MacroDefinition& definition);

    /// Start a macro, or put it behind the one already running.
    ///
    /// Returns false when the queue is full or the macro is not defined.
    /// Refused rather than dropped quietly: somebody leaning on a bound key
    /// should not queue up a minute of typing that arrives after they have
    /// moved on.
    bool enqueue(std::uint8_t macro_id, runtime::Route route, std::uint32_t now_ms);

    /// Take the next thing to do, if there is one right now.
    ///
    /// Returns false when there is nothing to do at this instant - which is
    /// most of the time, and is not the same as being finished. Never blocks
    /// and never waits.
    bool tick(std::uint32_t now_ms, MacroOutput& output);

    /// Stop everything and release whatever the running macro was holding.
    void stop_all();

    /// Stop everything and forget what was held, emitting nothing.
    ///
    /// For the caller that has already let go of everything on both computers
    /// by other means. Releases handed out afterwards would be noise, and -
    /// since they are paced one to a pass - noise that arrived after the step
    /// pool they came from had been rewritten underneath this.
    void abandon();

    bool active() const { return running_; }
    std::size_t queued_count() const { return queued_; }
    StopReason last_stop_reason() const { return last_stop_; }

private:
    struct Pending {
        std::uint8_t macro_id = 0;
        runtime::Route route = runtime::Route::Pc1;
    };

    bool start_next(std::uint32_t now_ms);
    void finish(StopReason reason);
    bool emit_release(MacroOutput& output);
    bool press(std::uint16_t usage);
    bool release(std::uint16_t usage);
    /// One event of a tap or a run of text. False when the run is finished.
    bool typing_step(MacroOutput& output);

    IRandom* random_ = nullptr;
    MacroDefinition macros_[kMaxMacros];

    bool running_ = false;
    std::uint8_t current_ = 0;
    runtime::Route route_ = runtime::Route::Pc1;
    std::size_t cursor_ = 0;
    /// When the current delay is over. Compared by subtraction, because the
    /// millisecond counter wraps after 49 days.
    std::uint32_t resume_at_ms_ = 0;
    bool waiting_ = false;

    /// What this macro is holding down. Only this - the operator's own keys
    /// are none of its business.
    std::uint16_t held_[kMaxMacroHeld] = {};
    std::size_t held_count_ = 0;
    /// Releases still to be handed out after the macro ended.
    std::size_t releasing_ = 0;
    /// A run of modifier-and-usage pairs being typed out.
    ///
    /// One event per pass: a tap squeezed into a single event hides its own
    /// release from anything counting, and the far side needs to see the key
    /// go down and come up as two separate reports to register a keystroke.
    const std::uint8_t* pairs_ = nullptr;
    std::uint16_t pair_count_ = 0;
    std::uint16_t pair_index_ = 0;
    /// 0 press modifiers, 1 press usage, 2 release usage, 3 release modifiers.
    std::uint8_t phase_ = 0;
    /// Which modifier bit the current phase has reached.
    std::uint8_t mod_bit_ = 0;
    bool typing_ = false;

    /// A consumer tap owes a release of its own, and holds no key.
    bool consumer_pending_ = false;
    std::uint16_t consumer_usage_ = 0;

    Pending queue_[kMacroQueueDepth];
    std::size_t queued_ = 0;
    StopReason last_stop_ = StopReason::None;
};

}  // namespace duo_input::u1::macros
