// What PC2 is actually told, and what it costs the pointer when it isn't.
//
// poll sends one frame per pass and returns after the first thing it has to
// say. Movement is a delta: whoever asks for it consumes it, and it cannot be
// asked for twice. Main used to consume it before poll had decided anything,
// so every pass in which PC2's keyboard changed - every keystroke, and every
// failed transfer - threw that pass's motion away. UsbService::publish has
// always done this the other way round for PC1.

#include "fakes/spi_link.hpp"
#include "hid/state_manager.hpp"
#include "link/host_addresses.hpp"
#include "link/spi_protocol.hpp"
#include "spi_master.hpp"
#include "test_support.hpp"

using duo_input::hid::HidStateManager;
using duo_input::hid::MouseSnapshot;
using duo_input::hid::Target;
using duo_input::protocol::SpiMessageType;
using duo_input::u1::SpiMaster;

namespace {

/// The movement PC2 was told about, added up across every MOUSE_DELTA frame.
int total_dx() {
    const duo::test::SpiLink& link = duo::test::spi_link();
    int moved = 0;
    for (std::size_t index = 0; index < link.count; ++index) {
        if (link.type[index] != SpiMessageType::MOUSE_DELTA) {
            continue;
        }
        MouseSnapshot mouse;
        CHECK(duo_input::link::decode_mouse_delta(
            duo_input::protocol::ByteView{link.payload[index], link.payload_size[index]},
            mouse));
        moved += mouse.delta_x;
    }
    return moved;
}

/// The first pass a fresh master makes always sends the keyboard state, having
/// never told U2 anything. Get it out of the way and forget it.
void prime(SpiMaster& link, HidStateManager& outputs) {
    duo::test::spi_link().reset();
    CHECK(link.poll(0, outputs));
    CHECK_EQ(duo::test::spi_link().count_of(SpiMessageType::KBD_STATE), 1u);
    duo::test::spi_link().reset();
}

}  // namespace

TEST_CASE(motion_survives_a_pass_in_which_the_pc2_keyboard_also_changed) {
    HidStateManager outputs;
    SpiMaster link;
    prime(link, outputs);

    // One pass: a key went down on PC2 and the mouse moved. poll returns after
    // the keyboard frame, so this pass says nothing about the movement.
    outputs.physical_key(Target::Pc2, 0x04, true);
    outputs.mouse_delta(Target::Pc2, 5, 0, 0, 0);
    CHECK(link.poll(1, outputs));
    CHECK_EQ(duo::test::spi_link().count_of(SpiMessageType::KBD_STATE), 1u);
    CHECK_EQ(duo::test::spi_link().count_of(SpiMessageType::MOUSE_DELTA), 0u);

    // The next pass has to. The pointer was delayed a millisecond, which is
    // what a busy link costs; it was not moved five counts less far, which is
    // what consuming the snapshot up front cost it on every keystroke.
    CHECK(link.poll(2, outputs));
    CHECK_EQ(duo::test::spi_link().count_of(SpiMessageType::MOUSE_DELTA), 1u);
    CHECK_EQ(total_dx(), 5);
}

TEST_CASE(motion_survives_a_transfer_that_did_not_go_out) {
    HidStateManager outputs;
    SpiMaster link;
    prime(link, outputs);

    outputs.mouse_delta(Target::Pc2, 7, 0, 0, 0);
    duo::test::spi_link().accept = false;
    CHECK_FALSE(link.poll(1, outputs));

    duo::test::spi_link().accept = true;
    CHECK(link.poll(2, outputs));

    // A flaky link delays the pointer. It must not silently lose where it went.
    CHECK_EQ(total_dx(), 7);
}

TEST_CASE(movement_that_did_go_out_is_not_sent_a_second_time) {
    HidStateManager outputs;
    SpiMaster link;
    prime(link, outputs);

    outputs.mouse_delta(Target::Pc2, 3, 0, 0, 0);
    CHECK(link.poll(1, outputs));
    CHECK_EQ(total_dx(), 3);

    // A delta already reported is a delta the pointer has already travelled.
    // Sending it again would move the cursor twice as far as the mouse did.
    link.poll(2, outputs);
    CHECK_EQ(total_dx(), 3);
    CHECK_EQ(duo::test::spi_link().count_of(SpiMessageType::MOUSE_DELTA), 1u);
}

