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

void ReferenceSourceAdapter::on_mount(const ReferenceCallbackRecord& record) {
    SourceIdentity identity{};
    const bool classified = pio_usb::classify_hid(
        record.protocol,
        record.descriptor_size != 0 ? record.descriptor.data() : nullptr,
        record.descriptor_size, identity);
    if (!classified || identity.kind == DeviceKind::Unknown) {
        // Nothing here this firmware can read. Deliberately not given a role:
        // an interface that cannot be parsed must not keep the real device
        // that follows it from ever claiming one.
        return;
    }

    identity.vendor_id = record.vid;
    identity.product_id = record.pid;

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

    if (identity.kind == DeviceKind::Keyboard && !keyboard_owned_) {
        entry->role = Role::Keyboard;
        keyboard_owned_ = true;
        keyboard_identity_ = identity;
        push(SourceEventKind::Ready, kKeyboardPort, identity);
        return;
    }

    if (identity.kind == DeviceKind::Mouse && !mouse_owned_) {
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
}

void ReferenceSourceAdapter::consume(const ReferenceCallbackRecord& record,
                                     std::uint32_t now_us) {
    switch (record.kind) {
        case ReferenceCallbackKind::Mount:
            on_mount(record);
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
