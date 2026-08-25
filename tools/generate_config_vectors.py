"""Regenerate deterministic binary configuration interoperability vectors."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "configurator" / "src"))

from duo_input.domain.config_binary import compile_device_config  # noqa: E402
from duo_input.domain.models import (  # noqa: E402
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
from duo_input.generated.protocol import MacroStepType  # noqa: E402


OUTPUT = ROOT / "tests" / "vectors" / "config_vectors"


def empty_profile(profile_id: int, name: str | None = None) -> Profile:
    return Profile(profile_id, name or f"Profile {profile_id}",
                   (profile_id, profile_id + 1, profile_id + 2),
                   KeyboardRoute.PC1, MouseRoute.PC1, TextLayout.US, (), ())


def vector_configs() -> tuple[DeviceConfig, DeviceConfig]:
    minimal = DeviceConfig(1, tuple(empty_profile(i) for i in range(1, 9)))
    steps = (
        MacroStep(MacroStepType.KEY_TAP, b"\x02\x04"),
        MacroStep(MacroStepType.KEY_DOWN, b"\x05"),
        MacroStep(MacroStepType.KEY_UP, b"\x05"),
        MacroStep(MacroStepType.CONSUMER_TAP, b"\xe9\x00"),
        MacroStep(MacroStepType.TEXT, b"\x02\x0b\x00\x0c"),
        MacroStep(MacroStepType.DELAY, b"\x0c\x00\x60\xea"),
        MacroStep(MacroStepType.SET_KEYBOARD_ROUTE, bytes([KeyboardRoute.BOTH])),
        MacroStep(MacroStepType.SET_MOUSE_ROUTE, bytes([MouseRouteCommand.TOGGLE])),
        MacroStep(MacroStepType.SET_PROFILE, b"\x08"),
    )
    macros = (
        Macro(1, "Привіт 🌍", TargetMode.INHERIT, steps),
        Macro(255, "Maximum ID", TargetMode.PC2, ()),
    )
    bindings = (
        Binding(Trigger(TriggerKind.KEYBOARD_USAGE, 4, 2), BindingMode.REPLACE,
                Action(ActionKind.RUN_MACRO, 255)),
        Binding(Trigger(TriggerKind.MOUSE_BUTTON, 5), BindingMode.ADD,
                Action(ActionKind.SET_PROFILE, 8)),
        Binding(Trigger(TriggerKind.KEYBOARD_USAGE, 7), BindingMode.ADD,
                Action(ActionKind.TOGGLE_KEYBOARD_ROUTE)),
        Binding(Trigger(TriggerKind.KEYBOARD_USAGE, 8), BindingMode.ADD,
                Action(ActionKind.SET_MOUSE_ROUTE, MouseRoute.PC2)),
    )
    profiles = [empty_profile(i, f"Профіль {i}") for i in range(1, 9)]
    profiles[0] = replace(
        profiles[0], text_layout=TextLayout.RU, bindings=bindings, macros=macros
    )
    profiles[1] = replace(profiles[1], text_layout=TextLayout.UA)
    return minimal, DeviceConfig(8, tuple(profiles))


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    minimal, full = vector_configs()
    (OUTPUT / "valid_minimal.bin").write_bytes(compile_device_config(minimal))
    (OUTPUT / "valid_full.bin").write_bytes(compile_device_config(full))


if __name__ == "__main__":
    main()
