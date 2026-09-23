from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from fp_perf_metrics import (
    PerfEvent,
    analyze,
    burst_accounting,
    concurrency_sweep,
    interval_union_ns,
    nearest_rank,
    parse_perf_lines,
    write_artifacts,
)


MS = 1_000_000
OSLOG_PREFIX = "2026-09-22 10:11:12.123 process[1] [perf] "
PERF_LINE = (
    "fp_perf event=fetch_enter mono_ns=1000000000 clock=swift_uptime "
    "entry_index=2 transfer_id=generation"
)


def event(source: str, name: str, ms: int, clock: str, **fields: object) -> PerfEvent:
    return PerfEvent(
        source=source,
        name=name,
        mono_ns=ms * MS,
        clock=clock,
        fields={key: str(value) for key, value in fields.items()},
    )


def dataset(count: int = 3) -> dict:
    return {
        "FILE_COUNT": count,
        "TOTAL_BYTES": count * 2_000,
        "MIN_FILE_SIZE": 2_000,
        "MEDIAN_FILE_SIZE": 2_000,
        "P95_FILE_SIZE": 2_000,
        "MAX_FILE_SIZE": 2_000,
        "files": [
            {"item": f"gen:{index}", "size": 2_000, "relative_path": f"f{index}"}
            for index in range(count)
        ],
    }


def correctness_ok(count: int = 3) -> dict:
    return {
        "EXPECTED_FILE_COUNT": count,
        "ACTUAL_FILE_COUNT": count,
        "BYTE_EXACT": True,
        "MISSING": [],
        "DIFF": [],
        "REFETCH_COUNT": 0,
        "-1005": 0,
        "-1004": 0,
        "-1000": 0,
        "Finder -36": 0,
    }


def synthetic_events(include_windows: bool = True) -> list[PerfEvent]:
    events: list[PerfEvent] = []
    intervals = [(0, 60), (10, 40), (20, 30)]
    for index, (start, end) in enumerate(intervals):
        item = f"gen:{index}"
        token = f"tok{index}"
        fields = {"item_identifier": item, "transfer_id": "gen", "entry_index": index}
        events.extend(
            [
                event("extension", "fetch_enter", start, "swift_uptime", **fields),
                event("extension", "open_fetch_call_begin", start + 1, "swift_uptime", fetch_token="none", **fields),
                event("extension", "open_fetch_reply", start + 3, "swift_uptime", fetch_token=token, status="ok", **fields),
                event("extension", "pull_call_begin", start + 4, "swift_uptime", fetch_token=token, pull_sequence=1, **fields),
                event("extension", "pull_reply", start + 6, "swift_uptime", fetch_token=token, pull_sequence=1, bytes=2000, eof="true", status="ok", **fields),
                event("extension", "chunk_write_complete", start + 7, "swift_uptime", fetch_token=token, pull_sequence=1, bytes=2000, **fields),
                event("extension", "first_write_complete", start + 7, "swift_uptime", fetch_token=token, bytes=2000, **fields),
                event("extension", "last_write_complete", start + 7, "swift_uptime", fetch_token=token, bytes=2000, **fields),
                event("extension", "fsync_complete", end - 3, "swift_uptime", fetch_token=token, **fields),
                event("extension", "close_complete", end - 2, "swift_uptime", fetch_token=token, **fields),
                event("extension", "finalize_complete", end - 1, "swift_uptime", fetch_token=token, **fields),
                event("extension", "completion_call", end, "swift_uptime", fetch_token=token, status="ok", **fields),
            ]
        )

    read_intervals = [(0, 20), (10, 30), (40, 50), (45, 50)]
    for read_id, (start, end) in enumerate(read_intervals, 1):
        index = min(read_id - 1, 2)
        token = f"tok{index}"
        common = {"transfer_id": "gen", "entry_index": index, "fetch_token": token, "read_id": read_id}
        events.append(event("mac", "file_read_send", start, "mac_python_monotonic", offset=0, length=2000, **common))
        events.append(event("mac", "file_chunk_receive", end, "mac_python_monotonic", offset=0, bytes=2000, **common))
        if include_windows:
            events.extend(
                [
                    event("windows", "file_read_receive", start + 100, "windows_python_monotonic", offset=0, length=2000, **common),
                    event("windows", "snapshot_lookup_begin", start + 101, "windows_python_monotonic", **common),
                    event("windows", "snapshot_lookup_end", start + 102, "windows_python_monotonic", **common),
                    event("windows", "source_read_begin", start + 102, "windows_python_monotonic", **common),
                    event("windows", "source_read_end", start + 104, "windows_python_monotonic", status="ok", bytes=2000, **common),
                    event("windows", "file_chunk_send", start + 105, "windows_python_monotonic", bytes=2000, **common),
                ]
            )

    for index in range(3):
        token = f"tok{index}"
        common = {"generation_id": "gen", "transfer_id": "gen", "entry_index": index, "fetch_token": token}
        events.extend(
            [
                event("mac", "open_fetch_enter", index, "mac_python_monotonic", **common),
                event("mac", "slot_acquired", index, "mac_python_monotonic", queued="false", **common),
                event("mac", "open_fetch_reply", index + 1, "mac_python_monotonic", size=2000, status="ok", **common),
                event("mac", "slot_released", 70 + index, "mac_python_monotonic", state="done", **common),
            ]
        )
    events.extend(
        [
            event("extension", "eviction_attempt", 25, "swift_uptime", item_identifier="gen:0", transfer_id="gen", attempt=1, retry_count=0),
            event("extension", "eviction_success", 26, "swift_uptime", item_identifier="gen:0", transfer_id="gen", attempt=1, retry_count=0),
        ]
    )
    return events


