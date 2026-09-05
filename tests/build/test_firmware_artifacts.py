"""What a configured Pico build must produce, and where it must fit.

The flash layout is not advisory. U1 keeps its configuration in two 384 KiB
slots that begin at 1 MiB, so firmware that grows past ``0x10100000`` does not
fail to build - it silently starts overwriting the operator's configuration the
first time it is written. This file is the check that catches that at build
time rather than in the field.

The UF2 files themselves are read rather than a linker map: a UF2 block states
the exact address its payload is written to, which is the thing that actually
matters.
"""

from __future__ import annotations

import os
import re
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

#: Where a Pico maps its flash.
XIP_BASE = 0x10000000

#: Firmware owns the first 1024 KiB. Config A starts here.
FIRMWARE_LIMIT = 0x10100000

#: Exactly these two, and nothing else.
EXPECTED_ARTIFACTS = ("duo_u1_main.uf2", "duo_u2_endpoint.uf2")

_UF2_MAGIC_START0 = 0x0A324655
_UF2_MAGIC_START1 = 0x9E5D5157
_UF2_MAGIC_END = 0x0AB16F30
_UF2_BLOCK = 512
_UF2_HEADER = struct.Struct("<8I")


def _build_dir() -> Path:
    override = os.environ.get("DUO_INPUT_PICO_BUILD")
    return Path(override) if override else REPOSITORY_ROOT / "build" / "pico-release"


def _artifacts() -> list[Path]:
    root = _build_dir()
    return sorted(root.rglob("*.uf2")) if root.is_dir() else []


pytestmark = pytest.mark.skipif(
    not _artifacts(),
    reason="no Pico build; run cmake --build --preset pico-release first",
)


def uf2_extent(path: Path) -> tuple[int, int]:
    """The lowest and highest flash address one UF2 writes to."""
    data = path.read_bytes()
    if len(data) % _UF2_BLOCK:
        raise ValueError(f"{path.name} is not a whole number of UF2 blocks")

    lowest = None
    highest = 0
    for offset in range(0, len(data), _UF2_BLOCK):
        block = data[offset : offset + _UF2_BLOCK]
        start0, start1, _flags, address, payload_size, *_rest = _UF2_HEADER.unpack_from(block)
        (end_magic,) = struct.unpack_from("<I", block, _UF2_BLOCK - 4)
        if start0 != _UF2_MAGIC_START0 or start1 != _UF2_MAGIC_START1:
            raise ValueError(f"{path.name} block at {offset} has no UF2 magic")
        if end_magic != _UF2_MAGIC_END:
            raise ValueError(f"{path.name} block at {offset} has no UF2 end magic")
        if payload_size > 476:
            raise ValueError(f"{path.name} block at {offset} claims {payload_size} payload bytes")
        lowest = address if lowest is None else min(lowest, address)
        highest = max(highest, address + payload_size)

    if lowest is None:
        raise ValueError(f"{path.name} contains no blocks")
    return lowest, highest


@pytest.fixture(scope="module")
def artifacts() -> dict[str, Path]:
    return {path.name: path for path in _artifacts()}


def test_both_firmware_images_are_produced(artifacts):
    assert sorted(artifacts) == sorted(EXPECTED_ARTIFACTS)


def test_the_build_produces_nothing_else(artifacts):
    # A third UF2 means a target nobody meant to ship, and someone would
    # eventually flash it.
    assert len(artifacts) == 2


@pytest.mark.parametrize("name", EXPECTED_ARTIFACTS)
def test_the_image_starts_at_the_beginning_of_flash(artifacts, name):
    lowest, _highest = uf2_extent(artifacts[name])

    assert lowest == XIP_BASE


@pytest.mark.parametrize("name", EXPECTED_ARTIFACTS)
def test_the_image_stays_below_the_configuration_slots(artifacts, name):
    _lowest, highest = uf2_extent(artifacts[name])

    assert highest <= FIRMWARE_LIMIT, (
        f"{name} reaches 0x{highest:08X}, past the 0x{FIRMWARE_LIMIT:08X} "
        "firmware boundary; it would overwrite config slot A"
    )


@pytest.mark.parametrize("name", EXPECTED_ARTIFACTS)
def test_the_image_is_not_suspiciously_empty(artifacts, name):
    lowest, highest = uf2_extent(artifacts[name])

    # A few hundred bytes would mean the target linked but contains nothing.
    assert highest - lowest > 4096


def test_the_two_images_are_different(artifacts):
    first, second = (artifacts[name].read_bytes() for name in EXPECTED_ARTIFACTS)

    assert first != second


# ----------------------------------------------- what a release must not do


def _u1_elf() -> Path:
    return _build_dir() / "firmware" / "u1_main" / "duo_u1_main.elf"


