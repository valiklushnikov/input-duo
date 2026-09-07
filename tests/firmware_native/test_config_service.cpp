// The CDC conversation, driven the way the configurator drives it.
//
// The Python emulator in configurator/src/duo_input/device/emulator.py is the
// reference for every payload shape here. The firmware has to agree with it
// byte for byte, because the configurator was written against it and cannot
// tell the two apart.

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "config_service.hpp"
#include "crypto/sha256.hpp"
#include "pio_usb/host_observation_mapping.hpp"
#include "storage/ab_store.hpp"
#include "test_support.hpp"

using duo_input::config::TriggerKind;
using duo_input::crypto::kSha256DigestSize;
using duo_input::crypto::sha256;
using duo_input::protocol::CdcFrame;
using duo_input::protocol::CdcMessageType;
using duo_input::protocol::ProtocolLimits;
using duo_input::storage::AbStore;
using duo_input::storage::FlashBackend;
using duo_input::u1::CdcError;
using duo_input::u1::CdcSink;
using duo_input::u1::CaptureRequest;
using duo_input::u1::ConfigService;
using duo_input::u1::IRuntimeConfig;
using duo_input::u1::input::InputEvent;
using duo_input::u1::input::InputEventKind;
using duo_input::u1::mapping::CaptureController;
using duo_input::u1::mapping::CapturedTrigger;

namespace {

// Frozen by hand from the pre-backend GET_DIAGNOSTICS wire contract: the
// original 43-byte status head, nine bucket marker and eight literal LE
// bucket edges, two empty 44-byte histograms, and two empty 42-byte ports.
// No serializer or production constant computes this expected byte string.
static constexpr std::uint8_t kFrozenLegacyDiagnosticsPrefix[] = {
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x09, 0xFA, 0x00, 0x00, 0x00,
    0xF4, 0x01, 0x00, 0x00, 0xE8, 0x03, 0x00, 0x00, 0xD0, 0x07, 0x00, 0x00, 0x88, 0x13, 0x00, 0x00,
    0x10, 0x27, 0x00, 0x00, 0x20, 0x4E, 0x00, 0x00, 0x50, 0xC3, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
};

static_assert(sizeof(kFrozenLegacyDiagnosticsPrefix) == 248u,
              "the frozen prefix is the complete pre-backend payload");

std::uint32_t read_u32(const std::uint8_t* at) {
    return static_cast<std::uint32_t>(at[0]) | (static_cast<std::uint32_t>(at[1]) << 8) |
           (static_cast<std::uint32_t>(at[2]) << 16) | (static_cast<std::uint32_t>(at[3]) << 24);
}

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

class RuntimeConfigRecorder final : public IRuntimeConfig {
public:
    bool activate(duo_input::protocol::ByteView package) override {
        ++activations;
        active = package;
        return package.data != nullptr && package.size != 0;
    }

    bool clear() override {
        ++clears;
        active = {nullptr, 0};
        return true;
    }

