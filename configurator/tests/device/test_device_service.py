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
from duo_input.device.transactions import FailureReason, PayloadError
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


# ------------------------------------------------- the latency the device knows


def _latency_block(
    edges: tuple[int, ...],
    keyboard: tuple[int, int, tuple[int, ...]],
    mouse: tuple[int, int, tuple[int, ...]],
) -> bytes:
    import struct

    out = bytes((len(edges) + 1,)) + b"".join(struct.pack("<I", edge) for edge in edges)
    for count, max_us, buckets in (keyboard, mouse):
        out += struct.pack("<II", count, max_us)
        out += b"".join(struct.pack("<I", value) for value in buckets)
    return out


def _diagnostics_head() -> bytes:
    import struct

    return (
        struct.pack("<BIIIII", 0, 0, 0, 0, 0, 0)
        + struct.pack("<BBIII", 1, 1, 900, 0, 0)
        + struct.pack("<BH", 0, 0)
        + struct.pack("<I", 0)
        + struct.pack("<B", 0)
    )


def test_the_device_reports_its_own_latency_as_counts_not_as_a_number() -> None:
    """A percentile cannot be recovered from a mean, and the device has no room
    to keep every sample. It counts them into buckets instead, and sends the
    bucket edges with them so the host cannot be wrong about what a bucket
    means."""
    from duo_input.device.transactions import parse_diagnostics

    payload = _diagnostics_head() + _latency_block(
        (250, 500, 1000, 2000, 5000, 10000, 20000, 50000),
        (100, 30000, (0, 0, 0, 96, 0, 0, 0, 4, 0)),
        (50, 700, (0, 50, 0, 0, 0, 0, 0, 0, 0)),
    )

    counters = parse_diagnostics(payload)

    assert counters.keyboard_latency is not None
    assert counters.keyboard_latency.count == 100
    assert counters.keyboard_latency.max_us == 30000
    assert counters.keyboard_latency.edges_us[-1] == 50000
    assert counters.mouse_latency is not None
    assert counters.mouse_latency.count == 50
    assert counters.mouse_latency.max_us == 700


def test_the_p95_is_reported_as_the_bucket_it_falls_in_not_a_point() -> None:
    """A histogram brackets a percentile between two edges. Naming a single
    number from inside a bucket would be an estimate dressed as a measurement,
    which is the one thing this acceptance path exists to prevent."""
    from duo_input.device.transactions import parse_diagnostics

    payload = _diagnostics_head() + _latency_block(
        (250, 500, 1000, 2000, 5000, 10000, 20000, 50000),
        (100, 30000, (0, 0, 0, 96, 0, 0, 0, 4, 0)),
        (0, 0, (0,) * 9),
    )

    keyboard = parse_diagnostics(payload).keyboard_latency
    assert keyboard is not None

    # 96 of 100 at or below 2 ms, so 95 of them are: the p95 is bounded by the
    # 2 ms edge and by nothing tighter.
    assert keyboard.p95_upper_bound_us() == 2000
    assert keyboard.at_or_below(2000) == 96


def test_a_p95_past_every_edge_is_reported_as_past_them_rather_than_guessed() -> None:
    from duo_input.device.transactions import parse_diagnostics

    payload = _diagnostics_head() + _latency_block(
        (250, 500, 1000, 2000, 5000, 10000, 20000, 50000),
        (100, 120000, (0, 0, 0, 90, 0, 0, 0, 0, 10)),
        (0, 0, (0,) * 9),
    )

    keyboard = parse_diagnostics(payload).keyboard_latency
    assert keyboard is not None

    # Ten samples in the overflow bucket is more than five, so the p95 is
    # somewhere above 50 ms and this data cannot say where.
    assert keyboard.p95_upper_bound_us() is None


def test_a_bound_that_is_not_a_bucket_edge_is_refused() -> None:
    from duo_input.device.transactions import parse_diagnostics

    payload = _diagnostics_head() + _latency_block(
        (250, 500, 1000, 2000, 5000, 10000, 20000, 50000),
        (10, 100, (10, 0, 0, 0, 0, 0, 0, 0, 0)),
        (0, 0, (0,) * 9),
    )

    keyboard = parse_diagnostics(payload).keyboard_latency
    assert keyboard is not None
    with pytest.raises(ValueError):
        keyboard.at_or_below(17500)


def test_a_percentile_of_no_samples_is_refused_rather_than_reported_as_fast() -> None:
    """A device that saw no input would otherwise look like the fastest device
    ever built."""
    from duo_input.device.transactions import parse_diagnostics

    payload = _diagnostics_head() + _latency_block(
        (250, 500, 1000, 2000, 5000, 10000, 20000, 50000),
        (0, 0, (0,) * 9),
        (0, 0, (0,) * 9),
    )

    keyboard = parse_diagnostics(payload).keyboard_latency
    assert keyboard is not None
    assert keyboard.count == 0
    with pytest.raises(ValueError):
        keyboard.p95_upper_bound_us()


