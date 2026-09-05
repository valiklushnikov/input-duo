"""Diagnostics stay useful without carrying the operator's macro text away."""

from __future__ import annotations

import json
import zipfile

import pytest

from duo_input.device.emulator import U1Emulator
from duo_input.device.service import DeviceService
from duo_input.domain.text_compiler import compile_project_to_binary
from duo_input.generated.protocol import TextLayout
from duo_input.persistence.diagnostic_export import (
    DIAGNOSTICS_MEMBER,
    LOG_MEMBER,
    PROJECT_MEMBER,
    UNKNOWN,
    DiagnosticSnapshot,
    build_diagnostic_zip,
    redact,
)
from duo_input.persistence.locations import configure_logging, log_directory
from duo_input.ui.models.macro_steps import text_step
from duo_input.ui.models.project_session import (
    AddMacro,
    ProjectSession,
    SetMacroSteps,
    default_project,
)

SECRET = "sudo rm -rf --no-preserve-root"


@pytest.fixture
def secret_project(tmp_path):
    """A saved project whose macro types a phrase that must not leak."""
    session = ProjectSession.new().apply(AddMacro(1, "Secret"))
    macro = session.active_profile.macros[0]
    session = session.apply(SetMacroSteps(1, macro.uuid, (text_step(SECRET),)))
    path = tmp_path / "secret.duoinput.json"
    return session.save(path)


@pytest.fixture
def emulator() -> U1Emulator:
    emulator = U1Emulator()
    emulator.install_active(compile_project_to_binary(default_project()))
    return emulator


def _members(archive) -> dict[str, bytes]:
    with zipfile.ZipFile(archive) as zipped:
        return {name: zipped.read(name) for name in zipped.namelist()}


# ------------------------------------------------------------------- privacy


def test_the_default_archive_never_carries_the_macro_text(tmp_path, secret_project):
    archive = build_diagnostic_zip(
        tmp_path / "diag.zip", DiagnosticSnapshot.unknown(), project=secret_project
    )

    assert SECRET.encode("utf-8") not in archive.read_bytes()
    assert PROJECT_MEMBER not in _members(archive)


def test_the_project_is_included_only_when_it_is_asked_for(tmp_path, secret_project):
    archive = build_diagnostic_zip(
        tmp_path / "diag.zip",
        DiagnosticSnapshot.unknown(),
        project=secret_project,
        include_config=True,
    )

    members = _members(archive)
    assert PROJECT_MEMBER in members
    assert SECRET.encode("utf-8") in members[PROJECT_MEMBER]


def test_including_a_project_that_was_never_saved_is_refused(tmp_path):
    with pytest.raises(ValueError):
        build_diagnostic_zip(
            tmp_path / "diag.zip",
            DiagnosticSnapshot.unknown(),
            project=ProjectSession.new(),
            include_config=True,
        )


def test_text_step_content_is_redacted_before_it_can_be_logged():
    assert SECRET not in redact(f"typed {SECRET}", (SECRET,))
    assert "***" in redact(f"typed {SECRET}", (SECRET,))


def test_redacting_nothing_leaves_the_message_alone():
    assert redact("write_config: ok", ()) == "write_config: ok"


# ------------------------------------------------------------------ contents


def test_the_archive_records_every_field_the_report_needs(tmp_path):
    archive = build_diagnostic_zip(tmp_path / "diag.zip", DiagnosticSnapshot.unknown())

    report = json.loads(_members(archive)[DIAGNOSTICS_MEMBER])
    for field in (
        "application_version",
        "protocol_version",
        "u1_firmware_version",
        "u2_firmware_version",
        "chip_id",
        "reset_reason",
        "watchdog_count",
        "input_backend",
        "input_backend_counters",
        "peripherals",
        "spi_crc_errors",
        "spi_timeouts",
        "cdc_bad_crc",
        "cdc_bad_sequence",
        "cdc_timeout",
        "cdc_disconnect",
        "cdc_aborted_staging",
    ):
        assert field in report


