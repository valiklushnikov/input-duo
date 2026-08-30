"""The configurator against a real U1, not the emulator.

The emulator is a model of the firmware, and a model agrees with itself. This
file is the only place where the two implementations are made to answer the
same questions, so a divergence between them shows up as a failure rather than
as a device that behaves oddly in someone's hands.

It is skipped when no board is attached, which is most of the time. Run it with
a U1 plugged in - and note that it *writes to the device*: the configuration
that was on it is replaced.

    python -m pytest configurator/tests/integration/test_real_config_contract.py -q
"""

from __future__ import annotations

import hashlib
import os
import time

import pytest

from duo_input.device.discovery import find_u1_ports
from duo_input.device.emulator import U1Emulator
from duo_input.device.qt_transport import SynchronousTransportLink
from duo_input.device.service import DeviceService
from duo_input.domain.text_compiler import compile_project_to_binary
from duo_input.ui.models.project_session import ProjectSession, RenameProfile, default_project

#: Writing to a real device replaces what is on it, so it is opt-in.
ALLOW_WRITES = os.environ.get("DUO_INPUT_HIL_WRITE") == "1"


class DeviceRefused(AssertionError):
    """The device answered, and the answer was a refusal."""


class DeviceSilent(AssertionError):
    """The device did not answer at all."""


def wait_for_operation(qtbot, service, call, timeout: int = 5000):
    """Run ``call`` and return the :class:`OperationResult`, or say why not.

    ``DeviceService`` reports every outcome, success or failure, and it reports
    a refusal as fast as the device gives one. Waiting only for
    ``operation_succeeded`` throws that reason away and replaces it with the
    full timeout, so a device that said "unsupported capability" in two
    milliseconds is indistinguishable from one that was never plugged in. Every
    wait in this file listens for both and reports whichever arrived.
    """
    outcomes: list[tuple[str, object]] = []

    def on_success(result: object) -> None:
        outcomes.append(("succeeded", result))

    def on_failure(failure: object) -> None:
        outcomes.append(("failed", failure))

    service.operation_succeeded.connect(on_success)
    service.operation_failed.connect(on_failure)
    try:
        call()
        try:
            qtbot.waitUntil(lambda: bool(outcomes), timeout=timeout)
        except Exception as error:  # pytest-qt raises its own TimeoutError
            if outcomes:
                raise
            raise DeviceSilent(
                f"the device neither answered nor failed within {timeout} ms"
            ) from error
    finally:
        service.operation_succeeded.disconnect(on_success)
        service.operation_failed.disconnect(on_failure)

    kind, value = outcomes[0]
    if kind == "failed":
        named = value.error_code.name if value.error_code is not None else ""
        raise DeviceRefused(
            f"{value.operation} failed: {value.reason.value}"
            + (f" ({named})" if named else "")
            + (f" - {value.detail}" if value.detail else "")
        )
    return value


@pytest.fixture(scope="session")
def port(qapp) -> str:
    """The serial port of an attached U1, or a skip.

    Found through the fixture rather than at import time: enumerating serial
    ports needs a Qt application, and building one while a module is still
    being imported disturbs every other test in the run.
    """
    ports = find_u1_ports()
    if not ports:
        pytest.skip("no U1 attached; this file talks to real hardware")
    return ports[0].port_name


@pytest.fixture
def device(qtbot, port) -> DeviceService:
    """A service connected to the board on the bus."""
    from duo_input.device.qt_transport import QSerialPortTransport

    service = DeviceService(timeout_ms=5000)
    result = wait_for_operation(
        qtbot, service, lambda: service.connect_device(QSerialPortTransport(port)), timeout=10000
    )
    assert result.operation == "connect_device"
    yield service
    service.disconnect_device()


@pytest.fixture
def emulated(qtbot) -> DeviceService:
    """A service connected to the emulator, for comparison."""
    emulator = U1Emulator()
    emulator.install_active(compile_project_to_binary(default_project()))
    service = DeviceService(timeout_ms=5000)
    wait_for_operation(
        qtbot, service, lambda: service.connect_device(SynchronousTransportLink(emulator))
    )
    yield service
    service.disconnect_device()


# --------------------------------------------------------------- negotiation


def test_the_real_device_answers_hello(device):
    info = device.device_info

    assert info is not None
    assert (info.protocol_major, info.protocol_minor) == (1, 0)


