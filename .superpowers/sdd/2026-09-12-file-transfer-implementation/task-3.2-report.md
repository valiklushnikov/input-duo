# Task 3.2 report: the measurement decision gate (reduced form)

**Status: DONE_WITH_CONCERNS.** Both gates were addressed. One closed
completely on measurement (§7, the disk read). The other could not be closed
and is recorded as inapplicable rather than passed or failed (§8, the window).
No window was added. One mid-task correction from the controller materially
changed the analysis and is documented below.

## Verification of the controller's premises before building on them

Every claim I was told to verify, I verified in the code:

| claim | verdict |
|---|---|
| `_measure` creates both identities locally | confirmed, `spike_measure_bridge.py:371-372` (pre-change numbering) |
| listens/connects on `127.0.0.1` | confirmed, `:386` |
| both `FileTransferService` instances in one process | confirmed, `_measure` builds `sender` and `receiver` in the same function |
| CLI has only `--size-mib` / `--cancel-after-mib` | confirmed, `_build_parser` |
| `READ_CHUNK_BYTES = 65536` | confirmed, `:66` (now `:72`) |
| `rtt_bound_mib_s` computes `bytes_per_chunk / RTT` | confirmed, `:145-154` |
| `_percentile` returns the max for n ≤ 100 | confirmed: `index = min(len(ordered)-1, int(len(ordered)*fraction))`; at n=100, `int(99.0)=99` = last index |
| the 0.8 ms crossover arithmetic | **my arithmetic agrees**: `0.0625 / 78.4 = 0.7972` ms |

## Commands run, and the captured logs

```
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe configurator/tests/transfer/spike_measure_bridge.py \
    --size-mib 2048 > configurator/tests/transfer/measure-plain.log 2>&1
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe configurator/tests/transfer/spike_measure_bridge.py \
    --size-mib 2048 --cancel-after-mib 512 > configurator/tests/transfer/measure-cancel.log 2>&1
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe configurator/tests/transfer/spike_measure_bridge.py \
    --size-mib 2048 --read-chunk-bytes 262144 > configurator/tests/transfer/measure-plain-256k.log 2>&1
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe configurator/tests/transfer/spike_measure_bridge.py \
    --size-mib 2048 --cancel-after-mib 512 --read-chunk-bytes 262144 > configurator/tests/transfer/measure-cancel-256k.log 2>&1
```

All four at the brief's full 2048 MiB — nothing was reduced. All four exited 0.
All four logs are committed.

**Codepage:** captured via Bash with `PYTHONIOENCODING=utf-8`, not PowerShell
redirection, so the UTF-8 bytes pass through unre-encoded. `file` reports
`UTF-8 text` on each, and I read each capture back and confirmed the Cyrillic
was intact **before** drawing anything from it. No run failed or raised.

## The finding that reframed the task

The controller caught this mid-task and it is the most important result here.

`READ_CHUNK_BYTES = 65536` is **the harness's own constant**, not a property of
the system. I verified the whole chain:

- `MAX_FILE_CHUNK_BYTES = 1_048_576` (`clipboard/wire.py:46`) — 16× larger.
- Production's chunk size is chosen by **Explorer**: `PipeStream._read`
  (`windows_com.py:635`) takes `cb` from the COM caller, computes
  `want = min(int(cb), remaining)`, and passes exactly that `want` to
  `self._request` (`:657`).
- `request_read` clamps only at the frame ceiling (`service.py:318`,
  `effective_length = min(length, MAX_FILE_CHUNK_BYTES)`), and 262144 < 1048576,
  so the reader's request size **is** the wire chunk size, one-for-one.
- Explorer's real `cb` was already measured at **262144**, "постоянно, без
  исключений", on every logged read across runs A/B/C
  (`records/2026-09-12-explorer-virtual-files-spike.md:463-465, 581`). The spec
  carries the number in §22 question 2.

So the first pass measured at a quarter of production's chunk size, and both
sides of the gate's `bytes_per_chunk / RTT` vs throughput comparison scale with
the chunk. **Third instance of this defect class in this plan**, after
`GetAsyncMode` and the stale `.pyc`.

I added `--read-chunk-bytes` (default 65536, so the eight mandated tests do not
move) and re-measured at 262144. I also made the chunk size **print in the
table**, because the discrepancy being invisible in the output is what let it
through.

**I proved the flag is not silently ignored** rather than assuming it — by
intercepting `FileTransferService.request_read` and recording the actual
lengths on the wire:

```
requested cb=65536:  observed FILE_READ lengths {65536: 64}   rtt_median=1.56 ms
requested cb=262144: observed FILE_READ lengths {262144: 16}  rtt_median=4.97 ms
```

16 reads of exactly 262144 on 4 MiB, none of any other length. And the RTT
moved, which is itself the warning the controller gave: **RTT is not
independent of the chunk size.**

## Step 2 table (production chunk 262144, plain run, loopback)

| | value |
|---|---|
| LAN ceiling, plain TCP | **НЕ ИЗМЕРЕНО — требует второй машины** |
| our throughput, one read in flight | 54.1 MiB/s (loopback) |
| **ratio (ours / ceiling)** | **НЕ ИЗМЕРЕНО — требует второй машины** |
| request/response RTT, median and p99 | 3.65 / 5.15 ms |
| sender disk-read latency per chunk, median and p99 | 0.16 / 0.29 ms |
| GUI tick p99 / max under load | 16.9 / 17.8 ms (16 ms nominal) |
| socket `bytesToWrite` maximum | 9 B |
| TLS/PeerLink throughput with the pipe bypassed | 91.9 MiB/s |
| peak Python memory | 3.5 MiB tracemalloc; peak RSS 76 MiB |
| pipe high_water | 1 chunk |
| cancel latency | 0 ms |
| largest heartbeat gap | 10.0 s against `HEARTBEAT_MS = 10_000` |

The 64 KiB run, kept because the discrepancy between the two is the finding:
throughput 32.6 MiB/s, RTT 1.51 / 2.48 ms, disk 0.05 / 0.13 ms, bypassed
84.3 MiB/s, tick 16.6 / 17.3 ms, `bytesToWrite` 0 B, high_water 1, RSS 94 MiB.

