// The bounded bridge out of the USB host callbacks.
//
// The upstream example formats and writes CDC from inside
// tuh_hid_report_received_cb, on the core that runs tuh_task. Anything slow or
// blocking there is paid for on the bus, and this session already measured what
// that costs: a single mistimed handshake is enough to make a device resend the
// same packet for ever. So the callbacks may only copy bounded data and re-arm;
// everything else happens in ordinary task context, on the far side of this
// queue.
//
// Bounded means bounded in both directions. A record can never be larger than
// its fixed arrays, and the queue can never grow: when it is full a capture is
// refused and counted rather than allocating, blocking or overwriting a record
// the consumer has not read yet. The count is what a later task turns into a
// visible Overflow event; losing reports silently is what this queue exists to
// rule out.

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

#include "callback_queue.hpp"
#include "test_support.hpp"

namespace {

//: The wire-visible "no compared byte disagreed" sentinel, written as a
//: literal so the rendering test does not borrow the implementation's own
//: idea of what "none" is.
constexpr std::uint16_t kNoDifferenceSentinel = 0xFFFFu;

struct CdcWriter final : IReferenceCdcWriter {
    std::size_t space = 512;
    std::size_t write_limit = 512;
    std::size_t write_calls = 0;
    std::size_t flush_calls = 0;
    std::string output;

    std::size_t available() const override { return space; }

    std::size_t write(const char* data, std::size_t size) override {
        ++write_calls;
        const std::size_t count = std::min(size, write_limit);
        output.append(data, count);
        return count;
    }

    void flush() override { ++flush_calls; }
};

//: Push one diagnostic and return exactly what a CDC service writes for it.
//: The tokens below carry the whole result of the experiment, so they are
//: asserted as literal lines rather than through the formatter.
std::string render_one(const ReferenceDescriptorDiagnostic& entry) {
    reference_queue_reset();
    CHECK(reference_descriptor_diagnostic_push(entry));
    CdcWriter writer;
    reference_service_cdc(writer);
    return writer.output;
}

ReferenceCallbackRecord report_record(std::uint8_t dev_addr,
                                      std::uint8_t instance,
                                      const std::uint8_t* bytes,
                                      std::uint16_t length,
                                      std::uint32_t now_us = 0) {
    return reference_make_report(dev_addr, instance, bytes, length, now_us);
}

}  // namespace

TEST_CASE(taking_from_an_empty_queue_reports_nothing) {
    reference_queue_reset();

    ReferenceCallbackRecord taken{};
    CHECK_FALSE(reference_take(taken));
    CHECK_EQ(reference_overflows(), 0u);
}

TEST_CASE(records_come_back_in_the_order_they_were_captured) {
    reference_queue_reset();

    for (std::uint8_t index = 0; index < 4; ++index) {
        const std::uint8_t payload[] = {index};
        CHECK(reference_capture(report_record(index, index, payload, 1)));
    }

    for (std::uint8_t index = 0; index < 4; ++index) {
        ReferenceCallbackRecord taken{};
        CHECK(reference_take(taken));
        CHECK_EQ(taken.dev_addr, index);
        CHECK_EQ(taken.instance, index);
        CHECK_EQ(taken.report[0], index);
    }

    ReferenceCallbackRecord drained{};
    CHECK_FALSE(reference_take(drained));
}

TEST_CASE(report_bytes_are_owned_by_the_queue_not_by_the_caller) {
    reference_queue_reset();

    std::uint8_t bytes[] = {1, 2, 3};
    CHECK(reference_capture(report_record(2, 0, bytes, 3)));

    // The USB stack reuses its buffer the moment the callback returns.
    bytes[0] = 99;

    ReferenceCallbackRecord taken{};
    CHECK(reference_take(taken));
    CHECK_EQ(taken.report_size, 3u);
    CHECK_EQ(taken.report[0], 1u);
    CHECK_EQ(taken.report[1], 2u);
    CHECK_EQ(taken.report[2], 3u);
}

TEST_CASE(a_report_longer_than_the_record_is_truncated_not_overflowed) {
    reference_queue_reset();

    std::uint8_t huge[kReferenceReportCapacity + 40];
    for (std::size_t index = 0; index < sizeof(huge); ++index) {
        huge[index] = static_cast<std::uint8_t>(index & 0xff);
    }

    CHECK(reference_capture(report_record(
        1, 0, huge, static_cast<std::uint16_t>(sizeof(huge)))));

    ReferenceCallbackRecord taken{};
    CHECK(reference_take(taken));
    CHECK_EQ(taken.report_size, kReferenceReportCapacity);
    CHECK_EQ(taken.report[0], 0u);
    CHECK_EQ(taken.report[kReferenceReportCapacity - 1],
             static_cast<std::uint8_t>((kReferenceReportCapacity - 1) & 0xff));
}

TEST_CASE(a_descriptor_longer_than_the_record_is_truncated_not_overflowed) {
    reference_queue_reset();

    std::uint8_t huge[kReferenceDescriptorCapacity + 64];
    for (std::size_t index = 0; index < sizeof(huge); ++index) {
        huge[index] = static_cast<std::uint8_t>(index & 0xff);
    }

    CHECK(reference_capture(reference_make_mount(
        3, 1, 1, 0x3554, 0xfa09, huge,
        static_cast<std::uint16_t>(sizeof(huge)), 0)));

    ReferenceCallbackRecord taken{};
    CHECK(reference_take(taken));
    CHECK_EQ(taken.descriptor_size, kReferenceDescriptorCapacity);
    CHECK_EQ(taken.descriptor[kReferenceDescriptorCapacity - 1],
             static_cast<std::uint8_t>((kReferenceDescriptorCapacity - 1) & 0xff));
}

