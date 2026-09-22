# File Provider Finder Copy Performance Profiling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Instrument and run one correctness-valid Finder File Provider copy so the approximately four seconds per file can be assigned to measured stages without implementing a performance fix.

**Architecture:** Emit sparse `fp_perf` key/value events from the Swift extension, macOS Python host, and optional Windows sender, each with its own absolute monotonic clock domain and existing correlation identifiers. An offline Python analyzer joins only safe correlations, computes same-domain durations, concurrency/queue/idle/wire intervals, and produces JSON, CSV, and Markdown artifacts; a separate run helper snapshots the dataset and enforces the correctness gate.

**Tech Stack:** Swift 5 / Foundation / FileProvider / OSLog, Python 3.12 / PySide6 / standard library, XCTest, pytest.

**Spec:** `docs/superpowers/specs/2026-09-22-file-provider-performance-profiling-design.md`

## Global Constraints

- This is performance investigation only: no production optimization.
- Do not change `MAX_ACTIVE_FETCHES`, chunk size, buffer limits, XPC protocol, wire protocol, `files/2`, `FILE_READ` strategy, prefetch, batching, eviction policy, lifecycle, durable generation storage, peer reconnect, or timeouts.
- Preserve `EVICTION_CAUSES_STALL = DISPROVEN` unless a new timeline names a shared lock/executor and demonstrates that it blocks fetch work.
- Record absolute monotonic nanoseconds and a clock-domain label on every perf event; never calculate latency from wall timestamps.
- Never subtract a Windows timestamp from a Mac timestamp or a Swift timestamp from a Python timestamp.
- Use `transfer_id`, `entry_index`, `fetch_token`, `read_id`, and attempt for correlation; do not change either protocol to add an identifier.
- Log once per stage and per chunk, never per byte; never log content bytes or full paths.
- `DESTINATION_MATERIALIZATION_TIMESTAMP = UNAVAILABLE` unless a reliable API is discovered and separately proven during implementation.
- Any correctness regression invalidates the run and stops performance analysis.
- Stop after one valid Finder run and, only if feasible, one separate CLI control; do not implement an optimization.

## Review Focus

- A single-chunk file must produce one requested and received chunk while retaining distinct RTT, write, fsync, and completion stages.
- Multiple overlapping fetches must not be accidentally serialized by logging, test hooks, or analyzer ordering assumptions.
- Rotated/noisy logs and missing optional Windows events must produce explicit `UNAVAILABLE`, not zero or fabricated cross-clock latency.
- A duplicate/refetch for the same item must make the trace ambiguous or increment `REFETCH_COUNT`; it must never be silently merged into the first fetch.
- Empty, missing, different, or extra destination files and any `-1005/-1004/-1000/Finder -36` regression must make `PERFORMANCE_BASELINE_VALID = NO`.

---

### Task 1: Shared Python monotonic perf-event contract

**Files:**
- Create: `configurator/src/duo_input/transfer/fileprovider_perf.py`
- Create: `configurator/tests/transfer/test_fileprovider_perf.py`

**Interfaces:**
- Produces: `PerfEmitter(logger, clock_domain, clock=time.monotonic_ns)`, `now() -> int`, `emit(event: str, **fields: object) -> int`, and `emit_at(stamp: int, event: str, **fields: object) -> int`.
- Produces: one parseable line shaped as `fp_perf event=file_read_send mono_ns=123456789 clock=mac_python_monotonic read_id=7`.
- Consumes: Python `logging.Logger` and an injectable monotonic nanosecond callable.

- [ ] **Step 1: Write the failing event-contract tests**

```python
def test_emitter_records_injected_monotonic_time_and_sorted_fields(caplog):
    log = logging.getLogger("duo_input.test.perf")
    perf = PerfEmitter(log, "mac_python_monotonic", clock=lambda: 123_456_789)
    with caplog.at_level(logging.INFO, logger=log.name):
        returned = perf.emit_at(perf.now(), "file_read_send", read_id=7, length=4096)
    assert returned == 123_456_789
    assert caplog.records[-1].getMessage() == (
        "fp_perf event=file_read_send mono_ns=123456789 "
        "clock=mac_python_monotonic length=4096 read_id=7"
    )


@pytest.mark.parametrize("value", ["has space", "line\nbreak", b"secret"])
def test_emitter_rejects_fields_that_cannot_be_safe_key_values(value):
    perf = PerfEmitter(logging.getLogger("test"), "mac_python_monotonic")
    with pytest.raises(ValueError):
        perf.emit("unsafe", payload=value)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `cd configurator && python -m pytest tests/transfer/test_fileprovider_perf.py -q`

Expected: collection fails because `duo_input.transfer.fileprovider_perf` does not exist.

- [ ] **Step 3: Implement the minimal emitter**

```python
class PerfEmitter:
    def __init__(self, logger, clock_domain: str, clock=time.monotonic_ns) -> None:
        self._logger = logger
        self._clock_domain = _atom(clock_domain)
        self._clock = clock

    def now(self) -> int:
        return int(self._clock())

    def emit(self, event: str, **fields: object) -> int:
        return self.emit_at(self.now(), event, **fields)

    def emit_at(self, stamp: int, event: str, **fields: object) -> int:
        atoms = [f"{key}={_atom(value)}" for key, value in sorted(fields.items())]
        suffix = f" {' '.join(atoms)}" if atoms else ""
        self._logger.info(
            "fp_perf event=%s mono_ns=%d clock=%s%s",
            _atom(event), stamp, self._clock_domain, suffix,
        )
        return stamp
```

`_atom` accepts `str`, `int`, `float`, `bool`, and `None`, rejects whitespace/control characters and bytes, and renders booleans as lowercase `true/false` and `None` as `none`. Do not add buffering, files, threads, or flush behavior.

- [ ] **Step 4: Run the focused and transfer tests**

Run: `cd configurator && python -m pytest tests/transfer/test_fileprovider_perf.py tests/transfer/test_fileprovider_observability.py -q`

Expected: PASS with the existing observability tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/transfer/fileprovider_perf.py configurator/tests/transfer/test_fileprovider_perf.py
git commit -m "test(fileprovider): define monotonic perf event contract"
```