### Establishing what each zero/absent reading actually is

Per the standing lesson — an absence is only evidence if presence prints:

- **`bytesToWrite` 0 B at 64 KiB.** Now settled, and only the 262144 run could
  settle it: **the same sampler reported a nonzero 9 B there.** Presence prints,
  so absence is evidence. Second possible cause also excluded: `_sample()` skips
  the read when `outgoing.is_open` is false and records it in
  `link_dropped_during_sampling` (`:601-606`), which `_run` raises on (`:734`);
  all four runs exited 0, so the guard never fired and every sample really read
  `bytes_to_write` (~758 samples at 50 ms over 37.9 s). Diagnosis: the socket
  was never the bottleneck. 9 B is not even a full frame header.
- **`p99 == max` does not appear in any of the four runs** — and I checked the
  sample counts rather than treating divergence as meaningful: 3925 / 975 /
  2369 / 625 tick samples, all well past the n ≤ 100 threshold. p99 is a real
  percentile in all four.
- **Heartbeat 10.0 s** is exactly `HEARTBEAT_MS = 10_000`
  (`clipboard/service.py:27`), verified in code — nominal, not stretched. The
  262144 cancel run reads "отправлено 1, пришло 1, не измерялись": one PING in
  10.0 s, and one PING has no gap after it. Benign, and not starvation — the
  guard fires on `sent > 1 and received == 0`, here 1/1.
- **Cancel latency 0 ms**, not "не измерялась" — the path ran for real and the
  table prints the two cases differently.

## Step 4 verdict: CLOSED, on measurement, completely

**The sender's disk read stays on the GUI thread.** §7's escape hatch is not
taken.

Numbers: tick p99 16.9 ms, max 17.8 ms against a 16 ms nominal timer — 0.9 and
1.8 ms of excess. The disk read itself is 0.16 / 0.29 ms, i.e. 3.5% of the
4.62 ms per-chunk cycle. A starved loop shows hundreds of ms, not 17.

**Why loopback is the pessimistic case here, so the conclusion must not later
be discounted as loopback-only.** The gate asks a property of *this process*,
not of the network, and two independent effects make this run harder than
production:

1. This one process runs both `FileTransferService` instances, both `PeerLink`s
   (hence **both** halves of TLS), the emulator thread and the instrument, all
   on one GIL. In production each machine runs one half.
2. It ran at a higher Qt event rate than production can reach. GUI load scales
   with chunks/second:

   | run | chunks/s |
   |---|---|
   | 262144, measured | 216.4 |
   | 262144, ceiling implied by 91.9 MiB/s transport | 367.6 (max possible) |
   | **65536, measured** | **521.6** |

   The 64 KiB run drove **521.6 chunks/s — above the 367.6/s that is even
   possible at Explorer's chunk size** — and posted tick p99 of **16.6 ms**,
   i.e. *better* than the slower run. Event rate rose 2.4× with no degradation.

So the GUI thread was measured above production's reachable event rate, doing
more work per event, and stayed within 17.0 ms p99 across all four runs. That
is a closure on measurement, not a projection.

## Step 3: which row fired, and the three numbers

The formal gate is **inapplicable**: no plain-TCP LAN ceiling, so no ratio.
Localisation, however, is internally valid on loopback (it answers "where is
the limit", not "what is the absolute speed"), so I did it.

**The three numbers, at the production chunk 262144:**

| | |
|---|---|
| our throughput | 54.1 MiB/s |
| `bytes_per_chunk / RTT` = 0.25 MiB / 3.65 ms | **68.49 MiB/s** |
| bypassed transport | 91.9 MiB/s |

ours / rtt_bound = **79.0%**; rtt_bound / bypassed = **74.5%** (was 49.1% at
64 KiB); ours / bypassed = **58.9%**.

**Row 1 fired — "RTT dominates" — but materially weaker than at the harness's
chunk, and that weakening is itself a finding.** Row 1 needs both
`bytes_per_chunk / RTT ≈ our throughput` (holds: 68.49 vs 54.1, ratio 1.27, and
these two are closer to each other than either is to 91.9) **and** "bypassed
TLS throughput far higher" (holds only **weakly**: 1.34×, where at 64 KiB it
was 2.0× and "far higher" read confidently). At Explorer's real chunk the
round trip is still the leading single constraint, but it is nearly co-limited
with the transport.

Every other row did **not** fire, each by measurement: disk p99 is 8.0% of RTT;
tick is 16.9/17.8 against 16 ms; `bytesToWrite` max 9 B and the bypassed
transport is the highest number in the run; heartbeat gap is exactly nominal.

### The decomposition, since RTT is not chunk-linear

Model `RTT(cb) = fixed + transport(cb) + disk(cb)`, with `transport(cb)` taken
from the bypass pass **at the same cb**:

```
64 KiB:  1.51 = fixed + 0.7414 + 0.05  -> fixed = 0.7186 ms
256 KiB: 3.65 = fixed + 2.7203 + 0.16  -> fixed = 0.7697 ms
```

Two **independent** estimates of the fixed term agree to 7%. Cross-check:
predicting the 256 KiB RTT from the 64 KiB run alone gives
`0.7186 + 2.7203 + 0.16 = 3.599` ms against **3.65** measured — **1.4% error**.
That agreement is what makes this a model rather than a guess, and it confirms
the controller's warning not to rescale linearly (4× the chunk gave 2.4× the
RTT, not 4×).

Per-chunk cycle at 262144 (cycle = 4.621 ms):

| component | ms | share |
|---|---|---|
| transport (payload-proportional) | 2.720 | **58.9%** |
| fixed round-trip overhead | 0.770 | 16.7% |
| disk read | 0.160 | 3.5% |
| residual outside RTT (pipe/IStream handoff) | 0.971 | 21.0% |

The largest single term is **the transport**, not round-trip overhead. That
bounds any window: it can only recover the serialization — fixed overhead plus
handoff, ≈38% of the cycle, so at most **1.70×** — and cannot make the
transport faster. A perfect window asymptotes to 91.9 MiB/s.

## The RTT crossover arithmetic, shown

