"""The Macros page and the deliberately awkward test run.

A test run generates *real* keystrokes on a real computer, so it is hard to
start by accident: the operator has to name the target, tick a confirmation,
and the device has to already be holding exactly the project on screen -
otherwise the macro that runs would not be the macro being edited. STOP AND
RELEASE ALL is reachable the entire time, and losing the link closes the dialog
rather than leaving a key held down somewhere out of sight.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from duo_input.device.service import DeviceService, DeviceState
from duo_input.domain.models import Macro, MacroStep, Profile
from duo_input.domain.text_compiler import TextTooLong, UnsupportedCharacter, compile_text
from duo_input.generated.protocol import (
    MACRO_STEPS_PER_MACRO,
    MAX_DELAY_MS,
    PROFILES,
    TEXT_CHARACTERS_PER_STEP,
    KeyboardRoute,
    MacroStepType,
    MouseRouteCommand,
    TargetMode,
)
from duo_input.ui.models.binding_table import MODIFIER_BITS, SELECTABLE_USAGES, key_name
from duo_input.ui.models.macro_steps import (
    STEP_BUILDERS,
    MacroStepListModel,
    consumer_tap_step,
    delay_bounds,
    delay_step,
    key_down_step,
    key_tap_step,
    key_up_step,
    set_keyboard_route_step,
    set_mouse_route_step,
    set_profile_step,
    text_step,
)
from duo_input.ui.models.project_session import (
    AddMacro,
    ProjectSession,
    RemoveMacro,
    RenameMacro,
    SetMacroSteps,
    SetMacroTarget,
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
    fact_form,
    field_label,
    page_header,
    set_role,
    set_signal,
)

#: The route a profile keyboard follows, as the macro target it corresponds to.
_ROUTE_AS_TARGET = {
    KeyboardRoute.PC1: TargetMode.PC1,
    KeyboardRoute.PC2: TargetMode.PC2,
    KeyboardRoute.BOTH: TargetMode.BOTH,
}


def effective_target(profile: Profile, target: TargetMode) -> TargetMode:
    """Where a macro actually types: INHERIT follows the profile route."""
    if TargetMode(target) is not TargetMode.INHERIT:
        return TargetMode(target)
    return _ROUTE_AS_TARGET[KeyboardRoute(profile.keyboard_route)]


class MacrosPage(QWidget):
    """One profile's macros, their steps and the editor for one step."""

    command_requested = Signal(object)

    #: Editor pages, one per step shape.
    _EDITOR_FOR = {
        MacroStepType.KEY_TAP: 0,
        MacroStepType.KEY_DOWN: 0,
        MacroStepType.KEY_UP: 0,
        MacroStepType.CONSUMER_TAP: 1,
        MacroStepType.TEXT: 2,
        MacroStepType.DELAY: 3,
        MacroStepType.SET_KEYBOARD_ROUTE: 4,
        MacroStepType.SET_MOUSE_ROUTE: 5,
        MacroStepType.SET_PROFILE: 6,
    }

    def __init__(self, service: DeviceService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._session = ProjectSession.new()
        self._macro_row = -1
        self._step_row = -1
        self._updating = False

        # The two columns together need more room than the smallest supported
        # window, so they scroll rather than clip a control off the edge.
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        splitter = QSplitter(Qt.Orientation.Horizontal, scroll)
        splitter.addWidget(self._build_macro_column(splitter))
        splitter.addWidget(self._build_step_column(splitter))
        splitter.setStretchFactor(1, 1)
        scroll.setWidget(splitter)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        outer.setSpacing(SPACE_LG)
        outer.addWidget(
            page_header(
                self.tr("Macros"),
                self.tr("Build a sequence once, then run it from any assigned trigger."),
                self,
            )
        )
        outer.addWidget(scroll, 1)
        self._refresh()

    # ---------------------------------------------------------------- layout

    def _build_macro_column(self, parent: QWidget) -> QWidget:
        box = QGroupBox(self.tr("Macros"), parent)
        layout = QVBoxLayout(box)
        layout.setSpacing(SPACE_MD)

        box.setMaximumWidth(380)
        self.macro_list = QListWidget(box)
        self.macro_list.setAccessibleName(self.tr("Macros of the active profile"))
        self.macro_list.currentRowChanged.connect(self._on_macro_row_changed)
        layout.addWidget(self.macro_list, 1)

        form = fact_form()
        self.name_edit = QLineEdit(box)
        self.name_edit.setAccessibleName(self.tr("Macro name"))
        self.name_edit.setMaxLength(48)
        self.name_edit.editingFinished.connect(self._on_name_edited)
        form.addRow(field_label(self.tr("Name:"), box), self.name_edit)

        self.target_combo = QComboBox(box)
        self.target_combo.setAccessibleName(self.tr("Macro target"))
        for target, label in (
            (TargetMode.INHERIT, self.tr("Inherit from profile")),
            (TargetMode.PC1, "PC1"),
            (TargetMode.PC2, "PC2"),
            (TargetMode.BOTH, self.tr("Both")),
        ):
            self.target_combo.addItem(label, target)
        self.target_combo.currentIndexChanged.connect(self._on_target_changed)
        form.addRow(field_label(self.tr("Types on:"), box), self.target_combo)
        layout.addLayout(form)

        buttons = QVBoxLayout()
        buttons.setSpacing(SPACE_SM)
        self.add_macro_button = QPushButton(self.tr("New macro"), box)
        self.add_macro_button.setAccessibleName(self.tr("Add a macro to this profile"))
        self.add_macro_button.clicked.connect(self._on_add_macro_clicked)
        self.remove_macro_button = QPushButton(self.tr("Delete macro"), box)
        self.remove_macro_button.setAccessibleName(self.tr("Delete the selected macro"))
        self.remove_macro_button.clicked.connect(self._on_remove_macro_clicked)
        self.test_button = QPushButton(self.tr("Test run..."), box)
        self.test_button.setAccessibleName(self.tr("Run this macro on the device"))
        self.test_button.clicked.connect(self.test_macro)
        for button in (self.add_macro_button, self.remove_macro_button, self.test_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        return box

    def _build_step_column(self, parent: QWidget) -> QWidget:
        box = QGroupBox(self.tr("Steps"), parent)
        layout = QVBoxLayout(box)
        layout.setSpacing(SPACE_MD)

        self.steps = MacroStepListModel(self)
        self.step_list = QListView(box)
        self.step_list.setAccessibleName(self.tr("Steps of the selected macro"))
        self.step_list.setModel(self.steps)
        self.step_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.step_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.step_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.step_list.setMinimumWidth(220)
        self.step_list.selectionModel().currentRowChanged.connect(self._on_step_row_changed)
        layout.addWidget(self.step_list, 1)

        add_row = QHBoxLayout()
        move_row = QHBoxLayout()
        add_row.setSpacing(SPACE_SM)
        move_row.setSpacing(SPACE_SM)
        self.step_type_combo = QComboBox(box)
        self.step_type_combo.setAccessibleName(self.tr("Step type"))
        self.step_type_combo.setMaximumWidth(200)
        for kind in MacroStepType:
            self.step_type_combo.addItem(kind.name, kind)
        self.step_type_combo.currentIndexChanged.connect(self._on_step_type_changed)
        self.add_step_button = QPushButton(self.tr("Add step"), box)
        self.add_step_button.setAccessibleName(self.tr("Add a step of this type"))
        self.add_step_button.clicked.connect(self._on_add_step_clicked)
        self.remove_step_button = QPushButton(self.tr("Delete step"), box)
        self.remove_step_button.setAccessibleName(self.tr("Delete the selected step"))
        self.remove_step_button.clicked.connect(self._on_remove_step_clicked)
        self.move_up_button = QPushButton(self.tr("Up"), box)
        self.move_up_button.setAccessibleName(self.tr("Move the selected step earlier"))
        self.move_up_button.clicked.connect(lambda: self._move_step(-1))
        self.move_down_button = QPushButton(self.tr("Down"), box)
        self.move_down_button.setAccessibleName(self.tr("Move the selected step later"))
        self.move_down_button.clicked.connect(lambda: self._move_step(1))
        for widget in (self.step_type_combo, self.add_step_button, self.remove_step_button):
            add_row.addWidget(widget)
        add_row.addStretch(1)
        for widget in (self.move_up_button, self.move_down_button):
            move_row.addWidget(widget)
        move_row.addStretch(1)
        layout.addLayout(add_row)
        layout.addLayout(move_row)

        layout.addWidget(self._build_step_editors(box))

        self.step_issue_label = QLabel(box)
        self.step_issue_label.setAccessibleName(self.tr("Why this step cannot be stored"))
        self.step_issue_label.setWordWrap(True)
        set_role(self.step_issue_label, ROLE_BANNER)
        set_signal(self.step_issue_label, SIGNAL_MUTED)
        layout.addWidget(self.step_issue_label)

        self.apply_step_button = QPushButton(self.tr("Apply to step"), box)
        self.apply_step_button.setAccessibleName(self.tr("Store the edited step"))
        set_role(self.apply_step_button, ROLE_PRIMARY)
        self.apply_step_button.clicked.connect(self._on_apply_step_clicked)
        layout.addWidget(self.apply_step_button)
        return box

    def _build_step_editors(self, parent: QWidget) -> QWidget:
        self.editors = QStackedWidget(parent)

        # 0 - key steps
        keys = QWidget(self.editors)
        key_form = fact_form()
        keys.setLayout(key_form)
        self.key_combo = QComboBox(keys)
        self.key_combo.setAccessibleName(self.tr("Key"))
        for usage in SELECTABLE_USAGES:
            self.key_combo.addItem(key_name(usage), usage)
        self.key_combo.currentIndexChanged.connect(self._on_editor_changed)
        key_form.addRow(field_label(self.tr("Key:"), keys), self.key_combo)
        modifier_row = QHBoxLayout()
        self.modifier_boxes: dict[str, QCheckBox] = {}
        for key, label, _bit in MODIFIER_BITS:
            check = QCheckBox(label, keys)
            check.setAccessibleName(self.tr("{0} modifier").format(label))
            check.toggled.connect(self._on_editor_changed)
            self.modifier_boxes[key] = check
            modifier_row.addWidget(check)
        modifier_row.addStretch(1)
        holder = QWidget(keys)
        holder.setLayout(modifier_row)
        key_form.addRow(field_label(self.tr("Modifiers:"), keys), holder)
        self.editors.addWidget(keys)

        # 1 - consumer control
        consumer = QWidget(self.editors)
        consumer_form = fact_form()
        consumer.setLayout(consumer_form)
        self.consumer_usage = QSpinBox(consumer)
        self.consumer_usage.setAccessibleName(self.tr("Consumer usage"))
        self.consumer_usage.setRange(1, 0xFFFF)
        self.consumer_usage.valueChanged.connect(self._on_editor_changed)
        consumer_form.addRow(
            field_label(self.tr("Usage:"), consumer), self.consumer_usage
        )
        self.editors.addWidget(consumer)

        # 2 - text
        text = QWidget(self.editors)
        text_layout = QVBoxLayout(text)
        self.text_edit = QPlainTextEdit(text)
        self.text_edit.setAccessibleName(self.tr("Text to type"))
        self.text_edit.setMinimumWidth(220)
        self.text_edit.textChanged.connect(self._on_editor_changed)
        text_layout.addWidget(self.text_edit)
        self.editors.addWidget(text)

        # 3 - delay
        delay = QWidget(self.editors)
        delay_form = fact_form()
        delay.setLayout(delay_form)
        self.delay_minimum = QSpinBox(delay)
        self.delay_minimum.setAccessibleName(self.tr("Shortest delay"))
        self.delay_minimum.setRange(0, MAX_DELAY_MS)
        self.delay_minimum.valueChanged.connect(self._on_editor_changed)
        self.delay_maximum = QSpinBox(delay)
        self.delay_maximum.setAccessibleName(self.tr("Longest delay"))
        self.delay_maximum.setRange(0, MAX_DELAY_MS)
        self.delay_maximum.valueChanged.connect(self._on_editor_changed)
        delay_form.addRow(
            field_label(self.tr("From, ms:"), delay), self.delay_minimum
        )
        delay_form.addRow(
            field_label(self.tr("To, ms:"), delay), self.delay_maximum
        )
        self.editors.addWidget(delay)

        # 4 - keyboard route
        keyboard = QWidget(self.editors)
        keyboard_form = fact_form()
        keyboard.setLayout(keyboard_form)
        self.keyboard_route_combo = QComboBox(keyboard)
        self.keyboard_route_combo.setAccessibleName(self.tr("Keyboard route"))
        for route in KeyboardRoute:
            self.keyboard_route_combo.addItem(route.name, route)
        self.keyboard_route_combo.currentIndexChanged.connect(self._on_editor_changed)
        keyboard_form.addRow(
            field_label(self.tr("Route:"), keyboard), self.keyboard_route_combo
        )
        self.editors.addWidget(keyboard)

        # 5 - mouse route
        mouse = QWidget(self.editors)
        mouse_form = fact_form()
        mouse.setLayout(mouse_form)
        self.mouse_route_combo = QComboBox(mouse)
        self.mouse_route_combo.setAccessibleName(self.tr("Mouse route command"))
        for command in MouseRouteCommand:
            self.mouse_route_combo.addItem(command.name, command)
        self.mouse_route_combo.currentIndexChanged.connect(self._on_editor_changed)
        mouse_form.addRow(
            field_label(self.tr("Route:"), mouse), self.mouse_route_combo
        )
        self.editors.addWidget(mouse)

        # 6 - profile
        profile = QWidget(self.editors)
        profile_form = fact_form()
        profile.setLayout(profile_form)
        self.profile_combo = QComboBox(profile)
        self.profile_combo.setAccessibleName(self.tr("Profile to switch to"))
        for slot in range(1, PROFILES + 1):
            self.profile_combo.addItem(str(slot), slot)
        self.profile_combo.currentIndexChanged.connect(self._on_editor_changed)
        profile_form.addRow(
            field_label(self.tr("Profile:"), profile), self.profile_combo
        )
        self.editors.addWidget(profile)
        return self.editors

    # ----------------------------------------------------------------- state

    @property
    def service(self) -> DeviceService:
        return self._service

    @property
    def session(self) -> ProjectSession:
        return self._session

    @property
    def profile(self) -> Profile:
        return self._session.active_profile

    def macro(self) -> Macro | None:
        macros = self.profile.macros
        return macros[self._macro_row] if 0 <= self._macro_row < len(macros) else None

    def pending_edit_command(self) -> object | None:
        """The command a focus-out would send, for text still being typed.

        The macro name commits on ``editingFinished``, so until focus leaves
        the field the session knows nothing about it; see the same method on
        the Profiles page, including why the command is handed back rather
        than emitted. The step editor below is not this: what it holds is
        staged until "Apply to step" is pressed, and committing it here would
        store a step the operator never applied.
        """
        return self._pending_name_command()

    def set_session(self, session: ProjectSession) -> None:
        """Render ``session``, keeping the selected macro and step if they live."""
        self._session = session
        macro_row, step_row = self._macro_row, self._step_row
        self._rebuild_macros()
        macros = self.profile.macros
        self.select_macro_row(min(macro_row, len(macros) - 1) if macros else -1)
        if 0 <= step_row < self.steps.rowCount():
            self.select_step_row(step_row)
        self._refresh()

    def select_macro_row(self, row: int) -> None:
        self.macro_list.setCurrentRow(row)
        self._on_macro_row_changed(row)

    def select_step_row(self, row: int) -> None:
        if 0 <= row < self.steps.rowCount():
            self.step_list.setCurrentIndex(self.steps.index(row, 0))
        else:
            self.step_list.clearSelection()
            self._step_row = -1
            self._refresh()

    def select_step_type(self, kind: MacroStepType) -> None:
        self.step_type_combo.setCurrentIndex(self.step_type_combo.findData(kind))

    # -------------------------------------------------------------- painting

    def _rebuild_macros(self) -> None:
        self._updating = True
        try:
            self.macro_list.clear()
            for macro in self.profile.macros:
                self.macro_list.addItem(
                    f"#{macro.id} {macro.name} ({len(macro.steps)})"
                )
        finally:
            self._updating = False

    def _load_macro(self) -> None:
        macro = self.macro()
        self._updating = True
        try:
            self.name_edit.setText(macro.name if macro else "")
            self.name_edit.setEnabled(macro is not None)
            self.target_combo.setEnabled(macro is not None)
            if macro is not None:
                self.target_combo.setCurrentIndex(
                    self.target_combo.findData(TargetMode(macro.target))
                )
            self.steps.set_macro(macro)
        finally:
            self._updating = False

    def _load_step(self, step: MacroStep) -> None:
        kind = MacroStepType(step.type)
        self._updating = True
        try:
            self.select_step_type(kind)
            if kind is MacroStepType.KEY_TAP and len(step.payload) == 2:
                self._set_key(step.payload[1], step.payload[0])
            elif kind in (MacroStepType.KEY_DOWN, MacroStepType.KEY_UP) and step.payload:
                self._set_key(step.payload[0], 0)
            elif kind is MacroStepType.CONSUMER_TAP and len(step.payload) == 2:
                self.consumer_usage.setValue(int.from_bytes(step.payload, "little"))
            elif kind is MacroStepType.TEXT:
                self.text_edit.setPlainText(step.source_text or "")
            elif kind is MacroStepType.DELAY and len(step.payload) == 4:
                minimum, maximum = delay_bounds(step)
                self.delay_minimum.setValue(minimum)
                self.delay_maximum.setValue(maximum)
            elif kind is MacroStepType.SET_KEYBOARD_ROUTE and step.payload:
                self.keyboard_route_combo.setCurrentIndex(
                    self.keyboard_route_combo.findData(KeyboardRoute(step.payload[0]))
                )
            elif kind is MacroStepType.SET_MOUSE_ROUTE and step.payload:
                self.mouse_route_combo.setCurrentIndex(
                    self.mouse_route_combo.findData(MouseRouteCommand(step.payload[0]))
                )
            elif kind is MacroStepType.SET_PROFILE and step.payload:
                self.profile_combo.setCurrentIndex(
                    self.profile_combo.findData(step.payload[0])
                )
        finally:
            self._updating = False
        self._refresh()

    def _set_key(self, usage: int, modifiers: int) -> None:
        index = self.key_combo.findData(usage)
        if index >= 0:
            self.key_combo.setCurrentIndex(index)
        for key, _label, bit in MODIFIER_BITS:
            self.modifier_boxes[key].setChecked(bool(modifiers & bit))

    def _refresh(self) -> None:
        kind = self.step_type_combo.currentData()
        if kind is not None:
            self.editors.setCurrentIndex(self._EDITOR_FOR[kind])

        macro = self.macro()
        connected = self._service.is_connected and self._session.connected
        self.remove_macro_button.setEnabled(macro is not None)
        self.test_button.setEnabled(macro is not None and connected)
        self.add_macro_button.setEnabled(len(self.profile.macros) < 32)

        room = self.steps.rowCount() < MACRO_STEPS_PER_MACRO
        self.add_step_button.setEnabled(macro is not None and room)
        selected = 0 <= self._step_row < self.steps.rowCount()
        self.remove_step_button.setEnabled(selected)
        self.move_up_button.setEnabled(selected and self._step_row > 0)
        self.move_down_button.setEnabled(selected and self._step_row < self.steps.rowCount() - 1)

        issue = self._step_issue(room)
        self.step_issue_label.setText(issue)
        set_signal(self.step_issue_label, SIGNAL_ERROR if issue else SIGNAL_MUTED)
        self.apply_step_button.setEnabled(selected and not issue)

    def _step_issue(self, room: bool) -> str:
        if not room:
            return self.tr("This macro already holds {0} steps.").format(MACRO_STEPS_PER_MACRO)
        kind = self.step_type_combo.currentData()
        if kind is MacroStepType.DELAY and self.delay_minimum.value() > self.delay_maximum.value():
            return self.tr("The shortest delay cannot exceed the longest.")
        if kind is MacroStepType.TEXT:
            return self._text_issue()
        return ""

    def _text_issue(self) -> str:
        text = self.text_edit.toPlainText()
        try:
            compile_text(text, self.profile.text_layout)
        except TextTooLong:
            return self.tr("A text step holds at most {0} characters; this one has {1}.").format(
                TEXT_CHARACTERS_PER_STEP, len(text)
            )
        except UnsupportedCharacter as error:
            return self.tr(
                "{0!r} at position {1} cannot be typed on the {2} layout."
            ).format(error.char, error.index, error.layout.name)
        return ""

    def text_issue_index(self) -> int | None:
        """Index of the character the layout cannot type, if there is one."""
        try:
            compile_text(self.text_edit.toPlainText(), self.profile.text_layout)
        except UnsupportedCharacter as error:
            return error.index
        except TextTooLong:
            return TEXT_CHARACTERS_PER_STEP
        return None

    def reveal_text_issue(self) -> None:
        """Select the character the layout cannot type, so it can be fixed."""
        index = self.text_issue_index()
        if index is None:
            return
        cursor = self.text_edit.textCursor()
        cursor.setPosition(index)
        cursor.movePosition(
            QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor
        )
        self.text_edit.setTextCursor(cursor)
        self.text_edit.setFocus()

    # -------------------------------------------------------------- building

    def current_step(self) -> MacroStep | None:
        """The step the editor describes, or ``None`` when it describes none."""
        kind = self.step_type_combo.currentData()
        if kind is None:
            return None
        usage = self.key_combo.currentData() or 0x04
        modifiers = sum(
            bit for key, _label, bit in MODIFIER_BITS if self.modifier_boxes[key].isChecked()
        )
        try:
            if kind is MacroStepType.KEY_TAP:
                return key_tap_step(modifiers, usage)
            if kind is MacroStepType.KEY_DOWN:
                return key_down_step(usage)
            if kind is MacroStepType.KEY_UP:
                return key_up_step(usage)
            if kind is MacroStepType.CONSUMER_TAP:
                return consumer_tap_step(self.consumer_usage.value())
            if kind is MacroStepType.TEXT:
                return text_step(self.text_edit.toPlainText())
            if kind is MacroStepType.DELAY:
                return delay_step(self.delay_minimum.value(), self.delay_maximum.value())
            if kind is MacroStepType.SET_KEYBOARD_ROUTE:
                return set_keyboard_route_step(self.keyboard_route_combo.currentData())
            if kind is MacroStepType.SET_MOUSE_ROUTE:
                return set_mouse_route_step(self.mouse_route_combo.currentData())
            return set_profile_step(self.profile_combo.currentData())
        except ValueError:
            return None

    def _store_steps(self) -> None:
        macro = self.macro()
        if macro is None:
            return
        self.command_requested.emit(
            SetMacroSteps(self.profile.id, macro.uuid, self.steps.steps())
        )

    # ----------------------------------------------------------------- slots

    def _on_macro_row_changed(self, row: int) -> None:
        self._macro_row = row
        self._step_row = -1
        self._load_macro()
        self._refresh()

    def _on_step_row_changed(self, current, _previous=None) -> None:
        self._step_row = current.row() if current is not None and current.isValid() else -1
        step = self.steps.step_at(self._step_row)
        if step is not None and not self._updating:
            self._load_step(step)
        else:
            self._refresh()

    def _on_step_type_changed(self, _index: int) -> None:
        self._refresh()

    def _on_editor_changed(self, *_args: object) -> None:
        if not self._updating:
            self._refresh()

    def _on_name_edited(self) -> None:
        command = self._pending_name_command()
        if command is not None:
            self.command_requested.emit(command)

    def _pending_name_command(self) -> RenameMacro | None:
        macro = self.macro()
        if self._updating or macro is None or self.name_edit.text() == macro.name:
            return None
        return RenameMacro(self.profile.id, macro.uuid, self.name_edit.text())

    def _on_target_changed(self, _index: int) -> None:
        macro = self.macro()
        target = self.target_combo.currentData()
        if self._updating or macro is None or target is None or target == macro.target:
            return
        self.command_requested.emit(SetMacroTarget(self.profile.id, macro.uuid, target))

    def _on_add_macro_clicked(self) -> None:
        self.command_requested.emit(
            AddMacro(self.profile.id, self.tr("Macro {0}").format(len(self.profile.macros) + 1))
        )

    def _on_remove_macro_clicked(self) -> None:
        macro = self.macro()
        if macro is not None:
            self.command_requested.emit(RemoveMacro(self.profile.id, macro.uuid))

    def _on_add_step_clicked(self) -> None:
        kind = self.step_type_combo.currentData()
        if kind is None or self.macro() is None:
            return
        self.steps.insert_step(self.steps.rowCount(), STEP_BUILDERS[kind]())
        self._store_steps()

    def _on_remove_step_clicked(self) -> None:
        if 0 <= self._step_row < self.steps.rowCount():
            self.steps.remove_step(self._step_row)
            self._store_steps()

    def _move_step(self, offset: int) -> None:
        target = self._step_row + offset
        if not 0 <= target < self.steps.rowCount():
            return
        if self.steps.move_step(self._step_row, target):
            self._step_row = target
            self._store_steps()

    def _on_apply_step_clicked(self) -> None:
        step = self.current_step()
        if step is None or not 0 <= self._step_row < self.steps.rowCount():
            return
        self.steps.set_step(self._step_row, step)
        self._store_steps()

    def test_macro(self) -> TestMacroDialog | None:
        """Open the test dialog for the selected macro."""
        macro = self.macro()
        if macro is None or not self._service.is_connected:
            return None
        dialog = TestMacroDialog(self._service, self._session, self.profile, macro, self)
        dialog.open()
        return dialog


class TestMacroDialog(QDialog):
    """Runs one committed macro on the device, with a stop always in reach."""

    def __init__(
        self,
        service: DeviceService,
        session: ProjectSession,
        profile: Profile,
        macro: Macro,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._session = session
        self._profile = profile
        self._macro = macro
        self.warning = ""

        self.setWindowTitle(self.tr("Test run"))
        self.setModal(True)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        layout = QVBoxLayout(self)

        layout.addWidget(
            QLabel(
                self.tr(
                    "This runs macro #{0} on the device. It generates real key "
                    "presses on the computer it is routed to."
                ).format(macro.id),
                self,
            )
        )

        form = fact_form()
        self.target_combo = QComboBox(self)
        self.target_combo.setAccessibleName(self.tr("Target computer"))
        self.target_combo.addItem(self.tr("Choose..."), None)
        for target in (TargetMode.PC1, TargetMode.PC2, TargetMode.BOTH):
            self.target_combo.addItem(target.name, target)
        self.target_combo.currentIndexChanged.connect(self._refresh)
        form.addRow(field_label(self.tr("Runs on:"), self), self.target_combo)
        layout.addLayout(form)

        self.confirm_box = QCheckBox(
            self.tr("I understand this types on a real computer."), self
        )
        self.confirm_box.setAccessibleName(self.tr("Confirm the test run"))
        self.confirm_box.toggled.connect(self._refresh)
        layout.addWidget(self.confirm_box)

        self.blocked_label = QLabel(self)
        self.blocked_label.setAccessibleName(self.tr("Why this macro cannot be tested"))
        self.blocked_label.setWordWrap(True)
        layout.addWidget(self.blocked_label)

        row = QHBoxLayout()
        self.run_button = QPushButton(self.tr("Run"), self)
        self.run_button.setAccessibleName(self.tr("Run the macro now"))
        self.run_button.clicked.connect(self._on_run_clicked)
        self.stop_button = QPushButton("STOP AND RELEASE ALL", self)
        self.stop_button.setAccessibleName(self.tr("Stop everything and release every key"))
        self.stop_button.setStyleSheet("background-color: #C1121F; color: white; font-weight: bold;")
        self.stop_button.clicked.connect(self._on_stop_clicked)
        self.close_button = QPushButton(self.tr("Close"), self)
        self.close_button.clicked.connect(self.reject)
        row.addWidget(self.run_button)
        row.addWidget(self.stop_button)
        row.addStretch(1)
        row.addWidget(self.close_button)
        layout.addLayout(row)

        self._service.state_changed.connect(self._on_state_changed)
        self._refresh()
        motion.fade_in(self, motion.SCRIM)

    @property
    def session(self) -> ProjectSession:
        """The session this dialog was opened on. It is never edited."""
        return self._session

    def select_target(self, target: TargetMode) -> None:
        self.target_combo.setCurrentIndex(self.target_combo.findData(TargetMode(target)))

    def _blocked_reason(self) -> str:
        if not self._service.is_connected:
            return self.tr("The device is not connected.")
        if not self._session.device_matches:
            return self.tr(
                "The device is not holding this project. Write it first, so the "
                "macro that runs is the macro on screen."
            )
        chosen = self.target_combo.currentData()
        if chosen is None:
            return self.tr("Choose which computer the macro should type on.")
        actual = effective_target(self._profile, self._macro.target)
        if chosen is not actual:
            return self.tr(
                "This macro is configured to type on {0}. Change its target first."
            ).format(actual.name)
        if not self.confirm_box.isChecked():
            return self.tr("Confirm that you expect real key presses.")
        return ""

    def _refresh(self, *_args: object) -> None:
        reason = self._blocked_reason()
        self.blocked_label.setText(reason)
        self.run_button.setEnabled(not reason)
        # Stop is never disabled: it is the way out of a macro that misbehaves.
        self.stop_button.setEnabled(True)

    def _on_run_clicked(self) -> None:
        if self._blocked_reason():
            return
        self._service.test_macro(self._profile.id, self._macro.id)

    def _on_stop_clicked(self) -> None:
        self._service.stop_and_release_all()

    def _on_state_changed(self, state: DeviceState) -> None:
        if state is DeviceState.DISCONNECTED:
            self.warning = "device_disconnected"
            self.reject()
        else:
            self._refresh()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        # Leaving a test behind could leave a key held down on the far computer.
        if self._service.is_connected:
            self._service.stop_and_release_all()
        super().closeEvent(event)


__all__ = ["MacrosPage", "TestMacroDialog", "effective_target"]
