"""FILEGROUPDESCRIPTORW built from a transfer manifest.

The wire uses ``/`` separators; this is the one conversion point where
``cFileName`` receives Windows ``\\`` separators.
"""

from __future__ import annotations

import ctypes
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="descriptor is Windows-only")

from duo_input.transfer.model import ENTRY_DIRECTORY, ENTRY_FILE, TransferEntry, TransferManifest
from duo_input.transfer.windows_com import (
    FD_FILESIZE,
    FILE_ATTRIBUTE_DIRECTORY,
    FILEDESCRIPTORW,
    filetime_from_ns,
)
from duo_input.transfer.windows_files import descriptor_entries, group_descriptor_bytes


def _manifest():
    return TransferManifest(
        transfer_id="t",
        entries=(
            TransferEntry(path="Photos", kind=ENTRY_DIRECTORY, size=0, mtime_ns=1_000_000_000),
            TransferEntry(
                path="Photos/img.jpg", kind=ENTRY_FILE, size=5_000_000_000, mtime_ns=2_000_000_000
            ),
        ),
    )


def _parse(blob: bytes):
    count = int.from_bytes(blob[:4], "little")
    stride = ctypes.sizeof(FILEDESCRIPTORW)
    parsed = []
    for index in range(count):
        start = 4 + index * stride
        descriptor = FILEDESCRIPTORW.from_buffer_copy(blob[start : start + stride])
        parsed.append(descriptor)
    return count, parsed


def test_the_blob_starts_with_the_entry_count():
    count, _descriptors = _parse(group_descriptor_bytes(_manifest()))

    assert count == 2


def test_the_blob_is_exactly_the_count_plus_the_descriptors():
    blob = group_descriptor_bytes(_manifest())

    assert len(blob) == 4 + 2 * ctypes.sizeof(FILEDESCRIPTORW)


def test_wire_slashes_become_backslashes_in_the_file_name():
    _count, descriptors = _parse(group_descriptor_bytes(_manifest()))

    assert descriptors[1].cFileName == "Photos\\img.jpg"


def test_a_directory_is_flagged_as_a_directory_and_declares_no_size():
    _count, descriptors = _parse(group_descriptor_bytes(_manifest()))

    assert descriptors[0].dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY
    assert not descriptors[0].dwFlags & FD_FILESIZE


def test_a_file_declares_its_size_across_both_halves():
    _count, descriptors = _parse(group_descriptor_bytes(_manifest()))
    size = (descriptors[1].nFileSizeHigh << 32) | descriptors[1].nFileSizeLow

    assert size == 5_000_000_000
    assert descriptors[1].dwFlags & FD_FILESIZE


def test_a_file_carries_the_modification_time_from_the_manifest():
    _count, descriptors = _parse(group_descriptor_bytes(_manifest()))
    expected = filetime_from_ns(2_000_000_000)

    assert descriptors[1].ftLastWriteTime.dwLowDateTime == expected.dwLowDateTime
    assert descriptors[1].ftLastWriteTime.dwHighDateTime == expected.dwHighDateTime


def test_the_descriptor_order_is_the_manifest_order_so_lindex_lines_up():
    manifest = _manifest()

    assert descriptor_entries(manifest) == manifest.entries


def test_an_empty_manifest_produces_only_a_zero_count():
    blob = group_descriptor_bytes(TransferManifest(transfer_id="t", entries=()))

    assert blob == (0).to_bytes(4, "little")
