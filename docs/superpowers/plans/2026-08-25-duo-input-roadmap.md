# Duo Input MVP Implementation Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Провести реализацию Duo Input от чистого общего протокола до проверенного комплекта из двух UF2 и Windows-установщика.

**Architecture:** Работа разделена на четыре последовательных плана с самостоятельными completion gates. Foundation блокирует все остальные этапы; core firmware и input runtime дают отдельно тестируемые аппаратные инкременты; configurator/release начинается на emulator и завершается HIL.

**Tech Stack:** Pico SDK C++17, TinyUSB, CH375B UART host, SPI1, Python 3.12, PySide6, QtSerialPort, pytest/CTest, Nuitka, Inno Setup.

**Spec:** `docs/superpowers/specs/2026-08-25-duo-input-firmware-configurator-design.md`

## Global Constraints

- Старые `legacy/`, `improved/` и `diagnostics/` не удалять и не переписывать.
- Каждый нижестоящий план начинается только после completion gate предыдущего.
- Все изменения выполняются TDD, маленькими коммитами из соответствующего task.
- Hardware claims подтверждаются измерениями; они не отмечаются выполненными по результатам симулятора.
- Рабочая ветка создаётся через `superpowers:using-git-worktrees` перед реализацией.

## Spec Coverage Map

| Spec sections | Implementation plan/tasks |
|---|---|
| 3–7 Hardware, U1/U2, USB | Core Firmware Tasks 1–5, 7 |
| 8–9 CH375 compatibility/events | Input/Mapping Tasks 1–4 |
| 10–13 routes/profiles/mouse/macros | Input/Mapping Tasks 5–7; Configurator Tasks 1–2, 5–6 |
| 14 binary config/Flash | Foundation Task 5; Core Firmware Task 6 |
| 15 CDC | Foundation Tasks 3–6; Configurator Task 3 |
| 16 SPI | Foundation Task 4; Core Firmware Task 4 |
| 17 fail-safe | Core Firmware Tasks 2, 4, 5, 7; Input/Mapping Task 7 |
| 18 Windows UI | Configurator Tasks 1–8 |
| 19–20 diagnostics/security | Foundation Task 7; Core Firmware Task 7; Configurator Task 7 |
| 21 testing | all completion gates; Configurator Task 10 |
| 22–24 versioning/release/sources | Foundation Task 2; Configurator Tasks 9–10 |

---

### Task 1: Execute protocol/config foundation

**Files:**
- Read/execute: `docs/superpowers/plans/2026-08-25-duo-input-foundation.md`

**Interfaces:**
- Produces generated C++/Python protocol IDs, shared vectors, strict codecs, binary config and U1 emulator.

- [x] **Step 1: Create isolated worktree and baseline**

Run the using-git-worktrees skill, then run existing repository tests and record their passing/failing baseline without modifying unrelated diagnostic files.

> **DONE (2026-08-25).** Foundation ledger, "Setup": worktree
> `work\duo-input-mvp`, branch `feature/duo-input-foundation` at `ea97f53`,
> baseline `6 passed` on Python 3.12.13 / pytest 8.4.2. The untracked
> `diagnostics/ps2_mouse_diagnostic/` was left untouched, as the step requires.

- [x] **Step 2: Execute Foundation Tasks 1–7 in order**

Use the exact red/green commands and commits in the foundation plan.

> **DONE (2026-08-25).** All seven tasks landed in order, each with its own
> independent task review: `ea97f53..c33241f`, `..9855fcb`, `..ceda472`,
> `..828ede0`, `..f9ba0b4`, `..7b74c0a`, `..f560a51`, plus the ABI fix `8704956`.
> Every step of that plan is now ticked with its commits.

- [x] **Step 3: Run foundation completion gate**

```powershell
python tools/generate_protocol.py --check
cmake --build build/native
ctest --test-dir build/native --output-on-failure
python -m pytest configurator/tests -q
```

> **DONE (2026-08-26 at `c27a314`).** Foundation ledger, "Session 2 verification":
> `--check` exit 0; a fresh MSVC/Ninja native build with CTest 7/7; configurator
> pytest 132; repository pytest 8 + 6 subtests; corpus freshness exit 0;
> `git diff --check` clean. The external Clang/libFuzzer gate that was still open
> at the foundation head was closed in the same session - three sanitizer
> campaigns, 2.2 M / 13.7 M / 7.4 M runs, all exit 0 with empty artifact
> directories.

