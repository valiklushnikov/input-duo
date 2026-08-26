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
import struct
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
