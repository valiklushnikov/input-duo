"""Building, naming and reordering the steps of one macro.

Every builder here refuses a payload the binary format would reject, so an
invalid step never reaches the project in the first place. The one exception
is TEXT: it keeps the operator's Unicode in ``source_text`` and leaves the
payload empty, because a text step compiles against the *profile's* layout and
only the compiler knows which layout that is.

Rows carry an identity of their own. It never reaches the project file - the
schema stores macros and bindings by UUID, not steps - it exists so a drag
that moves row 0 to row 2 can be followed through ``beginMoveRows`` and so the
editor can keep pointing at the same step after a reorder.
"""

from __future__ import annotations

import struct
from uuid import UUID, uuid4

from PySide6.QtCore import (
    QAbstractListModel,
    QCoreApplication,
    QModelIndex,
    QT_TRANSLATE_NOOP,
    Qt,
    Signal,
)

from duo_input.domain.models import Macro, MacroStep
from duo_input.generated.protocol import (
    MACRO_STEPS_PER_MACRO,
    MAX_DELAY_MS,
    PROFILES,
    TEXT_CHARACTERS_PER_STEP,
    KeyboardRoute,
    MacroStepType,
    MouseRouteCommand,
)
from duo_input.ui.models.binding_table import key_name, modifier_label

#: How much of a text step is shown in the list before it is elided.
TEXT_PREVIEW_CHARACTERS = 40

_DELAY = struct.Struct("<HH")


def key_tap_step(modifiers: int, usage: int) -> MacroStep:
    """Press and release one key, with modifiers held for that press."""
    if not 0 <= modifiers <= 0xFF:
        raise ValueError("a modifier byte is 0..255")
    if not 1 <= usage <= 0xFF:
        raise ValueError("a key tap needs a HID usage")
    return MacroStep(MacroStepType.KEY_TAP, bytes((modifiers, usage)))


def key_down_step(usage: int) -> MacroStep:
    """Hold one key down until a later KEY_UP or a release-all."""
    return MacroStep(MacroStepType.KEY_DOWN, _usage_payload(usage))


def key_up_step(usage: int) -> MacroStep:
    """Release one key that a KEY_DOWN is holding."""
    return MacroStep(MacroStepType.KEY_UP, _usage_payload(usage))


def _usage_payload(usage: int) -> bytes:
    if not 1 <= usage <= 0xFF:
        raise ValueError("a key step needs a HID usage")
    return bytes((usage,))


def consumer_tap_step(usage: int) -> MacroStep:
    """Tap one consumer control, such as volume or play/pause."""
    if not 1 <= usage <= 0xFFFF:
        raise ValueError("a consumer tap needs a usage of 1..65535")
    return MacroStep(MacroStepType.CONSUMER_TAP, usage.to_bytes(2, "little"))


def text_step(text: str) -> MacroStep:
    """Type ``text``; it is compiled against the profile layout at write time."""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if len(text) > TEXT_CHARACTERS_PER_STEP:
        raise ValueError(
            f"a text step holds at most {TEXT_CHARACTERS_PER_STEP} characters"
        )
    return MacroStep(MacroStepType.TEXT, b"", source_text=text)


def delay_step(minimum_ms: int, maximum_ms: int) -> MacroStep:
    """Wait between the two bounds; equal bounds mean a fixed delay."""
    if minimum_ms < 0 or maximum_ms < 0:
        raise ValueError("a delay cannot be negative")
    if minimum_ms > maximum_ms:
        raise ValueError("the shortest delay cannot exceed the longest")
    if maximum_ms > MAX_DELAY_MS:
        raise ValueError(f"a delay is at most {MAX_DELAY_MS} ms")
    return MacroStep(MacroStepType.DELAY, _DELAY.pack(minimum_ms, maximum_ms))


def set_keyboard_route_step(route: KeyboardRoute) -> MacroStep:
    """Send the keyboard to PC1, PC2 or both from inside the macro."""
    return MacroStep(MacroStepType.SET_KEYBOARD_ROUTE, bytes((KeyboardRoute(route),)))


