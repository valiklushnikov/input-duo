"""The Macros page: step editing, limits, layout errors and the safe test run."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel

from duo_input.device.emulator import U1Emulator
from duo_input.device.service import DeviceService
from duo_input.domain.text_compiler import compile_project_to_binary
from duo_input.generated.protocol import (
    MACRO_STEPS_PER_MACRO,
    TEXT_CHARACTERS_PER_STEP,
    KeyboardRoute,
    MacroStepType,
    TargetMode,
    TextLayout,
)
from duo_input.ui.macros import MacrosPage, effective_target
from duo_input.ui.macros import TestMacroDialog as MacroTestDialog
from duo_input.ui.models.macro_steps import delay_step, text_step
from duo_input.ui.models.project_session import (
    AddMacro,
    ProjectSession,
    SetMacroSteps,
    SetMacroTarget,
    default_project,
)
from duo_input.ui import theme


@pytest.fixture
def emulator() -> U1Emulator:
    emulator = U1Emulator()
    emulator.install_active(compile_project_to_binary(default_project()))
    return emulator


@pytest.fixture
def service(qtbot) -> DeviceService:
    return DeviceService(timeout_ms=5000)


@pytest.fixture
def session() -> ProjectSession:
    return ProjectSession.new().apply(AddMacro(1, "Куркума"))


@pytest.fixture
def page(qtbot, service, session) -> MacrosPage:
    page = MacrosPage(service)
    qtbot.addWidget(page)
    page.set_session(session)
    page.select_macro_row(0)
    return page


def _macro(page: MacrosPage):
    return page.session.active_profile.macros[0]


def _with_layout(page: MacrosPage, layout: TextLayout) -> None:
    """Put the active profile on ``layout``; text compiles against it."""
    from dataclasses import replace

    profiles = tuple(
        replace(profile, text_layout=layout) if profile.id == page.profile.id else profile
        for profile in page.session.project.profiles
    )
    page.set_session(
        replace(page.session, project=replace(page.session.project, profiles=profiles))
    )
    page.select_macro_row(0)


def _apply(page: MacrosPage) -> None:
    """Feed the page's own commands back, the way the shell does."""
    page.command_requested.connect(lambda command: page.set_session(page.session.apply(command)))


# -------------------------------------------------------------------- macros


def test_the_page_lists_the_macros_of_the_active_profile(page):
    assert page.macro_list.count() == 1
    assert "Куркума" in page.macro_list.item(0).text()


def test_adding_a_macro_asks_for_one(page, qtbot):
    with qtbot.waitSignal(page.command_requested) as blocker:
        page.add_macro_button.click()

    assert isinstance(blocker.args[0], AddMacro)


def test_choosing_a_target_asks_for_it(page, qtbot):
    with qtbot.waitSignal(page.command_requested) as blocker:
        page.target_combo.setCurrentIndex(page.target_combo.findData(TargetMode.BOTH))

    command = blocker.args[0]
    assert isinstance(command, SetMacroTarget)
    assert command.target is TargetMode.BOTH


def test_the_target_combo_offers_inherit_pc1_pc2_and_both(page):
    offered = [page.target_combo.itemData(row) for row in range(page.target_combo.count())]

    assert offered == [TargetMode.INHERIT, TargetMode.PC1, TargetMode.PC2, TargetMode.BOTH]


def test_an_inherited_target_follows_the_profile_keyboard_route():
    profile = default_project().profiles[0]

    assert effective_target(profile, TargetMode.INHERIT) is TargetMode.PC1
    assert effective_target(profile, TargetMode.PC2) is TargetMode.PC2


# --------------------------------------------------------------------- steps


def test_adding_a_step_asks_to_store_the_new_list(page, qtbot):
    page.select_step_type(MacroStepType.DELAY)

    with qtbot.waitSignal(page.command_requested) as blocker:
        page.add_step_button.click()

    command = blocker.args[0]
    assert isinstance(command, SetMacroSteps)
    assert command.uuid == _macro(page).uuid
    assert [step.type for step in command.steps] == [MacroStepType.DELAY]


def test_every_step_type_can_be_added(page):
    _apply(page)

    for kind in MacroStepType:
        page.select_step_type(kind)
        page.add_step_button.click()

    assert [step.type for step in _macro(page).steps] == list(MacroStepType)


