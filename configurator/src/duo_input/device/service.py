"""Asynchronous, non-blocking device service for the U1 CDC link.

The service owns exactly one in-flight request at a time, matches every reply
by sequence number, applies a per-command timeout and drives multi-step
transfers as continuations. It never sleeps, never spins and never blocks the
Qt event loop: every step is entered from a signal or a timer.

It depends only on the link boundary described in
:mod:`duo_input.device.qt_transport`, so the production ``QSerialPortTransport``
and the deterministic ``U1Emulator`` are interchangeable.
"""

from __future__ import annotations

import hashlib
import struct
from enum import StrEnum

from PySide6.QtCore import QObject, QTimer, Signal

from duo_input.generated.protocol import (
    BINARY_CONFIG_MAX_BYTES,
    CONFIG_CHUNK_MAX_BYTES,
    PROTOCOL_VERSION_MAJOR,
    Capability,
    CdcMessageType,
)
from duo_input.protocol.frame import CdcFrame, encode_cdc_frame

from .qt_transport import SynchronousTransportLink
from .transactions import (
    ErrorCode,
    FailureReason,
    FrameAssembler,
    FrameOverflowError,
    OperationFailure,
    OperationResult,
    PayloadError,
    SequenceGenerator,
    Transaction,
    parse_chunk_ack,
    parse_config_info,
    parse_device_info,
    parse_diagnostics,
    parse_hid_descriptor_capture,
    parse_read_chunk,
    parse_status,
    percentage,
    read_chunk_payload,
    reply_error,
    write_begin_payload,
    write_chunk_payload,
)
from .transport import AbstractByteTransport

DEFAULT_TIMEOUT_MS = 2000
REQUESTED_CAPABILITIES = sum(int(capability) for capability in Capability)

_CONNECT = "connect_device"
_READ_CONFIG = "read_config"
_WRITE_CONFIG = "write_config"
_BEGIN_CAPTURE = "begin_capture"
_TEST_MACRO = "test_macro"
_STOP_AND_RELEASE_ALL = "stop_and_release_all"
_GET_DIAGNOSTICS = "get_diagnostics"


