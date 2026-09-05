# Building the Duo Input firmware

Two UF2 images, one per board. The host test build and the firmware build
cannot share a build directory — one cross-compiles for ARM and the other
compiles for this machine — so `CMakeLists.txt` refuses that combination rather
than producing half of each.

## What you need

| Tool | Version used | Where it comes from |
|---|---|---|
| Raspberry Pi Pico SDK | 2.1.0 | `git clone --branch 2.1.0 https://github.com/raspberrypi/pico-sdk` |
| TinyUSB | SDK submodule | `git submodule update --init lib/tinyusb` inside the SDK |
| arm-none-eabi GCC | 10.3-2021.10 | `choco install gcc-arm-embedded` |
| A host C++ compiler | MSVC 14.4x | Visual Studio Build Tools — **required**, see below |
| CMake | 3.21+ | — |
| Ninja | any | — |

### Why a host compiler is needed for a cross build

The SDK builds `picotool` — a host program — to turn an ELF into a UF2. So the
firmware build needs *both* toolchains present: the ARM one for the firmware
and the host one for picotool. On Windows that means running the build from a
shell where `vcvars64.bat` has been sourced. Without it the configure step
fails with `No CMAKE_C_COMPILER could be found` from picotool's own
`CMakeLists.txt`, which is confusing until you know it is not your project
failing.

## Build

```bat
call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
set PICO_SDK_PATH=C:\Users\<you>\pico-sdk
set PICO_TOOLCHAIN_PATH=C:\ProgramData\chocolatey\lib\gcc-arm-embedded\tools\gcc-arm-none-eabi-10.3-2021.10
set PATH=%PICO_TOOLCHAIN_PATH%\bin;%PATH%

cmake --preset pico-release
cmake --build --preset pico-release --parallel
python -m pytest tests/build/test_firmware_artifacts.py -q
```

Output:

- `build/pico-release/firmware/u1_main/duo_u1_main.uf2`
- `build/pico-release/firmware/u2_endpoint/duo_u2_endpoint.uf2`

## The build contract

`tests/build/test_firmware_artifacts.py` reads the UF2 files themselves rather
than a linker map, because a UF2 block states the exact flash address its
payload is written to — which is the thing that actually matters.

It asserts:

- exactly two images are produced, and nothing else;
- each starts at `0x10000000`, the beginning of flash;
- each ends below `0x10100000`.

That last one is not a style rule. U1 keeps its configuration in two 384 KiB
slots starting at 1 MiB. Firmware that grows past the boundary does not fail to
link — it silently begins overwriting the operator's configuration the first
time one is written. The check exists to catch that at build time instead of in
someone's hands.

## Presets

| Preset | What it builds |
|---|---|
| `native` | firmware logic compiled for this machine, with CTest |
| `pico-release` | both UF2 images, MinSizeRel, `DUO_INPUT_BACKEND=CH375` (the current, shipping U1 input path) |
| `pico-debug` | the same CH375 images with symbols, for a debug probe |
| `pico-pio-usb-release` | U1 built against the native Pico-PIO-USB/TinyUSB host, `DUO_INPUT_BACKEND=PIO_USB` - see "The PIO USB backend toolchain" below |
| `pico-pio-usb-debug` | the same PIO USB host build with symbols, for a debug probe |

`DUO_INPUT_BACKEND` selects which USB host path U1 is built with. It accepts
exactly `CH375` or `PIO_USB`; any other value fails configuration rather than
silently defaulting to one of them.

## The PIO USB backend toolchain

The PIO USB backend (a native Pico-PIO-USB/TinyUSB host, replacing the CH375
chips - see `docs/superpowers/specs/2026-09-02-pio-usb-hub-v1-design.md`) is
pinned to its own toolchain, independent of `PICO_SDK_PATH` and everything
else CH375 depends on:

| Dependency | Revision |
|---|---|
| Pico SDK | `98a542c1a62fb549ffb5d66a3e5892b06276b670` |
| TinyUSB | `86ad6e56c1700e85f1c5678607a762cfe3aa2f47` |
| Pico-PIO-USB | `3c1eec341a5232640e4c00628b889b641af34b28` |

These are exact commits, not tags - `cmake/pio_usb_toolchain_lock.cmake` is
the single source of truth for them.

### Bootstrap

```bat
powershell -ExecutionPolicy Bypass -File tools\bootstrap_pio_usb_toolchain.ps1
```

This clones (or, if a directory is present but at the wrong revision, re-clones)
the three dependencies above into `.deps/pico-sdk`, `.deps/tinyusb` and
`.deps/pico-pio-usb`. Each is fetched by its exact commit SHA (GitHub serves
any reachable commit this way) and verified with `git rev-parse HEAD` before
the script reports success. `.deps/` is git-ignored; nothing under it is ever
tracked. Re-running the script is safe - a clone already at the pinned
revision is left alone.

No submodule of any of the three repositories is initialised. Pico SDK's own
TinyUSB submodule is superseded by the separately pinned `.deps/tinyusb`
(via `PICO_TINYUSB_PATH`, below); its other submodules are Pico W wireless
support this board does not have. TinyUSB and Pico-PIO-USB declare no
submodules at all at these revisions.

### Configure and build

```bat
call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
set PICO_TOOLCHAIN_PATH=C:\ProgramData\chocolatey\lib\gcc-arm-embedded\tools\gcc-arm-none-eabi-10.3-2021.10
set PATH=%PICO_TOOLCHAIN_PATH%\bin;%PATH%

cmake --preset pico-pio-usb-release
cmake --build --preset pico-pio-usb-release --parallel
```

