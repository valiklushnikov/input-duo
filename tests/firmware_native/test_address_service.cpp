// What U2 says to the one program that talks to it: who it is, and the other
// computer's addresses in exchange for this one's.

#include <cstring>
#include <vector>

#include "address_service.hpp"
#include "link/host_addresses.hpp"
#include "protocol/frame.hpp"
#include "test_support.hpp"

using duo_input::protocol::CdcFrame;
using duo_input::protocol::CdcMessageType;
using duo_input::protocol::CdcError;
using duo_input::protocol::ProtocolLimits;

namespace {

class Recorder : public duo_input::u2::ByteSink {
public:
    void write(const std::uint8_t* data, std::size_t size) override {
        last.assign(data, data + size);
        ++writes;
    }
    std::vector<std::uint8_t> last;
    int writes = 0;
};

struct Board {
    duo_input::link::AddressBook book;
    Recorder sink;
    duo_input::u2::AddressService service{book, sink};
    std::uint16_t sequence = 0;
    std::uint8_t scratch[ProtocolLimits::CDC_MAX_PAYLOAD] = {};
    duo_input::protocol::DecodeResult result{};

    CdcFrame send(CdcMessageType type, const std::uint8_t* payload, std::size_t size) {
        CdcFrame request;
        request.type = type;
        request.sequence = sequence++;
        request.payload = duo_input::protocol::ByteView{payload, size};
        std::uint8_t wire[256];
        std::uint8_t encode_scratch[256];
        std::size_t written = 0;
        duo_input::protocol::encode_cdc_frame(
            request, duo_input::protocol::MutableByteView{wire, sizeof(wire)},
            duo_input::protocol::MutableByteView{encode_scratch, sizeof(encode_scratch)},
            written);
        sink.writes = 0;
        service.on_cdc_bytes(wire, written);
        if (sink.writes == 0) {
            return CdcFrame{};
        }
        duo_input::protocol::decode_cdc_frame(
            duo_input::protocol::ByteView{sink.last.data(), sink.last.size()},
            duo_input::protocol::MutableByteView{scratch, sizeof(scratch)}, result);
        return result.cdc;
    }

    CdcFrame hello(std::uint32_t requested = 0xFFFFFFFFu) {
        const std::uint8_t request[4] = {
            static_cast<std::uint8_t>(requested), static_cast<std::uint8_t>(requested >> 8),
            static_cast<std::uint8_t>(requested >> 16), static_cast<std::uint8_t>(requested >> 24)};
        return send(CdcMessageType::HELLO, request, sizeof(request));
    }
};

std::uint32_t caps_of(const CdcFrame& frame) {
    const std::uint8_t* p = frame.payload.data + 3;
    return static_cast<std::uint32_t>(p[0]) | (static_cast<std::uint32_t>(p[1]) << 8) |
           (static_cast<std::uint32_t>(p[2]) << 16) | (static_cast<std::uint32_t>(p[3]) << 24);
}

}  // namespace

TEST_CASE(u2_answers_hello_with_a_device_info_the_configurator_can_parse) {
    Board board;
    const CdcFrame reply = board.hello();
    CHECK(reply.type == CdcMessageType::DEVICE_INFO);
    CHECK_EQ(reply.payload.size, 44u);
    CHECK_EQ(reply.payload.data[0], static_cast<std::uint8_t>(CdcError::Ok));
    CHECK_EQ(reply.payload.data[1], duo_input::protocol::PROTOCOL_VERSION_MAJOR);
    CHECK_EQ(caps_of(reply),
             static_cast<std::uint32_t>(duo_input::protocol::Capability::ADDRESS_EXCHANGE));
}

