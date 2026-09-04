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

| Component | Revision pinned | Licence | Status |
|---|---|---|---|
| Raspberry Pi Pico SDK | 2.1.0 (CH375 build, environment-provided) / `98a542c1a62fb549ffb5d66a3e5892b06276b670` (PIO USB build, `.deps/`) | BSD-3-Clause | Compiled into every shipped U1/U2 UF2. |
| TinyUSB | CH375 build: whatever revision Pico SDK 2.1.0's own `lib/tinyusb` submodule pins / PIO USB build: `86ad6e56c1700e85f1c5678607a762cfe3aa2f47` (`.deps/`, overrides the SDK's submodule via `PICO_TINYUSB_PATH`) | MIT | Compiled into every shipped U1/U2 UF2 (device stack; host stack additionally, once the PIO USB backend ships). |
| Pico-PIO-USB | `3c1eec341a5232640e4c00628b889b641af34b28` (`.deps/`) | MIT | Pinned for the PIO USB backend build; **not yet linked into any shipped UF2** - its firmware sources arrive in a later task of the migration (`docs/superpowers/plans/2026-09-03-pio-usb-hub-v1-implementation.md`). This row will move to "shipped" alongside that task, or be removed if the migration lands the PIO USB backend without it. |

All three are source-form, permissively licensed and statically linked into
firmware, not into the configurator installer this file otherwise describes -
they never touch `dist/DuoInput`. They are documented here anyway because the
UF2 files travel in the same release folder the installer ships in (see
`tools/build_release.ps1`), and "what the release actually contains" should
not stop at the installer's own directory.

See `docs/release/firmware-build.md` for how the PIO USB toolchain revisions
are pinned, cloned and verified.

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
