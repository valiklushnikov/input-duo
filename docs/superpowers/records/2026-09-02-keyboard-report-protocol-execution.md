# Generic Keyboard Report Protocol — Execution Record

**Plan:** `docs/superpowers/plans/2026-09-02-generic-keyboard-report-protocol.md`
**Branch:** `feature/generic-keyboard-report-protocol`
**Date:** 2026-09-02

## What was done, and what was not

Tasks 1 through 5 are implemented, tested and committed. Task 6's software
gates (steps 1 and 2) are run and recorded below. **Task 6 steps 3 through 6 —
the hardware acceptance — have not been run.** They need an operator at the
bench with the Aula receiver, a wired keyboard, the Trust mouse, the Keychron
M3 and two computers, and they need firmware flashed to the boards. Nothing in
this record should be read as evidence that keystrokes stopped being lost on
real hardware. That question is still open.

## Commits

| SHA | Message |
| --- | --- |
| `44a281b` | Capture keyboard HID report descriptors |
| `293fa9b` | Parse generic keyboard HID report layouts |
| `58c868f` | Cover four unguarded keyboard descriptor refusals |
| `0545253` | Normalize keyboard report protocol layouts |
| `499e194` | Prefer keyboard HID report protocol |
| `a68e5a7` | Route native keyboard HID reports |

The first two were inherited from a previous session; the review of the second
was left unfinished and is `58c868f`.

## Task 6 step 1 — software gates

Run from the VS developer environment:

```powershell
$vs = 'C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\Tools\VsDevCmd.bat'
$command = 'call "' + $vs + '" -arch=x64 -host_arch=x64 >nul && cmake --build build\native -j 8 && ctest --test-dir build\native --output-on-failure'
& cmd.exe /d /s /c $command
```

**Result: 36 of 36 tests passed.**

```powershell
.\.venv\Scripts\python.exe -m pytest configurator/tests tests -q
```

**Result: 1 failed, 899 passed, 4 skipped, 6 subtests passed in 43.35s.**

The one failure is
`configurator/tests/integration/test_real_config_contract.py::test_the_real_device_reports_its_diagnostic_counters`:

```
DeviceRefused: get_diagnostics failed: bad_payload
  - GET_DIAGNOSTICS latency block has the wrong size
```

