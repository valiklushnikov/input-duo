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
    BINDING_SIZE,
    HEADER_SIZE,
    MACRO_SIZE,
    MAGIC,
    PROFILE_SIZE,
    STEP_SIZE,
)
from duo_input.domain.models import (
    Action,
    Binding,
    DeviceConfig,
    DeviceProject,
    Macro,
    MacroStep,
    Profile,
    Trigger,
)
from duo_input.domain.project_store import PROJECT_SCHEMA_VERSION, ProjectError
from duo_input.generated.protocol import (
    ActionKind,
    BindingMode,
    KeyboardRoute,
    MacroStepType,
    MouseRoute,
    PROFILES,
    SCHEMA_VERSION_MAJOR,
    TargetMode,
    TextLayout,
    TriggerKind,
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

    expected_string_blob_offset = profile_table_offset + profile_count * PROFILE_SIZE
    if string_blob_offset != expected_string_blob_offset:
        raise ProjectError(
            f"string blob starts at {string_blob_offset}, expected "
            f"{expected_string_blob_offset} (immediately after the profile table)"
        )

    expected_data_blob_offset = (string_blob_offset + string_blob_length + 3) & ~3
    if data_blob_offset != expected_data_blob_offset:
        raise ProjectError(
            f"data blob starts at {data_blob_offset}, expected "
            f"{expected_data_blob_offset} (the string blob end, aligned up to four bytes)"
        )

    return PackageHeader(
        total_length=total_length,
        active_profile_id=active_profile_id,
        profile_count=profile_count,
        string_blob_offset=string_blob_offset,
        string_blob_length=string_blob_length,
        data_blob_offset=data_blob_offset,
        data_blob_length=data_blob_length,
    )


def _text(package: bytes, offset: int, length: int) -> str:
    """One UTF-8 name out of the string blob."""
    _region(package, offset, length, "name")
    try:
        return package[offset : offset + length].decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProjectError(f"a name is not valid UTF-8: {error}") from error


def _enum(enum_type: type, value: int, what: str):
    try:
        return enum_type(value)
    except ValueError as error:
        raise ProjectError(f"{what} {value} is not one this build knows") from error


def parse_device_config(package: bytes) -> DeviceConfig:
    """Read a whole package. Raises ProjectError on anything that does not add up."""
    header = parse_header(package)
    profiles = tuple(
        _read_profile(package, HEADER_SIZE + index * PROFILE_SIZE)
        for index in range(header.profile_count)
    )
    return DeviceConfig(active_profile_id=header.active_profile_id, profiles=profiles)


def binary_to_project(package: bytes) -> DeviceProject:
    """The same, as the editable project the interface works with."""
    config = parse_device_config(package)
    return DeviceProject(
        schema_version=PROJECT_SCHEMA_VERSION,
        active_profile_id=config.active_profile_id,
        profiles=config.profiles,
    )


def _read_profile(package: bytes, at: int) -> Profile:
    _region(package, at, PROFILE_SIZE, "profile descriptor")
    if package[at + 7] != 0:
        raise ProjectError("profile reserved byte must be zero")
    name_offset, name_length = struct.unpack_from("<IH", package, at + 8)
    binding_count = struct.unpack_from("<H", package, at + 14)[0]
    binding_offset = struct.unpack_from("<I", package, at + 16)[0]
    binding_size = struct.unpack_from("<H", package, at + 20)[0]
    macro_count = struct.unpack_from("<H", package, at + 22)[0]
    macro_offset = struct.unpack_from("<I", package, at + 24)[0]
    macro_size = struct.unpack_from("<H", package, at + 28)[0]
    reserved_short, reserved_long = struct.unpack_from("<HI", package, at + 30)
    if reserved_short != 0 or reserved_long != 0:
        raise ProjectError("profile reserved fields must be zero")
    if binding_size != BINDING_SIZE or macro_size != MACRO_SIZE:
        raise ProjectError("a record size in the profile descriptor is not the format's")
    _region(package, binding_offset, binding_count * BINDING_SIZE, "binding table")
    _region(package, macro_offset, macro_count * MACRO_SIZE, "macro table")

    return Profile(
        id=package[at],
        name=_text(package, name_offset, name_length),
        color_rgb=(package[at + 4], package[at + 5], package[at + 6]),
        keyboard_route=_enum(KeyboardRoute, package[at + 1], "keyboard route"),
        mouse_route=_enum(MouseRoute, package[at + 2], "mouse route"),
        text_layout=_enum(TextLayout, package[at + 3], "text layout"),
        bindings=tuple(
            _read_binding(package, binding_offset + index * BINDING_SIZE)
            for index in range(binding_count)
        ),
        macros=tuple(
            _read_macro(package, macro_offset + index * MACRO_SIZE)
            for index in range(macro_count)
        ),
    )


def _read_binding(package: bytes, at: int) -> Binding:
    """The twelve bytes at "Binding record"."""
    kind, code, modifiers, mode, action_kind, argument, reserved_short, reserved_long = (
        struct.unpack_from("<BBBBBBHI", package, at)
    )
    if reserved_short != 0 or reserved_long != 0:
        raise ProjectError("binding reserved fields must be zero")
    trigger = Trigger(
        kind=_enum(TriggerKind, kind, "trigger kind"),
        code=code,
        modifiers=modifiers,
    )
    action = Action(
        kind=_enum(ActionKind, action_kind, "action kind"),
        argument=argument,
    )
    return Binding(trigger=trigger, mode=_enum(BindingMode, mode, "binding mode"), action=action)


def _read_macro(package: bytes, at: int) -> Macro:
    """The twenty-four bytes at "Macro descriptor"."""
    (
        macro_id,
        target,
        reserved_short,
        name_offset,
        name_length,
        step_count,
        step_offset,
        step_size,
        reserved_short2,
        reserved_long,
    ) = struct.unpack_from("<BBHIHHIHHI", package, at)
    if reserved_short != 0 or reserved_short2 != 0 or reserved_long != 0:
        raise ProjectError("macro reserved fields must be zero")
    if step_size != STEP_SIZE:
        raise ProjectError("macro step record size is not the format's")
    _region(package, step_offset, step_count * STEP_SIZE, "step table")

    return Macro(
        id=macro_id,
        name=_text(package, name_offset, name_length),
        target=_enum(TargetMode, target, "macro target"),
        steps=tuple(
            _read_step(package, step_offset + index * STEP_SIZE)
            for index in range(step_count)
        ),
    )


def _read_step(package: bytes, at: int) -> MacroStep:
    """The twelve bytes at "Step descriptor"."""
    step_type, reserved_byte, payload_length, payload_offset, reserved_long = (
        struct.unpack_from("<BBHII", package, at)
    )
    if reserved_byte != 0 or reserved_long != 0:
        raise ProjectError("step reserved fields must be zero")
    _region(package, payload_offset, payload_length, "step payload")

    return MacroStep(
        type=_enum(MacroStepType, step_type, "macro step type"),
        payload=bytes(package[payload_offset : payload_offset + payload_length]),
        source_text=None,
    )


__all__ = ["PackageHeader", "parse_header", "parse_device_config", "binary_to_project"]
