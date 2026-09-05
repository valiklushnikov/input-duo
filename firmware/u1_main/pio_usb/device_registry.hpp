#pragma once

#include <cstddef>
#include <cstdint>

#include "input/source.hpp"

namespace duo_input::u1::pio_usb {

enum class LogicalRole : std::uint8_t {
    Ignored,
    Keyboard,
    Mouse,
    /// The Keychron M3 receiver's side-button channel: a second interface on
    /// the same physical device as a Mouse-role interface, shaped like a
    /// keyboard but never granted the Keyboard role (hid_setup.hpp's
    /// is_keychron_auxiliary_interface gates this on VID/PID, not shape
    /// alone). Its reports are queued as AuxiliaryReport, tagged with the
    /// neutral identity's kind overridden to Mouse so they reach the same
    /// InputPipeline instance the receiver's own mouse interface does -
    /// never as Report, and never counted as this device's Keyboard.
    Auxiliary,
};

class DeviceRegistry {
public:
    static constexpr std::size_t kDownstreamDeviceCapacity = 4;
    /// An average sizing assumption for kInterfaceCapacity below, not a
    /// per-device cap the code enforces anywhere - interfaces_ is one flat
    /// pool shared across every downstream device, searched by (dev_addr,
    /// instance), with no per-device limit checked on the way in. The
    /// Keychron M3 receiver's real topology is three interfaces on one
    /// device - mouse (instance 0), a vendor-neutral HID interface neither
    /// classify_hid nor is_keychron_auxiliary_interface gives a role to
    /// (instance 1, LogicalRole::Ignored), and the auxiliary keyboard-shaped
    /// channel (instance 2) - not two, so this constant already understates
    /// that one device. It still costs no reachable headroom: the flat pool
    /// is eight slots total, this receiver alone uses three of them, and V1
    /// only ever needs one more role-bearing interface (a second keyboard, or
    /// a lone mouse instead of this receiver) to reach its two accepted
    /// roles - four slots against eight available, even before whatever a
    /// fourth downstream device's own single interface would add.
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
    // Ready, Report, AuxiliaryReport and interface-level Fault are each
    // produced by processing exactly one CallbackRecord (HidMount, Report, or
    // ReportFault respectively) - never more than one SourceEvent per record -
    // and process_pending() drains at most kCallbackQueueCapacity records per
    // call, so 20 is the arithmetic ceiling on what one pass can queue here.
    //
    // push_event() does not offer all 20 to ordinary traffic: it holds the
    // last kFaultReservedSlots back for Fault, one per independently-arming
    // role-bearing interface V1 accepts (Keyboard, Mouse, and the Keychron
    // receiver's Auxiliary interface), so every one of them can still queue
    // its owed release-all even when all overflow inside the same undrained
    // window - see push_event()'s own comment. That leaves 17 for
    // Ready/Report/AuxiliaryReport, which a genuine pass still cannot reach:
    //
    //   * Report/AuxiliaryReport: only a role-owned interface produces one,
    //     at most three interfaces are ever role-owned at a time (Keyboard,
    //     Mouse, Auxiliary), and each holds at most one receive in flight -
    //     at most 3 per pass.
    //   * Ready: only Keyboard and Mouse ever produce one - Auxiliary never
    //     does, it is not a source InputPipeline is told about on its own -
    //     and a role is claimed once; role_is_owned() refuses a second
    //     claimant, so a third Ready inside one pass has to spend a
    //     DeviceUnmount record first to free the role again. Alternating
    //     HidMount/DeviceUnmount across the whole 20-record budget is the
    //     worst case and yields 10.
    //   * Fault comes out of the reserved slots, not out of these.
    //
    // Thirteen at worst against seventeen offered, so the reservation costs no
    // reachable headroom. The only way to reach the overflow path exercised
    // in tests is to withhold draining across many passes, the same way Task
    // 6's callback-queue overflow tests withhold processing.
    static constexpr std::size_t kEventQueueCapacity = kCallbackQueueCapacity;
    /// How many of kEventQueueCapacity are kept back for Fault: one per
    /// independently-arming role-bearing interface V1 accepts (Keyboard,
    /// Mouse, Auxiliary). Ready/Report/AuxiliaryReport are offered
    /// kEventQueueCapacity minus this.
    ///
    /// Reserving fewer than one per interface is not safe to reason about by
    /// counting distinct downstream pipelines instead (Mouse and Auxiliary
    /// both target the mouse's InputPipeline, so it can look as though one
    /// shared slot would do): push_event()'s processing order is FIFO over
    /// whichever callback records arrived, not grouped by pipeline, so an
    /// adversarial order can spend both of two reserved slots on Mouse and
    /// Auxiliary's redundant Faults before Keyboard's own ever reaches the
    /// queue, losing a release that has nothing to do with either of them.
    /// One slot per interface removes the ordering dependency entirely.
    static constexpr std::size_t kFaultReservedSlots = 3;
    static_assert(kEventQueueCapacity > kFaultReservedSlots,
                  "the queue must leave room for ordinary Ready/Report traffic");

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
    /// ``captured_us`` is the moment TinyUSB's own callback handed this
    /// report over - read by the callback itself, before this call, so it
    /// names when the report actually arrived rather than when this pass
    /// happened to get around to processing it.
    bool capture_report(std::uint8_t dev_addr, std::uint8_t instance,
                        const std::uint8_t* report, std::uint16_t report_size,
                        std::uint32_t captured_us);

    /// Process every queued callback record. A Report's received_us comes
    /// from the CallbackRecord itself - captured at the callback, not here -
    /// so this takes no clock of its own to stamp anything with.
    void process_pending();
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
        // The callback's own capture time. Zero (and unread) for every kind
        // but Report - HidMount/Report/Fault SourceEvents that are not
        // reports carry received_us == 0 regardless, per source.hpp.
        std::uint32_t received_us = 0;
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
    /// Whether some other mounted interface on ``dev_addr`` classify_hid
    /// found to be a mouse - the Keychron receiver's own mouse channel, which
    /// its auxiliary channel is associated with. Read from identity.kind, not
    /// from LogicalRole::Mouse: a competing mouse elsewhere can win
    /// role_is_owned(Mouse) and leave this receiver's own mouse interface
    /// LogicalRole::Ignored while classify_hid's verdict on it is still
    /// Mouse, and gating on the role would then read as "no sibling" and let
    /// the auxiliary channel win the Keyboard role instead - the exact
    /// pre-task defect, just reachable through a second mouse rather than
    /// through no mouse at all. Without this check at all (either form), a
    /// lone keyboard-shaped interface that merely happens to report this
    /// receiver's vendor/product - no mouse sibling ever mounted on the same
    /// device - would be pulled out of the Keyboard role it should still be
    /// free to earn.
    bool has_mouse_sibling(std::uint8_t dev_addr) const;
    void arm_if_needed(Interface& interface);
    void latch_fault(Interface& interface);
    void remove_device(std::uint8_t dev_addr);
    void process(const CallbackRecord& record);
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
