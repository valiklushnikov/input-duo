# Third-party licences in the Duo Input configurator

This file ships inside `dist/DuoInput` and is the licence page of the
installer. It records what the built program actually contains, not what the
project happens to have installed for development.

## What is bundled

| Component | Version | Licence | What it is |
|---|---|---|---|
| Qt for Python (PySide6) | 6.10.1 | LGPLv3 / GPLv3 / commercial | The Python bindings the interface is written against. |
| Qt (Widgets, Core, Gui, SerialPort, and the platform and style plugins) | 6.10.1 | LGPLv3 / GPLv3 / commercial | The toolkit itself, shipped as the DLLs beside `DuoInput.exe`. |
| CPython runtime | 3.12 | PSF License 2.0 | The interpreter Nuitka links into the build. |
| Golos Text | v7 | SIL Open Font License 1.1 | The interface typeface, shipped as `.ttf` files in `duo_input/resources/fonts`. |

Nuitka itself is **not** bundled. It is a build-time compiler
(Apache License 2.0) and none of its code ends up in `dist/DuoInput`.

Inno Setup is likewise **not** bundled. The installer stub it produces is
distributable under the [Inno Setup licence](https://jrsoftware.org/files/is/license.txt),
which permits shipping installers built with it, including commercially.

## Firmware build toolchain

`tools/build_release.ps1 -InputBackend` picks U1's backend for one release;
CH375 is the default until a later task's hardware acceptance decides
otherwise. U2 is not selectable - every release, whichever U1 backend it
carries, ships the U2 built from the CH375-toolchain `pico-release`
directory (Pico SDK 2.1.0). See "U2 is one artefact" in
`docs/release/firmware-build.md` for why: U2's own source is identical
either way, and shipping two different U2 binaries under one version,
distinguished only by which U1 backend a customer happened to pick, is
exactly the ambiguity this file's table exists to avoid.

| Component | Revision pinned | Licence | Status |
|---|---|---|---|
| Raspberry Pi Pico SDK | 2.1.0 (CH375 build and every shipped U2, environment-provided `PICO_SDK_PATH`) / `98a542c1a62fb549ffb5d66a3e5892b06276b670` (PIO USB U1 build only, `.deps/`) | BSD-3-Clause | 2.1.0 is compiled into every shipped U1 and U2 UF2. The `.deps/` revision is compiled into a shipped U1 only for a `-InputBackend PIO_USB` release, and never into U2. |
| TinyUSB | CH375 build and every U2: whatever revision Pico SDK 2.1.0's own `lib/tinyusb` submodule pins / PIO USB U1 build: `86ad6e56c1700e85f1c5678607a762cfe3aa2f47` plus `patches/tinyusb/`, built as `507766faf14f38a6752401fb4f324cc00cd145dd` (`.deps/`, overrides the SDK's submodule via `PICO_TINYUSB_PATH`) | MIT | Device stack compiled into every shipped U1/U2 UF2. The `.deps/` revision's host stack is additionally compiled into a shipped U1 only for a `-InputBackend PIO_USB` release. |
| Pico-PIO-USB | `3c1eec341a5232640e4c00628b889b641af34b28` plus `patches/pico-pio-usb/`, built as `ce67882de7c6e75734087e3181caeb2511f48c46` (`.deps/`) | MIT | Compiled into a shipped U1 UF2 only for a `-InputBackend PIO_USB` release (`tools/build_release.ps1`); never into U2, and not present at all in a default `CH375` release. |

All three are source-form, permissively licensed and statically linked into
firmware, not into the configurator installer this file otherwise describes -
they never touch `dist/DuoInput`. They are documented here anyway because the
UF2 files travel in the same release folder the installer ships in (see
`tools/build_release.ps1`), and "what the release actually contains" should
not stop at the installer's own directory.

See `docs/release/firmware-build.md` for how the PIO USB toolchain revisions
are pinned, cloned and verified, and for the artefact names each backend
produces.

## Qt for Python licensing route

**This is a decision, not a technical detail, and it is not yet made.**

Qt for Python is offered under LGPLv3, GPLv3 or a commercial licence. A build
succeeding says nothing about which of those applies. Before Duo Input is
distributed commercially, one of the two must be true and recorded here:

1. **Commercial Qt licence.** Record the licence holder, the licence number
   and its term.
2. **LGPLv3 compliance, reviewed.** The LGPL route requires, at minimum:
   - the Qt libraries stay as separate, replaceable DLLs beside the
     executable - which is why this build is `--standalone` and never
     `--onefile`;
   - the complete corresponding source of any modified Qt is offered;
   - the licence text and the relinking rights are conveyed to the recipient;
   - the recipient can replace the Qt DLLs with their own build.

   A standalone Nuitka build keeps the DLLs separate, which is necessary but
   **not sufficient**. Someone competent has to confirm the rest.

Prototype builds for private testing are not affected by this. Public or
commercial distribution is blocked until this section names a route and the
person who reviewed it.

## Golos Text

Interface typeface, bundled in `duo_input/resources/fonts`.
Copyright the Golos Text Project Authors.
SIL Open Font License 1.1 — the full text ships beside the fonts as `OFL.txt`.

## Full licence texts

The complete texts travel with the Qt installation used to build:

- LGPLv3: <https://www.gnu.org/licenses/lgpl-3.0.html>
- GPLv3: <https://www.gnu.org/licenses/gpl-3.0.html>
- PSF License 2.0: <https://docs.python.org/3/license.html>
- Apache License 2.0: <https://www.apache.org/licenses/LICENSE-2.0>

## What Duo Input itself does not do

- It makes no network requests and collects no telemetry, so no licence here
  covers a service.
- It installs no driver.
- It requires no administrator rights for ordinary use.
