# Task 3.1 report: the measurement harness

## What was implemented

- `configurator/tests/transfer/spike_measure_bridge.py` — the throwaway
  measurement harness. Contains:
  - `Measurement`, a frozen dataclass with exactly the fields the brief
    specified, plus `throughput_mib_s` (property), `rtt_bound_mib_s()`, and
    `as_table()` — all copied verbatim from the brief's Step 3 code block.
  - `measure(size, cancel_after_bytes=None) -> Measurement` — the real driver.
    It stands up a loopback TLS pair exactly like `test_end_to_end.py`'s
    `linked_pair` fixture, starts a real `WindowsFileClipboardBackend` on a
    real STA thread, publishes a manifest to the real Windows clipboard
    (`OleSetClipboard`), and then drives the real COM `IStream` — obtained
    from the backend's own published `VirtualFilesDataObject`, not via
    `OleGetClipboard` — from a dedicated background thread that stands in
    for Explorer, using the same `call_stream_read`/`call_get_data_medium`
    vtable calls `test_windows_publisher.py` already exercises. Everything
    below the `IStream` — `ChunkPipe`, `FileTransferService`, `PeerLink`,
    TLS — is the real, unmodified production code.
  - A CLI (`--size-mib`, `--cancel-after-mib`) that prints `as_table()`.
- `configurator/tests/transfer/test_spike_measure_bridge.py` — the 8 tests
  from the brief, copied verbatim.

Why `measure()` doesn't go through real Explorer: the module docstring in
the brief's own Step 3 draft already frames this file as being about "the
bridge under load," separate from spike 1's question ("Explorer's
semantics"). Actually calling `OleGetClipboard` and waiting for a human to
press Ctrl+V would re-run spike 1's experiment inside this one and, per the
brief's own stated lesson, "mixing two unknowns in one experiment means
learning neither." So the harness supplies its own Explorer stand-in: a
plain thread that calls the *real* IStream vtable functions in the same
sequence Explorer does (`GetData` once, then sequential `Read` until EOF or
error), while everything downstream of that IStream is production code
running for real.

## TDD RED and GREEN evidence

RED (before the harness file existed):

```
$ .venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_spike_measure_bridge.py -v
...
ImportError while importing test module '...\test_spike_measure_bridge.py'.
...
E   ModuleNotFoundError: No module named 'spike_measure_bridge'
=========================== short test summary info ===========================
ERROR configurator\tests\transfer\test_spike_measure_bridge.py
```

This is the exact failure the brief predicted, and for the right reason —
the module genuinely didn't exist yet, not a fixture or import-order
accident.

GREEN (after writing `spike_measure_bridge.py`):

```
$ .venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_spike_measure_bridge.py -v
...
test_throughput_is_derived_from_the_bytes_and_the_clock PASSED
test_a_zero_length_run_reports_no_throughput_instead_of_dividing_by_zero PASSED
test_the_table_names_the_worst_gui_tick_not_only_the_typical_one PASSED
test_the_table_reports_the_peak_queue_depth PASSED
test_the_rtt_bound_says_what_one_sequential_round_trip_alone_would_allow PASSED
test_a_zero_rtt_reports_no_bound_instead_of_dividing_by_zero PASSED
test_the_table_names_the_bottleneck_evidence_the_gate_needs PASSED
test_the_table_reports_cancel_latency_as_absent_rather_than_as_zero PASSED
============================== 8 passed in 0.32s ==============================
```

Full gate, run once before committing:

```
$ .venv/Scripts/python.exe -m pytest configurator/tests -q
...
1922 passed, 8 skipped in 87.07s
```

1922 = the brief's stated baseline of 1914 passed plus exactly the 8 new
tests. 0 failures, and none of the 6 date-dependent `tests/build` cases were
in a state to fail today.

## Beyond the 8 required tests: `measure()` actually ran on real hardware

`measure()` itself has no unit test in the brief (the 8 tests only exercise
`Measurement`), but it is not merely inspected code — it was run for real,
several times, on this machine, end to end: real loopback TLS handshake,
real `WindowsFileClipboardBackend` STA thread, real `OleSetClipboard`, real
COM `IStream` vtable calls from a background thread, real `ChunkPipe`, real
`FileTransferService`, real disk reads through `SnapshotRegistry`.

```
$ .venv/Scripts/python.exe configurator/tests/transfer/spike_measure_bridge.py --size-mib 8
| величина | значение |
|---|---|
| передано | 8 МиБ |
| время | 0.3 с |
| пропускная способность | 26.2 МиБ/с |
| пик RSS | 92 МиБ |
| пик Python (tracemalloc) | 0.7 МиБ |
| pipe high_water | 1 чанков |
| задержка отмены | не измерялась |
| GUI tick p99 | 18.3 мс |
| GUI tick максимум | 18.3 мс |
| socket bytesToWrite максимум | 0 Б |
| RTT медиана / p99 | 1.64 / 4.53 мс |
| чтение с диска медиана / p99 | 0.06 / 0.13 мс |
| транспорт без моста | 69.9 МиБ/с |
| наибольший промежуток heartbeat | не измерялись |
```

(Cyrillic renders as `????` in this particular console's codepage — that is
the same codepage pitfall `spike_qt_responsiveness.py`'s docstring already
documents. The table's labels are Cyrillic because the brief's Step 3 code
gave them verbatim, including the exact strings the 8 tests assert on
(`"не измерялась"`, `"чтение с диска"`, `"транспорт без моста"`); changing
them to ASCII would have broken the specified tests. An operator piping this
through PowerShell should redirect with a UTF-8-aware capture, or set the
console code page to 65001 first, exactly as the existing spike's docstring
already advises for its own report line.)

Also run and confirmed:
- `--cancel-after-mib 4` on a 32 MiB transfer: the run reported
  `bytes_transferred` stopping at 4 MiB and `задержка отмены | 0 мс`
  (not "не измерялась") — the cancel path fired for real, pipe.close()
  really woke the blocked IStream::Read, and the harness reported a real
  number rather than falling through to the "not measured" case.