TEST_CASE(a_mount_carries_the_identity_the_consumer_needs) {
    reference_queue_reset();

    const std::uint8_t descriptor[] = {0x05, 0x01, 0x09, 0x02};
    CHECK(reference_capture(
        reference_make_mount(2, 1, 2, 0x3434, 0xd030, descriptor, 4, 12345)));

    ReferenceCallbackRecord taken{};
    CHECK(reference_take(taken));
    CHECK(taken.kind == ReferenceCallbackKind::Mount);
    CHECK_EQ(taken.dev_addr, 2u);
    CHECK_EQ(taken.instance, 1u);
    CHECK_EQ(taken.vid, 0x3434u);
    CHECK_EQ(taken.pid, 0xd030u);
    CHECK_EQ(taken.protocol, 2u);
    CHECK_EQ(taken.descriptor_size, 4u);
    CHECK_EQ(taken.descriptor[1], 0x01u);
    CHECK_EQ(taken.received_us, 12345u);
}

TEST_CASE(an_unmount_names_the_interface_that_went_away) {
    reference_queue_reset();

    CHECK(reference_capture(reference_make_unmount(2, 1, 999)));

    ReferenceCallbackRecord taken{};
    CHECK(reference_take(taken));
    CHECK(taken.kind == ReferenceCallbackKind::Unmount);
    CHECK_EQ(taken.dev_addr, 2u);
    CHECK_EQ(taken.instance, 1u);
    CHECK_EQ(taken.report_size, 0u);
    CHECK_EQ(taken.descriptor_size, 0u);
    CHECK_EQ(taken.received_us, 999u);
}

TEST_CASE(a_full_queue_refuses_a_capture_and_counts_it) {
    reference_queue_reset();

    const std::uint8_t payload[] = {0xab};
    for (std::size_t index = 0; index < kReferenceQueueCapacity; ++index) {
        CHECK(reference_capture(
            report_record(static_cast<std::uint8_t>(index), 0, payload, 1)));
    }

    CHECK_FALSE(reference_capture(report_record(200, 0, payload, 1)));
    CHECK_EQ(reference_overflows(), 1u);

    CHECK_FALSE(reference_capture(report_record(201, 0, payload, 1)));
    CHECK_EQ(reference_overflows(), 2u);
}

TEST_CASE(a_refused_capture_does_not_disturb_what_is_already_queued) {
    reference_queue_reset();

    const std::uint8_t payload[] = {0xab};
    for (std::size_t index = 0; index < kReferenceQueueCapacity; ++index) {
        CHECK(reference_capture(
            report_record(static_cast<std::uint8_t>(index), 0, payload, 1)));
    }
    CHECK_FALSE(reference_capture(report_record(200, 0, payload, 1)));

    // The oldest record is still the oldest, and the newest is the last one
    // that was actually accepted - not the one that was refused.
    for (std::size_t index = 0; index < kReferenceQueueCapacity; ++index) {
        ReferenceCallbackRecord taken{};
        CHECK(reference_take(taken));
        CHECK_EQ(taken.dev_addr, static_cast<std::uint8_t>(index));
    }
    ReferenceCallbackRecord drained{};
    CHECK_FALSE(reference_take(drained));
}

TEST_CASE(space_freed_by_a_take_is_usable_again) {
    reference_queue_reset();

    const std::uint8_t payload[] = {0xab};
    for (std::size_t index = 0; index < kReferenceQueueCapacity; ++index) {
        CHECK(reference_capture(
            report_record(static_cast<std::uint8_t>(index), 0, payload, 1)));
    }
    CHECK_FALSE(reference_capture(report_record(200, 0, payload, 1)));

    ReferenceCallbackRecord taken{};
    CHECK(reference_take(taken));
    CHECK(reference_capture(report_record(201, 0, payload, 1)));

    // Draining the rest must end with the record that was accepted after the
    // space was freed, and nothing from the refused capture.
    ReferenceCallbackRecord last{};
    while (reference_take(taken)) {
        last = taken;
    }
    CHECK_EQ(last.dev_addr, 201u);
}

TEST_CASE(the_overflow_count_survives_draining_and_is_cleared_only_by_reset) {
    reference_queue_reset();

    const std::uint8_t payload[] = {0xab};
    for (std::size_t index = 0; index < kReferenceQueueCapacity; ++index) {
        CHECK(reference_capture(
            report_record(static_cast<std::uint8_t>(index), 0, payload, 1)));
    }
    CHECK_FALSE(reference_capture(report_record(200, 0, payload, 1)));

    ReferenceCallbackRecord taken{};
    while (reference_take(taken)) {
    }

    // A consumer that drains the queue must still be able to see that
    // something was lost; that is the whole point of counting it.
    CHECK_EQ(reference_overflows(), 1u);

    reference_queue_reset();
    CHECK_EQ(reference_overflows(), 0u);
}

TEST_CASE(the_queue_capacity_is_a_power_of_two) {
    // The implementation masks indices instead of dividing; a capacity that is
    // not a power of two would silently corrupt the ring rather than fail.
    CHECK(kReferenceQueueCapacity > 0);
    CHECK_EQ(kReferenceQueueCapacity & (kReferenceQueueCapacity - 1),
             static_cast<std::size_t>(0));
}

