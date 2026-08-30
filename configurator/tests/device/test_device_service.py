"""Asynchronous DeviceService driven against the deterministic U1 emulator."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer

from duo_input.device.emulator import ErrorCode, U1Emulator
from duo_input.device.qt_transport import SynchronousTransportLink
from duo_input.device.service import DeviceService, DeviceState
from duo_input.device.transactions import FailureReason
from duo_input.device.transport import AbstractByteTransport
from duo_input.domain.config_binary import compile_device_config, decode_device_config
from duo_input.domain.models import Macro, MacroStep, TargetMode
from duo_input.generated.protocol import (
    BINARY_CONFIG_MAX_BYTES,
    PROTOCOL_VERSION_MAJOR,
    CdcMessageType,
    MacroStepType,
)
from duo_input.protocol.frame import CdcFrame, decode_cdc_frame, encode_cdc_frame


# --------------------------------------------------------------------------- helpers


def _text_step(size: int) -> MacroStep:
    """A TEXT step of exactly ``size`` bytes of modifier/usage pairs."""
    return MacroStep(MacroStepType.TEXT, bytes(bytearray([0, 4] * (size // 2))))


def _package_with_text_steps(base, sizes: list[int]) -> bytes:
    steps = [_text_step(size) for size in sizes]
    macros: list[Macro] = []
    macro_id = 1
    while steps:
        macros.append(
            Macro(
                id=macro_id,
                name=f"M{macro_id}",
                target=TargetMode.INHERIT,
                steps=tuple(steps[:64]),
            )
        )
        steps = steps[64:]
        macro_id += 1
    first = replace(base.profiles[0], macros=tuple(macros), bindings=())
    return compile_device_config(replace(base, profiles=(first,) + base.profiles[1:]))


@pytest.fixture
def base_config():
    return decode_device_config(
        Path("tests/vectors/config_vectors/valid_minimal.bin").read_bytes()
    )


@pytest.fixture
def config_a() -> bytes:
    return Path("tests/vectors/config_vectors/valid_minimal.bin").read_bytes()


@pytest.fixture
def config_b(config_a: bytes) -> bytes:
    return compile_device_config(replace(decode_device_config(config_a), active_profile_id=2))


@pytest.fixture
def config_multi_chunk(base_config) -> bytes:
    package = _package_with_text_steps(base_config, [1024] * 3)
    assert 1536 < len(package) <= 4096
    return package


@pytest.fixture
def config_360_kib(base_config) -> bytes:
    package = _package_with_text_steps(base_config, [2048] * 178 + [1444])
    assert len(package) == BINARY_CONFIG_MAX_BYTES == 360 * 1024
    return package


@pytest.fixture
def emulator() -> U1Emulator:
    return U1Emulator()


@pytest.fixture
def service(qtbot) -> DeviceService:
    return DeviceService(timeout_ms=5000)


class _MutatingTransport(AbstractByteTransport):
    """Rewrites device replies on the wire without touching the emulator."""

    def __init__(self, emulator: U1Emulator, mutate) -> None:
        super().__init__()
        self._emulator = emulator
        self._mutate = mutate

    @property
    def is_open(self) -> bool:
        return self._emulator.is_open

    def open(self) -> None:
        self._emulator.open()

    def close(self) -> None:
        self._emulator.close()

    def write(self, data: bytes) -> bytes:
        raw = self._emulator.write(data)
        if not raw:
            return raw
        out = bytearray()
        for part in raw[:-1].split(b"\0"):
            frame = decode_cdc_frame(part + b"\0")
            out.extend(encode_cdc_frame(self._mutate(frame) or frame))
        return bytes(out)


def _connect(qtbot, service: DeviceService, transport, timeout: int = 5000):
    with qtbot.waitSignal(service.operation_succeeded, timeout=timeout) as blocker:
        service.connect_device(transport)
    result = blocker.args[0]
    assert result.operation == "connect_device"
    assert service.state is DeviceState.READY
    return result


def _fail(qtbot, service: DeviceService, call, timeout: int = 5000):
    with qtbot.waitSignal(service.operation_failed, timeout=timeout) as blocker:
        call()
    return blocker.args[0]


def _succeed(qtbot, service: DeviceService, call, timeout: int = 60000):
    with qtbot.waitSignal(service.operation_succeeded, timeout=timeout) as blocker:
        call()
    return blocker.args[0]


# --------------------------------------------------------------------------- connect


def test_connect_negotiates_and_reports_device_state(qtbot, service, emulator, config_a):
    emulator.install_active(config_a)
    states: list[DeviceState] = []
    service.state_changed.connect(states.append)
    statuses = []
    service.status_changed.connect(statuses.append)

    assert service.state is DeviceState.DISCONNECTED
    info = _connect(qtbot, service, emulator).value

    assert info.protocol_major == PROTOCOL_VERSION_MAJOR
    assert info.capabilities > 0
    assert info.active_hash == emulator.active_hash
    assert service.device_hash == emulator.active_hash
    assert states[0] is DeviceState.CONNECTING
    assert states[-1] is DeviceState.READY
    assert statuses and statuses[-1].active_profile == emulator.active_profile
    assert emulator.is_open


def test_frame_split_across_reads_is_reassembled(qtbot, service, emulator, config_a):
    emulator.install_active(config_a)
    link = SynchronousTransportLink(emulator, chunk_size=1)

    info = _connect(qtbot, service, link).value

    assert info.active_hash == emulator.active_hash


def test_operations_before_connect_are_rejected(qtbot, service):
    failure = _fail(qtbot, service, service.read_config)

    assert failure.reason is FailureReason.NOT_CONNECTED
    assert service.state is DeviceState.DISCONNECTED


def test_disconnect_device_returns_to_disconnected(qtbot, service, emulator, config_a):
    emulator.install_active(config_a)
    _connect(qtbot, service, emulator)

    service.disconnect_device()

    assert service.state is DeviceState.DISCONNECTED
    assert not emulator.is_open


def test_second_operation_while_busy_is_rejected(qtbot, service, emulator, config_a):
    emulator.install_active(config_a)
    _connect(qtbot, service, emulator)

    with qtbot.waitSignal(service.operation_failed, timeout=5000) as blocker:
        service.read_config()
        service.read_config()

    assert blocker.args[0].reason is FailureReason.BUSY
    assert service.state is DeviceState.BUSY


# --------------------------------------------------------------------------- read


def test_read_config_round_trips_the_active_package_with_full_progress(
    qtbot, service, emulator, config_a
):
    emulator.install_active(config_a)
    _connect(qtbot, service, emulator)
    progress: list[int] = []
    service.progress_changed.connect(progress.append)

    result = _succeed(qtbot, service, service.read_config)

    assert result.operation == "read_config"
    assert result.value == config_a
    assert progress[0] == 0
    assert progress[-1] == 100
    assert progress == sorted(progress)
    assert all(0 <= value <= 100 for value in progress)
    assert service.state is DeviceState.READY


# --------------------------------------------------------------------------- write


def test_write_config_transfers_360_kib_and_updates_device_hash(
    qtbot, service, emulator, config_a, config_360_kib
):
    emulator.install_active(config_a)
    _connect(qtbot, service, emulator)
    progress: list[int] = []
    service.progress_changed.connect(progress.append)

    result = _succeed(qtbot, service, lambda: service.write_config(config_360_kib))

    assert result.operation == "write_config"
    assert len(config_360_kib) == 360 * 1024
    assert emulator.active_hash == hashlib.sha256(config_360_kib).digest()
    assert service.device_hash == emulator.active_hash
    assert result.value == emulator.active_hash
    assert progress[0] == 0
    assert progress[-1] == 100
    assert progress == sorted(set(progress))
    assert not emulator.staging_active
    assert service.state is DeviceState.READY


def test_write_progress_reaches_both_endpoints_even_when_no_chunk_lands_on_them(
    qtbot, service, emulator, config_a, config_multi_chunk
):
    emulator.install_active(config_a)
    _connect(qtbot, service, emulator)
    progress: list[int] = []
    service.progress_changed.connect(progress.append)
    # The first acknowledged chunk is already past 0%, so 0 can only appear if
    # the service reports it before any chunk is sent.
    assert 512 * 100 // len(config_multi_chunk) > 0

    _succeed(qtbot, service, lambda: service.write_config(config_multi_chunk))

    assert progress[0] == 0
    assert progress[-1] == 100
    assert progress == sorted(set(progress))


def test_write_config_rejects_an_oversized_package(qtbot, service, emulator, config_a):
    emulator.install_active(config_a)
    _connect(qtbot, service, emulator)

    failure = _fail(
        qtbot, service, lambda: service.write_config(b"\0" * (BINARY_CONFIG_MAX_BYTES + 1))
    )

    assert failure.reason is FailureReason.INVALID_PACKAGE
    assert emulator.active_hash == hashlib.sha256(config_a).digest()


def test_write_config_reports_a_device_rejection_and_leaves_the_old_hash(
    qtbot, service, emulator, config_a
):
    emulator.install_active(config_a)
    old_hash = emulator.active_hash
    _connect(qtbot, service, emulator)

    failure = _fail(qtbot, service, lambda: service.write_config(b"not a config package"))

    assert failure.reason is FailureReason.DEVICE_ERROR
    assert failure.error_code is ErrorCode.INVALID_CONFIG
    assert emulator.active_hash == old_hash
    assert service.device_hash == old_hash
    assert not emulator.staging_active


# --------------------------------------------------------------------------- fault matrix


def test_disconnect_during_write_keeps_the_old_device_hash(
    qtbot, service, emulator, config_a, config_multi_chunk
):
    emulator.install_active(config_a)
    old_hash = emulator.active_hash
    _connect(qtbot, service, emulator)
    assert service.device_hash == old_hash
    injected: list[bool] = []

    def on_progress(value: int) -> None:
        if value > 0 and not injected:
            injected.append(True)
            emulator.inject_disconnect()

    service.progress_changed.connect(on_progress)

    failure = _fail(qtbot, service, lambda: service.write_config(config_multi_chunk))

    assert injected, "the disconnect was never triggered mid-transfer"
    assert failure.operation == "write_config"
    assert failure.reason is FailureReason.LINK_LOST
    assert emulator.active_hash == old_hash
    assert service.device_hash == old_hash
    assert not emulator.staging_active
    assert service.state is DeviceState.DISCONNECTED


def test_abort_during_write_keeps_the_old_device_hash(
    qtbot, service, emulator, config_a, config_multi_chunk
):
    emulator.install_active(config_a)
    old_hash = emulator.active_hash
    _connect(qtbot, service, emulator)
    aborted: list[bool] = []

    def on_progress(value: int) -> None:
        if value > 0 and not aborted:
            aborted.append(True)
            service.abort_write()

    service.progress_changed.connect(on_progress)

    failure = _fail(qtbot, service, lambda: service.write_config(config_multi_chunk))

    assert aborted, "abort_write was never reached"
    assert failure.reason is FailureReason.ABORTED
    assert emulator.active_hash == old_hash
    assert service.device_hash == old_hash
    assert not emulator.staging_active
    assert service.state is DeviceState.READY


def test_bad_crc_response_fails_the_operation(qtbot, service, emulator, config_a):
    emulator.install_active(config_a)
    _connect(qtbot, service, emulator)
    emulator.inject_bad_crc_response()

    failure = _fail(qtbot, service, service.get_diagnostics)

    assert failure.operation == "get_diagnostics"
    assert failure.reason is FailureReason.BAD_FRAME


def test_device_reported_bad_sequence_fails_the_connection(qtbot, service, emulator, config_a):
    emulator.install_active(config_a)
    emulator.open()
    # Skew the sequence the device expects before the host ever speaks.
    emulator.exchange(CdcFrame(CdcMessageType.PING, 500, b""))

    failure = _fail(qtbot, service, lambda: service.connect_device(emulator))

    assert failure.operation == "connect_device"
    assert failure.reason is FailureReason.DEVICE_ERROR
    assert failure.error_code is ErrorCode.BAD_SEQUENCE
    assert service.state is DeviceState.DISCONNECTED


def test_reply_with_a_mismatched_sequence_is_rejected(qtbot, service, emulator, config_a):
    emulator.install_active(config_a)
    skew: list[bool] = []

    def mutate(frame: CdcFrame):
        if skew:
            return replace(frame, sequence=(frame.sequence + 1) & 0xFFFF)
        return None

    transport = _MutatingTransport(emulator, mutate)
    _connect(qtbot, service, transport)
    skew.append(True)

    failure = _fail(qtbot, service, service.get_diagnostics)

    assert failure.reason is FailureReason.SEQUENCE_MISMATCH


def test_incompatible_device_major_refuses_the_connection(qtbot, service, emulator, config_a):
    emulator.install_active(config_a)

    def mutate(frame: CdcFrame):
        if frame.type is not CdcMessageType.DEVICE_INFO:
            return None
        payload = bytearray(frame.payload)
        payload[1] = PROTOCOL_VERSION_MAJOR + 1
        return replace(frame, payload=bytes(payload))

    transport = _MutatingTransport(emulator, mutate)

    failure = _fail(qtbot, service, lambda: service.connect_device(transport))

    assert failure.operation == "connect_device"
    assert failure.reason is FailureReason.PROTOCOL_MISMATCH
    assert service.state is DeviceState.DISCONNECTED
    assert not emulator.is_open


def test_timeout_fails_the_operation_without_blocking_the_event_loop(qtbot, emulator, config_a):
    service = DeviceService(timeout_ms=40)
    emulator.install_active(config_a)
    _connect(qtbot, service, emulator)
    emulator.inject_timeout()
    ticked: list[bool] = []
    QTimer.singleShot(0, lambda: ticked.append(True))

    failure = _fail(qtbot, service, service.get_diagnostics)

    assert failure.operation == "get_diagnostics"
    assert failure.reason is FailureReason.TIMEOUT
    assert ticked, "the Qt event loop never ran while the request was in flight"
    assert service.state is DeviceState.READY


def test_readback_mismatch_after_commit_is_reported(qtbot, service, emulator, config_a, config_b):
    emulator.install_active(config_a)

    def mutate(frame: CdcFrame):
        if frame.type is not CdcMessageType.GET_ACTIVE_CONFIG_INFO:
            return None
        return replace(frame, payload=bytes(frame.payload[:9]) + bytes(32))

    transport = _MutatingTransport(emulator, mutate)
    _connect(qtbot, service, transport)
    old_hash = service.device_hash

    failure = _fail(qtbot, service, lambda: service.write_config(config_b))

    assert failure.operation == "write_config"
    assert failure.reason is FailureReason.READBACK_MISMATCH
    assert service.device_hash == old_hash


# --------------------------------------------------------------------------- capture & misc


def test_capture_event_is_delivered_and_sequence_resynchronises(qtbot, service, emulator, config_a):
    emulator.install_active(config_a)
    link = SynchronousTransportLink(emulator)
    _connect(qtbot, service, link)

    _succeed(qtbot, service, service.begin_capture)
    assert emulator.capture_active
    assert emulator.queue_capture_event(b"\x01\x02\x03")

    with qtbot.waitSignal(service.capture_received, timeout=5000) as blocker:
        link.poll()

    assert bytes(blocker.args[0]) == b"\x01\x02\x03"

    # The device advanced its own sequence counter; the host must resynchronise.
    result = _succeed(qtbot, service, service.stop_and_release_all)
    assert result.operation == "stop_and_release_all"
    assert emulator.release_all_count == 1


def test_stop_and_release_all_reaches_the_device(qtbot, service, emulator, config_a):
    emulator.install_active(config_a)
    _connect(qtbot, service, emulator)

    _succeed(qtbot, service, service.stop_and_release_all)

    assert emulator.release_all_count == 1


def test_get_diagnostics_reports_device_counters(qtbot, service, emulator, config_a):
    emulator.install_active(config_a)
    _connect(qtbot, service, emulator)

    result = _succeed(qtbot, service, service.get_diagnostics)

    diagnostics = result.value
    assert result.operation == "get_diagnostics"
    assert diagnostics.bad_crc == 0
    assert diagnostics.disconnect == 0
    assert diagnostics.timeout == 0
    assert diagnostics.bad_sequence == 0
    assert diagnostics.aborted_staging == 0


def test_a_diagnostics_reply_the_host_cannot_read_is_named_not_waited_out(
    qtbot, service, emulator, config_a
):
    """A firmware whose GET_DIAGNOSTICS reply has another shape must be reported.

    ``firmware/u1_main/config_service.cpp`` answers GET_DIAGNOSTICS with the
    43-byte binary counters, except under ``DUO_SPI_DEBUG``/``DUO_CH375_PROBE``,
    where it answers with ``1 + link_debug_size_`` bytes of probe text instead.
    A host that met such a board must say what it could not read; a caller left
    waiting for a reply that already arrived learns nothing at all.
    """
    emulator.install_active(config_a)

    def mutate(frame: CdcFrame):
        if frame.type is CdcMessageType.GET_DIAGNOSTICS:
            probe_text = b"CH375 #1 answered, #2 silent"
            return CdcFrame(frame.type, frame.sequence, bytes((ErrorCode.OK,)) + probe_text)
        return None

    _connect(qtbot, service, _MutatingTransport(emulator, mutate))

    failure = _fail(qtbot, service, service.get_diagnostics)

    assert failure.operation == "get_diagnostics"
    assert failure.reason is FailureReason.BAD_PAYLOAD
    assert "GET_DIAGNOSTICS" in failure.detail


def test_a_device_info_request_is_refused_by_both_implementations(qtbot, service, emulator, config_a):
    """DEVICE_INFO travels device-to-host; asking for one is not a request.

    The firmware answers a single INVALID_REQUEST byte
    (``config_service.cpp`` dispatch), and so does the emulator. The host never
    sends one - it names DEVICE_INFO only as the *reply* it expects to HELLO -
    so this pins the shape both sides already agree on rather than a behaviour
    the configurator depends on.
    """
    emulator.install_active(config_a)
    emulator.open()

    reply = decode_cdc_frame(emulator.write(encode_cdc_frame(
        CdcFrame(CdcMessageType.DEVICE_INFO, 1, b"")
    )))

    assert reply.type is CdcMessageType.DEVICE_INFO
    assert bytes(reply.payload) == bytes((ErrorCode.INVALID_REQUEST,))


def test_diagnostics_report_input_the_device_could_not_deliver(qtbot, service, emulator, config_a):
    emulator.install_active(config_a)
    emulator.dropped_commands = 4
    _connect(qtbot, service, emulator)

    result = _succeed(qtbot, service, service.get_diagnostics)

    # Four presses, releases or macro steps the device produced and could not
    # deliver. Nothing else the operator can see says so: the keyboard simply
    # missed some letters, which reads as a hardware fault and is not one.
    assert result.value.dropped_commands == 4


def test_diagnostics_report_the_device_timeout_counter(qtbot, emulator, config_a):
    service = DeviceService(timeout_ms=40)
    emulator.install_active(config_a)
    _connect(qtbot, service, emulator)
    emulator.inject_timeout()
    _fail(qtbot, service, service.get_diagnostics)

    result = _succeed(qtbot, service, service.get_diagnostics)

    assert result.value.timeout == 1


# --------------------------------------------------------------------------- fix round 1


class _TruncatingTransport(AbstractByteTransport):
    """Cuts one reply short, leaving a partial frame in the host's assembler."""

    def __init__(self, emulator: U1Emulator) -> None:
        super().__init__()
        self._emulator = emulator
        self.truncate_next = False

    @property
    def is_open(self) -> bool:
        return self._emulator.is_open

    def open(self) -> None:
        self._emulator.open()

    def close(self) -> None:
        self._emulator.close()

    def write(self, data: bytes) -> bytes:
        raw = self._emulator.write(data)
        if self.truncate_next and len(raw) > 4:
            self.truncate_next = False
            return raw[:4]
        return raw