Unlike `pico-release`, `PICO_SDK_PATH` must **not** be set for this preset -
`cmake/pio_usb_toolchain_lock.cmake` sets it (and `PICO_TINYUSB_PATH`,
`PICO_PIO_USB_PATH`) itself, forced to the verified `.deps/` clones, so this
build never depends on whatever the CH375 build has that variable pointed at.

If any of the three `.deps/` clones is missing, or has drifted from its
pinned revision (edited by hand, or left over from an earlier `git checkout`),
configuration fails immediately with a message naming the dependency and
telling you to re-run the bootstrap script - it does not fall back to
`main`, and it does not silently build against whatever happens to be there.

The PIO USB backend's own C++ sources (`firmware/u1_main/pio_usb/...`) are
added in a later task of the migration. Until they exist, a
`pico-pio-usb-release` configure is expected to stop with an explicit
"arrives in a later task" error - **after** the three dependencies above have
already been located and verified. That is a different, earlier failure than
a toolchain problem, and the message says which one happened.

### Offline rebuild

Once `.deps/` has been populated by the bootstrap script, `pico-pio-usb-release`
and `pico-pio-usb-debug` configure entirely from local paths - no network
access is required to reconfigure or rebuild from an existing `.deps/` tree.
Only `tools/bootstrap_pio_usb_toolchain.ps1` itself needs network access, and
only for dependencies it has not already fetched at the correct revision.

### Licensing

Pico SDK, TinyUSB and Pico-PIO-USB are all permissively licensed (BSD-3-Clause,
MIT and MIT respectively). See `docs/release/third-party-licenses.md` for what
that means for anything actually shipped.

## Packaging a release

`tools/build_release.ps1` assembles one release folder from these presets,
plus the configurator and installer. It takes `-InputBackend CH375` (the
default) or `-InputBackend PIO_USB`:

```bat
powershell -ExecutionPolicy Bypass -File tools/build_release.ps1 -Version 0.1.0-rc1
powershell -ExecutionPolicy Bypass -File tools/build_release.ps1 -Version 0.1.0-rc1 -InputBackend PIO_USB
```

A `CH375` release builds only `pico-release` and names its U1 image
`duo-input-u1-<version>.uf2`. A `PIO_USB` release additionally builds
`pico-pio-usb-release` and names *that* preset's U1 image
`duo-input-u1-pio-usb-<version>.uf2` - the two names cannot collide in one
output folder, so a release directory that somehow contained both backends'
U1 images would still be unambiguous about which is which.

### U2 is one artefact

U2's own sources (`firmware/u2_endpoint/`) do not depend on
`DUO_INPUT_BACKEND` at all - it is built identically either way. What
differs is the *toolchain*: `pico-release` builds it against Pico SDK 2.1.0
and `pico-pio-usb-release` builds it against the pinned 2.3.0 in `.deps/`,
because that preset points `PICO_SDK_PATH` at the PIO USB toolchain for
*everything* it configures, U2 included (`cmake/pio_usb_toolchain_lock.cmake`).
Task 5 of this migration measured the result: two U2 UF2s with different
hashes at the same commit, from no code difference at all.

That is not a difference a release is allowed to expose. Shipping
`duo-input-u2-<version>.uf2` as two different binaries under one version
number - which one a customer got depending on which preset happened to
build last - is exactly the kind of ambiguity a version number exists to
rule out, and nothing about U2 (no CH375 chips, no PIO pins, no
`DUO_INPUT_BACKEND`) justifies it varying with U1's backend choice.

So `tools/build_release.ps1` always takes U2 from the `pico-release`
(CH375-toolchain, Pico SDK 2.1.0) build, regardless of `-InputBackend`. A
`PIO_USB` release therefore builds *both* presets - `pico-release` for U2 (and
for U1, on the `CH375` path), `pico-pio-usb-release` for U1 only, on the
`PIO_USB` path - and still emits exactly one U2 artefact, byte-identical to
the one a `CH375` release of the same commit emits. `pico-release` remaining
buildable is consequently a precondition for *any* release, not just a
`CH375` one.

### The label guard

Before naming or copying either backend's U1 UF2, `tools/build_release.ps1`
runs `tests/build/test_backend_artifacts.py` scoped to the one build
directory it just built (via `DUO_INPUT_PICO_BUILD`). That file cross-checks
the build directory's declared backend (`CMakeCache.txt`) against what the
linked ELF's symbol table actually contains - a CH375 image must link
`Ch375Device::tick` and must not link TinyUSB's host task or HID-receive
symbols; a PIO USB image is the reverse. A build directory that fails this
check - most plausibly a shared build directory reconfigured by hand, or by
another session, between one release and the next - never reaches the copy
step, so a PIO USB image cannot be shipped labelled CH375, or the reverse.

### What the release notes record

Every release's `RELEASE-NOTES.md` states which backend U1 was built with. A
`PIO_USB` release additionally records all three pinned toolchain revisions
(read from `cmake/pio_usb_toolchain_lock.cmake`, so the notes cannot drift
from what configuration actually verified), and states that U2 came from the
CH375 toolchain regardless.

## The target board

`PICO_BOARD` is `waveshare_rp2040_zero`. It is an RP2040 with 2 MB of flash,
like a Pico, so the flash layout is identical - but the pin header is not, and
it has no plain LED. What a Pico exposes on GP25, this board does not route at
all; its only indicator is an addressable WS2812 on GP16.

That matters more than it sounds. Built for the wrong board, indicator code
compiles happily and drives a pad connected to nothing, which is worse than
having no indicator: a dark LED then looks like a signal rather than an
absence. Link and device state are read over CDC instead.

## Flashing

See `docs/user/uf2-update-ru.md`. Both boards take the same procedure and
different files; the images are not interchangeable.