// --------------------------------------------------------------------------
// The trace side: what Core 1 hands to Core 0 to print.
// --------------------------------------------------------------------------

TEST_CASE(a_report_trace_summarises_the_report_bytes) {
    reference_queue_reset();

    const std::uint8_t bytes[] = {0x03, 0x01, 0x10, 0x00, 0xF0, 0xFF, 0x01, 0x00, 0xAA};
    const ReferenceTraceEntry entry =
        reference_trace_from(reference_make_report(2, 1, bytes, sizeof(bytes), 7));

    CHECK(entry.kind == ReferenceCallbackKind::Report);
    CHECK_EQ(entry.dev_addr, 2u);
    CHECK_EQ(entry.instance, 1u);
    CHECK_EQ(entry.length, 9u);
    // Only a prefix travels: a trace line is for recognising a report, not for
    // carrying it.
    CHECK_EQ(entry.prefix_size, kReferenceTracePrefix);
    CHECK_EQ(entry.prefix[0], 0x03u);
    CHECK_EQ(entry.prefix[7], 0x00u);
}

TEST_CASE(a_mount_trace_summarises_the_descriptor_instead) {
    reference_queue_reset();

    const std::uint8_t descriptor[] = {0x05, 0x01, 0x09, 0x02};
    const ReferenceTraceEntry entry = reference_trace_from(
        reference_make_mount(1, 0, 2, 0x3434, 0xd030, descriptor, 4, 0));

    CHECK(entry.kind == ReferenceCallbackKind::Mount);
    CHECK_EQ(entry.length, 4u);
    CHECK_EQ(entry.prefix_size, 4u);
    CHECK_EQ(entry.prefix[1], 0x01u);
}

TEST_CASE(a_short_report_traces_only_the_bytes_it_has) {
    reference_queue_reset();

    const std::uint8_t bytes[] = {0x01, 0x02, 0x03};
    const ReferenceTraceEntry entry =
        reference_trace_from(reference_make_report(1, 0, bytes, 3, 0));

    CHECK_EQ(entry.length, 3u);
    CHECK_EQ(entry.prefix_size, 3u);
    CHECK_EQ(entry.prefix[2], 0x03u);
    // Nothing beyond what arrived: the rest of the prefix stays zero rather
    // than showing whatever the previous entry left there.
    CHECK_EQ(entry.prefix[3], 0u);
}

TEST_CASE(trace_entries_come_back_in_order_and_the_queue_is_bounded) {
    reference_queue_reset();

    for (std::size_t index = 0; index < kReferenceTraceCapacity; ++index) {
        ReferenceTraceEntry entry{};
        entry.dev_addr = static_cast<std::uint8_t>(index);
        CHECK(reference_trace_push(entry));
    }

    ReferenceTraceEntry overflowing{};
    CHECK_FALSE(reference_trace_push(overflowing));
    CHECK_EQ(reference_trace_overflows(), 1u);

    for (std::size_t index = 0; index < kReferenceTraceCapacity; ++index) {
        ReferenceTraceEntry taken{};
        CHECK(reference_trace_take(taken));
        CHECK_EQ(taken.dev_addr, static_cast<std::uint8_t>(index));
    }

    ReferenceTraceEntry drained{};
    CHECK_FALSE(reference_trace_take(drained));
}

TEST_CASE(a_dropped_trace_line_never_costs_an_input_report) {
    reference_queue_reset();

    // Fill the trace queue so the next push is refused.
    for (std::size_t index = 0; index < kReferenceTraceCapacity; ++index) {
        CHECK(reference_trace_push(ReferenceTraceEntry{}));
    }
    CHECK_FALSE(reference_trace_push(ReferenceTraceEntry{}));

    // The record queue is a separate bound and is untouched by that: losing a
    // diagnostic must never be able to lose input.
    const std::uint8_t payload[] = {0x42};
    CHECK(reference_capture(reference_make_report(1, 0, payload, 1, 0)));
    CHECK_EQ(reference_overflows(), 0u);

    ReferenceCallbackRecord taken{};
    CHECK(reference_take(taken));
    CHECK_EQ(taken.report[0], 0x42u);
}

TEST_CASE(desc64_diagnostics_bypass_a_full_continuous_report_trace) {
    reference_queue_reset();
    for (std::size_t index = 0; index < kReferenceTraceCapacity; ++index) {
        ReferenceTraceEntry report{};
        report.kind = ReferenceCallbackKind::Report;
        report.dev_addr = 9;
        report.instance = 1;
        report.length = 8;
        CHECK(reference_trace_push(report));
    }

    ReferenceDescriptorDiagnostic diagnostic{};
    diagnostic.kind = ReferenceDescriptorDiagnosticKind::Mismatch;
    diagnostic.requested = 64u;
    diagnostic.actual_len = 0u;
    diagnostic.prefix.fill(0xA5u);
    diagnostic.prefix_size = 8u;
    CHECK(reference_descriptor_diagnostic_push(diagnostic));

    // The property this test is named for is a property of the CDC service,
    // so it has to be the CDC service that is asked: with the trace queue
    // saturated and staying saturated, the measurement is still what the very
    // next write carries. Taking from the diagnostic queue directly would
    // pass with the priority branch deleted.
    CdcWriter writer;
    reference_service_cdc(writer);
    CHECK_EQ(writer.output,
             std::string{
                 "DESC64_MISMATCH actual=0 first=none "
                 "prefix=A5A5A5A5A5A5A5A5\r\n"});
    CHECK_EQ(writer.flush_calls, 1u);
    CHECK_EQ(reference_trace_overflows(), 0u);
}

