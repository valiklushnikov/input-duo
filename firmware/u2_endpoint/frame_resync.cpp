#include "frame_resync.hpp"

namespace duo_input::u2 {

bool FrameResync::update(std::uint32_t now_ms, std::uint32_t remaining,
                         std::uint32_t frame_size) {
    if (remaining == frame_size || remaining == 0) {
        // Nothing has begun, or everything has arrived. Neither is a stall,
        // and both end whatever was being watched.
        watching_ = false;
        return false;
    }

    if (!watching_ || remaining != last_remaining_) {
        // A frame has begun, or it moved since the last look. Either way the
        // wait starts from now.
        watching_ = true;
        last_remaining_ = remaining;
        last_change_ms_ = now_ms;
        return false;
    }

    // Unsigned arithmetic, so the millisecond counter wrapping measures the
    // real gap instead of leaping backwards and abandoning a healthy frame.
    if (now_ms - last_change_ms_ < kResyncStallMs) {
        return false;
    }

    // Reported once. The caller restarts the transfer on being told, and
    // saying so again would restart the frame that had just begun arriving.
    watching_ = false;
    return true;
}

}  // namespace duo_input::u2