### Task 2: macOS XPC, scheduler, queue, and wire instrumentation

**Files:**
- Modify: `configurator/src/duo_input/transfer/fileprovider_client.py`
- Modify: `configurator/src/duo_input/transfer/fileprovider_backend.py`
- Modify: `configurator/tests/transfer/test_fileprovider_client.py`
- Modify: `configurator/tests/transfer/test_fileprovider_scheduler.py`
- Modify: `configurator/tests/transfer/test_fileprovider_observability.py`

**Interfaces:**
- Consumes: `PerfEmitter` from Task 1.
- Extends: `FileProviderServiceClient(parent: QObject | None = None, *, perf: PerfEmitter | None = None)` without changing registered callback signatures.
- Extends: `FileProviderBackend` with the keyword-only argument `perf: PerfEmitter | None = None` without changing scheduler limits or state transitions.
- Produces events: `xpc_call_received`, `xpc_dispatch_enter`, `xpc_reply_invoke`, `open_fetch_enter`, `open_fetch_reply`, `queue_enter`, `slot_acquired`, `work_start`, `file_read_send`, `file_chunk_receive`, `xpc_chunk_reply`, and `slot_released`.

- [ ] **Step 1: Write failing XPC-boundary tests**

Inject a fake clock with values `[100, 120, 150]` and an in-memory log handler.

```python
def test_open_callback_preserves_arguments_and_records_receive_dispatch_reply(qapp, caplog):
    ticks = iter([100, 120, 150])
    perf = PerfEmitter(
        logging.getLogger("duo_input.transfer.fileprovider_client"),
        "mac_python_monotonic",
        clock=lambda: next(ticks),
    )
    replies = []
    client = FileProviderServiceClient(perf=perf)
    with caplog.at_level(logging.INFO, logger="duo_input.transfer.fileprovider_client"):
        client.set_callbacks(
            open_fetch=lambda generation, index, reply: reply("tok", 3, None)
        )
        client._dispatch_extension_call(
            "open", "generation", 2, lambda *values: replies.append(values)
        )
    records = [record.getMessage() for record in caplog.records if "fp_perf" in record.getMessage()]
    assert replies == [("tok", 3, None)]
    assert event_names(records) == [
        "xpc_call_received", "xpc_dispatch_enter", "xpc_reply_invoke"
    ]
    assert all("clock=mac_python_monotonic" in line for line in records)
```

The test must assert that no logged line contains a byte payload or an absolute path.

- [ ] **Step 2: Run the client test and verify RED**

Run: `cd configurator && python -m pytest tests/transfer/test_fileprovider_client.py -q`

Expected: FAIL because the client does not accept `perf` or emit the three events.

- [ ] **Step 3: Implement XPC boundary timing without changing callback semantics**

At `_dispatch_extension_call`, capture `received_ns = perf.emit("xpc_call_received", kind=kind)` before `QMetaObject.invokeMethod` and include the integer in the private QVariant payload. `_run_dispatch` logs dispatch entry and wraps the existing settle-once reply:

```python
kind, args, received_ns = payload

def once(*values):
    nonlocal settled
    if not settled:
        settled = True
        self._perf.emit("xpc_reply_invoke", kind=kind, received_ns=received_ns)
        reply(*values)
```

Log only safe correlation fields extracted by kind: generation/index for open,
fetch token for pull/cancel. Do not alter `set_callbacks` or ObjC selectors.

- [ ] **Step 4: Write failing scheduler/wire event-order tests**

Extend the existing six-fetch scheduler fixture with an injected sequential clock.

```python
def test_six_fetches_measure_four_immediate_slots_and_two_queue_waits(qapp, caplog):
    backend, link, _remote, manifest = _backend(qapp, _manifest())
    tokens = _open_all(backend, manifest)
    assert events_for(tokens[0])[:2] == ["open_fetch_enter", "slot_acquired"]
    assert events_for(tokens[4])[:2] == ["open_fetch_enter", "queue_enter"]
    complete_first_fetch(backend, link, tokens[0], b"abc")
    assert "slot_acquired" in events_for(tokens[4])
    assert backend.counters["fp_active_fetches"] == 4


def test_one_pull_logs_send_and_matching_receive_with_sizes(qapp, caplog):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)))
    [token] = _open_all(backend, manifest)
    backend.pull_chunk(token)
    read = link.sent[-1]
    backend.handle_message(_reply(read, b"abc"))
    assert perf_fields("file_read_send", read_id=read.header["read_id"]) == {
        "offset": 0, "length": 3,
    }
    assert perf_fields("file_chunk_receive", read_id=read.header["read_id"])["bytes"] == 3
```

- [ ] **Step 5: Run scheduler tests and verify RED**

Run: `cd configurator && python -m pytest tests/transfer/test_fileprovider_scheduler.py tests/transfer/test_fileprovider_observability.py -q`

Expected: FAIL because the host stage events do not exist.

- [ ] **Step 6: Implement host stage events at existing transitions**

Use the following exact boundaries:

```text
open_fetch_enter    first line of open_fetch
open_fetch_reply    immediately before reply(token, size, None)
slot_acquired       when token enters _active, including immediate admission
queue_enter         when token enters _queue
work_start          first _request_chunk call that can send/read or finish EOF
file_read_send      immediately before self._link.send(message)
file_chunk_receive  after correlation validation, before state mutation
xpc_chunk_reply     immediately before reply(message.blob, eof, None)
slot_released       immediately after _active.discard in _finish_fetch
```

Every post-open event carries `transfer_id`, `entry_index`, and `fetch_token`;
per-read events also carry `read_id`, offset, requested/received size. Queue
promotion emits one `slot_acquired` with `queued=true`. Immediate admission emits
one with `queued=false`. Do not reorder reply, queue admission, watchdog, or
state changes.

- [ ] **Step 7: Run focused Mac host regression tests**

Run:

```bash
cd configurator
python -m pytest \
  tests/transfer/test_fileprovider_client.py \
  tests/transfer/test_fileprovider_scheduler.py \
  tests/transfer/test_fileprovider_streaming.py \
  tests/transfer/test_fileprovider_cancel.py \
  tests/transfer/test_fileprovider_observability.py -q
```

