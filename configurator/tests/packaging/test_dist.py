"""What a built ``dist/DuoInput`` must and must not contain.

These are contract tests over a real build, not over the build script. They
are skipped when nothing has been built yet, so an ordinary test run stays
fast; ``configurator/packaging/nuitka-build.ps1`` runs them after it builds,
which is where they are meant to bite.
"""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
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


# No module-wide ``pytestmark`` here on purpose: this file now also carries the
# macOS bundle contract (below) and the bundle-ID collision guard, which are
# gated on a *different* build artifact (or nothing at all, for the collision
# guard). A blanket module skip keyed on the Windows ``dist/DuoInput`` folder
# would silently skip the macOS/collision tests too on any machine that never
# produces that Windows layout (e.g. every macOS CI runner). Each Windows test
# below still skips exactly as before — the skip just moved from the module
# marker into the ``dist``/``files`` fixtures they all consume.


@pytest.fixture(scope="module")
def dist() -> Path:
    path = _dist()
    if not path.is_dir():
        pytest.skip("no standalone build; run configurator/packaging/nuitka-build.ps1 first")
    return path


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


def test_the_interface_typeface_ships(files, dist):
    """Without these three files the program silently wears Segoe UI instead.

    ``install_fonts`` may not fail loudly - a blank window is worse than the
    wrong typeface - so a build whose font ``--include-data-files`` line broke
    would look healthy and pass every other test here while shipping none of
    the typography the design specifies. The folder matters as much as the
    files: ``install_fonts`` reads ``duo_input/resources/fonts`` beside the
    package and nowhere else, so a face that landed elsewhere is a face that
    is never loaded.
    """
    shipped = {
        path.relative_to(dist).as_posix() for path in files if path.suffix.lower() == ".ttf"
    }

    for weight in ("Regular", "Medium", "SemiBold"):
        assert f"duo_input/resources/fonts/GolosText-{weight}.ttf" in shipped, weight


def test_the_licence_notices_ship(files):
    names = {path.name for path in files}

    assert "third-party-licenses.md" in names
    # SIL OFL 1.1 requires the licence to travel with the faces themselves,
    # so the build copies it beside the program. Shipping the fonts without
    # it is a licence breach, not an oversight in the notices file.
    assert "OFL-GolosText.txt" in names


def test_the_executable_carries_the_project_version(dist):
    # The version is read from the built exe rather than trusted from the
    # source tree, so a stale build cannot pass as a current one.
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


# ------------------------------------------------------------- what it can do


