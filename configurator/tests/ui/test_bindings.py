"""The Bindings page: triggers, Replace/Add, actions, conflicts and capture."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from duo_input.device.emulator import U1Emulator
from duo_input.device.qt_transport import SynchronousTransportLink
from duo_input.device.service import DeviceService
from duo_input.device.transactions import PayloadError, parse_capture_event
from duo_input.domain.models import Action, Binding, Macro, Trigger
from duo_input.domain.text_compiler import compile_project_to_binary
from duo_input.generated.protocol import (
    ActionKind,
    BindingMode,
    KeyboardRoute,
    MouseRoute,
    TargetMode,
    TriggerKind,
)
from duo_input.ui.bindings import BindingsPage, CaptureDialog
from duo_input.ui.models.binding_table import (
    action_kind_hint,
    action_kind_label,
    LEFT_ALT,
    LEFT_CTRL,
    LEFT_SHIFT,
    STANDARD_MOUSE_BUTTONS,
    BindingTableModel,
    MouseCapabilities,
    action_label,
    trigger_label,
)
from duo_input.ui.models.project_session import (
    AddBinding,
    ProjectSession,
    SetActiveProfile,
    default_project,
)
from duo_input.ui import theme


def _binding(code: int = 0x04, *, modifiers: int = 0, kind=TriggerKind.KEYBOARD_USAGE) -> Binding:
    return Binding(
        trigger=Trigger(kind, code, modifiers),
        mode=BindingMode.REPLACE,
        action=Action(ActionKind.TOGGLE_KEYBOARD_ROUTE, 0),
    )


@pytest.fixture
def emulator() -> U1Emulator:
    emulator = U1Emulator()
    emulator.install_active(compile_project_to_binary(default_project()))
    return emulator


@pytest.fixture
def service(qtbot) -> DeviceService:
    return DeviceService(timeout_ms=5000)


@pytest.fixture
def page(qtbot, service) -> BindingsPage:
    page = BindingsPage(service)
    qtbot.addWidget(page)
    page.set_session(ProjectSession.new())
    return page


def _sync_capabilities(page: BindingsPage) -> None:
    """Hand the page the capabilities the way MainWindow does after an operation."""
    page.set_capabilities(
        MouseCapabilities.from_device_info(page.service.device_info)
        if page.service.is_connected
        else MouseCapabilities()
    )


# ------------------------------------------------------------------- labels


def test_a_keyboard_trigger_reads_as_its_modifiers_and_key():
    label = trigger_label(Trigger(TriggerKind.KEYBOARD_USAGE, 0x04, LEFT_CTRL | LEFT_SHIFT))

    assert label == "Ctrl+Shift+A"


def test_an_unnamed_usage_reads_as_its_number():
    label = trigger_label(Trigger(TriggerKind.KEYBOARD_USAGE, 0xF0, 0))

    assert label == "usage 0xF0"


def test_a_mouse_trigger_reads_as_its_button_number():
    label = trigger_label(Trigger(TriggerKind.MOUSE_BUTTON, 4, 0))

    assert label == "Button 4"


def test_a_run_macro_action_reads_as_the_macro_it_runs():
    profile = default_project().profiles[0]
    macro = Macro(id=3, name="Куркума", target=TargetMode.INHERIT, steps=())
    profile = type(profile)(
        id=profile.id,
        name=profile.name,
        color_rgb=profile.color_rgb,
        keyboard_route=profile.keyboard_route,
        mouse_route=profile.mouse_route,
        text_layout=profile.text_layout,
        bindings=(),
        macros=(macro,),
    )

    label = action_label(Action(ActionKind.RUN_MACRO, 3), profile)

    # The protocol name moved to the tooltip; the column says what it does.
    assert "RUN_MACRO" not in label
    assert "Куркума" in label
    assert action_kind_hint(ActionKind.RUN_MACRO).endswith("RUN_MACRO")


def test_a_route_action_names_the_computer_it_points_at():
    """The action is said in words; the route it targets keeps its own name.

    PC1 and PC2 are what the labels on the hardware say, so translating them
    would help nobody.
    """
    profile = default_project().profiles[0]

    label = action_label(Action(ActionKind.SET_MOUSE_ROUTE, MouseRoute.PC2), profile)

    assert "SET_MOUSE_ROUTE" not in label
    assert "PC2" in label


# -------------------------------------------------------------- table model


def test_the_table_lists_the_bindings_of_one_profile(qtbot):
    model = BindingTableModel()
    session = (
        ProjectSession.new()
        .apply(AddBinding(1, _binding(0x04)))
        .apply(AddBinding(1, _binding(0x05)))
        .apply(AddBinding(2, _binding(0x06)))
    )

    model.set_profile(session.project.profiles[0])

    assert model.rowCount() == 2
    assert model.columnCount() == 3
    assert model.index(0, 0).data() == "A"
    assert model.index(0, 1).data() == "REPLACE"
    assert model.index(0, 2).data() == action_kind_label(ActionKind.TOGGLE_KEYBOARD_ROUTE)


def test_the_table_finds_a_binding_by_its_uuid(qtbot):
    model = BindingTableModel()
    session = (
        ProjectSession.new()
        .apply(AddBinding(1, _binding(0x04)))
        .apply(AddBinding(1, _binding(0x05)))
    )
    profile = session.project.profiles[0]
    model.set_profile(profile)

    assert model.row_of(profile.bindings[1].uuid) == 1
    assert model.binding_at(1) == profile.bindings[1]


# ------------------------------------------------------- mouse capabilities


def test_no_mouse_means_no_mouse_buttons():
    assert MouseCapabilities().buttons == ()


def test_an_advertised_mouse_offers_only_the_standard_buttons():
    assert MouseCapabilities(advertised=True).buttons == STANDARD_MOUSE_BUTTONS


def test_a_button_the_device_reported_becomes_available():
    capabilities = MouseCapabilities(advertised=True).observing(4)

    assert capabilities.buttons == STANDARD_MOUSE_BUTTONS + (4,)


def test_an_observation_is_ignored_while_no_mouse_is_advertised():
    assert MouseCapabilities().observing(4).buttons == ()


# ----------------------------------------------------------- capture parsing


def test_a_capture_event_carries_one_trigger():
    trigger = parse_capture_event(bytes((TriggerKind.MOUSE_BUTTON, 4, 0)))

    assert trigger == Trigger(TriggerKind.MOUSE_BUTTON, 4, 0)


def test_a_capture_event_of_the_wrong_size_is_refused():
    with pytest.raises(PayloadError):
        parse_capture_event(b"\x01\x04")


def test_a_capture_event_with_an_unknown_kind_is_refused():
    with pytest.raises(PayloadError):
        parse_capture_event(bytes((0x7F, 4, 0)))


# ---------------------------------------------------------------- the editor


def test_the_editor_starts_on_a_keyboard_trigger(page):
    assert page.trigger_kind.currentData() is TriggerKind.KEYBOARD_USAGE
    assert page.key_combo.isVisibleTo(page) is True
    assert page.mouse_combo.isVisibleTo(page) is False


def test_choosing_a_mouse_trigger_swaps_the_key_chooser(page):
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)

    assert page.key_combo.isVisibleTo(page) is False
    assert page.mouse_combo.isVisibleTo(page) is True


def test_modifiers_apply_only_to_a_keyboard_trigger(page):
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)

    for box in page.modifier_boxes.values():
        assert box.isEnabled() is False


def test_adding_a_binding_with_a_modifier_asks_for_that_trigger(page, qtbot):
    page.select_trigger_kind(TriggerKind.KEYBOARD_USAGE)
    page.key_combo.setCurrentIndex(page.key_combo.findData(0x04))
    page.modifier_boxes["ctrl"].setChecked(True)
    page.modifier_boxes["alt"].setChecked(True)
    page.mode_combo.setCurrentIndex(page.mode_combo.findData(BindingMode.ADD))

    with qtbot.waitSignal(page.command_requested) as blocker:
        page.add_button.click()

    command = blocker.args[0]
    assert isinstance(command, AddBinding)
    assert command.profile_id == 1
    assert command.binding.trigger == Trigger(
        TriggerKind.KEYBOARD_USAGE, 0x04, LEFT_CTRL | LEFT_ALT
    )
    assert command.binding.mode is BindingMode.ADD


def test_a_mouse_binding_never_carries_modifiers(page, qtbot):
    page.set_capabilities(MouseCapabilities(advertised=True))
    page.modifier_boxes["ctrl"].setChecked(True)
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    page.mouse_combo.setCurrentIndex(page.mouse_combo.findData(2))

    with qtbot.waitSignal(page.command_requested) as blocker:
        page.add_button.click()

    assert blocker.args[0].binding.trigger == Trigger(TriggerKind.MOUSE_BUTTON, 2, 0)


def test_the_action_argument_lists_the_routes_of_the_chosen_action(page):
    page.select_action_kind(ActionKind.SET_KEYBOARD_ROUTE)

    arguments = [page.argument_combo.itemData(row) for row in range(page.argument_combo.count())]

    assert arguments == [int(route) for route in KeyboardRoute]


def test_a_toggle_action_takes_no_argument(page):
    page.select_action_kind(ActionKind.TOGGLE_MOUSE_ROUTE)

    assert page.argument_combo.isEnabled() is False

    page.select_action_kind(ActionKind.SET_PROFILE)

    assert page.argument_combo.isEnabled() is True


def test_run_macro_is_unavailable_while_the_profile_has_no_macros(page):
    page.select_action_kind(ActionKind.RUN_MACRO)

    assert page.add_button.isEnabled() is False
    assert page.conflict_label.text()


# ------------------------------------------------------------- mouse buttons


def test_button_four_is_offered_only_once_the_device_reports_it(page):
    page.set_capabilities(MouseCapabilities(advertised=True))
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    offered = [page.mouse_combo.itemData(row) for row in range(page.mouse_combo.count())]
    assert offered == list(STANDARD_MOUSE_BUTTONS)

    page.set_capabilities(MouseCapabilities(advertised=True).observing(4))

    offered = [page.mouse_combo.itemData(row) for row in range(page.mouse_combo.count())]
    assert offered == list(STANDARD_MOUSE_BUTTONS) + [4]


def test_without_a_mouse_no_mouse_binding_can_be_added(page):
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)

    assert page.mouse_combo.count() == 0
    assert page.add_button.isEnabled() is False
    assert page.conflict_label.text()


# ---------------------------------------------------------------- conflicts


def test_a_trigger_the_profile_already_uses_blocks_the_add(page):
    page.set_session(page.session.apply(AddBinding(1, _binding(0x04))))
    page.key_combo.setCurrentIndex(page.key_combo.findData(0x04))

    assert page.add_button.isEnabled() is False
    assert "A" in page.conflict_label.text()


def test_the_same_trigger_in_another_profile_is_not_a_conflict(page):
    page.set_session(page.session.apply(AddBinding(2, _binding(0x04))))
    page.key_combo.setCurrentIndex(page.key_combo.findData(0x04))

    assert page.add_button.isEnabled() is True
    assert page.conflict_label.text() == ""


def test_a_modifier_clears_the_conflict(page):
    page.set_session(page.session.apply(AddBinding(1, _binding(0x04))))
    page.key_combo.setCurrentIndex(page.key_combo.findData(0x04))
    assert page.add_button.isEnabled() is False

    page.modifier_boxes["shift"].setChecked(True)

    assert page.add_button.isEnabled() is True


def test_editing_a_binding_onto_its_own_trigger_is_not_a_conflict(page):
    page.set_session(page.session.apply(AddBinding(1, _binding(0x04))))
    page.select_binding_row(0)

    assert page.apply_button.isEnabled() is True
    assert page.conflict_label.text() == ""


# ------------------------------------------------------------------- editing


def test_selecting_a_row_loads_it_into_the_editor(page):
    page.set_session(page.session.apply(AddBinding(1, _binding(0x05, modifiers=LEFT_SHIFT))))

    page.select_binding_row(0)

    assert page.key_combo.currentData() == 0x05
    assert page.modifier_boxes["shift"].isChecked() is True


def test_apply_updates_the_selected_binding(page, qtbot):
    page.set_session(page.session.apply(AddBinding(1, _binding(0x05))))
    uuid = page.session.project.profiles[0].bindings[0].uuid
    page.select_binding_row(0)
    page.key_combo.setCurrentIndex(page.key_combo.findData(0x06))

    with qtbot.waitSignal(page.command_requested) as blocker:
        page.apply_button.click()

    command = blocker.args[0]
    assert command.uuid == uuid
    assert command.binding.trigger.code == 0x06


def test_remove_asks_to_drop_the_selected_binding(page, qtbot):
    page.set_session(page.session.apply(AddBinding(1, _binding(0x05))))
    uuid = page.session.project.profiles[0].bindings[0].uuid
    page.select_binding_row(0)

    with qtbot.waitSignal(page.command_requested) as blocker:
        page.remove_button.click()

    assert blocker.args[0].uuid == uuid


def test_apply_and_remove_need_a_selected_binding(page):
    assert page.apply_button.isEnabled() is False
    assert page.remove_button.isEnabled() is False


def test_the_page_follows_the_active_profile(page):
    session = page.session.apply(AddBinding(2, _binding(0x04)))

    page.set_session(session.apply(SetActiveProfile(2)))

    assert page.model.rowCount() == 1
    assert page.profile_id == 2


# ----------------------------------------------------------------- capturing


def test_the_capture_dialog_counts_down_from_ten(qtbot, service):
    dialog = CaptureDialog(service)
    qtbot.addWidget(dialog)

    assert dialog.remaining_seconds == 10
    dialog.tick()
    assert dialog.remaining_seconds == 9
    assert dialog.ring.remaining == 9


def test_the_capture_dialog_shows_its_countdown_as_a_ring(qtbot, service):
    dialog = CaptureDialog(service)
    qtbot.addWidget(dialog)

    assert dialog.ring.remaining == dialog.remaining_seconds

    dialog.tick()

    assert dialog.ring.remaining == dialog.remaining_seconds == 9


def test_the_capture_dialog_has_no_window_frame(qtbot, service):
    from PySide6.QtCore import Qt

    dialog = CaptureDialog(service)
    qtbot.addWidget(dialog)

    assert bool(dialog.windowFlags() & Qt.WindowType.FramelessWindowHint)


def test_the_capture_dialog_gives_up_when_the_countdown_ends(qtbot, service):
    dialog = CaptureDialog(service)
    qtbot.addWidget(dialog)

    for _ in range(10):
        dialog.tick()

    assert dialog.trigger is None
    assert dialog.isVisible() is False


def test_a_captured_event_becomes_the_trigger(qtbot, service, emulator):
    link = SynchronousTransportLink(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(link)
    dialog = CaptureDialog(service)
    qtbot.addWidget(dialog)

    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        dialog.start()
    assert emulator.capture_active is True
    assert emulator.queue_capture_event(bytes((TriggerKind.MOUSE_BUTTON, 4, 0)))

    with qtbot.waitSignal(service.capture_received, timeout=5000):
        link.poll()

    assert dialog.trigger == Trigger(TriggerKind.MOUSE_BUTTON, 4, 0)


def test_the_table_says_what_each_binding_does(page):
    """The list of bindings is the page's answer to "what is set up here?"."""
    binding = Binding(
        trigger=Trigger(TriggerKind.KEYBOARD_USAGE, 0x3F, 0),
        mode=BindingMode.REPLACE,
        action=Action(ActionKind.TOGGLE_KEYBOARD_ROUTE, 0),
    )
    page.set_session(page.session.apply(AddBinding(1, binding)))

    text = page.model.data(page.model.index(0, page.model.ACTION), Qt.ItemDataRole.DisplayRole)

    assert "TOGGLE_KEYBOARD_ROUTE" not in text
    assert "клав" in text.lower() or "keyboard" in text.lower()


