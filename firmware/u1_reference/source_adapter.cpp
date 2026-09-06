#include "source_adapter.hpp"

#include <algorithm>
#include <cstring>

#include "pio_usb/hid_setup.hpp"

namespace duo_input::u1::reference {

namespace {

using input::DeviceKind;
using input::SourceEvent;
using input::SourceEventKind;
using input::SourceIdentity;

constexpr std::uint16_t kAulaVendorId = 0x3554;
constexpr std::uint16_t kAulaProductId = 0xFA09;
constexpr std::uint16_t kAulaKeyboardDescriptorLength = 64;
//: The follow-up asks for the whole document. The 77-byte read is the one
//: that came back rotated by 64; running it in the same session as the
//: one-packet read is what lets the two answers be compared at all.
constexpr std::uint16_t kAulaKeyboardFollowupLength = 77;

bool time_reached(std::uint32_t now, std::uint32_t deadline) {
    return static_cast<std::int32_t>(now - deadline) >= 0;
}

}  // namespace

ReferenceSourceAdapter::Interface* ReferenceSourceAdapter::find(
    std::uint8_t dev_addr, std::uint8_t instance) {
    for (Interface& entry : interfaces_) {
        if (entry.used && entry.dev_addr == dev_addr && entry.instance == instance) {
            return &entry;
        }
    }
    return nullptr;
}

ReferenceSourceAdapter::Interface* ReferenceSourceAdapter::claim_slot(
    std::uint8_t dev_addr, std::uint8_t instance) {
    if (Interface* existing = find(dev_addr, instance)) {
        return existing;
    }
    for (Interface& entry : interfaces_) {
        if (!entry.used) {
            entry.used = true;
            entry.dev_addr = dev_addr;
            entry.instance = instance;
            entry.role = Role::Ignored;
            return &entry;
        }
    }
    // The table is full. Refusing here means the interface earns no role and
    // its reports are ignored, which is the safe end of the trade: a device
    // that is never announced holds nothing that has to be released.
    return nullptr;
}

void ReferenceSourceAdapter::push(SourceEventKind kind,
                                  std::uint8_t source_id,
                                  const SourceIdentity& identity,
                                  std::uint8_t endpoint,
                                  const std::uint8_t* report,
                                  std::size_t report_size,
                                  std::uint32_t received_us) {
    if (pending_count_ >= kPendingCapacity) {
        // Unreachable by construction: the caller drains before consuming
        // again, and no record produces more events than there are roles.
        return;
    }

    const std::size_t at = (pending_head_ + pending_count_) % kPendingCapacity;
    Pending& slot = pending_[at];
    slot.event = SourceEvent{};
    slot.event.kind = kind;
    slot.event.source_id = source_id;
    slot.event.endpoint = endpoint;
    slot.event.received_us = received_us;
    if (report != nullptr && report_size != 0) {
        const std::size_t copied =
            std::min<std::size_t>(report_size, input::kMaxSourceReportBytes);
        std::memcpy(slot.event.report, report, copied);
        slot.event.report_size = copied;
    }
    slot.identity = identity;
    ++pending_count_;
}

void ReferenceSourceAdapter::request_protocol(std::uint8_t dev_addr,
                                              std::uint8_t instance,
                                              std::uint8_t protocol) {
    if (protocol_request_count_ >= kInterfaceCapacity) {
        return;
    }
    const std::size_t at =
        (protocol_request_head_ + protocol_request_count_) % kInterfaceCapacity;
    protocol_requests_[at] = ProtocolRequest{dev_addr, instance, protocol};
    ++protocol_request_count_;
}

void ReferenceSourceAdapter::cancel_protocol_requests(std::uint8_t dev_addr,
                                                      std::uint8_t instance) {
    ProtocolRequest kept[kInterfaceCapacity]{};
    std::uint8_t kept_count = 0;
    while (protocol_request_count_ != 0) {
        ProtocolRequest request{};
        take_protocol_request(request);
        if (request.dev_addr != dev_addr || request.instance != instance) {
            kept[kept_count++] = request;
        }
    }
    protocol_request_head_ = 0;
    protocol_request_count_ = kept_count;
    for (std::uint8_t index = 0; index < kept_count; ++index) {
        protocol_requests_[index] = kept[index];
    }
}

void ReferenceSourceAdapter::on_mount(const ReferenceCallbackRecord& record,
                                      std::uint32_t now_us) {
    SourceIdentity identity{};
    const pio_usb::HidLayoutSource layout_source = pio_usb::classify_hid_layout(
        record.protocol,
        record.descriptor_size != 0 ? record.descriptor.data() : nullptr,
        record.descriptor_size, identity);
    const bool classified = layout_source != pio_usb::HidLayoutSource::None;
    if (!classified || identity.kind == DeviceKind::Unknown) {
        // Nothing here this firmware can read. Deliberately not given a role:
        // an interface that cannot be parsed must not keep the real device
        // that follows it from ever claiming one.
        return;
    }

    identity.vendor_id = record.vid;
    identity.product_id = record.pid;

    Interface* const existing = find(record.dev_addr, record.instance);
    Interface* entry = claim_slot(record.dev_addr, record.instance);
    if (entry == nullptr) {
        return;
    }

    if (pio_usb::is_keychron_auxiliary_interface(identity.vendor_id,
                                                 identity.product_id,
                                                 identity.kind)) {
        // Shaped like a keyboard, and never one. Its reports belong to the
        // mouse this receiver also presents; it is not a source of its own, so
        // nothing downstream is told it appeared.
        entry->role = Role::Auxiliary;
        return;
    }

    // A descriptor layout describes report protocol, so the interface has to
    // be moved there for it to describe anything at all. An interface that
    // fell back to the boot layout must stay where it is: moving it would make
    // the device send a format nothing here knows how to read.
    //
    // Not requested for the auxiliary channel above: its reports are matched
    // by shape rather than by layout, and that shape is boot protocol's.
    const bool wants_report_protocol =
        layout_source == pio_usb::HidLayoutSource::ReportDescriptor;

    // A successful late read describes the interface already announced with
    // its boot fallback. Replace that identity in place and announce a new
    // Ready so InputPipeline atomically adopts the descriptor layout before
    // report protocol is requested.
    if (entry->role == Role::Keyboard && keyboard_owned_ &&
        identity.kind == DeviceKind::Keyboard && wants_report_protocol) {
        const SourceIdentity boot_identity = keyboard_identity_;
        keyboard_identity_ = identity;
        request_protocol(record.dev_addr, record.instance, kHidProtocolReport);
        push(SourceEventKind::Detached, kKeyboardPort, boot_identity);
        push(SourceEventKind::Ready, kKeyboardPort, identity);
        return;
    }
    if (existing != nullptr) {
        // A late descriptor may only refine the role this exact interface
        // already owns. A truncated or unrelated document must not turn an
        // existing keyboard into a mouse while leaving keyboard_owned_ set.
        return;
    }

    if (identity.kind == DeviceKind::Keyboard && !keyboard_owned_) {
        if (wants_report_protocol) {
            request_protocol(record.dev_addr, record.instance, kHidProtocolReport);
        }
        entry->role = Role::Keyboard;
        keyboard_owned_ = true;
        keyboard_identity_ = identity;
        if (!wants_report_protocol && record.vid == kAulaVendorId &&
            record.pid == kAulaProductId && !descriptor_request_.active) {
            descriptor_request_.active = true;
            descriptor_request_.request = DescriptorRequest{
                record.dev_addr, record.instance, kAulaKeyboardDescriptorLength};
            descriptor_request_.next_offer_us = now_us + kDescriptorQuietUs;
            descriptor_request_.offers = 0;
        }
        push(SourceEventKind::Ready, kKeyboardPort, identity);
        return;
    }

    if (identity.kind == DeviceKind::Mouse && !mouse_owned_) {
        if (wants_report_protocol) {
            request_protocol(record.dev_addr, record.instance, kHidProtocolReport);
        }
        entry->role = Role::Mouse;
        mouse_owned_ = true;
        mouse_identity_ = identity;
        push(SourceEventKind::Ready, kMousePort, identity);
        return;
    }

    // A second claimant for a role that is already taken. Accepted onto the
    // bus and ignored, deterministically, rather than displacing the device
    // that is already routing.
    entry->role = Role::Ignored;
}

void ReferenceSourceAdapter::on_unmount(const ReferenceCallbackRecord& record) {
    cancel_protocol_requests(record.dev_addr, record.instance);
    cancel_descriptor_request(record.dev_addr, record.instance);
    Interface* entry = find(record.dev_addr, record.instance);
    if (entry == nullptr) {
        return;
    }

    const Role role = entry->role;
    entry->used = false;
    entry->role = Role::Ignored;

    if (role == Role::Keyboard && keyboard_owned_) {
        // Announced before the identity is cleared: what the pipeline releases
        // it releases as this device, not as an anonymous one.
        push(SourceEventKind::Detached, kKeyboardPort, keyboard_identity_);
        keyboard_owned_ = false;
        keyboard_identity_ = SourceIdentity{};
        return;
    }

    if (role == Role::Mouse && mouse_owned_) {
        push(SourceEventKind::Detached, kMousePort, mouse_identity_);
        mouse_owned_ = false;
        mouse_identity_ = SourceIdentity{};
    }

    // An Auxiliary or Ignored interface owns nothing downstream, so there is
    // nothing to release and nobody to tell. The mouse it belongs to announces
    // its own departure through its own interface.
}

void ReferenceSourceAdapter::on_report(const ReferenceCallbackRecord& record,
                                       std::uint32_t now_us) {
    Interface* entry = find(record.dev_addr, record.instance);
    if (entry == nullptr) {
        return;
    }

    const std::uint32_t received =
        record.received_us != 0 ? record.received_us : now_us;

    switch (entry->role) {
        case Role::Keyboard:
            push(SourceEventKind::Report, kKeyboardPort, keyboard_identity_,
                 record.instance, record.report.data(), record.report_size,
                 received);
            return;
        case Role::Mouse:
            push(SourceEventKind::Report, kMousePort, mouse_identity_,
                 record.instance, record.report.data(), record.report_size,
                 received);
            return;
        case Role::Auxiliary: {
            // Serviced so it cannot block the mouse's own reports, and carried
            // to the same pipeline - never read as that pipeline's own layout.
            SourceIdentity identity = mouse_identity_;
            identity.kind = DeviceKind::Mouse;
            push(SourceEventKind::AuxiliaryReport, kMousePort, identity,
                 record.instance, record.report.data(), record.report_size,
                 received);
            return;
        }
        case Role::Ignored:
        default:
            return;
    }
}

void ReferenceSourceAdapter::on_overflow() {
    // Input was handed to this firmware and not kept, so nothing downstream
    // can still be trusted to know what is held. Every role that is holding
    // something is told, before its identity is cleared.
    if (keyboard_owned_) {
        push(SourceEventKind::Fault, kKeyboardPort, keyboard_identity_);
        keyboard_owned_ = false;
        keyboard_identity_ = SourceIdentity{};
    }
    if (mouse_owned_) {
        push(SourceEventKind::Fault, kMousePort, mouse_identity_);
        mouse_owned_ = false;
        mouse_identity_ = SourceIdentity{};
    }
    for (Interface& entry : interfaces_) {
        entry = Interface{};
    }
    note_descriptor_giveup(ReferenceDescriptorReason::Overflow);
    descriptor_request_ = PendingDescriptorRequest{};
}

void ReferenceSourceAdapter::consume(const ReferenceCallbackRecord& record,
                                     std::uint32_t now_us) {
    switch (record.kind) {
        case ReferenceCallbackKind::Mount:
            on_mount(record, now_us);
            return;
        case ReferenceCallbackKind::Unmount:
            on_unmount(record);
            return;
        case ReferenceCallbackKind::Report:
            on_report(record, now_us);
            return;
        case ReferenceCallbackKind::Overflow:
            on_overflow();
            return;
    }
}

bool ReferenceSourceAdapter::take_protocol_request(ProtocolRequest& request) {
    if (protocol_request_count_ == 0) {
        return false;
    }
    request = protocol_requests_[protocol_request_head_];
    protocol_request_head_ = static_cast<std::uint8_t>(
        (protocol_request_head_ + 1) % kInterfaceCapacity);
    --protocol_request_count_;
    return true;
}

bool ReferenceSourceAdapter::take_descriptor_request(std::uint32_t now_us,
                                                     DescriptorRequest& request) {
    if (!descriptor_request_.active ||
        descriptor_request_.offers >= kDescriptorMaxOffers ||
        !time_reached(now_us, descriptor_request_.next_offer_us)) {
        return false;
    }

    request = descriptor_request_.request;
    ++descriptor_request_.offers;
    descriptor_request_.next_offer_us = now_us + kDescriptorOfferIntervalUs;
    if (descriptor_request_.offers >= kDescriptorMaxOffers) {
        // This last offer may still be accepted, in which case
        // descriptor_request_accepted() withdraws the give-up again.
        note_descriptor_giveup(ReferenceDescriptorReason::NoOffer);
        descriptor_request_.active = false;
    }
    return true;
}

void ReferenceSourceAdapter::descriptor_request_accepted() {
    descriptor_attempted_ = true;
    descriptor_attempted_request_ = descriptor_request_.request;
    descriptor_request_ = PendingDescriptorRequest{};
    descriptor_giveup_ = ReferenceDescriptorReason::None;
    descriptor_giveup_request_ = DescriptorRequest{};
}

void ReferenceSourceAdapter::schedule_descriptor_followup(
    std::uint32_t now_us) {
    if (!descriptor_attempted_ || descriptor_followup_armed_ ||
        descriptor_request_.active) {
        // Nothing reached the wire, the follow-up has already been armed, or
        // a request is already pending. Any of the three makes a second
        // on-wire attempt for this experiment, which the design forbids.
        return;
    }
    descriptor_followup_armed_ = true;
    descriptor_request_.active = true;
    descriptor_request_.request =
        DescriptorRequest{descriptor_attempted_request_.dev_addr,
                          descriptor_attempted_request_.instance,
                          kAulaKeyboardFollowupLength};
    descriptor_request_.next_offer_us = now_us;
    descriptor_request_.offers = 0;
}

void ReferenceSourceAdapter::note_descriptor_giveup(
    ReferenceDescriptorReason reason) {
    if (!descriptor_request_.active ||
        descriptor_giveup_ != ReferenceDescriptorReason::None) {
        // Nothing was scheduled, or this attempt has already named its reason.
        // One line per attempt: a give-up repeated every pass is noise that
        // buries the measurement it is meant to explain.
        return;
    }
    descriptor_giveup_ = reason;
    descriptor_giveup_request_ = descriptor_request_.request;
}

bool ReferenceSourceAdapter::take_descriptor_giveup(
    ReferenceDescriptorReason& reason, DescriptorRequest& request) {
    if (descriptor_giveup_ == ReferenceDescriptorReason::None) {
        return false;
    }
    reason = descriptor_giveup_;
    request = descriptor_giveup_request_;
    descriptor_giveup_ = ReferenceDescriptorReason::None;
    descriptor_giveup_request_ = DescriptorRequest{};
    return true;
}

void ReferenceSourceAdapter::abandon_descriptor_request(
    ReferenceDescriptorReason reason, const DescriptorRequest& request) {
    descriptor_giveup_ = reason;
    descriptor_giveup_request_ = request;
    descriptor_request_ = PendingDescriptorRequest{};
}

void ReferenceSourceAdapter::cancel_descriptor_request(std::uint8_t dev_addr,
                                                       std::uint8_t instance) {
    if (descriptor_request_.active &&
        descriptor_request_.request.dev_addr == dev_addr &&
        descriptor_request_.request.instance == instance) {
        note_descriptor_giveup(ReferenceDescriptorReason::Unmounted);
        descriptor_request_ = PendingDescriptorRequest{};
    }
}

bool ReferenceSourceAdapter::take_event(SourceEvent& event,
                                        SourceIdentity& identity) {
    if (pending_count_ == 0) {
        return false;
    }
    const Pending& slot = pending_[pending_head_];
    event = slot.event;
    identity = slot.identity;
    pending_head_ = static_cast<std::uint8_t>((pending_head_ + 1) % kPendingCapacity);
    --pending_count_;
    return true;
}

}  // namespace duo_input::u1::reference
