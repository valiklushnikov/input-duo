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