def test_a_field_protocol_v1_does_not_carry_says_so(tmp_path):
    archive = build_diagnostic_zip(tmp_path / "diag.zip", DiagnosticSnapshot.unknown())

    report = json.loads(_members(archive)[DIAGNOSTICS_MEMBER])
    assert report["reset_reason"] == UNKNOWN
    assert report["input_backend"] == UNKNOWN
    assert report["input_backend_counters"] == {}
    assert report["peripherals"] == []


def test_the_application_log_travels_with_the_report(tmp_path):
    log = tmp_path / "duo-input.log"
    log.write_text("write_config: ok\n", encoding="utf-8")

    archive = build_diagnostic_zip(
        tmp_path / "diag.zip", DiagnosticSnapshot.unknown(), log_path=log
    )

    assert b"write_config: ok" in _members(archive)[LOG_MEMBER]


def test_a_missing_log_leaves_the_report_readable(tmp_path):
    archive = build_diagnostic_zip(
        tmp_path / "diag.zip", DiagnosticSnapshot.unknown(), log_path=tmp_path / "gone.log"
    )

    assert LOG_MEMBER not in _members(archive)
    assert DIAGNOSTICS_MEMBER in _members(archive)


def test_a_destination_that_cannot_be_written_is_reported(tmp_path):
    directory = tmp_path / "as-a-directory.zip"
    directory.mkdir()

    with pytest.raises(OSError):
        build_diagnostic_zip(directory, DiagnosticSnapshot.unknown())


# ------------------------------------------------------------- from a device


def test_a_connected_device_fills_in_what_protocol_v1_reports(qtbot, emulator):
    service = DeviceService(timeout_ms=5000)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.get_diagnostics()

    snapshot = DiagnosticSnapshot.from_service(service)

    assert snapshot.protocol_version != UNKNOWN
    assert snapshot.cdc_bad_crc == 0
    # Nothing in protocol v1 reports these, and the report says so.
    assert snapshot.reset_reason == UNKNOWN
    # The emulator answers the way firmware predating the suffix answers, and
    # nothing may invent a backend for it.
    assert snapshot.input_backend == UNKNOWN


def test_a_disconnected_service_yields_an_all_unknown_snapshot(qtbot):
    snapshot = DiagnosticSnapshot.from_service(DeviceService())

    assert snapshot == DiagnosticSnapshot.unknown()


# ---------------------------------------------------------------- log setup


def test_logging_writes_into_the_application_log_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    path = configure_logging()

    assert path.parent == log_directory()
    assert path.parent.is_dir()
    assert str(tmp_path) in str(path)


def test_the_log_is_rotated_rather_than_grown_without_end(tmp_path, monkeypatch):
    import logging

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    configure_logging()

    handlers = [
        handler
        for handler in logging.getLogger("duo_input").handlers
        if isinstance(handler, logging.handlers.RotatingFileHandler)
    ]

    assert handlers
    assert handlers[0].backupCount == 5
    assert handlers[0].maxBytes == 2 * 1024 * 1024


def test_a_text_step_never_reaches_the_log(tmp_path, monkeypatch, secret_project):
    import logging

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = configure_logging()
    logger = logging.getLogger("duo_input.test")

    logger.info(redact(f"macro text: {SECRET}", (SECRET,)))
    for handler in logging.getLogger("duo_input").handlers:
        handler.flush()

    assert SECRET not in path.read_text(encoding="utf-8")


def test_a_russian_project_survives_the_round_trip(tmp_path):
    session = ProjectSession.new().apply(AddMacro(1, "Куркума"))
    macro = session.active_profile.macros[0]
    session = session.apply(SetMacroSteps(1, macro.uuid, (text_step("привет"),)))
    profiles = tuple(
        type(profile)(
            id=profile.id,
            name=profile.name,
            color_rgb=profile.color_rgb,
            keyboard_route=profile.keyboard_route,
            mouse_route=profile.mouse_route,
            text_layout=TextLayout.RU,
            bindings=profile.bindings,
            macros=profile.macros,
        )
        for profile in session.project.profiles
    )
    from dataclasses import replace

    session = replace(session, project=replace(session.project, profiles=profiles))
    saved = session.save(tmp_path / "ru.duoinput.json")

    archive = build_diagnostic_zip(
        tmp_path / "diag.zip",
        DiagnosticSnapshot.unknown(),
        project=saved,
        include_config=True,
    )

    assert "привет".encode("utf-8") in _members(archive)[PROJECT_MEMBER]


