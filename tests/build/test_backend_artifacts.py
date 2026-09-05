"""The guard that stops a PIO USB U1 image from being labelled CH375, or back.

Both ``pico-release`` and ``pico-pio-usb-release`` produce a
``firmware/u1_main/duo_u1_main.elf``, and ``tools/build_release.ps1`` decides
which name to give the resulting UF2 (``duo-input-u1-<version>.uf2`` or
``duo-input-u1-pio-usb-<version>.uf2``) from which preset it was asked to
build. That is a naming decision made *before* this file ever runs, so the
thing worth checking is not the name - it is whether the artefact the name
will be attached to actually is the backend its own build directory claims.

Two independent sources have to agree, and this file checks that they do
rather than trusting either alone:

* ``CMakeCache.txt`` in the build directory - what the configure step was
  told to build. A stale build directory reconfigured by hand, or a shared
  worktree with objects left over from another session (a documented hazard
  of this repository - see ``tests/build/test_firmware_artifacts.py`` and the
  environment notes in the Task 12 brief), can drift from what its directory
  name implies.
* The linked ELF's symbol table - what was actually compiled and linked in,
  read with the same ``dump_usb_descriptors.Elf32`` reader
  ``tests/build/test_pio_usb_firmware_contract.py`` already uses for exactly
  this reason: a misplaced preprocessor guard still looks plausible in source
  text, and only the linked image says what Core 1 can actually do.

``tools/build_release.ps1`` runs this file, scoped to the one build directory
it just built, before it copies that directory's UF2 into the release folder
under either name. A build directory that fails this file never reaches the
copy step.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "tools"))

#: Present only in a CH375 image: firmware/u1_main/ch375/device.cpp.
CH375_ONLY_SYMBOL_FRAGMENT = "Ch375Device4tick"

#: Present only in a PIO USB image: TinyUSB's host task and HID receive path,
#: which the CH375 branch of firmware/u1_main/CMakeLists.txt never links.
PIO_USB_ONLY_SYMBOL_FRAGMENTS = ("tuh_task", "tuh_hid_receive_report")

#: Common to both backends (firmware/u1_main/input/pipeline.cpp is compiled
#: unconditionally), so its presence alone proves nothing about which
#: backend an image is - it only rules out a build that links neither.
SHARED_SYMBOL_FRAGMENT = "InputPipeline8on_event"


def _build_dir() -> Path:
    override = os.environ.get("DUO_INPUT_PICO_BUILD")
    return Path(override) if override else REPOSITORY_ROOT / "build" / "pico-release"


def _u1_elf(build_dir: Path) -> Path:
    return build_dir / "firmware" / "u1_main" / "duo_u1_main.elf"


def declared_backend(build_dir: Path) -> str:
    """What CMakeCache.txt in ``build_dir`` says DUO_INPUT_BACKEND is."""
    cache_path = build_dir / "CMakeCache.txt"
    cache = cache_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^DUO_INPUT_BACKEND:STRING=(.+)$", cache, re.MULTILINE)
    assert match, f"DUO_INPUT_BACKEND is absent from {cache_path}"
    return match.group(1).strip()


def linked_symbols(elf: Path) -> dict:
    from dump_usb_descriptors import Elf32

    return Elf32(elf.read_bytes()).symbols()


BUILD_DIR = _build_dir()
U1_ELF = _u1_elf(BUILD_DIR)

pytestmark = pytest.mark.skipif(
    not U1_ELF.is_file(),
    reason="no U1 ELF in this build; configure and build a preset first "
    "(see docs/release/firmware-build.md)",
)


def test_declared_backend_is_one_of_the_two_known_values():
    # cmake/*.cmake refuses any other value at configure time already; this
    # is the assumption every other test in this file is allowed to make.
    assert declared_backend(BUILD_DIR) in ("CH375", "PIO_USB")


def test_a_ch375_declared_build_actually_links_ch375_and_not_pio_usb():
    if declared_backend(BUILD_DIR) != "CH375":
        pytest.skip("this build directory is not configured for CH375")

    symbols = linked_symbols(U1_ELF)

    assert any(SHARED_SYMBOL_FRAGMENT in name for name in symbols), (
        f"{U1_ELF} contains no InputPipeline::on_event at all"
    )
    assert any(CH375_ONLY_SYMBOL_FRAGMENT in name for name in symbols), (
        f"{U1_ELF}'s build directory is configured for CH375 (CMakeCache.txt) "
        f"but its linked image contains no Ch375Device::tick - this image "
        "must not be named or shipped as duo-input-u1-<version>.uf2"
    )
    for fragment in PIO_USB_ONLY_SYMBOL_FRAGMENTS:
        assert not any(fragment in name for name in symbols), (
            f"{U1_ELF}'s build directory is configured for CH375, but its "
            f"linked image contains {fragment} - a PIO USB image would ship "
            "under the CH375 name"
        )


def test_a_pio_usb_declared_build_actually_links_pio_usb_and_not_ch375():
    if declared_backend(BUILD_DIR) != "PIO_USB":
        pytest.skip("this build directory is not configured for PIO_USB")

    symbols = linked_symbols(U1_ELF)

    assert any(SHARED_SYMBOL_FRAGMENT in name for name in symbols), (
        f"{U1_ELF} contains no InputPipeline::on_event at all"
    )
    for fragment in PIO_USB_ONLY_SYMBOL_FRAGMENTS:
        assert any(fragment in name for name in symbols), (
            f"{U1_ELF}'s build directory is configured for PIO_USB "
            f"(CMakeCache.txt) but its linked image contains no {fragment} - "
            "this image must not be named or shipped as "
            "duo-input-u1-pio-usb-<version>.uf2"
        )
    assert not any(CH375_ONLY_SYMBOL_FRAGMENT in name for name in symbols), (
        f"{U1_ELF}'s build directory is configured for PIO_USB, but its "
        f"linked image contains Ch375Device::tick - a CH375 image would "
        "ship under the PIO USB name"
    )