Expected: PASS; existing active/queued counts, chunk behavior, cancellation, and timeouts remain unchanged.

- [ ] **Step 8: Commit**

```bash
git add configurator/src/duo_input/transfer/fileprovider_client.py \
  configurator/src/duo_input/transfer/fileprovider_backend.py \
  configurator/tests/transfer/test_fileprovider_client.py \
  configurator/tests/transfer/test_fileprovider_scheduler.py \
  configurator/tests/transfer/test_fileprovider_observability.py
git commit -m "feat(fileprovider): trace Mac XPC queue and wire stages"
```

### Task 3: Windows FILE_READ service instrumentation

**Files:**
- Modify: `configurator/src/duo_input/transfer/service.py`
- Modify: `configurator/src/duo_input/transfer/source.py`
- Modify: `configurator/tests/transfer/test_service_sender.py`
- Modify: `configurator/tests/transfer/test_source.py`

**Interfaces:**
- Consumes: `PerfEmitter` from Task 1.
- Extends: `FileTransferService(parent: QObject | None = None, idle_timeout_ms: int = SESSION_IDLE_TIMEOUT_MS, *, perf: PerfEmitter | None = None)`.
- Extends: `SnapshotRegistry(perf: PerfEmitter | None = None)` and `read(transfer_id: str, entry_index: int, offset: int, length: int, *, read_id: int | None = None)` solely for correlation; callers that omit it retain current behavior.
- Produces Windows-domain events: `file_read_receive`, `snapshot_lookup_begin`, `snapshot_lookup_end`, `source_read_begin`, `source_read_end`, and `file_chunk_send`.

- [ ] **Step 1: Write failing sender service tests with a fake clock**

```python
def test_sender_records_lookup_read_and_send_for_one_read(sender_with_perf, tmp_path):
    service, link, records = sender_with_perf
    transfer_id = offer_bytes(service, tmp_path, b"0123456789")
    service.handle_message(_read(transfer_id, offset=3, length=4, read_id=7))
    assert event_names(records) == [
        "file_read_receive", "snapshot_lookup_begin", "snapshot_lookup_end",
        "source_read_begin", "source_read_end", "file_chunk_send",
    ]
    assert all(field(records, event, "read_id") == "7" for event in event_names(records))
    assert field(records, "file_chunk_send", "bytes") == "4"
    assert _sent(link, MessageType.FILE_CHUNK)[0].blob == b"3456"
```

Also add an error-path test proving `source_read_end` carries `status=source_changed`
and that no `file_chunk_send` is emitted.

- [ ] **Step 2: Run sender/source tests and verify RED**

Run: `cd configurator && python -m pytest tests/transfer/test_service_sender.py tests/transfer/test_source.py -q`

Expected: FAIL because the constructors and event boundaries are absent.

- [ ] **Step 3: Instrument the existing read path**

`FileTransferService` creates a default emitter whose domain is
`windows_python_monotonic` on `sys.platform == "win32"`; tests inject the domain
explicitly. `_answer_read` captures `received_ns = self._perf.now()` on its first
line, emits `file_read_receive` at that saved timestamp after safe header
validation, and passes `read_id` into `SnapshotRegistry.read`.

Inside `SnapshotRegistry.read`, bracket only the existing operations. Snapshot
lookup ends after validating the snapshot/entry and locating its source metadata;
source read begins before the existing descriptor open/verify/read sequence so
the metric includes first-open cost:

```python
self._perf.emit("snapshot_lookup_begin", read_id=read_id,
                transfer_id=transfer_id, entry_index=entry_index)
# existing snapshot/entry/source-metadata lookup
self._perf.emit("snapshot_lookup_end", read_id=read_id,
                transfer_id=transfer_id, entry_index=entry_index)
self._perf.emit("source_read_begin", read_id=read_id,
                transfer_id=transfer_id, entry_index=entry_index,
                offset=offset, length=length)
try:
    descriptor = snapshot.handles.get(entry_index)
    if descriptor is None:
        descriptor = self._open_and_verify(snapshot, entry_index, entry)
        snapshot.handles[entry_index] = descriptor
        snapshot.serving = True
    self._verify_unchanged(descriptor, entry)
    payload = self._read_at(descriptor, offset, length)
except (SourceChanged, SourceMissing, OSError) as error:
    status = "source_changed" if isinstance(error, SourceChanged) else "source_missing"
    self._perf.emit("source_read_end", read_id=read_id, status=status,
                    transfer_id=transfer_id, entry_index=entry_index)
    raise
self._perf.emit("source_read_end", read_id=read_id, status="ok", bytes=len(payload),
                transfer_id=transfer_id, entry_index=entry_index)
```

Record `file_chunk_send` immediately before the existing `_send(FILE_CHUNK)`.
Do not split the read into new I/O calls, change descriptor lifetime, or move
validation.

- [ ] **Step 4: Run sender and end-to-end regressions**

Run:

```bash
cd configurator
python -m pytest tests/transfer/test_source.py tests/transfer/test_service_sender.py \
  tests/transfer/test_end_to_end.py tests/transfer/test_fileprovider_end_to_end.py -q
```

Expected: PASS with byte-identical chunks and unchanged error reasons.

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/transfer/service.py \
  configurator/src/duo_input/transfer/source.py \
  configurator/tests/transfer/test_service_sender.py \
  configurator/tests/transfer/test_source.py