def test_every_key_the_device_can_report_has_a_legend(page):
    """A binding read back from the device must not read as "usage 0x68".

    The device reports HID usages, and it will report ones nobody typed on
    this page - F13 upwards exist on real keyboards and are exactly what a
    switch box gets bound to, because nothing else uses them.
    """
    from duo_input.ui.models.binding_table import key_name

    assert key_name(0x68) == "F13"
    assert key_name(0x73) == "F24"
    assert key_name(0x46) == "PrintScreen"
    assert key_name(0x48) == "Pause"
    assert key_name(0x59) == "Num1"
    assert key_name(0x58) == "NumEnter"


def test_an_unknown_usage_still_reads_as_a_number(page):
    """Whatever is left must degrade to something, not crash or read as empty."""
    from duo_input.ui.models.binding_table import key_name

    assert "0x01" in key_name(0x01)


def test_the_action_chooser_says_what_an_action_does(page):
    """TOGGLE_KEYBOARD_ROUTE is what the protocol calls it, not what it means."""
    offered = [page.action_combo.itemText(row) for row in range(page.action_combo.count())]

    assert offered
    for text in offered:
        assert text.upper() != text, f"still a protocol identifier: {text}"
        assert "_" not in text, f"still a protocol identifier: {text}"


def test_each_action_keeps_its_protocol_name_within_reach(page):
    """The identifier still has to be findable: a screenshot has to be readable
    against the diagnostics and the documentation, which both use it."""
    for row in range(page.action_combo.count()):
        hint = page.action_combo.itemData(row, Qt.ItemDataRole.ToolTipRole)
        assert hint, f"row {row} carries no tooltip"
        kind = page.action_combo.itemData(row)
        assert kind.name in hint