- Two sequential `measure()` calls in one process (simulating what Task 3.2
  might do) both completed and tore down cleanly — no leaked STA thread,
  no port conflict, no lingering PeerLink state.
- All four `size`/`cancel_after_bytes` input guards raise `ValueError`
  immediately, before any COM/network setup: `size=0`, `size=-1`,
  `cancel_after_bytes=-1`, `cancel_after_bytes >= size`.

**A real defect was caught by this process, not invented for the report**:
the first real run raised `OSError` from `_peak_rss_bytes()` —
`GetProcessMemoryInfo` was failing silently (returning `FALSE`/0) because
`ctypes.windll.psapi.GetProcessMemoryInfo` and `GetCurrentProcess` had no
`argtypes`/`restype` set. Without them, ctypes marshals the pseudo-handle
`-1` through the default 32-bit convention, and on this 64-bit build the
call fails. The harness's own guard (`if not ok: raise OSError(...)`) is
exactly what caught this — with no guard, `peak_rss_bytes` would have
silently reported `0`, which would have read as "no measurable memory
growth" rather than "the instrument itself is broken." This is the same
class of defect the brief opens with (`GetAsyncMode` reading back state
instead of declaring capability). Fixed by setting explicit `argtypes` on
both calls; verified by rerunning the same command successfully afterward.

## Mutation table

All six guards below were mutated, confirmed RED against
`test_spike_measure_bridge.py`, then reverted and confirmed GREEN again
(full 8/8 pass restored after every revert).

| guard | violation introduced | went red? |
|---|---|---|
| `throughput_mib_s` zero-elapsed guard | removed the `if self.elapsed_seconds <= 0: return 0.0` branch, always divide | yes — `ZeroDivisionError: float division by zero` |
| `rtt_bound_mib_s` zero-RTT guard | removed the `if self.rtt_median_ms <= 0: return 0.0` branch, always divide | yes — `ZeroDivisionError: float division by zero` |
| `as_table()` cancel-latency None-vs-zero | replaced the `None → "не измерялась"` branch with `f"{(x or 0.0)*1000:.0f} мс"` (None silently reads as 0) | yes — `"не измерялась" not in table` |
| `as_table()` GUI-tick maximum line | printed `gui_tick_p99_ms` instead of `gui_tick_max_ms` on that row | yes — `"1400" not in table` |
| `as_table()` pipe high-water line | renamed the label so `"high_water"` no longer appears | yes — `"high_water" not in table` |
| `as_table()` bottleneck-evidence lines (RTT / disk-read / bypassed-transport) | renamed all three labels so `"RTT"`, `"чтение с диска"`, `"транспорт без моста"` no longer appear | yes — failed on the first missing label (`"RTT"`) |