git commit -m "feat(fileprovider): trace Windows read service stages"
```

### Task 4: Swift fetch, write, fsync, close, and completion instrumentation

**Files:**
- Create: `configurator/fileprovider/Extension/PerfTrace.swift`
- Create: `configurator/fileprovider/Tests/PerfTraceTests.swift`
- Modify: `configurator/fileprovider/Extension/FileProviderExtension.swift`
- Modify: `configurator/fileprovider/Extension/FetchController.swift`
- Modify: `configurator/fileprovider/Tests/FetchControllerTests.swift`

**Interfaces:**
- Produces: `PerfTrace(clock: () -> UInt64, emit: (String) -> Void)` with `mark(_ event: String, at: UInt64? = nil, fields: [(String, String)]) -> UInt64`.
- Default clock: `DispatchTime.now().uptimeNanoseconds`; default output: OSLog category `perf`.
- Extends: `FetchController` with `perf: PerfTrace = .live` and the extension initializer with an injectable trace for tests.
- Produces events T0/T1/T3/T8/T11/T12a/T12b/T12/T13 plus per-pull reply size.

- [ ] **Step 1: Write failing `PerfTrace` formatting tests**

```swift
func testMarkUsesInjectedUptimeAndStableFields() {
    var lines: [String] = []
    let trace = PerfTrace(clock: { 123_456_789 }, emit: { lines.append($0) })
    let stamp = trace.mark("fetch_enter", fields: [
        ("transfer_id", "generation"), ("entry_index", "2")
    ])
    XCTAssertEqual(stamp, 123_456_789)
    XCTAssertEqual(lines, [
        "fp_perf event=fetch_enter mono_ns=123456789 clock=swift_uptime " +
        "entry_index=2 transfer_id=generation"
    ])
}
```

Add a test that whitespace/control characters are replaced by an explicit
`invalid_atom` marker rather than emitted raw.

- [ ] **Step 2: Run the Swift test and verify RED**

Run:

```bash
cd configurator/fileprovider
xcodegen generate
xcodebuild test -scheme DuoInputFileProvider -destination 'platform=macOS' \
  -only-testing:DuoInputFileProviderTests/PerfTraceTests
```

Expected: build fails because `PerfTrace` does not exist.

- [ ] **Step 3: Implement the Swift trace sink**

```swift
final class PerfTrace {
    typealias Clock = () -> UInt64
    typealias Emit = (String) -> Void
    private let clock: Clock
    private let emit: Emit

    @discardableResult
    func mark(_ event: String, at stamp: UInt64? = nil,
              fields: [(String, String)] = []) -> UInt64 {
        let value = stamp ?? clock()
        let suffix = fields.sorted { $0.0 < $1.0 }
            .map { "\($0.0)=\(Self.atom($0.1))" }.joined(separator: " ")
        emit("fp_perf event=\(Self.atom(event)) mono_ns=\(value) " +
             "clock=swift_uptime\(suffix.isEmpty ? "" : " " + suffix)")
        return value
    }
}
```

The live emitter calls `Logger(subsystem: "com.duoinput.configurator.fileprovider", category: "perf").info` once per
line. It does not persist or flush synchronously.

- [ ] **Step 4: Write failing fetch-stage tests**

Extend `CannedHost` to support deferred open and pull replies. Inject increasing
clock values and capture trace lines.

```swift
func testSingleChunkFetchRecordsOrderedStagesWithoutChangingBytes() throws {
    // 3-byte item, one 3-byte EOF chunk
    XCTAssertEqual(events, [
        "fetch_enter", "open_fetch_call_begin", "open_fetch_reply",
        "pull_call_begin", "pull_reply", "first_write_complete",
        "last_write_complete", "fsync_complete", "close_complete",
        "finalize_complete", "completion_call"
    ])
    XCTAssertEqual(try Data(contentsOf: XCTUnwrap(resultURL)), Data("abc".utf8))
}
```

Add multi-chunk, zero-byte, retry, and write-error tests. The one-chunk test
asserts first/last write use the same captured write-completion timestamp. The
error test asserts one `completion_call` with `status=error`.

- [ ] **Step 5: Run FetchController tests and verify RED**

Run: `cd configurator/fileprovider && xcodebuild test -scheme DuoInputFileProvider -destination 'platform=macOS' -only-testing:DuoInputFileProviderTests/FetchControllerTests`

Expected: FAIL because the fetch events are absent.

- [ ] **Step 6: Instrument exact Swift boundaries**

- Emit `fetch_enter` at the first executable line of `fetchContents`, before validation.
- Emit `open_fetch_call_begin` immediately before `host.openFetch` and `open_fetch_reply` as the first line of its callback.
- Emit `pull_call_begin` immediately before `host.pullChunk` and `pull_reply` as the first callback line.
- Capture one `writeDoneNs` immediately after `FileHandle.write`; use it for both first and last marks when a single chunk is both.
- Emit `fsync_complete` only after `synchronize()` returns, `close_complete` only after `close()` returns, and `finalize_complete` after the handle is cleared.
- Emit `completion_call` immediately before the existing success or error completion invocation.

Preserve the existing serial operation queue, pull recursion, retry schedule,
temporary-file lifecycle, `Progress`, and completion order.

- [ ] **Step 7: Run all File Provider tests**

Run:

```bash
cd configurator/fileprovider
xcodegen generate
xcodebuild test -scheme DuoInputFileProvider -destination 'platform=macOS' \
  CODE_SIGN_STYLE=Automatic DEVELOPMENT_TEAM=4YKVN22BMX
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add configurator/fileprovider/Extension/PerfTrace.swift \
  configurator/fileprovider/Extension/FileProviderExtension.swift \
  configurator/fileprovider/Extension/FetchController.swift \
  configurator/fileprovider/Tests/PerfTraceTests.swift \
  configurator/fileprovider/Tests/FetchControllerTests.swift