def test_a_mouse_only_capture_ignores_keyboard_events(qtbot, service):
    dialog = CaptureDialog(service, accepted_kind=TriggerKind.MOUSE_BUTTON)
    qtbot.addWidget(dialog)
    dialog.open()

    service.capture_received.emit(
        bytes((TriggerKind.KEYBOARD_USAGE, 0x04, 0))
    )

    assert dialog.trigger is None
    assert dialog.isVisible() is True

    service.capture_received.emit(bytes((TriggerKind.MOUSE_BUTTON, 5, 0)))

    assert dialog.trigger == Trigger(TriggerKind.MOUSE_BUTTON, 5, 0)
    assert dialog.isVisible() is False


def test_a_mouse_only_capture_still_gives_up_on_time(qtbot, service):
    """An ignored keypress must not hand the operator a fresh ten seconds."""
    dialog = CaptureDialog(service, accepted_kind=TriggerKind.MOUSE_BUTTON)
    qtbot.addWidget(dialog)
    dialog.open()

    service.capture_received.emit(bytes((TriggerKind.KEYBOARD_USAGE, 0x04, 0)))

    assert dialog.remaining_seconds == 10

    for _ in range(10):
        dialog.tick()

    assert dialog.trigger is None
    assert dialog.isVisible() is False