TEST_CASE(every_desc64_token_renders_exactly_as_the_output_contract_states) {
    std::array<std::uint8_t, kReferenceDescriptorPrefix> golden{};
    const std::uint8_t golden_head[] = {0x05, 0x01, 0x09, 0x06,
                                        0xA1, 0x01, 0x05, 0x08};
    std::copy(std::begin(golden_head), std::end(golden_head), golden.begin());

    ReferenceDescriptorDiagnostic start{};
    start.kind = ReferenceDescriptorDiagnosticKind::Start;
    start.requested = 64u;
    CHECK_EQ(render_one(start), std::string{"DESC64_START\r\n"});

    ReferenceDescriptorDiagnostic match{};
    match.kind = ReferenceDescriptorDiagnosticKind::Match;
    match.requested = 64u;
    match.actual_len = 64u;
    match.prefix = golden;
    match.prefix_size = 8u;
    CHECK_EQ(render_one(match),
             std::string{"DESC64_MATCH actual=64 prefix=05010906A1010508\r\n"});

    ReferenceDescriptorDiagnostic mismatch{};
    mismatch.kind = ReferenceDescriptorDiagnosticKind::Mismatch;
    mismatch.requested = 64u;
    mismatch.actual_len = 64u;
    mismatch.first_difference = 17u;
    mismatch.prefix = golden;
    mismatch.prefix_size = 8u;
    CHECK_EQ(render_one(mismatch),
             std::string{"DESC64_MISMATCH actual=64 first=17 "
                         "prefix=05010906A1010508\r\n"});

    // The measured case, and the discriminator this round exists to add: a
    // successful transfer of zero bytes. No byte was compared, so there is no
    // first difference, and the poison says nothing was written either.
    ReferenceDescriptorDiagnostic empty{};
    empty.kind = ReferenceDescriptorDiagnosticKind::Mismatch;
    empty.requested = 64u;
    empty.actual_len = 0u;
    empty.first_difference = kNoDifferenceSentinel;
    empty.prefix.fill(0xA5u);
    empty.prefix_size = 8u;
    CHECK_EQ(render_one(empty),
             std::string{"DESC64_MISMATCH actual=0 first=none "
                         "prefix=A5A5A5A5A5A5A5A5\r\n"});

    ReferenceDescriptorDiagnostic failure{};
    failure.kind = ReferenceDescriptorDiagnosticKind::Failure;
    failure.requested = 64u;
    failure.actual_len = 0u;
    failure.reason = ReferenceDescriptorReason::Transfer;
    CHECK_EQ(render_one(failure), std::string{"DESC64_FAIL actual=0 r=xfer\r\n"});

    ReferenceDescriptorDiagnostic skip{};
    skip.kind = ReferenceDescriptorDiagnosticKind::Skip;
    skip.requested = 64u;
    skip.reason = ReferenceDescriptorReason::NoOffer;
    CHECK_EQ(render_one(skip), std::string{"DESC64_SKIP r=nooffers\r\n"});
}

TEST_CASE(a_full_width_desc64_line_still_ends_in_crlf_and_fits) {
    ReferenceDescriptorDiagnostic widest{};
    widest.kind = ReferenceDescriptorDiagnosticKind::Mismatch;
    widest.requested = 65535u;
    widest.actual_len = 65535u;
    // One below the sentinel: the widest index that is still a real index.
    widest.first_difference = 65534u;
    widest.prefix.fill(0xFFu);
    widest.prefix_size = static_cast<std::uint8_t>(widest.prefix.size());
    const std::string line = render_one(widest);
    CHECK_EQ(line,
             std::string{"DESC65535_MISMATCH actual=65535 first=65534 "
                         "prefix=FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF"
                         "FFFF\r\n"});
}

TEST_CASE(an_unrenderable_diagnostic_is_dropped_rather_than_stopping_cdc) {
    reference_queue_reset();

    ReferenceDescriptorDiagnostic broken{};
    broken.kind = static_cast<ReferenceDescriptorDiagnosticKind>(0xFEu);
    CHECK(reference_descriptor_diagnostic_push(broken));

    ReferenceTraceEntry report{};
    report.kind = ReferenceCallbackKind::Report;
    report.dev_addr = 3;
    report.instance = 1;
    report.length = 8;
    CHECK(reference_trace_push(report));

    CdcWriter first;
    reference_service_cdc(first);
    CHECK(first.output.empty());

    // Retaining an entry nothing can render would stop every CDC line for
    // ever, which is the loudest possible way to lose the measurement.
    CdcWriter second;
    reference_service_cdc(second);
    CHECK_EQ(second.output, std::string{"REPORT a=3 i=1 len=8\r\n"});
}

TEST_CASE(the_diagnostic_capacity_is_a_power_of_two) {
    reference_queue_reset();
    ReferenceDescriptorDiagnostic entry{};
    std::size_t accepted = 0;
    while (reference_descriptor_diagnostic_push(entry)) {
        ++accepted;
        if (accepted > 64) {
            break;
        }
    }
    CHECK(accepted > 0);
    CHECK_EQ(accepted & (accepted - 1), static_cast<std::size_t>(0));
}

