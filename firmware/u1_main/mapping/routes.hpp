#pragma once

// Where input is going, and the rules about changing it.
//
// The keyboard may go to either computer or to both at once. The mouse may
// not: a pointer on two computers follows neither, and the operator has no way
// to tell which one they are aiming at.

#include <cstdint>

#include "config/format.hpp"
#include "hid/types.hpp"

namespace duo_input::u1::mapping {

class Routes {
public:
    config::KeyboardRoute keyboard() const { return keyboard_; }
    config::MouseRoute mouse() const { return mouse_; }

    /// Would this route be accepted?
    ///
    /// Asked before anything is released, so that a refused change releases
    /// nothing - nothing moved, and letting go of a computer's keys because
    /// somebody asked for an impossible route would be a fault of its own.
    static bool keyboard_route_is_valid(config::KeyboardRoute route);
    static bool mouse_route_is_valid(config::MouseRoute route);

    /// Returns false if the route was not one the keyboard can take.
    bool set_keyboard(config::KeyboardRoute route);
    bool set_mouse(config::MouseRoute route);

    void toggle_keyboard();
    void toggle_mouse();

    /// Does keyboard input currently reach this computer?
    bool keyboard_reaches(hid::Target target) const;
    bool mouse_reaches(hid::Target target) const;

private:
    config::KeyboardRoute keyboard_ = config::KeyboardRoute::PC1;
    config::MouseRoute mouse_ = config::MouseRoute::PC1;
};

}  // namespace duo_input::u1::mapping