def test_parser_extracts_perf_event_from_prefixed_oslog_line():
    [parsed] = parse_perf_lines([OSLOG_PREFIX + PERF_LINE], source="extension")
    assert parsed.name == "fetch_enter"
    assert parsed.mono_ns == 1_000_000_000
    assert parsed.clock == "swift_uptime"


def test_parser_collects_malformed_perf_lines():
    parsed = parse_perf_lines(["prefix fp_perf event=broken clock=x"], source="mac")
    assert parsed == []
    assert len(parsed.parse_errors) == 1


def test_burst_accounting_keeps_physical_fetches_and_eviction_refetch_visible():
    events = [
        event("extension", "fetch_enter", 0, "swift_uptime", item_identifier="gen:0", transfer_id="gen", entry_index=0, perf_fetch_id="a", scheduler_origin="FINDER", fetch_classification="NO_PREFETCH_REQUEST_RECORDED", duplicate_active="false", wave_id="none"),
        event("extension", "open_fetch_reply", 1, "swift_uptime", item_identifier="gen:0", transfer_id="gen", entry_index=0, perf_fetch_id="a", origin="FINDER", fetch_token="t0", status="ok"),
        event("extension", "fetch_enter", 2, "swift_uptime", item_identifier="gen:1", transfer_id="gen", entry_index=1, perf_fetch_id="b", scheduler_origin="FINDER", fetch_classification="NO_PREFETCH_REQUEST_RECORDED", duplicate_active="false", wave_id="none"),
        event("extension", "finder_burst_triggered", 3, "swift_uptime", transfer_id="gen", perf_fetch_id="b", trigger_item="gen:1", wave_id=1, request_count=2),
        event("extension", "request_download_call", 4, "swift_uptime", item_identifier="gen:2", transfer_id="gen", trigger_item="gen:1", wave_id=1, wave_position=1),
        event("extension", "open_fetch_reply", 4, "swift_uptime", item_identifier="gen:1", transfer_id="gen", entry_index=1, perf_fetch_id="b", origin="FINDER", fetch_token="t1", status="ok"),
        event("extension", "fetch_enter", 5, "swift_uptime", item_identifier="gen:2", transfer_id="gen", entry_index=2, perf_fetch_id="c", scheduler_origin="BURST", fetch_classification="KNOWN_PREFETCH_REQUESTED", duplicate_active="false", wave_id=1, wave_position=1, trigger_item="gen:1"),
        event("extension", "open_fetch_reply", 6, "swift_uptime", item_identifier="gen:2", transfer_id="gen", entry_index=2, perf_fetch_id="c", origin="BURST", fetch_token="t2", status="ok"),
        event("extension", "eviction_success", 7, "swift_uptime", item_identifier="gen:2", transfer_id="gen"),
        event("extension", "fetch_enter", 8, "swift_uptime", item_identifier="gen:2", transfer_id="gen", entry_index=2, perf_fetch_id="d", scheduler_origin="FINDER", fetch_classification="NO_PREFETCH_REQUEST_RECORDED", duplicate_active="true", wave_id="none"),
        event("extension", "open_fetch_reply", 9, "swift_uptime", item_identifier="gen:2", transfer_id="gen", entry_index=2, perf_fetch_id="d", origin="FINDER", fetch_token="t3", status="ok"),
    ]

    assert burst_accounting(events) == {
        "UNIQUE_ITEMS": 3,
        "FETCH_CONTENTS_INVOCATIONS": 4,
        "FILE_READ_SEQUENCES": 4,
        "KNOWN_PREFETCH_REQUESTED_FETCHES": 1,
        "NO_PREFETCH_REQUEST_RECORDED_FETCHES": 3,
        "AMBIGUOUS_FETCHES": 0,
        "REQUEST_DOWNLOAD_CALLS": 1,
        "DUPLICATE_FETCH_ITEMS": 1,
        "REFETCHED_ITEMS": 1,
        "EXTRA_FILE_READS": 1,
        "EVICTION_SUCCESS_THEN_REFETCH": 1,
        "WAVES": 1,
        "MAX_WAVE_SIZE": 2,
        "MAX_SPECULATIVE_LEAD_ITEMS": 1,
        "BURST_COMPLETION_TRIGGERED_NEXT_WAVE": "NO",
    }


