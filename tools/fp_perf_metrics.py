#!/usr/bin/env python3
"""Offline analysis for File Provider monotonic performance traces."""

from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence


UNAVAILABLE = "UNAVAILABLE"
NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class PerfEvent:
    source: str
    name: str
    mono_ns: int
    clock: str
    fields: dict[str, str]


class ParsedEvents(list[PerfEvent]):
    def __init__(self, events: Iterable[PerfEvent] = (), parse_errors: Iterable[str] = ()):
        super().__init__(events)
        self.parse_errors = list(parse_errors)


@dataclass
class FetchMetrics:
    item: str
    transfer_id: str
    entry_index: int
    size: int
    chunks: int
    fetch_total_ms: float | None
    open_fetch_ms: float | None
    first_byte_ms: float | None
    transfer_ms: float | None
    finalize_ms: float | None
    queue_wait_ms: float | None
    max_concurrency_at_start: int | None
    requested_chunk_sizes: list[int] = field(default_factory=list)
    received_chunk_sizes: list[int] = field(default_factory=list)
    fetch_startup_ms: float | None = None
    host_dispatch_ms: float | None = None
    read_to_first_chunk_ms: float | None = None
    fsync_ms: float | None = None
    finalization_ms: float | None = None


@dataclass
class ArtifactPaths:
    json: Path
    csv: Path
    report: Path


@dataclass
class RunAnalysis:
    events: list[PerfEvent]
    parse_errors: list[str]
    dataset: dict
    correctness: dict
    per_file: list[FetchMetrics]
    aggregates: dict[str, object]
    xpc_open_fetch_ms: list[float]
    queue_wait_ms: list[float]
    read_chunk_rtt_ms: list[float]
    windows_read_service_time_ms: list[float]
    wire_active_time_ms: float
    received_bytes: int
    active_transfer_throughput_bytes_per_second: float | None
    max_active_fetches: int | None
    avg_active_fetches: float | None
    active_fetch_time_ms: dict[object, float]
    avg_chunks_per_file: float | None
    eviction_on_critical_path: str
    windows_internal_breakdown: str
    network_plus_dispatch: str
    refetch_count: int
    performance_baseline_valid: bool
    finder_serialization: str
    internal_serialization: str
    primary_bottleneck: str
    secondary_bottleneck: str
    classification_evidence: list[str]
    top_five: list[FetchMetrics]
    contributions: dict[str, str]
    optimization_candidates: list[dict[str, str]]
    transport: dict[str, object]


def parse_perf_lines(lines: Iterable[str], source: str) -> ParsedEvents:
    events: list[PerfEvent] = []
    errors: list[str] = []
    for line_number, raw in enumerate(lines, 1):
        marker = raw.find("fp_perf ")
        if marker < 0:
            continue
        body = raw[marker + len("fp_perf ") :].strip()
        atoms: dict[str, str] = {}
        malformed = False
        for atom in body.split():
            if "=" not in atom:
                malformed = True
                break
            key, value = atom.split("=", 1)
            if not key or not value or key in atoms:
                malformed = True
                break
            atoms[key] = value
        try:
            name = atoms.pop("event")
            mono_ns = int(atoms.pop("mono_ns"))
            clock = atoms.pop("clock")
            if mono_ns < 0 or malformed:
                raise ValueError
        except (KeyError, ValueError):
            errors.append(f"{source}:{line_number}: malformed fp_perf event")
            continue
        events.append(PerfEvent(source, name, mono_ns, clock, atoms))
    return ParsedEvents(events, errors)


def nearest_rank(values: Sequence[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered) / 100) - 1)
    return ordered[index]


def interval_union_ns(intervals: Iterable[tuple[int, int]]) -> int:
    ordered = sorted((start, end) for start, end in intervals if end >= start)
    if not ordered:
        return 0
    total = 0
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            total += current_end - current_start
            current_start, current_end = start, end
    return total + current_end - current_start


def concurrency_sweep(intervals: Iterable[tuple[int, int]]) -> dict[str, object]:
    observed = [(start, end) for start, end in intervals if end >= start]
    if not observed:
        return {"max": None, "average": None, "time_ms": {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0, "ge4": 0.0}}
    # Windows' monotonic clock can legitimately quantize a short synchronous
    # handler to start == end. Such an interval contributes no measurable
    # duration and cannot overlap another half-open interval, but it still
    # proves that one operation ran. Excluding it from the boundary sweep also
    # prevents the usual "end before start at equal timestamps" ordering from
    # temporarily producing an impossible active count of -1.
    valid = [(start, end) for start, end in observed if end > start]
    if not valid:
        return {
            "max": 1,
            "average": 1.0,
            "time_ms": {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0, "ge4": 0.0},
        }
    boundaries: list[tuple[int, int]] = []
    for start, end in valid:
        boundaries.extend(((start, 1), (end, -1)))
    # At equal timestamps, completions precede starts, avoiding false overlap.
    boundaries.sort(key=lambda pair: (pair[0], pair[1]))
    buckets_ns: dict[object, int] = {0: 0, 1: 0, 2: 0, 3: 0, "ge4": 0}
    active = 0
    maximum = 0
    integral = 0
    previous = boundaries[0][0]
    for stamp, delta in boundaries:
        duration = stamp - previous
        bucket: object = active if active < 4 else "ge4"
        buckets_ns[bucket] += duration
        integral += active * duration
        active += delta
        maximum = max(maximum, active)
        previous = stamp
    span = boundaries[-1][0] - boundaries[0][0]
    return {
        "max": maximum,
        "average": integral / span if span else float(maximum),
        "time_ms": {key: value / 1_000_000 for key, value in buckets_ns.items()},
    }


def _item_id(event: PerfEvent) -> str | None:
    item = event.fields.get("item_identifier") or event.fields.get("item")
    if item:
        return item
    transfer = event.fields.get("transfer_id") or event.fields.get("generation_id")
    index = event.fields.get("entry_index")
    return f"{transfer}:{index}" if transfer is not None and index is not None else None


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _duration_ms(start: PerfEvent | None, end: PerfEvent | None) -> float | None:
    if (
        start is None
        or end is None
        or start.source != end.source
        or start.clock != end.clock
        or end.mono_ns < start.mono_ns
    ):
        return None
    return (end.mono_ns - start.mono_ns) / 1_000_000


def _first(events: Sequence[PerfEvent], name: str, *, status: str | None = None) -> PerfEvent | None:
    candidates = [e for e in events if e.name == name and (status is None or e.fields.get("status") == status)]
    return min(candidates, key=lambda e: e.mono_ns) if candidates else None


def _last(events: Sequence[PerfEvent], name: str, *, status: str | None = None) -> PerfEvent | None:
    candidates = [e for e in events if e.name == name and (status is None or e.fields.get("status") == status)]
    return max(candidates, key=lambda e: e.mono_ns) if candidates else None


def _distribution(values: Sequence[float]) -> tuple[float | None, float | None, float | None]:
    return nearest_rank(values, 50), nearest_rank(values, 95), max(values) if values else None


def _truth(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.upper() in {"YES", "TRUE"})


def _correctness_valid(correctness: dict, expected: int, refetch: int) -> bool:
    return (
        _int(str(correctness.get("EXPECTED_FILE_COUNT", -1))) == expected
        and _int(str(correctness.get("ACTUAL_FILE_COUNT", -2))) == expected
        and _truth(correctness.get("BYTE_EXACT"))
        and not correctness.get("MISSING")
        and not correctness.get("DIFF")
        and refetch == 0
        and all(_int(str(correctness.get(key, -1))) == 0 for key in ("-1005", "-1004", "-1000", "Finder -36"))
    )


