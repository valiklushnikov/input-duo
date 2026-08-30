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
        "device_generation",
        "device_active_profile",
        "peripheral_keyboard",
        "peripheral_mouse",
        "peripheral_consumer",
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


def test_the_overview_does_not_offer_a_firmware_version(page, service):
    """The device reports the CDC protocol version and nothing about its firmware.

    A field that can only ever read "unknown" tells the operator that something
    is broken, when in fact nothing is: there is no one to ask.
    """
    page.update_from(ProjectSession.new(), service)

    for key in ("u1_firmware_version", "u2_firmware_version"):
        with pytest.raises(KeyError):
            page.value(key)


def test_the_overview_does_not_carry_memory_or_hash_bookkeeping(page, service):
    """Sizes and hashes are an implementation's business, not the operator's.

    Whether the device agrees with the project is still said in words, in the
    state strip above the pages.
    """
    page.update_from(ProjectSession.new(), service)

    for key in (
        "project_config_size",
        "device_config_size",
        "file_hash",
        "compiled_hash",
        "device_hash",
        "device_sync",
    ):
        with pytest.raises(KeyError):
            page.value(key)



def test_routes_come_from_the_active_profile(page, service):
    session = ProjectSession.new().apply(SetActiveProfile(2))

    page.update_from(session, service)

    profile = session.active_profile
    assert page.value("active_profile") == f"{profile.id} - {profile.name}"
    assert page.value("keyboard_route") == profile.keyboard_route.name
    assert page.value("mouse_route") == profile.mouse_route.name
    assert page.value("text_layout") == profile.text_layout.name





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
    assert page.value("device_active_profile") == UNKNOWN
    assert page.role("device_active_profile") == theme.ROLE_PLACEHOLDER


def test_a_value_that_arrives_stops_reading_as_absence(qtbot, page, service, emulator):
    assert page.role("device_generation") == theme.ROLE_PLACEHOLDER

    _connected(qtbot, service, emulator)
    page.update_from(ProjectSession.new(), service)

    assert page.value("device_generation") != UNKNOWN
    assert page.role("device_generation") == theme.ROLE_MONO


def test_numbers_and_identifiers_are_set_where_columns_line_up(page, service):
    page.update_from(ProjectSession.new(), service)

    for key in ("keyboard_route", "mouse_route", "text_layout"):
        assert page.role(key) == theme.ROLE_MONO, key





def test_the_page_opens_by_saying_what_it_is(page):
    from PySide6.QtWidgets import QLabel

    titles = [
        label.text()
        for label in page.findChildren(QLabel)
        if label.property("role") == theme.ROLE_PAGE_TITLE
    ]

    assert len(titles) == 1
    assert titles[0]
