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


def test_the_executable_carries_a_stamped_icon(dist):
    # ``test_the_application_icon_ships`` above only proves a duo-input.ico
    # file landed somewhere under the dist folder, which happens through
    # ``--include-data-files`` and has nothing to do with the icon a user
    # actually sees. That icon comes from the exe's own PE resource table,
    # stamped in by the independent ``--windows-icon-from-ico`` flag. A build
    # that dropped that flag would still pass the test above while shipping
    # an exe with the generic default icon, so this test reads the resource
    # table directly instead of trusting a file's mere presence.
    import ctypes
    from ctypes import wintypes

    LOAD_LIBRARY_AS_DATAFILE = 0x00000002
    RT_GROUP_ICON = 14
    ERROR_RESOURCE_TYPE_NOT_FOUND = 1813

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LoadLibraryExW.restype = wintypes.HMODULE
    kernel32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    kernel32.FreeLibrary.argtypes = [wintypes.HMODULE]

    exe_path = dist / "DuoInput.exe"
    handle = kernel32.LoadLibraryExW(str(exe_path), None, LOAD_LIBRARY_AS_DATAFILE)
    if not handle:
        raise OSError(f"could not load {exe_path}: error {ctypes.get_last_error()}")

    try:
        found_icon_groups = []

        # ``resource_type`` and ``name`` below arrive as integer ordinals
        # (MAKEINTRESOURCE) for RT_GROUP_ICON, not real string pointers.
        # Declaring them LPCWSTR/LPWSTR would make ctypes auto-decode the
        # raw ordinal as a wide-string pointer on every callback
        # invocation -- an access violation for small values that hangs
        # the process outright (Windows Error Reporting blocks with
        # nothing to dismiss it in a headless run). Keep them as opaque
        # pointer-sized values instead; only their truthiness matters here.
        enum_resource_names_proc = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HMODULE, wintypes.LPARAM, wintypes.LPARAM, wintypes.LPARAM
        )

        def on_resource_found(module, resource_type, name, param):  # noqa: ARG001
            found_icon_groups.append(name)
            return True

        callback = enum_resource_names_proc(on_resource_found)

        kernel32.EnumResourceNamesW.restype = wintypes.BOOL
        kernel32.EnumResourceNamesW.argtypes = [
            wintypes.HMODULE,
            wintypes.LPCWSTR,
            enum_resource_names_proc,
            wintypes.LPARAM,
        ]
        ok = kernel32.EnumResourceNamesW(
            handle, ctypes.cast(RT_GROUP_ICON, wintypes.LPCWSTR), callback, 0
        )
        if not ok:
            error = ctypes.get_last_error()
            if error != ERROR_RESOURCE_TYPE_NOT_FOUND:
                raise OSError(f"EnumResourceNamesW failed: error {error}")
    finally:
        kernel32.FreeLibrary(handle)

    assert found_icon_groups, (
        "DuoInput.exe has no RT_GROUP_ICON resource; "
        "--windows-icon-from-ico was not applied to this build"
    )


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
