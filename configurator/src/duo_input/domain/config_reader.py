"""Turning the binary configuration package back into a project.

This is the mirror of :mod:`duo_input.domain.config_binary`, which writes the
same format. The package is self-describing - the header carries every offset,
count and record size - so nothing here has to guess at a layout. What it does
have to do is distrust: these bytes arrive over a serial link from a device,
and `protocol/config_format.md` requires a reader to validate every
``offset + count * record_size`` with checked arithmetic before reading it.

Every refusal raises :class:`ProjectError`. A package that cannot be read is a
thing to tell the operator about, never a reason to fall over.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from duo_input.domain.config_binary import (
    HEADER_SIZE,
    MAGIC,
    PROFILE_SIZE,
)
from duo_input.domain.project_store import ProjectError
from duo_input.generated.protocol import (
    PROFILES,
    SCHEMA_VERSION_MAJOR,
)


@dataclass(frozen=True)
class PackageHeader:
    """The sixty-four bytes that say where everything else is."""

    total_length: int
    active_profile_id: int
    profile_count: int
    string_blob_offset: int
    string_blob_length: int
    data_blob_offset: int
    data_blob_length: int


def _region(package: bytes, offset: int, length: int, what: str) -> None:
    """Refuse a region that runs past the end, in checked arithmetic."""
    if offset < 0 or length < 0 or offset + length > len(package):
        raise ProjectError(
            f"{what} runs past the end of the package "
            f"(offset {offset}, length {length}, package {len(package)})"
        )


def parse_header(package: bytes) -> PackageHeader:
    """Read and check the package header. Raises ProjectError if it is not one."""
    if len(package) < HEADER_SIZE:
        raise ProjectError(
            f"package is {len(package)} bytes, shorter than the {HEADER_SIZE}-byte header"
        )
    if package[0:4] != MAGIC:
        raise ProjectError("not a Duo Input configuration package")

    major = package[4]
    if major != SCHEMA_VERSION_MAJOR:
        raise ProjectError(
            f"schema major {major} cannot be read; this build understands "
            f"{SCHEMA_VERSION_MAJOR}"
        )
    if package[6] != 0 or package[7] != 0:
        raise ProjectError("header flags and reserved byte must be zero")

    total_length, stored_crc = struct.unpack_from("<II", package, 8)
    if total_length != len(package):
        raise ProjectError(
            f"header declares {total_length} bytes, package is {len(package)}"
        )

    computed = bytearray(package)
    struct.pack_into("<I", computed, 12, 0)
    if zlib.crc32(bytes(computed)) != stored_crc:
        raise ProjectError("package CRC does not match its contents")

    profile_count = package[16]
    active_profile_id = package[17]
    descriptor_size = package[18]
    if package[19] != 0:
        raise ProjectError("header reserved byte must be zero")
    if profile_count != PROFILES:
        raise ProjectError(f"profile count is {profile_count}, expected {PROFILES}")
    if not 1 <= active_profile_id <= PROFILES:
        raise ProjectError(f"active profile {active_profile_id} is outside 1..{PROFILES}")
    if descriptor_size != PROFILE_SIZE:
        raise ProjectError(
            f"profile descriptor is {descriptor_size} bytes, expected {PROFILE_SIZE}"
        )

    (
        profile_table_offset,
        string_blob_offset,
        string_blob_length,
        data_blob_offset,
        data_blob_length,
    ) = struct.unpack_from("<IIIII", package, 20)
    if profile_table_offset != HEADER_SIZE:
        raise ProjectError(
            f"profile table starts at {profile_table_offset}, expected {HEADER_SIZE}"
        )
    if any(package[40:64]):
        raise ProjectError("header reserved area must be zero")

    _region(package, profile_table_offset, profile_count * PROFILE_SIZE, "profile table")
    _region(package, string_blob_offset, string_blob_length, "string blob")
    _region(package, data_blob_offset, data_blob_length, "data blob")
    if data_blob_offset + data_blob_length != total_length:
        raise ProjectError("the data blob does not end where the package does")

    return PackageHeader(
        total_length=total_length,
        active_profile_id=active_profile_id,
        profile_count=profile_count,
        string_blob_offset=string_blob_offset,
        string_blob_length=string_blob_length,
        data_blob_offset=data_blob_offset,
        data_blob_length=data_blob_length,
    )


__all__ = ["PackageHeader", "parse_header"]
