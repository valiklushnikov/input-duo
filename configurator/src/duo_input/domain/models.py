from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from duo_input.generated.protocol import MacroStepType


class Route(IntEnum):
    U1 = 1
    U2 = 2
    BOTH = 3


class TextLayout(IntEnum):
    US = 1
    UK = 2
    DE = 3


class TriggerKind(IntEnum):
    KEYBOARD_USAGE = 1
    MOUSE_BUTTON = 2


class BindingMode(IntEnum):
    REPLACE = 1
    ADD = 2


class ActionKind(IntEnum):
    RUN_MACRO = 1
    TOGGLE_KEYBOARD_ROUTE = 2
    SET_KEYBOARD_ROUTE = 3
    TOGGLE_MOUSE_ROUTE = 4
    SET_MOUSE_ROUTE = 5
    SET_PROFILE = 6


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
    target: Route
    steps: tuple[MacroStep, ...]


@dataclass(frozen=True)
class Profile:
    id: int
    name: str
    color_rgb: tuple[int, int, int]
    keyboard_route: Route
    mouse_route: Route
    text_layout: TextLayout
    bindings: tuple[Binding, ...]
    macros: tuple[Macro, ...]


@dataclass(frozen=True)
class DeviceConfig:
    active_profile_id: int
    profiles: tuple[Profile, ...]