def burst_accounting(events: Iterable[PerfEvent]) -> dict[str, object]:
    """Account for logical fetches, physical reads, waves, and eviction refetches.

    This intentionally does not merge by item: every fetchContents invocation
    remains visible through its logging-only perf_fetch_id.
    """
    extension = [event for event in events if event.source == "extension"]
    fetches = [event for event in extension if event.name == "fetch_enter"]
    by_item: dict[str, list[PerfEvent]] = {}
    by_fetch_id: dict[str, PerfEvent] = {}
    for fetch in fetches:
        item = _item_id(fetch)
        if item is not None:
            by_item.setdefault(item, []).append(fetch)
        fetch_id = fetch.fields.get("perf_fetch_id")
        if fetch_id:
            by_fetch_id[fetch_id] = fetch

    tokens = {
        (event.fields.get("transfer_id", ""), token)
        for event in extension
        if event.name == "open_fetch_reply"
        and event.fields.get("status") == "ok"
        and (token := event.fields.get("fetch_token")) not in (None, "none")
    }
    wave_events = [event for event in extension if event.name == "finder_burst_triggered"]
    waves = {
        (event.fields.get("transfer_id", ""), event.fields.get("wave_id", ""))
        for event in wave_events
    }
    wave_sizes = [_int(event.fields.get("request_count")) or 0 for event in wave_events]

    leads: list[int] = []
    for fetch in fetches:
        if (fetch.fields.get("scheduler_origin") or fetch.fields.get("origin")) != "BURST":
            continue
        wave_position = _int(fetch.fields.get("wave_position"))
        if wave_position is not None:
            leads.append(wave_position)

    eviction_refetch_items = 0
    for item, item_fetches in by_item.items():
        ordered_fetches = sorted(event.mono_ns for event in item_fetches)
        if len(ordered_fetches) < 2:
            continue
        evictions = sorted(
            event.mono_ns
            for event in extension
            if event.name == "eviction_success" and _item_id(event) == item
        )
        if any(
            earlier < eviction < later
            for earlier, later in zip(ordered_fetches, ordered_fetches[1:])
            for eviction in evictions
        ):
            eviction_refetch_items += 1

    recursive = "NO"
    for wave in wave_events:
        trigger_fetch = by_fetch_id.get(wave.fields.get("perf_fetch_id", ""))
        if trigger_fetch is None or (
            trigger_fetch.fields.get("scheduler_origin") or trigger_fetch.fields.get("origin")
        ) != "FINDER":
            recursive = "YES"
            break

    unique_items = len(by_item)
    refetched_items = sum(len(item_fetches) > 1 for item_fetches in by_item.values())
    duplicate_items = {
        _item_id(event)
        for event in fetches
        if event.fields.get("duplicate_active") == "true" and _item_id(event) is not None
    }
    classifications = [event.fields.get("fetch_classification") for event in fetches]
    return {
        "UNIQUE_ITEMS": unique_items,
        "FETCH_CONTENTS_INVOCATIONS": len(fetches),
        "FILE_READ_SEQUENCES": len(tokens),
        "KNOWN_PREFETCH_REQUESTED_FETCHES": classifications.count("KNOWN_PREFETCH_REQUESTED")
            or sum(event.fields.get("origin") == "BURST" for event in fetches),
        "NO_PREFETCH_REQUEST_RECORDED_FETCHES": classifications.count("NO_PREFETCH_REQUEST_RECORDED")
            or sum(event.fields.get("origin") == "FINDER" for event in fetches),
        "AMBIGUOUS_FETCHES": classifications.count("AMBIGUOUS"),
        "REQUEST_DOWNLOAD_CALLS": sum(event.name == "request_download_call" for event in extension),
        "DUPLICATE_FETCH_ITEMS": len(duplicate_items),
        "REFETCHED_ITEMS": refetched_items,
        "EXTRA_FILE_READS": max(0, len(tokens) - unique_items),
        "EVICTION_SUCCESS_THEN_REFETCH": eviction_refetch_items,
        "WAVES": len(waves),
        "MAX_WAVE_SIZE": max(wave_sizes, default=0),
        "MAX_SPECULATIVE_LEAD_ITEMS": max(leads, default=0),
        "BURST_COMPLETION_TRIGGERED_NEXT_WAVE": recursive,
    }


