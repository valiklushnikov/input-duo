#!/usr/bin/env python3
"""Partition one Finder paste into measured segments (post-fetch tail diagnostics).

Pure external observer. Inputs (all wall-clock, one machine):

* host log slice        (~/.local/share/DuoInput/logs/duo-input.log; ms resolution)
* extension perf ndjson  (``log show --style ndjson --predicate
                          'subsystem == "com.duoinput.configurator.fileprovider"'``)
* destination timeline   (tools/fp_dest_timeline.py output)

Boundaries (only those actually present are reported; none are inferred):

  T0  first destination entry (Finder began the paste)          dest observer
  T1  first extension fetch_enter (fileproviderd demand)         extension
  T1h first host fp_fetch_started                                host
  T2  first host fp_range_issued (FILE_READ emitted)             host
  T3  last  host fp_range_issued                                 host
  T4  last  host fp_bytes_received (FILE_CHUNK correlated)       host
  T5  last  host fp_fetch_completed                              host
  T6  last  extension fetch_contents_complete (just BEFORE the
      fetchContents completionHandler is invoked)                extension
  T7  last  extension fetch_contents_returned (completionHandler
      returned)                                                  extension
  T9  = T0; T9b all destination entries present                  dest observer
  T10 all destination files at expected size                     dest observer
  T10b last Finder per-file in-progress marker cleared           dest observer
  T11 last destination metadata/size change (byte-stable)        dest observer
  T12 Finder UI completion                                       NOT OBSERVED (manual)

Usage: fp_tail_timeline.py <host.log> <ext.ndjson> <dest.json> <transfer_id> [--json out]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

_HOST = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) \S+ \S+ (fp_\w+) (.*)$")
_KV = re.compile(r"(\w+)=(\S+)")


def _host_events(path: Path, tid: str):
    out = []
    for line in path.read_text(errors="replace").splitlines():
        m = _HOST.match(line)
        if not m:
            continue
        kv = dict(_KV.findall(m.group(3)))
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S,%f").timestamp()
        out.append((ts, m.group(2), kv))
    out.sort(key=lambda e: e[0])
    # restrict to this generation (fetch tokens of this transfer)
    toks = {kv.get("fetch_token") for _, ev, kv in out if ev == "fp_fetch_started" and kv.get("transfer_id") == tid}
    return [e for e in out if e[2].get("transfer_id") == tid or e[2].get("fetch_token") in toks]


def _ext_events(path: Path, tid: str):
    out = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = rec.get("eventMessage", "")
        if not msg.startswith("fp_perf "):
            continue
        kv = dict(_KV.findall(msg))
        if tid not in (kv.get("transfer_id", "") + " " + kv.get("item_identifier", "")):
            continue
        ts = datetime.strptime(rec["timestamp"][:26], "%Y-%m-%d %H:%M:%S.%f").timestamp()
        out.append((ts, kv.get("event"), kv))
    out.sort(key=lambda e: e[0])
    return out


def _first(events, name):
    ts = [t for t, e, _ in events if e == name]
    return min(ts) if ts else None


def _last(events, name):
    ts = [t for t, e, _ in events if e == name]
    return max(ts) if ts else None


def _zero_gaps(events, start_ev, end_evs, t_from, t_to, min_s=0.1):
    """Intervals within [t_from, t_to] with 0 active (start_ev..end_evs) fetches."""
    active, gaps, gap_start, prev = 0, [], t_from, "window_start"
    for ts, ev, kv in events:
        if ev == start_ev:
            if active == 0 and gap_start is not None and ts - gap_start >= min_s:
                gaps.append({"start": gap_start, "end": ts, "duration_s": round(ts - gap_start, 3),
                             "previous_event": prev, "next_event": f"{ev} {kv.get('entry_index', kv.get('item_identifier', ''))}"})
            active += 1
            gap_start = None
        elif ev in end_evs:
            active = max(0, active - 1)
            if active == 0:
                gap_start = ts
                prev = f"{ev} {kv.get('entry_index', kv.get('item_identifier', ''))}"
    if gap_start is not None and t_to is not None and t_to - gap_start >= min_s:
        gaps.append({"start": gap_start, "end": t_to, "duration_s": round(t_to - gap_start, 3),
                     "previous_event": prev, "next_event": "destination_stable(T11)"})
    return gaps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host_log")
    ap.add_argument("ext_ndjson")
    ap.add_argument("dest_json")
    ap.add_argument("transfer_id")
    ap.add_argument("--json")
    a = ap.parse_args()

    host = _host_events(Path(a.host_log), a.transfer_id)
    ext = _ext_events(Path(a.ext_ndjson), a.transfer_id)
    dest = json.loads(Path(a.dest_json).read_text())

    T = {
        "T0_first_dest_entry": dest.get("t_first_entry"),
        "T1_first_ext_fetch_enter": _first(ext, "fetch_enter"),
        "T1h_first_host_fetch_started": _first(host, "fp_fetch_started"),
        "T2_first_FILE_READ": _first(host, "fp_range_issued"),
        "T3_last_FILE_READ": _last(host, "fp_range_issued"),
        "T4_last_FILE_CHUNK": _last(host, "fp_bytes_received"),
        "T5_last_host_fetch_completed": _last(host, "fp_fetch_completed"),
        "T6_last_ext_completion_invoked": _last(ext, "fetch_contents_complete"),
        "T7_last_ext_completion_returned": _last(ext, "fetch_contents_returned"),
        "T9b_all_dest_present": dest.get("t_all_present"),
        "T10_all_dest_sized": dest.get("t_all_sized"),
        "T10b_all_dest_finalized": dest.get("t_all_finalized"),
        "T11_dest_byte_stable": dest.get("t_last_change"),
        "T12_finder_ui_done": None,
    }
    base = T["T0_first_dest_entry"] or min(v for v in T.values() if v)

    def d(x, y):
        return None if T[x] is None or T[y] is None else round(T[y] - T[x], 3)

    seg = {
        "A_paste_to_first_fetch(T0->T1)": d("T0_first_dest_entry", "T1_first_ext_fetch_enter"),
        "B_fetch_entry_to_first_read(T1->T2)": d("T1_first_ext_fetch_enter", "T2_first_FILE_READ"),
        "C_network_active(T2->T4)": d("T2_first_FILE_READ", "T4_last_FILE_CHUNK"),
        "D_last_chunk_to_host_done(T4->T5)": d("T4_last_FILE_CHUNK", "T5_last_host_fetch_completed"),
        "E_host_done_to_ext_completion(T5->T6)": d("T5_last_host_fetch_completed", "T6_last_ext_completion_invoked"),
        "E2_completion_handler_duration(T6->T7)": d("T6_last_ext_completion_invoked", "T7_last_ext_completion_returned"),
        "F_ext_done_to_dest_sized(T6->T10)": d("T6_last_ext_completion_invoked", "T10_all_dest_sized"),
        "F2_dest_sized_to_finalized(T10->T10b)": d("T10_all_dest_sized", "T10b_all_dest_finalized"),
        "G_finalized_to_byte_stable(T10b->T11)": d("T10b_all_dest_finalized", "T11_dest_byte_stable"),
        "TOTAL_OBSERVED(T0->T11)": d("T0_first_dest_entry", "T11_dest_byte_stable"),
    }

    # per-file: extension completion -> destination finalized (Finder's copy of that file)
    per_item_ext = {}
    for ts, ev, kv in ext:
        if ev == "fetch_contents_complete":
            per_item_ext[kv.get("item_identifier")] = ts
    host_gaps = _zero_gaps(host, "fp_fetch_started", ("fp_fetch_completed", "fp_fetch_failed", "fp_fetch_cancelled"),
                           T["T1h_first_host_fetch_started"], T["T11_dest_byte_stable"])
    ext_gaps = _zero_gaps(ext, "fetch_enter", ("fetch_contents_complete",),
                          T["T1_first_ext_fetch_enter"], T["T11_dest_byte_stable"])
    for g in host_gaps + ext_gaps:
        g["class"] = ("POST_FINAL_FETCH" if g["next_event"].startswith("destination_stable")
                      else "BETWEEN_DEMAND_WAVES")

    rel = lambda v: None if v is None else round(v - base, 3)
    report = {
        "timeline_rel_s": {k: rel(v) for k, v in T.items()},
        "timeline_wall": {k: (None if v is None else datetime.fromtimestamp(v).strftime("%H:%M:%S.%f")[:-3]) for k, v in T.items()},
        "segments_s": seg,
        "counts": {
            "host_fetch_started": sum(1 for _, e, _ in host if e == "fp_fetch_started"),
            "host_fetch_completed": sum(1 for _, e, _ in host if e == "fp_fetch_completed"),
            "ext_fetch_enter": sum(1 for _, e, _ in ext if e == "fetch_enter"),
            "ext_fetch_contents_complete": sum(1 for _, e, _ in ext if e == "fetch_contents_complete"),
            "ext_fetch_contents_returned": sum(1 for _, e, _ in ext if e == "fetch_contents_returned"),
            "ext_request_download_call": sum(1 for _, e, _ in ext if e == "request_download_call"),
            "ext_fetch_coalesced": sum(1 for _, e, _ in ext if e == "fetch_coalesced"),
        },
        "host_active_zero_gaps": [dict(g, start=rel(g["start"]), end=rel(g["end"])) for g in host_gaps],
        "ext_active_zero_gaps": [dict(g, start=rel(g["start"]), end=rel(g["end"])) for g in ext_gaps],
    }
    json.dump(report, sys.stdout, indent=1, ensure_ascii=False)
    print()
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