class _CaptureRacingTransport(AbstractByteTransport):
    """Flushes a device-initiated CAPTURE_EVENT ahead of an already-buffered reply."""

    def __init__(self, emulator: U1Emulator) -> None:
        super().__init__()
        self._emulator = emulator
        self.requests: list[CdcFrame] = []
        self.race_next = False
        self.capture_sequence: int | None = None

    @property
    def is_open(self) -> bool:
        return self._emulator.is_open

    def open(self) -> None:
        self._emulator.open()

    def close(self) -> None:
        self._emulator.close()

    def write(self, data: bytes) -> bytes:
        if data:
            for part in data[:-1].split(b"\0"):
                self.requests.append(decode_cdc_frame(part + b"\0"))
        raw = self._emulator.write(data)
        if not self.race_next or not raw:
            return raw
        self.race_next = False
        reply = decode_cdc_frame(raw[: raw.index(0) + 1])
        self.capture_sequence = (reply.sequence + 1) & 0xFFFF
        capture = encode_cdc_frame(
            CdcFrame(CdcMessageType.CAPTURE_EVENT, self.capture_sequence, b"\x07")
        )
        return capture + raw


def test_read_config_rejects_a_device_reported_size_beyond_the_protocol_limit(
    qtbot, service, emulator, config_a
):
    emulator.install_active(config_a)
    seen: list[CdcMessageType] = []

    def mutate(frame: CdcFrame):
        seen.append(frame.type)
        if frame.type is not CdcMessageType.READ_CONFIG_BEGIN:
            return None
        payload = bytearray(frame.payload)
        payload[5:9] = struct.pack("<I", BINARY_CONFIG_MAX_BYTES + 1)
        return replace(frame, payload=bytes(payload))

    transport = _MutatingTransport(emulator, mutate)
    _connect(qtbot, service, transport)

    failure = _fail(qtbot, service, service.read_config)

    assert failure.operation == "read_config"
    assert failure.reason is FailureReason.BAD_PAYLOAD
    assert CdcMessageType.READ_CONFIG_CHUNK not in seen
    assert service.state is DeviceState.READY


