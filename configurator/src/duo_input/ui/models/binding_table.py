"""How one profile's bindings are named and listed.

Protocol identifiers are never localised: an action reads ``SET_MOUSE_ROUTE
PC2`` in every language, because that is the name the device, the logs and the
diagnostics ZIP all use. Only the column headers are translated.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID

from PySide6.QtCore import (
    QAbstractTableModel,
    QCoreApplication,
    QModelIndex,
    Qt,
)
from PySide6.QtCore import QT_TRANSLATE_NOOP

from duo_input.domain.models import Action, Binding, Profile, Trigger
from duo_input.generated.protocol import (
    ActionKind,
    Capability,
    KeyboardRoute,
    MouseRoute,
    TriggerKind,
)

# HID keyboard modifier bits, in report order.
LEFT_CTRL = 0x01
LEFT_SHIFT = 0x02
LEFT_ALT = 0x04
LEFT_GUI = 0x08

#: Modifier key of the editor, in the order the labels are joined.
MODIFIER_BITS: tuple[tuple[str, str, int], ...] = (
    ("ctrl", "Ctrl", LEFT_CTRL),
    ("shift", "Shift", LEFT_SHIFT),
    ("alt", "Alt", LEFT_ALT),
    ("gui", "Win", LEFT_GUI),
)

#: Buttons every HID mouse reports; 4 and 5 have to be seen before they exist.
STANDARD_MOUSE_BUTTONS: tuple[int, ...] = (1, 2, 3)

#: Highest button number the binary format can store, mirrored from validation.
MAX_MOUSE_BUTTON = 5


def _key_names() -> dict[int, str]:
    names = {usage: chr(ord("A") + usage - 0x04) for usage in range(0x04, 0x1E)}
    names.update({usage: "1234567890"[usage - 0x1E] for usage in range(0x1E, 0x28)})
    names.update(
        {
            0x28: "Enter",
            0x29: "Esc",
            0x2A: "Backspace",
            0x2B: "Tab",
            0x2C: "Space",
            0x2D: "-",
            0x2E: "=",
            0x2F: "[",
            0x30: "]",
            0x31: "\\",
            0x33: ";",
            0x34: "'",
            0x35: "`",
            0x36: ",",
            0x37: ".",
            0x38: "/",
            0x39: "CapsLock",
            0x49: "Insert",
            0x4A: "Home",
            0x4B: "PageUp",
            0x4C: "Delete",
            0x4D: "End",
            0x4E: "PageDown",
            0x4F: "Right",
            0x50: "Left",
            0x51: "Down",
            0x52: "Up",
        }
    )
    names.update({0x3A + index: f"F{index + 1}" for index in range(12)})
    return names


#: HID usage to the legend the operator sees. Missing usages read as a number.
KEY_NAMES: dict[int, str] = _key_names()

#: Usages the key chooser offers, in HID order.
SELECTABLE_USAGES: tuple[int, ...] = tuple(sorted(KEY_NAMES))


def key_name(usage: int) -> str:
    """The legend of one HID usage, or its number when it has no name here."""
    return KEY_NAMES.get(usage, f"usage 0x{usage:02X}")


def modifier_label(modifiers: int) -> str:
    """``Ctrl+Shift`` for the bits that are set, empty when none are."""
    return "+".join(label for _, label, bit in MODIFIER_BITS if modifiers & bit)


def trigger_label(trigger: Trigger) -> str:
    """One trigger as the operator reads it. Never localised."""
    if trigger.kind == TriggerKind.MOUSE_BUTTON:
        return f"Button {trigger.code}"
    prefix = modifier_label(trigger.modifiers)
    name = key_name(trigger.code)
    return f"{prefix}+{name}" if prefix else name


#: What each action does, said the way the operator would say it. The protocol
#: name stays available in a tooltip: a screenshot has to be readable against
#: the diagnostics and the documentation, which both speak in identifiers.
ACTION_MEANINGS: dict[ActionKind, tuple[str, str]] = {
    ActionKind.TOGGLE_KEYBOARD_ROUTE: (
        QT_TRANSLATE_NOOP("BindingTable", "Switch the keyboard between PC1 and PC2"),
        QT_TRANSLATE_NOOP(
            "BindingTable",
            "Each press sends the keyboard to the other computer.",
        ),
    ),
    ActionKind.TOGGLE_MOUSE_ROUTE: (
        QT_TRANSLATE_NOOP("BindingTable", "Switch the mouse between PC1 and PC2"),
        QT_TRANSLATE_NOOP(
            "BindingTable", "Each press sends the mouse to the other computer."
        ),
    ),
    ActionKind.SET_KEYBOARD_ROUTE: (
        QT_TRANSLATE_NOOP("BindingTable", "Send the keyboard to one computer"),
        QT_TRANSLATE_NOOP(
            "BindingTable",
            "Always the same computer, whichever one was being used before.",
        ),
    ),
    ActionKind.SET_MOUSE_ROUTE: (
        QT_TRANSLATE_NOOP("BindingTable", "Send the mouse to one computer"),
        QT_TRANSLATE_NOOP(
            "BindingTable",
            "Always the same computer, whichever one was being used before.",
        ),
    ),
    ActionKind.SET_PROFILE: (
        QT_TRANSLATE_NOOP("BindingTable", "Switch to another profile"),
        QT_TRANSLATE_NOOP(
            "BindingTable", "Loads a different set of bindings on the device."
        ),
    ),
    ActionKind.RUN_MACRO: (
        QT_TRANSLATE_NOOP("BindingTable", "Run a macro"),
        QT_TRANSLATE_NOOP(
            "BindingTable", "Plays a recorded sequence of keys and pauses."
        ),
    ),
}


def action_kind_label(kind: ActionKind) -> str:
    """What this action does, in words. Falls back to the protocol name."""
    meaning = ACTION_MEANINGS.get(kind)
    if meaning is None:
        return kind.name
    return QCoreApplication.translate("BindingTable", meaning[0])


def action_kind_hint(kind: ActionKind) -> str:
    """The sentence behind the label, with the protocol identifier after it."""
    meaning = ACTION_MEANINGS.get(kind)
    if meaning is None:
        return kind.name
    sentence = QCoreApplication.translate("BindingTable", meaning[1])
    return f"{sentence}\n{kind.name}"


def action_label(action: Action, profile: Profile | None = None) -> str:
    """One action in words, with the computer or macro it points at.

    The protocol name is not here: it is in the tooltip beside it. An operator
    reading this column wants to know what pressing the key will do, and
    ``SET_MOUSE_ROUTE PC2`` answers a different question.
    """
    kind = ActionKind(action.kind)
    if kind is ActionKind.RUN_MACRO:
        name = None
        if profile is not None:
            name = next(
                (macro.name for macro in profile.macros if macro.id == action.argument), None
            )
        target = f"#{action.argument}" if name is None else f"#{action.argument} {name}"
        return f"{action_kind_label(kind)}: {target}"
    if kind in (ActionKind.TOGGLE_KEYBOARD_ROUTE, ActionKind.TOGGLE_MOUSE_ROUTE):
        return action_kind_label(kind)
    if kind is ActionKind.SET_KEYBOARD_ROUTE:
        return f"{action_kind_label(kind)}: {_enum_name(KeyboardRoute, action.argument)}"
    if kind is ActionKind.SET_MOUSE_ROUTE:
        return f"{action_kind_label(kind)}: {_enum_name(MouseRoute, action.argument)}"
    return f"{action_kind_label(kind)}: #{action.argument}"


def _enum_name(enum_type: type, value: int) -> str:
    try:
        return enum_type(value).name
    except ValueError:
        return f"0x{value:02X}"


@dataclass(frozen=True)
class MouseCapabilities:
    """Which mouse buttons this configurator is willing to offer.

    Protocol v1 tells the host that a mouse is attached, not how many buttons
    it has. Rather than offer Button 4 and 5 to every operator and let the
    device silently ignore the binding, the extra buttons appear only once the
    device has actually reported one through a capture event. Reconnecting a
    different mouse starts the observation over, which is what makes a binding
    on Button 4 show up as unavailable.
    """

    advertised: bool = False
    observed: frozenset[int] = frozenset()

    @classmethod
    def from_device_info(cls, info: object | None) -> MouseCapabilities:
        capabilities = getattr(info, "capabilities", None)
        if capabilities is None:
            return cls()
        return cls(advertised=bool(int(capabilities) & int(Capability.MOUSE_HID)))

    @property
    def buttons(self) -> tuple[int, ...]:
        """Every button that may be bound right now, lowest first."""
        if not self.advertised:
            return ()
        return tuple(sorted(set(STANDARD_MOUSE_BUTTONS) | self.observed))

    def observing(self, button: int) -> MouseCapabilities:
        """Record that the device reported ``button``."""
        if not 1 <= int(button) <= MAX_MOUSE_BUTTON:
            raise ValueError(f"mouse button {button} is outside the protocol range")
        return replace(self, observed=self.observed | {int(button)})

    def allows(self, trigger: Trigger) -> bool:
        """Can ``trigger`` be pressed on the hardware that is attached now?"""
        if trigger.kind != TriggerKind.MOUSE_BUTTON:
            return True
        return trigger.code in self.buttons


class BindingTableModel(QAbstractTableModel):
    """The bindings of exactly one profile, in the order they are stored."""

    TRIGGER, MODE, ACTION = range(3)

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self._profile: Profile | None = None

    def set_profile(self, profile: Profile | None) -> None:
        self.beginResetModel()
        self._profile = profile
        self.endResetModel()

    @property
    def profile(self) -> Profile | None:
        return self._profile

    def bindings(self) -> tuple[Binding, ...]:
        return () if self._profile is None else self._profile.bindings

    def binding_at(self, row: int) -> Binding | None:
        bindings = self.bindings()
        return bindings[row] if 0 <= row < len(bindings) else None

    def row_of(self, uuid: UUID) -> int:
        """The row carrying ``uuid``, or ``-1`` when this profile has none."""
        for row, binding in enumerate(self.bindings()):
            if binding.uuid == uuid:
                return row
        return -1

    # ------------------------------------------------------- Qt model API

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        if parent is not None and parent.isValid():
            return 0
        return len(self.bindings())

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        if parent is not None and parent.isValid():
            return 0
        return 3

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        binding = self.binding_at(index.row())
        if binding is None:
            return None
        if index.column() == self.TRIGGER:
            return trigger_label(binding.trigger)
        if index.column() == self.MODE:
            return binding.mode.name
        return action_label(binding.action, self._profile)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        return (self.tr("Trigger"), self.tr("Mode"), self.tr("Action"))[section]


__all__ = [
    "KEY_NAMES",
    "LEFT_ALT",
    "LEFT_CTRL",
    "LEFT_GUI",
    "LEFT_SHIFT",
    "MAX_MOUSE_BUTTON",
    "MODIFIER_BITS",
    "SELECTABLE_USAGES",
    "STANDARD_MOUSE_BUTTONS",
    "BindingTableModel",
    "MouseCapabilities",
    "action_label",
    "key_name",
    "modifier_label",
    "trigger_label",
]