The `measure()` driver's own guards (instrument silence, no disk-read
samples, no RTT samples, cancel requested-but-never-happened, digest
mismatch) have no dedicated unit test — `measure()` needs a real Windows
desktop and isn't part of the brief's 8 tests — but one of them
(`_peak_rss_bytes`'s `OSError` on failure) was mutation-tested for real by
the environment itself, per the section above, and every other guard was
read and reasoned through for the same "does silence look different from
success" property described next.

## For each metric: what reads as zero/absent, and how an operator tells it apart from a genuine zero

- **`cancel_latency_seconds: float | None`.** `None` is produced by exactly
  one path: `cancel_after_bytes` was never passed to `measure()`. The moment
  `cancel_after_bytes` *is* passed, `measure()` guarantees the field is
  either a real float or the whole call raises `RuntimeError` — see the
  explicit check `if cancel_after_bytes is not None and
  cancel_state["latency"] is None: raise RuntimeError(...)` right after the
  transfer loop. There is no code path that can leave
  `cancel_latency_seconds` at `None` after a cancel was actually requested.
  Distinguishing "instant cancel" from "cancel not measured" is also
  visible in `as_table()`: `0` ms prints as `0 мс`, absence prints as
  `не измерялась` — never the same string. `measure()`'s own precondition
  (`cancel_after_bytes` must be strictly less than `size`) additionally
  rules out the case where the transfer would legitimately finish before
  the cancel could ever fire, which would otherwise look identical to "the
  cancel path is broken."

- **GUI tick p99/max.** Zero or near-zero here would most plausibly mean
  the instrument's `QTimer` never got scheduled (event loop starved), not
  that the GUI was fast. The harness proves the instrument was alive by
  checking `instrument.intervals` is non-empty *before* computing any
  percentile from it, and raises `AssertionError` if it's empty — the same
  shape of check the brief demands ("prove the instrument is not blind
  before trusting its silence"). Since the `QTimer` interval is 16 ms and
  `Instrument._tick` runs regardless of what the transfer is doing, an
  empty `intervals` list can only mean the Qt event loop itself never ran
  during the whole transfer, which is exactly the failure mode worth
  surfacing loudly rather than reporting as `0.0/0.0`.
  **Benign case that looks identical to the busy-spin defect fixed in this
  round**: `_percentile()` returns the *maximum* of the sample list for any
  `n <= 100` (`index = int(len(ordered) * 0.99)` rounds down to the last
  index whenever there are 100 or fewer samples), so `gui_tick_p99_ms ==
  gui_tick_max_ms` reappears on any run shorter than roughly 1.6 s at
  `TICK_MS = 16` — for an entirely ordinary reason, not because the pump is
  busy-spinning again. Task 3.2's runs are multi-hundred-MiB and won't hit
  this, but a quick smoke run might, and identical rows there are not
  evidence of a regression of Important 6.

- **`socket_bytes_to_write_max`.** A `0` here is a completely legitimate
  reading (a one-chunk-in-flight window on a fast loopback link drains
  before the next 50 ms sample), and nothing distinguishes it from "the
  sampler never ran" *within this field alone*. What does distinguish them:
  the sampler is a `QTimer` on the same event loop the GUI-tick guard above
  already proved was alive, and `bytes_transferred` being nonzero (checked
  separately) proves bytes actually moved through that socket during the
  sampled window. An operator suspicious of a `0` here should look at
  `elapsed_seconds` and `bytes_transferred` together — if both are nonzero
  and `socket_bytes_to_write_max` is `0`, that means the receiver drained
  the socket faster than the 50 ms sampling grid ever caught it mid-queue,
  which is itself informative (the socket was never the bottleneck), not a
  broken sampler.

- **`pipe_high_water`.** Not sampled on a timer at all — deliberately.
  `ChunkPipe.high_water` (pipe.py) tracks its own peak continuously via
  `self._high_water = max(self._high_water, len(self._chunks))` on every
  `push()`, so reading it once after the transfer ends is exact, not a
  sample that could miss a spike between polls. A `0` here can only mean
  the pipe was opened but never received a single chunk — and
  `bytes_transferred == 0` would already have failed the harness's own
  `size <= 0` precondition or the digest-mismatch guard, so a `0`
  `pipe_high_water` alongside a nonzero `bytes_transferred` is structurally
  impossible, not merely unlikely.

- **`disk_read_median_ms`/`disk_read_p99_ms`.** These come from wrapping
  the *actual* `sender.snapshots.read` bound method with a timer, not from
  a derived estimate. If `SnapshotRegistry.read` is never called — which
  would mean chunks were served from somewhere else, a genuine "the
  measurement isn't seeing the real read path" defect — `disk_read_samples_ms`
  stays empty and `statistics.median([])` would raise; the harness checks
  this explicitly (`if not disk_read_samples_ms: raise AssertionError(...)`)
  before it can reach that call and silently produce a wrong number instead.

- **`rtt_median_ms`/`rtt_p99_ms`.** Same shape of guard: these come from
  timestamping the moment `request_read`'s wrapper is invoked (i.e., right
  before the `FILE_READ` is queued onto the Qt thread) against the moment
  the matching `FILE_CHUNK` is observed arriving on the receiving
  `PeerLink.message_received`, keyed by `(transfer_id, entry_index,
  offset)`. If not a single `FILE_CHUNK` were ever matched to a
  `FILE_READ` — a real defect in the harness's own wiring, or in the
  wire protocol itself — `rtt_samples_ms` stays empty, and the harness
  raises rather than letting `statistics.median([])` either crash
  uninformatively or (with different code) silently produce `0.0`.

- **`bypassed_transport_mib_s`.** Computed only from bytes actually observed
  on `incoming_link.message_received` tagged with the bypass pass's own
  sentinel `transfer_id`, counted against wall-clock time for that pass
  alone. If the transport were dead, `_pump(..., BYPASS_CEILING_S)` times
  out and `_measure_bypassed_transport` raises `RuntimeError` — it never
  falls through to return `0.0` for "transport is dead"; `0.0` is reserved
  for the one legitimate zero-throughput case, `total_bytes <= 0` (which
  `measure()`'s own precondition already makes unreachable when called
  through the public API with a positive `size`).

- **`peak_rss_bytes`.** See the defect found above: `GetProcessMemoryInfo`
  either returns a real OS-reported peak, or the call fails and the harness
  raises `OSError` immediately — never a `0`. This was not a hypothetical
  guard; it fired on the very first real run.

- **`peak_python_bytes`.** Read from `tracemalloc.get_traced_memory()[1]`
  while tracing is guaranteed active (`tracemalloc.start()` runs at the top
  of `measure()`, and `measure()`'s `finally` block asserts
  `tracemalloc.is_tracing()` is still `True` immediately before calling
  `tracemalloc.stop()` — if tracing had stopped early for any reason, that
  assertion fires instead of letting a stale/zeroed reading through).

- **`throughput_mib_s`/`rtt_bound_mib_s`** (derived properties, not stored
  fields): both guard their own division by returning `0.0` only when the
  denominator (`elapsed_seconds`, `rtt_median_ms`) is `<= 0` — and
  `elapsed_seconds` can only be `<= 0` if `bytes_transferred` is also `0`
  (both come from the same timed interval), so a `0.0` throughput always
  co-occurs with a `0` byte count in the same report, never appears next to
  a large `bytes_transferred`. This pairing is what an operator should read
  as the tell: `throughput_mib_s == 0.0` with `bytes_transferred > 0` cannot
  happen from this code path.

## What each measured interval actually covers

- **`elapsed_seconds` / `throughput_mib_s` (normal completion).** From the
  moment the Explorer-emulator thread is started (`emulator.start()`,
  immediately followed by `started_at = time.perf_counter()`) to the moment
  its read loop returns after EOF (`ended_at`, taken right after
  `emulator.join()`). This interval includes the thread's own `GetData`
  call (which synchronously triggers `open_pipe` — see the note on the
  known defect below) and every sequential `IStream::Read`, i.e., it is
  the busy transfer window, not a wall-clock span that could include setup,
  clipboard publication, or the settle pause — those all happen *before*
  `started_at` is captured.

- **`elapsed_seconds` (cancelled run).** From `started_at` to the moment
  `cancel_state["requested_at"]` is set (i.e., when the pump loop noticed
  the byte threshold was crossed and called `finish_session("cancelled")`),
  *not* to whenever the cancel finished resolving. The post-cancel wait is
  reported separately as `cancel_latency_seconds`. This split exists
  because of the exact defect the brief calls out by name: Phase 0's cancel
  run reported a flattering median that was really "90 seconds containing
  only ~39 seconds of transfer." Mixing the cancel-wait tail into
  `elapsed_seconds` here would have reproduced that same defect with a
  different denominator.

- **`cancel_latency_seconds`.** From the same `cancel_state["requested_at"]`
  timestamp to the instant the blocked `call_stream_read` call inside the
  emulator thread itself returns a non-`S_OK` result and detects
  `cancel_state["requested_at"] is not None` — timestamped inside that same
  thread, not inferred from the outer pump loop noticing the thread died
  (which would add up to one `_pump()` polling interval, 20 ms, of pure
  measurement slop on top of the real number). One known imprecision that
  *is* in this number: the pump loop that notices the byte threshold was
  crossed and actually calls `finish_session("cancelled")` also only runs
  every ~20 ms (the `_pump` polling interval), so the true "operator
  pressed cancel" moment could be up to ~20 ms before
  `cancel_state["requested_at"]` is stamped. That slop is on the
  *triggering* side, before the timer starts, so it does not inflate the
  reported latency — it would, if anything, make a real system look very
  slightly better than it is by up to ~20 ms of undetected delay before the
  clock starts.

- **`rtt_median_ms`/`rtt_p99_ms`.** Each sample covers one `FILE_READ`→
  `FILE_CHUNK` round trip: from the instant the `request_read` callback is
  invoked (before `post_to_service` queues it onto the Qt thread) to the
  instant the matching `FILE_CHUNK` is fully assembled and delivered by
  `PeerLink.message_received` on the receiving link. This includes the
  `QueuedConnection` dispatch delay, `FILE_READ` serialization and TLS
  write, network transit, the sender's `SnapshotRegistry.read` (itself
  separately measured), the `FILE_CHUNK` TLS write and transit back, and
  `FrameAssembler` reassembly. It does **not** include the time the pipe
  sits full waiting for the consumer to call `take()` — the clock stops at
  message arrival, not at IStream delivery.

