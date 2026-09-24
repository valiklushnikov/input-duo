# Transport-only FILE_READ Window Experiment Plan

**Goal:** Compare one versus four same-file `FILE_READ` requests in flight over the real Duo Input TLS transport, without changing production transfer behavior.

**Architecture:** Add a diagnostic-only two-role harness under `configurator/tests/transfer`. The Windows role uses the production `PeerListener`, `PeerLink`, `FileTransferService`, and `SnapshotRegistry` to offer one deterministic 128 MiB file. The macOS role uses the production `PeerLink` and wire codec, but a purpose-built bounded requester that runs A1/B1/A2/B2 in one connection, matches responses by the existing files/2 correlation fields, writes by offset, validates every range, and emits JSON metrics. Production File Provider and `FileTransferService.request_read()` remain unchanged.

**Tech stack:** Python 3.12, PySide6/QtNetwork, pytest, existing files/2 protocol.

## Constraints

- Change only diagnostic files and tests.
- Keep chunk size at 1 MiB and socket/TLS configuration unchanged.
- Do not change Model C, File Provider scheduling, pipeline depth, wave size, or eviction.
- Do not change the files/2 wire protocol: it already supplies `transfer_id`, `entry_index`, `read_id`, and `offset`.
- Run all four samples on one connection and one Windows snapshot.
- Bound requested but unanswered data to `window * 1 MiB`.

## Task 1: RED tests for the bounded requester

- [ ] Add tests for the configured window bound.
- [ ] Add out-of-order response matching.
- [ ] Add partial final-chunk coverage.
- [ ] Add duplicate and unknown response rejection.
- [ ] Add correlated `FILE_ERROR` handling.
- [ ] Add disconnect cancellation.
- [ ] Run focused tests and confirm RED.

## Task 2: Implement the diagnostic state machine

- [ ] Implement immutable chunk/range accounting and unique read IDs.
- [ ] Track outstanding-count occupancy by monotonic time.
- [ ] Track per-request RTT and response arrival spacing.
- [ ] Write received chunks by offset and calculate final SHA-256.
- [ ] Reject gaps, overlaps, duplicates, unexpected chunks, and malformed sizes.
- [ ] Keep the implementation independent of production receiver state.
- [ ] Run focused tests and confirm GREEN.

## Task 3: Implement the real cross-host harness

- [ ] Add Windows `serve` role with production listener/link/service/snapshot path.
- [ ] Generate/reuse one deterministic 128 MiB file and publish it once.
- [ ] Add macOS `run` role that executes windows `1,4,1,4` sequentially.
- [ ] Record link queue high-water marks and run markers/read-id ranges.
- [ ] Persist raw JSON and human-readable summaries.
- [ ] Add command-line validation and smoke tests.

## Task 4: Verify and review

- [ ] Run focused diagnostic tests.
- [ ] Run transfer test suite.
- [ ] Run a loopback smoke test through real PeerLink/TLS/FileTransferService.
- [ ] Review the diff for production imports or behavior changes.
- [ ] Commit and push the diagnostic harness.
- [ ] Provide exact Windows and macOS commands for A1/B1/A2/B2 collection.

## Task 5: Analyze the cross-host run

- [ ] Validate byte/range correctness for all four runs.
- [ ] Join Windows send events only by correlation IDs; do not subtract clocks.
- [ ] Report all raw runs and the median throughput comparison.
- [ ] Classify the stop-and-wait hypothesis using the specified thresholds.
- [ ] Stop without production windowing.
