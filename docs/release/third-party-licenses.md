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
