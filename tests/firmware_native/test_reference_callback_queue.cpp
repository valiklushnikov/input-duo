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

#include <cstdint>
#include <cstring>

#include "callback_queue.hpp"
#include "test_support.hpp"

namespace {

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
        CHECK(reference_trace_push(report));
    }

    ReferenceDescriptorDiagnostic diagnostic{};
    diagnostic.kind = ReferenceDescriptorDiagnosticKind::Mismatch;
    diagnostic.actual_len = 63u;
    diagnostic.first_difference = 63u;
    diagnostic.prefix[0] = 0x05u;
    diagnostic.prefix_size = 1u;
    CHECK(reference_descriptor_diagnostic_push(diagnostic));

    ReferenceDescriptorDiagnostic taken{};
    CHECK(reference_descriptor_diagnostic_take(taken));
    CHECK(taken.kind == ReferenceDescriptorDiagnosticKind::Mismatch);
    CHECK_EQ(taken.actual_len, 63u);
    CHECK_EQ(taken.first_difference, 63u);
    CHECK_EQ(taken.prefix[0], 0x05u);
}

TEST_CASE(the_trace_capacity_is_a_power_of_two) {
    CHECK(kReferenceTraceCapacity > 0);
    CHECK_EQ(kReferenceTraceCapacity & (kReferenceTraceCapacity - 1),
             static_cast<std::size_t>(0));
}