def test_a_failed_operation_does_not_poison_the_next_one(qtbot, emulator, config_a):
    service = DeviceService(timeout_ms=40)
    emulator.install_active(config_a)
    transport = _TruncatingTransport(emulator)
    _connect(qtbot, service, transport)
    transport.truncate_next = True

    failure = _fail(qtbot, service, service.get_diagnostics)
    assert failure.reason is FailureReason.TIMEOUT

    result = _succeed(qtbot, service, service.get_diagnostics, timeout=5000)

    assert result.operation == "get_diagnostics"
    assert result.value.timeout == 0


def test_a_reply_never_rewinds_the_sequence_behind_a_capture_event(
    qtbot, service, emulator, config_a
):
    emulator.install_active(config_a)
    transport = _CaptureRacingTransport(emulator)
    _connect(qtbot, service, transport)
    transport.race_next = True

    with qtbot.waitSignal(service.capture_received, timeout=5000):
        service.get_diagnostics()
    qtbot.waitUntil(lambda: service.state is DeviceState.READY, timeout=5000)

    already_sent = len(transport.requests)
    service.stop_and_release_all()
    qtbot.waitUntil(lambda: len(transport.requests) > already_sent, timeout=5000)

    request = transport.requests[already_sent]
    assert request.type is CdcMessageType.STOP_AND_RELEASE_ALL
    assert transport.capture_sequence is not None
    # The capture event consumed capture_sequence, so the next host request must
    # be the one after it and must never rewind to a sequence already spent.
    assert request.sequence == (transport.capture_sequence + 1) & 0xFFFF
    # Let the in-flight request settle; the emulator never saw the fabricated
    # capture event, so it answers BAD_SEQUENCE and the operation ends there.
    qtbot.waitUntil(lambda: service.state is not DeviceState.BUSY, timeout=5000)


