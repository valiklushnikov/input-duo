"""The Bindings page: what a trigger does inside one profile.

Like every editor here the page renders a session and asks for changes through
``command_requested``; it never edits a project itself. A trigger that the
profile already uses, an action that points at nothing, or a mouse button the
attached hardware cannot produce are all refused *before* the command is sent,
so the operator sees the reason next to the button instead of a failed write.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from duo_input.device.service import DeviceService
from duo_input.device.transactions import PayloadError, parse_capture_event
from duo_input.domain.models import Action, Binding, Profile, Trigger
from duo_input.generated.protocol import (
    ActionKind,
    BindingMode,
    KeyboardRoute,
    MouseRoute,
    TriggerKind,
)
from duo_input.ui.models.binding_table import (
    MODIFIER_BITS,
    SELECTABLE_USAGES,
    BindingTableModel,
    MouseCapabilities,
    action_kind_hint,
    action_kind_label,
    key_name,
    trigger_label,
)
from duo_input.ui.models.project_session import (
    AddBinding,
    ProjectSession,
    RemoveBinding,
    UpdateBinding,
)
from duo_input.ui import motion
from duo_input.ui.theme import (
    ROLE_BANNER,
    ROLE_PRIMARY,
    SIGNAL_ERROR,
    SIGNAL_MUTED,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    CountdownRing,
    fact_form,
    field_label,
    page_header,
    set_role,
    set_signal,
)

#: Seconds the device keeps capture mode open, mirrored from the design spec.
CAPTURE_SECONDS = 10

#: Actions whose argument is fixed at zero.
_ARGUMENTLESS = (ActionKind.TOGGLE_KEYBOARD_ROUTE, ActionKind.TOGGLE_MOUSE_ROUTE)

# The one action every profile can always perform: it needs no macro, no other
# profile and no route argument, so a fresh editor is usable straight away.
_DEFAULT_ACTION = ActionKind.TOGGLE_KEYBOARD_ROUTE


class CaptureDialog(QDialog):
    """Waits for the device to report the next key or button that is pressed.

    The device ends capture mode by itself after ten seconds, so the countdown
    here only decides how long the dialog keeps listening; it never has to tell
    the device to stop.
    """

    def __init__(
        self,
        service: DeviceService,
        parent: QWidget | None = None,
        *,
        accepted_kind: TriggerKind | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._accepted_kind = accepted_kind
        self._trigger: Trigger | None = None
        self._remaining = CAPTURE_SECONDS
        self._listening = False

        self.setWindowTitle(self.tr("Detect a key or button"))
        self.setModal(True)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        layout = QVBoxLayout(self)
        prompt = (
            self.tr("Press the mouse button you want to use.")
            if accepted_kind is TriggerKind.MOUSE_BUTTON
            else self.tr("Press the key or mouse button you want to bind.")
        )
        self.prompt_label = QLabel(prompt, self)
        self.prompt_label.setWordWrap(True)
        layout.addWidget(self.prompt_label)
        self.ring = CountdownRing(CAPTURE_SECONDS, self)
        layout.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignHCenter)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.tick)
        self._listen()
        self._refresh()
        motion.fade_in(self, motion.SCRIM)

    @property
    def trigger(self) -> Trigger | None:
        """The captured trigger, or ``None`` while nothing has arrived."""
        return self._trigger

    @property
    def remaining_seconds(self) -> int:
        return self._remaining

    def start(self) -> None:
        """Put the device into capture mode and start the countdown."""
        self._trigger = None
        self._remaining = CAPTURE_SECONDS
        self._refresh()
        self._listen()
        self._timer.start()
        motion.lift_in(self)
        self._service.begin_capture()

    def tick(self) -> None:
        """Advance the countdown by one second; give up when it runs out."""
        self._remaining -= 1
        self._refresh()
        if self._remaining <= 0:
            # ``reject`` stops the countdown through ``done``.
            self.reject()

    def _refresh(self) -> None:
        self.ring.set_remaining(max(self._remaining, 0))

    def _on_capture_received(self, payload: bytes) -> None:
        try:
            self._trigger = parse_capture_event(bytes(payload))
        except PayloadError:
            # A payload this host cannot read is not a trigger; keep waiting
            # rather than binding something the operator never pressed.
            return
        if self._accepted_kind is not None and self._trigger.kind is not self._accepted_kind:
            self._trigger = None
            # Capture mode ends after the first physical press.  Start another
            # window without resetting the visible ten-second countdown.
            if self._service.is_connected:
                self._service.begin_capture()
            return
        self.accept()

    def done(self, result: int) -> None:  # noqa: N802 - Qt override
        """Stop listening, whichever way the dialog is being dismissed.

        Every exit funnels through here - ``accept``, ``reject``, Escape,
        the Cancel button and ``close`` - which ``closeEvent`` does not:
        ``QDialog.reject()`` delivers no close event at all, so Escape and
        Cancel used to leave the countdown running and this dialog still
        connected to ``capture_received``. The device keeps its own capture
        window open for ten seconds either way, so the next press would have
        arrived at a dialog the operator had already dismissed and rewritten
        the binding under edit - or armed the hardware again, on the
        mouse-only path.
        """
        self._stop_listening()
        super().done(result)

    def _listen(self) -> None:
        """Hear the device's capture events; connecting twice would double them."""
        if self._listening:
            return
        self._service.capture_received.connect(self._on_capture_received)
        self._listening = True

    def _stop_listening(self) -> None:
        self._timer.stop()
        self._listening = False
        try:
            self._service.capture_received.disconnect(self._on_capture_received)
        except (RuntimeError, TypeError):
            # Already disconnected: a second dismissal, or a service that was
            # torn down first. Nothing left to do either way.
            pass


