// The USB host callbacks, and nothing else.
//
// These are the same three callbacks the upstream example defines, with the
// work taken out of them. Upstream formats text and writes CDC here; this
// copies bounded data into a queue and re-arms the report, which is the only
// other thing the stack requires of it. Everything a trace line needs -
// formatting, hex, CDC - happens on Core 0, on the far side of two queues.
//
// Why that matters on this hardware rather than as a matter of taste: these run
// on the core that drives tuh_task, and a bus transaction that misses its
// window is not retried politely. This session measured a device resending one
// packet 19,065 times because a handshake arrived late. Time spent formatting
// here is time the bus is not serviced.
//
// main() and core1_main() stay in C and keep the upstream lifecycle exactly:
// clock, settle, launch Core 1, tuh_init on Core 1, tud_init on Core 0.

#include <cstdint>

#include "pico/time.h"
#include "tusb.h"

#include "callback_queue.hpp"

extern "C" bool reference_descriptor_unmounted(std::uint8_t dev_addr,
                                                std::uint8_t instance,
                                                std::uint32_t now_us);

// Pico-PIO-USB's control-transfer packet trace, from patches/pico-pio-usb/.
// The patch is confined to src/pio_usb_host.c - it touches no header, and a
// build test keeps it that way - so the drain hands back scalars and these
// prototypes are declared here rather than included. Nothing mirrors a struct
// layout across the boundary; only the three kind numbers do, and they are
// pinned below and by a build test on both sides.
extern "C" {
bool pio_usb_host_ctrl_trace_take(std::uint32_t* out_seq,
                                  std::uint8_t* out_kind,
                                  std::uint8_t* out_dev_addr,
                                  std::uint8_t* out_ep_num,
                                  std::uint8_t* out_pid,
                                  std::uint16_t* out_len,
                                  std::uint16_t* out_ep_size,
                                  std::uint16_t* out_actual_len,
                                  std::uint16_t* out_total_len,
                                  std::uint8_t* out_bytes,
                                  std::uint8_t* out_byte_count);
std::uint32_t pio_usb_host_ctrl_trace_lost(void);
}

static_assert(static_cast<std::uint8_t>(ReferenceControlTraceKind::Setup) == 0,
              "PIO_USB_CTRL_TRACE_KIND_SETUP is 0 in the patch");
static_assert(static_cast<std::uint8_t>(ReferenceControlTraceKind::Data) == 1,
              "PIO_USB_CTRL_TRACE_KIND_DATA is 1 in the patch");
static_assert(static_cast<std::uint8_t>(ReferenceControlTraceKind::Done) == 2,
              "PIO_USB_CTRL_TRACE_KIND_DONE is 2 in the patch");
static_assert(kReferenceControlTraceBytes == 16,
              "PIO_USB_CTRL_TRACE_BYTES is 16 in the patch, and the drain "
              "writes exactly that many bytes into the array below");

namespace {

// Core 0 only. Everything this does is copy out of a ring the host core
// filled; it never reaches back into the host stack.
class PioUsbControlTrace final : public IReferenceControlTraceSource {
public:
    bool take(ReferenceControlTraceEntry& entry) override {
        std::uint8_t kind = 0;
        std::uint8_t byte_count = 0;
        if (!pio_usb_host_ctrl_trace_take(
                &entry.seq, &kind, &entry.dev_addr, &entry.ep_num, &entry.pid,
                &entry.length, &entry.ep_size, &entry.actual_len,
                &entry.total_len, entry.bytes.data(), &byte_count)) {
            return false;
        }
        entry.kind = static_cast<ReferenceControlTraceKind>(kind);
        // Bounded here as well as in the patch: a count larger than the array
        // would be read past its end by the renderer.
        entry.byte_count = static_cast<std::uint8_t>(
            byte_count < entry.bytes.size() ? byte_count : entry.bytes.size());
        return true;
    }

    std::uint32_t lost() override { return pio_usb_host_ctrl_trace_lost(); }
};

PioUsbControlTrace g_control_trace;

class TinyUsbCdcWriter final : public IReferenceCdcWriter {
public:
    std::size_t available() const override {
        return tud_cdc_write_available();
    }

    std::size_t write(const char* data, std::size_t size) override {
        return tud_cdc_write(data, static_cast<std::uint32_t>(size));
    }

