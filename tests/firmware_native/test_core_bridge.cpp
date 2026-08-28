// The traffic between the two cores, and the order it happens in.
//
// Everything here was found by reading main.cpp rather than by a test, twice,
// which is why the code under test was moved out of main.cpp. Two things are
// worth the trouble.
//
// A capture ends by being answered. Core 1 stores the answer and then stores
// that the capture is over; if Core 0 reads those in the same order it can see
// neither - no answer yet, and no capture either - and the answer it takes on
// the next pass is refused for want of a capture to answer. The operator
// pressed a key and the configurator waits ten seconds for nothing.
//
// A profile acknowledgement means two different things depending on who asked.
// A host's is matched against the request it is answering. A binding's has no
// request to match, and putting it through that check reports a profile the
// device has stopped running.

#include <cstring>
#include <vector>

#include "config_service.hpp"
#include "core1_runtime.hpp"
#include "core_bridge.hpp"
#include "test_support.hpp"

using duo_input::config::ActionKind;
using duo_input::config::BindingMode;
using duo_input::config::TriggerKind;
using duo_input::protocol::CdcFrame;
using duo_input::protocol::CdcMessageType;
using duo_input::protocol::ProtocolLimits;
using duo_input::runtime::CommandKind;
using duo_input::runtime::OutputCommand;
using duo_input::storage::AbStore;
using duo_input::storage::FlashBackend;
using duo_input::u1::CdcError;
using duo_input::u1::CdcSink;
using duo_input::u1::ConfigHandoff;
using duo_input::u1::ConfigService;
using duo_input::u1::Core1Runtime;
using duo_input::u1::ICommandSink;
using duo_input::u1::IProfileSource;
using duo_input::u1::IRuntimeConfig;
using duo_input::u1::pump_core_bridge;
using duo_input::u1::input::InputEvent;
using duo_input::u1::input::InputEventKind;
using duo_input::u1::mapping::Binding;
using duo_input::u1::mapping::CapturedTrigger;

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
        return package.data != nullptr && package.size != 0;
    }
    bool clear() override { return true; }
};

struct RecordingSink final : ICommandSink {
    std::vector<OutputCommand> commands;

    /// Taken the instant it is given, so there is never anything waiting.
    std::size_t pending() const override { return 0; }

    bool accept = true;

    bool submit(const OutputCommand& command) override {
        if (!accept) {
            return false;
        }
        commands.push_back(command);
        return true;
    }
};

/// Two profiles, so a swap has somewhere to go.
struct TwoProfiles final : IProfileSource {
    std::vector<Binding> profile_zero;
    std::vector<Binding> profile_two;

    std::size_t bindings_for(std::uint8_t profile, Binding* out) const override {
        const std::vector<Binding>& source = profile == 0 ? profile_zero : profile_two;
        for (std::size_t index = 0; index < source.size(); ++index) {
            out[index] = source[index];
        }
        return source.size();
    }
};

/// One conversation over CDC, driven the way the configurator drives it.
struct Link {
    MemoryFlash flash;
    AbStore store{flash};
    Recorder replies;
    RuntimeConfigRecorder runtime_config;
    ConfigService service{store, replies, runtime_config};
    std::uint16_t sequence = 0;

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

    void hello() {
        std::uint8_t request[4] = {0xFF, 0xFF, 0xFF, 0xFF};
        send(CdcMessageType::HELLO, request, sizeof(request));
    }
};

InputEvent key(InputEventKind kind, std::uint16_t usage) {
    InputEvent event;
    event.kind = kind;
    event.code = usage;
    return event;
}

Binding set_profile_on(std::uint16_t usage, std::uint8_t profile) {
    Binding binding;
    binding.trigger = TriggerKind::KEYBOARD_USAGE;
    binding.code = usage;
    binding.mode = BindingMode::REPLACE;
    binding.action = ActionKind::SET_PROFILE;
    binding.parameter = profile;
    return binding;
}

