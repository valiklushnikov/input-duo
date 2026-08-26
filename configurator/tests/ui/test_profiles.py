"""The Profiles page: eight slots, name, colour, copy, clear and activate."""

from __future__ import annotations

import pytest

from duo_input.domain.models import Action, Binding, Trigger
from duo_input.generated.protocol import PROFILES, ActionKind, BindingMode, TriggerKind
from duo_input.ui.models.project_session import (
    AddBinding,
    ClearProfile,
    CopyProfile,
    ProjectSession,
    RenameProfile,
    SetActiveProfile,
    SetProfileColor,
)
from duo_input.ui.profiles import ProfilesPage


def _binding(code: int = 0x04) -> Binding:
    return Binding(
        trigger=Trigger(TriggerKind.KEYBOARD_USAGE, code, 0),
        mode=BindingMode.REPLACE,
        action=Action(ActionKind.TOGGLE_KEYBOARD_ROUTE, 0),
    )


@pytest.fixture
def page(qtbot) -> ProfilesPage:
    page = ProfilesPage()
    qtbot.addWidget(page)
    page.set_session(ProjectSession.new())
    return page


def test_the_page_shows_exactly_eight_slots(page):
    assert page.slots.count() == PROFILES
    assert [page.slots.item(row).data(page.PROFILE_ID_ROLE) for row in range(PROFILES)] == list(
        range(1, PROFILES + 1)
    )


def test_selecting_a_slot_shows_that_profile(page):
    page.select_profile(6)

    assert page.selected_profile_id == 6
    assert page.name_edit.text() == "Profile 6"


def test_each_slot_reports_how_many_bindings_it_holds(qtbot):
    page = ProfilesPage()
    qtbot.addWidget(page)
    session = ProjectSession.new().apply(AddBinding(2, _binding()))

    page.set_session(session)

    assert "1" in page.slots.item(1).text()


def test_editing_the_name_asks_for_a_rename(page, qtbot):
    page.select_profile(2)

    with qtbot.waitSignal(page.command_requested) as blocker:
        page.name_edit.setText("Работа")
        page.name_edit.editingFinished.emit()

    assert blocker.args[0] == RenameProfile(2, "Работа")


def test_an_unchanged_name_asks_for_nothing(page):
    page.select_profile(2)
    seen: list[object] = []
    page.command_requested.connect(seen.append)

    page.name_edit.editingFinished.emit()

    assert seen == []


def test_choosing_a_colour_asks_for_it(page, qtbot):
    page.select_profile(3)

    with qtbot.waitSignal(page.command_requested) as blocker:
        page.set_color((10, 20, 30))

    assert blocker.args[0] == SetProfileColor(3, (10, 20, 30))


def test_activate_asks_for_the_selected_profile(page, qtbot):
    page.select_profile(7)

    with qtbot.waitSignal(page.command_requested) as blocker:
        page.activate_button.click()

    assert blocker.args[0] == SetActiveProfile(7)


def test_activate_is_disabled_for_the_profile_that_is_already_active(page):
    page.select_profile(1)
    assert page.activate_button.isEnabled() is False

    page.select_profile(2)
    assert page.activate_button.isEnabled() is True


def test_copy_asks_to_copy_into_the_chosen_target(page, qtbot):
    page.select_profile(1)
    page.copy_target.setCurrentIndex(page.copy_target.findData(4))

    with qtbot.waitSignal(page.command_requested) as blocker:
        page.copy_button.click()

    assert blocker.args[0] == CopyProfile(1, 4)


def test_the_copy_target_never_offers_the_selected_slot(page):
    page.select_profile(3)

    targets = [page.copy_target.itemData(row) for row in range(page.copy_target.count())]

    assert 3 not in targets
    assert sorted(targets) == [1, 2, 4, 5, 6, 7, 8]


def test_clear_asks_to_clear_the_selected_slot(page, qtbot):
    page.select_profile(5)

    with qtbot.waitSignal(page.command_requested) as blocker:
        page.clear_button.click()

    assert blocker.args[0] == ClearProfile(5)


def test_the_page_never_edits_the_session_itself(page):
    before = page.session

    page.select_profile(2)
    page.set_color((1, 1, 1))
    page.clear_button.click()

    assert page.session is before


def test_a_new_session_keeps_the_selected_slot(page):
    page.select_profile(6)

    page.set_session(page.session.apply(RenameProfile(6, "Игра")))

    assert page.selected_profile_id == 6
    assert page.name_edit.text() == "Игра"


def test_every_control_carries_an_accessible_name(page):
    for widget in (
        page.slots,
        page.name_edit,
        page.color_button,
        page.copy_target,
        page.copy_button,
        page.clear_button,
        page.activate_button,
    ):
        assert widget.accessibleName()