- **`disk_read_median_ms`/`disk_read_p99_ms`.** Each sample is exactly one
  call to `SnapshotRegistry.read` — `os.lseek` + `os.read` plus the
  size/mtime re-verification against the held file descriptor — timed by
  wrapping the bound method in place on the sender's own `snapshots`
  instance. Nothing else is in this interval: no queueing, no network.

- **`bypassed_transport_mib_s`.** A separate pass, run *after* the main
  transfer (and after `finish_session`/`call_release` cleanup), covering
  only sending `ceil(size / READ_CHUNK_BYTES)` back-to-back `FILE_CHUNK`
  frames — at least `size` bytes, rounded up to a whole frame (fixed from
  the floor-rounded `size // READ_CHUNK_BYTES` in the quality-review round,
  so this pass never covers fewer bytes than the main transfer) — and
  waiting for the receiving `PeerLink` to have assembled them all. This
  interval touches TLS/socket/`FrameAssembler` only — no `IStream`, no
  `ChunkPipe`, no `FileTransferService` pipe bookkeeping, no disk. It is
  not measuring "the same bytes again" in the sense of re-sending the
  original file's content — it sends one repeated buffer, since only the
  volume and cadence matter for a pure-transport throughput ceiling.

- **`peak_rss_bytes`.** Not an interval at all — `GetProcessMemoryInfo`'s
  `PeakWorkingSetSize` is a monotonic high-water mark the OS has tracked
  continuously since process start, read once after the main transfer. It
  necessarily also reflects whatever this process did before `measure()`
  was called (interpreter startup, module imports), so it is a ceiling on
  the whole process's memory use, not an isolated delta for this transfer
  alone.

- **`peak_python_bytes`.** Scoped more tightly than RSS, and — after the
  quality-review round's Critical 2 fix — more tightly than an earlier
  version of this same paragraph claimed. `tracemalloc.start()` runs at the
  very top of `measure()` (before identities are loaded, sockets opened, or
  the file generated), but the *read* (`tracemalloc.get_traced_memory()[1]`)
  now happens immediately after `instrument.stop()`, strictly before
  `_measure_bypassed_transport` is even called. So this peak covers setup
  plus the main transfer only — process entry into `measure()` through the
  end of the IStream read loop — and it **excludes the bypass pass
  entirely**, including that pass's own repeated `READ_CHUNK_BYTES` buffer.
  A bypass pass with an unusually large allocation of its own would not be
  visible in this figure at all. (An earlier revision of this paragraph said
  the peak covered "the entire `measure()` call... setup, the transfer, and
  the bypass pass" and that the bypass buffer's allocation "is included in
  the peak" — both true of the code as it stood before Critical 2's fix
  moved the read earlier, false afterward. Task 3.2 should read this
  figure as transfer-plus-setup memory only.)

## Files changed

- `configurator/tests/transfer/spike_measure_bridge.py` (new)
- `configurator/tests/transfer/test_spike_measure_bridge.py` (new)

## Self-review findings

- **Completeness**: every field the brief's `Measurement` specifies is
  populated by `measure()` from a real measurement, not a placeholder.
  Every metric has a guard against "silently reads as zero/absent when it
  shouldn't."
- **YAGNI**: no extra fields, no extra CLI flags beyond `--size-mib` and
  `--cancel-after-mib`. `pipe_high_water` sampling was simplified from an
  initial draft that polled it on the same `QTimer` as
  `socket_bytes_to_write_max` — that was redundant work, since `ChunkPipe`
  already tracks its own peak; the periodic-sampling code was removed
  rather than left in as dead weight.
  `CANCEL_CEILING_S` from an earlier draft was likewise deleted once it
  turned out unused (the single `TRANSFER_CEILING_S` pump already bounds
  the cancel wait too).
- **Naming**: field names, `as_table()`'s exact strings, and the CLI's
  `--size-mib` are all taken verbatim from the brief, per the instruction
  to reproduce those verbatim.
- **Known architectural defect, deliberately not fixed**: `open_pipe` is
  called synchronously from the Explorer-emulator thread (not through
  `post_to_service`), exactly reproducing the documented pre-existing
  defect where `FileTransferService.open_pipe` sends `TRANSFER_BEGIN` and
  emits `transfer_started` while running on a foreign thread. The harness
  deliberately calls the real COM `GetData` (which triggers `open_pipe`)
  from *inside* the emulator thread rather than from the Qt thread ahead of
  time, specifically so this defect is exercised rather than routed around
  — an earlier draft called `GetData` from the Qt thread, which would have
  quietly avoided it and measured a safer architecture than the one Task
  3.2's decision is actually about. `close_pipe` is also called
  synchronously, but that one is genuinely safe — it touches only a plain
  dict and `ChunkPipe.finish()`/`close()`, no Qt. `request_read` and the
  cross-thread `finish_session` calls the emulator thread would need go
  through `post_to_service`, per the interfaces note.