- [x] **Step 4: Review protocol artifacts**

Confirm IDs occur only in `protocol/schema.json` and generated files; compare C++/Python golden vectors.

> **DONE (2026-08-26), and it found something.** The review is recorded in the
> foundation ledger under this step's own name, as a FINDING rather than a pass:
> `ErrorCode` (values 0..11) was hand-written in
> `configurator/src/duo_input/device/emulator.py` and absent from
> `protocol/schema.json`. It did not breach the constraint as worded - no C++ copy
> existed yet - but would have the moment firmware gained an error table. Closed
> later, as Task 10a of the configurator plan: `fe6fdfa` added `cdc_errors` to the
> schema, the generator now emits both `ErrorCode(IntEnum)` and
> `enum class CdcError`, and the hand-written copies in `config_service.hpp` and
> `emulator.py` are gone. Golden vectors are compared by construction: the native
> and Python suites read the same files under `tests/vectors/`.

- [ ] **Step 5: Create phase checkpoint commit/tag**

```powershell
git tag duo-input-foundation-v0.1
```

> **NOT DONE.** `git tag -l` lists `duo-input-configurator-v0.1`,
> `duo-input-input-runtime-v0.1` and `duo-input-v0.1.0-rc1`. There is no
> `duo-input-foundation-v0.1`, and the foundation ledger says why it was left:
> "Foundation tag: NOT created. The last foundation commit is `8704956`; HEAD has
> since moved to `c27a314` (Configurator Task 1). Roadmap Task 1 Step 5 assumes the
> branch head IS the foundation commit, so `git tag duo-input-foundation-v0.1` is
> a decision for the human partner, not an automatic step." Tagging `8704956`
> retroactively is still available and costs nothing; it has not been done, so the
> box stays empty.

### Task 2: Execute core U1/U2 firmware

**Files:**
- Read/execute: `docs/superpowers/plans/2026-08-25-duo-input-core-firmware.md`

**Interfaces:**
- Consumes foundation codecs/config/emulator.
- Produces two UF2 with USB HID, CDC, SPI fail-safe, Flash A/B and hardware recovery controls.

- [ ] **Step 1: Execute Core Firmware Tasks 1–7 in order**

Use synthetic input only; do not begin CH375 integration inside this phase.

> **PARTIAL.** Thirty-four of the core firmware plan's thirty-five steps are done
> and were ticked as they landed. One is not: **Task 5 Step 4, "Verify two-PC
> pattern"** - and it is now unrunnable, because `16afc0b` deleted the synthetic
> producer when Core 1 took the real input runtime. The core firmware plan's own
> Completion Gate therefore does not fully pass, which is what the Overall
> Completion Gate's first bullet depends on. Left unticked so that signal reaches
> the gate instead of stopping here.

- [x] **Step 2: Run native/build suites**

Run all commands from the core completion gate.

> **DONE (2026-08-27), against a gate narrowed on the record.** Core firmware plan
> Task 7 Step 4: both UF2 build, the native and Python suites are green, and the
> 100 ms U2 fail-safe is measured on hardware. SW1, SW2 and the 5-second factory
> confirmation were **dropped from the gate**, not skipped - the hardware will not
> have buttons (decided 2026-08-27), and what they would have done is reachable
> over CDC. That decision is written into the plan beside the step.

- [ ] **Step 3: Perform two-board link test**

Record USB descriptors, 10-minute pattern result, U2 measured release latency and power-cut config recovery.

> **PARTIAL - three of the four recordings exist; the 10-minute pattern does
> not.**
>
> - **USB descriptors - RECORDED.** `tools/dump_usb_descriptors.py` extracts them
>   from the ELF symbols and `tests/build/test_usb_descriptors.py` pins the
>   interface sets, five mouse buttons and unique product strings. Both boards were
>   enumerated separately on Windows and U1 exposes a COM port where U2 does not
>   (core firmware Task 3 Step 4).
> - **10-minute pattern result - NOT RECORDED.** It needs `DUO_TEST_PATTERN`,
>   which had no second computer to type on in August and no longer exists at all
>   after `16afc0b`. See the core firmware plan's Task 5 Step 4.
> - **U2 measured release latency - RECORDED, 2026-08-27.** Three cuts in a row,
>   `release_ms` 100, 100, 100, each costing exactly one damaged frame on recovery.
>   The link loss was produced by putting U1 into its bootloader over CDC rather
>   than by pulling wires, because everything the host can see about U2 travels
>   over the link that just went silent; U2 records the drop and reports it when
>   the link returns. (This is a different measurement from the HIL `link_fault`
>   scenario of 2026-08-30, which returned unmeasured because no fault was staged.
>   Neither substitutes for the other.)
> - **Power-cut config recovery - RECORDED.** Core firmware Task 6 Step 4:
>   configurations uploaded through CDC, readback SHA-256 compared, power cycled
>   during chunk transfer, old slot intact.