def test_a_mouse_only_capture_rearms_after_a_keyboard_event(
    qtbot, service, emulator
):
    link = SynchronousTransportLink(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(link)
    dialog = CaptureDialog(service, accepted_kind=TriggerKind.MOUSE_BUTTON)
    qtbot.addWidget(dialog)

    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        dialog.start()
    assert emulator.queue_capture_event(
        bytes((TriggerKind.KEYBOARD_USAGE, 0x04, 0))
    )
    with qtbot.waitSignal(service.capture_received, timeout=5000):
        link.poll()

    assert dialog.trigger is None
    qtbot.waitUntil(lambda: emulator.capture_active, timeout=5000)
    assert emulator.capture_active is True

    assert emulator.queue_capture_event(
        bytes((TriggerKind.MOUSE_BUTTON, 4, 0))
    )
    with qtbot.waitSignal(service.capture_received, timeout=5000):
        link.poll()

    assert dialog.trigger == Trigger(TriggerKind.MOUSE_BUTTON, 4, 0)


def test_escape_stops_the_countdown_and_binds_nothing(qtbot, service):
    """Escape is a dismissal, and a dismissed dialog must stop listening.

    ``QDialog.reject()`` - which is what Escape reaches - delivers no close
    event, so a dialog that only stops its timer in ``closeEvent`` keeps
    counting down and keeps its capture slot connected after the operator
    has walked away from it.
    """
    from PySide6.QtCore import Qt

    dialog = CaptureDialog(service)
    qtbot.addWidget(dialog)
    dialog.open()
    dialog.start()
    assert dialog._timer.isActive() is True

    qtbot.keyClick(dialog, Qt.Key.Key_Escape)

    assert dialog._timer.isActive() is False
    assert dialog.trigger is None
    assert dialog.isVisible() is False


def test_cancel_stops_the_countdown(qtbot, service):
    """The only button the dialog offers must also end the capture."""
    from PySide6.QtWidgets import QDialogButtonBox

    dialog = CaptureDialog(service)
    qtbot.addWidget(dialog)
    dialog.open()
    dialog.start()
    assert dialog._timer.isActive() is True

    dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).click()

    assert dialog._timer.isActive() is False
    assert dialog.trigger is None
    assert dialog.isVisible() is False