def transport_breakdown(
    events: Iterable[PerfEvent], *, total_bytes: int = 0
) -> dict[str, object]:
    """Decompose FILE_READ/FILE_CHUNK without crossing clock domains.

    Windows stage durations use only Windows events. Mac RTT and inflight
    concurrency use only Mac events. The two timelines are joined solely by
    the existing ``(transfer_id, read_id)`` identity.
    """
    event_list = list(events)
    groups: dict[tuple[str, str], list[PerfEvent]] = {}
    for perf_event in event_list:
        transfer = perf_event.fields.get("transfer_id") or perf_event.fields.get(
            "generation_id"
        )
        read_id = perf_event.fields.get("read_id")
        if transfer is not None and read_id is not None:
            groups.setdefault((transfer, read_id), []).append(perf_event)

    stage_names = {
        "DECODE": ("file_read_bytes_available", "file_read_decode_complete"),
        "DISPATCH": ("file_read_decode_complete", "file_read_handler_enter"),
        "SNAPSHOT_LOOKUP": ("snapshot_lookup_begin", "snapshot_lookup_end"),
        "SOURCE_OPEN": ("source_open_begin", "source_open_end"),
        "SOURCE_READ": ("source_read_begin", "source_read_end"),
        "CHUNK_ENCODE": ("source_read_end", "file_chunk_encode_complete"),
        "WINDOWS_PROCESSING": ("file_read_bytes_available", "outbound_enqueue"),
        "OUTBOUND_QUEUE_WAIT": ("outbound_enqueue", "outbound_plaintext_dequeue"),
        "SOCKET_WRITE": ("socket_write_begin", "socket_write_complete"),
        "ENCRYPTION_TIME": (
            "outbound_plaintext_dequeue",
            "outbound_frame_encrypted",
        ),
        "INBOUND_QUEUE_WAIT": ("file_read_bytes_available", "file_read_handler_enter"),
    }
    samples: dict[str, list[float]] = {name: [] for name in stage_names}
    mac_rtts: list[float] = []
    first_mac_rtts: list[float] = []
    subsequent_mac_rtts: list[float] = []
    first_windows: list[float] = []
    first_open: list[float] = []
    first_read: list[float] = []
    first_outbound_queue_wait: list[float] = []
    approximate_transport_residual: list[float] = []
    event_loop_lag: list[float] = []
    request_sizes: list[int] = []
    chunk_sizes: list[int] = []
    source_bytes = 0
    fd_open_count = 0
    fd_reuse_count = 0
    mac_intervals: list[tuple[int, int]] = []
    mac_intervals_by_file: dict[str, list[tuple[int, int]]] = {}
    windows_intervals: list[tuple[int, int]] = []
    source_intervals: list[tuple[int, int]] = []
    writer_intervals: list[tuple[int, int]] = []
    slow_reads: list[dict[str, object]] = []

    def stage(windows: Sequence[PerfEvent], name: str) -> float | None:
        start_name, end_name = stage_names[name]
        status = "ok" if end_name in {"source_open_end", "source_read_end"} else None
        return _duration_ms(
            _first(windows, start_name), _first(windows, end_name, status=status)
        )

    for (transfer, read_id), group in groups.items():
        mac = [item for item in group if item.source == "mac"]
        windows = [item for item in group if item.source == "windows"]
        mac_send = _first(mac, "file_read_send")
        mac_receive = _first(mac, "file_chunk_receive")
        mac_rtt = _duration_ms(mac_send, mac_receive)
        if mac_rtt is not None:
            mac_rtts.append(mac_rtt)
            interval = (mac_send.mono_ns, mac_receive.mono_ns)
            mac_intervals.append(interval)
            file_key = f"{transfer}:{mac_send.fields.get('entry_index', 'unknown')}"
            mac_intervals_by_file.setdefault(file_key, []).append(interval)

        offset_event = mac_send or _first(windows, "file_read_bytes_available")
        offset = _int(offset_event.fields.get("offset")) if offset_event else None
        requested = _int(mac_send.fields.get("length")) if mac_send else None
        returned = _int(mac_receive.fields.get("bytes")) if mac_receive else None
        if requested is not None:
            request_sizes.append(requested)
        if returned is not None:
            chunk_sizes.append(returned)

        measured = {name: stage(windows, name) for name in stage_names}
        for name, value in measured.items():
            if value is not None:
                samples[name].append(value)

        windows_start = _first(windows, "file_read_handler_enter")
        windows_end = _first(windows, "file_read_handler_exit") or _first(
            windows, "outbound_enqueue"
        )
        source_start = _first(windows, "source_read_begin")
        source_end = _first(windows, "source_read_end", status="ok")
        writer_start = _first(windows, "socket_write_begin")
        writer_end = _first(windows, "socket_write_complete")
        for target, start, end in (
            (windows_intervals, windows_start, windows_end),
            (source_intervals, source_start, source_end),
            (writer_intervals, writer_start, writer_end),
        ):
            if (
                start is not None
                and end is not None
                and start.source == end.source
                and start.clock == end.clock
                and end.mono_ns >= start.mono_ns
            ):
                target.append((start.mono_ns, end.mono_ns))

        open_end = _first(windows, "source_open_end", status="ok")
        if open_end is not None:
            if open_end.fields.get("fd_reused") == "true":
                fd_reuse_count += 1
            elif open_end.fields.get("fd_reused") == "false":
                fd_open_count += 1
        if source_end is not None:
            source_bytes += _int(source_end.fields.get("bytes")) or 0
        lag = _first(windows, "event_loop_lag")
        if lag is not None:
            lag_ns = _int(lag.fields.get("lag_ns"))
            if lag_ns is not None and lag_ns >= 0:
                event_loop_lag.append(lag_ns / 1_000_000)

        first = offset == 0
        if mac_rtt is not None:
            (first_mac_rtts if first else subsequent_mac_rtts).append(mac_rtt)
        if first:
            for target, name in (
                (first_windows, "WINDOWS_PROCESSING"),
                (first_open, "SOURCE_OPEN"),
                (first_read, "SOURCE_READ"),
            ):
                value = measured[name]
                if value is not None:
                    target.append(value)

        if mac_rtt is not None:
            queue_event = _first(windows, "outbound_enqueue")
            slow_reads.append(
                {
                    "file": f"{transfer}:{offset_event.fields.get('entry_index', 'unknown')}",
                    "read_id": _int(read_id),
                    "offset": offset,
                    "length": requested,
                    "mac_rtt_ms": mac_rtt,
                    "decode_ms": measured["DECODE"],
                    "dispatch_ms": measured["DISPATCH"],
                    "lookup_ms": measured["SNAPSHOT_LOOKUP"],
                    "open_ms": measured["SOURCE_OPEN"],
                    "read_ms": measured["SOURCE_READ"],
                    "encode_ms": measured["CHUNK_ENCODE"],
                    "queue_wait_ms": measured["OUTBOUND_QUEUE_WAIT"],
                    "socket_write_ms": measured["SOCKET_WRITE"],
                    "encryption_ms": measured["ENCRYPTION_TIME"],
                    "concurrent_active_files": None,
                    "outbound_queue_depth_bytes": (
                        _int(queue_event.fields.get("queue_depth_bytes"))
                        if queue_event is not None
                        else None
                    ),
                }
            )

            slow_reads[-1]["_mac_start_ns"] = mac_send.mono_ns

        if first and measured["OUTBOUND_QUEUE_WAIT"] is not None:
            first_outbound_queue_wait.append(measured["OUTBOUND_QUEUE_WAIT"])
        if mac_rtt is not None and measured["WINDOWS_PROCESSING"] is not None:
            approximate_transport_residual.append(
                max(0.0, mac_rtt - measured["WINDOWS_PROCESSING"])
            )

    for row in slow_reads:
        mac_start_ns = row.pop("_mac_start_ns", None)
        if isinstance(mac_start_ns, int):
            row["concurrent_active_files"] = sum(
                start <= mac_start_ns < end for start, end in mac_intervals
            )

    def distribution_fields(prefix: str, values: Sequence[float]) -> dict[str, object]:
        p50, p95, maximum = _distribution(values)
        return {
            f"{prefix}_P50": UNAVAILABLE if p50 is None else p50,
            f"{prefix}_P95": UNAVAILABLE if p95 is None else p95,
            f"{prefix}_MAX": UNAVAILABLE if maximum is None else maximum,
        }

    aggregates: dict[str, object] = {}
    for name, values in samples.items():
        aggregates.update(distribution_fields(name, values))
    aggregates.update(distribution_fields("MAC_READ_TO_CHUNK", mac_rtts))
    aggregates.update(distribution_fields("FIRST_READ_WINDOWS_PROCESSING", first_windows))
    aggregates.update(distribution_fields("FIRST_SOURCE_OPEN", first_open))
    aggregates.update(distribution_fields("FIRST_SOURCE_READ", first_read))
    aggregates.update(
        distribution_fields(
            "FIRST_OUTBOUND_QUEUE_WAIT", first_outbound_queue_wait
        )
    )
    aggregates.update(distribution_fields("FIRST_MAC_RTT", first_mac_rtts))
    aggregates.update(distribution_fields("SUBSEQUENT_MAC_RTT", subsequent_mac_rtts))
    aggregates.update(distribution_fields("EVENT_LOOP_LAG", event_loop_lag))
    aggregates.update(
        distribution_fields(
            "APPROXIMATE_TRANSPORT_RESIDUAL", approximate_transport_residual
        )
    )
    aggregates.update(distribution_fields("READ_SIZE", [float(value) for value in request_sizes]))
    aggregates.update(distribution_fields("CHUNK_SIZE", [float(value) for value in chunk_sizes]))
    queue_depths = [
        float(depth)
        for event in event_list
        if event.source == "windows" and event.name == "outbound_enqueue"
        for depth in [_int(event.fields.get("queue_depth_bytes"))]
        if depth is not None
    ]
    aggregates.update(distribution_fields("OUTBOUND_QUEUE_DEPTH_BYTES", queue_depths))

    windows_processing_total = sum(samples["WINDOWS_PROCESSING"])
    for name in (
        "DECODE",
        "DISPATCH",
        "SNAPSHOT_LOOKUP",
        "SOURCE_OPEN",
        "SOURCE_READ",
        "CHUNK_ENCODE",
        "OUTBOUND_QUEUE_WAIT",
        "SOCKET_WRITE",
        "ENCRYPTION_TIME",
    ):
        aggregates[f"{name}_PCT_WINDOWS_PROCESSING"] = (
            100 * sum(samples[name]) / windows_processing_total
            if windows_processing_total > 0
            else UNAVAILABLE
        )

    windows_events = [event for event in event_list if event.source == "windows"]
    first_windows_read = _first(windows_events, "file_read_bytes_available")
    configurations = [
        event
        for event in windows_events
        if event.name == "transport_configuration"
        and (
            first_windows_read is None
            or event.clock != first_windows_read.clock
            or event.mono_ns <= first_windows_read.mono_ns
        )
    ]
    configuration = (
        max(configurations, key=lambda event: event.mono_ns)
        if configurations
        else None
    )
    for report_name, field_name in (
        ("TCP_NODELAY", "tcp_nodelay"),
        ("SO_SNDBUF", "so_sndbuf"),
        ("SO_RCVBUF", "so_rcvbuf"),
        ("SO_KEEPALIVE", "so_keepalive"),
        ("TLS_BACKEND", "tls_backend"),
        ("TLS_LIBRARY", "tls_library"),
        ("PERF_CLOCK", "perf_clock"),
        ("PERF_CLOCK_RESOLUTION_NS", "perf_clock_resolution_ns"),
        ("PERF_CLOCK_IMPLEMENTATION", "perf_clock_implementation"),
    ):
        aggregates[report_name] = (
            configuration.fields.get(field_name, UNAVAILABLE)
            if configuration is not None
            else UNAVAILABLE
        )

    chunk_counts: dict[str, int] = {}
    for perf_event in event_list:
        if perf_event.source != "mac" or perf_event.name != "file_chunk_receive":
            continue
        transfer = perf_event.fields.get("transfer_id") or perf_event.fields.get(
            "generation_id", "unknown"
        )
        key = f"{transfer}:{perf_event.fields.get('entry_index', 'unknown')}"
        chunk_counts[key] = chunk_counts.get(key, 0) + 1
    aggregates.update(
        distribution_fields(
            "CHUNKS_PER_FILE", [float(value) for value in chunk_counts.values()]
        )
    )

    per_file_max = max(
        (
            concurrency_sweep(intervals)["max"] or 0
            for intervals in mac_intervals_by_file.values()
        ),
        default=0,
    )
    global_max = concurrency_sweep(mac_intervals)["max"] or 0
    aggregates.update(
        {
            "TOTAL_FILE_READS": sum(
                event.source == "mac" and event.name == "file_read_send"
                for event in event_list
            ),
            "TOTAL_FILE_CHUNKS": sum(
                event.source == "mac" and event.name == "file_chunk_receive"
                for event in event_list
            ),
            "PER_FILE_MAX_INFLIGHT_READS": per_file_max,
            "GLOBAL_MAX_INFLIGHT_READS": global_max,
            "WINDOWS_MAX_LOGICAL_READ_CONCURRENCY": concurrency_sweep(
                windows_intervals
            )["max"]
            or 0,
            "WINDOWS_MAX_SOURCE_READ_CONCURRENCY": concurrency_sweep(source_intervals)[
                "max"
            ]
            or 0,
            "WINDOWS_OUTBOUND_WRITER_CONCURRENCY": concurrency_sweep(
                writer_intervals
            )["max"]
            or 0,
            "FD_OPEN_COUNT": fd_open_count,
            "FD_REUSE_COUNT": fd_reuse_count,
            "SOURCE_READ_THROUGHPUT": (
                source_bytes * 1000 / sum(samples["SOURCE_READ"])
                if samples["SOURCE_READ"] and sum(samples["SOURCE_READ"]) > 0
                else UNAVAILABLE
            ),
            "SOURCE_QUEUE_WAIT_P50": NOT_APPLICABLE,
            "SOURCE_QUEUE_WAIT_P95": NOT_APPLICABLE,
            "SOURCE_QUEUE_WAIT_MAX": NOT_APPLICABLE,
            "PER_FILE_STOP_AND_WAIT": "YES" if per_file_max <= 1 and mac_intervals else "NO",
            "CHUNK_SIZE": max(request_sizes) if request_sizes else UNAVAILABLE,
            "TOTAL_BYTES": total_bytes,
        }
    )
    slow_reads.sort(key=lambda row: float(row["mac_rtt_ms"]), reverse=True)
    return {"aggregates": aggregates, "slowest_reads": slow_reads[:10]}


