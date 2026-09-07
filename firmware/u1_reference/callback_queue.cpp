#include "callback_queue.hpp"

#include <algorithm>
#include <cstdio>
#include <atomic>
#include <cstring>

namespace {

const char* kind_name(ReferenceCallbackKind kind) {
    switch (kind) {
        case ReferenceCallbackKind::Mount:
            return "MOUNT";
        case ReferenceCallbackKind::Unmount:
            return "UMOUNT";
        case ReferenceCallbackKind::Report:
            return "REPORT";
        case ReferenceCallbackKind::Overflow:
            return "OVERFLOW";
    }
    return "?";
}

//: End a trace line with the byte that separates it from a protocol frame.
//:
//: This target writes its plain-text trace and the configurator's COBS frames
//: to the same physical CDC endpoint. A COBS frame ends at the first zero
//: byte, so a trace line ending only in CRLF fuses with whatever frame follows
//: it into a single candidate no decoder can read - measured twice on
//: hardware, as "malformed COBS frame" and then as "invalid CDC magic". With
//: the zero byte here, each line is its own delimited candidate: the host's
//: reassembler fails to decode it, discards it, and carries on into the frame
//: behind it untouched.
//:
//: Deliberately here and not in ConfigService::reply or in the shared framing:
//: a leading delimiter on every frame would be the classic answer, but reply()
//: is shared with the CH375 and PIO_USB images and that would change what an
//: older configurator sees from every backend. Nothing here touches a protocol
//: byte.
//:
//: Returns how many bytes it wrote, so callers keep their own bounds.
std::size_t terminate_trace_line(char* end) {
    *end = '\0';
    return 1;
}

const char* reason_name(ReferenceDescriptorReason reason) {
    switch (reason) {
        case ReferenceDescriptorReason::None:
            return "none";
        case ReferenceDescriptorReason::Transfer:
            return "xfer";
        case ReferenceDescriptorReason::Gone:
            return "gone";
        case ReferenceDescriptorReason::TooLong:
            return "toolong";
        case ReferenceDescriptorReason::NoBuffer:
            return "nobuf";
        case ReferenceDescriptorReason::Unmounted:
            return "unmounted";
        case ReferenceDescriptorReason::NoInterface:
            return "noitf";
        case ReferenceDescriptorReason::NoOffer:
            return "nooffers";
        case ReferenceDescriptorReason::Overflow:
            return "overflow";
    }
    return "?";
}

constexpr std::size_t kIndexMask = kReferenceQueueCapacity - 1;
static_assert((kReferenceQueueCapacity & kIndexMask) == 0,
              "the capacity must be a power of two: indices are masked");

//: Static storage. The queue never allocates, so a burst can only ever be
//: refused, never turn into a heap the firmware has no room for.
ReferenceCallbackRecord g_records[kReferenceQueueCapacity];

//: Written by the producer, read by the consumer, and vice versa. Release on
//: publish and acquire on observe is what makes the record's bytes visible
//: before the index that advertises them - the two are on different cores in
//: later tasks, and a plain int would let the index arrive first.
std::atomic<std::uint32_t> g_head{0};
std::atomic<std::uint32_t> g_tail{0};
std::atomic<std::uint32_t> g_overflows{0};

std::uint16_t copy_bounded(std::uint8_t* destination,
                           std::size_t capacity,
                           const std::uint8_t* source,
                           std::uint16_t size) {
    if (source == nullptr) {
        return 0;
    }
    const std::size_t copied = std::min<std::size_t>(size, capacity);
    std::memcpy(destination, source, copied);
    return static_cast<std::uint16_t>(copied);
}

}  // namespace

ReferenceCallbackRecord reference_make_mount(std::uint8_t dev_addr,
                                             std::uint8_t instance,
                                             std::uint8_t protocol,
                                             std::uint16_t vid,
                                             std::uint16_t pid,
                                             const std::uint8_t* descriptor,
                                             std::uint16_t descriptor_size,
                                             std::uint32_t now_us) {
    ReferenceCallbackRecord record{};
    record.kind = ReferenceCallbackKind::Mount;
    record.dev_addr = dev_addr;
    record.instance = instance;
    record.protocol = protocol;
    record.vid = vid;
    record.pid = pid;
    record.received_us = now_us;
    record.descriptor_size = copy_bounded(record.descriptor.data(),
                                          record.descriptor.size(),
                                          descriptor,
                                          descriptor_size);
    return record;
}

