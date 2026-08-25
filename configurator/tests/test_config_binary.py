from dataclasses import replace
from pathlib import Path
import zlib

import pytest

from duo_input.domain.config_binary import ConfigError, compile_device_config, decode_device_config
from duo_input.domain.models import (
    Action,
    ActionKind,
    Binding,
    BindingMode,
    DeviceConfig,
    Macro,
    MacroStep,
    Profile,
    Route,
    TextLayout,
    Trigger,
    TriggerKind,
)
from duo_input.generated.protocol import MacroStepType


VECTOR_DIRECTORY = Path("tests/vectors/config_vectors")


def empty_profile(profile_id: int, *, name: str | None = None) -> Profile:
    return Profile(
        id=profile_id,
        name=name or f"Profile {profile_id}",
        color_rgb=(profile_id, profile_id + 1, profile_id + 2),
        keyboard_route=Route.U1,
        mouse_route=Route.U1,
        text_layout=TextLayout.US,
        bindings=(),
        macros=(),
    )


def minimal_config() -> DeviceConfig:
    return DeviceConfig(active_profile_id=1, profiles=tuple(empty_profile(i) for i in range(1, 9)))


def full_config() -> DeviceConfig:
    all_steps = (
        MacroStep(MacroStepType.KEY_TAP, b"\x02\x04"),
        MacroStep(MacroStepType.KEY_DOWN, b"\x00\x05"),
        MacroStep(MacroStepType.KEY_UP, b"\x00\x05"),
        MacroStep(MacroStepType.CONSUMER_TAP, b"\xe9\x00"),
        MacroStep(MacroStepType.TEXT, b"\x02\x0b\x00\x0c"),
        MacroStep(MacroStepType.DELAY, (12).to_bytes(2, "little") + (60000).to_bytes(2, "little")),
        MacroStep(MacroStepType.SET_KEYBOARD_ROUTE, bytes([Route.BOTH])),
        MacroStep(MacroStepType.SET_MOUSE_ROUTE, bytes([Route.U2])),
        MacroStep(MacroStepType.SET_PROFILE, b"\x08"),
    )
    macros = (
        Macro(id=1, name="Привіт 🌍", target=Route.BOTH, steps=all_steps),
        Macro(id=255, name="Maximum ID", target=Route.U2, steps=()),
    )
    bindings = (
        Binding(
            Trigger(TriggerKind.KEYBOARD_USAGE, 4, modifiers=2),
            BindingMode.REPLACE,
            Action(ActionKind.RUN_MACRO, 255),
        ),
        Binding(
            Trigger(TriggerKind.MOUSE_BUTTON, 5),
            BindingMode.ADD,
            Action(ActionKind.SET_PROFILE, 8),
        ),
        Binding(
            Trigger(TriggerKind.KEYBOARD_USAGE, 7),
            BindingMode.ADD,
            Action(ActionKind.TOGGLE_KEYBOARD_ROUTE),
        ),
        Binding(
            Trigger(TriggerKind.KEYBOARD_USAGE, 8),
            BindingMode.ADD,
            Action(ActionKind.SET_MOUSE_ROUTE, Route.BOTH),
        ),
    )
    profiles = [empty_profile(i, name=f"Профіль {i}") for i in range(1, 9)]
    profiles[0] = replace(profiles[0], bindings=bindings, macros=macros)
    return DeviceConfig(active_profile_id=8, profiles=tuple(profiles))


@pytest.mark.parametrize("config", [minimal_config(), full_config()])
def test_compile_is_deterministic_and_round_trips(config):
    first = compile_device_config(config)

    assert first == compile_device_config(config)
    assert decode_device_config(first) == config


def test_compile_rejects_mutable_collections_that_cannot_round_trip_equal():
    config = replace(minimal_config(), profiles=list(minimal_config().profiles))

    with pytest.raises(ConfigError, match="tuple"):
        compile_device_config(config)


def test_compile_reports_invalid_nested_domain_objects_as_config_errors():
    config = replace(minimal_config(), profiles=(object(),) + minimal_config().profiles[1:])

    with pytest.raises(ConfigError, match="Profile"):
        compile_device_config(config)


