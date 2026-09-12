"""Turning the binary configuration package back into a project.

The validation for this already lives beside the writer: `config_binary`'s
`decode_device_config` does the canonical single-cursor walk through the
package and rejects everything `protocol/config_format.md`'s "Reader
requirements" section demands - bad magic/CRC/lengths, noncanonical or
unaligned order, wrong record sizes and counts, invalid UTF-8, unknown enum
values, unknown references, duplicates, overlap, padding and trailing data.
It is exercised on every write the emulator accepts, so it stays honest.

This module does not re-implement any of that. It is a thin adapter: it
calls the existing decoder and translates its `ConfigError` into the
domain's `ProjectError`, then wraps the result into the editable
`DeviceProject` the interface works with. A second reader here would be a
second thing to keep in agreement with the format, and the two would drift.
"""

from __future__ import annotations

from duo_input.domain.config_binary import ConfigError, decode_device_config
from duo_input.domain.models import DeviceConfig, DeviceProject
from duo_input.domain.project_store import PROJECT_SCHEMA_VERSION, ProjectError


def parse_device_config(package: bytes) -> DeviceConfig:
    """Read a package, using the decoder that lives beside the writer.

    The validation this needs already exists and is exercised on every write
    the emulator accepts; a second reader would be a second thing to keep in
    agreement with the format, and the two would drift.
    """
    try:
        return decode_device_config(package)
    except ConfigError as error:
        raise ProjectError(str(error)) from error


def binary_to_project(package: bytes) -> DeviceProject:
    """The same, as the editable project the interface works with."""
    config = parse_device_config(package)
    return DeviceProject(
        schema_version=PROJECT_SCHEMA_VERSION,
        active_profile_id=config.active_profile_id,
        profiles=config.profiles,
        synchronised_control=config.synchronised_control,
    )


__all__ = ["parse_device_config", "binary_to_project"]
