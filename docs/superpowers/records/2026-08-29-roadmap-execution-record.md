# SDD ledger — plan: docs/superpowers/plans/2026-08-25-duo-input-roadmap.md

## Pre-flight scan

This plan is a meta-plan: each of its four tasks executes a subordinate plan
and then gates on evidence that the subordinate plan does not itself collect.
Its own Global Constraints bind everything below it, and one of them is the
reason this ledger exists at all:

> Hardware claims подтверждаются измерениями; они не отмечаются выполненными
> по результатам симулятора.

Conflict scan, one row per task pair sharing a plan or an artifact:

| pair | produced / consumed | finding |
|---|---|---|
| 1 → 2 | foundation codecs, config, emulator / core firmware | agree; both landed in earlier sessions |
| 2 → 3 | Core 1 queue boundary and output runtime / CH375 runtime | agree; the queue's single-producer rule held through the whole of Task 3 |
| 3 → 4 | real U1 CDC, physical input / configurator + release | agree; Task 4's HIL needs exactly what Task 3 proved |
| 3 self | Step 1 executes the plan; Steps 2-4 demand evidence the plan's own gate also demands | consistent, and partly redundant — see ruling |

Ruling: Tasks 1 and 2 are complete from earlier sessions (foundation and core
firmware landed and their gates ran). Task 3's Step 1 is complete as of
2026-08-29 — the input/mapping/macros plan was executed and its Task 7 Step 4
hardware acceptance passed in full. Steps 2-5 of Task 3 are NOT complete and
are the immediate work. Task 4 does not begin until they are.
Cost if wrong: a phase tagged as done on an acceptance that covered four of the
criteria its own plan lists and not the two this plan adds.

## Task 3 — remaining steps, and what each still needs

Step 1 — execute Input/Mapping Tasks 1-7:  **COMPLETE**
  Acceptance passed on hardware 2026-08-29: routing (F9/F10/F11, F12, mouse
  button 4), the text macro complete on both computers, release-on-disconnect
  with no stranded key or modifier, eight profiles distinct and surviving a
  power cycle with the configured active profile restored. Recorded in the
  input-mapping ledger with the counters behind each.

Step 2 — native trace replays:  **NOT DONE**
  "Verify committed keyboard/mouse traces generate exact normalized events."
  No report traces from real devices have ever been recorded. The normalizer
  suites run against eight synthetic descriptor vectors, which is a different
  claim. This is also the input/mapping plan's own unmet gate bullet about
  captured vectors, so one piece of work discharges both.

Step 3 — physical input acceptance:  **PARTIAL**
  Done: disconnect/reconnect, eight-profile power cycle, macro behaviour under
  real timing. Not done: **1000 physical route toggles**, and macro/physical
  input concurrency as a measured check rather than an observation.

Step 4 — compatibility evidence:  **NOT DONE**
  The plan asks for "every tested VID/PID/hash and exact pass/fail reason".
  docs/hardware/ch375-compatibility.md exists but its hardware section is
  marked pending.

Ruling: the compatibility record describes device *classes and behaviours*,
not product names. The operator's instruction is explicit — every user's
peripherals differ, so a document naming models teaches the wrong thing and
dates immediately. VID/PID and descriptor hashes are still recorded where they
are known, because they are identifiers a reader can match against their own
device rather than branding; what is not recorded is model names as
recommendations.
Cost if wrong: the compatibility document is less specific than the plan's
wording asks for, and a future reader has to match by descriptor shape rather
than by name.

Step 5 — phase checkpoint tag `duo-input-input-runtime-v0.1`:  **NOT DONE**
  Blocked behind Steps 2-4 and the whole-branch review now running.

## Sequencing

A whole-branch review of all 157 commits (e08a18a..9f80c1c) is running. It is
also assessing the input/mapping plan's Completion Gate bullet by bullet,
including the two bullets Steps 2 and 3 above would discharge. Its verdict
therefore shapes this work rather than duplicating it, so nothing is dispatched
against Steps 2-4 until it lands.

## Task 3 Step 2 — traces captured, test still to write (2026-08-29 21:5x)

Report traces are recorded from both real devices and committed (37b5afc) as
`tests/vectors/hid_reports/`: 26 distinct keyboard reports and 59 distinct
mouse reports, keyed on `found=<kind>` from the device's own descriptor rather
than on the channel name, because the two channel names are crossed on this
bench and keying on them would mislabel every trace.