class DeviceState(StrEnum):
    """Coarse lifecycle state of the link, never localised."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    READY = "ready"
    BUSY = "busy"


class DeviceService(QObject):
    """Talks to one U1 over an asynchronous byte link."""

    state_changed = Signal(object)
    status_changed = Signal(object)
    capture_received = Signal(bytes)
    progress_changed = Signal(int)
    operation_failed = Signal(object)
    operation_succeeded = Signal(object)

    def __init__(self, timeout_ms: int = DEFAULT_TIMEOUT_MS, parent: QObject | None = None) -> None:
        super().__init__(parent)
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        self._timeout_ms = timeout_ms
        self._link = None
        self._state = DeviceState.DISCONNECTED
        self._assembler = FrameAssembler()
        self._sequence = SequenceGenerator()
        self._pending: Transaction | None = None
        self._operation: str | None = None
        self._deferred_failure: OperationFailure | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)

        self._device_info = None
        self._status = None
        self._diagnostics = None
        self._hid_descriptor_capture = None
        self._captures_received = 0
        self._capture_decision = "none"
        self._capture_payload = b""
        self._device_hash = b""
        self._last_progress: int | None = None

        self._read_buffer = bytearray()
        self._read_info = None
        self._write_package = b""
        self._write_digest = b""
        self._write_offset = 0
        self._write_chunk_length = 0
        self._staging_open = False
        self._abort_requested = False

    # ------------------------------------------------------------------ state

    @property
    def capture_observation(self) -> str:
        return f"received={self._captures_received} decision={self._capture_decision} payload={self._capture_payload.hex(' ')}"

    def note_capture_decision(self, decision: str) -> None:
        """The listening dialog reports whether its filter accepted the reply."""
        self._capture_decision = decision

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._link is not None and self._link.is_open

    @property
    def device_info(self):
        return self._device_info

    @property
    def status(self):
        return self._status

    @property
    def diagnostics(self):
        """Counters from the last ``get_diagnostics``; ``None`` until asked for."""
        return self._diagnostics

    @property
    def hid_descriptor_capture(self):
        """Optional report-descriptor evidence fetched with diagnostics."""
        return self._hid_descriptor_capture

    @property
    def device_hash(self) -> bytes:
        """Last hash the device reported for its active configuration."""
        return self._device_hash

    # ------------------------------------------------------------- public API

    def connect_device(self, transport) -> None:
        """Open ``transport`` and negotiate a session.

        ``transport`` may be any link (see ``qt_transport.DeviceLink``) or a
        synchronous :class:`AbstractByteTransport`, which is wrapped for you.
        """
        if self._link is not None:
            self.operation_failed.emit(OperationFailure(_CONNECT, FailureReason.BUSY))
            return
        link = (
            SynchronousTransportLink(transport, parent=self)
            if isinstance(transport, AbstractByteTransport)
            else transport
        )
        link.bytes_received.connect(self._on_bytes_received)
        link.link_lost.connect(self._on_link_lost)
        self._link = link
        self._assembler.clear()
        self._sequence = SequenceGenerator()
        self._device_info = None
        self._status = None
        self._diagnostics = None
        self._hid_descriptor_capture = None
        self._captures_received = 0
        self._capture_decision = "none"
        self._capture_payload = b""
        if not link.open():
            self._teardown_link()
            self.operation_failed.emit(
                OperationFailure(_CONNECT, FailureReason.NOT_CONNECTED, detail="port did not open")
            )
            return
        self._operation = _CONNECT
        self._set_state(DeviceState.CONNECTING)
        self._request(
            CdcMessageType.HELLO,
            CdcMessageType.DEVICE_INFO,
            struct.pack("<I", REQUESTED_CAPABILITIES),
            self._on_hello,
        )

    def disconnect_device(self) -> None:
        """Close the link without reporting a failure for an idle service."""
        self._operation = None
        self._deferred_failure = None
        self._teardown_link()

    def read_config(self) -> None:
        if not self._begin_operation(_READ_CONFIG):
            return
        self._read_buffer = bytearray()
        self._read_info = None
        self._last_progress = None
        self._request(
            CdcMessageType.READ_CONFIG_BEGIN,
            CdcMessageType.READ_CONFIG_BEGIN,
            b"",
            self._on_read_begin,
        )

    def write_config(self, package: bytes) -> None:
        if not self._begin_operation(_WRITE_CONFIG):
            return
        if not isinstance(package, (bytes, bytearray, memoryview)):
            self._fail(FailureReason.INVALID_PACKAGE, detail="package must be bytes-like")
            return
        package = bytes(package)
        if not package or len(package) > BINARY_CONFIG_MAX_BYTES:
            self._fail(FailureReason.INVALID_PACKAGE, detail="package size is out of range")
            return
        self._write_package = package
        self._write_digest = hashlib.sha256(package).digest()
        self._write_offset = 0
        self._write_chunk_length = 0
        self._staging_open = False
        self._abort_requested = False
        self._last_progress = None
        self._request(
            CdcMessageType.WRITE_BEGIN,
            CdcMessageType.WRITE_BEGIN,
            write_begin_payload(len(package), self._write_digest),
            self._on_write_begin,
        )

    def abort_write(self) -> bool:
        """Ask an in-flight ``write_config`` to stop; nothing is committed."""
        if self._operation != _WRITE_CONFIG or self._abort_requested:
            return False
        self._abort_requested = True
        return True

    def begin_capture(self) -> None:
        if not self._begin_operation(_BEGIN_CAPTURE):
            return
        if self._device_info is not None and self._device_info.capabilities & Capability.DIAGNOSTICS:
            self._request(CdcMessageType.GET_DIAGNOSTICS, CdcMessageType.GET_DIAGNOSTICS, b"", self._capture_after_inventory)
            return
        self._request_capture_begin()

    def _capture_after_inventory(self, payload: bytes) -> None:
        self._diagnostics = parse_diagnostics(payload)
        self.status_changed.emit(self._status)
        self._request_capture_begin()

    def _request_capture_begin(self) -> None:
        self._request(
            CdcMessageType.CAPTURE_BEGIN, CdcMessageType.CAPTURE_BEGIN, b"", self._on_acknowledged
        )

    def test_macro(self, profile_id: int, macro_id: int) -> None:
        """Run one macro that is already committed on the device.

        Nothing is uploaded: the device runs the macro from its active
        configuration, so what runs is what was written, not what is being
        edited.
        """
        if not self._begin_operation(_TEST_MACRO):
            return
        if not (1 <= profile_id <= 0xFF and 1 <= macro_id <= 0xFF):
            self._fail(
                FailureReason.INVALID_PACKAGE,
                detail="profile and macro identifiers are 1..255",
            )
            return
        self._request(
            CdcMessageType.TEST_MACRO,
            CdcMessageType.TEST_MACRO,
            bytes((profile_id, macro_id)),
            self._on_acknowledged,
        )

    def stop_and_release_all(self) -> None:
        if not self._begin_operation(_STOP_AND_RELEASE_ALL):
            return
        self._request(
            CdcMessageType.STOP_AND_RELEASE_ALL,
            CdcMessageType.STOP_AND_RELEASE_ALL,
            b"",
            self._on_acknowledged,
        )

    def get_diagnostics(self) -> None:
        if not self._begin_operation(_GET_DIAGNOSTICS):
            return
        self._request(
            CdcMessageType.GET_DIAGNOSTICS,
            CdcMessageType.GET_DIAGNOSTICS,
            b"",
            self._on_diagnostics,
        )

    # ------------------------------------------------------------- link plumbing

    def _request(
        self,
        request_type: CdcMessageType,
        reply_type: CdcMessageType,
        payload: bytes,
        on_reply,
        ignore_device_error: bool = False,
    ) -> None:
        sequence = self._sequence.next()
        self._pending = Transaction(
            self._operation or "",
            request_type,
            reply_type,
            sequence,
            payload,
            on_reply,
            ignore_device_error,
        )
        wire = encode_cdc_frame(CdcFrame(request_type, sequence, payload))
        self._timer.start(self._timeout_ms)
        self._link.send(wire)

    def _on_bytes_received(self, data: bytes) -> None:
        try:
            scan = self._assembler.push(bytes(data))
        except FrameOverflowError as error:
            self._fail(FailureReason.BAD_FRAME, detail=str(error))
            return
        for frame in scan.frames:
            self._handle_frame(frame)
            if self._link is None:
                return
        if scan.discarded and not scan.frames and self._pending is not None:
            # Bytes that are not a frame are only noise while they sit beside
            # one: a trace line ahead of a reply costs nothing once the reply
            # behind it has been read. With no frame in this read at all and a
            # request still outstanding, the same bytes are the reply - damaged
            # - and the caller is owed the decoder's reason now rather than a
            # bare timeout two seconds later.
            self._fail(FailureReason.BAD_FRAME, detail=scan.discarded[-1])

    def _handle_frame(self, frame: CdcFrame) -> None:
        if frame.type is CdcMessageType.CAPTURE_EVENT:
            # Device-initiated: it owns the sequence counter for this frame.
            self._sequence.align_after(frame.sequence)
            self._captures_received += 1
            self._capture_payload = bytes(frame.payload[:8])
            self._capture_decision = "no listening dialog"
            self.capture_received.emit(bytes(frame.payload))
            return
        pending = self._pending
        if pending is None:
            return  # a late or duplicated reply for a request we already closed
        if frame.sequence != pending.sequence:
            self._fail(
                FailureReason.SEQUENCE_MISMATCH,
                detail=f"expected sequence {pending.sequence}, received {frame.sequence}",
            )
            return
        if frame.type is not pending.reply_type:
            self._fail(
                FailureReason.UNEXPECTED_REPLY,
                detail=f"expected {pending.reply_type.name}, received {frame.type.name}",
            )
            return
        self._timer.stop()
        self._pending = None
        # No align_after here: the reply echoes a sequence this host allocated,
        # so the counter is already past it. Realigning would rewind it behind a
        # capture event that the device flushed ahead of this reply.
        payload = bytes(frame.payload)
        try:
            error = reply_error(payload)
        except PayloadError as failure:
            self._fail(FailureReason.BAD_PAYLOAD, detail=str(failure))
            return
        if error is not ErrorCode.OK and not pending.ignore_device_error:
            self._fail(FailureReason.DEVICE_ERROR, error_code=error)
            return
        try:
            pending.on_reply(payload)
        except PayloadError as failure:
            self._fail(FailureReason.BAD_PAYLOAD, detail=str(failure))

    def _on_timeout(self) -> None:
        self._pending = None
        self._fail(FailureReason.TIMEOUT)

    def _on_link_lost(self, detail: str = "") -> None:
        operation = self._operation
        self._operation = None
        deferred = self._deferred_failure
        self._deferred_failure = None
        self._teardown_link()
        if deferred is not None:
            self.operation_failed.emit(deferred)
        elif operation is not None:
            self.operation_failed.emit(
                OperationFailure(operation, FailureReason.LINK_LOST, detail=detail)
            )

    def _teardown_link(self) -> None:
        link = self._link
        self._link = None
        self._pending = None
        self._staging_open = False
        # Counters belong to the device that reported them, not to the host.
        self._diagnostics = None
        self._hid_descriptor_capture = None
        self._timer.stop()
        self._assembler.clear()
        if link is not None:
            for signal, slot in (
                (link.bytes_received, self._on_bytes_received),
                (link.link_lost, self._on_link_lost),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):  # pragma: no cover - already detached
                    pass
            link.close()
        self._set_state(DeviceState.DISCONNECTED)

    # ------------------------------------------------------------- bookkeeping

    def _set_state(self, state: DeviceState) -> None:
        if state is not self._state:
            self._state = state
            self.state_changed.emit(state)

    def _begin_operation(self, operation: str) -> bool:
        if self._link is None or not self._link.is_open:
            self.operation_failed.emit(OperationFailure(operation, FailureReason.NOT_CONNECTED))
            return False
        if self._operation is not None:
            self.operation_failed.emit(OperationFailure(operation, FailureReason.BUSY))
            return False
        self._operation = operation
        self._set_state(DeviceState.BUSY)
        return True

    def _emit_progress(self, value: int) -> None:
        if value != self._last_progress:
            self._last_progress = value
            self.progress_changed.emit(value)

    def _finish_success(self, value: object = None) -> None:
        operation = self._operation or ""
        self._operation = None
        self._set_state(DeviceState.READY if self.is_connected else DeviceState.DISCONNECTED)
        self.operation_succeeded.emit(OperationResult(operation, value))

    def _fail(
        self,
        reason: FailureReason,
        error_code: ErrorCode | None = None,
        detail: str = "",
    ) -> None:
        self._timer.stop()
        self._pending = None
        # Any retained bytes belong to the request being abandoned; keeping them
        # would prefix the next reply and break every later operation.
        self._assembler.clear()
        if self._deferred_failure is not None:
            # The cleanup issued for an earlier failure itself failed; the
            # original cause is what the caller needs to see.
            failure, self._deferred_failure = self._deferred_failure, None
            self._finish_failure(failure)
            return
        failure = OperationFailure(self._operation or "", reason, error_code, detail)
        if self._staging_open and self.is_connected:
            # A partial transfer must never be left staged on the device.
            self._staging_open = False
            self._deferred_failure = failure
            self._request(
                CdcMessageType.WRITE_ABORT,
                CdcMessageType.WRITE_ABORT,
                b"",
                self._on_cleanup_aborted,
                ignore_device_error=True,
            )
            return
        self._finish_failure(failure)

    def _on_cleanup_aborted(self, payload: bytes) -> None:
        failure, self._deferred_failure = self._deferred_failure, None
        self._finish_failure(failure)

    def _finish_failure(self, failure: OperationFailure) -> None:
        self._operation = None
        self._staging_open = False
        self._write_package = b""
        if failure.operation == _CONNECT:
            # Never keep a link to a device we could not negotiate with.
            self._teardown_link()
        else:
            self._set_state(DeviceState.READY if self.is_connected else DeviceState.DISCONNECTED)
        self.operation_failed.emit(failure)

    # ------------------------------------------------------------ continuations

    def _on_hello(self, payload: bytes) -> None:
        info = parse_device_info(payload)
        if info.protocol_major != PROTOCOL_VERSION_MAJOR:
            self._fail(
                FailureReason.PROTOCOL_MISMATCH,
                detail=f"device reports protocol major {info.protocol_major}",
            )
            return
        self._device_info = info
        self._device_hash = info.active_hash
        self._request(
            CdcMessageType.GET_STATUS, CdcMessageType.GET_STATUS, b"", self._on_connect_status
        )

    def _on_connect_status(self, payload: bytes) -> None:
        self._status = parse_status(payload)
        self.status_changed.emit(self._status)
        self._finish_success(self._device_info)

    def _on_acknowledged(self, payload: bytes) -> None:
        self._finish_success(None)

    def _on_diagnostics(self, payload: bytes) -> None:
        self._diagnostics = parse_diagnostics(payload)
        if self._device_info is not None and (
            self._device_info.capabilities & int(Capability.HID_DESCRIPTOR_DIAGNOSTICS)
        ):
            self._request(
                CdcMessageType.GET_HID_DESCRIPTOR_CAPTURE,
                CdcMessageType.GET_HID_DESCRIPTOR_CAPTURE,
                b"",
                self._on_hid_descriptor_capture,
            )
            return
        self._finish_success(self._diagnostics)

    def _on_hid_descriptor_capture(self, payload: bytes) -> None:
        self._hid_descriptor_capture = parse_hid_descriptor_capture(payload)
        self._finish_success(self._diagnostics)

    # read -------------------------------------------------------------------

    def _on_read_begin(self, payload: bytes) -> None:
        info = parse_config_info(payload)
        if info.size > BINARY_CONFIG_MAX_BYTES:
            self._fail(
                FailureReason.BAD_PAYLOAD,
                detail=f"device reported a {info.size}-byte configuration",
            )
            return
        self._read_info = info
        self._emit_progress(0)
        self._read_next_chunk()

    def _read_next_chunk(self) -> None:
        remaining = self._read_info.size - len(self._read_buffer)
        if remaining <= 0:
            self._finish_read()
            return
        self._request(
            CdcMessageType.READ_CONFIG_CHUNK,
            CdcMessageType.READ_CONFIG_CHUNK,
            read_chunk_payload(len(self._read_buffer), min(CONFIG_CHUNK_MAX_BYTES, remaining)),
            self._on_read_chunk,
        )

    def _on_read_chunk(self, payload: bytes) -> None:
        offset, chunk = parse_read_chunk(payload)
        if offset != len(self._read_buffer) or not chunk:
            self._fail(FailureReason.ACK_MISMATCH, detail=f"unexpected chunk at offset {offset}")
            return
        self._read_buffer.extend(chunk)
        self._emit_progress(percentage(len(self._read_buffer), self._read_info.size))
        self._read_next_chunk()

    def _finish_read(self) -> None:
        package = bytes(self._read_buffer)
        if hashlib.sha256(package).digest() != self._read_info.digest:
            self._fail(FailureReason.READBACK_MISMATCH, detail="read-back digest mismatch")
            return
        self._device_hash = self._read_info.digest
        self._emit_progress(100)
        self._finish_success(package)

    # write ------------------------------------------------------------------

    def _on_write_begin(self, payload: bytes) -> None:
        self._staging_open = True
        self._emit_progress(0)
        self._write_next_chunk()

    def _write_next_chunk(self) -> None:
        if self._abort_requested:
            self._fail(FailureReason.ABORTED, detail="write aborted by the operator")
            return
        if self._write_offset >= len(self._write_package):
            self._request(
                CdcMessageType.WRITE_VERIFY,
                CdcMessageType.WRITE_VERIFY,
                b"",
                self._on_write_verified,
            )
            return
        chunk = self._write_package[
            self._write_offset : self._write_offset + CONFIG_CHUNK_MAX_BYTES
        ]
        self._write_chunk_length = len(chunk)
        self._request(
            CdcMessageType.WRITE_CHUNK,
            CdcMessageType.WRITE_CHUNK,
            write_chunk_payload(self._write_offset, chunk),
            self._on_write_chunk,
        )

    def _on_write_chunk(self, payload: bytes) -> None:
        acknowledged = parse_chunk_ack(payload)
        expected = self._write_offset + self._write_chunk_length
        if acknowledged != expected:
            self._fail(
                FailureReason.ACK_MISMATCH,
                detail=f"device acknowledged {acknowledged}, expected {expected}",
            )
            return
        self._write_offset = acknowledged
        self._emit_progress(percentage(self._write_offset, len(self._write_package)))
        self._write_next_chunk()

    def _on_write_verified(self, payload: bytes) -> None:
        if self._abort_requested:
            self._fail(FailureReason.ABORTED, detail="write aborted by the operator")
            return
        self._request(
            CdcMessageType.WRITE_COMMIT, CdcMessageType.WRITE_COMMIT, b"", self._on_write_committed
        )

    def _on_write_committed(self, payload: bytes) -> None:
        self._staging_open = False
        self._request(
            CdcMessageType.GET_ACTIVE_CONFIG_INFO,
            CdcMessageType.GET_ACTIVE_CONFIG_INFO,
            b"",
            self._on_write_read_back,
        )

    def _on_write_read_back(self, payload: bytes) -> None:
        info = parse_config_info(payload)
        if info.digest != self._write_digest or info.size != len(self._write_package):
            # device_hash deliberately keeps the last confirmed value.
            self._fail(
                FailureReason.READBACK_MISMATCH,
                detail="the device did not report the package that was written",
            )
            return
        self._device_hash = info.digest
        self._emit_progress(100)
        self._finish_success(info.digest)


__all__ = [
    "DEFAULT_TIMEOUT_MS",
    "REQUESTED_CAPABILITIES",
    "DeviceService",
    "DeviceState",
]