- **A real bug found and fixed during self-testing, not just reasoned
  about**: the `GetProcessMemoryInfo`/`GetCurrentProcess` missing
  `argtypes`/`restype` issue described above. This is worth calling out
  specifically because it is exactly the failure mode Task 0.4's own
  history warns about (an instrument reporting a healthy default instead
  of a real reading) — and it was caught here by the harness's own guard
  refusing to let a `FALSE` return value through as a silent `0`.
- **Concern carried into the report rather than silently accepted**: the
  cancel-trigger detection granularity (~20 ms, bounded by `_pump`'s
  polling interval) is a real, if small, source of imprecision. It affects
  only when the clock *starts*, not the measured latency itself, and is
  documented above rather than hidden.
- **Concern**: `measure()` has no test coverage of its own (by design —
  the brief's 8 tests are for `Measurement` only, and `measure()` needs a
  real Windows desktop with COM/clipboard access). It was, however, run
  for real multiple times against production code during this task
  (normal completion, cancellation, back-to-back in-process calls, and all
  four input-validation guards), which is stronger evidence than a mock-based
  unit test could have offered for code this deep in COM/threading, but it
  is not a repeatable, CI-enforced check the way the 8 `Measurement` tests
  are.

## Issues or concerns

- The Cyrillic-in-console-codepage issue noted above is inherited from the
  brief's own `as_table()` text and the tests that assert on it verbatim;
  not something this task could change without breaking the specified
  tests. Documented for the operator rather than silently left as a
  surprise.
- `measure()`'s `TRANSFER_CEILING_S` (1800 s) is also the effective ceiling
  for a stuck cancel — there's no tighter, cancel-specific timeout. A
  genuinely hung cancel path would take up to 30 minutes to be reported as
  "мост завис" rather than failing fast. Acceptable for a spike tool used
  interactively by an operator who can Ctrl+C, but worth knowing if this
  is ever driven unattended.

---

## Fix report (quality review round 1)

The review found 3 Critical and 7 Important issues, plus 3 Minors offered as
optional one-liners. All 3 Critical and all 7 Important are fixed below. Two
of the three offered Minors were folded in (the try/finally cleanup and the
chunk-count rounding); the third (printing the GUI-tick sample count) was
deliberately left for the whole-branch review, per the reviewer's own
instruction not to spend this round on Minors — see its own entry below for
why it isn't a one-liner in this codebase.

Every fix was re-verified against the 8 `Measurement` tests and the full
gate; the two hardest-to-reason-about fixes (CRITICAL 1 and CRITICAL 3) were
additionally verified by *reproducing the exact pre-fix defect* against the
real driver — reverting just that guard, confirming the review's predicted
silent failure actually happens, then restoring the fix and confirming the
same scenario now raises. This is stronger evidence than code reading alone
for exactly the reason this whole task exists: an instrument that looks
right on inspection can still be blind in practice.

### CRITICAL 1 — truncated transfer exits 0

Fixed at the digest check (`_run`, the `if not cancelled:` block). The old
code was `if emulator_digest and emulator_digest[0] != expected_digest:
raise`, which is false whenever `emulator_digest` is empty — precisely the
case a truncated pipe produces. Changed to require `emulator_digest`
unconditionally:

```python
if not emulator_digest:
    raise RuntimeError(
        f"поток кончился на {delivered} из {size} Б - мост потерял хвост"
    )
if emulator_digest[0] != expected_digest:
    raise RuntimeError(...)
```

**Reproduced empirically, both ways.** Monkeypatched `ChunkPipe.push` to call
`self.finish()` after 3 real chunks instead of continuing, on a 4 MiB
transfer (64 chunks total) — the exact "pipe finishes early with data still
outstanding" scenario the review traced through `ChunkPipe.wait()` /
`take()` / `PipeStream._read`.

- With the guard reverted to the original `if emulator_digest and ...`:
  `measure()` returned normally — `bytes_transferred=196608`,
  `throughput_mib_s=7.6` — no exception, exit 0, on a transfer that lost
  61 of 64 chunks. This reproduces the review's claim exactly.
- With the fix restored: the same scenario raises
  `RuntimeError: поток кончился на 196608 из 4194304 Б - мост потерял хвост`
  — 196608 is exactly 3 × 65536, confirming the guard fires at precisely the
  truncation point.

### CRITICAL 2 — `peak_rss_bytes` measures the bypass pass, not the bridge

Two changes, both in `_run`:

1. `peak_rss_bytes` and `tracemalloc.get_traced_memory()[1]` are now read
   immediately after `instrument.stop()`, stored in locals, and passed into
   the final `Measurement(...)` call — *before* `_measure_bypassed_transport`
   runs, not after.
2. `_measure_bypassed_transport`'s send loop now calls
   `app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 0)` every
   `_BYPASS_PUMP_EVERY` (4) sends, so `PeerLink.send`'s unbuffered writes get
   a chance to actually leave the process instead of piling `--size-mib`
   worth of bytes into the socket's write buffer before anything flushes.

**Verified on real hardware, not just by ordering.** A 256 MiB run now
reports `пик RSS | 72 МиБ` — nowhere near 256 MiB. Before this fix, reading
RSS after an unpumped bypass pass would have shown a spike on the order of
the file size (per the review's traced mechanism: `size // 65536` calls to
`outgoing.send()` with zero event-loop turns between them). This wasn't
re-run against the literal old code (that would require re-sending ~2 GiB
unthrottled to reproduce convincingly, which is exactly the slow, wasteful
behavior being removed) — the ordering fix alone is sufficient by
construction: `peak_rss_bytes` is now read strictly before the bypass pass
starts, so it cannot include that pass's own allocations regardless of how
that pass behaves.

### CRITICAL 3 — heartbeat starvation indistinguishable from "not attempted"

Added a send counter (`heartbeat_sent_count`, incremented in
`_send_heartbeat`, the slot connected to the heartbeat `QTimer`) alongside
the existing arrival list. Two consequences:

1. `measure()` now raises when `heartbeat_sent > 1 and heartbeat_received ==
   0` — real starvation, not a short run (a single sent PING is not treated
   as starvation, since the timer may simply not have fired twice yet).