def test_deleting_a_step_removes_exactly_that_row(page):
    _apply(page)
    for kind in (MacroStepType.DELAY, MacroStepType.TEXT, MacroStepType.KEY_TAP):
        page.select_step_type(kind)
        page.add_step_button.click()

    page.select_step_row(1)
    page.remove_step_button.click()

    assert [step.type for step in _macro(page).steps] == [
        MacroStepType.DELAY,
        MacroStepType.KEY_TAP,
    ]


def test_a_step_can_be_moved_down_and_back_up(page):
    _apply(page)
    page.select_step_type(MacroStepType.DELAY)
    page.add_step_button.click()
    page.select_step_type(MacroStepType.KEY_TAP)
    page.add_step_button.click()

    page.select_step_row(0)
    page.move_down_button.click()
    assert [step.type for step in _macro(page).steps] == [
        MacroStepType.KEY_TAP,
        MacroStepType.DELAY,
    ]

    page.move_up_button.click()
    assert [step.type for step in _macro(page).steps] == [
        MacroStepType.DELAY,
        MacroStepType.KEY_TAP,
    ]


def test_the_first_step_cannot_move_up(page):
    _apply(page)
    page.select_step_type(MacroStepType.DELAY)
    page.add_step_button.click()
    page.select_step_row(0)

    assert page.move_up_button.isEnabled() is False
    assert page.move_down_button.isEnabled() is False


def test_the_step_limit_stops_the_add_button(page):
    steps = (delay_step(1, 1),) * MACRO_STEPS_PER_MACRO
    page.set_session(page.session.apply(SetMacroSteps(1, _macro(page).uuid, steps)))
    page.select_macro_row(0)

    assert page.step_list.model().rowCount() == MACRO_STEPS_PER_MACRO
    assert page.add_step_button.isEnabled() is False
    assert page.step_issue_label.text()


# ---------------------------------------------------------------- step editors


def test_a_fixed_delay_uses_the_same_bound_twice(page, qtbot):
    _apply(page)
    page.select_step_type(MacroStepType.DELAY)
    page.add_step_button.click()
    page.select_step_row(0)

    page.delay_minimum.setValue(120)
    page.delay_maximum.setValue(120)
    page.apply_step_button.click()

    assert _macro(page).steps[0].payload == b"\x78\x00\x78\x00"


def test_a_random_delay_keeps_both_bounds(page):
    _apply(page)
    page.select_step_type(MacroStepType.DELAY)
    page.add_step_button.click()
    page.select_step_row(0)

    page.delay_minimum.setValue(50)
    page.delay_maximum.setValue(400)
    page.apply_step_button.click()

    assert _macro(page).steps[0].payload == b"\x32\x00\x90\x01"


def test_a_delay_that_runs_backwards_is_refused(page):
    _apply(page)
    page.select_step_type(MacroStepType.DELAY)
    page.add_step_button.click()
    page.select_step_row(0)

    page.delay_minimum.setValue(400)
    page.delay_maximum.setValue(50)

    assert page.apply_step_button.isEnabled() is False
    assert page.step_issue_label.text()


def test_a_text_step_keeps_the_unicode_it_was_given(page):
    _apply(page)
    _with_layout(page, TextLayout.RU)
    page.select_step_type(MacroStepType.TEXT)
    page.add_step_button.click()
    page.select_step_row(0)

    page.text_edit.setPlainText("Привет мир")
    page.apply_step_button.click()

    assert _macro(page).steps[0].source_text == "Привет мир"


def test_a_text_step_read_from_the_device_says_where_it_came_from(qtbot):
    """The source text was never stored on the device, only the keystrokes.

    An empty text box would read as data loss; the truth is that this step can
    still be run and can no longer be edited as a sentence.
    """
    from duo_input.domain.models import MacroStep
    from duo_input.generated.protocol import MacroStepType
    from duo_input.ui.models.macro_steps import step_label

    recovered = MacroStep(MacroStepType.TEXT, bytes((0x00, 0x04, 0x00, 0x05)), None)

    summary = step_label(recovered)

    assert summary
    assert summary != ""
    assert "устройств" in summary.lower() or "device" in summary.lower()
    # Four bytes are two (modifier, usage) pairs, so two keystrokes.
    assert "2" in summary


def test_the_text_limit_blocks_the_apply(page):
    _apply(page)
    page.select_step_type(MacroStepType.TEXT)
    page.add_step_button.click()
    page.select_step_row(0)

    page.text_edit.setPlainText("x" * (TEXT_CHARACTERS_PER_STEP + 1))

    assert page.apply_step_button.isEnabled() is False
    assert str(TEXT_CHARACTERS_PER_STEP) in page.step_issue_label.text()