ReferenceCallbackRecord reference_make_unmount(std::uint8_t dev_addr,
                                               std::uint8_t instance,
                                               std::uint32_t now_us) {
    ReferenceCallbackRecord record{};
    record.kind = ReferenceCallbackKind::Unmount;
    record.dev_addr = dev_addr;
    record.instance = instance;
    record.received_us = now_us;
    return record;
}

ReferenceCallbackRecord reference_make_report(std::uint8_t dev_addr,
                                              std::uint8_t instance,
                                              const std::uint8_t* report,
                                              std::uint16_t report_size,
                                              std::uint32_t now_us) {
    ReferenceCallbackRecord record{};
    record.kind = ReferenceCallbackKind::Report;
    record.dev_addr = dev_addr;
    record.instance = instance;
    record.received_us = now_us;
    record.report_size = copy_bounded(record.report.data(),
                                      record.report.size(),
                                      report,
                                      report_size);
    return record;
}

bool reference_capture(const ReferenceCallbackRecord& record) {
    const std::uint32_t head = g_head.load(std::memory_order_relaxed);
    const std::uint32_t tail = g_tail.load(std::memory_order_acquire);

    if (head - tail >= kReferenceQueueCapacity) {
        // Full. Refuse rather than overwrite: the record already in that slot
        // is one the consumer has not read, and dropping the newest is the
        // only choice that cannot lose a report the consumer already saw
        // announced.
        g_overflows.fetch_add(1, std::memory_order_relaxed);
        return false;
    }

    ReferenceCallbackRecord& slot = g_records[head & kIndexMask];
    slot.kind = record.kind;
    slot.dev_addr = record.dev_addr;
    slot.instance = record.instance;
    slot.protocol = record.protocol;
    slot.vid = record.vid;
    slot.pid = record.pid;
    slot.received_us = record.received_us;
    slot.descriptor_size = record.descriptor_size;
    slot.report_size = record.report_size;
    // Only the bytes the record says it carries: a report is at most 64 bytes,
    // and copying the 256-byte descriptor array on every one of them would be
    // work done a thousand times a second for nothing.
    std::memcpy(slot.descriptor.data(), record.descriptor.data(),
                record.descriptor_size);
    std::memcpy(slot.report.data(), record.report.data(), record.report_size);

    g_head.store(head + 1, std::memory_order_release);
    return true;
}

bool reference_take(ReferenceCallbackRecord& record) {
    const std::uint32_t tail = g_tail.load(std::memory_order_relaxed);
    const std::uint32_t head = g_head.load(std::memory_order_acquire);

    if (head == tail) {
        return false;
    }

    const ReferenceCallbackRecord& slot = g_records[tail & kIndexMask];
    record.kind = slot.kind;
    record.dev_addr = slot.dev_addr;
    record.instance = slot.instance;
    record.protocol = slot.protocol;
    record.vid = slot.vid;
    record.pid = slot.pid;
    record.received_us = slot.received_us;
    record.descriptor_size = slot.descriptor_size;
    record.report_size = slot.report_size;
    std::memcpy(record.descriptor.data(), slot.descriptor.data(),
                slot.descriptor_size);
    std::memcpy(record.report.data(), slot.report.data(), slot.report_size);

    g_tail.store(tail + 1, std::memory_order_release);
    return true;
}

std::uint32_t reference_overflows() {
    return g_overflows.load(std::memory_order_relaxed);
}

namespace {

//: Core 0 writes it in its loop and prints it from the same loop, so no
//: cross-core ordering is involved and none is claimed.
ReferenceLinkStatus g_link_status{};
bool g_link_status_pending = false;

}  // namespace

void reference_link_status_publish(const ReferenceLinkStatus& status) {
    g_link_status = status;
    g_link_status_pending = true;
}

