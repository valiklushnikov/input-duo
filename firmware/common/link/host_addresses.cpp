#include "link/host_addresses.hpp"

namespace duo_input::link {

bool encode_host_addresses(const HostAddresses& addresses, protocol::MutableByteView output,
                           std::size_t& written) {
    written = 0;
    if (addresses.count > kMaxHostAddresses) {
        return false;
    }
    const std::size_t size = 1 + 4 * static_cast<std::size_t>(addresses.count);
    if (output.data == nullptr || output.size < size) {
        return false;
    }
    output.data[0] = addresses.count;
    for (std::size_t index = 0; index < addresses.count; ++index) {
        for (std::size_t octet = 0; octet < 4; ++octet) {
            output.data[1 + 4 * index + octet] = addresses.octets[index][octet];
        }
    }
    written = size;
    return true;
}

bool decode_host_addresses(protocol::ByteView payload, HostAddresses& addresses) {
    if (payload.data == nullptr || payload.size == 0) {
        return false;
    }
    const std::uint8_t count = payload.data[0];
    if (count > kMaxHostAddresses ||
        payload.size != 1 + 4 * static_cast<std::size_t>(count)) {
        return false;
    }
    HostAddresses decoded;
    decoded.count = count;
    for (std::size_t index = 0; index < count; ++index) {
        bool all_zero = true;
        for (std::size_t octet = 0; octet < 4; ++octet) {
            const std::uint8_t value = payload.data[1 + 4 * index + octet];
            decoded.octets[index][octet] = value;
            all_zero = all_zero && value == 0;
        }
        if (all_zero) {
            // Nobody can be called at 0.0.0.0. A list carrying it was built
            // by something that did not know an address, and pretending it
            // did would send a connection nowhere.
            return false;
        }
    }
    addresses = decoded;
    return true;
}

void AddressBook::set_local(const HostAddresses& addresses) {
    local_ = addresses;
    has_local_ = true;
}

bool AddressBook::accept_peer(protocol::ByteView payload) {
    HostAddresses decoded;
    if (!decode_host_addresses(payload, decoded)) {
        return false;
    }
    peer_ = decoded;
    return true;
}

bool AddressBook::local_due(std::uint32_t now_ms) const {
    return has_local_ && (!ever_sent_ || now_ms - last_sent_ms_ >= kHostAddressesIntervalMs);
}

void AddressBook::mark_local_sent(std::uint32_t now_ms) {
    ever_sent_ = true;
    last_sent_ms_ = now_ms;
}

bool AddressBook::take_reply_slot() {
    if (!has_local_) {
        return false;
    }
    if (++replies_ < kEndpointAddressesEvery) {
        return false;
    }
    replies_ = 0;
    return true;
}

}  // namespace duo_input::link