def test_the_built_program_can_actually_open_a_tls_connection(dist: Path):
    """TLS должен работать внутри сборки, а не только в среде разработки.

    Недостающая криптографическая библиотека выглядит у пользователя как "нет
    связи" и никак иначе, поэтому её отсутствие ловится здесь, а не в отзывах.
    """
    executable = dist / "DuoInput.exe"
    result = subprocess.run(
        [str(executable), "--self-check-tls"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert "tls: ok" in result.stdout.lower(), result.stdout + result.stderr


def test_the_packaged_build_can_actually_invoke_a_com_vtable_callback(dist: Path):
    executable = dist / "DuoInput.exe"
    result = subprocess.run(
        [str(executable), "--self-check-files"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "files: ok" in result.stdout.lower(), result.stdout + result.stderr
    assert "callback: addref 2, release 1" in result.stdout.lower(), (
        "the packaged program did not prove that its generated ctypes COM callback "
        f"is invocable: {result.stdout}{result.stderr}"
    )


def test_the_packaged_build_reports_its_descriptor_size(dist: Path):
    executable = dist / "DuoInput.exe"
    result = subprocess.run(
        [str(executable), "--self-check-files"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "descriptor: 592" in result.stdout.lower(), (
        "FILEDESCRIPTORW has the wrong packaged layout; Explorer would read its fields "
        f"at the wrong offsets: {result.stdout}{result.stderr}"
    )


# --------------------------------------------------------- macOS bundle (FP)
#
# Task 18: the Nuitka host bundle must embed the signed File Provider
# `.appex` and its shim dylib. These tests are a contract over a *real*
# `configurator/packaging/nuitka-build-macos.sh` build (a signed
# `dist/DuoInput.app`), gated independently of the Windows tests above via
# the ``mac_dist``/``mac_files`` fixtures, so a machine with neither build
# skips both groups cleanly rather than failing either.

DEFAULT_MAC_DIST = REPOSITORY_ROOT / "configurator" / "dist" / "DuoInput.app"
FILEPROVIDER_APPEX_NAME = "DuoInputFileProvider.appex"
DUOFPPROTO_DYLIB_NAME = "libduofpproto.dylib"

#: The Personal Team this whole feature is signed under (see
#: configurator/fileprovider/project.yml DEVELOPMENT_TEAM and the controller
#: ruling for Task 18: no Developer ID / paid-account requirement).
EXPECTED_TEAM_IDENTIFIER = "4YKVN22BMX"


def _mac_dist() -> Path:
    """Where the signed macOS bundle landed. ``DUO_INPUT_MAC_DIST`` overrides it."""
    override = os.environ.get("DUO_INPUT_MAC_DIST")
    return Path(override) if override else DEFAULT_MAC_DIST


@pytest.fixture(scope="module")
def mac_dist() -> Path:
    path = _mac_dist()
    if not path.is_dir():
        pytest.skip(
            "no signed macOS bundle; run configurator/packaging/nuitka-build-macos.sh first"
        )
    return path


def _codesign_dv(path: Path) -> str:
    """Raw ``codesign -dv`` text (codesign writes its report to stderr)."""
    result = subprocess.run(
        ["codesign", "-dv", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout + result.stderr


def _codesign_field(path: Path, field: str) -> str:
    output = _codesign_dv(path)
    for line in output.splitlines():
        if line.startswith(f"{field}="):
            return line.split("=", 1)[1]
    raise AssertionError(f"{field} not found in `codesign -dv {path}`:\n{output}")


def _codesign_entitlements(path: Path) -> dict:
    """The entitlements plist as a dict, or ``{}`` if the code has none."""
    result = subprocess.run(
        ["codesign", "-d", "--entitlements", ":-", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    if not result.stdout.strip():
        return {}
    return plistlib.loads(result.stdout.encode())


@pytest.fixture(scope="module")
def mac_appex(mac_dist: Path) -> Path:
    appex = mac_dist / "Contents" / "PlugIns" / FILEPROVIDER_APPEX_NAME
    assert appex.is_dir(), f"{appex} missing from the signed bundle"
    return appex


def test_the_appex_is_embedded_in_plugins(mac_dist: Path):
    assert (mac_dist / "Contents" / "PlugIns" / FILEPROVIDER_APPEX_NAME).is_dir()


def test_the_shim_dylib_is_embedded_in_frameworks(mac_dist: Path):
    assert (mac_dist / "Contents" / "Frameworks" / DUOFPPROTO_DYLIB_NAME).is_file()


def test_the_appex_is_signed_by_the_personal_team(mac_appex: Path):
    assert _codesign_field(mac_appex, "TeamIdentifier") == EXPECTED_TEAM_IDENTIFIER


def test_the_outer_bundle_is_signed_by_the_personal_team(mac_dist: Path):
    assert _codesign_field(mac_dist, "TeamIdentifier") == EXPECTED_TEAM_IDENTIFIER


def test_the_host_entitlements_carry_no_application_groups(mac_dist: Path):
    entitlements = _codesign_entitlements(mac_dist)

    assert "com.apple.security.application-groups" not in entitlements


def test_the_host_entitlements_carry_no_restricted_grant(mac_dist: Path):
    """Belt-and-suspenders for the Task 18 hard block: no App Group, named
    Mach service, or temporary-exception entitlement anywhere on the host.
    """
    entitlements = _codesign_entitlements(mac_dist)
    restricted = [
        key
        for key in entitlements
        if "application-groups" in key
        or "temporary-exception" in key
        or "mach-lookup" in key
        or "mach-register" in key
    ]

    assert restricted == [], restricted


def test_the_appex_entitlements_keep_app_sandbox(mac_appex: Path):
    entitlements = _codesign_entitlements(mac_appex)

    assert entitlements.get("com.apple.security.app-sandbox") is True


def test_the_signed_bundle_passes_deep_strict_verification(mac_dist: Path):
    result = subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(mac_dist)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_nested_mach_o_libraries_are_signed_by_the_personal_team(mac_dist: Path):
    """Regression guard: the Nuitka-produced nested Mach-O — the embedded
    Python interpreter and the bundled PySide6 Qt libraries — must carry the
    SAME Team ID as the host executable.

    Nuitka ad-hoc self-signs the bundle it emits, leaving Contents/MacOS/Python
    and the (extensionless) Qt libraries with "TeamIdentifier not set". Under
    the hardened runtime, dyld refuses to map a non-platform library whose team
    differs from the loading process, so such a bundle CRASHES at launch with
    "different Team IDs" — even though `codesign --verify --deep --strict`
    passes. `codesign --verify` therefore does not catch this class of bug;
    only an explicit per-library team check (or an actual launch) does.
    """
    macos_dir = mac_dist / "Contents" / "MacOS"
    # The embedded interpreter, plus at least one bundled Qt library (which
    # ships EXTENSIONLESS under Contents/MacOS/, e.g. QtCore) — the exact files
    # a `*.so`/`*.dylib`-only signing sweep would miss.
    python_lib = macos_dir / "Python"
    qt_libs = sorted(macos_dir.glob("Qt*"))
    assert python_lib.is_file(), f"expected embedded interpreter at {python_lib}"
    assert qt_libs, f"expected bundled Qt libraries under {macos_dir}"

    for lib in [python_lib, qt_libs[0]]:
        team = _codesign_field(lib, "TeamIdentifier")
        assert team == EXPECTED_TEAM_IDENTIFIER, (
            f"{lib.name} is signed by team {team!r}, not {EXPECTED_TEAM_IDENTIFIER!r} "
            "— the bundle will crash at launch under the hardened runtime "
            "(dyld 'different Team IDs')."
        )


# ------------------------------------------------- bundle-ID collision guard
#
# Pure logic, no build required: reads the source project files directly, so
# it runs (and must pass) on every machine regardless of build capability.
# The File Provider feature has accumulated disposable spike projects under
# scratchpad/ during earlier exploration (FileProviderPasteSpike,
# FileProviderIPCSpike, the DuoNuitkaHost lazy-file spike) — this guard makes
# sure none of THEIR bundle identifiers ever leak into the real production
# ids, and that the production host/appex/domain ids never collide with each
# other.

FILEPROVIDER_PROJECT_YML = REPOSITORY_ROOT / "configurator" / "fileprovider" / "project.yml"
FILEPROVIDER_DOMAIN_MODULE = (
    REPOSITORY_ROOT / "configurator" / "src" / "duo_input" / "transfer" / "fileprovider_domain.py"
)
NUITKA_BUILD_MACOS_SCRIPT = REPOSITORY_ROOT / "configurator" / "packaging" / "nuitka-build-macos.sh"

#: Every bundle/domain identifier a disposable spike project under
#: scratchpad/ has ever used (`grep -rn "PRODUCT_BUNDLE_IDENTIFIER\|
#: CFBundleIdentifier\|domainIdentifier" scratchpad/`), plus the two literal
#: examples named in the Task 18 brief. A production id equal to any of
#: these would mean a spike's leftover Info.plist/keychain state could be
#: mistaken for (or fight with) the real thing on a machine that also ran
#: the spikes.
KNOWN_SPIKE_BUNDLE_IDS = frozenset(
    {
        "com.duoinput.DuoNuitka",
        "com.duoinput.DuoNuitka.FileProviderExtension",
        "com.duoinput.DuoEnumSpike",
        "com.duoinput.DuoEnumSpike.FileProviderExtension",
        "com.duoinput.DuoFPX2",
        "com.duoinput.DuoFPX2.FileProviderExtension",
        # Literal examples named in the Task 18 brief.
        "com.duoinput.FileProviderPasteSpike",
        "com.duoinput.FileProviderPasteSpike.FileProviderExtension",
    }
)

KNOWN_SPIKE_DOMAIN_IDS = frozenset({"DuoInputEnumTest", "DuoInputIPCSpikeR8"})


def _xcodegen_target_bundle_ids(project_yml_text: str) -> dict[str, str]:
    """``{target name: PRODUCT_BUNDLE_IDENTIFIER}`` for every xcodegen target.

    ``project.yml`` is plain YAML, but the test environment has no PyYAML
    dependency (and this file should not gain one just to read three
    identifiers) — targets are top-level keys indented exactly two spaces
    under ``targets:``, so splitting on that indentation is exact for this
    file's shape without a YAML parser.
    """
    targets_section = re.search(
        r"^targets:\n(.*?)(?=^\S|\Z)", project_yml_text, re.MULTILINE | re.DOTALL
    )
    assert targets_section, "project.yml has no `targets:` section"

    chunks = re.split(r"^  (\S+):\n", targets_section.group(1), flags=re.MULTILINE)
    ids: dict[str, str] = {}
    # chunks == [preamble, name1, body1, name2, body2, ...]
    for name, body in zip(chunks[1::2], chunks[2::2]):
        identifier = re.search(r"PRODUCT_BUNDLE_IDENTIFIER:\s*(\S+)", body)
        if identifier:
            ids[name] = identifier.group(1)
    return ids


def _domain_identifier(domain_module_text: str) -> str:
    match = re.search(r'^DOMAIN_IDENTIFIER\s*=\s*"([^"]+)"', domain_module_text, re.MULTILINE)
    assert match, "fileprovider_domain.py has no DOMAIN_IDENTIFIER assignment"
    return match.group(1)


def _host_bundle_id(nuitka_build_macos_text: str) -> str:
    match = re.search(r'^HOST_BUNDLE_ID="([^"]+)"', nuitka_build_macos_text, re.MULTILINE)
    assert match, "nuitka-build-macos.sh has no HOST_BUNDLE_ID assignment"
    return match.group(1)


@pytest.fixture(scope="module")
def production_ids() -> dict[str, str]:
    project_yml_text = FILEPROVIDER_PROJECT_YML.read_text()
    domain_module_text = FILEPROVIDER_DOMAIN_MODULE.read_text()
    nuitka_build_macos_text = NUITKA_BUILD_MACOS_SCRIPT.read_text()

    target_ids = _xcodegen_target_bundle_ids(project_yml_text)

    return {
        "host": _host_bundle_id(nuitka_build_macos_text),
        "appex": target_ids["DuoInputFileProvider"],
        "duofpproto": target_ids["duofpproto"],
        "domain": _domain_identifier(domain_module_text),
    }


def test_the_nuitka_build_script_passes_the_host_id_to_signing(production_ids: dict[str, str]):
    text = NUITKA_BUILD_MACOS_SCRIPT.read_text()

    assert "--macos-signed-app-name=" in text
    assert production_ids["host"] in text


def test_production_bundle_and_domain_ids_are_pairwise_unique(production_ids: dict[str, str]):
    host, appex, domain = (
        production_ids["host"],
        production_ids["appex"],
        production_ids["domain"],
    )

    assert len({host, appex, domain}) == 3, production_ids


def test_no_production_id_collides_with_a_known_spike_bundle_id(production_ids: dict[str, str]):
    colliding = {
        name: identifier
        for name, identifier in production_ids.items()
        if identifier in KNOWN_SPIKE_BUNDLE_IDS
    }

    assert colliding == {}


def test_the_domain_id_does_not_collide_with_a_known_spike_domain_id(
    production_ids: dict[str, str],
):
    assert production_ids["domain"] not in KNOWN_SPIKE_DOMAIN_IDS


def test_the_appex_bundle_id_is_nested_under_the_host_prefix(production_ids: dict[str, str]):
    """Not an Apple requirement, but the convention this project follows
    (``project.yml``'s ``bundleIdPrefix: com.duoinput.configurator``) — a
    production appex id that drifted from that prefix would be an easy way
    to accidentally collide with some unrelated future bundle instead.
    """
    assert production_ids["appex"].startswith(production_ids["host"] + ".")