/// A runtime whose answers a test dictates.
///
/// It exists for one interleaving that cannot be staged against the real
/// Core1Runtime from a single thread: Core 1 finishing a capture between Core
/// 0's two reads, so that the runtime says no capture is running and hands
/// over the trigger that ended it in the same pass.
struct StandInRuntime {
    CapturedTrigger trigger;
    bool has_event = false;
    bool active = false;
    int begins = 0;
    int cancels = 0;

    void request_capture_begin() { ++begins; }
    void request_capture_cancel() { ++cancels; }
    bool capture_active() const { return active; }

    bool take_capture_event(CapturedTrigger& out) {
        if (!has_event) {
            return false;
        }
        has_event = false;
        out = trigger;
        return true;
    }

    void request_profile(std::uint8_t) {}
    bool take_profile_ack(std::uint8_t&, bool&) { return false; }
    std::uint32_t dropped_commands() const { return 0; }
};

}  // namespace

// ------------------------------------------------------------------ capture

TEST_CASE(a_capture_the_runtime_has_not_reached_yet_is_still_a_capture) {
    Link link;
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    link.hello();
    link.send(CdcMessageType::CAPTURE_BEGIN);

    // One pass: the request is posted and Core 1 has not ticked. Between those
    // two moments nothing anywhere is running a capture, and publishing that
    // fact is what makes the operator's answer arrive with nothing to answer.
    pump_core_bridge(link.service, runtime);

    CHECK_EQ(link.send(CdcMessageType::GET_STATUS).payload.data[2], 1u);
}

TEST_CASE(the_answer_to_a_capture_survives_the_pass_that_asked_for_it) {
    Link link;
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    link.hello();
    link.send(CdcMessageType::CAPTURE_BEGIN);

    // Pass one carries the request across. Core 1 then begins the capture and
    // is answered before Core 0 comes round again - which is ordinary: a pass
    // round Core 0 is a millisecond of USB and a person can press a key inside
    // one.
    pump_core_bridge(link.service, runtime);
    runtime.tick(1000);
    runtime.handle_input(key(InputEventKind::KeyDown, 0x1A), 1010);

    link.replies.clear();
    pump_core_bridge(link.service, runtime);

    CHECK_EQ(link.replies.count(), 1u);
    if (link.replies.count() != 1) {
        return;
    }
    const CdcFrame event = link.decode_last();
    CHECK(event.type == CdcMessageType::CAPTURE_EVENT);
    CHECK_EQ(event.payload.data[1], 0x1Au);
}

TEST_CASE(a_capture_that_ended_between_the_two_reads_is_still_answered) {
    Link link;
    link.hello();
    link.send(CdcMessageType::CAPTURE_BEGIN);

    // No capture running - Core 1 has just ended it - and the trigger that
    // ended it, both in one pass.
    StandInRuntime runtime;
    runtime.active = false;
    runtime.has_event = true;
    runtime.trigger.kind = TriggerKind::KEYBOARD_USAGE;
    runtime.trigger.code = 0x1A;
    runtime.trigger.modifiers = 0;

    link.replies.clear();
    pump_core_bridge(link.service, runtime);

    // The state read said no capture. Publishing that before taking the event
    // ends the capture on this side, and the answer is then refused - the
    // operator pressed a key and the configurator waits out the timeout.
    CHECK_EQ(link.replies.count(), 1u);
    if (link.replies.count() != 1) {
        return;
    }
    CHECK(link.decode_last().type == CdcMessageType::CAPTURE_EVENT);
}

TEST_CASE(the_host_asking_to_end_a_capture_reaches_the_other_core) {
    Link link;
    link.hello();
    link.send(CdcMessageType::CAPTURE_BEGIN);
    StandInRuntime runtime;
    runtime.active = true;
    pump_core_bridge(link.service, runtime);

    link.send(CdcMessageType::CAPTURE_END);
    pump_core_bridge(link.service, runtime);

    CHECK_EQ(runtime.begins, 1);
    CHECK_EQ(runtime.cancels, 1);
}