- [ ] **Step 4: Review fail-safe invariants**

Verify every reset/error path reaches released HID state and damaged frames cause no reports.

> **EVIDENCE MISSING for the review; partial evidence for the invariants.** No
> ledger entry, report or review record describes this review being performed -
> and there is no SDD ledger directory for the core firmware plan at all, so if it
> happened it left nothing behind.
>
> What does exist, and is not the same thing: native suites pin the release paths
> (`test_link_watchdog`, `test_frame_resync`, `test_hid_state_manager`,
> `a_sequence_that_goes_backwards_is_a_gap_not_a_duplicate`), the disconnect
> release was accepted on hardware in the input runtime phase, and the whole-branch
> review of all 157 commits reported that "every release-on-disconnect path fails
> loudly when broken". Against that, the HIL `link_fault` row records "a damaged
> frame produces no report" as **Unmeasured**, because whether a damaged frame
> produced a report is only visible at PC2. Left unticked: an invariant review that
> nobody can point at is not a review.

- [ ] **Step 5: Create phase checkpoint tag**

```powershell
git tag duo-input-core-firmware-v0.1
```

> **NOT DONE.** No `duo-input-core-firmware-v0.1` exists in `git tag -l`. Unlike
> the foundation tag, this one also has an open step behind it (Step 1 above), so
> the checkpoint it would mark was never reached.

### Task 3: Execute CH375, mapping and macros

**Files:**
- Read/execute: `docs/superpowers/plans/2026-08-25-duo-input-input-mapping-macros.md`

**Interfaces:**
- Consumes U1 Core 1 queue boundary and output runtime.
- Produces physical USB input, profiles, bindings, routes, capture and autonomous macros.

- [x] **Step 1: Execute Input/Mapping Tasks 1–7 in order**

Start from WCH command-table verification and scripted transport before enabling TXS OE.

> **DONE (2026-08-29).** The plan was executed from Task 1's WCH command-table
> transcription through Task 7's hardware acceptance, which passed in full:
> routing by key and by mouse button, the text macro complete on both computers,
> release-on-disconnect with nothing stranded, and eight profiles surviving a power
> cycle into the configured active profile.
>
> Two notes the tick does not erase. The TXS OE step named in this step's wording
> **was dropped** - OE is tied to VCCA on this board and no firmware can hold it
> low (`docs/hardware/ch375-wiring.md`). And four steps of that plan remain
> unticked: three "verify red state" steps whose test and implementation landed in
> one commit with no record of a failing run, and Task 3 Step 4's "reconnect 20
> times each", which was never performed. They are bookkeeping and one small
> hardware exercise, not open implementation.

- [x] **Step 2: Run native trace replays**

Verify committed keyboard/mouse traces generate exact normalized events.

> **DONE (2026-08-29).** `37b5afc` committed `tests/vectors/hid_reports/` - 26
> distinct keyboard and 59 distinct mouse reports recorded off the real devices -
> and `f565504` added the `trace_replay` suite, which replays all 85 and asserts a
> golden stream of 54 and 62 events: index, kind, code, signed deltas. Four
> production mutations were run to prove it can fail (73, 28, 60 and 72 failures),
> each reverted. Stated limit: the diagnostics carry only a packet's size and its
> first four bytes, so nothing past byte 3 is asserted - which is a whole boot
> mouse report and the fields the keyboard normalizer reads.

- [x] **Step 3: Perform physical input acceptance**

Run 1000 toggles, disconnect/reconnect, eight-profile power cycle and macro/physical concurrency checks.

