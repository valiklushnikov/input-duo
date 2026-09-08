#pragma once

#include <cstddef>
#include <cstdint>

#include "input/source.hpp"

namespace duo_input::u1::pio_usb {

enum class LogicalRole : std::uint8_t {
    // Legacy diagnostic ownership only; these sources are accepted and polled.
    Ignored,
    Keyboard,
    Mouse,
};

class DeviceRegistry {
public:
    static constexpr std::size_t kDownstreamDeviceCapacity = 4;
    /// An average used only to size the flat interface pool below. It is not
    /// a per-device limit: every HID interface competes for the same eight
    /// slots and is identified by (dev_addr, instance).
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
    // Processing one callback record produces at most one Ready, Report, or
    // Fault. Ordinary traffic can therefore consume at most the twenty slots
    // in one callback pass. A further slot per interface is reserved so every
    // source can still queue its terminal Fault when drains are withheld
    // across passes and the ordinary portion is already full.
    static constexpr std::size_t kEventQueueCapacity =
        kCallbackQueueCapacity + kInterfaceCapacity;
    /// One terminal-Fault slot for every independently polled interface.
    static constexpr std::size_t kFaultReservedSlots = kInterfaceCapacity;
    static_assert(kEventQueueCapacity > kFaultReservedSlots,
                  "the queue must leave room for ordinary Ready/Report traffic");

    /// Bounded FIFO capacity for coalesced Detached releases (Task 10).
    ///
    /// One callback pass can retire at most twenty mounted interfaces. An
    /// overflow caused by withholding drains is escalated to the same
    /// terminal Fault used by every other loss path.
    static constexpr std::size_t kDetachQueueCapacity = kCallbackQueueCapacity;
    /// Bounded backoff for a receive-arm refusal, and for the shared stall-
    /// signal budget (Task 10). Three attempts, each waiting longer than the
    /// last, before escalating to a logical Fault and giving up on this
    /// interface for good - TinyUSB's own re-enumeration (a real unmount/
    /// remount) is what recovers it, not a bus reset from here, which would
    /// also drop this role's independently-arming sibling.
    static constexpr std::uint8_t kMaxArmRetries = 3;

    struct Interface {
        bool mounted = false;
        std::uint8_t dev_addr = 0;
        std::uint8_t instance = 0;
        std::uint8_t source_id = 0;
        std::uint8_t interface_protocol = 0;
        bool descriptor_present = false;
        /// How many report-descriptor bytes this interface gave up, kept
        /// because the diagnostics reply carries the length beside the hash
        /// and the callback's own buffer is long gone by then. Zero whenever
        /// descriptor_present is false - and note that a zero LENGTH and a
        /// zero HASH mean the same thing here, no descriptor was read, which
        /// is exactly what CH375's side already reports for a boot-protocol
        /// device that declined to give one up.
        std::uint16_t descriptor_bytes = 0;
        LogicalRole role = LogicalRole::Ignored;
        input::SourceIdentity identity{};
        bool report_in_flight = false;
        bool fault_pending = false;
        bool faulted = false;
        /// This source's own generation: assigned once, from a
        /// registry-wide monotonic counter, the moment a fresh HidMount
        /// claims this slot - never on a duplicate mount of the same still-
        /// mounted interface. Lets take_event() tell "this source slot's
        /// current occupant" apart from "whatever the slot's previous,
        /// already-detached occupant left queued" without caring about
        /// array-slot or dev_addr reuse. Zero means "never claimed".
        std::uint32_t generation = 0;
        /// Bounded receive-arm retry bookkeeping (Task 10). Independent per
        /// interface, so a stalled keyboard's backoff/escalation cannot
        /// perturb the mouse's, or vice versa.
        ///
        /// Shared between two distinct failure shapes that both need the
        /// same bounded-retry treatment: a synchronous
        /// tuh_hid_receive_report() refusal, and a run of zero-length
        /// completions on an otherwise-armed receive (see process()'s
        /// Report handling) - real TinyUSB's hidh_xfer_cb forwards
        /// xferred_bytes to tuh_hid_report_received_cb regardless of
        /// xfer_result, so a stalled/errored transfer completes with (at
        /// most) a handful of bytes rather than silently never completing
        /// at all. An idle, healthy endpoint produces neither: it simply
        /// does not complete until real data arrives, so nothing here ever
        /// mistakes "nothing pressed" for a stall.
        std::uint8_t arm_retry_count = 0;
        bool arm_retry_pending = false;
        std::uint32_t arm_retry_deadline_us = 0;
    };