def test_firmware_without_the_latency_block_still_parses() -> None:
    """The emulator has no input pipeline and no peripheral clock, so it sends
    no latency at all. Refusing that reply would make the emulator unreadable
    by the code that reads the device."""
    from duo_input.device.transactions import parse_diagnostics

    counters = parse_diagnostics(_diagnostics_head())

    assert counters.keyboard_latency is None
    assert counters.mouse_latency is None


def test_a_truncated_latency_block_is_refused_rather_than_half_read() -> None:
    """Half a histogram read as a whole one would report a p95 over a sample
    count that was never sent."""
    from duo_input.device.transactions import parse_diagnostics

    payload = _diagnostics_head() + _latency_block(
        (250, 500, 1000, 2000, 5000, 10000, 20000, 50000),
        (10, 100, (10, 0, 0, 0, 0, 0, 0, 0, 0)),
        (0, 0, (0,) * 9),
    )

    with pytest.raises(PayloadError):
        parse_diagnostics(payload[:-4])


def test_a_latency_block_claiming_no_buckets_is_refused() -> None:
    """A device that says it has zero buckets is a device this cannot read, and
    dividing by its sample count later would be worse than saying so now."""
    from duo_input.device.transactions import parse_diagnostics

    with pytest.raises(PayloadError):
        parse_diagnostics(_diagnostics_head() + bytes((0,)))


def _peripheral_block(*ports: tuple[int, int, int, int, int, int, int, bytes]) -> bytes:
    import struct

    out = b""
    for attached, ready, kind, vid, pid, buttons, desc_bytes, digest in ports:
        out += struct.pack(
            "<BBBHHBH32s", attached, ready, kind, vid, pid, buttons, desc_bytes, digest
        )
    return out


def _full_latency() -> bytes:
    return _latency_block(
        (250, 500, 1000, 2000, 5000, 10000, 20000, 50000),
        (0, 0, (0,) * 9),
        (0, 0, (0,) * 9),
    )


def test_the_device_names_the_peripherals_on_its_own_ports() -> None:
    """A device plugged into U1 is on U1's bus, not either computer's, so no
    host can enumerate it. This reply is the only place a compatibility matrix
    can learn what a row is about."""
    from duo_input.device.transactions import parse_diagnostics

    payload = (
        _diagnostics_head()
        + _full_latency()
        + _peripheral_block(
            (1, 1, 1, 0x046D, 0xC31C, 0, 0, bytes(32)),
            (1, 1, 2, 0x1234, 0x5678, 5, 67, bytes(range(32))),
        )
    )

    ports = parse_diagnostics(payload).peripherals

    assert ports is not None
    assert len(ports) == 2
    assert ports[0].vendor_id == 0x046D
    assert ports[0].product_id == 0xC31C
    assert ports[0].kind == "keyboard"
    assert ports[1].kind == "mouse"
    assert ports[1].buttons == 5
    assert ports[1].descriptor_hash == bytes(range(32)).hex()


def test_an_empty_port_is_reported_as_empty_not_omitted() -> None:
    from duo_input.device.transactions import parse_diagnostics

    payload = (
        _diagnostics_head()
        + _full_latency()
        + _peripheral_block(
            (0, 0, 0, 0, 0, 0, 0, bytes(32)),
            (0, 0, 0, 0, 0, 0, 0, bytes(32)),
        )
    )

    ports = parse_diagnostics(payload).peripherals

    assert ports is not None
    assert ports[0].attached is False
    assert ports[0].kind == "none"
    # No descriptor was read, so there is no hash - not the hash of nothing,
    # which every such device would share.
    assert ports[0].descriptor_hash is None


def test_firmware_without_the_peripheral_block_still_parses() -> None:
    from duo_input.device.transactions import parse_diagnostics

    counters = parse_diagnostics(_diagnostics_head() + _full_latency())

    assert counters.peripherals is None


def test_a_truncated_peripheral_block_is_refused() -> None:
    from duo_input.device.transactions import parse_diagnostics

    payload = (
        _diagnostics_head()
        + _full_latency()
        + _peripheral_block((0, 0, 0, 0, 0, 0, 0, bytes(32)))
    )

    with pytest.raises(PayloadError):
        parse_diagnostics(payload)


def test_diagnostics_without_the_endpoint_report_are_still_readable() -> None:
    import struct

    from duo_input.device.transactions import parse_diagnostics

    payload = struct.pack("<BIIIII", 0, 0, 0, 0, 0, 0) + struct.pack("<BBIII", 1, 1, 900, 0, 0)

    counters = parse_diagnostics(payload)

    assert counters.endpoint_answering is True
    assert counters.endpoint_drops is None


# ----------------------------------------------- which backend read the ports


def _backend_block(backend: int, *counters: int) -> bytes:
    """The appended suffix: a backend identifier, a count, then the counters."""
    import struct

    return (
        bytes((backend, len(counters)))
        + b"".join(struct.pack("<I", value) for value in counters)
    )


