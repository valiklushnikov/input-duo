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
    //: The measurement gave up before anything reached the wire. Without this
    //: the give-up paths print nothing at all, and an experiment that never
    //: scheduled looks exactly like a board that was never flashed.
    Skip,
};

//: Why a measurement failed or gave up, rendered as the `r=` field. Short
//: enough to stay greppable on a CDC line, and named so the answer says what
//: happened rather than only that something did.
enum class ReferenceDescriptorReason : std::uint8_t {
    None,
    //: The control transfer itself did not complete successfully.
    Transfer,
    //: The interface went away before its answer arrived.
    Gone,
    //: The reported length was larger than the request buffer.
    TooLong,
    //: No buffer reached the comparison. An internal fault, not a device
    //: measurement, and it must never be reported as a mismatch.
    NoBuffer,
    //: The interface vanished before an on-wire attempt was made.
    Unmounted,
    //: bInterfaceNumber could not be read, so no request could be formed.
    NoInterface,
    //: The offer budget drained without the host stack ever accepting one.
    NoOffer,
    //: A refused capture retired everything downstream, this request with it.
    Overflow,
};

//: Every compared byte agreed, so there is no first difference to report.
//: Rendered as `first=none` rather than as a byte index nothing computed: a
//: fabricated index reads as "those bytes were golden", which is the very
//: claim this measurement exists to test.
inline constexpr std::uint16_t kReferenceNoDifference = 0xFFFFu;

//: Wide enough to show buffer[13..20]. If the 77-byte answer was the golden
//: document rotated by 64, those bytes read 05 01 09 06 A1 01 05 08; if only a
//: 13-byte second packet ever landed at offset zero, they read A5. Eight bytes
//: cannot tell those apart, which is why this is not kReferenceTracePrefix.
inline constexpr std::size_t kReferenceDescriptorPrefix = 24;

struct ReferenceDescriptorDiagnostic {
    ReferenceDescriptorDiagnosticKind kind{};
    ReferenceDescriptorReason reason{};
    std::uint8_t dev_addr{};
    std::uint8_t instance{};
    //: The wLength this attempt asked for, and the number in its token:
    //: DESC64_ for the one-packet read, DESC77_ for the whole document. It is
    //: carried rather than assumed so a line can never claim to belong to an
    //: experiment that did not produce it.
    std::uint16_t requested{};
    std::uint16_t actual_len{};
    std::uint16_t first_difference = kReferenceNoDifference;
    //: The first bytes of the *request buffer*, not of what the transfer said
    //: it delivered. With the buffer poisoned before every attempt this is
    //: what separates "nothing arrived" from "bytes arrived and only the
    //: count was lost", and it is reported on every completion outcome.
    std::uint8_t prefix_size{};
    std::array<std::uint8_t, kReferenceDescriptorPrefix> prefix{};
};

// Descriptor measurements bypass the ordinary report trace queue. They remain
// bounded and non-blocking, but continuous report traffic cannot hide them.
bool reference_descriptor_diagnostic_push(const ReferenceDescriptorDiagnostic& entry);
bool reference_descriptor_diagnostic_peek(ReferenceDescriptorDiagnostic& entry);
bool reference_descriptor_diagnostic_take(ReferenceDescriptorDiagnostic& entry);
void reference_descriptor_diagnostic_reset();

// The control-transfer packet trace.
//
// Pico-PIO-USB records one entry per DATA packet on a control endpoint into a
// static ring inside its own transaction path - after the handshake for that
// packet has already gone out, never before. This is the far side of that
// ring: Core 0 drains it in the bounded CDC service below and turns it into
// one line per packet, so "the device sent nothing" can be told apart from
// "the host stack lost what the device sent".
//
// The ring itself lives in the dependency, which cannot include this header,
// so the source is an interface: the firmware implements it over the C drain
// API and native tests implement it over a vector.

enum class ReferenceControlTraceKind : std::uint8_t {
    //: The eight SETUP bytes of the request the DATA packets answer.
    Setup = 0,
    //: One received DATA packet.
    Data = 1,
    //: The transfer stopped being active. It bounds the run of packets before
    //: it, which is what makes a zero-length first packet legible as a
    //: completed transfer rather than a stalled one.
    Done = 2,
};

//: At most this many payload bytes are kept per packet. Copying more inside
//: the transaction path buys nothing: what a reader needs is the first items
//: of the descriptor and the length, not the whole packet.
inline constexpr std::size_t kReferenceControlTraceBytes = 16;

struct ReferenceControlTraceEntry {
    ReferenceControlTraceKind kind{};
    std::uint8_t dev_addr{};
    std::uint8_t ep_num{};
    //: The received PID byte, as it appeared on the wire.
    std::uint8_t pid{};
    //: Monotonic across every entry, and incremented even when the ring was
    //: full, so a gap in the sequence is itself visible.
    std::uint32_t seq{};
    std::uint16_t length{};
    //: ep->size. This is what turns "EP0 is 8 bytes" from an assumption into
    //: a measurement.
    std::uint16_t ep_size{};
    std::uint16_t actual_len{};
    std::uint16_t total_len{};
    std::uint8_t byte_count{};
    std::array<std::uint8_t, kReferenceControlTraceBytes> bytes{};
};

class IReferenceControlTraceSource {
public:
    virtual ~IReferenceControlTraceSource() = default;
    //: False when the ring is empty; `entry` is then untouched.
    virtual bool take(ReferenceControlTraceEntry& entry) = 0;
    //: How many entries the ring refused since boot. Never resets.
    virtual std::uint32_t lost() = 0;
};

//: Installed once, from Core 0. Null until then, and null again after a reset,
//: so nothing in a native test can reach a source that has gone out of scope.
void reference_set_control_trace_source(IReferenceControlTraceSource* source);

//: Tests only, and any deliberate restart.
void reference_control_trace_reset();

class IReferenceCdcWriter {
public:
    virtual ~IReferenceCdcWriter() = default;
    virtual std::size_t available() const = 0;
    virtual std::size_t write(const char* data, std::size_t size) = 0;
    virtual void flush() = 0;
};

// Service one CDC item. Priority is absolute and in this order: a retained
// descriptor diagnostic, then the control-transfer packet trace, then the
// ordinary report trace. Both diagnostics outrank report traffic because a
// keyboard produces thousands of reports a second and would otherwise bury
// every line the measurement exists to produce.
void reference_service_cdc(IReferenceCdcWriter& writer);