```
assumed practical gigabit ceiling = 112 MiB/s     (NOT MEASURED - no second machine)
70% bar                           = 0.70 * 112 = 78.4 MiB/s
chunk (Explorer's real cb)        = 262144 B = 0.25 MiB
required RTT = 0.25 MiB / 78.4 MiB/s = 0.0031888 s = 3.1888 ms
measured RTT (loopback, 262144)   = 3.65 ms
-> bar not met, shortfall 14.5%
```

**My arithmetic agrees with the controller's at 64 KiB** (`0.0625 / 78.4 =
0.7972` ms). But the crossover **scales with the chunk**, and quoting it at the
harness's chunk distorts the conclusion by about 7×:

| chunk | required RTT | measured RTT | shortfall |
|---|---|---|---|
| 65536 (harness) | 0.7972 ms | 1.51 ms | **89%** |
| **262144 (Explorer)** | **3.1888 ms** | **3.65 ms** | **14.5%** |

"Structurally unreachable, by 2×" was an artifact of the wrong chunk. At the
real chunk we miss by 14.5% — a qualitatively different conclusion.

### Where the 3.65 ms actually lives — correcting the controller, as instructed

The controller's original premise was loopback RTT ≈0.1 ms. **Measurement does
not support that, and the controller retracted it.** Loopback's network
component is ~0.05 ms; the measured 3.65 ms is almost entirely **our own
stack** — the queued `invokeMethod` hop, service processing, framing, TLS,
socket, parse, signal emission, and the pipe handoff back to the COM thread.
The decomposition localises it: 2.72 ms transport, 0.77 ms fixed, 0.16 ms disk.

So **the 3.19 ms crossover is unreachable because of us, not because of the
network.** The network is a minority contributor. The controller's asymmetry
claim stays directionally true but was misleading about magnitude, and the
record says so.

### Asymptote: what one read in flight can ever reach

By the validated model `RTT(cb) = 0.770 + (cb/MiB)*1000/91.9 + (cb/MiB)*0.64`:

| cb | RTT | one-read bound | vs 78.4 bar |
|---|---|---|---|
| 64 KiB | 1.49 ms | 41.94 MiB/s | below |
| **256 KiB** | **3.65 ms (measured)** | **68.49 MiB/s** | **below** |
| 512 KiB | 6.53 ms | 76.56 MiB/s | below |
| 1024 KiB (`MAX_FILE_CHUNK_BYTES`) | 12.29 ms | 81.36 MiB/s | **clears** |

All rows but 256 KiB are **projection, marked as such**. One read in flight is
therefore **not structurally capped below the bar** — it clears it at the 1 MiB
protocol ceiling. But getting there means reading further ahead than Explorer
asked for, which *is* read-ahead, and therefore belongs to the window task with
the same `Seek` invalidation rule. There is no free lever.

## The window decision

**No window added. No prefetch logic added.** The basis is not a convenient
number: the §8 gate rests on a ratio to the plain-TCP ceiling of the same
network, and that number does not exist here. §8 fixes the threshold in advance
precisely so an inconvenient measurement cannot be reinterpreted as a licence,
and permits a window only together with the generation/read-token rule.

Recorded conditions under which this would stop holding: a two-machine run
showing ratio < 70% **and** localisation still pointing at the round trip; the
bar needs RTT ≤ 3.1888 ms at chunk 262144 against 3.65 ms measured; and even
then the payoff is capped at 1.70× against a 91.9 MiB/s transport ceiling.

Finding carried forward for that future task, not acted on here: the
better-return lever may be a larger request rather than pipelining several,
since fixed overhead is only 16.7% of the cycle while transport is 58.9%. Both
are read-ahead; both need the `Seek` rule.

## What I wrote, and where I adapted prescribed wording

**Record (new):** `docs/superpowers/records/2026-09-12-file-transfer-bridge-measurement.md`
— environment, both methodology corrections, verbatim numbers from all four
runs, the Step 2 table, Step 4's closure with the pessimistic-case reasoning,
Step 3's localisation and budget, the crossover arithmetic, the conditional
window decision, and "Что этот прогон не может сказать никому".

**Spec amendments** (`docs/superpowers/specs/2026-09-12-file-transfer-design.md`):

- **§7** — disk-read paragraph gains the measured verdict and the
  pessimistic-case argument. Escape hatch marked not taken.
- **§8** — `256 KiB × 8` "гипотеза" replaced; measured numbers added; the
  missing ratio named explicitly with what closes it; localisation and the
  cycle budget recorded; a warning about measuring at the wrong chunk. Memory
  ceiling marked confirmed by measurement (`high_water = 1`, 76 MiB RSS on
  2048 MiB).
- **§8 threshold** — marked **"Статус порога: НЕ ПРИМЕНЁН"**, with 70%
  explicitly not moved.
- **§22 question 2** — **kept open**, narrowed, with the two-machine run named
  as the closer.

**Where I adapted prescribed wording, and why.** Three places — **but item 1
below is WITHDRAWN; see the fix report's opening. It was not an adaptation,
because the brief never told me to close §22 q2 unconditionally. Left in place
rather than rewritten, so the withdrawal has something to point at.**

1. ~~**The brief's Step 6 told me to close §22 question 2**~~ — **withdrawn.**
   The brief says "Remove §22 question 2 **if it closed**", under the ≥70%
   branch that never fired, so keeping it open was plain compliance and no
   adaptation was involved. I misread the instruction in my own favour. What
   remains true is the substance: the number the gate asks for does not exist,
   so I replaced the hypothesis with what *was* established and kept the
   question **open**, naming what closes it. The brief's verification grep
   returns one hit that reads as settled-with-a-named-gap, satisfying "hits
   that now read as settled rather than open".
2. **The brief's commit message** says "Measured over the real LAN rather than
   loopback". That is false for this run, so I wrote my own message saying
   loopback on one machine, why, and that the threshold was not applied. A
   commit message that overclaims is the same defect as a report that does.
3. **Two adjacent clauses the brief did not name** but which my edits would
   have left false — the reason for checking either side of a claim:
   - **§20's spike 2 section** listed "throughput at one read in flight — this
     *is* the §8 decision" while the same paragraph prescribes **loopback**.
     That is the contradiction this task hit, and it originates **in the spec**,
     not in the harness: the harness was built exactly to that paragraph, and
     task 3.2's brief then demanded a real LAN it cannot do. Recorded there.
   - **§7's cross-reference** ("Если спайк 2 покажет, что окно необходимо") was
     future-tense about a spike that has now happened without showing it.
     Updated, keeping the conditional for a future two-machine run.
   - **§22's intro** said question 2 "остаётся открытым до спайка 2" — stale
     once spike 2 ran without closing it. Updated.

## Files changed

- `docs/superpowers/records/2026-09-12-file-transfer-bridge-measurement.md` (new)
- `docs/superpowers/specs/2026-09-12-file-transfer-design.md` (§7, §8, §20, §22)
- `configurator/tests/transfer/spike_measure_bridge.py` — `--read-chunk-bytes`,
  `Measurement.read_chunk_bytes`, table row, two validation guards, threading
  through `_measure`/`_run`/`_measure_bypassed_transport`
- `configurator/tests/transfer/measure-plain.log`, `measure-cancel.log`,
  `measure-plain-256k.log`, `measure-cancel-256k.log` (new, raw evidence)
- `configurator/tests/transfer/test_spike_measure_bridge.py` — **unchanged**

**No production code changed.** The harness is a spike file under `tests/`.

Commit: `ab50605` "Measure the bridge on loopback and leave the prefetch
question open".

## Self-review findings

- **The eight mandated tests did not move**: `test_spike_measure_bridge.py` is
  byte-for-byte unchanged and passes 8/8 against the modified harness. The new
  field is defaulted to `READ_CHUNK_BYTES`, so the tests' fixture — which does
  not set it — still constructs a `Measurement`, and the default is *truthful*
  rather than a sentinel `0` that would print as "0 B" and be its own
  silent-default defect.
- **Full gate re-run: `1922 passed, 8 skipped`** — identical to the 3.1
  baseline. No test added, correctly: the change is a spike-file parameter.
- **I proved the new flag does something** instead of assuming it, by observing
  actual `FILE_READ` lengths on the wire. This is the same discipline the
  chunk-size defect itself violated.
- **Two stale comments my own change created, found and fixed**: the bypass
  pass's comment still said "фиксированный буфер в `READ_CHUNK_BYTES`" after
  the buffer became a parameter; and `_generate`'s `take = min(READ_CHUNK_BYTES,
  remaining)` now carries a note that it is **deliberately** not the read size
  (file block size, independent of read size — the digest is over the whole
  stream, empirically confirmed by the 262144 runs passing the digest check
  while generating in 65536 blocks). Without that note a later reader would
  plausibly "fix" it to track the flag.
- **The 64 KiB runs were kept, not discarded.** They are load-bearing twice
  over: they supply the second independent estimate that validates the RTT
  decomposition, and they provide the 521 chunks/s data point that closes Step 4
  on measurement rather than projection.
- **Every unmeasured row is marked "НЕ ИЗМЕРЕНО — требует второй машины"** —
  never 0, blank, or "N/A".

## Issues and concerns

1. **§8's gate remains unapplied, and no work on this machine can apply it.**
   This is the headline limitation. It needs one two-machine run; task 4.3
   already requires two machines, so the opportunity exists.
2. **The loopback-vs-LAN asymmetry is not one-directional, contrary to the
   original framing — including my own first reading of it.** Two effects pull
   opposite ways: loopback omits physical transit (real LAN RTT higher), but
   loopback pays **both** TLS halves on one GIL (real per-machine CPU lower).
   Neither magnitude is measured, so **the sign of the net difference is
   undetermined**. In particular the 91.9 MiB/s transport figure is plausibly an
   *under*estimate of a two-machine ceiling. This weakens the "loopback can
   show a window might be needed but never that it isn't" claim to: this run
   cannot establish the direction at all. The outcome is unaffected — the gate
   lacks its denominator either way — but the reasoning in the record is
   corrected rather than inherited.
3. **Our software transport ceiling (91.9 MiB/s) sits below a gigabit wire
   ceiling (~112 MiB/s).** On gigabit the binding constraint would likely be our
   own TLS/framing, not the network. Unverifiable without a second machine, but
   it bears on how the future two-machine run should be interpreted: a poor
   ratio there may indict our transport rather than the round trip.
4. **One run per configuration.** No variance, no confidence intervals. The
   7% agreement of the independent fixed-overhead estimates and the 1.4%
   prediction accuracy are indirect evidence of stability, not a substitute for
   repeats.
5. **The `open_pipe`-on-a-foreign-thread defect is reproduced, not avoided**
   (task 3.1 self-review). All numbers are from the architecture *with* that
   defect.
6. **Explorer was not involved.** The request size now matches its measured
   `cb`, but its cadence, three `Seek`s per entry, and concurrent `GetData` on
   other formats are still absent. Task 4.3's business.
7. **A pre-existing harness concern I did not fix**, noted in 3.1 and still
   true: the `link_dropped_during_sampling` and `emulator_error` raises sit
   before the `try:` whose `finally` calls `call_release`, so those error paths
   leak the COM `IStream`. Out of scope here and it never fired in these runs
   (all four exited 0), but it is still there.

---

## Fix report (quality review round 1)

3 Critical and 3 Important, all of them claims in the record or spec that said
more than the measurement supports. All six fixed. Four of the seven deferred
Minors folded in, plus the reviewer's invariance finding. No measurement was
re-run to change a number: every headline figure stands as committed, and the
reviewer independently recomputed all of them plus verified the intervals via
`PINGs == ceil(elapsed/10)` at `HEARTBEAT_MS = 10_000` in all four runs.

**On §22 q2 I was wrong about the brief, and the reviewer is right.** I reported
that I had "adapted prescribed wording" because the brief told me to close the
question. It does not: it says "Remove §22 question 2 **if it closed**", and
that instruction sits under the >=70% branch, which never fired. So nothing was
overridden and no adaptation was needed — keeping the question open was simply
following the brief. I misread it in my own favour, which is the more
uncomfortable direction to get wrong, and the claim of an adaptation is
withdrawn. The other two adaptations I reported (the commit message, and the
three stale adjacent clauses) were real.

### CRITICAL 1 — spec asserted the decision rests on evidence; the record says the opposite

`spec:577-580` read "окно остаётся не-инвариантом, и **теперь по evidence, а не
по осторожности**", while `spec:592-593` says the threshold was never applied
and `record:507-513` says the decision rests on the gate's missing denominator
and §8's rule. The overclaim sat in the lead paragraph of §8's flow section —
the sentence a later reader quotes — and in the document that survives, denied
only in the one that expires.

Fixed with the reviewer's wording: the window stays a non-invariant, "теперь
измерено, **что́ именно** ограничивает передачу, но **порог §8 остался
неприменим**", with the basis stated explicitly as §8's rule and the absent
number, "**а не** на измеренную достаточность".

### CRITICAL 2 — the §7 closure rested on page-cache reads

The disk figures are 0.16 / 0.29 ms. The 2048 MiB file was written by the same
process seconds earlier on a 15.9 GB machine, so it was largely still in the
Windows page cache, and **`262144 B / 0.16 ms` = 1.6 GB/s is a cache speed, not
a disk one** — no medium in this machine delivers that. So
`SnapshotRegistry.read` barely touched the device.

**The controller has stated this was its error, and I am recording that, but
the part I own is that I wrote it into the spec.** The instruction was "Step 4
closes COMPLETELY on loopback, and loopback is the pessimistic case". That is
true for the **GUI ticks** — higher loopback throughput means more work per
second on the Qt thread, so acceptable ticks there imply acceptable ticks on a
slower link — and that half stands unchanged. It is **false for the disk figure
itself**, where a warm cache makes loopback optimistic. The generalisation from
one half to the whole gate came from the instruction; I did not catch it, and
"закрыт полностью" was mine.

Fixed in three places, numbers untouched:
- **spec §7** gains a clause: medium and cache state were not varied; the
  closure covers a locally generated, cache-warm file on this machine's
  storage; an external drive, network share or cold spindle costs tens of ms
  for a 256 KiB read and is "ровно тот случай, для которого запасной выход и
  назван". The hatch is now "не задействован **на этом классе источников**".
- **record** — the step 4 heading changed from "закрыт полностью" to "с
  названной областью", with a subsection carrying the 1.6 GB/s arithmetic, and
  a new limits item 8.
- **record conclusion table** — step 4's row now separates the tick half
  (holds firmly) from the disk figure (cache-warm, medium not varied).

### CRITICAL 3 — model residuals stated in the spec as measured percentages

`spec:600-603` gave "транспорт 58.9%, постоянные накладные круга 16.7%, диск
3.5%, остаток 21.0%" with neither "модель" nor "проекция" anywhere in §8. Only
`RTT`, `transport` and `disk` are measured; **16.7% and 21.0% are residuals of
an assumption**, and a reader designing the window task would have cited
"накладные круга 16.7%" as a measured fact.

Fixed by prefixing with the reviewer's clause — "по модели
`RTT = fixed + transport(cb) + disk`, где `transport` взят из обойдённого
прохода" — and adding that `fixed` "является её **остатком**, а не измеренной
величиной", with the two residual shares named explicitly as "residuals модели,
не наблюдения".

### IMPORTANT 1 — the central verification was the one thing uncommitted

The proof that `--read-chunk-bytes` reaches the wire lived only in a session
transcript, in a record whose own thesis is that an instrument must be shown to
be reading the system. The reviewer's point is sharp: the log's chunk-size row
is **a print of the CLI argument, not an observation**.

Took the better of the two options offered and committed it:
`configurator/tests/transfer/spike_measure_checks.py --verify-chunk-size`, with
output in `measure-chunk-size-verify.log`. It intercepts
`FileTransferService.request_read`, records the actual lengths, and **raises if
the observed set differs from the expected one** rather than printing it
quietly. Observed `{262144: 16}` on 4 MiB, and `{65536: 64}`. The record now
also names the independent corroboration — the
`windows_com.py:642 -> :657 -> service.py:318` forwarding chain and the
three-field behavioural shift — so the claim does not rest on the interception
alone.

**Guard mutation-verified**, per this project's standing lesson: with the
expectation deliberately set to 17 reads instead of 16, the strict-equality
check fires (`observed {262144: 16} != expected {262144: 17}`). The guard is not
vacuous.

### IMPORTANT 2 — I called a denominator impossible that this laptop can produce

`record:368-370` said "Проверить это без второй машины нельзя". True of the
gigabit comparison; **false of the narrower question my own record raises twice
and then drops** — whether 91.9 MiB/s is our TLS-and-framing ceiling or the
local transport's. Step 2 says "any plain TCP throughput check", and loopback
needs no second machine.

Measured it: `spike_measure_checks.py --plain-tcp`, both ends in one process on
two threads — deliberately the same topology as the bypassed pass, so the
difference isolates TLS and framing rather than process layout.

| chunk | plain TCP on 127.0.0.1 (median of 3) | ours | share |
|---|---|---|---|
| 65536 | 2534.1 MiB/s | 84.3 MiB/s | 3.3% |
| 262144 | **2026.6 MiB/s** | 91.9 MiB/s | **4.5%** |

**The answer is unambiguous: 91.9 MiB/s is our own TLS-and-framing ceiling, not
a transport limit.** The local socket has ~22x headroom. For a future window
task this is the decision the reviewer said it would be: there *is* room to
chase above 92 MiB/s, and what binds is our Python framing and encryption path.

It also upgrades the gigabit remark from conjecture to inference: our software
ceiling (91.9) sits below a gigabit wire ceiling (~112), and 2026.6 proves the
medium is not what binds. Added to spec §8 and to the record, with the
"нельзя" sentence quoted and corrected rather than silently replaced.

**Guarded against the obvious misuse**, since a 2026.6 MiB/s number next to a
70% gate invites it: the Step 2 table marks the row "*(взамен)*... не знаменатель
для порога §8 — петля не сеть", and limits item 1 says it "знаменателем для
порога **не является** и подставляться в отношение не должен". The LAN rows stay
**НЕ ИЗМЕРЕНО — требует второй машины**.

**One weakness found in my own new script while mutation-testing it**: the
short-delivery guard fires only after `receiver_done.wait(300.0)` expires, so a
wedged pass takes 300 s to report rather than failing fast. It does correctly
raise rather than return a flattering number (verified by truncating `sendall`
after 3 writes: `RuntimeError: получатель не досчитал за 300 с`). Measured runs
complete in 0.8-1.0 s, three orders of magnitude inside the ceiling, so this is
recorded rather than fixed — the same judgement the 3.1 report made about
`TRANSFER_CEILING_S`.

### IMPORTANT 3 — one arithmetic fact presented as two corroborations

Both halves of `record:344-348` were wrong, and I had inherited the error into
`record:511`:

- **"предсказание RTT при 256 KiB *только* из прогона 64 KiB" is false.** Of
  `0.7186 + 2.7203 + 0.16`, the `2.7203` is the **256 KiB** bypassed pass and
  `0.16` is the **256 KiB** disk median. Only `fixed` comes from the 64 KiB run.
  It is a substitution of two 256 KiB measurements plus one carried constant,
  not a one-point prediction.
- **"ошибка 1.4%" is not a second check.** The 0.051 ms residual is **by
  construction** the difference of the two `fixed` estimates
  (0.7697 - 0.7186 = 0.0511); divided by 3.65 that is 1.4%. So "error 1.4%" is
  "agreement 7%" in a different normalisation.

Both corrected in place, with the old text quoted and the mechanism spelled
out, and the conclusion restated: the model is verified **in one respect, not
two** — the same number in two normalisations.

Also added the model's unstated assumption as limits item 9:
`transport(cb) = cb/91.9` converts a **back-to-back streaming** throughput into
a **serial** per-chunk latency, so if the bypassed pass overlapped any send with
receive, 2.72 ms is a lower bound on transport and `fixed = 0.77` ms an upper
bound. Noted that the bias runs **against** adding machinery — real fixed
overhead would be smaller, and so would a window's payoff — so nothing is
inflated in the dangerous direction.

### Minors folded in (4 of 7, plus the reviewer's invariance finding)

- **"примерно в семь раз" -> six.** 89.4 / 14.46 = **6.2**. Verified.
- **The model's asymptote is 86.8 MiB/s, not 91.9.** As `cb -> inf`,
  `1000 / (1000/91.9 + 0.64) = 86.80`, because the model's own disk term
  (0.64 ms/MiB) stays in the denominator. The earlier text took the bypassed
  transport figure itself as the asymptote and ignored that term. Corrected in
  the record's projection table, and the window ceiling updated to **~86.8
  MiB/s / ~1.60x** in both documents. The two derivations now agree exactly:
  `86.8 / 54.1 = 1.604` and `4.621 / 2.880 = 1.605`. **1.70x** is retained
  where it appears, explicitly as the looser upper bound it is.
- **"сокет никогда не был узким местом" -> "не был устойчивым узким местом".**
  A 50 ms sampler over ~758 ticks cannot see every moment of 8192 chunks.
- **Two line-number citations corrected**, both verified against the committed
  base: the identities are at `:373-374` (not `:371-372` — I copied that from
  the brief), and the `link_dropped_during_sampling` raise is at `:740-741`
  (not `:734`).

**The invariance finding, added because it strengthens the case.** The 79%
ratio was computed only at 256 KiB. Across all four runs it is
**78.8 / 78.7 / 79.0 / 79.3 percent** — a ±0.3 pp band over a **4x chunk range**
and two transfer lengths. I reproduced the reviewer's mechanism exactly:
`ours / rtt_bound = RTT / cycle`, so invariance needs `residual / RTT` stable
(26.95% vs 26.60%), and that holds because decomposing the residual as
`a + b*(cb/MiB)` gives `a = 0.219` ms, `b = 3.008` ms/MiB — a fixed share of
**22.6%** against RTT's **21.1%**. Two independent splits nearly coincide, so
both scale with the chunk almost identically and the quotient survives. Recorded
with the reviewer's caveat: demonstrated over 4x and dependent on that
coincidence, so **evidence, not a law**.

Three Minors remain deferred to the whole-branch review as instructed.

### Files changed (this round)

- `docs/superpowers/specs/2026-09-12-file-transfer-design.md` (§7, §8, §22)
- `docs/superpowers/records/2026-09-12-file-transfer-bridge-measurement.md`
- `configurator/tests/transfer/spike_measure_checks.py` (new)
- `configurator/tests/transfer/measure-plain-tcp.log` (new)
- `configurator/tests/transfer/measure-chunk-size-verify.log` (new)
- `configurator/tests/transfer/spike_measure_bridge.py` — **unchanged this round**
- `configurator/tests/transfer/test_spike_measure_bridge.py` — **unchanged**

No production code changed in this round either.

### Verification run (this round)

```
$ .venv/Scripts/python.exe -m pytest configurator/tests -q
1922 passed, 8 skipped in 84.02s
```

Unchanged from the baseline, as expected: this round touched two documents and
added one spike file with no test surface.

Commit: `3bc0546` "Narrow the measurement's claims to what it measured".

---

## Fix report (quality re-review round 2)

Re-review confirmed all six round-1 findings addressed, and found three that
round 1 introduced: 2 Critical and 1 Important. All three fixed. Four of the
five deferred observations folded in, since they sat in text being edited
anyway. The fifth (the 300 s short-delivery timeout) is recorded and not fixed.

All three new findings are the same defect as round 1's, one round later: a
correction that landed next to a claim it invalidated. Twice now in this task.
The lesson this project already carries — check the clauses either side of a
claim — was applied to the *record* in round 1 and not to the *fix* in round 1.

### IMPORTANT C — the plain-TCP figure is not reproducible at the quoted precision

Taken first, because the two Criticals lean on it.

An independent re-run of the committed script got **2956.1 / 4174.2 MiB/s**
where this branch recorded **2534.1 / 2026.6** — a factor of **2.06×** at the
production chunk, between two invocations each already a median of three. And
the chunk ordering **inverts**, so the two-row table was presenting run-to-run
noise as a chunk effect.

**I characterised the variance instead of just requoting.** Fifteen repetitions
per chunk:

| chunk | min | median | max | max/min |
|---|---|---|---|---|
| 65536 | 2530.9 | 2851.4 | 3025.8 | 1.20× |
| 262144 | **2025.2** | 4336.9 | 4449.3 | **2.20×** |

The mechanism is warm-up: at 262144 the ordered samples are
`2025, 2351, 3995, 4216, …` — the first two or three sit systematically low,
then it settles around 4300. **So a median of three on a cold start lands near
the minimum of the distribution, not its middle**, which is exactly how 2026.6
got committed. A later warm run of the fixed script gave 2486/2923/3030 and
4181/4383/4513, reproducing the reviewer's ordering.

So `spike_measure_checks.py:176-177`'s justification for median-of-three —
"один прогон на этой машине гуляет на единицы процентов" — was false as
written. Fixed:

- `PLAIN_TCP_REPEATS = 15`, and the script now prints **min / median / max plus
  the spread ratio**, never a lone number;
- the comment now records what was actually observed (2.2× spread, warm-up,
  and why a median-of-three sits near the minimum);
- the script **refuses the per-chunk comparison in its own output**: "разброс
  здесь того же порядка, что и разница между чанками, поэтому сравнивать чанки
  по этим числам НЕЛЬЗЯ… это шум, а не эффект размера кадра";
- the share is printed as a **range**, not a percentage, because the divisor
  moves by 2×.

Requoted in both documents as **"единицы процентов"** and **"запас десятки
раз"** — dropping `4.5%` (`spec:618-620`, `spec:1448`) and "примерно
двадцатидвухкратный запас" (`record:490-493`). The old figures are quoted and
marked wrong rather than silently replaced. The record's Step 2 row and limits
item 1 now read "порядка 2000–4500 МиБ/с, разброс до 2.2x между запусками".

**The load-bearing conclusion is untouched and I checked that explicitly:** the
gap is an order to two orders of magnitude, and no plausible variance
correction closes it. 91.9 MiB/s is our software ceiling, not the local
transport's. The error direction was conservative — real headroom is larger —
but the spec is the surviving document and it carried 4.5% as a measured
quantity, which is the part that needed fixing.

### CRITICAL A — gigabit upgraded to "measured" while the same document denied it three times

`record:495-499` read "делает его измеренным, а не предположительным … Значит
на гигабите связывающим ограничением был бы наш софт", with the previous
hedge ("скорее всего … Проверить это без второй машины нельзя") removed. The
same document denies it in three places: `record:509` flags 112 MiB/s as
"(НЕ ИЗМЕРЕН — нет второй машины)", `record:474` says the gigabit comparison
needs the second machine, and limits item 3 says our 91.9 is **probably an
underestimate** for a two-machine run because encryption and decryption would
not share a core.

That last one is the sharp end, and the reviewer is right that it can **flip
the conclusion outright**: if our per-machine ceiling rises above 112 MiB/s,
the network becomes the binding constraint, not our software. Loopback TCP at
2000–4500 MiB/s says nothing about a path through a NIC.

Fixed: the hedge is restored ("оказался бы, **скорее всего**, наш софт — но
проверить это без второй машины нельзя"), the previous overclaim is quoted and
marked as wrong, and the inference is stated as **conditional on two unmeasured
quantities** — the ≈112 MiB/s environment ceiling (an estimate, not a
measurement) and our own per-machine ceiling, with the flip named explicitly.
The same conditionality was added to spec §8.

**Re-read the clauses either side, as instructed.** All eight gigabit mentions
in the record now agree: 474 (needs the second machine), 525–534 (the hedged
correction), 537 (not a §8 denominator), 546 (112 marked НЕ ИЗМЕРЕН), 663
(loopback is not a denominator). No surviving contradiction.

### CRITICAL B — the gap attributed to TLS and framing on a topology parity that does not exist

`record:479-481` and `spike_measure_checks.py:17-19` claimed both ends were
"в одном процессе на двух потоках — та же топология, что у прохода с
обойдённым мостом, чтобы разница была именно TLS и кадрирование".

**Verified against the code: false.** `_measure_bypassed_transport` sends in a
main-thread loop pumped by `processEvents` and observes via
`message_received` on that same thread (`spike_measure_bridge.py:428-451`,
`:915-952`), so encryption, decryption and `FrameAssembler` all serialize on
**one** thread — which my own limits item 3 already said. The plain-TCP check
runs `sendall`/`recv_into` on **two** threads that release the GIL inside the
syscalls. So part of the gap is one core versus two.

Fixed in both the record and the script docstring, with the contributors named
as the reviewer asked and explicitly marked **inseparable by this
measurement**: single-thread serialization vs two, Qt event dispatch, per-frame
`Message` construction, and TLS with framing. Spec §8 gained the same caveat,
and the §22 row now says "ограничивает нас **наш софт**" rather than "TLS с
кадрированием", with a sentence saying the decomposition is not available.

**The load-bearing conclusion again survives and the reviewer does not dispute
it**: 91.9 MiB/s is our own software ceiling by an order of magnitude. What was
withdrawn is only the claim that the comparison *isolates* TLS and framing.

### Deferred observations folded in (4 of 5)

- **`record:608`'s "≈92 МиБ/с" asymptote** — three lines after round 1
  corrected it to 86.8. Now reads "одну и ту же асимптоту модели ≈86.8 МиБ/с
  (не 91.9 — … диск в модели тоже пропорционален чанку)". This is the same
  class as the Criticals above: round 1 fixed the asymptote in one place and
  left it stale three lines later.
- **`spec:638-640`'s "исключены измерением"** now carries the "(на **горячем
  кэше**, см. §7)" annotation that only the §22 row got in round 1.
- **`spec:500`'s §7 lead** read "Измерено, шлагбаум закрыт" unqualified,
  sixteen lines ahead of the narrowing — the same quote-the-lead exposure
  Critical 1 was about. Now "закрыт — **на измеренном классе источников** …
  файл читался из горячего кэша".
- **The report body's withdrawn §22 adaptation** (`:322-333`) is now struck
  through and annotated in place, pointing at the withdrawal, rather than left
  to contradict it. Left visible rather than rewritten so the withdrawal has
  something to point at. Confirmed again that nothing leaked into the record or
  spec: both correctly treat §22 q2 as open by plain compliance.

**Not fixed, recorded:** `spike_measure_checks.py:126-132`'s short-delivery
guard still reports only after `receiver_done.wait(300.0)` expires, so a wedged
pass takes five minutes to fail a one-second run. It does raise rather than
return a flattering number (mutation-verified in round 1). Measured runs finish
in under two seconds, three orders of magnitude inside the ceiling, so this is
latent; noted here rather than changed, because tightening it would mean
re-running the measurement to be sure the new ceiling never trips a legitimate
slow pass.

### Files changed (this round)

- `docs/superpowers/specs/2026-09-12-file-transfer-design.md` (§7, §8, §22)
- `docs/superpowers/records/2026-09-12-file-transfer-bridge-measurement.md`
- `configurator/tests/transfer/spike_measure_checks.py`
- `configurator/tests/transfer/measure-plain-tcp.log` (regenerated, n=15)
- `configurator/tests/transfer/measure-chunk-size-verify.log` (regenerated)
- `.superpowers/sdd/.../task-3.2-report.md` (in-place annotation at :322-333)
- `configurator/tests/transfer/spike_measure_bridge.py` — **unchanged**
- `configurator/tests/transfer/test_spike_measure_bridge.py` — **unchanged**

No production code changed in this round either. No headline measurement from
the bridge runs changed: this round corrected a supporting measurement's
precision and three statements about what the measurements mean.

### Verification run (this round)

```
$ .venv/Scripts/python.exe -m pytest configurator/tests -q
1922 passed, 8 skipped in 83.14s
```

Commit: `95f9ca6` "Requote the loopback TCP ceiling at the precision it has".

---

## Fix report (quality re-review round 3)

**Status: DONE_WITH_CONCERNS.** Fixed only the two remaining findings C and B
assigned for round 3. No executable behavior, measurement results, or production
code changed. The two out-of-scope reviewer observations remain deferred to
final review as instructed.

### Changes and rationale

- `docs/superpowers/records/2026-09-12-file-transfer-bridge-measurement.md`:
  removed the parenthetical claiming observed headroom of approximately
  29x–49x. The adjacent production-chunk observation, 2025.2 MiB/s, divided by
  91.9 MiB/s is **22.04x**, outside that interval. Retained the supported
  coarser statements "единицы процентов" and "десятки раз". Changed the
  section lead from "потолком нашего TLS с кадрированием" to "потолком нашего
  софта", so the question posed by the lead matches the qualified answer.
- `configurator/tests/transfer/spike_measure_checks.py`: changed the module
  lead to the same software-ceiling wording. Replaced the function docstring's
  false two-thread topology-parity claim with the actual distinction: plain
  TCP sends and receives on two threads; the bypass sends and observes receipt
  on one Qt thread. Explicitly stated that the comparison cannot separate
  that difference from TLS, framing, Qt dispatch, or per-frame Message
  construction. Also restored the missing space in the adjacent contributor
  list. All Python edits are within docstrings.

### Neighborhood audit

Re-read the script's full module lead and contributor list, the function
docstring and implementation, the repeat-count comments, CLI description, and
printed comparison. Checked `_measure_bypassed_transport` and its caller:
the observed topology agrees with the replacement text. Existing quotations
of the old parity assertion are explicitly identified as false.

Re-read the record's surrounding model paragraph, section lead, variance
discussion, software-ceiling conclusion, topology explanation, inseparable
contributor list, gigabit caveat, Step 2 table, final gate table, and limitation
list. Audited spec section 8's leads and comparison and section 22's lead and
question 2 row. For the two assigned findings these now agree: the comparison
supports a software ceiling below local transport, not attribution to TLS and
framing; headroom is stated coarsely across the observed dispersion. The
committed log's 29–36x and 45–49x are invocation-specific rows, not a claim
about all observations. No spec or measurement-log changes were needed.

### Focused verification

`git diff --check` and `git diff --cached --check`: exit 0, no whitespace
errors. Git emitted only its normal LF-to-CRLF working-copy notices.

Executed this PowerShell check against the pre-fix HEAD before committing:

```powershell
@'
import ast
import pathlib
import subprocess