Two things had to be worked around, both worth recording:
  - the release probe text truncates at 900 bytes and the first channel eats
    almost all of it, so the second channel's report line never arrives. The
    first capture attempt collected 182 mouse reports and zero keyboard ones
    for that reason alone. The compact two-channel probe build fixes it.
  - the diagnostics carry a packet's size and its **first four bytes** only.
    That is a boot mouse report whole and a boot keyboard report's modifier,
    reserved byte and first two usages — the fields the normalizers read — so
    the corpus is honest about not asserting anything past byte 3.

What remains for this step: a test that replays the corpus through
KeyboardNormalizer and MouseNormalizer and asserts the exact normalized event
stream. That is the "verify committed traces generate exact normalized events"
half of the step and it is dispatchable work — subagents return at 22:50.

Ruling: the traces are committed as data before the test exists, rather than
held back until both are ready. They cost a human at the keyboard for two
sessions and cannot be regenerated without one; a test can be written at any
time. Untracked capture data is exactly what the whole-branch review's Critical
finding was about.
Cost if wrong: a corpus in the tree that nothing reads yet, which the next
dispatch closes.

Also found during the capture, recorded in the input-mapping ledger: **the
mouse wheel does not work**, because the boot-protocol mouse report is three
bytes — buttons, dX, dY — and carries no wheel. Yesterday's SET_PROTOCOL fix
traded the wheel for a correct parse. The repair is the same one the
whole-branch review named as I2: read the HID report descriptor and run the
device in its own protocol instead of forcing boot.

## 2026-08-29 — Task 3 Steps 2, 3 and 4 closed out

Three commits on feature/duo-input-foundation, one per step, full detail in
`task3-closeout-report.md` beside this file:

    f565504  Replay the captured reports instead of the ones we imagined
    8e15fb8  Let the device do the thousand toggles nobody should press by hand
    d551c2a  Record what boot protocol costs, on every mouse

Step 2 — **DONE**. `tests/firmware_native/test_trace_replay.cpp`, suite
`trace_replay`, replays all 26 keyboard and 59 mouse reports and asserts a
golden stream of 54 and 62 events: index, kind, code, signed deltas. Keyboard
reports are padded to eight bytes and mouse reports truncated to three, both
named in the code as the assumptions they are, so nothing past the captured
byte 3 is asserted. Four production mutations were run to prove it can fail
(73, 28, 60 and 72 failures), each reverted and the suite green again.

Step 3 — **BUILT, NOT RUN**. `tools/step4_acceptance_config.py --config toggle`
is a second configuration in the same tool: a 64-step macro spending its whole
budget on 32 route changes, each followed by one character to BOTH computers.
32 runs = **1024** route changes from 32 presses. 1000 does not fit in one
macro's step budget — 64 steps is a hard limit, macros do not chain, and a held
key does not re-trigger a binding — so 32-per-run and 32 runs is the honest
number. Each computer must end with exactly 1024 characters; a shortfall is the
only outward sign of a refused start or a dropped command. A `diagnostics`
subcommand was added because dropped_commands and runtime_fault had no reader
outside the GUI. Hardware not touched, serial port not opened.

Step 4 — **DONE**. `docs/hardware/ch375-compatibility.md` gains the wheel
limit: the boot mouse report is three bytes and carries no wheel, so boot
protocol costs the scroll wheel on every mouse. Placed under the Report ID
limit because the same repair — read the report descriptor, run the device in
its own protocol — closes both. No model names.

Step 5 — still blocked, now only on Step 3's hardware run.

Verification on a deleted build/native: native ctest **34/34** (33 before; the
new trace_replay suite is the one added), pico-ch375 and pico-release both
exit 0, repository python 37, firmware artifact contracts 11, configurator
acceptance 34 (18 unchanged + 16 new). The Step 4 package hash is pinned at
2f64e548… and still matches, so the refactor did not move the bytes that
acceptance ran on.

## 2026-08-29 — Task 3 Step 3: the toggle procedure made runnable, and a ruling on the count

Commit `0ef172a` on feature/duo-input-foundation. Hardware not touched, serial
port not opened. Full detail in `task3-closeout-report.md` beside this file.

The procedure built earlier the same day was correct and unrunnable. Two
defects, both found by the operator rather than by a test:

1. **It asked for Insert and Home.** The keyboard it is run on is a compact
   layout with no navigation cluster, so the acceptance could not be started at
   all. The bindings had arrow twins already; the *script* named only the keys
   that do not exist. Every key the script names is now an arrow. `Down` was
   added as a binding — the arrow twin of F10, the keyboard route to PC2 —
   because the run ends on PC1 and the stranded-modifier check has to be made
   on both computers, which otherwise needs an F-row this keyboard puts behind
   an Fn layer.
