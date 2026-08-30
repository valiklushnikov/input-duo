# Plan reconciliation — what was ticked, what was not, and on what

Date: 2026-08-30. Branch `feature/duo-input-foundation`, starting at `b7a97c5`,
tagged `duo-input-v0.1.0-rc1`. No code was touched: `pytest tests -q` reads 90
passed before and after, and the tree is clean.

Seventy-seven checkboxes across five plans were unticked. Sixty-five are now
ticked; twelve are not. Every tick names what proves it. Every empty box now
carries a blockquote saying whether the work was not done, or whether the
evidence for it no longer exists — and those are different answers, kept apart
throughout.

| Plan | Was open | Ticked | Left open |
|---|---|---|---|
| `2026-08-25-duo-input-foundation.md` | 35 | 35 | 0 |
| `2026-08-25-duo-input-roadmap.md` | 20 | 15 | 5 |
| `2026-08-25-duo-input-input-mapping-macros.md` | 17 | 12 | 5 |
| `2026-08-25-duo-input-configurator-release.md` | 4 | 3 | 1 |
| `2026-08-25-duo-input-core-firmware.md` | 1 | 0 | 1 |
| | **77** | **65** | **12** |

Commits, one per plan:

    6dd1fe7  Tick the foundation plan against what its ledger already proved
    9c7c6b6  Say why the two-PC pattern run cannot happen, not that it is waiting
    a7c9e33  Tick the input runtime plan against hardware, and name the four gaps
    2da2efe  Close three release steps, and keep the clean-VM smoke open
    (this commit)  the roadmap, its gate, this report

## The rule

**A tick names what proves it, or it is not a tick.** This project has spent two
days finding capabilities that were written, tested, and never called. A ticked
box with nothing behind it is the same failure in documentation form, and it is
harder to find because documentation has no test suite.

Three verdicts were kept apart and never blurred into each other:

- **Done** — a commit range, a ledger entry, or a counter read off hardware can
  be named. Ticked, with that evidence beside it.
- **Not done** — ticked by nobody, with a blockquote saying so and why.
- **Cannot be told from what survives** — left unticked, saying the evidence is
  missing rather than guessing in either direction. Four steps landed here.

Where a step's wording no longer describes what was built, the deviation is
written into a note. No requirement was edited.

---

## Foundation — 35 open, 35 ticked, 0 left

All seven tasks landed on 2026-08-25 in a single session, each with its own
independent task review, and nobody returned to the boxes. The ledger's "Task
results" section names a commit range per task and the task reports record RED
and GREEN separately, with the actual failure text — `ModuleNotFoundError: No
module named 'duo_input'`, missing symbols, missing vectors. Each task now
carries a blockquote naming its range, the commits inside it, and the ledger
line.

    Task 1  ea97f53..c33241f    Task 5  828ede0..f9ba0b4
    Task 2  c33241f..9855fcb    Task 6  f9ba0b4..7b74c0a
    Task 3  9855fcb..ceda472    Task 7  7b74c0a..f560a51
    Task 4  ceda472..828ede0    ABI fix 8704956

Two deviations are recorded rather than ticked quietly:

- **Task 7 Step 1's red state was never a crashing seed.** The ledger's ruling
  stands: the Tasks 4/5 parsers were already hardened and reviewed, so
  manufacturing a crash would have regressed the product. The corpus seeding the
  step asks for *was* done — declared length 0, legal maximum, maximum+1 and
  `0xFFFF`, each with valid and corrupt-CRC variants, reproduced byte-exactly by
  `tests/fuzz/generate_corpus.py --check`. What never happened is an observed
  sanitizer failure, because none was staged.
- **Steps 2 and 4 were closed a day after the rest of the task**, at `c27a314`,
  once a Clang/libFuzzer toolchain existed. The campaign table is in the ledger:
  2 249 901 / 13 743 483 / 7 440 331 runs, all exit 0, artifact directories
  empty. The parsers were unchanged between `8704956` and `c27a314`, which is
  what makes the later campaign evidence about the earlier code.

