# Global bounded FILE_READ budget over per-file W4 — DESIGN (phase 1)

Branch: feature/fileprovider-read-window-v2 (base = perf/file-provider-profiling,
which carries the Swift FinderBurst prefetch fix). BASE_HEAD at design time = fe749875.
Keep PER_FILE_READ_WINDOW = 4, CHUNK = 1 MiB. Do NOT change wire/XPC/Swift/pipeline/
prefetch/TLS/Windows _answer_read.

## Proven root cause (recap)
Isolated large file: W4 = 4.78 MB/s vs W1 3.52 (+35.7%, r2c 262 vs 514 ms) → per-file
W4 benefit CONFIRMED. Mixed 100-file (restored net): W4 −14.7% vs W1. Class D (>4 MiB,
77.4% of bytes) fetch dur +29%, r2c +125% under W4. Small files (≤1 MiB, 93 files)
neutral. Global outstanding: W1 ≤4, W4 ≤16. Windows `_answer_read` serial on one Qt
thread / one TLS socket. => 4 active fetches × 4 per-file reads = up to 16 concurrent
1-MiB reads over a single serially-answered socket → queueing that regresses even
large files. Fix target: keep per-file W4 + add a GLOBAL outstanding-read budget.

## Permit model (§3/§4) — permit == entry in self.by_read_id
`by_read_id[read_id]=fetch` is added ONLY in `_issue_read` (before send) and popped
in `_on_chunk` on RECEIVED and in `_clear_all_ranges` on any terminal settle. So
`len(self.by_read_id)` is ALREADY the exact, explicit set of in-flight reads = permits
in use. No separate counter (no leak risk).

```
GLOBAL_PERMIT_ACQUIRE = _issue_read: by_read_id[read_id]=fetch (range->IN_FLIGHT), before send()
GLOBAL_PERMIT_RELEASE = _on_chunk RECEIVED (by_read_id.pop) + _clear_all_ranges
                        (FILE_ERROR/cancel/disconnect/timeout/fail/whole-fetch-retry)
GLOBAL_PERMITS_IN_USE == len(self.by_read_id)   (invariant; explicit ownership set)
```
Narrowest correct boundary = "FILE_READ sent, response not yet correlated" = exactly
the proven over-subscription boundary. A RECEIVED chunk awaiting in-order consume does
NOT hold a transport permit (it is reorder-buffer memory, not transport).

## Invariants
```
FETCH_IN_FLIGHT_READS  = fetch.in_flight() <= PER_FILE_READ_WINDOW (4)   (existing per-file cap)
GLOBAL_IN_FLIGHT_READS = len(self.by_read_id) <= GLOBAL_READ_BUDGET (N)  (new gate, single emit site)
```

## Scheduler (§6/§7/§8) — round-robin permit pool
State: `self._rr: deque[str]` = active (REQUESTING, admitted) fetch tokens;
`fetch.pulled: bool` = consumer has started pulling.
`_wants_read(f)` = f is not None and f.pulled and f.state==REQUESTING and
token in _active and len(f.ranges) < WINDOW and f.plan_offset < f.size.

`_schedule_reads()` (replaces direct `_fill_window`/`_resume_windows` issuing):
```
scanned = 0
while len(self.by_read_id) < self._global_budget and scanned < len(self._rr):
    token = self._rr[0]; self._rr.rotate(-1)          # fair rotation
    f = self.by_token.get(token)
    if self._wants_read(f):
        length = min(CHUNK, f.size - f.plan_offset)
        off = f.plan_offset; f.plan_offset += length
        if not self._issue_read(f, off, length):       # genuine send-fail -> fetch settled
            continue
        scanned = 0                                    # progress -> reset scan
    else:
        scanned += 1
```
Each freed permit goes to the NEXT eligible fetch in rotation -> interleave ABCD ABCD,
not AAAA (§7). Starvation impossible (rotate(-1) + reset-on-progress).

Borrowing (§8): one active large fetch -> rotation of 1 -> it issues until
in_flight==WINDOW while budget>=4 -> isolated large file keeps full W4 (critical).
Never statically reserve a permit per possible fetch.

