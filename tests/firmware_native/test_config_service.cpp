// The CDC conversation, driven the way the configurator drives it.
//
// The Python emulator in configurator/src/duo_input/device/emulator.py is the
// reference for every payload shape here. The firmware has to agree with it
// byte for byte, because the configurator was written against it and cannot
// tell the two apart.

#include <cstring>
#include <vector>

#include "config_service.hpp"
#include "crypto/sha256.hpp"
#include "storage/ab_store.hpp"
#include "test_support.hpp"

using duo_input::crypto::kSha256DigestSize;
using duo_input::crypto::sha256;
using duo_input::protocol::CdcFrame;
using duo_input::protocol::CdcMessageType;
using duo_input::protocol::ProtocolLimits;
using duo_input::storage::AbStore;
using duo_input::storage::FlashBackend;
using duo_input::u1::CdcError;
using duo_input::u1::CdcSink;
using duo_input::u1::ConfigService;

namespace {

class MemoryFlash : public FlashBackend {
public:
    MemoryFlash() : bytes_(duo_input::storage::kFlashSize, 0xFF) {}

    bool erase(std::uint32_t offset, std::size_t size) override {
        std::memset(&bytes_[offset], 0xFF, size);
        return true;
    }
    bool program(std::uint32_t offset, const std::uint8_t* data, std::size_t size) override {
        std::memcpy(&bytes_[offset], data, size);
        return true;
    }
    bool read(std::uint32_t offset, std::uint8_t* data, std::size_t size) const override {
        std::memcpy(data, &bytes_[offset], size);
        return true;
    }
    const std::uint8_t* direct(std::uint32_t offset) const override { return &bytes_[offset]; }

private:
    std::vector<std::uint8_t> bytes_;
};

/// Collects the replies the service writes out.
class Recorder : public CdcSink {
public:
    void write(const std::uint8_t* data, std::size_t size) override {
        frames_.emplace_back(data, data + size);
    }

    std::size_t count() const { return frames_.size(); }
    const std::vector<std::uint8_t>& last() const { return frames_.back(); }
    void clear() { frames_.clear(); }

private:
    std::vector<std::vector<std::uint8_t>> frames_;
};

/// One conversation: a device, its flash and the wire between them.
struct Link {
    MemoryFlash flash;
    AbStore store{flash};
    Recorder replies;
    ConfigService service{store, replies};
    std::uint16_t sequence = 0;

    /// Send one request and return the decoded reply.
    CdcFrame send(CdcMessageType type, const std::uint8_t* payload, std::size_t size) {
        CdcFrame request;
        request.type = type;
        request.sequence = sequence++;
        request.payload = duo_input::protocol::ByteView{payload, size};

        std::uint8_t wire[1200];
        std::uint8_t scratch[1200];
        std::size_t written = 0;
        duo_input::protocol::encode_cdc_frame(
            request, duo_input::protocol::MutableByteView{wire, sizeof(wire)},
            duo_input::protocol::MutableByteView{scratch, sizeof(scratch)}, written);

        replies.clear();
        service.on_cdc_bytes(wire, written);
        return decode_last();
    }

    CdcFrame send(CdcMessageType type) { return send(type, nullptr, 0); }

    CdcFrame decode_last() {
        duo_input::protocol::decode_cdc_frame(
            duo_input::protocol::ByteView{replies.last().data(), replies.last().size()},
            duo_input::protocol::MutableByteView{payload_scratch, sizeof(payload_scratch)},
            result);
        return result.cdc;
    }

    std::uint8_t payload_scratch[ProtocolLimits::CDC_MAX_PAYLOAD] = {};
    duo_input::protocol::DecodeResult result{};

