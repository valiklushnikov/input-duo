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
    //     claimant, so a third Ready inside one pass has to spend a whole
    //     second record freeing the role first. Two records per Ready across
    //     the 20-record budget is the worst case and yields 10.
    //   * Fault comes out of the reserved slots, not out of these.
    //
    // Thirteen at worst against seventeen offered, so the reservation costs no
    // reachable headroom. The only way to reach the overflow path exercised
    // in tests is to withhold draining across many passes, the same way Task
    // 6's callback-queue overflow tests withhold processing.
    //
    // A NOTE ON THE FAULT HALF OF THAT BOUND, corrected here rather than left
    // to drift: earlier revisions of this comment justified the reservation
    // with "at most one Fault per role-bearing interface per pass", and that
    // sentence stopped being true when latch_fault() was given a second job.
    // An escalated interface now hands its role slot back (see latch_fault()'s
    // own comment) so a replacement device can claim it, which makes a role
    // RE-CLAIMABLE INSIDE ONE PASS - so one pass can produce more than one
    // Fault per role, and counting Faults per role bounds nothing.
    //
    // kFaultReservedSlots is still enough, by an argument that does not
    // depend on that: every queued event, Fault included, is produced by
    // processing exactly one CallbackRecord, and process_pending() drains at
    // most kCallbackQueueCapacity of them per call. kEventQueueCapacity ==
    // kCallbackQueueCapacity, so once the ordinary traffic has filled the
    // 17 slots offered to it, at most three further records remain in the
    // whole budget and therefore at most three further pushes of any kind can
    // follow - which the three reserved slots hold exactly. The reservation
    // is sized to what the record budget can still deliver after saturation,
    // not to a per-role count.
    //
    // Task 10's other additions do not touch this ceiling either. Retrying a
    // receive-arm or counting stall signals produces no SourceEvent at all
    // until the interface is escalated, at which point it is faulted and
    // produces nothing further; and Detached releases draw from
    // kDetachQueueCapacity, a separate bounded FIFO, whose own overflow
    // escalation pushes a Fault through the same record budget counted above
    // and is in any case unreachable in a genuine pass, since that queue is
    // now sized above the per-pass Detached ceiling.
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

    /// Bounded FIFO capacity for coalesced Detached releases (Task 10).
    ///
    /// The first derivation of this constant said "one Detached per
    /// meaningfully-processed DeviceUnmount, so kCallbackQueueCapacity/2 =
    /// 10 mount/unmount pairs is the ceiling". That was wrong by a factor of
    /// two: remove_device() queues one Detached per ROLE SLOT, not one per
    /// call, so a composite keyboard+mouse receiver - this project's normal
    /// hardware - yields TWO from a single DeviceUnmount record. One such
    /// cycle costs three callback records (HidMount keyboard + HidMount
    /// mouse + DeviceUnmount; HidMount self-creates the device through
    /// ensure_device, so no DeviceMount record is needed) and yields two
    /// Detached, which puts twelve against a capacity of ten inside a single
    /// 20-record pass. Re-derived honestly, with two bounds that hold
    /// whatever order the records arrive in:
    ///
    ///   * Slot bound. remove_device() pushes at most one Detached per role
    ///     slot per call, and there are two slots (Keyboard, and Mouse
    ///     shared with Auxiliary). So Detached <= 2 * U, where U is the
    ///     number of DeviceUnmount records processed this pass.
    ///   * Interface bound. Every Detached retires a distinct MOUNTED,
    ///     role-bearing interface, and an interface is mounted either
    ///     before the pass began or by a HidMount record inside it. So
    ///     Detached <= S + M, where S is how many role-bearing interfaces
    ///     were already mounted when the pass started (<= kInterfaceCapacity
    ///     = 8, a deliberately loose bound - role_is_owned actually keeps
    ///     far fewer than eight role-bearing at once) and M is the number of
    ///     HidMount records processed.
    ///
    /// M + U <= kCallbackQueueCapacity = 20, so the worst case is
    /// max over M+U<=20 of min(2U, 8+M) = 18 (at U=10, M=10, and again at
    /// U=9, M=11). Eighteen fits in twenty, so the queue is sized to the
    /// whole callback budget and a genuine single process_pending() pass -
    /// the only kind main.cpp's Core 1 loop ever produces, since it drains
    /// take_event() completely before the next tuh_task() call - still
    /// cannot overflow it.
    ///
    /// Overflow is nevertheless reachable by withholding drains across many
    /// SEPARATE passes (exactly how this file's other overflow paths are
    /// reached in tests), and it is NOT merely counted: a dropped Detached
    /// is a dropped release-all, so remove_device() escalates that interface
    /// through latch_fault() - the same terminal release-all every other
    /// overflow path in this file already uses - before clearing it. The
    /// counter (detach_overflow_count()) is the diagnostic, not the remedy.
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
        /// mounted interface. Lets take_event() tell "this role slot's
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
    /// How many of ignored_interface_count() were ignored only because the
    /// logical role they wanted was already held by another interface - V1's
    /// "exactly one keyboard and one mouse" rule, applied deterministically to
    /// the first usable claimant.
    ///
    /// Kept apart from the total on purpose: the remainder is "nothing here
    /// could classify this interface", and on a bench a spare second keyboard
    /// and a device this firmware cannot read are entirely different problems
    /// with entirely different fixes. A single total cannot tell them apart,
    /// and the reply that carries it is the only outward sign of either.
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
    /// role-owned interface - the stalled/errored-transfer signal real
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
    /// How many already-queued Ready/Report/AuxiliaryReport SourceEvents
    /// were discarded, at take_event() time, because a Detached for their
    /// own generation (or an older one sharing their role slot) had already
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
    /// Per role slot (Keyboard = 0, Mouse/Auxiliary = 1): the highest
    /// generation whose Detached has already been queued. Anything of that
    /// generation or older still sitting in event_queue_ is discarded at
    /// pop_event() time rather than delivered out of order behind a
    /// Detached that jumped the queue ahead of it - see pop_event()'s own
    /// comment.
    std::uint32_t retired_generation_[2] = {0, 0};
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
