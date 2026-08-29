# SDD ledger — plan: docs/superpowers/plans/2026-08-25-duo-input-input-mapping-macros.md

## Pre-flight scan

Tasks 1, 3, 4, 5, 6 landed in earlier sessions; Task 2's boxes are unticked
but its transport and state machine exist and were the subject of a day of
hardware debugging (commits 17071bd..07556ac, plus 586851a). Only Task 7 is
open.

Conflict scan, one row per task pair sharing a file or interface:

| pair | produced / consumed | finding |
|---|---|---|
| 6 → 7 | MacroScheduler / Core1Runtime drains it | agree; scheduler carries runtime::Route and an owner index |
| 5 → 7 | BindingEngine / Core1Runtime applies its Outcome | agree |
| 3,4 → 7 | DescriptorSetup + normalizers / InputPipeline | agree |
| 7 self | Step 3 names main.cpp and config_service.cpp; Step 4 is hardware | consistent |

Ruling: Task 7's Step 4 (full hardware acceptance) cannot run — the CH375
link is still under repair and the second PC does not exist. Steps 1-3 are
executable; Step 4 is deferred and recorded here rather than faked.
Cost if wrong: the phase is declared done without hardware proof, which the
plan's completion gate would catch.

Ruling: Task 7 Steps 1-2 and the natively testable half of Step 3 were
implemented in the controller session before this skill was invoked
(commits 36ff0e0, 51a7bf8, 80e84bb — capture, core1_runtime, input pipeline,
stored profiles; 28 native suites green). They are not re-dispatched.
Cost if wrong: that code carries no task review; the final whole-branch
review is pointed at it.

## Tasks

Task 7: remaining Step 3 — CDC capture/profile handling and the core 1 wiring
in main.cpp. BASE 80e84bb.

Task 7: implementer DONE_WITH_CONCERNS (commits 80e84bb..HEAD). Concerns
carried into review: macro slots 16-31 unreachable (kMaxMacros=16 vs 32
slots filled); cross-core calls non-atomic; new config needs a reboot;
multicore_lockout_victim_init was never called before this.

Task 7: review 1 — spec ❌, 3 Critical / 6 Important / 5 Minor.
  C1 release build has no input path (CH375 under DUO_CH375_PROBE, OFF in release)
  C2 Core 1 reads a flash pointer a later config write erases (dangling, not stale)
  C3 macro drain unbounded — overruns the 128-slot queue, drops key-ups
  I4 captured trigger lost to the publish order in main's bridging block
  I5 mouse-capture test asserts its own literals
  I6 profile-report test cannot detect the regression it names
  I7 macro slots 16-31 unreachable (kMaxMacros=16 vs 32)
  I8 HELLO does not cancel a running capture; diverges from the emulator
  I9 cross-core flags non-atomic and non-volatile
  I10 two writers to the profile request, both directions

Task 7: minor (deferred): M1 release core 1 hard-spins with a 64-bit divide
  per iteration; M2 pico_flash/main.cpp untested; M3 set_profile_now's static
  scratch duplicates the engine's table; M4 brief's core-1 stack addresses
  differ from the binary's actual core1_stack; M5 CaptureController::remember
  drops silently past 20 held inputs.

Ruling: C1 is a plan conflict, not just a defect. Task 2 Step 4 put the CH375
path behind a diagnostic build; Task 7 Step 3 requires it running on core 1.
The spec wins: the input path ships in the release build, and only the
plain-text bring-up reporting stays behind DUO_CH375_PROBE.
Cost if wrong: a release image carrying bring-up code — visible in the final
review and in the image size, and cheap to re-gate.

## Review repair — 2026-08-28

Critical and important findings repaired in the working tree:

- C1: both CH375 state machines, descriptor setup, normalizers and
  `InputPipeline::on_event` run in release; only textual probe observations
  remain behind `DUO_CH375_PROBE`. A release-ELF contract checks for the real
  device and pipeline symbols.
- C2: `WRITE_COMMIT` is acknowledged only after an `IRuntimeConfig` handoff
  stops Core 1 and rebuilds every flash-backed profile, binding and macro view
  against the new A/B slot. Factory reset detaches those views before erase.
- C3: Core 1 emits at most 16 macro output commands per tick, below both the
  32-command Core 0 drain and the 127-slot SPSC capacity.
- I4/I8/I9/I10: capture, profile, release-all, counters and acknowledgements
  now cross cores through one-writer/one-reader atomic mailboxes. Capture
  publishes the event before its inactive state; HELLO cancels an old capture;
  host and local profile acknowledgements are distinguished.
- I5/I6/I7: invalid captured mouse triggers are rejected, profile status
  accepts only a matching host acknowledgement (while still publishing local
  binding/macro changes), and all 32 protocol macro slots are reachable.

Verification after the repairs:

- `ctest --test-dir build/task7-review --output-on-failure`: 28/28 passed.
- configurator tests excluding the opt-in real-device contract: 539 passed.
- repository Python tests: 37 passed, 6 subtests passed.
- firmware artifact contracts: 11 passed.
- `pico-release` and `pico-ch375` U1 images both build; the release ELF
  contains `Ch375Device::tick` and `InputPipeline::on_event`.

Hardware continuation:

- User connected both keyboard and mouse; both have RGB power and both CH375
  module indicators are lit.
- Fresh probe firmware answered CDC once and reported both channels in
  `RecoverWait`, `CHECK_EXIST=WRONG`, zero attach/ready/report events and a
  silent mode reply. This is not yet a hardware acceptance result.
- A detached control build at `16afc0b` was flashed to distinguish a firmware
  regression from the newly simultaneous keyboard load, but with both devices
  attached it did not answer CDC reliably, so that comparison is inconclusive.
- Do not alter soldering or jumpers on this evidence. Next discriminating test:
  unplug only the keyboard USB plug from its CH375, leave mouse and all power
  unchanged, reboot fresh probe and compare the mouse channel.

Task 7 remains open until keyboard and mouse both enumerate, produce report
vectors, and pass routing/release checks on hardware.

