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
| U1 | `build/pico-release/firmware/u1_main/duo_u1_main.uf2` | `2b866ea7b90e6de88e1325b9ac88a1dc41f819bf61dcbf9cbb1f0328c02771d4` |
| U2 | `build/pico-release/firmware/u2_endpoint/duo_u2_endpoint.uf2` | `4c0673e1e1cfce2c74cd2f7a018b0f0c284d6a78fb1ebc318aee2abfc19223ae` |

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
| U1 UF2 hash | `sha256sum .../duo_u1_main.uf2` | `2b866ea7b90e6de88e1325b9ac88a1dc41f819bf61dcbf9cbb1f0328c02771d4` |
| U2 UF2 hash | `sha256sum .../duo_u2_endpoint.uf2` | `4c0673e1e1cfce2c74cd2f7a018b0f0c284d6a78fb1ebc318aee2abfc19223ae` |
| Firmware artifact contract | `pytest tests/build/test_firmware_artifacts.py -q` | 13/13 passed |
| Known-good CH375 release | `dist/release/0.1.0-rc1/SHA256SUMS.txt` | present, untouched, from commit `b7a97c5` |

No claim in this record is unmeasured; every row above was produced by a
command run in this session.
