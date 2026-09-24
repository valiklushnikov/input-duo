# Production per-file FILE_READ window (PER_FILE_READ_WINDOW = 4)

Status: DESIGN + implementation (offline gates). Runtime validation (spec §23–31)
handed off as a runbook — cannot be executed without the two-machine Finder/Windows
stand.

BASE_HEAD = a4aeb43

## 0. Problem

Production File Provider (Model C: `contentPolicy=downloadLazily`, file-level
pipeline depth 4, prefetch wave 8, eviction OFF) is byte-exact and stable, but a
single file transfers stop-and-wait: exactly **one `FILE_READ` is outstanding on
the host↔Windows link per fetch**. A controlled 128 MiB experiment showed a
repeatable **+14–19 %** throughput gain from keeping **4** `FILE_READ` in flight
per file (median +16.09 %), with bounded memory (≤4 MiB outstanding) and perfect
correctness.

Goal: implement that measured configuration in production — bounded, correct,
cancel/retry/disconnect-safe — **without** touching the wire protocol, the XPC
contract, the Swift consumer, or the file-level scheduler.

## 1. Where WINDOW=1 lives today (audit)

Chain: Finder → fileproviderd → Swift `FetchOperation.pull` → XPC `pullChunk`
→ Python `pull_chunk` → `FILE_READ` → Windows → `FILE_CHUNK` → `_on_chunk` →
`reply(blob)` → Swift writes → next pull.

| Layer | Location | Single-read assumption |
|---|---|---|
| Wire | `clipboard/wire.py` `FILE_READ/FILE_CHUNK` | `FILE_READ` carries `{transfer_id, entry_index, offset, length, read_id}`; `FILE_CHUNK` correlated by `read_id`. **Range identity SUFFICIENT.** |
| Host backend | `fileprovider_backend.py` `Fetch` | one `read_id`/`offset`/`expected`; guards in `pull_chunk`, `_request_chunk`, `_admit_pull`, `_on_chunk`, `_pending_fetch` (`offset == fetch.offset`). Bytes NOT buffered — `reply(message.blob)` by reference. Host owns cursor. |
| XPC | `fileprovider_proto.py` / `DuoXPC.swift` `pullChunk:reply:` | token-only → `(NSData, BOOL eof, NSError)`. **No offset in XPC** — host serves the "next sequential" chunk. |
| Swift consumer | `FetchController.swift` `FetchOperation` | strictly sequential: one `pullChunk` at a time (`pullSequence`), append `FileHandle.write(contentsOf:)`, `offset +=`, then next pull. |
| Windows serve | `windows_files.py` `request_read(pipe, offset, length)` | addressed by (offset,length); non-blocking enqueue — window-capable. |

The measured stop-and-wait gap is the interval between a chunk arriving at the
host and the *next* `FILE_READ` leaving — because the host only issues the next
read after Swift consumes the previous one over XPC. That gap is entirely on the
host↔Windows link.

## 2. Chosen design — Design A (host-side prefetch window + bounded reorder)

The host prefetches up to 4 ranges ahead of the Swift pull cursor and keeps the
network link full, while still serving the **unchanged, sequential** `pullChunk`
XPC contract from an in-order buffer. XPC, Swift, and the wire are untouched.

Rejected alternative (Design B): push offset/length into the XPC contract and let
Swift issue concurrent pulls + positional `pwrite`. Larger blast radius (proto
change, Swift concurrency, multiplied cancel/retry race surface) for no extra
network benefit — the stop-and-wait is host↔Windows, which Design A already
saturates. Spec §6 explicitly permits "bounded reorder storage" and §8 already
budgets a per-stream host read-ahead of 4 MiB, so Design A is what the spec
describes.

### 2.1 Per-fetch state

```
PER_FILE_READ_WINDOW = 4
CHUNK = MAX_FILE_CHUNK_BYTES = 1 MiB

Fetch:
  size            total bytes
  consume_offset  next byte to hand to Swift (in-order delivery cursor)
  plan_offset     next byte to REQUEST (window planner cursor)
  ranges          {offset -> _Range}   (at most WINDOW live)
  reply           parked consumer pull reply (at most one — Swift pulls serially)

_Range:
  read_id, offset, length, state ∈ {IN_FLIGHT, RECEIVED}, blob
```

Invariant: `len(ranges) <= WINDOW`. A range is live from the moment its
`FILE_READ` is issued (IN_FLIGHT) until Swift consumes it (deleted). So
`in_flight + received_buffered <= 4 ranges <= 4 MiB` per stream.

### 2.2 Window planner (`_fill_window`)

