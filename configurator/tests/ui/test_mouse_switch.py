"""The Mouse page: pick a trigger, then say which computer the mouse serves."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel

from duo_input.device.emulator import U1Emulator
from duo_input.device.qt_transport import SynchronousTransportLink
from duo_input.device.service import DeviceService
from duo_input.domain.models import Action, Binding, Trigger
from duo_input.generated.protocol import (
    ActionKind,
    BindingMode,
    MouseRoute,
    TriggerKind,
)
from duo_input.ui.models.binding_table import (
    LEFT_CTRL,
    STANDARD_MOUSE_BUTTONS,
    MouseCapabilities,
)
from duo_input.ui.bindings import CaptureDialog
from duo_input.ui.models.project_session import (
    AddBinding,
    ProjectSession,
    UpdateBinding,
)
from duo_input.ui.mouse import MouseSwitchPage
from duo_input.ui import theme


def _switch(code: int, *, kind=TriggerKind.MOUSE_BUTTON, action=None) -> Binding:
    return Binding(
        trigger=Trigger(kind, code, 0),
        mode=BindingMode.REPLACE,
        action=action or Action(ActionKind.TOGGLE_MOUSE_ROUTE, 0),
    )


@pytest.fixture
def page(qtbot) -> MouseSwitchPage:
    page = MouseSwitchPage()
    qtbot.addWidget(page)
    page.set_session(ProjectSession.new())
    page.set_capabilities(MouseCapabilities(advertised=True))
    return page


# ------------------------------------------------------------ the trigger first


def test_nothing_can_be_bound_before_a_trigger_kind_is_chosen(page):
    assert page.trigger_kind.currentData() is None
    assert page.key_combo.isEnabled() is False
    assert page.mouse_combo.isEnabled() is False
    assert page.action_combo.isEnabled() is False
    assert page.mode_combo.isEnabled() is False
    assert page.apply_button.isEnabled() is False


def test_choosing_a_keyboard_trigger_opens_the_rest_of_the_page(page):
    page.select_trigger_kind(TriggerKind.KEYBOARD_USAGE)

    assert page.key_combo.isEnabled() is True
    assert page.mouse_combo.isEnabled() is False
    assert page.action_combo.isEnabled() is True
    assert page.mode_combo.isEnabled() is True


def test_choosing_a_mouse_trigger_opens_the_button_chooser(page):
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)

    assert page.key_combo.isEnabled() is False
    assert page.mouse_combo.isEnabled() is True
    assert [page.mouse_combo.itemData(row) for row in range(page.mouse_combo.count())] == list(
        STANDARD_MOUSE_BUTTONS
    )


# --------------------------------------------------------------------- actions


def test_toggle_asks_for_the_toggle_action(page, qtbot):
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    page.mouse_combo.setCurrentIndex(page.mouse_combo.findData(3))
    page.select_action(ActionKind.TOGGLE_MOUSE_ROUTE, 0)

    with qtbot.waitSignal(page.command_requested) as blocker:
        page.apply_button.click()

    command = blocker.args[0]
    assert isinstance(command, AddBinding)
    assert command.binding.trigger == Trigger(TriggerKind.MOUSE_BUTTON, 3, 0)
    assert command.binding.action == Action(ActionKind.TOGGLE_MOUSE_ROUTE, 0)


def test_pc2_asks_for_a_fixed_route(page, qtbot):
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    page.mouse_combo.setCurrentIndex(page.mouse_combo.findData(2))
    page.select_action(ActionKind.SET_MOUSE_ROUTE, MouseRoute.PC2)

    with qtbot.waitSignal(page.command_requested) as blocker:
        page.apply_button.click()

    assert blocker.args[0].binding.action == Action(ActionKind.SET_MOUSE_ROUTE, MouseRoute.PC2)


def test_the_page_offers_exactly_toggle_pc1_and_pc2(page):
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)

    offered = [page.action_combo.itemData(row) for row in range(page.action_combo.count())]

    assert offered == [
        Action(ActionKind.TOGGLE_MOUSE_ROUTE, 0),
        Action(ActionKind.SET_MOUSE_ROUTE, int(MouseRoute.PC1)),
        Action(ActionKind.SET_MOUSE_ROUTE, int(MouseRoute.PC2)),
    ]


def test_add_mode_reaches_the_command(page, qtbot):
    page.select_trigger_kind(TriggerKind.KEYBOARD_USAGE)
    page.key_combo.setCurrentIndex(page.key_combo.findData(0x3E))
    page.mode_combo.setCurrentIndex(page.mode_combo.findData(BindingMode.ADD))

    with qtbot.waitSignal(page.command_requested) as blocker:
        page.apply_button.click()

    assert blocker.args[0].binding.mode is BindingMode.ADD
    assert blocker.args[0].binding.trigger.code == 0x3E


def test_a_keyboard_trigger_may_carry_modifiers(page, qtbot):
    page.select_trigger_kind(TriggerKind.KEYBOARD_USAGE)
    page.key_combo.setCurrentIndex(page.key_combo.findData(0x3E))
    page.modifier_boxes["ctrl"].setChecked(True)

    with qtbot.waitSignal(page.command_requested) as blocker:
        page.apply_button.click()

    assert blocker.args[0].binding.trigger.modifiers == LEFT_CTRL


# ---------------------------------------------------------------- capabilities


def test_without_a_mouse_no_mouse_trigger_can_be_used(qtbot):
    page = MouseSwitchPage()
    qtbot.addWidget(page)
    page.set_session(ProjectSession.new())

    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)

    assert page.mouse_combo.count() == 0
    assert page.apply_button.isEnabled() is False
    assert page.warning_label.text()


def test_button_four_appears_only_after_the_device_reports_it(page):
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    assert page.mouse_combo.findData(4) < 0

    page.set_capabilities(MouseCapabilities(advertised=True).observing(4))

    assert page.mouse_combo.findData(4) >= 0


def test_mouse_buttons_have_names_a_person_can_recognise(page):
    page.set_capabilities(
        MouseCapabilities(advertised=True).observing(4).observing(5)
    )
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)

    assert [
        page.mouse_combo.itemText(row) for row in range(page.mouse_combo.count())
    ] == [
        "Left button",
        "Right button",
        "Middle button (wheel)",
        "Side button 1",
        "Side button 2",
    ]


# -------------------------------------------------------------------- capture


def test_detecting_a_button_needs_the_device_and_mouse_trigger(qtbot):
    service = DeviceService(timeout_ms=5000)
    page = MouseSwitchPage(service)
    qtbot.addWidget(page)

    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    assert page.capture_button.isEnabled() is False

    emulator = U1Emulator()
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(SynchronousTransportLink(emulator))
    page.set_capabilities(MouseCapabilities(advertised=True))
    assert page.capture_button.isEnabled() is True

    page.select_trigger_kind(TriggerKind.KEYBOARD_USAGE)
    assert page.capture_button.isEnabled() is False


def test_a_detected_side_button_is_selected(qtbot):
    service = DeviceService(timeout_ms=5000)
    emulator = U1Emulator()
    link = SynchronousTransportLink(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(link)
    page = MouseSwitchPage(service)
    qtbot.addWidget(page)
    page.set_capabilities(MouseCapabilities(advertised=True))
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)

    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        dialog = page.capture_mouse_button()
    assert isinstance(dialog, CaptureDialog)
    assert emulator.queue_capture_event(
        bytes((TriggerKind.MOUSE_BUTTON, 5, 0))
    )

    with qtbot.waitSignal(service.capture_received, timeout=5000):
        link.poll()

    assert page.mouse_combo.currentData() == 5
    assert page.mouse_combo.currentText() == "Side button 2"


def test_binding_a_new_button_replaces_the_old_mouse_switch(page):
    old = _switch(4)
    page.set_session(page.session.apply(AddBinding(1, old)))
    page.apply_captured_trigger(Trigger(TriggerKind.MOUSE_BUTTON, 5, 0))
    commands = []
    page.command_requested.connect(commands.append)

    page.apply_button.click()

    assert len(commands) == 1
    assert isinstance(commands[0], UpdateBinding)
    assert commands[0].uuid == old.uuid
    assert commands[0].binding.trigger == Trigger(
        TriggerKind.MOUSE_BUTTON, 5, 0
    )


# ------------------------------------------------------ existing switch bindings


def test_the_page_lists_the_switch_bindings_of_the_active_profile(page):
    page.set_session(page.session.apply(AddBinding(1, _switch(3))))

    assert page.existing_list.count() == 1
    assert "Middle button (wheel)" in page.existing_list.item(0).text()


def test_a_binding_that_is_not_about_the_mouse_route_is_not_listed(page):
    other = Binding(
        trigger=Trigger(TriggerKind.KEYBOARD_USAGE, 0x04, 0),
        mode=BindingMode.REPLACE,
        action=Action(ActionKind.TOGGLE_KEYBOARD_ROUTE, 0),
    )
    page.set_session(page.session.apply(AddBinding(1, other)))

    assert page.existing_list.count() == 0


def test_a_button_the_current_mouse_lacks_is_reported_as_unavailable(page):
    page.set_session(page.session.apply(AddBinding(1, _switch(4))))

    assert "Side button 1" in page.warning_label.text()


def test_the_warning_clears_once_the_device_reports_that_button(page):
    page.set_session(page.session.apply(AddBinding(1, _switch(4))))
    assert page.warning_label.text()

    page.set_capabilities(MouseCapabilities(advertised=True).observing(4))

    assert page.warning_label.text() == ""


def test_a_keyboard_switch_binding_is_never_reported_as_unavailable(page):
    page.set_session(
        page.session.apply(AddBinding(1, _switch(0x3E, kind=TriggerKind.KEYBOARD_USAGE)))
    )

    assert page.warning_label.text() == ""


# ------------------------------------------------------------------- conflicts


def test_an_existing_mouse_switch_can_be_replaced(page):
    old = _switch(3)
    page.set_session(page.session.apply(AddBinding(1, old)))
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    page.mouse_combo.setCurrentIndex(page.mouse_combo.findData(3))
    commands = []
    page.command_requested.connect(commands.append)

    assert page.apply_button.isEnabled() is True
    page.apply_button.click()
    assert isinstance(commands[0], UpdateBinding)
    assert commands[0].uuid == old.uuid


def test_the_page_never_edits_the_session_itself(page):
    before = page.session
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    page.mouse_combo.setCurrentIndex(page.mouse_combo.findData(3))

    page.apply_button.click()

    assert page.session is before


def test_every_control_carries_an_accessible_name(page):
    for widget in (
        page.trigger_kind,
        page.key_combo,
        page.mouse_combo,
        page.capture_button,
        page.action_combo,
        page.mode_combo,
        page.apply_button,
        page.existing_list,
    ):
        assert widget.accessibleName()


def test_the_page_uses_the_shared_visual_hierarchy(page):
    titles = [
        label.text()
        for label in page.findChildren(QLabel)
        if label.property("role") == theme.ROLE_PAGE_TITLE
    ]

    assert titles == ["Mouse"]
    assert page.warning_label.property("role") == theme.ROLE_BANNER
    assert page.apply_button.property("role") == theme.ROLE_PRIMARY


def test_a_conflict_is_shown_as_an_error_not_a_warning(page):
    other = Binding(
        trigger=Trigger(TriggerKind.KEYBOARD_USAGE, 0x04, 0),
        mode=BindingMode.REPLACE,
        action=Action(ActionKind.TOGGLE_KEYBOARD_ROUTE, 0),
    )
    page.set_session(
        page.session.apply(
            AddBinding(
                page.session.project.active_profile_id,
                other,
            )
        )
    )
    page.select_trigger_kind(TriggerKind.KEYBOARD_USAGE)

    assert page.warning_label.text()
    assert page.warning_label.property("signal") == theme.SIGNAL_ERROR
