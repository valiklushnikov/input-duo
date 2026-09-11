"""Diagnostics stay useful without carrying the operator's macro text away."""

from __future__ import annotations

import hashlib
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
        "reference_callback_overflows",
        "reference_ignored_interfaces",
        "reference_keyboard_ready",
        "reference_mouse_ready",
        "peripherals",
        "spi_crc_errors",
        "spi_timeouts",
        "cdc_bad_crc",
        "cdc_bad_sequence",
        "cdc_timeout",
        "cdc_disconnect",
        "cdc_aborted_staging",
        "hid_descriptor_capture",
        "hid_report_sets",
    ):
        assert field in report


def test_a_field_protocol_v1_does_not_carry_says_so(tmp_path):
    archive = build_diagnostic_zip(tmp_path / "diag.zip", DiagnosticSnapshot.unknown())

    report = json.loads(_members(archive)[DIAGNOSTICS_MEMBER])
    assert report["reset_reason"] == UNKNOWN
    assert report["input_backend"] == UNKNOWN
    assert report["input_backend_counters"] == {}
    assert report["reference_callback_overflows"] == UNKNOWN
    assert report["reference_ignored_interfaces"] == UNKNOWN
    assert report["reference_keyboard_ready"] == UNKNOWN
    assert report["reference_mouse_ready"] == UNKNOWN
    assert report["peripherals"] == []
    assert report["hid_descriptor_capture"] == UNKNOWN
    assert report["hid_report_sets"] == UNKNOWN


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
    assert snapshot.hid_descriptor_capture == {"present": False}
    assert snapshot.hid_report_sets == []


def test_keychron_report_set_decisions_export_with_symbolic_roles_and_reasons(
    qtbot, emulator, tmp_path
):
    from duo_input.device.transactions import (
        HidReportEntry,
        HidReportRejectionReason,
        HidReportRole,
        HidReportSets,
        HidReportSource,
        RejectedHidReportEntry,
    )

    emulator.set_hid_report_sets(HidReportSets((HidReportSource(
        5, 0x3434, 0xD030, 2,
        (
            HidReportEntry(HidReportRole.KEYBOARD, 1, 8),
            HidReportEntry(HidReportRole.CONSUMER, 2, 2),
            HidReportEntry(HidReportRole.KEYBOARD, 12, 20),
        ),
        (RejectedHidReportEntry(
            HidReportRole.MOUSE, 3, HidReportRejectionReason.NO_MOUSE_REPORT
        ),),
        4,
    ),)))
    service = DeviceService(timeout_ms=5000)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.get_diagnostics()

    archive = build_diagnostic_zip(
        tmp_path / "diag.zip", DiagnosticSnapshot.from_service(service)
    )
    report_sets = json.loads(_members(archive)[DIAGNOSTICS_MEMBER])["hid_report_sets"]

    assert report_sets == [{
        "device_address": 5,
        "vendor_id": "0x3434",
        "product_id": "0xD030",
        "interface_number": 2,
        "accepted": [
            {"role": "keyboard", "report_id": 1, "minimum_body_bytes": 8},
            {"role": "consumer", "report_id": 2, "minimum_body_bytes": 2},
            {"role": "keyboard", "report_id": 12, "minimum_body_bytes": 20},
        ],
        "rejected": [{"role": "mouse", "report_id": 3, "reason": "no_mouse_report"}],
        "rejected_overflow": 4,
    }]


def test_a_captured_hid_descriptor_round_trips_exactly_through_the_archive(
    qtbot, emulator, tmp_path
):
    descriptor = bytes((0x05, 0x01, 0x09, 0x02))
    emulator.set_hid_descriptor_capture(0x3434, 0xD030, 2, descriptor)
    service = DeviceService(timeout_ms=5000)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.get_diagnostics()

    archive = build_diagnostic_zip(
        tmp_path / "diag.zip", DiagnosticSnapshot.from_service(service)
    )
    capture = json.loads(_members(archive)[DIAGNOSTICS_MEMBER])["hid_descriptor_capture"]

    assert capture == {
        "present": True,
        "truncated": False,
        "vendor_id": "0x3434",
        "product_id": "0xD030",
        "interface_number": 2,
        "original_size": 4,
        "captured_size": 4,
        "sha256": hashlib.sha256(descriptor).hexdigest(),
        "hex": "05 01 09 02",
    }


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