def analyze(events: Iterable[PerfEvent], dataset: dict, correctness: dict) -> RunAnalysis:
    parsed_errors = list(getattr(events, "parse_errors", []))
    event_list = list(events)
    by_item: dict[str, list[PerfEvent]] = {}
    for perf_event in event_list:
        item = _item_id(perf_event)
        if item is not None:
            by_item.setdefault(item, []).append(perf_event)

    extension_intervals: list[tuple[int, int]] = []
    interval_by_item: dict[str, tuple[int, int]] = {}
    successful_completions = 0
    for item, item_events in by_item.items():
        swift = [event for event in item_events if event.source == "extension"]
        start = _first(swift, "fetch_enter")
        end = _last(swift, "completion_call", status="ok")
        successful_completions += sum(
            event.name == "completion_call" and event.fields.get("status") == "ok"
            for event in swift
        )
        if start and end and start.clock == end.clock and end.mono_ns >= start.mono_ns:
            extension_intervals.append((start.mono_ns, end.mono_ns))
            interval_by_item[item] = (start.mono_ns, end.mono_ns)

    concurrency = concurrency_sweep(extension_intervals)

    read_groups: dict[tuple[str, str], list[PerfEvent]] = {}
    for perf_event in event_list:
        read_id = perf_event.fields.get("read_id")
        transfer = perf_event.fields.get("transfer_id") or perf_event.fields.get("generation_id")
        if read_id is not None and transfer is not None:
            read_groups.setdefault((transfer, read_id), []).append(perf_event)

    read_rtt: list[float] = []
    wire_intervals: list[tuple[int, int]] = []
    received_bytes = 0
    windows_service: list[float] = []
    snapshot_lookup: list[float] = []
    source_read: list[float] = []
    network_residual: list[float] = []
    for group in read_groups.values():
        send = _first([e for e in group if e.source == "mac"], "file_read_send")
        receive = _first([e for e in group if e.source == "mac"], "file_chunk_receive")
        duration = _duration_ms(send, receive)
        if duration is not None:
            read_rtt.append(duration)
            wire_intervals.append((send.mono_ns, receive.mono_ns))
            received_bytes += _int(receive.fields.get("bytes")) or 0
        windows = [e for e in group if e.source == "windows"]
        win_receive = _first(windows, "file_read_receive")
        win_send = _first(windows, "file_chunk_send")
        lookup_start = _first(windows, "snapshot_lookup_begin")
        lookup_end = _first(windows, "snapshot_lookup_end")
        source_start = _first(windows, "source_read_begin")
        source_end = _first(windows, "source_read_end", status="ok")
        for target, start, end in (
            (windows_service, win_receive, win_send),
            (snapshot_lookup, lookup_start, lookup_end),
            (source_read, source_start, source_end),
        ):
            value = _duration_ms(start, end)
            if value is not None:
                target.append(value)
        if duration is not None:
            service_duration = _duration_ms(win_receive, win_send)
            if service_duration is not None:
                network_residual.append(max(0.0, duration - service_duration))

    wire_union_ns = interval_union_ns(wire_intervals)
    throughput = received_bytes * 1_000_000_000 / wire_union_ns if wire_union_ns else None

    dataset_files = list(dataset.get("files", []))
    expected_count = int(dataset.get("FILE_COUNT", len(dataset_files)))
    if not dataset_files:
        items = sorted(
            (item for item in by_item if any(e.name == "fetch_enter" for e in by_item[item])),
            key=lambda item: (_int(item.rsplit(":", 1)[-1]) or 0, item),
        )
        dataset_files = [{"item": item, "size": 0} for item in items]

    per_file: list[FetchMetrics] = []
    xpc_open: list[float] = []
    queue_waits: list[float] = []
    first_byte_values: list[float] = []
    fetch_startup_values: list[float] = []
    post_open_wait_values: list[float] = []
    transfer_values: list[float] = []
    local_finalize_values: list[float] = []
    fsync_values: list[float] = []
    finalization_values: list[float] = []
    close_values: list[float] = []
    first_chunk_values: list[float] = []
    host_dispatch_values: list[float] = []
    host_open_values: list[float] = []
    request_sizes: list[int] = []
    local_write_values: list[float] = []

    for position, file_info in enumerate(dataset_files):
        item = str(file_info.get("item") or file_info.get("item_identifier") or "")
        if not item:
            index_hint = int(file_info.get("entry_index", position))
            matches = [key for key in by_item if key.endswith(f":{index_hint}")]
            item = matches[0] if len(matches) == 1 else f"unknown:{index_hint}"
        item_events = by_item.get(item, [])
        swift = [event for event in item_events if event.source == "extension"]
        mac = [event for event in item_events if event.source == "mac"]
        t0 = _first(swift, "fetch_enter")
        t1 = _last(swift, "open_fetch_call_begin")
        t3 = _last(swift, "open_fetch_reply", status="ok")
        t8 = _first(swift, "first_write_complete")
        t11 = _last(swift, "last_write_complete")
        t12a = _last(swift, "fsync_complete")
        t12b = _last(swift, "close_complete")
        t12 = _last(swift, "finalize_complete")
        t13 = _last(swift, "completion_call", status="ok")
        total = _duration_ms(t0, t13)
        open_ms = _duration_ms(t1, t3)
        first_byte = _duration_ms(t0, t8)
        transfer_ms = _duration_ms(t8, t11)
        local_finalize = _duration_ms(t11, t13)
        fsync_ms = _duration_ms(t11, t12a)
        finalization_ms = _duration_ms(t11, t12)
        startup_ms = _duration_ms(t0, t3)
        post_open_wait = _duration_ms(t3, t8)
        close_ms = _duration_ms(t12a, t12b)
        if open_ms is not None:
            xpc_open.append(open_ms)
        for target, value in (
            (first_byte_values, first_byte),
            (transfer_values, transfer_ms),
            (local_finalize_values, local_finalize),
            (fsync_values, fsync_ms),
            (finalization_values, finalization_ms),
            (fetch_startup_values, startup_ms),
            (post_open_wait_values, post_open_wait),
            (close_values, close_ms),
        ):
            if value is not None:
                target.append(value)

        queue_enter = _first(mac, "queue_enter")
        slot = _first(mac, "slot_acquired")
        if queue_enter is not None:
            queue_wait = _duration_ms(queue_enter, slot)
        elif slot is not None and slot.fields.get("queued") == "false":
            queue_wait = 0.0
        else:
            queue_wait = None
        if queue_wait is not None:
            queue_waits.append(queue_wait)

        reads = sorted((event for event in mac if event.name == "file_read_send"), key=lambda e: e.mono_ns)
        receives = sorted((event for event in mac if event.name == "file_chunk_receive"), key=lambda e: e.mono_ns)
        requested = [_int(event.fields.get("length")) or 0 for event in reads]
        received = [_int(event.fields.get("bytes")) or 0 for event in receives]
        request_sizes.extend(requested)
        first_chunk = _duration_ms(reads[0] if reads else None, receives[0] if receives else None)
        if first_chunk is not None:
            first_chunk_values.append(first_chunk)
        host_enter = _first(mac, "open_fetch_enter")
        host_reply = _last(mac, "open_fetch_reply", status="ok")
        host_dispatch = _duration_ms(host_enter, reads[0] if reads else None)
        host_open = _duration_ms(host_enter, host_reply)
        if host_dispatch is not None:
            host_dispatch_values.append(host_dispatch)
        if host_open is not None:
            host_open_values.append(host_open)

        replies_by_sequence = {
            event.fields.get("pull_sequence"): event
            for event in swift
            if event.name == "pull_reply" and event.fields.get("status") == "ok"
        }
        for write in (event for event in swift if event.name == "chunk_write_complete"):
            reply = replies_by_sequence.get(write.fields.get("pull_sequence"))
            local_write = _duration_ms(reply, write)
            if local_write is not None:
                local_write_values.append(local_write)

        interval = interval_by_item.get(item)
        at_start = None
        if interval:
            at_start = sum(start <= interval[0] < end for start, end in extension_intervals)
        transfer = item.rsplit(":", 1)[0] if ":" in item else "unknown"
        index = _int(item.rsplit(":", 1)[-1]) if ":" in item else position
        per_file.append(
            FetchMetrics(
                item=item,
                transfer_id=transfer,
                entry_index=index if index is not None else position,
                size=int(file_info.get("size", 0)),
                chunks=len(receives),
                fetch_total_ms=total,
                open_fetch_ms=open_ms,
                first_byte_ms=first_byte,
                transfer_ms=transfer_ms,
                finalize_ms=local_finalize,
                queue_wait_ms=queue_wait,
                max_concurrency_at_start=at_start,
                requested_chunk_sizes=requested,
                received_chunk_sizes=received,
                fetch_startup_ms=startup_ms,
                host_dispatch_ms=host_dispatch,
                read_to_first_chunk_ms=first_chunk,
                fsync_ms=fsync_ms,
                finalization_ms=finalization_ms,
            )
        )

    fetch_totals = [metric.fetch_total_ms for metric in per_file if metric.fetch_total_ms is not None]
    chunk_counts = [float(metric.chunks) for metric in per_file]
    sizes = [float(metric.size) for metric in per_file]
    refetch_count = max(0, successful_completions - len(interval_by_item))
    fp_error_counts = {
        code: sum(
            event.name == "completion_call"
            and event.fields.get("status") == "error"
            and event.fields.get("error_code") == code
            for event in event_list
        )
        for code in ("-1005", "-1004", "-1000")
    }
    complete_fetches = len(extension_intervals) == expected_count == len(per_file)
    trace_complete = all(
        metric.fetch_total_ms is not None
        and metric.open_fetch_ms is not None
        and metric.queue_wait_ms is not None
        and (
            metric.size == 0
            or (
                metric.first_byte_ms is not None
                and metric.transfer_ms is not None
                and metric.finalize_ms is not None
                and metric.read_to_first_chunk_ms is not None
                and metric.chunks > 0
            )
        )
        for metric in per_file
    )
    baseline_valid = (
        not parsed_errors
        and complete_fetches
        and trace_complete
        and _correctness_valid(correctness, expected_count, refetch_count)
    )

    inter_file_idle: list[float] = []
    for (_, previous_end), (next_start, _) in zip(sorted(extension_intervals), sorted(extension_intervals)[1:]):
        if next_start >= previous_end:
            inter_file_idle.append((next_start - previous_end) / 1_000_000)

    internal_intervals: list[tuple[int, int]] = []
    for item_events in by_item.values():
        mac = [event for event in item_events if event.source == "mac"]
        acquired = _first(mac, "slot_acquired")
        released = _last(mac, "slot_released")
        if acquired and released and acquired.clock == released.clock:
            internal_intervals.append((acquired.mono_ns, released.mono_ns))
    internal_concurrency = concurrency_sweep(internal_intervals)

    finder_serialization = "UNKNOWN"
    if complete_fetches:
        finder_serialization = "YES" if (concurrency["max"] or 0) <= 1 else "NO"
    internal_serialization = "UNKNOWN"
    if finder_serialization != "UNKNOWN" and internal_concurrency["max"] is not None:
        internal_serialization = (
            "YES" if finder_serialization == "NO" and internal_concurrency["max"] <= 1 else "NO"
        )

    eviction_events = [event for event in event_list if event.name.startswith("eviction_")]
    eviction_attempts = sum(event.name == "eviction_attempt" for event in eviction_events)
    eviction_retries = sum(event.name == "eviction_deferred" for event in eviction_events)
    eviction_critical = "UNKNOWN"
    if any(event.fields.get("named_shared_blocker") == "true" for event in eviction_events):
        eviction_critical = "YES"
    elif eviction_events:
        progress_names = {"pull_reply", "first_write_complete", "last_write_complete", "completion_call"}
        parallel_progress = False
        for eviction in eviction_events:
            if eviction.source != "extension":
                continue
            for item, (start, end) in interval_by_item.items():
                if start <= eviction.mono_ns <= end and any(
                    progress.name in progress_names
                    and progress.clock == eviction.clock
                    and eviction.mono_ns <= progress.mono_ns <= end
                    for progress in by_item.get(item, [])
                ):
                    parallel_progress = True
                    break
        if parallel_progress:
            eviction_critical = "NO"

    windows_available = bool(windows_service) and len(windows_service) == len(read_rtt)
    windows_breakdown = "AVAILABLE" if windows_available else UNAVAILABLE
    network_plus_dispatch = (
        f"AGGREGATE_DURATION_RESIDUAL_P50={nearest_rank(network_residual, 50):.3f} ms"
        if windows_available and network_residual
        else UNAVAILABLE
    )

    wall_ns = (
        max(end for _, end in extension_intervals) - min(start for start, _ in extension_intervals)
        if extension_intervals
        else 0
    )
    active_union_ns = interval_union_ns(extension_intervals)
    zero_active_ms = concurrency["time_ms"][0]
    classification, secondary, evidence = classify_bottleneck(
        baseline_valid=baseline_valid,
        fetch_totals=fetch_totals,
        queue_waits=queue_waits,
        xpc_open=xpc_open,
        read_rtt=read_rtt,
        transfer=transfer_values,
        finalize=local_finalize_values,
        zero_active_ms=zero_active_ms,
        wall_ms=wall_ns / 1_000_000,
        eviction=eviction_critical,
    )

    p50_size, p95_size, _ = _distribution(sizes)
    fetch_p50, fetch_p95, fetch_max = _distribution(fetch_totals)
    first_p50, first_p95, _ = _distribution(first_byte_values)
    xpc_p50, xpc_p95, xpc_max = _distribution(xpc_open)
    first_chunk_p50, first_chunk_p95, _ = _distribution(first_chunk_values)
    transfer_p50, transfer_p95, _ = _distribution(transfer_values)
    fsync_p50, fsync_p95, _ = _distribution(fsync_values)
    final_p50, final_p95, _ = _distribution(finalization_values)
    local_p50, local_p95, _ = _distribution(local_finalize_values)
    idle_p50, idle_p95, idle_max = _distribution(inter_file_idle)
    queue_p50, queue_p95, queue_max = _distribution(queue_waits)
    chunk_p50, chunk_p95, _ = _distribution(chunk_counts)
    rtt_p50, rtt_p95, rtt_max = _distribution(read_rtt)
    lookup_p50, lookup_p95, _ = _distribution(snapshot_lookup)
    source_p50, source_p95, _ = _distribution(source_read)
    win_p50, win_p95, _ = _distribution(windows_service)
    host_dispatch_p50, host_dispatch_p95, _ = _distribution(host_dispatch_values)
    local_write_p50, local_write_p95, _ = _distribution(local_write_values)
    startup_p50, startup_p95, _ = _distribution(fetch_startup_values)
    post_open_p50, post_open_p95, _ = _distribution(post_open_wait_values)
    close_p50, close_p95, _ = _distribution(close_values)
    host_open_p50, host_open_p95, _ = _distribution(host_open_values)

    def available(value: object) -> object:
        return UNAVAILABLE if value is None else value

    aggregates: dict[str, object] = {
        "FILES": expected_count,
        "TOTAL_BYTES": int(dataset.get("TOTAL_BYTES", sum(metric.size for metric in per_file))),
        "TOTAL_WALL_TIME": wall_ns / 1_000_000_000 if wall_ns else UNAVAILABLE,
        "SEC_PER_FILE": wall_ns / 1_000_000_000 / expected_count if wall_ns and expected_count else UNAVAILABLE,
        "FILE_SIZE_P50": available(p50_size), "FILE_SIZE_P95": available(p95_size),
        "FETCH_TOTAL_P50": available(fetch_p50), "FETCH_TOTAL_P95": available(fetch_p95), "FETCH_TOTAL_MAX": available(fetch_max),
        "TIME_TO_FIRST_BYTE_P50": available(first_p50), "TIME_TO_FIRST_BYTE_P95": available(first_p95),
        "XPC_OPEN_FETCH_P50": available(xpc_p50), "XPC_OPEN_FETCH_P95": available(xpc_p95), "XPC_OPEN_FETCH_MAX": available(xpc_max),
        "FILE_READ_TO_FIRST_CHUNK_P50": available(first_chunk_p50), "FILE_READ_TO_FIRST_CHUNK_P95": available(first_chunk_p95),
        "ACTIVE_BYTE_TRANSFER_P50": available(transfer_p50), "ACTIVE_BYTE_TRANSFER_P95": available(transfer_p95),
        "FSYNC_P50": available(fsync_p50), "FSYNC_P95": available(fsync_p95),
        "FINALIZATION_P50": available(final_p50), "FINALIZATION_P95": available(final_p95),
        "LOCAL_FINALIZE_P50": available(local_p50), "LOCAL_FINALIZE_P95": available(local_p95),
        "INTER_FILE_IDLE_P50": available(idle_p50), "INTER_FILE_IDLE_P95": available(idle_p95), "INTER_FILE_IDLE_MAX": available(idle_max),
        "INTER_FETCH_IDLE_TOTAL": sum(inter_file_idle),
        "ACTIVE_BYTE_TRANSFER_TOTAL": sum(transfer_values),
        "QUEUE_WAIT_P50": available(queue_p50), "QUEUE_WAIT_P95": available(queue_p95), "QUEUE_WAIT_MAX": available(queue_max),
        "MAX_ACTIVE_FETCHES": available(concurrency["max"]), "AVG_ACTIVE_FETCHES": available(concurrency["average"]),
        "TIME_WITH_0_ACTIVE": concurrency["time_ms"][0], "TIME_WITH_1_ACTIVE": concurrency["time_ms"][1],
        "TIME_WITH_2_ACTIVE": concurrency["time_ms"][2], "TIME_WITH_3_ACTIVE": concurrency["time_ms"][3],
        "TIME_WITH_GE4_ACTIVE": concurrency["time_ms"]["ge4"],
        "AVG_CHUNKS_PER_FILE": sum(chunk_counts) / len(chunk_counts) if chunk_counts else UNAVAILABLE,
        "P50_CHUNKS_PER_FILE": available(chunk_p50), "P95_CHUNKS_PER_FILE": available(chunk_p95),
        "AVG_REQUEST_SIZE": sum(request_sizes) / len(request_sizes) if request_sizes else UNAVAILABLE,
        "READ_CHUNK_RTT_P50": available(rtt_p50), "READ_CHUNK_RTT_P95": available(rtt_p95), "READ_CHUNK_RTT_MAX": available(rtt_max),
        "WINDOWS_READ_SERVICE_TIME": available(win_p50),
        "SNAPSHOT_LOOKUP_P50": available(lookup_p50), "SNAPSHOT_LOOKUP_P95": available(lookup_p95),
        "SOURCE_READ_P50": available(source_p50), "SOURCE_READ_P95": available(source_p95),
        "WINDOWS_READ_TO_SEND_P50": available(win_p50), "WINDOWS_READ_TO_SEND_P95": available(win_p95),
        "WIRE_ACTIVE_TIME": wire_union_ns / 1_000_000 if wire_union_ns else UNAVAILABLE,
        "ACTIVE_TRANSFER_THROUGHPUT": available(throughput),
        "EVICTION_ATTEMPTS": eviction_attempts, "EVICTION_RETRIES": eviction_retries,
        "EVICTION_ON_CRITICAL_PATH": eviction_critical,
        "EVICTION_ATTEMPTS_PER_ITEM": eviction_attempts / expected_count if expected_count else UNAVAILABLE,
        "EVICTION_RETRY_COUNT": eviction_retries,
        "HOST_DISPATCH_P50": available(host_dispatch_p50), "HOST_DISPATCH_P95": available(host_dispatch_p95),
        "LOCAL_WRITE_P50": available(local_write_p50), "LOCAL_WRITE_P95": available(local_write_p95),
        "FINDER_REQUEST_CONCURRENCY": available(concurrency["max"]),
        "INTERNAL_FETCH_CONCURRENCY": available(internal_concurrency["max"]),
        "REFETCH_COUNT": refetch_count,
        **fp_error_counts,
        "FETCH_STARTUP_P50": available(startup_p50), "FETCH_STARTUP_P95": available(startup_p95),
        "POST_OPEN_WAIT_P50": available(post_open_p50), "POST_OPEN_WAIT_P95": available(post_open_p95),
        "CLOSE_P50": available(close_p50), "CLOSE_P95": available(close_p95),
        "HOST_OPEN_PROCESSING_P50": available(host_open_p50), "HOST_OPEN_PROCESSING_P95": available(host_open_p95),
        "XPC_REPLY_TO_EXTENSION": UNAVAILABLE,
    }
    aggregates.update(burst_accounting(event_list))

    finder_pct = zero_active_ms / (wall_ns / 1_000_000) * 100 if wall_ns else 0
    contributions = {
        "FINDER_SCHEDULING_CONTRIBUTION": f"{zero_active_ms:.3f} ms ({finder_pct:.1f}% covered wall)",
        "OUR_PIPELINE_CONTRIBUTION": f"{active_union_ns / 1_000_000:.3f} ms interval union",
        "WIRE_CONTRIBUTION": f"{wire_union_ns / 1_000_000:.3f} ms outstanding-request union" if wire_union_ns else UNAVAILABLE,
        "FINALIZATION_CONTRIBUTION": f"p50 {local_p50:.3f} ms" if local_p50 is not None else UNAVAILABLE,
        "EVICTION_CONTRIBUTION": "not on critical path" if eviction_critical == "NO" else eviction_critical,
    }
    candidates = _optimization_candidates(classification, aggregates) if classification != "I" else []
    top_five = sorted(
        (metric for metric in per_file if metric.fetch_total_ms is not None),
        key=lambda metric: metric.fetch_total_ms or 0,
        reverse=True,
    )[:5]
    transport = transport_breakdown(
        event_list,
        total_bytes=int(dataset.get("TOTAL_BYTES", sum(metric.size for metric in per_file))),
    )
    return RunAnalysis(
        events=event_list, parse_errors=parsed_errors, dataset=dataset, correctness=correctness,
        per_file=per_file, aggregates=aggregates, xpc_open_fetch_ms=xpc_open,
        queue_wait_ms=queue_waits, read_chunk_rtt_ms=read_rtt,
        windows_read_service_time_ms=windows_service,
        wire_active_time_ms=wire_union_ns / 1_000_000, received_bytes=received_bytes,
        active_transfer_throughput_bytes_per_second=throughput,
        max_active_fetches=concurrency["max"], avg_active_fetches=concurrency["average"],
        active_fetch_time_ms=concurrency["time_ms"],
        avg_chunks_per_file=sum(chunk_counts) / len(chunk_counts) if chunk_counts else None,
        eviction_on_critical_path=eviction_critical,
        windows_internal_breakdown=windows_breakdown,
        network_plus_dispatch=network_plus_dispatch, refetch_count=refetch_count,
        performance_baseline_valid=baseline_valid, finder_serialization=finder_serialization,
        internal_serialization=internal_serialization, primary_bottleneck=classification,
        secondary_bottleneck=secondary, classification_evidence=evidence,
        top_five=top_five, contributions=contributions,
        optimization_candidates=candidates,
        transport=transport,
    )