def test_speculative_lead_counts_wave_positions_not_sparse_manifest_distance():
    events = [
        event(
            "extension", "fetch_enter", position, "swift_uptime",
            item_identifier=f"gen:{index}", transfer_id="gen", entry_index=index,
            perf_fetch_id=f"burst-{position}", scheduler_origin="BURST",
            fetch_classification="KNOWN_PREFETCH_REQUESTED", duplicate_active="false",
            wave_id=1, wave_position=position, trigger_item="gen:27",
        )
        for position, index in enumerate(
            [28, 29, 30, 31, 33, 34, 35, 36], start=1
        )
    ]

    assert burst_accounting(events)["MAX_SPECULATIVE_LEAD_ITEMS"] == 8


def test_analyzer_never_subtracts_different_clock_domains():
    events = synthetic_events(include_windows=False)
    result = analyze(events, dataset(), correctness_ok())
    assert result.xpc_open_fetch_ms == [2.0, 2.0, 2.0]
    assert result.windows_internal_breakdown == "UNAVAILABLE"
    assert result.network_plus_dispatch == "UNAVAILABLE"


def test_nearest_rank_and_interval_union_are_deterministic():
    assert nearest_rank([9, 1, 5, 3], 50) == 3
    assert nearest_rank([9, 1, 5, 3], 95) == 9
    assert nearest_rank([], 50) is None
    assert interval_union_ns([(0, 20), (10, 30), (40, 50), (45, 50)]) == 40


def test_interval_union_and_active_throughput_use_only_outstanding_reads():
    result = analyze(synthetic_events(), dataset(), correctness_ok())
    assert result.wire_active_time_ms == 40.0
    assert result.received_bytes == 8_000
    assert result.active_transfer_throughput_bytes_per_second == 200_000.0


def test_concurrency_integral_and_distribution_are_time_weighted():
    result = analyze(synthetic_events(), dataset(), correctness_ok())
    assert result.max_active_fetches == 3
    assert result.active_fetch_time_ms == {0: 0.0, 1: 30.0, 2: 20.0, 3: 10.0, "ge4": 0.0}
    assert result.avg_active_fetches == pytest.approx(5 / 3)
    direct = concurrency_sweep([(0, 10 * MS), (20 * MS, 30 * MS)])
    assert direct["time_ms"][0] == 10.0


