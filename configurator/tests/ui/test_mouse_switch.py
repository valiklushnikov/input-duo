"""The Mouse page: pick a trigger, then say which computer the mouse serves."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel

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
from duo_input.ui.models.project_session import AddBinding, ProjectSession
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


# ------------------------------------------------------ existing switch bindings


def test_the_page_lists_the_switch_bindings_of_the_active_profile(page):
    page.set_session(page.session.apply(AddBinding(1, _switch(3))))

    assert page.existing_list.count() == 1
    assert "Button 3" in page.existing_list.item(0).text()


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

    assert "Button 4" in page.warning_label.text()


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


def test_a_trigger_the_profile_already_uses_cannot_be_added(page):
    page.set_session(page.session.apply(AddBinding(1, _switch(3))))
    page.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    page.mouse_combo.setCurrentIndex(page.mouse_combo.findData(3))

    assert page.apply_button.isEnabled() is False
    assert "Button 3" in page.warning_label.text()


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
    page.set_session(
        page.session.apply(
            AddBinding(
                page.session.project.active_profile_id,
                _switch(0x04, kind=TriggerKind.KEYBOARD_USAGE),
            )
        )
    )
    page.select_trigger_kind(TriggerKind.KEYBOARD_USAGE)

    assert page.warning_label.text()
    assert page.warning_label.property("signal") == theme.SIGNAL_ERROR