def test_both_implementations_agree_on_the_protocol_version(device, emulated):
    assert device.device_info.protocol_major == emulated.device_info.protocol_major
    assert device.device_info.protocol_minor == emulated.device_info.protocol_minor


def test_the_real_device_grants_only_what_it_can_do(device, emulated):
    from duo_input.generated.protocol import Capability

    granted = device.device_info.capabilities

    # The emulator implements everything; the firmware does not yet, and says
    # so rather than accepting requests it cannot honour.
    assert granted & int(Capability.CONFIG_READ)
    assert granted & int(Capability.CONFIG_WRITE)
    assert granted & int(Capability.DIAGNOSTICS)
    assert granted != emulated.device_info.capabilities


def test_the_real_device_reports_its_diagnostic_counters(qtbot, device):
    """Diagnostics, not status: status is what ``connect_device`` already read."""
    counters = wait_for_operation(qtbot, device, device.get_diagnostics).value

    assert counters.bad_crc >= 0
    assert counters.bad_sequence >= 0


def test_a_refusal_is_named_at_once_rather_than_waited_out(qtbot, emulated):
    """No hardware: this is about how this file waits, not about the board.

    Waiting only for ``operation_succeeded`` turns a device that refused in
    milliseconds into a five-second timeout that names nothing - which is how a
    firmware/host disagreement reaches a person as "the tests hang" instead of
    as the reason the device gave.
    """
    emulated.disconnect_device()
    started = time.monotonic()

    with pytest.raises(DeviceRefused, match="not_connected"):
        wait_for_operation(qtbot, emulated, emulated.get_diagnostics, timeout=5000)

    assert time.monotonic() - started < 1.0


# ------------------------------------------------------------------- write


@pytest.mark.skipif(not ALLOW_WRITES, reason="set DUO_INPUT_HIL_WRITE=1 to write to the device")
def test_a_configuration_written_to_the_device_reads_back_identically(qtbot, device):
    session = ProjectSession.new().apply(RenameProfile(1, "Проверка"))
    package = compile_project_to_binary(session.project)

    result = wait_for_operation(qtbot, device, lambda: device.write_config(package), timeout=60000)

    assert result.operation == "write_config"
    # The device reports the digest of what it stored, computed from flash.
    assert device.device_hash == hashlib.sha256(package).digest()


@pytest.mark.skipif(not ALLOW_WRITES, reason="set DUO_INPUT_HIL_WRITE=1 to write to the device")
def test_reading_the_configuration_back_returns_the_same_bytes(qtbot, device):
    session = ProjectSession.new().apply(RenameProfile(2, "Чтение"))
    package = compile_project_to_binary(session.project)
    wait_for_operation(qtbot, device, lambda: device.write_config(package), timeout=60000)

    result = wait_for_operation(qtbot, device, device.read_config, timeout=60000)

    assert result.value == package


@pytest.mark.skipif(not ALLOW_WRITES, reason="set DUO_INPUT_HIL_WRITE=1 to write to the device")
def test_the_configuration_survives_a_reconnect(qtbot, device, port):
    session = ProjectSession.new().apply(RenameProfile(3, "Живучесть"))
    package = compile_project_to_binary(session.project)
    wait_for_operation(qtbot, device, lambda: device.write_config(package), timeout=60000)
    stored = device.device_hash

    device.disconnect_device()
    from duo_input.device.qt_transport import QSerialPortTransport

    wait_for_operation(
        qtbot, device, lambda: device.connect_device(QSerialPortTransport(port)), timeout=10000
    )

    # Flash, not RAM: the whole point of the A/B store.
    assert device.device_hash == stored


@pytest.mark.skipif(not ALLOW_WRITES, reason="set DUO_INPUT_HIL_WRITE=1 to write to the device")
def test_a_second_write_lands_in_the_other_slot_and_still_reads_back(qtbot, device):
    first = compile_project_to_binary(ProjectSession.new().apply(RenameProfile(1, "Раз")).project)
    second = compile_project_to_binary(ProjectSession.new().apply(RenameProfile(1, "Два")).project)

    wait_for_operation(qtbot, device, lambda: device.write_config(first), timeout=60000)
    wait_for_operation(qtbot, device, lambda: device.write_config(second), timeout=60000)

    assert device.device_hash == hashlib.sha256(second).digest()