// ------------------------------------------------------------------ profiles

TEST_CASE(a_profile_a_binding_chose_becomes_the_reported_profile) {
    Link link;
    RecordingSink sink;
    TwoProfiles profiles;
    profiles.profile_zero.push_back(set_profile_on(0x3E, 2));
    Core1Runtime runtime(sink, profiles);
    link.hello();

    // Nobody asked over CDC. A key did.
    runtime.handle_input(key(InputEventKind::KeyDown, 0x3E), 1000);
    runtime.tick(1000);
    pump_core_bridge(link.service, runtime);

    // Put through the host's confirmation gate this is discarded, because
    // there is no outstanding request for it to match - and GET_STATUS then
    // reports a profile the device stopped running.
    CHECK_EQ(link.service.active_profile(), 2u);
}

TEST_CASE(an_acknowledgement_the_host_never_asked_for_is_not_reported_as_its_answer) {
    Link link;
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    link.hello();
    const std::uint8_t before = link.service.active_profile();

    // Flagged as the host's, because it came in through the cross-core mailbox
    // Core 0 owns - but this ConfigService has no SET_ACTIVE_PROFILE
    // outstanding, so there is nothing for it to be the answer to.
    runtime.request_profile(3);
    runtime.tick(1000);
    pump_core_bridge(link.service, runtime);

    CHECK_EQ(link.service.active_profile(), before);
}

// -------------------------------------------------------- refused commands

TEST_CASE(what_the_queue_refused_is_carried_across_to_the_service) {
    Link link;
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    link.hello();

    sink.accept = false;
    runtime.handle_input(key(InputEventKind::KeyDown, 0x04), 1000);
    runtime.handle_input(key(InputEventKind::KeyDown, 0x05), 1001);
    pump_core_bridge(link.service, runtime);

    // Two keys the far computer was never told about. Without this the only
    // sign is a keyboard that missed two letters.
    CHECK_EQ(link.service.dropped_commands(), 2u);
}

// ------------------------------------------------------- configuration handoff

TEST_CASE(a_handed_over_configuration_is_adopted_once_and_acknowledged_once) {
    ConfigHandoff handoff;
    const std::uint8_t package[4] = {1, 2, 3, 4};

    const std::uint32_t ticket = handoff.post(
        duo_input::protocol::ByteView{package, sizeof(package)});
    CHECK_FALSE(handoff.finished(ticket));

    duo_input::protocol::ByteView taken{nullptr, 0};
    CHECK(handoff.take(taken));
    CHECK(taken.data == package);
    CHECK_EQ(taken.size, sizeof(package));
    CHECK_FALSE(handoff.finished(ticket));

    handoff.complete(true);
    CHECK(handoff.finished(ticket));

    // Once. Core 1 takes this at the top of every pass, and a request that is
    // never used up would tear down and rebuild the bindings, the macros and
    // everything held on both computers several thousand times a second.
    duo_input::protocol::ByteView again{nullptr, 0};
    CHECK_FALSE(handoff.take(again));
}

TEST_CASE(what_the_other_core_made_of_the_package_comes_back_with_it) {
    ConfigHandoff handoff;
    const std::uint8_t package[4] = {1, 2, 3, 4};
    duo_input::protocol::ByteView taken{nullptr, 0};

    const std::uint32_t refused = handoff.post(duo_input::protocol::ByteView{nullptr, 0});
    handoff.take(taken);
    handoff.complete(false);
    CHECK(handoff.finished(refused));
    CHECK_FALSE(handoff.succeeded());

    const std::uint32_t accepted = handoff.post(
        duo_input::protocol::ByteView{package, sizeof(package)});
    handoff.take(taken);
    handoff.complete(true);
    CHECK(handoff.finished(accepted));
    // WRITE_COMMIT is answered from this. Reporting a package Core 1 could not
    // read as accepted leaves the configurator believing in a configuration
    // the device is not running.
    CHECK(handoff.succeeded());
}
