# SDD ledger — plan: docs/superpowers/plans/2026-09-03-pio-usb-hub-v1-implementation.md

## Task 1 — Preserve the baseline and establish the isolated migration branch (2026-09-04)

Spec: `docs/superpowers/specs/2026-09-02-pio-usb-hub-v1-design.md`

### Baseline commit and branch point

The task brief and the plan's Global Constraints both name `af49b2f` as the
commit to branch from. That commit is recorded here as the baseline:

```
$ git show -s --format="%H %ci %s" af49b2f
af49b2fea652848fdf363242d7910193c11ddb1d 2026-09-02 17:30:40 +0300 Flash the release build and close the failing test
```

HEAD on this branch is **not** `af49b2f` but `73b3f14`. This was approved in
the dispatch for this task: `73b3f14` is `af49b2f` plus exactly one docs-only
commit that carried the migration's own plan and spec onto this branch (they
had been committed onto `feature/shared-clipboard` by accident and were
otherwise unreadable here). Verified before touching anything else:

```
$ git log --oneline af49b2f..HEAD
73b3f14 Bring the PIO USB hub design and plan onto their own branch

$ git diff --stat af49b2f..HEAD
 .../2026-09-03-pio-usb-hub-v1-implementation.md    | 348 +++++++++++++++++++++
 .../specs/2026-09-02-pio-usb-hub-v1-design.md      | 308 ++++++++++++++++++
 2 files changed, 656 insertions(+)
```

Only the plan and spec documents changed between the baseline and HEAD; no
firmware, configurator, or test source differs. `af49b2f` is the baseline
commit for every later comparison in this migration; `73b3f14` is the branch
point actually in use, for the reason above. History was not rewritten.

### Branch state

`feature/pio-usb-host-hub-v1` already existed and was already checked out
before this task started, per the dispatch's decision #1. It was not created
and no branch switch was performed.

```
$ git branch -vv
  feature/generic-keyboard-report-protocol 571dd4b Plan the shared clipboard, milestone one
* feature/pio-usb-host-hub-v1              73b3f14 Bring the PIO USB hub design and plan onto their own branch
+ feature/recovered-mouse-switch           ea97f53 (...) docs: plan Duo Input MVP implementation
  feature/shared-clipboard                 bd59c49 Keep the clipboard spikes that mapped Windows paste behavior
  main                                     24494d9 Plan generic keyboard report protocol support
```

The CH375 branch (`main`, and every ancestor of `af49b2f`) is untouched by
this task: nothing was committed to it, and no file under it was edited.

### Tracked tree was clean before starting

```
$ git rev-parse HEAD
73b3f145f30bc102a060bdede439931f0eedce55

$ git status --short --branch
## feature/pio-usb-host-hub-v1
```

No output besides the branch line — no modified, staged, or untracked
tracked-path entries. (The worktree does carry untracked scratch files under
`configurator/tests/clipboard/spike_*` per the pre-existing git status shown
at session start; those are untracked files outside this task's scope and are
left alone. They do not affect "tracked files are dirty" — there are none.)

### Known-good CH375 release stays recoverable

The shipped CH375 release artifacts are untouched and still present, built
from a commit that predates this branch entirely:

```
$ cat dist/release/0.1.0-rc1/RELEASE-NOTES.md | head -4
# Duo Input 0.1.0-rc1
- Commit: `b7a97c59a5fa9850e4f5468cea277cbbb1c262f1` on `feature/duo-input-foundation`
- Built: 2026-08-30 13:56:34 +03:00
- Working tree: clean
```

```
$ cat dist/release/0.1.0-rc1/SHA256SUMS.txt
8163da9d6e71cef286d4e90c3ac4678db3a93e1eee0f15da8f934cd2eb4fe0c7  compatibility-matrix.md
f9c0bbaadf491d34f32885bdd1bed4a1e30a2bde575095e78fa1b10c1ca26267  DuoInput-Setup-0.1.0-rc1-x64.exe
78ed70e22afc0d048235b25e1c4456782673d00f481352acfe1e2b193e115dbc  duo-input-u1-0.1.0-rc1.uf2
00a2386f0f3e5e923467971c685ca5045e2681ac6bfe06c046d86f1fca377a33  duo-input-u2-0.1.0-rc1.uf2
5069a0b6845a1dc335e4a23098bf0c5ed9615cb9e1593addbd0f3d2f0a33d0a2  quick-start-ru.md
3506e19f5bff85ec7ebe91ae5d0c0d1089aa631f8df704fac3192908065278e2  third-party-licenses.md
6bcbf6e2df95d3b28c3f0a568cf93e6e0bc2627edbcaf17e2604ab6a07d907a3  uf2-update-ru.md
```

These are the known-good pre-migration U1/U2 hashes. Recovery, should this
migration need to be abandoned, is: check out `main` (or tag
`duo-input-v0.1.0-rc1`) and reflash from `dist/release/0.1.0-rc1/`. These
hashes are **not** the same UF2s measured below — that dist release was built
from `b7a97c5` with the packaging script (`tools/build_release.ps1`), while
the hashes below come from a plain `pico-release` preset build at this task's
own baseline (`af49b2f`/`73b3f14`). Both are legitimate CH375 builds; they are
recorded separately and neither should be confused for the other.

### Spec status changed

`docs/superpowers/specs/2026-09-02-pio-usb-hub-v1-design.md` header changed
from `design agreed in conversation, awaiting document review` to
`Approved for implementation`, and now names the approved plan:

```
**Date:** 2026-09-02
**Status:** Approved for implementation
**Approved plan:** `docs/superpowers/plans/2026-09-03-pio-usb-hub-v1-implementation.md`
**Target branch after approval:** `feature/pio-usb-host-hub-v1`
```

### Native build and test suite (host, `native` preset)

Build directory `build/native` was reused (already configured; its cache
already recorded the MSVC `cl.exe` paths and the vendored ninja at
`.superpowers/runtime-venv/Scripts/ninja.exe`). No reconfigure was needed or
performed.

```
$ cmake --build --preset native
ninja: no work to do.
```

```
$ ctest --preset native
100% tests passed, 0 tests failed out of 36
Total Test time (real) =   0.82 sec
```

**Baseline native test count: 36/36 passed.**

### Firmware build (`pico-release` preset) and artifact hashes

Build directory `build/pico-release` was reused (already configured; its
cache already recorded `PICO_SDK_PATH=C:/Users/Valentyn/pico-sdk`,
`arm-none-eabi-gcc` from `C:/ProgramData/chocolatey/lib/gcc-arm-embedded/...`,
and the same vendored ninja). `PICO_SDK_PATH` was exported for this shell
before building, and `arm-none-eabi-gcc`'s directory
(`C:\ProgramData\chocolatey\bin`) was prepended to `PATH`, per the dispatch's
environment facts.

```
$ export PICO_SDK_PATH="C:/Users/Valentyn/pico-sdk"
$ cmake --build --preset pico-release
[... 196 build steps, both duo_u1_main.elf and duo_u2_endpoint.elf linked ...]

$ cmake --build --preset pico-release   # re-run to confirm idempotence
ninja: no work to do.
```

Pico SDK revision actually used for this build:

```
$ (cd C:/Users/Valentyn/pico-sdk && git rev-parse HEAD && git describe --tags)
95ea6acad131124694cda1c162c52cd30e0aece0
2.1.0
```