def test_the_report_says_whether_the_second_board_is_answering(qtbot, emulator):
    """A report from a device whose second board is dead must not look like one
    where everything works. Every other counter here counts failures, and a
    link that never started produces none of them - so without this, both
    devices export the same five zeros. During bring-up that is exactly what
    happened: the link was silent for days and no reading said so."""
    emulator.endpoint_answering = False
    emulator.link_frames_sent = 4321
    emulator.link_crc_errors = 4321
    service = DeviceService(timeout_ms=5000)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.get_diagnostics()

    snapshot = DiagnosticSnapshot.from_service(service)

    assert snapshot.endpoint_answering == "no"
    assert snapshot.spi_frames_sent == 4321
    assert snapshot.spi_crc_errors == 4321


def test_input_the_device_could_not_deliver_reaches_the_report(qtbot, emulator):
    """The one counter with no other outward sign at all.

    A refused command is a press, a release or a macro step that never reached
    the computer it was meant for. The operator sees a keyboard that missed a
    letter, which reads as a hardware fault; this is the reading that says
    otherwise, and the report is what they send when they ask."""
    emulator.dropped_commands = 6
    service = DeviceService(timeout_ms=5000)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.get_diagnostics()

    snapshot = DiagnosticSnapshot.from_service(service)

    assert snapshot.dropped_commands == 6


def test_a_healthy_link_reads_as_healthy(qtbot, emulator):
    service = DeviceService(timeout_ms=5000)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.get_diagnostics()

    snapshot = DiagnosticSnapshot.from_service(service)

    assert snapshot.endpoint_answering == "yes"
    assert snapshot.spi_crc_errors == 0


# ------------------------------------------------------- which backend spoke


def test_the_report_names_the_backend_and_its_counters(qtbot, emulator):
    """A report emailed to a stranger has to say which host stack read the
    peripherals; the two backends fail in entirely different ways."""
    emulator.input_backend = 2
    emulator.backend_counters = (2, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    emulator.peripheral_ports = (
        (1, 1, 1, 0x046D, 0xC31C, 0, 0, bytes(32)),
        (1, 1, 2, 0x3434, 0xD030, 5, 67, bytes(range(32))),
    )
    service = DeviceService(timeout_ms=5000)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.get_diagnostics()

    snapshot = DiagnosticSnapshot.from_service(service)

    assert snapshot.input_backend == "PIO_USB"
    assert snapshot.input_backend_counters["ignored_interfaces"] == 2
    assert snapshot.input_backend_counters["ignored_role_already_claimed"] == 1


def test_the_report_names_the_two_role_slots_neutrally(qtbot, emulator, tmp_path):
    """"Keyboard channel" named a CH375 pin pair. The PIO USB host has one bus,
    so the report names the logical role the slot holds and nothing else."""
    emulator.input_backend = 2
    emulator.peripheral_ports = (
        (1, 1, 1, 0x046D, 0xC31C, 0, 0, bytes(32)),
        (0, 0, 0, 0, 0, 0, 0, bytes(32)),
    )
    service = DeviceService(timeout_ms=5000)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.get_diagnostics()

    archive = build_diagnostic_zip(
        tmp_path / "diag.zip", DiagnosticSnapshot.from_service(service)
    )
    report = json.loads(_members(archive)[DIAGNOSTICS_MEMBER])

    assert [entry["role"] for entry in report["peripherals"]] == ["Keyboard", "Mouse"]
    assert report["peripherals"][0]["vendor_id"] == "0x046D"
    # Nothing was on the mouse slot, and an absence is reported as one rather
    # than as a device with a vendor of zero.
    assert report["peripherals"][1]["vendor_id"] == UNKNOWN


def test_a_report_from_firmware_without_the_suffix_says_unknown(qtbot, emulator):
    service = DeviceService(timeout_ms=5000)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.get_diagnostics()

    snapshot = DiagnosticSnapshot.from_service(service)

    assert snapshot.input_backend == UNKNOWN
    assert snapshot.input_backend_counters == {}