# --------------------------------------------------------------------------- test macro


def test_test_macro_names_the_profile_and_macro_the_device_holds(qtbot, service, emulator, base_config):
    package = _package_with_text_steps(base_config, [2])
    emulator.install_active(package)
    _connect(qtbot, service, emulator)

    result = _succeed(qtbot, service, lambda: service.test_macro(1, 1))

    assert result.operation == "test_macro"


def test_test_macro_reports_a_macro_the_device_does_not_have(qtbot, service, emulator, config_a):
    emulator.install_active(config_a)
    _connect(qtbot, service, emulator)

    failure = _fail(qtbot, service, lambda: service.test_macro(1, 200))

    assert failure.operation == "test_macro"
    assert failure.error_code is ErrorCode.INVALID_REQUEST


def test_test_macro_refuses_identifiers_the_protocol_cannot_carry(qtbot, service, emulator, config_a):
    emulator.install_active(config_a)
    _connect(qtbot, service, emulator)

    failure = _fail(qtbot, service, lambda: service.test_macro(0, 1))

    assert failure.operation == "test_macro"
    assert failure.reason is FailureReason.INVALID_PACKAGE


def test_the_service_remembers_the_counters_the_device_last_reported(qtbot, service, emulator, config_a):
    emulator.install_active(config_a)
    _connect(qtbot, service, emulator)
    assert service.diagnostics is None

    result = _succeed(qtbot, service, service.get_diagnostics)

    assert service.diagnostics == result.value


