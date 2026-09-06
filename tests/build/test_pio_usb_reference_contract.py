"""Build contract for the frozen Pico-PIO-USB golden reference target."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
REFERENCE_BUILD = ROOT / "build" / "pico-pio-usb-reference-release"
REFERENCE_ELF = (
    REFERENCE_BUILD / "firmware" / "u1_reference" / "duo_u1_reference.elf"
)
REFERENCE_UF2 = (
    REFERENCE_BUILD / "firmware" / "u1_reference" / "duo_u1_reference.uf2"
)
REFERENCE_U2_UF2 = (
    REFERENCE_BUILD / "firmware" / "u2_endpoint" / "duo_u2_endpoint.uf2"
)
PIO_USB_U2_UF2 = (
    ROOT
    / "build"
    / "pico-pio-usb-release"
    / "firmware"
    / "u2_endpoint"
    / "duo_u2_endpoint.uf2"
)
PICO_PIO_USB_ROOT = ROOT / ".deps" / "pico-pio-usb"
UPSTREAM_REFERENCE = (
    PICO_PIO_USB_ROOT / "examples" / "host_hid_to_device_cdc"
)
REFERENCE_PRESET = "pico-pio-usb-reference-release"
PIO_USB_PRESET = "pico-pio-usb-release"
FIXED_SOURCE_DATE_EPOCH = "1788691431"
PINNED_PICO_PIO_USB_REVISION = "3c1eec341a5232640e4c00628b889b641af34b28"
REVIEWED_REFERENCE_SHA256 = {
    "main.c": "e8539134690e597be9254ee179f72a2b5cc93becf355e033f994d08955ea8ea1",
    "tusb_config.h": "4ce4ff7a45fc93b5695ddc9375c091995ce19ab078fc32a23d3f4299ee95594c",
    "usb_descriptors.c": "18745d895aff262c81f7a1a7b70887af4670e16100cb4a566782ea8601cf9143",
}

sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "build"))

from reference_build_support import rebuild_reference_u1_artifacts


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reference_symbols(elf: Path) -> dict:
    from dump_usb_descriptors import Elf32

    assert elf.is_file(), (
        f"missing reference ELF: {elf}; build "
        "pico-pio-usb-reference-release first"
    )
    return Elf32(elf.read_bytes()).symbols()


def _configured_make_program() -> str:
    cache = (REFERENCE_BUILD / "CMakeCache.txt").read_text(
        encoding="utf-8", errors="replace"
    )
    match = re.search(r"^CMAKE_MAKE_PROGRAM:[^=]+=(.+)$", cache, re.MULTILINE)
    assert match, (
        f"CMAKE_MAKE_PROGRAM is absent from {REFERENCE_BUILD / 'CMakeCache.txt'}"
    )
    return match.group(1).strip()


def _configure_and_build_u2(preset: str) -> None:
    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = FIXED_SOURCE_DATE_EPOCH
    make_program = _configured_make_program()

    configure = subprocess.run(
        [
            "cmake",
            "--preset",
            preset,
            f"-DCMAKE_MAKE_PROGRAM={make_program}",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert configure.returncode == 0, configure.stdout + configure.stderr

    build = subprocess.run(
        ["cmake", "--build", "--preset", preset, "--target", "duo_u2_endpoint"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr


@pytest.fixture
def fresh_reference_artifacts(tmp_path):
    return rebuild_reference_u1_artifacts(
        root=ROOT,
        build_dir=REFERENCE_BUILD,
        backup_dir=tmp_path / "prior-reference-artifacts",
    )


def test_reference_preset_selects_only_the_reference_backend():
    presets = json.loads((ROOT / "CMakePresets.json").read_text(encoding="utf-8"))
    selected = next(
        preset
        for preset in presets["configurePresets"]
        if preset["name"] == "pico-pio-usb-reference-release"
    )

    assert selected["cacheVariables"]["DUO_INPUT_BACKEND"] == "PIO_USB_REFERENCE"
    assert selected["binaryDir"] == "${sourceDir}/build/pico-pio-usb-reference-release"

    build_selected = next(
        preset
        for preset in presets["buildPresets"]
        if preset["name"] == REFERENCE_PRESET
    )
    assert build_selected["configurePreset"] == REFERENCE_PRESET


def test_invalid_backend_configuration_is_rejected_by_cmake(tmp_path):
    result = subprocess.run(
        [
            "cmake",
            "--preset",
            REFERENCE_PRESET,
            "-B",
            str(tmp_path / "invalid-backend"),
            "-DDUO_INPUT_BACKEND=NOT_A_DUO_BACKEND",
            f"-DCMAKE_MAKE_PROGRAM={_configured_make_program()}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "Unknown DUO_INPUT_BACKEND 'NOT_A_DUO_BACKEND'" in output


def test_failed_reference_rebuild_restores_exact_prior_artifacts(tmp_path):
    build_dir = tmp_path / "build"
    elf = build_dir / "firmware" / "u1_reference" / "duo_u1_reference.elf"
    uf2 = build_dir / "firmware" / "u1_reference" / "duo_u1_reference.uf2"
    elf.parent.mkdir(parents=True)
    elf.write_bytes(b"prior ELF")
    uf2.write_bytes(b"prior UF2")

    def fail_build(*_args, **_kwargs):
        elf.write_bytes(b"partial new ELF")
        return subprocess.CompletedProcess([], 1, "partial stdout", "build failed")

    with pytest.raises(AssertionError, match="build failed"):
        rebuild_reference_u1_artifacts(
            root=ROOT,
            build_dir=build_dir,
            backup_dir=tmp_path / "backup",
            run_build=fail_build,
        )

    assert elf.read_bytes() == b"prior ELF"
    assert uf2.read_bytes() == b"prior UF2"


def test_reference_sources_are_maintained_outside_build_output():
    for name in ("main.c", "tusb_config.h", "usb_descriptors.c"):
        path = ROOT / "firmware" / "u1_reference" / name
        assert path.is_file(), f"missing maintained reference source: {path}"


def test_reference_sources_are_byte_for_byte_the_pinned_upstream_example():
    copies = {
        "main.c": "host_hid_to_device_cdc.c",
        "tusb_config.h": "tusb_config.h",
        "usb_descriptors.c": "usb_descriptors.c",
    }

    revision = subprocess.run(
        ["git", "-C", str(PICO_PIO_USB_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert revision == PINNED_PICO_PIO_USB_REVISION

    checkout_status = subprocess.run(
        [
            "git",
            "-C",
            str(PICO_PIO_USB_ROOT),
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert checkout_status == "", "pinned Pico-PIO-USB checkout is dirty"

    for maintained_name, upstream_name in copies.items():
        maintained = ROOT / "firmware" / "u1_reference" / maintained_name
        upstream = UPSTREAM_REFERENCE / upstream_name
        reviewed_hash = REVIEWED_REFERENCE_SHA256[maintained_name]
        assert _sha256(maintained) == reviewed_hash, (
            f"{maintained} does not have its immutable reviewed SHA-256"
        )
        assert _sha256(upstream) == reviewed_hash, (
            f"pinned blob checkout {upstream} does not have its reviewed SHA-256"
        )

    assert b"tud_cdc_write(" in (ROOT / "firmware" / "u1_reference" / "main.c").read_bytes()


def test_reference_elf_contains_only_the_upstream_host_device_path(
    fresh_reference_artifacts,
):
    freshly_built_elf, freshly_built_uf2 = fresh_reference_artifacts
    assert freshly_built_uf2.is_file()
    symbols = _reference_symbols(freshly_built_elf)

    for required in ("tuh_task", "tuh_hid_receive_report", "tud_task"):
        assert any(required in name for name in symbols), (
            f"{REFERENCE_ELF} contains no {required}"
        )

    # tud_cdc_write() is an always-inline TinyUSB wrapper; the controller's
    # ruling requires exact membership for its out-of-line implementation so
    # tud_cdc_n_write_flush cannot accidentally satisfy this data-write gate.
    assert "tud_cdc_n_write" in symbols, (
        f"{REFERENCE_ELF} contains no exact tud_cdc_n_write symbol"
    )

    for excluded in (
        "Ch375Device4tick",
        "InputPipeline8on_event",
        "PioUsbBackend4task",
    ):
        assert not any(excluded in name for name in symbols), (
            f"{REFERENCE_ELF} unexpectedly contains {excluded}"
        )


def test_reference_u2_matches_the_same_toolchain_pio_usb_u2():
    _configure_and_build_u2(PIO_USB_PRESET)
    _configure_and_build_u2(REFERENCE_PRESET)

    assert REFERENCE_U2_UF2.is_file(), f"missing reference-preset U2: {REFERENCE_U2_UF2}"
    assert PIO_USB_U2_UF2.is_file(), f"missing PIO_USB-preset U2: {PIO_USB_U2_UF2}"
    assert REFERENCE_U2_UF2.read_bytes() == PIO_USB_U2_UF2.read_bytes()