def test_comparison_totals_measure_idle_and_sum_active_transfer_time():
    events = [
        PerfEvent(item.source, item.name, item.mono_ns + 5 * MS, item.clock, item.fields)
        if item.name == "last_write_complete"
        and item.fields.get("entry_index") in {"0", "1"}
        else item
        for item in synthetic_events()
    ]
    result = analyze(events, dataset(), correctness_ok())

    assert result.aggregates["INTER_FETCH_IDLE_TOTAL"] == 0.0
    assert result.aggregates["ACTIVE_BYTE_TRANSFER_TOTAL"] == 10.0


def test_chunks_windows_queue_eviction_and_top_five_are_measured():
    events = synthetic_events()
    # Turn the third host fetch into a measurable queue wait.
    events = [event for event in events if not (event.name == "slot_acquired" and event.fields.get("entry_index") == "2")]
    events.extend(
        [
            event("mac", "queue_enter", 2, "mac_python_monotonic", generation_id="gen", transfer_id="gen", entry_index=2, fetch_token="tok2"),
            event("mac", "slot_acquired", 7, "mac_python_monotonic", generation_id="gen", transfer_id="gen", entry_index=2, fetch_token="tok2", queued="true"),
        ]
    )
    result = analyze(events, dataset(), correctness_ok())
    assert result.queue_wait_ms == [0.0, 0.0, 5.0]
    assert result.avg_chunks_per_file == pytest.approx(4 / 3)
    assert result.windows_read_service_time_ms == [5.0] * 4
    assert result.eviction_on_critical_path == "NO"
    assert result.aggregates["LOCAL_WRITE_P50"] == 1.0
    assert str(result.network_plus_dispatch).startswith("AGGREGATE_DURATION_RESIDUAL_P50=")
    assert result.top_five[0].fetch_total_ms == 60.0


def test_eviction_is_unknown_without_overlap_evidence():
    events = [event for event in synthetic_events() if not event.name.startswith("eviction_")]
    result = analyze(events, dataset(), correctness_ok())
    assert result.eviction_on_critical_path == "UNKNOWN"


def test_duplicate_successful_item_fetch_is_not_silently_merged():
    events = synthetic_events()
    events.append(event("extension", "completion_call", 80, "swift_uptime", item_identifier="gen:0", transfer_id="gen", entry_index=0, fetch_token="another", status="ok"))
    result = analyze(events, dataset(), correctness_ok())
    assert result.refetch_count == 1
    assert result.performance_baseline_valid is False


def test_parse_error_invalidates_baseline():
    parsed = parse_perf_lines([PERF_LINE, "fp_perf event=broken mono_ns=nope clock=x"], "extension")
    result = analyze(parsed, dataset(1), correctness_ok(1))
    assert result.performance_baseline_valid is False


def test_report_contains_required_stop_fields_and_unavailable_markers(tmp_path: Path):
    result = analyze(synthetic_events(include_windows=False), dataset(), correctness_ok())
    paths = write_artifacts(result, tmp_path)
    report = paths.report.read_text()
    assert "PERFORMANCE_BASELINE_VALID = YES" in report
    assert "WINDOWS_INTERNAL_BREAKDOWN = UNAVAILABLE" in report
    assert "DESTINATION_MATERIALIZATION_TIMESTAMP = UNAVAILABLE" in report
    assert "TOP 5 slowest" in report
    assert len(list(csv.DictReader(paths.csv.open()))) == 3
    payload = json.loads(paths.json.read_text())
    assert payload["aggregates"]["FILES"] == 3


def test_checked_in_fixture_logs_parse_without_errors():
    fixture_dir = Path(__file__).parent / "fixtures" / "fp_perf"
    for source in ("extension", "mac", "windows"):
        parsed = parse_perf_lines(
            (fixture_dir / f"synthetic-{source}.log").read_text().splitlines(), source
        )
        assert parsed
        assert parsed.parse_errors == []
