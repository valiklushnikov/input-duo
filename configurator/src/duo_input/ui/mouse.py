"""The Mouse page: the one job the device exists for, on its own screen.

Everything here is expressible on the Bindings page too. This page exists
because switching the mouse between two computers is what most operators come
to configure, and it asks the two questions that matter in order: *what do you
press* and *what should it do*. Nothing after the first question is offered
until it is answered.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSpinBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from duo_input.device.service import DeviceService
from duo_input.domain.models import Action, Binding, Profile, Trigger
from duo_input.generated.protocol import ActionKind, BindingMode, MouseRoute, TriggerKind
from duo_input.ui.models.binding_table import (
    MODIFIER_BITS,
    SELECTABLE_USAGES,
    MouseCapabilities,
    key_name,
    trigger_label,
)
from duo_input.ui.bindings import CaptureDialog
from duo_input.ui.models.project_session import (
    AddBinding,
    ProjectSession,
    RemoveBinding,
    UpdateBinding,
)
from duo_input.ui.theme import (
    ROLE_BANNER,
    ROLE_PRIMARY,
    SIGNAL_ERROR,
    SIGNAL_MUTED,
    SIGNAL_WARN,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    fact_form,
    field_label,
    page_header,
    set_role,
    set_signal,
)

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
    #: A mouse button the device just reported, so the shell can remember it.
    button_observed = Signal(int)

    def __init__(
        self,
        service: DeviceService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._session = ProjectSession.new()
        self._capabilities = MouseCapabilities()
        self._captured: Trigger | None = None
        self._updating = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        outer.setSpacing(SPACE_LG)
        outer.addWidget(
            page_header(
                self.tr("Mouse"),
                self.tr(
                    "Choose the key or mouse button that sends the pointer to PC1 or PC2."
                ),
                self,
            )
        )

        # The themed controls and page heading are taller than the stock Qt
        # widgets.  Keep the supported 1024 x 700 window useful by scrolling
        # this page's body instead of making the entire application taller.
        body = QWidget(self)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, SPACE_SM, 0)
        body_layout.setSpacing(SPACE_LG)
        body_layout.addWidget(self._build_editor())
        body_layout.addWidget(self._build_existing(), 1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self._refresh()

    # ---------------------------------------------------------------- layout

    def _build_editor(self) -> QWidget:
        box = QGroupBox(self.tr("Switch the mouse"), self)
        box.setMaximumWidth(820)
        layout = QVBoxLayout(box)
        layout.setSpacing(SPACE_MD)
        form = fact_form()

        self.trigger_kind = QComboBox(box)
        self.trigger_kind.setAccessibleName(self.tr("Trigger kind"))
        self.trigger_kind.addItem(self.tr("Choose..."), None)
        self.trigger_kind.addItem(self.tr("Keyboard key"), TriggerKind.KEYBOARD_USAGE)
        self.trigger_kind.addItem(self.tr("Mouse button"), TriggerKind.MOUSE_BUTTON)
        self.trigger_kind.addItem(self.tr("Consumer control"), TriggerKind.CONSUMER_USAGE)
        self.trigger_kind.currentIndexChanged.connect(self._on_trigger_edited)
        self.trigger_kind_label = field_label(self.tr("1. What do you press?"), box)
        form.addRow(self.trigger_kind_label, self.trigger_kind)

        self.key_combo = QComboBox(box)
        self.key_combo.setAccessibleName(self.tr("Keyboard key"))
        for usage in SELECTABLE_USAGES:
            self.key_combo.addItem(key_name(usage), usage)
        self.key_combo.currentIndexChanged.connect(self._on_trigger_edited)
        self.key_label = field_label(self.tr("Key:"), box)
        form.addRow(self.key_label, self.key_combo)
        self.consumer_usage = QSpinBox(box)
        self.consumer_usage.setRange(1, 0xFFFF)
        self.consumer_usage.setDisplayIntegerBase(16)
        self.consumer_usage.setPrefix("0x")
        self.consumer_usage.setAccessibleName(self.tr("Consumer usage"))
        self.consumer_usage.valueChanged.connect(self._on_trigger_edited)
        self.consumer_label = field_label(self.tr("Consumer usage:"), box)
        form.addRow(self.consumer_label, self.consumer_usage)

        self.mouse_combo = QComboBox(box)
        self.mouse_combo.setAccessibleName(self.tr("Mouse button"))
        self.mouse_combo.currentIndexChanged.connect(self._on_trigger_edited)
        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(SPACE_SM)
        button_row.addWidget(self.mouse_combo, 1)
        self.capture_button = QPushButton(self.tr("Detect button or key"), box)
        self.capture_button.setAccessibleName(
            self.tr("Detect a button or key on the mouse")
        )
        self.capture_button.clicked.connect(self.capture_mouse_button)
        button_row.addWidget(self.capture_button)
        button_holder = QWidget(box)
        button_holder.setLayout(button_row)
        self.mouse_label = field_label(self.tr("Button:"), box)
        form.addRow(self.mouse_label, button_holder)

        modifiers = QHBoxLayout()
        self.modifier_boxes: dict[str, QCheckBox] = {}
        for key, label, _bit in MODIFIER_BITS:
            check = QCheckBox(label, box)
            check.setAccessibleName(self.tr("{0} modifier").format(label))
            check.toggled.connect(self._on_trigger_edited)
            self.modifier_boxes[key] = check
            modifiers.addWidget(check)
        modifiers.addStretch(1)
        self.modifiers_holder = QWidget(box)
        self.modifiers_holder.setLayout(modifiers)
        self.modifiers_label = field_label(self.tr("Modifiers:"), box)
        form.addRow(self.modifiers_label, self.modifiers_holder)

        self.action_combo = QComboBox(box)
        self.action_combo.setAccessibleName(self.tr("What the trigger does"))
        self.action_combo.addItem(self.tr("Toggle between PC1 and PC2"), SWITCH_ACTIONS[0])
        self.action_combo.addItem(self.tr("Always PC1"), SWITCH_ACTIONS[1])
        self.action_combo.addItem(self.tr("Always PC2"), SWITCH_ACTIONS[2])
        self.action_combo.currentIndexChanged.connect(self._on_changed)
        form.addRow(field_label(self.tr("2. What should it do?"), box), self.action_combo)

        self.mode_combo = QComboBox(box)
        self.mode_combo.setAccessibleName(self.tr("Binding mode"))
        self.mode_combo.addItem(self.tr("Replace the key"), BindingMode.REPLACE)
        self.mode_combo.addItem(self.tr("Add to the key"), BindingMode.ADD)
        form.addRow(field_label(self.tr("Mode:"), box), self.mode_combo)
        layout.addLayout(form)

        self.warning_label = QLabel(box)
        self.warning_label.setAccessibleName(self.tr("Mouse switching warnings"))
        self.warning_label.setWordWrap(True)
        set_role(self.warning_label, ROLE_BANNER)
        set_signal(self.warning_label, SIGNAL_MUTED)
        layout.addWidget(self.warning_label)

        row = QHBoxLayout()
        self.apply_button = QPushButton(self.tr("Bind"), box)
        self.apply_button.setAccessibleName(self.tr("Bind this trigger to the mouse route"))
        set_role(self.apply_button, ROLE_PRIMARY)
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
        self.existing_list.setAlternatingRowColors(True)
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

    def pending_edit_command(self) -> object | None:
        """Nothing to commit: this page holds no free-text field.

        See ``BindingsPage.pending_edit_command`` - every editor page answers
        this so the shell can ask all of them before a device read repaints.
        """
        return None

    def set_session(self, session: ProjectSession) -> None:
        self._captured = None
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
        if self._captured is not None:
            return self._captured
        kind = self.trigger_kind.currentData()
        if kind is TriggerKind.CONSUMER_USAGE:
            return Trigger(kind, self.consumer_usage.value())
        if kind is TriggerKind.MOUSE_BUTTON:
            button = self.mouse_combo.currentData()
            if button is None:
                return None
            return self._qualify(Trigger(kind, int(button), 0))
        if kind is TriggerKind.KEYBOARD_USAGE:
            usage = self.key_combo.currentData()
            if usage is None:
                return None
            modifiers = sum(
                bit for key, _label, bit in MODIFIER_BITS if self.modifier_boxes[key].isChecked()
            )
            return self._qualify(Trigger(kind, int(usage), modifiers))
        return None

    def _qualify(self, trigger: Trigger) -> Trigger:
        """Name the device this trigger came from, when it is still that press.

        The source belongs to the capture, not to the editor: a trigger the
        operator then changed by hand is a different press, and carrying the
        old device onto it would qualify a key to hardware that never sent it.
        """
        captured = self._captured
        if captured is None or captured.source is None:
            return trigger
        if replace(captured, source=None) != trigger:
            return trigger
        return replace(trigger, source=captured.source)

    def current_binding(self) -> Binding | None:
        trigger = self.current_trigger()
        if trigger is None:
            return None
        return Binding(
            trigger=trigger,
            mode=self.mode_combo.currentData(),
            action=self.action_combo.currentData(),
        )

    def apply_captured_trigger(self, trigger: Trigger) -> None:
        """Select the key or button the device reported.

        A press the attached mouse produced belongs on this page whatever kind
        it is: the extra buttons of many mice are wired to keyboard usages, and
        discarding those is what used to hide them here.
        """
        if trigger.kind is TriggerKind.MOUSE_BUTTON:
            self._capabilities = self._capabilities.observing(trigger.code)
        elif trigger.kind not in (TriggerKind.KEYBOARD_USAGE, TriggerKind.CONSUMER_USAGE):
            return
        self._captured = trigger
        self._updating = True
        try:
            friendly_capture = self._shows_friendly_capture()
            self.select_trigger_kind(
                TriggerKind.MOUSE_BUTTON
                if friendly_capture
                else TriggerKind(trigger.kind)
            )
            if friendly_capture:
                if (
                    trigger.kind is TriggerKind.KEYBOARD_USAGE
                    and self.key_combo.findData(trigger.code) < 0
                ):
                    self.key_combo.addItem(key_name(trigger.code), trigger.code)
                self.key_combo.setCurrentIndex(-1)
                self.consumer_usage.setValue(self.consumer_usage.minimum())
                for box in self.modifier_boxes.values():
                    box.setChecked(False)
            elif trigger.kind is TriggerKind.CONSUMER_USAGE:
                self.consumer_usage.setValue(trigger.code)
            elif trigger.kind is TriggerKind.MOUSE_BUTTON:
                self._rebuild_mouse_buttons()
                self.mouse_combo.setCurrentIndex(
                    self.mouse_combo.findData(trigger.code)
                )
            else:
                if self.key_combo.findData(trigger.code) < 0:
                    self.key_combo.addItem(key_name(trigger.code), trigger.code)
                self.key_combo.setCurrentIndex(self.key_combo.findData(trigger.code))
                for key, _label, bit in MODIFIER_BITS:
                    self.modifier_boxes[key].setChecked(bool(trigger.modifiers & bit))
        finally:
            self._updating = False
        self._refresh()
        if trigger.kind is TriggerKind.MOUSE_BUTTON:
            # The shell owns the observations: a page-local memory of them
            # would be wiped by the next device operation, which re-reads what
            # is advertised.
            self.button_observed.emit(int(trigger.code))

    def accepts_capture(self, trigger: Trigger) -> bool:
        """Is this press one the mouse this page is about produced?

        A press that names no source is what firmware predating the source
        table sends; nothing can be asked of it beyond what was always asked,
        so a mouse button is taken and anything else is not. A press that does
        name a source is judged on that source alone, whatever its kind: a
        button on another device on U1's bus is no more this page's press than
        a key on that device is.
        """
        if trigger.source is None:
            return trigger.kind is TriggerKind.MOUSE_BUTTON
        return self._capabilities.is_mouse(trigger.source)

    def capture_mouse_button(self) -> CaptureDialog | None:
        """Open the ten-second capture dialog for this mouse's presses."""
        if (
            self._service is None
            or not self._service.is_connected
            or self.trigger_kind.currentData() is None
        ):
            return None
        dialog = CaptureDialog(
            self._service,
            self,
            accepts=self.accepts_capture,
            prompt=self.tr("Press the button or key on the mouse you want to use."),
        )
        dialog.accepted.connect(lambda: self._on_capture_accepted(dialog))
        dialog.open()
        dialog.start()
        return dialog

    def _on_capture_accepted(self, dialog: CaptureDialog) -> None:
        if dialog.trigger is not None:
            self.apply_captured_trigger(dialog.trigger)

    # -------------------------------------------------------------- painting

    def _rebuild_mouse_buttons(self) -> None:
        current = self.mouse_combo.currentData()
        self.mouse_combo.clear()
        for button in self._capabilities.buttons:
            self.mouse_combo.addItem(self._mouse_button_label(button), button)
        if self._shows_friendly_capture():
            self.mouse_combo.addItem(self.tr("Side button"), None)
            self.mouse_combo.setCurrentIndex(self.mouse_combo.count() - 1)
        elif current is not None:
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
                f"{self._trigger_label(binding.trigger)} -> {route} ({binding.mode.name})"
            )

    def _mouse_button_label(self, button: int) -> str:
        labels = {
            1: self.tr("Left button"),
            2: self.tr("Right button"),
            3: self.tr("Middle button (wheel)"),
            4: self.tr("Side button 1"),
            5: self.tr("Side button 2"),
        }
        return labels.get(int(button), self.tr("Button {0}").format(button))

    def _trigger_label(self, trigger: Trigger) -> str:
        if trigger.kind is TriggerKind.MOUSE_BUTTON:
            return self._mouse_button_label(trigger.code)
        if self._capabilities.is_mouse(trigger.source):
            return self.tr("Side button")
        return trigger_label(replace(trigger, source=None))

    def _shows_friendly_capture(self) -> bool:
        trigger = self._captured
        return (
            trigger is not None
            and trigger.kind in (
                TriggerKind.KEYBOARD_USAGE,
                TriggerKind.CONSUMER_USAGE,
            )
            and self._capabilities.is_mouse(trigger.source)
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
            friendly_capture = self._shows_friendly_capture()
            self.consumer_usage.setSpecialValueText("—" if friendly_capture else "")
            for key, label, _bit in MODIFIER_BITS:
                self.modifier_boxes[key].setText("" if friendly_capture else label)
            self.consumer_usage.setEnabled(kind is TriggerKind.CONSUMER_USAGE)
            self.key_combo.setEnabled(kind is TriggerKind.KEYBOARD_USAGE)
            self.mouse_combo.setEnabled(kind is TriggerKind.MOUSE_BUTTON)
            for box in self.modifier_boxes.values():
                box.setEnabled(kind is TriggerKind.KEYBOARD_USAGE)
            self.action_combo.setEnabled(kind is not None)
            self.mode_combo.setEnabled(kind is not None)
            # Not gated on the trigger kind: a press detected on the mouse
            # may arrive as a keyboard usage, and selecting that kind must not
            # be what takes the button away from the operator who then wants
            # to try a different key.
            self.capture_button.setEnabled(
                kind is not None
                and self._service is not None
                and self._service.is_connected
            )
            self._rebuild_mouse_buttons()
            self._rebuild_existing()
        finally:
            self._updating = False

        conflict = self._conflict()
        warning = self._warning()
        self.warning_label.setText(warning)
        if warning and conflict:
            set_signal(self.warning_label, SIGNAL_ERROR)
        else:
            set_signal(self.warning_label, SIGNAL_WARN if warning else SIGNAL_MUTED)
        self.apply_button.setEnabled(conflict == "" and self.current_binding() is not None)

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
                if not is_mouse_switch(binding):
                    return self.tr("{0} is already bound in this profile.").format(
                        self._trigger_label(trigger)
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
            names = ", ".join(
                self._mouse_button_label(button) for button in unavailable
            )
            return self.tr(
                "{0} is bound here but the attached mouse has not reported it."
            ).format(names)
        return ""

    # ----------------------------------------------------------------- slots

    def _on_changed(self, *_args: object) -> None:
        self._refresh()

    def _on_trigger_edited(self, *_args: object) -> None:
        if not self._updating:
            self._captured = None
        self._refresh()

    def _on_apply_clicked(self) -> None:
        binding = self.current_binding()
        if binding is None or self._conflict():
            return
        profile_id = self._session.project.active_profile_id
        previous = [item for item in self.profile.bindings if is_mouse_switch(item)]
        if not previous:
            self.command_requested.emit(AddBinding(profile_id, binding))
            return
        self.command_requested.emit(
            UpdateBinding(profile_id, previous[0].uuid, binding)
        )
        for obsolete in previous[1:]:
            self.command_requested.emit(RemoveBinding(profile_id, obsolete.uuid))


__all__ = ["SWITCH_ACTIONS", "MouseSwitchPage", "is_mouse_switch"]