TEST_CASE(a_quiet_link_still_heartbeats) {
    HidStateManager outputs;
    SpiMaster link;
    prime(link, outputs);

    // Silence and a severed cable look identical from U2's end, so a pass with
    // nothing to report still has to say something - but not on every pass.
    CHECK_FALSE(link.poll(1, outputs));
    CHECK(link.poll(duo_input::u1::kHeartbeatIntervalMs, outputs));
    CHECK_EQ(duo::test::spi_link().count_of(SpiMessageType::HEARTBEAT), 1u);
}

// -------------------------------------------------------- address exchange

namespace {

duo_input::link::HostAddresses one_address() {
    duo_input::link::HostAddresses list;
    list.count = 1;
    list.octets[0][0] = 192;
    list.octets[0][1] = 168;
    list.octets[0][2] = 1;
    list.octets[0][3] = 7;
    return list;
}

}  // namespace

TEST_CASE(no_address_frame_goes_out_until_the_host_gave_a_list) {
    HidStateManager outputs;
    SpiMaster link;
    duo_input::link::AddressBook book;
    link.set_address_book(&book);
    prime(link, outputs);
    for (std::uint32_t now = 1; now < 1000; ++now) {
        link.poll(now, outputs);
    }
    CHECK_EQ(duo::test::spi_link().count_of(SpiMessageType::HOST_ADDRESSES), 0u);
}

TEST_CASE(the_hosts_list_goes_out_on_its_interval_even_under_continuous_motion) {
    HidStateManager outputs;
    SpiMaster link;
    duo_input::link::AddressBook book;
    link.set_address_book(&book);
    prime(link, outputs);
    book.set_local(one_address());

    // A mouse that never stops: HEARTBEAT would never be due, so the list
    // must not depend on it.
    for (std::uint32_t now = 1; now <= 1000; ++now) {
        outputs.mouse_delta(Target::Pc2, 1, 0, 0, 0);
        link.poll(now, outputs);
    }
    const std::size_t sent = duo::test::spi_link().count_of(SpiMessageType::HOST_ADDRESSES);
    CHECK(sent >= 4u);
    CHECK(sent <= 5u);
}

TEST_CASE(motion_held_back_by_an_address_frame_is_sent_on_the_next_pass) {
    HidStateManager outputs;
    SpiMaster link;
    duo_input::link::AddressBook book;
    link.set_address_book(&book);
    prime(link, outputs);
    book.set_local(one_address());

    outputs.mouse_delta(Target::Pc2, 5, 0, 0, 0);
    CHECK(link.poll(1, outputs));
    CHECK_EQ(duo::test::spi_link().count_of(SpiMessageType::HOST_ADDRESSES), 1u);
    CHECK(link.poll(2, outputs));
    CHECK_EQ(total_dx(), 5);
}

TEST_CASE(an_endpoint_address_reply_fills_the_peer_list) {
    SpiMaster link;
    duo_input::link::AddressBook book;
    link.set_address_book(&book);
    const std::uint8_t payload[] = {1, 10, 0, 0, 2};
    duo_input::protocol::SpiFrame frame;
    frame.type = SpiMessageType::ENDPOINT_ADDRESSES;
    frame.payload = duo_input::protocol::ByteView{payload, sizeof(payload)};

    link.apply_reply(frame);

    CHECK(link.status().answered);
    CHECK_EQ(book.peer().count, 1u);
    CHECK_EQ(book.peer().octets[0][0], 10u);
}

TEST_CASE(an_endpoint_address_reply_with_no_book_set_does_not_crash) {
    // U2 can start answering ENDPOINT_ADDRESSES before main() has wired a
    // book in, or in a build that never does. Nothing here may assume the
    // pointer is non-null.
    SpiMaster link;
    const std::uint8_t payload[] = {1, 10, 0, 0, 2};
    duo_input::protocol::SpiFrame frame;
    frame.type = SpiMessageType::ENDPOINT_ADDRESSES;
    frame.payload = duo_input::protocol::ByteView{payload, sizeof(payload)};

    link.apply_reply(frame);

    CHECK(link.status().answered);
}

TEST_CASE(an_address_reply_is_an_endpoint_reply_and_the_hosts_own_is_not) {
    // U1 tells an echo on the wires from U2's answer by type alone. Sharing a
    // type between the two directions would let an echo pass as U2.
    CHECK(duo_input::link::is_endpoint_reply(SpiMessageType::ENDPOINT_ADDRESSES));
    CHECK_FALSE(duo_input::link::is_endpoint_reply(SpiMessageType::HOST_ADDRESSES));
}