    /// Negotiate, the way the configurator does before anything else.
    void hello() {
        std::uint8_t request[4] = {0xFF, 0xFF, 0xFF, 0xFF};
        send(CdcMessageType::HELLO, request, sizeof(request));
    }
};

CdcError error_of(const CdcFrame& frame) {
    return static_cast<CdcError>(frame.payload.data[0]);
}

std::uint32_t u32_at(const CdcFrame& frame, std::size_t offset) {
    const std::uint8_t* p = frame.payload.data + offset;
    return static_cast<std::uint32_t>(p[0]) | (static_cast<std::uint32_t>(p[1]) << 8) |
           (static_cast<std::uint32_t>(p[2]) << 16) | (static_cast<std::uint32_t>(p[3]) << 24);
}

}  // namespace

// ------------------------------------------------------------------- hello

TEST_CASE(hello_is_answered_with_device_info) {
    Link link;
    std::uint8_t request[4] = {0xFF, 0xFF, 0xFF, 0xFF};

    const CdcFrame reply = link.send(CdcMessageType::HELLO, request, sizeof(request));

    CHECK(reply.type == CdcMessageType::DEVICE_INFO);
    CHECK_EQ(error_of(reply), CdcError::Ok);
    CHECK_EQ(reply.payload.size, 44u);
}

TEST_CASE(device_info_states_the_protocol_version) {
    Link link;
    link.hello();
    std::uint8_t request[4] = {0xFF, 0xFF, 0xFF, 0xFF};
    link.sequence = 1;

    const CdcFrame reply = link.send(CdcMessageType::HELLO, request, sizeof(request));

    CHECK_EQ(reply.payload.data[1], duo_input::protocol::PROTOCOL_VERSION_MAJOR);
    CHECK_EQ(reply.payload.data[2], duo_input::protocol::PROTOCOL_VERSION_MINOR);
}

TEST_CASE(the_device_only_agrees_to_capabilities_it_actually_has) {
    Link link;
    std::uint8_t request[4] = {0xFF, 0xFF, 0xFF, 0xFF};

    const CdcFrame reply = link.send(CdcMessageType::HELLO, request, sizeof(request));

    // The host asked for everything. The reply says what it will really do,
    // so a host that trusts the answer is not surprised later.
    const std::uint32_t granted = u32_at(reply, 3);
    CHECK(granted != 0xFFFFFFFFu);
    CHECK(granted != 0u);
}

TEST_CASE(hello_with_the_wrong_payload_size_is_refused) {
    Link link;
    std::uint8_t request[3] = {1, 2, 3};

    const CdcFrame reply = link.send(CdcMessageType::HELLO, request, sizeof(request));

    CHECK(reply.type == CdcMessageType::DEVICE_INFO);
    CHECK_EQ(error_of(reply), CdcError::InvalidRequest);
}

TEST_CASE(a_request_before_hello_is_refused) {
    Link link;

    const CdcFrame reply = link.send(CdcMessageType::GET_STATUS);

    // Nothing may happen before both ends agree what the other can do.
    CHECK_EQ(error_of(reply), CdcError::BadState);
}

// ------------------------------------------------------------------ status

TEST_CASE(status_reports_the_active_profile_and_no_staging) {
    Link link;
    link.hello();

    const CdcFrame reply = link.send(CdcMessageType::GET_STATUS);

    CHECK_EQ(error_of(reply), CdcError::Ok);
    CHECK_EQ(reply.payload.size, 8u);
    CHECK_EQ(reply.payload.data[2], 0u);  // capture not running
    CHECK_EQ(reply.payload.data[3], 0u);  // no write in progress
}

TEST_CASE(a_blank_device_reports_no_configuration) {
    Link link;
    link.hello();

    const CdcFrame reply = link.send(CdcMessageType::GET_ACTIVE_CONFIG_INFO);

    CHECK_EQ(error_of(reply), CdcError::Ok);
    CHECK_EQ(reply.payload.size, 41u);
    CHECK_EQ(u32_at(reply, 1), 0u);  // generation
    CHECK_EQ(u32_at(reply, 5), 0u);  // size
}

// -------------------------------------------------------------------- ping

TEST_CASE(ping_echoes_what_it_was_given) {
    Link link;
    link.hello();
    const std::uint8_t body[5] = {'h', 'e', 'l', 'l', 'o'};

    const CdcFrame reply = link.send(CdcMessageType::PING, body, sizeof(body));

    CHECK_EQ(error_of(reply), CdcError::Ok);
    CHECK_EQ(reply.payload.size, 6u);
    CHECK(std::memcmp(reply.payload.data + 1, body, sizeof(body)) == 0);
}

