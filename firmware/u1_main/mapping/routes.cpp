#include "mapping/routes.hpp"

namespace duo_input::u1::mapping {

bool Routes::keyboard_route_is_valid(config::KeyboardRoute route) {
    return route == config::KeyboardRoute::PC1 || route == config::KeyboardRoute::PC2 ||
           route == config::KeyboardRoute::BOTH;
}

bool Routes::mouse_route_is_valid(config::MouseRoute route) {
    // BOTH is a keyboard route, and it is the one value somebody is most
    // likely to put here by mistake. A pointer on two computers at once
    // follows neither of them, and the operator has no way to tell which one
    // they are aiming - so this is refused rather than approximated.
    return route == config::MouseRoute::PC1 || route == config::MouseRoute::PC2;
}

config::MouseRoute Routes::mouse_beside(config::KeyboardRoute route) {
    return route == config::KeyboardRoute::PC2 ? config::MouseRoute::PC2
                                               : config::MouseRoute::PC1;
}

config::KeyboardRoute Routes::keyboard_beside(config::MouseRoute route) {
    return route == config::MouseRoute::PC2 ? config::KeyboardRoute::PC2
                                            : config::KeyboardRoute::PC1;
}

bool Routes::set_keyboard(config::KeyboardRoute route) {
    if (!keyboard_route_is_valid(route)) {
        return false;
    }
    keyboard_ = route;
    return true;
}

bool Routes::set_mouse(config::MouseRoute route) {
    if (!mouse_route_is_valid(route)) {
        return false;
    }
    mouse_ = route;
    return true;
}

void Routes::toggle_keyboard() {
    // Out of BOTH, to the computer the pointer is already on. The cursor is
    // the only thing telling the operator which machine they are working on,
    // and a keyboard that lands anywhere else lands where they are not
    // looking. Under synchronised control this also brings the pair back
    // together without dragging the pointer across a screen.
    if (keyboard_ == config::KeyboardRoute::BOTH) {
        keyboard_ = keyboard_beside(mouse_);
        return;
    }
    keyboard_ = keyboard_ == config::KeyboardRoute::PC1 ? config::KeyboardRoute::PC2
                                                        : config::KeyboardRoute::PC1;
}

void Routes::toggle_mouse() {
    mouse_ = mouse_ == config::MouseRoute::PC1 ? config::MouseRoute::PC2
                                               : config::MouseRoute::PC1;
}

bool Routes::keyboard_reaches(hid::Target target) const {
    if (keyboard_ == config::KeyboardRoute::BOTH) {
        return true;
    }
    return (target == hid::Target::Pc1) == (keyboard_ == config::KeyboardRoute::PC1);
}

bool Routes::mouse_reaches(hid::Target target) const {
    return (target == hid::Target::Pc1) == (mouse_ == config::MouseRoute::PC1);
}

}  // namespace duo_input::u1::mapping
