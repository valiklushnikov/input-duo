# Phase F — dataless → materialized transition tail (2026-09-27)

Diagnostic only. One clean Finder Cmd+V of B-mixed-22 (new generation f71c6d0c…,
all 100 replica items SF_DATALESS before paste), W4/B8 defaults, 0 stale-generation
events, 100 fetches / 131 FILE_READ, 0 failures, 100/100 byte-exact.
Observers (passive): host log, extension perf (live log stream), fileproviderd debug
stream, per-file replica stat (SF_DATALESS cleared, 50 ms poll), destination per-file
`st_ctime_ns` (Finder finalization; the 20 ms dest poller had timed out before the
paste).

## Per-item facts
- extension completion → replica item materialized on disk: p50 64 ms, p95 115,
  max 126 (poll 50 ms) — state propagation is fast.
- extension completion → destination final: p50 5.23 s, p95 5.61, max 8.04, min 3.99.
- Destination files finalized strictly in entry_index order (0,1,2,…).
- **0 of 100 destination finals before the last extension completion**; first
  destination final 82 ms after it (17:41:52.785 → .867); last at 17:41:56.789.

## fileproviderd coordination
200 `provideItemAtURL` (NSFileCoordinator) transactions in the paste window:
- 94 during materialization: each finishes as its item is materialized (first +1.34 s,
  last +8.705 s; finished − preceding extension completion p50 19 ms). fileproviderd
  does NOT hold them until the end.
- 106 after the last materialization: serial, interval p50 13.9 ms (warm control
  7.6 ms), span 3.99 s; each destination final follows the nearest provide-finished by
  p50 1.6 ms (p95 5.4 ms).
- Largest post-barrier stalls coincide with fileproviderd batch bookkeeping for the
  whole generation: 1.03 s between items 2→3 (≈290 "Enumerator notification
  completed/waiting for flush", 104 "owner changed for doc", "scheduler not stable:
  jobs are running"); 0.63 s between 12→13 (100 × "NSProgress … Unpublishing").
  Correlation is chronological (not proven causal).

## Conclusions
- DESTINATION_COPY_WAITS_FOR_ALL_MATERIALIZATION: CONFIRMED (this workload/path).
  Barrier owner: Finder — sources are individually ready (provides released
  progressively, replica materialized within ~64 ms) yet no destination clone starts
  before the 100th.
- Post-barrier phase 4.00 s vs warm materialized Cmd+V 1.04 s: Finder performs a
  second serial provide→clone pass per item; it runs ~2x slower per item than warm and
  stalls while fileproviderd flushes per-generation bookkeeping triggered by the
  just-finished downloads. Extension/host excluded (completion 0–2 ms, materialization
  64 ms).
- Owner: MIXED (Finder barrier + serial pass; fileproviderd post-download bookkeeping).
  Not our transport.
