// TinyUSB's own HID host callbacks, invoked from inside tuh_task() while
// PioUsbBackend::task() runs it on Core 1.
//
// Each callback performs only bounded metadata lookup/copy and queueing. The
// ordinary Core 1 pass processes those records and owns every receive arm;
// no parser, normalizer, pipeline or route is called from this file.
//
// tuh_hid_report_received_cb is the one exception worth naming: it reads
// time_us_32() before handing the report to the registry, because that is
// the only point anywhere in this path that is actually the moment the
// report arrived. A monotonic clock read is bounded, non-routing work - the
// same shape as every other lookup here - not a second thing this callback
// does. Reading it later, once Core 1's ordinary pass gets around to
// draining the callback queue, would stamp every report processed in that
// pass with one shared, later timestamp instead of each report's own.

#include <atomic>
#include <cstdint>

// Included, not extern-declared. The SDK's time_us_32() is a `static inline`
// register read, so an extern declaration would compile here and then leave
// the Pico link with an undefined symbol - unlike tuh_hid_receive_report and
// friends, which are real out-of-line functions this tree does declare by
// hand. The native test build shadows this header from fakes/, the same way
// it already shadows hardware/clocks.h for set_sys_clock_khz.
#include "hardware/timer.h"

#include "pio_usb/backend.hpp"

extern "C" bool tuh_vid_pid_get(std::uint8_t dev_addr, std::uint16_t* vendor_id,
                                std::uint16_t* product_id);
extern "C" std::uint8_t tuh_hid_interface_protocol(std::uint8_t dev_addr,
                                                    std::uint8_t instance);
// __get_MSP(), out of line. pio_usb/core1_stack_pointer.cpp is the shipping
// definition and fakes/tinyusb_host.cpp is the native one, which is what lets
// a test drive Core 1's stack depth without an ARM core - the same
// extern-declaration convention this tree already uses for the library entry
// points above. Declared rather than included from CMSIS because the pinned
// SDK's cmsis_gcc_m.h does not compile as C++; see that file for the detail.
extern "C" std::uint32_t duo_core1_stack_pointer(void);