def classify_bottleneck(**metrics: object) -> tuple[str, str, list[str]]:
    if not metrics["baseline_valid"]:
        return "I", "I", ["PERFORMANCE_BASELINE_VALID"]
    required = ("fetch_totals", "xpc_open", "read_rtt", "finalize")
    if any(not metrics[name] for name in required):
        return "I", "I", [f"missing {name}" for name in required if not metrics[name]]
    wall_ms = float(metrics["wall_ms"])
    components = {
        "A": float(metrics["zero_active_ms"]),
        "B": sum(metrics["queue_waits"]) / len(metrics["queue_waits"]) if metrics["queue_waits"] else 0.0,
        "C": nearest_rank(metrics["xpc_open"], 50) or 0.0,
        "D": nearest_rank(metrics["read_rtt"], 50) or 0.0,
        "E": nearest_rank(metrics["transfer"], 50) or 0.0,
        "F": nearest_rank(metrics["finalize"], 50) or 0.0,
        "G": wall_ms if metrics["eviction"] == "YES" else 0.0,
    }
    ordered = sorted(components.items(), key=lambda pair: pair[1], reverse=True)
    primary = ordered[0][0]
    secondary = ordered[1][0] if ordered[1][1] >= ordered[0][1] * 0.35 and ordered[1][1] > 0 else "NONE"
    if ordered[0][1] <= 0:
        return "I", "I", ["no positive measured contribution"]
    evidence = [f"{name}={value:.3f}ms" for name, value in ordered[:3]]
    return primary, secondary, evidence


