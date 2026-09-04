"""What the PIO USB toolchain lock and its presets must say, verbatim.

Task 1's baseline recorded that the CH375 ``pico-release`` build resolves
``PICO_SDK_PATH`` from the shell environment, to Pico SDK 2.1.0 - not the
2.3.0 line this migration pins for the new PIO USB backend. The two builds
must not blend: CH375 keeps its environment-based path unmodified, and the
PIO USB backend gets its own, independently verified toolchain.

This file does not run a configure - that needs a cloned ``.deps/`` tree,
``ninja`` and the ARM toolchain, none of which pytest should require just to
check that three revisions and two preset names are spelled correctly. It
reads ``cmake/pio_usb_toolchain_lock.cmake`` and ``CMakePresets.json``
directly, the way a human reviewing a diff would.
"""

from __future__ import annotations

import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

#: The exact revisions the task brief names. Copied character for character;
#: a short hash would let the pin silently drift to a different commit later.
PICO_SDK_REVISION = "98a542c1a62fb549ffb5d66a3e5892b06276b670"
TINYUSB_REVISION = "86ad6e56c1700e85f1c5678607a762cfe3aa2f47"
PICO_PIO_USB_REVISION = "3c1eec341a5232640e4c00628b889b641af34b28"

ALL_REVISIONS = (PICO_SDK_REVISION, TINYUSB_REVISION, PICO_PIO_USB_REVISION)


def _lock_text() -> str:
    path = REPOSITORY_ROOT / "cmake" / "pio_usb_toolchain_lock.cmake"
    return path.read_text(encoding="utf-8")


def _presets() -> dict:
    path = REPOSITORY_ROOT / "CMakePresets.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _configure_preset(presets: dict, name: str) -> dict:
    for preset in presets["configurePresets"]:
        if preset["name"] == name:
            return preset
    raise AssertionError(f"no configurePreset named {name!r}")


def _effective_cache_variables(presets: dict, name: str) -> dict:
    """A preset's cacheVariables, with one level of ``inherits`` resolved.

    CMakePresets.json does not merge cacheVariables the way a Python dict
    literal would suggest to someone skimming this file; a child preset's
    values override, rather than replace, its parent's.
    """
    preset = _configure_preset(presets, name)
    resolved: dict = {}
    parent = preset.get("inherits")
    if parent:
        resolved.update(_effective_cache_variables(presets, parent))
    resolved.update(preset.get("cacheVariables", {}))
    return resolved


# --------------------------------------------------------------- revisions


def test_pico_sdk_revision_is_pinned():
    assert PICO_SDK_REVISION in _lock_text()


def test_tinyusb_revision_is_pinned():
    assert TINYUSB_REVISION in _lock_text()


def test_pico_pio_usb_revision_is_pinned():
    assert PICO_PIO_USB_REVISION in _lock_text()


def test_the_three_revisions_are_full_sha1s():
    # A short hash can drift to a different commit as a repository grows;
    # only a full 40-character SHA-1 is unambiguous forever.
    for revision in ALL_REVISIONS:
        assert len(revision) == 40, revision
        int(revision, 16)  # raises ValueError if it is not hex


def test_the_three_revisions_are_distinct():
    assert len(set(ALL_REVISIONS)) == 3


# ------------------------------------------------------------------ presets


def test_the_two_pio_usb_presets_exist():
    names = {preset["name"] for preset in _presets()["configurePresets"]}
    assert {"pico-pio-usb-release", "pico-pio-usb-debug"} <= names


def test_pico_pio_usb_release_selects_the_pio_usb_backend():
    resolved = _effective_cache_variables(_presets(), "pico-pio-usb-release")
    assert resolved["DUO_INPUT_BACKEND"] == "PIO_USB"


def test_pico_pio_usb_debug_also_selects_the_pio_usb_backend():
    resolved = _effective_cache_variables(_presets(), "pico-pio-usb-debug")
    assert resolved["DUO_INPUT_BACKEND"] == "PIO_USB"


def test_pico_pio_usb_presets_still_target_the_waveshare_board():
    # Inheriting from pico-release should carry PICO_BOARD along; the PIO USB
    # backend runs on the same board as CH375, not a different one.
    for name in ("pico-pio-usb-release", "pico-pio-usb-debug"):
        resolved = _effective_cache_variables(_presets(), name)
        assert resolved["PICO_BOARD"] == "waveshare_rp2040_zero"


def test_existing_pico_release_still_selects_ch375():
    resolved = _effective_cache_variables(_presets(), "pico-release")
    assert resolved["DUO_INPUT_BACKEND"] == "CH375"


def test_pico_debug_still_selects_ch375():
    resolved = _effective_cache_variables(_presets(), "pico-debug")
    assert resolved["DUO_INPUT_BACKEND"] == "CH375"


def test_native_preset_declares_no_backend():
    # DUO_INPUT_BACKEND selects a firmware USB host path; it means nothing to
    # the host-compiled native test build and must not appear there.
    resolved = _effective_cache_variables(_presets(), "native")
    assert "DUO_INPUT_BACKEND" not in resolved