Triggers of `_schedule_reads()`: pull_chunk (after setting fetch.pulled=True);
_on_chunk (after permit release AND after _try_deliver consume); _admit_from_queue
(new active fetch); _finish_fetch (permit release + remove token from _rr).
`_rr` membership: add on transition to REQUESTING (_open_fetch REQUESTING branch and
_admit_from_queue promotion); remove in _finish_fetch.

## Small / zero-byte (§9)
Small 1-range file consumes exactly 1 permit, released on response correlation.
Zero-byte consumes 0 permits (no FILE_READ; completes via _try_deliver). Permits =
real outstanding reads, never reservation for WINDOW.

## Memory bounds (§10) for budget B
```
GLOBAL_IN_FLIGHT_PAYLOAD_BOUND = B * 1 MiB            (B=4->4, 6->6, 8->8 MiB)
GLOBAL_REORDER_BUFFER_BOUND    = sum over active fetches of RECEIVED-not-consumed;
    per-fetch ranges dict <= WINDOW=4 (in-flight+received together) => <=4 MiB/stream;
    active fetches <= MAX_ACTIVE_FETCHES=4 => <=16 MiB total window-state.
TOTAL_WINDOW_STATE_BOUND       = <= 4 * WINDOW * 1 MiB = 16 MiB (in-flight subset of this)
PER_STREAM                     = <= 4 MiB (unchanged)
```
Honest: the permit budget bounds IN-FLIGHT (<=B MiB); RECEIVED reorder blobs can
coexist with new in-flight, so total window-state cap stays 16 MiB (same as unbounded
W4, enforced by the per-fetch ranges<=WINDOW cap), NOT "B*1MiB". The budget fixes
transport over-subscription, not the overall memory cap.

## Budget selector
`GLOBAL_READ_BUDGET` via env `DUO_FP_READ_BUDGET` (mirrors `DUO_FP_READ_WINDOW`):
runtime-compare 4 / 6 / 8; a value >=16 == current unbounded-W4 control. Production
default chosen from runtime winner. Default/invalid -> pick a safe bounded value
(candidate 4) — decided after runtime.

## RED tests (§11) — 20 cases
isolated fetch still reaches per-file W4; budget 4/6/8 never exceeded; four large
fetches all progress (no starvation, interleave); permit returned on normal chunk;
out-of-order RECEIVED does not leak permit; FILE_ERROR returns permits; cancel with
outstanding returns permits; late chunk after cancel no double-release; disconnect
returns permits; retry clean permit ownership; completion leaves 0 owned; zero-byte
0 permits; small 1-range 1 permit; completion of one fetch wakes another; permit
count never <0; never > budget; four streams + out-of-order byte-exact; scheduler
never permanently starves an eligible fetch.

## SCHEDULER DESIGN block (for §21 report)
```
GLOBAL_PERMIT_ACQUIRE = _issue_read (by_read_id[read_id]=fetch, before send)
GLOBAL_PERMIT_RELEASE = _on_chunk RECEIVED + _clear_all_ranges (by_read_id.pop)
FAIRNESS_MODEL        = round-robin deque _rr, one read per selected eligible fetch, rotate(-1), reset-on-progress
PER_FETCH_BOUND       = 4
GLOBAL_BOUND          = len(by_read_id) <= DUO_FP_READ_BUDGET (test 4/6/8)
GLOBAL_IN_FLIGHT_MEMORY_BOUND = B * 1 MiB
GLOBAL_REORDER_MEMORY_BOUND   = <=16 MiB
TOTAL_BOUND                   = <=16 MiB window-state; per-stream <=4 MiB
```

## Implementation plan (next)
1. Constants: `_read_budget_from_env()` + `GLOBAL_READ_BUDGET`; `self._global_budget`;
   `self._rr = deque()`; `Fetch.pulled: bool = False`.
2. `_wants_read`, `_schedule_reads` (above). Redefine `_fill_window(fetch)` -> add
   token to _rr if active + `_schedule_reads()`; `_resume_windows` -> `_schedule_reads`.
