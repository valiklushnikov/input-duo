#pragma once

#include <cstddef>
#include <cstdint>

#include "input/source.hpp"

namespace duo_input::u1::pio_usb {

enum class LogicalRole : std::uint8_t {
    Ignored,
    Keyboard,
    Mouse,
};

class DeviceRegistry {
public:
    static constexpr std::size_t kDownstreamDeviceCapacity = 4;
    static constexpr std::size_t kInterfacesPerDownstreamDevice = 2;
    static constexpr std::size_t kDeviceCapacity = 1 + kDownstreamDeviceCapacity;
    static constexpr std::size_t kInterfaceCapacity =
        kDownstreamDeviceCapacity * kInterfacesPerDownstreamDevice;
    // A Core 1 pass can receive every in-flight HID report, then TinyUSB's
    // one downstream-device unmount and one HID-instance unmount per
    // interface: 8 reports + 4 device removals + 8 HID removals = 20.
    static constexpr std::size_t kCallbackQueueCapacity =
        (2 * kInterfaceCapacity) + kDownstreamDeviceCapacity;
    static constexpr std::size_t kMaxDescriptorBytes = 256;
    // Ready, Report and interface-level Fault are each produced by processing
    // exactly one CallbackRecord (HidMount, Report, or ReportFault
    // respectively) - never more than one SourceEvent per record - so sizing
    // this to kCallbackQueueCapacity guarantees a genuine single pass, which
    // drains at most that many records, can never overflow this queue. The
    // only way to reach the overflow path exercised in tests is to withhold
    // draining across many passes, the same way Task 6's callback-queue
    // overflow tests withhold processing.
    static constexpr std::size_t kEventQueueCapacity = kCallbackQueueCapacity;

    struct Interface {
        bool mounted = false;
        std::uint8_t dev_addr = 0;
        std::uint8_t instance = 0;
        std::uint8_t interface_protocol = 0;
        bool descriptor_present = false;
        LogicalRole role = LogicalRole::Ignored;
        input::SourceIdentity identity{};
        bool report_in_flight = false;
        bool fault_pending = false;
        bool faulted = false;
    };

    void record_host_initialization(bool configure_succeeded, bool init_succeeded);

    bool capture_device_mount(std::uint8_t dev_addr, std::uint16_t vendor_id,
                              std::uint16_t product_id);
    bool capture_unmount(std::uint8_t dev_addr);
    bool capture_hid_mount(std::uint8_t dev_addr, std::uint8_t instance,
                           std::uint16_t vendor_id, std::uint16_t product_id,
                           std::uint8_t interface_protocol,
                           const std::uint8_t* descriptor, std::uint16_t descriptor_size);
    bool capture_report(std::uint8_t dev_addr, std::uint8_t instance,
                        const std::uint8_t* report, std::uint16_t report_size);

    /// Process every queued callback record. ``now_us`` stamps the
    /// ``received_us`` of any Report SourceEvent this pass produces - the
    /// same per-pass granularity Ch375Device::tick's own received_us already
    /// uses, not a separate per-callback clock read.
    void process_pending(std::uint32_t now_us = 0);
    bool take_event(input::SourceEvent& event, input::SourceIdentity& identity);

    const Interface* find(std::uint8_t dev_addr, std::uint8_t instance) const;
    const Interface* owner(input::DeviceKind kind) const;

    std::size_t device_count() const;
    std::size_t interface_count() const;
    std::uint32_t device_overflow_count() const { return device_overflows_; }
    std::uint32_t interface_overflow_count() const { return interface_overflows_; }
    std::uint32_t callback_overflow_count() const { return callback_overflows_; }
    std::uint32_t duplicate_mount_count() const { return duplicate_mounts_; }
    std::uint32_t ignored_interface_count() const { return ignored_interfaces_; }
    std::uint32_t arm_failure_count() const { return arm_failures_; }
    /// How many Ready/Report/Fault SourceEvents could not be queued because
    /// kEventQueueCapacity was already full. Only reachable by withholding
    /// take_event() drains across many passes - see kEventQueueCapacity.
    std::uint32_t event_overflow_count() const { return event_overflows_; }

private:
    struct Device {
        bool mounted = false;
        std::uint8_t dev_addr = 0;
        std::uint16_t vendor_id = 0;
        std::uint16_t product_id = 0;
    };

    enum class CallbackKind : std::uint8_t {
        DeviceMount,
        DeviceUnmount,
        HidMount,
        Report,
        ReportFault,
    };

    struct CallbackRecord {
        CallbackKind kind = CallbackKind::DeviceMount;
        std::uint8_t dev_addr = 0;
        std::uint8_t instance = 0;
        std::uint8_t interface_protocol = 0;
        std::uint16_t vendor_id = 0;
        std::uint16_t product_id = 0;
        std::uint16_t size = 0;
        bool payload_present = false;
        std::uint8_t payload[kMaxDescriptorBytes] = {};
    };

    struct PendingEvent {
        bool present = false;
        input::SourceEvent event{};
        input::SourceIdentity identity{};
    };

    bool push(const CallbackRecord& record);
    bool pop(CallbackRecord& record);
    Device* ensure_device(std::uint8_t dev_addr, std::uint16_t vendor_id,
                          std::uint16_t product_id);
    Interface* find_mutable(std::uint8_t dev_addr, std::uint8_t instance);
    bool role_is_owned(LogicalRole role) const;
    void arm_if_needed(Interface& interface);
    void latch_fault(Interface& interface);
    void remove_device(std::uint8_t dev_addr);
    void process(const CallbackRecord& record, std::uint32_t now_us);
    /// Queue one Ready/Report/Fault SourceEvent for ``interface``'s own
    /// stream. False means kEventQueueCapacity was already full; the caller
    /// counts the overflow and decides what happens to the interface - this
    /// never trims or retries, matching every other bounded copy in this file.
    bool push_event(const Interface& interface, input::SourceEventKind kind,
                    std::uint8_t endpoint, const std::uint8_t* report,
                    std::size_t report_size, std::uint32_t received_us);
    bool pop_event(input::SourceEvent& event, input::SourceIdentity& identity);

    Device devices_[kDeviceCapacity] = {};
    Interface interfaces_[kInterfaceCapacity] = {};
    CallbackRecord callbacks_[kCallbackQueueCapacity] = {};
    PendingEvent detach_events_[2] = {};
    // Ready, Report and interface-level Fault, in the order they were
    // produced. Kept apart from detach_events_ above: ordering a Detached
    // callback (whole-device teardown, several interfaces at once) against
    // this queue is Task 10's "ordered teardown" job, not this one's - what
    // this queue guarantees is that a single interface's own Ready/Report/
    // Fault stream is never reordered against itself, which is what a
    // ReportFault following an already-queued Report would otherwise risk.
    PendingEvent event_queue_[kEventQueueCapacity] = {};
    std::size_t event_head_ = 0;
    std::size_t event_count_ = 0;
    std::size_t callback_head_ = 0;
    std::size_t callback_count_ = 0;
    bool host_fault_pending_ = false;
    std::uint32_t device_overflows_ = 0;
    std::uint32_t interface_overflows_ = 0;
    std::uint32_t callback_overflows_ = 0;
    std::uint32_t duplicate_mounts_ = 0;
    std::uint32_t ignored_interfaces_ = 0;
    std::uint32_t arm_failures_ = 0;
    std::uint32_t event_overflows_ = 0;
};

void set_callback_registry(DeviceRegistry* registry) noexcept;

}  // namespace duo_input::u1::pio_usb