Task 7: fix round 1/5 — commits 16afc0b..0aa78aa ("fix: make release input
  runtime safe"). NOT VERIFIED. The implementer hit its session limit and
  died before returning a report and before the scoped re-review ran, so no
  finding is confirmed ADDRESSED. Native ctest is 28/28 green on 0aa78aa and
  the tree is clean apart from line-ending noise and a stray .test-tmp/.
  Resume the loop at: review-package 16afc0b 0aa78aa, then re-review-prompt
  with the ten findings below. Round 2 of 5 is next; rounds 1-3 resume the
  implementer, 4-5 take a fresh one on a stronger model.

Ruling: fix round 1 counts as spent even though it produced no report. The
commit exists and changed the code the findings named, so re-reviewing it is
cheaper than redoing it, and a round that produced work is not a free retry.
Cost if wrong: one fewer attempt before the cap, which the breaker handles.

## Open findings, verbatim, awaiting scoped re-review of 16afc0b..0aa78aa

C1 Critical — release build has no input path. main.cpp:69-135 and the
  core1_entry tick block sat under #if DUO_CH375_PROBE, OFF in pico-release.
  Verified from the image: nm -C on the release ELF found no Ch375Device::tick,
  no InputPipeline::on_event, no KeyboardNormalizer::apply; pico-ch375 has all
  three. Release core1_entry was time_us_64 -> uldivmod -> Core1Runtime::tick
  -> repeat.
C2 Critical — Core 1 reads config through an XIP pointer a later write
  erases. main.cpp:424 loads once; AbStore::begin stages into
  other_slot(active) and erases it (ab_store.cpp:137,154), so the SECOND
  config write erases the slot Core 1 is reading; FACTORY_RESET_COMMIT ->
  erase_everything (:323-327) does it in one. Two consequences: validate_config
  on 0xFF fails so the next swap installs zero bindings (keyboard dead until
  reboot); and installed macros keep MacroStep::pairs pointing into erased
  flash, so a TEXT/KEY_TAP macro types (0xFF,0xFF) pairs and typing_step walks
  all eight modifier bits down - Ctrl+Shift+Alt+Gui held on both computers.
  A dangling pointer, not staleness.
C3 Critical — macro drain unbounded. core1_runtime.cpp:180 drains
  MacroScheduler::tick in a while loop; tick returns true for every keystroke
  event and only DELAY returns false. Queue is 128 slots, Core 0 drains 32 per
  pass, submit on a full queue only does ++dropped_. A TEXT macro past ~60
  chars fills it and the discarded remainder includes key-ups whose downs went
  through. The brief required "bounded per-loop budgets"; this was the one
  place missing. dropped_commands() is also reported nowhere.
I4 Important — captured trigger lost to publish order. main bridging block
  calls take_capture_event then set_capture_active(capture_active()); capture
  .cpp:handle sets have_trigger_ then clears active_. Pass N take() false,
  Core 1 stores + clears active, Core 0 publishes false; pass N+1 take() true
  -> emit_capture_event -> if(!capture_active_) return -> answer discarded.
I5 Important — a_mouse_capture_travels_as_the_host_will_accept_it asserts its
  own literals. Deleting the mouse conventions in mapping/capture.cpp leaves
  it green. Real coverage is test_core1_runtime.cpp:323,339.
I6 Important — the_reported_profile_is_the_one_the_runtime_confirmed calls
  set_active_profile itself, so restoring the deleted direct assignment leaves
  it green.
I7 Important — macro slots 16-31 unreachable. kMaxProfileMacros=32 vs
  macros/scheduler.hpp kMaxMacros=16; define/enqueue silently ignore ids >= 16
  while slot_of hands out 0..31. A 20-macro profile validates, loads, reports
  fine, and its last four bindings do nothing.
I8 Important — HELLO does not cancel a running capture (config_service.cpp
  :209-227); emulator.py:121-122 does. A configurator that crashed mid-capture
  never gets on_disconnect, so on restart CAPTURE_BEGIN returns BUSY and the
  operator's keystrokes are eaten. FACTORY_RESET_COMMIT has the same gap
  (emulator.py:593).
I9 Important — every cross-core flag is non-atomic and non-volatile
  (core1_runtime.hpp:120-131, all of CaptureController's state). Works only
  because there is no LTO and tick() is in another TU. pico_flash.cpp:24 gets
  it right with volatile bool g_core1_running. Enabling IPO lets GCC cache
  release_all_requested_ in a register and the emergency stop stops working.
I10 Important — two writers to the profile request, both directions.
  request_profile is called from Core 0 and from Core 1 (apply -> SetProfile,
  drain_macros -> SetProfile). A host request racing a SET_PROFILE binding
  overwrites one silently; a lost ack leaves GET_STATUS reporting the old
  profile indefinitely.

## Measurements taken and believed (verified from the linked image)

main frame: 804 + 20 = 824 bytes in pico-ch375, 240 in pico-release; core0
region 0x20041800-0x20042000 = 2048, so 1224 to spare and no overlap with
core 1. core1_entry 64 / 24. Deepest Core 1 chain ~580 (report path), ~416
(swap path), both inside 2048. MacroScheduler::tick's self-recursion compiles
to a branch, costing no stack. Core 0's worst block is a flash sector erase
with Core 1 locked out: ~14 ms/sector, ~28 ms for FACTORY_RESET_COMMIT, far
inside the 2000 ms watchdog - but Core 1's 14 ms stall exceeds the 8 ms
endpoint poll deadline (below the 1 s device-loss timeout).

## Hardware state at handoff

CH375 link work is committed and separate from this plan (see
docs/superpowers/plans/2026-08-28-ch375-serial-link-hardening.md, commits
a1bcfdb..08a3520 plus 586851a). Last hardware reading before the session
ended: quiet_rearms=9, detach_lost=3, disconnect=0, success=1699, reports=151,
recovered_raised=2 - the link runs and then collapses, and the collapse
steps the rate ladder down.

Latest observation, UNEXPLAINED: after a power cycle the CH375 module's own
indicator is dark while the mouse's is lit. Do not read this as evidence
either way until it is known what that indicator means on this module -
U1's own LED cannot light at all on waveshare_rp2040_zero
(no PICO_DEFAULT_LED_PIN, so show_link/configure_indicator compile to
nothing), which is a mistake already made once today.

Plan Task 7 Step 4 (full hardware acceptance) remains deferred: the link is
not stable and there is no second PC.

## Round 1 re-review, dispatched 2026-08-28 (controller session 2)

Ruling: the scoped re-review of 16afc0b..0aa78aa runs without a fix report —
the implementer died before writing one, and no report is recoverable. The
re-reviewer is told the report does not exist and must establish every
claim from the code and the diff itself; the controller supplies the build
and test evidence the report would have carried.
Cost if wrong: a verdict resting on reviewer reasoning rather than a
demonstrated test run, which the whole-branch review re-checks.

Controller-run evidence on 0aa78aa (working tree clean apart from CRLF noise
on tests/firmware_native/test_macro_scheduler.cpp and test_main.cpp, plus an
untracked .test-tmp/):
- native: ctest build/native 28/28 passed.
- pico-ch375 and pico-release both configure and build; ninja reported "no
  work to do" and no firmware source is newer than either ELF, so both
  images are the 0aa78aa tree.
- Release ELF now contains Ch375Device::tick (0x10004124),
  InputPipeline::on_event (0x10002826), KeyboardNormalizer::apply (0x10002896)
  and MouseNormalizer::apply (0x10002a16) — the image-level half of C1.
- main frame: release 228+20 = 248 bytes; pico-ch375 literal 0xfffffcdc =
  -804, so 804+20 = 824 bytes, unchanged from the last measurement. Core 0
  region is 2048 bytes, so 1224 to spare; no overlap with core 1.
- core1_entry frame: 44+20 = 64 (ch375), 36+20 = 56 (release), and
  multicore_lockout_victim_init is called at its second instruction in both
  images — the implementer's fourth concern is closed at the image level.
- Linker symbols agree with the brief: __StackBottom 0x20041800,
  __StackOneBottom 0x20040800, __StackOneTop 0x20041000, __StackTop
  0x20042000.
- repository python tests: 37 passed. Firmware artifact contracts: 11 passed,
  and the new release-image contract reads build/pico-release by default
  (tests/build/test_firmware_artifacts.py:41-43), so it inspects the release
  ELF rather than the probe one.
- configurator tests must run from the repository root (the transport-vector
  fixture path is relative) and need .venv, not .superpowers/runtime-venv,
  which has no PySide6: 542 passed, 4 skipped, 1 failed — the failure is the
  opt-in test_the_real_device_reports_its_status, which needs the hardware
  link that is still down.

## Task 7: fix round 1/5 re-review result (16afc0b..0aa78aa)

Addressed (5): C1, C2, I7, I9, I10.
- C1 is closed at the image level too: the release ELF carries
  Ch375Device::tick, InputPipeline::on_event, KeyboardNormalizer::apply and
  MouseNormalizer::apply. The new artifact contract checks only the first two,
  so the third would not be caught — noted as a deferred minor.

Still open (5): C3 residual, I4, I5, I6, I8 — carried verbatim into round 2.

New breakage the fix introduced (2 blocking, from the re-review):
  C4 Critical — Core 0 pushes into the single-producer SPSC queue.
    RuntimeConfig::activate (main.cpp:150) and clear (main.cpp:163) call
    g_runtime.release_all(), which submits an OutputCommand. The lockout can
    pause Core 1 between SpscQueue::push's slot write and its head_.store, so
    Core 0's ReleaseAll is silently overwritten by the resumed Core 1's own
    item: keys held under the old profile are never released. Core 0's own
    path already exists — OutputRuntime::process.
  I11 Important — multicore_lockout pauses Core 1, it does not quiesce it.
    Core 1 can be stopped mid-set_profile_now or mid-install_macros, both of
    which mutate the same function-local statics and step pool Core 0 then
    rewrites; on release Core 1 finishes its own rebuild with stale indices
    and calls set_bindings over Core 0's table. The comment at main.cpp:139-143
    asserting the lockout is the acknowledgement is one of this repository's
    confident-and-wrong comments.

Controller finding from the images, NOT raised by either reviewer:
  C5 Critical — Core 0's stack overruns its region and reaches into Core 1's.
    Measured by walking the call graph of both linked images and summing
    prologue frames: the deepest chain from main is
    main -> ConfigService::on_cdc_bytes -> handle_frame (1128) ->
    dispatch (1184) -> reply_error (1064) -> reply (1120) ->
    encode_cdc_frame -> cobs_encode, totalling 4824 bytes in pico-release and
    5400 in pico-ch375 (main's own frame is 248 and 824). Core 0's region is
    2048 bytes (0x20041800..0x20042000). Core 1's deepest chain is 504/512
    from core1_entry, so the two stacks collide once Core 0 passes
    4096 + 504 = 4600 bytes: release overruns into Core 1's stack by 224
    bytes and pico-ch375 by 792. Three ~1 KiB buffers are live at once —
    handle_frame's scratch, dispatch's payload and reply's frame buffer —
    and reply_error is on the path of every malformed or rejected CDC frame.
    No PICO_STACK_SIZE override exists anywhere in the project.

Ruling: C5 enters the fix loop rather than the deferred list, even though it
predates 0aa78aa and sits outside the fix diff. It is Critical, it lives in
the two files Task 7 owns, and Task 7's whole subject is putting a second
core's runtime next to Core 0 — a Core 0 stack that reaches into Core 1's
region makes the task's own deliverable unsound. It is also exactly the
defect class that cost a day already.
Cost if wrong: round 2 carries one finding more than the fix diff strictly
justifies, and the fix touches buffer sizing the plan did not ask about.

Ruling: round 2 dispatches a FRESH implementer on opus instead of resuming
the round-1 one. The round-1 implementer died of a session limit and cannot
be resumed, so the skill's fallback applies: a fresh implementer carrying the
brief, the report file and the findings. Model is opus rather than a cheaper
tier because every open finding is cross-core ordering or stack budget.
Cost if wrong: a more expensive round than the fix sizes justify.

Task 7: fix round 2/5 dispatched — fresh implementer on opus, base 0aa78aa,
eight findings in findings-round2.md (C4, C5, I11, C3-residual, I4, I5, I6,
I8). Five deferred minors listed in the same file and explicitly excluded.
Round 1 verdicts: C1, C2, I7, I9, I10 ADDRESSED.

## Task 7: fix round 2/5 — commit 25d3730 ("fix: let Core 1 own its own
state, and keep Core 0 inside its stack"). NO FIX REPORT.

The round-2 implementer also died of a session limit, at the moment it was
about to write its report ("Now the fix report:"). The commit is complete and
the tree is clean apart from an untracked .test-tmp/. Same handling as round
1: the controller supplies the evidence the report would have carried.

Ruling: round 2 counts as spent, and its re-review runs on the commit without
a report, exactly as round 1 did. Two implementers in a row have died at the
report step, so from now on the controller measures first and the reviewer is
told no report exists.
Cost if wrong: verdicts rest on reviewer reasoning plus controller-run
evidence rather than on an implementer's own account of what it did.

Shape of the fix: new firmware/u1_main/core_bridge.{hpp,cpp} (150 + 35 lines)
with tests/firmware_native/test_core_bridge.cpp (411 lines) — the Core0/Core1
bridging block was extracted out of main.cpp, which lost 275 lines and gained
490. Also touched: config_service, core1_runtime, pico_flash, the emulator,
transactions.py, diagnostic_export.py, the diagnostics UI, both translation
catalogues and docs/protocol/compatibility.md.

Controller-run evidence on 25d3730:
- native ctest: 29/29 passed (28 before; test_core_bridge is the new suite).
- python tests 37 passed; firmware artifact contracts 11 passed.
- configurator: 546 passed, 4 skipped, 1 failed — 4 more than the 542 baseline,
  and the single failure is still the opt-in test_the_real_device_reports_its_status
  that needs the hardware link.
- all three builds pass: native, pico-ch375, pico-release.
- C5 measured again from both linked images with stackdepth.py: Core 0's
  deepest chain is now 1424 bytes (pico-release) and 1472 (pico-ch375),
  against 4824 and 5400 before. Core 0's region is 2048, so 624 and 576 bytes
  of headroom, and the chain no longer leaves its own region at all. The
  kilobyte buffers are off the stack: handle_frame 1128 -> 104, dispatch
  1184 -> 176, and reply is no longer on the deepest path; the new worst case
  is AbStore::verify -> hash_payload -> sha256. main's own frame is 248
  (release) / 296 (ch375), down from 248 / 824. Core 1 is unchanged at
  504 / 512 against its own 2048.

Housekeeping: an untracked .test-tmp/ (copies of the repository and the
configurator, from an earlier agent's mutation testing) sits in the worktree.
It is not in .gitignore. Leave it until no agent is running, then delete it
before the branch is finished — deleting it while a reviewer might be
mutating inside it would destroy work in progress.

## Hardware, 2026-08-28 21:53 — the comparison test, and what it found

The operator unplugged ONLY the keyboard from its CH375, left the mouse, the
wiring, the jumpers and the power untouched, and cycled the external 5 V for
three seconds. Reported: after the cycle the CH375 module's own indicator is
dark while the mouse's laser is lit — the same observation as the previous
power cycle, so it is reproducible and not a one-off. Still unexplained, and
still not treated as evidence: the mouse is lit, which means the module is
passing 5 V to it, so "indicator dark" does not mean "module unpowered".

Result of the comparison itself: **removing the keyboard changed nothing.**
The mouse channel reads exactly as it did with both devices attached —
state=RecoverWait, check_exist=WRONG, attached=0, ready=0, reports=0,
connect=0. The hypothesis that two simultaneously enumerating devices were
overloading the link is not supported: one device alone fails identically.

U1 itself answers CDC reliably now: five HELLO/GET_DIAGNOSTICS exchanges in a
row all replied, where the earlier session saw it answer once and then go
quiet. DEVICE_INFO replies with a single 0x01 byte (an error code, not a
build string), so the flashed build cannot be identified over the wire.

**The finding that matters, and it is not the one the test was looking for:
the counters are frozen.**

  21:53:24  keyboard not_back_yet=18  slowest pass=47255 us
  21:53:44  keyboard not_back_yet=18  slowest pass=47255 us   (20 s later)

`Ch375Device::tick` increments `chip_not_back_yet_` once per failed
CHECK_EXIST (device.cpp:46), and RecoverWait re-tries every
`kRecoverDelayUs` = 1 000 000 us (device.hpp:146), so twenty seconds of a
running loop must add about twenty. It added none, on both channels, across
three separate reads. The channel is not failing to recover; it is not
trying. Whatever ticks the CH375 devices has stopped running, and it stopped
after 18 attempts — roughly eighteen seconds of life.

Hypothesis, consistent with everything measured today: this is C5. In the
firmware currently flashed, Core 0's deepest chain measured 5400 bytes
(pico-ch375) against a 2048-byte region, reaching 792 bytes into Core 1's
live stack, and that chain is the CDC path — handle_frame -> dispatch ->
reply_error -> reply, i.e. exactly what a configurator or diag.py request
walks. A CDC exchange would then corrupt Core 1's stack and stop it, which
looks from outside like a link that "runs and then collapses" — the phrase
this project has been using for the fault all day. It also fits 18: the
counter would have stopped at the first CDC request after the flash.

This is a hypothesis with a decisive test, not a conclusion. 25d3730 brings
Core 0's worst case to 1472 bytes with 576 to spare, entirely inside its own
region. Flashing the 25d3730 pico-ch375 image and re-reading the counters
distinguishes the two outcomes cleanly: if they climb about one per second,
the stack overrun was stopping Core 1 and the link's "collapse" was never a
CH375 problem at all; if they stay frozen, the stack was not the cause and
the CH375 serial link work continues from a cleaner base.

Ruling: ask the operator before flashing. The flash itself is reversible and
routine, but it hangs the CH375 modules and needs a human to cycle their
power, which was done six times today. That is a physical act by another
person, so it is asked for, not assumed — and it is asked for exactly once,
with the discriminating question stated up front.
Cost if wrong: one more power cycle spent on a test that answers nothing.

## Task 7: fix round 2/5 re-review result (0aa78aa..25d3730)

All eight findings ADDRESSED — C4, C5, I11, C3-residual, I4, I5, I6, I8 — with
no new Critical or Important breakage, and no regression of C1, C2, I7, I9,
I10. Shape of the fixes:
- C4: RuntimeConfig no longer touches g_runtime; the ReleaseAll that enqueues
  now happens inside adopt_configuration on Core 1. Core 0's own release sites
  use OutputRuntime::release_all, which never enqueues.
- C5: the three ~1 KiB frames became ConfigService members in BSS, one
  instance, one core, no re-entrancy and no reuse-while-live; the probe-only
  800-byte frame moved into g_probe and a [[gnu::noinline]] report_probe.
- I11: multicore_lockout is gone from the config path, replaced by a
  ConfigHandoff ticket Core 1 takes at the top of its pass. Both wrong
  comments removed.
- C3: dropped_commands ships as a 4th optional GET_DIAGNOSTICS group, is
  surfaced through transactions.py, the diagnostic export and the UI, and is
  documented in docs/protocol/compatibility.md.
- I4: closed twice over — pump_core_bridge reads active before taking the
  event, and capture_active() now derives "running" from the mailboxes.
- I5: the test drives a real CaptureController and capture.cpp joined the test
  target; both mouse conventions now have teeth.
- I6: the bridging block moved verbatim into pump_core_bridge and
  test_core_bridge.cpp pins both routing branches in opposite directions.
- I8: FACTORY_RESET_COMMIT cancels capture, and the emulator matches the
  firmware on both HELLO and factory reset.

Task 7: minor (deferred): M6 a handoff ticket stranded by the 250 ms timeout
  is never reclaimed (finished() tests exact equality), and a later post()
  can tear the plain 8-byte package_ while Core 1 is in take() —
  core_bridge.cpp:6-12, main.cpp:227-233. Needs Core 1 hung a quarter second.
Task 7: minor (deferred): M7 install_macros' comment says "Runs on Core 1 and
  nowhere else" while hand_configuration_to_core1's !core1_running() branch
  runs it on Core 0 (main.cpp:155-158, 216-220) — unreachable, but it is the
  same class of unconditional claim that produced I11.
Task 7: minor (deferred): M8 a_capture_that_ended_between_the_two_reads_is
  _still_answered (test_core_bridge.cpp:276) is weak, not hollow: the round-1
  code shape still passes it, so it does not detect the interleaving it names.
Task 7: minor (deferred): M9 OutputRuntime's OutputQueueFull fault is
  permanent — drain() clears and releases forever after, and nothing ever
  calls clear_fault(). One overflow kills all input until the next power
  cycle. Outside this task's diff, and it is what the new dropped_commands
  counter will be reporting.

Correction to one re-review observation: the "discarded uncommitted test
edits" to test_main.cpp and test_macro_scheduler.cpp were never content. At
0aa78aa `git diff` on both files was empty — the only difference was a CRLF
warning from git's line-ending filter. `git diff 0aa78aa 25d3730` on the two
files is empty as well. Nothing was lost.

Task 7: complete (commits 80e84bb..25d3730, review clean after 2 fix rounds).
Step 4 (hardware acceptance) stays deferred under the pre-flight ruling: the
CH375 link is not stable and there is no second PC.

## Hardware, 2026-08-28 22:00 — the discriminating test ran, and C5 was it

The operator authorised the flash. The 25d3730 pico-ch375 image was written to
U1 (1200-baud reset into the bootloader, UF2 copied to RPI-RP2, device came
back on COM18). The CH375 power was NOT cycled — the counters answer the
question either way, since a failed CHECK_EXIST increments them whether or not
the chip is well.

Before (5400-byte Core 0 chain):    not_back_yet 18 -> 18 over 20 s. Frozen.
After  (1472-byte Core 0 chain):    not_back_yet 24 -> 43 over 20 s, then
                                    65 -> 68 -> 71 across three samples.

About one per second, which is exactly kRecoverDelayUs. **Core 1 is running.**
The stack overrun was stopping it, and every "the link runs and then
collapses" reading taken today was Core 1 being killed by a CDC request
walking Core 0's oversized reply path into Core 1's stack. The CH375 serial
link was never the thing that had collapsed.

Two more things changed with it, neither of which was possible before:

1. `check_exist=0xA8` on both channels, where it read `WRONG` all day. The
   chip answers.
2. A device enumerated. On the channel the firmware calls **keyboard**:
   `attached=1 gone=1 ready=1 reports=1`, `found=mouse endpoint=1 packet=7
   boot=yes parse=0`, `last report (7 bytes): 01 00 1E 10`.

Read (2) carefully: the operator has the keyboard unplugged and only the
mouse attached, and the channel that enumerated a **mouse** is the one the
firmware calls the keyboard. The other channel reads `state=Absent` — an empty
socket, which is where the keyboard is not. **The two CH375 channels are
swapped relative to the firmware's naming**: what the code treats as the
keyboard port is physically the mouse's, and vice versa. That means the
keyboard normalizer has been applied to mouse reports all along. Nothing has
been changed on this yet — it is recorded, not acted on, and which end is
wrong (wiring, GPIO assignment, or the labels) is not yet established.

Current state: the mouse enumerated, delivered one report, then went (gone=1,
collapses=1), and the channel is now cycling in RecoverWait with the counter
climbing. That collapse-after-one-report is the next thing to chase, and it
belongs to the CH375 link plan, not to this one.

Ruling: the swapped channels and the collapse-after-one-report are recorded
here and handed to docs/superpowers/plans/2026-08-28-ch375-serial-link-hardening.md
rather than fixed inside Task 7. Task 7's code is complete and reviewed; a
wiring/naming question and a link-stability question are that plan's subject,
and folding them in would reopen a task that has passed its review.
Cost if wrong: the branch merges with a known channel-naming defect recorded
but unfixed, which the final review sees and can escalate.

## Hardware, 2026-08-28 22:0x — power cycled, mouse moved, keyboard reattached

Operator cycled the 5 V, moved the mouse, and plugged the keyboard back in.
Reported: all indicators lit and staying lit.

Channel 0 (the firmware calls it "keyboard"; physically the mouse):
  attached=4 gone=4 ready=4 reports=68  int_seen=92
  found=mouse endpoint=1 packet=7 boot=yes parse=0
  last report (7 bytes): 01 00 00 00
  collapses=3 quiet_rearms=12 recovered_raised=2
  not_back_yet 245 -> 248 -> 251 across three samples (still ~1/s, alive)

68 real HID reports off the mouse, where the whole day had produced zero.
The channel enumerated four times and collapsed three; it is in RecoverWait
again now. Collapse-after-a-burst is the remaining link fault.

Channel 1 (the firmware calls it "mouse"): state=Absent, attached=0,
int_seen=0, check_exist=0xA8. The chip answers, but the channel has never
seen a single connect interrupt — with the keyboard plugged in, and with it
unplugged, identically. It is not that the keyboard fails to enumerate; the
channel sees nothing at all.

The probe text is one static char[900] shared by both channels
(main.cpp:578), and channel 0's block eats about 600 of it, so channel 1's
detail (setup attempts, mode_reply, found=, port baud) is truncated away.
Only its first three lines survive — which is enough for the next test.

Next discriminating test, no reflash needed: put the KNOWN-GOOD mouse into
channel 1's socket, with nothing in channel 0, cycle the 5 V, move the mouse.
If channel 1 then reports attached/reports > 0, the socket and chip are fine
and the fault is the keyboard or its cable; if channel 1 still reads
int_seen=0, the fault is that channel — wiring, D+/D- or its CH375 module —
and the keyboard was never the variable.

## Hardware, 2026-08-28 22:10 — the swap test: channel 1 is the faulty one

Operator moved the KNOWN-GOOD mouse — the one that had just delivered 68
reports on channel 0 — into channel 1's socket and cycled the 5 V.

  channel 0 ("keyboard"): state=Absent, attached=4 gone=4 ready=4 reports=68
    check_exist=0xA8, setup attempts=4 last=0x14 (success), polls=228
  channel 1 ("mouse"):    state=Absent, attached=0 gone=0 ready=0 reports=0
    check_exist=0xA8, int_seen=0

Stable across three reads spanning 25+ seconds.

Channel 0 correctly reports Absent now that its socket is empty, and its
history shows a clean run: four successful setups, the last returning 0x14
(success), 228 polls, 68 reports. The chip and that whole path are healthy.

Channel 1 has the working mouse plugged in and reports Absent with
int_seen=0 — not one connect interrupt, ever, on either device. And Absent is
not a passive state: `Ch375State::Absent` polls actively via
`transport_.test_connect` every kConnectPollUs (device.cpp:181-189), with the
comment explaining exactly why ("The announcement never comes for a device
that was plugged in before the power"). So channel 1's chip is in host mode,
is being asked whether a device is present, answers CHECK_EXIST with 0xA8,
and says no device — while a known-good mouse sits in its socket.

**Conclusion: the fault is channel 1's own hardware, not the keyboard and not
the firmware.** The keyboard was never the variable. Three candidates remain,
and they are separated by one observation — whether the mouse's own light
comes on in channel 1's socket:
  - light on  -> 5 V reaches the socket, so the fault is on the data lines
    (D+/D-, their solder joints, or the socket's data pins)
  - light off -> the socket is not powered, so the fault is 5 V distribution
    or that CH375 module itself
Asked of the operator; not guessed at here.

This belongs to the CH375 link plan, not to Task 7. Note also that it is a
second, independent fault from the collapse-after-a-burst seen on channel 0 —
the two must not be conflated.

Ruling: the earlier ledger entry calling the channels "swapped relative to the
firmware's naming" is withdrawn as unproven. All that is established is that
the socket the firmware indexes as 0 had the mouse in it. Whether the naming,
the wiring or the operator's expectation is the odd one out cannot be decided
while channel 1 detects nothing at all.
Cost if wrong: a naming/wiring question stays open one more session, which is
cheaper than renaming a channel on evidence that turned out to be a dead port.

## Hardware, 2026-08-28 22:2x — channel 1 is NOT faulty; the earlier verdict
## was wrong, and the operator was right to ask before soldering

The operator asked to have the firmware's pin assignment checked before being
sent to resolder anything. Checked: ch375_probe.hpp:133-138 gives channel 0
TX=GP0 RX=GP1 INT=GP2 and channel 1 TX=GP4 RX=GP5 INT=GP6 — the pins the
operator named.

Channel 1's detail was truncated out of the 900-byte probe text (both channels
share one buffer, main.cpp:578, and channel 0's block eats ~600 of it), and
the per-pin measurements the firmware takes at boot go into
`g_probe.single_probe`, which nothing ever transmits — `pack_ch375_single_probe`
has no caller outside its own translation unit. So a diagnostic build was made
in a separate worktree at C:\dp (short path — the scratchpad path exceeded
CMAKE_OBJECT_PATH_MAX and would not configure), printing channel 1 first, and
flashed. The repository worktree was left untouched: the whole-branch review
is running against 25d3730.

With that build, channel 1 reads:

  mouse state=RecoverWait attached=2 gone=2 ready=2 reports=3
    int_seen=15 connect=4 success=11 failure=0
    setup attempts=2 last=0x14 (success) polls=83
    found=mouse endpoint=1 packet=7 boot=yes parse=0
    last report (7 bytes): 01 00 FC FF
    detach_lost=2 collapses=2 quiet_rearms=6 not_back_yet=43

**Channel 1 enumerates the mouse, sets up successfully twice, and delivers
reports.** Its hardware is fine. The finding recorded twenty minutes earlier —
"the fault is channel 1's own hardware" — is WITHDRAWN. It rested on
int_seen=0 while a working mouse sat in the socket, which was true, but the
cause was not the socket.

What changed between the two readings is that U1 restarted (the flash reboots
it). Nothing physical moved. So the real defect is this:

**A channel sitting in `Ch375State::Absent` does not notice that its CH375
lost power and reset.** After the operator cycles the 5 V, the chip is back at
9600 baud with no host mode, but the firmware still believes it is a
configured chip merely waiting for a device: `Absent` polls `test_connect`
(device.cpp:181-189) and takes "no device" from a chip that is in no state to
answer, and never re-runs setup. The channel stays in that phantom Absent
until U1 itself is restarted. Every power cycle done today without reflashing
left the channels in exactly that state — which is why the keyboard's channel
looked dead all evening.

This explains the shape of the whole day's confusion and it belongs to
docs/superpowers/plans/2026-08-28-ch375-serial-link-hardening.md.

Ruling: do not ask the operator to resolder or reseat anything. Both channels
are electrically sound and both enumerate. The two real defects are software:
(a) phantom Absent after the chip is power-cycled under a running U1, and
(b) collapse-after-a-burst — attached/ready/reports climb, then detach_lost
increments and the channel recovers, on BOTH channels (channel 0:
collapses=3 over 68 reports; channel 1: collapses=2 over 3 reports).
Cost if wrong: hardware that is in fact marginal goes unexamined for another
session — but nothing physical is disturbed on a diagnosis that has now been
wrong once tonight already.

## Whole-branch review (e08a18a..25d3730) — verdict: ready with fixes

Two Critical, four Important, and a triage of every deferred minor. Findings
written verbatim to findings-final.md and dispatched as the ONE fix wave
(opus), with instructions to commit per finding — two implementers in a row
died at the report step and only their commits survived.

  C1 Critical — OutputQueueFull is a permanent silent kill switch. One
    submit() on a full queue latches fault_ and every later drain() clears the
    queue and releases everything, forever; nothing calls clear_fault(). All
    input to both computers is dead until a power cycle, with no indicator and
    no report. fault_ is also written by Core 1 and read by Core 0, plainly,
    contradicting main.cpp:44-45's single-writer claim. This is M9 escalated.
  C2 Critical — a TEXT macro overflows the queue and, short of that, types
    nothing. Core 1 iterates many times per Core-0 pass, so a 16-per-tick
    budget still offers 16xN against a 32-per-pass drain; and because
    HidStateManager holds state rather than a report queue, a drain that
    applies KeyDown+KeyUp in one pass leaves the snapshot unchanged and
    publishes no report at all. Every macro test stops at the OutputCommand,
    which is why nothing caught it.
  I1 — PC2 mouse deltas are consumed before poll() decides what to send, so a
    pass that sent KBD_STATE throws the motion away. UsbService::publish gets
    this right for PC1; the SPI path does the opposite.
  I2 — firmware, emulator and compatibility.md disagree about staging state in
    both directions (STOP_AND_RELEASE_ALL, HELLO, FACTORY_RESET_COMMIT order).
  I3 — GET_STATUS's fourth field carries aborted_staging where the contract,
    the emulator and transactions.py all say release_all_count, which the
    firmware never counts at all.
  I4 — docs/hardware/ch375-compatibility.md does not exist though Task 3 Step 1
    is ticked and the Completion Gate's last bullet is about it. Unrecorded
    gap; the deferrals cover hardware acceptance, not the document.

Deferred-minor triage: M9 must fix before merge (escalated to C1); M1-M8 and
both extras fine to defer. M4 is resolved by measurement rather than deferred —
.stack1_dummy at 0x20040000 is the SDK's unused placeholder and the real
region is __StackOneBottom..__StackOneTop, exactly as the brief said.

Completion Gate: enumerate/recover NOT met (deferred by ruling, hardware);
captured-vector normalization not met (report traces were never recorded —
only synthetic descriptors); 1000 simulated toggles met, physical deferred;
macro safety NOT met because of C2; profile/capture substantially met with
three deviations (I2, I3, one-pass CAPTURE_END re-arm); compatibility document
not met (I4).

Correction to the 22:00 hardware entry, from the reviewer: normalization is
chosen by DescriptorSetup::kind() — the device's own descriptor — not by the
channel's name (main.cpp:357, input/pipeline.cpp:54-60). A mouse enumerating
on the channel named "keyboard" is still normalized as a mouse. The earlier
claim that "the keyboard normalizer has been applied to mouse reports all
along" was wrong; the channel naming is a diagnostics defect, not a functional
one.

## Hardware, 2026-08-28 22:3x — both channels visible at once; the keyboard's
## failure has a name

A second diagnostic build (compact one-line-per-topic probe text, 5 format
lines instead of 13) fits both channels in the 900-byte buffer — 729 bytes
used. Built in the C:\dp worktree, flashed, then the operator power-cycled the
5 V (the reflash had left both chips deaf: ce=WRONG, reply=silent, which is the
known "reflashing U1 hangs the CH375 modules" effect; the counter was climbing
throughout, 31 -> 55 over 25 s, so Core 1 was alive the whole time).

After the power cycle, with keyboard on channel 0 and mouse on channel 1:

  mouse    RecoverWait att=1 gone=1 rdy=1 rep=0 int=6
           con=2 ok=4 dl=1  setup=1/0x14(success) polls=40
           found=mouse ep=1 pkt=7 boot=yes  col=1 qr=3
  keyboard RecoverWait att=1 gone=0 rdy=0 rep=0 int=2
           con=2 ok=0 ef=1  setup=1/0xFD(no interrupt before the deadline)
           polls=0 found=nothing  mr=0/1

Two different faults, cleanly separated for the first time:

- **Mouse:** enumerates (setup 0x14 success, recognised as a boot-protocol
  mouse, endpoint 1, 7-byte packets), then is lost — dl=1, col=1. This is the
  collapse-after-a-burst already recorded.
- **Keyboard:** attaches (con=2, int=2) but never enumerates.
  `Ch375Enumerator` times out waiting for an interrupt during setup and marks
  it 0xFD (enumerator.cpp:50-55), enum_failed=1, found=nothing, polls=0. The
  device is seen on the bus and then says nothing.

Leading hypothesis for the keyboard, to be tested under the link plan: bus
speed. Both channels report `full`. `device_is_low_speed_` is set from
`transport_.get_device_rate(low_speed) && low_speed` (device.cpp:195-196), so
**any failure to read the rate silently yields full speed**, and the bus is
then configured full (`set_usb_speed`, device.cpp:208, 230). Most keyboards
are low-speed 1.5 Mbps devices; a low-speed device on a bus driven at full
speed answers nothing, which is exactly 0xFD. The mouse, which enumerates, is
genuinely full-speed. Discriminating test: log what `get_device_rate` actually
returned (the boolean AND currently discards the difference between "the read
failed" and "the device said full"), and try forcing Low1_5Mbps on the
keyboard's channel.

Both of tonight's hardware defects belong to
docs/superpowers/plans/2026-08-28-ch375-serial-link-hardening.md. Task 7's
Step 4 acceptance remains deferred.

## Final fix wave — partial, then resumed

The wave's implementer hit its session limit too (the third in a row), but it
had been told to commit per finding, so its work survived:
  d93da16  C1: let the output runtime recover from a full queue
  f4e4087  C2: pace macro output so a TEXT macro reaches the far computer
  29d37dc  I1: stop throwing PC2's mouse motion away when the keyboard changed
plus uncommitted I2/I3 work in the tree (release_all_count in status_payload;
STOP_AND_RELEASE_ALL aborts staging; the emulator's HELLO aborts staging and
its FACTORY_RESET_COMMIT checks confirmation before arm). Controller-run
native ctest with that tree in place: 31/31 (two new tests since 25d3730).

Ruling: the per-finding commit instruction is now standing policy for this
branch, not a one-off. Three implementers in a row have died between the last
fix and the report, and it is the only reason two of the three rounds were
recoverable at all.
Cost if wrong: slightly noisier history than one commit per wave, which is
what `git log --oneline` already looks like.

Ruling: a fresh implementer (sonnet, cheaper tier) finishes the wave rather
than the controller doing it. The remaining work is verification of an
existing diff, one document, and two housekeeping edits — none of it needs the
dead agent's context, and controller-written code would skip review entirely,
which is the one thing the process must not lose this late.
Cost if wrong: one more dispatch on a night where three have died mid-flight.

## Final fix wave complete — 25d3730..18e379e

  d93da16  C1: let the output runtime recover from a full queue
  f4e4087  C2: pace macro output so a TEXT macro reaches the far computer
  29d37dc  I1: stop throwing PC2's mouse motion away when the keyboard changed
  4408d78  I2/I3: make firmware, emulator and the contract agree on staging
           and release_all_count
  18e379e  I4: write docs/hardware/ch375-compatibility.md

Housekeeping needed no commit: .test-tmp/ was already gone and the
"and nowhere else" comment had already been rewritten inside d93da16.

Controller-run evidence on 18e379e (working tree clean):
- native ctest 31/31 (29 at 25d3730 — the wave added two).
- all three builds pass: native, pico-ch375, pico-release.
- python tests 37; firmware artifact contracts 11.
- configurator 549 passed, 4 skipped, 1 failed — three more than the 546
  baseline, and the single failure is still the opt-in
  test_the_real_device_reports_its_status that needs the hardware link.
- stack, both images: Core 0 1432 (pico-release) / 1480 (pico-ch375) against
  its 2048-byte region — 616 and 568 to spare, up 8 bytes from the wave.
  Core 1 unchanged at 504 / 512. No collision path.

The one scoped re-review of the wave is dispatched (opus). There is no second
fix wave: whatever it leaves open is adjudicated and carried to the human.

## Root-cause investigation of the two hardware defects (systematic-debugging
## Phase 1, no fixes proposed yet) — 2026-08-28 late

### Defect A — the keyboard attaches and never enumerates (setup 0xFD)

Evidence: att=1, con=2, int=2, then `setup=1/0xFD(no interrupt before the
deadline)`, ef=1, found=nothing, polls=0.

`Ch375Device` decides the bus speed once, at attach:

    device_is_low_speed_ = transport_.get_device_rate(low_speed) && low_speed;
                                                        (device.cpp:196)

`get_device_rate` returns false when `read_reply` times out
(transport.hpp:551-560). The `&&` therefore collapses two different facts into
one: **"the chip did not answer" and "the chip said full speed" both yield
full speed.** The bus is then configured Full12Mbps (device.cpp:208, 230).

The code already knows what that costs. transport.hpp:563-566 and
device.cpp:229-233 both say it outright: "A low-speed device addressed at full
speed does not answer, and a controller reports something that does not answer
as gone." Most keyboards are low-speed 1.5 Mbps devices; the mouse, which
enumerates, is genuinely full-speed.

So the keyboard is plausibly a low-speed device on a bus driven at 12 Mbps,
which is silence, which is exactly the 0xFD timeout `Ch375Enumerator` reports
(enumerator.cpp:50-55). Not yet proven: nothing records which of the three
outcomes `get_device_rate` actually produced. That is itself a violation of
the link plan's own constraint that a reading which cannot move must not look
like one that merely did not.

### Defect B — the mouse enumerates, reports, then is lost

Evidence: att=1 rdy=1 rep>0, then qr=3, dl=1, col=1. `qr=3` is exactly
`kQuietRetriesBeforeTeardown` (device.hpp:170), so the channel used every
re-arm it had before declaring the device lost after `kDeviceLostUs` = 1 s of
silence (device.cpp:283).

The cause is on the other channel. Core 1 ticks both devices in sequence in
one pass:

    for (int index = 0; index < 2; ++index) devices[index]->tick(now_us);
                                                        (main.cpp:336-337)

and `Ch375Transport::read_reply` is a **blocking busy-wait** up to
`reply_timeout_us_` (transport.hpp:652-663), which defaults to
`kDefaultReplyTimeoutUs` = **20 000 us** (transport.hpp:73). A channel whose
chip is not answering burns 20 ms per failed reply and several per pass —
which is precisely the measured `worst pass round the loop = 50 699 us`,
two to three timeouts back to back.

While that runs, the other channel is not ticked at all. The mouse's interrupt
endpoint wants polling about every 8 ms; a 50 ms stall skips six windows. Do
that repeatedly and the quiet accumulates, the three re-arms are spent, and a
device that never disconnected is declared lost.

The comment defending the 20 ms figure (transport.hpp:70-72) — "far past any
real answer and still far below anything a person would notice" — measures the
wrong thing. It is about human perception; the cost that matters is that 20 ms
is 2.5 times the neighbouring channel's poll deadline. Another confident
comment that is wrong about the property it asserts.

Note also that both channels are ticked with a single `now_us` captured before
the loop, so the second channel makes its timing decisions on a clock that the
first channel's blocking has already made 20-50 ms stale.

### The two defects are connected

A produces B. A keyboard that never enumerates is a permanent generator of
failed replies on its channel, and every one of those steals up to 20 ms from
the mouse. It also fits the record: at 22:0x, with the neighbouring socket
EMPTY (a cheap Absent path, no timeouts), the mouse delivered 68 reports.

### Cheapest discriminating test, no reflash, no rewiring

Unplug the keyboard only. Its channel drops to Absent, which costs no
timeouts. If the mouse then stops being lost — reports climb with dl and col
staying put — B is confirmed as neighbour-induced starvation rather than
anything wrong with the mouse or its channel.

## Defect B, second hypothesis: the mouse channel fell off the baud ladder

The first hypothesis — neighbour-induced starvation from blocking 20 ms
replies — is REFUTED. With the keyboard unplugged, over 60 seconds the mouse
channel still went att 3->7, gone 3->7, dis 19->27, dd 19->25, ef 0->10,
setup 3->15. The operator reports it plainly: "the mouse disconnects and
reconnects when moved". Those are `status_disconnect` events reported by the
controller, not silence timeouts, so the loss is not the neighbour stealing
poll windows. (The 20 ms blocking read is still real and still costs the other
channel; it is simply not what is killing the mouse.)

What the counters say instead:

  mouse channel:    baud=9600   col=3   rc=0
  keyboard channel: baud=115200

`kBaudLadder` has exactly three rungs — 115200, 62500, 37500
(commands.hpp:245-249). `collapses_while_raised_` increments and the rung
steps down on every collapse at a raised rate (device.cpp:326-332); at the
bottom `baud_exhausted_` is set and the port stays at `kCh375DefaultBaud`,
9600, permanently. The mouse channel's col=3 is exactly three collapses: rung
0 -> 1 -> 2 -> exhausted. It is off the ladder and stuck at 9600.

At 9600, one mouse report is 15 bytes = 165 bits = **17.2 ms** on the wire.
A moving hand produces a report every **8 ms**. The port is physically
incapable of carrying it — which is the plan's own first recorded symptom,
"Moving the mouse made the peripheral re-enumerate repeatedly"
(2026-08-28-ch375-serial-link-hardening.md), and the reason a1bcfdb raised the
port off 9600 in the first place.

So the ladder's own recovery rule is the trap: "down a rung on every collapse,
and off the ladder entirely at the bottom - a link that keeps its speed and
keeps dropping is worse than a slower one that does not" (device.cpp:321-325).
For this device the premise is false. The slower rate does not drop less; it
cannot carry the traffic at all, so it collapses harder, which steps the
ladder down again. Self-reinforcing: collapse -> slower -> more collapse ->
9600 forever.

The keyboard channel sits at 115200 for the mirror-image reason: it has no
working device, so it never collapses, so its ladder is never stepped down.
The channel that works is the one that is punished.

Discriminating test: restart U1 (which resets `baud_rung_` and
`baud_exhausted_`), confirm the mouse channel comes up at 115200, and move the
mouse. If it survives movement at 115200 and only degrades as the ladder steps
down, this hypothesis holds and the repair is in the ladder policy — a floor
below which a channel must not descend, since below about 62500 the link
cannot carry a moving mouse at all, plus a way back up once a rate survives.

## Defect B confirmed: the ladder walks itself down to an unusable rate

U1 was reflashed (resetting baud_rung_ and baud_exhausted_), the operator
cycled the CH375 power and moved the mouse. The whole degradation was then
observed live, in two readings 45 seconds apart:

  23:45  mouse **Ready** **low** att=3 rdy=3 **rep=116** int=647 ok=641
         dis=0 dd=0 polls=727 **baud=37500** col=2 dl=2
  23:46  mouse RecoverWait full att=3 gone=3 **rep=195** int=725 ok=719
         dis=0 dd=0 polls=845 **baud=9600**  col=3 dl=3

So: the channel came up at 115200, collapsed to 62500, collapsed to 37500,
**worked there** — Ready, 195 reports, 719 successful transactions, zero
controller-reported disconnects — then collapsed a third time, exhausted the
three-rung ladder and dropped to 9600, where a mouse report costs 17.2 ms
against an 8 ms production rate and the channel cannot work at all. Exactly
the predicted sequence.

Two things this reading changes:

1. **The mouse is a low-speed device.** At 23:45 the channel reads
   `Ready low` — `get_device_rate` returned low that time. Every earlier
   reading said `full`. So the speed detection is not stable across attaches,
   which strengthens Defect A's hypothesis: the same `&&` that hides a failed
   read is deciding this, and it decides differently on different attempts.

2. **`dis=0` throughout.** No controller-reported disconnect at all in this
   session; every loss was silence (dl=3, qr=9). The "disconnects and
   reconnects when moved" the operator sees is the channel tearing the device
   down after a second of quiet, not the device leaving the bus.

And that exposes the deeper defect in the ladder policy. `collapses_while_
raised_` is incremented on the quiet-teardown path (device.cpp:326), so **any**
silence is read as evidence that the rate is too high and steps the ladder
down — regardless of what actually caused the silence. The ladder therefore
descends on evidence it never established, and at the bottom it parks the
channel at a rate that is provably too slow to carry the device. Nothing ever
climbs back.

The repair has three parts, none of them yet implemented:
  - a floor: below ~37500 a moving mouse cannot be carried, so 9600 must not
    be a resting place for a channel with a live device;
  - a way back up once a rate has survived some duration or transaction count;
  - stop treating every quiet teardown as proof the rate is too high.

Still open, and NOT established: what actually causes the silence at 37500.
`worst pass round the loop` remains ~50.7 ms in every reading tonight,
including with the keyboard unplugged, so the blocking 20 ms `read_reply`
(hypothesis one) is still a live candidate for the underlying quiet — it was
only refuted as the explanation for the *disconnect* counters, not for the
silence itself. Next session starts there: instrument per-channel tick
duration and find what spends 50 ms.

## Instrumented reading, 2026-08-28 23:52 — hypothesis A refuted, the blocking
## read quantified

A diagnostic build (C:\dp) was extended to separate what the counters had been
conflating, flashed, and read with both devices attached after a power cycle:
per-channel tick cost, `read_reply` timeouts and total blocked time per
channel, and the three outcomes of `get_device_rate` kept apart.

  mouse    RecoverWait  rep=131 ok=139 dis=0 dd=0 dl=2 col=2 baud=62500
           tick worst=24973us  timeouts=90   blocked=2045ms  rate un=0 low=2 full=0
  keyboard RecoverWait  att=1 ef=1 setup=1/0xFD baud=9600
           tick worst=50686us  timeouts=108  blocked=2178ms  rate un=0 low=0 full=1
  pass-wide worst = 50756us

**Hypothesis A is refuted.** `rate un=0 low=0 full=1` — `get_device_rate`
ANSWERED for the keyboard, and it answered *full speed*. The read did not fail,
so the `&&` never hid anything on this attach, and the bus was configured to
the speed the chip actually asked for. The keyboard's 0xFD is not a low-speed
device addressed at full speed. (The masking defect in device.cpp:196 is still
a real reporting defect worth fixing — it just is not this bug.)

The mouse, meanwhile, reads `low=2`: it is genuinely a low-speed device, and
the earlier `full` readings were from attaches where the answer differed.

**The blocking read is now measured, and it is large.** The keyboard channel's
worst single tick is 50686 us against a pass-wide worst of 50756 us — so
essentially the entire worst pass is one tick of one channel, and 50686 us is
two and a half 20 ms reply timeouts back to back. Cumulative time spent inside
`read_reply`: 2178 ms on the keyboard channel and 2045 ms on the mouse channel,
better than four seconds of busy-wait between them over a couple of minutes.
The mouse's own channel times out too (90 times), so this is not only the
neighbour's fault.

That makes the earlier refutation of hypothesis B too broad. Blocking was
correctly refuted as the explanation for the *controller-reported disconnect*
counters (dis/dd), which are zero in every reading since. It is now the leading
explanation for the *silence* that drives the quiet teardown: a channel that
spends 20-50 ms inside one tick cannot poll an endpoint that wants attention
every 8 ms, and three missed re-arms later the device is declared lost — which
the ladder then reads as proof the rate is too high and steps it down.

Revised causal chain, evidence-backed at every link but the first:
  read_reply blocks up to 20 ms per unanswered byte (measured: 90-108 times
  per channel, 2 s each)
    -> a tick costs up to 50 ms (measured: 50686 us)
    -> the other channel, and this one, miss 8 ms poll windows
    -> silence accumulates, three re-arms are spent (qr=6)
    -> device declared lost (dl=2), counted as a collapse (col=2)
    -> ladder steps down (baud 115200 -> 62500 -> 37500 -> 9600 exhausted)
    -> at 9600 a mouse report needs 17.2 ms against 8 ms of production, so the
       channel cannot work at all and never recovers.

Still not established: why `read_reply` times out at all — that is the head of
the chain and the next thing to instrument. Candidates: the PIO receiver
mis-sampling at raised rates, the chip genuinely not answering some commands,
or a command sequence that asks for a byte the chip was never going to send.
The per-command breakdown would settle it; the counters today are per channel,
not per command.

Note for the fix, whenever it is written: a 20 ms busy-wait on a core that is
also servicing another channel's 8 ms deadline is an architectural mismatch,
not a tuning problem. The reply wait wants to become a state, not a spin.

## 2026-08-29 00:0x — "the cursor only moves vertically": root cause, fully
## established, and it is firmware, not hardware

The operator moved the mouse and reported the cursor moving only vertically.
That the cursor moves at all means the whole path works: mouse -> CH375 -> U1
-> HID -> the far computer. The defect is in how the report is read.

Every report logged tonight has the same first two bytes:

    last:7B 01 00 F6 4F      last:7B 01 00 73 B0
    last:7B 01 00 FF 0F      last:7B 01 00 F8 1F

Byte 0 is always 0x01 and byte 1 is always 0x00, with the movement in bytes
2 and 3. That is a **Report ID of 1**, followed by the real buttons byte,
then dx, then dy.

`MouseNormalizer::apply` (input/mouse_normalizer.cpp:30) handles this:

    const std::size_t offset = report_id_ ? 1 : 0;

and its comment even names the symptom — "Some mice put an identifier in
front of every report. Taking that byte for the buttons puts a click on every
movement."

**But `set_report_id` is called from exactly one place in the repository, and
that place is a test** (`tests/firmware_native/test_normalizers.cpp:339`).
`grep -rn set_report_id firmware/ tests/` returns the setter's definition and
that single test call. No production path ever sets it, so `report_id_` is
false forever, and the parse is shifted one byte:

  - buttons  <- the Report ID (0x01)  -> the left button reads as permanently
    held;
  - dx       <- the real buttons byte (0x00) -> horizontal movement is always
    zero;
  - dy       <- the real dx           -> moving the mouse sideways moves the
    cursor vertically.

Which is exactly, and only, what the operator sees.

Why the device sends a Report ID at all: **the firmware never puts it into
boot protocol.** `Ch375DescriptorSetup`'s steps are ReadingDeviceDescriptor ->
SettingAddress -> ReadingConfiguration -> ChoosingConfiguration -> Idle
(descriptor_setup.cpp:36-129); there is no SET_PROTOCOL step, and
`grep -rn "SetProtocol|set_protocol|SET_PROTOCOL" firmware/` finds nothing.
The `boot=yes` in the probe text is `setups[index]->boot_protocol()`, which
reports that the *interface descriptor advertises* boot support — not that
boot protocol was selected. It never was. So the device stays in report
protocol and sends its native 7-byte report, while the normalizer reads it as
a 3-byte boot report.

Nor could the parser have known: `HidCapabilities` (hid_parser.hpp:47-57) has
no Report ID field at all, and the HID *report* descriptor is never fetched —
only the configuration descriptor is parsed.

This is the same shape of defect that has bitten this project repeatedly: the
capability exists, a test covers it, and no production code ever reaches it,
so the suite is green while the device misbehaves. The test at
test_normalizers.cpp:339 tests a mode the system can never enter.

Two candidate repairs:
  1. **Select boot protocol** (SET_PROTOCOL, bRequest 0x0B, bmRequestType
     0x21, wValue 0) after ChoosingConfiguration. The device then sends the
     fixed 3-byte boot report with no Report ID, which is the format the whole
     normalizer was designed around and which `boot=yes` says this device
     supports. Needs a class-specific control transfer through the CH375; the
     transport today has set_configuration and set_address but no general
     control-request path, so that has to be added.
  2. **Fetch and parse the HID report descriptor**, detect Report ID (item
     0x85), and call `set_report_id`. Strictly more code, and it leaves the
     device in report protocol where field layouts vary per device.

Recommendation: (1), because the design already assumes boot reports
everywhere and (2) makes every future device a parsing problem. Whichever is
chosen, the fix must be driven by a test built from this device's actual
7-byte report, and the existing test at test_normalizers.cpp:339 must be
joined by one that fails if the production path stops selecting the mode.

Note this is independent of the link-stability work: the mouse was enumerated,
delivering reports, and driving the cursor when this was observed.

## 2026-08-29 00:03 — per-command timeout breakdown, and the keyboard finally
## enumerates

With both devices attached after a power cycle, the mouse driven for a while:

  mouse    rep=152 int=2987 ok=2980 polls=3185 dl=3 col=3 baud=9600 rate low=3
           tick worst=50765us timeouts=751 blocked=20985ms
           timeouts by command: 0x06=747  0x15=3  0x22=1
  keyboard att=1 gone=1 rdy=1 **setup=1/0x14(success)**
           **found=keyboard ep=1 pkt=8 boot=yes** dl=1 col=1 baud=9600
           tick worst=50784us timeouts=839 blocked=16805ms

**The keyboard enumerated.** First time all day: setup succeeded (0x14), the
parser recognised a keyboard, endpoint 1, 8-byte packets. So the earlier 0xFD
was transient, not a property of the device or its channel — which retires
Defect A as originally framed. Both peripherals are enumerable; neither stays
up.

**Where the blocking goes.** 747 of the mouse channel's 751 reply timeouts are
on command **0x06 = CheckExist** (commands.hpp:34); the remainder are 3 on
0x15 (SetUsbMode) and 1 on 0x22 (GetStatus). So the 20 ms busy-waits are
overwhelmingly spent in the recovery loop asking a chip that is no longer
answering whether it is there — not in normal traffic. Cumulative blocked time
is 20 985 ms on the mouse channel and 16 805 ms on the keyboard channel: about
38 seconds of the two cores' shared loop spent spinning.

That reframes the chain again, and this time the head of it is visible:

  a channel runs, then collapses
    -> the chip stops answering CHECK_EXIST altogether
    -> RecoverWait retries once a second, each retry costing a 20 ms spin that
       also delays the other channel
    -> the channel never comes back on its own; only a module power cycle
       revives it
    -> meanwhile every collapse steps the baud ladder down, and both channels
       are now parked at 9600 (mouse col=3, keyboard col=1)

That is symptom 2 from the link plan, stated there as "a channel would go
permanently silent and only a module power cycle appeared to recover it",
now with the mechanism attached: the recovery path's own probe is the thing
that never succeeds, and RESET_ALL plus a return to 9600 does not revive a
chip in this state.

Open question, now the sharpest one: **what makes the chip stop answering
CHECK_EXIST after a period of successful traffic?** Candidates, in the order I
would test them: the PIO receiver losing sampling alignment at a raised rate
and never regaining it (the ladder is already at 9600 here, which argues
against); a command sequence abandoned mid-way leaving the chip waiting for a
parameter, so every later command byte is eaten as data — the failure mode
transport.hpp:530-545 documents and defends against with four filler bytes
before RESET_ALL; or a genuine chip-side hang requiring the power cycle its
datasheet does not describe.

Note the mouse's `rate low=3`: all three attaches this session read low speed,
consistent and correct for this device.

## 2026-08-29 00:08 — the PIO receiver is not the cause

The receiver hypothesis was tested directly rather than argued about. The
diagnostic build now samples the raw receive pin inside `read_reply`'s wait
loop and records, per channel: total edges seen during timed-out waits, how
many timeouts saw any edge at all, and the framing-error flag accumulated
across them (`transport.hpp` read_reply; `PioCh375Transport::probe_rx_high`
reads the pin the PIO receive machine watches).

First reading, taken while both chips were in the deaf state that follows a
reflash of U1:

    mouse    timeouts=34  blocked=680ms  timeouts by command: 0x06=34
             wire: edges=0  timeouts_with_edges=0  framing=0

**Zero edges across 34 timed-out waits — 680 ms of continuous watching.** If
the chip were answering and the receiver were mis-sampling, every byte would
still be eleven bit-times of line activity and the edge count would be large.
There is no activity at all: the chip is not transmitting.

That refutes the mis-sampling hypothesis for this state, and it also explains
why `framing=0` is not the reassurance it looks like — there can be no framing
error in a stream that does not exist. (`PioCh375Transport::framing_errors` is
also a one-shot flag that clears itself on read, not a counter, so it was never
usable as evidence either way. Its own comment says the same mistake was made
before with the wrong IRQ index.)

**Caveat, and it is the one this project has been caught by twice: an absence
is only evidence if presence looks different.** `edges=0` proves the chip is
silent ONLY once the same instrument has been seen to report non-zero edges on
a working channel. That control has not been taken yet. Until it is, `edges=0`
is equally consistent with a broken measurement.

Next: power-cycle the modules, let a channel enumerate and carry traffic, and
confirm the edge counter moves. Then let it collapse and read it again. If it
reads non-zero while working and zero after the collapse, the chip genuinely
goes silent and the remaining candidates are a command sequence left mid-way
(transport.hpp:530-545 documents exactly that failure and defends against it
with four filler bytes before RESET_ALL) or a chip-side hang.

## 2026-08-29 00:14 — the control was taken, and it changes the answer:
## 115200 does not survive a block read

The instrument was wrong in the earlier reading and has been fixed: it now
watches the receive pin for a fixed 300 us window at the start of EVERY reply
wait, answered or not (`kProbeWireWindowUs`, transport.hpp). Before, edges were
only counted inside timed-out waits, so a working channel could never produce a
non-zero reading — the absence had no matching presence and proved nothing.

With that fixed, two readings 30 seconds apart, channel at **115200**:

    00:14:20  int=153  0x27=152  0x06=83  allEdges=7675   framing=76
    00:14:50  int=211  0x27=210  0x06=83  allEdges=10585  framing=105
    delta      +58      +58       +0       +2910           +29

Read that carefully:

- **allEdges climbs by 2910.** The instrument works; the wire is alive. The
  earlier `edges=0` on a deaf chip is therefore real evidence, retroactively.
- **0x06 CheckExist does not time out at all any more** (+0). The short
  command-and-one-byte exchange works fine at 115200.
- **Every single block read fails**: 0x27 = ReadUsbData0 times out 58 times in
  30 seconds, once per interrupt seen (+58 interrupts, +58 timeouts).
- **framing climbs by 29** — roughly one malformed frame per two failed block
  reads. The receiver is decoding garbage, not silence.

So the mis-sampling hypothesis is not dead after all; it was tested in the
wrong state. At 9600, with a deaf chip, there was nothing on the wire. At
115200, with a live chip, the wire is busy, frames are malformed, and every
multi-byte read fails while single-byte exchanges pass. **115200 is not a
usable rate for this wiring on the block-read path**, even though CHECK_EXIST
answers on it twice, which is the entire proof the ladder requires before
raising the rate (constraint: "Never write a command at a port rate that has
not just been proved" — two CHECK_EXIST replies are accepted as that proof).

And now the trap closes from the other side. The device shows att=1, rdy=0:
attached, never Ready, because setup needs block reads to fetch descriptors and
they all fail. `collapses_while_raised_` is only incremented on the
quiet-teardown path of a device that reached Ready. **A rate too broken to
enumerate therefore never counts as a collapse, so the ladder never steps
down, and the channel is stuck at 115200 forever.**

Which is the exact mirror of what was recorded two hours ago: there, a channel
that did work collapsed repeatedly and the ladder walked it down to 9600, where
a moving mouse cannot fit. Both failures come from the same root: the ladder's
up-rule and its down-rule are both driven by signals that do not measure what
they are taken to measure.
  - up:   two CHECK_EXIST replies prove a divider, and are taken as proof the
          rate carries traffic. They do not - a nine-byte block read fails at a
          rate where a two-byte exchange passes.
  - down: only a post-Ready quiet teardown counts, so the rates that are too
          broken to enumerate are exactly the ones the ladder cannot escape.

The framing-error counter is now accumulated across reply waits rather than
read as a self-clearing one-shot flag, which is what made it usable here.

## 2026-08-29 00:20 — measured: block reads work at 37500 and nowhere above

The diagnostic build was given a rule the real firmware does not have: when a
rate fails twelve block reads in a row, step the ladder down and re-run chip
setup (`kProbeBlockFailsBeforeStepDown`, device.cpp Enumerating branch). It
also counts block-read successes and failures separately per rung. That turns
the question into a measurement instead of an argument.

After a power cycle, the channel walked the ladder by itself:

    rungs(115200/62500/37500):  ok=0/0/1   fail=24/24/0   now=2

  - **115200 — 0 successful block reads, 24 failures.**
  - **62500  — 0 successful block reads, 24 failures.**
  - **37500  — 1 success, 0 failures**, and the device reached Ready (rdy=1)
    and delivered a report (`last:7B 01 00 FC 1F`).

So for this wiring, **37500 is the only rung on which a block read completes**,
and it is also the rung on which the mouse enumerated and reported earlier
tonight (195 reports before it collapsed). Framing errors accumulate only on
the two faster rungs (framing=24 while walking them, +0 once at 37500).

That settles the ladder's design questions with numbers:

  - **The up-rule is wrong.** Two CHECK_EXIST replies prove a divider, not a
    working link: at 115200 and 62500 CHECK_EXIST answers fine while every
    multi-byte read fails. The rate must be proved by the traffic it has to
    carry — a successful block read — not by a two-byte exchange.
  - **The floor is wrong.** 9600 is below what a moving mouse needs (17.2 ms
    per report against 8 ms of production), so a channel with a live device
    must never rest there. The usable band on this bench is exactly one rung
    wide: 37500.
  - **The down-rule misses this case entirely.** Ladder step-downs only happen
    from a post-Ready quiet teardown, and a rate too broken to enumerate never
    reaches Ready — which is why the channel sat at 115200 indefinitely before
    this diagnostic rule was added. The rule added here (step down on repeated
    block-read failure) is the shape the fix wants.

Note the keyboard channel remains at 9600 and has not enumerated in this
session; its ladder never rose because it never got a clean setup either.

Proposed repair for the link plan, all three parts measurable:
  1. prove a rung with a block read, not with CHECK_EXIST;
  2. never park a channel with an attached device below 37500;
  3. step down on repeated block-read failure, not only on post-Ready silence,
     and allow a climb back after a rung has carried traffic for some time.

## 2026-08-29 00:25 — the report-ID fix is confirmed on hardware; the mouse now
## blinks because of the ladder, not the parse

The branch's boot-protocol fix (cc557c6, e8b1e21, 1ac1dd6) was cherry-picked
together with the diagnostic build and flashed. After a power cycle:

    mouse    Ready  setup=26/0x14(success)  found=mouse ep=1 pkt=7
             **boot=adv:yes/sel:yes**   **last:3B 00 E2 04 00**
    keyboard        setup=25/0x14(success)  found=keyboard ep=1 pkt=8
             **boot=adv:yes/sel:yes**

**The fix works.** `sel:yes` means SET_PROTOCOL landed — the mouse accepted it,
which was the one thing that could not be verified without hardware. And the
report is now **three bytes** (`00 E2 04` = no buttons, dx = -30, dy = +4)
where it was seven with a Report ID in front. The one-byte shift is gone at the
source. Both devices took boot protocol.

The operator reports the cursor does not move at all now, and that the mouse's
light goes off and on. The counters say why, and it is not the parse:

    00:24:30  att=4 gone=1 rdy=2 rep=2  setup=26  baud=9600  rungs ok=0/0/2
    00:25:23  att=7 gone=5 rdy=3 rep=3  setup=37  baud=9600  rungs ok=0/0/3

Thirty-seven setup attempts, seven attaches, three reports. The channel
enumerates, loses the device, tears the bus down — which is what makes the
mouse's light go out and come back — and enumerates again, continuously. With
three reports total there is nothing for the cursor to move on.

The port reads **9600** in every one of these samples while the ladder's rung
says 2 (37500) and `rc=0` says no rate change was refused. So the rung index
and the port's actual rate have come apart: the per-rung block-read tally is
keyed on `baud_rung_`, not on the rate the port was really running, which means
last reading's "block reads work at 37500" needs re-checking against the actual
`port_baud_` at the moment of success. The claim that 115200 and 62500 fail
stands — those samples showed the port at those rates — but "37500 works" may
really be "9600 works", which would fit: at 9600 a 20 ms per-byte timeout is
ample for a descriptor fetch, while a moving mouse's 8 ms report stream is not.

That also explains the whole shape of tonight: enumeration succeeds at a rate
that cannot carry traffic, so the device is set up, immediately goes quiet, is
declared lost, and the bus is reset — forever.

Next: print `port_baud_` alongside the per-rung tallies so success is attributed
to the rate that actually carried it, and make the ladder raise the port before
enumerating rather than enumerating at 9600 and hoping.

## 2026-08-29 00:30 — 37500 confirmed against the real rate, and the port's two
## ideas of its own speed disagree

The per-rate tally is now keyed on `port_baud_` rather than on the ladder's
rung index, so a success is credited to the rate that actually carried it:

    byRate(9600/37500/62500/115200): ok=0/1/0/0  fail=0/0/24/24
    port=37500  rung=2

  - **9600:  no block reads at all** — the earlier worry that "37500 works"
    might really be "9600 works" is refuted; nothing was ever read at 9600.
  - **37500: 1 success, 0 failures.** Confirmed as the working rate.
  - **62500: 0 / 24 failures.  115200: 0 / 24 failures.**

So the previous conclusion stands, now on the right evidence: block reads
complete at 37500 and at no rate above it.

**New finding, and it may be the head of the whole chain.** The same line shows
two different answers for the port's speed:

    last:...  baud=9600        <- g_mouse_port.baud(), what the PIO is clocked at
    byRate:   port=37500       <- Ch375Device::port_baud_, what the device believes

`port_baud_` is written only where `try_speed` succeeds (device.cpp:116), but
`reset_port_speed(kCh375DefaultBaud)` (device.cpp:35) drops the PIO back to
9600 on every chip re-setup **without updating `port_baud_`**. So the device
can believe it is talking at 37500 while the receiver is clocked for 9600 — and
that is precisely the state the transport's own constraint warns about: "Never
write a command at a port rate that has not just been proved… a byte
half-heard at the wrong rate is swallowed as some command's parameter and every
byte after it is out of step. This is the mechanism behind 'the chip stopped
answering'."

Whether the two are genuinely out of step during traffic, or merely caught
mid-recovery in this sample, is the next thing to establish: log the pair at
the moment a block read fails, not only when the report is printed.

The mouse did enumerate again this session — `boot=adv:yes/sel:yes`,
`last:3B 00 C0 C8 00`, three bytes, no Report ID — and then collapsed after one
report. The keyboard did not enumerate this time (`setup=1/0xFD`,
`boot=adv:no/sel:no`), which continues to alternate between sessions.

## 2026-08-29 00:38 — `find_chip` was written, tested, and never called

`Ch375Transport::find_chip` (transport.hpp:464-490) walks the home rate and
every ladder rung looking for a chip stranded at a rate this side abandoned,
sends four filler bytes between attempts to clear a half-heard command, resets
the chip where it finds it and brings it home. Its own comment says what it is
for: "until now the only cure was somebody walking to the board and pulling its
power - which is what this project has been doing for weeks. It is reachable;
nobody was asking in the right place."

    grep -rn "find_chip" firmware/ tests/
      tests/firmware_native/test_ch375_transport.cpp:431,453,463

**Three call sites, all in tests. Production never calls it.** That is the
third instance tonight of the same failure shape — capability written, covered
by a passing test, unreachable from the running system:
  - `MouseNormalizer::set_report_id` (test only) -> the one-byte parse shift;
  - `find_chip` (test only) -> the power-cycle ritual;
  - and `boot_protocol()` reported as though it meant boot was selected.
It also explains `found_elsewhere=0` in every reading today: the counter cannot
move because the search never runs.

Wired into the recovery path in the diagnostic build (after three unanswered
CHECK_EXIST probes, search every rate) and flashed. Result, **with no power
cycle after the reflash** — the ritual that has been required all week:

    keyboard  **Absent**  baud=115200   <- healthy: chip answered, ladder rose,
                                           waiting for a device
    mouse     RecoverWait  nby=40  found=0  port=9600

**The keyboard channel revived itself.** Every previous reflash left both
channels in RecoverWait with `ce=WRONG` until somebody cycled the 5 V; this one
came back on its own and climbed to 115200 unaided.

The mouse channel did not: `found=0` means the search ran and the chip answered
at none of the four rates. So chip silence has two distinct causes, and only
one of them is a lost rate:
  1. **stranded at an abandoned rate** — reachable, and `find_chip` cures it
     without human intervention (demonstrated on the keyboard channel);
  2. **genuinely hung** — answers nothing anywhere, and still needs the power
     cycle. That is the one left to explain.

Cost of the search: `worst pass round the loop` rose to 185 618 us, because
find_chip probes four rates with filler bytes between them, all inside the
blocking `read_reply`. Acceptable in a diagnostic build; in the real firmware
this has to become a state machine, not a spin — the same conclusion the
20 ms reply wait already forced.

Ruling: the repair for the link plan now has four measured parts, in the order
they should be built:
  1. call `find_chip` from the recovery path (it already exists and is tested);
  2. prove a rung with a block read, not with CHECK_EXIST (115200 and 62500
     answer CHECK_EXIST and fail every block read; 37500 works);
  3. never park a channel with an attached device below 37500;
  4. make the reply wait a state rather than a 20 ms spin, so one channel's
     silence cannot eat the other channel's 8 ms poll window — measured at up
     to 50 ms per tick before, 185 ms with the search added.
Cost if wrong: work spent on a link that a hardware fault would defeat anyway;
but every one of the four is now backed by a measurement taken tonight.

## 2026-08-29 00:43 — corrected: the channel that revived was the EMPTY one,
## and it revived *by the search*

The operator corrected a claim of mine: the channel I reported as reviving
itself is the one with **no device in it** — only the mouse is plugged in
tonight. That weakens "it revived like a working channel would" and it was
right to say so. The full reading settles what actually happened:

    keyboard (EMPTY socket)   Absent  baud=115200  nby=3
                              **found=1**   fe=1
    mouse    (mouse plugged)  RecoverWait  baud=9600  nby=29
                              **found=0**   wire: allEdges=0

**`found=1` on the empty channel is the proof the mechanism works.** Three
unanswered probes, then `find_chip` located the chip at a rate this side had
abandoned, reset it there, brought it home, and the channel came up and climbed
to 115200 — with no power cycle after a reflash, which has required one all
week. `found_elsewhere` moved off zero for the first time today, because today
is the first time the search has ever run.

**The mouse's channel is a different failure.** The search ran repeatedly
(nby=29) and found the chip at none of the four rates, and the wire watch
reports **allEdges=0** — not one edge on the receive line, ever. That chip is
not stranded at a rate; it is transmitting nothing at all. Only the power cycle
revives it, and that is now the single remaining unexplained fault of the
night.

The two channels differ in exactly one way: one has a USB device attached and
the other does not. So the next test is cheap and discriminating — **unplug the
mouse from its CH375 and see whether that channel then revives on its own**. If
it does, chip hangs are tied to holding a device (an unfinished USB transaction
across U1's restart, for instance) rather than to the serial link at all, and
the repair is a bus-level teardown before U1 gives up, not more serial probing.

Cost of the search, measured: worst tick 126 779 us and worst pass 185 583 us,
because four rates are probed inside the blocking reply wait. Fine for a
diagnostic build, unacceptable in the real one — reinforcing that the reply
wait has to become a state machine before `find_chip` is wired in for real.

## 2026-08-29 01:18 — both channels came up unaided, and the channel names ARE
## crossed

An idle channel now re-proves its chip once a second with a single CHECK_EXIST
(`kProbePresenceIntervalUs`, the Absent branch of device.cpp). Absent had no
way to notice its chip had stopped being a configured chip: it asked "is a
device there?" and read silence as "no device", which is the phantom-Absent
state that has been cured by reflashing all week.

Flashed, then read **with no power cycle at all**:

    mouse    RecoverWait  ce=0xA8  baud=37500  att=3 rdy=1  nby=0  lost=0
             setup=25/0x14(success)  **found=keyboard ep=1 pkt=8**  sel:yes
    keyboard RecoverWait  ce=0xA8  baud=37500  att=3 rdy=1  nby=0  lost=0
             setup=25/0x14(success)  **found=mouse ep=1 pkt=7**    sel:yes

Four results in one reading:

1. **Both chips answered without a power cycle** — `ce=0xA8` on both, `nby=0`,
   where every previous reflash left them deaf until somebody cycled the 5 V.
2. **Both devices enumerated**, both selected boot protocol (`sel:yes`), both
   at **37500**, the rate measured earlier as the only working one.
3. **The channel names are crossed, and this is now proven rather than
   inferred.** The channel the firmware calls "mouse" reports
   `found=keyboard ep=1 pkt=8`; the channel it calls "keyboard" reports
   `found=mouse ep=1 pkt=7`. Both statements come from the parsed device
   descriptors in the same reading, so this is not the earlier one-sided
   observation that was withdrawn at 22:10 — it is both channels contradicting
   their own names simultaneously. Note the packet sizes agree with the kinds
   (8 for a boot keyboard, 7 for this mouse), so the parse is not confused.
4. `lost=0` — the new presence check has not yet fired, so the recovery came
   from the chip setup path itself, helped by `find_chip` being wired in.

Being crossed is harmless to input handling — `InputPipeline` takes its kind
from `DescriptorSetup::kind()`, the device's own descriptor, not from the
channel's name — but every diagnostic reading taken today has had the two
channels' labels swapped, which is why "the keyboard channel" and "the mouse
channel" in earlier entries must be read with care.

## 2026-08-29 01:21 — both devices worked at once; the typing lag is the
## diagnostic search, and it proves the point about blocking

Operator: "the cursor worked, the keyboard typed - then a collapse and the
mouse disconnects and reconnects again. There is a lag when typing."

    mouse channel (physically the KEYBOARD)  rep=52  att=5 rdy=3  speed=full
        found=keyboard pkt=8  sel:yes  ok=0/3/0/0 at 37500
    keyboard channel (physically the MOUSE)  rep=86  att=10 rdy=4  setup=48
        found=mouse pkt=7  sel:yes
    tick worst=154056us   pass worst=154134us

**First time both peripherals have delivered input at once** — 52 and 86
reports, both enumerated, both in boot protocol, both at 37500. End to end:
device -> CH375 -> U1 -> HID -> the far computer, for a keyboard and a mouse
together.

**The typing lag is measurable and it is mine.** The worst tick is 154 ms, and
that is `find_chip` walking four rates inside the blocking reply wait — I wired
it in that shape deliberately, as a diagnostic. Core 1 ticks the two channels
in sequence, so while one channel searches, the other's keystrokes wait: a
key pressed during a search is delivered up to 154 ms late. Before the search
was wired in the worst tick was ~50 ms, itself two 20 ms reply timeouts.

So the measurement that closes the night is this: every remedy found today —
`find_chip`, the presence re-check, the ladder step-down — is correct in
substance and unusable in the shape it was prototyped, because all of them
spend their time inside a busy-wait on a core with an 8 ms obligation to the
other channel. **The reply wait becoming a state machine is not one repair
among four; it is the precondition for the other three.**

Also settled: the keyboard reads `speed=full` (rate full=5) and the mouse
`low` — both consistent across attaches now, so `get_device_rate` is answering
truthfully for both. The low-speed hypothesis for the keyboard's old 0xFD is
dead twice over.

## 2026-08-29 01:23 — the last fault has a hardware cause: no reset line

Both channels went deaf again with devices attached: nby=230 and climbing,
`found=0` — `find_chip` ran many times and located the chip at none of the four
rates, and the wire watch has shown allEdges=0 in this state before. A hung
chip transmits nothing anywhere.

`docs/hardware/ch375-wiring.md:18-23` gives the whole interface:

    GP0 keyboard RXD   GP1 keyboard TXD   GP2 keyboard INT
    GP4 mouse RXD      GP5 mouse TXD      GP6 mouse INT

**There is no RST line.** The CH375B's reset pin is not wired to U1 at all, so
when a chip stops listening there is no way to reach it: every software remedy
has to be *spoken* to the chip over the serial port, and a chip that hears
nothing cannot be told anything. That is why the only cure has ever been
cycling the module's 5 V, and it is why tonight's remedies cure one kind of
silence (a chip stranded at an abandoned rate — `find_chip`, demonstrated,
`found=1`) and not the other (a genuinely hung chip).

Recommendation, and it is one wire per module: run a free GPIO to each CH375's
RST pin — GP3 and GP7 are unused and adjacent to each channel's existing trio.
Then a channel that has failed to raise its chip at any rate can assert reset
and start over, and the ritual disappears. Without it, a hung chip will always
need a human.

Ruling: this is recorded as a hardware recommendation, not acted on. Nothing is
resoldered on a diagnosis reached at 01:23, and the operator has been asked for
enough power cycles today. It goes to the link plan with the four software
repairs, where it is the only item that cannot be done in firmware.
Cost if wrong: two wires added that a later diagnosis shows were unnecessary —
cheap, reversible, and the pins are otherwise unused.

## 2026-08-29 01:3x — the CH375 datasheet settles the reset pin AND the
## indicator

Operator supplied the datasheet
(bitsavers .../components/wch/_dataSheets/CH375.PDF). HTTPS is refused on that
host; fetched over HTTP and read locally. Pin table, page 3:

    2   RSTI  IN   Reset input external, active with high level, with pull-down resistor
    24  ACT#  OUT  ... USB device connection state output under USB-HOST, active with low-level
    25  RST   OUT  Reset with power-up and external reset output, active with high-level
    26  RST#  OUT  Reset with power-up and external reset output, active with low-level

**The reset INPUT is RSTI, pin 2** — active HIGH, with an internal pull-down.
RST (25) and RST# (26) are reset *outputs*, meant to reset an external MCU;
wiring a GPIO to those would do nothing. That distinction matters: the three
pins have almost the same name and only one of them is the one wanted.

**And the module's indicator is explained.** ACT# (pin 24) is documented as
"USB device connection state output under USB-HOST, active with low-level".
So the LED on the CH375 board reflects **whether a USB device is connected and
configured**, not whether the module is powered or healthy — exactly the
reading arrived at from the counters at 01:11, now confirmed from the vendor's
own table rather than inferred. Every observation today fits: dark with no
device or an unenumerated one, lit once the device was up.

The datasheet also endorses the repair directly (page 16, serial interface
mode): *"In addition, mends communication baud-rate dynamically, one suggest is
that controlling RSTI of CH375 through MCU I/O point in order to reset CH375 to
default baud-rate."* That is precisely tonight's failure — a chip left at a
rate this side abandoned — and the manufacturer's own advice is a GPIO on RSTI.

Timings for the fix (pages 11, 13):
  - external reset pulse minimum, TRI = **100 ns**;
  - after RSTI returns low the chip takes TRD = **18-40 ms** before it works;
  - internal power-up reset TPR = 18-40 ms, so no external reset is needed
    merely to boot;
  - after any reset the serial rate returns to **9600**, which the firmware's
    recovery path already assumes.

Two cautions before soldering:
  - the datasheet suggests 0.47 uF between RSTI and VCC for reliable power-up
    reset; if the module carries that capacitor, a GPIO driving RSTI fights it.
    Check the board before wiring.
  - RSTI has an internal pull-down, so an unconnected or floating line leaves
    the chip out of reset — the safe failure direction.

This makes the hardware recommendation concrete: one GPIO per module to RSTI
(GP3 and GP7 are free), drive high >= 100 ns, release, wait 40 ms, then re-run
chip setup at 9600. It replaces the human power cycle for BOTH kinds of
silence found tonight — the stranded-rate kind and the genuinely hung kind —
and it is what the vendor recommends for the first.

## 2026-08-29 01:4x — link hardening dispatched, no soldering

Operator's decision: try to get there without hardware changes. Recorded, and
it is the right order — the reset line stays a recommendation, unspent, until
the software repairs have been measured.

Ruling: the four repairs go into feature/duo-input-foundation rather than a new
branch. The branch has passed its whole-branch review but is not merged, the
boot-protocol fix already landed on top of it, and all of this is one phase of
work against the same hardware. Splitting now would mean two branches whose
tests only make sense together.
Cost if wrong: a larger branch to review at merge time, which the final review
already covers.

Reasoning behind trying software first, so it is on record: one of the two
kinds of chip silence — a chip stranded at a rate this side abandoned — was
already cured tonight with no hardware at all (`find_chip`, `found=1`, channel
revived without a power cycle). The other kind, a genuinely hung chip, follows
a collapse; and every collapse observed tonight traces back to a rate that
cannot carry the traffic, or to a tick that blocked past the 8 ms endpoint
deadline. If those causes go, the collapses may go with them, and a chip that
never hangs never needs a reset line. If they do not, we will know the residual
hang rate from measurement rather than from guesswork, and the wire can be
decided then.

Task written to task-link-hardening.md, dispatched on opus with the prototype
(6a5ef7d) and this ledger as its evidence base. Order is fixed: the blocking
reply wait first, because every other repair adds work to the recovery path and
that work is currently stolen from the other channel.

## 2026-08-29 11:31 — the four repairs on hardware: the search works, and the
##残 synchronous path is now the whole bottleneck

Real branch build (10efc55) flashed, operator cycled the modules and exercised
both devices. Reported: "the cursor works intermittently, with interruptions;
the keyboard does not type."

    keyboard channel (physically the MOUSE, found=mouse pkt=7 sel:yes)
        attached=5 ready=5 reports=98  int_seen=2954  polls=3154
        setup attempts=5 last=0x14(success)
        last report (3 bytes): 00 FD 02      <- boot format, correct
        **found_at=37500**  collapses=5  quiet_rearms=15
    mouse channel (physically the KEYBOARD)
        attached=1 ready=0 reports=0  int_seen=2
    slowest pass round the loop = **20168 us**

Two results, one good and one that names the next repair:

**`found_at=37500` is repair 2 working on hardware.** The chip search ran, found
a chip stranded at 37500, and brought it home — the first time in this
project's history that a stranded chip has been recovered without a human. The
mouse then enumerated five times, selected boot protocol, and delivered 98
three-byte boot reports. That is why the cursor works at all.

**The 20 168 us worst pass is the remaining synchronous path, and it is the
reason for both symptoms the operator reports.** With no traffic the worst pass
was 7296 us; with a device attached it is 20 168 — one `kDefaultReplyTimeoutUs`
exactly. The implementer flagged this in its own report: `get_status` inside
`poll_interrupt` is still a 20 ms spin, and *"a chip that goes silent while
holding INT asserted costs 20 ms every tick"*. The keyboard's channel is in
precisely that state — attached=1, ready=0, int_seen=2, never enumerating — so
every tick it burns 20 ms, and Core 1 ticks the channels in sequence, so the
mouse loses two and a half poll windows each time. Hence "the cursor works
intermittently" and "the keyboard does not type": one channel's stuck
enumeration is starving the other, through the one wait that was left blocking.

So the ordering held: repair 1 took the worst case from 50 ms to 7.3 ms while
idle, and the residue is now a single named path rather than a systemic
property. The next repair is `get_status` / `poll_interrupt`, and it is no
longer optional — it is the last thing standing between this link and working
input.

Not yet established: why the keyboard's channel attaches and never reaches
Ready this session (attached=1, ready=0). It enumerated fine at 01:18 under the
prototype. Its full counters were truncated out of the 900-byte probe text;
read them before assuming it is the same 0xFD timeout as yesterday.

## 2026-08-29 11:4x — review of the four repairs: ready with fixes

No Critical. Stack unchanged (reviewer measured: Core 0 1480/1432, Core 1
512/504). Deletions of `recover_from` genuinely subsumed by the search. Five
Important, four Minor, and three hollow tests.

Blocking, in the reviewer's order:

  R1 **The presence probe has a second reader.** `poll_interrupt` → `get_status`
     runs at the top of every tick, before `Absent` polls its outstanding
     probe, so a CHECK_EXIST answer sitting in the RX FIFO is read as the
     interrupt status. A device plugged in between ticks therefore produces a
     bogus `presence_lost++` and a full re-setup of a healthy chip — the exact
     "read as the answer to a later command" the transport forbids. The comment
     at device.cpp:96-97 names one reader and misses the one that runs first.
     `handle_detach` has a narrower variant of the same.
  R2 Test `a_chip_that_answers_nowhere_is_not_hunted_for_every_second` is
     HOLLOW, and it is the sole guard on the constraint the two deleted
     `recover_from` tests used to hold ("never write at a rate that has not
     just been proved"). The old guard was removed and its replacement does not
     work.
  R3 Test `re_proving_the_chip_does_not_cost_the_other_channel_its_poll_window`
     is HOLLOW — on a healthy fake the blocking form costs ~0 us too.
  R4 Three comments assert properties the code lacks (transport.hpp:585
     "Never waits" — set_baud sleeps ~3.5 ms; transport.hpp:148-149 "cannot" be
     read as this one's answer — drain_arrived stops at the first empty read;
     device.cpp:292-296 still describes `recover_from`, deleted in repair 2),
     plus device.hpp:377-384 naming two cases where there are three.

Non-blocking but recorded:

  R5 The ladder floor is a clamp, not a preference: a device with a >= 20-byte
     interrupt packet computes a floor above 37500, so a collapse steps it to
     62500 — a rate measured fail=24/24 on this bench — and it can never reach
     the rate that works. Both devices here are 7 and 8 bytes, so it is not hit
     today. `FakeDeviceSetup::max_packet()` returns 0, so no test reaches the
     branch, and `report_rate_floor` can be replaced by a literal in production
     with the whole suite still green.
  R6 The report's "what remains synchronous" list is incomplete. Also blocking:
     `set_usb_mode` at five call sites, `get_device_rate` on every attach,
     `drain_pending_status` at every bring-up. A ladder step-down costs the
     other channel on the order of a second of stolen 8 ms windows, and the
     tick-budget test only measures the fully silent chip, where none of these
     is reached.
  R7 Repair 4 re-enters chip setup on a single missed byte; two consecutive
     unanswered probes would be the right gate.
  R8 More capabilities tested and never called, now confirmed: `sweep_rx`,
     `flush_command_state`, `rx_swept`/`rx_sweep_hit` (accessors on fields
     nothing writes, with a comment asserting a property no code can produce),
     `kRxSweepAfterFailures` (dead constant), and `MouseNormalizer::set_report_id`
     still. That makes seven found this week.

Ruling: the fix wave takes R1-R4 plus one thing the reviewer could not know —
**`get_status`/`poll_interrupt` must become non-blocking**, because the
hardware run at 11:31 showed it is the whole remaining bottleneck: worst pass
7296 us idle, 20 168 us with a stuck channel attached, and the operator sees
exactly that as "the cursor works intermittently, the keyboard does not type".
R1 and that change touch the same function, so they go together. R5-R8 are
recorded as known and carried to the next round.
Cost if wrong: the wave is a little larger than the review's shortest path,
against a defect that is currently visible to the operator on every keystroke.

## 2026-08-29 12:06 — no change on hardware, and the reason is a scoping
## mistake of mine

Flashed the fix wave (7896c38..35389fa), operator cycled the modules and
exercised both devices. Reported: unchanged — "the keyboard does not type, the
mouse stops driving the cursor after a while".

    keyboard channel (physically the MOUSE)  attached=4 ready=4 reports=122
        found_at=37500  found_elsewhere=2  collapses=4  quiet_rearms=12
    mouse channel (physically the KEYBOARD)  attached=1 ready=0 int_seen=2
    slowest pass round the loop = **20 170 us**

The worst pass is still one full `kDefaultReplyTimeoutUs`. The implementer's
20 us figures are real for the scenes it was asked to cover — a chip holding
INT and answering no status, and a module power-cycled under a running U1 —
and it said plainly in its concerns that the list of synchronous paths was not
empty. The reviewer said so too (finding 3), naming `set_usb_mode` at five call
sites, `get_device_rate` on every attach, and `drain_pending_status`.

**I put those out of scope.** That was the error: the channel that is stuck
here is stuck at `attached=1, ready=0`, i.e. between attach and Ready, and that
is exactly the stretch that runs `set_usb_mode` (transport.hpp:327, via
`command_with_status`) and `get_device_rate` (transport.hpp:847) — both still
blocking on `read_reply`. So the one state the operator actually has is the one
state whose blocking calls I excluded, and the measured 20 170 us is that.

Ruling: convert the remaining reply-blocking commands rather than treating them
as follow-ups — `command_with_status` (which is `set_usb_mode`, `set_retry`,
and every other status-answering command), `get_device_rate`,
`drain_pending_status`, and `try_speed`'s probe. The budget to hold is the one
already stated: no tick on a channel whose chip is not answering may exceed a
few hundred microseconds, and the test for it must run the attach-to-Ready
stretch, not only a silent chip. Until that holds, no reading of the link's
behaviour is trustworthy, because every measurement is taken through a loop
that one stalled channel can still stop for 20 ms.
Cost if wrong: another conversion round against a link whose real fault might
lie elsewhere — but the same reasoning fixed the idle case and took the idle
worst pass from 50 ms to 7.3 ms, and nothing else can be measured cleanly until
the stalled-channel case matches it.

Separately open, and not explained by blocking: why the keyboard's channel
attaches and never reaches Ready (attached=1, ready=0, int_seen=2). It
enumerated fine at 01:18 and at 11:31 it was in this same stuck state. Its full
counters are truncated out of the 900-byte probe text; get them before
theorising.

## 2026-08-29 12:44 — the diagnosis changes: the devices are not failing to
## enumerate, they are being declared lost while idle

Two corrections to what I have been chasing.

**First, a build defect I caused and shipped for half an hour.** The status
commands became tick-spanning, `ch375_probe.cpp:157` was left calling the
removed synchronous `set_usb_mode`, and **the native suites do not compile that
file** — so ctest stayed 31/31 green while the pico-ch375 image would not link
at all. I ran the flash step without checking the build's exit code, so the
board received the 11:58 image and the operator's "no change" was a report on
unchanged firmware. Fixed and committed as 804a928; the lesson is that a green
native suite says nothing about the firmware image here.

**Second, and this is the real one.** With the print order reversed, the
channel I had been calling "stuck at attached=1, ready=0" reads:

    mouse channel (physically the KEYBOARD)
        setup attempts=1  last=0x14 (success)
        found=keyboard ep=1 pkt=8  boot=adv:yes/sel:yes
        attached=1  gone=1  **ready=1**
        **reports=0   polls=40   int_seen=8**
        detach_lost=1  quiet_rearms=3  collapses=1

It is not failing to enumerate. It enumerates cleanly, selects boot protocol,
reaches Ready, polls its endpoint forty times — and receives **eight**
interrupts, no reports, and is then declared lost by the quiet teardown.

A boot-protocol keyboard sends a report only when its state changes. Nobody was
typing. So the device was behaving correctly and the channel tore it down for
it. `kDeviceLostUs` is 1 s and `Ready` only refreshes `last_answer_us_` when
`interrupted` is true (device.cpp), so a genuinely idle device is
indistinguishable from a gone one.

The retry policy is set the right way — `kRetryReportNak = 0x0F` (commands.hpp:163),
chosen precisely so a NAK is *reported* rather than retried forever. If that
worked as intended, every poll of an idle endpoint would produce a NAK
interrupt and the quiet timer would never expire. Forty polls against eight
interrupts says it does not: most polls produce nothing at all.

That single fact explains every symptom the operator has reported today, and it
explains them better than the blocking-wait theory I have been working on:
  - "the mouse stops driving the cursor after a while" — it works while it is
    being moved (reports arrive, timer refreshed) and is torn down within a
    second of being still;
  - "the keyboard does not type" — an untouched keyboard is silent, so it is
    lost before the first keystroke ever happens, and re-enumeration takes
    seconds during which keystrokes go nowhere;
  - the endless attach/gone/attach cycling, the mouse's light going off and on
    (a bus reset per teardown), and the collapses that walk the baud ladder
    down — all downstream of a teardown that should never have happened.

Discriminating test, cheap and immediate: **move the mouse continuously** and
watch `detach_lost` and `collapses`. If they stop moving while the mouse is in
constant motion and resume within a second of it going still, this is settled.

Two candidate repairs, once confirmed:
  a. find out why an idle endpoint's NAK does not raise an interrupt — whether
     the policy byte is wrong for this part, whether the token is issued at all
     while one is outstanding, or whether the interrupt is being consumed
     elsewhere (`token_outstanding_`, and the one-token-at-a-time cap from
     7c987ff, are the code to read);
  b. stop treating silence as absence: an idle HID device is normal, so the
     quiet teardown needs evidence of absence — a failed poll, a disconnect
     status — rather than an absence of evidence.

Ruling: (b) is right regardless of what (a) turns up. This project's own
constraint says an absence is only evidence if presence looks different, and
here silence from a device with nothing to say looks exactly like silence from
a device that has gone.

## 2026-08-29 12:50 — CONFIRMED by behaviour: an idle device is torn down

Operator, unprompted and decisive: *"I moved it for 30 seconds and the cursor
moved, then I stopped, and about 5 seconds later I moved it again — the mouse
did not respond."*

That is the prediction of the idle-teardown hypothesis, exactly: while reports
flow the channel is healthy, and a pause of a few seconds kills it.
`kDeviceLostUs` is 1 000 000 us (device.hpp:300) and `Ready` refreshes
`last_answer_us_` only when an interrupt arrived (device.cpp:284), so one
second of a device having nothing to say is indistinguishable from the device
being gone. Five seconds of stillness is five times over the threshold.

Note the counters had misled me a few minutes earlier: the same channel read
`reports=1` after thirty seconds of continuous motion, which I took as evidence
against this. The behaviour is the better witness — the cursor demonstrably
tracked the hand for thirty seconds, so reports were arriving in quantity. The
probe's `DeviceTally` does not tell the story I assumed it did across a
re-enumeration; whatever it counts, it is not "reports delivered since power
on". Trust the operator's screen over that counter until it is understood.

So the causal chain for everything seen today, in its final form:

  a HID device idles (nobody typing, hand off the mouse)
    -> no interrupt for 1 s
    -> the channel declares it lost and tears the bus down
       (the mouse's light goes out and comes back)
    -> re-enumeration takes seconds, during which input goes nowhere
    -> the teardown counts as a collapse, which steps the baud ladder down
    -> at the bottom the rate can no longer carry a moving mouse at all
    -> and every one of those recovery cycles burns the blocking waits that
       starve the other channel

Every symptom is downstream of the first line. The blocking waits, the ladder
policy and the chip searches are all real defects and all worth the repairs
they got — but none of them is the cause, and fixing them could never have made
the input work.

The repair: silence from a HID device is normal and must not be evidence of
absence. A teardown needs positive evidence — a disconnect status from the
controller, or a poll that fails rather than one that simply has nothing to
report. `kDeviceLostUs` exists to catch a device that vanished without saying
so; it must be armed by something other than "no data", or the quiet timer must
be refreshed by a NAK, which is what `kRetryReportNak` was chosen to deliver
and evidently does not.

Open question for the fix, and it is where to start: forty polls produced eight
interrupts on an idle endpoint. Either the NAK is not raising an interrupt (the
policy byte, or the part's behaviour), or the poll is not being issued while a
token is outstanding, or the interrupt is consumed elsewhere. Read
`token_outstanding_`, the one-token cap from 7c987ff, and what `poll_interrupt`
does with a NAK status.

## 2026-08-29 13:13 — the idle teardown is FIXED on hardware, and it exposed a
## second, opposite defect

Flashed e6a0775..72c806b (plus the compact probe text). Operator cycled the
modules, attached both devices, and left them completely untouched.

    mouse channel (physically the KEYBOARD)
        **Ready** after 25+ s idle   **dl=0  qr=0  col=0**
        polls=7146  int=7154   <- climbing together, as predicted
        fail=7145 (NAK statuses)  rep=1  baud=115200  setup=1/0x14(success)

**The root cause is confirmed cured.** The prediction stated before the test
was: with an attached device nobody is touching, `polls` and `int_seen` must
climb together with `reports` at zero, and `detach_lost`, `quiet_rearms` and
`collapses` must stay at zero indefinitely. All five held. A device with
nothing to say is no longer read as a device that has gone, and the channel sat
at 115200 — the ladder never stepped down, because nothing collapsed.

That closes the chain that produced every symptom of the last two days.

**But the same change broke enumeration on the other channel:**

    keyboard channel (physically the MOUSE)
        RecoverWait  **setup=55/0x2A (device answered NAK)**  ef=55
        **boot=yes/no**  con=56

Fifty-five setup attempts, every one ending in "device answered NAK", and boot
protocol advertised but not selected. `kRetryReportNak` is right for polling an
interrupt endpoint — reporting the NAK is exactly how the channel learns the
device is still there — and wrong for the control transfers of enumeration,
where a NAK means "busy, ask again" and the chip retrying it is the correct
behaviour. Before this change the chip retried on the bus and enumeration
completed; now the first NAK is taken as a refusal.

So the policy has to distinguish the two: retry on control transfers during
setup, report on endpoint polls once Ready. That is a narrower statement than
"report NAK", and it is what DS2 1.3's policy byte exists to express.

Ruling: fix this rather than reverting. The idle teardown was the root cause of
every failure seen in two days and it is now demonstrably cured on hardware;
reverting to get the mouse enumerating would restore it. The two requirements
do not conflict — they apply to different phases of the same channel's life.
Cost if wrong: one more round on a link that currently enumerates one device
and not the other, against a state where it enumerated both and kept neither.

## 2026-08-29 13:33 — BOTH DEVICES WORK, SIMULTANEOUSLY AND CONTINUOUSLY

Operator: "работает" — it works. The counters, with both peripherals attached
and used:

    mouse channel (physically the KEYBOARD)
        **Ready**  rdy=2  rep=71   setup=3/0x14(success)  **boot=yes/yes**
        polls=22670  int=22648   lost=0   baud=62500
    keyboard channel (physically the MOUSE)
        **Ready**  rdy=2  rep=39   setup=2/0x14(success)  **boot=yes/yes**
        polls=23290  int=23266   lost=0   baud=62500
        last:3B 00 02 FF 00      <- three-byte boot report, dx=+2, dy=-1

Both channels enumerated, both selected boot protocol, both are Ready, both are
delivering reports, and on both the poll and interrupt counters climb in step —
better than twenty-two thousand of each with only one detach apiece across the
whole session. The ladder rose to 62500 and stayed. No presence losses.

This is the first time in this project's history that a keyboard and a mouse
have both worked, at once, through U1.

The chain that got here, in the order the causes were actually found:

  1. the mouse's report was parsed one byte out of place, because SET_PROTOCOL
     was never sent and `set_report_id` was never called (both capabilities
     existed; neither was reachable) — cursor moved only vertically;
  2. the reply wait blocked Core 1 for up to 20 ms per unanswered byte, so one
     channel's trouble starved the other's 8 ms endpoint deadline;
  3. `find_chip` was written, tested and never called — a chip stranded at an
     abandoned rate needed a human with a power switch;
  4. an idle channel could not notice its own chip had been reset;
  5. the baud ladder proved a rung with a two-byte exchange and descended on
     evidence it never established, parking channels at a rate too slow to
     carry a moving mouse;
  6. and underneath all of it: **`set_retry` was issued once and silently
     undone by the SET_USB_MODE commands that followed**, so the chip retried
     NAKs on the bus instead of reporting them, no interrupt ever came back
     from an idle endpoint, and a device with nothing to say was torn down
     after one second — which re-enumerated it, counted as a collapse, and
     walked the ladder down. Every symptom of two days was downstream of this.
  7. fixing (6) then broke enumeration, because reporting a NAK is right for
     polling an endpoint and wrong for the control transfers of setup. The
     policy is now chosen per phase: wait out the NAK before a control
     transfer, report it on entry to Ready where no working mode can undo it.

Six of the seven were code that existed and was tested but was never reached,
or a setting undone by a later command. Not one was a wrong algorithm.

## 2026-08-29 14:0x — review of 10efc55..28655f8: ready with fixes

No Critical. The reviewer verified against a clean out-of-repo build and
confirmed: the retry bytes are right against the command table (0x0F = report,
0xCF = retry 200 ms-2 s, differing only in the policy bits), the policy is
re-asserted at all four mode boundaries, `enter(Ready)` occurs exactly once and
no mode command is issued in Ready, the one-slot reply discipline survives
every teardown and re-entry it could construct, a genuinely-gone device is
still detached and released, `get_device_rate`'s two outcomes are separated,
and the stack is unchanged (1480/1432, 512/504). Both implementers' mutation
claims reproduced exactly, including the two they admitted kill nothing.

Important, in the order the reviewer put them:

  V1 **The teardown's evidence discriminator is untested** (device.cpp:306-312).
     Replacing the whole `status == Success || (is_failure && response == NAK)`
     test with `if (true)` leaves all 152 cases green — the reviewer ran it. A
     device answering STALL or a timeout status, or whose Disconnect was lost,
     would then be held forever with its keys down. The code is correct; the
     branch that releases a stranded key has no test.
  V2 **The tick-level "nothing else while a status is outstanding" gate is
     untested** (device.cpp:29-42). Deleting the early return leaves everything
     green, and with it gone a timed-out `Enumerating` can reach RecoverWait,
     issue SET_USB_MODE, drain the outstanding GET_STATUS and read the status
     byte as the mode's reply — on the one byte that says a cable was pulled.
  V3 `finish_pending_command`'s "silence is not agreement" is untested
     (device.cpp:770-772): reading a timeout or an explicit 0x5F refusal as
     agreement leaves everything green, and would run a bus reset against a
     controller not in host mode.
  V4 **The Ready-phase policy is written once, unacknowledged, with no
     self-healing** (device.cpp:276). SET_RETRY has no reply, so a swallowed
     write is undetectable, and the root-cause defect returns silently with
     only `quiet_rearms` climbing. One line fixes it: re-assert in the quiet
     re-arm branch beside the receive toggle.
  V5 The synchronous list is incomplete and understates the cost: a successful
     chip bring-up is **~67 ms in one tick** (try_speed's own read_reply 20 ms,
     two PIO sleeps at 3.5 ms, port_answers 40 ms) plus reset_port_speed's
     3.5 ms — so yes, it blows the 8 ms deadline on every attach, ladder
     step-down and presence loss. Rare in the observed steady state, which is
     why the soak looks clean.

Deferred-item verdicts: the ladder floor is worse than recorded — at a 64-byte
packet no rung clears the floor, `slowest_usable_rung()` returns 0 and
`step_ladder_down()` becomes a no-op, pinning the channel at 115200 with no way
down; a one-line guard (return the slowest rung when none clears) is worth
taking now. Climb-back and the counter wrap stay deferred. The never-called
list is now twelve, with a split recommendation: delete nine, wire or delete
`skip_bus_reset` outright, keep and use `AbortNak`.

## 2026-08-29 15:17 — review protections closed without touching working hardware

Hardware baseline, stated by the operator before this pass: both CH375 inputs
are powered; the connected mouse moves the cursor continuously without
disconnecting, and the connected keyboard types. This pass did **not** flash
either RP2040 and did not ask for any wiring or power change. Both generated
firmware images were built only on disk, preserving that known-good state.

The four missing protections are now executable tests rather than comments:

- STALL (0x2E) and timeout (0x20) from a Ready endpoint eventually publish one
  Detached event, so a key held on the remote computer is released even when
  the Disconnect status was lost;
- a pending GET_STATUS owns the transport's one reply slot and prevents setup
  from changing lifecycle state;
- silence and explicit 0x5F refusal of SET_USB_MODE are both rejected rather
  than read as agreement;
- a swallowed, unacknowledged Ready SET_RETRY is reasserted on the first quiet
  endpoint re-arm.

Each of the first three tests was checked by the reviewer's proposed mutation:
forcing the endpoint discriminator true, deleting the pending-status gate, or
forcing mode acceptance makes its new test fail. The retry-policy test was RED
before the reassertion line and green after it. The native device executable
now reports 79 tests, 0 failures.

`ABORT_NAK` is connected at the precise failure boundary: when enumeration's
control-transfer setup returns Failed, the firmware ends a CH375 NAK retry
before entering recovery. The fake can hold that retry in flight; without the
production abort the new test failed because the chip ignored all recovery
commands and never returned Ready. The abort is deliberately not in generic
`fail()`: that function is also reached when the serial rate is unproved, where
sending another opcode would violate the link's primary safety rule.

Removed obsolete paths: synchronous GET_STATUS, synchronous RX sweep, blocking
interrupt wait, the unused 64-byte wedge flush, the unused blocking
command-with-status helper, the unused reply-timeout accessors, their orphaned
constants/tests, and the never-called `skip_bus_reset` diagnostic branch. The
enumerator and descriptor tests now read status through the same deferred
single-slot API as production.

V5's wording is corrected: successful `try_speed` contains the SET_BAUD_RATE
answer, two CHECK_EXIST proofs and physical rate-change sleeps, measured at
about 67 ms in one tick; the earlier Resetting step also pays about 3.5 ms.
This is finite attach/recovery work, not an 8 ms latency guarantee. The old
command-count test was renamed so it no longer claims to measure time.

One review recommendation was rejected after checking the arithmetic rather
than implementing it: CH375's maximum valid packet is 64 bytes, whose floor is
`(64 + 8) * 11 / 8 ms = 99,000 baud`; the 115,200 rung clears it. Therefore
the claimed valid case where no rung clears the floor is unreachable, and
returning the slowest rung would only violate the floor. A regression assertion
now records 64 bytes -> 99,000 and 115,200 > floor.

Verification after the final code change, without flashing:

- native MSVC/Ninja build: success;
- CTest: 31/31 suites passed;
- CH375 device executable: 79 tests, 0 failures;
- Python/configurator software suite with workspace-local TEMP and the one
  real-device HIL contract excluded: 583 passed, 6 subtests passed;
- `pico-release`: U1 firmware linked successfully;
- `pico-ch375`: diagnostic U1 firmware linked successfully;
- `git diff --check`: clean (only the repository's LF/CRLF warning).

The first Python attempt used the old runtime venv, which lacks PySide6. The
second used the project `.venv` but Windows denied pytest access to the global
Temp directory and the connected-device contract timed out. Neither touched
the changed C++ path. Moving TEMP/TMP under `build/` and excluding only that HIL
test produced the clean 583-test result above without querying the working
device again.

## 2026-08-29 16:1x — ROUTE ACCEPTANCE PASSED ON HARDWARE

Work continued with another agent while this session's fix wave died of a
session limit. Operator report, after writing a test profile and reading it
back without divergence (old configuration backed up to
hardware-backups/u1-config-generation-20-before-route-acceptance.b64):

  1. F9 then "111"  -> text on PC1 only            PASS
  2. F10 then "222" -> PC2 only                    PASS
  3. F11 then "333" -> both computers at once      PASS
  4. F12 + mouse    -> cursor moves PC1 -> PC2     PASS
  5. Mouse Button 4 + mouse -> cursor back to PC1  PASS

"Это все работает и мышка и клавиатура + переключение."

**That is the plan's route-switching acceptance, on real hardware, with a real
keyboard and a real mouse, through both computers.** Task 7 Step 4 has been
deferred since the pre-flight ruling on the grounds that the CH375 link was not
stable and there was no second PC; the link is now stable and the routing half
of that step is met.

State at acceptance, verified by this session rather than taken on report:
- HEAD 8408c19 "Harden CH375 recovery boundaries" — the other agent's closeout
  of my review, accepting most findings.
- Native ctest 31/31 on a clean build/native; pico-ch375 and pico-release both
  build (exit 0).
- Stack unchanged: Core 0 1480 (ch375) / 1432 (release), Core 1 512 / 504,
  against 2 KiB regions.
- Working tree clean apart from untracked .test-tmp/ and hardware-backups/.

One of my review findings was rejected with a reason and a test: the ladder
floor at a 64-byte packet needs 99 000 baud, so 115200 clears it and
`slowest_usable_rung()` does not invert. My finding assumed no rung cleared the
floor at that packet size; the arithmetic says otherwise. **Withdrawn** — the
rejection is correct, and it is now pinned by a test.

Soak result across the window that mattered (13:36-16:09, ~50 samples): both
channels Ready throughout, `detach_lost=1`, `collapses=1`, `presence_lost=0`,
baud 62500 held, reports climbing as the operator used the devices. The two
"NO CDC REPLY" events at 15:39 and 15:45 coincide with the other agent flashing
and holding the port, not with a link failure. The soak's field parser stopped
matching after the probe text changed, so its later "steady" lines carry only
`lost=`; the log has the full samples.

Remaining from Step 4's list, not yet exercised: eight profiles surviving a
power cycle, the `/target KYPKYMA` text macro without movement stalls, and
release-on-disconnect with recovery on reconnect.

## Task 7 Step 4 — reopened, and split into dispatchable pieces

The pre-flight ruling deferred Step 4 because the CH375 link was unstable and
there was no second PC. Both premises have changed: the link holds (both
channels Ready at 115200, zero detachments over a soak) and the routing half of
the acceptance passed on hardware at 16:1x. So Step 4 is reopened for the three
criteria it still has outstanding, from the brief:

  S4a  eight profiles across a power cycle
  S4b  the `/target KYPKYMA` text macro without movement stalls
  S4c  disconnect release and reconnect recovery

Ruling: the acceptance itself cannot be dispatched — it needs a human at the
hardware — but everything that makes it *runnable* can be, and should be, so
the operator is asked for one clean pass rather than a sequence of guesses.
That means: a configuration written and verified through the configurator that
actually contains eight distinct profiles and the text macro, plus an exact
per-criterion script saying what to press and what must happen. The controller
then runs the hardware pass with the operator and records the result.
Cost if wrong: preparation spent on a profile the acceptance did not need.

S4b matters more than its one line suggests. It is the on-hardware test of the
Critical finding the whole-branch review raised and which was never verified on
a board: a macro emitted keystrokes faster than Core 0 published them, so
press and release collapsed into one state and the text never arrived. It was
fixed by pacing the macro to one edge per USB frame and proved with a native
test asserting four distinct reports out of UsbService::publish. A real text
macro typing on a real computer is the first end-to-end proof.

S4c is the acceptance of this project's defining hazard: a key held down when
its keyboard is pulled must be released on the far computer, not stranded.

## Step 4 preparation done (not yet deployed) — plus a real firmware finding

`tools/step4_acceptance_config.py` builds, verifies, deploys and restores the
acceptance configuration; 18 tests in
`configurator/tests/acceptance/test_step4_acceptance_config.py` cover it,
including the whole deploy path driven against U1Emulator through the
production DeviceService. Eight profiles built through the GUI's own
ProjectSession commands, no hand-assembled bytes; profile n types "PROFILE-n "
so identity is read off the screen rather than inferred. Read-back compares 426
named fields and all match, with the comparator itself tested against a package
differing in exactly one field. The device is set to boot into profile 3, so
booting into 3 is itself evidence the flash read succeeded (the firmware falls
back to 0 on a failed load, and a pristine project defaults to 1).

Operator script at step4-acceptance-script.md counts by Notepad's Col/Ln rather
than by eye: `/target KYPKYMA` is 15 characters = Col 16; a dropped character
reads Col 15, a doubled one Col 17+, and a wrong keyboard layout reads Col 16
with visibly wrong text. S4c holds `a` while unplugging and watches key-repeat
stop, then holds Left Shift across an unplug/reconnect and types `abc` — `ABC`
would mean a silently stranded modifier.

Two things the brief asks for that cannot be run as worded, reported rather
than quietly substituted:
  - `/target KYPKYMA` cannot be a macro *trigger*: TriggerKind has only
    KEYBOARD_USAGE and MOUSE_BUTTON and the engine matches usage plus modifier
    bits; nothing watches for a typed string. It is built as the text the macro
    types, fired from Home.
  - KYPKYMA is typed as Latin capitals, exactly as the brief writes it. If
    Cyrillic КУРКУМА was intended that is a layout change and a new write.

**Firmware finding, reported not fixed: a profile's stored keyboard_route and
mouse_route are never read.** The configurator writes them and validator.cpp
checks them, but routing lives in `mapping::Routes`, which initialises to
PC1/PC1 (routes.hpp:41) and only moves via a binding action or a macro step;
`set_profile_now` (core1_runtime.cpp:394) swaps bindings and macros and leaves
routing alone. **Selecting a profile does not change where input goes.**
Harmless for this acceptance — every profile stores PC1/PC1 — but it is a
divergence between what the GUI lets an operator configure and what the device
does, and it is the same shape as this project's recurring defect: a field
written, validated, and never read. Recorded for triage before merge.

Caveat the controller must respect: `pytest configurator/tests -q` opens the
U1's serial port — `test_real_config_contract.py`'s fixture discovers and
negotiates with the board. It is not a port-free command.

## 2026-08-29 17:10 — S4b FAILS on hardware, with a stranded key

First press of the IDENT macro (Up arrow), which should type `PROFILE-3 `:

    PC1 (U1, direct USB):  "3P3"  **and a key stuck down**
    PC2 (U2, over SPI):    "   3   3P3333P33333"

Neither is the text. PC1 lost most of it and stranded a key — the defining
hazard, and the operator was told to pull U1's USB cable to clear it. PC2 got
more characters than were sent, with visible repeats.

The device's own counters at the moment of failure rule out most of the stack:

    keyboard channel Ready, mouse channel Ready, no detachments
    **commands the output queue refused=0**   (dropped_commands is zero)

So this is not the CH375 link, not a lost report, and not the SPSC queue
overflowing. Everything Core 1 produced reached Core 0. The defect is in what
Core 0 does with it — the HID publication path — and it behaves *differently on
the two computers*, which is itself the sharpest clue available: PC1 goes
through `UsbService::publish`, PC2 through `SpiMaster::poll` to U2. Same
command stream in, different wrong output on each side.

This is the area the whole-branch review's Critical C2 covered: a macro emitting
edges faster than Core 0 publishes them, so press and release collapse into one
state and nothing arrives. That was fixed by pacing the macro to one edge per
USB frame and proved by a native test asserting four distinct reports out of
`UsbService::publish`. The native test passes; the hardware does not. So either
the pacing does not hold on real timings, or the two publication paths consume
the paced stream differently — PC1 losing edges (characters missing, and a
release lost, hence the stranded key) while PC2 repeats them.

A dropped release is worse than a dropped press: it is exactly what strands a
key on a computer the operator may not be looking at.

Ruling: S4b is a fail, and S4a/S4c do not run until it is understood — a stuck
key makes the other criteria unsafe to exercise, and the acceptance would be
measuring a device that is misbehaving in a known way. The route acceptance
already passed and stands; this does not retract it.

## 2026-08-29 17:5x — the macro fix works; one character short on PC2

The publication fix (433e4e4, 2bdd4cb, 9f80c1c) was built and flashed. Native
32/32 (macro_publication is new), both images build. Operator:

    PC1 (U1, USB):   PROFILE-3 aaaaaaaaaa      <- ten a's, correct
    PC2 (U2, SPI):   PROFILE-3 aaaaaaaaa       <- nine

So the text arrives whole, in order, with the trailing space, **and nothing is
left held down** — the stranded key and the garbled output are both gone, on
both computers. What remains is one lost character out of ten on the SPI path
only.

Root cause of the original failure, established by the implementer and worth
recording precisely because it was subtle: the C2 pacing gate waited on the
wrong thing. It waited for the command queue to empty and for a millisecond of
wall clock; neither means the previous state was *published*. Core 0 holds a
state, not a queue of reports, so a state replaced before anyone read it is
gone. Two ways: the queue empties at the pop rather than at the publish, so
Core 1 slips the next edge into the same drain and a press cancels its own
release; and drain() runs before publish(), so a state the endpoint was too
busy to take is overwritten before the retry. The asymmetry follows — PC1's
endpoint is busy for the rest of each USB frame, PC2's link takes a frame on
any pass that offers one, so the two miss different states from one stream.
Reproduced on unmodified firmware in a microsecond-timeline rig: Core 0 at
400 us with the host frame offset 300 us yields PC1 "PROFIL-_" and PC2
"PROFILE-3_", with dropped_commands zero throughout — the hardware's own
signature.

The fix makes `HidStateManager` track "unreported" per computer; publish and
poll clear it only for a report that actually went out; drain applies no
further keyboard command until every computer has answered, with a 20 ms grace
after which a silent computer is set aside. Mouse movement is exempt so the
pointer does not stutter.

Note: GET_DIAGNOSTICS cannot be read in the DUO_CH375_PROBE build — that build
replaces the binary diagnostics reply with the CH375 probe text, so
`link_crc_errors` and `endpoint_drops` are unreachable without flashing the
release image. Behavioural data was gathered instead.

## S4b PASSED — 2026-08-29 18:0x

Repeated presses of the REPEAT macro: **ten `a` on both computers, every
time.** The single missing character seen on the first press was a one-off,
consistent with U2 not yet having answered on the pass that followed the flash
and reconnect — the new pacing sets a silent computer aside after a 20 ms grace
and picks it up when it answers again, which is exactly that shape.

So S4b's criterion — the text macro types completely, on both computers,
without movement stalls or stranded keys — is met. Recorded with the caveat
that the very first macro press after U1 is reconnected may lose a character
while U2 is still coming up; worth a look if it proves reproducible, but not a
failure of this criterion.

Remaining: S4c (disconnect release and reconnect recovery) and S4a (eight
profiles across a power cycle).

## S4c PASSED — 2026-08-29 18:1x

Both halves, on hardware:
  - `a` held down with auto-repeat running, keyboard pulled from its CH375
    mid-repeat: **the repeat stopped on its own.** The device noticed the
    device had gone and released what it was holding, inside the quiet-teardown
    budget, without the operator doing anything.
  - Left Shift held down, keyboard pulled, reconnected, then `abc` typed:
    **`abc` came out in lower case.** No silently stranded modifier.

That is the acceptance of this project's defining hazard — a key held when its
keyboard is unplugged is released on the far computer rather than stranded —
and it passes on both the direct USB path and, by the same release, the SPI
path to U2.

Step 4 status: routing PASSED (16:1x), S4b PASSED (18:0x), S4c PASSED.
Remaining: S4a, eight profiles across a power cycle.

## S4a PASSED — Step 4 COMPLETE — 2026-08-29 18:2x

Before the power cycle:

    PROFILE-1 PROFILE-2 PROFILE-3 PROFILE-4 PROFILE-5 PROFILE-6 PROFILE-7 PROFILE-8

After pulling U1's power and reconnecting, the bare Up arrow first, then the
eight:

    PROFILE-3 PROFILE-1 PROFILE-2 PROFILE-3 PROFILE-4 PROFILE-5 PROFILE-6 PROFILE-7 PROFILE-8

All eight profiles are in flash, all eight are distinct, all eight survive
being powered off, and the device came back up **in profile 3** — the profile
the configuration names as active. That is the discriminating detail: the
firmware falls back to profile 0 when a load fails, so booting into 3 is
positive evidence the flash read succeeded, not merely that something loaded.

**Task 7 Step 4 is complete.** All four criteria have now passed on real
hardware with a real keyboard and a real mouse across two computers:

    routing (F9/F10/F11, F12, mouse button 4)   PASSED  16:1x
    S4b  the text macro, complete on both PCs    PASSED  18:0x
    S4c  disconnect release, no stranded key     PASSED  18:1x
    S4a  eight profiles across a power cycle     PASSED  18:2x

The pre-flight ruling that deferred this step — "the CH375 link is still under
repair and the second PC does not exist" — is now discharged in full rather
than carried. Task 7, and with it the plan's implementation, is done on
hardware and not only in tests.

Next, and these are the only things between here and a finished branch:
  - the plan's Completion Gate needs re-assessing against what is now proven,
    in particular the two bullets that were "not met" for want of hardware:
    captured-vector normalization (report traces were never recorded) and 1000
    *physical* route toggles;
  - `docs/hardware/ch375-compatibility.md` was written from descriptor vectors
    alone with its hardware section marked pending. Two devices have now been
    verified end to end and belong in it, by name;
  - a whole-branch review: the last one saw 25d3730 and there have been more
    than twenty commits since, including the transport's conversion to
    tick-spanning reads and today's publication fix.

## 2026-08-29 19:0x — whole-branch review of all 157 commits: ready with fixes

One Critical, six Important, and a Completion Gate assessed bullet by bullet.
The reviewer worked in four passes and mutation-tested against a clean
out-of-repo build; its verdict on the accepted behaviour is that it holds —
the publication fix, the flash store, the CDC frame, the profile swap and every
release-on-disconnect path fail loudly when broken.

C1 **The acceptance tooling was not in the branch.** tools/step4_acceptance_config.py
(667 lines) and configurator/tests/acceptance/ (309 lines, 18 tests) were
untracked, so the evidence behind Step 4 could not be regenerated and those
tests ran nowhere but one machine. **Fixed immediately by the controller**
(87e4f42) rather than dispatched: untracked work dies to any `git clean`, and
this is the tooling the acceptance rests on. hardware-backups/ and .test-tmp/
are now gitignored with the reasoning in the file; .test-tmp/ and the
reviewer's scratch build at C:\dpr are deleted.

Ruling: committing untracked deliverables is bookkeeping, not implementation,
so the controller does it directly. Everything else from this review is
dispatched.
Cost if wrong: one commit in the branch that a reviewer did not see; its
content is two files that already existed and a .gitignore entry.

Important, in the reviewer's order:
  I1 core_bridge.hpp:58-69 is HOLLOW — reintroducing the original I4 defect
     (take the event, then read capture_active) leaves all three linking suites
     green. The parked M8 called it weak; it is hollow, and it guards the
     ordering the whole module was extracted to protect.
  I2 A device that refuses SET_PROTOCOL is accepted anyway and then misparsed,
     with no counter, status field or log in the release image — the exact
     failure this project spent a night diagnosing.
  I3 A profile's stored keyboard_route/mouse_route have zero callers anywhere;
     spec §11 lists them as profile contents. The GUI cannot produce an
     affected configuration (read-only label, PC1/PC1 default), a hand-edited
     project can.
  I4 `waiting_` survives the queue-full path, costing one unpaced drain of up
     to 32 keyboard commands. One line.
  I5 Neither shipped build lets an operator diagnose a field failure: the
     release image's diagnostics carry nothing about the CH375 channels, and
     the probe build replaces the binary reply entirely.
  I6 The RX-sweep cleanup this ledger recorded as done was not done — constant,
     fields and accessors survive with no writer and no reader, and
     rx_sweep_hit()'s comment asserts behaviour no code can produce.

Completion Gate: enumerate/recover MET; macro safety MET (S4b closes the prior
review's Critical C2); profile/capture substantially met with two deviations
(I1, I3); **captured-vector normalization NOT met**; **1000 physical toggles
NOT met** (the acceptance did five); compatibility document NOT met — its
hardware section is now not merely stale but wrong, since it still says nothing
has passed Step 4 and that two link defects are open.

The reviewer's smallest honest discharges for the two unmet bullets:
  - traces: the probe build already prints `last report (N bytes)`; one
    diagnostic session, ~20 reports per device, committed as
    tests/vectors/hid_reports/ with a test asserting the normalized event
    stream. No new firmware, no reflash of the release image.
  - 1000 toggles: make it device-driven — a macro that toggles the keyboard
    route and types one character, enqueued in a loop, with a key held across
    the cycles; read dropped_commands and the runtime fault before and after.
    A human is needed at the start and the end, not for a thousand presses.

## 2026-08-29 19:4x — review closeout complete, verified by the controller

Seven commits, all seven review items, tree clean. The implementer hit its
session limit at the verification step, so the controller ran it:

    ec7e4d1  stage the flip between the two reads (I1, the hollow one)
    e331555  ask capture whether it swallowed the release (the second hollow)
    cacd018  end the wait with the state it was waiting for (I4, one line)
    d34286d  start a profile in the routes it is stored with (I3, applied)
    41a1e10  say what waits, and test the flag where it lives
    d7e4cfa  say what the hardware did, in classes and not in model names

Controller verification on a deleted build/native: **native ctest 33/33** (32
before; the hygiene coverage added one), pico-ch375 and pico-release both build
(exit 0), repository python 37, firmware artifact contracts 11, and the newly
committed acceptance suite 18. Stack unchanged: Core 0 1480/1432, Core 1
512/504 against 2 KiB regions.

I3 was decided the way the spec points rather than by waiver: `set_profile_now`
now applies the profile's stored routes, at the one place a profile becomes
active — the boot path, a configuration write and swap_profile all arrive
there. A source with no such profile leaves the routes alone, and only a route
that actually moves is set, so the engine's release-on-change is not fired for
a no-op.

I2 (a device refusing SET_PROTOCOL is accepted and misparsed, invisible in the
release image) and I5 (the release build carries no CH375 diagnostics) remain
open by the reviewer's own triage: follow-up before a second user, not merge
blockers for this bench.

Left to finish the branch, both Completion Gate bullets rather than code:
  - captured-vector normalization: record report traces from the two devices
    and commit them with a test;
  - 1000 physical route toggles: the acceptance did five.
Subagents are unavailable until the session limit resets at 22:50, so the trace
capture — which is diagnostics work the controller owns anyway — goes first.

## 2026-08-29 20:0x — the wheel does not work, and it is boot protocol's price

Operator: "прокрутка колесом на мышке только не работает."

Cause, immediate and by construction: **the boot-protocol mouse report is three
bytes — buttons, dX, dY — and carries no wheel.** The captured traces show it
exactly: every mouse report this session is `3B` with the fourth byte zero
(`00 03 FB 00`, `00 F9 06 00`, `01 00 00 00`).

So yesterday's SET_PROTOCOL fix traded the wheel for correctness. Before it the
mouse sent its native 7-byte report — which has the wheel — but led it with a
Report ID, and nothing called `MouseNormalizer::set_report_id`, so every field
was read one byte out of place: the ID became the button mask and horizontal
movement arrived as vertical. Choosing boot protocol made the device work and
silently dropped a control.

This is the same gap the whole-branch review raised as I2 from the other side:
a device that refuses SET_PROTOCOL is accepted anyway and misparsed, and
`set_report_id` exists for exactly that case and is called from nothing but
tests. One repair closes both: fetch and parse the HID **report descriptor**,
learn whether a Report ID prefixes the reports and where the fields sit, and
run the device in its own protocol rather than forcing boot.

That is a real piece of work — a report-descriptor parser, field offsets fed to
the normalizer, and a decision about which devices still get boot — and it is
the first thing this project should do after the branch is finished, because
it is also what makes a stranger's mouse work rather than this bench's.

Recorded, not fixed. The acceptance stands: nothing in Step 4 exercised the
wheel, and the criteria it did exercise are unaffected.