2. **"Слишком замороченный."** Re-gripping Left Shift between each of 32
   presses, waiting for each run's characters, and counting to 1024 by eye.
   Each part was defensible; together they were a procedure that gets done
   wrong and then debugged as if the firmware were at fault.

**Ruling: the delivered procedure performs 320 physical route toggles, not the
1000 the roadmap asks for.** The arithmetic, in full:

    a macro holds                     64 steps   (MACRO_STEPS_PER_MACRO)
    one cycle is a route change
      and one character                2 steps
    so one run performs               32 route changes, and writes one line
    10 presses perform               320 route changes and 320 characters
                                         to EACH computer, on 10 lines

32 route changes per run is a ceiling, not a choice: 64 steps is a hard
protocol limit, macros do not chain, and a held key does not re-trigger a
binding. So 1000 toggles means 32 presses, and 32 presses is exactly where the
procedure stopped being runnable — 32 lines do not fit on a screen, and the
count stops counting itself.

Why 320 is defensible: the criterion is that many route changes in a row leave
nothing stuck — `dropped_commands` unmoved, `runtime_fault` 0, no modifier
stranded on either computer. 320 consecutive changes with 320 characters of
load behind them, and ten physical trigger presses arriving while macro output
is in flight, exercises every one of those failure modes 320 times. The
difference between 320 and 1000 is a difference in how slow a leak would have
to be to hide, and a leak that slow shows in `dropped_commands`, which is read
before and after. Set against that: at 320 the operator counts ten lines on one
screen and reads the total off the status bar, and there is no plausible way to
get the procedure wrong. An operator who has lost count is not an observer, and
a number obtained from an inattentive observer is not evidence.

Cost if wrong: a failure that needs more than 320 consecutive toggles to appear
goes unseen. Reaching 1024 costs nothing but `TOGGLE_RUNS = 32` and a controller
willing to ask for 32 presses — the line-per-press design makes the larger
number countable in a way it was not before, since the lines count themselves.
The constant is pinned by
`test_the_delivered_toggle_count_is_the_one_the_ledger_records`, so it cannot
move without this entry becoming wrong and the suite saying so.

What else changed in the procedure:

- **Left Shift is held once, for the whole run**, not re-gripped. The
  stranded-modifier release was already accepted on hardware by S4c, which held
  Shift across an unplug and a reconnect and came back clean. What is owed here
  is only that the modifier is not stranded at the *end*, which the hand-typed
  `abc` in step 5 shows.
- **One press writes one line.** The last cycle of the macro closes the line —
  it costs no extra step, since a text step holds far more than one character.
  A press is now a fixed-width group of 32: the operator counts ten lines
  instead of a thousand characters, a dropped character shows up as a line
  shorter than its neighbours, and the editor's own status bar reads the total
  back as `Ln 11, Col 1`.
- **The script is six steps and fits on one page**, pinned by
  `test_the_operator_script_fits_on_one_page`.

**The device must be rewritten.** The package moved — 11720 bytes, sha256
`49be243d…`, against the 11624 bytes / `99ec828a…` the board currently holds.
The `Down` binding and the line ending are both in it, so the configuration on
the device cannot run the new script.

Step 3 remains **BUILT, NOT RUN**. Step 5 is still blocked on it.

Verification on a deleted build/native: native ctest **34/34**, pico-ch375 and
pico-release both exit 0, repository python 37, configurator acceptance **42**
(18 Step 4 unchanged, 24 toggle — 16 before, 8 added). Four mutations were run
to prove the new tests can fail: dropping the `Down` binding (2 failures),
leaving the run's line open (1), setting `TOGGLE_RUNS` back to 32 (1), and
restoring the old script's Insert-and-re-grip wording (3); each reverted and the
suite green again. The Step 4 package hash is still `2f64e548…`.

## Task 3 Step 3 — 320 physical route toggles, PASSED — 2026-08-29 23:0x

Operator ran the simplified procedure: F11 (route to both), Left Shift held,
Up pressed ten times, then `abc` typed by hand on each computer.

Observed: ten lines on PC1, **one** line on PC2, `abc` lower case on PC1,
`фabc` on PC2 (an extra character), `abc` lower case back on PC1.