TEST_CASE(u2_exchanges_addresses_after_hello) {
    Board board;
    const std::uint8_t peer[] = {1, 192, 168, 1, 7};
    CHECK(board.book.accept_peer(duo_input::protocol::ByteView{peer, sizeof(peer)}));
    board.hello();

    const std::uint8_t mine[] = {1, 10, 0, 0, 2};
    const CdcFrame reply = board.send(CdcMessageType::EXCHANGE_ADDRESSES, mine, sizeof(mine));

    CHECK(reply.type == CdcMessageType::EXCHANGE_ADDRESSES);
    CHECK_EQ(reply.payload.size, 6u);
    CHECK_EQ(reply.payload.data[0], static_cast<std::uint8_t>(CdcError::Ok));
    CHECK_EQ(reply.payload.data[2], 192u);
    CHECK(board.book.has_local());
    CHECK_EQ(board.book.local().octets[0][0], 10u);
}

TEST_CASE(u2_rejects_a_malformed_hello_and_does_not_negotiate) {
    Board board;
    const std::uint8_t short_hello[] = {1, 2, 3};  // HELLO wants exactly 4 bytes.
    const CdcFrame hello_reply =
        board.send(CdcMessageType::HELLO, short_hello, sizeof(short_hello));
    CHECK(hello_reply.type == CdcMessageType::DEVICE_INFO);
    CHECK_EQ(hello_reply.payload.data[0], static_cast<std::uint8_t>(CdcError::InvalidRequest));

    // A malformed HELLO must not count as a handshake: the next request is
    // still pre-negotiation.
    const std::uint8_t mine[] = {1, 10, 0, 0, 2};
    const CdcFrame reply = board.send(CdcMessageType::EXCHANGE_ADDRESSES, mine, sizeof(mine));
    CHECK_EQ(reply.payload.data[0], static_cast<std::uint8_t>(CdcError::BadState));
}

TEST_CASE(u2_refuses_an_exchange_before_hello) {
    Board board;
    const std::uint8_t mine[] = {1, 10, 0, 0, 2};
    const CdcFrame reply = board.send(CdcMessageType::EXCHANGE_ADDRESSES, mine, sizeof(mine));
    CHECK_EQ(reply.payload.data[0], static_cast<std::uint8_t>(CdcError::BadState));
    CHECK_FALSE(board.book.has_local());
}

TEST_CASE(u2_refuses_an_exchange_the_host_did_not_ask_for) {
    Board board;
    board.hello(0);
    const std::uint8_t mine[] = {1, 10, 0, 0, 2};
    const CdcFrame reply = board.send(CdcMessageType::EXCHANGE_ADDRESSES, mine, sizeof(mine));
    CHECK_EQ(reply.payload.data[0], static_cast<std::uint8_t>(CdcError::UnsupportedCapability));
}

TEST_CASE(u2_refuses_a_broken_list_and_keeps_the_old_one) {
    Board board;
    board.hello();
    const std::uint8_t good[] = {1, 10, 0, 0, 2};
    board.send(CdcMessageType::EXCHANGE_ADDRESSES, good, sizeof(good));
    const std::uint8_t broken[] = {2, 10, 0, 0, 3};
    const CdcFrame reply = board.send(CdcMessageType::EXCHANGE_ADDRESSES, broken, sizeof(broken));
    CHECK_EQ(reply.payload.data[0], static_cast<std::uint8_t>(CdcError::InvalidRequest));
    CHECK_EQ(board.book.local().octets[0][3], 2u);
}

TEST_CASE(u2_says_plainly_that_it_is_not_a_configurable_board) {
    Board board;
    board.hello();
    const CdcFrame reply = board.send(CdcMessageType::GET_STATUS, nullptr, 0);
    CHECK(reply.type == CdcMessageType::GET_STATUS);
    CHECK_EQ(reply.payload.data[0], static_cast<std::uint8_t>(CdcError::UnsupportedCapability));
}

TEST_CASE(u2_answers_ping_before_hello) {
    Board board;
    const std::uint8_t probe[] = {7, 8};
    const CdcFrame reply = board.send(CdcMessageType::PING, probe, sizeof(probe));
    CHECK(reply.type == CdcMessageType::PING);
    CHECK_EQ(reply.payload.size, 3u);
    CHECK_EQ(reply.payload.data[2], 8u);
}