class BindingsPage(QWidget):
    """The bindings of the active profile, plus the editor that changes them."""

    command_requested = Signal(object)
    #: A mouse button the device just reported, so the shell can remember it.
    button_observed = Signal(int)

    def __init__(self, service: DeviceService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._session = ProjectSession.new()
        self._capabilities = MouseCapabilities()
        self._updating = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        outer.setSpacing(SPACE_LG)
        outer.addWidget(
            page_header(
                self.tr("Bindings"),
                self.tr("Choose what a key or mouse button does in this profile."),
                self,
            )
        )

        self.model = BindingTableModel(self)
        self.table = QTableView(self)
        self.table.setAccessibleName(self.tr("Bindings of the active profile"))
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        outer.addWidget(self.table, 1)

        outer.addWidget(self._build_editor())
        self._refresh()

    # ---------------------------------------------------------------- layout

    def _build_editor(self) -> QWidget:
        box = QGroupBox(self.tr("Binding"), self)
        box.setMaximumWidth(820)
        layout = QVBoxLayout(box)
        layout.setSpacing(SPACE_MD)

        form = fact_form()

        self.trigger_kind = QComboBox(box)
        self.trigger_kind.setAccessibleName(self.tr("Trigger kind"))
        self.trigger_kind.addItem(self.tr("Keyboard key"), TriggerKind.KEYBOARD_USAGE)
        self.trigger_kind.addItem(self.tr("Mouse button"), TriggerKind.MOUSE_BUTTON)
        self.trigger_kind.currentIndexChanged.connect(self._on_trigger_kind_changed)
        form.addRow(field_label(self.tr("Trigger:"), box), self.trigger_kind)

        self.key_combo = QComboBox(box)
        self.key_combo.setAccessibleName(self.tr("Keyboard key"))
        for usage in SELECTABLE_USAGES:
            self.key_combo.addItem(key_name(usage), usage)
        self.key_combo.currentIndexChanged.connect(self._on_editor_changed)
        self.key_label = field_label(self.tr("Key:"), box)
        form.addRow(self.key_label, self.key_combo)

        self.mouse_combo = QComboBox(box)
        self.mouse_combo.setAccessibleName(self.tr("Mouse button"))
        self.mouse_combo.currentIndexChanged.connect(self._on_editor_changed)
        self.mouse_label = field_label(self.tr("Button:"), box)
        form.addRow(self.mouse_label, self.mouse_combo)

        modifier_row = QHBoxLayout()
        self.modifier_boxes: dict[str, QCheckBox] = {}
        for key, label, _bit in MODIFIER_BITS:
            check = QCheckBox(label, box)
            check.setAccessibleName(self.tr("{0} modifier").format(label))
            check.toggled.connect(self._on_editor_changed)
            self.modifier_boxes[key] = check
            modifier_row.addWidget(check)
        modifier_row.addStretch(1)
        form.addRow(field_label(self.tr("Modifiers:"), box), self._wrap(modifier_row, box))

        self.mode_combo = QComboBox(box)
        self.mode_combo.setAccessibleName(self.tr("Binding mode"))
        self.mode_combo.addItem(self.tr("Replace"), BindingMode.REPLACE)
        self.mode_combo.addItem(self.tr("Add"), BindingMode.ADD)
        form.addRow(field_label(self.tr("Mode:"), box), self.mode_combo)

        self.action_combo = QComboBox(box)
        self.action_combo.setAccessibleName(self.tr("Action"))
        for kind in ActionKind:
            # The list says what the action does; the protocol name it carries
            # stays one hover away, because the diagnostics and the docs speak
            # in identifiers and a screenshot has to be readable against them.
            self.action_combo.addItem(action_kind_label(kind), kind)
            row = self.action_combo.count() - 1
            self.action_combo.setItemData(
                row, action_kind_hint(kind), Qt.ItemDataRole.ToolTipRole
            )
        self.action_combo.setCurrentIndex(self.action_combo.findData(_DEFAULT_ACTION))
        self.action_combo.currentIndexChanged.connect(self._on_action_kind_changed)
        form.addRow(field_label(self.tr("Action:"), box), self.action_combo)

        self.argument_combo = QComboBox(box)
        self.argument_combo.setAccessibleName(self.tr("Action target"))
        self.argument_combo.currentIndexChanged.connect(self._on_editor_changed)
        form.addRow(field_label(self.tr("Target:"), box), self.argument_combo)
        layout.addLayout(form)

        self.conflict_label = QLabel(box)
        self.conflict_label.setAccessibleName(self.tr("Why this binding cannot be used"))
        self.conflict_label.setWordWrap(True)
        set_role(self.conflict_label, ROLE_BANNER)
        set_signal(self.conflict_label, SIGNAL_MUTED)
        layout.addWidget(self.conflict_label)

        buttons = QHBoxLayout()
        self.capture_button = QPushButton(self.tr("Detect"), box)
        self.capture_button.setAccessibleName(self.tr("Detect the trigger on the device"))
        self.capture_button.clicked.connect(self.capture_trigger)
        self.add_button = QPushButton(self.tr("Add"), box)
        self.add_button.setAccessibleName(self.tr("Add this binding"))
        set_role(self.add_button, ROLE_PRIMARY)
        self.add_button.clicked.connect(self._on_add_clicked)
        self.apply_button = QPushButton(self.tr("Apply"), box)
        self.apply_button.setAccessibleName(self.tr("Apply the changes to the selected binding"))
        self.apply_button.clicked.connect(self._on_apply_clicked)
        self.remove_button = QPushButton(self.tr("Remove"), box)
        self.remove_button.setAccessibleName(self.tr("Remove the selected binding"))
        self.remove_button.clicked.connect(self._on_remove_clicked)
        buttons.setSpacing(SPACE_SM)
        for button in (self.capture_button, self.add_button, self.apply_button, self.remove_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return box

    @staticmethod
    def _wrap(layout, parent: QWidget) -> QWidget:
        holder = QWidget(parent)
        holder.setLayout(layout)
        return holder

    # ----------------------------------------------------------------- state

    @property
    def service(self) -> DeviceService:
        return self._service

    @property
    def session(self) -> ProjectSession:
        return self._session

    @property
    def capabilities(self) -> MouseCapabilities:
        return self._capabilities

    @property
    def profile_id(self) -> int:
        return self._session.project.active_profile_id

    @property
    def profile(self) -> Profile:
        return self._session.active_profile

    def commit_pending_edit(self) -> None:
        """Nothing to commit: this page holds no free-text field.

        Every editor page answers this, because the shell asks all of them
        before it lets a device read repaint anything. A page that grows a
        field committing on ``editingFinished`` has to answer it for real -
        an unanswered one loses whatever was being typed into it.
        """

    def set_session(self, session: ProjectSession) -> None:
        """Render ``session``; the selected binding stays selected if it lives."""
        selected = self.selected_binding()
        self._session = session
        self.model.set_profile(session.active_profile)
        if selected is not None:
            row = self.model.row_of(selected.uuid)
            if row >= 0:
                self.select_binding_row(row)
        self._refresh()

    def set_capabilities(self, capabilities: MouseCapabilities) -> None:
        self._capabilities = capabilities
        self._refresh()

    # -------------------------------------------------------------- editing

    def selected_binding(self) -> Binding | None:
        rows = self.table.selectionModel().selectedRows()
        return self.model.binding_at(rows[0].row()) if rows else None

    def select_binding_row(self, row: int) -> None:
        self.table.selectRow(row)

    def clear_selection(self) -> None:
        self.table.clearSelection()

    def select_trigger_kind(self, kind: TriggerKind) -> None:
        self.trigger_kind.setCurrentIndex(self.trigger_kind.findData(kind))

    def select_action_kind(self, kind: ActionKind) -> None:
        self.action_combo.setCurrentIndex(self.action_combo.findData(kind))

    def current_trigger(self) -> Trigger | None:
        """The trigger the editor describes, or ``None`` when it describes none."""
        kind = self.trigger_kind.currentData()
        if kind is TriggerKind.MOUSE_BUTTON:
            button = self.mouse_combo.currentData()
            if button is None:
                return None
            return Trigger(TriggerKind.MOUSE_BUTTON, int(button), 0)
        usage = self.key_combo.currentData()
        if usage is None:
            return None
        modifiers = sum(
            bit for key, _label, bit in MODIFIER_BITS if self.modifier_boxes[key].isChecked()
        )
        return Trigger(TriggerKind.KEYBOARD_USAGE, int(usage), modifiers)

    def current_binding(self) -> Binding | None:
        trigger = self.current_trigger()
        action = self.current_action()
        if trigger is None or action is None:
            return None
        return Binding(trigger=trigger, mode=self.mode_combo.currentData(), action=action)

    def current_action(self) -> Action | None:
        kind = self.action_combo.currentData()
        if kind is None:
            return None
        if kind in _ARGUMENTLESS:
            return Action(kind, 0)
        argument = self.argument_combo.currentData()
        if argument is None:
            return None
        return Action(kind, int(argument))

    def load_binding(self, binding: Binding) -> None:
        """Show ``binding`` in the editor without asking for any change."""
        self._updating = True
        try:
            self.select_trigger_kind(TriggerKind(binding.trigger.kind))
            if binding.trigger.kind == TriggerKind.MOUSE_BUTTON:
                self._rebuild_mouse_buttons(include=binding.trigger.code)
                self.mouse_combo.setCurrentIndex(self.mouse_combo.findData(binding.trigger.code))
            else:
                self.key_combo.setCurrentIndex(self.key_combo.findData(binding.trigger.code))
            for key, _label, bit in MODIFIER_BITS:
                self.modifier_boxes[key].setChecked(bool(binding.trigger.modifiers & bit))
            self.mode_combo.setCurrentIndex(self.mode_combo.findData(binding.mode))
            self.select_action_kind(ActionKind(binding.action.kind))
            self._rebuild_arguments()
            index = self.argument_combo.findData(int(binding.action.argument))
            if index >= 0:
                self.argument_combo.setCurrentIndex(index)
        finally:
            self._updating = False
        self._refresh()

    def apply_captured_trigger(self, trigger: Trigger) -> None:
        """Put a trigger the device reported into the editor."""
        if trigger.kind == TriggerKind.MOUSE_BUTTON:
            self._capabilities = self._capabilities.observing(trigger.code)
        self._updating = True
        try:
            self.select_trigger_kind(TriggerKind(trigger.kind))
            if trigger.kind == TriggerKind.MOUSE_BUTTON:
                self._rebuild_mouse_buttons()
                self.mouse_combo.setCurrentIndex(self.mouse_combo.findData(trigger.code))
            else:
                self.key_combo.setCurrentIndex(self.key_combo.findData(trigger.code))
                for key, _label, bit in MODIFIER_BITS:
                    self.modifier_boxes[key].setChecked(bool(trigger.modifiers & bit))
        finally:
            self._updating = False
        self._refresh()
        if trigger.kind == TriggerKind.MOUSE_BUTTON:
            self.button_observed.emit(int(trigger.code))

    def capture_trigger(self) -> CaptureDialog | None:
        """Open the capture dialog; returns it so callers can drive it in tests."""
        if not self._service.is_connected:
            return None
        dialog = CaptureDialog(self._service, self)
        dialog.accepted.connect(lambda: self._on_capture_accepted(dialog))
        dialog.open()
        dialog.start()
        return dialog

    def _on_capture_accepted(self, dialog: CaptureDialog) -> None:
        if dialog.trigger is not None:
            self.apply_captured_trigger(dialog.trigger)

    # -------------------------------------------------------------- painting

    def _rebuild_mouse_buttons(self, include: int | None = None) -> None:
        wanted = list(self._capabilities.buttons)
        if include is not None and include not in wanted:
            wanted.append(int(include))
            wanted.sort()
        current = self.mouse_combo.currentData()
        self.mouse_combo.clear()
        for button in wanted:
            self.mouse_combo.addItem(self.tr("Button {0}").format(button), button)
        if current is not None:
            index = self.mouse_combo.findData(current)
            if index >= 0:
                self.mouse_combo.setCurrentIndex(index)

    def _rebuild_arguments(self) -> None:
        kind = self.action_combo.currentData()
        current = self.argument_combo.currentData()
        self.argument_combo.clear()
        if kind is ActionKind.RUN_MACRO:
            for macro in self.profile.macros:
                self.argument_combo.addItem(f"#{macro.id} {macro.name}", macro.id)
        elif kind is ActionKind.SET_KEYBOARD_ROUTE:
            for route in KeyboardRoute:
                self.argument_combo.addItem(route.name, int(route))
        elif kind is ActionKind.SET_MOUSE_ROUTE:
            for route in MouseRoute:
                self.argument_combo.addItem(route.name, int(route))
        elif kind is ActionKind.SET_PROFILE:
            for profile in self._session.project.profiles:
                self.argument_combo.addItem(f"{profile.id} - {profile.name}", profile.id)
        if current is not None:
            index = self.argument_combo.findData(current)
            if index >= 0:
                self.argument_combo.setCurrentIndex(index)
        self.argument_combo.setEnabled(kind not in _ARGUMENTLESS)

    def _refresh(self) -> None:
        if self._updating:
            return
        self._updating = True
        try:
            keyboard = self.trigger_kind.currentData() is TriggerKind.KEYBOARD_USAGE
            self.key_combo.setVisible(keyboard)
            self.key_label.setVisible(keyboard)
            self.mouse_combo.setVisible(not keyboard)
            self.mouse_label.setVisible(not keyboard)
            for box in self.modifier_boxes.values():
                box.setEnabled(keyboard)
            if not keyboard:
                self._rebuild_mouse_buttons()
            self._rebuild_arguments()
        finally:
            self._updating = False

        selected = self.selected_binding()
        reason = self._reason_this_cannot_be_used(selected)
        self.conflict_label.setText(reason)
        set_signal(self.conflict_label, SIGNAL_ERROR if reason else SIGNAL_MUTED)
        usable = not reason and self.current_binding() is not None
        self.add_button.setEnabled(usable)
        self.apply_button.setEnabled(usable and selected is not None)
        self.remove_button.setEnabled(selected is not None)
        self.capture_button.setEnabled(self._service.is_connected)

    def _reason_this_cannot_be_used(self, selected: Binding | None) -> str:
        kind = self.trigger_kind.currentData()
        if kind is TriggerKind.MOUSE_BUTTON and not self._capabilities.buttons:
            return self.tr("No mouse is attached, so no mouse button can be bound.")
        trigger = self.current_trigger()
        if trigger is None:
            return self.tr("Choose a trigger first.")
        action_kind = self.action_combo.currentData()
        if action_kind is ActionKind.RUN_MACRO and not self.profile.macros:
            return self.tr("This profile has no macros to run.")
        if self.current_action() is None:
            return self.tr("Choose what the trigger should do.")
        for binding in self.profile.bindings:
            if selected is not None and binding.uuid == selected.uuid:
                continue
            if binding.trigger == trigger:
                return self.tr("{0} is already bound in this profile.").format(
                    trigger_label(trigger)
                )
        return ""

    # ----------------------------------------------------------------- slots

    def _on_trigger_kind_changed(self, _index: int) -> None:
        self._refresh()

    def _on_action_kind_changed(self, _index: int) -> None:
        self._refresh()

    def _on_editor_changed(self, *_args: object) -> None:
        self._refresh()

    def _on_selection_changed(self, *_args: object) -> None:
        binding = self.selected_binding()
        if binding is not None:
            self.load_binding(binding)
        else:
            self._refresh()

    def _on_add_clicked(self) -> None:
        binding = self.current_binding()
        if binding is not None:
            self.command_requested.emit(AddBinding(self.profile_id, binding))

    def _on_apply_clicked(self) -> None:
        selected = self.selected_binding()
        binding = self.current_binding()
        if selected is not None and binding is not None:
            self.command_requested.emit(
                UpdateBinding(self.profile_id, selected.uuid, binding)
            )

    def _on_remove_clicked(self) -> None:
        selected = self.selected_binding()
        if selected is not None:
            self.command_requested.emit(RemoveBinding(self.profile_id, selected.uuid))


__all__ = ["CAPTURE_SECONDS", "BindingsPage", "CaptureDialog"]