    void record_host_initialization(bool configure_succeeded, bool init_succeeded);

    bool capture_device_mount(std::uint8_t dev_addr, std::uint16_t vendor_id,
                              std::uint16_t product_id);
    bool capture_unmount(std::uint8_t dev_addr);
    bool capture_hid_mount(std::uint8_t dev_addr, std::uint8_t instance,
                           std::uint16_t vendor_id, std::uint16_t product_id,
                           std::uint8_t interface_protocol,
                           const std::uint8_t* descriptor, std::uint16_t descriptor_size,
                           std::uint8_t interface_number = 0xFF);
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
    ///
    /// ``now_us`` is not a stamp: it is the single clock reading this whole
    /// pass judges receive-arm backoff deadlines against, threaded down from
    /// PioUsbBackend::task()'s own caller so that a deadline ARMED in this
    /// pass and the retry_pending_arms() sweep that later CHECKS it are
    /// measured against the same clock. An earlier revision read
    /// time_us_32() inside arm_if_needed() instead, which was right in
    /// production (both readings came from the same pass) and untestable
    /// everywhere else, because a test rig feeding two independent clocks
    /// puts every deadline in the past before it is ever checked.
    void process_pending(std::uint32_t now_us);
    bool take_event(input::SourceEvent& event, input::SourceIdentity& identity);

    /// Retry any interface whose receive-arm backoff has elapsed. Called
    /// once per Core 1 pass, unconditionally, the same as process_pending()
    /// - never blocks, never sleeps.
    ///
    /// ``now_us`` is the same clock reading PioUsbBackend::task() already
    /// takes from its caller, threaded through rather than re-read here so
    /// every interface in one pass is judged against the same "now".
    void retry_pending_arms(std::uint32_t now_us);

    const Interface* find(std::uint8_t dev_addr, std::uint8_t instance) const;
    const Interface* owner(input::DeviceKind kind) const;

    std::size_t device_count() const;
    std::size_t interface_count() const;
    std::uint32_t device_overflow_count() const { return device_overflows_; }
    std::uint32_t interface_overflow_count() const { return interface_overflows_; }
    std::uint32_t callback_overflow_count() const { return callback_overflows_; }
    std::uint32_t duplicate_mount_count() const { return duplicate_mounts_; }
    std::uint32_t ignored_interface_count() const { return ignored_interfaces_; }
    /// Accepted sources without diagnostic ownership because the first
    /// same-kind source already owns the summary. The remainder of the legacy
    /// ignored counter represents unknown layouts. Both cases remain polled
    /// and emit their own Ready and ordinary Report events.
    std::uint32_t ignored_role_taken_count() const { return ignored_role_taken_; }
    std::uint32_t arm_failure_count() const { return arm_failures_; }
    /// How many Ready/Report/Fault SourceEvents could not be queued because
    /// kEventQueueCapacity was already full. Only reachable by withholding
    /// take_event() drains across many passes - see kEventQueueCapacity.
    std::uint32_t event_overflow_count() const { return event_overflows_; }
    /// How many interfaces exhausted kMaxArmRetries and were escalated to a
    /// logical Fault (release-all) rather than left spinning. Task 11-
    /// nameable diagnostic.
    std::uint32_t arm_escalation_count() const { return arm_escalations_; }
    /// How many zero-length report completions were observed on an armed,
    /// interface - the stalled/errored-transfer signal real
    /// TinyUSB's hidh_xfer_cb produces (it forwards xferred_bytes to
    /// tuh_hid_report_received_cb regardless of xfer_result), counted
    /// whether or not that particular one went on to escalate. Task 11-
    /// nameable diagnostic.
    std::uint32_t stall_signal_count() const { return stall_signals_; }
    /// How many Detached releases could not be queued because
    /// kDetachQueueCapacity was already full. Only reachable by withholding
    /// take_event() drains across many whole process_pending() passes - see
    /// kDetachQueueCapacity's own derivation below.
    std::uint32_t detach_overflow_count() const { return detach_overflows_; }
    /// How many already-queued Ready/Report SourceEvents
    /// were discarded, at take_event() time, because a Detached for their
    /// own generation (or an older one sharing their source slot) had already
    /// been delivered - the "stale queued report after reconnect" case.
    std::uint32_t stale_event_discard_count() const { return stale_events_discarded_; }

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
        std::uint8_t interface_number = 0xFF;
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
        input::SourceEvent event{};
        input::SourceIdentity identity{};
        /// The generation of the interface this event was produced from.
        /// Zero for the two synthetic host-fault Fault events, which carry
        /// no interface at all.
        std::uint32_t generation = 0;
    };