TEST_CASE(ping_works_before_any_negotiation) {
    Link link;

    // A host uses this to find out whether the link is alive at all, which is
    // exactly when negotiation may not have happened.
    const CdcFrame reply = link.send(CdcMessageType::PING, nullptr, 0);

    CHECK_EQ(error_of(reply), CdcError::Ok);
}

TEST_CASE(stop_and_release_all_works_before_any_negotiation) {
    Link link;

    const CdcFrame reply = link.send(CdcMessageType::STOP_AND_RELEASE_ALL);

    // This must never depend on the link being in a good mood; it is the way
    // out of a macro that is holding keys down.
    CHECK_EQ(error_of(reply), CdcError::Ok);
    CHECK(link.service.take_release_all_request());
    CHECK_FALSE(link.service.take_release_all_request());
}

// ------------------------------------------------------------- sequencing

TEST_CASE(a_sequence_that_skips_ahead_is_refused) {
    Link link;
    link.hello();
    link.sequence += 5;

    const CdcFrame reply = link.send(CdcMessageType::GET_STATUS);

    CHECK_EQ(error_of(reply), CdcError::BadSequence);
    CHECK_EQ(link.service.diagnostics().bad_sequence, 1u);
}

TEST_CASE(an_identical_repeat_gets_the_same_reply_without_acting_twice) {
    Link link;
    link.hello();

    // A lost reply looks exactly like this from the device's side. Acting on
    // the request again would erase a slot for the second time.
    std::uint8_t request[4] = {0xFF, 0xFF, 0xFF, 0xFF};
    link.sequence = 1;
    link.send(CdcMessageType::GET_STATUS);
    const std::vector<std::uint8_t> first = link.replies.last();

    link.sequence = 1;
    link.send(CdcMessageType::GET_STATUS);

    CHECK(link.replies.last() == first);
    CHECK_EQ(link.service.diagnostics().bad_sequence, 0u);
    (void)request;
}

TEST_CASE(a_damaged_frame_is_counted_and_never_answered) {
    Link link;
    link.hello();
    std::uint8_t wire[16] = {5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 0};

    link.replies.clear();
    link.service.on_cdc_bytes(wire, sizeof(wire));

    // Answering would mean guessing which sequence it claimed to be.
    CHECK_EQ(link.replies.count(), 0u);
    CHECK_EQ(link.service.diagnostics().bad_crc, 1u);
}

TEST_CASE(a_frame_split_across_two_reads_is_still_one_frame) {
    Link link;
    link.hello();

    CdcFrame request;
    request.type = CdcMessageType::GET_STATUS;
    request.sequence = 1;
    std::uint8_t wire[128];
    std::uint8_t scratch[128];
    std::size_t written = 0;
    duo_input::protocol::encode_cdc_frame(
        request, duo_input::protocol::MutableByteView{wire, sizeof(wire)},
        duo_input::protocol::MutableByteView{scratch, sizeof(scratch)}, written);

    link.replies.clear();
    // USB delivers what it delivers; a frame arriving in pieces is ordinary.
    link.service.on_cdc_bytes(wire, 3);
    CHECK_EQ(link.replies.count(), 0u);
    link.service.on_cdc_bytes(wire + 3, written - 3);

    CHECK_EQ(link.replies.count(), 1u);
}

// --------------------------------------------------------- write transaction

