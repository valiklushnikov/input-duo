"""Overview page: only data the device actually reports, never invented values."""

from __future__ import annotations

import pytest

from duo_input.device.emulator import U1Emulator
from duo_input.device.service import DeviceService, DeviceState
from duo_input.domain.text_compiler import compile_project_to_binary
from duo_input.generated.protocol import (
    BINARY_CONFIG_MAX_BYTES,
    PROTOCOL_VERSION_MAJOR,
    PROTOCOL_VERSION_MINOR,
)
from duo_input.ui import theme
from duo_input.ui.models.project_session import ProjectSession, SetActiveProfile, default_project
from duo_input.ui.overview import IN_SYNC, OUT_OF_SYNC, UNKNOWN, OverviewPage


@pytest.fixture
def emulator() -> U1Emulator:
    emulator = U1Emulator()
    emulator.install_active(compile_project_to_binary(default_project()))
    return emulator


@pytest.fixture
def service(qtbot) -> DeviceService:
    return DeviceService(timeout_ms=5000)


@pytest.fixture
def page(qtbot) -> OverviewPage:
    page = OverviewPage()
    qtbot.addWidget(page)
    return page


def _connected(qtbot, service: DeviceService, emulator: U1Emulator) -> None:
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(emulator)
    assert service.state is DeviceState.READY


def test_device_fields_are_unknown_before_a_connection(page, service):
    page.update_from(ProjectSession.new(), service)

    for key in (
        "u1_protocol_version",
        "u1_firmware_version",
        "u2_firmware_version",
        "device_generation",
        "device_active_profile",
        "peripheral_keyboard",
        "peripheral_mouse",
        "peripheral_consumer",
        "device_config_size",
        "device_hash",
    ):
        assert page.value(key) == UNKNOWN, key


def test_connected_device_fields_come_from_the_service(qtbot, page, service, emulator):
    _connected(qtbot, service, emulator)

    page.update_from(ProjectSession.new().with_connection(True), service)

    assert page.value("u1_protocol_version") == f"{PROTOCOL_VERSION_MAJOR}.{PROTOCOL_VERSION_MINOR}"
    assert page.value("device_generation") == str(emulator.active_generation)
    assert page.value("device_active_profile") == str(emulator.active_profile)
    assert page.value("peripheral_keyboard") != UNKNOWN
    assert page.value("peripheral_mouse") != UNKNOWN
    assert page.value("peripheral_consumer") != UNKNOWN


def test_firmware_versions_stay_unknown_because_the_protocol_never_reports_them(
    qtbot, page, service, emulator
):
    _connected(qtbot, service, emulator)

    page.update_from(ProjectSession.new().with_connection(True), service)

    assert page.value("u1_firmware_version") == UNKNOWN
    assert page.value("u2_firmware_version") == UNKNOWN


def test_device_config_size_stays_unknown_until_the_device_reports_one(
    qtbot, page, service, emulator
):
    _connected(qtbot, service, emulator)

    page.update_from(ProjectSession.new().with_connection(True), service)

    assert page.value("device_config_size") == UNKNOWN


def test_routes_come_from_the_active_profile(page, service):
    session = ProjectSession.new().apply(SetActiveProfile(2))

    page.update_from(session, service)

    profile = session.active_profile
    assert page.value("active_profile") == f"{profile.id} - {profile.name}"
    assert page.value("keyboard_route") == profile.keyboard_route.name
    assert page.value("mouse_route") == profile.mouse_route.name
    assert page.value("text_layout") == profile.text_layout.name


def test_memory_usage_comes_from_the_compiled_project(page, service):
    session = ProjectSession.new()

    page.update_from(session, service)

    text = page.value("project_config_size")
    assert str(session.compiled_size) in text
    assert str(BINARY_CONFIG_MAX_BYTES) in text


def test_project_config_size_is_unknown_when_the_project_cannot_compile(page, service):
    from dataclasses import replace

    session = ProjectSession(project=replace(default_project(), active_profile_id=99))

    page.update_from(session, service)

    assert page.value("project_config_size") == UNKNOWN
    assert page.value("compiled_hash") == UNKNOWN