This is **Pico SDK 2.1.0, revision 95ea6ac** — the same revision the existing
CH375 `pico-release` build used. The plan for this migration pins **2.3.0**
for the new PIO-USB build only (Global Constraints, Tech Stack line); that
difference is deliberate and is why this task recorded the exact revision
rather than just the branch tag.

UF2 artifact hashes, from the freshly built images:

```
$ sha256sum build/pico-release/firmware/u1_main/duo_u1_main.uf2 build/pico-release/firmware/u2_endpoint/duo_u2_endpoint.uf2
2b866ea7b90e6de88e1325b9ac88a1dc41f819bf61dcbf9cbb1f0328c02771d4  build/pico-release/firmware/u1_main/duo_u1_main.uf2
4c0673e1e1cfce2c74cd2f7a018b0f0c284d6a78fb1ebc318aee2abfc19223ae  build/pico-release/firmware/u2_endpoint/duo_u2_endpoint.uf2
```

| Board | UF2 | SHA-256 |
|---|---|---|
| U1 | `build/pico-release/firmware/u1_main/duo_u1_main.uf2` | `2b866ea7b90e6de88e1325b9ac88a1dc41f819bf61dcbf9cbb1f0328c02771d4` **— superseded, see "Correction" below; do not use as a comparison baseline** |
| U2 | `build/pico-release/firmware/u2_endpoint/duo_u2_endpoint.uf2` | `4c0673e1e1cfce2c74cd2f7a018b0f0c284d6a78fb1ebc318aee2abfc19223ae` — still current |

Firmware artifact contract test:

```
$ .venv/Scripts/python.exe -m pytest tests/build/test_firmware_artifacts.py -q
13 passed in 0.20s
```

**Baseline firmware artifact test count: 13/13 passed.** No U1 board was
flashed — the plan's Global Constraints forbid flashing U1 without explicit
user approval of the exact UF2, and this task's brief does not ask for a
flash.

### Commit

```
$ git add docs/superpowers && git commit -m "Record PIO USB migration baseline"
```

### Summary table

| Check | Command | Result |
|---|---|---|
| Baseline commit | `git rev-parse af49b2f` | `af49b2fea652848fdf363242d7910193c11ddb1d` |
| Branch point in use | `git rev-parse HEAD` | `73b3f145f30bc102a060bdede439931f0eedce55` (baseline + 1 docs commit, approved) |
| Tree clean before start | `git status --short --branch` | clean (branch line only) |
| Branch exists, checked out | `git branch -vv` | `* feature/pio-usb-host-hub-v1  73b3f14 ...` |
| Native build | `cmake --build --preset native` | `ninja: no work to do` (already current) |
| Native tests | `ctest --preset native` | 36/36 passed |
| Pico SDK revision | `git -C C:/Users/Valentyn/pico-sdk rev-parse HEAD` | `95ea6acad131124694cda1c162c52cd30e0aece0` (2.1.0) |
| Firmware build | `cmake --build --preset pico-release` | built, then `ninja: no work to do` on re-run |
| U1 UF2 hash | `sha256sum .../duo_u1_main.uf2` | `2b866ea7b90e6de88e1325b9ac88a1dc41f819bf61dcbf9cbb1f0328c02771d4` — **superseded, see "Correction" below** |
| U2 UF2 hash | `sha256sum .../duo_u2_endpoint.uf2` | `4c0673e1e1cfce2c74cd2f7a018b0f0c284d6a78fb1ebc318aee2abfc19223ae` |
| Firmware artifact contract | `pytest tests/build/test_firmware_artifacts.py -q` | 13/13 passed |
| Known-good CH375 release | `dist/release/0.1.0-rc1/SHA256SUMS.txt` | present, untouched, from commit `b7a97c5` |

No claim in this record is unmeasured; every row above was produced by a
command run in this session.

## Correction (Task 5 fix round, 2026-09-04): the U1 baseline hash

Task 5's own review turned up that the U1 UF2 hash carried by this record
since Task 1, `2b866ea7b90e6de88e1325b9ac88a1dc41f819bf61dcbf9cbb1f0328c02771d4`,
does not match a from-clean-source build measured during that review, and
that Task 5's own first-draft figure (`b5bb529d...`, since superseded in
`.superpowers/sdd/2026-09-03-pio-usb-hub-v1-implementation/task-5-report.md`)
did not either. This section is the correction: what was actually
established, what was not, and the rule this record now sets for every
later comparison.

### What was verified

A clean build of commit `c948a4b` (`Cover Ch375SourceAdapter::identity()
against a real DescriptorSetup` — the commit immediately before Task 5's own
changes, and the CH375 source this whole migration branches from at that
point) was made in an isolated detached worktree with no other build
directory reused:

```
C:\Users\Valentyn\AppData\Local\Temp\duo-task5-c948-e7afe62292ed40bb99b2486288ba2d1f
$ git rev-parse HEAD
c948a4b0ffb420e09d73709149b7406b6b13072c
$ git status --short
(no output)

$ cmd.exe /d /c 'call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x64 && set "PICO_SDK_PATH=C:\Users\Valentyn\pico-sdk" && set "SOURCE_DATE_EPOCH=1788545287" && cmake -S . -B build\baseline-ch375-v2 -G Ninja -DCMAKE_BUILD_TYPE=MinSizeRel -DDUO_PICO_FIRMWARE=ON -DPICO_BOARD=waveshare_rp2040_zero -DDUO_INPUT_BACKEND=CH375 -DCMAKE_MAKE_PROGRAM=C:\Users\Valentyn\Documents\Codex\2026-08-01\new-chat\work\duo-input-mvp\.superpowers\runtime-venv\Scripts\ninja.exe'
$ cmd.exe /d /c 'call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x64 && set "PICO_SDK_PATH=C:\Users\Valentyn\pico-sdk" && set "SOURCE_DATE_EPOCH=1788545287" && cmake --build build\baseline-ch375-v2 --parallel'
[230/230] Linking CXX executable firmware\u2_endpoint\duo_u2_endpoint.elf
```

`SOURCE_DATE_EPOCH=1788545287` is `c948a4b`'s own commit timestamp
(2026-09-04 21:08:07 +0300, confirmed by `git show -s --format=%ci
c948a4b`), pinned explicitly rather than left to the wall clock, per
`cmake/source_date.cmake`'s own rule. Pico SDK revision `95ea6acad131...`
(2.1.0), the same one Task 1 used; `arm-none-eabi-gcc 10.3.1`; CMake `4.4.3`;
Ninja `1.13.0`; MSVC host tools `19.51.36252` from this same `vcvarsall.bat`.

Independently re-verified in this correction directly against the artifacts
already on disk (not retyped from a report table):

