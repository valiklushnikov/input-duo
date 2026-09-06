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

void reference_queue_reset() {
    g_head.store(0, std::memory_order_relaxed);
    g_tail.store(0, std::memory_order_relaxed);
    g_overflows.store(0, std::memory_order_relaxed);
    for (auto& record : g_records) {
        record = ReferenceCallbackRecord{};
    }
    reference_trace_reset();
    reference_descriptor_diagnostic_reset();
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
            written = std::snprintf(line, sizeof(line), "DESC64_START\r\n");
            break;
        case ReferenceDescriptorDiagnosticKind::Match:
            // prefix= on a match too: what the buffer holds is reported on
            // every completion outcome, not only on a disagreement.
            written = std::snprintf(line, sizeof(line),
                                    "DESC64_MATCH actual=%u prefix=",
                                    entry.actual_len);
            renders_prefix = true;
            break;
        case ReferenceDescriptorDiagnosticKind::Mismatch:
            if (entry.first_difference == kReferenceNoDifference) {
                written = std::snprintf(
                    line, sizeof(line),
                    "DESC64_MISMATCH actual=%u first=none prefix=",
                    entry.actual_len);
            } else {
                written = std::snprintf(
                    line, sizeof(line),
                    "DESC64_MISMATCH actual=%u first=%u prefix=",
                    entry.actual_len, entry.first_difference);
            }
            renders_prefix = true;
            break;
        case ReferenceDescriptorDiagnosticKind::Failure:
            written = std::snprintf(line, sizeof(line),
                                    "DESC64_FAIL actual=%u r=%s\r\n",
                                    entry.actual_len, reason_name(entry.reason));
            break;
        case ReferenceDescriptorDiagnosticKind::Skip:
            written = std::snprintf(line, sizeof(line), "DESC64_SKIP r=%s\r\n",
                                    reason_name(entry.reason));
            break;
    }

    // snprintf reports what it *would* have written, so clamp to what fits -
    // keeping two bytes in hand, so the CRLF below always has room whatever
    // the header cost. used <= sizeof(line) - 2 holds from here on.
    std::size_t used =
        written > 0 ? std::min<std::size_t>(static_cast<std::size_t>(written),
                                            sizeof(line) - 2)
                    : 0;
    if (used != 0 && renders_prefix) {
        for (std::uint8_t index = 0; index < entry.prefix_size; ++index) {
            // Two hex digits, and the CRLF that must still fit after them.
            if (used + 2 > sizeof(line) - 2) {
                break;
            }
            std::snprintf(line + used, 3, "%02X", entry.prefix[index]);
            used += 2;
        }
        // Both the clamp above and the loop guard keep used at or below
        // sizeof(line) - 2, so these two writes are always in bounds.
        line[used++] = '\r';
        line[used++] = '\n';
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

void reference_service_cdc(IReferenceCdcWriter& writer) {
    if (deliver_one_descriptor_diagnostic(writer)) {
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
         written < static_cast<int>(sizeof(line)) - 4;
         ++index) {
        written += std::snprintf(line + written, sizeof(line) - written,
                                 " %02X", entry.prefix[index]);
    }
    if (written > 0 && written < static_cast<int>(sizeof(line)) - 2) {
        line[written++] = '\r';
        line[written++] = '\n';
    }
    if (written > 0) {
        writer.write(line, static_cast<std::size_t>(written));
        writer.flush();
    }
}