def test_a_payload_after_a_dismissal_neither_binds_nor_rearms(
    qtbot, service, emulator, monkeypatch
):
    """A device still in capture mode must not reach a dismissed dialog.

    The device keeps its own ten-second window, so a press can arrive after
    the operator cancelled. A dialog still connected to ``capture_received``
    would take that press as the answer and rewrite the binding under edit -
    or, on the mouse-only path, arm the hardware again from a dialog nobody
    is looking at.
    """
    link = SynchronousTransportLink(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(link)
    dialog = CaptureDialog(service, accepted_kind=TriggerKind.MOUSE_BUTTON)
    qtbot.addWidget(dialog)
    dialog.open()
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        dialog.start()

    dialog.reject()

    rearms: list[int] = []
    monkeypatch.setattr(service, "begin_capture", lambda: rearms.append(1))
    service.capture_received.emit(bytes((TriggerKind.KEYBOARD_USAGE, 0x04, 0)))
    service.capture_received.emit(bytes((TriggerKind.MOUSE_BUTTON, 5, 0)))

    assert dialog.trigger is None
    assert rearms == []
    assert dialog._timer.isActive() is False


def test_capture_is_offered_only_while_the_device_is_connected(page, emulator, qtbot):
    assert page.capture_button.isEnabled() is False

    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.service.connect_device(emulator)
    _sync_capabilities(page)

    assert page.capture_button.isEnabled() is True


def test_a_captured_mouse_button_becomes_available_on_the_page(page, emulator, qtbot):
    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.service.connect_device(emulator)
    _sync_capabilities(page)

    page.apply_captured_trigger(Trigger(TriggerKind.MOUSE_BUTTON, 5, 0))

    assert page.trigger_kind.currentData() is TriggerKind.MOUSE_BUTTON
    assert page.mouse_combo.currentData() == 5
    assert 5 in page.capabilities.buttons


def test_every_control_carries_an_accessible_name(page):
    for widget in (
        page.table,
        page.trigger_kind,
        page.key_combo,
        page.mouse_combo,
        page.mode_combo,
        page.action_combo,
        page.argument_combo,
        page.add_button,
        page.apply_button,
        page.remove_button,
        page.capture_button,
    ):
        assert widget.accessibleName()


def test_the_page_uses_the_shared_visual_hierarchy(page):
    titles = [
        label.text()
        for label in page.findChildren(QLabel)
        if label.property("role") == theme.ROLE_PAGE_TITLE
    ]

    assert titles == ["Bindings"]
    assert page.conflict_label.property("role") == theme.ROLE_BANNER
    assert page.add_button.property("role") == theme.ROLE_PRIMARY