git commit -m "feat(fileprovider): trace extension fetch and finalization stages"
```

### Task 5: Eviction timeline instrumentation

**Files:**
- Modify: `configurator/fileprovider/Extension/EvictionCoordinator.swift`
- Modify: `configurator/fileprovider/Extension/FileProviderExtension.swift`
- Modify: `configurator/fileprovider/Tests/EvictionCoordinatorTests.swift`

**Interfaces:**
- Consumes: the same `PerfTrace` instance used by `FetchController`.
- Produces perf events: `cleanup_scheduled`, `materialization_observed`, `eviction_attempt`, `eviction_deferred`, `eviction_success`, `eviction_verified`, and `cleanup_abandoned`.
- Preserves every existing `fp_*` cleanup log and state transition.

- [ ] **Step 1: Write failing eviction event-order tests**

```swift
func testMaterializationEvictionAndVerificationCarryMonotonicItemTimeline() {
    let trace = recordingTrace()
    let coord = EvictionCoordinator(environment: env, executor: { $0() }, perf: trace)
    // Drive existing fake environment through materialize -> grace -> evict -> verify.
    XCTAssertEqual(trace.events.map(\.name), [
        "cleanup_scheduled", "materialization_observed",
        "eviction_attempt", "eviction_success", "eviction_verified"
    ])
    XCTAssertTrue(trace.events.allSatisfy { $0.item == "gen:0" })
}
```

Add a `-2008` test asserting attempt/retry counts and timestamps while retaining
the current retry delays and terminal state.

- [ ] **Step 2: Run eviction tests and verify RED**

Run: `cd configurator/fileprovider && xcodebuild test -scheme DuoInputFileProvider -destination 'platform=macOS' -only-testing:DuoInputFileProviderTests/EvictionCoordinatorTests`

Expected: FAIL because the coordinator has no perf sink.

- [ ] **Step 3: Add perf marks beside, not instead of, existing events**

Add `perf: PerfTrace = .live` to the initializer. Each existing cleanup event
transition emits one corresponding perf mark with transfer id, item identifier,
attempt, retry count, error code, and reason where applicable. Do not move calls
to `queryMaterialized`, `schedule`, or `evict`; do not change executor choice or
configuration defaults.

- [ ] **Step 4: Run File Provider tests**

Run:

```bash
cd configurator/fileprovider
xcodegen generate
xcodebuild test -scheme DuoInputFileProvider -destination 'platform=macOS' \
  CODE_SIGN_STYLE=Automatic DEVELOPMENT_TEAM=4YKVN22BMX
```

Expected: PASS with the original eviction behavior tests and new timeline tests.

- [ ] **Step 5: Commit**

```bash
git add configurator/fileprovider/Extension/EvictionCoordinator.swift \
  configurator/fileprovider/Extension/FileProviderExtension.swift \
  configurator/fileprovider/Tests/EvictionCoordinatorTests.swift
git commit -m "feat(fileprovider): timestamp eviction observations"
```

### Task 6: Offline parser, metrics engine, CSV, JSON, and Markdown report

**Files:**
- Create: `tools/fp_perf_metrics.py`
- Create: `tools/fp_perf_analyze.py`
- Create: `tools/test_fp_perf_metrics.py`
- Create: `tools/fixtures/fp_perf/synthetic-extension.log`
- Create: `tools/fixtures/fp_perf/synthetic-mac.log`
- Create: `tools/fixtures/fp_perf/synthetic-windows.log`

**Interfaces:**
- Produces: `parse_perf_lines(lines, source) -> list[PerfEvent]`.
- Produces: `analyze(events, dataset, correctness) -> RunAnalysis`.
- Produces CLI with repeatable/path-list `--extension-log`, `--mac-log`, optional `--windows-log`, plus `--dataset`, optional `--correctness`, and `--output-dir` arguments.
- Writes: `run.json`, `per-file.csv`, and `report.md`.

- [ ] **Step 1: Write failing parser and clock-safety tests**

```python
def test_parser_extracts_perf_event_from_prefixed_oslog_line():
    [event] = parse_perf_lines([OSLOG_PREFIX + PERF_LINE], source="extension")
    assert event.name == "fetch_enter"
    assert event.mono_ns == 1_000_000_000
    assert event.clock == "swift_uptime"


def test_analyzer_never_subtracts_different_clock_domains():
    result = analyze(cross_clock_fixture(), dataset(), correctness_ok())
    assert result.xpc_open_fetch_ms == [10.0]       # T1/T3, Swift only
    assert result.windows_internal_breakdown == "UNAVAILABLE"
    assert result.network_plus_dispatch == "UNAVAILABLE"
```

Malformed perf lines are collected under `parse_errors`; a required malformed
line makes the baseline invalid rather than being silently skipped.

- [ ] **Step 2: Run parser tests and verify RED**

Run: `python -m pytest tools/test_fp_perf_metrics.py -q`

Expected: collection fails because the metrics module does not exist.

- [ ] **Step 3: Implement parser and typed records**

```python
@dataclass(frozen=True)
class PerfEvent:
    source: str
    name: str
    mono_ns: int
    clock: str
    fields: dict[str, str]


@dataclass
class FetchMetrics:
    transfer_id: str
    entry_index: int
    size: int
    chunks: int
    fetch_total_ms: float | None
    open_fetch_ms: float | None
    first_byte_ms: float | None
    transfer_ms: float | None
    finalize_ms: float | None
    queue_wait_ms: float | None
    max_concurrency_at_start: int | None
```

The parser searches for the literal `fp_perf ` inside arbitrary log prefixes,
splits only `key=value` atoms, validates integer timestamps, and preserves raw
lines only in diagnostics—not in the report.

- [ ] **Step 4: Write failing deterministic metric tests**

Use hand-authored nanosecond fixtures with three overlapping fetches, four reads,
one queue wait, one inter-file idle gap, and eviction overlapping an active fetch.

```python
def test_interval_union_and_active_throughput_use_only_outstanding_reads():
    result = analyze_fixture()
    assert result.wire_active_time_ms == 40.0
    assert result.received_bytes == 8_000
    assert result.active_transfer_throughput_bytes_per_second == 200_000.0


def test_concurrency_integral_and_distribution_are_time_weighted():
    result = analyze_fixture()
    assert result.max_active_fetches == 3
    assert result.active_fetch_time_ms == {0: 10.0, 1: 30.0, 2: 20.0, 3: 10.0, "ge4": 0.0}
    assert result.avg_active_fetches == 1.0


def test_duplicate_successful_item_fetch_is_not_silently_merged():
    result = analyze(duplicate_fetch_fixture(), dataset(), correctness_ok())
    assert result.refetch_count == 1
    assert result.performance_baseline_valid is False
```

Also test nearest-rank p50/p95/max, queue wait, TOP 5 ordering, single-chunk
statistics, Windows service duration, eviction `NO/UNKNOWN`, and missing-stage
`UNAVAILABLE` rendering.

- [ ] **Step 5: Implement metric functions**

Expose and test the following pure helpers; the percentile and interval-union implementations are:

```python
def nearest_rank(values: Sequence[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered) / 100) - 1)
    return ordered[index]