    void flush() override { tud_cdc_write_flush(); }
};

}  // namespace

extern "C" {

//--------------------------------------------------------------------+
// Host HID callbacks - Core 1, task context
//--------------------------------------------------------------------+

void tuh_hid_mount_cb(uint8_t dev_addr,
                      uint8_t instance,
                      uint8_t const* desc_report,
                      uint16_t desc_len) {
    uint16_t vid = 0;
    uint16_t pid = 0;
    tuh_vid_pid_get(dev_addr, &vid, &pid);
    uint8_t const itf_protocol = tuh_hid_interface_protocol(dev_addr, instance);

    reference_capture(reference_make_mount(dev_addr, instance, itf_protocol,
                                           vid, pid, desc_report, desc_len,
                                           time_us_32()));

    // Upstream arms the report here for boot keyboards and mice, and the stack
    // delivers nothing until it is armed. Keep that, and only that.
    if (itf_protocol == HID_ITF_PROTOCOL_KEYBOARD ||
        itf_protocol == HID_ITF_PROTOCOL_MOUSE) {
        tuh_hid_receive_report(dev_addr, instance);
    }
}

void tuh_hid_umount_cb(uint8_t dev_addr, uint8_t instance) {
    // The pinned host stack cancels an in-flight control transfer on removal
    // without invoking its application completion callback. Retire the
    // matching application-side lifetime token here so a replug can retry.
    reference_descriptor_unmounted(dev_addr, instance, time_us_32());
}

void tuh_hid_report_received_cb(uint8_t dev_addr,
                                uint8_t instance,
                                uint8_t const* report,
                                uint16_t len) {
    reference_capture(
        reference_make_report(dev_addr, instance, report, len, time_us_32()));

    // Re-arm unconditionally, as upstream does: a report that is not requested
    // again is the last one this interface will ever deliver.
    tuh_hid_receive_report(dev_addr, instance);
}

//--------------------------------------------------------------------+
// Device CDC - Core 0
//--------------------------------------------------------------------+

void tud_cdc_rx_cb(uint8_t itf) {
    // Deliberately does not read: Task 5's ConfigService drains the CDC RX
    // FIFO by polling tud_cdc_available()/tud_cdc_read() from main()'s Core 0
    // loop, the same way u1_main's does. Reading (and discarding) bytes here
    // as an earlier revision of this target did would steal them out from
    // under that poll before ConfigService ever saw them - every configurator
    // request would be consumed and silently dropped before this callback's
    // caller returned.
    (void)itf;
}

//--------------------------------------------------------------------+
// The two drains, called from the two loops in main.c
//--------------------------------------------------------------------+

// Core 1, after each tuh_task() returns. At most one record per pass, so a
// burst can never turn a service loop into a long one.
//
// Kept for the overflow watch below. Since Task 3 the input path is the
// queue's only consumer, so this no longer takes records itself - taking them
// here would mean a report reaching the diagnostics instead of the keyboard.
void reference_drain_one_callback(void) {
    // A refused capture is the one thing that must never pass unnoticed: it is
    // input this firmware was handed and did not keep. Counting it is not
    // enough on its own - nobody reads a counter - so a change is turned into
    // an ordinary trace entry, in line with the reports around it, carrying how
    // many were lost.
    static uint32_t reported_overflows = 0;
    const uint32_t overflows = reference_overflows();
    if (overflows != reported_overflows) {
        ReferenceTraceEntry lost{};
        lost.kind = ReferenceCallbackKind::Overflow;
        lost.length = static_cast<uint16_t>(overflows - reported_overflows);
        if (reference_trace_push(lost)) {
            reported_overflows = overflows;
        }
        // If even that push was refused, leave reported_overflows alone and
        // try again next pass rather than losing the fact that input was lost.
        return;
    }

}

// Core 0, from the device loop. TinyUSB is only the writer; priority,
// retention, formatting, and queue consumption live in the native-tested
// service coordinator.
void reference_service_one_cdc(void) {
    // Installed here, on Core 0, and nowhere else: draining the packet ring
    // belongs to the core that prints, never to the core that fills it.
    static bool trace_installed = false;
    if (!trace_installed) {
        reference_set_control_trace_source(&g_control_trace);
        trace_installed = true;
    }

    TinyUsbCdcWriter writer;
    reference_service_cdc(writer);
}

}  // extern "C"