bool reference_link_status_take(ReferenceLinkStatus& status) {
    if (!g_link_status_pending) {
        return false;
    }
    status = g_link_status;
    g_link_status_pending = false;
    return true;
}

void reference_link_status_reset() {
    g_link_status = ReferenceLinkStatus{};
    g_link_status_pending = false;
}

void reference_queue_reset() {
    g_head.store(0, std::memory_order_relaxed);
    g_tail.store(0, std::memory_order_relaxed);
    g_overflows.store(0, std::memory_order_relaxed);
    for (auto& record : g_records) {
        record = ReferenceCallbackRecord{};
    }
    reference_trace_reset();
    reference_descriptor_diagnostic_reset();
    reference_control_trace_reset();
    reference_link_status_reset();
}

// --------------------------------------------------------------------------
// Trace queue
// --------------------------------------------------------------------------

namespace {

constexpr std::size_t kTraceMask = kReferenceTraceCapacity - 1;
static_assert((kReferenceTraceCapacity & kTraceMask) == 0,
              "the trace capacity must be a power of two: indices are masked");

ReferenceTraceEntry g_trace[kReferenceTraceCapacity];
std::atomic<std::uint32_t> g_trace_head{0};
std::atomic<std::uint32_t> g_trace_tail{0};
std::atomic<std::uint32_t> g_trace_overflows{0};

}  // namespace

ReferenceTraceEntry reference_trace_from(const ReferenceCallbackRecord& record) {
    ReferenceTraceEntry entry{};
    entry.kind = record.kind;
    entry.dev_addr = record.dev_addr;
    entry.instance = record.instance;
    entry.length = (record.kind == ReferenceCallbackKind::Mount)
                       ? record.descriptor_size
                       : record.report_size;

    const std::uint8_t* source = (record.kind == ReferenceCallbackKind::Mount)
                                     ? record.descriptor.data()
                                     : record.report.data();
    const std::size_t copied = std::min<std::size_t>(entry.length, kReferenceTracePrefix);
    std::memcpy(entry.prefix.data(), source, copied);
    entry.prefix_size = static_cast<std::uint8_t>(copied);
    return entry;
}

bool reference_trace_push(const ReferenceTraceEntry& entry) {
    const std::uint32_t head = g_trace_head.load(std::memory_order_relaxed);
    const std::uint32_t tail = g_trace_tail.load(std::memory_order_acquire);

    if (head - tail >= kReferenceTraceCapacity) {
        g_trace_overflows.fetch_add(1, std::memory_order_relaxed);
        return false;
    }

    g_trace[head & kTraceMask] = entry;
    g_trace_head.store(head + 1, std::memory_order_release);
    return true;
}

bool reference_trace_take(ReferenceTraceEntry& entry) {
    const std::uint32_t tail = g_trace_tail.load(std::memory_order_relaxed);
    const std::uint32_t head = g_trace_head.load(std::memory_order_acquire);

    if (head == tail) {
        return false;
    }

    entry = g_trace[tail & kTraceMask];
    g_trace_tail.store(tail + 1, std::memory_order_release);
    return true;
}

std::uint32_t reference_trace_overflows() {
    return g_trace_overflows.load(std::memory_order_relaxed);
}

void reference_trace_reset() {
    g_trace_head.store(0, std::memory_order_relaxed);
    g_trace_tail.store(0, std::memory_order_relaxed);
    g_trace_overflows.store(0, std::memory_order_relaxed);
    for (auto& entry : g_trace) {
        entry = ReferenceTraceEntry{};
    }
}

namespace {

//: Power of two, and masked rather than divided, like the other two queues.
constexpr std::size_t kDescriptorDiagnosticCapacity = 4;
constexpr std::size_t kDescriptorDiagnosticMask =
    kDescriptorDiagnosticCapacity - 1;
static_assert((kDescriptorDiagnosticCapacity & kDescriptorDiagnosticMask) == 0,
              "the diagnostic capacity must be a power of two: indices are "
              "masked");

ReferenceDescriptorDiagnostic g_descriptor_diagnostics[kDescriptorDiagnosticCapacity];
std::atomic<std::uint32_t> g_descriptor_diagnostic_head{0};
std::atomic<std::uint32_t> g_descriptor_diagnostic_tail{0};
std::size_t g_descriptor_delivery_offset = 0;

}  // namespace