TEST_CASE(the_trace_capacity_is_a_power_of_two) {
    CHECK(kReferenceTraceCapacity > 0);
    CHECK_EQ(kReferenceTraceCapacity & (kReferenceTraceCapacity - 1),
             static_cast<std::size_t>(0));
}

// --------------------------------------------------------------------------
// The control-transfer packet trace
// --------------------------------------------------------------------------
//
// A poor man's logic analyser inside the RP2040. Pico-PIO-USB records one
// entry per DATA packet on a control endpoint, after the handshake has already
// been sent, into a static ring; Core 0 drains it here and turns it into text.
// Everything below is that rendering and its accounting - the half of the
// instrument that can be tested without hardware.

namespace {

struct FakeControlTrace final : IReferenceControlTraceSource {
    std::vector<ReferenceControlTraceEntry> entries;
    std::size_t head = 0;
    std::uint32_t lost_count = 0;
    std::size_t take_calls = 0;

    bool take(ReferenceControlTraceEntry& entry) override {
        ++take_calls;
        if (head >= entries.size()) {
            return false;
        }
        entry = entries[head++];
        return true;
    }

    std::uint32_t lost() override { return lost_count; }
};

//: Render exactly one control trace entry the way the CDC service does.
std::string render_control(const ReferenceControlTraceEntry& entry) {
    reference_queue_reset();
    FakeControlTrace source;
    source.entries.push_back(entry);
    reference_set_control_trace_source(&source);
    CdcWriter writer;
    reference_service_cdc(writer);
    reference_set_control_trace_source(nullptr);
    return writer.output;
}

ReferenceControlTraceEntry data_entry() {
    ReferenceControlTraceEntry entry{};
    entry.kind = ReferenceControlTraceKind::Data;
    entry.dev_addr = 2;
    entry.ep_num = 0x80;
    entry.seq = 8;
    entry.pid = 0x4Bu;  // DATA1
    entry.length = 0;
    entry.ep_size = 64;
    entry.actual_len = 0;
    entry.total_len = 64;
    entry.byte_count = 0;
    return entry;
}

}  // namespace

TEST_CASE(a_control_setup_entry_carries_the_request_that_caused_the_packets) {
    // Without this line a run of DATA packets cannot be attributed to the
    // request that produced it, and GET_DESCRIPTOR 0x22 cannot be isolated
    // from the enumeration traffic around it.
    ReferenceControlTraceEntry setup{};
    setup.kind = ReferenceControlTraceKind::Setup;
    setup.dev_addr = 2;
    setup.ep_num = 0;
    setup.seq = 7;
    setup.pid = 0xD2u;  // ACK
    setup.length = 8;
    const std::uint8_t request[] = {0x80, 0x06, 0x00, 0x22,
                                    0x00, 0x00, 0x40, 0x00};
    std::copy(std::begin(request), std::end(request), setup.bytes.begin());
    setup.byte_count = 8;

    CHECK_EQ(render_control(setup),
             std::string{"CTRL_SETUP a=2 ep=0 seq=7 pid=ACK "
                         "bytes=8006002200004000\r\n"});
}

TEST_CASE(a_control_data_entry_renders_the_measured_zero_length_packet) {
    // The ZLP prediction, written as the line an operator would read: a
    // DATA1 packet of zero bytes, acknowledged, against a 64-byte endpoint.
    CHECK_EQ(render_control(data_entry()),
             std::string{"CTRL_RX a=2 ep=128 seq=8 pid=DATA1 len=0 size=64 "
                         "act=0 tot=64 bytes=\r\n"});
}

TEST_CASE(every_control_trace_field_comes_from_its_own_source) {
    // Distinct values throughout, so no two fields can be swapped and still
    // pass. ep->size is the whole point of the size= field: EP0's maximum
    // packet size becomes measured rather than assumed.
    ReferenceControlTraceEntry entry = data_entry();
    entry.dev_addr = 3;
    entry.ep_num = 0x80;
    entry.seq = 41;
    entry.pid = 0xC3u;  // DATA0
    entry.length = 3;
    entry.ep_size = 8;
    entry.actual_len = 11;
    entry.total_len = 22;
    entry.bytes[0] = 0xDE;
    entry.bytes[1] = 0xAD;
    entry.bytes[2] = 0xBE;
    entry.byte_count = 3;

    CHECK_EQ(render_control(entry),
             std::string{"CTRL_RX a=3 ep=128 seq=41 pid=DATA0 len=3 size=8 "
                         "act=11 tot=22 bytes=DEADBE\r\n"});
}

TEST_CASE(a_truncated_control_payload_is_never_rendered_as_if_complete) {
    // A 64-byte packet of which only the first 16 bytes were kept must not
    // read as a 16-byte packet, and must not read as a complete dump either.
    ReferenceControlTraceEntry entry = data_entry();
    entry.length = 64;
    entry.actual_len = 64;
    entry.byte_count = static_cast<std::uint8_t>(entry.bytes.size());
    for (std::size_t index = 0; index < entry.bytes.size(); ++index) {
        entry.bytes[index] = static_cast<std::uint8_t>(index);
    }

    CHECK_EQ(render_control(entry),
             std::string{
                 "CTRL_RX a=2 ep=128 seq=8 pid=DATA1 len=64 size=64 act=64 "
                 "tot=64 bytes=000102030405060708090A0B0C0D0E0F+\r\n"});
}