This is a hardware-in-the-loop test that talks to whatever U1 is plugged in. It
is not a regression from this branch: `git diff 293fa9b..HEAD --name-only`
touches no configurator, protocol or diagnostics file, and the diagnostics
payload format was not changed here. The attached board is still running
firmware from an earlier session and its GET_DIAGNOSTICS reply no longer
matches what the configurator expects. Re-flashing it is a hardware action and
was left for the operator; until the board is re-flashed and this test re-run,
the failure is unexplained by anything in this branch rather than proven
harmless.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/build/test_firmware_artifacts.py -q
```

**Result: 13 passed in 0.30s.**

## Task 6 step 2 — firmware builds and hashes

Both configurations build clean.

| Artifact | Bytes | SHA-256 |
| --- | --- | --- |
| `build\pico-release\firmware\u1_main\duo_u1_main.uf2` | 314880 | `4E0DDF97C1D8F53CE91C2663D426F75F25164CBB25A8D4261BC2DCAFB06B5583` |
| `build\pico-release\firmware\u2_endpoint\duo_u2_endpoint.uf2` | 62976 | `AAA7B062083AF0AC5E75D780138DCE75A2F47DAAD5B3715949F886297D74CD43` |
| `build\pico-ch375\firmware\u1_main\duo_u1_main.uf2` | 324096 | `C88A5C737CF819F97A50CF4B086E6D0271724A3F40B69EA096F7A0CC5E08B736` |

Nothing was flashed.

## What the Aula's descriptor turned out to say

The 77 bytes captured in Task 1
(`tests/vectors/usb_descriptors/aula_f75_keyboard_report.hex`) decode to:

| Field | Bits | Meaning |
| --- | --- | --- |
| modifiers | 0-7 | usages `0xE0`-`0xE7`, one bit each |
| constant | 8-15 | the reserved byte |
| keys | 16-55 | **five** 8-bit array slots over usages `0x00`-`0xFF` |
| vendor | 56-63 | one byte on vendor page `0xFF00` |

Five slots, not six. Boot protocol's report has six, so read at boot offsets
that vendor byte is a sixth key slot — whatever the keyboard puts there arrives
as a keystroke nobody made, and one nothing will ever release, because the
report that clears it looks like the release of something real.

Whether this is *the* cause of the lost keystrokes is exactly what the hardware
acceptance is for. It is a mechanism this branch removes; it is not yet an
observation that the loss stopped.

## Mutation verification

The project's recorded failure mode is a passing test that proves nothing, so
every guard added or touched here was removed in turn and the suite re-run.

| Component | Guards checked | Survivors found | Outcome |
| --- | --- | --- | --- |
| `report_descriptor.cpp` (Task 2, inherited) | 8 | 4 | four tests added in `58c868f` |
| `keyboard_normalizer.cpp` (Task 3) | 14 | 6 | 5 tests added, 2 redundant guards folded into one, 1 dead condition removed |
| `descriptor_setup.cpp` (Task 4) | 7 | 2 | 1 test added, 1 dead assignment removed |
| `pipeline.cpp` (Task 5) | 1 | 0 | the report-ID test fails when the layout is dropped |

Three pieces of code were deleted rather than tested around, because no test
could distinguish them from their absence:

- The keyboard normalizer's `minimum_body_bytes` term. The layout's declared
  length can never be stricter than the reach of its own fields, so the length
  check is computed from the fields alone.
- The `layout_.key_kind == Array` half of the rollover condition. Error slots
  are only counted in the array branch, so a bitmap can never reach six of them.
- The layout reset inside `fallback_keyboard_to_boot`. `begin()` already sets
  the layout to boot's for every attempt, and the only thing that replaces it
  is a descriptor that parsed — which finishes and never reaches the fallback.

## Bench session, 2026-09-02 - what the hardware said

**Status: the polling fix is PROVISIONAL. The operator is testing through the
day; nothing below should be read as a closed result.**

### The feature itself works

Flashed the probe U1 and read `tools\keychron_probe.py COM18`. The Aula row:

```
hid=keyboard ep=1 pkt=8 boot=yes/no setup=2 last=F2
rd=00 err=0 got/want=77/77
klayout=report kkind=1 kbits=8/5@16 kid=no/0 kmin=7
kbd-desc=77/77:05010906A101...0903750895018102C0
```

Boot advertised and **not** selected, layout read from the device's own
descriptor, five slots at bit 16, descriptor complete, bytes identical to the
captured fixture. That is Task 6 step 3 satisfied for this keyboard.

### But it did not stop the loss, and four theories were wrong

The operator still lost letters when typing fast. Each theory was measured and
each was disproved by the measurement, not by argument:

| Theory | Measurement | Verdict |
| --- | --- | --- |
| Five `0x01` values read as "nothing held" (the rollover rule this plan specified as "six") | `kerr[5]=0` - the receiver never sends more than one | wrong |
| Every keystroke doubled by false releases | `down` rose 105, not 204 | wrong |
| Reports refused whole by the normalizer | `ref=0 rej=0` throughout | wrong |
| Publish grace expiring and collapsing presses | `unpaced=0` throughout | wrong |

An arithmetic error of mine belongs here too: `down` exceeding the characters
on screen was read as "lost after the normalizer", when the operator had been
correcting typos and every Backspace is a keystroke that removes a character.
Corrected by a run with a known target string, where `down` matched the
characters exactly.

The operator also had to correct me on the topology: the laptop is attached to
**U1**, so U2 is not in this path at all. Time was spent reading `u2_endpoint`
for nothing.

### The cause

`report_poll_interval_us` derives how often the endpoint carrying the reports
must be sampled - 2 ms, because a key whose whole press falls between two polls
was never here. A composite receiver spends every other token on its service
endpoint, so that interval was halved for the endpoint it was derived for.

Measured with both receivers attached:

| Channel | Tokens/s | To the report endpoint | Interval |
| --- | --- | --- | --- |
| Aula (keyboard) | 521 | 260 | **3.86 ms** |
| Keychron (mouse) | 152 | 76 | **13.2 ms** |

3.86 ms sits inside the band already recorded beside `kFastestReportPollUs`:
this receiver lost two keys in thirty-nine at 8 ms and none in two hundred and
thirty-one at 2 ms. Its own service endpoint had sent **one** report in the
whole session while taking half of every poll.

Fixed in `a5864f1`: the token clock runs at the endpoint's interval divided by
the number of endpoints sharing it. After it, same bench:

| Channel | Tokens/s | To the report endpoint | Interval |
| --- | --- | --- | --- |
| Aula | 1024 | 512 | **1.95 ms** |
| Keychron | 1525 | 763 | **1.31 ms** |

2549 tokens a second across both channels against 673, both devices still
`Ready`. The service endpoint keeps its every-other-token share, so the wedging
the Keychron receiver is known for is unaffected.

The first test written for this passed with the polling loop still using the
old interval - caught by mutation and replaced with one that compares a device
sharing its schedule against one that does not.

### Evidence for the fix, and its limits

| Run | Typed | Lost |
| --- | --- | --- |
| `тест` x13, before the fix | 13 words | one `тст` - a letter inside a word |
| `тест` x11, after | 11 words | none; two spaces between words |
| `привет` x10, after | 69 chars | none |

Letters inside words stopped disappearing, which is the symptom that was
reproducing. Two caveats, stated plainly:

- Repeated words are **not** the sensitive test. Before the fix, `1234567890`
  x6 typed fast was already clean; the losses showed up in live Russian prose.
  No prose run has been done since the fix.
- In the second run two spaces were missing while `down` said both were read
  and `kusb sent` said every state went out. Either there is a further stage of
  loss not yet measured, or a hand typing the same word eleven times sometimes
  misses the space. The counters cannot separate those.

The `0x01`-in-one-report-of-four behaviour of the receiver is unchanged at 28%
before and after. It stopped costing anything because a real state now arrives
between two of them.

## What is still to do

1. Flash the probe U1 (after checking the drive label reads `RPI-RP2`) and read
   `tools\keychron_probe.py COM18`. The Aula row must show VID/PID `3554:FA09`,
   `hid=keyboard`, `boot=yes/no` (advertised, not selected), `klayout=report`,
   `kbits=8/5@16`, `kid=no/0`, `kmin=7`, a complete descriptor length of 77, a
   nonzero hash, zero failed polls and zero dropped commands. Stop if any field
   disagrees.
2. Keyboard acceptance on PC1 through the CH375: repeated fixed groups,
   sustained rapid and ordinary typing, Shift/Ctrl/Alt, repeat, release,
   receiver reconnect, U2 switching and return, and BOTH routing. Then the same
   basic pass and a disconnect recovery with the wired keyboard.
3. Mouse regression: Trust movement, buttons and wheel; Keychron M3 movement,
   wheel, side-button switch to U2 and back, receiver reconnect.
4. Flash the release U1, repeat a shorter pass, and read release diagnostics.
5. Re-run the configurator HIL test against the re-flashed board and record
   whether the GET_DIAGNOSTICS failure above clears.
6. If report protocol does not remove the loss, restore the pre-feature release
   U1 (SHA-256
   `D2072DFF3CF958F6FDC9B43C5278E81F316F7EB529B1B78809E9D37F2518B81B`) and
   record the hypothesis as disproved.

## Limits of this record

- The hardware section above supersedes this: the probe U1 was flashed several
  times and the operator typed through it. The release build was never flashed
  and none of the routing, reconnect or mouse acceptance was run.
- The polling fix is provisional pending a day of use by the operator.
- The 77-byte Aula descriptor is real, captured on hardware in Task 1. The
  five-slot reading of it is decoded from those bytes and asserted in
  `the_captured_aula_keyboard_descriptor_is_used_as_the_aula_declared_it`.
- The NKRO bitmap path has no hardware behind it at all. It is written to the
  HID specification and tested against synthetic descriptors; no NKRO keyboard
  has been through this firmware.
- The one failing test in the Python suite is unexplained rather than shown
  harmless. See step 1 above.