namespace duo_input::u1::pio_usb {
namespace {
DeviceRegistry* callback_registry = nullptr;
std::atomic<std::uint32_t> mount_events{0};
std::atomic<std::uint32_t> umount_events{0};
std::atomic<std::uint32_t> hid_mount_events{0};

// No static_assert on is_always_lock_free here, deliberately. On this target
// GCC reports that trait FALSE - ARMv6-M has no LDREX/STREX, so a
// read-modify-write atomic is a libcall - while the relaxed loads and stores
// this file actually performs still compile to a plain LDR and STR, which are
// naturally atomic for an aligned word. Asserting the trait would fail the
// firmware build over a property nothing here relies on. What this code does
// rely on - that no publication in the host path becomes a library call that
// could take a lock inside the SOF interrupt - is checked against the LINKED
// IMAGE instead, by
// tests/build/test_pio_usb_firmware_contract.py's no-atomic-libcall guard.

// TinyUSB's own event ids (hcd.h): attach, remove, transfer complete, and a
// deferred function call that is not an HCD event and is not counted.
constexpr std::uint32_t kEventAttach = 0;
constexpr std::uint32_t kEventRemove = 1;
constexpr std::uint32_t kEventXferComplete = 2;

// Index 0 is Core 1 in thread context, inside tuh_task(); index 1 is Core 1 in
// the SOF alarm interrupt. Both write these words, and RP2040 has no atomic
// read-modify-write, so one shared word would silently lose an increment
// whenever the alarm landed inside the other context's load-modify-store. A
// lost attach is the difference between "one device attached" and "two", which
// is the entire reading. One word per context has exactly one writer each, so
// a plain relaxed load/store pair is correct; Core 0 adds the two in
// host_callback_observability() below.
constexpr std::size_t kContexts = 2;
std::atomic<std::uint32_t> attach_events[kContexts];
std::atomic<std::uint32_t> remove_events[kContexts];
std::atomic<std::uint32_t> xfer_events[kContexts];
// The deepest stack pointer each context saw. Split for the same reason, and
// it matters more here: a lost sample makes the minimum read HIGHER, which is
// the direction that hides an overflow.
std::atomic<std::uint32_t> min_stack_pointer[kContexts];

void increment_saturating(std::atomic<std::uint32_t>& counter) noexcept {
    const std::uint32_t value = counter.load(std::memory_order_relaxed);
    if (value < 0xFFFFu) {
        // TinyUSB invokes these callbacks on Core 1 only. Core 0 only loads;
        // the atomic makes that concurrent snapshot defined, while the
        // single-writer load/store keeps this operation strictly bounded.
        counter.store(value + 1u, std::memory_order_relaxed);
    }
}

void increment_saturating(std::atomic<std::uint32_t>& counter,
                          std::uint32_t limit) noexcept {
    const std::uint32_t value = counter.load(std::memory_order_relaxed);
    if (value < limit) {
        counter.store(value + 1u, std::memory_order_relaxed);
    }
}

std::uint32_t add_clamped(std::uint32_t left, std::uint32_t right,
                          std::uint32_t limit) noexcept {
    const std::uint32_t total = left + right;
    return total > limit ? limit : total;
}

/// The deeper of two stack readings, where zero means "never sampled".
///
/// Zero is not a stack pointer on this part - SRAM begins at 0x20000000 - so
/// it is free to carry "the hook has not run yet", and it must never win a
/// minimum: read as an address it would be the most alarming value the field
/// can hold, on a board that is merely idle.
std::uint32_t deeper(std::uint32_t left, std::uint32_t right) noexcept {
    if (left == 0) {
        return right;
    }
    if (right == 0) {
        return left;
    }
    return left < right ? left : right;
}
}  // namespace

void set_callback_registry(DeviceRegistry* registry) noexcept {
    callback_registry = registry;
}

void reset_host_callback_observability() noexcept {
    mount_events.store(0, std::memory_order_relaxed);
    umount_events.store(0, std::memory_order_relaxed);
    hid_mount_events.store(0, std::memory_order_relaxed);
    for (std::size_t context = 0; context < kContexts; ++context) {
        attach_events[context].store(0, std::memory_order_relaxed);
        remove_events[context].store(0, std::memory_order_relaxed);
        xfer_events[context].store(0, std::memory_order_relaxed);
        min_stack_pointer[context].store(0, std::memory_order_relaxed);
    }
}

HostCallbackObservability host_callback_observability() noexcept {
    HostCallbackObservability out;
    out.mount_events =
        static_cast<std::uint16_t>(mount_events.load(std::memory_order_relaxed));
    out.umount_events =
        static_cast<std::uint16_t>(umount_events.load(std::memory_order_relaxed));
    out.hid_mount_events =
        static_cast<std::uint16_t>(hid_mount_events.load(std::memory_order_relaxed));
    out.attach_events_from_task = attach_events[0].load(std::memory_order_relaxed);
    out.attach_events_from_isr = attach_events[1].load(std::memory_order_relaxed);
    const std::uint32_t attach = add_clamped(out.attach_events_from_task,
                                             out.attach_events_from_isr,
                                             kHostEventAttachLimit);
    const std::uint32_t remove =
        add_clamped(remove_events[0].load(std::memory_order_relaxed),
                    remove_events[1].load(std::memory_order_relaxed),
                    kHostEventRemoveLimit);
    const std::uint32_t xfer =
        add_clamped(xfer_events[0].load(std::memory_order_relaxed),
                    xfer_events[1].load(std::memory_order_relaxed),
                    kHostEventXferLimit);
    out.host_event_counts = attach | (remove << kHostEventRemoveShift) |
                            (xfer << kHostEventXferShift);
    out.core1_min_sp = deeper(min_stack_pointer[0].load(std::memory_order_relaxed),
                              min_stack_pointer[1].load(std::memory_order_relaxed));
    return out;
}
}  // namespace duo_input::u1::pio_usb

