# Global FILE_READ budget — runtime 4/6/8 results (2026-09-25)

Branch `feature/fileprovider-read-window-v2`, HEAD d90ba6c1 (GLOBAL_BUDGET 138d9e95 +
LIFECYCLE d90ba6c1). Production Nuitka bundle rebuilt from HEAD and installed (the
previously installed bundle pre-dated d90ba6c1). Procedure = runbook
`docs/superpowers/specs/2026-09-25-fileprovider-global-budget-runtime-runbook.md`,
unchanged, plus a 3rd block (B8 B6 B4 W1) agreed with the user because B6/B8 were
within noise after two blocks. No implementation changes.

Stand: Mac 192.168.0.252, Windows 192.168.0.128, 5 GHz 802.11ac.
Tooling (external observers, scratchpad/fp-budget-runtime/): `bench.sh`
(prep/precheck/collect), `watch_dest.py` (user-visible copy time: first dest entry →
all files final size, Finder in-progress birthtime cleared), `bench_metrics.py`
(extends tools/fp_window_metrics.py), `lifecycle_check.py`, `verify_copy.py`.

## Cold runs without eviction
28 byte-distinct dataset copies generated on Windows (`make-copies.ps1`): B-mixed-01..20
(100 files, 42,158,741 B each), L-single-51..58 (large-003, 8,543,456 B). Copy NN =
names prefixed `cNN__`, first 16 bytes of every 1 MiB block XOR NN. Per-copy sha256
manifests computed on Mac by the mirror script. Every measured run: precheck
NEW_IDENTITIES=YES, DATALESS=YES (0 fetch, 0 replica blocks), DEST empty, link attached;
every run then showed 100 fp_fetch_started / 131 FILE_READ for that generation.

## Network (iperf3 Windows→Mac, receiver)
block1 before 65.1 / after 70.8; block2 before 60.9 / after 69.3;
block3 before 73.6 / after 64.2 Mbit/s.

## Mixed workload (14 runs, all 100/100 byte-exact)
| cfg | runs (uvt s) | median uvt | thr MB/s | fetch win | large fetch p50/p95 ms | large r2c p50/p95 ms | small fetch p50/p95 ms | sched wait p50/p95 ms | maxG | maxPF |
|---|---|---|---|---|---|---|---|---|---|---|
| W1 | R01 10.91, R10 11.69, R14 13.08 | 11.69 | 3.61 | 8.52 | 2739/3234 | 420/552 | 17/204 | 2/4 | 4 | 1 |
| B4 | R02 12.42, R09 11.52, R13 12.73 | 12.42 | 3.39 | 8.73 | 2908/3184 | 433/606 | 18/220 | 3/198 | 4 | 4 |
| B6 | R03 11.07, R08 10.99, R12 13.49 | 11.07 | 3.81 | 8.21 | 3147/3296 | 657/808 | 18/227 | 2/7 | 6 | 4 |
| **B8** | R04 10.61, R07 11.32, R11 10.45 | **10.61** | **3.98** | 7.92 | 3097/3136 | 830/973 | 17/323 | 2/6 | 8 | 4 |
| W4UNB | R05 11.04, R06 11.49 | 11.26 | 3.74 | 7.75 | 2976/3178 | 1347/1860 | 18/240 | 2/6 | 16 | 4 |
(per-config values = median of per-run p50/p95.)

Within-block rank among B4/B6/B8/W1: B8 1/2/1, B6 3/1/4, W1 2/4/3, B4 4/3/2.
B8 vs W1 paired per block: −2.7%, −3.2%, −20.1% time. B8 vs W4UNB (blocks 1,2): −3.9%, −1.5%.
Medians: B8 vs W1 −9.3% time (+10.2% thr); vs W4UNB −5.8%.

Observations
- Large-file r2c scales with global outstanding reads (≈420 → 433 → 657 → 830 → 1347 ms
  for 4/4/6/8/16): the over-subscription queueing is real and the budget caps it.
- The historical end-to-end mixed W4 regression (−14.7%) did NOT reproduce on this
  network: W4UNB ≈ W1. Differences between configs are of the order of run noise;
  largest noise sources are outside the host: Finder tail after the last fetch
  (2.5–4.2 s) and idle gaps with 0 active fetches (R12: 40% of window).
- B4 is the worst end-to-end: the budget itself becomes the bottleneck (scheduler
  wait p95 ≈ 200 ms, fetches queue for permits).

## Isolated large file (ABBA, 8.5 MB)
W1 I1 1.070 s, I4 1.302 s (mean 1.186 s); B8 I2 1.025 s, I3 1.111 s (mean 1.068 s).
GAIN +11.0% throughput; B8 MAX_PER_FETCH = 4 (window full ≈78% of time). The global
budget does not collapse an isolated fetch to W1. Gain is smaller than historical
+35.7% because base r2c is now 123–150 ms (was 514 ms).

## Safety (all runs)
BYTE_EXACT 1400/1400 mixed + 4/4 isolated + D1 100/100 + D2R 100/100.
Duplicate/overlapping ranges 0; invalid late acceptance 0; refetch/duplicate fetch 0;
permit leaks 0; bound violations 0 (maxG never exceeded 4/6/8/16; per-fetch ≤ window).

Cancellation (B8, C1 + C2): Finder stop does NOT propagate to fetchContents —
fileproviderd finishes the in-flight downloads (C2: all 100 fetched into the replica,
Finder kept 61/61 byte-exact, removed the rest); no cancel reached the extension.
NEW_READS_AFTER_CANCEL 0, INVALID_WRITES 0, DOUBLE_COMPLETION 0, PERMIT_LEAKS 0.
The host cancel path (fp_cancel_outstanding) is NOT exercised at runtime by Finder;
covered only offline.

Disconnect/retry (B8):
- D1: 10 s Wi-Fi outage < 30 s silence/read limits → TCP survived, 15.4 s stall, then
  resumed; 100/100 byte-exact, no failure, no leak, no deadlock.
- D2: ~40 s outage → silence drop at +30 s, 4 in-flight fetches failed (attempt A).
  After Windows re-attached, extension auto-retried the old generation: every retry
  failed ≈10 ms after FILE_READ (source refuses the pre-drop generation; host does
  not log the FILE_ERROR reason). Retry loop is bounded (stopped after ~2.5 min).
  STALE_ATTEMPT_A_READS_ACCEPTED 0, DOUBLE_COMPLETION 0, PERMIT_LEAKS 0, DEADLOCK NO.
- D2R: fresh copy of the same set in the SAME host process after the drop:
  100/100 byte-exact, MAX_GLOBAL = 8 (full budget available ⇒ no leaked permits).

## Verdict
BEST_FIXED_CONFIGURATION = PER_FILE_READ_WINDOW 4, GLOBAL_READ_BUDGET 8.
Root-cause hypothesis: PARTIAL — over-subscription confirmed in telemetry and bounded;
the large end-to-end regression it was blamed for is not reproduced on this network.
Follow-ups (out of scope here): make 8 the production default; old-generation retry
after a silence drop is refused by the Windows source (log FILE_ERROR reason on host);
host cancel path has no runtime trigger via Finder.