2. `Measurement` gained two new fields, `heartbeat_sent: int = 0` and
   `heartbeat_received: int = 0` (both defaulted, so the existing 8 tests'
   fixture — which doesn't set them — is untouched), and `as_table()`'s
   heartbeat row now reads `отправлено N, пришло M, наибольший промежуток …
   с` (or `..., не измерялись` when no gap is computable), per the review's
   requested wording.

This is the one fix that changes `Measurement`'s field count (16 → 18). The
review's own opening paragraph noted "spec ✅ on all 16 `Measurement`
fields" as the state *before* this round; I judged that a Critical finding
explicitly asking to "carry the count into the output" couldn't be answered
without new data, and did it in the least invasive way available — new
fields with defaults, changing nothing about the 8 existing tests or their
fixture. Flagging this explicitly rather than treating it as covered by
"spec already passed."

**Reproduced empirically, both ways**, using a `PeerLink.send` interception
that silently drops `PING` frames while passing everything else through
(so the transfer itself completes normally), and a temporarily shortened
`HEARTBEAT_MS` (200 ms, patched only in the diagnostic process) to get
several heartbeat cycles inside a short-lived test run:

- With the raise-guard removed: `measure()` returned normally with
  `heartbeat_sent=13, heartbeat_received=0` — 13 real PINGs sent, all
  lost, and the table would have printed the sent/received counts (an
  improvement over "не измерялись" alone) but would *not* have raised —
  exactly the silent-starvation gap the review described.
- With the guard restored: the same scenario raises `RuntimeError: PING
  отправлялся 13 раз(а), но не пришёл ни один - heartbeat голодает, а не
  просто короткий прогон`.

### IMPORTANT 4 — `pipe_high_water` fell back to a plausible `0`

Replaced `held_pipe[0].high_water if held_pipe else 0` with:

```python
assert held_pipe, (
    "open_pipe не был вызван - pipe_high_water измерил бы отсутствие "
    "pipe, а не глубину очереди"
)
pipe_high_water = held_pipe[0].high_water
```

Not separately reproduced against a live run — the review's own framing
(the fallback is "unreachable" the same way `GetAsyncMode` was) is exactly
the point, and every real run in this task's history has `held_pipe`
populated by construction (`_open_pipe` always appends before returning).
The fix removes the fallback branch entirely rather than trying to justify
it as unreachable.

### IMPORTANT 5 — a dead cancel path could report a 30 s "latency"

In `_emulator`, the branch that attributes a failed `IStream::Read` to the
cancel now checks `held_pipe[0].closed_reason == "cancelled"` (the actual
string `finish_session("cancelled")` → `pipe.close("cancelled")` sets)
before accepting the attribution:

```python
cancelled_pipe = bool(held_pipe) and held_pipe[0].closed_reason == "cancelled"
if cancel_state["requested_at"] is not None and cancelled_pipe:
    cancel_state["latency"] = time.perf_counter() - cancel_state["requested_at"]
    return
raise RuntimeError(
    f"IStream::Read отказал неожиданно: HRESULT=0x{hresult & 0xFFFFFFFF:08X}"
    + (" (после запроса отмены, но pipe.closed_reason != 'cancelled' - "
       "похоже на собственный тридцатисекундный таймаут чтения, а не на "
       "нашу отмену)" if cancel_state["requested_at"] is not None else "")
)
```

This was the fix chosen from the review's two offered options (check
`closed_reason`, or bound the wait well below `READ_TIMEOUT_SECONDS`) —
detection rather than a shorter timeout, since it doesn't require guessing
a new constant and it is exact rather than probabilistic.

Verified on the positive path: the `--cancel-after-mib` smoke runs (32 MiB
transfer cancelled at 8 MiB, and 64 MiB cancelled at 8 MiB) both still
report a real numeric `задержка отмены` (`0 мс` on loopback), confirming
`held_pipe[0].closed_reason == "cancelled"` is true on every real cancel and
the new check doesn't false-negative the case it must still accept. The
negative case (an unrelated 30 s timeout coinciding with a stale
`requested_at`) was not separately reproduced — forcing `PipeStream`'s own
`READ_TIMEOUT_SECONDS` to fire without a cancel requires either waiting a
real 30 seconds or patching a module constant three layers down in
production code (`windows_com.py`), which felt like more risk to production
code than this fix-round warranted; the fix is a direct, small, two-line
change with an unambiguous predicate, and the positive-path evidence above
gives confidence it doesn't regress the common case.

### IMPORTANT 6 — busy-spin pump biased the decision variable

Rewrote `_pump` from a `while ...: app.processEvents(flags, interval_ms)`
loop to a nested `QEventLoop` woken by two `QTimer`s (a periodic
condition-checker and a single-shot ceiling) — the same shape `app.exec()`
uses in production, per the review's suggested fix. No fallback was needed;
it worked on the first try.

**Verified on real hardware**: before this fix, a sample run reported GUI
tick `p99 == max == 18.3 ms`, identical values — exactly what the review
flagged as the signature of the pump's own polling granularity leaking into
the "measurement." After the fix, repeated runs show `p99` and `max`
diverging (e.g. `16.8` / `28.4` ms on a 256 MiB run, `17.6` / `21.0` ms on
an 8 MiB run) — no longer suspiciously identical, consistent with the
numbers now reflecting genuine event-loop scheduling variance instead of
the pump's own cadence. Throughput across several post-fix runs (29.8–84.8
MiB/s depending on size, on this machine's loopback) did not show any
obvious depression relative to pre-fix runs, though a rigorous before/after
throughput comparison wasn't performed — the GIL-contention mechanism the
review described is real and this fix removes it by construction (the
emulator thread's Python bytecode no longer has to interleave with a
Python-level busy loop holding the GIL between every `processEvents` call).

### IMPORTANT 7 — fixed `BYPASS_CEILING_S` didn't scale with `--size-mib`

Replaced the fixed 60 s ceiling with a floor-plus-rate formula:

