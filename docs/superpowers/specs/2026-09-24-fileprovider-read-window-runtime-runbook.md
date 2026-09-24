# Runtime validation runbook — per-file FILE_READ WINDOW=4 (spec §23–31)

These steps require the physical two-machine stand (a Mac running the File
Provider extension + real Finder, and a Windows peer serving files over the real
network). They cannot be run in the offline dev/CI environment and are handed off
for you to execute. Fill the RESULTS blocks and paste them back into the final
report (§33).

BASE_HEAD = a4aeb43
PRODUCTION_WINDOW_COMMIT = 483bed0   (REVIEW_FIX_COMMIT = see final report)

## Build the two variants (§23)

The only difference between A and B must be the window size. Use a build-time or
env override of `PER_FILE_READ_WINDOW` — do NOT change anything else.

- Variant **A (WINDOW=1)**: set `PER_FILE_READ_WINDOW = 1` in
  `configurator/src/duo_input/transfer/fileprovider_backend.py` (or via an env
  shim if you add one) and build the app.
- Variant **B (WINDOW=4)**: the shipped default (`PER_FILE_READ_WINDOW = 4`).

Everything else identical: contentPolicy=downloadLazily, pipeline depth 4,
prefetch wave 8, eviction OFF, same CHUNK=1 MiB, same network.

## Instrumentation to capture each run

- Host app log: `~/.local/share/DuoInput/logs/` — grep the structured events
  `fp_range_issued`, `fp_range_received`, `fp_range_consumed`, `fp_late_chunk`,
  `fp_unexpected_chunk`, `fp_cancel_outstanding`, and the max gauges in
  `backend.counters` (`fp_max_outstanding_reads_per_stream`,
  `fp_max_global_outstanding_reads`, `fp_max_outstanding_bytes_per_stream`,
  `fp_max_global_outstanding_bytes`, `fp_window_bound_violation`).
- Extension log: `log stream --predicate 'subsystem == "com.duoinput.configurator.fileprovider"'`
  (categories `fetch`) for `fp_bytes_received` / `fp_fetch_completed` per item.
- fileproviderd log for -1005/-1004/-1000/Finder -36.
- Use `tools/fp_log_capture.py` (commit d292508) to snapshot both logs per run.
- Wall-clock USER_VISIBLE_COPY_TIME: time from Finder Cmd+V to the copy sheet
  disappearing.

## §24 Primary workload — mixed dataset

- Dataset: the accepted mixed-size set, FILES=100, TOTAL_BYTES ≈ 42,158,741.
- Fresh dataless generation, fresh empty destination folder, ONE Finder Cmd+V,
  no cancellation, no forced eviction, same network conditions.

## §25 Balanced sequence

Run at minimum, alternating to average out network variance:

```
W1-A, W4-A, W4-B, W1-B
```

If variance is high, extend with `W1-C, W4-C`. Do not cherry-pick — report all.

## §26/§27 Per-run: record

For each run: FETCH_WINDOW, USER_VISIBLE_COPY_TIME, TOTAL_BYTES,
EFFECTIVE_THROUGHPUT, MAX_ACTIVE_FETCHES, MAX_OUTSTANDING_READS_PER_STREAM,
MAX_GLOBAL_OUTSTANDING_READS, MAX_OUTSTANDING_BYTES_PER_STREAM,
MAX_GLOBAL_OUTSTANDING_BYTES, READ_TO_FIRST_CHUNK p50/p95,
CHUNK_ARRIVAL_GAP p50/p95, WINDOW_OCCUPANCY distribution, and for W4
TIME_WINDOW_FULL_PERCENT.

Correctness acceptance (every valid run): 100/100 byte-exact; missing=0; diff=0;
refetched=0; duplicate-fetch=0; duplicate/overlapping/unexpected ranges=0;
-1005=0; -1004=0; -1000=0; Finder -36=0; AND
MAX_OUTSTANDING_READS_PER_STREAM ≤ 4; MAX_OUTSTANDING_BYTES_PER_STREAM ≤ 4 MiB;
`fp_window_bound_violation` == 0.

## §29 Cancellation runtime test (one bounded run)

Use a sufficiently large file (≥ 8 MiB so W4 has multiple outstanding reads).
Start Finder paste; wait until `fp_range_issued` shows multiple outstanding for
the item; cancel the Finder operation. Record: OUTSTANDING_AT_CANCEL (from
`fp_cancel_outstanding`), LATE_CHUNKS (`fp_late_chunk` after cancel),
NEW_READS_AFTER_CANCEL (must be 0), INVALID_WRITES_AFTER_CANCEL (0),
DOUBLE_COMPLETION (0), PENDING_RANGE_LEAK (0 — by_read_id/by_token drained).

## §30 Disconnect/retry runtime test (one controlled run)

Only after §24 + §29 pass. Large transfer with W4 outstanding; trigger one
transient disconnect via the existing supported reconnect mechanism (do NOT
redesign reconnect). Verify: no deadlock; no stale-response corruption; final
output byte-exact OR a clean expected failure per the existing retry contract.
If it fails: STOP and diagnose (do not tune further).

## §31 Memory

Report observed peak PER_STREAM_OUTSTANDING_BYTES and GLOBAL_OUTSTANDING_BYTES
(from the max gauges). Expected normal upper bound with 4 active fetches ≈ 16 MiB
payload + framing/copy. Measure HOST_RSS_DELTA_W1 / HOST_RSS_DELTA_W4 if reliably
measurable (e.g. `footprint`/Activity Monitor around the paste); otherwise
RSS = UNAVAILABLE.

## §32 Do NOT tune further

Forbidden: WINDOW>4, adaptive window, CHUNK_SIZE/pipeline/prefetch/socket/TLS/
compression changes, new transport, FP cache changes. The question is only
whether bounded WINDOW=4 is safe and beneficial in production.

## RESULTS (fill in)

```
=== W1 REAL FILE PROVIDER ===   RUN_A: time= thr=   RUN_B: time= thr=   MEDIAN=
=== W4 REAL FILE PROVIDER ===   RUN_A: time= thr=   RUN_B: time= thr=   MEDIAN=
=== COMPARISON === user_visible_gain=  throughput_gain=  repeatable=YES/NO/INCONCLUSIVE
=== BOUNDS === reads/stream=  global reads=  bytes/stream=  global bytes=  violations=
=== CORRECTNESS === byte_exact=  -1005/-1004/-1000/-36=  refetch/dup=
=== CANCELLATION === outstanding_at_cancel=  late=  new_reads=0  invalid_writes=0  double=0  leak=0
=== DISCONNECT/RETRY === result=  stale_accepted=  byte_exact=  deadlock=
```