TEST_CASE(a_control_done_entry_bounds_the_run_of_packets_before_it) {
    ReferenceControlTraceEntry done{};
    done.kind = ReferenceControlTraceKind::Done;
    done.dev_addr = 2;
    done.ep_num = 0x80;
    done.seq = 9;
    done.pid = 0x1E;
    done.actual_len = 0;
    done.total_len = 64;
    done.ep_size = 64;

    CHECK_EQ(render_control(done),
             std::string{"CTRL_DONE a=2 ep=128 seq=9 pid=STALL act=0 tot=64\r\n"});
}

TEST_CASE(every_descriptor_failure_reason_has_a_stable_r_token) {
    const struct {
        ReferenceDescriptorReason reason;
        const char* token;
    } cases[] = {
        {ReferenceDescriptorReason::Transfer, "xfer"},
        {ReferenceDescriptorReason::Gone, "gone"},
        {ReferenceDescriptorReason::TooLong, "toolong"},
        {ReferenceDescriptorReason::NoBuffer, "nobuf"},
        {ReferenceDescriptorReason::Unmounted, "unmounted"},
        {ReferenceDescriptorReason::NoInterface, "noitf"},
        {ReferenceDescriptorReason::NoOffer, "nooffers"},
        {ReferenceDescriptorReason::Overflow, "overflow"},
    };
    for (const auto& item : cases) {
        ReferenceDescriptorDiagnostic diagnostic{};
        diagnostic.kind = ReferenceDescriptorDiagnosticKind::Failure;
        diagnostic.reason = item.reason;
        diagnostic.requested = 64;
        CHECK_EQ(render_one(diagnostic),
                 std::string{"DESC64_FAIL actual=0 r="} + item.token + "\r\n");
    }
}

TEST_CASE(every_control_trace_pid_renders_by_name_or_by_value) {
    const struct {
        std::uint8_t pid;
        const char* rendered;
    } cases[] = {
        {0xC3u, "DATA0"}, {0x4Bu, "DATA1"}, {0xD2u, "ACK"},
        {0x5Au, "NAK"},   {0x1Eu, "STALL"}, {0x2Du, "SETUP"},
        {0x00u, "0x00"},  {0xABu, "0xAB"},
    };
    for (const auto& item : cases) {
        ReferenceControlTraceEntry data = data_entry();
        data.pid = item.pid;
        const std::string line = render_control(data);
        CHECK_EQ(line,
                 std::string{"CTRL_RX a=2 ep=128 seq=8 pid="} + item.rendered +
                     " len=0 size=64 act=0 tot=64 bytes=\r\n");
    }
}

TEST_CASE(a_lost_control_trace_entry_is_counted_and_reported_not_dropped) {
    reference_queue_reset();
    FakeControlTrace source;
    source.lost_count = 3;
    source.entries.push_back(data_entry());
    reference_set_control_trace_source(&source);

    // An announcement the writer had no room for is an announcement that did
    // not happen: the count must not be marked as reported until the line has
    // actually been written.
    CdcWriter cramped;
    cramped.space = 4;
    reference_service_cdc(cramped);
    CHECK(cramped.output.empty());

    CdcWriter first;
    reference_service_cdc(first);
    CHECK_EQ(first.output, std::string{"CTRL_LOST n=3\r\n"});

    // Announced once, at the point it was noticed - not once per pass, which
    // would bury the packets the loss is meant to qualify.
    CdcWriter second;
    reference_service_cdc(second);
    CHECK_EQ(second.output,
             std::string{"CTRL_RX a=2 ep=128 seq=8 pid=DATA1 len=0 size=64 "
                         "act=0 tot=64 bytes=\r\n"});

    CdcWriter third;
    reference_service_cdc(third);
    CHECK(third.output.empty());

    // A further loss is a further line, carrying only what was lost since.
    source.lost_count = 5;
    CdcWriter fourth;
    reference_service_cdc(fourth);
    CHECK_EQ(fourth.output, std::string{"CTRL_LOST n=2\r\n"});

    reference_set_control_trace_source(nullptr);
}

TEST_CASE(a_newly_installed_control_trace_source_counts_its_own_losses) {
    // The count belongs to the ring, not to this file. Carrying a previous
    // source's total across would make a fresh source with two losses report
    // nothing at all.
    reference_queue_reset();
    FakeControlTrace first;
    first.lost_count = 9;
    reference_set_control_trace_source(&first);
    CdcWriter announced;
    reference_service_cdc(announced);
    CHECK_EQ(announced.output, std::string{"CTRL_LOST n=9\r\n"});

    FakeControlTrace second;
    second.lost_count = 2;
    reference_set_control_trace_source(&second);
    CdcWriter again;
    reference_service_cdc(again);
    reference_set_control_trace_source(nullptr);
    CHECK_EQ(again.output, std::string{"CTRL_LOST n=2\r\n"});
}

