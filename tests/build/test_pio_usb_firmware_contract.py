"""What the PIO USB dual-role scaffold must actually build, not merely claim to.

Task 5 makes ``firmware/u1_main/pio_usb/backend.cpp`` exist, which is the file
whose absence made every PIO USB configure stop deliberately (see the
``FATAL_ERROR`` in the repository's top-level ``CMakeLists.txt``). Once it
exists, the CH375 build must still be the CH375 build - not a byte of TinyUSB
host code - and the PIO USB build must actually link a TinyUSB host, PIO
USB's own controller driver, and the InputPipeline boundary it will one day
feed, while linking none of CH375's sources.

Two kinds of evidence, in one file because they check one split:

* The CMake inputs (``firmware/u1_main/CMakeLists.txt``,
  ``firmware/u1_main/tusb_config.h``) - read directly, the way a human
  reviewing a diff would, and cheap enough to run on every ``pytest`` pass
  with no build required.
* The linked ELF, for each backend - inspected with the same
  ``dump_usb_descriptors.Elf32`` reader ``tests/build/test_firmware_artifacts.py``
  already uses on the CH375 image, so a misplaced preprocessor guard that
  still looks plausible in ``main.cpp`` cannot pass by looking at source
  text alone. Skipped when the corresponding build does not exist locally.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "tools"))


# ------------------------------------------------------------- CMake inputs

def _u1_cmakelists_text() -> str:
    return (REPOSITORY_ROOT / "firmware" / "u1_main" / "CMakeLists.txt").read_text(encoding="utf-8")


def _tusb_config_text() -> str:
    return (REPOSITORY_ROOT / "firmware" / "u1_main" / "tusb_config.h").read_text(encoding="utf-8")


#: Both the source list and the target_link_libraries() call in
#: firmware/u1_main/CMakeLists.txt branch the same way: CH375 sources/libs in
#: the "if", PIO USB sources/libs in the "else". Reading every such block
#: out of the real file - rather than asserting against a string this test
#: made up itself - is what makes "tinyusb_host is linked" mean something
#: other than "this test contains the word tinyusb_host".
_BACKEND_BLOCK = re.compile(
    r'if\(DUO_INPUT_BACKEND STREQUAL "CH375"\)(?P<ch375>.*?)else\(\)(?P<pio>.*?)endif\(\)',
    re.DOTALL,
)


def _backend_blocks(text: str) -> list[re.Match]:
    matches = list(_BACKEND_BLOCK.finditer(text))
    if not matches:
        raise AssertionError(
            'firmware/u1_main/CMakeLists.txt has no '
            'if(DUO_INPUT_BACKEND STREQUAL "CH375") ... else() ... endif() '
            "block to read either backend's inputs out of - the PIO USB "
            "scaffolding this test checks for does not exist yet."
        )
    return matches


def _ch375_branch_text(text: str) -> str:
    return "\n".join(match.group("ch375") for match in _backend_blocks(text))


def _pio_usb_branch_text(text: str) -> str:
    return "\n".join(match.group("pio") for match in _backend_blocks(text))


def test_pio_usb_branch_links_the_tinyusb_host_stack():
    assert "tinyusb_host" in _pio_usb_branch_text(_u1_cmakelists_text())


def test_pio_usb_branch_links_pico_pio_usb():
    assert "tinyusb_pico_pio_usb" in _pio_usb_branch_text(_u1_cmakelists_text())


def test_pio_usb_branch_compiles_the_backend_sources():
    text = _pio_usb_branch_text(_u1_cmakelists_text())
    assert "pio_usb/backend.cpp" in text
    assert "pio_usb/tinyusb_host_callbacks.cpp" in text


def test_ch375_branch_still_compiles_the_ch375_device():
    # Task 5 must not have moved or removed what the CH375 image links.
    assert "ch375/device.cpp" in _ch375_branch_text(_u1_cmakelists_text())


def test_ch375_sources_are_absent_from_the_pio_usb_branch():
    text = _pio_usb_branch_text(_u1_cmakelists_text())
    assert "ch375/device.cpp" not in text
    assert "ch375_probe.cpp" not in text
    assert "input/ch375_source_adapter.cpp" not in text


def test_pio_usb_sources_are_absent_from_the_ch375_branch():
    assert "pio_usb/backend.cpp" not in _ch375_branch_text(_u1_cmakelists_text())


def test_tusb_config_enables_the_pio_usb_host_only_under_the_pio_backend():
    match = re.search(r"#ifdef DUO_INPUT_BACKEND_PIO_USB(.*?)#endif", _tusb_config_text(), re.DOTALL)
    assert match, "tusb_config.h has no #ifdef DUO_INPUT_BACKEND_PIO_USB block"
    block = match.group(1)
    assert "CFG_TUH_ENABLED" in block
    assert "CFG_TUH_HUB" in block
    assert "CFG_TUH_RPI_PIO_USB" in block
    assert "CFG_TUH_HID" in block
    # RHPort 0 stays the device port outside this block; RHPort 1 is what
    # the PIO backend adds.
    assert "CFG_TUSB_RHPORT1_MODE" in block
    assert "OPT_MODE_HOST" in block


def test_tusb_config_device_rhport_is_unconditional():
    # RHPort 0 as device must not have moved inside the PIO-only block above
    # - it is what both backends share, and CH375 never sees
    # DUO_INPUT_BACKEND_PIO_USB defined at all.
    text = _tusb_config_text()
    before_pio_block = text.split("#ifdef DUO_INPUT_BACKEND_PIO_USB")[0]
    assert "CFG_TUSB_RHPORT0_MODE" in before_pio_block
    assert "OPT_MODE_DEVICE" in before_pio_block


# ------------------------------------------------------------------ the ELFs

def _pio_usb_build_dir() -> Path:
    override = os.environ.get("DUO_INPUT_PIO_USB_BUILD")
    return Path(override) if override else REPOSITORY_ROOT / "build" / "pico-pio-usb-release"


def _ch375_build_dir() -> Path:
    override = os.environ.get("DUO_INPUT_PICO_BUILD")
    return Path(override) if override else REPOSITORY_ROOT / "build" / "pico-release"


def _u1_elf(build_dir: Path) -> Path:
    return build_dir / "firmware" / "u1_main" / "duo_u1_main.elf"


def _symbols(elf: Path):
    from dump_usb_descriptors import Elf32

    return Elf32(elf.read_bytes()).symbols()


_pio_elf = _u1_elf(_pio_usb_build_dir())
_ch375_elf = _u1_elf(_ch375_build_dir())

pio_usb_elf_required = pytest.mark.skipif(
    not _pio_elf.is_file(),
    reason="no PIO USB U1 build; run cmake --build --preset pico-pio-usb-release first",
)
ch375_elf_required = pytest.mark.skipif(
    not _ch375_elf.is_file(),
    reason="no CH375 U1 build; run cmake --build --preset pico-release first",
)


@pio_usb_elf_required
def test_pio_usb_elf_contains_tuh_task():
    symbols = _symbols(_pio_elf)
    assert any("tuh_task" in name for name in symbols), (
        "PIO USB ELF does not contain tuh_task (or its tuh_task_ext, which "
        "TinyUSB's always-inline tuh_task() compiles down to) - Core 1 "
        "cannot be servicing the host stack without it"
    )


@pio_usb_elf_required
def test_pio_usb_elf_contains_tuh_hid_receive_report():
    symbols = _symbols(_pio_elf)
    assert any("tuh_hid_receive_report" in name for name in symbols), (
        "PIO USB ELF does not contain tuh_hid_receive_report - nothing arms "
        "HID report reception, so no report could ever arrive"
    )


@pio_usb_elf_required
def test_pio_usb_elf_contains_input_pipeline_on_event():
    symbols = _symbols(_pio_elf)
    assert any("InputPipeline8on_event" in name for name in symbols), (
        "PIO USB ELF does not contain InputPipeline::on_event - Core 1's "
        "drain loop is not wired to the same pipeline entry point the "
        "CH375 build feeds"
    )


@pio_usb_elf_required
def test_pio_usb_elf_contains_no_ch375_device_tick():
    symbols = _symbols(_pio_elf)
    assert not any("Ch375Device4tick" in name for name in symbols), (
        "PIO USB ELF contains Ch375Device::tick - the CH375 sources leaked "
        "into a build that must never link them"
    )


@ch375_elf_required
def test_ch375_elf_contains_no_tinyusb_host_symbols():
    symbols = _symbols(_ch375_elf)
    assert not any("tuh_task" in name for name in symbols), (
        "CH375 ELF contains a TinyUSB host symbol - the two backends' "
        "sources are no longer isolated from each other"
    )
