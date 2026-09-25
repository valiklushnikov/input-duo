// The list of addresses a computer can be reached at, as the two boards carry
// it between the two computers - and when each board repeats its own.

#include "link/host_addresses.hpp"
#include "test_support.hpp"

using duo_input::link::AddressBook;
using duo_input::link::HostAddresses;
using duo_input::link::decode_host_addresses;
using duo_input::link::encode_host_addresses;
using duo_input::link::kEndpointAddressesEvery;
using duo_input::link::kHostAddressesIntervalMs;
using duo_input::protocol::ByteView;
using duo_input::protocol::MutableByteView;

namespace {

HostAddresses two_addresses() {
    HostAddresses list;
    list.count = 2;
    const std::uint8_t first[4] = {192, 168, 1, 7};
    const std::uint8_t second[4] = {10, 0, 0, 2};
    for (int i = 0; i < 4; ++i) {
        list.octets[0][i] = first[i];
        list.octets[1][i] = second[i];
    }
    return list;
}

}  // namespace

TEST_CASE(a_list_survives_the_round_trip) {
    std::uint8_t wire[33] = {};
    std::size_t written = 0;
    CHECK(encode_host_addresses(two_addresses(), MutableByteView{wire, sizeof(wire)}, written));
    CHECK_EQ(written, 9u);
    CHECK_EQ(wire[0], 2u);
    CHECK_EQ(wire[1], 192u);

    HostAddresses back;
    CHECK(decode_host_addresses(ByteView{wire, written}, back));
    CHECK_EQ(back.count, 2u);
    CHECK_EQ(back.octets[1][0], 10u);
    CHECK_EQ(back.octets[1][3], 2u);
}

TEST_CASE(an_empty_list_is_one_byte) {
    std::uint8_t wire[33] = {};
    std::size_t written = 0;
    CHECK(encode_host_addresses(HostAddresses{}, MutableByteView{wire, sizeof(wire)}, written));
    CHECK_EQ(written, 1u);
    HostAddresses back;
    CHECK(decode_host_addresses(ByteView{wire, 1}, back));
    CHECK_EQ(back.count, 0u);
}

TEST_CASE(more_than_eight_addresses_is_refused) {
    std::uint8_t wire[1 + 4 * 9] = {9};
    for (std::size_t i = 1; i < sizeof(wire); ++i) wire[i] = 1;
    HostAddresses back;
    CHECK_FALSE(decode_host_addresses(ByteView{wire, sizeof(wire)}, back));
}

TEST_CASE(a_length_that_disagrees_with_the_count_is_refused) {
    const std::uint8_t short_by_one[] = {1, 192, 168, 1};
    const std::uint8_t long_by_one[] = {1, 192, 168, 1, 7, 0};
    HostAddresses back;
    CHECK_FALSE(decode_host_addresses(ByteView{short_by_one, sizeof(short_by_one)}, back));
    CHECK_FALSE(decode_host_addresses(ByteView{long_by_one, sizeof(long_by_one)}, back));
    CHECK_FALSE(decode_host_addresses(ByteView{nullptr, 0}, back));
}

TEST_CASE(the_unspecified_address_is_refused) {
    const std::uint8_t zero[] = {1, 0, 0, 0, 0};
    HostAddresses back;
    CHECK_FALSE(decode_host_addresses(ByteView{zero, sizeof(zero)}, back));
}

TEST_CASE(a_refused_peer_list_leaves_the_previous_one) {
    AddressBook book;
    std::uint8_t wire[33] = {};
    std::size_t written = 0;
    encode_host_addresses(two_addresses(), MutableByteView{wire, sizeof(wire)}, written);
    CHECK(book.accept_peer(ByteView{wire, written}));

    const std::uint8_t broken[] = {3, 1, 2};
    CHECK_FALSE(book.accept_peer(ByteView{broken, sizeof(broken)}));
    CHECK_EQ(book.peer().count, 2u);
}

TEST_CASE(nothing_is_due_before_the_host_has_said_anything) {
    AddressBook book;
    CHECK_FALSE(book.local_due(0));
    CHECK_FALSE(book.local_due(100000));
    for (std::uint32_t i = 0; i < 2 * kEndpointAddressesEvery; ++i) {
        CHECK_FALSE(book.take_reply_slot());
    }
}

TEST_CASE(the_master_repeats_the_list_on_its_interval) {
    AddressBook book;
    book.set_local(two_addresses());
    CHECK(book.local_due(5));
    book.mark_local_sent(5);
    CHECK_FALSE(book.local_due(5 + kHostAddressesIntervalMs - 1));
    CHECK(book.local_due(5 + kHostAddressesIntervalMs));
}

TEST_CASE(an_empty_list_from_the_host_is_still_repeated) {
    // "No addresses" is an answer: the far side must stop showing old ones.
    AddressBook book;
    book.set_local(HostAddresses{});
    CHECK(book.local_due(0));
}

TEST_CASE(the_endpoint_answers_with_its_list_once_in_so_many_replies) {
    AddressBook book;
    book.set_local(two_addresses());
    std::uint32_t slots = 0;
    for (std::uint32_t i = 0; i < 3 * kEndpointAddressesEvery; ++i) {
        if (book.take_reply_slot()) ++slots;
    }
    CHECK_EQ(slots, 3u);
}
