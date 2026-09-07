"""Real coverage of ``tools/build_release.ps1``'s own naming/sourcing logic.

Every other file under ``tests/build/`` inspects a *build directory* - a UF2,
an ELF, a ``CMakeCache.txt``. None of them exercises the PowerShell script
that decides which build directory becomes which artefact name, or which
directories the backend/label guard (``tests/build/test_backend_artifacts.py``)
actually runs against. That gap is real: deleting either call to
``Invoke-BackendArtifactGuard``, swapping ``$u1Name`` between the two
``-InputBackend`` branches, or repointing U2's source at the PIO USB
toolchain's own build directory all leave every other suite in this
repository green, because none of them ever runs the script.

``tools/build_release.ps1 -DryRun`` resolves exactly this - which build
directories get guarded, and what U1/U2 are named and sourced from - without
running the protocol check, the native/Python suites, the configurator or the
installer, and writes it as JSON to ``build/release-dry-run.json``. This file
invokes that flag for real, for all three backends, and asserts on the plan it
reports. ``Invoke-BackendArtifactGuard`` records every directory it is
actually called with (see its own ``$Script:GuardCallLog`` line in
``tools/build_release.ps1``) *before* doing anything else, so a deleted call
site is invisible to nothing here - the guarded-directories list would simply
be missing that entry.

Skipped, like every other file in this directory, when the build directories
``-DryRun`` needs are not present locally - it still runs real
``Test-BackendCache``/``Invoke-BackendArtifactGuard`` calls against them, so it
needs what they need.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY_ROOT / "tools" / "build_release.ps1"
PLAN_PATH = REPOSITORY_ROOT / "build" / "release-dry-run.json"

CH375_BUILD_DIR = REPOSITORY_ROOT / "build" / "pico-release"
PIO_USB_BUILD_DIR = REPOSITORY_ROOT / "build" / "pico-pio-usb-release"
REFERENCE_BUILD_DIR = REPOSITORY_ROOT / "build" / "pico-pio-usb-reference-release"


def _project_version() -> str:
    text = (REPOSITORY_ROOT / "configurator/src/duo_input/__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    assert match, "no __version__ in configurator/src/duo_input/__init__.py"
    return match.group(1)


VERSION = _project_version()


def _run_dry_run(input_backend: str) -> dict:
    """Run the real script with -DryRun and return its resolved plan.

    -AllowDirty because this file must work in a working tree with pending
    changes (this very test file is one, the first time it is run); -DryRun
    means no build, no protocol check, no suites, no configurator, no
    installer are touched by running this test.
    """
    if PLAN_PATH.is_file():
        PLAN_PATH.unlink()

    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-Version",
            VERSION,
            "-InputBackend",
            input_backend,
            "-AllowDirty",
            "-DryRun",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, (
        f"build_release.ps1 -InputBackend {input_backend} -DryRun exited "
        f"{completed.returncode}\n--- stdout ---\n{completed.stdout}\n"
        f"--- stderr ---\n{completed.stderr}"
    )
    assert PLAN_PATH.is_file(), (
        f"build_release.ps1 -DryRun did not write {PLAN_PATH}\n"
        f"--- stdout ---\n{completed.stdout}"
    )
    # PowerShell 5.1's Set-Content -Encoding utf8 writes a BOM; utf-8-sig
    # strips it if present and reads plain UTF-8 identically if not.
    return json.loads(PLAN_PATH.read_text(encoding="utf-8-sig"))


pytestmark = pytest.mark.skipif(
    not CH375_BUILD_DIR.is_dir(),
    reason="no build/pico-release; -DryRun still runs Test-BackendCache/"
    "Invoke-BackendArtifactGuard for real and needs it to exist "
    "(run cmake --build --preset pico-release first)",
)


def test_ch375_release_guards_only_the_ch375_directory_and_names_u1_plainly():
    plan = _run_dry_run("CH375")

    assert plan["InputBackend"] == "CH375"
    # Catches a deleted `Invoke-BackendArtifactGuard $ch375BuildDir` call:
    # with it gone, this list is empty instead of containing the directory.
    assert [Path(p) for p in plan["GuardedBuildDirs"]] == [CH375_BUILD_DIR]
    assert plan["U1Source"] == "build/pico-release/firmware/u1_main/duo_u1_main.uf2"
    # Catches swapping $u1Name between the two -InputBackend branches.
    assert plan["U1Name"] == f"duo-input-u1-{VERSION}.uf2"
    assert plan["U2Source"] == "build/pico-release/firmware/u2_endpoint/duo_u2_endpoint.uf2"


@pytest.mark.skipif(
    not PIO_USB_BUILD_DIR.is_dir(),
    reason="no build/pico-pio-usb-release; -DryRun still runs "
    "Test-BackendCache/Invoke-BackendArtifactGuard for real and needs it to "
    "exist (run cmake --build --preset pico-pio-usb-release first)",
)
def test_pio_usb_release_guards_both_directories_and_names_u1_distinctly():
    plan = _run_dry_run("PIO_USB")

    assert plan["InputBackend"] == "PIO_USB"
    # Catches a deleted `Invoke-BackendArtifactGuard $pioBuildDir` call: with
    # it gone, the PIO USB build directory would be built and shipped
    # entirely unguarded while the CH375 one still shows up here.
    assert [Path(p) for p in plan["GuardedBuildDirs"]] == [
        CH375_BUILD_DIR,
        PIO_USB_BUILD_DIR,
    ]
    assert plan["U1Source"] == "build/pico-pio-usb-release/firmware/u1_main/duo_u1_main.uf2"
    # Catches swapping $u1Name between the two -InputBackend branches, and
    # catches the two backends' U1 artefact names colliding.
    assert plan["U1Name"] == f"duo-input-u1-pio-usb-{VERSION}.uf2"
    # The load-bearing assertion for the U2 decision: U2 must come from the
    # CH375-toolchain pico-release directory even on the PIO_USB path, never
    # from build/pico-pio-usb-release/firmware/u2_endpoint/ - catches
    # repointing U2's source at the PIO USB toolchain's own build directory.
    assert plan["U2Source"] == "build/pico-release/firmware/u2_endpoint/duo_u2_endpoint.uf2"


@pytest.mark.skipif(
    not REFERENCE_BUILD_DIR.is_dir(),
    reason="no build/pico-pio-usb-reference-release; -DryRun still runs "
    "Test-BackendCache/Invoke-BackendArtifactGuard for real and needs it to "
    "exist (run cmake --build --preset pico-pio-usb-reference-release first)",
)
def test_reference_release_guards_both_directories_and_names_u1_distinctly():
    plan = _run_dry_run("PIO_USB_REFERENCE")

    assert plan["InputBackend"] == "PIO_USB_REFERENCE"
    assert [Path(p) for p in plan["GuardedBuildDirs"]] == [
        CH375_BUILD_DIR,
        REFERENCE_BUILD_DIR,
    ]
    assert plan["U1Source"] == (
        "build/pico-pio-usb-reference-release/firmware/u1_reference/"
        "duo_u1_reference.uf2"
    )
    assert plan["U1Name"] == f"duo-input-u1-pio-usb-reference-{VERSION}.uf2"
    assert plan["U2Source"] == (
        "build/pico-release/firmware/u2_endpoint/duo_u2_endpoint.uf2"
    )


@pytest.mark.skipif(
    not PIO_USB_BUILD_DIR.is_dir(),
    reason="no build/pico-pio-usb-release",
)
def test_u2_source_is_identical_regardless_of_backend():
    # Restated as its own test, independent of the two above, because this
    # is exactly the property a release must never violate: one U2 artefact,
    # not one per U1 backend choice. See "U2 is one artefact" in
    # docs/release/firmware-build.md.
    ch375_plan = _run_dry_run("CH375")
    pio_plan = _run_dry_run("PIO_USB")

    assert ch375_plan["U2Source"] == pio_plan["U2Source"]
    assert "pico-pio-usb-release" not in pio_plan["U2Source"]
