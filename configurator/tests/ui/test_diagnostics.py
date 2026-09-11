"""The Diagnostics page and the shell it hangs off of."""

from __future__ import annotations

import zipfile
import struct

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


def test_deleted_page_stops_following_service_state(qtbot, service, emulator):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QWidget

    owner = QWidget()
    deleted_page = DiagnosticsPage(service, owner)
    surviving_page = DiagnosticsPage(service)
    qtbot.addWidget(surviving_page)
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    with qtbot.captureExceptions() as errors:
        with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
            service.connect_device(emulator)

    assert errors == []
    assert surviving_page.value("protocol_version") == "1.0"


def test_refresh_shows_interface_boundary_evidence(page, emulator, qtbot, monkeypatch):
    legacy = bytearray(252); legacy[43] = 9
    inventory = struct.pack("<BHBIHHBBB48s", 1, 63, 1, 0, 0x1234, 0x5678, 2, 1, 1, b"Receiver")
    trace = struct.pack("<BHBIIB9sBBBBB", 1, 27, 1, 6, 3, 9, bytes.fromhex("0101004f0000000003"), 1, 1, 8, 0, 4)
    monkeypatch.setattr(emulator, "_handle_get_diagnostics", lambda payload: bytes(legacy) + inventory + trace)
    with qtbot.waitSignal(page.service.operation_succeeded):
        page.service.connect_device(emulator)
    with qtbot.waitSignal(page.service.operation_succeeded):
        page.refresh_button.click()
    value = page.value("input_sources")
    assert "1234:5678 interface 2" in value
    assert "reports=6 decoded=3" in value
    assert "id=1 min=8" in value
    assert "01 01 00 4f 00 00 00 00 03" in value


def test_refresh_shows_each_hid_report_set_source_read_only(page, emulator, qtbot):
    from duo_input.device.transactions import (
        HidReportEntry,
        HidReportRole,
        HidReportSets,
        HidReportSource,
    )

    emulator.set_hid_report_sets(HidReportSets((HidReportSource(
        5, 0x3434, 0xD030, 2,
        (
            HidReportEntry(HidReportRole.KEYBOARD, 1, 8),
            HidReportEntry(HidReportRole.CONSUMER, 2, 2),
            HidReportEntry(HidReportRole.KEYBOARD, 12, 20),
        ),
        (),
        0,
    ),)))
    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.service.connect_device(emulator)
    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.refresh_button.click()

    assert page.value("hid_report_sets") == (
        "3434:D030 interface 2: keyboard id=1 min=8; consumer id=2 min=2; "
        "keyboard id=12 min=20"
    )


def test_refresh_distinguishes_supported_empty_report_sets_from_unsupported(
    page, emulator, qtbot
):
    assert page.value("hid_report_sets") == UNKNOWN
    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.service.connect_device(emulator)
    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.refresh_button.click()

    assert page.value("hid_report_sets") == "none"


def test_refresh_keeps_rejected_report_overflow_visible_when_no_entries_fit(
    page, emulator, qtbot
):
    from duo_input.device.transactions import HidReportSets, HidReportSource

    emulator.set_hid_report_sets(HidReportSets((
        HidReportSource(5, 0x3434, 0xD030, 2, (), (), 4),
    )))
    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.service.connect_device(emulator)
    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.refresh_button.click()

    assert page.value("hid_report_sets") == (
        "3434:D030 interface 2: rejected overflow=4"
    )


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


def test_the_reference_counters_reach_the_page(page, emulator, qtbot):
    """Task 3's bounded queue overflow count and how many of the reference
    target's own interfaces earned no role were both readable in the firmware
    from the day each was added, and neither had ever reached this page until
    this block existed. Whether each role is ready is what tells a selected
    route apart from one nothing is actually reaching."""
    emulator.input_backend = 3
    emulator.reference_counters = (6, 2, 1, 0)
    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.service.connect_device(emulator)

    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.refresh_button.click()

    assert page.value("reference_callback_overflows") == "6"
    assert page.value("reference_ignored_interfaces") == "2"
    assert page.value("reference_keyboard_ready") == "yes"
    assert page.value("reference_mouse_ready") == "no"


def test_a_ch375_board_never_renders_reference_counters_as_real_readings(
    page, emulator, qtbot
):
    """The regression this page's own rows must not reproduce: CH375 links
    the exact same ConfigService as the reference target and never calls
    set_reference_counters, so these rows must read as unknown here - never
    as "0" and "no" on a board whose keyboard is actively typing. A firmware
    built before this block existed sends no block at all and lands here the
    same way."""
    emulator.input_backend = 1
    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.service.connect_device(emulator)

    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.refresh_button.click()

    assert page.value("reference_callback_overflows") == UNKNOWN
    assert page.value("reference_ignored_interfaces") == UNKNOWN
    assert page.value("reference_keyboard_ready") == UNKNOWN
    assert page.value("reference_mouse_ready") == UNKNOWN


