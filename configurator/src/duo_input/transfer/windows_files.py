"""Publish a transfer manifest as Windows virtual-file descriptors.

Manifest paths use ``/`` on the wire.  Windows Explorer expects ``\\`` in
``FILEDESCRIPTORW.cFileName``; this module is the single conversion point.
"""

from __future__ import annotations

import ctypes

from .model import ENTRY_DIRECTORY, TransferEntry, TransferManifest
from .windows_com import (
    FD_ATTRIBUTES,
    FD_FILESIZE,
    FD_PROGRESSUI,
    FD_WRITESTIME,
    FILE_ATTRIBUTE_DIRECTORY,
    FILEDESCRIPTORW,
    filetime_from_ns,
)

_FILE_ATTRIBUTE_NORMAL = 0x80


def descriptor_entries(manifest: TransferManifest) -> tuple[TransferEntry, ...]:
    """Return entries in manifest order, which is the Explorer ``lindex`` order."""
    return manifest.entries


def group_descriptor_bytes(manifest: TransferManifest) -> bytes:
    """Build a ``FILEGROUPDESCRIPTORW`` blob from ``manifest``."""
    entries = descriptor_entries(manifest)
    blob = bytearray(len(entries).to_bytes(4, "little"))
    for entry in entries:
        blob.extend(bytes(memoryview(_descriptor_for(entry)).cast("B")))
    return bytes(blob)


def _descriptor_for(entry: TransferEntry) -> FILEDESCRIPTORW:
    descriptor = FILEDESCRIPTORW()
    descriptor.dwFlags = FD_ATTRIBUTES | FD_WRITESTIME | FD_PROGRESSUI
    descriptor.cFileName = entry.path.replace("/", "\\")
    descriptor.ftLastWriteTime = filetime_from_ns(entry.mtime_ns)
    if entry.kind == ENTRY_DIRECTORY:
        descriptor.dwFileAttributes = FILE_ATTRIBUTE_DIRECTORY
        return descriptor

    descriptor.dwFileAttributes = _FILE_ATTRIBUTE_NORMAL
    descriptor.dwFlags |= FD_FILESIZE
    descriptor.nFileSizeHigh = (entry.size >> 32) & 0xFFFFFFFF
    descriptor.nFileSizeLow = entry.size & 0xFFFFFFFF
    return descriptor


__all__ = ["descriptor_entries", "group_descriptor_bytes"]