TEST_CASE(control_trace_entries_are_serviced_in_the_order_they_were_recorded) {
    reference_queue_reset();
    FakeControlTrace source;
    for (std::uint32_t seq = 0; seq < 3; ++seq) {
        ReferenceControlTraceEntry entry = data_entry();
        entry.seq = seq;
        source.entries.push_back(entry);
    }
    reference_set_control_trace_source(&source);

    std::string output;
    for (int pass = 0; pass < 3; ++pass) {
        CdcWriter writer;
        reference_service_cdc(writer);
        output += writer.output;
    }
    reference_set_control_trace_source(nullptr);

    CHECK_EQ(output,
             std::string{
                 "CTRL_RX a=2 ep=128 seq=0 pid=DATA1 len=0 size=64 act=0 "
                 "tot=64 bytes=\r\n"
                 "CTRL_RX a=2 ep=128 seq=1 pid=DATA1 len=0 size=64 act=0 "
                 "tot=64 bytes=\r\n"
                 "CTRL_RX a=2 ep=128 seq=2 pid=DATA1 len=0 size=64 act=0 "
                 "tot=64 bytes=\r\n"});
}

TEST_CASE(a_control_trace_line_outranks_continuous_report_traffic) {
    reference_queue_reset();
    for (std::size_t index = 0; index < kReferenceTraceCapacity; ++index) {
        ReferenceTraceEntry report{};
        report.kind = ReferenceCallbackKind::Report;
        report.dev_addr = 9;
        report.instance = 1;
        report.length = 8;
        CHECK(reference_trace_push(report));
    }

    FakeControlTrace source;
    source.entries.push_back(data_entry());
    reference_set_control_trace_source(&source);

    CdcWriter writer;
    reference_service_cdc(writer);
    reference_set_control_trace_source(nullptr);

    CHECK_EQ(writer.output,
             std::string{"CTRL_RX a=2 ep=128 seq=8 pid=DATA1 len=0 size=64 "
                         "act=0 tot=64 bytes=\r\n"});
}

TEST_CASE(a_descriptor_measurement_still_outranks_the_control_trace) {
    reference_queue_reset();
    FakeControlTrace source;
    source.entries.push_back(data_entry());
    reference_set_control_trace_source(&source);

    ReferenceDescriptorDiagnostic diagnostic{};
    diagnostic.kind = ReferenceDescriptorDiagnosticKind::Start;
    diagnostic.requested = 64u;
    CHECK(reference_descriptor_diagnostic_push(diagnostic));

    CdcWriter writer;
    reference_service_cdc(writer);
    reference_set_control_trace_source(nullptr);

    CHECK_EQ(writer.output, std::string{"DESC64_START\r\n"});
}

TEST_CASE(a_control_trace_line_that_does_not_fit_is_retained_not_truncated) {
    reference_queue_reset();
    ReferenceTraceEntry report{};
    report.kind = ReferenceCallbackKind::Report;
    report.dev_addr = 3;
    report.instance = 1;
    report.length = 8;
    CHECK(reference_trace_push(report));

    FakeControlTrace source;
    source.entries.push_back(data_entry());
    reference_set_control_trace_source(&source);

    CdcWriter cramped;
    cramped.space = 4;
    reference_service_cdc(cramped);
    CHECK(cramped.output.empty());
    // And the report behind it must not be let past while the measurement
    // waits: out-of-order packets are worse than late ones.
    CHECK_EQ(cramped.write_calls, 0u);

    CdcWriter roomy;
    reference_service_cdc(roomy);
    reference_set_control_trace_source(nullptr);
    CHECK_EQ(roomy.output,
             std::string{"CTRL_RX a=2 ep=128 seq=8 pid=DATA1 len=0 size=64 "
                         "act=0 tot=64 bytes=\r\n"});
}

TEST_CASE(an_absent_control_trace_source_leaves_the_report_trace_alone) {
    reference_queue_reset();
    ReferenceTraceEntry report{};
    report.kind = ReferenceCallbackKind::Report;
    report.dev_addr = 3;
    report.instance = 1;
    report.length = 8;
    CHECK(reference_trace_push(report));

    CdcWriter writer;
    reference_service_cdc(writer);
    CHECK_EQ(writer.output, std::string{"REPORT a=3 i=1 len=8\r\n"});
}

TEST_CASE(the_widest_control_trace_line_still_ends_in_crlf_and_fits) {
    ReferenceControlTraceEntry widest{};
    widest.kind = ReferenceControlTraceKind::Data;
    widest.dev_addr = 255;
    widest.ep_num = 255;
    widest.seq = 4294967295u;
    widest.pid = 0x4Bu;
    widest.length = 65535u;
    widest.ep_size = 65535u;
    widest.actual_len = 65535u;
    widest.total_len = 65535u;
    widest.bytes.fill(0xFFu);
    widest.byte_count = static_cast<std::uint8_t>(widest.bytes.size());

    CHECK_EQ(render_control(widest),
             std::string{"CTRL_RX a=255 ep=255 seq=4294967295 pid=DATA1 "
                         "len=65535 size=65535 act=65535 tot=65535 "
                         "bytes=FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF+\r\n"});
}

