#include "core_bridge.hpp"

namespace duo_input::u1 {

std::uint32_t ConfigHandoff::post(protocol::ByteView package) {
    package_ = package;
    const std::uint32_t ticket = requested_.load(std::memory_order_relaxed) + 1;
    // Release: the package has to be visible to Core 1 before the ticket that
    // tells it to go and read the package.
    requested_.store(ticket, std::memory_order_release);
    return ticket;
}

bool ConfigHandoff::finished(std::uint32_t ticket) const {
    return acknowledged_.load(std::memory_order_acquire) == ticket;
}

bool ConfigHandoff::take(protocol::ByteView& package) {
    const std::uint32_t ticket = requested_.load(std::memory_order_acquire);
    if (ticket == acknowledged_.load(std::memory_order_relaxed)) {
        return false;
    }
    taken_ = ticket;
    package = package_;
    return true;
}

void ConfigHandoff::complete(bool loaded) {
    loaded_ = loaded;
    // Release: the outcome, and everything Core 1 rebuilt before saying so,
    // must be visible to Core 0 before the acknowledgement it is waiting on.
    acknowledged_.store(taken_, std::memory_order_release);
}

}  // namespace duo_input::u1