While `len(ranges) < WINDOW` and `plan_offset < size` and the global byte budget
allows: issue a `FILE_READ` for `[plan_offset, min(CHUNK, size-plan_offset)]`,
advance `plan_offset`. If the budget would be exceeded, park the fetch token in
`_pull_queue` (FIFO) and stop; it resumes when a prior range is consumed
(`_resume_windows`). Called on admission, after each consume, and on budget free.

### 2.3 Response correlation (spec §5 — reuse existing identity)

`by_read_id[read_id] -> Fetch`. On `FILE_CHUNK`: look up fetch by `read_id`, then
the range by header `offset`; require `range.read_id == read_id`,
`range.state == IN_FLIGHT`, `transfer_id`/`entry_index` match, `len(blob) ==
range.length`, `offset+len <= size`. No wire change; `read_id` is a monotonic
`itertools.count(1)` — globally unique for the process, so a stale response can
never alias a live range (spec §11 satisfied by construction).

### 2.4 Ordered consumer output (spec §6)

`_try_deliver(fetch)`: if a consumer reply is parked and `ranges[consume_offset]`
is RECEIVED, pop it, `consume_offset += length`, delete the range, and
`reply(blob, eof, None)` where `eof = consume_offset >= size`. Out-of-order
arrival (R0 R2 R1 R3) is handled: R2/R3 buffer as RECEIVED; delivery only advances
when the range at `consume_offset` is present. Reorder buffer ≤ 4 MiB/stream.

### 2.5 EOF / small files (spec §15, §16)

- size 0: `consume_offset (0) >= size (0)` → first `pull_chunk` replies `(b"",
  True)` and finishes DONE; `_fill_window` issues nothing.
- final partial: last range length `= size - plan_offset` (< 1 MiB).
- <4 MiB files: planner naturally issues fewer than 4 ranges (500 KiB→1,
  1.5 MiB→2, 2.5 MiB→3, ≥4 MiB→4). No forced 4 reads.

### 2.6 Cancellation (spec §10)

`cancel_fetch` → `_finish_fetch(CANCELLED)` which clears **all** ranges: pop every
range's `read_id` from `by_read_id`, disarm every per-read watchdog, drop buffered
blobs, settle the parked reply once. After cancel: no new `FILE_READ` (planner
gated on non-terminal state + membership in `_active`), late `FILE_CHUNK` for any
of the 4 read_ids finds no `by_read_id` entry → `fp_late_chunk`, dropped; cannot
resurrect (settle-once via `in_use_counted` + terminal state guard).

### 2.7 Late chunks (spec §10)

A `FILE_CHUNK` whose `read_id` is not in `by_read_id`, or whose fetch is terminal,
or whose offset/read_id do not match a live IN_FLIGHT range → counted
`fp_late_chunk` (or `fp_unexpected_chunk`) and dropped. Never writes, never
refills, never completes.

### 2.8 Disconnect (spec §12)

`link.disconnected` → `_on_link_lost` → `_fail_all_active(PeerLost=3)`; each fetch
funnels through `_finish_fetch`, clearing all its ranges. No deadlock, no pending
ranges, memory released. Unchanged reconnect architecture; Swift's existing
transient retry (codes 3/5/8) drives recovery.

### 2.9 Retry (spec §13)

Retry stays **whole-fetch** and lives on the Swift side (`FetchOperation.
scheduleRetry`: `cancelFetch(token)`, reset offset 0, new `openFetch`). The host
never retries an individual range. A new fetch gets fresh `read_id`s; a late
chunk from attempt A (old read_id) is dropped by §2.7. No per-range retry is
introduced, so there is no double-retry.

### 2.10 FILE_ERROR (spec §14)

First terminal `FILE_ERROR` on any range → `_finish_fetch(FAILED, mapped_code)`:
stop the planner, clear all ranges, settle once. Other in-flight/received ranges'
later chunks are dropped as late (§2.7). Exactly one failure emitted.

## 3. Bounds (spec §8)

```
PER_FILE_READ_WINDOW              = 4
CHUNK                            = 1 MiB
MAX_OUTSTANDING_READ_BYTES/stream = 4 MiB   (in-flight ranges)
MAX_REORDER_BUFFER_BYTES/stream   = 4 MiB   (received-not-consumed)
  — unified: total live ranges ≤ 4 ⇒ ≤ 4 MiB/stream combined
FILE_PROVIDER_PIPELINE_DEPTH      = 4  (unchanged)
MAX_THEORETICAL_GLOBAL_READS      = 4 streams × 4 = 16
MAX_THEORETICAL_GLOBAL_PAYLOAD    = 16 MiB (+ framing/copy)
```

