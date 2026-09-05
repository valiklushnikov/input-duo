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

std::size_t detach_slot(LogicalRole role) {
    return role == LogicalRole::Keyboard ? 0u : 1u;
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
                                       std::uint16_t descriptor_size) {
    CallbackRecord record;
    record.kind = CallbackKind::HidMount;
    record.dev_addr = dev_addr;
    record.instance = instance;
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

void DeviceRegistry::arm_if_needed(Interface& interface) {
    if (!interface.mounted || interface.fault_pending || interface.faulted ||
        interface.report_in_flight) {
        return;
    }
    if (tuh_hid_receive_report(interface.dev_addr, interface.instance)) {
        interface.report_in_flight = true;
    } else {
        ++arm_failures_;
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
    if (interface.role != LogicalRole::Ignored && !was_already_faulted) {
        if (!push_event(interface, input::SourceEventKind::Fault, 0, nullptr, 0, 0)) {
            ++event_overflows_;
        }
    }
}

void DeviceRegistry::remove_device(std::uint8_t dev_addr) {
    for (Device& device : devices_) {
        if (device.mounted && device.dev_addr == dev_addr) {
            device = {};
        }
    }
    for (Interface& interface : interfaces_) {
        if (!interface.mounted || interface.dev_addr != dev_addr) {
            continue;
        }
        if (interface.role != LogicalRole::Ignored) {
            PendingEvent& pending = detach_events_[detach_slot(interface.role)];
            if (!pending.present) {
                pending.present = true;
                pending.event = {};
                pending.event.kind = input::SourceEventKind::Detached;
                pending.event.source_id = interface.dev_addr;
                pending.identity = interface.identity;
            }
        }
        interface = {};
    }
}

bool DeviceRegistry::push_event(const Interface& interface, input::SourceEventKind kind,
                                std::uint8_t endpoint, const std::uint8_t* report,
                                std::size_t report_size, std::uint32_t received_us) {
    // Fault keeps the last kFaultReservedSlots for itself - one per role-owned
    // interface V1 accepts, not one slot in total. One reserved slot only
    // protects whichever of the keyboard and the mouse overflows first: fill
    // to capacity-1 with ordinary traffic, fault the keyboard (its Fault
    // spends the single reserved slot), and the mouse's next report then finds
    // the queue full, latches its own Fault into a queue with nothing left,
    // and loses it. That lost Fault is the mouse's whole release-all - a
    // dropped release, which this queue exists to make impossible - and the
    // mouse interface is faulted afterwards, so nothing will ever produce it
    // again. Reserving one per role means every Ready/Report push here refuses
    // two slots early and both sources' overflow Faults, each pushed straight
    // after its own push already failed, always have room.
    //
    // The eighteen slots this leaves ordinary traffic are still far more than
    // a genuine pass can use - about ten at worst; the derivation is on
    // kEventQueueCapacity in the header.
    const std::size_t capacity = kind == input::SourceEventKind::Fault
                                     ? kEventQueueCapacity
                                     : kEventQueueCapacity - kFaultReservedSlots;
    if (event_count_ >= capacity) {
        return false;
    }
    const std::size_t index = (event_head_ + event_count_) % kEventQueueCapacity;
    PendingEvent& slot = event_queue_[index];
    slot.present = true;
    slot.event = input::SourceEvent{};
    slot.event.kind = kind;
    slot.event.source_id = interface.dev_addr;
    slot.event.endpoint = endpoint;
    slot.event.received_us = received_us;
    if (report != nullptr && report_size != 0) {
        std::memcpy(slot.event.report, report, report_size);
    }
    slot.event.report_size = report_size;
    slot.identity = interface.identity;
    ++event_count_;
    return true;
}

bool DeviceRegistry::pop_event(input::SourceEvent& event, input::SourceIdentity& identity) {
    if (event_count_ == 0) {
        return false;
    }
    PendingEvent& slot = event_queue_[event_head_];
    event = slot.event;
    identity = slot.identity;
    slot = PendingEvent{};
    event_head_ = (event_head_ + 1) % kEventQueueCapacity;
    --event_count_;
    return true;
}

void DeviceRegistry::process(const CallbackRecord& record) {
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
            arm_if_needed(*interface);
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
        interface->interface_protocol = record.interface_protocol;
        interface->descriptor_present = record.payload_present;
        const bool classified = classify_hid(
            record.interface_protocol,
            record.payload_present ? record.payload : nullptr,
            record.payload_present ? record.size : 0,
            interface->identity);
        interface->identity.vendor_id = record.vendor_id;
        interface->identity.product_id = record.product_id;

        const LogicalRole wanted =
            classified ? role_for_kind(interface->identity.kind) : LogicalRole::Ignored;
        if (wanted != LogicalRole::Ignored && !role_is_owned(wanted)) {
            interface->role = wanted;
        } else {
            interface->role = LogicalRole::Ignored;
            ++ignored_interfaces_;
        }
        if (interface->role != LogicalRole::Ignored) {
            // Told once, before its first Report: InputPipeline::on_event
            // reads Ready to learn what this source is and which layout to
            // read its reports through, and a Report ahead of that would be
            // read under whatever the pipeline was left holding from before.
            if (!push_event(*interface, input::SourceEventKind::Ready, 0, nullptr, 0, 0)) {
                ++event_overflows_;
                latch_fault(*interface);
                return;
            }
        }
        arm_if_needed(*interface);
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
    if (interface->role == LogicalRole::Ignored) {
        // Serviced so a second, unrouted interface on the same device cannot
        // stall the bus behind an un-drained endpoint - never turned into an
        // event, because nothing above this line would know which owner's
        // stream it belonged to.
        arm_if_needed(*interface);
        return;
    }
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
    arm_if_needed(*interface);
}

void DeviceRegistry::process_pending() {
    CallbackRecord record;
    while (pop(record)) {
        process(record);
    }
}

bool DeviceRegistry::take_event(input::SourceEvent& event,
                                input::SourceIdentity& identity) {
    if (host_fault_pending_) {
        host_fault_pending_ = false;
        event = {};
        event.kind = input::SourceEventKind::Fault;
        identity = {};
        return true;
    }
    for (PendingEvent& pending : detach_events_) {
        if (pending.present) {
            event = pending.event;
            identity = pending.identity;
            pending = {};
            return true;
        }
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
