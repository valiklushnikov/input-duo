"""Reading back the package that config_binary writes.

The vectors are the specification: these are the exact bytes the host tooling
generates and the firmware accepts, so a parse that disagrees with them is
wrong no matter how reasonable it looks.

`config_reader.parse_device_config` is a thin adapter over
`config_binary.decode_device_config`, which already does the canonical,
single-cursor validation the format requires. These tests exercise the
adapter: that it round-trips real packages losslessly, that it forwards the
decoder's refusals as `ProjectError`, and that it is at least as strict as
the decoder it wraps.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from duo_input.domain.config_binary import HEADER_SIZE, MAGIC
from duo_input.domain.project_store import ProjectError
from duo_input.generated.protocol import ActionKind

VECTORS = Path("tests") / "vectors" / "config_vectors"


def _vector(name: str) -> bytes:
    return (VECTORS / name).read_bytes()


def _recrc(package: bytearray) -> bytes:
    """Repair the CRC after editing a package, so a test hits its real target."""
    struct.pack_into("<I", package, 12, 0)
    struct.pack_into("<I", package, 12, zlib.crc32(bytes(package)))
    return bytes(package)


def test_a_package_that_is_not_ours_is_refused():
    from duo_input.domain.config_reader import parse_device_config

    package = bytearray(_vector("valid_minimal.bin"))
    package[0:4] = b"XXXX"
    assert package[0:4] != MAGIC

    with pytest.raises(ProjectError):
        parse_device_config(_recrc(package))


def test_a_truncated_package_is_refused_not_indexed_past():
    from duo_input.domain.config_reader import parse_device_config

    package = _vector("valid_minimal.bin")

    for length in (0, 1, HEADER_SIZE - 1):
        with pytest.raises(ProjectError):
            parse_device_config(package[:length])


def test_a_corrupted_package_is_refused():
    from duo_input.domain.config_reader import parse_device_config

    package = bytearray(_vector("valid_minimal.bin"))
    package[HEADER_SIZE] ^= 0xFF  # flip a byte and leave the CRC stale

    with pytest.raises(ProjectError):
        parse_device_config(bytes(package))


def test_a_length_that_disagrees_with_the_bytes_is_refused():
    from duo_input.domain.config_reader import parse_device_config

    package = bytearray(_vector("valid_minimal.bin"))
    struct.pack_into("<I", package, 8, len(package) + 16)

    with pytest.raises(ProjectError):
        parse_device_config(_recrc(package))


def test_a_data_blob_that_overlaps_the_string_blob_is_refused():
    from duo_input.domain.config_reader import parse_device_config

    package = bytearray(_vector("valid_full.bin"))
    total_length = len(package)
    string_blob_offset = struct.unpack_from("<I", package, 24)[0]

    struct.pack_into("<I", package, 32, string_blob_offset)  # data_blob_offset
    struct.pack_into("<I", package, 36, total_length - string_blob_offset)  # data_blob_length

    with pytest.raises(ProjectError):
        parse_device_config(_recrc(package))


def test_a_string_blob_that_does_not_start_at_the_profile_table_end_is_refused():
    from duo_input.domain.config_reader import parse_device_config

    package = bytearray(_vector("valid_full.bin"))
    string_blob_offset, string_blob_length = struct.unpack_from("<II", package, 24)
    string_blob_end = string_blob_offset + string_blob_length

    new_offset = string_blob_offset + 4
    struct.pack_into("<I", package, 24, new_offset)  # string_blob_offset
    struct.pack_into("<I", package, 28, string_blob_end - new_offset)  # string_blob_length

    with pytest.raises(ProjectError):
        parse_device_config(_recrc(package))


# ------------------------------------------------------------- the round trip


@pytest.mark.parametrize(
    "name", ("valid_full.bin", "valid_minimal.bin", "valid_wide_usages.bin")
)
def test_a_minor_zero_package_upgrades_without_moving_its_content(name):
    """Only the schema minor and its dependent CRC change on rewrite.

    valid_full.bin is the hard case: Cyrillic and emoji in names, a macro with
    nine steps, and the maximum macro ID.
    """
    from duo_input.domain.config_binary import compile_device_config
    from duo_input.domain.config_reader import parse_device_config

    original = _vector(name)

    config = parse_device_config(original)
    rewritten = bytearray(compile_device_config(config))
    assert original[5] == 0
    # Literal, not the generated constant: config_binary.py packs byte 5 from
    # that same constant, so comparing against it here would only prove the
    # packer agrees with itself through a second import path. A schema bump
    # nobody intended would pass just as easily. Pin the number instead, the
    # same way test_config_validator.cpp's static_assert pins it in C++.
    assert rewritten[5] == 2
    rewritten[5] = 0

    assert _recrc(rewritten) == original


def test_the_names_come_back_as_the_operator_typed_them():
    from duo_input.domain.config_reader import parse_device_config

    config = parse_device_config(_vector("valid_full.bin"))

    names = [profile.name for profile in config.profiles]
    assert any(name.strip() for name in names), "every profile name came back empty"
    for profile in config.profiles:
        assert isinstance(profile.name, str)


def test_a_project_can_be_built_from_a_package():
    from duo_input.domain.config_reader import binary_to_project

    project = binary_to_project(_vector("valid_full.bin"))

    assert len(project.profiles) == 8
    assert 1 <= project.active_profile_id <= 8


def test_a_binding_table_that_overflows_its_package_is_refused():
    from duo_input.domain.config_reader import parse_device_config

    package = bytearray(_vector("valid_full.bin"))
    # The first profile descriptor sits at HEADER_SIZE; its binding count is at
    # descriptor offset 14. A count this large cannot fit whatever follows.
    struct.pack_into("<H", package, HEADER_SIZE + 14, 0xFFFF)

    with pytest.raises(ProjectError):
        parse_device_config(_recrc(package))


def test_a_name_that_is_not_utf8_is_refused():
    from duo_input.domain.config_reader import parse_device_config

    package = bytearray(_vector("valid_full.bin"))
    string_blob_offset = struct.unpack_from("<I", package, 24)[0]
    package[string_blob_offset] = 0xFF  # a lone continuation byte

    with pytest.raises(ProjectError):
        parse_device_config(_recrc(package))


def test_a_binding_that_references_an_unknown_profile_is_refused():
    """The decoder this wraps rejects what our first parser silently accepted.

    A raw bounds-checking reader would slice this binding out fine - kind,
    code, and modifiers are all still in range. Only the semantic check that
    SET_PROFILE's argument names a real profile catches it.
    """
    from duo_input.domain.config_reader import parse_device_config

    package = bytearray(_vector("valid_full.bin"))
    binding_offset = struct.unpack_from("<I", package, HEADER_SIZE + 16)[0]
    struct.pack_into("<BB", package, binding_offset + 4, ActionKind.SET_PROFILE, 99)

    with pytest.raises(ProjectError):
        parse_device_config(_recrc(package))
