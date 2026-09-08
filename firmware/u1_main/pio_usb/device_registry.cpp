#include "pio_usb/device_registry.hpp"

#include <cstring>

#include "pio_usb/hid_setup.hpp"

extern "C" bool tuh_hid_receive_report(std::uint8_t dev_addr, std::uint8_t instance);

namespace duo_input::u1::pio_usb {
namespace {

LogicalRole role_for_kind(input::DeviceKind kind) {
    if (kind == input::DeviceKind::Keyboard) {
        return LogicalRole::Keyboard;
    }
    if (kind == input::DeviceKind::Mouse) {
        return LogicalRole::Mouse;
    }
    return LogicalRole::Ignored;
}

}  // namespace

void DeviceRegistry::record_host_initialization(bool configure_succeeded,
                                                bool init_succeeded) {
    if (!configure_succeeded || !init_succeeded) {
        host_fault_pending_ = true;
    }
}

bool DeviceRegistry::push(const CallbackRecord& record) {
    if (callback_count_ == kCallbackQueueCapacity) {
        return false;
    }
    const std::size_t index = (callback_head_ + callback_count_) % kCallbackQueueCapacity;
    callbacks_[index] = record;
    ++callback_count_;
    return true;
}

bool DeviceRegistry::pop(CallbackRecord& record) {
    if (callback_count_ == 0) {
        return false;
    }
    record = callbacks_[callback_head_];
    callback_head_ = (callback_head_ + 1) % kCallbackQueueCapacity;
    --callback_count_;
    return true;
}

bool DeviceRegistry::capture_device_mount(std::uint8_t dev_addr, std::uint16_t vendor_id,
                                          std::uint16_t product_id) {
    CallbackRecord record;
    record.kind = CallbackKind::DeviceMount;
    record.dev_addr = dev_addr;
    record.vendor_id = vendor_id;
    record.product_id = product_id;
    if (push(record)) {
        return true;
    }
    ++callback_overflows_;
    return false;
}

bool DeviceRegistry::capture_unmount(std::uint8_t dev_addr) {
    CallbackRecord record;
    record.kind = CallbackKind::DeviceUnmount;
    record.dev_addr = dev_addr;
    if (push(record)) {
        return true;
    }

    ++callback_overflows_;
    for (Interface& interface : interfaces_) {
        if (interface.mounted && interface.dev_addr == dev_addr) {
            latch_fault(interface);
        }
    }
    return false;
}

bool DeviceRegistry::capture_hid_mount(std::uint8_t dev_addr, std::uint8_t instance,
                                       std::uint16_t vendor_id, std::uint16_t product_id,
                                       std::uint8_t interface_protocol,
                                       const std::uint8_t* descriptor,
                                       std::uint16_t descriptor_size,
                                       std::uint8_t interface_number) {
    CallbackRecord record;
    record.kind = CallbackKind::HidMount;
    record.dev_addr = dev_addr;
    record.instance = instance;
    record.interface_number = interface_number;
    record.vendor_id = vendor_id;
    record.product_id = product_id;
    record.interface_protocol = interface_protocol;
    if (descriptor != nullptr && descriptor_size != 0 &&
        descriptor_size <= kMaxDescriptorBytes) {
        record.payload_present = true;
        record.size = descriptor_size;
        std::memcpy(record.payload, descriptor, descriptor_size);
    }
    if (push(record)) {
        return true;
    }
    ++callback_overflows_;
    return false;
}

bool DeviceRegistry::capture_report(std::uint8_t dev_addr, std::uint8_t instance,
                                    const std::uint8_t* report,
                                    std::uint16_t report_size,
                                    std::uint32_t captured_us) {
    Interface* interface = find_mutable(dev_addr, instance);
    if (interface == nullptr || !interface->report_in_flight || interface->fault_pending ||
        interface->faulted) {
        return false;
    }

    CallbackRecord record;
    record.kind = report_size > input::kMaxSourceReportBytes ||
                          (report == nullptr && report_size != 0)
                      ? CallbackKind::ReportFault
                      : CallbackKind::Report;
    record.dev_addr = dev_addr;
    record.instance = instance;
    record.size = record.kind == CallbackKind::Report ? report_size : 0;
    // Carried even on a ReportFault record: it costs nothing to keep and
    // nothing reads it there, since a Fault SourceEvent's received_us is
    // always zero regardless of when the oversized report arrived.
    record.received_us = captured_us;
    if (record.size != 0) {
        record.payload_present = true;
        std::memcpy(record.payload, report, record.size);
    }

    if (!push(record)) {
        ++callback_overflows_;
        // The transfer has physically completed, but keeping this logical
        // flag set prevents any duplicate arm while the owed Fault waits.
        latch_fault(*interface);
        return false;
    }

    // An accepted fatal record blocks later mount/report processing before it
    // reaches the queue head. Only then may this receive cease to be in flight.
    if (record.kind == CallbackKind::ReportFault) {
        interface->fault_pending = true;
    }
    interface->report_in_flight = false;
    return true;
}

DeviceRegistry::Device* DeviceRegistry::ensure_device(std::uint8_t dev_addr,
                                                      std::uint16_t vendor_id,
                                                      std::uint16_t product_id) {
    for (Device& device : devices_) {
        if (device.mounted && device.dev_addr == dev_addr) {
            device.vendor_id = vendor_id;
            device.product_id = product_id;
            return &device;
        }
    }
    for (Device& device : devices_) {
        if (!device.mounted) {
            device = Device{true, dev_addr, vendor_id, product_id};
            return &device;
        }
    }
    ++device_overflows_;
    return nullptr;
}

DeviceRegistry::Interface* DeviceRegistry::find_mutable(std::uint8_t dev_addr,
                                                        std::uint8_t instance) {
    for (Interface& interface : interfaces_) {
        if (interface.mounted && interface.dev_addr == dev_addr &&
            interface.instance == instance) {
            return &interface;
        }
    }
    return nullptr;
}

const DeviceRegistry::Interface* DeviceRegistry::find(std::uint8_t dev_addr,
                                                      std::uint8_t instance) const {
    for (const Interface& interface : interfaces_) {
        if (interface.mounted && interface.dev_addr == dev_addr &&
            interface.instance == instance) {
            return &interface;
        }
    }
    return nullptr;
}

bool DeviceRegistry::role_is_owned(LogicalRole role) const {
    if (role == LogicalRole::Ignored) {
        return false;
    }
    for (const Interface& interface : interfaces_) {
        if (interface.mounted && interface.role == role) {
            return true;
        }
    }
    return false;
}

const DeviceRegistry::Interface* DeviceRegistry::owner(input::DeviceKind kind) const {
    const LogicalRole role = kind == input::DeviceKind::Keyboard
                                 ? LogicalRole::Keyboard
                                 : kind == input::DeviceKind::Mouse ? LogicalRole::Mouse
                                                                    : LogicalRole::Ignored;
    if (role == LogicalRole::Ignored) {
        return nullptr;
    }
    for (const Interface& interface : interfaces_) {
        if (interface.mounted && interface.role == role) {
            return &interface;
        }
    }
    return nullptr;
}

void DeviceRegistry::arm_if_needed(Interface& interface, std::uint32_t now_us) {
    if (!interface.mounted || interface.fault_pending || interface.faulted ||
        interface.report_in_flight) {
        return;
    }
    if (tuh_hid_receive_report(interface.dev_addr, interface.instance)) {
        interface.report_in_flight = true;
        interface.arm_retry_pending = false;
        return;
    }
    ++arm_failures_;
    if (interface.arm_retry_count >= kMaxArmRetries) {
        // Bounded retry is exhausted. Spinning further would be the
        // callback-that-performs-routing-work problem's cousin: an endpoint
        // this firmware keeps re-asking never yields a report either way, so
        // escalate to the same release-all every other terminal path uses
        // and stop. Recovery from here on is TinyUSB's own re-enumeration -
        // a real unmount/remount - not a bus reset issued from here, which
        // would also drop this role's independently-arming sibling (the
        // brief's "keyboard and mouse recover independently" requirement).
        interface.arm_retry_pending = false;
        ++arm_escalations_;
        latch_fault(interface);
        return;
    }
    interface.arm_retry_pending = true;
    // Computed from the caller's own clock reading, not from a second
    // time_us_32() call here: retry_pending_arms() compares this deadline
    // against the reading PioUsbBackend::task() was handed, and two
    // independent clocks make the guard that compares them untestable - a
    // deadline armed on one clock and checked against the other is already
    // in the past the first time it is looked at.
    interface.arm_retry_deadline_us = now_us + kArmRetryBackoffUs[interface.arm_retry_count];
    ++interface.arm_retry_count;
}

void DeviceRegistry::retry_pending_arms(std::uint32_t now_us) {
    // Deliberately does not chase an interface whose receive is still
    // report_in_flight with nothing back yet: for a healthy HID device that
    // is simply idle (nothing pressed, nothing moved), that is the entire,
    // indefinitely-long normal state, and a timeout here would fault a
    // perfectly working keyboard for the crime of nobody typing on it. Real
    // TinyUSB's hidh_xfer_cb forwards xferred_bytes to
    // tuh_hid_report_received_cb regardless of xfer_result, so a
    // stalled/errored transfer DOES complete - with a near-empty report,
    // not silence - which process()'s Report handling below catches by
    // size instead.
    for (Interface& interface : interfaces_) {
        if (!interface.mounted || interface.fault_pending || interface.faulted ||
            interface.report_in_flight) {
            continue;
        }
        // Wrap-safe, the same shape main.cpp's own busy-wait loops use
        // (time_us_32() - started < for_us): time_us_32() wraps every ~71.6
        // minutes, and a deadline computed across that wrap compares wrong
        // under a plain >= in both directions - it either fires immediately,
        // skipping the backoff entirely, or defers the retry by up to 71
        // minutes, leaving the interface un-armed, un-escalated and its held
        // keys never released. The signed difference is right either side of
        // the wrap as long as the real interval is under ~35 minutes, which
        // kArmRetryBackoffUs (16 ms at most) is by five orders of magnitude.
        if (interface.arm_retry_pending &&
            static_cast<std::int32_t>(now_us - interface.arm_retry_deadline_us) >= 0) {
            arm_if_needed(interface, now_us);
        }
    }
}

void DeviceRegistry::latch_fault(Interface& interface) {
    interface.fault_pending = false;
    const bool was_already_faulted = interface.faulted;
    interface.faulted = true;
    // Once, not on every call: capture_unmount's callback-queue-overflow
    // fallback loops over every interface on a device and can reach one that
    // a Report path already faulted. A second Fault would only make
    // InputPipeline's release_all a no-op the second time - harmless - but
    // still spends a slot this interface's own stream may still need before
    // Task 10's recovery gives it a fresh generation.
    if (!was_already_faulted) {
        if (!push_event(interface, input::SourceEventKind::Fault, 0, nullptr, 0, 0)) {
            ++event_overflows_;
        }
        // The source remains mounted but is no longer a diagnostic role owner.
        // A duplicate mount cannot reclaim it: that path only tries to re-arm,
        // and arm_if_needed() refuses a faulted interface.
        interface.role = LogicalRole::Ignored;
    }
}

void DeviceRegistry::remove_device(std::uint8_t dev_addr) {
    for (Device& device : devices_) {
        if (device.mounted && device.dev_addr == dev_addr) {
            device = {};
        }
    }
    // Each mounted interface owns its own SourceTable slot, so each needs its
    // own release before the registry forgets it.
    for (Interface& interface : interfaces_) {
        if (!interface.mounted || interface.dev_addr != dev_addr) {
            continue;
        }
        PendingEvent pending{};
        pending.event.kind = input::SourceEventKind::Detached;
        pending.event.source_id = interface.source_id;
        pending.identity = interface.identity;
        pending.generation = interface.generation;
        if (push_detach(pending)) {
            if (interface.generation > retired_generation_[interface.source_id]) {
                retired_generation_[interface.source_id] = interface.generation;
            }
            continue;
        }
        // Only reachable by withholding take_event() drains across many
        // separate process_pending() passes - see kDetachQueueCapacity, now
        // sized above the per-pass Detached ceiling. Counted AND escalated: a
        // Detached that does not fit is a release-all that would otherwise
        // never be delivered, and whatever this source was holding would stay
        // held for ever. Every other overflow path in this file escalates to
        // latch_fault() for exactly that reason, and the central constraint -
        // a dropped release is a release-all fault, not a recoverable report
        // loss - leaves this one no exemption. The Fault goes to event_queue_,
        // which reserves kFaultReservedSlots for precisely this, and carries
        // the same identity the lost Detached would have, so it reaches the
        // same pipeline and runs the same release_all.
        ++detach_overflows_;
        latch_fault(interface);
    }
    // Stop accepting reports and free layout/held state together: clearing
    // mounted here is what makes find_mutable() refuse any report already in
    // the callback queue for this interface once process() reaches it, and
    // every generation this device carried is retired above before this line
    // ever runs.
    for (Interface& interface : interfaces_) {
        if (interface.mounted && interface.dev_addr == dev_addr) {
            interface = {};
        }
    }
}

bool DeviceRegistry::push_event(const Interface& interface, input::SourceEventKind kind,
                                std::uint8_t endpoint, const std::uint8_t* report,
                                std::size_t report_size, std::uint32_t received_us) {
    // Ready and Report can use the callback-pass-sized ordinary portion.
    // Fault may also use the final per-interface reservation so a failed push
    // can still be followed by the source's terminal release-all.
    const std::size_t capacity = kind == input::SourceEventKind::Fault
                                     ? kEventQueueCapacity
                                     : kEventQueueCapacity - kFaultReservedSlots;
    if (event_count_ >= capacity) {
        return false;
    }
    const std::size_t index = (event_head_ + event_count_) % kEventQueueCapacity;
    PendingEvent& slot = event_queue_[index];
    slot.event = input::SourceEvent{};
    slot.event.kind = kind;
    slot.event.source_id = interface.source_id;
    slot.event.endpoint = endpoint;
    slot.event.received_us = received_us;
    if (report != nullptr && report_size != 0) {
        std::memcpy(slot.event.report, report, report_size);
    }
    slot.event.report_size = report_size;
    slot.identity = interface.identity;
    slot.generation = interface.generation;
    ++event_count_;
    return true;
}

bool DeviceRegistry::push_detach(const PendingEvent& event) {
    // A dedicated bounded FIFO rather than a slot inside event_queue_: a
    // Detached must never compete with ordinary Ready/Report traffic for
    // kFaultReservedSlots capacity, and must never be reordered behind
    // whatever of the SAME generation is still sitting in that queue - see
    // pop_event()'s stale-generation discard, which is what makes that
    // reordering safe once a Detached from here has been delivered ahead of
    // it.
    if (detach_count_ == kDetachQueueCapacity) {
        return false;
    }
    const std::size_t index = (detach_head_ + detach_count_) % kDetachQueueCapacity;
    detach_events_[index] = event;
    ++detach_count_;
    return true;
}

bool DeviceRegistry::pop_detach(PendingEvent& event) {
    if (detach_count_ == 0) {
        return false;
    }
    event = detach_events_[detach_head_];
    detach_events_[detach_head_] = PendingEvent{};
    detach_head_ = (detach_head_ + 1) % kDetachQueueCapacity;
    --detach_count_;
    return true;
}

bool DeviceRegistry::pop_event(input::SourceEvent& event, input::SourceIdentity& identity) {
    while (event_count_ != 0) {
        PendingEvent& slot = event_queue_[event_head_];
        // Ready/Report from a generation whose Detached has
        // already been delivered are stale: a Detached is delivered ahead of
        // this queue (take_event() drains detach_events_ first), so by the
        // time one of these is reached here its own release has already run
        // and delivering it now would either be a no-op read against an
        // already-torn-down pipeline or - if a newer generation has since
        // claimed the same source slot - misrouted into that NEW device's state.
        // Fault/Detached themselves are never filtered: this file's own
        // ordering keeps a Fault self-consistent with whatever of the same
        // generation precedes it (Fault shares this same queue, so anything
        // still ahead of it here genuinely arrived first), and Detached
        // never reaches this queue at all - see push_detach().
        const bool filterable = slot.event.kind == input::SourceEventKind::Ready ||
                                 slot.event.kind == input::SourceEventKind::Report;
        const std::size_t source_slot = slot.event.source_id;
        if (filterable && slot.generation != 0 &&
            slot.generation <= retired_generation_[source_slot]) {
            ++stale_events_discarded_;
            slot = PendingEvent{};
            event_head_ = (event_head_ + 1) % kEventQueueCapacity;
            --event_count_;
            continue;
        }
        event = slot.event;
        identity = slot.identity;
        slot = PendingEvent{};
        event_head_ = (event_head_ + 1) % kEventQueueCapacity;
        --event_count_;
        return true;
    }
    return false;
}

void DeviceRegistry::process(const CallbackRecord& record, std::uint32_t now_us) {
    if (record.kind == CallbackKind::DeviceMount) {
        ensure_device(record.dev_addr, record.vendor_id, record.product_id);
        return;
    }
    if (record.kind == CallbackKind::DeviceUnmount) {
        remove_device(record.dev_addr);
        return;
    }
    if (record.kind == CallbackKind::HidMount) {
        if (ensure_device(record.dev_addr, record.vendor_id, record.product_id) == nullptr) {
            return;
        }
        Interface* interface = find_mutable(record.dev_addr, record.instance);
        if (interface != nullptr) {
            ++duplicate_mounts_;
            arm_if_needed(*interface, now_us);
            return;
        }
        for (Interface& candidate : interfaces_) {
            if (!candidate.mounted) {
                interface = &candidate;
                break;
            }
        }
        if (interface == nullptr) {
            ++interface_overflows_;
            return;
        }

        *interface = {};
        interface->mounted = true;
        interface->dev_addr = record.dev_addr;
        interface->instance = record.instance;
        interface->source_id = static_cast<std::uint8_t>(interface - interfaces_);
        interface->interface_protocol = record.interface_protocol;
        interface->descriptor_present = record.payload_present;
        interface->descriptor_bytes = record.payload_present ? record.size : 0;
        // Assigned once, here, from the registry-wide monotonic counter -
        // never on the duplicate-mount branch above, which re-arms the same
        // still-mounted interface rather than claiming a fresh one. This is
        // what lets pop_event() tell "this source slot's current occupant"
        // apart from whatever an older, already-detached occupant of the
        // same slot left queued.
        interface->generation = ++next_generation_;
        const bool classified = classify_hid(
            record.interface_protocol,
            record.payload_present ? record.payload : nullptr,
            record.payload_present ? record.size : 0,
            interface->identity);
        interface->identity.vendor_id = record.vendor_id;
        interface->identity.product_id = record.product_id;
        interface->identity.interface_number = record.interface_number;
        interface->identity.device_address = record.dev_addr;

        const LogicalRole wanted =
            classified ? role_for_kind(interface->identity.kind)
                       : LogicalRole::Ignored;

        if (wanted != LogicalRole::Ignored && !role_is_owned(wanted)) {
            interface->role = wanted;
        } else {
            interface->role = LogicalRole::Ignored;
            ++ignored_interfaces_;
            if (wanted != LogicalRole::Ignored) {
                // Accepted as a source, while the first interface retains
                // ownership of the legacy per-kind diagnostic summary.
                ++ignored_role_taken_;
            }
        }
        // Told once, before its first Report. Unknown layouts still claim a
        // source slot: their pipeline intentionally decodes nothing, while
        // the backend continues servicing the interface.
        if (!push_event(*interface, input::SourceEventKind::Ready, 0, nullptr, 0, 0)) {
            ++event_overflows_;
            latch_fault(*interface);
            return;
        }
        arm_if_needed(*interface, now_us);
        return;
    }

    Interface* interface = find_mutable(record.dev_addr, record.instance);
    if (interface == nullptr) {
        return;
    }
    if (record.kind == CallbackKind::ReportFault) {
        latch_fault(*interface);
        return;
    }
    if (record.size == 0) {
        // A stalled or errored transfer, not an idle one (Task 10). Real
        // TinyUSB's hidh_xfer_cb forwards xferred_bytes to
        // tuh_hid_report_received_cb regardless of xfer_result, so a
        // stall/error completes with (near) zero bytes rather than never
        // completing at all - an idle, healthy device that simply has
        // nothing new to report produces no CallbackRecord whatsoever,
        // which is why this can never mistake "nobody typed anything" for a
        // fault. Never turned into a Report SourceEvent -
        // InputPipeline has no zero-length shape to read - and given the
        // same bounded-retry budget a synchronous receive-arm refusal uses:
        // repeated signals in a row escalate to a release-all, one
        // isolated signal (or one followed by a real report, which resets
        // the count below) does not.
        ++stall_signals_;
        if (interface->arm_retry_count >= kMaxArmRetries) {
            ++arm_escalations_;
            latch_fault(*interface);
            return;
        }
        ++interface->arm_retry_count;
        arm_if_needed(*interface, now_us);
        return;
    }
    // Forward progress: whatever run of arm refusals or stall signals came
    // before this real report, the interface has just proven itself
    // healthy again.
    interface->arm_retry_count = 0;
    if (!push_event(*interface, input::SourceEventKind::Report, record.instance,
                    record.payload_present ? record.payload : nullptr, record.size,
                    record.received_us)) {
        ++event_overflows_;
        // The report that did not fit is not retried and this interface is
        // not re-armed: re-arming here would ask TinyUSB for another report
        // while this one is already unaccounted for, and whatever it held is
        // exactly the owed release Fault exists to cover instead.
        latch_fault(*interface);
        return;
    }
    arm_if_needed(*interface, now_us);
}

void DeviceRegistry::process_pending(std::uint32_t now_us) {
    CallbackRecord record;
    while (pop(record)) {
        process(record, now_us);
    }
}

bool DeviceRegistry::take_event(input::SourceEvent& event,
                                input::SourceIdentity& identity) {
    // A whole-host Fault (record_host_initialization's failure path, the
    // only source of one today) has no interface and so no logical_port to
    // route through if delivered with identity.kind left Unknown - that
    // routed nowhere and released nothing, harmless only as long as this
    // could only fire from begin(), before any device could be mounted.
    // Delivered instead as two separate, properly-identified Fault events -
    // one per role slot - so PioUsbBackend::logical_port() sends each to its
    // own pipeline; releasing a pipeline that holds nothing is a no-op, so
    // the "wrong" one of the two costs nothing.
    if (host_fault_pending_) {
        host_fault_pending_ = false;
        event = {};
        event.kind = input::SourceEventKind::Fault;
        event.source_id = input::kWholeHostSource;
        identity = {};
        return true;
    }
    PendingEvent pending;
    if (pop_detach(pending)) {
        event = pending.event;
        identity = pending.identity;
        return true;
    }
    return pop_event(event, identity);
}

std::size_t DeviceRegistry::device_count() const {
    std::size_t count = 0;
    for (const Device& device : devices_) {
        count += device.mounted ? 1u : 0u;
    }
    return count;
}

std::size_t DeviceRegistry::interface_count() const {
    std::size_t count = 0;
    for (const Interface& interface : interfaces_) {
        count += interface.mounted ? 1u : 0u;
    }
    return count;
}

}  // namespace duo_input::u1::pio_usb
