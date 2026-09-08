"""The shell wiring: editor pages, applied commands and issue navigation."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from duo_input.device.emulator import U1Emulator
from duo_input.device.service import DeviceService
from duo_input.domain.models import Action, Binding, Trigger, TriggerSource
from duo_input.domain.text_compiler import compile_project_to_binary
from duo_input.generated.protocol import ActionKind, BindingMode, TriggerKind
from duo_input.ui.main_window import MainWindow
from duo_input.ui.models.binding_table import MouseCapabilities
from duo_input.ui.models.project_session import AddBinding, RenameProfile, default_project


#: The mouse this emulator puts on U1's bus, and a device that is not there.
#: Test data: nothing in the program may know either pair.
BUS_MOUSE = TriggerSource(0x3434, 0xD030, 1)
ELSEWHERE = TriggerSource(0x1234, 0x5678, 0)


def _binding(code: int = 0x04, *, source: TriggerSource | None = None) -> Binding:
    return Binding(
        trigger=Trigger(TriggerKind.KEYBOARD_USAGE, code, 0, source),
        mode=BindingMode.REPLACE,
        action=Action(ActionKind.TOGGLE_KEYBOARD_ROUTE, 0),
    )


def _settle(qtbot, window: MainWindow) -> None:
    """Wait out the read every connect queues, so a test starts from its answer."""
    settled = [False]

    def _mark(result: object) -> None:
        if getattr(result, "operation", None) == "read_config":
            settled[0] = True

    window.service.operation_succeeded.connect(_mark)
    window.service.operation_failed.connect(_mark)
    try:
        qtbot.waitUntil(lambda: settled[0], timeout=5000)
    finally:
        window.service.operation_succeeded.disconnect(_mark)
        window.service.operation_failed.disconnect(_mark)


@pytest.fixture
def emulator() -> U1Emulator:
    emulator = U1Emulator()
    emulator.install_active(compile_project_to_binary(default_project()))
    return emulator


@pytest.fixture
def window(qtbot) -> MainWindow:
    window = MainWindow(DeviceService(timeout_ms=5000), transport_factory=lambda: None)
    qtbot.addWidget(window)
    return window


# ------------------------------------------------------------------ the pages


def test_the_shell_offers_every_editor_page(window):
    assert window.nav.count() == len(window.PAGE_ORDER)
    assert window.pages.count() == len(window.PAGE_ORDER)


def test_choosing_a_section_shows_that_page(window):
    window.show_page(window.PAGE_BINDINGS)

    assert window.pages.currentWidget() is window.bindings
    assert window.nav.currentRow() == window.PAGE_BINDINGS


def test_every_page_sees_the_current_session(window):
    window.set_session(window.session.apply(RenameProfile(1, "Работа")))

    assert window.profiles.session is window.session
    assert window.bindings.session is window.session
    assert window.mouse.session is window.session


# ------------------------------------------------------------ applied commands


def test_a_command_from_a_page_reaches_the_session(window):
    window.profiles.select_profile(2)

    window.profiles.name_edit.setText("Игра")
    window.profiles.name_edit.editingFinished.emit()

    assert window.session.project.profiles[1].name == "Игра"
    assert window.session.dirty is True


def test_a_command_the_project_refuses_is_reported_and_changes_nothing(window):
    window.set_session(window.session.apply(AddBinding(1, _binding(0x04))))
    before = window.session

    window.apply_command(AddBinding(1, _binding(0x04)))

    assert window.session is before
    assert any("add_binding" in event for event in window.overview.events())


def test_a_binding_added_on_the_mouse_page_shows_up_on_the_bindings_page(window):
    window.mouse.set_capabilities(MouseCapabilities(advertised=True))
    window.mouse.select_trigger_kind(TriggerKind.MOUSE_BUTTON)
    window.mouse.mouse_combo.setCurrentIndex(window.mouse.mouse_combo.findData(3))

    window.mouse.apply_button.click()

    assert window.bindings.model.rowCount() == 1
    assert window.session.project.profiles[0].bindings[0].trigger.code == 3


def test_the_shell_tells_the_table_which_devices_are_on_the_bus(
    window, qtbot, emulator
):
    """The greying has to be reached the way the operator reaches it.

    Nothing here sets a capability by hand: the peripheral report comes off
    the emulator, through the Refresh button on the Diagnostics page, and out
    into the pages the shell repaints afterwards.
    """
    emulator.input_backend = 2
    emulator.peripheral_ports = (
        (1, 1, 2, BUS_MOUSE.vendor_id, BUS_MOUSE.product_id, 5, 0, bytes(32)),
        (0, 0, 0, 0, 0, 0, 0, bytes(32)),
    )
    with qtbot.waitSignal(window.service.operation_succeeded, timeout=5000):
        window.connect_device(emulator)
    _settle(qtbot, window)
    here = _binding(0x07, source=BUS_MOUSE)
    away = _binding(0x08, source=ELSEWHERE)
    window.apply_command(AddBinding(window.session.project.active_profile_id, here))
    window.apply_command(AddBinding(window.session.project.active_profile_id, away))

    window.show_page(window.PAGE_DIAGNOSTICS)
    with qtbot.waitSignal(window.service.operation_succeeded, timeout=5000):
        QTest.mouseClick(
            window.diagnostics.refresh_button, Qt.MouseButton.LeftButton
        )

    assert window.bindings.capabilities.mouse_id == (
        BUS_MOUSE.vendor_id,
        BUS_MOUSE.product_id,
    )
    model = window.bindings.model
    present = model.index(model.row_of(here.uuid), model.TRIGGER)
    absent = model.index(model.row_of(away.uuid), model.TRIGGER)
    assert model.data(present, Qt.ItemDataRole.ForegroundRole) is None
    assert model.data(absent, Qt.ItemDataRole.ForegroundRole) is not None
    assert "1234:5678 (interface 0)" in model.data(absent)

    # Unplugging takes the report with it: nothing is left saying the device
    # is missing, so nothing may go on claiming it is.
    window.disconnect_device()

    assert window.bindings.capabilities.mouse_id is None
    assert model.data(absent, Qt.ItemDataRole.ForegroundRole) is None


# ------------------------------------------------------------------- validation


def test_the_issue_list_is_empty_for_a_valid_project(window):
    assert window.issues_list.count() == 0
    assert window.issues_list.isVisibleTo(window) is False


def test_a_broken_project_lists_its_issues_and_blocks_the_write(window):
    broken = replace(window.session.project, active_profile_id=99)

    window.set_session(replace(window.session, project=broken))

    assert window.issues_list.count() == 1
    assert "/active_profile_id" in window.issues_list.item(0).text()
    assert window.write_button.isEnabled() is False


def test_clicking_a_binding_issue_opens_that_binding(window):
    session = window.session.apply(AddBinding(3, _binding(0x04)))
    profile = session.project.profiles[2]
    broken_binding = replace(
        profile.bindings[0], action=Action(ActionKind.RUN_MACRO, 7)
    )
    profile = replace(profile, bindings=(broken_binding,))
    profiles = tuple(
        profile if candidate.id == 3 else candidate for candidate in session.project.profiles
    )
    window.set_session(
        replace(session, project=replace(session.project, profiles=profiles))
    )
    assert window.issues_list.count() == 1

    window.open_issue(0)

    assert window.pages.currentWidget() is window.bindings
    assert window.session.project.active_profile_id == 3
    assert window.bindings.selected_binding() == broken_binding


def test_clicking_a_profile_issue_opens_that_profile(window):
    profiles = list(window.session.project.profiles)
    profiles[4] = replace(profiles[4], name="\0broken")
    window.set_session(
        replace(
            window.session,
            project=replace(window.session.project, profiles=tuple(profiles)),
        )
    )

    window.open_issue(0)

    assert window.pages.currentWidget() is window.profiles
    assert window.profiles.selected_profile_id == 5


def test_an_issue_without_a_profile_opens_the_overview(window):
    window.set_session(
        replace(window.session, project=replace(window.session.project, active_profile_id=99))
    )

    window.open_issue(0)

    assert window.pages.currentWidget() is window.overview


# ---------------------------------------------------------------- capabilities


def test_connecting_tells_the_editors_what_the_device_advertises(window, emulator, qtbot):
    assert window.bindings.capabilities.advertised is False

    with qtbot.waitSignal(window.service.operation_succeeded, timeout=5000):
        window.connect_device(emulator)

    assert window.bindings.capabilities.advertised is True
    assert window.mouse.capabilities.advertised is True


def test_disconnecting_takes_the_mouse_buttons_away_again(window, emulator, qtbot):
    with qtbot.waitSignal(window.service.operation_succeeded, timeout=5000):
        window.connect_device(emulator)

    window.disconnect_device()

    assert window.bindings.capabilities.buttons == ()
    assert window.mouse.capabilities.buttons == ()


# ---------------------------------------------------------------------- macros


def test_the_shell_offers_the_macros_page(window):
    window.show_page(window.PAGE_MACROS)

    assert window.pages.currentWidget() is window.macros
    assert window.macros.session is window.session


def test_a_macro_added_on_the_page_reaches_the_session(window):
    window.show_page(window.PAGE_MACROS)

    window.macros.add_macro_button.click()

    assert len(window.session.active_profile.macros) == 1


def test_clicking_a_macro_issue_opens_that_macro(window):
    from duo_input.ui.models.project_session import AddMacro, RenameMacro

    session = window.session.apply(AddMacro(3, "M"))
    macro = session.project.profiles[2].macros[0]
    window.set_session(session.apply(RenameMacro(3, macro.uuid, "bad\0name")))
    assert window.issues_list.count() == 1
    assert "/macros/0/name" in window.issues_list.item(0).text()

    window.open_issue(0)

    assert window.pages.currentWidget() is window.macros
    assert window.session.project.active_profile_id == 3