def test_a_character_the_layout_cannot_type_is_reported_with_its_index(page):
    _apply(page)
    page.select_step_type(MacroStepType.TEXT)
    page.add_step_button.click()
    page.select_step_row(0)

    page.text_edit.setPlainText("ok 😀")

    assert page.apply_step_button.isEnabled() is False
    assert "3" in page.step_issue_label.text()


def test_clicking_the_text_issue_selects_the_offending_character(page):
    _apply(page)
    page.select_step_type(MacroStepType.TEXT)
    page.add_step_button.click()
    page.select_step_row(0)
    page.text_edit.setPlainText("ok 😀")

    page.reveal_text_issue()

    assert page.text_edit.textCursor().selectionStart() == 3
    assert page.text_edit.hasFocus() or page.text_edit.textCursor().hasSelection()


def test_russian_text_compiles_on_a_russian_profile(page):
    _apply(page)
    _with_layout(page, TextLayout.RU)
    page.select_step_type(MacroStepType.TEXT)
    page.add_step_button.click()
    page.select_step_row(0)

    page.text_edit.setPlainText("КУРКУМА")

    assert page.step_issue_label.text() == ""
    assert page.apply_step_button.isEnabled() is True


def test_a_target_macro_line_compiles_to_the_exact_chords(page):
    _apply(page)
    page.select_step_type(MacroStepType.TEXT)
    page.add_step_button.click()
    page.select_step_row(0)
    page.text_edit.setPlainText("/target KYPKYMA")
    page.apply_step_button.click()

    package = compile_project_to_binary(page.session.project)

    from duo_input.domain.text_compiler import compile_text_payload

    assert compile_text_payload("/target KYPKYMA", TextLayout.US) in package


def test_a_key_tap_carries_the_chosen_modifiers(page):
    _apply(page)
    page.select_step_type(MacroStepType.KEY_TAP)
    page.add_step_button.click()
    page.select_step_row(0)

    page.key_combo.setCurrentIndex(page.key_combo.findData(0x06))
    page.modifier_boxes["ctrl"].setChecked(True)
    page.apply_step_button.click()

    assert _macro(page).steps[0].payload == b"\x01\x06"


def test_a_set_profile_step_carries_the_chosen_slot(page):
    _apply(page)
    page.select_step_type(MacroStepType.SET_PROFILE)
    page.add_step_button.click()
    page.select_step_row(0)

    page.profile_combo.setCurrentIndex(page.profile_combo.findData(6))
    page.apply_step_button.click()

    assert _macro(page).steps[0].payload == b"\x06"


def test_a_keyboard_route_step_carries_the_chosen_route(page):
    _apply(page)
    page.select_step_type(MacroStepType.SET_KEYBOARD_ROUTE)
    page.add_step_button.click()
    page.select_step_row(0)

    page.keyboard_route_combo.setCurrentIndex(
        page.keyboard_route_combo.findData(KeyboardRoute.BOTH)
    )
    page.apply_step_button.click()

    assert _macro(page).steps[0].payload == bytes((KeyboardRoute.BOTH,))


# ------------------------------------------------------------------ test run


def _dialog(qtbot, service, session, *, macro=None) -> MacroTestDialog:
    profile = session.active_profile
    dialog = MacroTestDialog(service, session, profile, macro or profile.macros[0])
    qtbot.addWidget(dialog)
    return dialog


def _connected(qtbot, service, emulator, session) -> ProjectSession:
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(emulator)
    return session.with_connection(True).with_device_hash(service.device_hash)


def test_a_test_run_needs_a_target_and_a_confirmation(qtbot, service, emulator, session):
    session = session.apply(
        SetMacroSteps(1, session.active_profile.macros[0].uuid, (delay_step(1, 1),))
    )
    emulator.install_active(compile_project_to_binary(session.project))
    session = _connected(qtbot, service, emulator, session)
    dialog = _dialog(qtbot, service, session)

    assert dialog.run_button.isEnabled() is False

    dialog.select_target(TargetMode.PC1)
    assert dialog.run_button.isEnabled() is False

    dialog.confirm_box.setChecked(True)
    assert dialog.run_button.isEnabled() is True


def test_a_target_the_device_would_not_use_is_refused(qtbot, service, emulator, session):
    emulator.install_active(compile_project_to_binary(session.project))
    session = _connected(qtbot, service, emulator, session)
    dialog = _dialog(qtbot, service, session)

    dialog.select_target(TargetMode.PC2)
    dialog.confirm_box.setChecked(True)

    assert dialog.run_button.isEnabled() is False
    assert "PC1" in dialog.blocked_label.text()