bool reference_descriptor_diagnostic_push(const ReferenceDescriptorDiagnostic& entry) {
    const std::uint32_t head = g_descriptor_diagnostic_head.load(std::memory_order_relaxed);
    const std::uint32_t tail = g_descriptor_diagnostic_tail.load(std::memory_order_acquire);
    if (head - tail >= kDescriptorDiagnosticCapacity) {
        return false;
    }
    g_descriptor_diagnostics[head & kDescriptorDiagnosticMask] = entry;
    g_descriptor_diagnostic_head.store(head + 1, std::memory_order_release);
    return true;
}

bool reference_descriptor_diagnostic_take(ReferenceDescriptorDiagnostic& entry) {
    const std::uint32_t tail = g_descriptor_diagnostic_tail.load(std::memory_order_relaxed);
    const std::uint32_t head = g_descriptor_diagnostic_head.load(std::memory_order_acquire);
    if (head == tail) {
        return false;
    }
    entry = g_descriptor_diagnostics[tail & kDescriptorDiagnosticMask];
    g_descriptor_diagnostic_tail.store(tail + 1, std::memory_order_release);
    g_descriptor_delivery_offset = 0;
    return true;
}

bool reference_descriptor_diagnostic_peek(ReferenceDescriptorDiagnostic& entry) {
    const std::uint32_t tail = g_descriptor_diagnostic_tail.load(std::memory_order_relaxed);
    const std::uint32_t head = g_descriptor_diagnostic_head.load(std::memory_order_acquire);
    if (head == tail) {
        return false;
    }
    entry = g_descriptor_diagnostics[tail & kDescriptorDiagnosticMask];
    return true;
}

void reference_descriptor_diagnostic_reset() {
    g_descriptor_diagnostic_head.store(0, std::memory_order_relaxed);
    g_descriptor_diagnostic_tail.store(0, std::memory_order_relaxed);
    g_descriptor_delivery_offset = 0;
    for (auto& entry : g_descriptor_diagnostics) {
        entry = ReferenceDescriptorDiagnostic{};
    }
}

