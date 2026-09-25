#pragma once

// The CDC side of U2: the one thing PC2's program has to say to its board.
//
// U2 holds no configuration and never will; this port exists so PC2 can hand
// over its own addresses and read PC1's. It speaks the same framing as U1 so
// the configurator reuses its transport, and it answers everything else with
// UNSUPPORTED_CAPABILITY rather than silence: a program pointed at the wrong
// board should be told so, not left to time out.

#include <cstddef>
#include <cstdint>

#include "link/host_addresses.hpp"
#include "protocol/frame.hpp"
#include "protocol/generated.hpp"

namespace duo_input::u2 {

class ByteSink {
public:
    virtual ~ByteSink() = default;
    virtual void write(const std::uint8_t* data, std::size_t size) = 0;
};

class AddressService {
private:
    /// Largest request this service answers, framed: HELLO or a full list,
    /// with room for COBS overhead. Anything longer is discarded to the next
    /// delimiter.
    ///
    /// Declared ahead of the public section below because kMaxReplyWire's
    /// in-class initializer needs its value, and a static data member's
    /// initializer - unlike a member function body - cannot forward-reference
    /// one declared later in the class.
    static constexpr std::size_t kMaxWire = 96;

public:
    AddressService(link::AddressBook& book, ByteSink& sink) : book_(book), sink_(sink) {}

    void on_cdc_bytes(const std::uint8_t* data, std::size_t size);

    /// The host went away. The next one starts with HELLO.
    void on_disconnect();

    /// The most bytes any single call to ByteSink::write can ever carry -
    /// exactly the capacity of reply()'s encode buffer (out_ below), sized
    /// generously for COBS overhead. Public so the CDC TX FIFO it is written
    /// into can be sized against it at compile time (see the static_assert
    /// beside U2's ByteSink in main.cpp).
    static constexpr std::size_t kMaxReplyWire = 2 * kMaxWire;

private:
    static constexpr std::size_t kMaxPayload = 64;

    void handle_frame(const std::uint8_t* wire, std::size_t size);
    void reply(protocol::CdcMessageType type, std::uint16_t sequence,
               const std::uint8_t* payload, std::size_t size);

    link::AddressBook& book_;
    ByteSink& sink_;
    bool negotiated_ = false;
    std::uint32_t capabilities_ = 0;
    std::uint8_t pending_[kMaxWire] = {};
    std::size_t pending_size_ = 0;
    bool overflowed_ = false;
    std::uint8_t decoded_[protocol::ProtocolLimits::CDC_MAX_PAYLOAD] = {};
    std::uint8_t payload_[kMaxPayload] = {};
    std::uint8_t out_[kMaxReplyWire] = {};
    std::uint8_t scratch_[kMaxReplyWire] = {};
};

}  // namespace duo_input::u2
