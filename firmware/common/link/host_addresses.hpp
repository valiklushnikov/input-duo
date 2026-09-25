#pragma once

// Where a computer can be reached, as the two boards pass it between the two
// computers.
//
// Each computer tells its own board its IPv4 addresses; the boards swap those
// lists over the SPI link; each computer then asks its board for the other
// one's. The list travels whole, every time, like everything else on that
// link: a lost frame costs a repeat, never a disagreement.
//
// Nothing here is stored in flash. A board that lost power knows nothing
// until its computer tells it again, which it does every few seconds.

#include <cstddef>
#include <cstdint>

#include "protocol/bytes.hpp"

namespace duo_input::link {

inline constexpr std::size_t kMaxHostAddresses = 8;

/// The count byte and eight addresses of four bytes.
inline constexpr std::size_t kHostAddressesMaxSize = 1 + 4 * kMaxHostAddresses;

/// U1 repeats its computer's list this often. Well inside a human's patience,
/// and one frame in two hundred and fifty on a busy link.
inline constexpr std::uint32_t kHostAddressesIntervalMs = 250;

/// U2 answers with its computer's list instead of its status once in this
/// many replies. U1 clocks at least one transfer every 20 ms, so this is at
/// most every 1.3 s.
inline constexpr std::uint32_t kEndpointAddressesEvery = 64;

struct HostAddresses {
    std::uint8_t count = 0;
    /// Network order: octets[i][0] is the first number of the dotted form.
    std::uint8_t octets[kMaxHostAddresses][4] = {};
};

bool encode_host_addresses(const HostAddresses& addresses, protocol::MutableByteView output,
                           std::size_t& written);

/// Refuses anything that is not exactly one count byte and that many
/// addresses, more than kMaxHostAddresses, or 0.0.0.0.
bool decode_host_addresses(protocol::ByteView payload, HostAddresses& addresses);

/// Both lists one board holds, and when it is due to repeat its own.
class AddressBook {
public:
    void set_local(const HostAddresses& addresses);
    bool has_local() const { return has_local_; }
    const HostAddresses& local() const { return local_; }

    const HostAddresses& peer() const { return peer_; }

    /// Take the far side's list from a frame. A payload that does not decode
    /// leaves the list that was there.
    bool accept_peer(protocol::ByteView payload);

    /// Master side: time to send the local list again.
    bool local_due(std::uint32_t now_ms) const;
    void mark_local_sent(std::uint32_t now_ms);

    /// Slave side: called once per reply; true when this reply should carry
    /// the local list instead of the status.
    bool take_reply_slot();

private:
    HostAddresses local_{};
    HostAddresses peer_{};
    bool has_local_ = false;
    bool ever_sent_ = false;
    std::uint32_t last_sent_ms_ = 0;
    std::uint32_t replies_ = 0;
};

}  // namespace duo_input::link
