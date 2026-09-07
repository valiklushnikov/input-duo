#pragma once

// Whether U2 has just come back, and therefore has to be told to let go.
//
// U2 releases everything after 100 ms of silence; U1 keeps no such clock and
// SpiMaster sends on change, so after an outage the two ends can disagree
// about what PC2 is holding with nothing to correct it. One
// CONTROL_RELEASE_ALL on the pass the link starts answering again makes them
// agree, and clears SpiMaster's own record of what PC2 already knows so the
// next state goes out whether or not it changed.
//
// A class rather than two lines in main because main.cpp cannot be built on a
// desktop, and "once per reconnection, not once per pass" is exactly the kind
// of thing that is only ever wrong on hardware: a release every pass is a
// keyboard that cannot hold a key on PC2 at all.
//
// The first answer after boot counts as a reconnection. U1 has just started
// and knows nothing about what U2 is holding, which is the same position an
// outage leaves it in.

namespace duo_input::u1::reference {

class LinkReconnect {
public:
    /// Call once per pass with SpiMaster's ``status().answered``.
    ///
    /// Returns true on the pass the link started answering, and only then.
    bool should_release(bool answered) {
        const bool returned = answered && !answered_;
        answered_ = answered;
        return returned;
    }

private:
    bool answered_ = false;
};

}  // namespace duo_input::u1::reference