extern "C" {

// TinyUSB's weak host event hook, defined here rather than patched into the
// pinned stack. usbh.c's queue_event() calls it for EVERY event the host
// stack queues, which makes it the only place in this firmware that can see
// the host stack's own event stream: how many attaches were accepted, how many
// removals, and how many transfers completed - the last of which is how far
// along a control chain the last successful transfer got.
//
// Inert by construction. It runs after osal_queue_send has already succeeded,
// it takes no lock, allocates nothing, calls nothing that can block, and
// changes no decision anywhere. An event DROPPED by a full queue never reaches
// here, which is itself a reading: the counts below are what the stack
// accepted, not what the hardware raised.
//
// in_isr is TinyUSB's own statement of which of Core 1's two contexts it is
// in, and it is used for nothing but choosing which context's word to touch.
void tuh_event_hook_cb(std::uint8_t rhport, std::uint32_t eventid, bool in_isr) {
    (void)rhport;
    const std::size_t context = in_isr ? 1u : 0u;
    switch (eventid) {
        case duo_input::u1::pio_usb::kEventAttach:
            duo_input::u1::pio_usb::increment_saturating(
                duo_input::u1::pio_usb::attach_events[context],
                duo_input::u1::pio_usb::kHostEventAttachLimit);
            break;
        case duo_input::u1::pio_usb::kEventRemove:
            duo_input::u1::pio_usb::increment_saturating(
                duo_input::u1::pio_usb::remove_events[context],
                duo_input::u1::pio_usb::kHostEventRemoveLimit);
            break;
        case duo_input::u1::pio_usb::kEventXferComplete:
            duo_input::u1::pio_usb::increment_saturating(
                duo_input::u1::pio_usb::xfer_events[context],
                duo_input::u1::pio_usb::kHostEventXferLimit);
            break;
        default:
            // USBH_EVENT_FUNC_CALL is not an HCD event and is not counted as
            // one; a future event id this firmware does not know is not
            // counted either, rather than being folded into a total that would
            // then be wrong about all three.
            break;
    }

    // Sampled here because here is as deep inside tuh_task()'s call chain as
    // this firmware can reach without patching TinyUSB: enumeration, the hub
    // driver and the HID class driver all queue events from inside their own
    // frames. Core 1 runs a 2 KB stack and now carries the whole host
    // enumeration on top of this project's own pipeline; nothing else in this
    // reply can say how close that came to the bottom.
    const std::uint32_t stack_pointer = duo_core1_stack_pointer();
    auto& deepest = duo_input::u1::pio_usb::min_stack_pointer[context];
    const std::uint32_t previous = deepest.load(std::memory_order_relaxed);
    if (previous == 0 || stack_pointer < previous) {
        deepest.store(stack_pointer, std::memory_order_relaxed);
    }
}

void tuh_mount_cb(std::uint8_t dev_addr) {
    duo_input::u1::pio_usb::increment_saturating(
        duo_input::u1::pio_usb::mount_events);
    auto* registry = duo_input::u1::pio_usb::callback_registry;
    if (registry == nullptr) {
        return;
    }
    std::uint16_t vendor_id = 0;
    std::uint16_t product_id = 0;
    (void)tuh_vid_pid_get(dev_addr, &vendor_id, &product_id);
    registry->capture_device_mount(dev_addr, vendor_id, product_id);
}

void tuh_umount_cb(std::uint8_t dev_addr) {
    duo_input::u1::pio_usb::increment_saturating(
        duo_input::u1::pio_usb::umount_events);
    auto* registry = duo_input::u1::pio_usb::callback_registry;
    if (registry != nullptr) {
        registry->capture_unmount(dev_addr);
    }
}

void tuh_hid_mount_cb(std::uint8_t dev_addr, std::uint8_t instance,
                      std::uint8_t const* report_desc, std::uint16_t desc_len) {
    duo_input::u1::pio_usb::increment_saturating(
        duo_input::u1::pio_usb::hid_mount_events);
    auto* registry = duo_input::u1::pio_usb::callback_registry;
    if (registry == nullptr) {
        return;
    }
    std::uint16_t vendor_id = 0;
    std::uint16_t product_id = 0;
    (void)tuh_vid_pid_get(dev_addr, &vendor_id, &product_id);
    registry->capture_hid_mount(dev_addr, instance, vendor_id, product_id,
                                tuh_hid_interface_protocol(dev_addr, instance),
                                report_desc, desc_len);
}

void tuh_hid_umount_cb(std::uint8_t dev_addr, std::uint8_t instance) {
    (void)instance;
    auto* registry = duo_input::u1::pio_usb::callback_registry;
    if (registry != nullptr) {
        registry->capture_unmount(dev_addr);
    }
}

void tuh_hid_report_received_cb(std::uint8_t dev_addr, std::uint8_t instance,
                                std::uint8_t const* report, std::uint16_t len) {
    auto* registry = duo_input::u1::pio_usb::callback_registry;
    if (registry != nullptr) {
        registry->capture_report(dev_addr, instance, report, len, time_us_32());
    }
}

}  // extern "C"
