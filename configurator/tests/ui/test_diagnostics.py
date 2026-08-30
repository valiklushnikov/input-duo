"""The Diagnostics page and the recovery the shell offers at startup."""

from __future__ import annotations

import zipfile

import pytest
from PySide6.QtWidgets import QMessageBox

from duo_input.device.emulator import U1Emulator
from duo_input.device.service import DeviceService
from duo_input.domain.text_compiler import compile_project_to_binary
from duo_input.persistence.autosave import AutosaveService
from duo_input.persistence.diagnostic_export import (
    DIAGNOSTICS_MEMBER,
    PROJECT_MEMBER,
    UNKNOWN,
)
from duo_input.ui import theme
from duo_input.ui.diagnostics import DiagnosticsPage
from duo_input.ui.main_window import MainWindow
from duo_input.ui.models.macro_steps import text_step
from duo_input.ui.models.project_session import (
    AddMacro,
    ProjectSession,
    RenameProfile,
    SetMacroSteps,
    default_project,
)

SECRET = "correct horse battery staple"


def _discard_on_teardown(window: MainWindow) -> None:
    window._confirm_close = lambda: QMessageBox.StandardButton.Discard


@pytest.fixture
def emulator() -> U1Emulator:
    emulator = U1Emulator()
    emulator.install_active(compile_project_to_binary(default_project()))
    return emulator


@pytest.fixture
def service(qtbot) -> DeviceService:
    return DeviceService(timeout_ms=5000)


@pytest.fixture
def page(qtbot, service) -> DiagnosticsPage:
    page = DiagnosticsPage(service)
    qtbot.addWidget(page)
    page.set_session(ProjectSession.new())
    return page


# ---------------------------------------------------------------- the page


def test_everything_is_unknown_without_a_device(page):
    assert page.value("protocol_version") == UNKNOWN
    assert page.value("reset_reason") == UNKNOWN
    assert page.value("ch375_state") == UNKNOWN


def test_connecting_fills_in_what_the_device_reports(page, emulator, qtbot):
    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.service.connect_device(emulator)
    page.refresh()

    assert page.value("protocol_version") == "1.0"
    # Protocol v1 carries neither of these, and the page keeps saying so.
    assert page.value("u1_firmware_version") == UNKNOWN
    assert page.value("reset_reason") == UNKNOWN


def test_refreshing_asks_the_device_for_its_counters(page, emulator, qtbot):
    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.service.connect_device(emulator)

    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000) as blocker:
        page.refresh_button.click()

    assert blocker.args[0].operation == "get_diagnostics"
    assert page.value("cdc_bad_crc") == "0"


def test_counters_cannot_be_asked_for_without_a_device(page):
    assert page.refresh_button.isEnabled() is False


# ------------------------------------------------------------------- export


def test_the_export_excludes_the_macro_text_by_default(page, tmp_path):
    session = ProjectSession.new().apply(AddMacro(1, "Secret"))
    macro = session.active_profile.macros[0]
    session = session.apply(SetMacroSteps(1, macro.uuid, (text_step(SECRET),)))
    page.set_session(session.save(tmp_path / "secret.duoinput.json"))

    archive = page.export_to(tmp_path / "report.zip")

    assert SECRET.encode("utf-8") not in archive.read_bytes()
    with zipfile.ZipFile(archive) as zipped:
        assert DIAGNOSTICS_MEMBER in zipped.namelist()
        assert PROJECT_MEMBER not in zipped.namelist()


def test_ticking_the_box_includes_the_named_project(page, tmp_path):
    page.set_session(ProjectSession.new().save(tmp_path / "work.duoinput.json"))
    page.include_config_box.setChecked(True)

    archive = page.export_to(tmp_path / "report.zip")

    with zipfile.ZipFile(archive) as zipped:
        assert PROJECT_MEMBER in zipped.namelist()


def test_the_box_is_unavailable_until_the_project_has_a_file(page, tmp_path):
    assert page.include_config_box.isEnabled() is False

    page.set_session(ProjectSession.new().save(tmp_path / "work.duoinput.json"))

    assert page.include_config_box.isEnabled() is True


def test_a_destination_that_cannot_be_written_is_reported(page, tmp_path):
    directory = tmp_path / "taken.zip"
    directory.mkdir()

    with pytest.raises(OSError):
        page.export_to(directory)


def test_every_control_carries_an_accessible_name(page):
    for widget in (page.refresh_button, page.export_button, page.include_config_box):
        assert widget.accessibleName()


