#pragma once

// What a keystroke turns into once the operator has had their say.
//
// Most input passes straight through to wherever the route points. A few keys
// are bound to something, and a smaller number of those change where input
// goes at all - which is the part that has to be handled carefully.
//
// Whatever is held down when a route changes is held on the computer being
// left behind, and that computer will never hear about it again. So it is
// released before the switch, and the physical key still under somebody's
// finger is marked as belonging to the computer it was pressed on: it does not
// arrive on the new one as a fresh press, and its release is swallowed there
// too, because that machine never saw it go down.
//
// A modifier stuck on the computer you just left changes what every later
// keystroke there means, and you cannot fix it from where you are standing.

#include <cstddef>
#include <cstdint>
#include <initializer_list>

#include "config/format.hpp"
#include "hid/types.hpp"
#include "input/events.hpp"
#include "input/source_table.hpp"
#include "mapping/binding.hpp"
#include "mapping/routes.hpp"

namespace duo_input::u1::mapping {

enum class ActionRequestKind : std::uint8_t {
    None,
    /// Pass this input to whatever the route currently points at.
    SendInput,
    /// Let go of everything this computer is holding.
    ReleaseTarget,
    /// ``parameter`` is the macro to run.
    RunMacro,
    /// ``parameter`` is the profile to switch to.
    SetProfile,
};

struct ActionRequest {
    ActionRequestKind kind = ActionRequestKind::None;
    input::InputEvent event{};
    hid::Target target = hid::Target::Pc1;
    std::uint8_t parameter = 0;
    /// RunMacro keeps the keyboard route at this action's position. Later
    /// matching route bindings must not redirect an earlier macro request.
    config::KeyboardRoute macro_route = config::KeyboardRoute::PC1;
};

/// Fixed output budget. Matching bindings run in stored order until the next
/// complete action (including prerequisite releases) no longer fits.
inline constexpr std::size_t kMaxActionsPerEvent = 4;

struct Outcome {
    ActionRequest actions[kMaxActionsPerEvent];
    std::size_t count = 0;
};

/// How many physical inputs can be held at once and remembered.
///
/// Six keys is what a boot keyboard reports, plus eight modifiers and five
/// mouse buttons.
inline constexpr std::size_t kMaxHeld = 20 * input::SourceTable::kMaxSources;

class BindingEngine {
public:
    /// Attach on the input core before events arrive. The table must outlive us.
    void set_sources(const input::SourceTable& sources) { sources_ = &sources; }

    /// One logical point of control: every route change moves the keyboard and
    /// the mouse to the same computer. Set from the stored configuration
    /// before a profile is installed - a profile applied under the old setting
    /// would be applied under the wrong rule.
    void set_synchronised_control(bool synchronised) { synchronised_ = synchronised; }
    bool synchronised_control() const { return synchronised_; }

    void set_bindings(std::initializer_list<Binding> bindings);
    void set_bindings(const Binding* bindings, std::size_t count);

    /// Decide what one input event means.
    Outcome handle(const input::InputEvent& event);

    /// Let go of everything, everywhere. What the emergency control does: the
    /// operator cannot see which computer is holding what, so it reaches both.
    Outcome release_everything();

    /// Move a route because something other than a binding asked - a macro
    /// step, or the host. Releases the computer being left behind exactly as
    /// the binding path does, because the reason for the move does not change
    /// what happens to the keys that were held for the old one.
    Outcome set_keyboard_route(config::KeyboardRoute route);
    Outcome set_mouse_route(config::MouseRoute route);

    config::KeyboardRoute keyboard_route() const { return routes_.keyboard(); }
    config::MouseRoute mouse_route() const { return routes_.mouse(); }

private:
    struct Held {
        input::InputEventKind kind = input::InputEventKind::None;
        std::uint16_t code = 0;
        std::uint8_t source_index = 0;
        /// True once the route moved underneath it. The input stays down
        /// physically, and is ignored until it is let go.
        bool orphaned = false;
        /// True when the press was swallowed by a Replace binding. The far
        /// side never saw it go down, so it must not see it come up.
        bool suppressed = false;
    };

    bool matches(const Binding& binding, const input::InputEvent& event,
                 const input::SourceKey* source) const;
    bool apply_binding(Outcome& outcome, const Binding& binding);
    bool remember(const input::InputEvent& event, bool suppressed);
    bool forget(const input::InputEvent& event);
    Held* find_held(input::InputEventKind kind, std::uint16_t code, std::uint8_t source_index);
    bool forwarded(input::InputEventKind kind, std::uint16_t code) const;
    std::uint8_t held_modifiers() const;
    /// Mark held inputs as belonging to where they were pressed. A keyboard
    /// route change does not orphan mouse buttons, or the other way round.
    void orphan(bool keys, bool buttons);
    void add(Outcome& outcome, const ActionRequest& request) const;
    /// Release every computer the given device currently reaches - called
    /// before the route moves, while "currently" still means the old one.
    void release_reached(Outcome& outcome, bool keyboard, bool mouse) const;
    void release_both(Outcome& outcome) const;
    /// The one place a route actually moves. Returns false if it was refused.
    bool move_route(Outcome& outcome, bool keyboard, bool toggle, std::uint8_t parameter);

    Routes routes_;
    const input::SourceTable* sources_ = nullptr;
    Binding bindings_[kMaxBindings];
    std::size_t binding_count_ = 0;

    Held held_[kMaxHeld];
    std::size_t held_count_ = 0;
    /// Which modifiers are down, as the bindings' conditions see them.
    std::uint8_t modifiers_ = 0;
    /// Whether the two devices travel together. See ``move_route``.
    bool synchronised_ = false;
};

}  // namespace duo_input::u1::mapping