> **DONE ON HARDWARE (2026-08-29), at 320 toggles rather than 1000.** Ten
> device-driven runs of 32 route changes each, Left Shift held throughout, with
> ten physical trigger presses arriving while macro output was in flight - which
> is the macro/physical concurrency check. Counters before and after:
> `dropped_commands` 0 to 0, `runtime_fault` 0 to 0, `bad_crc` 0, `timeout` 0,
> `link_frames_sent` 1257 to 155 287 - 154 030 frames, no CRC error and no
> timeout - and no stranded modifier on either computer. Disconnect/reconnect
> (S4c) and the eight-profile power cycle (S4a) passed the same day.
>
> **The shortfall is real and is not rounded up.** 32 route changes per run is a
> ceiling, not a choice: 64 steps is a hard macro limit, macros do not chain, and a
> held key does not re-trigger a binding. 1000 toggles therefore means 32 presses,
> and at 32 presses the operator can no longer count what they saw - and a number
> from an inattentive observer is not evidence. `TOGGLE_RUNS` is pinned by
> `test_the_delivered_toggle_count_is_the_one_the_ledger_records`, so the count
> cannot drift away from this note without the suite saying so. What a run past 320
> would catch is an accumulation defect slower than the counters that were read
> before and after.

- [x] **Step 4: Update compatibility evidence**

Record every tested VID/PID/hash and exact pass/fail reason.

> **DONE (2026-08-29 and 2026-08-30).** `docs/hardware/ch375-compatibility.md`
> carries the eight descriptor vectors with the exact `ParseError` each returns,
> the boot-protocol wheel limit, and "a channel's name is not the device on it".
> `docs/release/compatibility-matrix.md` carries the two bench devices by VID, PID
> and report-descriptor SHA-256 with a pass reason each, plus the explicit
> "End-to-end keystroke p95 - not measurable on this rig" row.
>
> **Deviation, ruled by the operator and recorded rather than absorbed:** the
> documents record device *classes, identifiers and behaviours*, not product
> names. Every user's peripherals differ, so a document naming one bench's
> hardware dates immediately and invites a reader to read the absence of their own
> device as incompatibility. VID/PID and descriptor hashes are kept precisely
> because they are identifiers a reader can match against their own device.

- [x] **Step 5: Create phase checkpoint tag**

```powershell
git tag duo-input-input-runtime-v0.1
```

> **DONE.** `duo-input-input-runtime-v0.1` exists, and its tag message lists what
> was accepted on hardware rather than what was built: routing, the text macro,
> release-on-disconnect, eight profiles across a power cycle, and 320 route toggles
> with no dropped command and 154 030 SPI frames without a CRC error.

### Task 4: Execute configurator and release

**Files:**
- Read/execute: `docs/superpowers/plans/2026-08-25-duo-input-configurator-release.md`

**Interfaces:**
- Consumes emulator, real U1 CDC and binary compiler.
- Produces Windows installer, release UF2 files, compatibility matrix, hashes and user documentation.

- [x] **Step 1: Execute Configurator Tasks 1–9 against emulator first**

No UI task may wait for physical hardware when the emulator contract covers it.

> **DONE.** Tasks 1-9 were built and tested against the emulator: `a4378f6`,
> `60373c9`, `cf8d483`, `788d4ef`, `5fa7674`, `caf6aa2`, with tag
> `duo-input-configurator-v0.1` marking the point. No UI task waited on hardware.
> One step inside Task 9 is still open and is not an emulator step: the clean
> Windows VM installer smoke.

- [x] **Step 2: Run real-device integration**

Repeat connect/read/write/capture/test/diagnostics flows against U1.

> **DONE (2026-08-30): 9/9 against the board.** Read-only pass 5 passed, 4
> skipped; with `DUO_INPUT_HIL_WRITE=1`, **9 passed** - connect, read, write,
> capture, test-run and diagnostics all exercised against the real U1.
>
> Worth keeping: the run that failed first was worth more than the pass. A test
> named "reports its status" called `get_diagnostics()` and waited only on
> `operation_succeeded`, turning a refusal answered in half a second into an
> anonymous five-second timeout. Given the reason instead of the timeout
> (`bad_payload - GET_DIAGNOSTICS payload has the wrong size`, in 0.51 s) it named
> the actual cause: the board was running a `DUO_CH375_PROBE` image whose
> GET_DIAGNOSTICS returns probe text instead of 43 binary bytes. **The silence had
> been manufactured by the test.** An operating rule came out of the same session
> and is now followed: any run that writes to the board restores the operator's
> configuration immediately afterwards, in the same breath.