namespace {

/// A minimal valid package is hard to build by hand here, so the write tests
/// use arbitrary bytes and check the transaction rather than the contents.
std::vector<std::uint8_t> arbitrary_package(std::size_t size) {
    std::vector<std::uint8_t> data(size);
    for (std::size_t index = 0; index < size; ++index) {
        data[index] = static_cast<std::uint8_t>((index * 7) & 0xFF);
    }
    return data;
}

void begin_write(Link& link, const std::vector<std::uint8_t>& data) {
    std::uint8_t request[36];
    const std::uint32_t size = static_cast<std::uint32_t>(data.size());
    request[0] = static_cast<std::uint8_t>(size);
    request[1] = static_cast<std::uint8_t>(size >> 8);
    request[2] = static_cast<std::uint8_t>(size >> 16);
    request[3] = static_cast<std::uint8_t>(size >> 24);
    std::uint8_t digest[kSha256DigestSize];
    sha256(data.data(), data.size(), digest);
    std::memcpy(request + 4, digest, sizeof(digest));
    link.send(CdcMessageType::WRITE_BEGIN, request, sizeof(request));
}

CdcFrame send_chunk(Link& link, std::uint32_t offset, const std::uint8_t* data,
                    std::size_t size) {
    std::uint8_t request[4 + ProtocolLimits::CONFIG_CHUNK_MAX_BYTES];
    request[0] = static_cast<std::uint8_t>(offset);
    request[1] = static_cast<std::uint8_t>(offset >> 8);
    request[2] = static_cast<std::uint8_t>(offset >> 16);
    request[3] = static_cast<std::uint8_t>(offset >> 24);
    std::memcpy(request + 4, data, size);
    return link.send(CdcMessageType::WRITE_CHUNK, request, 4 + size);
}

}  // namespace

TEST_CASE(a_write_begins_and_reports_that_it_is_staging) {
    Link link;
    link.hello();
    begin_write(link, arbitrary_package(1024));

    const CdcFrame reply = link.send(CdcMessageType::GET_STATUS);

    CHECK_EQ(reply.payload.data[3], 1u);
}

TEST_CASE(a_chunk_is_acknowledged_with_the_next_offset_expected) {
    Link link;
    link.hello();
    const auto data = arbitrary_package(1024);
    begin_write(link, data);

    const CdcFrame reply = send_chunk(link, 0, data.data(), 512);

    CHECK_EQ(error_of(reply), CdcError::Ok);
    CHECK_EQ(u32_at(reply, 1), 512u);
}

TEST_CASE(a_chunk_out_of_order_is_refused_and_says_what_was_expected) {
    Link link;
    link.hello();
    const auto data = arbitrary_package(1024);
    begin_write(link, data);
    send_chunk(link, 0, data.data(), 512);

    const CdcFrame reply = send_chunk(link, 900, data.data() + 900, 100);

    // The reply carries the offset the device still wants, so the host can
    // resume rather than start over.
    CHECK_EQ(error_of(reply), CdcError::BadChunk);
    CHECK_EQ(u32_at(reply, 1), 512u);
}

TEST_CASE(a_second_write_while_one_is_running_is_refused) {
    Link link;
    link.hello();
    const auto data = arbitrary_package(512);
    begin_write(link, data);

    std::uint8_t request[36] = {};
    request[0] = 1;
    const CdcFrame reply = link.send(CdcMessageType::WRITE_BEGIN, request, sizeof(request));

    CHECK_EQ(error_of(reply), CdcError::Busy);
}

TEST_CASE(a_package_larger_than_the_protocol_allows_is_refused) {
    Link link;
    link.hello();

    std::uint8_t request[36] = {};
    const std::uint32_t size = ProtocolLimits::BINARY_CONFIG_MAX_BYTES + 1;
    request[0] = static_cast<std::uint8_t>(size);
    request[1] = static_cast<std::uint8_t>(size >> 8);
    request[2] = static_cast<std::uint8_t>(size >> 16);
    request[3] = static_cast<std::uint8_t>(size >> 24);

    const CdcFrame reply = link.send(CdcMessageType::WRITE_BEGIN, request, sizeof(request));

    CHECK_EQ(error_of(reply), CdcError::BadSize);
}

TEST_CASE(verifying_before_every_chunk_has_arrived_is_refused) {
    Link link;
    link.hello();
    const auto data = arbitrary_package(1024);
    begin_write(link, data);
    send_chunk(link, 0, data.data(), 512);

    const CdcFrame reply = link.send(CdcMessageType::WRITE_VERIFY);

    CHECK_EQ(error_of(reply), CdcError::BadSize);
}

