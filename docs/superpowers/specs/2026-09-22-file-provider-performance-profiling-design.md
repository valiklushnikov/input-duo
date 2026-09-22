# File Provider Finder Copy Performance Profiling Design

**Date:** 2026-09-22  
**Status:** APPROVED design; implementation requires a separately reviewed plan  
**Question:** `WHERE_DOES_THE_~4_SECONDS_PER_FILE_GO?`

## 1. Purpose and success condition

Instrument one clean Finder copy of 35–50 representative small files and account
for the observed approximately 4.3 seconds per file without changing transfer,
scheduling, eviction, timeout, or File Provider semantics.

The investigation succeeds when a correctness-valid run produces:

- a monotonic, correlated timeline for every fetch and every `FILE_READ`;
- measured Finder-facing and internal concurrency;
- measured queue waits and inter-file idle gaps;
- XPC, wire, byte-transfer, local finalization, and eviction contributions;
- a bottleneck classification from A–I with evidence and estimated contribution;
- at most three evidence-based optimization candidates, with no optimization
  implemented in this phase.

The clean-run baseline supplied before this investigation is contextual only:
35 byte-exact files, about 149 seconds total, about 4.3 seconds per file, 35
fetches, no refetch, no `-1005`, and no Finder `-36`. New measurements are
authoritative.

## 2. Hard scope boundary

This phase adds diagnostic events, offline analysis, tests, and the minimum build
and run support needed to collect those events. It does not change:

- `MAX_ACTIVE_FETCHES`, chunk size, or buffer limits;
- XPC or wire protocol semantics;
- `files/2`, `FILE_READ` strategy, prefetch, or batching;
- eviction grace, retry policy, or eviction behavior;
- Finder/File Provider lifecycle;
- durable generation storage, peer reconnect, or timeouts;
- architecture or concurrency.

The prior result `EVICTION_CAUSES_STALL = DISPROVEN` remains accepted. Eviction
is observed only. It can be classified as causal in this run solely with a
timeline showing a shared lock/executor blocking fetch work.

## 3. Approach

Use the existing logging paths with one structured performance event per stage
and per chunk:

- Swift extension events use `Logger` and
  `DispatchTime.now().uptimeNanoseconds`.
- macOS Python host events use the existing rotating application log and
  `time.monotonic_ns()`.
- Windows sender events use the same Python application logging mechanism and
  `time.monotonic_ns()` when a compatible diagnostic build can be run.
- An offline analyzer parses the captured logs and writes machine-readable JSON,
  a complete per-file CSV, and a compact Markdown report.

The expected event volume is bounded by the number of fetch stages and chunks.
There is no per-byte logging. The existing bounded/reopen-safe OSLog capture and
rotating Python logs remain the storage mechanisms; no tracing service or new
runtime IPC is introduced.

## 4. Correlation model

Use existing identifiers:

- `transfer_id` / generation id;
- `entry_index` / item identifier;
- `fetch_token` after `openFetch` succeeds;
- `read_id` for each `FILE_READ`/`FILE_CHUNK` pair;
- fetch attempt for retry disambiguation.

For the required clean run, one fetch per item and zero refetch makes
`transfer_id + entry_index` unique before a fetch token exists. The analyzer
must reject an ambiguous trace rather than guess if duplicate successful fetches
for the same item occur. No `perf_fetch_id` and no protocol field are required.

Events contain identifiers, monotonic timestamps, counts, sizes, offsets,
durations, and enum-like states only. They never contain file contents or full
paths. A basename may be retained only where existing privacy tests already
permit it.

## 5. Clock domains

Every event records an absolute monotonic nanosecond timestamp and a clock-domain
label:

- `swift_uptime` for the extension;
- `mac_python_monotonic` for the macOS host;
- `windows_python_monotonic` for the Windows sender;
- `observer_monotonic` for the external run observer.

Durations are computed only between timestamps in the same clock domain. Wall
timestamps may locate log files or delimit a run but never determine performance
latency.

The design does not subtract a Windows timestamp from a Mac timestamp. Mac wire
RTT and Windows service duration remain separate. A derived residual such as
`Mac RTT - Windows service duration` may be reported only as an explicitly
labelled aggregate estimate, never as one-way network latency.

Swift and Python run in different processes. Even though both clocks are based
on macOS monotonic facilities, the analyzer does not assume their absolute epochs
are interchangeable. XPC one-way durations across those domains are unavailable;
same-side round trips and processing durations remain exact.

## 6. Extension timeline

The Swift extension records:

| Mark | Event |
|---|---|
| T0 | `fetchContents` entry |
| T1 | `openFetch` invocation immediately before the XPC call |
| T3 | `openFetch` reply received by the extension |
| T8 | first chunk write completed |
| T11 | last chunk write completed |
| T12a | `synchronize()` completed |
| T12b | `close()` completed |
| T12 | all local finalization completed |
| T13 | immediately before calling the File Provider completion handler |

The extension also records each `pullChunk` invocation/reply, received chunk
length, cumulative offset, EOF, attempt, and success/failure. Zero-byte files
record finalization but no chunk events.

The externally meaningful destination-materialization time has no reliable
public completion callback in the current design. Therefore:

`DESTINATION_MATERIALIZATION_TIMESTAMP = UNAVAILABLE`

An external filesystem observation may be recorded as a separate observation if
its semantics prove reliable. It is not renamed T14 and is not used inside
`total_fetch`.

## 7. macOS host, XPC, queue, and wire timeline

The XPC adapter and `FileProviderBackend` record:

- T2: `openFetch` received on the host;
- host open processing completed and reply invoked;
- fetch state at open: admitted or queued;
- queue enter;
- slot acquired;
- actual work start when an admitted pull begins useful work;
- T4/T9: every `FILE_READ` send, including `read_id`, generation, entry,
  offset, and requested length;
- T7/T10: every matching `FILE_CHUNK` receive, including payload length;
- XPC chunk reply invocation;
- slot release / terminal fetch state.

Queue wait is `slot_acquired - queue_enter` for queued fetches and zero for
immediately admitted fetches. Actual-work delay is separately available as
`first_file_read_send - host_open_enter`; it is not folded invisibly into queue
wait.

The extension-side XPC duration is `T3 - T1`. Host-side open processing is
`host_open_reply - T2`. Exact reply-to-extension one-way latency is unavailable
because it crosses clock domains.

## 8. Windows service timeline

When a compatible diagnostic Windows build can be deployed, the sender records,
for each `read_id`:

- T5: `FILE_READ` receive;
- snapshot lookup begin/end;
- source read begin/end;
- T6: `FILE_CHUNK` enqueue/send boundary;
- payload length and echoed correlation fields.

This yields snapshot lookup, source read, and Windows receive-to-send service
durations without cross-machine clock arithmetic. If these events cannot be
collected for the run, the report states:

`WINDOWS_INTERNAL_BREAKDOWN = UNAVAILABLE`

Mac `FILE_READ_TO_CHUNK_RTT` remains available independently.

## 9. Eviction observations

Existing eviction lifecycle events gain monotonic timestamps and retain exact
item correlation. The trace includes:

- fetch completion;
- materialization observed by the coordinator;
- first and subsequent eviction attempts;
- `-2008` retries;
- eviction success and verification;
- abandonment.

The analyzer overlays eviction intervals with extension fetch intervals and host
work intervals. Overlap without a fetch stall is evidence that eviction is not
on the critical path. Correlation alone is insufficient to classify contention.
`EVICTION_ON_CRITICAL_PATH = YES` requires a named shared executor/lock and a
timeline demonstrating blocked fetch work. Otherwise the value is `NO` when
parallel progress is demonstrated, or `UNKNOWN` when evidence is incomplete.

## 10. Derived metrics

Per file, compute within valid clock domains:

```text
fetch_startup         = T3 - T0
xpc_open_fetch        = T3 - T1
host_dispatch         = T4 - T2
read_to_first_chunk   = T7 - T4
time_to_first_byte    = T8 - T0
active_byte_transfer  = T11 - T8
local_finalize        = T13 - T11
total_fetch           = T13 - T0
```

`host_dispatch` is a host-side duration because T2 and T4 are both emitted by
the macOS Python host. The analyzer will not fabricate a metric when either
endpoint is absent or belongs to a different clock domain.

For every `read_id`, compute Mac send-to-receive RTT. Windows receive-to-send
service time is computed separately when present. Chunk statistics include file
size, chunk count, requested and received sizes, average request size, and
p50/p95/max distributions.

`WIRE_ACTIVE_TIME` is the union of Mac intervals during which at least one
`FILE_READ` is outstanding. Active transfer throughput is received bytes divided
by that union duration. Whole-operation wall time is never presented as wire
throughput.

## 11. Concurrency and gaps

The analyzer sweeps T0/T13 extension intervals to compute Finder-request
concurrency:

- maximum and time-weighted average active fetches;
- time spent with 0, 1, 2, 3, and at least 4 active fetches;
- concurrency active at every fetch start.

It separately sweeps host slot-acquire/release intervals for internal fetch
concurrency. The classifications are:

- `FINDER_SERIALIZATION = YES` when extension fetch intervals never overlap;
- `INTERNAL_SERIALIZATION = YES` when Finder provides overlapping fetches but
  useful host work is limited to one despite available independent work;
- otherwise each is `NO`, with incomplete traces reported as unknown rather
  than inferred.

For non-overlapping fetches, inter-file idle is
`next.fetch_enter - previous.fetch_complete`. For overlapping fetches, the
analyzer also reports the gap between ordered first-useful-work events and the
preceding relevant terminal/useful stage. Negative overlap is not coerced into
idle time.

## 12. Analyzer outputs

One run produces:

- raw captured logs preserved unchanged;
- `run.json` containing dataset facts, clock domains, correctness results,
  parsed events, aggregates, and availability markers;
- `per-file.csv` with all 35–50 rows;
- `report.md` with required aggregates, representative rows, all outliers, and
  TOP 5 slowest files.

The report contains at minimum all fields requested in the task: file and byte
counts, size distribution, wall time, per-file time, fetch/time-to-first-byte/
XPC/read-RTT/transfer/finalization/inter-file/queue distributions, concurrency,
chunk statistics, active throughput, eviction attempts/retries, and critical-
path classification.

Missing stages are printed as `UNAVAILABLE`; the analyzer never substitutes
zero. Percentiles use a documented deterministic nearest-rank rule. Average
active concurrency is the integral of active fetch count divided by the covered
timeline duration.

## 13. Dataset and run protocol

Before the run, record:

- `FILE_COUNT`, `TOTAL_BYTES`, minimum, median, p95, and maximum file size;
- source hashes for later byte-exact comparison;
- an empty, newly created destination.

Run exactly one Finder Cmd+V. During the measurement there is no cancellation,
second paste, manual eviction, or parallel CLI copy. The run observer records
only its own monotonic start/end and filesystem observations. It does not drive
transfer concurrency.

After Finder completes, verify expected/actual counts, hashes, missing and
different files, refetch count, `-1005`, `-1004`, `-1000`, and Finder `-36`.
Any correctness regression invalidates the run and stops performance analysis.

Only after a valid Finder run may a separate CLI `cp` control be performed on
the same kind of dataless items and a comparable dataset. It uses a new empty
destination and the same available event schema. Finder and CLI never run
concurrently.

## 14. Bottleneck decision

The final report chooses one primary category and, when justified, one secondary
category:

```text
A Finder/File Provider scheduling
B internal queue/serialization
C XPC openFetch
D FILE_READ / Windows service / network RTT
E byte throughput
F local write/fsync/finalization
G eviction contention
H mixed
I insufficient evidence
```

Each significant category includes measured milliseconds, fraction of covered
wall time, and the evidence supporting causality. Unmeasured residual time is
reported explicitly; it is not assigned to Finder by elimination unless the
event boundaries prove that attribution.

After localization, list no more than three optimization candidates. Each names
the measured bottleneck, expected effect, risk, and smallest validating
experiment. No candidate is implemented in this phase.

## 15. Test strategy

Tests are written before implementation and cover:

- structured event fields, privacy, correlation, and monotonic clock labels;
- Swift fetch event ordering for normal, multi-chunk, zero-byte, error, and
  retry paths;
- Python queue admission/release and per-read send/receive event ordering;
- Windows read-service stage ordering around real snapshot reads;
- analyzer parsing of interleaved processes and incomplete optional stages;
- percentile, interval-union, throughput, concurrency-integral, queue-wait,
  overlap, and TOP-5 calculations;
- rejection of ambiguous refetch traces and missing correlation;
- correctness-gate invalidation and explicit `UNAVAILABLE` output;
- source-level guards against changes to forbidden production constants and
  protocols within the profiling patch.

Verification consists of focused tests, the complete Python test suite, the
complete File Provider Xcode test suite, build/package validation, and inspection
of the scoped diff before installing a diagnostic build.

## 16. Stop condition and final handoff

After one valid clean Finder run and, if feasible, one separate CLI control, the
investigation stops. The final handoff reports:

```text
PERFORMANCE_BASELINE_VALID
PRIMARY_BOTTLENECK
SECONDARY_BOTTLENECK
~4_SEC_PER_FILE_ACCOUNTED_FOR
FINDER_SCHEDULING_CONTRIBUTION
OUR_PIPELINE_CONTRIBUTION
WIRE_CONTRIBUTION
FINALIZATION_CONTRIBUTION
EVICTION_CONTRIBUTION
INTERNAL_SERIALIZATION
FINDER_SERIALIZATION
TOP_OPTIMIZATION_CANDIDATES
RECOMMENDED_NEXT_EXPERIMENT
```

No Gate C/D work and no production optimization follows automatically.