```
$ sha256sum build/pico-release/firmware/u1_main/duo_u1_main.uf2 build/pico-release/firmware/u1_main/duo_u1_main.bin build/pico-release/firmware/u2_endpoint/duo_u2_endpoint.uf2 build/pico-release/firmware/u2_endpoint/duo_u2_endpoint.bin
26cc0f4982075a5e5a3f7d16fb7d6a1c3c8c085dcf5f8c5de282fdd792a50689  build/pico-release/firmware/u1_main/duo_u1_main.uf2
79dff4550e9b7e58b2b799d22d2ba387c5d46736cf9eded26ee3ecbf0e1942e8  build/pico-release/firmware/u1_main/duo_u1_main.bin
4c0673e1e1cfce2c74cd2f7a018b0f0c284d6a78fb1ebc318aee2abfc19223ae  build/pico-release/firmware/u2_endpoint/duo_u2_endpoint.uf2
8022e001f99e2b153eca98f7b6b4ddf6a992da5ee7cce31f389c5a9c5d5676b8  build/pico-release/firmware/u2_endpoint/duo_u2_endpoint.bin

$ arm-none-eabi-objcopy --strip-debug build/pico-release/firmware/u1_main/duo_u1_main.elf /tmp/u1-stripped.elf
$ arm-none-eabi-objcopy --strip-debug build/pico-release/firmware/u2_endpoint/duo_u2_endpoint.elf /tmp/u2-stripped.elf
$ sha256sum /tmp/u1-stripped.elf /tmp/u2-stripped.elf
0f42d76a3daf199311299b79b35575defbbe9dff38beddeb6cb94b91cf659858  u1-stripped.elf
629183b5bac1f07922a56be31068996e7a8caa654c4520a50d7933b776610fc6  u2-stripped.elf

$ sha256sum C:\Users\Valentyn\AppData\Local\Temp\duo-task5-c948-e7afe62292ed40bb99b2486288ba2d1f\build\baseline-ch375-v2\firmware\u1_main\duo_u1_main.uf2
26cc0f4982075a5e5a3f7d16fb7d6a1c3c8c085dcf5f8c5de282fdd792a50689  ...\baseline-ch375-v2\...\duo_u1_main.uf2
```

The last line confirms the isolated `c948a4b` worktree's own build produces
the same U1 UF2 hash as `build/pico-release` in this working tree at commit
`0e015fc` (Task 5, fixed). Task 5's fix round additionally compared both
builds' `arm-none-eabi-nm -S --defined-only` output (every symbol, type,
size and address — 1109/1109 matching for U1, 421/421 for U2, zero deltas)
and both builds' `arm-none-eabi-objdump -drwC` and `-s` output after
stripping debug info (byte-for-byte identical instruction streams and
section contents for both boards); see
`.superpowers/sdd/2026-09-03-pio-usb-hub-v1-implementation/task-5-report.md`,
"Fix round 1/5", for the full comparison. **`26cc0f4982075a5e5a3f7d16fb7d6a1c3c8c085dcf5f8c5de282fdd792a50689`
is the verified, clean-build, reproducible U1 UF2 hash for commit `c948a4b`
and for this branch's CH375 build as it stands after Task 5's fix
(`0e015fc`).** U2 did not move: `4c0673e1e1cfce2c74cd2f7a018b0f0c284d6a78fb1ebc318aee2abfc19223ae`
is the same value Task 1 recorded, confirmed above from the same
independent re-hash, and no U2 source file has changed at any point in this
migration.

| Board | Artifact | SHA-256 (clean `c948a4b`, `build\baseline-ch375-v2`) |
|---|---|---|
| U1 | UF2 | `26cc0f4982075a5e5a3f7d16fb7d6a1c3c8c085dcf5f8c5de282fdd792a50689` |
| U1 | BIN | `79dff4550e9b7e58b2b799d22d2ba387c5d46736cf9eded26ee3ecbf0e1942e8` |
| U1 | ELF, stripped (`objcopy --strip-debug`) | `0f42d76a3daf199311299b79b35575defbbe9dff38beddeb6cb94b91cf659858` |
| U2 | UF2 | `4c0673e1e1cfce2c74cd2f7a018b0f0c284d6a78fb1ebc318aee2abfc19223ae` |
| U2 | BIN | `8022e001f99e2b153eca98f7b6b4ddf6a992da5ee7cce31f389c5a9c5d5676b8` |
| U2 | ELF, stripped (`objcopy --strip-debug`) | `629183b5bac1f07922a56be31068996e7a8caa654c4520a50d7933b776610fc6` |

The full (non-stripped) ELF hashes are **not** listed as canonical: they
differ between the two build directories (different absolute worktree
paths embedded in DWARF debug info) even though the stripped ELFs, and
everything derived from them (UF2, BIN), are identical. Compare stripped
ELFs or the UF2/BIN, never the raw `.elf`, when the two builds being
compared do not share a build directory path.

### What was not established, and is recorded as unknown rather than guessed

This correction did **not** perform an isolated clean rebuild of `af49b2f`
or `73b3f14` — Task 1's own baseline commit and branch point, the commit
`2b866ea7...` was actually recorded against. Only `c948a4b`, a later commit,
was rebuilt clean. Whether `2b866ea7...` would reproduce from a genuinely
fresh, isolated build of `af49b2f`/`73b3f14` itself was **not tested** and
is **not known** either way. Do not read the rest of this section as proof
that `2b866ea7...` was numerically wrong for its own commit — that specific
question was not answered here.

Two things, independent of that open question, were established and are
both, on their own, sufficient reason `2b866ea7...` cannot be compared
directly against `26cc0f49...` or used as a stand-in for it:

1. **Real source changes.** Tasks 3 and 4 changed files the CH375 U1 image
   actually compiles, between `af49b2f`/`73b3f14` and `c948a4b`:

   ```
   $ git diff --stat af49b2f c948a4b -- firmware/u1_main
    firmware/u1_main/CMakeLists.txt                  |   3 +-
    firmware/u1_main/ch375/report_descriptor.cpp     | 790 +----------------------
    firmware/u1_main/ch375/report_descriptor.hpp     | 131 +---
    firmware/u1_main/input/ch375_source_adapter.cpp  |  71 ++
    firmware/u1_main/input/ch375_source_adapter.hpp  |  41 ++
    firmware/u1_main/input/hid/report_descriptor.cpp | 789 ++++++++++++++++++++++
    firmware/u1_main/input/hid/report_descriptor.hpp | 122 ++++
    firmware/u1_main/input/keyboard_normalizer.cpp   |  12 +-
    firmware/u1_main/input/keyboard_normalizer.hpp   |   6 +-
    firmware/u1_main/input/mouse_normalizer.cpp      |   2 +-
    firmware/u1_main/input/mouse_normalizer.hpp      |   6 +-
    firmware/u1_main/input/pipeline.cpp              |  43 +-
    firmware/u1_main/input/pipeline.hpp              |  30 +-
    firmware/u1_main/input/source.hpp                | 113 ++++
    firmware/u1_main/main.cpp                        |  30 +-
    15 files changed, 1228 insertions(+), 961 deletions(-)
   ```

   `main.cpp`, both normalizers, `input/pipeline.*` and the HID report
   descriptor parser (moved from `ch375/` to `input/hid/`, Task 3) are all
   part of the CH375 build's own source list. A hash difference between a
   build of `af49b2f`/`73b3f14` and a build of `c948a4b` is expected on this
   basis alone, with no build-directory defect required to explain it.

2. **Different embedded build date.** `af49b2f` and `73b3f14` are dated
   2026-09-02; `c948a4b` is dated 2026-09-04 — two days apart. Absent
   `SOURCE_DATE_EPOCH`, `cmake/source_date.cmake` stamps
   `PICO_PROGRAM_BUILD_DATE` from the HEAD commit's own timestamp (Task 1's
   record itself does not show `SOURCE_DATE_EPOCH` being set), so the two
   builds embed different date strings regardless of any other change.

