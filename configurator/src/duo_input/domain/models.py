from __future__ import annotations

from dataclasses import dataclass

from duo_input.generated.protocol import (
    ActionKind,
    BindingMode,
    KeyboardRoute,
    MacroStepType,
    MouseRoute,
    MouseRouteCommand,
    TargetMode,
    TextLayout,
    TriggerKind,
)


@dataclass(frozen=True)
class Trigger:
    kind: TriggerKind
    code: int
    modifiers: int = 0


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    argument: int = 0


@dataclass(frozen=True)
class Binding:
    trigger: Trigger
    mode: BindingMode
    action: Action


@dataclass(frozen=True)
class MacroStep:
    type: MacroStepType
    payload: bytes


@dataclass(frozen=True)
class Macro:
    id: int
    name: str
    target: TargetMode
    steps: tuple[MacroStep, ...]


@dataclass(frozen=True)
class Profile:
    id: int
    name: str
    color_rgb: tuple[int, int, int]
    keyboard_route: KeyboardRoute
    mouse_route: MouseRoute
    text_layout: TextLayout
    bindings: tuple[Binding, ...]
    macros: tuple[Macro, ...]


@dataclass(frozen=True)
class DeviceConfig:
    active_profile_id: int
    profiles: tuple[Profile, ...]