TEST_CASE(the_desc77_follow_up_renders_with_its_own_token_prefix) {
    // Part 1's second measurement. Same grammar, its own prefix, so one boot
    // yields both the 64-byte and the 77-byte answer without either being
    // mistaken for the other.
    ReferenceDescriptorDiagnostic start{};
    start.kind = ReferenceDescriptorDiagnosticKind::Start;
    start.requested = 77u;
    CHECK_EQ(render_one(start), std::string{"DESC77_START\r\n"});

    ReferenceDescriptorDiagnostic mismatch{};
    mismatch.kind = ReferenceDescriptorDiagnosticKind::Mismatch;
    mismatch.requested = 77u;
    mismatch.actual_len = 77u;
    mismatch.first_difference = kNoDifferenceSentinel;
    mismatch.prefix_size = 4u;
    mismatch.prefix[0] = 0x81;
    mismatch.prefix[1] = 0x00;
    mismatch.prefix[2] = 0x05;
    mismatch.prefix[3] = 0xFF;
    CHECK_EQ(render_one(mismatch),
             std::string{"DESC77_MISMATCH actual=77 first=none "
                         "prefix=810005FF\r\n"});

    ReferenceDescriptorDiagnostic skip{};
    skip.kind = ReferenceDescriptorDiagnosticKind::Skip;
    skip.requested = 77u;
    skip.reason = ReferenceDescriptorReason::Unmounted;
    CHECK_EQ(render_one(skip), std::string{"DESC77_SKIP r=unmounted\r\n"});
}

TEST_CASE(the_descriptor_prefix_is_wide_enough_to_show_the_second_packet) {
    // The rotation prediction lives at buffer[13..20]. A prefix of 8 bytes
    // cannot show it, so the field is widened to at least 24 and the whole
    // width has to render.
    CHECK(kReferenceDescriptorPrefix >= 24u);

    ReferenceDescriptorDiagnostic entry{};
    entry.kind = ReferenceDescriptorDiagnosticKind::Mismatch;
    entry.requested = 77u;
    entry.actual_len = 77u;
    entry.first_difference = 0u;
    const std::uint8_t rotated[24] = {
        0x81, 0x00, 0x05, 0xFF, 0x09, 0x03, 0x75, 0x08, 0x95, 0x01, 0x81, 0x02,
        0xC0, 0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x05, 0x08, 0x19, 0x01, 0x29};
    // Bounded by the field being tested, so a narrowed prefix fails the check
    // above rather than overrunning the array and taking the suite with it.
    const std::size_t copied =
        std::min<std::size_t>(sizeof(rotated), entry.prefix.size());
    std::copy_n(std::begin(rotated), copied, entry.prefix.begin());
    entry.prefix_size = static_cast<std::uint8_t>(copied);

    CHECK_EQ(render_one(entry),
             std::string{"DESC77_MISMATCH actual=77 first=0 "
                         "prefix=810005FF0903750895018102C005010906A101050819"
                         "0129\r\n"});
}

// The link to U2, as a line somebody can read.
//
// Task 4 puts SPI on Core 0, and this board has no LED that says whether the
// far end answered - the RP2040-Zero's only lamp is a WS2812 nothing here
// drives. Without a line on the wire, "U2 is answering" is an operator's
// impression and not a measurement, which is exactly the kind of closure this
// project has already had to record honestly once.
//
// One retained slot rather than a queue: the newest reading is the only
// interesting one, and a link polled every pass would otherwise bury the
// report trace under a thousand identical lines a second.

TEST_CASE(a_link_status_nobody_published_prints_nothing) {
    reference_queue_reset();

    CdcWriter writer;
    reference_service_cdc(writer);

    CHECK(writer.output.empty());
}

TEST_CASE(a_published_link_status_prints_one_line) {
    reference_queue_reset();

    ReferenceLinkStatus status{};
    status.answered = true;
    status.frames_sent = 42;
    status.crc_errors = 3;
    status.echoed_frames = 1;
    status.endpoint_drops = 2;
    status.endpoint_release_ms = 100;
    reference_link_status_publish(status);

    CdcWriter writer;
    reference_service_cdc(writer);

    CHECK(writer.output == "LINK ans=1 tx=42 crc=3 echo=1 drops=2 rel=100\r\n");
}

TEST_CASE(a_link_that_never_answered_says_so_rather_than_saying_nothing) {
    reference_queue_reset();

    ReferenceLinkStatus status{};
    status.answered = false;
    reference_link_status_publish(status);

    CdcWriter writer;
    reference_service_cdc(writer);

    CHECK(writer.output == "LINK ans=0 tx=0 crc=0 echo=0 drops=0 rel=0\r\n");
}

TEST_CASE(only_the_newest_link_status_is_printed) {
    reference_queue_reset();

    ReferenceLinkStatus older{};
    older.frames_sent = 1;
    reference_link_status_publish(older);

    ReferenceLinkStatus newer{};
    newer.answered = true;
    newer.frames_sent = 9;
    reference_link_status_publish(newer);

    CdcWriter writer;
    reference_service_cdc(writer);

    CHECK(writer.output == "LINK ans=1 tx=9 crc=0 echo=0 drops=0 rel=0\r\n");
}

TEST_CASE(a_link_status_is_printed_once_and_not_repeated) {
    reference_queue_reset();

    ReferenceLinkStatus status{};
    status.answered = true;
    reference_link_status_publish(status);

    CdcWriter first;
    reference_service_cdc(first);
    CHECK(!first.output.empty());

    CdcWriter second;
    reference_service_cdc(second);
    CHECK(second.output.empty());
}

TEST_CASE(a_link_status_is_printed_ahead_of_report_traffic) {
    reference_queue_reset();

    const std::uint8_t report[] = {0x01, 0x02};
    ReferenceTraceEntry entry =
        reference_trace_from(report_record(2, 0, report, sizeof(report)));
    CHECK(reference_trace_push(entry));

    ReferenceLinkStatus status{};
    status.answered = true;
    reference_link_status_publish(status);

    CdcWriter writer;
    reference_service_cdc(writer);

    CHECK(writer.output.rfind("LINK ", 0) == 0);
}
