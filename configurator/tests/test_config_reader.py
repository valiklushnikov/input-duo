"""Reading back the package that config_binary writes.

The vectors are the specification: these are the exact bytes the host tooling
generates and the firmware accepts, so a parse that disagrees with them is
wrong no matter how reasonable it looks.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from duo_input.domain.config_binary import HEADER_SIZE, MAGIC
from duo_input.domain.config_reader import parse_header
from duo_input.domain.project_store import ProjectError

VECTORS = Path("tests") / "vectors" / "config_vectors"


def _vector(name: str) -> bytes:
    return (VECTORS / name).read_bytes()


def _recrc(package: bytearray) -> bytes:
    """Repair the CRC after editing a package, so a test hits its real target."""
    struct.pack_into("<I", package, 12, 0)
    struct.pack_into("<I", package, 12, zlib.crc32(bytes(package)))
    return bytes(package)


def test_the_header_of_a_real_package_reads_back():
    header = parse_header(_vector("valid_full.bin"))

    assert header.total_length == len(_vector("valid_full.bin"))
    assert 1 <= header.active_profile_id <= 8
    assert header.profile_count == 8
    assert header.string_blob_offset >= HEADER_SIZE
    assert header.data_blob_offset >= header.string_blob_offset


def test_a_package_that_is_not_ours_is_refused():
    package = bytearray(_vector("valid_minimal.bin"))
    package[0:4] = b"XXXX"
    assert package[0:4] != MAGIC

    with pytest.raises(ProjectError):
        parse_header(_recrc(package))


def test_a_truncated_package_is_refused_not_indexed_past():
    package = _vector("valid_minimal.bin")

    for length in (0, 1, HEADER_SIZE - 1):
        with pytest.raises(ProjectError):
            parse_header(package[:length])


def test_a_corrupted_package_is_refused():
    package = bytearray(_vector("valid_minimal.bin"))
    package[HEADER_SIZE] ^= 0xFF  # flip a byte and leave the CRC stale

    with pytest.raises(ProjectError):
        parse_header(bytes(package))


def test_a_length_that_disagrees_with_the_bytes_is_refused():
    package = bytearray(_vector("valid_minimal.bin"))
    struct.pack_into("<I", package, 8, len(package) + 16)

    with pytest.raises(ProjectError):
        parse_header(_recrc(package))


def test_a_data_blob_that_overlaps_the_string_blob_is_refused():
    package = bytearray(_vector("valid_full.bin"))
    total_length = len(package)
    string_blob_offset = struct.unpack_from("<I", package, 24)[0]

    struct.pack_into("<I", package, 32, string_blob_offset)  # data_blob_offset
    struct.pack_into("<I", package, 36, total_length - string_blob_offset)  # data_blob_length

    with pytest.raises(ProjectError):
        parse_header(_recrc(package))


def test_a_string_blob_that_does_not_start_at_the_profile_table_end_is_refused():
    package = bytearray(_vector("valid_full.bin"))
    string_blob_offset, string_blob_length = struct.unpack_from("<II", package, 24)
    string_blob_end = string_blob_offset + string_blob_length

    new_offset = string_blob_offset + 4
    struct.pack_into("<I", package, 24, new_offset)  # string_blob_offset
    struct.pack_into("<I", package, 28, string_blob_end - new_offset)  # string_blob_length

    with pytest.raises(ProjectError):
        parse_header(_recrc(package))
