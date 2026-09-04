#include "pio_usb/device_registry.hpp"

#include <cstring>

#include "crypto/sha256.hpp"

extern "C" bool tuh_hid_receive_report(std::uint8_t dev_addr, std::uint8_t instance);

namespace duo_input::u1::pio_usb {
namespace {

constexpr std::uint8_t kProtocolKeyboard = 1;
constexpr std::uint8_t kProtocolMouse = 2;

LogicalRole role_for_protocol(std::uint8_t protocol) {
    if (protocol == kProtocolKeyboard) {
        return LogicalRole::Keyboard;
    }
    if (protocol == kProtocolMouse) {
        return LogicalRole::Mouse;
    }
    return LogicalRole::Ignored;
}

input::DeviceKind kind_for_role(LogicalRole role) {
    if (role == LogicalRole::Keyboard) {
        return input::DeviceKind::Keyboard;
    }
    if (role == LogicalRole::Mouse) {
        return input::DeviceKind::Mouse;
    }
    return input::DeviceKind::Unknown;
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
                                    std::uint16_t report_size) {
    Interface* interface = find_mutable(dev_addr, instance);
    if (interface == nullptr || !interface->report_in_flight || interface->faulted) {
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

    // Only an accepted, bounded record advances the logical receive state.
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
    if (!interface.mounted || interface.faulted || interface.report_in_flight) {
        return;
    }
    if (tuh_hid_receive_report(interface.dev_addr, interface.instance)) {
        interface.report_in_flight = true;
    } else {
        ++arm_failures_;
    }
}

void DeviceRegistry::latch_fault(Interface& interface) {
    interface.faulted = true;
    if (interface.role != LogicalRole::Ignored) {
        interface.fault_event_pending = true;
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

        interface->mounted = true;
        interface->dev_addr = record.dev_addr;
        interface->instance = record.instance;
        interface->interface_protocol = record.interface_protocol;
        interface->descriptor_present = record.payload_present;
        interface->identity.vendor_id = record.vendor_id;
        interface->identity.product_id = record.product_id;
        if (record.payload_present) {
            crypto::sha256(record.payload, record.size, interface->identity.descriptor_hash);
        }

        const LogicalRole wanted = role_for_protocol(record.interface_protocol);
        if (wanted != LogicalRole::Ignored && !role_is_owned(wanted)) {
            interface->role = wanted;
            interface->identity.kind = kind_for_role(wanted);
            if (wanted == LogicalRole::Keyboard) {
                interface->identity.keyboard_layout = input::hid::boot_keyboard_layout();
            } else {
                interface->identity.mouse_layout = input::hid::boot_mouse_layout();
            }
        } else {
            interface->role = LogicalRole::Ignored;
            ++ignored_interfaces_;
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
    // Task 6 proves bounded capture and receive ownership. Task 8 converts
    // this accepted copy into a SourceEvent; until then it is deliberately
    // consumed here, outside the callback, and no routing code is called.
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
    for (Interface& interface : interfaces_) {
        if (interface.mounted && interface.fault_event_pending) {
            interface.fault_event_pending = false;
            event = {};
            event.kind = input::SourceEventKind::Fault;
            event.source_id = interface.dev_addr;
            identity = interface.identity;
            return true;
        }
    }
    return false;
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