def interval_union_ns(intervals: Iterable[tuple[int, int]]) -> int:
    ordered = sorted((start, end) for start, end in intervals if end >= start)
    if not ordered:
        return 0
    total = 0
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            total += current_end - current_start
            current_start, current_end = start, end
    return total + current_end - current_start
```

`concurrency_sweep` sorts `(timestamp, delta)` boundaries with completions before
starts at equal timestamps, integrates each count over the following interval,
and returns maximum, time-weighted average, and buckets 0/1/2/3/≥4.
`correlate_fetches` keys pre-open events by `(transfer_id, entry_index)` and
post-open events by `fetch_token`; `correlate_reads` keys by `(transfer_id,
read_id)`. `classify_bottleneck` consumes only measured fields and returns I when
coverage is incomplete.

For each fetch, calculate the spec formulas exactly:

```python
fetch_startup_ms = ns_to_ms(t3 - t0)
xpc_open_fetch_ms = ns_to_ms(t3 - t1)
host_dispatch_ms = ns_to_ms(t4 - t2)
read_to_first_chunk_ms = ns_to_ms(t7 - t4)
time_to_first_byte_ms = ns_to_ms(t8 - t0)
active_byte_transfer_ms = ns_to_ms(t11 - t8)
local_finalize_ms = ns_to_ms(t13 - t11)
total_fetch_ms = ns_to_ms(t13 - t0)
```

Enforce same-domain endpoints before every subtraction. Partition each Swift
fetch as `T0–T3 startup + T3–T8 post-open wait + T8–T11 transfer + T11–T13
finalize`; this identity must equal `T13–T0`. At operation level, calculate the
union of T0–T13 intervals and the zero-active gaps between earliest T0 and latest
T13. This accounts for wall time without double-counting overlapping fetches.
Host queue/RTT intervals remain supporting causal evidence and are not added a
second time to the Swift wall partition.

The aggregate object and Markdown renderer expose these exact names:

```text
FILES TOTAL_BYTES TOTAL_WALL_TIME SEC_PER_FILE
FILE_SIZE_P50 FILE_SIZE_P95 FETCH_TOTAL_P50 FETCH_TOTAL_P95 FETCH_TOTAL_MAX
TIME_TO_FIRST_BYTE_P50 TIME_TO_FIRST_BYTE_P95
XPC_OPEN_FETCH_P50 XPC_OPEN_FETCH_P95 XPC_OPEN_FETCH_MAX
FILE_READ_TO_FIRST_CHUNK_P50 FILE_READ_TO_FIRST_CHUNK_P95
ACTIVE_BYTE_TRANSFER_P50 ACTIVE_BYTE_TRANSFER_P95
FSYNC_P50 FSYNC_P95 FINALIZATION_P50 FINALIZATION_P95
LOCAL_FINALIZE_P50 LOCAL_FINALIZE_P95
INTER_FILE_IDLE_P50 INTER_FILE_IDLE_P95 INTER_FILE_IDLE_MAX
QUEUE_WAIT_P50 QUEUE_WAIT_P95 QUEUE_WAIT_MAX
MAX_ACTIVE_FETCHES AVG_ACTIVE_FETCHES
TIME_WITH_0_ACTIVE TIME_WITH_1_ACTIVE TIME_WITH_2_ACTIVE
TIME_WITH_3_ACTIVE TIME_WITH_GE4_ACTIVE
AVG_CHUNKS_PER_FILE P50_CHUNKS_PER_FILE P95_CHUNKS_PER_FILE AVG_REQUEST_SIZE
READ_CHUNK_RTT_P50 READ_CHUNK_RTT_P95 READ_CHUNK_RTT_MAX
WINDOWS_READ_SERVICE_TIME SNAPSHOT_LOOKUP_P50 SNAPSHOT_LOOKUP_P95
SOURCE_READ_P50 SOURCE_READ_P95 WINDOWS_READ_TO_SEND_P50 WINDOWS_READ_TO_SEND_P95
WIRE_ACTIVE_TIME ACTIVE_TRANSFER_THROUGHPUT
EVICTION_ATTEMPTS EVICTION_RETRIES EVICTION_ON_CRITICAL_PATH
```

Per-file CSV columns are `item,size,chunks,fetch_total_ms,open_fetch_ms,
first_byte_ms,transfer_ms,finalize_ms,queue_wait_ms,max_concurrency_at_start`.
The JSON additionally preserves requested/received chunk-size arrays and all
availability markers.

`classify_bottleneck` returns `I` whenever required coverage is insufficient.
It may select A–H only from measured contributions and includes the exact metric
names used as evidence. It does not hard-code the old 4.3/0.9-second baselines.

- [ ] **Step 6: Write failing artifact-rendering tests**

```python
def test_report_contains_required_stop_fields_and_unavailable_markers(tmp_path):
    paths = write_artifacts(analyze_fixture_without_windows(), tmp_path)
    report = paths.report.read_text()
    assert "PERFORMANCE_BASELINE_VALID = YES" in report
    assert "WINDOWS_INTERNAL_BREAKDOWN = UNAVAILABLE" in report
    assert "DESTINATION_MATERIALIZATION_TIMESTAMP = UNAVAILABLE" in report
    assert "TOP 5 slowest" in report
    assert len(list(csv.DictReader(paths.csv.open()))) == 3
```

- [ ] **Step 7: Implement JSON/CSV/Markdown writers and CLI**

The Markdown contains every aggregate and final stop field from the spec,
representative rows plus TOP 5, measured contribution text, and no more than
three candidate slots. Candidate slots remain empty when classification is I;
the analyzer does not invent generic advice.

- [ ] **Step 8: Run analyzer tests**

Run: `python -m pytest tools/test_fp_perf_metrics.py -q`

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add tools/fp_perf_metrics.py tools/fp_perf_analyze.py \
  tools/test_fp_perf_metrics.py tools/fixtures/fp_perf
git commit -m "tools(fileprovider): analyze monotonic performance traces"
```

### Task 7: Dataset manifest, destination guard, and correctness gate

**Files:**
- Create: `tools/fp_perf_run.py`
- Create: `tools/test_fp_perf_run.py`

