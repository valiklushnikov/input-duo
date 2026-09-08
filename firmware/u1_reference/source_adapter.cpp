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
            entry.source_id = static_cast<std::uint8_t>(&entry - interfaces_);
            entry.role = Role::Ignored;
            return &entry;
        }
    }
    // The table is full. An interface that cannot be assigned a source slot
    // cannot be announced safely.
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
        // again, and no record produces more events than there are sources.
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
    identity.vendor_id = record.vid;
    identity.product_id = record.pid;
    identity.interface_number = record.instance;

    Interface* const existing = find(record.dev_addr, record.instance);
    Interface* entry = claim_slot(record.dev_addr, record.instance);
    if (entry == nullptr) {
        return;
    }

    // A descriptor layout describes report protocol, so the interface has to
    // be moved there for it to describe anything at all. An interface that
    // fell back to the boot layout must stay where it is: moving it would make
    // the device send a format nothing here knows how to read.
    //
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
        entry->identity = identity;
        request_protocol(record.dev_addr, record.instance, kHidProtocolReport);
        push(SourceEventKind::Detached, entry->source_id, boot_identity);
        push(SourceEventKind::Ready, entry->source_id, identity);
        return;
    }
    if (existing != nullptr) {
        // A late descriptor may only refine the role this exact interface
        // already owns. A truncated or unrelated document must not turn an
        // existing keyboard into a mouse while leaving keyboard_owned_ set.
        return;
    }

    entry->identity = identity;

    if (!classified || identity.kind == DeviceKind::Unknown) {
        entry->role = Role::Ignored;
        ++ignored_interface_count_;
        push(SourceEventKind::Ready, entry->source_id, identity);
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
        push(SourceEventKind::Ready, entry->source_id, identity);
        return;
    }

    if (identity.kind == DeviceKind::Mouse && !mouse_owned_) {
        if (wants_report_protocol) {
            request_protocol(record.dev_addr, record.instance, kHidProtocolReport);
        }
        entry->role = Role::Mouse;
        mouse_owned_ = true;
        mouse_identity_ = identity;
        push(SourceEventKind::Ready, entry->source_id, identity);
        return;
    }

    // A second claimant for a role that is already taken. Accepted onto the
    // bus and ignored, deterministically, rather than displacing the device
    // that is already routing.
    entry->role = Role::Ignored;
    ++ignored_interface_count_;
    push(SourceEventKind::Ready, entry->source_id, identity);
}

void ReferenceSourceAdapter::on_unmount(const ReferenceCallbackRecord& record) {
    cancel_protocol_requests(record.dev_addr, record.instance);
    cancel_descriptor_request(record.dev_addr, record.instance);
    Interface* entry = find(record.dev_addr, record.instance);
    if (entry == nullptr) {
        return;
    }

    const Role role = entry->role;
    const std::uint8_t source_id = entry->source_id;
    const SourceIdentity identity = entry->identity;
    entry->used = false;
    entry->role = Role::Ignored;

    if (role == Role::Keyboard && keyboard_owned_) {
        // Announced before the identity is cleared: what the pipeline releases
        // it releases as this device, not as an anonymous one.
        push(SourceEventKind::Detached, source_id, identity);
        keyboard_owned_ = false;
        keyboard_identity_ = SourceIdentity{};
        return;
    }

    if (role == Role::Mouse && mouse_owned_) {
        push(SourceEventKind::Detached, source_id, identity);
        mouse_owned_ = false;
        mouse_identity_ = SourceIdentity{};
        return;
    }

    push(SourceEventKind::Detached, source_id, identity);
}

void ReferenceSourceAdapter::on_report(const ReferenceCallbackRecord& record,
                                       std::uint32_t now_us) {
    Interface* entry = find(record.dev_addr, record.instance);
    if (entry == nullptr) {
        return;
    }

    const std::uint32_t received =
        record.received_us != 0 ? record.received_us : now_us;

    push(SourceEventKind::Report, entry->source_id, entry->identity,
         record.instance, record.report.data(), record.report_size, received);
}

void ReferenceSourceAdapter::on_overflow() {
    // Input was handed to this firmware and not kept, so nothing downstream
    // can still be trusted to know what is held. Every mounted source is told
    // before its identity is cleared.
    for (Interface& entry : interfaces_) {
        if (entry.used) {
            push(SourceEventKind::Fault, entry.source_id, entry.identity);
        }
        entry = Interface{};
    }
    keyboard_owned_ = false;
    mouse_owned_ = false;
    keyboard_identity_ = SourceIdentity{};
    mouse_identity_ = SourceIdentity{};
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