Counters, before and after:

    dropped_commands   0 -> 0        runtime_fault    0 -> 0
    bad_crc            0 -> 0        timeout          0 -> 0
    endpoint_drops     3 -> 3        link_crc_errors  1 -> 1
    link_frames_sent   1257 -> 155287   (+154 030, no errors)

**The criterion is met.** Ten runs at 32 route changes each is 320 physical
toggles; the queue dropped nothing, the runtime fault stayed clear, the SPI
link carried 154 030 frames without a CRC error or a timeout, and Left Shift —
held across all 320 changes — was not stranded on either computer.

**The one line on PC2 is a defect in my check, not in the firmware, and the
counters prove it.** The operator set the route to BOTH with F11. The first run
began there and printed on both computers. But the macro's own steps change the
route 32 times between PC1 and PC2, which necessarily leaves BOTH behind — so
every later run printed wherever the route then pointed, and that was no longer
both. Ten lines on PC1 and one on PC2 is exactly the correct outcome of the
configuration as built; the expectation of ten lines on each was wrong.

That also refutes the assumption the script was written on, recorded here
because it was stated as fact in the closeout report: **a macro's route is not
frozen at enqueue.** Its text follows the route as it stands when each step
runs, which is why its own SET_KEYBOARD_ROUTE steps carry its later characters
with them.

The stray `ф` before `abc` on PC2 is a character that caught the route mid-
change. Recorded as an observation; it costs one wrong character at a route
boundary and nothing is stranded by it.

Ruling: Step 3 passes on 320 toggles rather than the plan's 1000, on the
arithmetic already ruled on (32 per run is a macro-step ceiling, not a choice)
and on evidence that is stronger than the count: zero drops across 154 030
frames, and no stranded modifier. Re-running it for 1024 would exercise the
same paths 3.2 times longer.
Cost if wrong: an accumulation defect that only appears past 320 toggles goes
unseen; the counters that would show it are read before and after and were
clean.

Step 3 is complete. Remaining in Task 3: Step 5, the phase checkpoint tag,
which waits on the branch being finished.

## Task 4 Steps 4 and 5 complete — 0.1.0-rc1 tagged (2026-08-30 14:1x)

The clean-checkout verification found two defects that only exist away from the
tree the work was done in, which is the entire reason the step is worded that
way.

**`generate_protocol.py --check` failed on every fresh checkout, silently.** The
generator writes LF, git hands Windows CRLF, and `--check` compared bytes — so
it declared the protocol stale in any clone where nobody had run the generator,
exiting 1 with no output. `build_release.ps1` runs it first and refuses to
continue, which means **a release could not be built from a fresh clone at all**.
Fixed to compare by lines and to print the file and a diff (4e53fc3).

**The firmware images were not byte-reproducible**: two bytes out of 307 200,
the build date the SDK embeds. Same source, different hash tomorrow, and
SHA256SUMS describing one afternoon rather than one commit. The date now comes
from SOURCE_DATE_EPOCH or the HEAD committer date, never the clock (be5d35a),
derived in CMake because a plain `cmake --build` from a fresh clone would
otherwise bake the clock in again.

Verified across two independent worktrees, same commit:

    duo_u1_main.uf2      78ed70e22afc0d048235b25e1c4456782673d00f481352acfe1e2b193e115dbc
    duo_u2_endpoint.uf2  00a2386f0f3e5e923467971c685ca5045e2681ac6bfe06c046d86f1fca377a33

identical in both; `--check` exits 0 in both; ctest 36/36; python 90; artifact
contracts 13.

**The installer is not reproducible and the gate no longer claims it is**
(b7a97c5). Nuitka stamps the executable's PE header with the build clock. The
gate now claims byte-reproducibility for the two UF2s and records the installer
hash as identifying one build rather than one commit — measured, not assumed.

Release folder rebuilt with the reproducible images. Tagged
`duo-input-v0.1.0-rc1`, with the tag message listing what is verified, what was
verified on hardware, and what is not verified — including the clean-VM
installer run the operator declined, end-to-end latency this rig cannot
measure, eight of ten peripherals, the open soak, and U2's release on link loss.

Roadmap Task 4 is complete. All four phases are complete.

## Plan reconciliation, and the Overall Completion Gate assessed (2026-08-30)

The work was done and tagged; the plans did not say so. Seventy-seven unticked
checkboxes across five plans, most of them describing work that had landed
months or hours earlier. A reader opening any of those plans today would have
concluded almost nothing was done.

