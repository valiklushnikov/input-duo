#pragma once

// A fixed-size bridge out of the USB host callbacks.
//
// The upstream example formats text and writes CDC from inside
// tuh_hid_report_received_cb, which runs on the core that drives tuh_task.
// Time spent there is time the bus is not being serviced, and this hardware has
// already shown how little that takes: a handshake that misses its window makes
// a device resend the same packet indefinitely. So a callback may only copy
// bounded data and re-arm the report; parsing, formatting and routing happen in
// ordinary task context on the far side of this queue.
//
// Nothing here allocates, blocks or grows. A record cannot exceed its arrays,
// and a capture into a full queue is refused and counted rather than
// overwriting a record the consumer has not read. Losing input silently is the
// failure this exists to make impossible; losing it visibly is what
// reference_overflows() reports.

#include <array>
#include <cstddef>
#include <cstdint>

enum class ReferenceCallbackKind : std::uint8_t {
    Mount,
    Unmount,
    Report,
    Overflow,
    DescriptorStart,
    DescriptorFailure,
};

//: CFG_TUH_ENUMERATION_BUFSIZE is 256, so a report descriptor TinyUSB was able
//: to fetch at all cannot be longer than this.
inline constexpr std::size_t kReferenceDescriptorCapacity = 256;

//: CFG_TUH_HID_EPIN_BUFSIZE is 64, the largest a single interrupt IN packet can
//: be at full speed.
inline constexpr std::size_t kReferenceReportCapacity = 64;

//: Power of two: indices are masked rather than divided. Deep enough to absorb
//: a burst from two devices between drains, small enough that the whole queue
//: is under 3 KB of static RAM.
inline constexpr std::size_t kReferenceQueueCapacity = 8;

struct ReferenceCallbackRecord {
    ReferenceCallbackKind kind{};
    std::uint8_t dev_addr{};
    std::uint8_t instance{};
    //: The HID interface protocol byte (0 none, 1 keyboard, 2 mouse). Only the
    //: callback can ask TinyUSB for it, and the adapter cannot classify an
    //: interface without it - a descriptor that will not parse leaves this as
    //: the only thing left to go on.
    std::uint8_t protocol{};
    std::uint16_t vid{};
    std::uint16_t pid{};
    std::uint16_t descriptor_size{};
    std::uint16_t report_size{};
    std::uint32_t received_us{};
    std::array<std::uint8_t, kReferenceDescriptorCapacity> descriptor{};
    std::array<std::uint8_t, kReferenceReportCapacity> report{};
};

// Build records with their payloads truncated to what a record can hold. The
// callbacks call these, so the bound is applied in one place rather than at
// every call site - a length the caller forgot to clamp is exactly the kind of
// mistake that turns into a buffer overrun in an interrupt-driven path.
ReferenceCallbackRecord reference_make_mount(std::uint8_t dev_addr,
                                             std::uint8_t instance,
                                             std::uint8_t protocol,
                                             std::uint16_t vid,
                                             std::uint16_t pid,
                                             const std::uint8_t* descriptor,
                                             std::uint16_t descriptor_size,
                                             std::uint32_t now_us);

ReferenceCallbackRecord reference_make_unmount(std::uint8_t dev_addr,
                                               std::uint8_t instance,
                                               std::uint32_t now_us);

ReferenceCallbackRecord reference_make_report(std::uint8_t dev_addr,
                                              std::uint8_t instance,
                                              const std::uint8_t* report,
                                              std::uint16_t report_size,
                                              std::uint32_t now_us);

//: The protocol byte belongs to the interface, not to a single report, so a
//: report record carries the one its mount established. The adapter fills it
//: from what it already knows about that interface.

//: Returns false when the queue is full; the record is dropped and counted.
bool reference_capture(const ReferenceCallbackRecord& record);

//: Returns false when the queue is empty; `record` is then untouched.
bool reference_take(ReferenceCallbackRecord& record);

//: How many captures have been refused since the last reset. Survives
//: draining: a consumer that empties the queue must still be able to see that
//: something was lost.
std::uint32_t reference_overflows();

//: Tests only, and any deliberate restart. Not called during normal operation.
void reference_queue_reset();

// The trace side of the bridge.
//
// Core 1 owns the host stack and must not spend its time formatting CDC text,
// so it moves at most one drained record per tuh_task() into this much smaller
// queue and Core 0 prints from it. An entry carries only what a trace line
// needs: what happened, to whom, how long the report was, and enough of its
// leading bytes to recognise it.
inline constexpr std::size_t kReferenceTracePrefix = 8;
inline constexpr std::size_t kReferenceTraceCapacity = 16;

struct ReferenceTraceEntry {
    ReferenceCallbackKind kind{};
    std::uint8_t dev_addr{};
    std::uint8_t instance{};
    std::uint16_t length{};
    std::uint8_t prefix_size{};
    std::array<std::uint8_t, kReferenceTracePrefix> prefix{};
};

//: Summarise a drained record into a trace entry.
ReferenceTraceEntry reference_trace_from(const ReferenceCallbackRecord& record);

//: Returns false when the trace queue is full; the entry is dropped and
//: counted. A dropped trace line loses a diagnostic, never an input report -
//: the record it summarised has already left the callback queue.
bool reference_trace_push(const ReferenceTraceEntry& entry);

//: Returns false when the trace queue is empty.
bool reference_trace_take(ReferenceTraceEntry& entry);

std::uint32_t reference_trace_overflows();

//: Tests only.
void reference_trace_reset();

enum class ReferenceDescriptorDiagnosticKind : std::uint8_t {
    Start,
    Match,
    Mismatch,
    Failure,
};

struct ReferenceDescriptorDiagnostic {
    ReferenceDescriptorDiagnosticKind kind{};
    std::uint8_t dev_addr{};
    std::uint8_t instance{};
    std::uint16_t actual_len{};
    std::uint16_t first_difference{};
    std::uint8_t prefix_size{};
    std::array<std::uint8_t, kReferenceTracePrefix> prefix{};
};

// Descriptor measurements bypass the ordinary report trace queue. They remain
// bounded and non-blocking, but continuous report traffic cannot hide them.
bool reference_descriptor_diagnostic_push(const ReferenceDescriptorDiagnostic& entry);
bool reference_descriptor_diagnostic_peek(ReferenceDescriptorDiagnostic& entry);
bool reference_descriptor_diagnostic_take(ReferenceDescriptorDiagnostic& entry);
void reference_descriptor_diagnostic_reset();

class IReferenceCdcWriter {
public:
    virtual ~IReferenceCdcWriter() = default;
    virtual std::size_t available() const = 0;
    virtual std::size_t write(const char* data, std::size_t size) = 0;
    virtual void flush() = 0;
};

// True while a descriptor diagnostic owns CDC, including when it could not be
// completely queued this pass. The entry is removed only after a full write.
bool reference_deliver_one_descriptor_diagnostic(IReferenceCdcWriter& writer);
