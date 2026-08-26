#include "link_watchdog.hpp"

namespace duo_input::u2 {

void LinkWatchdog::observe_valid(std::uint32_t now_ms) {
    last_valid_ms_ = now_ms;
    heard_ = true;
}

std::uint32_t LinkWatchdog::silence_ms(std::uint32_t now_ms) const {
    if (!heard_) {
        return 0;
    }
    // Unsigned subtraction wraps the same way the millisecond counter does, so
    // this stays correct across the wrap at 49 days. Comparing the two values
    // directly would see an enormous gap and release every key on a device
    // that was working perfectly.
    return now_ms - last_valid_ms_;
}

bool LinkWatchdog::expired(std::uint32_t now_ms) const {
    if (!heard_) {
        return true;
    }
    return silence_ms(now_ms) >= timeout_ms_;
}

void LinkWatchdog::reset() {
    heard_ = false;
    last_valid_ms_ = 0;
}

}  // namespace duo_input::u2