def test_committed_vectors_match_deterministic_python_compiler():
    assert (VECTOR_DIRECTORY / "valid_minimal.bin").read_bytes() == compile_device_config(minimal_config())
    assert (VECTOR_DIRECTORY / "valid_full.bin").read_bytes() == compile_device_config(full_config())


@pytest.mark.parametrize(
    ("profiles", "message"),
    [
        (tuple(empty_profile(i) for i in range(1, 8)), "exactly 8"),
        (tuple(empty_profile(i) for i in range(1, 10)), "exactly 8"),
        (tuple(empty_profile(i) for i in (1, 2, 3, 4, 5, 6, 7, 7)), "profile IDs"),
    ],
)
def test_compile_rejects_missing_ninth_and_duplicate_profile_ids(profiles, message):
    with pytest.raises(ConfigError, match=message):
        compile_device_config(DeviceConfig(active_profile_id=1, profiles=profiles))


def test_compile_rejects_129th_binding_and_duplicate_trigger():
    binding = Binding(
        Trigger(TriggerKind.KEYBOARD_USAGE, 4),
        BindingMode.REPLACE,
        Action(ActionKind.SET_PROFILE, 1),
    )
    profile = replace(empty_profile(1), bindings=tuple(replace(binding, trigger=replace(binding.trigger, code=i)) for i in range(1, 130)))
    with pytest.raises(ConfigError, match="128 bindings"):
        compile_device_config(replace(minimal_config(), profiles=(profile,) + minimal_config().profiles[1:]))

    profile = replace(empty_profile(1), bindings=(binding, binding))
    with pytest.raises(ConfigError, match="duplicate trigger"):
        compile_device_config(replace(minimal_config(), profiles=(profile,) + minimal_config().profiles[1:]))


def test_compile_rejects_33rd_macro_duplicate_macro_id_and_65th_step():
    macros = tuple(Macro(i, f"Macro {i}", Route.U1, ()) for i in range(1, 34))
    profile = replace(empty_profile(1), macros=macros)
    with pytest.raises(ConfigError, match="32 macros"):
        compile_device_config(replace(minimal_config(), profiles=(profile,) + minimal_config().profiles[1:]))


def test_compile_rejects_package_over_360_kib_limit():
    largest_record_payload = b"\0\x04" * 32767
    macro = Macro(
        1,
        "oversized",
        Route.U1,
        tuple(MacroStep(MacroStepType.TEXT, largest_record_payload) for _ in range(6)),
    )
    profile = replace(empty_profile(1), macros=(macro,))

    with pytest.raises(ConfigError, match="maximum size"):
        compile_device_config(replace(minimal_config(), profiles=(profile,) + minimal_config().profiles[1:]))

    duplicate = Macro(1, "duplicate", Route.U1, ())
    profile = replace(empty_profile(1), macros=(duplicate, duplicate))
    with pytest.raises(ConfigError, match="duplicate macro"):
        compile_device_config(replace(minimal_config(), profiles=(profile,) + minimal_config().profiles[1:]))

    too_many_steps = Macro(1, "large", Route.U1, tuple(MacroStep(MacroStepType.KEY_TAP, b"\0\x04") for _ in range(65)))
    profile = replace(empty_profile(1), macros=(too_many_steps,))
    with pytest.raises(ConfigError, match="64 steps"):
        compile_device_config(replace(minimal_config(), profiles=(profile,) + minimal_config().profiles[1:]))


@pytest.mark.parametrize("name", ["x" * 49, "😀" * 49, "bad\0name"])
def test_compile_rejects_overlong_unicode_and_embedded_nul_names(name):
    profile = replace(empty_profile(1), name=name)
    with pytest.raises(ConfigError, match="name"):
        compile_device_config(replace(minimal_config(), profiles=(profile,) + minimal_config().profiles[1:]))