TEST_CASE(u2_truncates_a_ping_reply_instead_of_overflowing_its_buffer) {
    Board board;
    // At kMaxPayload (64) bytes the request itself still fits comfortably in
    // the service's 96-byte wire budget (raw frame 14 + 64 = 78 bytes, COBS
    // overhead 2 bytes => 80-byte wire frame), so this exercises the reply
    // truncation guard, not the input-side overflow guard covered below.
    std::uint8_t probe[64];
    for (std::size_t i = 0; i < sizeof(probe); ++i) {
        probe[i] = static_cast<std::uint8_t>(i);
    }
    const CdcFrame reply = board.send(CdcMessageType::PING, probe, sizeof(probe));
    CHECK(reply.type == CdcMessageType::PING);
    CHECK_EQ(reply.payload.size, 1u);
    CHECK_EQ(reply.payload.data[0], static_cast<std::uint8_t>(CdcError::Ok));
}

TEST_CASE(u2_forgets_negotiation_after_disconnect) {
    Board board;
    board.hello();
    board.service.on_disconnect();
    const std::uint8_t mine[] = {1, 10, 0, 0, 2};
    const CdcFrame reply = board.send(CdcMessageType::EXCHANGE_ADDRESSES, mine, sizeof(mine));
    CHECK_EQ(reply.payload.data[0], static_cast<std::uint8_t>(CdcError::BadState));
}

TEST_CASE(u2_ignores_garbage_and_recovers_on_the_next_frame) {
    Board board;
    std::uint8_t noise[300];
    std::memset(noise, 0x55, sizeof(noise));
    board.service.on_cdc_bytes(noise, sizeof(noise));
    const std::uint8_t delimiter = 0;
    board.service.on_cdc_bytes(&delimiter, 1);
    CHECK_EQ(board.sink.writes, 0);
    const CdcFrame reply = board.hello();
    CHECK(reply.type == CdcMessageType::DEVICE_INFO);
}

TEST_CASE(u2_discards_a_whole_overflow_run_even_when_it_hides_a_valid_frame) {
    Board board;

    // A complete, independently valid HELLO frame, built without going
    // through the service, so it can be glued onto the end of an overflow
    // run with no delimiter of its own in front of it.
    const std::uint8_t hello_request[4] = {0xFF, 0xFF, 0xFF, 0xFF};
    duo_input::protocol::CdcFrame request;
    request.type = CdcMessageType::HELLO;
    request.sequence = 1;
    request.payload = duo_input::protocol::ByteView{hello_request, sizeof(hello_request)};
    std::uint8_t hello_wire[64];
    std::uint8_t encode_scratch[64];
    std::size_t hello_wire_size = 0;
    CHECK(duo_input::protocol::encode_cdc_frame(
        request, duo_input::protocol::MutableByteView{hello_wire, sizeof(hello_wire)},
        duo_input::protocol::MutableByteView{encode_scratch, sizeof(encode_scratch)},
        hello_wire_size));

    // Two full cycles of the service's 96-byte wire budget: enough that a
    // mutant which resets pending_size_ to 0 on overflow (instead of
    // discarding to the next delimiter) would land back on an aligned zero
    // right as the embedded HELLO begins, and so accidentally decode it
    // cleanly. Any length would demonstrate that CRC rejects raw noise;
    // only an aligned length demonstrates that a resync-by-luck bug is
    // caught too.
    std::uint8_t noise[192];
    std::memset(noise, 0x55, sizeof(noise));
    board.service.on_cdc_bytes(noise, sizeof(noise));
    board.service.on_cdc_bytes(hello_wire, hello_wire_size);
    CHECK_EQ(board.sink.writes, 0);

    // The buffer must be clean for the next, unrelated frame.
    const CdcFrame reply = board.hello();
    CHECK(reply.type == CdcMessageType::DEVICE_INFO);
}