namespace {

bool deliver_one_descriptor_diagnostic(IReferenceCdcWriter& writer) {
    ReferenceDescriptorDiagnostic entry{};
    if (!reference_descriptor_diagnostic_peek(entry)) {
        return false;
    }

    // The widest line this can produce is
    //   DESC64_MISMATCH actual=65535 first=65535 prefix=<16 hex>\r\n
    // which is 66 characters. 128 leaves room the arithmetic below never
    // needs, and every append is bounded regardless of that.
    char line[128];
    int written = 0;
    bool renders_prefix = false;
    switch (entry.kind) {
        case ReferenceDescriptorDiagnosticKind::Start:
            written = std::snprintf(line, sizeof(line), "DESC%u_START\r\n",
                                    entry.requested);
            break;
        case ReferenceDescriptorDiagnosticKind::Match:
            // prefix= on a match too: what the buffer holds is reported on
            // every completion outcome, not only on a disagreement.
            written = std::snprintf(line, sizeof(line),
                                    "DESC%u_MATCH actual=%u prefix=",
                                    entry.requested, entry.actual_len);
            renders_prefix = true;
            break;
        case ReferenceDescriptorDiagnosticKind::Mismatch:
            if (entry.first_difference == kReferenceNoDifference) {
                written = std::snprintf(
                    line, sizeof(line),
                    "DESC%u_MISMATCH actual=%u first=none prefix=",
                    entry.requested, entry.actual_len);
            } else {
                written = std::snprintf(
                    line, sizeof(line),
                    "DESC%u_MISMATCH actual=%u first=%u prefix=",
                    entry.requested, entry.actual_len, entry.first_difference);
            }
            renders_prefix = true;
            break;
        case ReferenceDescriptorDiagnosticKind::Failure:
            written = std::snprintf(line, sizeof(line),
                                    "DESC%u_FAIL actual=%u r=%s\r\n",
                                    entry.requested, entry.actual_len,
                                    reason_name(entry.reason));
            break;
        case ReferenceDescriptorDiagnosticKind::Skip:
            written = std::snprintf(line, sizeof(line), "DESC%u_SKIP r=%s\r\n",
                                    entry.requested, reason_name(entry.reason));
            break;
    }

    // snprintf reports what it *would* have written, so clamp to what fits -
    // keeping three bytes in hand, so the CRLF below and the zero byte after
    // it always have room whatever the header cost.
    // used <= sizeof(line) - 3 holds from here on.
    std::size_t used =
        written > 0 ? std::min<std::size_t>(static_cast<std::size_t>(written),
                                            sizeof(line) - 3)
                    : 0;
    if (used != 0 && renders_prefix) {
        for (std::uint8_t index = 0; index < entry.prefix_size; ++index) {
            // Two hex digits, and the CRLF and terminator that must still fit
            // after them.
            if (used + 2 > sizeof(line) - 3) {
                break;
            }
            std::snprintf(line + used, 3, "%02X", entry.prefix[index]);
            used += 2;
        }
        // Both the clamp above and the loop guard keep used at or below
        // sizeof(line) - 3, so these two writes are always in bounds.
        line[used++] = '\r';
        line[used++] = '\n';
    }
    if (used != 0) {
        used += terminate_trace_line(line + used);
    }

    if (used == 0) {
        // Nothing renders this entry. Retaining it would stop every CDC line
        // for ever, which is the loudest possible way to lose a measurement.
        ReferenceDescriptorDiagnostic unrenderable{};
        reference_descriptor_diagnostic_take(unrenderable);
        return true;
    }
    const std::size_t line_size = used;
    if (g_descriptor_delivery_offset > line_size) {
        g_descriptor_delivery_offset = 0;
    }
    const std::size_t remaining = line_size - g_descriptor_delivery_offset;
    if (writer.available() < remaining) {
        return true;
    }
    const std::size_t accepted = writer.write(
        line + g_descriptor_delivery_offset, remaining);
    g_descriptor_delivery_offset += std::min(accepted, remaining);
    if (g_descriptor_delivery_offset != line_size) {
        return true;
    }

    ReferenceDescriptorDiagnostic consumed{};
    reference_descriptor_diagnostic_take(consumed);
    writer.flush();
    return true;
}

}  // namespace

// --------------------------------------------------------------------------
// The control-transfer packet trace
// --------------------------------------------------------------------------

