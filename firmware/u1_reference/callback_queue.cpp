#include "callback_queue.hpp"

#include <algorithm>
#include <atomic>
#include <cstring>

namespace {

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
