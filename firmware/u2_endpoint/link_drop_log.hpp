#pragma once

// What U2 remembers about a link that died.
//
// The fail-safe cannot be watched while it happens. Everything the host can
// see about U2 travels over the link to U1, so at the moment U2 lets go of
// every key there is nothing left to tell. It has to remember instead, and
// say so once the link is back.
//
// That is what turns "releases within 100 ms" from an assertion in a unit
// test into something the hardware can be asked about.

#include <cstdint>

namespace duo_input::u2 {

class LinkDropLog {
public:
    /// Everything was released because the link went quiet.
    ///
    /// Called for as long as the link stays down; only the first call of a
    /// silence counts, because that is when the keys were let go.
    void released(std::uint32_t silence_ms);

    /// A valid frame arrived. The next silence is a new drop.
    void recovered();

    /// How many times the link has died. Saturates rather than wrapping.
    std::uint8_t drops() const { return drops_; }

    /// The silence that caused the most recent release, in milliseconds.
    std::uint16_t last_release_ms() const { return last_release_ms_; }

private:
    std::uint8_t drops_ = 0;
    std::uint16_t last_release_ms_ = 0;
    bool in_drop_ = false;
};

}  // namespace duo_input::u2