def test_the_host_stack_row_reports_a_host_started_on_the_wrong_core(
    page, emulator, qtbot
):
    """Driven through the Refresh button, not by setting the value.

    A test that reached past the interface would pass with a completely dead
    handler - this project has shipped exactly that mistake before. The row it
    fills in is the one an operator reads when nothing enumerated and every
    counter on the page is zero.
    """
    emulator.input_backend = 2
    emulator.host_observation = (
        0b1111,
        120_000_000,
        120_000_000,
        880_000,
        0b0001,
        0,
        1_000_000,
        3,
        1,
        2,
        4,
        3,
        450_000,
        7,
        2,
        1,
        0x0010B9B0,
        0x00290102,
        0x00001010,
        2,
        950,
        0x20040A40,
        0x00270101,
        41,
        0,
        975,
        1128,
    )
    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.service.connect_device(emulator)

    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.refresh_button.click()

    reported = page.value("host_stack")
    assert "Host stack was already up before Core 1: yes" in reported
    assert "System clock when the input core began (Hz): 120000000" in reported
    assert "System clock now, and the PIO divider clock (Hz): 120000000" in reported
    assert "Root-port frames sent: 880000" in reported
    assert "Root port connected: no" in reported
    assert "Input core passes: 1000000" in reported
    assert "Device mount callbacks: 3" in reported
    assert "HID mount callbacks: 2" in reported
    assert "Endpoint slots opened (high-water): 4" in reported
    assert "Endpoint transaction failures (high-water): 3" in reported
    assert "Longest input-core pass gap (us): 450000" in reported
    assert "Largest SOF-frame advance between passes: 7" in reported
    assert "Root-port resets seen (lower bound): 2" in reported
    assert "Hub mounts seen (lower bound): 1" in reported
    # The five-statement-window readings reach the page decoded, not packed.
    assert (
        "Endpoint slot map, live (pool slots 0-3): slot0 dev5 ep0 out "
        "| slot1 dev5 ep1 in | slot2 dev0 ep0 out | slot3 closed" in reported
    )
    assert (
        "Host events queued since boot: attach 2 | remove 1 "
        "| transfer completions 41" in reported
    )
    assert (
        "Enumeration reached, by address (1-4 devices, 5 hub): dev1 nothing "
        "| dev2 nothing | dev3 nothing | dev4 nothing | dev5 configured+descriptor"
        in reported
    )
    assert "Input-core passes blocked over 20 ms (some are normal): 2" in reported
    assert (
        "Time in passes blocked over 20 ms (ms; ~500 per enumeration is normal): 950"
        in reported
    )
    assert (
        "Deepest input-core stack pointer (0 = no host event was ever queued): "
        "0x20040A40" in reported
    )
    assert (
        "Endpoint transfer state, live (pool slots 0-3): slot0 open idle "
        "| slot1 open idle | slot2 open active SETUP host-out | slot3 closed"
        in reported
    )
    assert (
        "Transfer completions when latest attach was queued (the baseline): 41"
        in reported
    )
    # And the subtraction itself, so the bench procedure never asks a person
    # holding a board to do arithmetic between two printed numbers.
    assert (
        "Transfer completions since the latest attach (5 of them are the "
        "hub's own): 0" in reported
    )
    assert (
        "Address-0 recovery attach events submitted (0 is not a health verdict): 0"
        in reported
    )
    assert "Shortest actual SOF interval (us): 975" in reported
    assert "Longest actual SOF interval (us): 1128" in reported
    # The page must not carry the old derived row either: it called a healthy
    # board faulty and it separated this board from a healthy one not at all.
    assert "unchanged since bring-up" not in reported


def test_a_healthy_host_stack_reads_as_healthy_on_the_page(page, emulator, qtbot):
    """Every reading is shown, not only the ones that are "wrong".

    "Root port connected: no" is the whole answer on a board that enumerated
    nothing, so a row that hid the readings which happen to be false or zero
    would hide the answer.
    """
    emulator.input_backend = 2
    emulator.host_observation = (
        0b1110,
        120_000_000,
        120_000_000,
        41_234,
        0b1011,
        1,
        987_654,
    )
    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.service.connect_device(emulator)

    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.refresh_button.click()

    reported = page.value("host_stack")
    assert "Host stack was already up before Core 1: no" in reported
    # The clock was selected and settled before Core 1 started.
    assert "System clock when the input core began (Hz): 120000000" in reported
    assert "System clock now, and the PIO divider clock (Hz): 120000000" in reported
    assert "unchanged since bring-up" not in reported
    assert "Root port connected: yes" in reported
    assert "Root-port attaches seen (lower bound): 1" in reported


def test_an_image_with_no_host_stack_leaves_the_row_unknown(page, emulator, qtbot):
    """The CH375 image has no host stack to observe. Showing seven zeros for it
    would put measurements of absent hardware on the page."""
    emulator.input_backend = 1
    emulator.backend_counters = ()
    emulator.host_observation = None
    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.service.connect_device(emulator)

    with qtbot.waitSignal(page.service.operation_succeeded, timeout=5000):
        page.refresh_button.click()

    assert page.value("input_backend") == "CH375"
    assert page.value("host_stack") == UNKNOWN


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