`MAX_TOTAL_BUFFERED_BYTES` rises from 8 MiB → 16 MiB so the file-level pipeline
(4) × window (4) is not artificially throttled. `_outstanding_bytes()` sums range
lengths across all fetches and gates the planner (backpressure, spec §9). The two
concurrency levels stay independent and are instrumented separately (spec §17).

## 4. Telemetry (spec §21)

Counters on `backend.counters` (plain dict, no framework):
`fp_range_issued`, `fp_range_received`, `fp_range_consumed`, `fp_late_chunk`,
`fp_unexpected_chunk`, `fp_oversized_chunk`, `fp_truncated`,
`fp_max_outstanding_reads_per_stream`, `fp_max_global_outstanding_reads`,
`fp_max_outstanding_bytes_per_stream`, `fp_max_global_outstanding_bytes`,
`fp_window_occupancy_sum`/`_samples` (for mean), `fp_cancel_outstanding`.
Runtime assertion: `_issue_read` refuses to exceed WINDOW and updates the max
gauges — proves `MAX_OBSERVED_OUTSTANDING_READS <= 4`.

## 5. Test plan (spec §19 — RED first)

`tests/transfer/test_fileprovider_window.py`, 15 cases matching §19. Existing
scheduler/streaming/cancel/errors/observability tests that encode the WINDOW=1
`Fetch` shape are migrated to the windowed invariant (byte-exactness and
independence preserved).

## 6. Review findings & resolutions (spec §22 gate)

A multi-angle review (line-by-line, removed-behavior, cross-file) ran against the
implementation commit. Cross-file: no production call site broke; the XPC
`pullChunk(blob, eof, error)` contract and the Swift consumer are untouched.
Windows concurrency (§18): `TransferService._answer_read` is stateless and the
peer link dispatches `handle_message` serially on one Qt event-loop thread
(`peer.py` `message_received` from the socket `readyRead`), so up to 16 in-flight
FILE_READs are serviced one at a time — the shared per-entry descriptor
(`SnapshotRegistry._read_at`, lseek+read) is safe under serial access. Findings:

- **Watchdog deadline vs serial sender (FIXED).** The per-read watchdog was armed
  at issue time; with 4 reads issued at once and a serial sender, a later read's
  30s clock was consumed by head-of-line queueing and could fail a
  steadily-progressing transfer (a regression vs WINDOW=1). Fix: `_on_chunk`
  restarts every still-IN_FLIGHT read's watchdog on each chunk arrival
  (`_rearm_inflight_watchdogs`), so the watchdog again means "no chunk for 30s"
  (a genuinely hung host). Covered by
  `test_chunk_arrival_rearms_sibling_inflight_watchdogs`.
- **Zero-byte QUEUED fetch settling out of turn (FIXED).** `_try_deliver` now
  requires the fetch to be admitted (REQUESTING and in `_active`) before
  completing. Covered by
  `test_zero_byte_queued_fetch_completes_only_after_admission`.
- **Global byte-budget gate is defense-in-depth (BY DESIGN).** With the shipped
  config `MAX_TOTAL_BUFFERED_BYTES == MAX_ACTIVE_FETCHES × window × chunk` the
  per-fetch window cap already bounds outstanding reads and the global gate never
  fires; it engages only if a future WINDOW/MAX_ACTIVE change would exceed the
  global budget. Comment clarified; kept as an honest bound.
- **Occupancy-mean telemetry (FIXED).** Removed the issue-time-only occupancy
  mean (biased low); peak gauges stay exact and TIME_WINDOW_FULL_PERCENT (§27) is
  derived from the timestamped `fp_range_issued/received/consumed` events.
- **Stale comments/docstrings (FIXED).**

### Known bounded trade-off (accepted, not fixed)

An abandoned consumer that stops pulling **without** cancelling or disconnecting
can leave a fetch holding up to `PER_FILE_READ_WINDOW` buffered ranges
(≤ 4 MiB/stream) plus its generation in-use ref, with no watchdog once every
range is RECEIVED (watchdogs cover IN_FLIGHT reads only). This is **bounded**
(≤ 4 MiB/stream, ≤ 16 MiB total — the declared memory cap) and **self-heals** on
the normal termination paths (Finder cancel → `cancel_fetch`; peer disconnect →
`_fail_all_active`). A consumer-idle timeout was deliberately NOT added:
fileproviderd may legitimately pause between pulls under memory pressure, so
timing that out would introduce spurious failures. Watch
`fp_max_outstanding_bytes_per_stream` / `fp_max_global_outstanding_bytes` in
runtime artifacts to confirm the bound holds.
