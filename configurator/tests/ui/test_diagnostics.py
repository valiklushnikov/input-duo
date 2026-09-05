"""The Diagnostics page and the shell it hangs off of."""

from __future__ import annotations

import zipfile

import pytest

from duo_input.device.emulator import U1Emulator
from duo_input.device.service import DeviceService
from duo_input.domain.text_compiler import compile_project_to_binary
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
    SetMacroSteps,
    default_project,
)

SECRET = "correct horse battery staple"


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


def test_diagnostics_does_not_offer_a_firmware_version(page):
    """Protocol v1 carries no firmware version, from either microcontroller.

    A row that can only ever read "unknown" reads as a fault in the device,
    when the truth is that nothing is asked and nothing answers.
    """
    for key in ("u1_firmware_version", "u2_firmware_version"):
        with pytest.raises(KeyError):
            page.value(key)


def test_everything_is_unknown_without_a_device(page):
    assert page.value("protocol_version") == UNKNOWN
    assert page.value("reset_reason") == UNKNOWN
    assert page.value("input_backend") == UNKNOWN


def test_the_page_never_names_a_backend_in_a_row_label(page):
    """U1's two input channels can be read by either backend now, so a row
    called "CH375 state" is a label that lies on half the builds. The backend
    is named once, as a value, in the row that exists to report it."""
    from PySide6.QtWidgets import QLabel

    labels = [label.text() for label in page.findChildren(QLabel)]

    assert not [text for text in labels if "CH375" in text]
    with pytest.raises(KeyError):
        page.value("ch375_state")


def test_the_two_role_slots_read_as_keyboard_and_mouse(page, emulator, qtbot):
    """Backend-neutral names for the two logical roles. "Keyboard channel" was
    a CH375 pin pair; the PIO USB host has one bus and no channels at all."""
    emulator.input_backend = 2
    emulator.peripheral_ports = (
        (1, 1, 1, 0x046D, 0xC31C, 0, 0, bytes(32)),
        (0, 0, 0, 0, 0, 0, 0, bytes(32)),
    )
    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.service.connect_device(emulator)

    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.refresh_button.click()

    reported = page.value("peripherals")
    assert "Keyboard" in reported
    assert "Mouse" in reported
    assert "channel" not in reported.lower()


def test_the_backend_row_names_the_host_that_read_the_ports(page, emulator, qtbot):
    """Driven through the Refresh button rather than by setting the value: a
    handler this page never reaches is a handler a test must not pass over."""
    emulator.input_backend = 2
    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.service.connect_device(emulator)

    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.refresh_button.click()

    assert page.value("input_backend") == "PIO_USB"


def test_older_firmware_that_names_no_backend_leaves_the_row_empty(page, emulator, qtbot):
    """The emulator sends the payload older firmware sends. Nothing may invent
    a backend for it - "unknown" is the true answer and it is not "CH375"."""
    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.service.connect_device(emulator)

    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.refresh_button.click()

    assert page.value("input_backend") == UNKNOWN


def test_the_reason_an_interface_was_ignored_reaches_the_page(page, emulator, qtbot):
    """V1 accepts one keyboard and one mouse. The second keyboard is ignored on
    purpose, and this row is the only place that says so."""
    emulator.input_backend = 2
    emulator.backend_counters = (2, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.service.connect_device(emulator)

    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.refresh_button.click()

    reported = page.value("input_backend_counters")
    assert "ignored_interfaces 2" in reported
    assert "ignored_role_already_claimed 1" in reported


def test_connecting_fills_in_what_the_device_reports(page, emulator, qtbot):
    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.service.connect_device(emulator)
    page.refresh()

    assert page.value("protocol_version") == "1.0"
    # Protocol v1 carries neither of these, and the page keeps saying so.
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


# --------------------------------------------------------------- the shell


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch) -> MainWindow:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    window = MainWindow(DeviceService(timeout_ms=5000), transport_factory=lambda: None)
    qtbot.addWidget(window)
    return window


def test_the_shell_offers_the_diagnostics_page(window):
    window.show_page(window.PAGE_DIAGNOSTICS)

    assert window.pages.currentWidget() is window.diagnostics


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
