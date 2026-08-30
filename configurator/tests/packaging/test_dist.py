"""What a built ``dist/DuoInput`` must and must not contain.

These are contract tests over a real build, not over the build script. They
are skipped when nothing has been built yet, so an ordinary test run stays
fast; ``configurator/packaging/nuitka-build.ps1`` runs them after it builds,
which is where they are meant to bite.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from duo_input import __version__
from duo_input.i18n import LANGUAGES

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DIST = REPOSITORY_ROOT / "configurator" / "dist" / "DuoInput"

#: Anything that would mean source, tests or someone's project got bundled.
FORBIDDEN_SUFFIXES = (".py", ".pyc", ".ts", ".duoinput.json")


def _dist() -> Path:
    """Where the build landed. ``DUO_INPUT_DIST`` overrides it for CI."""
    override = os.environ.get("DUO_INPUT_DIST")
    return Path(override) if override else DEFAULT_DIST


pytestmark = pytest.mark.skipif(
    not _dist().is_dir(),
    reason="no standalone build; run configurator/packaging/nuitka-build.ps1 first",
)


@pytest.fixture(scope="module")
def dist() -> Path:
    return _dist()


@pytest.fixture(scope="module")
def files(dist: Path) -> list[Path]:
    return [path for path in dist.rglob("*") if path.is_file()]


# --------------------------------------------------------------- what ships


def test_the_executable_is_there(dist):
    assert (dist / "DuoInput.exe").is_file()


def test_the_qt_platform_plugin_ships(files):
    names = {path.name.lower() for path in files}

    assert "qwindows.dll" in names


def test_the_serial_port_library_ships(files):
    names = {path.name.lower() for path in files}

    assert any(name.startswith("qt6serialport") for name in names)


def test_the_widget_library_ships(files):
    names = {path.name.lower() for path in files}

    assert any(name.startswith("qt6widgets") for name in names)


@pytest.mark.parametrize("language", LANGUAGES)
def test_both_compiled_catalogues_ship(files, language):
    names = {path.name for path in files}

    assert f"duo_input_{language}.qm" in names


def test_the_chevron_the_interface_draws_ships(files):
    """The combo boxes point at this file; without it they lose their arrow."""
    names = {path.name for path in files}

    assert "chevron-down.png" in names
    assert "chevron-down@2x.png" in names


def test_the_application_icon_ships(files):
    names = {path.name for path in files}

    assert any(name.endswith("duo-input.ico") for name in names)


def test_the_licence_notices_ship(files):
    names = {path.name for path in files}

    assert "third-party-licenses.md" in names


def test_the_executable_carries_the_project_version(dist):
    # The version is read from the built exe rather than trusted from the
    # source tree, so a stale build cannot pass as a current one.
    import subprocess

    output = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-Item '{dist / 'DuoInput.exe'}').VersionInfo.FileVersion",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    assert output.startswith(__version__)


# ----------------------------------------------------------- what never does


def test_no_python_source_or_test_file_is_bundled(files):
    leaked = sorted(
        path.name
        for path in files
        if any(path.name.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES)
    )

    assert leaked == []


def test_no_test_directory_is_bundled(dist):
    assert not list(dist.rglob("tests"))
    assert not list(dist.rglob("test_*"))


def test_no_macro_project_is_bundled(files):
    # The executable is called DuoInput.exe, so this matches the project file
    # extension rather than the word, which the program name also contains.
    leaked = sorted(path.name for path in files if path.name.endswith(".duoinput.json"))

    assert leaked == []


def test_the_build_does_not_carry_the_repository_with_it(files):
    assert not any(".git" in path.parts for path in files)