path = 'configurator/tests/transfer/spike_measure_checks.py'
before = ast.parse(subprocess.check_output(['git', 'show', 'HEAD:' + path]).decode('utf-8'))
after = ast.parse(pathlib.Path(path).read_text(encoding='utf-8'))

class StripDocstrings(ast.NodeTransformer):
    def visit_Expr(self, node):
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return None
        return self.generic_visit(node)

strip = StripDocstrings()
assert ast.dump(strip.visit(before)) == ast.dump(strip.visit(after)), 'Executable AST changed'
print('PASS: Python parses; executable AST unchanged after stripping docstrings')
print('Observed production-chunk minimum headroom: %.2fx' % (2025.2 / 91.9))
'@ | .venv/Scripts/python.exe -
```

Output (exit 0):

```text
PASS: Python parses; executable AST unchanged after stripping docstrings
Observed production-chunk minimum headroom: 22.04x
```

An initial attempt to pass this check using `python -c` failed with a
PowerShell quoting SyntaxError before running the check. The stdin form above
resolved that invocation issue. This was not a source-file syntax failure.

Targeted audit command (the three paths are the script, record, and spec named
above):

```powershell
rg -n 'потолком нашего TLS|потолок НАШЕГО TLS|~29×|как в проходе с' $taskClaimFiles
```

No matches (`rg` exit 1); the wrapper reported
`PASS: no surviving targeted overclaims` and exited 0. No heavy test suites or
measurements were repeated because executable behavior is unchanged.

### Commit and concerns

Commit: `21d1cbe9877d594d4e00a50001cf112ae62bc0fa`
"Correct remaining loopback comparison overclaims".

Only the two requested source/docs files are committed. This report is tracked
in the current checkout (`git check-ignore -v` has no match), so this appended
artifact section is left uncommitted to keep the fixes commit scoped as
requested. The two out-of-scope observations are unchanged and remain for
final review. No new implementation concerns were introduced; the LAN gate
still requires the separate two-machine measurement.
