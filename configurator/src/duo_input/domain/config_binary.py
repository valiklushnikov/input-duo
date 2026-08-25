from __future__ import annotations

import struct
import zlib

from duo_input.domain.models import (
    Action,
    ActionKind,
    Binding,
    BindingMode,
    DeviceConfig,
    KeyboardRoute,
    Macro,
    MacroStep,
    MouseRoute,
    MouseRouteCommand,
    Profile,
    TargetMode,
    TextLayout,
    Trigger,
    TriggerKind,
)
from duo_input.generated.protocol import (
    BINARY_CONFIG_MAX_BYTES,
    BINDINGS_PER_PROFILE,
    MACRO_STEPS_PER_MACRO,
    MACROS_PER_PROFILE,
    MAX_DELAY_MS,
    PROFILES,
    SCHEMA_VERSION_MAJOR,
    SCHEMA_VERSION_MINOR,
    MacroStepType,
)


class ConfigError(ValueError):
    """The domain model or encoded configuration is not valid."""


MAGIC = b"DUOC"
HEADER_SIZE = 64
PROFILE_SIZE = 36
BINDING_SIZE = 12
MACRO_SIZE = 24
STEP_SIZE = 12

_HEADER = struct.Struct("<4sBBBBIIBBBBIIIII24s")
_PROFILE = struct.Struct("<BBBB3sBIHHIHHIHHI")
_BINDING = struct.Struct("<BBBBBBHI")
_MACRO = struct.Struct("<BBHIHHIHHI")
_STEP = struct.Struct("<BBHII")

assert _HEADER.size == HEADER_SIZE
assert _PROFILE.size == PROFILE_SIZE
assert _BINDING.size == BINDING_SIZE
assert _MACRO.size == MACRO_SIZE
assert _STEP.size == STEP_SIZE


def _enum(value: object, enum_type: type, field: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"unknown {field}") from exc