def _optimization_candidates(category: str, aggregates: dict[str, object]) -> list[dict[str, str]]:
    candidates = {
        "A": ("Finder/File Provider scheduling", "Reduce measured zero-active gaps", "May depend on system-owned behavior", "Compare one separate CLI control trace"),
        "B": ("Internal queue wait", "Reduce measured queue delay", "Could alter memory/concurrency invariants", "Diagnostic-only alternate-limit A/B run"),
        "C": ("XPC openFetch RTT", "Remove measured per-file open fixed cost", "Protocol/lifecycle coupling", "Measure a no-op openFetch control"),
        "D": ("FILE_READ RTT/service", "Reduce measured read round-trip cost", "Wire and sender correctness", "One diagnostic request-coalescing simulation"),
        "E": ("Active byte transfer", "Improve active transfer throughput", "Buffer and memory pressure", "Offline chunk-size replay benchmark"),
        "F": ("Local finalization", "Reduce measured fsync/finalization cost", "Durability semantics", "Instrumented filesystem-only microbenchmark"),
        "G": ("Eviction contention", "Remove demonstrated shared-resource blocking", "Cache lifecycle correctness", "Trace the named lock/executor in isolation"),
        "H": ("Mixed measured stages", "Address the two largest measured shares", "Interacting changes obscure causality", "Run one-factor-at-a-time experiments"),
    }
    bottleneck, effect, risk, experiment = candidates[category]
    return [{
        "measured_bottleneck_addressed": bottleneck,
        "expected_effect": effect,
        "risk": risk,
        "smallest_experiment": experiment,
    }]