**Interfaces:**
- CLI `snapshot --root SOURCE --output dataset.json` records SHA-256 and size for 35–50 files.
- CLI `check-destination --path DESTINATION` succeeds only for a new or empty explicit directory and never deletes contents.
- CLI `verify --dataset dataset.json --destination DESTINATION --events run.json --finder-error none --output correctness.json` produces the mandatory correctness fields and exit code 0 only for a valid baseline.

- [ ] **Step 1: Write failing dataset and empty-destination tests**

```python
def test_snapshot_records_hand_checked_size_distribution_and_hashes(tmp_path):
    write(tmp_path / "source/a", b"a")
    write(tmp_path / "source/b", b"bbb")
    manifest = snapshot_dataset(tmp_path / "source")
    assert manifest["FILE_COUNT"] == 2
    assert manifest["TOTAL_BYTES"] == 4
    assert manifest["MIN_FILE_SIZE"] == 1
    assert manifest["MAX_FILE_SIZE"] == 3
    assert manifest["files"][0]["sha256"] == hashlib.sha256(b"a").hexdigest()


def test_destination_guard_refuses_a_nonempty_directory_without_deleting_it(tmp_path):
    destination = tmp_path / "destination"
    write(destination / "keep.txt", b"keep")
    with pytest.raises(DestinationNotEmpty):
        ensure_empty_destination(destination)
    assert (destination / "keep.txt").read_bytes() == b"keep"
```

- [ ] **Step 2: Run helper tests and verify RED**

Run: `python -m pytest tools/test_fp_perf_run.py -q`

Expected: collection fails because `fp_perf_run.py` does not exist.

- [ ] **Step 3: Implement snapshot and destination guard**

Walk regular files only, store normalized relative paths, sizes, and SHA-256,
and use the same nearest-rank rule as the analyzer for median/p95. Reject fewer
than 35 or more than 50 files in CLI mode unless `--allow-test-count` is passed
by unit tests. `check-destination` may create the exact requested directory when
its parent exists; it never removes or truncates anything.

- [ ] **Step 4: Write failing correctness-gate tests**

```python
def test_verify_marks_exact_destination_valid(tmp_path):
    dataset, destination, events = exact_run_fixture(tmp_path, file_count=35)
    result = verify_dataset(dataset, destination, events, finder_error=None)
    assert result == {
        "EXPECTED_FILE_COUNT": 35,
        "ACTUAL_FILE_COUNT": 35,
        "BYTE_EXACT": True,
        "MISSING": [],
        "DIFF": [],
        "REFETCH_COUNT": 0,
        "-1005": 0,
        "-1004": 0,
        "-1000": 0,
        "Finder -36": 0,
        "PERFORMANCE_BASELINE_VALID": True,
    }


@pytest.mark.parametrize("mutation", ["missing", "different", "extra", "refetch", "-1005", "finder-36"])
def test_any_correctness_regression_invalidates_baseline(mutation, tmp_path):
    dataset, destination, events = exact_run_fixture(tmp_path, file_count=35)
    apply_mutation(mutation, dataset, destination, events)
    result = verify_dataset(dataset, destination, events, finder_error=None)
    assert result["PERFORMANCE_BASELINE_VALID"] is False
```

- [ ] **Step 5: Implement verification and CLI exit status**

Hash destination files by relative path. Read refetch and File Provider error
counts from `run.json`; accept Finder error observation only as explicit CLI
input. Missing evidence is `UNKNOWN` and invalidates the baseline instead of
being treated as zero.

- [ ] **Step 6: Run tool tests**

Run: `python -m pytest tools/test_fp_perf_run.py tools/test_fp_perf_metrics.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add tools/fp_perf_run.py tools/test_fp_perf_run.py
git commit -m "tools(fileprovider): enforce profiling correctness gate"
```

### Task 8: Full verification, diagnostic builds, one Finder run, and report

**Files:**
- Create at run time only: `scratchpad/fp-perf/2026-09-22-finder-01/dataset.json`
- Create at run time only: `scratchpad/fp-perf/2026-09-22-finder-01/extension.log*`
- Create at run time only: `scratchpad/fp-perf/2026-09-22-finder-01/mac-host.log*`
- Create when available: `scratchpad/fp-perf/2026-09-22-finder-01/windows.log*`
- Create at run time only: `scratchpad/fp-perf/2026-09-22-finder-01/run.json`
- Create at run time only: `scratchpad/fp-perf/2026-09-22-finder-01/per-file.csv`
- Create at run time only: `scratchpad/fp-perf/2026-09-22-finder-01/correctness.json`
- Create at run time only: `scratchpad/fp-perf/2026-09-22-finder-01/report.md`

**Interfaces:**
- Consumes all instrumentation and tools from Tasks 1–7.
- Produces the single authoritative Finder performance report and optional separate CLI comparison.

- [ ] **Step 1: Run the complete Python suite**

Run: `python -m pytest configurator/tests tests tools/test_fp_log_capture.py tools/test_fp_perf_metrics.py tools/test_fp_perf_run.py -q`

Expected: PASS. Report every pre-existing or unrelated failure by test name; do not hide it.

- [ ] **Step 2: Run the complete File Provider Swift suite**

Run:

```bash
cd configurator/fileprovider
xcodegen generate
xcodebuild test -scheme DuoInputFileProvider -destination 'platform=macOS' \
  CODE_SIGN_STYLE=Automatic DEVELOPMENT_TEAM=4YKVN22BMX
```

Expected: PASS.

- [ ] **Step 3: Audit the scoped diff for forbidden changes**

Run:

```bash
git diff bec1c22 -- \
  configurator/fileprovider/Shared \
  configurator/src/duo_input/clipboard/wire.py \
  configurator/src/duo_input/transfer/fileprovider_backend.py \
  configurator/fileprovider/Extension/EvictionCoordinator.swift
```

Manually verify that protocol files are unchanged and all backend/eviction
changes are event emission or dependency injection only. Also compare values of
`MAX_ACTIVE_FETCHES`, `MAX_TOTAL_BUFFERED_BYTES`, `FETCH_READ_TIMEOUT_MS`, and
all `EvictionCoordinator.Config` defaults with the spec-time values.