TEST_CASE(a_package_whose_bytes_do_not_match_its_digest_is_refused_and_abandoned) {
    Link link;
    link.hello();
    auto data = arbitrary_package(512);
    begin_write(link, data);
    data[100] ^= 0xFF;
    send_chunk(link, 0, data.data(), data.size());

    const CdcFrame reply = link.send(CdcMessageType::WRITE_VERIFY);

    CHECK_EQ(error_of(reply), CdcError::BadHash);
    // The staging slot is dropped: a package that failed its own digest is
    // not something to leave lying around half-committed.
    CHECK_FALSE(link.store.staging());
    CHECK_EQ(link.service.diagnostics().aborted_staging, 1u);
}

TEST_CASE(committing_without_verifying_is_refused) {
    Link link;
    link.hello();
    const auto data = arbitrary_package(512);
    begin_write(link, data);
    send_chunk(link, 0, data.data(), data.size());

    const CdcFrame reply = link.send(CdcMessageType::WRITE_COMMIT);

    CHECK_EQ(error_of(reply), CdcError::BadState);
}

TEST_CASE(aborting_a_write_leaves_the_device_ready_again) {
    Link link;
    link.hello();
    begin_write(link, arbitrary_package(512));

    const CdcFrame reply = link.send(CdcMessageType::WRITE_ABORT);

    CHECK_EQ(error_of(reply), CdcError::Ok);
    CHECK_FALSE(link.store.staging());
    CHECK_EQ(link.send(CdcMessageType::GET_STATUS).payload.data[3], 0u);
}

TEST_CASE(aborting_when_nothing_is_running_is_refused) {
    Link link;
    link.hello();

    CHECK_EQ(error_of(link.send(CdcMessageType::WRITE_ABORT)), CdcError::BadState);
}

TEST_CASE(a_host_that_vanishes_mid_write_leaves_nothing_staged) {
    Link link;
    link.hello();
    begin_write(link, arbitrary_package(512));

    link.service.on_disconnect();

    CHECK_FALSE(link.store.staging());
    CHECK_EQ(link.service.diagnostics().aborted_staging, 1u);
    CHECK_EQ(link.service.diagnostics().disconnect, 1u);
}

// --------------------------------------------------------------- read back

TEST_CASE(reading_from_a_blank_device_is_refused) {
    Link link;
    link.hello();
    std::uint8_t request[6] = {0, 0, 0, 0, 0x00, 0x02};

    const CdcFrame reply = link.send(CdcMessageType::READ_CONFIG_CHUNK, request, sizeof(request));

    CHECK_EQ(error_of(reply), CdcError::BadState);
}

TEST_CASE(asking_for_more_than_a_chunk_may_carry_is_refused) {
    Link link;
    link.hello();
    std::uint8_t request[6] = {0, 0, 0, 0, 0x01, 0x04};  // 1025

    const CdcFrame reply = link.send(CdcMessageType::READ_CONFIG_CHUNK, request, sizeof(request));

    CHECK_EQ(error_of(reply), CdcError::BadSize);
}

// ------------------------------------------------------------- other paths

TEST_CASE(a_factory_reset_needs_someone_at_the_device) {
    Link link;
    link.hello();

    const CdcFrame reply = link.send(CdcMessageType::FACTORY_RESET_ARM);

    // Erasing the operator's work is not something a program alone may do.
    CHECK_EQ(error_of(reply), CdcError::PhysicalConfirmationRequired);
}

TEST_CASE(a_reply_type_sent_as_a_request_is_refused) {
    Link link;
    link.hello();

    const CdcFrame reply = link.send(CdcMessageType::DEVICE_INFO);

    CHECK_EQ(error_of(reply), CdcError::InvalidRequest);
}

TEST_CASE(diagnostics_carry_every_counter_the_host_expects) {
    Link link;
    link.hello();

    const CdcFrame reply = link.send(CdcMessageType::GET_DIAGNOSTICS);

    CHECK_EQ(error_of(reply), CdcError::Ok);
    CHECK_EQ(reply.payload.size, 21u);
}