def _jsonable(analysis: RunAnalysis) -> dict:
    return {
        "dataset": analysis.dataset,
        "correctness": analysis.correctness,
        "parse_errors": analysis.parse_errors,
        "events": [asdict(event) for event in analysis.events],
        "per_file": [asdict(metric) for metric in analysis.per_file],
        "aggregates": analysis.aggregates,
        "transport": analysis.transport,
        "availability": {
            "WINDOWS_INTERNAL_BREAKDOWN": analysis.windows_internal_breakdown,
            "NETWORK_PLUS_DISPATCH": analysis.network_plus_dispatch,
            "DESTINATION_MATERIALIZATION_TIMESTAMP": UNAVAILABLE,
        },
        "classification": {
            "PERFORMANCE_BASELINE_VALID": analysis.performance_baseline_valid,
            "PRIMARY_BOTTLENECK": analysis.primary_bottleneck,
            "SECONDARY_BOTTLENECK": analysis.secondary_bottleneck,
            "FINDER_SERIALIZATION": analysis.finder_serialization,
            "INTERNAL_SERIALIZATION": analysis.internal_serialization,
            "REFETCH_COUNT": analysis.refetch_count,
            "evidence": analysis.classification_evidence,
        },
        "contributions": analysis.contributions,
        "optimization_candidates": analysis.optimization_candidates,
    }


