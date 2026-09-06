"""Build contract for the frozen Pico-PIO-USB golden reference target."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REFERENCE_BUILD = ROOT / "build" / "pico-pio-usb-reference-release"
REFERENCE_ELF = (
    REFERENCE_BUILD / "firmware" / "u1_reference" / "duo_u1_reference.elf"
)
UPSTREAM_REFERENCE = (
    ROOT / ".deps" / "pico-pio-usb" / "examples" / "host_hid_to_device_cdc"
)

sys.path.insert(0, str(ROOT / "tools"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reference_symbols() -> dict:
    from dump_usb_descriptors import Elf32

    assert REFERENCE_ELF.is_file(), (
        f"missing reference ELF: {REFERENCE_ELF}; build "
        "pico-pio-usb-reference-release first"
    )
    return Elf32(REFERENCE_ELF.read_bytes()).symbols()


def test_reference_preset_selects_only_the_reference_backend():
    presets = json.loads((ROOT / "CMakePresets.json").read_text(encoding="utf-8"))
    selected = next(
        preset
        for preset in presets["configurePresets"]
        if preset["name"] == "pico-pio-usb-reference-release"
    )

    assert selected["cacheVariables"]["DUO_INPUT_BACKEND"] == "PIO_USB_REFERENCE"
    assert selected["binaryDir"] == "${sourceDir}/build/pico-pio-usb-reference-release"


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

    for maintained_name, upstream_name in copies.items():
        maintained = ROOT / "firmware" / "u1_reference" / maintained_name
        upstream = UPSTREAM_REFERENCE / upstream_name
        assert _sha256(maintained) == _sha256(upstream), (
            f"{maintained} is not a byte-for-byte copy of {upstream}"
        )

    assert b"tud_cdc_write(" in (ROOT / "firmware" / "u1_reference" / "main.c").read_bytes()


def test_reference_elf_contains_only_the_upstream_host_device_path():
    symbols = _reference_symbols()

    for required in (
        "tuh_task",
        "tuh_hid_receive_report",
        "tud_task",
        # tud_cdc_write() is an always-inline TinyUSB wrapper; this is its
        # out-of-line implementation in the pinned TinyUSB revision.
        "tud_cdc_n_write",
    ):
        assert any(required in name for name in symbols), (
            f"{REFERENCE_ELF} contains no {required}"
        )

    for excluded in (
        "Ch375Device4tick",
        "InputPipeline8on_event",
        "PioUsbBackend4task",
    ):
        assert not any(excluded in name for name in symbols), (
            f"{REFERENCE_ELF} unexpectedly contains {excluded}"
        )
