"""The Mouse page: the one job the device exists for, on its own screen.

Everything here is expressible on the Bindings page too. This page exists
because switching the mouse between two computers is what most operators come
to configure, and it asks the two questions that matter in order: *what do you
press* and *what should it do*. Nothing after the first question is offered
until it is answered.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from duo_input.domain.models import Action, Binding, Profile, Trigger
from duo_input.generated.protocol import ActionKind, BindingMode, MouseRoute, TriggerKind
from duo_input.ui.models.binding_table import (
    MODIFIER_BITS,
    SELECTABLE_USAGES,
    MouseCapabilities,
    key_name,
    trigger_label,
)
from duo_input.ui.models.project_session import AddBinding, ProjectSession

#: Every action this page can produce, in the order it offers them.
SWITCH_ACTIONS: tuple[Action, ...] = (
    Action(ActionKind.TOGGLE_MOUSE_ROUTE, 0),
    Action(ActionKind.SET_MOUSE_ROUTE, int(MouseRoute.PC1)),
    Action(ActionKind.SET_MOUSE_ROUTE, int(MouseRoute.PC2)),
)


def is_mouse_switch(binding: Binding) -> bool:
    """Does this binding decide which computer the mouse serves?"""
    return ActionKind(binding.action.kind) in (
        ActionKind.TOGGLE_MOUSE_ROUTE,
        ActionKind.SET_MOUSE_ROUTE,
    )


class MouseSwitchPage(QWidget):
    """Bind one trigger to Toggle, PC1 or PC2 for the mouse."""

    command_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session = ProjectSession.new()
        self._capabilities = MouseCapabilities()
        self._updating = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)
        outer.addWidget(self._build_editor())
        outer.addWidget(self._build_existing(), 1)
        self._refresh()

    # ---------------------------------------------------------------- layout

    def _build_editor(self) -> QWidget:
        box = QGroupBox(self.tr("Switch the mouse"), self)
        layout = QVBoxLayout(box)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.trigger_kind = QComboBox(box)
        self.trigger_kind.setAccessibleName(self.tr("Trigger kind"))
        self.trigger_kind.addItem(self.tr("Choose..."), None)
        self.trigger_kind.addItem(self.tr("Keyboard key"), TriggerKind.KEYBOARD_USAGE)
        self.trigger_kind.addItem(self.tr("Mouse button"), TriggerKind.MOUSE_BUTTON)
        self.trigger_kind.currentIndexChanged.connect(self._on_changed)
        form.addRow(QLabel(self.tr("1. What do you press?"), box), self.trigger_kind)

        self.key_combo = QComboBox(box)
        self.key_combo.setAccessibleName(self.tr("Keyboard key"))
        for usage in SELECTABLE_USAGES:
            self.key_combo.addItem(key_name(usage), usage)
        self.key_combo.currentIndexChanged.connect(self._on_changed)
        form.addRow(QLabel(self.tr("Key:"), box), self.key_combo)

        self.mouse_combo = QComboBox(box)
        self.mouse_combo.setAccessibleName(self.tr("Mouse button"))
        self.mouse_combo.currentIndexChanged.connect(self._on_changed)
        form.addRow(QLabel(self.tr("Button:"), box), self.mouse_combo)

        modifiers = QHBoxLayout()
        self.modifier_boxes: dict[str, QCheckBox] = {}
        for key, label, _bit in MODIFIER_BITS:
            check = QCheckBox(label, box)
            check.setAccessibleName(self.tr("{0} modifier").format(label))
            check.toggled.connect(self._on_changed)
            self.modifier_boxes[key] = check
            modifiers.addWidget(check)
        modifiers.addStretch(1)
        holder = QWidget(box)
        holder.setLayout(modifiers)
        form.addRow(QLabel(self.tr("Modifiers:"), box), holder)

        self.action_combo = QComboBox(box)
        self.action_combo.setAccessibleName(self.tr("What the trigger does"))
        self.action_combo.addItem(self.tr("Toggle between PC1 and PC2"), SWITCH_ACTIONS[0])
        self.action_combo.addItem(self.tr("Always PC1"), SWITCH_ACTIONS[1])
        self.action_combo.addItem(self.tr("Always PC2"), SWITCH_ACTIONS[2])
        self.action_combo.currentIndexChanged.connect(self._on_changed)
        form.addRow(QLabel(self.tr("2. What should it do?"), box), self.action_combo)

        self.mode_combo = QComboBox(box)
        self.mode_combo.setAccessibleName(self.tr("Binding mode"))
        self.mode_combo.addItem(self.tr("Replace the key"), BindingMode.REPLACE)
        self.mode_combo.addItem(self.tr("Add to the key"), BindingMode.ADD)
        form.addRow(QLabel(self.tr("Mode:"), box), self.mode_combo)
        layout.addLayout(form)

        self.warning_label = QLabel(box)
        self.warning_label.setAccessibleName(self.tr("Mouse switching warnings"))
        self.warning_label.setWordWrap(True)
        layout.addWidget(self.warning_label)

        row = QHBoxLayout()
        self.apply_button = QPushButton(self.tr("Bind"), box)
        self.apply_button.setAccessibleName(self.tr("Bind this trigger to the mouse route"))
        self.apply_button.clicked.connect(self._on_apply_clicked)
        row.addWidget(self.apply_button)
        row.addStretch(1)
        layout.addLayout(row)
        return box

    def _build_existing(self) -> QWidget:
        box = QGroupBox(self.tr("Mouse switching in this profile"), self)
        layout = QVBoxLayout(box)
        self.existing_list = QListWidget(box)
        self.existing_list.setAccessibleName(self.tr("Existing mouse switch bindings"))
        layout.addWidget(self.existing_list)
        return box

    # ----------------------------------------------------------------- state

    @property
    def session(self) -> ProjectSession:
        return self._session

    @property
    def capabilities(self) -> MouseCapabilities:
        return self._capabilities

    @property
    def profile(self) -> Profile:
        return self._session.active_profile

    def set_session(self, session: ProjectSession) -> None:
        self._session = session
        self._refresh()

    def set_capabilities(self, capabilities: MouseCapabilities) -> None:
        self._capabilities = capabilities
        self._refresh()

    def select_trigger_kind(self, kind: TriggerKind | None) -> None:
        self.trigger_kind.setCurrentIndex(self.trigger_kind.findData(kind))

    def select_action(self, kind: ActionKind, argument: int) -> None:
        # QComboBox.findData compares Qt variants, which does not reach the
        # dataclass __eq__ of an Action, so the rows are compared here instead.
        wanted = Action(ActionKind(kind), int(argument))
        for row in range(self.action_combo.count()):
            if self.action_combo.itemData(row) == wanted:
                self.action_combo.setCurrentIndex(row)
                return
        raise ValueError(f"{kind.name} {argument} is not a mouse switch action")

    def current_trigger(self) -> Trigger | None:
        kind = self.trigger_kind.currentData()
        if kind is TriggerKind.MOUSE_BUTTON:
            button = self.mouse_combo.currentData()
            return None if button is None else Trigger(kind, int(button), 0)
        if kind is TriggerKind.KEYBOARD_USAGE:
            usage = self.key_combo.currentData()
            if usage is None:
                return None
            modifiers = sum(
                bit for key, _label, bit in MODIFIER_BITS if self.modifier_boxes[key].isChecked()
            )
            return Trigger(kind, int(usage), modifiers)
        return None

    def current_binding(self) -> Binding | None:
        trigger = self.current_trigger()
        if trigger is None:
            return None
        return Binding(
            trigger=trigger,
            mode=self.mode_combo.currentData(),
            action=self.action_combo.currentData(),
        )

    # -------------------------------------------------------------- painting

    def _rebuild_mouse_buttons(self) -> None:
        current = self.mouse_combo.currentData()
        self.mouse_combo.clear()
        for button in self._capabilities.buttons:
            self.mouse_combo.addItem(self.tr("Button {0}").format(button), button)
        if current is not None:
            index = self.mouse_combo.findData(current)
            if index >= 0:
                self.mouse_combo.setCurrentIndex(index)

    def _rebuild_existing(self) -> None:
        self.existing_list.clear()
        for binding in self.profile.bindings:
            if not is_mouse_switch(binding):
                continue
            route = (
                self.tr("Toggle")
                if ActionKind(binding.action.kind) is ActionKind.TOGGLE_MOUSE_ROUTE
                else MouseRoute(binding.action.argument).name
            )
            self.existing_list.addItem(
                f"{trigger_label(binding.trigger)} -> {route} ({binding.mode.name})"
            )

    def _unavailable_buttons(self) -> tuple[int, ...]:
        """Buttons a switch binding needs that the attached mouse cannot press."""
        available = self._capabilities.buttons
        return tuple(
            sorted(
                {
                    binding.trigger.code
                    for binding in self.profile.bindings
                    if is_mouse_switch(binding)
                    and binding.trigger.kind == TriggerKind.MOUSE_BUTTON
                    and binding.trigger.code not in available
                }
            )
        )

    def _refresh(self) -> None:
        if self._updating:
            return
        self._updating = True
        try:
            kind = self.trigger_kind.currentData()
            self.key_combo.setEnabled(kind is TriggerKind.KEYBOARD_USAGE)
            self.mouse_combo.setEnabled(kind is TriggerKind.MOUSE_BUTTON)
            for box in self.modifier_boxes.values():
                box.setEnabled(kind is TriggerKind.KEYBOARD_USAGE)
            self.action_combo.setEnabled(kind is not None)
            self.mode_combo.setEnabled(kind is not None)
            self._rebuild_mouse_buttons()
            self._rebuild_existing()
        finally:
            self._updating = False

        warning = self._warning()
        self.warning_label.setText(warning)
        self.apply_button.setEnabled(self._conflict() == "" and self.current_binding() is not None)

    def _conflict(self) -> str:
        kind = self.trigger_kind.currentData()
        if kind is None:
            return self.tr("Choose what you press first.")
        if kind is TriggerKind.MOUSE_BUTTON and not self._capabilities.buttons:
            return self.tr("No mouse is attached, so no mouse button can be bound.")
        trigger = self.current_trigger()
        if trigger is None:
            return self.tr("Choose what you press first.")
        for binding in self.profile.bindings:
            if binding.trigger == trigger:
                return self.tr("{0} is already bound in this profile.").format(
                    trigger_label(trigger)
                )
        return ""

    def _warning(self) -> str:
        conflict = self._conflict()
        # "Choose one first" is guidance, not a warning; it would otherwise
        # shout at the operator before they have touched anything.
        if conflict and self.trigger_kind.currentData() is not None:
            return conflict
        unavailable = self._unavailable_buttons()
        if unavailable:
            names = ", ".join(f"Button {button}" for button in unavailable)
            return self.tr(
                "{0} is bound here but the attached mouse has not reported it."
            ).format(names)
        return ""

    # ----------------------------------------------------------------- slots

    def _on_changed(self, *_args: object) -> None:
        self._refresh()

    def _on_apply_clicked(self) -> None:
        binding = self.current_binding()
        if binding is None or self._conflict():
            return
        self.command_requested.emit(
            AddBinding(self._session.project.active_profile_id, binding)
        )


__all__ = ["SWITCH_ACTIONS", "MouseSwitchPage", "is_mouse_switch"]
