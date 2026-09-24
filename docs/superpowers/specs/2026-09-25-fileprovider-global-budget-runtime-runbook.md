# Global FILE_READ budget — runtime 4/6/8 runbook (paused, resume when stand stable)

Status: implementation COMPLETE + offline-proven (commit 138d9e95, 806 tests).
Runtime physical measurement PAUSED on 2026-09-25 due to test-stand instability
(Wi-Fi/peer link dropping mid-transfer) and a fileproviderd caching obstacle
(below). This runbook captures everything needed to collect the 4/6/8 numbers
cleanly when a stable two-machine stand is available.

## What is proven already
- Budget selector is LIVE in a production Nuitka build: the host log emits
  `fp_read_window_selected window=N` AND `fp_read_budget_selected budget=M`.
  Set via `launchctl setenv DUO_FP_READ_WINDOW <w>` + `DUO_FP_READ_BUDGET <b>`,
  then relaunch `/Applications/DuoInput.app` (env is read at host import).
- One clean W1 reference captured (old build, 100/100 byte-exact):
  throughput ≈ 4.72 MB/s, fetch_window 8.94 s, read→chunk p50/p95 = 19/484 ms,
  max_global_outstanding_reads = 4, per-stream = 1, 0 late/fail.
- Default budget = MAX_ACTIVE_FETCHES*PER_FILE_READ_WINDOW (16 at window 4;
  4 at window 1) = current unbounded-W4 ceiling → inert by default.

## THE COLD-READ OBSTACLE (must solve to get valid numbers)
fileproviderd keeps a materialized replica per item in
`~/Library/CloudStorage/DuoInput-DuoInput/<transfer_id>/`. Empirically, pasting
the SAME source content a 2nd time (even as a NEW generation with new item IDs
`<transfer_id>:<index>`) serves entirely from that replica: **0 FILE_READ, 0
host fetch, instant copy** — so every repeat run measures nothing. Production
has `post_fetch_eviction = OFF`, so nothing reverts items to dataless between
runs.

Two ways to force COLD reads each run:
1. RECOMMENDED — distinct dataset copies. Pre-make N byte-distinct copies of the
   mixed set with the SAME size profile but unique filenames/bytes
   (`B-mixed-01 … B-mixed-12`, one per planned run). Different content ⇒ never
   dedups against a prior replica ⇒ always cold. Throughput stays comparable
   (throughput depends on the byte/size profile, which is identical). This
   sidesteps caching without touching the extension.
   - Generate on Windows, e.g. per copy: same 100 files/42,158,741 B layout,
     but append a per-copy random tail or regenerate random bytes at each size.
2. Post-fetch eviction (NSFileProviderManager.evictItem). The extension already
   has a tested `EvictionCoordinator`/`ManagerEvictionEnvironment`; wiring it ON
   makes items dataless ~post-copy so the NEXT run is cold. CONFIRMED to work
   (all 100 files went blocks=0/dataless after a run). BUT with grace=2s it
   RACES a live multi-file paste and destabilised the copy in this session
   ("нужно загрузить …", 99/100). If used, raise `grace`/only evict on an
   explicit between-run trigger, never at 2s during a burst paste. A standalone
   evictor is NOT possible: NSFileProviderManager from a non-provider process
   fails -2001, and the appex has no app-group entitlement to share the domain.

Cold-read verification (external, no app change): after a paste,
`find <domain>/<gen> -type f -exec stat -f '%b %z %N' {} \;` — `%b` (blocks) is
0 for dataless, >0 for materialized. A valid run must show the host log with
100 `fp_fetch_started` + `fp_range_issued` for THAT generation.

## Link-stability caveat (hit repeatedly this session)
- Do NOT leave the host app down for minutes (e.g. during a rebuild) while a
  Windows transfer/pairing is expected: the peer times out and logs
  `связь разорвана: второй компьютер молчит`; it must be re-paired from Windows.
- A quick config relaunch (~5 s) auto-reattaches (inbound TLS): before pasting,
  confirm in the host log a fresh `coordinator_link_attached` +
  `peer_capabilities_known capabilities=clipboard/1,files/2` + `fp_ipc_connect`,
  and NO later `связь разорвана`.
- Net capacity drifted 34→67 Mbit/s within ~15 min here — bracket every block
  with `iperf3` (Mac `iperf3 -s -1`; Windows `iperf3 -c <mac-ip> -t 10`) and use
  the balanced order below. Mac LAN IP this session = 192.168.0.252 (verify with
  `ipconfig getifaddr en0`; the old 192.168.0.139 is stale).

## Config matrix
```
W1      : DUO_FP_READ_WINDOW=1  DUO_FP_READ_BUDGET unset   (control)
B4      : DUO_FP_READ_WINDOW=4  DUO_FP_READ_BUDGET=4
B6      : DUO_FP_READ_WINDOW=4  DUO_FP_READ_BUDGET=6
B8      : DUO_FP_READ_WINDOW=4  DUO_FP_READ_BUDGET=8
W4unb   : DUO_FP_READ_WINDOW=4  DUO_FP_READ_BUDGET=16      (old unbounded control)
```

## Per-run procedure
1. `launchctl setenv DUO_FP_READ_WINDOW <w>`; set/`unsetenv DUO_FP_READ_BUDGET`.
2. Quit + relaunch `/Applications/DuoInput.app`; confirm both selector log lines
   and a healthy re-attached link (above).
3. Fresh empty target folder (`mkdir`), note the host-log line count as a marker.
4. On Windows: copy the NEXT unused distinct dataset copy (`B-mixed-NN`).
5. In Finder: Cmd+V into the target; wait for all 100; confirm 100 files /
   42,158,741 B and 100 `fp_fetch_started` for that generation in the log.
6. Slice the log from the marker; run
   `configurator/.venv-build/bin/python tools/fp_window_metrics.py <slice>
   --total-bytes 42158741`. Record throughput, fetch_window_s, read→chunk
   p50/p95, chunk-gap p50/p95, max_global_outstanding_reads,
   max_outstanding_reads_per_stream, fp_late_chunk/failed = 0.

## Balanced order (network drift → interleave, ≥2 obs per candidate)
```
iperf3;  W1 B4 B6 B8 W4unb ;  iperf3;  W4unb B8 B6 B4 W1 ;  iperf3
(3rd block only for candidates whose two observations disagree >~10%.)
```
Acceptance gates (spec §27/§28): 100/100 byte-exact; B4/B6/B8
max_global_outstanding_reads ≤ 4/6/8; isolated large fetch still reaches
per-fetch 4 under B≥4; bound_violations=0. Then one cancel + one disconnect run
against the best candidate (§30). Do NOT test B>8; no adaptive logic (§31).

## Interpretation (spec §29)
Pick the measured winner. If B4 wins → PER_FILE_WINDOW=4, GLOBAL_BUDGET=4. If all
bounded ≈ W1 → contention controlled but little mixed-workload W4 benefit. If all
bounded regress → STOP, report, no adaptive policy.
