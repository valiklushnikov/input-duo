from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

TOOL = Path(__file__).with_name("fp_tail_timeline.py")
TID = "a" * 32


def _host(ts: str, ev: str, rest: str) -> str:
    return f"2026-09-25 {ts} INFO duo_input.transfer.fileprovider_backend {ev} {rest}"


def _ext(ts: str, event: str, rest: str) -> str:
    return json.dumps({"timestamp": f"2026-09-25 {ts}000+0200",
                       "eventMessage": f"fp_perf event={event} mono_ns=1 clock=swift_uptime {rest}"})


def _wall(ts: str) -> float:
    return datetime.strptime(f"2026-09-25 {ts}", "%Y-%m-%d %H:%M:%S.%f").timestamp()


def test_partitions_one_fetch_into_measured_segments(tmp_path: Path) -> None:
    other = "b" * 32
    host = [
        _host("10:00:00,100", "fp_fetch_started", f"transfer_id={TID} entry_index=0 fetch_token=t1 name=x"),
        _host("10:00:00,110", "fp_range_issued", f"transfer_id={TID} entry_index=0 fetch_token=t1 read_id=1 offset=0 length=5"),
        _host("10:00:00,300", "fp_bytes_received", f"transfer_id={TID} entry_index=0 fetch_token=t1 read_id=1 bytes=5"),
        _host("10:00:00,310", "fp_fetch_completed", f"transfer_id={TID} entry_index=0 fetch_token=t1 live_reads=0"),
        # another generation must be ignored
        _host("10:00:05,000", "fp_fetch_failed", f"transfer_id={other} entry_index=0 fetch_token=zz live_reads=0"),
    ]
    ext = [
        _ext("10:00:00.090", "fetch_enter", f"item_identifier={TID}:0 transfer_id={TID} entry_index=0"),
        _ext("10:00:00.330", "fetch_contents_complete", f"item_identifier={TID}:0 transfer_id={TID} status=ok"),
        _ext("10:00:00.331", "fetch_contents_returned", f"item_identifier={TID}:0 transfer_id={TID} status=ok"),
    ]
    dest = {"t_first_entry": _wall("10:00:00.000"), "t_all_present": _wall("10:00:00.050"),
            "t_all_sized": _wall("10:00:01.330"), "t_all_finalized": _wall("10:00:01.400"),
            "t_last_change": _wall("10:00:01.500")}
    (tmp_path / "h.log").write_text("\n".join(host))
    (tmp_path / "e.ndjson").write_text("\n".join(ext))
    (tmp_path / "d.json").write_text(json.dumps(dest))
    out = subprocess.run([sys.executable, str(TOOL), str(tmp_path / "h.log"), str(tmp_path / "e.ndjson"),
                          str(tmp_path / "d.json"), TID], capture_output=True, text=True, check=True)
    rep = json.loads(out.stdout)
    seg = rep["segments_s"]
    assert seg["A_paste_to_first_fetch(T0->T1)"] == 0.09
    assert seg["C_network_active(T2->T4)"] == 0.19
    assert seg["D_last_chunk_to_host_done(T4->T5)"] == 0.01
    assert seg["E_host_done_to_ext_completion(T5->T6)"] == 0.02
    assert seg["E2_completion_handler_duration(T6->T7)"] == 0.001
    assert seg["F_ext_done_to_dest_sized(T6->T10)"] == 1.0
    assert seg["TOTAL_OBSERVED(T0->T11)"] == 1.5
    assert rep["timeline_rel_s"]["T12_finder_ui_done"] is None  # never fabricated
    assert rep["counts"]["host_fetch_started"] == 1
    # post-final-fetch zero-active gap up to destination byte-stable
    gaps = rep["host_active_zero_gaps"]
    assert gaps and gaps[-1]["class"] == "POST_FINAL_FETCH"
    assert gaps[-1]["duration_s"] == 1.19