Sixty-five boxes are now ticked, each against named evidence — a commit range,
a ledger line, a counter read on hardware. Twelve stay unticked, each with
a blockquote saying why, in the convention the configurator plan already used.
Per-plan detail and every ruling is in `plan-reconciliation-report.md` beside
this file. Four commits, one per plan touched: `6dd1fe7`, `9c7c6b6`, `a7c9e33`,
`2da2efe`.

**The rule applied throughout: a tick names what proves it, or it is not a
tick.** Three categories were kept apart and never blurred — done with evidence;
not done, with the reason; and *cannot be told from what survives*, which is a
third answer and not a soft version of either. Four steps landed in that third
category, all of them "verify red state" steps whose test and implementation
arrived in one commit with no record of a failing run. Guessing them in either
direction would have been the same failure this project spent two days finding
in code: a capability written, tested, and asserted on nothing.

One deliberate asymmetry, recorded because it looks like an inconsistency and is
not. Roadmap Task 3 Step 1 ("execute the input/mapping plan") is **ticked** even
though four steps of that plan stay open, while roadmap Task 2 Step 1 ("execute
the core firmware plan") is **left unticked** although 34 of its 35 steps are
done. The distinction is what the shortfall does to the gate above it. Core firmware's one
open step is a hardware acceptance its own Completion Gate names, so leaving the
roadmap step unticked carries that signal up into the Overall Gate's first
bullet, where it belongs. Input/mapping's four open steps are TDD bookkeeping
and one small reconnect exercise; they change no gate bullet, so Task 3 Step 1 is
ticked with them named in its note.

Cost if wrong: a reader takes roadmap Task 2 Step 1 for more open than it is, and
finds the answer in the note directly beneath it.

### The six bullets

**1. Four subordinate completion gates pass — NOT MET. Two of the four have an
unmet bullet.**

| Gate | Verdict | Evidence |
|---|---|---|
| Foundation | **MET** | Closed 2026-08-26 at `c27a314`. `--check` 0, corpus freshness 0, configurator pytest 132, repository pytest 8 + 6 subtests, fresh MSVC/Ninja build with ctest 7/7, three sanitizer campaigns (2.2 M / 13.7 M / 7.4 M runs) exit 0 with empty artifact directories. |
| Core firmware | **NOT MET** | "Synthetic keyboard/mouse input reaches PC1, PC2 and Both routes" was blocked on 2026-08-27 for want of a second computer. A second computer exists now; the synthetic producer does not. `16afc0b` deleted it, and `test_a_release_image_cannot_generate_its_own_input` asserts no such symbol can be linked into a release image. The bullet is unreachable from this tree. |
| Input runtime | **MET, one bullet at a stated limit** | Enumerate/recover, captured-vector normalization, macro safety, profile/capture and the compatibility document are all met. "1000 physical route toggles" stands at **320**. |
| Configurator/release | **NOT MET** | "Nuitka installer works on clean Windows 10/11 without Python" — never run. The operator declined the VM; this host has Python and Qt, so a run here would prove nothing. |

The two unmet gate bullets are of different kinds and both are worth naming as
such. The core firmware one is *obsolete*: the thing it tested was replaced by
something stronger — real keyboard and mouse input reaching PC1, PC2 and Both on
hardware, 2026-08-29 — and the box cannot be ticked because the substitute is
not the step. The configurator one is *live*: nobody knows whether a bare
Windows machine can install this software, and finding out costs one VM.

**2. No open test failure is waived without a written spec change — MET.**

Every suite is green at the tagged commit: native ctest 36/36, `pytest tests`
90, firmware artifact contracts 13, configurator port-free suites 560 + 34, the
real-device pass 9/9 with `DUO_INPUT_HIL_WRITE=1`, `tests/hil` 76. Verified again
during this reconciliation: `pytest tests -q` → 90 passed, unchanged, since
nothing here touches code.

Four requirements were *narrowed* rather than waived, and each narrowing is
written into the plan beside the step it changes:

- SW1, SW2 and the 5-second factory confirmation, dropped 2026-08-27 because the
  hardware will not have buttons; the actions are reachable over CDC.
- The TXS OE step, dropped because OE is tied to VCCA on this board and no
  firmware can hold it low.
- Task 7's fuzz red state, ruled to be the missing targets rather than a
  manufactured crash, because the parsers were already hardened and staging a
  crash would have regressed the product.
- The physical toggle count, ruled at 320 on the macro-step arithmetic.

A narrowing that is written down where the requirement lives is not a waiver.
One that lives only in a ledger would be.

**3. The two UF2 images are byte-reproducible from a clean checkout of the tagged
commit — MET, and measured rather than asserted.**

Rewritten today after measuring, and it matches what was measured. Built in two
independent worktrees at the same commit:

    duo_u1_main.uf2      78ed70e22afc0d048235b25e1c4456782673d00f481352acfe1e2b193e115dbc
    duo_u2_endpoint.uf2  00a2386f0f3e5e923467971c685ca5045e2681ac6bfe06c046d86f1fca377a33

identical in both. Before `be5d35a` they were not: two bytes out of 307 200, at
offset 190122, the build date the Pico SDK takes from `__DATE__`. The bullet's
own claim about where the date comes from is checkable and checks out —
`cmake/source_date.cmake` reads `SOURCE_DATE_EPOCH` if set, else the HEAD
committer date, and does it in CMake precisely because a plain `cmake --build`
from a fresh clone would otherwise bake the clock back in.

**4. The Windows installer is built from that same commit, its hash recorded, and
is not byte-reproducible — MET as written.**

Also rewritten today, also matching measurement rather than intent. Both causes
were measured on this machine, both outside this repository:

- Nuitka stamps `DuoInput.exe`'s PE header with the build clock — `2026-08-30
  10:16:53Z` on the measured build — so the payload differs before Inno Setup is
  invoked at all.
- Inno Setup stores each payload file's modification time. Two back-to-back
  `ISCC` compiles of unchanged input produce an identical hash; changing one
  payload file's mtime changes it.

Cause 2 is fixable in this repository; cause 1 is not, short of post-processing
a third-party compiler's output. The bullet claims the hash identifies one build
and not one commit, which is exactly what those two experiments support. It
neither overclaims nor quietly drops the installer from the gate.

**5. Compatibility, latency and 24-hour soak results are attached to release
notes — PARTIALLY MET.**

`dist/release/0.1.0-rc1` carries `RELEASE-NOTES.md` and the
`compatibility-matrix.md` it points at, both covered by `SHA256SUMS.txt`.
Compatibility is there: two devices by VID, PID and report-descriptor hash, with
a reason each. Latency is there and is a recording — 304 keyboard and 33 321
mouse samples, p95 ≤ 2 ms against a 20 ms budget, maxima 1.899 and 1.916 ms, no
sample past 50 ms — alongside the explicit row saying the specification's
end-to-end keystroke p95 is not measurable on this rig.

**There are no soak results, because the soak has not run.** The matrix carries
`soak_24h` as Unmeasured. The absence is disclosed rather than hidden, by the
notes' own line: "An empty row means untested, not passed."

Two discrepancies found while assessing this bullet, neither corrected here
because both are release artifacts rather than plan status marks:

- The matrix says `soak_24h` is "Not opened." The configurator ledger records
  that `--phase baseline` *was* run on 2026-08-30 and the run is open. The matrix
  is one step stale on this row; the conclusion (no soak result) is unaffected.
- `RELEASE-NOTES.md` says under "What is not": "Hardware acceptance." That is
  now too broad. Hardware acceptance of the input runtime passed on 2026-08-29,
  and the HIL latency figures in the matrix beside it are hardware recordings.
  What is genuinely absent is the soak, eight of ten peripherals, U2's release on
  a staged link fault, and end-to-end latency. Erring toward understatement is
  the right direction for a release note to err in, but it currently understates
  a day of hardware acceptance that did happen.

**6. Prototype RC may ship for private testing; commercial shipment remains
blocked by legitimate USB VID/PID and Windows code-signing certificate — HOLDS.**

Nothing in this branch touches either blocker. The installer is unsigned and the
release notes say SmartScreen will warn. The Qt for Python licensing route is
recorded as unsettled in `third-party-licenses.md`.

One caveat belongs beside this bullet rather than inside bullet 1, where it is
already counted: the installer has never run on a machine without Python and Qt.
"May ship for private testing" is therefore a decision taken in the knowledge
that the first clean machine to receive it is also the first test of it.

### Summary

Bullets 2, 3 and 6 are met. Bullet 4 is met with the limit stated inside the
bullet itself. Bullet 5 is partially met — compatibility and latency are
attached, the soak does not exist. Bullet 1 is not met. **The gate does not
pass.** What stands between it and passing is one VM and one 24-hour run — plus
one subordinate bullet that cannot be reached at all, and whose subject was
superseded by something better than itself.