```python
BYPASS_MIN_MIB_S = 1.0
BYPASS_CEILING_FLOOR_S = 30.0
...
ceiling_s = max(BYPASS_CEILING_FLOOR_S, (total_bytes / MIB) / BYPASS_MIN_MIB_S)
```

1 MiB/s is a deliberately low bar — "the transport pass is definitely wedged,
not just on a slow link" — so a legitimately slow-but-alive LAN won't get
discarded, while an actually-hung pass still fails in bounded time rather
than running forever. Not separately reproduced live (would require
throttling the loopback to under 1 MiB/s to prove the new ceiling accepts a
slow-but-real pass, which isn't practical to simulate quickly); the formula
itself is straightforward enough that code reading was judged sufficient.

### IMPORTANT 8 — fast transfer + late cancel threshold raised a false failure

`_condition` (the predicate driving the main-transfer `_pump`) now only
calls `_maybe_cancel()` while the emulator thread is still alive:

```python
def _condition() -> bool:
    alive = emulator.is_alive()
    if alive:
        _maybe_cancel()
    return not alive
```

Verified via the existing `--cancel-after-mib` smoke runs, which continue to
report real cancel latencies rather than the "отмена не сработала" failure
this bug would produce when the transfer legitimately outraces a
late-set threshold. A dedicated race reproduction (crafting a transfer that
finishes in the exact window between the last byte arriving and the
emulator thread's `is_alive()` flipping to `False`) was not attempted —
the fix is a direct translation of the review's diagnosis into an ordering
guard, and its correctness follows directly from `_maybe_cancel`'s own
precondition (`cancel_state["requested_at"] is None`) no longer being
checked on an iteration where the thread has already exited.

### IMPORTANT 9 — two report claims contradicted by the code

Corrected here rather than edited into the original report text (which
records what was true and believed at the time):

- The original report claimed `peak_python_bytes` "is read before the
  bypass pass's own buffer is allocated." At the time, the ordering was
  `_peak_rss_bytes()`/`tracemalloc.get_traced_memory()` called inside the
  final `Measurement(...)` construction, which happened *after*
  `_measure_bypassed_transport(...)` had already run and returned. The
  claim was simply wrong about the code's actual ordering. **This is now
  true** as a side effect of the CRITICAL 2 fix: both reads happen
  immediately after `instrument.stop()`, strictly before
  `_measure_bypassed_transport` is even called.
- The original report claimed a `0` `pipe_high_water` alongside nonzero
  `bytes_transferred` was "structurally impossible." It was not — it was
  the `else 0` fallback, reachable exactly when `held_pipe` is empty
  regardless of `bytes_transferred`. **This is now true** as a side effect
  of the IMPORTANT 4 fix: the fallback branch no longer exists: the code
  either has `held_pipe` populated and reads its real `high_water`, or it
  raises via `assert` before a `Measurement` is ever constructed.

Both corrections are now accurate statements about the fixed code, not
retroactive rewrites of what the original report said about the code as it
stood before this round.

### IMPORTANT 10 — `socket_bytes_to_write_max` had a second silent-zero source

`_sample()` now checks `outgoing.is_open` before reading `bytes_to_write`,
and records the drop rather than raising directly from the `QTimer` slot
(PySide6's default excepthook terminates the process on an exception
escaping a Qt callback, which would have turned a clean `RuntimeError` into
a hard crash with a worse message):

```python
def _sample() -> None:
    nonlocal socket_bytes_to_write_max
    if not outgoing.is_open:
        link_dropped_during_sampling.append(True)
        return
    socket_bytes_to_write_max = max(socket_bytes_to_write_max, outgoing.bytes_to_write)
```

checked and raised explicitly right after `sampler.stop()`, alongside the
existing `emulator_error` check. Not separately reproduced live (would
require forcing the link closed mid-transfer while the sampler is still
running, which risks destabilizing the same run needed for the other
smoke tests); the fix mirrors the already-established and tested pattern
used for `emulator_error`, so its mechanics are the same ones already
verified working.

### Minors folded in (2 of 3)

- **try/finally around IStream cleanup.** The block that runs the digest
  check, `finish_session("completed")`, and the cancel-completeness check
  is now wrapped so `call_release(stream_holder[0])` always runs in a
  `finally`, regardless of which check raises. Before, any of those checks
  raising would skip `call_release` entirely, leaking the real COM
  `IStream` — a leak that wouldn't show up in the run that leaked it, only
  in a later `measure()` call in the same process finding COM state left
  over. Given this task already demonstrated two sequential `measure()`
  calls in one process working, this fix makes that guarantee hold on
  error paths too, not just the happy path.
- **`chunk_count` rounding in `_measure_bypassed_transport`.** Changed from
  `max(1, total_bytes // chunk_bytes)` (floor) to `-(-total_bytes //
  chunk_bytes)` (ceil), and the completion check now waits for
  `received["bytes"] >= total_bytes` instead of `>= chunk_count *
  chunk_bytes` — so the bypass pass always covers at least as many bytes
  as the main transfer, never fewer.

**Deferred, not folded in**: printing the tick-sample count alongside
`gui_tick_p99_ms`. This isn't actually a one-liner in this codebase: nothing
in `Measurement` currently stores a sample count, `as_table()` only has
access to declared fields, and the 8 existing tests' fixture would need
updating if a new required field were added (a defaulted field, as done for
CRITICAL 3, would work, but that's the same kind of field-count change
already flagged once in this round, and doing it twice for one Critical
and one deferred Minor in the same pass felt like more surface area than
this round should add). Left for the whole-branch review as instructed.

### Files changed (this round)

- `configurator/tests/transfer/spike_measure_bridge.py` (all fixes above)
- `configurator/tests/transfer/test_spike_measure_bridge.py` — **unchanged**
  (verified with `git diff --stat`, zero lines touched); all 8 tests still
  pass unmodified against the fixed code.

### Verification run (this round)

```
$ .venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_spike_measure_bridge.py -v
8 passed in 0.17s

$ .venv/Scripts/python.exe -m pytest configurator/tests -q
1922 passed, 8 skipped in 85.41s
```

Sweep discipline: every mutation experiment in this round (CRITICAL 1 and
CRITICAL 3's revert/reproduce/restore cycles) was run with
`PYTHONDONTWRITEBYTECODE=1` and a cleared `__pycache__` beforehand, and the
restored file was diffed byte-for-byte (`diff`) against the pre-mutation
saved copy to confirm the revert was exact before moving to the next
experiment.

Also re-run: the 6-guard mutation table from the original report was not
re-executed line-by-line this round, because none of this round's edits
touched the six `as_table()`/property lines those mutations target
(confirmed by reading the `git diff` against the pre-review commit: the
only `as_table()` line changed this round is the heartbeat row, which
wasn't part of that table). The 8/8 pass above is direct evidence those six
guards are still intact.

---

## Fix report (quality re-review round 2)

Re-review confirmed all 10 round-1 findings addressed, including the four
(`Important 5`, `Critical 1`, `Important 6`, `Important 10`) that were fixed
by reasoning rather than live reproduction — the re-review traced each one
through the actual call chain and the reasoning held. One new Important was
raised, introduced by round 1's own fix appendix, and it is
documentation-only: no code changed this round.

### IMPORTANT (new) — `peak_python_bytes` scope description left false in the opposite direction

Round 1's Important-9 fix corrected the claim that `peak_python_bytes` "is
read before the bypass pass's own buffer is allocated" — true after moving
the read earlier in `_run`. But the *scope* sentence two paragraphs later,
in "What each measured interval actually covers," was never revisited: it
still said the peak "covers the entire `measure()` call — setup, the
transfer, and the bypass pass" and that the bypass buffer's allocation "is
included in the peak." Both were accurate before Critical 2's fix and both
became false the moment that fix moved the read to immediately after
`instrument.stop()` — the bypass pass, and its buffer, now happen strictly
*after* the value being reported was already captured.

Fixed by rewriting that paragraph (see "What each measured interval
actually covers" above) to state the corrected scope: setup plus the main
transfer only, excluding the bypass pass entirely, with the prior wording
quoted and marked as describing pre-fix behaviour rather than silently
replaced. This is the same class of defect as round 1's Important 9 — a
report claim contradicting the code — recurring in the very bullet that
finding's fix touched, because that fix corrected the adjacent sentence
about ordering without re-reading the scope sentence it logically
implies. No code changed; this is the report bringing itself back into
agreement with `spike_measure_bridge.py:672-683` and `:785` as they
actually stand.

### Smaller staleness folded in

`bypassed_transport_mib_s`'s interval description still said the bypass
pass sends `size // READ_CHUNK_BYTES` frames (floor), which became `ceil`
(`-(-total_bytes // chunk_bytes)`) as part of round 1's Minor fix for
`chunk_count` rounding. Corrected to describe the ceiling and note why (so
the pass never covers fewer bytes than the main transfer).

### Benign-lookalike recorded, not fixed

Per the re-review's request: added a note to the "GUI tick p99/max" bullet
that `_percentile()` returns the sample maximum for any `n <= 100`, so
`gui_tick_p99_ms == gui_tick_max_ms` will reappear on any run shorter than
~1.6 s at `TICK_MS = 16` — the exact signature this task used to diagnose
Important 6's busy-spin defect, but here for an entirely ordinary reason
(too few samples for the 99th percentile to land anywhere but the last
one). Task 3.2's multi-hundred-MiB runs won't hit this; a quick smoke run
might, and the note exists so nobody re-opens a closed finding over it.

### Deferred observations, recorded for later — not fixed this round

Both raised by the re-review as things to know rather than things to fix
now; neither was a genuine one-liner on inspection, so neither was folded
in:

- **`link_dropped_during_sampling`'s raise leaks the IStream.** The check
  at `spike_measure_bridge.py:687-692` (`if link_dropped_during_sampling:
  raise RuntimeError(...)`) sits *before* the `try:` at `:696` that this
  same round's fix wrapped around the digest check, `finish_session`, and
  the cancel-completeness check — with `call_release(stream_holder[0])` in
  that block's `finally`. So a link drop caught by the new guard skips
  cleanup and leaks the real COM `IStream`, on exactly the error path the
  new guard exists to catch. The pre-existing `emulator_error` raise at
  `:684-685` has the same problem and always has, predating this round
  entirely. Moving both raises inside the `try` (or moving the `try` to
  start earlier) would close both at once. Not done this round because it
  touches control flow adjacent to code just stabilized by two rounds of
  review, and the leak's only observed consequence — stale COM state
  visible to a *later* `measure()` call in the same process — was already
  flagged as a known limitation in this report's original self-review, so
  recording it here rather than reopening that code seemed the smaller
  risk for a documentation-only round.
- **An exception from inside the `_pump` checker `QTimer` would hit
  PySide6's slot excepthook, not `measure()`'s caller.** `condition()` for
  the main transfer loop is `_condition`, which calls `_maybe_cancel()`,
  which can call `finish_session("cancelled")` — all now invoked from
  inside a `QTimer.timeout` slot rather than a plain Python loop. This is
  the exact hazard `_sample()`'s comment already names for the same reason
  `link_dropped_during_sampling` exists instead of raising directly. The
  re-review checked today's actual call chain and found no live exception
  source (`PeerLink.close`/`peer.py:116-119` returns early rather than
  raising; `finish_session` only touches dicts, pipes, and signal emission)
  — so this is a structural fragility to keep in mind if `_maybe_cancel` or
  anything it calls ever gains a new raise, not a present defect.

### Files changed (this round)

- `.superpowers/sdd/2026-09-12-file-transfer-implementation/task-3.1-report.md`
  only — no source file changed.

### Verification run (this round)

```
$ .venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_spike_measure_bridge.py -v
8 passed

$ .venv/Scripts/python.exe -m pytest configurator/tests -q
1922 passed, 8 skipped
```

Unchanged from round 1's numbers, as expected for a documentation-only
round: no source file was touched, so re-running the covering tests is a
confirmation that this round's edits are indeed confined to the report.
