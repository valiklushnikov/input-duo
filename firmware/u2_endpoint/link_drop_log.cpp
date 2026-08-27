#include "link_drop_log.hpp"

namespace duo_input::u2 {

void LinkDropLog::released(std::uint32_t silence_ms) {
    if (in_drop_) {
        // Already counted. How much longer the link stays down says nothing
        // about the fail-safe, and counting every pass would measure how fast
        // the loop runs.
        return;
    }
    in_drop_ = true;

    if (drops_ < 255) {
        // Stops rather than wrapping: a link that drops constantly must not
        // report a handful of drops, which is the opposite of the truth.
        ++drops_;
    }
    last_release_ms_ = silence_ms > 65535 ? 65535 : static_cast<std::uint16_t>(silence_ms);
}

void LinkDropLog::recovered() {
    in_drop_ = false;
}

}  // namespace duo_input::u2