A gate-status note was added at the foot of the plan, including the `ErrorCode`
finding that Roadmap Task 1 Step 4 raised and that Task 10a closed in `fe6fdfa`.

**Ambiguity encountered: none.** This is the best-evidenced plan of the five —
every step has a report with its own red and green output.

---

## Core firmware — 1 open, 0 ticked, 1 left

**Task 5 Step 4, "Verify two-PC pattern" — NOT DONE, and no longer runnable.**

The existing note said it was blocked for want of a second computer. That note
is now wrong in a way worth catching: a second computer exists, but the thing it
was to receive does not. `16afc0b` ("Give Core 1 the real input runtime instead
of a test pattern") deleted the synthetic producer; `grep -r test_pattern
firmware/` returns nothing, and
`tests/build/test_firmware_artifacts.py::test_a_release_image_cannot_generate_its_own_input`
now asserts that no such symbol can be linked into a release image at all.

The temptation here was to tick it on the strength of what came later — real
keyboard and mouse routed to PC1, PC2 and Both on hardware on 2026-08-29, and
HIL runs with no gap over 50 ms across 304 keyboard and 33 321 mouse samples.
That is a *stronger* claim than the step's, and it is still not the step's
claim: no 15-minute continuous pattern run was ever performed. The substitute is
named beside the step and credited to nothing.

The plan's Completion Gate bullet was updated from "**Blocked** until U2 can be
connected to a second computer" to "**NOT MET, and now unreachable**", because
the old wording implies a wait that will end.

---

## Input runtime (CH375, mapping, macros) — 17 open, 12 ticked, 5 left

### Ticked (12)

**Task 2, all five steps.** The ledger's own pre-flight said it plainly: "Task
2's boxes are unticked but its transport and state machine exist and were the
subject of a day of hardware debugging." `test_ch375_device.cpp` (`8af5a09`)
scripts connect, host mode, disconnect, malformed response and the one-second
retry. Two instances exist in `main.cpp`. The nine-bit frame is in PIO as the
step requires — `ch375_serial.pio`, "one start bit, nine data bits and one stop
bit". The diagnostic build is `DUO_CH375_PROBE`, and independent state and
recovery were observed on hardware (ledger 2026-08-29 13:33, "BOTH DEVICES WORK,
SIMULTANEOUSLY AND CONTINUOUSLY"; both channels Ready with counters past
twenty-two thousand each, one detach apiece, no presence losses).

Two deviations recorded in the note: the TXS OE step was dropped because OE is
tied to VCCA on this board, and the baud is **found by a block-read ladder rather
than configured** — `CHECK_EXIST` answered correctly at 115200 and 62500 while
every multi-byte read at those rates failed, zero successes against twenty-four
failures each, and only 37500 carried a descriptor.

**Task 4 Step 4, replay captured reports.** `37b5afc` committed
`tests/vectors/hid_reports/` — 26 distinct keyboard and 59 distinct mouse
reports off the real devices — and `f565504` added the `trace_replay` suite,
replaying all 85 against a golden stream of 54 and 62 events. Four production
mutations were run to prove the suite can fail (73, 28, 60 and 72 failures),
each reverted. Stated limit: the diagnostics carry a packet's size and its first
four bytes only, so nothing past byte 3 is asserted.

**Task 7 Steps 1, 3, 4 and 5.** Step 3 is `16afc0b` exactly. Step 4 is the
hardware acceptance of 2026-08-29 — all four criteria, on real hardware, across
two computers:

    routing (F9/F10/F11, F12, mouse button 4)   PASSED  16:1x
    the text macro, complete on both PCs        PASSED  18:0x  (S4b)
    disconnect release, no stranded key         PASSED  18:1x  (S4c)
    eight profiles across a power cycle         PASSED  18:2x  (S4a)

S4a's discriminating detail is kept in the note: the device came back up **in
profile 3**, the profile the configuration names as active. The firmware falls
back to profile 0 when a load fails, so booting into 3 is positive evidence the
flash read succeeded rather than merely that something loaded. One deviation
noted: the step names the macro `/target KYPKYMA`; what ran was the text macro
built by `tools/step4_acceptance_config.py`. The criterion met is the one the
step states.

**Four "Step 5: Commit" steps** (Tasks 2, 3, 4, 5) were satisfied by commits
with prose subjects rather than the literal messages the plan drafted. That is a
branch-wide convention, not a gap; each note names the actual commits.

### Left open (5)

**Task 3 Step 4 — PARTIAL.** The recording half is done and citable: VID `0x1BCF`
PID `0x0005` with report-descriptor SHA-256 `f93525fd…` and five declared
buttons, VID `0x258A` PID `0x010C` with no hash because that device gave up no
report descriptor, interrupt IN endpoint 1 on both read from the endpoint
descriptor, `wMaxPacketSize` 8 and 7. **What was never done is "reconnect 20
times each without reset".** No count of reconnections exists in any ledger,
report or artifact. What is recorded — one detach per device across a long
session with no presence losses — is a weaker and different claim, and was not
allowed to stand in for the count.

**Task 2 Step 2, Task 4 Step 2, Task 5 Step 2 — EVIDENCE MISSING.** Each is a
"verify the tests fail" step. In all three cases the test file and the
implementation it tests landed in the same commit — `8af5a09`, `102571e`,
`2ab40e7` — and no ledger entry, task report or review names a failing run. The
tests exist and pass. Whether anyone watched them fail first cannot be
determined, so they stay unticked. This is the third verdict, not a soft "no".

**Task 7 Step 2 — EVIDENCE AMBIGUOUS.** Here there *is* a claim: the ledger's
pre-flight ruling says "Task 7 Steps 1-2 and the natively testable half of Step 3
were implemented in the controller session before this skill was invoked
(commits `36ff0e0`, `51a7bf8`, `80e84bb`)". That names the step but not a failing
run, and "implemented" is not a verb that fits "verify red state". Left unticked
on the same rule as the three above — a summary line is not a measurement. This
was the single closest call in the whole reconciliation, and it was decided
against the tick because the phrase that would justify it is ambiguous and
nothing else corroborates it.

### Gate

A bullet-by-bullet gate status was added. Five bullets met; the physical-toggle
bullet stands at **320 rather than 1000**, with the arithmetic that makes 32 per
run a ceiling and the counters that make 320 meaningful (`dropped_commands` 0,
`runtime_fault` 0, 154 030 SPI frames with no CRC error, no stranded modifier).
The shortfall is recorded as a shortfall.

---

## Configurator and release — 4 open, 3 ticked, 1 left

**Task 10 Step 2 — TICKED.** The old PARTIAL note was correct when written: the
Pico firmware build could not run because `PICO_SDK_PATH` was unset and no
arm-none-eabi toolchain existed. Both exist now, both presets build to exit 0,
`pico-release` was flashed for every HIL run, and `build_release.ps1` runs this
same command list end to end before assembling anything. The last full run was
taken in two trees: `--check` 0, ctest 36/36, `pytest tests` 90, artifact
contracts 13, in both.

**Task 10 Step 3 — TICKED WITH LIMITS.** Every scenario the rig can reach was run
against the board on COM18. The measurements are recordings: 304 keyboard and
33 321 mouse samples, p95 ≤ 2 ms against a 20 ms budget, maxima 1.899 and
1.916 ms, no gap over 50 ms, control-link p95 2.632 ms, two devices identified by
VID/PID/hash/buttons.

The note under the tick lists, individually, everything that came back
unmeasured and why: end-to-end keystroke p95 (this rig cannot measure it at all),
U2's release on link loss (no fault staged, by the operator's decision), eight of
ten peripherals (U1 has two ports), the 24-hour soak (baseline written, run
open), `config_power_cut` and `profile_power_cycle` (this runner neither writes
configuration nor cuts power), and `route_toggle`'s own three questions (visible
only at the computer an event was routed to).

**This was the second-closest call.** The step's own wording names "records U2
release ≤ 100 ms", and that specific recording was not taken by this runner. The
tick was given because the step's verb is *execute and record*, every scenario
was executed, and the unmeasured results were recorded as unmeasured with their
reasons — which is precisely the recording the step asks for and precisely what
`9b6b8f8` was written to guarantee. A reader who sees only the tick and not the
note would be misled, so the note is long and sits directly beneath it.

**Task 10 Step 4 — TICKED, UNSIGNED.** `build_release.ps1 -Version 0.1.0-rc1`
assembled `dist/release/0.1.0-rc1` from a clean tree, running the generator
check, the native build and tests, the Python suites, the Pico build, Nuitka and
Inno Setup end to end. The folder was rebuilt at `b7a97c5` after `be5d35a` made
the images reproducible, so its `SHA256SUMS.txt` describes the tagged commit
rather than the afternoon it was first built. Nothing is code-signed — the step
allows that for a prototype — and the installer's hash identifies one build, not
one commit.

**Task 9 Step 4 — STILL NOT DONE.** The note's reason was updated: the clean VM
is not merely unavailable, it was **declined** by the operator on 2026-08-30. The
installer builds and the dist contract passes over the built folder; what is
untested is that it runs on a machine with no Python and no Qt. This host has
both, so a run here would prove nothing — which is why the step asks for a clean
VM, and why a same-host smoke was not labelled as one.

A gate status was added: five of six bullets met (one with a stated limit), the
installer bullet not met.

---

## Roadmap — 20 open, 15 ticked, 5 left

### Ticked (15)

- **Task 1 Steps 1–4.** Worktree and baseline (`6 passed`), the foundation plan
  executed in order, the foundation gate run at `c27a314`, and the protocol
  artifact review — which is ticked because it *found* something: the
  hand-written `ErrorCode` in `emulator.py`, later closed by `fe6fdfa`.
- **Task 2 Step 2.** The core firmware gate commands, run 2026-08-27 against a
  gate narrowed on the record (buttons dropped because the hardware will not have
  them).
- **Task 3 Steps 1–5.** The input runtime phase, ending in the
  `duo-input-input-runtime-v0.1` tag, which exists.
- **Task 4 Steps 1–5.** The configurator phase, ending in
  `duo-input-v0.1.0-rc1`, which exists.

Two of those carry limits stated in their notes. **Task 4 Step 3** ("all latency,
soak, power-cut and compatibility metrics must be recorded") is ticked on the
reading that a metric recorded *as unmeasured, with its reason and the rig it
would take* is recorded — and every such item is enumerated beneath the tick.
**Task 4 Step 4** ("run native, Python, Pico build, clean-VM installer and
SHA-256 checks from a clean checkout") is ticked for everything except the
clean-VM installer, which is named in the note as not done and cross-referenced
to the configurator plan's Task 9 Step 4. The clean checkout earned its place
twice over: it found `--check` failing silently on every fresh clone (CRLF versus
LF, `4e53fc3`) and the firmware images differing by the two bytes of embedded
build date (`be5d35a`).

### Left open (5)

- **Task 1 Step 5 and Task 2 Step 5 — the two phase tags do not exist.**
  `git tag -l` lists `duo-input-configurator-v0.1`,
  `duo-input-input-runtime-v0.1` and `duo-input-v0.1.0-rc1`, and nothing else.
  The foundation ledger records the reason the first was left: by the time the
  step came round, HEAD had moved past the foundation commit, so tagging was "a
  decision for the human partner, not an automatic step". Tagging `8704956`
  retroactively is still available and costs nothing.
- **Task 2 Step 1 — PARTIAL.** 34 of the core firmware plan's 35 steps are done;
  Task 5 Step 4 is not, and cannot be. Left unticked deliberately so the signal
  reaches the Overall Gate's first bullet rather than stopping here.
- **Task 2 Step 3 — PARTIAL, three of four recordings exist.** USB descriptors:
  recorded, and both boards enumerated separately on Windows. U2 measured release
  latency: **recorded**, 2026-08-27, three cuts in a row at 100 ms each — this is
  a different measurement from the HIL `link_fault` scenario that came back
  unmeasured on 2026-08-30, and neither substitutes for the other. Power-cut
  config recovery: recorded. The 10-minute pattern result: never taken, and now
  unreachable.
- **Task 2 Step 4 — EVIDENCE MISSING for the review itself.** Nothing describes
  this fail-safe invariant review being performed, and there is no SDD ledger
  directory for the core firmware plan at all, so if it happened it left nothing.
  Partial evidence exists for the invariants — native suites pin the release
  paths, the disconnect release was accepted on hardware, and the whole-branch
  review reported that every release-on-disconnect path fails loudly when broken
  — but the HIL matrix records "a damaged frame produces no report" as
  **Unmeasured**, because that is visible only at PC2. An invariant review nobody
  can point at is not a review.

### The asymmetry, stated on purpose

Roadmap Task 3 Step 1 ("execute the input/mapping plan") is ticked even though
four steps of that plan remain open, while Task 2 Step 1 ("execute the core
firmware plan") is left unticked although 34 of its 35 steps are done. The
distinction is what each shortfall does to the gate above it. Core firmware's one
open step is a hardware acceptance its own Completion Gate names as a bullet, so
the roadmap step stays open to carry that upward. Input/mapping's four open steps
are TDD bookkeeping and one small reconnect exercise; they change no gate bullet,
so the step is ticked with them named in its note.

---

## The Overall Completion Gate — three met, one with a stated limit, one partial, one not met

The full assessment with its evidence is in `progress.md` beside this file. In
brief:

| # | Bullet | Verdict |
|---|---|---|
| 1 | Four subordinate completion gates pass | **NOT MET** — core firmware and configurator each have one unmet bullet |
| 2 | No open test failure waived without a written spec change | **MET** — every suite green; four narrowings, each written into its plan |
| 3 | Two UF2 byte-reproducible from a clean checkout of the tagged commit | **MET**, measured across two worktrees |
| 4 | Installer built from that commit, hash recorded, not byte-reproducible | **MET as written** — both causes measured |
| 5 | Compatibility, latency and soak results attached to release notes | **PARTIALLY MET** — no soak results, because the soak has not run |
| 6 | Prototype RC may ship privately; commercial shipment blocked | **HOLDS** |

Bullets 3 and 4 were rewritten today after measuring, and both were checked
against the measurements rather than against intent. Bullet 3's claim about
`SOURCE_DATE_EPOCH` and the commit date is implemented in
`cmake/source_date.cmake` in exactly the terms it states. Bullet 4's two causes
are each backed by an experiment — back-to-back `ISCC` compiles identical, one
changed mtime different, and Nuitka's PE header stamped `2026-08-30 10:16:53Z`.
Neither bullet overclaims.

## Two things found that were not fixed, because they are not plan status marks

Both are release artifacts, and both are recorded here rather than edited:

1. **`compatibility-matrix.md` says `soak_24h` is "Not opened."** The
   configurator ledger records that `--phase baseline` was run on 2026-08-30 and
   the run is open. The matrix is one step stale on that row. The conclusion — no
   soak result — is unaffected.
2. **`RELEASE-NOTES.md` says, under "What is not": "Hardware acceptance."** That
   is now too broad. Hardware acceptance of the input runtime passed on
   2026-08-29, and the latency figures in the matrix beside it are hardware
   recordings. What is genuinely absent is the soak, eight of ten peripherals,
   U2's release on a staged fault, and end-to-end latency. Understatement is the
   right direction for a release note to err in, but it currently understates a
   day of hardware acceptance that did happen.

## Verification

- `.superpowers/runtime-venv/Scripts/python.exe -m pytest tests -q` → **90
  passed**, unchanged.
- `git status` clean.
- No code, test, firmware or tooling file was touched. Only plan status marks,
  plan status notes, this ledger and this report.