TEST_CASE(running_a_macro_is_refused_because_the_device_cannot_yet) {
    Link link;
    link.hello();
    std::uint8_t request[2] = {3, 7};

    const CdcFrame reply = link.send(CdcMessageType::TEST_MACRO, request, sizeof(request));

    // There is no macro engine yet, so the device does not advertise
    // TEST_MACRO. Saying "not supported" is what lets the configurator grey
    // the button out instead of waiting for something that is not coming.
    CHECK_EQ(error_of(reply), CdcError::UnsupportedCapability);
}

TEST_CASE(capture_is_refused_because_there_is_nothing_to_capture_from) {
    Link link;
    link.hello();

    const CdcFrame reply = link.send(CdcMessageType::CAPTURE_BEGIN);

    // The CH375B does not exist yet, so no key can arrive to be captured.
    CHECK_EQ(error_of(reply), CdcError::UnsupportedCapability);
}

TEST_CASE(status_reports_that_no_capture_is_running) {
    Link link;
    link.hello();

    const CdcFrame reply = link.send(CdcMessageType::GET_STATUS);

    // Reported truthfully rather than omitted, so the host reads an answer.
    CHECK_EQ(reply.payload.data[2], 0u);
}


// ------------------------------------------------- starting a fresh session

TEST_CASE(a_hello_at_any_sequence_starts_a_new_session) {
    Link link;
    link.hello();
    link.send(CdcMessageType::GET_STATUS);
    link.send(CdcMessageType::GET_STATUS);

    // A configurator that closed the port and opened it again starts counting
    // from zero, because it has no memory of the previous session either.
    // Refusing that leaves the device permanently unreachable to the program
    // that is meant to configure it - which is what a real board did.
    link.sequence = 0;
    std::uint8_t request[4] = {0xFF, 0xFF, 0xFF, 0xFF};
    const CdcFrame reply = link.send(CdcMessageType::HELLO, request, sizeof(request));

    CHECK(reply.type == CdcMessageType::DEVICE_INFO);
    CHECK_EQ(error_of(reply), CdcError::Ok);
}

TEST_CASE(the_sequence_after_a_new_hello_continues_from_that_hello) {
    Link link;
    link.hello();
    link.send(CdcMessageType::GET_STATUS);

    link.sequence = 0;
    std::uint8_t request[4] = {0xFF, 0xFF, 0xFF, 0xFF};
    link.send(CdcMessageType::HELLO, request, sizeof(request));
    const CdcFrame reply = link.send(CdcMessageType::GET_STATUS);

    CHECK_EQ(error_of(reply), CdcError::Ok);
}

TEST_CASE(a_new_session_forgets_what_the_previous_one_negotiated) {
    Link link;
    link.hello();

    link.sequence = 0;
    std::uint8_t none[4] = {0, 0, 0, 0};
    link.send(CdcMessageType::HELLO, none, sizeof(none));

    // The second session asked for nothing, so it may do nothing - even
    // though the first session was granted everything.
    CHECK_EQ(error_of(link.send(CdcMessageType::GET_DIAGNOSTICS)),
             CdcError::UnsupportedCapability);
}

TEST_CASE(a_non_hello_request_at_a_wrong_sequence_is_still_refused) {
    Link link;
    link.hello();

    link.sequence = 500;
    const CdcFrame reply = link.send(CdcMessageType::GET_STATUS);

    // Only a handshake may restart the count. Anything else jumping is still
    // a host that lost track, and acting on it could erase a slot.
    CHECK_EQ(error_of(reply), CdcError::BadSequence);
}