namespace {

//: The wire PID bytes, as Pico-PIO-USB's usb_definitions.h defines them. They
//: are repeated here rather than included because that header belongs to a
//: dependency this file is deliberately kept independent of; the mapping is
//: pinned by a native test.
constexpr std::uint8_t kPidData0 = 0xC3;
constexpr std::uint8_t kPidData1 = 0x4B;
constexpr std::uint8_t kPidAck = 0xD2;
constexpr std::uint8_t kPidNak = 0x5A;
constexpr std::uint8_t kPidStall = 0x1E;
constexpr std::uint8_t kPidSetup = 0x2D;

IReferenceControlTraceSource* g_control_trace_source = nullptr;

//: One line is built at a time and retained until the writer has taken all of
//: it. A half-written CTRL line is worse than a late one: it would read as a
//: packet with different fields.
char g_control_line[192];
std::size_t g_control_line_size = 0;
std::size_t g_control_offset = 0;

//: How many refused records have already been announced. Marked as announced
//: when the line is built rather than when it lands, which is safe precisely
//: because a built line is retained until the writer has taken all of it: the
//: only path that discards one also resets this counter.
std::uint32_t g_control_reported_lost = 0;

const char* pid_name(std::uint8_t pid, char* scratch, std::size_t scratch_size) {
    switch (pid) {
        case kPidData0:
            return "DATA0";
        case kPidData1:
            return "DATA1";
        case kPidAck:
            return "ACK";
        case kPidNak:
            return "NAK";
        case kPidStall:
            return "STALL";
        case kPidSetup:
            return "SETUP";
        default:
            break;
    }
    // Anything else is reported as the byte it was. Rendering it as "?" would
    // hide exactly the packet a reader most needs to see.
    std::snprintf(scratch, scratch_size, "0x%02X", pid);
    return scratch;
}

//: Append the payload bytes that were kept, and say so when there were more
//: than were kept. A truncated dump rendered as a complete one is a lie about
//: what was on the wire.
std::size_t append_payload(char* line,
                           std::size_t capacity,
                           std::size_t used,
                           const ReferenceControlTraceEntry& entry) {
    for (std::uint8_t index = 0; index < entry.byte_count; ++index) {
        // Two hex digits, the truncation marker, and the CRLF after them.
        if (used + 2 > capacity - 3) {
            break;
        }
        std::snprintf(line + used, 3, "%02X", entry.bytes[index]);
        used += 2;
    }
    if (entry.byte_count < entry.length && used < capacity - 3) {
        line[used++] = '+';
    }
    return used;
}

//: Build the next control trace line, or leave the buffer empty when there is
//: nothing to say. Never blocks and never allocates.
void build_next_control_line() {
    if (g_control_trace_source == nullptr) {
        return;
    }

    const std::uint32_t lost = g_control_trace_source->lost();
    if (lost != g_control_reported_lost) {
        // Announced once, at the point it was noticed: a loss repeated every
        // pass is noise that buries the packets it exists to qualify.
        const int written = std::snprintf(
            g_control_line, sizeof(g_control_line), "CTRL_LOST n=%lu\r\n",
            static_cast<unsigned long>(lost - g_control_reported_lost));
        std::size_t lost_used =
            written > 0 ? std::min<std::size_t>(static_cast<std::size_t>(written),
                                                sizeof(g_control_line) - 1)
                        : 0;
        if (lost_used != 0) {
            lost_used += terminate_trace_line(g_control_line + lost_used);
        }
        g_control_line_size = lost_used;
        g_control_offset = 0;
        g_control_reported_lost = lost;
        return;
    }

    ReferenceControlTraceEntry entry{};
    if (!g_control_trace_source->take(entry)) {
        return;
    }

    g_control_offset = 0;

    char scratch[8];
    int written = 0;
    bool renders_payload = false;
    switch (entry.kind) {
        case ReferenceControlTraceKind::Setup:
            written = std::snprintf(
                g_control_line, sizeof(g_control_line),
                "CTRL_SETUP a=%u ep=%u seq=%lu pid=%s bytes=", entry.dev_addr,
                entry.ep_num, static_cast<unsigned long>(entry.seq),
                pid_name(entry.pid, scratch, sizeof(scratch)));
            renders_payload = true;
            break;
        case ReferenceControlTraceKind::Data:
            written = std::snprintf(
                g_control_line, sizeof(g_control_line),
                "CTRL_RX a=%u ep=%u seq=%lu pid=%s len=%u size=%u act=%u "
                "tot=%u bytes=",
                entry.dev_addr, entry.ep_num,
                static_cast<unsigned long>(entry.seq),
                pid_name(entry.pid, scratch, sizeof(scratch)), entry.length,
                entry.ep_size, entry.actual_len, entry.total_len);
            renders_payload = true;
            break;
        case ReferenceControlTraceKind::Done:
            written = std::snprintf(
                g_control_line, sizeof(g_control_line),
                "CTRL_DONE a=%u ep=%u seq=%lu pid=%s act=%u tot=%u\r\n",
                entry.dev_addr, entry.ep_num,
                static_cast<unsigned long>(entry.seq),
                pid_name(entry.pid, scratch, sizeof(scratch)),
                entry.actual_len, entry.total_len);
            break;
    }

    // snprintf reports what it would have written, so clamp to what fits and
    // keep three bytes in hand for the CRLF below and the zero byte after it.
    std::size_t used =
        written > 0 ? std::min<std::size_t>(static_cast<std::size_t>(written),
                                            sizeof(g_control_line) - 3)
                    : 0;
    if (used != 0 && renders_payload) {
        used = append_payload(g_control_line, sizeof(g_control_line), used,
                              entry);
        g_control_line[used++] = '\r';
        g_control_line[used++] = '\n';
    }
    if (used != 0) {
        // append_payload's own guard leaves used at or below capacity - 3, so
        // the CRLF above lands at capacity - 2 and - 1 at worst and this byte
        // is still inside the buffer.
        used += terminate_trace_line(g_control_line + used);
    }
    g_control_line_size = used;
}

bool deliver_one_control_trace(IReferenceCdcWriter& writer) {
    if (g_control_line_size == 0) {
        build_next_control_line();
    }
    if (g_control_line_size == 0) {
        return false;
    }

    const std::size_t remaining = g_control_line_size - g_control_offset;
    if (writer.available() < remaining) {
        // Retain, and hold the lower-priority queue back with it: a report
        // let past here would appear between two packets of one transfer.
        return true;
    }

    const std::size_t accepted =
        writer.write(g_control_line + g_control_offset, remaining);
    g_control_offset += std::min(accepted, remaining);
    if (g_control_offset != g_control_line_size) {
        return true;
    }

    g_control_line_size = 0;
    g_control_offset = 0;
    writer.flush();
    return true;
}

}  // namespace