def _both_ports() -> bytes:
    return _peripheral_block(
        (1, 1, 1, 0x046D, 0xC31C, 0, 0, bytes(32)),
        (1, 1, 2, 0x3434, 0xD030, 5, 67, bytes(range(32))),
    )


def test_firmware_that_names_no_backend_still_parses() -> None:
    """The compatibility direction that is easy to forget.

    Firmware predating the suffix answers with the prefix alone, exactly as it
    always did. Refusing that reply would make this configurator the thing that
    broke, over a field the older device never claimed to have.
    """
    from duo_input.device.transactions import parse_diagnostics

    counters = parse_diagnostics(_diagnostics_head() + _full_latency() + _both_ports())

    assert counters.backend is None
    # And everything in front of the missing suffix is read as it always was.
    assert counters.peripherals is not None
    assert counters.peripherals[0].vendor_id == 0x046D


def test_the_ch375_backend_names_itself_and_publishes_no_counters() -> None:
    """CH375 keeps none of the host-stack counters, and says so with a count of
    zero rather than twelve zeros a reader would take for measurements."""
    from duo_input.device.transactions import parse_diagnostics

    payload = _diagnostics_head() + _full_latency() + _both_ports() + _backend_block(1)

    backend = parse_diagnostics(payload).backend

    assert backend is not None
    assert backend.name == "CH375"
    assert backend.ignored_interfaces is None


def test_the_pio_usb_backend_reports_its_own_counters() -> None:
    from duo_input.device.transactions import parse_diagnostics

    payload = (
        _diagnostics_head()
        + _full_latency()
        + _both_ports()
        + _backend_block(2, 3, 2, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)
    )

    backend = parse_diagnostics(payload).backend

    assert backend is not None
    assert backend.name == "PIO_USB"
    assert backend.ignored_interfaces == 3
    assert backend.ignored_role_already_claimed == 2
    assert backend.event_overflows == 5
    assert backend.detach_overflows == 7
    assert backend.stale_events_discarded == 11
    assert backend.arm_failures == 13
    assert backend.arm_escalations == 17
    assert backend.stall_signals == 19
    assert backend.duplicate_mounts == 23
    assert backend.device_overflows == 29
    assert backend.interface_overflows == 31
    assert backend.callback_overflows == 37