def test_a_project_the_device_is_not_holding_cannot_be_tested(qtbot, service, emulator, session):
    emulator.install_active(compile_project_to_binary(session.project))
    session = _connected(qtbot, service, emulator, session)
    edited = session.apply(
        SetMacroSteps(1, session.active_profile.macros[0].uuid, (delay_step(3, 3),))
    )
    dialog = _dialog(qtbot, service, edited)

    dialog.select_target(TargetMode.PC1)
    dialog.confirm_box.setChecked(True)

    assert dialog.run_button.isEnabled() is False
    assert dialog.blocked_label.text()


def test_running_sends_test_macro_and_changes_no_hash(qtbot, service, emulator, session):
    emulator.install_active(compile_project_to_binary(session.project))
    session = _connected(qtbot, service, emulator, session)
    dialog = _dialog(qtbot, service, session)
    dialog.select_target(TargetMode.PC1)
    dialog.confirm_box.setChecked(True)
    before = service.device_hash

    with qtbot.waitSignal(service.operation_succeeded, timeout=5000) as blocker:
        dialog.run_button.click()

    assert blocker.args[0].operation == "test_macro"
    assert service.device_hash == before
    assert dialog.session is session


def test_stop_is_offered_the_whole_time_and_releases_everything(qtbot, service, emulator, session):
    emulator.install_active(compile_project_to_binary(session.project))
    session = _connected(qtbot, service, emulator, session)
    dialog = _dialog(qtbot, service, session)

    assert dialog.stop_button.isEnabled() is True

    with qtbot.waitSignal(service.operation_succeeded, timeout=5000) as blocker:
        dialog.stop_button.click()

    assert blocker.args[0].operation == "stop_and_release_all"
    assert emulator.release_all_count == 1


def test_losing_the_device_warns_and_closes_the_dialog(qtbot, service, emulator, session):
    emulator.install_active(compile_project_to_binary(session.project))
    session = _connected(qtbot, service, emulator, session)
    dialog = _dialog(qtbot, service, session)
    dialog.open()

    service.disconnect_device()

    assert dialog.isVisible() is False
    assert dialog.warning


def test_the_test_dialog_never_edits_the_project(qtbot, service, emulator, session):
    emulator.install_active(compile_project_to_binary(session.project))
    session = _connected(qtbot, service, emulator, session)
    dialog = _dialog(qtbot, service, session)
    dialog.select_target(TargetMode.PC1)
    dialog.confirm_box.setChecked(True)

    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        dialog.run_button.click()

    assert dialog.session.project == session.project
    assert dialog.session.file_hash == session.file_hash
    assert dialog.session.device_hash == session.device_hash


def test_the_test_macro_dialog_matches_the_capture_dialog_shape(qtbot, service, session):
    from PySide6.QtCore import Qt

    dialog = _dialog(qtbot, service, session)

    assert bool(dialog.windowFlags() & Qt.WindowType.FramelessWindowHint)


def test_testing_is_offered_only_for_a_macro_on_a_connected_device(page, emulator, qtbot):
    assert page.test_button.isEnabled() is False

    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.service.connect_device(emulator)
    page.set_session(page.session.with_connection(True))

    assert page.test_button.isEnabled() is True


def test_every_control_carries_an_accessible_name(page):
    for widget in (
        page.macro_list,
        page.name_edit,
        page.target_combo,
        page.step_list,
        page.step_type_combo,
        page.add_step_button,
        page.remove_step_button,
        page.move_up_button,
        page.move_down_button,
        page.apply_step_button,
        page.test_button,
    ):
        assert widget.accessibleName()


def test_the_page_uses_the_shared_visual_hierarchy(page):
    titles = [
        label.text()
        for label in page.findChildren(QLabel)
        if label.property("role") == theme.ROLE_PAGE_TITLE
    ]

    assert titles == ["Macros"]
    assert page.step_issue_label.property("role") == theme.ROLE_BANNER
    assert page.apply_step_button.property("role") == theme.ROLE_PRIMARY


def test_every_step_editor_uses_the_shared_field_labels(page):
    editor_labels = {
        "Key:",
        "Modifiers:",
        "Usage:",
        "From, ms:",
        "To, ms:",
        "Route:",
        "Profile:",
    }
    labels = [
        label
        for label in page.findChildren(QLabel)
        if label.text() in editor_labels
    ]

    assert labels
    assert all(label.property("role") == theme.ROLE_FIELD_LABEL for label in labels)