def test_disconnecting_forgets_the_counters(qtbot, service, emulator, config_a):
    emulator.install_active(config_a)
    _connect(qtbot, service, emulator)
    _succeed(qtbot, service, service.get_diagnostics)

    service.disconnect_device()

    assert service.diagnostics is None


# ------------------------------------------------------ link state over CDC


def test_diagnostics_report_whether_the_second_board_is_answering() -> None:
    """The counters beside these all count failures, and a link that never
    started produces none of them. During bring-up every reading this port
    offered was zero while the second board was not there at all - which is
    the one thing an operator most needs to be told."""
    import struct

    from duo_input.device.transactions import parse_diagnostics

    payload = struct.pack(
        "<BIIIII", 0, 1, 2, 3, 4, 5
    ) + struct.pack("<BBIII", 1, 1, 900, 7, 3)

    counters = parse_diagnostics(payload)

    assert counters.bad_crc == 1
    assert counters.endpoint_answering is True
    assert counters.endpoint_mounted is True
    assert counters.link_frames_sent == 900
    assert counters.link_crc_errors == 7
    assert counters.link_echoed_frames == 3


def test_diagnostics_from_firmware_without_link_state_still_parse() -> None:
    """Firmware predating the link fields answers with the counters alone.
    Refusing that reply would turn an older device into an unreachable one."""
    import struct

    from duo_input.device.transactions import parse_diagnostics

    counters = parse_diagnostics(struct.pack("<BIIIII", 0, 1, 2, 3, 4, 5))

    assert counters.bad_crc == 1
    assert counters.endpoint_answering is None
    assert counters.link_frames_sent is None


