#!/usr/bin/env python3
"""Derive per-run WINDOW acceptance metrics (spec §9) from the Duo Input host
log (``~/.local/share/DuoInput/logs/duo-input.log`` or a per-run snapshot).

Pure external observer: it reads the structured ``_log_event`` lines the host
already emits (``fp_range_issued`` / ``fp_bytes_received`` / ``fp_range_consumed``
/ ``fp_fetch_started`` / ``fp_fetch_completed|failed|cancelled`` / ``fp_late_chunk``
/ ``fp_cancel_outstanding`` / ``fp_window_bound_violation`` /
``fp_oversized_chunk`` / ``fp_truncated``) and reconstructs, over time:

* outstanding READS per stream   = issued - received (network in-flight)
* outstanding BYTES per stream   = issued - consumed (in-flight + reorder buffer)
* window occupancy / time-window-full, read->chunk latency, chunk-arrival gaps
* fetch window (first started -> last completed), throughput, invocations,
  unique items, refetch/duplicate-fetch detection, max concurrent fetches.

It changes nothing in the app. Usage:

    tools/fp_window_metrics.py <log-or-snapshot> [--total-bytes N] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) \S+ \S+ "
    r"(?P<event>fp_\w+|FETCH_\w+) (?P<rest>.*)$"
)
_KV = re.compile(r"(\w+)=(\S+)")


def _ts(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S,%f").timestamp()


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def parse(path: Path) -> list[tuple[float, str, dict]]:
    events: list[tuple[float, str, dict]] = []
    for line in path.read_text(errors="replace").splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        kv = {k: v for k, v in _KV.findall(m.group("rest"))}
        events.append((_ts(m.group("ts")), m.group("event"), kv))
    events.sort(key=lambda e: e[0])
    return events


def analyse(events: list[tuple[float, str, dict]], total_bytes: int | None) -> dict:
    # read_id -> (fetch_token, length); join key across issued/received/consumed
    read_len: dict[str, int] = {}
    read_fetch: dict[str, str] = {}
    issued_ts: dict[str, float] = {}

    # time series of (ts, fetch_token, d_inflight, d_live_bytes, d_live_reads)
    per_fetch_reads: dict[str, int] = {}
    per_fetch_bytes: dict[str, int] = {}
    max_reads_stream = 0
    max_bytes_stream = 0
    max_global_reads = 0
    max_global_bytes = 0

    # occupancy: sample live-range count per fetch on every change, time-weighted
    live_ranges: dict[str, int] = {}
    occ_area = 0.0  # sum over fetches of integral(live_ranges dt)
    full_area = 0.0  # integral(1 if live==WINDOW) — WINDOW inferred as observed max
    last_ts: dict[str, float] = {}

    read_to_chunk: list[float] = []
    recv_ts: list[float] = []

    started: list[tuple[float, str]] = []
    completed_ts: list[float] = []
    fetch_started_keys: list[str] = []
    active = 0
    max_active = 0

    counts = {
        "fp_late_chunk": 0,
        "fp_window_bound_violation": 0,
        "fp_oversized_chunk": 0,
        "fp_truncated": 0,
        "fp_fetch_completed": 0,
        "fp_fetch_failed": 0,
        "fp_fetch_cancelled": 0,
        "fp_cancel_outstanding": 0,
    }
    cancel_outstanding: list[int] = []

    def bump_global() -> None:
        nonlocal max_global_reads, max_global_bytes
        max_global_reads = max(max_global_reads, sum(per_fetch_reads.values()))
        max_global_bytes = max(max_global_bytes, sum(per_fetch_bytes.values()))

    def occ_step(tok: str, ts: float) -> None:
        # accrue time-weighted occupancy for tok up to ts, then caller mutates
        nonlocal occ_area, full_area
        prev = last_ts.get(tok)
        if prev is not None:
            dt = ts - prev
            occ_area += live_ranges.get(tok, 0) * dt
            if live_ranges.get(tok, 0) >= observed_window:
                full_area += dt
        last_ts[tok] = ts

    # first pass to learn the observed window (max live ranges any fetch reached)
    observed_window = 1
    tmp_live: dict[str, int] = {}
    for _ts_, ev, kv in events:
        tok = kv.get("fetch_token", "")
        if ev == "fp_range_issued":
            tmp_live[tok] = tmp_live.get(tok, 0) + 1
            observed_window = max(observed_window, tmp_live[tok])
        elif ev == "fp_range_consumed":
            tmp_live[tok] = max(0, tmp_live.get(tok, 0) - 1)

    active_span: dict[str, list[float]] = {}

    for ts, ev, kv in events:
        tok = kv.get("fetch_token", "")
        rid = kv.get("read_id", "")
        if ev == "fp_fetch_started":
            active += 1
            max_active = max(max_active, active)
            started.append((ts, f'{kv.get("transfer_id")}:{kv.get("entry_index")}'))
            fetch_started_keys.append(f'{kv.get("transfer_id")}:{kv.get("entry_index")}')
        elif ev in ("fp_fetch_completed", "fp_fetch_failed", "fp_fetch_cancelled"):
            counts[ev] += 1
            active = max(0, active - 1)
            completed_ts.append(ts)
        elif ev == "fp_range_issued":
            length = int(kv.get("length", 0))
            read_len[rid] = length
            read_fetch[rid] = tok
            issued_ts[rid] = ts
            occ_step(tok, ts)
            live_ranges[tok] = live_ranges.get(tok, 0) + 1
            per_fetch_reads[tok] = per_fetch_reads.get(tok, 0) + 1
            per_fetch_bytes[tok] = per_fetch_bytes.get(tok, 0) + length
            max_reads_stream = max(max_reads_stream, per_fetch_reads[tok])
            max_bytes_stream = max(max_bytes_stream, per_fetch_bytes[tok])
            bump_global()
            active_span.setdefault(tok, [ts, ts])[1] = ts
        elif ev == "fp_bytes_received":
            # a range left the network (issued -> received); still buffered
            ftok = read_fetch.get(rid, tok)
            if per_fetch_reads.get(ftok):
                per_fetch_reads[ftok] -= 1
            if rid in issued_ts:
                read_to_chunk.append(ts - issued_ts[rid])
            recv_ts.append(ts)
            active_span.setdefault(ftok, [ts, ts])[1] = ts
        elif ev == "fp_range_consumed":
            length = int(kv.get("length", read_len.get(rid, 0)))
            occ_step(tok, ts)
            live_ranges[tok] = max(0, live_ranges.get(tok, 0) - 1)
            if per_fetch_bytes.get(tok):
                per_fetch_bytes[tok] = max(0, per_fetch_bytes[tok] - length)
            active_span.setdefault(tok, [ts, ts])[1] = ts
        elif ev in counts:
            counts[ev] += 1
            if ev == "fp_cancel_outstanding":
                cancel_outstanding.append(int(kv.get("outstanding", 0)))

    fetch_window = None
    if started and completed_ts:
        fetch_window = max(completed_ts) - min(t for t, _ in started)

    throughput = None
    if total_bytes and fetch_window and fetch_window > 0:
        throughput = total_bytes / fetch_window

    gaps = [b - a for a, b in zip(sorted(recv_ts), sorted(recv_ts)[1:])]
    total_active_time = sum(sp[1] - sp[0] for sp in active_span.values()) or 1.0

    unique_items = len(set(fetch_started_keys))
    refetched = len(fetch_started_keys) - unique_items

    return {
        "observed_window": observed_window,
        "fetch_invocations": len(fetch_started_keys),
        "unique_items": unique_items,
        "refetched_items": refetched,
        "duplicate_fetch_items": refetched,
        "max_active_fetches": max_active,
        "max_outstanding_reads_per_stream": max_reads_stream,
        "max_global_outstanding_reads": max_global_reads,
        "max_outstanding_bytes_per_stream": max_bytes_stream,
        "max_global_outstanding_bytes": max_global_bytes,
        "read_to_first_chunk_p50": _pct(read_to_chunk, 0.50),
        "read_to_first_chunk_p95": _pct(read_to_chunk, 0.95),
        "chunk_arrival_gap_p50": _pct(gaps, 0.50),
        "chunk_arrival_gap_p95": _pct(gaps, 0.95),
        "window_occupancy_mean": (occ_area / total_active_time),
        "time_window_full_percent": 100.0 * full_area / total_active_time,
        "fetch_window_s": fetch_window,
        "throughput_bps": throughput,
        "counts": counts,
        "cancel_outstanding": cancel_outstanding,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("log", type=Path)
    ap.add_argument("--total-bytes", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = analyse(parse(args.log), args.total_bytes)
    if args.json:
        print(json.dumps(result, indent=2))
        return
    for key, value in result.items():
        if key == "counts":
            print("counts:")
            for k, v in value.items():
                print(f"    {k} = {v}")
        elif isinstance(value, float):
            print(f"{key} = {value:.4f}")
        else:
            print(f"{key} = {value}")


if __name__ == "__main__":
    main()
