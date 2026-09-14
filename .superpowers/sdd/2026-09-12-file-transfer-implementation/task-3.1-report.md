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
  only sending `size // READ_CHUNK_BYTES` back-to-back `FILE_CHUNK` frames
  and waiting for the receiving `PeerLink` to have assembled them all. This
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

- **`peak_python_bytes`.** Scoped more tightly than RSS: `tracemalloc.start()`
  runs at the very top of `measure()` (before identities are loaded,
  sockets opened, or the file generated), so this peak covers the entire
  `measure()` call — setup, the transfer, and the bypass pass — not the
  transfer alone. It is read before the bypass pass's own small buffer is
  allocated, so that allocation is included in the peak but does not
  dominate it (one `READ_CHUNK_BYTES`-sized buffer, reused for every frame).

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