3. pull_chunk: `fetch.pulled = True`.
4. _open_fetch REQUESTING + _admit_from_queue: add token to _rr.
5. _finish_fetch: remove token from _rr (like _pull_queue removal).
6. Keep MAX_TOTAL_BUFFERED_BYTES as secondary guard (won't fire at B<=8).
7. Telemetry (§12): global_budget, global_permits_in_use (=len by_read_id),
   permit_acquired/released, scheduler_wait/wakeup/selected_fetch, eligible_fetch_count,
   MAX_GLOBAL_OUTSTANDING_READS, MAX_PER_FETCH_OUTSTANDING_READS.
8. Migrate existing tests that assumed unbounded fill / _pull_queue byte-park.
9. Offline suites (window/FP/transport/Swift) + review-fix commit.
10. Runtime 4/6/8 + controls W1 and unbounded-W4, balanced/interleaved (network drift),
    isolated-large control (W4 retained), mixed primary. Report per §21.
```
Runtime selector recap: DUO_FP_READ_WINDOW (per-file, keep 4), DUO_FP_READ_BUDGET (global).
Host launch: launchctl setenv both, then open /Applications/DuoInput.app.
Metrics: reconstruct per generation_id from ~/.local/share/DuoInput/logs/duo-input.log*
(rotates; filter by transfer_id), tools/fp_window_metrics.py + scratchpad analyze.py.
Windows source = C:\Users\Valentyn\Desktop\B-mixed (100 files, 42,158,741 B).
Mac=192.168.0.139, Windows=192.168.0.128. iperf3 on Mac available.
```

## AS-BUILT (phase 2, commit 138d9e95, branch feature/fileprovider-read-window-v2)
Implemented exactly per plan; NO Phase-1 contradiction found, so no redesign.
- `_read_budget_from_env()`/`GLOBAL_READ_BUDGET` + `DUO_FP_READ_BUDGET` (mirrors the
  window selector). DEFAULT = `MAX_ACTIVE_FETCHES*PER_FILE_READ_WINDOW` = 16 =
  today's unbounded-W4 ceiling => budget INERT by default, existing tests (incl.
  `test_four_streams_independently_bounded` = 16 outstanding) unchanged; >=16 == control.
- `Fetch.pulled`; `_wants_read`; `_schedule_reads` (round-robin `_rr` deque, one read
  per selected fetch, rotate(-1), reset-on-progress; byte gate parks in `_pull_queue`,
  inert at B<=8; `_scheduling` reentrancy guard). `_fill_window` -> ensure `_rr`
  membership + `_schedule_reads`; `_resume_windows` -> `_schedule_reads`; `_on_chunk`
  schedules after permit release (out-of-order path). `_rr` add on REQUESTING admission
  (`_open_fetch`/`_admit_from_queue`, deduped), remove in `_finish_fetch`. Defensive
  `fp_read_budget_violation` telemetry at emission. Permit == `by_read_id` entry (no 2nd
  counter). `_issue_read` is the SOLE emitter and is called ONLY from `_schedule_reads`.
- RED: 21-case suite `tests/transfer/test_fileprovider_global_budget.py` (17 failed / 3
  guard-passed pre-impl; selector ImportError + `assert 16<=4` bound failures = expected).
- GREEN offline: new suite 21/21; full transfer 806 passed, 10 skipped (Windows-only
  ctypes.WINFUNCTYPE collection excluded); files/2 wire 45 passed. Swift untouched.
- Invariants (measured): B4/B6/B8 MAX_GLOBAL = 4/6/8 (fully borrowable, never exceeded);
  ISOLATED_MAX_PER_FETCH = 4; SMALL = 1; ZERO_BYTE = 0; byte-exact; permit leaks = 0;
  budget/window bound violations = 0; four large fetches all progress under B=4.
- REVIEW_FIX_COMMIT = NONE (dedicated review of bypass/leak/double-release/RR-dup/stale-RR/
  starvation/reentrancy/cancel-disconnect-retry races/plan-offset/reorder-bound found no
  proven defect; races covered by existing cancel/disconnect/retry/error suites + new
  reentrancy test). RUNTIME (§24-31) NOT run: needs the two-machine stand; runbook below.