# ----------------------------------------------------------------- recovery


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch) -> MainWindow:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    window = MainWindow(DeviceService(timeout_ms=5000))
    qtbot.addWidget(window, before_close_func=_discard_on_teardown)
    return window


def test_the_shell_offers_the_diagnostics_page(window):
    window.show_page(window.PAGE_DIAGNOSTICS)

    assert window.pages.currentWidget() is window.diagnostics


def test_nothing_is_offered_when_there_is_no_autosave(window):
    assert window.offer_recovery() is False


def test_a_recovery_the_operator_accepts_becomes_the_session(window, tmp_path):
    window.autosave.save(ProjectSession.new().apply(RenameProfile(1, "Работа")))
    window._ask_recovery = lambda recovery: QMessageBox.StandardButton.Yes

    assert window.offer_recovery() is True

    assert window.session.project.profiles[0].name == "Работа"
    assert window.session.dirty is True


def test_a_recovery_the_operator_declines_is_thrown_away(window):
    window.autosave.save(ProjectSession.new().apply(RenameProfile(1, "Работа")))
    window._ask_recovery = lambda recovery: QMessageBox.StandardButton.No

    assert window.offer_recovery() is False

    assert window.session.project.profiles[0].name == "Profile 1"
    assert window.autosave.recovery() is None


def test_a_corrupted_autosave_is_reported_and_does_not_stop_startup(window):
    window.autosave.save(ProjectSession.new().apply(RenameProfile(1, "Работа")))
    window.autosave.autosave_path.write_text("{ not json", encoding="utf-8")
    window._ask_recovery = lambda recovery: QMessageBox.StandardButton.Yes

    assert window.offer_recovery() is False
    assert any("autosave" in event for event in window.overview.events())


def test_saving_the_project_clears_the_recovery(window, tmp_path):
    window.set_session(window.session.apply(RenameProfile(1, "Работа")))
    window.autosave.save(window.session)
    assert window.autosave.recovery() is not None

    assert window.save_project(tmp_path / "work.duoinput.json") is True

    assert window.autosave.recovery() is None


def test_the_shell_autosaves_on_its_own_schedule(window):
    window.set_session(window.session.apply(RenameProfile(1, "Работа")))
    assert window.autosave.recovery() is None

    window.autosave_now()

    assert window.autosave.recovery() is not None


def test_the_autosave_timer_runs_while_the_shell_is_open(window):
    assert window.autosave.timer.isActive() is True
    assert window.autosave.timer.interval() > 0


def test_an_autosave_that_cannot_be_written_is_reported_not_raised(window, monkeypatch):
    window.set_session(window.session.apply(RenameProfile(1, "Работа")))
    monkeypatch.setattr(
        window.autosave, "save", lambda session: (_ for _ in ()).throw(OSError("disk full"))
    )

    window.autosave_now()

    assert any("autosave" in event for event in window.overview.events())


# --------------------------------------------------------- how a fact reads


def test_the_page_opens_by_saying_what_it_is(page):
    from PySide6.QtWidgets import QLabel

    titles = [
        label.text()
        for label in page.findChildren(QLabel)
        if label.property("role") == theme.ROLE_PAGE_TITLE
    ]

    assert len(titles) == 1
    assert titles[0]


def test_a_counter_nobody_has_asked_for_reads_as_absence(page):
    assert page.value("chip_id") == UNKNOWN
    assert page.role("chip_id") == theme.ROLE_PLACEHOLDER


def test_a_counter_that_arrives_reads_as_a_number(page, emulator, qtbot):
    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.service.connect_device(emulator)
    page.refresh()

    assert page.value("protocol_version") == "1.0"
    assert page.role("protocol_version") == theme.ROLE_MONO


def test_every_counter_is_set_where_columns_line_up(page):
    """A page of numbers is only scannable when the digits align."""
    for key in page.field_keys():
        assert page.role(key) in (theme.ROLE_MONO, theme.ROLE_PLACEHOLDER), key


def test_the_privacy_note_gets_louder_only_once_it_applies(page, tmp_path):
    assert page.privacy_label.property("signal") != theme.SIGNAL_WARN

    page.set_session(ProjectSession.new().save(tmp_path / "p.duoinput.json"))
    page.include_config_box.setChecked(True)

    assert page.privacy_label.property("signal") == theme.SIGNAL_WARN