- [x] **Step 3: Execute Task 10 HIL and release workflow**

All latency, soak, power-cut and compatibility metrics must be recorded.

> **DONE, WITH LIMITS (2026-08-30).** Every scenario the rig can reach was run
> against the board, and the ones it cannot are recorded as unmeasured with their
> reason and the rig it would take - which is what "recorded" has to mean here,
> and is not the same as "passed".
>
> Recorded as measurements: keyboard 304 samples and mouse 33 321 samples, both
> p95 <= 2 ms against a 20 ms budget, maxima 1.899 and 1.916 ms, no gap over 50 ms;
> control-link round trip p95 2.632 ms; two devices identified by VID, PID,
> descriptor hash and button count.
>
> Recorded as unmeasured, each with its reason: end-to-end keystroke p95 (this rig
> cannot measure it at all - nothing timestamps a finger); U2's release on link
> loss (no fault staged, by the operator's decision); eight of the ten peripherals
> (U1 has two ports); the 24-hour soak (baseline written, run open);
> `config_power_cut` and `profile_power_cycle` (this runner neither writes
> configuration nor cuts power); and `route_toggle`'s own three questions, which
> are visible only at the computer an event was routed to.
>
> The release workflow half ran end to end: `build_release.ps1 -Version 0.1.0-rc1`
> assembled `dist/release/0.1.0-rc1` including the Pico build, Nuitka and Inno
> Setup, and the folder was rebuilt at `b7a97c5` once the images were reproducible.

- [x] **Step 4: Run final clean verification**

Run native, Python, Pico build, clean-VM installer and SHA-256 checks from a clean checkout.

> **DONE except the clean-VM installer, which was declined (2026-08-30).** The
> clean checkout was the point of the step and it earned its keep: two defects
> exist only away from the tree the work was done in, and both were found here.
>
> `generate_protocol.py --check` compared bytes; the generator writes LF and git
> hands Windows CRLF, so it declared the protocol stale in **any** fresh clone,
> exiting 1 with no output - and `build_release.ps1` runs it first and refuses to
> continue, so **a release could not be built from a fresh clone at all**
> (`4e53fc3`). And the firmware images were not byte-reproducible: two bytes out of
> 307 200, the build date the SDK embeds, so `SHA256SUMS.txt` described one
> afternoon rather than one commit. The date now comes from `SOURCE_DATE_EPOCH` or
> the HEAD committer date, never the clock (`be5d35a`, `cmake/source_date.cmake`).
>
> Verified in two independent worktrees at the same commit - this one and `C:\dv`:
>
>     duo_u1_main.uf2      78ed70e22afc0d048235b25e1c4456782673d00f481352acfe1e2b193e115dbc
>     duo_u2_endpoint.uf2  00a2386f0f3e5e923467971c685ca5045e2681ac6bfe06c046d86f1fca377a33
>
> identical in both; `--check` exit 0 in both; ctest 36/36 in both; `pytest tests`
> 90 in both; firmware artifact contracts 13 in both.
>
> **The clean-VM installer run was not performed** - the operator declined it and
> no VM exists here. It is the configurator plan's Task 9 Step 4 and is recorded
> there as NOT DONE. The tick on this step covers native, Python, Pico build and
> SHA-256 from a clean checkout, and nothing more.

- [x] **Step 5: Create release candidate tag**

```powershell
git tag duo-input-v0.1.0-rc1
```

> **DONE.** `duo-input-v0.1.0-rc1` at `b7a97c5`. Its tag message lists what is
> verified from a clean checkout, what was verified on hardware, and - in its own
> section - what is not verified: the clean-VM installer run the operator declined,
> end-to-end latency this rig cannot measure, eight of ten peripherals, the open
> soak, and U2's release on link loss.

## Overall Completion Gate

- Four subordinate completion gates pass.
- No open test failure is waived without a written spec change.
- The two versioned UF2 images are byte-reproducible from a clean checkout of the
  tagged commit: the same commit built in two trees produces identical SHA-256.
  Their build date comes from `SOURCE_DATE_EPOCH`, else the commit, never the clock
  (`cmake/source_date.cmake`).
- The Windows x64 installer is built from that same commit and its hash is recorded,
  but it is **not** byte-reproducible, and the gate does not claim it is. Two causes
  were measured, both outside this repository: Nuitka stamps `DuoInput.exe`'s PE
  header with the build clock, and Inno Setup stores each payload file's
  modification time (back-to-back compiles of untouched input are identical;
  changing one file's mtime changes the installer hash). Its SHA-256 therefore
  identifies one build, not one commit.
- Compatibility, latency and 24-hour soak results are attached to release notes.
- Prototype RC may ship for private testing; commercial shipment remains blocked by legitimate USB VID/PID and Windows code-signing certificate.

> **OVERALL GATE STATUS, assessed 2026-08-30 against the five plans as they now
> stand. Three bullets met, one met with a stated limit, one partially met, one
> not met - so the gate does not pass.** The full
> assessment, with the evidence behind each line, is in
> `.superpowers/sdd/2026-08-25-duo-input-roadmap/progress.md`.
>
> 1. **Four subordinate completion gates pass — NOT MET; two of the four have an
>    unmet bullet.** Foundation: MET (closed 2026-08-26 at `c27a314`, sanitizer
>    campaigns clean). Core firmware: **NOT fully met** — "Synthetic keyboard/mouse
>    input reaches PC1, PC2 and Both" was blocked for want of a second computer and
>    is now unreachable, because `16afc0b` deleted the synthetic producer. Input
>    runtime: MET, with the physical-toggle bullet standing at 320 rather than
>    1000. Configurator/release: **NOT met** — the installer has never been run on
>    a clean Windows machine without Python and Qt.
> 2. **No open test failure is waived without a written spec change — MET.** Every
>    suite is green at the tagged commit: native ctest 36/36, `pytest tests` 90,
>    firmware artifact contracts 13, configurator port-free suites 560 + 34, the
>    real-device pass 9/9, `tests/hil` 76. Nothing is skipped to keep a run green.
>    Four requirements were *narrowed*, and each is written into its plan beside
>    the step: SW1/SW2 and the factory confirmation dropped because the hardware
>    will not have buttons; the TXS OE step dropped because OE is tied to VCCA;
>    Task 7's fuzz red state ruled to be the missing targets rather than a
>    manufactured crash; the physical toggle count ruled at 320.
> 3. **The two UF2 images are byte-reproducible from a clean checkout — MET, and
>    measured rather than asserted.** Built in two independent worktrees at the
>    same commit, `78ed70e2…` and `00a2386f…` identical in both. Before `be5d35a`
>    they were not: two bytes out of 307 200, the build date the SDK reads off the
>    clock. `cmake/source_date.cmake` now derives it from `SOURCE_DATE_EPOCH` else
>    the HEAD committer date, in CMake rather than only from the environment,
>    because a plain `cmake --build` from a fresh clone would otherwise bake the
>    clock back in.
> 4. **The installer is built from that same commit, its hash recorded, and not
>    byte-reproducible — MET as written.** Both causes were measured, not assumed:
>    two back-to-back `ISCC` compiles of unchanged input give an identical hash,
>    and changing one payload file's mtime changes it — Inno embeds source mtimes,
>    and Nuitka stamps the PE header with the build clock (`2026-08-30 10:16:53Z`
>    on the measured build). Cause 2 is fixable here; cause 1 is not, without
>    post-processing a third-party compiler's output. The bullet claims one build,
>    not one commit, which is what the evidence supports.
> 5. **Compatibility, latency and 24-hour soak results attached to release notes —
>    PARTIALLY MET.** `RELEASE-NOTES.md` points at `compatibility-matrix.md` and
>    both ship in `dist/release/0.1.0-rc1`. Compatibility and latency are there and
>    are recordings. **There are no soak results, because the soak has not run** —
>    the matrix carries it as Unmeasured. The absence is disclosed rather than
>    hidden, by the notes' own line: "An empty row means untested, not passed."
> 6. **Prototype RC may ship for private testing; commercial shipment remains
>    blocked — HOLDS, with one caveat that belongs beside it.** Nothing in this
>    branch touches USB VID/PID or code signing, and the installer is unsigned.
>    The caveat: the installer has never been run on a machine without Python and
>    Qt, so "may ship for private testing" is a decision taken knowing that the
>    first clean machine to receive it is also the first test of it.