What genuinely is a documented irregularity, independent of both points
above, is Task 1's own account of how `2b866ea7...` was measured:

> Build directory `build/pico-release` was reused (already configured; its
> cache already recorded `PICO_SDK_PATH=C:/Users/Valentyn/pico-sdk`,
> `arm-none-eabi-gcc` from `C:/ProgramData/chocolatey/lib/gcc-arm-embedded/...`,
> and the same vendored ninja).

— quoted verbatim from this record's own Task 1 section, above. A reused,
already-configured build directory is not what a canonical baseline
measurement should come from, whether or not it actually produced a wrong
number in this instance: Ninja's incremental build is only as correct as
its dependency graph, and nothing in that Task 1 session verified the
directory's history (which branch/commit it was last configured or built
against, whether any file had been touched outside of Ninja's tracking).
The safe, checkable state is a build directory that has never held any
other commit's objects. `build/pico-release` in the current working tree,
and `build/pico-pio-usb-release`/`build/pico-pio-usb-debug`, have all seen
multiple commits and multiple backends across this migration by now and are
in exactly the same position — reused, not fresh — which is why the rule
below exists.

### The rule this record sets, effective immediately

**Image comparisons - any comparison between two U1 or U2 builds meant to
answer "did the image change" - are made between clean builds, each in its
own fresh build directory that has never held any other commit's or any
other backend's build state, never against or from a directory that was
incrementally reused.** A `git clone`/`git archive`/detached-worktree
checkout into an empty directory, configured and built once, is what
"clean" means here. This is what Task 5's fix round did for `c948a4b`
above, and what produced a number that, unlike `2b866ea7...` or the
original `b5bb529d...`, is now verified two independent ways (bit-for-bit
UF2/BIN/stripped-ELF match, and a full symbol-and-disassembly comparison)
against a second, separately-built copy of the same commit.

Task 14's hardware gate, and any other later task that compares a U1 or U2
image against "what it was before," must build both sides of that
comparison this way. `26cc0f4982075a5e5a3f7d16fb7d6a1c3c8c085dcf5f8c5de282fdd792a50689`
(U1) and `4c0673e1e1cfce2c74cd2f7a018b0f0c284d6a78fb1ebc318aee2abfc19223ae`
(U2) are the CH375 baseline this migration now compares against; the
Task 1 hashes at the top of this record are superseded and must not be
used for that comparison.

## Reference-first rebuild, Task 1 Step 7 — hardware gate FAILED (2026-09-06)

Plan: `docs/superpowers/plans/2026-09-06-pio-usb-reference-first-rebuild.md`.
Gate image: `duo_u1_reference.uf2`, 102400 bytes, SHA-256
`b6e722f1f9efb1e9f79f2182be401d7c71f09342753ae769910aad47542cb2c6`,
built from clean tree at `ff6e391`.

### The previous session's conclusion was wrong, and how that was settled

The prior session reported "the problem is in our almost-upstream integrated
build" from an A/B in which the reference image was judged without a U1 power
cycle and the upstream standalone was judged after one. Two measurements
retired that conclusion:

1. **Static.** `build/task1-fix4-upstream-sde/.../host_hid_to_device_cdc` and
   the reference image differ in exactly one thing: the program-name string in
   `binary_info` (`host_hid_to_device_cdc` → `duo_u1_reference`, `.rodata`
   `0x5ec` → `0x5e4`). Both disassemble to 22658 lines with no differing
   instruction, and the printable-string sets differ only by that name.
2. **Dynamic.** The same reference image produced 0 bytes of CDC output in 30 s
   before a U1 power cycle and 29139 bytes / 1687 lines after one.

**Rule this sets:** a U1 power cycle is a required step of every hardware gate,
performed after flashing and before any judgement about the image. Silence in
CDC without it is not evidence about the firmware. The upstream example has no
`pico_enable_stdio_usb`, so the 1200-baud BOOTSEL reset does not work on it;
BOOTSEL needs BOOT held across a power cycle.

### What the gate actually measured

All rows below are after a U1 power cycle, hub `1A86:8091` (WCH, 4 ports).
The hub, both receivers, mouse and keyboard were verified working when the same
hub was plugged directly into PC1.

| Configuration | Result |
| --- | --- |
| hub + both receivers | only `3434:d030` mounts (Interface0 Mouse, Interface1 None, Interface2 Keyboard); 1687–1943 mouse report lines |
| hub + `3554:FA09` alone (Aula "2.4G Wireless Receiver") | 0 bytes in 25 s |
| replug of any device, port open | no `umount`, no `mount`; a replugged device is left unpowered |
| the same hub and devices directly on PC1 | everything enumerates and works |

Task 1 Step 7 requires hub enumeration, keyboard **and** mouse mount, keyboard
characters, wheel, simultaneous use and re-enumeration after each replug. Only
mouse mount and mouse movement/buttons were obtained. **The gate fails, and per
the plan's own rule the sequence stops at Task 1.** No later task may be built
on this image.

### Hypotheses tested and retired

Each was a single-variable diagnostic image, never committed; the tree was
restored to `ff6e391` after each build and the golden artifact rebuilt to its
recorded hash.