@pytest.mark.parametrize(
    "binding",
    [
        Binding(Trigger(99, 1), BindingMode.REPLACE, Action(ActionKind.SET_PROFILE, 1)),
        Binding(Trigger(TriggerKind.MOUSE_BUTTON, 6), BindingMode.REPLACE, Action(ActionKind.SET_PROFILE, 1)),
        Binding(Trigger(TriggerKind.KEYBOARD_USAGE, 0), BindingMode.REPLACE, Action(ActionKind.SET_PROFILE, 1)),
        Binding(Trigger(TriggerKind.KEYBOARD_USAGE, 4), 99, Action(ActionKind.SET_PROFILE, 1)),
        Binding(Trigger(TriggerKind.KEYBOARD_USAGE, 4), BindingMode.REPLACE, Action(99, 0)),
        Binding(Trigger(TriggerKind.KEYBOARD_USAGE, 4), BindingMode.REPLACE, Action(ActionKind.RUN_MACRO, 4)),
        Binding(Trigger(TriggerKind.KEYBOARD_USAGE, 4), BindingMode.REPLACE, Action(ActionKind.SET_PROFILE, 9)),
    ],
)
def test_compile_rejects_unknown_or_out_of_range_binding_fields(binding):
    profile = replace(empty_profile(1), bindings=(binding,))
    with pytest.raises(ConfigError):
        compile_device_config(replace(minimal_config(), profiles=(profile,) + minimal_config().profiles[1:]))


@pytest.mark.parametrize(
    "step",
    [
        MacroStep(255, b""),
        MacroStep(MacroStepType.KEY_TAP, b"\0"),
        MacroStep(MacroStepType.KEY_DOWN, b"\0\0"),
        MacroStep(MacroStepType.CONSUMER_TAP, b"\0\0"),
        MacroStep(MacroStepType.TEXT, b""),
        MacroStep(MacroStepType.TEXT, b"\0\x04\0"),
        MacroStep(MacroStepType.TEXT, b"\0\0"),
        MacroStep(MacroStepType.DELAY, b"\0\0\0"),
        MacroStep(MacroStepType.DELAY, (10).to_bytes(2, "little") + (9).to_bytes(2, "little")),
        MacroStep(MacroStepType.DELAY, (0).to_bytes(2, "little") + (60001).to_bytes(2, "little")),
        MacroStep(MacroStepType.SET_KEYBOARD_ROUTE, b"\0"),
        MacroStep(MacroStepType.SET_PROFILE, b"\x09"),
    ],
)
def test_compile_rejects_unknown_or_malformed_step_payloads(step):
    macro = Macro(1, "bad", Route.U1, (step,))
    profile = replace(empty_profile(1), macros=(macro,))
    with pytest.raises(ConfigError):
        compile_device_config(replace(minimal_config(), profiles=(profile,) + minimal_config().profiles[1:]))


def repair_crc(data: bytearray) -> bytes:
    data[12:16] = b"\0" * 4
    data[12:16] = zlib.crc32(data).to_bytes(4, "little")
    return bytes(data)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.__setitem__(slice(0, 4), b"FAIL"),
        lambda data: data.__setitem__(4, 2),
        lambda data: data.__setitem__(6, 1),
        lambda data: data.__setitem__(7, 1),
        lambda data: data.__setitem__(slice(8, 12), (1).to_bytes(4, "little")),
        lambda data: data.__setitem__(slice(16, 18), (7).to_bytes(2, "little")),
        lambda data: data.__setitem__(18, 0),
        lambda data: data.__setitem__(19, 1),
        lambda data: data.__setitem__(slice(20, 24), (65).to_bytes(4, "little")),
        lambda data: data.__setitem__(40, 1),
    ],
)
def test_decode_rejects_repaired_invalid_header_fields(mutation):
    malformed = bytearray(compile_device_config(minimal_config()))
    mutation(malformed)
    with pytest.raises(ConfigError):
        decode_device_config(repair_crc(malformed))


def test_decode_rejects_crc_damage_truncation_invalid_utf8_and_trailing_bytes():
    encoded = compile_device_config(minimal_config())
    damaged = bytearray(encoded)
    damaged[-1] ^= 1
    with pytest.raises(ConfigError, match="CRC"):
        decode_device_config(bytes(damaged))

    for boundary in (0, 63, 64, 319, len(encoded) - 1):
        with pytest.raises(ConfigError):
            decode_device_config(encoded[:boundary])

    invalid_utf8 = bytearray(encoded)
    name_offset = int.from_bytes(invalid_utf8[64 + 8 : 64 + 12], "little")
    invalid_utf8[name_offset] = 0xFF
    with pytest.raises(ConfigError, match="UTF-8"):
        decode_device_config(repair_crc(invalid_utf8))

    with pytest.raises(ConfigError, match="length"):
        decode_device_config(encoded + b"\0")