void reference_set_control_trace_source(IReferenceControlTraceSource* source) {
    // A new source counts its own losses from its own zero, and a line half
    // built from the old one describes a ring that is no longer being read.
    g_control_trace_source = source;
    g_control_line_size = 0;
    g_control_offset = 0;
    g_control_reported_lost = 0;
}

void reference_control_trace_reset() {
    g_control_trace_source = nullptr;
    g_control_line_size = 0;
    g_control_offset = 0;
    g_control_reported_lost = 0;
}

void reference_service_cdc(IReferenceCdcWriter& writer) {
    if (deliver_one_descriptor_diagnostic(writer)) {
        return;
    }
    if (deliver_one_control_trace(writer)) {
        return;
    }

    // Ahead of the report trace and behind both measurements: a keyboard
    // produces thousands of reports a second, and a link line that queues
    // behind them is a link line nobody sees.
    ReferenceLinkStatus link{};
    if (reference_link_status_take(link)) {
        // 96, not 64: every counter at its widest renders 79 characters, and
        // snprintf returns what it *would* have written, so the old buffer
        // could be handed to write() with a length past its own end.
        char line[96];
        const int written = std::snprintf(
            line, sizeof(line),
            "LINK ans=%u tx=%lu crc=%lu echo=%lu drops=%u rel=%u\r\n",
            link.answered ? 1u : 0u,
            static_cast<unsigned long>(link.frames_sent),
            static_cast<unsigned long>(link.crc_errors),
            static_cast<unsigned long>(link.echoed_frames),
            static_cast<unsigned>(link.endpoint_drops),
            static_cast<unsigned>(link.endpoint_release_ms));
        if (written > 0) {
            std::size_t used = std::min<std::size_t>(
                static_cast<std::size_t>(written), sizeof(line) - 1);
            used += terminate_trace_line(line + used);
            writer.write(line, used);
            writer.flush();
        }
        return;
    }

    ReferenceTraceEntry entry{};
    if (!reference_trace_take(entry)) {
        return;
    }

    char line[96];
    int written = std::snprintf(line, sizeof(line), "%s a=%u i=%u len=%u",
                                kind_name(entry.kind), entry.dev_addr,
                                entry.instance, entry.length);
    for (std::uint8_t index = 0;
         index < entry.prefix_size && written > 0 &&
         written < static_cast<int>(sizeof(line)) - 5;
         ++index) {
        written += std::snprintf(line + written, sizeof(line) - written,
                                 " %02X", entry.prefix[index]);
    }
    if (written > 0 && written < static_cast<int>(sizeof(line)) - 3) {
        line[written++] = '\r';
        line[written++] = '\n';
    }
    if (written > 0) {
        std::size_t used = std::min<std::size_t>(
            static_cast<std::size_t>(written), sizeof(line) - 1);
        used += terminate_trace_line(line + used);
        writer.write(line, used);
        writer.flush();
    }
}