def set_mouse_route_step(command: MouseRouteCommand) -> MacroStep:
    """Send the mouse to PC1, PC2 or toggle it from inside the macro."""
    return MacroStep(MacroStepType.SET_MOUSE_ROUTE, bytes((MouseRouteCommand(command),)))


def set_profile_step(profile_id: int) -> MacroStep:
    """Switch the device to another profile."""
    if not 1 <= profile_id <= PROFILES:
        raise ValueError(f"a profile ID is 1..{PROFILES}")
    return MacroStep(MacroStepType.SET_PROFILE, bytes((profile_id,)))


#: A default step of every type, so the editor can offer all of them.
STEP_BUILDERS = {
    MacroStepType.KEY_TAP: lambda: key_tap_step(0, 0x04),
    MacroStepType.KEY_DOWN: lambda: key_down_step(0x04),
    MacroStepType.KEY_UP: lambda: key_up_step(0x04),
    MacroStepType.CONSUMER_TAP: lambda: consumer_tap_step(0x00E9),
    MacroStepType.TEXT: lambda: text_step(""),
    MacroStepType.DELAY: lambda: delay_step(50, 50),
    MacroStepType.SET_KEYBOARD_ROUTE: lambda: set_keyboard_route_step(KeyboardRoute.PC1),
    MacroStepType.SET_MOUSE_ROUTE: lambda: set_mouse_route_step(MouseRouteCommand.TOGGLE),
    MacroStepType.SET_PROFILE: lambda: set_profile_step(1),
}


def delay_bounds(step: MacroStep) -> tuple[int, int]:
    """The two millisecond bounds of a DELAY step."""
    if step.type != MacroStepType.DELAY or len(step.payload) != _DELAY.size:
        raise ValueError("not a delay step")
    return _DELAY.unpack(step.payload)


def step_label(step: MacroStep) -> str:
    """One step as the operator reads it. The type is never localised."""
    kind = MacroStepType(step.type)
    name = kind.name
    if kind is MacroStepType.KEY_TAP and len(step.payload) == 2:
        prefix = modifier_label(step.payload[0])
        key = key_name(step.payload[1])
        return f"{name} {prefix}+{key}" if prefix else f"{name} {key}"
    if kind in (MacroStepType.KEY_DOWN, MacroStepType.KEY_UP) and step.payload:
        return f"{name} {key_name(step.payload[0])}"
    if kind is MacroStepType.CONSUMER_TAP and len(step.payload) == 2:
        return f"{name} 0x{int.from_bytes(step.payload, 'little'):04X}"
    if kind is MacroStepType.TEXT:
        if step.source_text is None:
            keystrokes = len(step.payload) // 2
            origin = QCoreApplication.translate(
                "MacroSteps", QT_TRANSLATE_NOOP("MacroSteps", "From the device: {0} keystrokes")
            ).format(keystrokes)
            return f"{name} {origin}"
        text = step.source_text
        if len(text) > TEXT_PREVIEW_CHARACTERS:
            text = text[:TEXT_PREVIEW_CHARACTERS] + "..."
        return f"{name} {text}" if text else name
    if kind is MacroStepType.DELAY and len(step.payload) == _DELAY.size:
        minimum, maximum = delay_bounds(step)
        span = f"{minimum}" if minimum == maximum else f"{minimum}-{maximum}"
        return f"{name} {span} ms"
    if kind is MacroStepType.SET_KEYBOARD_ROUTE and step.payload:
        return f"{name} {_enum_name(KeyboardRoute, step.payload[0])}"
    if kind is MacroStepType.SET_MOUSE_ROUTE and step.payload:
        return f"{name} {_enum_name(MouseRouteCommand, step.payload[0])}"
    if kind is MacroStepType.SET_PROFILE and step.payload:
        return f"{name} {step.payload[0]}"
    return name


def _enum_name(enum_type: type, value: int) -> str:
    try:
        return enum_type(value).name
    except ValueError:
        return f"0x{value:02X}"