def _fmt(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _table(rows: Sequence[FetchMetrics]) -> str:
    header = "| item | size | chunks | fetch_total_ms | open_fetch_ms | first_byte_ms | transfer_ms | finalize_ms | queue_wait_ms | max_concurrency_at_start |"
    separator = "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    rendered = [header, separator]
    for metric in rows:
        values = [
            metric.item, metric.size, metric.chunks, metric.fetch_total_ms,
            metric.open_fetch_ms, metric.first_byte_ms, metric.transfer_ms,
            metric.finalize_ms, metric.queue_wait_ms, metric.max_concurrency_at_start,
        ]
        rendered.append("| " + " | ".join(_fmt(UNAVAILABLE if value is None else value) for value in values) + " |")
    return "\n".join(rendered)


def render_report(analysis: RunAnalysis) -> str:
    lines = ["# File Provider performance report", "", "## Aggregate measurements", ""]
    lines.extend(f"{name} = {_fmt(value)}" for name, value in analysis.aggregates.items())
    lines.extend([
        "", f"WINDOWS_INTERNAL_BREAKDOWN = {analysis.windows_internal_breakdown}",
        f"NETWORK_PLUS_DISPATCH = {analysis.network_plus_dispatch}",
        f"DESTINATION_MATERIALIZATION_TIMESTAMP = {UNAVAILABLE}",
        "", "## Correctness guard", "",
    ])
    lines.extend(f"{name} = {_fmt(value)}" for name, value in analysis.correctness.items())
    transport_aggregates = analysis.transport.get("aggregates", {})
    slowest_reads = analysis.transport.get("slowest_reads", [])
    lines.extend(["", "## Windows FILE_READ transport", ""])
    if isinstance(transport_aggregates, dict):
        lines.extend(
            f"{name} = {_fmt(value)}"
            for name, value in transport_aggregates.items()
        )
    lines.extend([
        "",
        "### TOP 10 slowest Mac FILE_READ to FILE_CHUNK",
        "",
        "| file | offset | length | mac_rtt_ms | decode_ms | dispatch_ms | lookup_ms | open_ms | read_ms | encode_ms | queue_wait_ms | socket_write_ms | encryption_ms | active_files | queue_depth_bytes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    if isinstance(slowest_reads, list):
        for row in slowest_reads:
            if not isinstance(row, dict):
                continue
            columns = (
                "file", "offset", "length", "mac_rtt_ms", "decode_ms",
                "dispatch_ms", "lookup_ms", "open_ms", "read_ms", "encode_ms",
                "queue_wait_ms", "socket_write_ms", "encryption_ms",
                "concurrent_active_files",
                "outbound_queue_depth_bytes",
            )
            lines.append(
                "| "
                + " | ".join(_fmt(row.get(column, UNAVAILABLE)) for column in columns)
                + " |"
            )
    lines.extend([
        "", "## Per-file representative rows", "", _table(analysis.per_file[:5]),
        "", "## TOP 5 slowest", "", _table(analysis.top_five),
        "", "## Bottleneck classification", "",
        f"PERFORMANCE_BASELINE_VALID = {'YES' if analysis.performance_baseline_valid else 'NO'}",
        f"PRIMARY_BOTTLENECK = {analysis.primary_bottleneck}",
        f"SECONDARY_BOTTLENECK = {analysis.secondary_bottleneck}",
        f"~4_SEC_PER_FILE_ACCOUNTED_FOR = {'YES' if analysis.primary_bottleneck != 'I' else 'NO'}",
        "evidence = " + (", ".join(analysis.classification_evidence) or UNAVAILABLE),
    ])
    lines.extend(f"{name} = {value}" for name, value in analysis.contributions.items())
    lines.extend([
        f"INTERNAL_SERIALIZATION = {analysis.internal_serialization}",
        f"FINDER_SERIALIZATION = {analysis.finder_serialization}",
        "", "TOP_OPTIMIZATION_CANDIDATES =",
    ])
    if analysis.optimization_candidates:
        for index, candidate in enumerate(analysis.optimization_candidates[:3], 1):
            lines.append(f"{index}. {candidate['measured_bottleneck_addressed']}; expected: {candidate['expected_effect']}; risk: {candidate['risk']}; experiment: {candidate['smallest_experiment']}")
    else:
        lines.append("UNAVAILABLE")
    lines.extend(["", "RECOMMENDED_NEXT_EXPERIMENT = " + (
        analysis.optimization_candidates[0]["smallest_experiment"]
        if analysis.optimization_candidates else UNAVAILABLE
    ), ""])
    return "\n".join(lines)


def write_artifacts(analysis: RunAnalysis, output_dir: Path | str) -> ArtifactPaths:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "run.json"
    csv_path = target / "per-file.csv"
    report_path = target / "report.md"
    json_path.write_text(json.dumps(_jsonable(analysis), indent=2, sort_keys=True) + "\n")
    columns = [
        "item", "size", "chunks", "fetch_total_ms", "open_fetch_ms",
        "first_byte_ms", "transfer_ms", "finalize_ms", "queue_wait_ms",
        "max_concurrency_at_start",
    ]
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for metric in analysis.per_file:
            writer.writerow({column: getattr(metric, column) for column in columns})
    report_path.write_text(render_report(analysis))
    return ArtifactPaths(json=json_path, csv=csv_path, report=report_path)


__all__ = [
    "ArtifactPaths", "FetchMetrics", "ParsedEvents", "PerfEvent", "RunAnalysis",
    "NOT_APPLICABLE", "UNAVAILABLE", "analyze", "classify_bottleneck", "concurrency_sweep",
    "burst_accounting", "interval_union_ns", "nearest_rank", "parse_perf_lines", "render_report",
    "transport_breakdown", "write_artifacts",
]