def _declared_backend() -> str | None:
    """What CMakeCache.txt says DUO_INPUT_BACKEND is, or None if unreadable.

    Both ``pico-release`` and ``pico-pio-usb-release`` land a
    ``duo_u1_main.elf`` at this same relative path, and they link different
    input paths into it - see ``tests/build/test_backend_artifacts.py`` for
    the guard that checks a build directory's declared backend against what
    its ELF actually links. This file only needs to know which real-input
    assertion applies below.
    """
    cache_path = _build_dir() / "CMakeCache.txt"
    if not cache_path.is_file():
        return None
    cache = cache_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^DUO_INPUT_BACKEND:STRING=(.+)$", cache, re.MULTILINE)
    return match.group(1).strip() if match else None


@pytest.mark.skipif(not _u1_elf().is_file(), reason="no U1 ELF in this build")
def test_a_release_image_cannot_generate_its_own_input():
    """The synthetic input generator must be compiled out, not merely idle.

    A device that can type on its own is exactly the thing that must not ship
    because someone forgot a flag. DUO_TEST_PATTERN defaults to OFF, and this
    checks the default actually took effect in the linked image rather than
    trusting that it did.
    """
    import sys

    sys.path.insert(0, str(REPOSITORY_ROOT / "tools"))
    from dump_usb_descriptors import Elf32

    symbols = Elf32(_u1_elf().read_bytes()).symbols()
    generated = [name for name in symbols if "test_pattern" in name]

    assert generated == []


@pytest.mark.skipif(not _u1_elf().is_file(), reason="no U1 ELF in this build")
def test_a_release_image_contains_the_real_peripheral_input_path():
    """Release must read real peripheral reports and feed the input pipeline.

    Bring-up diagnostics may be compiled out, but the product's only physical
    input path may not disappear with them.  Inspect the linked image rather
    than the source: a misplaced preprocessor guard still looks plausible in
    ``main.cpp`` while producing a Core 1 loop that can never receive input.

    Which real-input symbols are the right ones to demand depends on which
    backend this build directory is configured for - CH375 reads a chip over
    SPI, PIO USB runs a TinyUSB host - so the declared backend (read the same
    way ``tests/build/test_backend_artifacts.py`` does, from CMakeCache.txt)
    selects which of the two this test requires. Either way,
    ``InputPipeline::on_event`` must be present: both backends feed it.
    """
    import sys

    sys.path.insert(0, str(REPOSITORY_ROOT / "tools"))
    from dump_usb_descriptors import Elf32

    symbols = Elf32(_u1_elf().read_bytes()).symbols()
    backend = _declared_backend()

    assert any("InputPipeline8on_event" in name for name in symbols), (
        "release ELF does not contain InputPipeline::on_event"
    )

    if backend == "PIO_USB":
        assert any("tuh_task" in name for name in symbols), (
            "release ELF is configured for PIO_USB but contains no tuh_task"
        )
        assert any("tuh_hid_receive_report" in name for name in symbols), (
            "release ELF is configured for PIO_USB but contains no "
            "tuh_hid_receive_report"
        )
    else:
        # CH375, or an older build directory with no DUO_INPUT_BACKEND at
        # all - the only backend that predates this cache variable.
        assert any("Ch375Device4tick" in name for name in symbols), (
            "release ELF does not contain Ch375Device::tick"
        )


# ------------------------------------- what a release has to be able to redo

#: ``__DATE__`` spells months this way, in the C locale, always.
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

#: Anything shaped like a ``__DATE__`` string sitting in a flash image.
_DATE_IN_IMAGE = re.compile(rb"[A-Z][a-z]{2} [ 0-9][0-9] 20[0-9]{2}")


def source_date_epoch() -> int:
    """The one timestamp a build of this tree is allowed to know about.

    ``SOURCE_DATE_EPOCH`` is the cross-ecosystem convention for pinning it. In
    its absence the commit being built is its own best answer: it is a property
    of the source, which is exactly what "reproducible from clean checkout"
    requires and what the wall clock is not.
    """
    pinned = os.environ.get("SOURCE_DATE_EPOCH")
    if pinned:
        return int(pinned)
    completed = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), "log", "-1", "--format=%ct"],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(completed.stdout.strip())


def expected_build_date() -> str:
    """That timestamp in ``__DATE__`` spelling: ``Mmm dd yyyy``, day space padded."""
    moment = datetime.fromtimestamp(source_date_epoch(), timezone.utc)
    return f"{_MONTHS[moment.month - 1]} {moment.day:2d} {moment.year}"


@pytest.mark.parametrize("name", EXPECTED_ARTIFACTS)
def test_the_image_dates_itself_by_the_source_not_by_the_clock(artifacts, name):
    """Two builds of one commit must be the same image.

    The Pico SDK puts ``__DATE__`` in the binary info block, so the same source
    produced a different image every day and ``SHA256SUMS.txt`` described one
    afternoon rather than one commit. The date is worth keeping; taking it from
    the clock is not.

    Asserting on *every* date-shaped string in the image, rather than only on
    the one the SDK emits today, means a future ``__DATE__`` leaking in from
    anywhere else fails here too.
    """
    found = sorted({match.group().decode() for match in _DATE_IN_IMAGE.finditer(artifacts[name].read_bytes())})

    assert found == [expected_build_date()]