    bool push(const CallbackRecord& record);
    bool pop(CallbackRecord& record);
    /// Bounded FIFO of coalesced whole-device Detached releases, separate
    /// from event_queue_ so a Detached can never be starved by ordinary
    /// Ready/Report traffic filling the shared queue - see push_detach()'s
    /// own comment for why this cannot simply share kFaultReservedSlots.
    bool push_detach(const PendingEvent& event);
    bool pop_detach(PendingEvent& event);
    Device* ensure_device(std::uint8_t dev_addr, std::uint16_t vendor_id,
                          std::uint16_t product_id);
    Interface* find_mutable(std::uint8_t dev_addr, std::uint8_t instance);
    bool role_is_owned(LogicalRole role) const;
    /// Ask TinyUSB for this interface's next report, or - if it refuses -
    /// schedule the next bounded backoff against ``now_us``. Takes the clock
    /// rather than reading one: see process_pending().
    void arm_if_needed(Interface& interface, std::uint32_t now_us);
    /// Terminal release-all for one interface: queue its Fault, then hand
    /// its role slot back so a replacement device can claim it. Idempotent -
    /// a second call on an already-faulted interface queues nothing.
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

    static constexpr std::uint32_t kArmRetryBackoffUs[kMaxArmRetries] = {1000, 4000, 16000};

    Device devices_[kDeviceCapacity] = {};
    Interface interfaces_[kInterfaceCapacity] = {};
    CallbackRecord callbacks_[kCallbackQueueCapacity] = {};
    PendingEvent detach_events_[kDetachQueueCapacity] = {};
    std::size_t detach_head_ = 0;
    std::size_t detach_count_ = 0;
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
    /// Set once a whole-host Fault is owed a second, still-undelivered
    /// SourceEvent: take_event() emits the host fault as two events - one
    /// per role slot (Keyboard, then Mouse) - since neither pipeline
    /// instance is reachable through a single Unknown-kind event, and
    /// releasing an idle pipeline is harmless. 0 = nothing owed, 2 = the
    /// Mouse-kind Fault is still owed.
    std::uint8_t host_fault_stage_ = 0;
    /// Registry-wide monotonic counter. Each fresh HidMount claim (never a
    /// duplicate mount of an already-mounted interface) is assigned the next
    /// value, so no two interfaces - even ones reusing the same array slot
    /// or the same dev_addr - ever compare equal.
    std::uint32_t next_generation_ = 0;
    /// Per source slot: the highest
    /// generation whose Detached has already been queued. Anything of that
    /// generation or older still sitting in event_queue_ is discarded at
    /// pop_event() time rather than delivered out of order behind a
    /// Detached that jumped the queue ahead of it - see pop_event()'s own
    /// comment.
    std::uint32_t retired_generation_[kInterfaceCapacity] = {};
    std::uint32_t device_overflows_ = 0;
    std::uint32_t interface_overflows_ = 0;
    std::uint32_t callback_overflows_ = 0;
    std::uint32_t duplicate_mounts_ = 0;
    std::uint32_t ignored_interfaces_ = 0;
    std::uint32_t ignored_role_taken_ = 0;
    std::uint32_t arm_failures_ = 0;
    std::uint32_t event_overflows_ = 0;
    std::uint32_t arm_escalations_ = 0;
    std::uint32_t stall_signals_ = 0;
    std::uint32_t detach_overflows_ = 0;
    std::uint32_t stale_events_discarded_ = 0;
};

void set_callback_registry(DeviceRegistry* registry) noexcept;

}  // namespace duo_input::u1::pio_usb