def _u8(value: object, field: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= 0xFF:
        raise ConfigError(f"{field} is outside its u8 range")
    return value


def _name(value: object, field: str) -> bytes:
    if not isinstance(value, str) or "\0" in value or len(value) > 48:
        raise ConfigError(f"{field} name must contain at most 48 code points and no NUL")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ConfigError(f"{field} name is not valid Unicode") from exc
    if len(encoded) > 0xFFFF:
        raise ConfigError(f"{field} name is too large")
    return encoded


def _validate_step(step: MacroStep) -> tuple[MacroStepType, bytes]:
    if not isinstance(step, MacroStep):
        raise ConfigError("macro steps must be MacroStep values")
    step_type = _enum(step.type, MacroStepType, "macro step type")
    if not isinstance(step.payload, bytes):
        raise ConfigError("macro step payload must be bytes")
    payload = step.payload
    if len(payload) > 0xFFFF:
        raise ConfigError("macro step payload is too large")
    if step_type is MacroStepType.KEY_TAP:
        if len(payload) != 2 or payload[1] == 0:
            raise ConfigError("KEY_TAP requires modifier and nonzero usage")
    elif step_type in (MacroStepType.KEY_DOWN, MacroStepType.KEY_UP):
        if len(payload) != 1 or payload[0] == 0:
            raise ConfigError(f"{step_type.name} requires one nonzero usage byte")
    elif step_type is MacroStepType.CONSUMER_TAP:
        if len(payload) != 2 or int.from_bytes(payload, "little") == 0:
            raise ConfigError("consumer step requires a nonzero u16 usage")
    elif step_type is MacroStepType.TEXT:
        if not payload or len(payload) % 2 or any(payload[index] == 0 for index in range(1, len(payload), 2)):
            raise ConfigError("TEXT payload must contain modifier/usage pairs")
    elif step_type is MacroStepType.DELAY:
        if len(payload) != 4:
            raise ConfigError("delay payload must contain two u16 values")
        minimum, maximum = struct.unpack("<HH", payload)
        if minimum > maximum or maximum > MAX_DELAY_MS:
            raise ConfigError("delay range is invalid")
    elif step_type is MacroStepType.SET_KEYBOARD_ROUTE:
        if len(payload) != 1:
            raise ConfigError("keyboard route step requires one byte")
        _enum(payload[0], KeyboardRoute, "keyboard route")
    elif step_type is MacroStepType.SET_MOUSE_ROUTE:
        if len(payload) != 1:
            raise ConfigError("mouse route step requires one byte")
        _enum(payload[0], MouseRouteCommand, "mouse route command")
    elif step_type is MacroStepType.SET_PROFILE:
        if len(payload) != 1 or not 1 <= payload[0] <= PROFILES:
            raise ConfigError("profile step references an unknown profile")
    else:  # pragma: no cover - generated enum additions must deliberately define a shape
        raise ConfigError("unknown macro step type")
    return step_type, payload


def _validate_model(config: DeviceConfig):
    if not isinstance(config, DeviceConfig):
        raise ConfigError("config must be DeviceConfig")
    if not isinstance(config.profiles, tuple):
        raise ConfigError("profiles must be an immutable tuple")
    if len(config.profiles) != PROFILES:
        raise ConfigError(f"config must contain exactly {PROFILES} profiles")
    if any(not isinstance(profile, Profile) for profile in config.profiles):
        raise ConfigError("profiles must be Profile values")
    ids = [_u8(profile.id, "profile ID", minimum=1) for profile in config.profiles]
    if ids != list(range(1, PROFILES + 1)):
        raise ConfigError("profile IDs must be exactly 1..8 in order")
    active_profile_id = _u8(config.active_profile_id, "active profile ID", minimum=1)
    if active_profile_id not in ids:
        raise ConfigError("active profile references an unknown profile")

    validated = []
    for profile in config.profiles:
        if not isinstance(profile.bindings, tuple) or not isinstance(profile.macros, tuple):
            raise ConfigError("profile bindings and macros must be immutable tuples")
        profile_name = _name(profile.name, "profile")
        if not isinstance(profile.color_rgb, tuple) or len(profile.color_rgb) != 3:
            raise ConfigError("profile color must be three u8 values")
        for component in profile.color_rgb:
            _u8(component, "profile color")
        keyboard_route = _enum(profile.keyboard_route, KeyboardRoute, "keyboard route")
        mouse_route = _enum(profile.mouse_route, MouseRoute, "mouse route")
        text_layout = _enum(profile.text_layout, TextLayout, "text layout")
        if len(profile.bindings) > BINDINGS_PER_PROFILE:
            raise ConfigError(f"profile may contain at most {BINDINGS_PER_PROFILE} bindings")
        if len(profile.macros) > MACROS_PER_PROFILE:
            raise ConfigError(f"profile may contain at most {MACROS_PER_PROFILE} macros")

        macro_ids: set[int] = set()
        validated_macros = []
        for macro in profile.macros:
            if not isinstance(macro, Macro):
                raise ConfigError("profile macros must be Macro values")
            if not isinstance(macro.steps, tuple):
                raise ConfigError("macro steps must be an immutable tuple")
            macro_id = _u8(macro.id, "macro ID", minimum=1)
            if macro_id in macro_ids:
                raise ConfigError("duplicate macro ID within profile")
            macro_ids.add(macro_id)
            if len(macro.steps) > MACRO_STEPS_PER_MACRO:
                raise ConfigError(f"macro may contain at most {MACRO_STEPS_PER_MACRO} steps")
            validated_macros.append(
                (
                    macro_id,
                    _name(macro.name, "macro"),
                    _enum(macro.target, TargetMode, "macro target"),
                    tuple(_validate_step(step) for step in macro.steps),
                )
            )

        triggers: set[tuple[int, int, int]] = set()
        validated_bindings = []
        for binding in profile.bindings:
            if (
                not isinstance(binding, Binding)
                or not isinstance(binding.trigger, Trigger)
                or not isinstance(binding.action, Action)
            ):
                raise ConfigError("profile bindings must contain Binding, Trigger, and Action values")
            kind = _enum(binding.trigger.kind, TriggerKind, "trigger kind")
            code = _u8(binding.trigger.code, "trigger code", minimum=1)
            modifiers = _u8(binding.trigger.modifiers, "trigger modifiers")
            if kind is TriggerKind.MOUSE_BUTTON and (code > 5 or modifiers != 0):
                raise ConfigError("mouse trigger must be button 1..5 without modifiers")
            key = (int(kind), code, modifiers)
            if key in triggers:
                raise ConfigError("duplicate trigger within profile")
            triggers.add(key)
            mode = _enum(binding.mode, BindingMode, "binding mode")
            action_kind = _enum(binding.action.kind, ActionKind, "action kind")
            argument = _u8(binding.action.argument, "action argument")
            if action_kind is ActionKind.RUN_MACRO:
                if argument not in macro_ids:
                    raise ConfigError("run-macro action references an unknown macro")
            elif action_kind in (ActionKind.TOGGLE_KEYBOARD_ROUTE, ActionKind.TOGGLE_MOUSE_ROUTE):
                if argument != 0:
                    raise ConfigError("toggle action argument must be zero")
            elif action_kind is ActionKind.SET_KEYBOARD_ROUTE:
                _enum(argument, KeyboardRoute, "keyboard route")
            elif action_kind is ActionKind.SET_MOUSE_ROUTE:
                _enum(argument, MouseRoute, "mouse route")
            elif action_kind is ActionKind.SET_PROFILE:
                if argument not in ids:
                    raise ConfigError("set-profile action references an unknown profile")
            validated_bindings.append((kind, code, modifiers, mode, action_kind, argument))
        validated.append(
            (profile, profile_name, keyboard_route, mouse_route, text_layout,
             tuple(validated_bindings), tuple(validated_macros))
        )
    return tuple(validated)


def _align4(value: int) -> int:
    return (value + 3) & ~3


def compile_device_config(config: DeviceConfig) -> bytes:
    validated = _validate_model(config)
    string_blob = bytearray()
    profile_names: list[tuple[int, int]] = []
    macro_names: list[list[tuple[int, int]]] = []
    string_start = HEADER_SIZE + PROFILES * PROFILE_SIZE
    def append_string(value: bytes) -> None:
        if len(value) > BINARY_CONFIG_MAX_BYTES - string_start - len(string_blob):
            raise ConfigError("compiled package exceeds maximum size")
        string_blob.extend(value)

    for _, profile_name, _, _, _, _, _ in validated:
        profile_names.append((string_start + len(string_blob), len(profile_name)))
        append_string(profile_name)
    for _, _, _, _, _, _, macros in validated:
        per_profile = []
        for _, macro_name, _, _ in macros:
            per_profile.append((string_start + len(string_blob), len(macro_name)))
            append_string(macro_name)
        macro_names.append(per_profile)

    data_start = _align4(string_start + len(string_blob))
    if data_start > BINARY_CONFIG_MAX_BYTES:
        raise ConfigError("compiled package exceeds maximum size")
    data = bytearray()
    def append_data(value: bytes) -> None:
        if len(value) > BINARY_CONFIG_MAX_BYTES - data_start - len(data):
            raise ConfigError("compiled package exceeds maximum size")
        data.extend(value)

    profile_tables = []
    for profile_index, (_, _, _, _, _, bindings, macros) in enumerate(validated):
        binding_offset = data_start + len(data)
        for kind, code, modifiers, mode, action_kind, argument in bindings:
            append_data(_BINDING.pack(kind, code, modifiers, mode, action_kind, argument, 0, 0))
        macro_offset = data_start + len(data)
        macro_table_start = len(data)
        append_data(b"\0" * (len(macros) * MACRO_SIZE))
        macro_records = []
        for macro_index, (macro_id, _, target, steps) in enumerate(macros):
            step_offset = data_start + len(data)
            step_table_start = len(data)
            append_data(b"\0" * (len(steps) * STEP_SIZE))
            for step_index, (step_type, payload) in enumerate(steps):
                while len(data) % 4:
                    append_data(b"\0")
                payload_offset = data_start + len(data)
                append_data(payload)
                _STEP.pack_into(data, step_table_start + step_index * STEP_SIZE,
                                step_type, 0, len(payload), payload_offset, 0)
            while len(data) % 4:
                append_data(b"\0")
            macro_records.append((macro_id, target, macro_names[profile_index][macro_index],
                                  len(steps), step_offset))
        for macro_index, (macro_id, target, (name_offset, name_length), step_count, step_offset) in enumerate(macro_records):
            _MACRO.pack_into(data, macro_table_start + macro_index * MACRO_SIZE,
                             macro_id, target, 0, name_offset, name_length, step_count,
                             step_offset, STEP_SIZE, 0, 0)
        profile_tables.append((binding_offset, len(bindings), macro_offset, len(macros)))

    total_length = data_start + len(data)
    if total_length > BINARY_CONFIG_MAX_BYTES:
        raise ConfigError("compiled package exceeds maximum size")
    package = bytearray(total_length)
    package[string_start : string_start + len(string_blob)] = string_blob
    package[data_start:] = data
    for index, (profile, _, keyboard_route, mouse_route, text_layout, _, _) in enumerate(validated):
        name_offset, name_length = profile_names[index]
        binding_offset, binding_count, macro_offset, macro_count = profile_tables[index]
        _PROFILE.pack_into(
            package,
            HEADER_SIZE + index * PROFILE_SIZE,
            profile.id,
            keyboard_route,
            mouse_route,
            text_layout,
            bytes(profile.color_rgb),
            0,
            name_offset,
            name_length,
            binding_count,
            binding_offset,
            BINDING_SIZE,
            macro_count,
            macro_offset,
            MACRO_SIZE,
            0,
            0,
        )
    _HEADER.pack_into(
        package, 0, MAGIC, SCHEMA_VERSION_MAJOR, SCHEMA_VERSION_MINOR, 0, 0,
        total_length, 0, PROFILES, config.active_profile_id, PROFILE_SIZE, 0,
        HEADER_SIZE, string_start, len(string_blob), data_start, len(data), b"\0" * 24,
    )
    struct.pack_into("<I", package, 12, zlib.crc32(package))
    return bytes(package)


def _checked_region(total: int, offset: int, length: int, field: str) -> tuple[int, int]:
    if offset < 0 or length < 0 or offset > total or length > total - offset:
        raise ConfigError(f"{field} is outside the package")
    return offset, offset + length


def _read_name(data: bytes, offset: int, length: int, expected_offset: int, string_end: int, field: str):
    if offset != expected_offset:
        raise ConfigError(f"{field} string offset is not canonical")
    begin, end = _checked_region(len(data), offset, length, field)
    if end > string_end:
        raise ConfigError(f"{field} string is outside the string blob")
    raw = data[begin:end]
    if b"\0" in raw:
        raise ConfigError(f"{field} name contains NUL")
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{field} name is not valid UTF-8") from exc
    if len(value) > 48:
        raise ConfigError(f"{field} name exceeds 48 code points")
    return value, end


def decode_device_config(data: bytes) -> DeviceConfig:
    if not isinstance(data, bytes):
        raise ConfigError("encoded config must be bytes")
    if len(data) < HEADER_SIZE or len(data) > BINARY_CONFIG_MAX_BYTES:
        raise ConfigError("package length is invalid")
    (
        magic, major, _minor, flags, reserved, total_length, stored_crc, profile_count,
        active_profile_id, profile_size, header_reserved, profile_offset, string_offset, string_length,
        data_offset, data_length, reserved_tail,
    ) = _HEADER.unpack_from(data)
    if magic != MAGIC:
        raise ConfigError("invalid package magic")
    if major != SCHEMA_VERSION_MAJOR:
        raise ConfigError("incompatible schema major")
    if flags != 0 or reserved != 0 or header_reserved != 0 or reserved_tail != b"\0" * 24:
        raise ConfigError("flags and reserved bytes must be zero")
    if total_length != len(data):
        raise ConfigError("declared total length does not match input")
    crc_input = bytearray(data)
    crc_input[12:16] = b"\0" * 4
    if zlib.crc32(crc_input) != stored_crc:
        raise ConfigError("invalid package CRC")
    if profile_count != PROFILES or profile_size != PROFILE_SIZE or profile_offset != HEADER_SIZE:
        raise ConfigError("invalid profile table")
    profile_end = profile_offset + profile_count * profile_size
    if profile_end != string_offset:
        raise ConfigError("profile and string regions are not ordered")
    string_begin, string_end = _checked_region(len(data), string_offset, string_length, "string blob")
    if data_offset != _align4(string_end) or data_offset % 4:
        raise ConfigError("data blob offset is not canonical or aligned")
    if any(data[string_end:data_offset]):
        raise ConfigError("string padding must be zero")
    data_begin, data_end = _checked_region(len(data), data_offset, data_length, "data blob")
    if data_begin != data_offset or data_end != len(data):
        raise ConfigError("data blob must consume the package tail")

    profile_raw = []
    expected_string = string_begin
    for index in range(PROFILES):
        fields = _PROFILE.unpack_from(data, profile_offset + index * PROFILE_SIZE)
        (
            profile_id, keyboard_route, mouse_route, text_layout, color, profile_reserved,
            name_offset, name_length, binding_count, binding_offset, binding_size,
            macro_count, macro_offset, macro_size, profile_reserved2, profile_reserved3,
        ) = fields
        if profile_id != index + 1:
            raise ConfigError("profile IDs must be unique and ordered 1..8")
        keyboard_route = _enum(keyboard_route, KeyboardRoute, "keyboard route")
        mouse_route = _enum(mouse_route, MouseRoute, "mouse route")
        text_layout = _enum(text_layout, TextLayout, "text layout")
        if profile_reserved or profile_reserved2 or profile_reserved3:
            raise ConfigError("profile reserved fields must be zero")
        if binding_count > BINDINGS_PER_PROFILE or binding_size != BINDING_SIZE:
            raise ConfigError("invalid binding count or record size")
        if macro_count > MACROS_PER_PROFILE or macro_size != MACRO_SIZE:
            raise ConfigError("invalid macro count or record size")
        profile_name, expected_string = _read_name(
            data, name_offset, name_length, expected_string, string_end, "profile")
        profile_raw.append((profile_id, profile_name, tuple(color), keyboard_route, mouse_route,
                            text_layout, binding_count, binding_offset, macro_count, macro_offset))

    # Tables and payloads are a single canonical cursor through the data blob.
    cursor = data_begin
    profiles = []
    for profile_index, raw_profile in enumerate(profile_raw):
        (profile_id, profile_name, color, keyboard_route, mouse_route, text_layout,
         binding_count, binding_offset, macro_count, macro_offset) = raw_profile
        if binding_offset != cursor or binding_offset % 4:
            raise ConfigError("binding table offset is not canonical or aligned")
        _, cursor = _checked_region(len(data), binding_offset, binding_count * BINDING_SIZE, "binding table")
        if macro_offset != cursor or macro_offset % 4:
            raise ConfigError("macro table offset is not canonical or aligned")
        _, macro_table_end = _checked_region(len(data), macro_offset, macro_count * MACRO_SIZE, "macro table")
        cursor = macro_table_end
        macro_raw = []
        macro_ids: set[int] = set()
        for macro_index in range(macro_count):
            (
                macro_id, target, macro_reserved, name_offset, name_length, step_count,
                step_offset, step_size, macro_reserved2, macro_reserved3,
            ) = _MACRO.unpack_from(data, macro_offset + macro_index * MACRO_SIZE)
            if macro_id == 0 or macro_id in macro_ids:
                raise ConfigError("duplicate or zero macro ID")
            macro_ids.add(macro_id)
            target = _enum(target, TargetMode, "macro target")
            if macro_reserved or macro_reserved2 or macro_reserved3:
                raise ConfigError("macro reserved fields must be zero")
            if step_count > MACRO_STEPS_PER_MACRO or step_size != STEP_SIZE:
                raise ConfigError("invalid step count or record size")
            macro_name, expected_string = _read_name(
                data, name_offset, name_length, expected_string, string_end, "macro")
            macro_raw.append((macro_id, macro_name, target, step_count, step_offset))

        macros = []
        for macro_id, macro_name, target, step_count, step_offset in macro_raw:
            if step_offset != cursor or step_offset % 4:
                raise ConfigError("step table offset is not canonical or aligned")
            _, cursor = _checked_region(len(data), step_offset, step_count * STEP_SIZE, "step table")
            steps = []
            for step_index in range(step_count):
                step_type, step_reserved, payload_length, payload_offset, step_reserved2 = _STEP.unpack_from(
                    data, step_offset + step_index * STEP_SIZE)
                if step_reserved or step_reserved2:
                    raise ConfigError("step reserved fields must be zero")
                aligned = _align4(cursor)
                if payload_offset != aligned or any(data[cursor:aligned]):
                    raise ConfigError("step payload offset or padding is not canonical")
                _, cursor = _checked_region(len(data), payload_offset, payload_length, "step payload")
                decoded_step = MacroStep(_enum(step_type, MacroStepType, "macro step type"), data[payload_offset:cursor])
                _validate_step(decoded_step)
                steps.append(decoded_step)
            aligned = _align4(cursor)
            if any(data[cursor:aligned]):
                raise ConfigError("macro padding must be zero")
            cursor = aligned
            macros.append(Macro(macro_id, macro_name, target, tuple(steps)))

        bindings = []
        triggers: set[tuple[int, int, int]] = set()
        for binding_index in range(binding_count):
            values = _BINDING.unpack_from(data, binding_offset + binding_index * BINDING_SIZE)
            kind, code, modifiers, mode, action_kind, argument, reserved1, reserved2 = values
            if reserved1 or reserved2:
                raise ConfigError("binding reserved fields must be zero")
            trigger_kind = _enum(kind, TriggerKind, "trigger kind")
            mode_value = _enum(mode, BindingMode, "binding mode")
            action_value = _enum(action_kind, ActionKind, "action kind")
            trigger = Trigger(trigger_kind, code, modifiers)
            binding = Binding(trigger, mode_value, Action(action_value, argument))
            key = (kind, code, modifiers)
            if key in triggers:
                raise ConfigError("duplicate trigger within profile")
            triggers.add(key)
            # Reuse model validation below for code ranges and references.
            bindings.append(binding)
        profiles.append(Profile(profile_id, profile_name, color, keyboard_route, mouse_route,
                                text_layout, tuple(bindings), tuple(macros)))
    if expected_string != string_end:
        raise ConfigError("string blob contains trailing or overlapping bytes")
    if cursor != data_end:
        raise ConfigError("data blob contains trailing or overlapping bytes")
    result = DeviceConfig(active_profile_id, tuple(profiles))
    _validate_model(result)
    return result