| Hypothesis | Image | Result |
| --- | --- | --- |
| HID instance slots exhausted (`CFG_TUH_HID 4` vs production's 8) | `8f7206a759d7c2f98dc0e98c0fb50be45a1a5fe2f418df8e7bfd3c9dcb2073ab` | identical behaviour; retired |
| config descriptor exceeds `CFG_TUH_ENUMERATION_BUFSIZE 256` | `019441d24c8ef98c49d9ce2402e76f36f377b302440f80245118841898e7f164` | identical behaviour; retired |
| enumeration never reaches HID interface opening | `e1f2cf26767d4c80320578a77d747d5961b907a16b2d50821ebc7db6e5de4175` (device-level `tuh_mount_cb`) | confirmed: no non-hub device mounts. Note `tuh_mount_cb` is **not** called for hubs (`usbh.c:1760`), so its silence says nothing about the hub |

### Root cause, from TinyUSB's own host log

Instrument: `d17d2f91808f0c10c4431f61afedf4555b22d2acf57380cc984a2dec8bdb538d`
— `CFG_TUSB_DEBUG 2`, `CFG_TUH_LOG_LEVEL 2`, `CFG_TUD_LOG_LEVEL 0`, host-core
log buffered in a 64 KB RAM ring and drained from core 0 only after the CDC host
connects, so the boot enumeration survives. `CFG_TUSB_DEBUG 3` does not compile:
`hid_host.c:413,448` log `hidh_interface_t` members that do not exist.

The hub enumerates normally at address 5 and powers all four ports. It then
reports port changes **for both ports**, one after the other:

```
  Hub Status Change = 0x10          <- port 4, the receiver that works
  ...
  Set Address = 1
  [1:1] Control data:
    0000:  12 01 10 01 00 00 00 08 34 34 30 D0 ...    <- bMaxPacketSize0 = 8
  ...
  Hub Status Change = 0x02          <- port 1, the receiver that fails
  HUB Set Feature: PORT_RESET, addr = 5 port = 1
  [1:] USBH Device Attach
  [1:0] Control data:
    0000:  12 01 00 02 00 00 00 40                    <- bMaxPacketSize0 = 64
  Set Address = 2
  on EP 00 with 8 bytes: OK
  [1:2] Open EP0 with Size = 64
  Get Device Descriptor
  [1:2] Get Descriptor: 80 06 00 01 00 00 12 00
  on EP 00 with 0 bytes: FAILED
  [1:2] Control FAILED, xferred_bytes = 0
  Enumeration attempt 1 ... 2 ... 3
  on EP 82 with 0 bytes: FAILED     <- the working device's IN transfers fail too
  Queue EP 81 with 1 bytes ... OK   <- three attempts, then silent give-up
```

**The defect:** on the pinned Pico-PIO-USB / TinyUSB revisions, a full-speed
device behind the hub whose EP0 max packet size is 64 fails every control
transfer issued to its new address, while a device behind the same hub with an
8-byte EP0 enumerates and runs. `SET_ADDRESS` itself succeeds; the first
transaction at the new address does not. During the retries, interrupt transfers
belonging to the already-mounted device fail as well, so the fault is not
confined to the device being enumerated. After three attempts TinyUSB abandons
the device without a callback, which is why the upstream example prints nothing.

This is in the pinned dependency stack, not in Duo Input code, and it reproduces
on a byte-for-byte copy of the upstream example. The reference-first plan
assumed the upstream example is a working hub baseline on this hardware; that
assumption is now measured false.

Full host log: captured 2026-09-06 17:03, 434 lines, 14063 bytes.

## The enumeration defect is fixable in TinyUSB, and one gap remains (2026-09-06)

With permission to modify the pinned dependencies, the failure recorded above
was traced to a specific place in TinyUSB's enumeration and fixed there. The
measurement is unambiguous, because the instrumented build counts SETUP
transactions per device address at the bus level.

### The measurement that names the failure

Diagnostic build `cdf31764ea8635f49dfd9cf5dff4e6029ff5b49cf3afe4bd6a31e56231da23a3`
counts SETUP outcomes inside `pio_usb_host.c` and records, for each failure, the
receive phase and the token bytes actually transmitted:

```
SETUP addr=0 ok=6  fail=0
SETUP addr=1 ok=12 fail=0     <- Keychron receiver, EP0 = 8
SETUP addr=5 ok=19 fail=0     <- hub 1A86:8091, EP0 = 64
SETUP addr=2 ok=0  fail=12 last=0x00 NONE phase=NO-RX-START token=02 a8
```

`token = 02 a8` is a correctly formed token: address 2, endpoint 0, CRC5 `0x15`
(`0x15 << 3 = 0xa8`). `phase = NO-RX-START` means the receiver never saw even
the start of a reply. So the host emits a valid SETUP to address 2 and nothing
on the bus answers, while the same device answered every request at address 0
and its `SET_ADDRESS` status stage completed.

Two candidate explanations were retired without touching the bench:

- **CRC5.** The library's `calc_usb_crc5` was compared against the USB
  specification algorithm for all 16 addresses across three endpoint numbers.
  No mismatch.
- **SETUP packet length.** `prepare_tx_data` uses
  `pio_usb_ll_get_transaction_len`, which is `min(ep->size, remaining)`, so a
  64-byte EP0 still emits an 8-byte SETUP.

TinyUSB does honour the 2 ms address-recovery time (`usbh.c:1448`, USB 9.2.6.3).

### The fix

`usbh.c` already contains the remedy, written by upstream and disabled:
`ENUM_RESET_2` — "2nd reset before set address" — sits behind `#if 0` with the
comment *"not used by now, but may be needed for some devices !?"*. Its
supporting states `ENUM_HUB_GET_STATUS_2` and `ENUM_HUB_CLEAR_RESET_2` are live
and reachable; only the entry into them is compiled out. The device measured
here is exactly the class that comment describes.

```diff
--- a/src/host/usbh.c
+++ b/src/host/usbh.c
@@ -1411,11 +1411,10 @@ static void process_enumeration(tuh_xfer_t* xfer) {
       TU_ASSERT(tuh_descriptor_get_device(addr0, _usbh_epbuf.ctrl, 8,
-                                          process_enumeration, ENUM_SET_ADDR),);
+                                          process_enumeration, ENUM_RESET_2),);
       break;
     }
 
-#if 0
       case ENUM_RESET_2:
@@ -1423,7 +1422,7 @@ static void process_enumeration(tuh_xfer_t* xfer) {
           hcd_port_reset( _dev0.rhport );
-          tusb_time_delay_ms_api(RESET_DELAY);
+          tusb_time_delay_ms_api(ENUM_RESET_DELAY_MS);
@@ -1437,7 +1436,6 @@ static void process_enumeration(tuh_xfer_t* xfer) {
         TU_ATTR_FALLTHROUGH;
-#endif
 
     case ENUM_SET_ADDR:
```

`RESET_DELAY` no longer exists in this revision; `ENUM_RESET_DELAY_MS` (50 ms,
`usbh.c:1305`) is its replacement. Without that substitution the file does not
compile, which is why the block had rotted unnoticed.

### Measured effect

Same hardware, same hub, same two receivers, one power cycle:

| | before | after |
| --- | --- | --- |
| `SETUP addr=2` | ok=0, fail=12 | **ok=7, fail=0** |
| device at address 2 | never enumerates | full descriptor, configuration descriptor (59 bytes), `Set Configuration`, HID setup requests |
| other devices | unaffected | unaffected, no new failures at any address |

The device that had been invisible is now enumerated. Nothing regressed: every
address shows zero SETUP failures.

### The gap that remains — the gate still fails

Enumeration is fixed; **HID mounting for that receiver is not**. After the fix,
address 2 completes `Set Configuration` and begins per-interface HID setup, but
no `tuh_hid_mount_cb` fires for it and typing produces no characters. A clean
boot capture with no mouse traffic at all (so nothing can be lost from the CDC
FIFO) shows only the Keychron receiver's three interfaces.

One contributing limit is already visible in the log and is separate from the
enumeration defect:

```
[1:2] Interface 1: class = 3 subclass = 1 protocol = 2 is not supported
```

The upstream example sets `CFG_TUH_HID 4`. The Keychron receiver alone consumes
three instances, so a second composite receiver cannot fit. Raising it to 8 (the
production firmware's `2 * CFG_TUH_DEVICE_MAX`) was included in the validation
image `6e3f766617c10aa1cc70e34a8a0688e990701e0c66826b206a8589f7572c29ac` and did
not by itself produce a mount, so the remaining cause is elsewhere in the HID
setup path for that device and is not yet identified.

**Task 1's gate therefore still fails** and the plan stays stopped. What is
established is that the blocker is a defect in the pinned dependency stack with
a located, measured, partially effective fix — not a defect in Duo Input code,
and not the "almost upstream integrated build" the previous session blamed.

Any adoption of this fix must go through a forked dependency and a new pinned
revision in `cmake/pio_usb_toolchain_lock.cmake`. That lock correctly refuses to
build against a hand-edited clone; the diagnostic builds above bypassed it only
through a temporary, reverted `DUO_PIO_USB_DIAGNOSTIC_ALLOW_DIRTY` flag, and no
such build is a release candidate.

## Second defect: an unbounded retry loop in the control IN path (2026-09-06)

With the enumeration fix in place, the same receiver still failed to mount, and
the host log stopped dead at one request:

```
HID Get Report Descriptor
[1:1] Get Descriptor: 81 06 00 22 00 00 4D 00     <- 77 bytes wanted
on EP 00 with 8 bytes: OK                          <- SETUP fine
                                                   <- data stage never completes
```

77 bytes on a 64-byte EP0 is this device's first **multi-packet** control IN.
The device descriptor (18 bytes) and configuration descriptor (59 bytes) each
fit one packet. The Keychron receiver has an 8-byte EP0 and does multi-packet
control transfers constantly, which is why it never showed this.

### What the counters proved

Per-address IN transaction counters inside `usb_in_transaction`
(`2a0040eef4f4237f60a05880507d710075f1000010b2b6682534ae4c718fbef5`):

```
IN addr=1 try=19072 ok=7 toggle=19065 nak=0 busy=0 lastlen=64 lastpid=0x4b
```

`try` climbs by ~1000/s for ever while `ok` stays at 7. Every one of those
attempts receives a valid 64-byte packet with a good CRC whose PID is
`0x4b = DATA1`, while the host — having already accepted the first packet and
flipped its toggle — expects DATA0. The device is re-sending the same packet,
so it never saw our ACK.

The reason nothing recovers is a missing bound. Every other outcome in
`usb_in_transaction` counts failures and gives up after `TRANSACTION_MAX_RETRY`;
the DATA0/1 mismatch branch is empty except for a comment:

```c
} else {
  // DATA0/1 mismatched, 0 for re-try next frame
}
```

So the transfer neither completes nor fails, and TinyUSB waits on it for ever
with nothing to recover from. That is the hang.

### Bounding it (adopted)

```diff
--- a/src/pio_usb_host.c
+++ b/src/pio_usb_host.c
     } else {
-      // DATA0/1 mismatched, 0 for re-try next frame
+      // DATA0/1 mismatched. ... Bound it exactly like the NAK and error paths below.
+      res = -1;
+      if (++ep->failed_count >= TRANSACTION_MAX_RETRY) {
+        pio_usb_ll_transfer_complete(ep, PIO_USB_INTS_ENDPOINT_ERROR_BITS);
+      }
     }
```

Measured effect: `try` fell from 19072-and-climbing to 10, `toggle` stopped at
the retry limit of 3, and the transfer now ends as a real error the stack can
see:

```
on EP 80 with 64 bytes: FAILED
[1:1] Control FAILED, xferred_bytes = 64
```

This converts an unrecoverable hang into a recoverable failure and keeps the
host stack alive. It does **not** make the device mount: TinyUSB does not retry
the report-descriptor fetch, so the HID interface is still not enumerated.

### The turnaround hypothesis, tested and rejected

The device never seeing our ACK points at the handshake timing. The receive
loop skips the inter-packet delay at full speed entirely:

```c
// Since there is also overhead, we only wait 1.5 bit for LS and no wait for FS
if (pp->low_speed) { busy_wait_at_least_cycles(turnaround_in_cycle); }
```

Adding 2 bit times of delay for full speed (USB 7.1.18) was built and flashed
(`c23c6904827433b46c4dc4a4412dd99f324dba934a5167b620bbccaa4d7596cf`) and made
things strictly worse: **the hub stopped enumerating too**, stalling on the very
first device-descriptor request. The specification requires the handshake within
a 2-to-7 bit-time window, not merely after 2 bit times; the implementation
already spends that budget on overhead, so the added delay pushes the ACK past
the upper bound. The change was reverted. Any future work here needs the window
measured, not guessed — a logic analyser on D+/D-, not another build.

### State at the end of this session

Two fixes are established and carried as patches, neither adopted into the
pinned clones:

- `tinyusb-reset2.patch` — enables `ENUM_RESET_2`; turns a device that never
  enumerates into one that enumerates fully.
- `pico-pio-usb-bounded-toggle-retry.patch` — bounds the DATA0/1 mismatch
  retry; turns an unrecoverable hang into a reported error.

Clean candidate carrying both plus `CFG_TUH_HID 8`:
`706d909a07e624669a8ad9f8ba6aa51ce16b4555c75063f32b7e296347408ae7`
(102400 bytes). It is what is currently flashed on U1.

**Task 1's gate still fails.** The Aula receiver `3554:FA09` enumerates but its
HID interfaces do not mount, because the report-descriptor fetch cannot complete
while the device and host disagree about the data toggle. That single unresolved
question — why a full-speed device behind this hub misses the host's ACK on a
64-byte packet — is what a focused bug plan should start from.

## Both receivers work: the toggle resync closes it (2026-09-06)

Continuing with permission to modify the pinned dependencies, two further
defects were found and the hardware now runs both wireless receivers at once.

### Third defect: a failed Report Descriptor abandons a mountable interface

`hid_host.c` fetches the HID Report Descriptor after `SET_IDLE`/`SET_PROTOCOL`.
If that control transfer fails, `process_set_config` returns at its opening
`TU_ASSERT` and the interface is never mounted, never reported, and
`usbh_driver_set_config_complete` is never called.

That descriptor is optional for a boot keyboard or mouse: the boot report layout
is fixed by the specification and `SET_PROTOCOL` has already been sent. The same
function already mounts without it when the descriptor is too large for
`CFG_TUH_ENUMERATION_BUFSIZE`. The fix routes a failed fetch into that same
path, for boot-protocol interfaces only:

```c
if (xfer->result != XFER_RESULT_SUCCESS) {
  if (state == CONFIG_COMPLETE && p_hid->itf_protocol != HID_ITF_PROTOCOL_NONE) {
    config_driver_mount_complete(daddr, idx, NULL, 0);
  }
  return;
}
```

Measured effect: `3554:FA09` mounted for the first time
(`HID Interface0, Protocol = Keyboard` / `Interface1, Protocol = Mouse`) and
delivered one character. Then it went silent again.

### Fourth defect: a toggle mismatch discards every report for ever

The interrupt IN endpoint accepted exactly one report and then stopped: `ok`
frozen at 11 while the mismatch counter climbed by ~1500/s. Host and device
disagreed about the data toggle, and the mismatch branch discards the packet —
so every report the device would ever send was thrown away.

Discarding is right for a stale duplicate on a control transfer. It is wrong for
an interrupt IN carrying input reports: a duplicated report is harmless, a
permanently deaf endpoint is not. The fix resyncs to the device on non-control
endpoints and takes the data; control endpoints keep the bounded-retry
behaviour recorded above.

```c
if ((ep->ep_num & 0x7f) != 0) {
  ep->data_id = (receive_pid == USB_PID_DATA1) ? 1 : 0;
  memcpy(ep->app_buf, &pp->usb_rx_buffer[2], receive_len);
  pio_usb_ll_transfer_continue(ep, receive_len);
} else { /* bounded retry, as before */ }
```

### Result on hardware

Image `c0fd6306839828cd1f1532ed2b1d922fee7138023d7ac3a9f9b574069ea98a3b`
(102400 bytes, no diagnostics), hub `1A86:8091`, both receivers, one power cycle:

```
[3434:d030][1] HID Interface0, Protocol = Mouse
[3434:d030][1] HID Interface1, Protocol = None
[3434:d030][1] HID Interface2, Protocol = Keyboard
[3554:fa09][2] HID Interface3, Protocol = Keyboard
[3554:fa09][2] HID Interface4, Protocol = Mouse
```

35 s of simultaneous use: **3463 mouse reports** (addresses stable, `L` and `R`
button states observed) and **85 keyboard characters** of coherent typed text
(`ffddgsgssd123456777889812345678901qwertyqwerty...`). Both devices work at the
same time, through the hub, on the PIO host.

### What the wheel column means

Wheel stays zero in every capture. That is the reference example working as
designed, not a defect: TinyUSB puts these interfaces in **boot protocol**, and
the boot mouse report is three bytes — buttons, X, Y, with no wheel field. The
example prints a fourth byte the device never sends. Wheel, side buttons and
anything else beyond boot layout require report-descriptor parsing, which is
what the production firmware does and what Task 3 of the plan restores.

### The change set

Two patches against the pinned clones, carried outside the tree:

- `tinyusb-enum-and-bootmount.patch` — enables `ENUM_RESET_2` (second port reset
  before `SET_ADDRESS`, with `RESET_DELAY` corrected to `ENUM_RESET_DELAY_MS`),
  and mounts a boot-protocol interface when the Report Descriptor fetch fails.
- `pico-pio-usb-toggle-and-turnaround.patch` — bounds the control-endpoint
  toggle retry, resyncs the toggle on non-control endpoints, and applies the
  1 bit-time inter-packet delay before the handshake at full speed as well as
  low speed.

Plus `CFG_TUH_HID 4 -> 8` in the reference `tusb_config.h`; a single composite
receiver consumes three HID instances, so two of them cannot fit in four.

### What is not yet established

- The 1 bit-time full-speed turnaround has **not** been shown to be necessary on
  its own. It measurably reduced early toggle mismatches (1018 to 6 in the first
  window) but the resync is what actually fixed reporting. A minimisation pass
  should test the candidate without it before adoption.
- Why this device disagrees about the toggle at all is still unexplained. The
  resync makes the endpoint work; it does not identify the cause.
- Adoption still requires forked dependencies and new pinned revisions in
  `cmake/pio_usb_toolchain_lock.cmake`. Every image in this session was built
  with the temporary, reverted `DUO_PIO_USB_DIAGNOSTIC_ALLOW_DIRTY` bypass and
  none is a release candidate.
- Task 1's remaining gate rows are untested: replug/re-enumeration of each
  device, keyboard and mouse under rapid simultaneous load, and detach while
  held.

## Gate rows run on the working image, and where it still fails (2026-09-06)

Image `6fb3d6b0454102c261a3797adadfafde1a575cbaa2911be32ae5d6419e15e086`
(`candidate_v4`, 102400 bytes) refines the toggle resync: discard first, as the
specification says, and resync only after a run of consecutive mismatches.
Resyncing on *every* mismatch (candidate_v3) accepted an endlessly repeated
packet endlessly, flooding the bus at ~670 reports/s and starving the other
endpoints.

### Passing

**Cold start with both receivers, repeated three times.** Five HID interfaces
mount every time and both devices work simultaneously:

| run | mouse reports | keyboard chars | unmounts | errors |
| --- | --- | --- | --- | --- |
| candidate_v3 | 3463 | 85 | 0 | 0 |
| candidate_v3 (repeat) | 3133 | 89 | 0 | 0 |
| candidate_v4 | 3329 | 133 | 0 | 0 |

All mouse reports come from address 1 at a normal rate (~100/s). No stalls, no
stuck state, no lost devices over 30-35 s of continuous simultaneous input.

### Failing: hot replug

Unplugging a receiver produces **no `is unmounted` line**, and plugging it back
produces no mount line and no working device. The host keeps polling endpoints
that are gone, and the bus degrades into ~670 reports/s from both addresses with
the keyboard dead. Identical on candidate_v3 and candidate_v4, so this is not
caused by the resync policy.

This is the same class of symptom as the original pre-fix report: hub port
change events are serviced during start-up but not during operation. What the
fixes repaired is enumeration at power-up; hot-plug event handling is untouched
and remains broken.

**It is bounded and recoverable.** A U1 power cycle restores everything: 2737
mouse reports from address 1 and 116 keyboard characters immediately afterwards,
with no flood. So the failure mode is "replug requires a power cycle", not a
dead rig.

### The wired keyboard takes the whole bus down

Connecting the Aula F75 by its own cable instead of its receiver worked briefly
(characters were received) and then killed enumeration entirely: after it, even
a power cycle produced 0 bytes and the mouse receiver that had been working
stopped enumerating too. Removing the cable restored everything on the next
power cycle (37837 bytes, mouse mounted and reporting).

The leading suspect is power, not protocol: the hub is fed from PC1 VBUS and a
backlit keyboard adds substantial current, and a brownout stops enumeration for
every device at once. This is what the RGB power gate in
`docs/release/pio-usb-hardware-checklist-ru.md` exists to catch. It has not been
measured, and no conclusion should be drawn until it is.

### Gate status

| Task 1 Step 7 row | result |
| --- | --- |
| hub enumeration | pass |
| keyboard and mouse HID mount | pass (5 interfaces, both receivers) |
| keyboard characters | pass |
| mouse movement and buttons | pass (`L`, `R`) |
| mouse wheel | not observable in boot protocol - see above |
| simultaneous use | pass, 30-35 s, three runs |
| re-enumeration after each replug | **fail** |

The gate is not passed. What changed today is that the blocker is now a single
known row with a documented workaround, rather than a device that could not be
enumerated at all.

## Fifth defect: one failed status poll ends hub monitoring for the session (2026-09-06)

Hot replug never worked, and the counters showed why. Per-address IN transaction
counters, sampled every 3 s during a replug:

```
CNT addr=1 try=58365 ok=70   ...  try=76365   <- device endpoint, polled normally
CNT addr=5 try=467   ok=18   ...  try=467     <- hub endpoint, FROZEN
```

The hub's status endpoint was not failing - it was **not being polled at all**.
`try` stopped increasing entirely. The last real transaction on it had
`lastpid = 0x97`, which is neither ACK, NAK, STALL nor a DATA PID: a corrupted
response. That single corrupted response ended hub monitoring permanently, so no
port change could ever be reported again and hot-plug was impossible.

The cause is one line in `hub.c`:

```c
bool hub_xfer_cb(uint8_t dev_addr, uint8_t ep_addr, xfer_result_t result, uint32_t xferred_bytes) {
  TU_VERIFY(result == XFER_RESULT_SUCCESS);   // returns without re-arming
```

Every other branch of that function carefully re-arms the poll with
`hub_edpt_status_xfer()` - including the branch for a zero status change, which
upstream comments as "This shouldn't happen, but it does with some devices". The
failure branch is the only one that does not.

```diff
-  TU_VERIFY(result == XFER_RESULT_SUCCESS);
+  if (result != XFER_RESULT_SUCCESS) {
+    // Giving up here ends hub monitoring for the rest of the session ...
+    return hub_edpt_status_xfer(dev_addr);
+  }
```

Measured effect, same rig, same replug:

| | before | after |
| --- | --- | --- |
| `Hub Status Change` events | 1-2, at boot only | 4 |
| `is unmounted` lines | 0 | 1, then 6 across two replugs |
| hub endpoint counter | frozen at `try=467 ok=18` | `try=3616 ok=39`, still growing |
| device after replug | dead, bus flooded | re-enumerated, 2596 reports |

### Validation of the complete change set

Image `9c2b9dd0bcfd81c5835d3e02b03abcba3f299d8e8848b36d708d06124baaa6be`
(`candidate_v5`, 102400 bytes, no diagnostics).

Cold start, both receivers: 3270 mouse reports from address 1, `L` and `R`
buttons, 59 keyboard characters, 0 unmounts, 0 errors.

Hot replug without any power cycle: six unmount lines (two complete
three-interface detach events) and six matching mount lines, 1179 mouse reports
afterwards at a normal rate, keyboard typing throughout. Both replugs were of
the receiver at address 1. The address 2 receiver was not replugged at all - it
sits where the dongle is physically hard to reach - so that row is untested
rather than failed. The mechanism repaired here is the hub's own status poll,
which is not specific to either downstream device, so the result is expected to
generalise; it has simply not been measured.

### The five fixes

`tinyusb-host-fixes.patch`:
1. `usbh.c` - enable `ENUM_RESET_2` (second port reset before `SET_ADDRESS`),
   with `RESET_DELAY` corrected to `ENUM_RESET_DELAY_MS`.
2. `hid_host.c` - mount a boot-protocol interface when the Report Descriptor
   fetch fails, instead of abandoning it.
3. `hub.c` - re-arm the hub status poll after a failed transfer.

`pico-pio-usb-host-fixes.patch`:
4. `pio_usb_host.c` - bound the control-endpoint toggle-mismatch retry; on
   non-control endpoints discard first and resync only after a run of
   consecutive mismatches.
5. `pio_usb.c` - apply the 1 bit-time inter-packet delay before the handshake at
   full speed as well as low speed.

Plus `CFG_TUH_HID 4 -> 8` in the reference `tusb_config.h`.

### Gate status now

| Task 1 Step 7 row | result |
| --- | --- |
| hub enumeration | pass |
| keyboard and mouse HID mount | pass, 5 interfaces |
| keyboard characters | pass |
| mouse movement and buttons | pass |
| mouse wheel | not observable in boot protocol |
| simultaneous use | pass |
| re-enumeration after replug | pass for the address 1 receiver; address 2 untested (dongle not reachable) |

Still outstanding: the address 2 replug row; whether fix 5 is necessary on its
own; why the device disagrees about the data toggle at all; the wired Aula F75
taking the whole bus down; and adoption through forked dependencies with new
pinned revisions in `cmake/pio_usb_toolchain_lock.cmake`.

## Minimisation: the turnaround change is unnecessary (2026-09-06)

`candidate_v6` (`2216d782c0db377375056d1a026379e222b93ab57e90a1f7b69ac6575c41ee63`)
is `candidate_v5` with fix 5 removed, so `pio_usb.c` is untouched and no bus
timing is altered at all.

| | v5 (with fix 5) | v6 (without) |
| --- | --- | --- |
| interfaces mounted | 5 | 5 |
| mouse reports, cold start | 3270 | 2378 |
| keyboard characters | 59 | 86 |
| unmounts / errors | 0 / 0 | 0 / 0 |
| hot replug | 6 unmount + 6 mount, 1179 reports after | 3 unmount + 3 mount, 2711 reports after |

The report counts differ only by how much the mouse was moved. Nothing regressed,
so **fix 5 is dropped**: its earlier apparent benefit (early toggle mismatches
falling from 1018 to 6) was not needed once the toggle policy and the hub poll
were repaired, and it is not worth changing handshake timing without cause.

### The adopted change set is four fixes

`tinyusb-host-fixes.patch`:
1. `usbh.c` - enable `ENUM_RESET_2`, `RESET_DELAY` -> `ENUM_RESET_DELAY_MS`.
2. `hid_host.c` - mount a boot-protocol interface when the Report Descriptor
   fetch fails.
3. `hub.c` - re-arm the hub status poll after a failed transfer.

`pico-pio-usb-host-fixes.patch`:
4. `pio_usb_host.c` - bound the control-endpoint toggle-mismatch retry; on
   non-control endpoints discard first and resync only after a run of
   consecutive mismatches.

Plus `CFG_TUH_HID 4 -> 8` in the reference `tusb_config.h`. `pio_usb.c`,
`usb_crc.c` and every PIO program are unmodified.

## Adoption: the fixes are now reproducible, not hand-applied (2026-09-06)

Every image before this point was built through a temporary bypass of the
toolchain lock, which is not a state any later task can build on. The four
fixes are now carried as reviewed patches under version control.

**How.** `patches/tinyusb/` and `patches/pico-pio-usb/` hold the diffs.
`tools/bootstrap_pio_usb_toolchain.ps1` fetches the upstream base revision as
before, normalises the clone to LF, applies the patches and commits them with a
fixed author, committer, date (`1788691431 +0000`) and message. Everything a
git commit hashes is therefore fixed, so the resulting revision is the same on
any machine. `cmake/pio_usb_toolchain_lock.cmake` pins those revisions and keeps
both of its original checks unchanged - exact SHA and an empty
`git status --porcelain`. A hand-edited clone still cannot be built against.

| | upstream base | built as |
| --- | --- | --- |
| TinyUSB | `86ad6e56c1700e85f1c5678607a762cfe3aa2f47` | `507766faf14f38a6752401fb4f324cc00cd145dd` |
| Pico-PIO-USB | `3c1eec341a5232640e4c00628b889b641af34b28` | `a2a076497ab6f373ae1c9e98777bf3a0c6f4a40e` |
| Pico SDK | `98a542c1a62fb549ffb5d66a3e5892b06276b670` | unmodified |

**Verified, not assumed.** `.deps/pico-pio-usb` was deleted outright and the
bootstrap re-ran the full clone-and-patch path against GitHub. It produced
`a2a076497ab6f373ae1c9e98777bf3a0c6f4a40e` - the pinned SHA - and the firmware
rebuilt from that fresh clone is
`2216d782c0db377375056d1a026379e222b93ab57e90a1f7b69ac6575c41ee63`, byte for
byte the `candidate_v6` image validated on the bench. The build no longer needs
the diagnostic bypass, and `DUO_PIO_USB_DIAGNOSTIC_ALLOW_DIRTY` is gone from the
tree.

**Two traps found while doing it**, both of the kind that work on one machine
and fail on another:

- The clone is checked out under the global `core.autocrlf=true`, so its files
  had CRLF. A commit made over that tree hashes differently than the same patch
  applied on Linux, and the LF patch would not even apply. The bootstrap now
  normalises before patching. TinyUSB happened to already be LF and gave the
  same SHA either way; Pico-PIO-USB did not.
- The patch files themselves would have been converted to CRLF on checkout,
  which breaks `git apply` against the normalised clone. `.gitattributes` now
  exempts them, and a test asserts that.

**`CFG_TUH_HID 4 -> 8`** is the one place the reference deviates from the
upstream example. `test_pio_usb_reference_contract.py` no longer just carries a
different hash for `tusb_config.h`: it asserts the maintained copy differs from
upstream in exactly the listed lines, so an accidental second edit fails as
loudly as an unreviewed first one.

Verification run: 113 build tests pass, 43 native tests pass, and all three Pico
presets build.
