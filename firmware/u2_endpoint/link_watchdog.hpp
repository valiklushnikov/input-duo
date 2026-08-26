#pragma once

// The one rule U2 keeps without being told: no valid frame, nothing held.
//
// If the link to U1 dies while a key is down, PC2 keeps receiving that key
// until something says otherwise - and nothing will, because the thing that
// would say so is the link that just died. A stuck modifier is worse than a
// missed keystroke: it changes what every later key means, and the operator
// cannot fix it from the computer that is receiving it.
//
// So U2 counts the silence itself and releases everything after 100 ms. That
// number is a compromise: long enough that ordinary jitter on a 1 MHz link
// does not trip it, short enough that nobody finishes a sentence in it.

#include <cstdint>

namespace duo_input::u2 {

/// How long U2 waits without a valid frame before releasing everything.
inline constexpr std::uint32_t kLinkTimeoutMs = 100;

class LinkWatchdog {
public:
    explicit LinkWatchdog(std::uint32_t timeout_ms = kLinkTimeoutMs)
        : timeout_ms_(timeout_ms) {}

    /// A frame arrived and passed its CRC. Note the time.
    void observe_valid(std::uint32_t now_ms);

    /// Has the link been silent for longer than the timeout?
    ///
    /// True before the first valid frame: a board that has never heard from U1
    /// has no link, and saying otherwise would be a claim it cannot support.
    bool expired(std::uint32_t now_ms) const;

    /// Forget the link, as after a reset.
    void reset();

    /// Milliseconds since the last valid frame, or 0 if there has been none.
    std::uint32_t silence_ms(std::uint32_t now_ms) const;

private:
    std::uint32_t timeout_ms_;
    std::uint32_t last_valid_ms_ = 0;
    bool heard_ = false;
};

}  // namespace duo_input::u2
