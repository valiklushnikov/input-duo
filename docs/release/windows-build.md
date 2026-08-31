# Building the Duo Input configurator for Windows

Two commands produce a folder and an installer. Everything else on this page
explains why they are shaped the way they are, and what to check before giving
the result to anyone.

## What you need

| Tool | Version | Where it comes from |
|---|---|---|
| Windows | 10 or 11, x64 | — |
| Python | 3.12, x64 | python.org; `py -3.12 --version` must answer |
| MSVC Build Tools | 14.4x or newer, x64 | Visual Studio Build Tools, "Desktop development with C++" |
| Inno Setup | 6.x | <https://jrsoftware.org/isdl.php>, or `choco install innosetup` |

Everything Python-side is pinned in `configurator/requirements-build.txt` and
installed into a build-only virtual environment by the script. Do not install
build requirements into the development environment: keeping them apart is how
an undeclared dependency gets noticed.

## Build

```powershell
powershell -ExecutionPolicy Bypass -File configurator/packaging/nuitka-build.ps1
ISCC.exe configurator\packaging\duo-input.iss
```

The first command produces `configurator/dist/DuoInput/DuoInput.exe`; the
second produces `configurator/dist/DuoInput-Setup-<version>-x64.exe`.

PowerShell 7 is not required; stock Windows PowerShell 5.1 runs both scripts.

Pass `-Clean` to discard the build environment and previous output, and
`-SkipTests` only while iterating on packaging itself - never for a build
anyone else will run.

## What the script does, and why

1. **Creates a clean Python 3.12 virtual environment.** Separate from the
   development one, so a dependency that was never declared fails here rather
   than on someone else's machine.
2. **Installs the locked requirements.** Exact versions; a build made later
   uses the same Qt as this one.
3. **Checks and compiles the translation catalogues.** `.qm` files are build
   output. Regenerating them means a stale catalogue cannot ship as current,
   and the build stops if any string is untranslated.
4. **Runs the test suite.** A build is not allowed to succeed on a tree whose
   tests fail. An installer that ships a known-broken program is worse than no
   installer.
5. **Compiles with Nuitka `--standalone`.** Never `--onefile`: a onefile build
   unpacks into a temporary directory on every start, hides what actually
   shipped, and slows the first launch. Keeping the Qt DLLs as separate,
   replaceable files is also what the LGPL route needs (see
   `third-party-licenses.md`).
6. **Copies the licence notices** into the folder, where the installer picks
   them up as its licence page.
7. **Runs the dist contract tests** against what was actually produced -
   `configurator/tests/packaging/test_dist.py`. These assert the executable,
   the Qt platform plugin, the serial port library and both catalogues are
   present, and that no `.py`, no test file and no `.duoinput.json` project
   leaked in.

## What the installer does, and why

- **Per-user, no elevation.** The configurator needs no administrator rights
  to run, so it must not demand them to install.
- **No driver installation.** The device is a plain USB HID plus a CDC serial
  port; Windows already has drivers for both.
- **Start Menu shortcut and an uninstall entry**, and an optional desktop icon
  that is unchecked by default.
- **Uninstall removes only what it installed.** `%LOCALAPPDATA%\DuoInput`
  holds the operator's logs and settings, and their `.duoinput.json`
  projects live wherever they chose to save them. Neither is touched.

## Before handing the build to anyone

1. Install it on a clean Windows 10 or 11 x64 machine with **no** Python and
   **no** Qt.
2. Launch it. The interface must come up in Russian on a first run.
3. Connect a U1, or the emulator, and read the configuration back.
4. Save a project, close, reopen it.
5. Uninstall, and confirm the saved project is still where the operator left
   it.

A build that has not been through this on a clean machine has not been
verified: the development machine has Qt on it, and that hides exactly the
kind of missing-DLL failure this step exists to catch.

## Before distributing publicly

Two things are outside this document and block commercial release:

- **Qt for Python licensing.** Record the route - commercial licence or
  reviewed LGPLv3 compliance - in `third-party-licenses.md`. A successful
  build is not legal approval.
- **Code signing.** An unsigned installer triggers SmartScreen and teaches
  operators to click through warnings. Prototype builds for private testing
  may ship unsigned; anything wider needs a trusted Windows signing
  certificate, and the device needs a legitimate USB VID/PID.