def test_the_report_carries_the_reference_targets_own_counters(qtbot, emulator):
    """Task 3's bounded queue overflow count and how many interfaces earned
    no role were both readable in the firmware from the day each was added,
    and neither had ever reached a report until this block existed. Whether
    each role is ready is what tells a selected route apart from one nothing
    is actually reaching."""
    emulator.input_backend = 3
    emulator.reference_counters = (6, 2, 1, 0)
    service = DeviceService(timeout_ms=5000)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.get_diagnostics()

    snapshot = DiagnosticSnapshot.from_service(service)

    assert snapshot.reference_callback_overflows == 6
    assert snapshot.reference_ignored_interfaces == 2
    assert snapshot.reference_keyboard_ready == "yes"
    assert snapshot.reference_mouse_ready == "no"


def test_a_ch375_board_never_reports_reference_counters_as_real_readings(qtbot, emulator):
    """The regression this section exists to catch: CH375 links the exact
    same ConfigService as the reference target and never calls
    set_reference_counters, so its reply must read as unknown here - never as
    zero overflows and two roles reported not-ready on a board whose keyboard
    is actively typing. The same holds for a firmware built before this block
    existed at all, which sends no block whatsoever; both must land here as
    unknown rather than as a guess."""
    emulator.input_backend = 1
    service = DeviceService(timeout_ms=5000)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.get_diagnostics()

    snapshot = DiagnosticSnapshot.from_service(service)

    assert snapshot.reference_callback_overflows == UNKNOWN
    assert snapshot.reference_ignored_interfaces == UNKNOWN
    assert snapshot.reference_keyboard_ready == UNKNOWN
    assert snapshot.reference_mouse_ready == UNKNOWN


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


# ------------------------------------- what the host stack and root port said


