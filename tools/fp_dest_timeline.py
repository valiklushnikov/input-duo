#!/usr/bin/env python3
"""Passive destination observer for one Finder paste (Finder post-fetch tail diagnostics).

Polls ``os.stat`` of the destination directory entries (no reads, no hashing,
never touches the File Provider domain) and records, on the wall clock that the
host log and ``log show`` use:

* T9  first destination entry appears        (``t_first_entry``)
* T9b all expected entries present           (``t_all_present``)
* T10 all expected files at expected size    (``t_all_sized``)
* T10b last Finder in-progress marker cleared (Finder stamps a 1984-01-24
       creation date on files it is still copying and restores it per file
       when that file's copy finalizes)          (``t_all_finalized``)
* T11 destination byte-stable: the LAST time any (size, mtime, ctime,
       birthtime) of any expected file changed, observed over a quiet period
       after T10b                               (``t_last_change``)

Per-file first-seen / sized / finalized times and a coarse (count, bytes)
series are kept for gap analysis. Timing error = one poll interval.
Byte-exact validation is a separate step AFTER completion (tools never hash
during the copy).

Usage: fp_dest_timeline.py <dest> <manifest.json> <out.json>
                           [--interval 0.05] [--quiet 5] [--timeout 900]
manifest.json: {"files": [{"relative_path": ..., "size": ...}, ...]}
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def _busy(st: os.stat_result) -> bool:
    return time.localtime(st.st_birthtime).tm_year == 1984


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dest")
    ap.add_argument("manifest")
    ap.add_argument("out")
    ap.add_argument("--interval", type=float, default=0.05)
    ap.add_argument("--quiet", type=float, default=5.0)
    ap.add_argument("--timeout", type=float, default=900.0)
    a = ap.parse_args()

    dest = Path(a.dest)
    want = {f["relative_path"]: f["size"] for f in json.loads(Path(a.manifest).read_text())["files"]}
    first_seen: dict[str, float] = {}
    sized: dict[str, float] = {}
    finalized: dict[str, float] = {}
    last_sig: dict[str, tuple] = {}
    t_first_entry = t_all_present = t_all_sized = t_all_finalized = None
    t_last_change = None
    series: list[tuple[float, int, int]] = []
    t0 = time.time()
    status = "TIMEOUT"
    while time.time() - t0 < a.timeout:
        now = time.time()
        try:
            names = os.listdir(dest)
        except FileNotFoundError:
            names = []
        if names and t_first_entry is None:
            t_first_entry = now
        count = total = 0
        for n in want:
            try:
                st = os.stat(dest / n)
            except FileNotFoundError:
                continue
            count += 1
            total += st.st_size
            first_seen.setdefault(n, now)
            if st.st_size == want[n]:
                sized.setdefault(n, now)
            if st.st_size == want[n] and not _busy(st):
                finalized.setdefault(n, now)
            sig = (st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_birthtime)
            if last_sig.get(n) != sig:
                last_sig[n] = sig
                t_last_change = now
        if not series or series[-1][1:] != (count, total):
            series.append((round(now, 3), count, total))
        if t_all_present is None and count == len(want):
            t_all_present = now
        if t_all_sized is None and len(sized) == len(want):
            t_all_sized = now
        if t_all_finalized is None and len(finalized) == len(want):
            t_all_finalized = now
        if t_all_finalized is not None and now - t_last_change >= a.quiet:
            status = "DONE"
            break
        time.sleep(a.interval)

    out = {
        "status": status,
        "interval_s": a.interval,
        "quiet_s": a.quiet,
        "t_first_entry": t_first_entry,
        "t_all_present": t_all_present,
        "t_all_sized": t_all_sized,
        "t_all_finalized": t_all_finalized,
        "t_last_change": t_last_change,
        "files_expected": len(want),
        "files_finalized": len(finalized),
        "per_file": {n: {"first_seen": first_seen.get(n), "sized": sized.get(n),
                         "finalized": finalized.get(n)} for n in want},
        "series": series,
    }
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k not in ("per_file", "series")}))
    return 0 if status == "DONE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