- [ ] **Step 4: Build and install the signed macOS diagnostic app**

Run:

```bash
FP_RUN_DIR=/Users/valik/Desktop/duo/input-duo/scratchpad/fp-perf/2026-09-22-finder-01
mkdir -p "$FP_RUN_DIR"
configurator/packaging/nuitka-build-macos.sh --skip-tests
codesign --verify --deep --strict configurator/dist/DuoInput.app
osascript -e 'tell application "DuoInput" to quit'
mv /Applications/DuoInput.app "$FP_RUN_DIR/DuoInput.app.before"
ditto configurator/dist/DuoInput.app /Applications/DuoInput.app
open /Applications/DuoInput.app
```

The move preserves the previous app as a recoverable run-scoped backup before
installing and launching the diagnostic build.
Do not remove/reset the File Provider domain, generation store, or eviction state.
Confirm the host and extension reconnect before starting the run.

- [ ] **Step 5: Prepare the representative dataset and new empty destination**

On the Windows source machine, run:

```powershell
$PerfSource = 'C:\DuoInputPerf\source'
$PerfDataset = 'C:\DuoInputPerf\dataset.json'
python tools/fp_perf_run.py snapshot --root $PerfSource --output $PerfDataset
```

Copy only `dataset.json` to the Mac run directory. Confirm its count is 35–50
and record the six required dataset aggregates. On Mac, run:

```bash
FP_DESTINATION=/Users/valik/Desktop/duo/fp-perf-destination-2026-09-22-finder-01
python tools/fp_perf_run.py check-destination --path "$FP_DESTINATION"
```

Do not delete or reuse a destination and do not start CLI copy activity.

- [ ] **Step 6: Start bounded capture and delimit the diagnostic run**

Ensure `tools/fp_log_capture.py` is running against `DuoInputFileProvider` and
record the current inode/size of its active and rotated files plus the Mac host
log files. Start the compatible Windows diagnostic build when available and
record its log paths. Publish the prepared Windows dataset once and note its
`transfer_id` from `fp_clipboard_armed`.

- [ ] **Step 7: Execute exactly one Finder Cmd+V**

Focus the new empty destination in Finder and press Cmd+V once. Do not cancel,
re-paste, manually evict, or run a CLI copy. Wait for Finder to finish and note
whether Finder displayed error `-36` or another error. Do not interpret the run
until correctness verification passes.

- [ ] **Step 8: Freeze logs and run the analyzer**

Copy the relevant bounded extension, Mac host, and optional Windows log
generations into the run directory without truncating originals. Run:

```bash
FP_RUN_DIR=/Users/valik/Desktop/duo/input-duo/scratchpad/fp-perf/2026-09-22-finder-01
FP_TRANSFER_ID=$(rg -o 'fp_clipboard_armed transfer_id=[^ ]+' "$FP_RUN_DIR"/mac-host.log* | tail -1 | cut -d= -f2)
FP_WINDOWS_ARGS=()
if [ -f "$FP_RUN_DIR/windows.log" ]; then
  FP_WINDOWS_ARGS=(--windows-log "$FP_RUN_DIR/windows.log")
fi
python tools/fp_perf_analyze.py \
  --extension-log "$FP_RUN_DIR"/extension.log* \
  --mac-log "$FP_RUN_DIR"/mac-host.log* \
  "${FP_WINDOWS_ARGS[@]}" \
  --dataset "$FP_RUN_DIR/dataset.json" \
  --transfer-id "$FP_TRANSFER_ID" \
  --output-dir "$FP_RUN_DIR"
```

Omit `--windows-log` when unavailable; the analyzer must then state
`WINDOWS_INTERNAL_BREAKDOWN = UNAVAILABLE`.

- [ ] **Step 9: Run the correctness gate and stop on failure**

Run:

```bash
python tools/fp_perf_run.py verify \
  --dataset "$FP_RUN_DIR/dataset.json" \
  --destination "$FP_DESTINATION" \
  --events "$FP_RUN_DIR/run.json" \
  --finder-error none \
  --output "$FP_RUN_DIR/correctness.json"
```

Use the actual Finder error argument, not `none`, if any error was observed. If
the command exits nonzero, set `PERFORMANCE_BASELINE_VALID = NO`, report the
correctness fields, and stop without bottleneck classification.

- [ ] **Step 10: Finalize Finder analysis and inspect all attributions**

Re-run the analyzer with `--correctness correctness.json`. Confirm:

```bash
python tools/fp_perf_analyze.py \
  --extension-log "$FP_RUN_DIR"/extension.log* \
  --mac-log "$FP_RUN_DIR"/mac-host.log* \
  "${FP_WINDOWS_ARGS[@]}" \
  --dataset "$FP_RUN_DIR/dataset.json" \
  --correctness "$FP_RUN_DIR/correctness.json" \
  --transfer-id "$FP_TRANSFER_ID" \
  --output-dir "$FP_RUN_DIR"
```

Then confirm:

- every file has a complete T0–T13 success trace or an explicit unavailable stage;
- all `read_id` pairs match exactly once;
- reported durations use one clock domain;
- total wall time equals earliest successful T0 to latest successful T13;
- active throughput uses the union of outstanding Mac read intervals;
- Finder and internal concurrency are separate;
- eviction classification follows the evidence rule;
- contributions plus explicitly unassigned residual explain the wall span.

- [ ] **Step 11: Optionally run one separate CLI control**

Only after a valid Finder report, use a different empty destination and the same
kind of dataless source items. Run one `cp` operation with no Finder copy active,
capture the same events, verify byte-exactness, and render a separate control
report. Skip this step if the same dataless-item conditions cannot be reproduced;
state `CLI_CONTROL = UNAVAILABLE` rather than substituting the historical 0.9
seconds per file.

- [ ] **Step 12: Deliver the stop report without implementing a fix**

Link `report.md`, `per-file.csv`, and `run.json`. State the required stop fields,
primary/secondary bottleneck, measured contributions, serialization verdicts,
TOP 5, no more than three measured candidates, and one recommended next
experiment. End the task and wait for a decision.