def test_diagnostics_carry_what_the_endpoint_saw_when_the_link_died() -> None:
    """U2 releases every key 100 ms after U1 goes quiet, and nothing can watch
    that happen - the link that would carry the news is the one that went
    silent. So U2 remembers and reports it on the way back, and this is where
    the host reads it."""
    import struct

    from duo_input.device.transactions import parse_diagnostics

    payload = (
        struct.pack("<BIIIII", 0, 0, 0, 0, 0, 0)
        + struct.pack("<BBIII", 1, 1, 900, 0, 0)
        + struct.pack("<BH", 2, 104)
    )

    counters = parse_diagnostics(payload)

    assert counters.endpoint_drops == 2
    assert counters.endpoint_release_ms == 104


def test_diagnostics_say_whether_the_output_queue_is_refusing_commands_now() -> None:
    """``dropped_commands`` never goes down, so it cannot tell a burst that is
    over from one still in progress. The fault byte beside it can: the firmware
    clears it after a pass in which nothing was refused."""
    import struct

    from duo_input.device.transactions import parse_diagnostics

    payload = (
        struct.pack("<BIIIII", 0, 0, 0, 0, 0, 0)
        + struct.pack("<BBIII", 1, 1, 900, 0, 0)
        + struct.pack("<BH", 0, 0)
        + struct.pack("<I", 12)
        + struct.pack("<B", 1)
    )

    counters = parse_diagnostics(payload)

    assert counters.dropped_commands == 12
    assert counters.runtime_fault == 1

    # And a firmware that stops before it is still a firmware this can read.
    older = parse_diagnostics(payload[:-1])
    assert older.dropped_commands == 12
    assert older.runtime_fault is None


def test_diagnostics_without_the_endpoint_report_are_still_readable() -> None:
    import struct

    from duo_input.device.transactions import parse_diagnostics

    payload = struct.pack("<BIIIII", 0, 0, 0, 0, 0, 0) + struct.pack("<BBIII", 1, 1, 900, 0, 0)

    counters = parse_diagnostics(payload)

    assert counters.endpoint_answering is True
    assert counters.endpoint_drops is None