TEST_CASE(a_new_session_abandons_a_write_the_previous_one_left_running) {
    Link link;
    link.hello();
    begin_write(link, arbitrary_package(1024));
    CHECK(link.store.staging());

    // Closing a serial port does not unmount USB, so a configurator that quit
    // mid-write leaves the transaction open. Without this the device answers
    // Busy to every later WRITE_BEGIN, forever, and the only way out is
    // unplugging it - which is exactly what a real board did.
    link.sequence = 0;
    std::uint8_t request[4] = {0xFF, 0xFF, 0xFF, 0xFF};
    link.send(CdcMessageType::HELLO, request, sizeof(request));

    CHECK_FALSE(link.store.staging());
    CHECK_EQ(link.service.diagnostics().aborted_staging, 1u);
}

TEST_CASE(a_write_can_start_immediately_after_a_new_session) {
    Link link;
    link.hello();
    begin_write(link, arbitrary_package(1024));

    link.sequence = 0;
    std::uint8_t request[4] = {0xFF, 0xFF, 0xFF, 0xFF};
    link.send(CdcMessageType::HELLO, request, sizeof(request));

    const auto data = arbitrary_package(512);
    begin_write(link, data);
    CHECK(link.store.staging());
    CHECK_EQ(error_of(send_chunk(link, 0, data.data(), data.size())), CdcError::Ok);
}


// ------------------------------------------------------------ factory reset

TEST_CASE(a_factory_reset_is_refused_until_someone_confirms_at_the_device) {
    Link link;
    link.hello();

    CHECK_EQ(error_of(link.send(CdcMessageType::FACTORY_RESET_ARM)),
             CdcError::PhysicalConfirmationRequired);
    CHECK_EQ(error_of(link.send(CdcMessageType::FACTORY_RESET_COMMIT)),
             CdcError::PhysicalConfirmationRequired);
}

TEST_CASE(a_confirmed_factory_reset_erases_the_configuration) {
    Link link;
    link.hello();
    // Seeded through the store rather than the protocol: a package only has to
    // be a valid configuration to be committed over CDC, and building one by
    // hand here would test the config format rather than the reset.
    const auto data = arbitrary_package(512);
    std::uint8_t digest[kSha256DigestSize];
    sha256(data.data(), data.size(), digest);
    link.store.begin(static_cast<std::uint32_t>(data.size()), digest);
    link.store.write_chunk(0, data.data(), data.size());
    link.store.verify();
    link.store.commit();
    CHECK(link.store.scan().has_active);

    // Someone held the button for five seconds.
    link.service.confirm_factory_reset();

    CHECK_EQ(error_of(link.send(CdcMessageType::FACTORY_RESET_ARM)), CdcError::Ok);
    CHECK_EQ(error_of(link.send(CdcMessageType::FACTORY_RESET_COMMIT)), CdcError::Ok);
    CHECK_FALSE(link.store.scan().has_active);
}

TEST_CASE(a_confirmation_is_spent_by_the_reset_it_authorises) {
    Link link;
    link.hello();
    link.service.confirm_factory_reset();
    link.send(CdcMessageType::FACTORY_RESET_ARM);
    link.send(CdcMessageType::FACTORY_RESET_COMMIT);

    // A second reset needs someone to walk over and confirm it again. A
    // standing confirmation would let a program erase the configuration
    // repeatedly on the strength of one button press.
    CHECK_EQ(error_of(link.send(CdcMessageType::FACTORY_RESET_COMMIT)),
             CdcError::PhysicalConfirmationRequired);
}

TEST_CASE(committing_a_reset_that_was_never_armed_is_refused) {
    Link link;
    link.hello();
    link.service.confirm_factory_reset();

    CHECK_EQ(error_of(link.send(CdcMessageType::FACTORY_RESET_COMMIT)), CdcError::BadState);
}

TEST_CASE(a_new_session_forgets_a_confirmation_nobody_used) {
    Link link;
    link.hello();
    link.service.confirm_factory_reset();

    link.sequence = 0;
    std::uint8_t request[4] = {0xFF, 0xFF, 0xFF, 0xFF};
    link.send(CdcMessageType::HELLO, request, sizeof(request));

    // The person who pressed the button and the program now connected are not
    // necessarily the same person.
    CHECK_EQ(error_of(link.send(CdcMessageType::FACTORY_RESET_ARM)),
             CdcError::PhysicalConfirmationRequired);
}
