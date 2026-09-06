#pragma once

// What a captured callback record means, decided in ordinary task context.
//
// This is the only place in the reference target that decides what a device is
// and which of the two roles it may occupy. It reuses the neutral pieces
// rather than re-deriving them: classify_hid for what an interface is, the
// shared report-descriptor parsers underneath it, and the receiver-specific
// check that tells a real keyboard from the Keychron side-button channel.
// Everything else here is role ownership, which is small and stated once.
//
// Bounded like everything on this path. Interfaces live in a fixed table, and
// one record produces at most one event per role that has to be told
// something - two, when an overflow means both are holding keys nobody can
// account for any more.

#include <cstddef>
#include <cstdint>

#include "callback_queue.hpp"
#include "input/source.hpp"

namespace duo_input::u1::reference {

class ReferenceSourceAdapter {
public:
    //: The pipeline instance each role's events are addressed to. The
    //: pipeline never interprets a source_id, so these only have to be
    //: distinct and stable.
    static constexpr std::uint8_t kKeyboardPort = 0;
    static constexpr std::uint8_t kMousePort = 1;

    /// Turn one captured record into whatever it means.
    ///
    /// ``now_us`` stands in for a record that carries no timestamp of its own.
    void consume(const ReferenceCallbackRecord& record, std::uint32_t now_us);

    /// Take one event this adapter has produced. False when there is none.
    bool take_event(input::SourceEvent& event, input::SourceIdentity& identity);

    //: HID interface protocol values, named here so this stays transport
    //: neutral and unit-testable without TinyUSB.
    static constexpr std::uint8_t kHidProtocolBoot = 0;
    static constexpr std::uint8_t kHidProtocolReport = 1;

    /// An interface that has to be moved to a different HID protocol.
    ///
    /// A layout taken from a report descriptor describes what the device sends
    /// in *report* protocol. TinyUSB starts boot-capable interfaces in boot
    /// protocol, whose mouse report is three bytes with no Report ID, so
    /// reading those with a descriptor layout drops every report. The adapter
    /// cannot call TinyUSB itself - it is transport neutral and tested without
    /// it - so it says which interface needs what, and the host core acts.
    struct ProtocolRequest {
        std::uint8_t dev_addr = 0;
        std::uint8_t instance = 0;
        std::uint8_t protocol = kHidProtocolReport;
    };

    /// Take one pending protocol change. False when there is none.
    bool take_protocol_request(ProtocolRequest& request);

    struct DescriptorRequest {
        std::uint8_t dev_addr = 0;
        std::uint8_t instance = 0;
        std::uint16_t length = 0;
    };

    // The post-mount read is an experiment for the one receiver measured to
    // fail its 77-byte descriptor during enumeration. One second leaves the
    // sibling-interface enumeration and normal report traffic settled. If
    // TinyUSB's single control slot is still occupied, offers are spaced by
    // 10 ms and stop after one second; once the API accepts one transfer there
    // is never a second on-wire attempt.
    static constexpr std::uint32_t kDescriptorQuietUs = 1000000u;
    static constexpr std::uint32_t kDescriptorOfferIntervalUs = 10000u;
    static constexpr std::uint32_t kDescriptorMaxOffers = 100u;

    bool take_descriptor_request(std::uint32_t now_us,
                                 DescriptorRequest& request);
    void descriptor_request_accepted();

    /// Whether an event is waiting. The caller drains before consuming again,
    /// so nothing this adapter produced is ever dropped for want of room.
    bool has_pending() const { return pending_count_ != 0; }

    std::uint8_t logical_port(input::DeviceKind kind) const {
        return kind == input::DeviceKind::Keyboard ? kKeyboardPort : kMousePort;
    }

private:
    enum class Role : std::uint8_t {
        /// Known, and deliberately carrying nothing.
        Ignored,
        Keyboard,
        Mouse,
        /// The Keychron receiver's side-button channel: reports reach the
        /// mouse's pipeline as AuxiliaryReport, and it never becomes the
        /// keyboard.
        Auxiliary,
    };

    struct Interface {
        bool used = false;
        std::uint8_t dev_addr = 0;
        std::uint8_t instance = 0;
        Role role = Role::Ignored;
    };

    //: Two devices behind the hub, each of which may present a mouse, a
    //: keyboard-shaped channel and a vendor interface that earns no role.
    static constexpr std::size_t kInterfaceCapacity = 8;

    struct Pending {
        input::SourceEvent event{};
        input::SourceIdentity identity{};
    };

    //: One per role. An overflow is the only record that has to tell both.
    static constexpr std::size_t kPendingCapacity = 2;

    Interface* find(std::uint8_t dev_addr, std::uint8_t instance);
    Interface* claim_slot(std::uint8_t dev_addr, std::uint8_t instance);
    void push(input::SourceEventKind kind,
              std::uint8_t source_id,
              const input::SourceIdentity& identity,
              std::uint8_t endpoint = 0,
              const std::uint8_t* report = nullptr,
              std::size_t report_size = 0,
              std::uint32_t received_us = 0);

    void request_protocol(std::uint8_t dev_addr, std::uint8_t instance,
                          std::uint8_t protocol);
    void cancel_protocol_requests(std::uint8_t dev_addr,
                                  std::uint8_t instance);
    void on_mount(const ReferenceCallbackRecord& record, std::uint32_t now_us);
    void on_unmount(const ReferenceCallbackRecord& record);
    void on_report(const ReferenceCallbackRecord& record, std::uint32_t now_us);
    void on_overflow();

    //: One request per interface that can ask for one, which is at most the
    //: interface table itself.
    ProtocolRequest protocol_requests_[kInterfaceCapacity]{};
    std::uint8_t protocol_request_count_ = 0;
    std::uint8_t protocol_request_head_ = 0;

    Interface interfaces_[kInterfaceCapacity]{};
    Pending pending_[kPendingCapacity]{};
    std::uint8_t pending_count_ = 0;
    std::uint8_t pending_head_ = 0;

    input::SourceIdentity keyboard_identity_{};
    input::SourceIdentity mouse_identity_{};
    bool keyboard_owned_ = false;
    bool mouse_owned_ = false;

    struct PendingDescriptorRequest {
        bool active = false;
        DescriptorRequest request{};
        std::uint32_t next_offer_us = 0;
        std::uint32_t offers = 0;
    } descriptor_request_{};
};

}  // namespace duo_input::u1::reference