class MacroStepListModel(QAbstractListModel):
    """The ordered steps of one macro, with drag-and-drop reordering."""

    #: Emitted with the complete new step tuple after every change.
    steps_changed = Signal(tuple)

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self._steps: list[MacroStep] = []
        self._keys: list[UUID] = []

    # ------------------------------------------------------------- contents

    def set_macro(self, macro: Macro | None) -> None:
        """Show the steps of ``macro``; every row gets a fresh identity."""
        self.beginResetModel()
        self._steps = list(macro.steps) if macro is not None else []
        self._keys = [uuid4() for _ in self._steps]
        self.endResetModel()

    def steps(self) -> tuple[MacroStep, ...]:
        return tuple(self._steps)

    def step_at(self, row: int) -> MacroStep | None:
        return self._steps[row] if 0 <= row < len(self._steps) else None

    def key_of(self, row: int) -> UUID | None:
        """The stable identity of one row; it survives moves and edits."""
        return self._keys[row] if 0 <= row < len(self._keys) else None

    def row_of(self, key: UUID) -> int:
        return self._keys.index(key) if key in self._keys else -1

    # -------------------------------------------------------------- editing

    def insert_step(self, row: int, step: MacroStep) -> None:
        if len(self._steps) >= MACRO_STEPS_PER_MACRO:
            raise ValueError(f"a macro holds at most {MACRO_STEPS_PER_MACRO} steps")
        if not 0 <= row <= len(self._steps):
            raise ValueError(f"row {row} is outside the macro")
        self.beginInsertRows(QModelIndex(), row, row)
        self._steps.insert(row, step)
        self._keys.insert(row, uuid4())
        self.endInsertRows()
        self._announce()

    def remove_step(self, row: int) -> None:
        if not 0 <= row < len(self._steps):
            raise ValueError(f"row {row} is outside the macro")
        self.beginRemoveRows(QModelIndex(), row, row)
        del self._steps[row]
        del self._keys[row]
        self.endRemoveRows()
        self._announce()

    def set_step(self, row: int, step: MacroStep) -> None:
        """Replace one row in place; its identity does not change."""
        if not 0 <= row < len(self._steps):
            raise ValueError(f"row {row} is outside the macro")
        self._steps[row] = step
        index = self.index(row, 0)
        self.dataChanged.emit(index, index)
        self._announce()

    def move_step(self, source: int, target: int) -> bool:
        """Move one row to ``target``. Returns False when nothing moved."""
        count = len(self._steps)
        if not 0 <= source < count or not 0 <= target < count:
            raise ValueError("a macro step can only move inside the macro")
        if source == target:
            return False
        # beginMoveRows counts the destination in the pre-move coordinates, so
        # a downward move needs the row after the target.
        destination = target + 1 if target > source else target
        if not self.beginMoveRows(QModelIndex(), source, source, QModelIndex(), destination):
            return False
        self._steps.insert(target, self._steps.pop(source))
        self._keys.insert(target, self._keys.pop(source))
        self.endMoveRows()
        self._announce()
        return True

    def _announce(self) -> None:
        self.steps_changed.emit(self.steps())

    # ---------------------------------------------------------- Qt model API

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        if parent is not None and parent.isValid():
            return 0
        return len(self._steps)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        step = self.step_at(index.row())
        return None if step is None else step_label(step)

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        if index.isValid():
            return base | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsSelectable
        return base | Qt.ItemFlag.ItemIsDropEnabled

    def supportedDropActions(self) -> Qt.DropAction:  # noqa: N802
        return Qt.DropAction.MoveAction


__all__ = [
    "MACRO_STEPS_PER_MACRO",
    "STEP_BUILDERS",
    "TEXT_PREVIEW_CHARACTERS",
    "MacroStepListModel",
    "consumer_tap_step",
    "delay_bounds",
    "delay_step",
    "key_down_step",
    "key_tap_step",
    "key_up_step",
    "set_keyboard_route_step",
    "set_mouse_route_step",
    "set_profile_step",
    "step_label",
    "text_step",
]