def test_the_report_carries_what_the_host_stack_and_root_port_are_doing(
    qtbot, emulator, tmp_path
):
    """The readings that were missing when the board enumerated nothing.

    Every backend counter above is a reason a peripheral that enumerated was
    not read. None of them says anything when nothing enumerates, so a report
    from that board was twelve zeros and no way to tell a dead host from an
    idle one. These are the readings from below all of it.
    """
    emulator.input_backend = 2
    emulator.host_observation = (
        0b1111,  # host was already up before the input core: the smoking gun
        120_000_000,
        120_000_000,
        880_000,
        0b0001,  # root port initialised, nothing connected
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
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        975,
        1128,
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
    host = report["host_stack"]

    assert host["Host stack was already up before Core 1"] == "yes"
    assert host["System clock when the input core began (Hz)"] == "120000000"
    assert host["System clock now, and the PIO divider clock (Hz)"] == "120000000"
    assert host["Root-port frames sent"] == "880000"
    assert host["Root port connected"] == "no"
    # Named as a lower bound where it is read, not only in the firmware.
    assert host["Root-port attaches seen (lower bound)"] == "0"
    assert host["Input core passes"] == "1000000"
    assert host["Device mount callbacks"] == "3"
    assert host["Device unmount callbacks"] == "1"
    assert host["HID mount callbacks"] == "2"
    assert host["Endpoint slots opened (high-water)"] == "4"
    assert host["Endpoint transaction failures (high-water)"] == "3"
    assert host["Longest input-core pass gap (us)"] == "450000"
    assert host["Largest SOF-frame advance between passes"] == "7"
    assert host["Root-port resets seen (lower bound)"] == "2"
    assert host["Hub mounts seen (lower bound)"] == "1"
    assert host["Shortest actual SOF interval (us)"] == "975"
    assert host["Longest actual SOF interval (us)"] == "1128"
    # No derived clock row. There was one, it called a healthy board faulty,
    # and it separated nothing - a healthy board prints these same two numbers.
    assert not any("unchanged since bring-up" in label for label in host)


def test_the_report_never_calls_a_healthy_clock_pair_a_fault(qtbot, emulator, tmp_path):
    """The current reordered image reports the settled 120 MHz twice.

    These are plain readings rather than a derived verdict so older 125/120
    images remain readable without being accused of a clock fault.
    """
    emulator.input_backend = 2
    emulator.host_observation = (
        0b1110,  # host was NOT already up: this is the healthy board
        120_000_000,
        120_000_000,
        41_234,
        0b1011,
        1,
        987_654,
    )
    service = DeviceService(timeout_ms=5000)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.get_diagnostics()

    archive = build_diagnostic_zip(
        tmp_path / "diag.zip", DiagnosticSnapshot.from_service(service)
    )
    host = json.loads(_members(archive)[DIAGNOSTICS_MEMBER])["host_stack"]

    assert host["Host stack was already up before Core 1"] == "no"
    assert host["System clock when the input core began (Hz)"] == "120000000"
    assert host["System clock now, and the PIO divider clock (Hz)"] == "120000000"
    # Nothing in the report says these two differing is wrong, by any wording.
    for label, value in host.items():
        assert "fault" not in label.lower()
        assert "mismatch" not in f"{label} {value}".lower()
    assert not any("unchanged since bring-up" in label for label in host)


def test_a_report_from_an_image_with_no_host_stack_invents_no_readings(
    qtbot, emulator
):
    """The CH375 image has no host stack, no root port and no backend loop.

    Printing "Root-port frames sent: 0" for it would put a measurement of
    absent hardware into a report someone acts on months later - the same rule
    the backend counters already follow.
    """
    emulator.input_backend = 1
    emulator.backend_counters = ()
    emulator.host_observation = None
    service = DeviceService(timeout_ms=5000)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.get_diagnostics()

    snapshot = DiagnosticSnapshot.from_service(service)

    assert snapshot.input_backend == "CH375"
    assert snapshot.host_stack == {}


def test_a_report_from_firmware_without_the_host_block_says_nothing_about_it(
    qtbot, emulator
):
    emulator.input_backend = 2
    service = DeviceService(timeout_ms=5000)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.get_diagnostics()

    snapshot = DiagnosticSnapshot.from_service(service)

    assert snapshot.input_backend == "PIO_USB"
    assert snapshot.host_stack == {}


def test_a_broken_host_block_reads_as_broken_rather_than_as_absent(qtbot, emulator):
    """A firmware that sent a garbled block is not one that sent none.

    Both leave every reading unavailable, but only one of them is a defect
    somebody has to chase - and a report that blanked them the same way would
    hide it behind the blank the CH375 image legitimately leaves.
    """
    from duo_input.persistence.diagnostic_export import HOST_STACK_UNREADABLE

    emulator.input_backend = 2
    # A declared field length with fewer bytes than that behind it. Written
    # over the emulator's own encoder, because the emulator is a correct
    # device and only a broken one produces this.
    emulator.host_observation = (0, 0, 0, 0, 0, 0, 0)
    original = emulator._appended_diagnostics

    def truncated() -> bytes:
        return original()[:-4]

    emulator._appended_diagnostics = truncated

    service = DeviceService(timeout_ms=5000)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.get_diagnostics()

    snapshot = DiagnosticSnapshot.from_service(service)

    assert list(snapshot.host_stack) == [HOST_STACK_UNREADABLE]
    assert snapshot.host_stack[HOST_STACK_UNREADABLE].startswith("unreadable:")
    # And everything in front of it is complete: the prefix is not lost to a
    # garbled suffix.
    assert snapshot.input_backend == "PIO_USB"


# ----------------------------- the five-statement window, decoded for a person


#: The board round 4 was built to read: hub configured, one downstream device
#: enumerating, stopped before the configuration-descriptor parse.
WEDGED_MID_ENUMERATION = (
    0b1110,
    120_000_000,
    120_000_000,
    63_706,
    0b1011,
    1,
    1_628_416,
    0,  # mount_events: TinyUSB suppresses these for hubs
    0,
    0,
    3,  # ep_slots_opened
    0,  # ep_max_failed_count: nothing on the wire ever failed
    500_708,
    450,
    1,
    1,  # hub_mount_events
    0x0010B9B0,  # slot0 dev5 ep0 out, slot1 dev5 ep1 in, slot2 dev0 ep0 out
    0x00290102,  # attach 2, remove 1, 41 transfer completions
    0x00001010,  # address 5 configured and descriptor read
    2,
    950,
    0x20040A40,
    0x00270101,  # slots 0/1 idle; slot2 active SETUP host-out
    41,  # current total is 41 too: no completion after downstream attach
    0,  # enum_stall_recoveries: the watchdog has not fired
    975,
    1128,
)


def _host_rows(qtbot, tmp_path, observation, name="window"):
    # Its own emulator each time: a DeviceService connects once, and two
    # readings of two different boards is exactly what these cases compare.
    emulator = U1Emulator()
    emulator.install_active(compile_project_to_binary(default_project()))
    emulator.input_backend = 2
    emulator.host_observation = observation
    service = DeviceService(timeout_ms=5000)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.connect_device(emulator)
    with qtbot.waitSignal(service.operation_succeeded, timeout=5000):
        service.get_diagnostics()
    archive = build_diagnostic_zip(
        tmp_path / f"{name}.zip", DiagnosticSnapshot.from_service(service)
    )
    return json.loads(_members(archive)[DIAGNOSTICS_MEMBER])["host_stack"]


def test_the_report_decodes_the_endpoint_slot_map_into_addresses(qtbot, tmp_path):
    """A packed u32 is not a reading anybody takes correctly at a bench.

    The whole round-3 diagnosis turned on "a configured hub occupies two pool
    slots, so a third is a downstream device" - a derivation from source, never
    a measurement. This row is the measurement, and it has to name the device
    behind each slot rather than hand the operator four bytes to decode.
    """
    host = _host_rows(qtbot, tmp_path, WEDGED_MID_ENUMERATION)

    slots = host["Endpoint slot map, live (pool slots 0-3)"]
    assert slots == (
        "slot0 dev5 ep0 out | slot1 dev5 ep1 in | slot2 dev0 ep0 out | slot3 closed"
    )


def test_the_report_decodes_the_host_event_counts_and_the_progress_mask(
    qtbot, tmp_path
):
    """Two attaches is the whole answer, and it must be legible as "two"."""
    host = _host_rows(qtbot, tmp_path, WEDGED_MID_ENUMERATION)

    assert host["Host events queued since boot"] == (
        "attach 2 | remove 1 | transfer completions 41"
    )
    assert host["Enumeration reached, by address (1-4 devices, 5 hub)"] == (
        "dev1 nothing | dev2 nothing | dev3 nothing | dev4 nothing "
        "| dev5 configured+descriptor"
    )


def test_the_report_decodes_live_endpoint_transfer_state(qtbot, tmp_path):
    """The next bench read must distinguish an idle open slot from SETUP."""
    host = _host_rows(qtbot, tmp_path, WEDGED_MID_ENUMERATION)

    assert host["Endpoint transfer state, live (pool slots 0-3)"] == (
        "slot0 open idle | slot1 open idle | "
        "slot2 open active SETUP host-out | slot3 closed"
    )


def test_endpoint_transfer_state_names_every_live_flag(qtbot, tmp_path):
    observation = list(WEDGED_MID_ENUMERATION)
    # slot0 closed; slot1 open idle behind PRE; slot2 active host-IN DATA1 with
    # PRE/stall/abort; slot3 active host-OUT DATA0.
    observation[22] = 0x07DB1100
    host = _host_rows(qtbot, tmp_path, tuple(observation), name="all-xfer-flags")

    assert host["Endpoint transfer state, live (pool slots 0-3)"] == (
        "slot0 closed | slot1 open idle PRE | "
        "slot2 open active DATA1 host-in PRE stalled aborted | "
        "slot3 open active DATA0 host-out"
    )


def test_the_report_names_recovery_requests_without_calling_zero_healthy(qtbot, tmp_path):
    """The label says exactly what an operator can conclude from the value."""
    host = _host_rows(qtbot, tmp_path, WEDGED_MID_ENUMERATION)

    label = "Address-0 recovery attach events submitted (0 is not a health verdict)"
    assert host[label] == "0"

    retried = list(WEDGED_MID_ENUMERATION)
    retried[24] = 3
    climbing = _host_rows(qtbot, tmp_path, tuple(retried), name="retried")
    assert climbing[label] == "3"


def test_the_report_subtracts_the_attach_baseline_so_nobody_has_to(qtbot, tmp_path):
    """The delta is the reading, so the report has to carry it as a row.

    Every version of the bench procedure that asked an operator to subtract one
    printed number from another was asking for the one step a person holding a
    board gets wrong.
    """
    from duo_input.persistence.diagnostic_export import (
        HUB_COMPLETIONS_AFTER_ATTACH,
        completions_since_attach_label,
    )

    host = _host_rows(qtbot, tmp_path, WEDGED_MID_ENUMERATION)

    # 41 completions in total and 41 at the attach: nothing at all completed
    # after it, not even the hub's own five, which is itself a reading.
    assert host[completions_since_attach_label()] == "0"
    assert str(HUB_COMPLETIONS_AFTER_ATTACH) in completions_since_attach_label()


def test_the_subtracted_row_counts_forward_from_the_baseline_not_backward(qtbot, tmp_path):
    """Direction matters, and only a non-zero delta can prove which way it runs.

    The wedged capture has the same number in both places, so a subtraction
    performed backwards produces the same zero and reads as correct. This is
    the row-4 case of the bench decision table: two stages completed after the
    attach on top of the hub's own five.
    """
    from duo_input.persistence.diagnostic_export import completions_since_attach_label

    later = list(WEDGED_MID_ENUMERATION)
    later[17] = (48 << 16) | (0 << 8) | 2
    host = _host_rows(qtbot, tmp_path, tuple(later), name="seven-after-attach")

    assert host[completions_since_attach_label()] == "7"


def test_the_subtracted_row_announces_a_saturated_completion_count(qtbot, tmp_path):
    """65535 is a clamp, not a measurement, and the delta below it is a fiction."""
    from duo_input.persistence.diagnostic_export import completions_since_attach_label

    saturated = list(WEDGED_MID_ENUMERATION)
    saturated[17] = (0xFFFF << 16) | (0 << 8) | 2
    host = _host_rows(qtbot, tmp_path, tuple(saturated), name="saturated")

    assert host[completions_since_attach_label()] == (
        "unavailable - the completion count has saturated at 65535"
    )


def test_the_subtracted_row_refuses_to_be_a_baseline_before_any_attach(qtbot, tmp_path):
    """With no accepted attach the snapshot is a zero, not a measurement."""
    from duo_input.persistence.diagnostic_export import completions_since_attach_label

    never_attached = list(WEDGED_MID_ENUMERATION)
    never_attached[17] = (41 << 16) | (0 << 8) | 0
    never_attached[23] = 0
    host = _host_rows(qtbot, tmp_path, tuple(never_attached), name="no-attach")

    assert host[completions_since_attach_label()] == (
        "not a baseline - no attach has been accepted yet"
    )


def test_the_report_names_the_attach_snapshot_as_a_delta_baseline(qtbot, tmp_path):
    """The number is a baseline, not a second cumulative completion count."""
    host = _host_rows(qtbot, tmp_path, WEDGED_MID_ENUMERATION)

    assert host["Transfer completions when latest attach was queued (the baseline)"] == "41"


def test_the_report_says_that_blocked_passes_are_normal_where_it_reports_them(
    qtbot, tmp_path
):
    """A healthy enumeration blocks for half a second, twice.

    A row that only said "passes blocked over 20 ms: 2" would send an operator
    hunting a stall that every working board also produces. The label carries
    the caveat, because the label is what reaches the report and the page.
    """
    host = _host_rows(qtbot, tmp_path, WEDGED_MID_ENUMERATION)

    assert host["Input-core passes blocked over 20 ms (some are normal)"] == "2"
    assert (
        host["Time in passes blocked over 20 ms (ms; ~500 per enumeration is normal)"]
        == "950"
    )


def test_the_report_says_a_zero_stack_reading_means_no_host_event_yet(
    qtbot, tmp_path
):
    """Zero is not an overflowed stack, and the label has to say so.

    The stack pointer is sampled inside the host event hook. On a board where
    the host stack never queued an event the hook never ran, and the field
    reports zero - which read as "the stack pointer reached address 0" would be
    the most alarming possible misreading of a board that is merely idle.
    """
    host = _host_rows(qtbot, tmp_path, WEDGED_MID_ENUMERATION)
    assert (
        host["Deepest input-core stack pointer (0 = no host event was ever queued)"]
        == "0x20040A40"
    )

    never_ran_values = list(WEDGED_MID_ENUMERATION)
    never_ran_values[21] = 0
    never_ran = tuple(never_ran_values)
    idle = _host_rows(qtbot, tmp_path, never_ran, name="idle")
    assert (
        idle["Deepest input-core stack pointer (0 = no host event was ever queued)"]
        == "0"
    )


def test_every_host_block_field_the_parser_reads_reaches_the_report(qtbot, tmp_path):
    """A field on the wire and nowhere in the report is a field nobody reads.

    Guards the mistake that costs a bench trip rather than a test run: adding a
    reading to the firmware, the wire and the parser, and forgetting the one
    place an operator actually looks.
    """
    import dataclasses

    from duo_input.device.transactions import HostObservation
    from duo_input.persistence.diagnostic_export import host_stack_field_names

    on_the_wire = {
        field.name
        for field in dataclasses.fields(HostObservation)
        if field.name not in ("state", "unreadable_reason")
    }
    assert host_stack_field_names() == on_the_wire

    host = _host_rows(qtbot, tmp_path, WEDGED_MID_ENUMERATION)
    assert len(host) == len(on_the_wire)