def test_an_ignored_extra_device_says_why_it_was_ignored() -> None:
    """V1 takes one logical keyboard and one logical mouse. A second keyboard is
    ignored deterministically, and a bench needs to tell that apart from an
    interface nothing could classify - a spare keyboard and a broken one."""
    from duo_input.device.transactions import parse_diagnostics

    payload = (
        _diagnostics_head()
        + _full_latency()
        + _both_ports()
        + _backend_block(2, 2, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    )

    backend = parse_diagnostics(payload).backend

    assert backend is not None
    assert backend.ignored_interfaces == 2
    assert backend.ignored_role_already_claimed == 1


def test_no_device_on_either_port_still_names_the_backend() -> None:
    """An empty port is a fact about the run. Which host looked at it is too."""
    from duo_input.device.transactions import parse_diagnostics

    payload = (
        _diagnostics_head()
        + _full_latency()
        + _peripheral_block(
            (0, 0, 0, 0, 0, 0, 0, bytes(32)),
            (0, 0, 0, 0, 0, 0, 0, bytes(32)),
        )
        + _backend_block(2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    )

    counters = parse_diagnostics(payload)

    assert counters.peripherals is not None
    assert counters.peripherals[0].kind == "none"
    assert counters.backend is not None
    assert counters.backend.name == "PIO_USB"


def test_a_backend_this_configurator_does_not_know_is_not_invented() -> None:
    from duo_input.device.transactions import parse_diagnostics

    payload = _diagnostics_head() + _full_latency() + _both_ports() + _backend_block(99)

    backend = parse_diagnostics(payload).backend

    assert backend is not None
    assert backend.name == "unknown"


def test_a_malformed_backend_suffix_does_not_take_the_prefix_down() -> None:
    """The counters, the link state and both ports in front of the suffix are
    complete and correct however garbled the suffix is. Throwing them away
    would lose good readings over a trailing block nobody needs to read."""
    from duo_input.device.transactions import parse_diagnostics

    prefix = _diagnostics_head() + _full_latency() + _both_ports()
    # A declared count of twelve with only one counter behind it.
    truncated = parse_diagnostics(prefix + bytes((2, 12)) + struct.pack("<I", 1))
    # A backend identifier with no count byte at all.
    headless = parse_diagnostics(prefix + bytes((2,)))

    for counters in (truncated, headless):
        assert counters.bad_crc == 0
        assert counters.link_frames_sent == 900
        assert counters.peripherals is not None
        assert counters.peripherals[1].product_id == 0xD030
        # And the suffix is reported as unreadable rather than as absent: a
        # firmware that sent a broken block is not a firmware that sent none.
        assert counters.backend is not None
        assert counters.backend.name == "unreadable"
        assert counters.backend.unreadable_reason
        assert counters.backend.ignored_interfaces is None


def test_a_shorter_counter_run_than_this_configurator_knows_is_read_as_far_as_it_goes() -> None:
    """Append-only in the other direction too: firmware publishing the first few
    counters is read for those, and says nothing about the ones it never sent."""
    from duo_input.device.transactions import parse_diagnostics

    payload = _diagnostics_head() + _full_latency() + _both_ports() + _backend_block(2, 4, 3)

    backend = parse_diagnostics(payload).backend

    assert backend is not None
    assert backend.ignored_interfaces == 4
    assert backend.ignored_role_already_claimed == 3
    assert backend.event_overflows is None


# -------------------------------------- what the host stack and port are doing


def _host_block(
    init_flags: int = 0,
    clock_at_begin: int = 0,
    clock_now: int = 0,
    sof_frames: int = 0,
    root_state: int = 0,
    root_connects: int = 0,
    core1_passes: int = 0,
    mount_events: int | None = None,
    umount_events: int = 0,
    hid_mount_events: int = 0,
    ep_slots_opened: int = 0,
    ep_max_failed_count: int = 0,
    max_pass_gap_us: int = 0,
    max_sof_gap: int = 0,
    root_port_resets: int = 0,
    hub_mount_events: int | None = None,
    ep_slot_map: int | None = None,
    host_event_counts: int = 0,
    enum_progress_mask: int = 0,
    long_pass_count: int = 0,
    long_pass_total_ms: int = 0,
    core1_min_sp: int = 0,
    ep_transfer_flags: int | None = None,
    xfer_completions_at_attach: int = 0,
    enum_stall_recoveries: int = 0,
) -> bytes:
    """The appended host suffix: one length byte, then the fields behind it."""
    import struct

    fields = struct.pack(
        "<BIIIBHI",
        init_flags,
        clock_at_begin,
        clock_now,
        sof_frames,
        root_state,
        root_connects,
        core1_passes,
    )
    if mount_events is not None:
        fields += struct.pack(
            "<HHHBBIHH",
            mount_events,
            umount_events,
            hid_mount_events,
            ep_slots_opened,
            ep_max_failed_count,
            max_pass_gap_us,
            max_sof_gap,
            root_port_resets,
        )
        if hub_mount_events is not None:
            fields += struct.pack("<H", hub_mount_events)
            if ep_slot_map is not None:
                fields += struct.pack(
                    "<IIIIII",
                    ep_slot_map,
                    host_event_counts,
                    enum_progress_mask,
                    long_pass_count,
                    long_pass_total_ms,
                    core1_min_sp,
                )
                if ep_transfer_flags is not None:
                    fields += struct.pack(
                        "<III",
                        ep_transfer_flags,
                        xfer_completions_at_attach,
                        enum_stall_recoveries,
                    )
    return bytes((len(fields),)) + fields


def _empty_host_block() -> bytes:
    """What an image with no host stack to observe sends: a length of zero."""
    return bytes((0,))


def _twelve(backend: int = 2) -> bytes:
    return _backend_block(backend, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)


def test_firmware_that_sends_no_host_block_still_parses() -> None:
    """The compatibility direction that matters most here.

    Every U1 built before this block existed answers with the backend block as
    its last block, exactly as it always did. Refusing that reply would make
    this configurator the thing that broke, over a field the older device never
    claimed to have - and it is also the reply the backend block's own
    length-checking used to insist on.
    """
    from duo_input.device.transactions import parse_diagnostics

    counters = parse_diagnostics(
        _diagnostics_head() + _full_latency() + _both_ports() + _twelve()
    )

    assert counters.host_observation is None
    # And the backend block in front of it is still read exactly as before.
    assert counters.backend is not None
    assert counters.backend.name == "PIO_USB"
    assert counters.backend.callback_overflows == 0


def test_an_image_with_no_host_stack_reports_none_rather_than_zeros() -> None:
    """Zero readings and no readings are different facts about a device.

    The CH375 image has no TinyUSB host, no root port and no input-core backend
    loop. Reporting "Root-port frames sent: 0" for it would be inventing a
    measurement of hardware that is not there - the same rule the backend
    counters already follow with their count of zero.
    """
    from duo_input.device.transactions import parse_diagnostics

    payload = (
        _diagnostics_head()
        + _full_latency()
        + _both_ports()
        + _backend_block(1)
        + _empty_host_block()
    )

    observation = parse_diagnostics(payload).host_observation

    assert observation is not None
    assert observation.state == "none"
    assert observation.sof_frame_count is None
    assert observation.core1_passes is None
    assert observation.clock_hz_now is None


def test_the_host_block_is_read_behind_a_backend_that_publishes_counters() -> None:
    """The backend block stopped being the last block; its count finds the end.

    A reader that took everything after the backend identifier as counters -
    which is what the exact-length check used to amount to - would report this
    whole reply as an unreadable backend block and lose both.
    """
    from duo_input.device.transactions import parse_diagnostics

    payload = (
        _diagnostics_head()
        + _full_latency()
        + _both_ports()
        + _backend_block(2, 3, 2, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)
        + _host_block(
            init_flags=0b1110,
            clock_at_begin=120_000_000,
            clock_now=120_000_000,
            sof_frames=41234,
            root_state=0b1011,
            root_connects=2,
            core1_passes=987_654,
            mount_events=3,
            umount_events=1,
            hid_mount_events=2,
            ep_slots_opened=4,
            ep_max_failed_count=3,
            max_pass_gap_us=450_000,
            max_sof_gap=7,
            root_port_resets=2,
            hub_mount_events=1,
            ep_slot_map=0x0010B9B0,
            host_event_counts=0x00290102,
            enum_progress_mask=0x00001010,
            long_pass_count=2,
            long_pass_total_ms=950,
            core1_min_sp=0x20040A40,
            ep_transfer_flags=0x00270101,
            xfer_completions_at_attach=41,
            enum_stall_recoveries=2,
        )
    )

    diagnostics = parse_diagnostics(payload)

    assert diagnostics.backend is not None
    assert diagnostics.backend.name == "PIO_USB"
    assert diagnostics.backend.ignored_interfaces == 3
    assert diagnostics.backend.callback_overflows == 37

    observation = diagnostics.host_observation
    assert observation is not None
    assert observation.state == "reported"
    assert observation.host_already_active is False
    assert observation.host_configured is True
    assert observation.host_initialized is True
    assert observation.host_inited is True
    # What the reordered image prints: main selected and settled the clock
    # before Core 1 began.
    assert observation.clock_hz_before_core1_change == 120_000_000
    assert observation.clock_hz_now == 120_000_000
    assert observation.sof_frame_count == 41234
    assert observation.root_port_initialized is True
    assert observation.root_port_connected is True
    assert observation.root_port_suspended is False
    assert observation.root_port_fullspeed is True
    assert observation.root_port_connects == 2
    assert observation.core1_passes == 987_654
    assert observation.mount_events == 3
    assert observation.umount_events == 1
    assert observation.hid_mount_events == 2
    assert observation.ep_slots_opened == 4
    assert observation.ep_max_failed_count == 3
    assert observation.max_pass_gap_us == 450_000
    assert observation.max_sof_gap == 7
    assert observation.root_port_resets == 2
    assert observation.hub_mount_events == 1
    # The round-4 window fields. Raw here; the export decodes them, because a
    # packed u32 is not something anybody reads correctly at a bench.
    assert observation.ep_slot_map == 0x0010B9B0
    assert observation.host_event_counts == 0x00290102
    assert observation.enum_progress_mask == 0x00001010
    assert observation.long_pass_count == 2
    assert observation.long_pass_total_ms == 950
    assert observation.core1_min_sp == 0x20040A40
    assert observation.ep_transfer_flags == 0x00270101
    assert observation.xfer_completions_at_attach == 41
    assert observation.enum_stall_recoveries == 2
    assert observation.enum_stall_recoveries == 2
    assert observation.xfer_completions_since_attach == 0


def test_a_host_started_on_the_wrong_core_reads_as_such() -> None:
    """The reading the whole block was added for.

    Bit 0 set says the host stack was already up before the input core reached
    its own bring-up. The three bits behind it still say "healthy", because the
    calls they report really did return true - on an rhport somebody else had
    already activated. Bit 0 is the ONLY field that separates this board from a
    healthy one; see the test below, which is what stops anything from being
    built on the clock pair again.
    """
    from duo_input.device.transactions import parse_diagnostics

    payload = (
        _diagnostics_head()
        + _full_latency()
        + _both_ports()
        + _twelve()
        + _host_block(
            init_flags=0b1111,
            clock_at_begin=125_000_000,
            clock_now=120_000_000,
            sof_frames=880_000,
            root_state=0b0001,
            core1_passes=1_000_000,
        )
    )

    observation = parse_diagnostics(payload).host_observation

    assert observation is not None
    assert observation.host_already_active is True
    assert observation.host_inited is True
    # Frames are being emitted and nothing is attached: the bus is being driven
    # and nothing on it answers, which is a different fault from a bus nobody
    # drives.
    assert observation.sof_frame_count == 880_000
    assert observation.root_port_connected is False


def test_a_legacy_clock_pair_alone_does_not_override_the_host_active_bit() -> None:
    """Older valid firmware reported 125/120, and that remains readable.

    The current image reports 120/120 after moving the clock change to main(),
    but a reader must not reinterpret a legacy pair as a fault. Bit 0 remains
    the only direct reading of whether another path started the host first.

    The two payloads below differ in exactly one bit, and it is not a clock.
    """
    from duo_input.device.transactions import parse_diagnostics

    def observation(init_flags: int):
        payload = (
            _diagnostics_head()
            + _full_latency()
            + _both_ports()
            + _twelve()
            + _host_block(
                init_flags=init_flags,
                clock_at_begin=125_000_000,
                clock_now=120_000_000,
            )
        )
        return parse_diagnostics(payload).host_observation

    healthy = observation(0b1110)
    broken = observation(0b1111)

    assert healthy is not None and broken is not None
    assert healthy.clock_hz_before_core1_change == broken.clock_hz_before_core1_change
    assert healthy.clock_hz_now == broken.clock_hz_now
    assert healthy.host_already_active is False
    assert broken.host_already_active is True
    # And nothing derived from the pair is offered for either of them, because
    # any such reading would have to be wrong about one of the two.
    assert not hasattr(healthy, "clocks_agree")


def test_a_malformed_host_suffix_does_not_take_the_prefix_down() -> None:
    """Everything in front of the host block is complete however garbled it is.

    Reported as unreadable rather than as absent, for the same reason the
    backend block is: a firmware that sent a broken block is not one that sent
    none, and conflating them would hide the defect.
    """
    from duo_input.device.transactions import parse_diagnostics

    prefix = _diagnostics_head() + _full_latency() + _both_ports() + _twelve()
    # A declared length with fewer bytes than that behind it.
    truncated = parse_diagnostics(prefix + bytes((20, 1, 2, 3)))
    # A block shorter than the fields this configurator reads.
    short = parse_diagnostics(prefix + bytes((4, 0, 0, 0, 0)))

    for diagnostics in (truncated, short):
        assert diagnostics.bad_crc == 0
        assert diagnostics.peripherals is not None
        assert diagnostics.backend is not None
        assert diagnostics.backend.name == "PIO_USB"
        assert diagnostics.host_observation is not None
        assert diagnostics.host_observation.state == "unreadable"
        assert diagnostics.host_observation.unreadable_reason
        assert diagnostics.host_observation.sof_frame_count is None


def test_every_complete_and_partial_host_append_boundary_is_classified() -> None:
    """Each field may be absent whole, but never present by half."""
    from duo_input.device.transactions import parse_diagnostics

    prefix = _diagnostics_head() + _full_latency() + _both_ports() + _twelve()
    full_body = _host_block(
        init_flags=0b1110,
        clock_at_begin=120_000_000,
        clock_now=120_000_000,
        sof_frames=450,
        root_state=0b1011,
        root_connects=1,
        core1_passes=99,
        mount_events=2,
        umount_events=3,
        hid_mount_events=4,
        ep_slots_opened=5,
        ep_max_failed_count=6,
        max_pass_gap_us=450_000,
        max_sof_gap=450,
        root_port_resets=7,
        hub_mount_events=8,
        ep_slot_map=0x0010B9B0,
        host_event_counts=0x00290102,
        enum_progress_mask=0x00001010,
        long_pass_count=2,
        long_pass_total_ms=950,
        core1_min_sp=0x20040A40,
        ep_transfer_flags=0x00270101,
        xfer_completions_at_attach=41,
        enum_stall_recoveries=2,
    )[1:]

    complete_boundaries = (
        20, 22, 24, 26, 27, 28, 32, 34, 36, 38, 42, 46, 50, 54, 58, 62, 66, 70, 74,
    )
    partial_boundaries = (
        21, 23, 25, 29, 30, 31, 33, 35, 37,
        39, 40, 41, 43, 44, 45, 47, 48, 49,
        51, 52, 53, 55, 56, 57, 59, 60, 61,
        63, 64, 65, 67, 68, 69, 71, 72, 73,
    )
    for boundary in complete_boundaries:
        block = bytes((boundary,)) + full_body[:boundary]
        observation = parse_diagnostics(prefix + block).host_observation
        assert observation is not None
        assert observation.state == "reported", boundary

    for boundary in partial_boundaries:
        block = bytes((boundary,)) + full_body[:boundary]
        observation = parse_diagnostics(prefix + block).host_observation
        assert observation is not None
        assert observation.state == "unreadable", boundary

    previous_shape = parse_diagnostics(
        prefix + bytes((62,)) + full_body[:62]
    ).host_observation
    assert previous_shape is not None
    assert previous_shape.ep_transfer_flags is None
    assert previous_shape.xfer_completions_at_attach is None

    first_round_five_field = parse_diagnostics(
        prefix + bytes((66,)) + full_body[:66]
    ).host_observation
    assert first_round_five_field is not None
    assert first_round_five_field.ep_transfer_flags == 0x00270101
    assert first_round_five_field.xfer_completions_at_attach is None


def test_a_longer_host_block_than_this_configurator_knows_is_read_as_far_as_it_goes() -> None:
    """Append-only in the other direction: the length byte finds the end.

    A later firmware appending an eighth field must not make this reader refuse
    the seven it does know - that is the same rule that lets older firmware be
    read here at all.
    """
    from duo_input.device.transactions import parse_diagnostics

    import struct

    fields = struct.pack(
        "<BIIIBHIHHHBBIHHHIIIIIIIII",
        0b1110,
        120_000_000,
        120_000_000,
        9,
        0b0011,
        1,
        42,
        2,
        3,
        4,
        5,
        6,
        450_000,
        450,
        7,
        8,
        0x0010B9B0,
        0x00290102,
        0x00001010,
        2,
        950,
        0x20040A40,
        0x00270101,
        41,
        2,
    )
    opaque_future_tail = b"\xA5\x5A\xC3\x3C\x10\x20\x30\x40\x50"
    fields += opaque_future_tail
    payload = (
        _diagnostics_head()
        + _full_latency()
        + _both_ports()
        + _twelve()
        + bytes((len(fields),))
        + fields
    )

    observation = parse_diagnostics(payload).host_observation

    assert observation is not None
    assert observation.state == "reported"
    assert observation.sof_frame_count == 9
    assert observation.core1_passes == 42
    assert observation.mount_events == 2
    assert observation.hub_mount_events == 8
    assert observation.ep_slot_map == 0x0010B9B0
    assert observation.core1_min_sp == 0x20040A40
    assert observation.ep_transfer_flags == 0x00270101
    assert observation.xfer_completions_at_attach == 41
    assert observation.enum_stall_recoveries == 2


def test_the_emulator_and_the_parser_agree_about_the_host_block() -> None:
    """The emulator is the reference payload the firmware is written against.

    A parser that agreed only with a payload this test file built itself would
    prove nothing about either.
    """
    from duo_input.device.emulator import U1Emulator
    from duo_input.device.transactions import parse_diagnostics

    emulator = U1Emulator()
    emulator.input_backend = 2
    emulator.host_observation = (0b1110, 120_000_000, 120_000_000, 4321, 0b1011, 3, 55)

    payload = emulator._handle_get_diagnostics(b"")
    observation = parse_diagnostics(payload).host_observation

    assert observation is not None
    assert observation.state == "reported"
    assert observation.sof_frame_count == 4321
    assert observation.root_port_connects == 3
    assert observation.core1_passes == 55
    # This is also the shape a base-only publication uses (see
    # firmware/u1_main/config_service.hpp's set_host_observation_base): a
    # build that knows only the base reading declares this many bytes, and
    # everything behind it must read as unmeasured, not as zero.
    assert observation.mount_events is None
    assert observation.ep_slot_map is None

    emulator.host_observation = None
    empty = parse_diagnostics(emulator._handle_get_diagnostics(b"")).host_observation
    assert empty is not None
    assert empty.state == "none"


def test_the_emulator_can_speak_the_whole_current_host_block() -> None:
    """The emulator is the reference payload, so it has to reach the last field.

    An emulator frozen at the sixteen-value shape would let every configurator
    test above pass while the eight fields these rounds exist for were never
    carried by anything the parser was pointed at.
    """
    from duo_input.device.emulator import U1Emulator
    from duo_input.device.transactions import parse_diagnostics

    emulator = U1Emulator()
    emulator.input_backend = 2
    emulator.host_observation = (
        0b1110,
        120_000_000,
        120_000_000,
        63_706,
        0b1011,
        1,
        1_628_416,
        0,
        0,
        0,
        3,
        0,
        500_708,
        450,
        1,
        1,
        0x0010B9B0,
        0x00290102,
        0x00001010,
        2,
        950,
        0x20040A40,
        0x00270101,
        41,
        2,
    )

    observation = parse_diagnostics(
        emulator._handle_get_diagnostics(b"")
    ).host_observation

    assert observation is not None
    assert observation.state == "reported"
    assert observation.ep_slot_map == 0x0010B9B0
    assert observation.host_event_counts == 0x00290102
    assert observation.enum_progress_mask == 0x00001010
    assert observation.long_pass_count == 2
    assert observation.long_pass_total_ms == 950
    assert observation.core1_min_sp == 0x20040A40
    assert observation.ep_transfer_flags == 0x00270101
    assert observation.xfer_completions_at_attach == 41
    assert observation.enum_stall_recoveries == 2


# ------------------------------------------------- the reference target's own counters
#
# Task 3's bounded callback queue overflow count and how many of the
# reference target's own USB interfaces earned no logical role were both
# readable in the firmware from the day each was added, and neither had ever
# been read on hardware - there was no CDC path to ask a board for them until
# this block existed. Whether each of the two roles has an owner is what
# tells a route selected by a freshly loaded profile (PC1-only, PC2-only,
# both) apart from one nothing is actually reaching.


def _reference_counters_block(
    callback_overflows: int, ignored_interfaces: int, keyboard_ready: int, mouse_ready: int
) -> bytes:
    """The appended suffix: a one-byte length, then two u32 counters and two
    single-byte flags - the same "length, then that many bytes" shape the
    host block uses, and for the same reason: CH375 and PIO_USB link this
    exact ConfigService and never publish these counters, and the length is
    what lets them say so instead of sending ten zero bytes a reader would
    take for real measurements."""
    import struct

    fields = struct.pack(
        "<IIBB", callback_overflows, ignored_interfaces, keyboard_ready, mouse_ready
    )
    return bytes((len(fields),)) + fields


def _empty_reference_counters_block() -> bytes:
    """What a backend that never calls set_reference_counters sends: a
    length of zero - the regression this whole section exists to catch. See
    ``test_a_backend_that_never_publishes_reference_counters_reports_none``."""
    return bytes((0,))


def test_the_reference_counters_reach_the_configurator() -> None:
    from duo_input.device.transactions import parse_diagnostics

    payload = (
        _diagnostics_head()
        + _full_latency()
        + _both_ports()
        + _twelve(backend=3)
        + _empty_host_block()
        + _reference_counters_block(6, 2, 1, 0)
    )

    counters = parse_diagnostics(payload).reference_counters

    assert counters is not None
    assert counters.state == "reported"
    assert counters.callback_overflows == 6
    assert counters.ignored_interfaces == 2
    assert counters.keyboard_ready is True
    assert counters.mouse_ready is False
    assert counters.unreadable_reason is None


def test_a_backend_that_never_publishes_reference_counters_reports_none() -> None:
    """The regression this whole section exists to catch: CH375 and PIO_USB
    link the exact same ConfigService as the reference target and never call
    set_reference_counters. Their reply must say "none" here, never render as
    zero overflows and two not-ready roles on a board that never measured
    either - the same failure the host block's own "none" state prevents for
    an image with no host stack at all."""
    from duo_input.device.transactions import parse_diagnostics

    payload = (
        _diagnostics_head()
        + _full_latency()
        + _both_ports()
        + _twelve()
        + _empty_host_block()
        + _empty_reference_counters_block()
    )

    counters = parse_diagnostics(payload).reference_counters

    assert counters is not None
    assert counters.state == "none"
    assert counters.callback_overflows is None
    assert counters.ignored_interfaces is None
    assert counters.keyboard_ready is None
    assert counters.mouse_ready is None


def test_firmware_that_predates_the_reference_counters_block_still_parses() -> None:
    """The compatibility direction that matters most here: every U1 built
    before this block existed answers with the host block as its last block,
    exactly as it always did - no bytes at all for this block, not even the
    one-byte "none" marker every build built from this commit now sends."""
    from duo_input.device.transactions import parse_diagnostics

    payload = (
        _diagnostics_head() + _full_latency() + _both_ports() + _twelve() + _empty_host_block()
    )

    counters = parse_diagnostics(payload)

    assert counters.reference_counters is None
    # And everything in front of it is still read exactly as before.
    assert counters.host_observation is not None
    assert counters.host_observation.state == "none"
    assert counters.backend is not None
    assert counters.backend.name == "PIO_USB"


def test_a_short_reference_counters_block_is_unreadable_rather_than_raised() -> None:
    """A block that IS there but cannot be read is reported as such, not
    raised - everything complete and correct in front of it must not be
    thrown away over a broken trailing block."""
    from duo_input.device.transactions import parse_diagnostics

    payload = (
        _diagnostics_head()
        + _full_latency()
        + _both_ports()
        + _twelve()
        + _empty_host_block()
        + bytes((3, 1, 2, 3))  # declares 3 bytes of fields, fewer than the 10 known
    )

    counters = parse_diagnostics(payload)

    assert counters.reference_counters is not None
    assert counters.reference_counters.state == "unreadable"
    assert counters.reference_counters.callback_overflows is None
    assert counters.reference_counters.unreadable_reason is not None
    # Everything in front of it is still complete and correct.
    assert counters.backend is not None
    assert counters.backend.name == "PIO_USB"


def test_the_emulator_and_the_parser_agree_about_the_reference_counters() -> None:
    """The emulator is the reference payload the firmware is written against.

    A parser that agreed only with a payload this test file built itself
    would prove nothing about either.
    """
    from duo_input.device.emulator import U1Emulator
    from duo_input.device.transactions import parse_diagnostics

    emulator = U1Emulator()
    emulator.input_backend = 3
    emulator.reference_counters = (6, 2, 1, 1)

    counters = parse_diagnostics(emulator._handle_get_diagnostics(b"")).reference_counters

    assert counters is not None
    assert counters.state == "reported"
    assert counters.callback_overflows == 6
    assert counters.ignored_interfaces == 2
    assert counters.keyboard_ready is True
    assert counters.mouse_ready is True

    # The emulator's own default - unset, like every backend but the
    # reference target - reports "none", never a shape this configurator
    # would render as real counters.
    emulator.reference_counters = None
    unset = parse_diagnostics(emulator._handle_get_diagnostics(b"")).reference_counters
    assert unset is not None
    assert unset.state == "none"