def test_hash_row_distinguishes_file_compiled_and_device_state(qtbot, page, service, emulator, tmp_path):
    session = ProjectSession.new()
    page.update_from(session, service)

    assert page.value("file_hash") == UNKNOWN
    assert page.value("compiled_hash") == session.compiled_hash
    assert page.value("device_hash") == UNKNOWN
    assert page.value("device_sync") == UNKNOWN

    saved = session.save(tmp_path / "profile.duoinput.json")
    aligned = saved.with_connection(True).with_device_hash(bytes.fromhex(saved.compiled_hash))
    page.update_from(aligned, service)

    assert page.value("file_hash") == saved.file_hash
    assert page.value("device_hash") == aligned.device_hash
    assert page.value("device_sync") == IN_SYNC

    stale = aligned.apply(SetActiveProfile(6))
    page.update_from(stale, service)

    assert page.value("device_sync") == OUT_OF_SYNC
    assert page.value("compiled_hash") == stale.compiled_hash
    assert page.value("compiled_hash") != saved.compiled_hash
    assert page.value("device_hash") == aligned.device_hash
    assert page.value("file_hash") == saved.file_hash


def test_recent_events_keep_protocol_identifiers_unlocalised(page):
    page.append_event("write_config", "bad_frame")

    events = page.events()

    assert events
    assert "write_config" in events[0]
    assert "bad_frame" in events[0]


def test_recent_events_are_capped_and_newest_first(page):
    for index in range(60):
        page.append_event("read_config", str(index))

    events = page.events()

    assert len(events) <= 50
    assert "59" in events[0]


# --------------------------------------------------------- how a fact reads


def test_a_value_the_device_never_reports_reads_as_absence(page):
    """``unknown`` is not a value in a lighter colour; it is a different kind."""
    assert page.value("u1_firmware_version") == UNKNOWN
    assert page.role("u1_firmware_version") == theme.ROLE_PLACEHOLDER


def test_a_value_that_arrives_stops_reading_as_absence(qtbot, page, service, emulator):
    assert page.role("device_generation") == theme.ROLE_PLACEHOLDER

    _connected(qtbot, service, emulator)
    page.update_from(ProjectSession.new(), service)

    assert page.value("device_generation") != UNKNOWN
    assert page.role("device_generation") == theme.ROLE_MONO


def test_numbers_and_identifiers_are_set_where_columns_line_up(page, service):
    page.update_from(ProjectSession.new(), service)

    for key in ("keyboard_route", "mouse_route", "text_layout", "compiled_hash"):
        assert page.role(key) == theme.ROLE_MONO, key


def test_a_hash_never_widens_the_page_it_sits_on(page):
    """Sixty-four characters with nowhere to wrap used to push a scrollbar."""
    for key in ("file_hash", "compiled_hash", "device_hash"):
        label = page.label(key)
        assert isinstance(label, theme.ElidingLabel), key
        assert label.minimumSizeHint().width() == 0, key


def test_the_hash_is_still_the_whole_hash_to_anything_that_reads_it(page, service):
    session = ProjectSession.new()

    page.update_from(session, service)

    assert page.value("compiled_hash") == session.compiled_hash
    assert page.label("compiled_hash").toolTip() == session.compiled_hash


def test_agreement_and_divergence_are_signalled_not_only_worded(qtbot, page, service, emulator, tmp_path):
    session = ProjectSession.new()
    page.update_from(session, service)
    assert page.signal("device_sync") == theme.SIGNAL_MUTED

    _connected(qtbot, service, emulator)
    aligned = session.with_connection(True).with_device_hash(service.device_hash)
    page.update_from(aligned, service)
    assert page.value("device_sync") == IN_SYNC
    assert page.signal("device_sync") == theme.SIGNAL_OK

    stale = aligned.apply(SetActiveProfile(4))
    page.update_from(stale, service)
    assert page.value("device_sync") == OUT_OF_SYNC
    assert page.signal("device_sync") == theme.SIGNAL_WARN


def test_the_page_opens_by_saying_what_it_is(page):
    from PySide6.QtWidgets import QLabel

    titles = [
        label.text()
        for label in page.findChildren(QLabel)
        if label.property("role") == theme.ROLE_PAGE_TITLE
    ]

    assert len(titles) == 1
    assert titles[0]
