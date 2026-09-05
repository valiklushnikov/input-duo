#include "pio_usb/device_registry.hpp"

#include <cstring>

#include "hardware/timer.h"
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
    // Auxiliary shares Mouse's slot deliberately: both are the same physical
    // device's interfaces and target the same InputPipeline instance, so
    // remove_device()'s "first one claims the slot" guard below already
    // collapses their teardown into the single Detached that pipeline needs -
    // never two, which would double-run its release-all harmlessly but is
    // not what a single detach is.
    return role == LogicalRole::Keyboard ? 0u : 1u;
}

/// The fixed "endpoint" value input/pipeline.cpp's on_auxiliary_report
/// requires of a Keychron side-button report. Not this interface's own
/// TinyUSB instance number - enumeration order does not guarantee that is 1 -
/// but the same constant the CH375 quirk this replaces used for the same
/// physical channel (interface 2, endpoint 1 on that transport).
constexpr std::uint8_t kKeychronAuxiliaryEndpoint = 1;

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

bool DeviceRegistry::has_mouse_sibling(std::uint8_t dev_addr) const {
    // Classified as Mouse, not granted the Mouse ROLE: a competing mouse
    // elsewhere can win role_is_owned(Mouse) and leave this receiver's own
    // mouse interface LogicalRole::Ignored while classify_hid's verdict on it
    // - identity.kind - is untouched and still Mouse (process() only
    // overwrites identity.kind for an interface this function itself has
    // already approved as Auxiliary). Gating on the role instead would make
    // that the exact pre-task defect return: this receiver's auxiliary
    // channel would find no sibling, fall through to role_for_kind(Keyboard),
    // and - since the real Mouse role is free precisely because the
    // competing mouse is occupying it, not this receiver - win the Keyboard
    // role and type every side-button press as a phantom modifier keystroke.
    // Excluding Auxiliary keeps this a one-hop check: an already-approved
    // auxiliary channel's own overridden identity.kind must never itself
    // count as "the mouse" for some third interface on the same device.
    for (const Interface& candidate : interfaces_) {
        if (candidate.mounted && candidate.dev_addr == dev_addr &&
            candidate.role != LogicalRole::Auxiliary &&
            candidate.identity.kind == input::DeviceKind::Mouse) {
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
    interface.arm_retry_deadline_us = time_us_32() + kArmRetryBackoffUs[interface.arm_retry_count];
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
        if (interface.arm_retry_pending && now_us >= interface.arm_retry_deadline_us) {
            arm_if_needed(interface);
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
    // Local to this one call, not persistent registry state: several
    // interfaces on the same physical device (the Keychron receiver's Mouse
    // and Auxiliary channels) collapse into one Detached per role slot here,
    // exactly as before. A PERSISTENT "already pending" guard was tried
    // first and was wrong - it also suppressed a DIFFERENT, later device's
    // own genuine Detached for the same role slot if an earlier one had not
    // been drained yet, which is a real dropped release, not a harmless
    // double-send. Draining always happens between physical teardowns in
    // real operation (main.cpp drains take_event() completely every pass),
    // so this per-call guard is all "collapse this device's own interfaces"
    // ever needed.
    bool queued_slot[2] = {false, false};
    for (Interface& interface : interfaces_) {
        if (!interface.mounted || interface.dev_addr != dev_addr) {
            continue;
        }
        if (interface.role != LogicalRole::Ignored) {
            const std::size_t slot = detach_slot(interface.role);
            if (!queued_slot[slot]) {
                queued_slot[slot] = true;
                PendingEvent pending{};
                pending.present = true;
                pending.event.kind = input::SourceEventKind::Detached;
                pending.event.source_id = interface.dev_addr;
                pending.identity = interface.identity;
                pending.generation = interface.generation;
                if (push_detach(pending)) {
                    if (interface.generation > retired_generation_[slot]) {
                        retired_generation_[slot] = interface.generation;
                    }
                } else {
                    // Only reachable by withholding take_event() drains
                    // across many separate process_pending() passes - see
                    // kDetachQueueCapacity. Counted, not silently lost, the
                    // same standard this file already applies to every other
                    // overflow path.
                    ++detach_overflows_;
                }
            }
        }
        // Stop accepting reports and free layout/held state together:
        // clearing mounted here is what makes find_mutable() refuse any
        // report already in the callback queue for this interface once
        // process() reaches it, and the interface's own generation is
        // retired above before this line ever runs.
        interface = {};
    }
}

bool DeviceRegistry::push_event(const Interface& interface, input::SourceEventKind kind,
                                std::uint8_t endpoint, const std::uint8_t* report,
                                std::size_t report_size, std::uint32_t received_us) {
    // Fault keeps the last kFaultReservedSlots for itself - one per
    // independently-arming role-bearing interface V1 accepts (Keyboard,
    // Mouse, and the Keychron receiver's Auxiliary channel), not one slot in
    // total and not one slot per downstream InputPipeline. Two reserved slots
    // are not enough for three such interfaces: fill to capacity with
    // ordinary traffic, fault the keyboard (spends one reserved slot), fault
    // the mouse (spends the other), and the auxiliary channel's next report
    // then finds the queue full, latches its own Fault into a queue with
    // nothing left, and loses it - or, in whatever order the callback queue
    // happens to drain in, the keyboard's Fault can just as easily be the one
    // that finds nothing left, since push_event() processes records FIFO, not
    // grouped by which interface or pipeline they belong to. That lost Fault
    // is a dropped release-all, which this queue exists to make impossible -
    // and the interface is faulted afterwards, so nothing will ever produce
    // it again. Reserving one slot per interface means every Ready/Report/
    // AuxiliaryReport push here refuses three slots early and every source's
    // overflow Fault, pushed straight after its own push already failed,
    // always has room, in any arrival order.
    //
    // The seventeen slots this leaves ordinary traffic are still far more
    // than a genuine pass can use - about thirteen at worst; the derivation
    // is on kEventQueueCapacity in the header.
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
        // Ready/Report/AuxiliaryReport from a generation whose Detached has
        // already been delivered are stale: a Detached is delivered ahead of
        // this queue (take_event() drains detach_events_ first), so by the
        // time one of these is reached here its own release has already run
        // and delivering it now would either be a no-op read against an
        // already-torn-down pipeline or - if a newer generation has since
        // claimed the same role - misrouted into that NEW device's state.
        // Fault/Detached themselves are never filtered: this file's own
        // ordering keeps a Fault self-consistent with whatever of the same
        // generation precedes it (Fault shares this same queue, so anything
        // still ahead of it here genuinely arrived first), and Detached
        // never reaches this queue at all - see push_detach().
        const bool filterable = slot.event.kind == input::SourceEventKind::Ready ||
                                 slot.event.kind == input::SourceEventKind::Report ||
                                 slot.event.kind == input::SourceEventKind::AuxiliaryReport;
        const std::size_t role_slot =
            slot.identity.kind == input::DeviceKind::Keyboard ? 0u : 1u;
        if (filterable && slot.generation != 0 &&
            slot.generation <= retired_generation_[role_slot]) {
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
        // Assigned once, here, from the registry-wide monotonic counter -
        // never on the duplicate-mount branch above, which re-arms the same
        // still-mounted interface rather than claiming a fresh one. This is
        // what lets pop_event() tell "this role slot's current occupant"
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

        // The Keychron M3 receiver's side button is emitted by a second
        // interface shaped like a keyboard - never a real keyboard. Granting
        // it the Keyboard role would read its side-button reports at boot
        // offsets and invent a modifier keystroke on every press; associating
        // it with the mouse's own channel instead means its bytes only ever
        // reach InputPipeline's report-shape check (pipeline.cpp's
        // keychron_side_state), which still refuses everything but the exact
        // side-button trace. Gated on the exact vendor/product this receiver
        // reports, not on shape alone, so every other composite device's
        // keyboard-shaped interface keeps the Keyboard role it would
        // otherwise earn - and further gated on this device already having a
        // sibling interface classify_hid found to be a mouse (see
        // has_mouse_sibling), so a lone keyboard that merely reports this
        // vendor/product (nothing else of this receiver's shape present) is
        // not pulled out of the Keyboard role it should still be free to
        // earn. The sibling check reads identity.kind, not LogicalRole::
        // Mouse, on purpose: a second, unrelated mouse can win
        // role_is_owned(Mouse) and leave this receiver's own mouse interface
        // Ignored while classify_hid still calls it a mouse, and gating on
        // the role would misread that as "no sibling" - handing the
        // Keyboard role to the auxiliary channel after all.
        const bool auxiliary_of_mouse =
            classified &&
            is_keychron_auxiliary_interface(record.vendor_id, record.product_id,
                                            interface->identity.kind) &&
            has_mouse_sibling(record.dev_addr);
        LogicalRole wanted = LogicalRole::Ignored;
        if (auxiliary_of_mouse) {
            // Transport association only: the neutral identity says Mouse so
            // this reaches the same InputPipeline instance the receiver's own
            // mouse interface does. Its keyboard-shaped layout fields are
            // left as classify_hid set them but are never read - AuxiliaryReport
            // is handled from the raw bytes, not through a layout.
            interface->identity.kind = input::DeviceKind::Mouse;
        } else if (classified) {
            wanted = role_for_kind(interface->identity.kind);
        }

        if (auxiliary_of_mouse) {
            interface->role = LogicalRole::Auxiliary;
        } else if (wanted != LogicalRole::Ignored && !role_is_owned(wanted)) {
            interface->role = wanted;
        } else {
            interface->role = LogicalRole::Ignored;
            ++ignored_interfaces_;
        }
        if (interface->role == LogicalRole::Keyboard || interface->role == LogicalRole::Mouse) {
            // Told once, before its first Report: InputPipeline::on_event
            // reads Ready to learn what this source is and which layout to
            // read its reports through, and a Report ahead of that would be
            // read under whatever the pipeline was left holding from before.
            // Auxiliary never reaches here - it is not a source the pipeline
            // is separately told about, only a second channel of the Mouse
            // one already was.
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
    if (record.size == 0) {
        // A stalled or errored transfer, not an idle one (Task 10). Real
        // TinyUSB's hidh_xfer_cb forwards xferred_bytes to
        // tuh_hid_report_received_cb regardless of xfer_result, so a
        // stall/error completes with (near) zero bytes rather than never
        // completing at all - an idle, healthy device that simply has
        // nothing new to report produces no CallbackRecord whatsoever,
        // which is why this can never mistake "nobody typed anything" for a
        // fault. Never turned into a Report/AuxiliaryReport SourceEvent -
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
        arm_if_needed(*interface);
        return;
    }
    // Forward progress: whatever run of arm refusals or stall signals came
    // before this real report, the interface has just proven itself
    // healthy again.
    interface->arm_retry_count = 0;
    if (interface->role == LogicalRole::Auxiliary) {
        // The Keychron receiver's side-button channel. AuxiliaryReport, not
        // Report - InputPipeline reads this from raw bytes through its own
        // shape check, never through a keyboard or mouse layout - and the
        // fixed endpoint that check requires, not this interface's own
        // instance number.
        if (!push_event(*interface, input::SourceEventKind::AuxiliaryReport,
                        kKeychronAuxiliaryEndpoint,
                        record.payload_present ? record.payload : nullptr, record.size,
                        record.received_us)) {
            ++event_overflows_;
            latch_fault(*interface);
            return;
        }
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
        host_fault_stage_ = 2;
        event = {};
        event.kind = input::SourceEventKind::Fault;
        identity = {};
        identity.kind = input::DeviceKind::Keyboard;
        return true;
    }
    if (host_fault_stage_ == 2) {
        host_fault_stage_ = 0;
        event = {};
        event.kind = input::SourceEventKind::Fault;
        identity = {};
        identity.kind = input::DeviceKind::Mouse;
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