    int activations = 0;
    int clears = 0;
    duo_input::protocol::ByteView active{nullptr, 0};
};

/// One conversation: a device, its flash and the wire between them.
struct Link {
    MemoryFlash flash;
    AbStore store{flash};
    Recorder replies;
    RuntimeConfigRecorder runtime;
    ConfigService service{store, replies, runtime};
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

// ------------------------------------------------------- the trace's own gate
//
// The reference target's plain-text trace and the configurator's own
// COBS-framed replies share one physical CDC endpoint (see
// firmware/u1_reference/main.cpp). conversation_active() is the guard that
// keeps trace text out of the wire once a real client is on it - hardware
// proved that omitting it corrupts the configurator's framing, not any test
// here. These drive on_cdc_bytes() exactly the way main.cpp's own
// tud_cdc_read()/on_cdc_bytes() call does; what main.cpp does with the
// answer - gate reference_service_one_cdc() on it - is covered separately
// in tests/build/test_pio_usb_reference_contract.py, which is the only place
// that can read main.cpp's own wiring.

TEST_CASE(the_trace_gate_starts_open_before_any_frame_arrives) {
    Link link;

    CHECK_FALSE(link.service.conversation_active());
}

TEST_CASE(a_valid_frame_closes_the_trace_gate) {
    Link link;
    CHECK_FALSE(link.service.conversation_active());

    link.hello();

    CHECK(link.service.conversation_active());
}

TEST_CASE(a_damaged_frame_leaves_the_trace_gate_open) {
    Link link;
    std::uint8_t wire[16] = {5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 0};

    link.service.on_cdc_bytes(wire, sizeof(wire));

    // Garbage on the wire is not evidence of a real client, only a frame
    // that survives decode_cdc_frame's own CRC check is - the same frame
    // this test's twin above (a_damaged_frame_is_counted_and_never_answered)
    // shows is never answered either.
    CHECK_FALSE(link.service.conversation_active());
}

TEST_CASE(disconnecting_reopens_the_trace_gate) {
    Link link;
    link.hello();
    CHECK(link.service.conversation_active());

    link.service.on_disconnect();

    // Only a genuine disconnect reopens it - never merely a quiet pass with
    // no bytes to read, which happens between every pair of ordinary
    // requests and must not turn the trace back on mid-session.
    CHECK_FALSE(link.service.conversation_active());
}

TEST_CASE(an_idle_pass_with_no_bytes_does_not_reopen_the_trace_gate) {
    Link link;
    link.hello();
    CHECK(link.service.conversation_active());

    // What main.cpp's loop does every pass nothing arrived on: hand
    // on_cdc_bytes zero bytes. This must be indistinguishable from not
    // calling it at all.
    link.service.on_cdc_bytes(nullptr, 0);

    CHECK(link.service.conversation_active());
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

std::vector<std::uint8_t> valid_package() {
    const std::string path =
        std::string(DUO_TEST_VECTOR_DIR) + "/config_vectors/valid_full.bin";
    std::FILE* file = std::fopen(path.c_str(), "rb");
    if (file == nullptr) {
        return {};
    }
    std::vector<std::uint8_t> bytes;
    std::uint8_t chunk[4096];
    std::size_t read = 0;
    while ((read = std::fread(chunk, 1, sizeof(chunk), file)) > 0) {
        bytes.insert(bytes.end(), chunk, chunk + read);
    }
    std::fclose(file);
    return bytes;
}

CdcFrame write_valid_package(Link& link, const std::vector<std::uint8_t>& data) {
    begin_write(link, data);
    for (std::size_t offset = 0; offset < data.size();) {
        const std::size_t remaining = data.size() - offset;
        const std::size_t size = remaining < ProtocolLimits::CONFIG_CHUNK_MAX_BYTES
                                     ? remaining
                                     : ProtocolLimits::CONFIG_CHUNK_MAX_BYTES;
        send_chunk(link, static_cast<std::uint32_t>(offset), data.data() + offset, size);
        offset += size;
    }
    link.send(CdcMessageType::WRITE_VERIFY);
    return link.send(CdcMessageType::WRITE_COMMIT);
}

}  // namespace

TEST_CASE(a_committed_configuration_is_activated_before_commit_is_acknowledged) {
    Link link;
    link.hello();
    const std::vector<std::uint8_t> data = valid_package();
    CHECK(!data.empty());

    const CdcFrame reply = write_valid_package(link, data);

    CHECK_EQ(error_of(reply), CdcError::Ok);
    CHECK_EQ(link.runtime.activations, 1);
    CHECK(link.runtime.active.data != nullptr);
    CHECK_EQ(link.service.active_profile(), 8u);
}

TEST_CASE(a_factory_reset_detaches_the_runtime_before_erasing_its_flash) {
    Link link;
    link.hello();
    const std::vector<std::uint8_t> data = valid_package();
    CHECK_EQ(error_of(write_valid_package(link, data)), CdcError::Ok);
    CHECK(link.runtime.active.data != nullptr);

    link.service.confirm_factory_reset();
    CHECK_EQ(error_of(link.send(CdcMessageType::FACTORY_RESET_ARM)), CdcError::Ok);
    const CdcFrame reply = link.send(CdcMessageType::FACTORY_RESET_COMMIT);

    CHECK_EQ(error_of(reply), CdcError::Ok);
    CHECK_EQ(link.runtime.clears, 1);
    CHECK(link.runtime.active.data == nullptr);
}

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
    // Every fixed block, plus the appended backend block's own two-byte head,
    // plus the host block's one-byte length, plus the reference-counters
    // block's own one-byte length - all three appended blocks say "nothing
    // published" the same way, with a single marker byte. The reply is no
    // longer one fixed length: a backend that publishes no counters, an
    // image that publishes no host observation, and one that never calls
    // set_reference_counters all send a shorter one, and
    // kDiagnosticsPayloadSize is the ceiling rather than the length. Both
    // ends of that are checked here.
    CHECK_EQ(reply.payload.size, duo_input::u1::kBackendBlockOffset + 2 + 1 + 1);
    CHECK(reply.payload.size <= duo_input::u1::kDiagnosticsPayloadSize);
    // Appended, never rearranged: a host reading only the first 43 bytes still
    // reads exactly what it always read.
    CHECK(duo_input::u1::kBackendBlockOffset > 43u);
}

// The device's own latency, and the only latency it is in a position to know.
//
// A histogram rather than a number, because "p95 <= 20 ms" is a question about
// how many samples were at or below 20 ms and nothing else answers it exactly.
// The edges travel with the counts so a host can never be wrong about what a
// bucket means - the alternative is two copies of eight numbers that agree
// until somebody edits one of them.
TEST_CASE(diagnostics_carry_the_latency_the_device_measured_of_itself) {
    Link link;
    link.hello();
    duo_input::diagnostics::LatencyHistogram keyboard;
    duo_input::diagnostics::LatencyHistogram mouse;
    keyboard.record(300);
    keyboard.record(30000);
    mouse.record(80);
    link.service.set_input_latency(keyboard, mouse);

    const CdcFrame reply = link.send(CdcMessageType::GET_DIAGNOSTICS);
    const std::uint8_t* p = reply.payload.data;

    const std::size_t buckets = duo_input::diagnostics::kLatencyBucketCount;
    CHECK_EQ(p[43], static_cast<std::uint8_t>(buckets));
    for (std::size_t index = 0; index + 1 < buckets; ++index) {
        CHECK_EQ(read_u32(p + 44 + 4 * index),
                 duo_input::diagnostics::kLatencyBucketEdgesUs[index]);
    }

    const std::size_t keyboard_at = 44 + 4 * (buckets - 1);
    CHECK_EQ(read_u32(p + keyboard_at), 2u);
    CHECK_EQ(read_u32(p + keyboard_at + 4), 30000u);
    CHECK_EQ(read_u32(p + keyboard_at + 8 + 4 * 1), 1u);

    const std::size_t mouse_at = keyboard_at + 8 + 4 * buckets;
    CHECK_EQ(read_u32(p + mouse_at), 1u);
    CHECK_EQ(read_u32(p + mouse_at + 4), 80u);
    CHECK_EQ(read_u32(p + mouse_at + 8), 1u);
    CHECK_EQ(mouse_at + 8 + 4 * buckets, duo_input::u1::kPeripheralBlockOffset);
}

// A device that has been running and seen nothing must not look like a device
// that is fast. Nothing measured is nothing measured, and a host reading zero
// samples has to say so rather than report a p95 of zero.
TEST_CASE(a_device_that_has_measured_nothing_reports_no_samples) {
    Link link;
    link.hello();

    const CdcFrame reply = link.send(CdcMessageType::GET_DIAGNOSTICS);
    const std::size_t buckets = duo_input::diagnostics::kLatencyBucketCount;
    const std::size_t keyboard_at = 44 + 4 * (buckets - 1);

    CHECK_EQ(read_u32(reply.payload.data + keyboard_at), 0u);
}

// Which peripherals are on U1's own USB ports, and what they are.
//
// A device plugged into U1 is invisible to the computer at the other end of
// this link, so without this a compatibility matrix has no way to name the
// device a row is about - and "keyboard 3" is not something anyone can act on
// six months later. Two ports, always both reported: a port with nothing on it
// is a fact about the run, not an absence to be inferred.
TEST_CASE(diagnostics_name_the_peripherals_on_the_two_ports) {
    Link link;
    link.hello();
    duo_input::u1::PeripheralPort keyboard;
    keyboard.attached = true;
    keyboard.ready = true;
    keyboard.kind = 1;
    keyboard.vendor_id = 0x046D;
    keyboard.product_id = 0xC31C;
    keyboard.report_descriptor_bytes = 0;
    duo_input::u1::PeripheralPort mouse;
    mouse.attached = true;
    mouse.ready = false;
    mouse.kind = 2;
    mouse.vendor_id = 0x1234;
    mouse.product_id = 0x5678;
    mouse.buttons = 5;
    mouse.report_descriptor_bytes = 67;
    for (std::size_t index = 0; index < 32; ++index) {
        mouse.descriptor_hash[index] = static_cast<std::uint8_t>(index + 1);
    }
    link.service.set_peripherals(keyboard, mouse);

    const CdcFrame reply = link.send(CdcMessageType::GET_DIAGNOSTICS);
    const std::uint8_t* p = reply.payload.data;
    const std::size_t at = duo_input::u1::kPeripheralBlockOffset;

    CHECK_EQ(p[at], 1u);
    CHECK_EQ(p[at + 1], 1u);
    CHECK_EQ(p[at + 2], 1u);
    CHECK_EQ(static_cast<std::uint16_t>(p[at + 3] | (p[at + 4] << 8)), 0x046Du);
    CHECK_EQ(static_cast<std::uint16_t>(p[at + 5] | (p[at + 6] << 8)), 0xC31Cu);

    const std::size_t second = at + duo_input::u1::kPeripheralPortBytes;
    CHECK_EQ(p[second + 1], 0u);
    CHECK_EQ(p[second + 2], 2u);
    CHECK_EQ(static_cast<std::uint16_t>(p[second + 3] | (p[second + 4] << 8)), 0x1234u);
    CHECK_EQ(p[second + 7], 5u);
    CHECK_EQ(static_cast<std::uint16_t>(p[second + 8] | (p[second + 9] << 8)), 67u);
    CHECK_EQ(p[second + 10], 1u);
    CHECK_EQ(p[second + 41], 32u);
    // The peripheral block still ends exactly where it always did. What
    // follows it is the appended backend block, which no older configurator
    // reads and which cannot move anything in front of it.
    CHECK_EQ(second + duo_input::u1::kPeripheralPortBytes,
             duo_input::u1::kBackendBlockOffset);
}

TEST_CASE(an_empty_port_is_reported_as_empty_rather_than_left_out) {
    Link link;
    link.hello();

    const CdcFrame reply = link.send(CdcMessageType::GET_DIAGNOSTICS);
    const std::size_t at = duo_input::u1::kPeripheralBlockOffset;

    CHECK_EQ(reply.payload.data[at], 0u);
    CHECK_EQ(reply.payload.data[at + duo_input::u1::kPeripheralPortBytes], 0u);
}

// The reply is one frame and the frame has a ceiling. This block grew the
// payload by nearly three times; the next thing appended must not be the one
// that runs off the end without anybody noticing.
TEST_CASE(the_diagnostics_reply_still_fits_in_one_frame) {
    CHECK(duo_input::u1::kDiagnosticsPayloadSize <= ProtocolLimits::CDC_MAX_PAYLOAD);
}

// ------------------------------------------------------- which backend spoke

// U1's two input channels can be read by either backend now - the CH375 pair
// or the single PIO USB host - and a diagnostic that does not say which one
// produced it cannot be read six months later. The identifier goes AFTER the
// two peripheral records, never among them.
TEST_CASE(the_diagnostics_name_which_backend_read_the_peripherals) {
    Link link;
    link.hello();

    link.service.set_backend(duo_input::protocol::InputBackend::CH375);

    const CdcFrame reply = link.send(CdcMessageType::GET_DIAGNOSTICS);
    const std::uint8_t* p = reply.payload.data;
    const std::size_t at = duo_input::u1::kBackendBlockOffset;

    CHECK_EQ(p[at], static_cast<std::uint8_t>(duo_input::protocol::InputBackend::CH375));
    // The CH375 pair keeps none of the host-stack counters below, and a
    // backend that keeps none says so with a count of zero rather than
    // sending twelve zeros a reader would take for measurements.
    CHECK_EQ(p[at + 1], 0u);
    // Two for the backend head, one for the host block's length byte, one
    // for the reference-counters block's own length byte - nothing has
    // published a host observation or reference counters here either.
    CHECK_EQ(reply.payload.size, at + 2 + 1 + 1);
}

// The counters the PIO USB host keeps, in the one fixed order the wire has.
// Every one of them is a reason input did not arrive, and none of them has any
// other outward sign.
TEST_CASE(the_pio_usb_backend_publishes_its_own_counters) {
    Link link;
    link.hello();

    duo_input::u1::BackendCounters counters;
    counters.ignored_interfaces = 3;
    counters.ignored_role_already_claimed = 2;
    counters.event_overflows = 5;
    counters.detach_overflows = 7;
    counters.stale_events_discarded = 11;
    counters.arm_failures = 13;
    counters.arm_escalations = 17;
    counters.stall_signals = 19;
    counters.duplicate_mounts = 23;
    counters.device_overflows = 29;
    counters.interface_overflows = 31;
    counters.callback_overflows = 37;
    link.service.set_backend(duo_input::protocol::InputBackend::PIO_USB, counters);

    const CdcFrame reply = link.send(CdcMessageType::GET_DIAGNOSTICS);
    const std::uint8_t* p = reply.payload.data;
    const std::size_t at = duo_input::u1::kBackendBlockOffset;

    CHECK_EQ(p[at], static_cast<std::uint8_t>(duo_input::protocol::InputBackend::PIO_USB));
    CHECK_EQ(p[at + 1], static_cast<std::uint8_t>(duo_input::u1::kBackendCounterCount));
    const std::uint32_t expected[duo_input::u1::kBackendCounterCount] = {
        3, 2, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37,
    };
    for (std::size_t index = 0; index < duo_input::u1::kBackendCounterCount; ++index) {
        CHECK_EQ(read_u32(p + at + 2 + 4 * index), expected[index]);
    }
    // Every counter, an empty host block behind them, and an empty
    // reference-counters block behind that - this test publishes neither, so
    // the ceiling is two field-blocks short of reached.
    CHECK_EQ(reply.payload.size,
             duo_input::u1::kBackendBlockOffset + duo_input::u1::kBackendBlockBytes + 1 + 1);
}

// V1 accepts exactly one logical keyboard and one logical mouse, and ignores
// every further supported interface. Ignoring it silently is what makes a
// second keyboard look like a broken one, so the reason travels in the reply:
// how many were ignored at all, and how many of those only because the role
// they wanted was already taken.
TEST_CASE(an_ignored_extra_interface_says_why_it_was_ignored) {
    Link link;
    link.hello();

    duo_input::u1::BackendCounters counters;
    counters.ignored_interfaces = 2;
    counters.ignored_role_already_claimed = 1;
    link.service.set_backend(duo_input::protocol::InputBackend::PIO_USB, counters);

    const std::uint8_t* p = link.send(CdcMessageType::GET_DIAGNOSTICS).payload.data;
    const std::size_t at = duo_input::u1::kBackendBlockOffset;

    CHECK_EQ(read_u32(p + at + 2), 2u);
    // One of the two wanted a role another interface already held; the other
    // was nothing this firmware could classify. A single total cannot tell
    // those apart, and they are different faults on the bench.
    CHECK_EQ(read_u32(p + at + 6), 1u);
}

// The whole point of appending: an older configurator reads the prefix it has
// always read and never sees the suffix at all. So the prefix must be byte for
// byte what it was before a backend was ever published.
TEST_CASE(the_backend_block_leaves_the_prefix_byte_for_byte_unchanged) {
    Link after;
    after.hello();
    duo_input::u1::BackendCounters counters;
    counters.ignored_interfaces = 9;
    after.service.set_backend(duo_input::protocol::InputBackend::PIO_USB, counters);
    const CdcFrame published = after.send(CdcMessageType::GET_DIAGNOSTICS);

    CHECK_EQ(published.payload.size,
             duo_input::u1::kBackendBlockOffset + duo_input::u1::kBackendBlockBytes + 1 + 1);
    for (std::size_t index = 0; index < sizeof(kFrozenLegacyDiagnosticsPrefix); ++index) {
        CHECK_EQ(published.payload.data[index], kFrozenLegacyDiagnosticsPrefix[index]);
    }
}

// A firmware that has not been told which backend is running must not guess
// one. Nothing above reads this as CH375 by default, because a wrong answer
// here is worse than no answer.
TEST_CASE(a_backend_nobody_published_reports_itself_as_unknown) {
    Link link;
    link.hello();

    const CdcFrame reply = link.send(CdcMessageType::GET_DIAGNOSTICS);
    const std::size_t at = duo_input::u1::kBackendBlockOffset;

    CHECK_EQ(reply.payload.data[at],
             static_cast<std::uint8_t>(duo_input::protocol::InputBackend::UNKNOWN));
    CHECK_EQ(reply.payload.data[at + 1], 0u);
}

// --------------------------------------------- what the host stack is doing

// The reading that was missing when nothing enumerated.
//
// Every counter above is a reason a device that DID enumerate was not read.
// None of them says anything when nothing enumerates at all, and none of them
// distinguishes a host that never started from a host that started and saw an
// empty bus, or from a Core 1 that stopped before it could count anything.
// These seven fields are the only ones in the whole reply read from below the
// registry, and they are what make those three cases three different readings.
TEST_CASE(the_diagnostics_carry_what_the_host_stack_and_root_port_are_doing) {
    Link link;
    link.hello();

    duo_input::u1::BackendCounters counters;
    link.service.set_backend(duo_input::protocol::InputBackend::PIO_USB, counters);
    duo_input::u1::HostObservation observation;
    observation.init_flags = 0x0E;  // configured, initialized, inited; not already active
    observation.clk_hz_at_begin = 120000000u;
    observation.clk_hz_now = 120000000u;
    observation.sof_frame_count = 41234u;
    observation.root_port_state = 0x0B;  // initialized, connected, full speed
    observation.root_port_connects = 2u;
    observation.core1_passes = 987654u;
    observation.mount_events = 3u;
    observation.umount_events = 1u;
    observation.hid_mount_events = 2u;
    observation.ep_slots_opened = 4u;
    observation.ep_max_failed_count = 3u;
    observation.max_pass_gap_us = 450000u;
    observation.max_sof_gap = 7u;
    observation.root_port_resets = 2u;
    observation.hub_mount_events = 1u;
    observation.ep_slot_map = 0x0010B9B0u;
    observation.host_event_counts = 0x00290102u;
    observation.enum_progress_mask = 0x00001010u;
    observation.long_pass_count = 2u;
    observation.long_pass_total_ms = 950u;
    observation.core1_min_sp = 0x20040A40u;
    observation.ep_transfer_flags = 0x00002F01u;
    observation.xfer_completions_at_attach = 37u;
    observation.enum_stall_recoveries = 4u;
    link.service.set_host_observation(observation);

    const CdcFrame reply = link.send(CdcMessageType::GET_DIAGNOSTICS);
    const std::uint8_t* p = reply.payload.data;
    const std::size_t at =
        duo_input::u1::kBackendBlockOffset + duo_input::u1::kBackendBlockBytes;

    CHECK_EQ(p[at], static_cast<std::uint8_t>(duo_input::u1::kHostObservationBytes));
    CHECK_EQ(p[at + 1], 0x0Eu);
    CHECK_EQ(read_u32(p + at + 2), 120000000u);
    CHECK_EQ(read_u32(p + at + 6), 120000000u);
    CHECK_EQ(read_u32(p + at + 10), 41234u);
    CHECK_EQ(p[at + 14], 0x0Bu);
    CHECK_EQ(static_cast<std::uint16_t>(p[at + 15] | (p[at + 16] << 8)), 2u);
    CHECK_EQ(read_u32(p + at + 17), 987654u);
    CHECK_EQ(static_cast<std::uint16_t>(p[at + 21] | (p[at + 22] << 8)), 3u);
    CHECK_EQ(static_cast<std::uint16_t>(p[at + 23] | (p[at + 24] << 8)), 1u);
    CHECK_EQ(static_cast<std::uint16_t>(p[at + 25] | (p[at + 26] << 8)), 2u);
    CHECK_EQ(p[at + 27], 4u);
    CHECK_EQ(p[at + 28], 3u);
    CHECK_EQ(read_u32(p + at + 29), 450000u);
    CHECK_EQ(static_cast<std::uint16_t>(p[at + 33] | (p[at + 34] << 8)), 7u);
    CHECK_EQ(static_cast<std::uint16_t>(p[at + 35] | (p[at + 36] << 8)), 2u);
    CHECK_EQ(static_cast<std::uint16_t>(p[at + 37] | (p[at + 38] << 8)), 1u);
    // The round-4 window fields, appended behind hub_mount_events and nowhere
    // else: which pool slot holds whose endpoint, how many events the host
    // stack ever queued, how far each address got, the measured blocking
    // budget, and how deep Core 1's stack went while it was all running.
    CHECK_EQ(read_u32(p + at + 39), 0x0010B9B0u);
    CHECK_EQ(read_u32(p + at + 43), 0x00290102u);
    CHECK_EQ(read_u32(p + at + 47), 0x00001010u);
    CHECK_EQ(read_u32(p + at + 51), 2u);
    CHECK_EQ(read_u32(p + at + 55), 950u);
    CHECK_EQ(read_u32(p + at + 59), 0x20040A40u);
    // The round-5 pair, appended behind core1_min_sp and nowhere else:
    // whether a transfer is outstanding on each of the first four pool slots,
    // and the completion total at the moment the last attach was queued.
    CHECK_EQ(read_u32(p + at + 63), 0x00002F01u);
    CHECK_EQ(read_u32(p + at + 67), 37u);
    // The round-6 recovery counter, appended behind the round-5 pair.
    CHECK_EQ(read_u32(p + at + 71), 4u);
    // This test never calls set_reference_counters, so the ceiling is one
    // field-block short of reached - the reference-counters block sends its
    // own single not-published marker byte instead of the full ten.
    CHECK_EQ(reply.payload.size,
             duo_input::u1::kDiagnosticsPayloadSize -
                 duo_input::u1::kReferenceCounterFieldBytes);
}

// The two clocks are the whole point of carrying both. A host brought up at
// 125 MHz whose bus now runs at 120 MHz has PIO dividers computed for a clock
// that no longer exists - a 4% bit-rate error against full speed's 0.25%
// tolerance - and this is the reading that says so without a scope.
TEST_CASE(the_two_clock_readings_are_reported_independently) {
    Link link;
    link.hello();

    duo_input::u1::HostObservation observation;
    observation.clk_hz_at_begin = 125000000u;
    observation.clk_hz_now = 120000000u;
    link.service.set_host_observation(observation);

    const std::uint8_t* p = link.send(CdcMessageType::GET_DIAGNOSTICS).payload.data;
    const std::size_t at = duo_input::u1::kBackendBlockOffset + 2;

    CHECK_EQ(read_u32(p + at + 2), 125000000u);
    CHECK_EQ(read_u32(p + at + 6), 120000000u);
}

// An image with no host stack publishes a length of zero rather than seven
// zeroed readings of hardware it does not have - the same rule the backend
// block's counter count already follows, for the same reason. Zero readings
// and no readings are different facts, and a report that prints "SOF frames:
// 0" for a board with no root port has invented a measurement.
TEST_CASE(an_image_with_no_host_stack_publishes_an_empty_host_block) {
    Link link;
    link.hello();

    link.service.set_backend(duo_input::protocol::InputBackend::CH375);
    link.service.set_host_observation();

    const CdcFrame reply = link.send(CdcMessageType::GET_DIAGNOSTICS);
    const std::size_t at = duo_input::u1::kBackendBlockOffset + 2;

    CHECK_EQ(reply.payload.data[at], 0u);
    CHECK_EQ(reply.payload.size, at + 1 + 1);
}

// A build that reads pio_usb's own root port and frame counter directly, and
// has none of the endpoint-pool or enumeration-progress instrumentation the
// shipping PIO USB backend keeps, declares the base shape rather than the
// full one - so the sixteen fields it never measured are left off the wire
// entirely, not sent as invented zeros behind the seven it actually knows.
TEST_CASE(a_base_only_host_observation_declares_the_base_shape_and_stops_there) {
    Link link;
    link.hello();

    duo_input::u1::HostObservation observation;
    observation.init_flags = 0x0E;
    observation.clk_hz_at_begin = 120000000u;
    observation.clk_hz_now = 120000000u;
    observation.sof_frame_count = 41234u;
    observation.root_port_state = 0x0B;
    observation.root_port_connects = 2u;
    observation.core1_passes = 987654u;
    // Never read on the wire below: a base-only publication must not leak
    // this into a field the base shape does not carry.
    observation.mount_events = 0xFFFFu;
    link.service.set_host_observation_base(observation);

    const CdcFrame reply = link.send(CdcMessageType::GET_DIAGNOSTICS);
    const std::uint8_t* p = reply.payload.data;
    const std::size_t at = duo_input::u1::kBackendBlockOffset + 2;

    CHECK_EQ(p[at], static_cast<std::uint8_t>(duo_input::u1::kHostObservationBaseBytes));
    CHECK_EQ(p[at + 1], 0x0Eu);
    CHECK_EQ(read_u32(p + at + 2), 120000000u);
    CHECK_EQ(read_u32(p + at + 6), 120000000u);
    CHECK_EQ(read_u32(p + at + 10), 41234u);
    CHECK_EQ(p[at + 14], 0x0Bu);
    CHECK_EQ(static_cast<std::uint16_t>(p[at + 15] | (p[at + 16] << 8)), 2u);
    CHECK_EQ(read_u32(p + at + 17), 987654u);
    // The block ends here: one length byte plus kHostObservationBaseBytes,
    // never the sixteen further fields the full shape would carry.
    CHECK_EQ(reply.payload.size,
             at + 1 + duo_input::u1::kHostObservationBaseBytes + 1);
}

// The same rule the backend block was appended under, checked the same way:
// everything in front of kBackendBlockOffset is byte for byte what it was
// before any of this existed, so a configurator that stops reading there reads
// exactly what it always read.
TEST_CASE(the_host_block_leaves_the_prefix_byte_for_byte_unchanged) {
    Link after;
    after.hello();
    duo_input::u1::BackendCounters counters;
    counters.ignored_interfaces = 9;
    after.service.set_backend(duo_input::protocol::InputBackend::PIO_USB, counters);
    duo_input::u1::HostObservation observation;
    observation.init_flags = 0x01;
    observation.sof_frame_count = 7u;
    observation.core1_passes = 5u;
    after.service.set_host_observation(observation);
    const CdcFrame published = after.send(CdcMessageType::GET_DIAGNOSTICS);

    // This test never calls set_reference_counters, so the ceiling is one
    // field-block short of reached - see the same note on the host-stack
    // test above.
    CHECK_EQ(published.payload.size,
             duo_input::u1::kDiagnosticsPayloadSize -
                 duo_input::u1::kReferenceCounterFieldBytes);
    for (std::size_t index = 0; index < sizeof(kFrozenLegacyDiagnosticsPrefix); ++index) {
        CHECK_EQ(published.payload.data[index], kFrozenLegacyDiagnosticsPrefix[index]);
    }
    // And the backend block in between is untouched too: the host block was
    // appended behind it, not folded into it.
    const std::size_t backend_at = duo_input::u1::kBackendBlockOffset;
    CHECK_EQ(published.payload.data[backend_at],
             static_cast<std::uint8_t>(duo_input::protocol::InputBackend::PIO_USB));
    CHECK_EQ(published.payload.data[backend_at + 1],
             static_cast<std::uint8_t>(duo_input::u1::kBackendCounterCount));
    CHECK_EQ(read_u32(published.payload.data + backend_at + 2), 9u);
}

TEST_CASE(the_real_host_mapping_reaches_the_wire_without_relabeling_or_overwrite) {
    duo_input::u1::pio_usb::HostObservability observed;
    observed.init_flags = 0x0Du;
    observed.clk_hz_at_begin = 0x11223344u;
    observed.clk_hz_now = 0x55667788u;
    observed.sof_frame_count = 0x99AABBCCu;
    observed.root_port_state = 0x0Bu;
    observed.root_port_connects = 0x1234u;
    observed.core1_passes = 0xDEADBEEFu;
    observed.mount_events = 0x0102u;
    observed.umount_events = 0x0304u;
    observed.hid_mount_events = 0x0506u;
    observed.ep_slots_opened = 0x07u;
    observed.ep_max_failed_count = 0x08u;
    observed.max_pass_gap_us = 0x10203040u;
    observed.max_sof_gap = 0x1112u;
    observed.root_port_resets = 0x1314u;
    observed.hub_mount_events = 0x1516u;
    observed.ep_slot_map = 0x21222324u;
    observed.host_event_counts = 0x25262728u;
    observed.enum_progress_mask = 0x292A2B2Cu;
    observed.long_pass_count = 0x2D2E2F30u;
    observed.long_pass_total_ms = 0x31323334u;
    observed.core1_min_sp = 0x35363738u;
    observed.ep_transfer_flags = 0x393A3B3Cu;
    observed.xfer_completions_at_attach = 0x3D3E3F40u;
    observed.enum_stall_recoveries = 0x41424344u;

    Link link;
    link.hello();
    link.service.set_host_observation(
        duo_input::u1::pio_usb::to_wire_host_observation(observed));
    const CdcFrame reply = link.send(CdcMessageType::GET_DIAGNOSTICS);
    const std::size_t at = duo_input::u1::kBackendBlockOffset + 2u;
    const std::uint8_t expected[] = {
        0x4A, 0x0D, 0x44, 0x33, 0x22, 0x11, 0x88, 0x77, 0x66, 0x55,
        0xCC, 0xBB, 0xAA, 0x99, 0x0B, 0x34, 0x12, 0xEF, 0xBE, 0xAD,
        0xDE, 0x02, 0x01, 0x04, 0x03, 0x06, 0x05, 0x07, 0x08, 0x40,
        0x30, 0x20, 0x10, 0x12, 0x11, 0x14, 0x13, 0x16, 0x15, 0x24,
        0x23, 0x22, 0x21, 0x28, 0x27, 0x26, 0x25, 0x2C, 0x2B, 0x2A,
        0x29, 0x30, 0x2F, 0x2E, 0x2D, 0x34, 0x33, 0x32, 0x31, 0x38,
        0x37, 0x36, 0x35, 0x3C, 0x3B, 0x3A, 0x39, 0x40, 0x3F, 0x3E,
        0x3D, 0x44, 0x43, 0x42, 0x41,
    };
    // This test never calls set_reference_counters, so that block sends only
    // its own single not-published marker byte.
    CHECK_EQ(reply.payload.size, at + sizeof(expected) + 1);
    for (std::size_t index = 0; index < sizeof(expected); ++index) {
        CHECK_EQ(reply.payload.data[at + index], expected[index]);
    }
}

// ------------------------------------------------------ reference counters
//
// Task 3's bounded callback queue overflow count and how many of the
// reference target's own USB interfaces earned no logical role were both
// readable in the firmware from the day each was added, and neither had ever
// been read on hardware - there was no CDC path to ask a board for them until
// this block existed. Whether each of the two roles has an owner is what
// tells a route selected by a freshly loaded profile (PC1-only, PC2-only,
// both) apart from one nothing is actually reaching.
//
// The leading length byte is what the backend and host blocks already use for
// the same reason: CH375 and PIO_USB link this exact ConfigService and never
// call set_reference_counters, and without it their ten zero bytes would read
// as ten real measurements on a board that never took them.

// The regression this block exists to prevent: a CH375 or PIO_USB image -
// which never calls set_reference_counters - must publish a not-published
// marker, never ten zero bytes a configurator would render as real counters
// on a board with a keyboard actively typing.
TEST_CASE(a_backend_that_never_publishes_reference_counters_sends_only_the_marker) {
    Link link;
    link.hello();

    link.service.set_backend(duo_input::protocol::InputBackend::CH375);

    const CdcFrame reply = link.send(CdcMessageType::GET_DIAGNOSTICS);
    // Backend head (identifier + zero count) plus the host block's own
    // length-of-zero byte: nothing has published either here.
    const std::size_t at = duo_input::u1::kBackendBlockOffset + 2 + 1;

    CHECK_EQ(reply.payload.data[at], 0u);
    CHECK_EQ(reply.payload.size, at + 1);
}

TEST_CASE(the_reference_counters_reach_the_wire_at_their_fixed_offset) {
    Link link;
    link.hello();

    duo_input::u1::ReferenceCounters counters;
    counters.callback_overflows = 6;
    counters.ignored_interfaces = 2;
    counters.keyboard_ready = true;
    counters.mouse_ready = false;
    link.service.set_reference_counters(counters);

    const CdcFrame reply = link.send(CdcMessageType::GET_DIAGNOSTICS);
    const std::size_t at = duo_input::u1::kBackendBlockOffset + 2 + 1;

    CHECK_EQ(reply.payload.data[at], duo_input::u1::kReferenceCounterFieldBytes);
    CHECK_EQ(read_u32(reply.payload.data + at + 1), 6u);
    CHECK_EQ(read_u32(reply.payload.data + at + 5), 2u);
    CHECK_EQ(reply.payload.data[at + 9], 1u);
    CHECK_EQ(reply.payload.data[at + 10], 0u);
    CHECK_EQ(reply.payload.size, at + duo_input::u1::kReferenceCounterBlockBytes);
}

// The compatibility contract this block has to keep, checked the way the
// backend and host block tests above check their own: a reply naming the
// reference backend and carrying this block still begins with exactly the
// literal bytes a configurator that predates every appended block has always
// read, appends the new fields only after everything that came before them,
// and never grows past what one CDC frame can carry.
TEST_CASE(reference_diagnostics_begin_with_the_old_reply_and_stay_under_one_frame) {
    Link link;
    link.hello();

    // What the reference target's own main loop actually publishes: the
    // PIO_USB_REFERENCE identifier with no legacy backend counters (it keeps
    // none of DeviceRegistry's twelve) and its own four counters.
    link.service.set_backend(duo_input::protocol::InputBackend::PIO_USB_REFERENCE);
    duo_input::u1::ReferenceCounters counters;
    counters.callback_overflows = 6;
    counters.ignored_interfaces = 2;
    counters.keyboard_ready = true;
    counters.mouse_ready = true;
    link.service.set_reference_counters(counters);

    const CdcFrame reference_reply = link.send(CdcMessageType::GET_DIAGNOSTICS);

    CHECK(std::equal(std::begin(kFrozenLegacyDiagnosticsPrefix),
                      std::end(kFrozenLegacyDiagnosticsPrefix),
                      reference_reply.payload.data));
    CHECK(reference_reply.payload.size <= 1024u);
    CHECK_EQ(reference_reply.payload.size,
             duo_input::u1::kBackendBlockOffset + 2 + 1 +
                 duo_input::u1::kReferenceCounterBlockBytes);
}

TEST_CASE(the_diagnostics_say_whether_the_output_queue_is_overflowing_now) {
    Link link;
    link.hello();

    CHECK_EQ(link.send(CdcMessageType::GET_DIAGNOSTICS).payload.data[42], 0u);

    link.service.set_runtime_fault(duo_input::runtime::RuntimeFault::OutputQueueFull);

    // The counter beside it says input was lost at some point since boot. This
    // says the output runtime is refusing commands right now - which is the
    // difference between a burst that has passed and one still going on, and
    // there is no other outward sign of either.
    CHECK_EQ(link.send(CdcMessageType::GET_DIAGNOSTICS).payload.data[42],
             static_cast<std::uint8_t>(duo_input::runtime::RuntimeFault::OutputQueueFull));
}

TEST_CASE(the_diagnostics_carry_what_the_other_core_could_not_hand_over) {
    Link link;
    link.hello();
    link.service.set_dropped_commands(9);

    const CdcFrame reply = link.send(CdcMessageType::GET_DIAGNOSTICS);

    // Nine presses, releases or macro steps that never reached a computer.
    // Without this the only outward sign is a keyboard that missed some
    // letters, which reads as a hardware fault and is not one.
    CHECK_EQ(read_u32(reply.payload.data + 38), 9u);
}

TEST_CASE(diagnostics_say_whether_the_endpoint_is_answering) {
    // Without this the host cannot tell a working device from one whose second
    // board is dead, and neither could the people building it: the link was
    // silent for days and every reading available over this port said nothing
    // either way. The counters that existed were all counters of errors, which
    // a link that never started does not produce.
    Link link;
    link.hello();
    duo_input::u1::LinkState state;
    state.answered = true;
    state.mounted = true;
    state.frames_sent = 0x11223344;
    state.crc_errors = 7;
    state.echoed_frames = 3;
    link.service.set_link_state(state);

    const CdcFrame reply = link.send(CdcMessageType::GET_DIAGNOSTICS);
    const std::uint8_t* p = reply.payload.data;

    CHECK_EQ(p[21], 1u);
    CHECK_EQ(p[22], 1u);
    CHECK_EQ(read_u32(p + 23), 0x11223344u);
    CHECK_EQ(read_u32(p + 27), 7u);
    CHECK_EQ(read_u32(p + 31), 3u);
}

TEST_CASE(diagnostics_carry_what_the_endpoint_saw_when_the_link_died) {
    // The fail-safe cannot be watched as it happens: the link that would
    // carry the news is the one that went quiet. U2 records it and reports it
    // on the way back, which is what lets "releases within 100 ms" be checked
    // against real hardware rather than only against a unit test.
    Link link;
    link.hello();
    duo_input::u1::LinkState state;
    state.endpoint_drops = 2;
    state.endpoint_release_ms = 104;
    link.service.set_link_state(state);

    const CdcFrame reply = link.send(CdcMessageType::GET_DIAGNOSTICS);
    const std::uint8_t* p = reply.payload.data;

    CHECK_EQ(p[35], 2u);
    CHECK_EQ(static_cast<std::uint16_t>(p[36] | (p[37] << 8)), 104u);
}

TEST_CASE(a_silent_endpoint_is_reported_as_silent) {
    Link link;
    link.hello();
    duo_input::u1::LinkState state;
    state.answered = false;
    state.frames_sent = 900;
    link.service.set_link_state(state);

    const CdcFrame reply = link.send(CdcMessageType::GET_DIAGNOSTICS);
    const std::uint8_t* p = reply.payload.data;

    // Frames going out and nothing coming back is the exact shape of the fault
    // that took days to find, and it is now one reading.
    CHECK_EQ(p[21], 0u);
    CHECK_EQ(read_u32(p + 23), 900u);
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

TEST_CASE(status_reports_that_no_capture_is_running) {
    Link link;
    link.hello();

    const CdcFrame reply = link.send(CdcMessageType::GET_STATUS);

    // Reported truthfully rather than omitted, so the host reads an answer.
    CHECK_EQ(reply.payload.data[2], 0u);
}

// ----------------------------------------------------------------- capture

TEST_CASE(the_device_says_it_can_capture) {
    Link link;
    std::uint8_t request[4] = {0xFF, 0xFF, 0xFF, 0xFF};

    const CdcFrame reply = link.send(CdcMessageType::HELLO, request, sizeof(request));

    // There is a peripheral to capture from now, so the configurator may ask.
    // A device that stayed quiet about it would leave the operator looking at
    // a greyed-out button beside a keyboard that works.
    const std::uint32_t capabilities = u32_at(reply, 3);
    CHECK((capabilities &
           static_cast<std::uint32_t>(duo_input::protocol::Capability::CAPTURE)) != 0u);
}

TEST_CASE(a_capture_is_asked_for_rather_than_started_here) {
    Link link;
    link.hello();

    const CdcFrame reply = link.send(CdcMessageType::CAPTURE_BEGIN);

    CHECK_EQ(error_of(reply), CdcError::Ok);
    CHECK_EQ(reply.payload.size, 1u);
    // The capture runs on the other core. This class records what was asked
    // for and the loop carries it across; reaching into the runtime from here
    // would be one core writing the other's state from a USB callback.
    CHECK(link.service.take_capture_request() == CaptureRequest::Begin);
    // Taken once. A request read twice starts a second capture nobody asked
    // for, and the operator's next keystroke disappears into it.
    CHECK(link.service.take_capture_request() == CaptureRequest::None);
}

TEST_CASE(a_second_capture_while_one_is_running_is_busy) {
    Link link;
    link.hello();
    link.send(CdcMessageType::CAPTURE_BEGIN);

    const CdcFrame reply = link.send(CdcMessageType::CAPTURE_BEGIN);

    CHECK_EQ(error_of(reply), CdcError::Busy);
}

TEST_CASE(ending_a_capture_nobody_started_is_a_bad_state) {
    Link link;
    link.hello();

    CHECK_EQ(error_of(link.send(CdcMessageType::CAPTURE_END)), CdcError::BadState);
}

TEST_CASE(ending_a_capture_is_asked_for_rather_than_done_here) {
    Link link;
    link.hello();
    link.send(CdcMessageType::CAPTURE_BEGIN);
    link.service.take_capture_request();

    const CdcFrame reply = link.send(CdcMessageType::CAPTURE_END);

    CHECK_EQ(error_of(reply), CdcError::Ok);
    CHECK(link.service.take_capture_request() == CaptureRequest::Cancel);
}

TEST_CASE(status_reports_a_capture_the_runtime_says_is_running) {
    Link link;
    link.hello();
    link.send(CdcMessageType::CAPTURE_BEGIN);

    // Pushed in by the loop, the way the link state is. A capture ends on its
    // own after ten seconds and this class would never hear about it.
    link.service.set_capture_active(true);
    CHECK_EQ(link.send(CdcMessageType::GET_STATUS).payload.data[2], 1u);

    link.service.set_capture_active(false);
    CHECK_EQ(link.send(CdcMessageType::GET_STATUS).payload.data[2], 0u);
}

TEST_CASE(a_completed_capture_is_reported_without_being_asked) {
    Link link;
    link.hello();
    link.send(CdcMessageType::CAPTURE_BEGIN);
    const std::uint16_t asked = static_cast<std::uint16_t>(link.sequence - 1);

    CapturedTrigger trigger;
    trigger.kind = TriggerKind::KEYBOARD_USAGE;
    trigger.code = 0x1A;
    trigger.modifiers = 0x02;
    link.replies.clear();
    link.service.emit_capture_event(trigger);

    CHECK_EQ(link.replies.count(), 1u);
    const CdcFrame event = link.decode_last();
    CHECK(event.type == CdcMessageType::CAPTURE_EVENT);
    // Nobody asked, so it takes the sequence the next request would have used.
    CHECK_EQ(event.sequence, static_cast<std::uint16_t>(asked + 1));
    // Three bytes, in the order the host unpacks them.
    CHECK_EQ(event.payload.size, 3u);
    CHECK_EQ(event.payload.data[0], static_cast<std::uint8_t>(TriggerKind::KEYBOARD_USAGE));
    CHECK_EQ(event.payload.data[1], 0x1Au);
    CHECK_EQ(event.payload.data[2], 0x02u);
}

TEST_CASE(a_mouse_capture_travels_as_the_host_will_accept_it) {
    Link link;
    link.hello();
    link.send(CdcMessageType::CAPTURE_BEGIN);

    // Filled in by the controller that captures it, not written out here.
    //
    // The two conventions the host insists on - buttons counted from one, and
    // no modifiers on a mouse trigger, whatever is actually held - live in
    // mapping/capture.cpp. A trigger built by hand beside this assertion
    // agrees with the assertion and with nothing else, and both of those
    // conventions could be deleted from the firmware without it noticing.
    CaptureController capture;
    InputEvent control;
    control.kind = InputEventKind::KeyDown;
    control.code = 0xE0;
    InputEvent letter;
    letter.kind = InputEventKind::KeyDown;
    letter.code = 0x04;
    InputEvent press;
    press.kind = InputEventKind::MouseButtonDown;
    press.code = 4;  // the fifth button, counting from zero as the wire does
    CapturedTrigger trigger;

    // The second question of a session, after one that was answered with a
    // modifier held. The controller is the same object and the trigger it
    // fills in is the same field, so the mouse branch has to write the
    // modifier byte rather than leave the last answer's in place.
    capture.begin(1000);
    capture.handle(control);
    capture.handle(letter);
    CHECK(capture.take(trigger));
    CHECK_EQ(trigger.modifiers, 0x01u);

    capture.begin(2000);
    capture.handle(press);
    CHECK(capture.take(trigger));
    link.replies.clear();
    link.service.emit_capture_event(trigger);

    // The host refuses a button outside one to five, refuses one carrying
    // modifiers and refuses a zero code, so a payload it throws away is a
    // capture the operator has to perform again for nothing - and the service
    // refuses to send one at all, which is why an empty reply is the failure.
    CHECK_EQ(link.replies.count(), 1u);
    if (link.replies.count() != 1) {
        return;
    }
    const CdcFrame event = link.decode_last();
    CHECK_EQ(event.payload.data[0], static_cast<std::uint8_t>(TriggerKind::MOUSE_BUTTON));
    CHECK_EQ(event.payload.data[1], 5u);
    CHECK_EQ(event.payload.data[2], 0u);
}

TEST_CASE(an_invalid_mouse_capture_is_not_published_to_the_host) {
    Link link;
    link.hello();
    link.send(CdcMessageType::CAPTURE_BEGIN);

    CapturedTrigger invalid;
    invalid.kind = TriggerKind::MOUSE_BUTTON;
    invalid.code = 0;
    invalid.modifiers = 0x01;
    link.replies.clear();
    link.service.emit_capture_event(invalid);

    CHECK_EQ(link.replies.count(), 0u);
    CHECK(link.service.capture_active());
}

TEST_CASE(an_unsolicited_event_moves_the_sequence_on) {
    Link link;
    link.hello();
    link.send(CdcMessageType::CAPTURE_BEGIN);

    CapturedTrigger trigger;
    trigger.code = 0x04;
    link.replies.clear();
    link.service.emit_capture_event(trigger);
    const std::uint16_t event_sequence = link.decode_last().sequence;

    // The device spoke, so the host's next request counts from what the device
    // said. A configurator carrying on from its own last request would be
    // refused from here to the end of the session.
    link.sequence = static_cast<std::uint16_t>(event_sequence + 1);
    CHECK_EQ(error_of(link.send(CdcMessageType::GET_STATUS)), CdcError::Ok);
}

TEST_CASE(reporting_a_capture_ends_it) {
    Link link;
    link.hello();
    link.send(CdcMessageType::CAPTURE_BEGIN);

    CapturedTrigger trigger;
    trigger.code = 0x04;
    link.service.emit_capture_event(trigger);
    link.sequence = static_cast<std::uint16_t>(link.decode_last().sequence + 1);

    // One question, one answer. A capture still running after it was answered
    // goes on eating the operator's keystrokes.
    CHECK_EQ(error_of(link.send(CdcMessageType::CAPTURE_END)), CdcError::BadState);
}

TEST_CASE(a_capture_nobody_started_is_not_reported) {
    Link link;
    link.hello();
    link.replies.clear();

    CapturedTrigger trigger;
    trigger.code = 0x04;
    link.service.emit_capture_event(trigger);

    // Speaking out of turn costs the host its sequence over a trigger it never
    // asked for.
    CHECK_EQ(link.replies.count(), 0u);
}

TEST_CASE(a_host_that_went_away_stops_the_capture_it_left_running) {
    Link link;
    link.hello();
    link.send(CdcMessageType::CAPTURE_BEGIN);
    link.service.take_capture_request();

    link.service.on_disconnect();

    // Otherwise a configurator that crashed mid-question leaves a keyboard
    // silently eating its own input until the timeout runs out.
    CHECK(link.service.take_capture_request() == CaptureRequest::Cancel);
}

TEST_CASE(a_new_hello_session_cancels_a_capture_left_by_the_previous_session) {
    Link link;
    link.hello();
    link.send(CdcMessageType::CAPTURE_BEGIN);
    CHECK(link.service.take_capture_request() == CaptureRequest::Begin);

    const std::uint8_t request[4] = {0xFF, 0xFF, 0xFF, 0xFF};
    link.send(CdcMessageType::HELLO, request, sizeof(request));

    CHECK(link.service.take_capture_request() == CaptureRequest::Cancel);
}

TEST_CASE(a_factory_reset_stops_a_capture_too) {
    Link link;
    link.hello();
    link.send(CdcMessageType::CAPTURE_BEGIN);
    link.service.take_capture_request();
    link.service.confirm_factory_reset();
    link.send(CdcMessageType::FACTORY_RESET_ARM);

    link.send(CdcMessageType::FACTORY_RESET_COMMIT);

    // Everything the question was about has just been erased. The emulator the
    // configurator was written against ends the capture here, and a keyboard
    // left swallowing its own input after a reset has no way back except the
    // ten-second timeout.
    CHECK(link.service.take_capture_request() == CaptureRequest::Cancel);
    CHECK_EQ(error_of(link.send(CdcMessageType::CAPTURE_END)), CdcError::BadState);
}

TEST_CASE(releasing_everything_stops_a_capture_too) {
    Link link;
    link.hello();
    link.send(CdcMessageType::CAPTURE_BEGIN);
    link.service.take_capture_request();

    link.send(CdcMessageType::STOP_AND_RELEASE_ALL);

    // The one control that has to work when everything else is wrong.
    CHECK(link.service.take_capture_request() == CaptureRequest::Cancel);
    CHECK_EQ(error_of(link.send(CdcMessageType::CAPTURE_END)), CdcError::BadState);
}

// ---------------------------------------------------------- active profile

namespace {

/// The interoperability vector, seeded straight into the store.
///
/// Building a configuration by hand beside the code that reads it would test
/// this file's idea of the format. This is the one the configurator writes.
bool seed_stored_config(Link& link) {
    const std::string path = std::string(DUO_TEST_VECTOR_DIR) + "/config_vectors/valid_full.bin";
    std::FILE* file = std::fopen(path.c_str(), "rb");
    if (file == nullptr) {
        return false;
    }
    std::vector<std::uint8_t> bytes;
    std::uint8_t chunk[4096];
    std::size_t read = 0;
    while ((read = std::fread(chunk, 1, sizeof(chunk), file)) > 0) {
        bytes.insert(bytes.end(), chunk, chunk + read);
    }
    std::fclose(file);
    if (bytes.empty()) {
        return false;
    }

    std::uint8_t digest[kSha256DigestSize];
    sha256(bytes.data(), bytes.size(), digest);
    link.store.begin(static_cast<std::uint32_t>(bytes.size()), digest);
    link.store.write_chunk(0, bytes.data(), bytes.size());
    link.store.verify();
    link.store.commit();
    return link.store.scan().has_active;
}

}  // namespace

TEST_CASE(a_profile_change_is_asked_for_rather_than_asserted) {
    Link link;
    CHECK(seed_stored_config(link));
    link.hello();
    const std::uint8_t before = link.service.active_profile();

    std::uint8_t request[1] = {3};
    const CdcFrame reply =
        link.send(CdcMessageType::SET_ACTIVE_PROFILE, request, sizeof(request));

    CHECK_EQ(error_of(reply), CdcError::Ok);
    std::uint8_t wanted = 0;
    CHECK(link.service.take_profile_request(wanted));
    CHECK_EQ(wanted, 3u);
    CHECK_FALSE(link.service.take_profile_request(wanted));
    // Not running it yet. Core 1 has a macro to stop and held keys to let go
    // of before the bindings change underneath them, and saying the swap
    // happened before it did is how a key ends up stranded on a computer.
    CHECK_EQ(link.service.active_profile(), before);
}

TEST_CASE(the_reported_profile_is_the_one_the_runtime_confirmed) {
    Link link;
    CHECK(seed_stored_config(link));
    link.hello();
    std::uint8_t request[1] = {3};
    link.send(CdcMessageType::SET_ACTIVE_PROFILE, request, sizeof(request));
    std::uint8_t wanted = 0;
    link.service.take_profile_request(wanted);

    // A wrong/stale acknowledgement cannot publish a profile that was never
    // installed, but the matching acknowledgement can.
    CHECK_FALSE(link.service.confirm_profile_applied(2));
    CHECK(link.service.active_profile() != 2u);
    CHECK(link.service.confirm_profile_applied(wanted));

    CHECK_EQ(link.send(CdcMessageType::GET_STATUS).payload.data[1], 3u);
    CHECK_FALSE(link.service.confirm_profile_applied(wanted));
}

TEST_CASE(a_profile_selected_on_the_device_is_reported_without_a_host_request) {
    Link link;
    link.hello();

    link.service.publish_local_profile(4);

    CHECK_EQ(link.send(CdcMessageType::GET_STATUS).payload.data[1], 4u);
}

TEST_CASE(a_profile_the_configuration_does_not_have_is_not_asked_for) {
    Link link;
    CHECK(seed_stored_config(link));
    link.hello();

    std::uint8_t request[1] = {200};
    const CdcFrame reply =
        link.send(CdcMessageType::SET_ACTIVE_PROFILE, request, sizeof(request));

    CHECK_EQ(error_of(reply), CdcError::InvalidRequest);
    std::uint8_t wanted = 0;
    CHECK_FALSE(link.service.take_profile_request(wanted));
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
