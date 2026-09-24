"""Diagnostic-only TLS/QSslSocket versus plain/QTcpSocket transport probe.

This module lives outside ``duo_input`` and is never imported by the
application. TLS runs use the production ``PeerLink`` and ``PeerListener``.
Plain runs use a test-only ``PeerLink`` subclass which replaces only the Qt
socket/handshake layer; framing, serialization, queue bounds, readyRead path,
and ``FileTransferService`` remain the production implementations.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal
from PySide6.QtNetwork import (
    QAbstractSocket,
    QHostAddress,
    QSslSocket,
    QTcpServer,
    QTcpSocket,
)

from duo_input.clipboard.identity import NodeIdentity, load_or_create
from duo_input.clipboard.listener import PeerListener
from duo_input.clipboard.peer import PeerLink, READ_BUFFER_BYTES
from duo_input.clipboard.wire import CAPABILITY_FILES, Message, MessageType
from duo_input.transfer.fileprovider_perf import PerfEmitter
from duo_input.transfer.model import ENTRY_FILE, decode_manifest
from duo_input.transfer.service import FileTransferService

from transport_socket_buffer_probe import (
    RecordingPerfEmitter,
    _gap_summary,
    _lag_summary,
    _source_digest,
    summarize_receive_boundaries,
)
from transport_window_probe import (
    MIB,
    RUN_READ_ID_STRIDE,
    WindowRequester,
    _configure_logging,
    _write_json,
    deterministic_digest,
)


DEFAULT_TLS_PORT = 24866
DEFAULT_TCP_PORT = 24867
DEFAULT_SIZE_MIB = 128
PER_FILE_WINDOW = 1
WARMUP_BYTES = 8 * MIB
CONNECT_TIMEOUT_MS = 30_000
OFFER_TIMEOUT_MS = 30_000
RUN_TIMEOUT_MS = 10 * 60_000
CAPS = frozenset({CAPABILITY_FILES})

logger = logging.getLogger("duo_input.transport_tls_tcp_probe")


class TransportKind(str, Enum):
    TLS = "tls"
    PLAIN = "plain"


@dataclass(frozen=True)
class RunSpec:
    name: str
    transport: TransportKind
    measured: bool
    connection_number: int


RUN_SEQUENCE = (
    RunSpec("TLS_WARMUP", TransportKind.TLS, False, 1),
    RunSpec("TCP_WARMUP", TransportKind.PLAIN, False, 2),
    RunSpec("A1", TransportKind.TLS, True, 3),
    RunSpec("B1", TransportKind.PLAIN, True, 4),
    RunSpec("B2", TransportKind.PLAIN, True, 5),
    RunSpec("A2", TransportKind.TLS, True, 6),
    RunSpec("A3", TransportKind.TLS, True, 7),
    RunSpec("B3", TransportKind.PLAIN, True, 8),
)
MEASURED_RUNS = tuple(run for run in RUN_SEQUENCE if run.measured)


class ProbeError(RuntimeError):
    """The bounded diagnostic contract could not be preserved."""


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[max(0, math.ceil(fraction * len(ordered)) - 1)])


def _summary(values: Sequence[float]) -> dict[str, float]:
    return {
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "max": max(values, default=0.0),
    }


def _socket_configuration(link: PeerLink) -> dict[str, object]:
    socket = link._socket
    if socket is None:
        raise ProbeError("connected diagnostic socket unavailable")

    def option(name: QAbstractSocket.SocketOption) -> int:
        return int(socket.socketOption(name))

    return {
        "descriptor": int(socket.socketDescriptor()),
        "socket_class": type(socket).__name__,
        "sndbuf_effective": option(QAbstractSocket.SocketOption.SendBufferSizeSocketOption),
        "rcvbuf_effective": option(QAbstractSocket.SocketOption.ReceiveBufferSizeSocketOption),
        "tcp_nodelay": option(QAbstractSocket.SocketOption.LowDelayOption),
        "so_keepalive": option(QAbstractSocket.SocketOption.KeepAliveOption),
    }


class PlainPeerLink(PeerLink):
    """Test-only PeerLink whose byte stream is a plain ``QTcpSocket``."""

    def create_outbound_socket(self) -> QTcpSocket:
        return QTcpSocket(self)

    @property
    def is_open(self) -> bool:
        socket = self._socket
        return (
            socket is not None
            and socket.state() is QAbstractSocket.SocketState.ConnectedState
        )

    def connect_to(
        self, address: str, port: int, expected_fingerprint: str | None = None
    ) -> None:
        if expected_fingerprint is not None:
            raise ProbeError("plain diagnostic link cannot accept a TLS fingerprint")
        self._direction = "outbound-diagnostic-plain"
        socket = self.create_outbound_socket()
        self._wire_up_plain(socket, emit_connected=True)
        socket.connectToHost(address, port)

    def adopt_plain(self, socket: QTcpSocket) -> None:
        self._direction = "inbound-diagnostic-plain"
        self._wire_up_plain(socket, emit_connected=False)
        self._on_plain_connected()

    def _wire_up_plain(self, socket: QTcpSocket, *, emit_connected: bool) -> None:
        self._socket = socket
        self._pending_write_frames.clear()
        socket.setParent(self)
        socket.setReadBufferSize(READ_BUFFER_BYTES)
        socket.bytesWritten.connect(
            lambda count, bound_socket=socket: self._on_bytes_written(
                bound_socket, int(count)
            )
        )
        if emit_connected:
            socket.connected.connect(self._on_plain_connected)
        socket.readyRead.connect(self._on_ready_read)
        socket.disconnected.connect(lambda: self._fail("соединение закрыто"))
        self._check_congestion()

    def _on_plain_connected(self) -> None:
        socket = self._socket
        if socket is None:
            return
        socket.setSocketOption(QAbstractSocket.SocketOption.KeepAliveOption, 1)
        self.connected.emit("")


def make_diagnostic_link(
    transport: TransportKind,
    identity: NodeIdentity,
    parent: QObject | None = None,
    *,
    perf: PerfEmitter | None = None,
) -> PeerLink:
    if transport is TransportKind.TLS:
        return PeerLink(identity, parent, perf=perf)
    if transport is TransportKind.PLAIN:
        return PlainPeerLink(identity, parent, perf=perf)
    raise ProbeError(f"unknown transport: {transport!r}")


def bind_recording_perf(link: PeerLink, perf: PerfEmitter) -> None:
    """Make socket/framing and file service events share one run timeline."""
    link._perf = perf


class PlainListener(QObject):
    link_ready = Signal(object)

    def __init__(self, identity: NodeIdentity, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._identity = identity
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._on_pending)
        self._links: list[PlainPeerLink] = []

    @property
    def port(self) -> int:
        return int(self._server.serverPort())

    def listen(self, port: int) -> bool:
        return self._server.listen(QHostAddress.SpecialAddress.Any, port)

    def stop(self) -> None:
        for link in self._links:
            link.close()
        self._links.clear()
        self._server.close()

    def _on_pending(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                return
            link = PlainPeerLink(self._identity, self)
            link.adopt_plain(socket)
            self._links.append(link)
            link.disconnected.connect(lambda _reason, item=link: self._forget(item))
            self.link_ready.emit(link)

    def _forget(self, link: PlainPeerLink) -> None:
        if link in self._links:
            self._links.remove(link)


class QueueDrainTracker:
    def __init__(self, clock: Callable[[], int] = time.perf_counter_ns) -> None:
        self._clock = clock
        self._active = False

    def start(self, queue_bytes: int) -> None:
        if self._active:
            raise ProbeError("queue tracker already active")
        self._active = True
        self._last_ns = self._clock()
        self._last_queue = int(queue_bytes)
        self._nonzero_ns = 0
        self._samples = [float(queue_bytes)]
        self._high_water = int(queue_bytes)
        self._bytes_written = 0
        self._write_ns: list[int] = []

    def _observe(self, queue_bytes: int) -> int:
        if not self._active or queue_bytes < 0:
            raise ProbeError("invalid queue tracker observation")
        now = self._clock()
        if self._last_queue > 0:
            self._nonzero_ns += now - self._last_ns
        self._last_ns = now
        self._last_queue = int(queue_bytes)
        self._samples.append(float(queue_bytes))
        self._high_water = max(self._high_water, int(queue_bytes))
        return now

    def observe_enqueue(self, queue_bytes: int) -> None:
        self._observe(queue_bytes)

    def observe_bytes_written(self, count: int, queue_bytes: int) -> None:
        if count < 0:
            raise ProbeError("negative bytesWritten count")
        now = self._observe(queue_bytes)
        self._bytes_written += int(count)
        self._write_ns.append(now)

    def finish(self, queue_bytes: int) -> dict[str, object]:
        self._observe(queue_bytes)
        self._active = False
        cadence = [
            (later - earlier) / 1_000_000
            for earlier, later in zip(self._write_ns, self._write_ns[1:])
        ]
        return {
            "bytes_to_write": _summary(self._samples),
            "queue_high_water": self._high_water,
            "time_queue_nonzero_seconds": self._nonzero_ns / 1_000_000_000,
            "bytes_written": self._bytes_written,
            "bytes_written_cadence_ms": _summary(cadence),
        }


class PipelineRecordingPerfEmitter(RecordingPerfEmitter):
    _RECORDED = RecordingPerfEmitter._RECORDED | frozenset(
        {"file_read_bytes_available", "socket_write_complete"}
    )


def _events_by_read(events: Sequence[dict[str, object]], name: str) -> dict[int, int]:
    result: dict[int, int] = {}
    for event in events:
        if event.get("event") != name:
            continue
        read_id = event.get("read_id")
        mono_ns = event.get("mono_ns")
        if isinstance(read_id, int) and isinstance(mono_ns, int):
            result[read_id] = mono_ns
    return result


def summarize_windows_processing(
    events: Sequence[dict[str, object]], first_read_id: int, last_read_id: int
) -> dict[str, float]:
    starts = _events_by_read(events, "file_read_bytes_available")
    ends = _events_by_read(events, "socket_write_complete")
    durations = [
        (ends[read_id] - starts[read_id]) / 1_000_000
        for read_id in range(first_read_id, last_read_id + 1)
        if read_id in starts and read_id in ends and ends[read_id] >= starts[read_id]
    ]
    return _summary(durations)


def _spec_for(name: object, transport: object) -> RunSpec | None:
    for spec in RUN_SEQUENCE:
        if spec.name == name and spec.transport.value == transport:
            return spec
    return None


def serve(args: argparse.Namespace) -> int:
    _configure_logging(args.log)
    size = args.size_mib * MIB
    source = args.source.resolve()
    digest = _source_digest(source, size, args.recreate)
    app = QCoreApplication.instance() or QCoreApplication([])
    identity = load_or_create(args.state_dir / "identity")
    tls_listener = PeerListener(identity)
    tls_listener.expect(None)
    plain_listener = PlainListener(identity)
    if not tls_listener.listen(args.tls_port):
        raise RuntimeError(f"cannot listen on TLS port {args.tls_port}")
    if not plain_listener.listen(args.tcp_port):
        tls_listener.stop()
        raise RuntimeError(f"cannot listen on TCP port {args.tcp_port}")

    state: dict[str, object] = {
        "source": str(source),
        "size": size,
        "sha256": digest,
        "fingerprint": identity.fingerprint,
        "tls_port": tls_listener.port,
        "tcp_port": plain_listener.port,
        "runs": {},
        "warmups": {},
        "connections": [],
        "failure": None,
    }
    services: list[FileTransferService] = []
    accepted_connections = {"count": 0}

    def fail(link: PeerLink, reason: str) -> None:
        state["failure"] = reason
        logger.error("TLS/TCP probe failed: %s", reason)
        _write_json(args.result, state)
        link.close()
        app.exit(2)

    def on_link(link: PeerLink, transport: TransportKind) -> None:
        accepted_connections["count"] += 1
        connection_number = accepted_connections["count"]
        perf = PipelineRecordingPerfEmitter(logger, "windows_python_monotonic")
        bind_recording_perf(link, perf)
        service = FileTransferService(perf=perf)
        services.append(service)
        service.attach_link(link)
        service.set_peer_capabilities(CAPS)
        link.message_received.connect(service.handle_message)
        socket = link._socket
        if socket is None:
            fail(link, "accepted connection has no socket")
            return
        connection_record = {
            "connection_number": connection_number,
            "transport": transport.value,
            "accepted_ns": time.perf_counter_ns(),
            "socket_configuration": _socket_configuration(link),
        }
        state["connections"].append(connection_record)
        tracker = QueueDrainTracker()
        active: dict[str, object] = {"spec": None, "first_read_id": None}

        socket.bytesWritten.connect(
            lambda count: (
                tracker.observe_bytes_written(int(count), link.bytes_to_write)
                if active["spec"] is not None
                else None
            )
        )

        def observe(message: Message) -> None:
            event = message.header.get("probe_event") if message.type is MessageType.PING else None
            if event == "run_start":
                spec = _spec_for(message.header.get("run"), message.header.get("transport"))
                if spec is None or active["spec"] is not None:
                    fail(link, "invalid or overlapping run_start")
                    return
                if spec.connection_number != connection_number:
                    fail(
                        link,
                        f"run {spec.name} used connection {connection_number}; "
                        f"expected {spec.connection_number}",
                    )
                    return
                active["spec"] = spec
                active["first_read_id"] = int(message.header["first_read_id"])
                connection_record["run"] = spec.name
                connection_record["run_start_ns"] = time.perf_counter_ns()
                tracker.start(link.bytes_to_write)
                return
            if message.type is MessageType.FILE_READ and active["spec"] is not None:
                tracker.observe_enqueue(link.bytes_to_write)
                return
            if event == "run_end":
                spec = active["spec"]
                if not isinstance(spec, RunSpec) or message.header.get("run") != spec.name:
                    fail(link, "run_end does not match active run")
                    return
                first = int(active["first_read_id"])
                chunks = int(message.header["total_chunks"])
                last = first + chunks - 1
                sends = [
                    item
                    for item in perf.events
                    if item.get("event") == "file_chunk_send"
                    and isinstance(item.get("read_id"), int)
                    and first <= int(item["read_id"]) <= last
                ]
                lags = [
                    item
                    for item in perf.events
                    if item.get("event") == "event_loop_lag"
                    and isinstance(item.get("read_id"), int)
                    and first <= int(item["read_id"]) <= last
                ]
                record = {
                    "run": spec.name,
                    "transport": spec.transport.value,
                    "measured": spec.measured,
                    "file_chunk_send_count": len(sends),
                    "windows_chunk_send_gap_ms": _gap_summary(sends),
                    "windows_event_loop_lag_ms": _lag_summary(lags),
                    "windows_app_processing_ms": summarize_windows_processing(
                        perf.events, first, last
                    ),
                    "socket_configuration": _socket_configuration(link),
                    **tracker.finish(link.bytes_to_write),
                }
                target = "runs" if spec.measured else "warmups"
                state[target][spec.name] = record
                active["spec"] = None
                connection_record["run_end_ns"] = time.perf_counter_ns()
                link.send(
                    Message(
                        MessageType.PING,
                        {"probe_event": "run_ready", "run": spec.name},
                        b"",
                    )
                )
                return
            if event == "probe_done":
                if len(state["runs"]) != len(MEASURED_RUNS) or len(state["warmups"]) != 2:
                    fail(link, "probe_done arrived before all runs completed")
                    return
                _write_json(args.result, state)
                QTimer.singleShot(500, app.quit)

        link.message_received.connect(observe)
        transfer_id = service.offer_local_files([source])
        if transfer_id is None:
            fail(link, "offer_local_files refused source")

    tls_listener.link_ready.connect(lambda link: on_link(link, TransportKind.TLS))
    plain_listener.link_ready.connect(lambda link: on_link(link, TransportKind.PLAIN))
    print(
        json.dumps(
            {
                "status": "ready",
                "tls_port": tls_listener.port,
                "tcp_port": plain_listener.port,
                "fingerprint": identity.fingerprint,
                "file_size": size,
                "sha256": digest,
                "runs": [run.name for run in RUN_SEQUENCE],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    exit_code = app.exec()
    _write_json(args.result, state)
    tls_listener.stop()
    plain_listener.stop()
    return int(exit_code)


class ProbeClient:
    def __init__(self, app: QCoreApplication, args: argparse.Namespace) -> None:
        self.app = app
        self.args = args
        self.identity = load_or_create(args.state_dir / "identity")
        self.perf = RecordingPerfEmitter(logger, "python_monotonic")
        self.run_index = 0
        self.link: PeerLink | None = None
        self.manifest = None
        self.entry_index: int | None = None
        self.active: WindowRequester | None = None
        self.results: list[dict[str, object]] = []
        self.warmups: list[dict[str, object]] = []
        self.failure: str | None = None
        self.lifecycle: dict[str, int | None] = {}
        self.timeout = QTimer()
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(lambda: self._fail("probe timeout"))

    def start(self) -> None:
        self._connect_next()

    def _connect_next(self) -> None:
        if self.run_index >= len(RUN_SEQUENCE):
            self._complete()
            return
        spec = RUN_SEQUENCE[self.run_index]
        self.manifest = None
        self.entry_index = None
        self.lifecycle = {
            "connect_begin_ns": time.monotonic_ns(),
            "tcp_connected_ns": None,
            "tls_handshake_begin_ns": None,
            "tls_established_ns": None,
            "first_request_ns": None,
            "last_response_ns": None,
            "connection_close_ns": None,
        }
        link = make_diagnostic_link(spec.transport, self.identity, perf=self.perf)
        self.link = link
        link.message_received.connect(self._on_message)
        link.disconnected.connect(
            lambda reason, source=link: self._on_disconnected(source, reason)
        )
        link.connected.connect(lambda _fingerprint, source=link: self._on_connected(source))
        port = self.args.tls_port if spec.transport is TransportKind.TLS else self.args.tcp_port
        self.timeout.start(CONNECT_TIMEOUT_MS)
        expected = self.args.fingerprint if spec.transport is TransportKind.TLS else None
        link.connect_to(self.args.host, port, expected)
        socket = link._socket
        if socket is None:
            self._fail("diagnostic link did not create a socket")
            return
        socket.connected.connect(
            lambda source=link: self._record_lifecycle(source, "tcp_connected_ns")
        )
        if isinstance(socket, QSslSocket):
            signal = getattr(socket, "startedClientEncryptionHandshake", None)
            if signal is not None:
                signal.connect(
                    lambda source=link: self._record_lifecycle(
                        source, "tls_handshake_begin_ns"
                    )
                )
            socket.encrypted.connect(
                lambda source=link: self._record_lifecycle(source, "tls_established_ns")
            )

    def _record_lifecycle(self, source: PeerLink, key: str) -> None:
        if source is self.link and self.lifecycle.get(key) is None:
            self.lifecycle[key] = time.monotonic_ns()

    def _on_connected(self, source: PeerLink) -> None:
        if source is not self.link:
            return
        spec = RUN_SEQUENCE[self.run_index]
        if spec.transport is TransportKind.PLAIN:
            self._record_lifecycle(source, "tcp_connected_ns")
        else:
            self._record_lifecycle(source, "tls_established_ns")
            if self.lifecycle["tls_handshake_begin_ns"] is None:
                self.lifecycle["tls_handshake_begin_ns"] = self.lifecycle["tcp_connected_ns"]
        self.timeout.start(OFFER_TIMEOUT_MS)

    def _on_disconnected(self, source: PeerLink, reason: str) -> None:
        if source is not self.link:
            return
        if self.active is not None:
            self.active.disconnect(reason)
        self._fail(f"link disconnected during run: {reason}")

    def _on_message(self, message: Message) -> None:
        try:
            event = message.header.get("probe_event") if message.type is MessageType.PING else None
            if event == "run_ready":
                if self.active is not None or message.header.get("run") != RUN_SEQUENCE[self.run_index].name:
                    raise ProbeError("run_ready arrived in invalid state")
                if self.run_index == len(RUN_SEQUENCE) - 1:
                    self._complete()
                else:
                    self._close_and_advance()
                return
            if message.type is MessageType.FILE_OFFER:
                if self.manifest is not None:
                    raise ProbeError("duplicate FILE_OFFER")
                self.manifest = decode_manifest(message.blob)
                files = [
                    index
                    for index, entry in enumerate(self.manifest.entries)
                    if entry.kind == ENTRY_FILE
                ]
                if len(files) != 1:
                    raise ProbeError("probe offer must contain one file")
                self.entry_index = files[0]
                self.timeout.stop()
                QTimer.singleShot(0, self._start_run)
                return
            if self.active is not None:
                self.active.handle_message(message)
                if self.active.complete:
                    self._finish_run()
        except (ProbeError, ValueError, OSError, RuntimeError) as error:
            self._fail(str(error))

    def _send_marker(self, event: str, spec: RunSpec, **fields: object) -> None:
        if self.link is None:
            raise ProbeError("marker send without link")
        header: dict[str, object] = {
            "probe_event": event,
            "run": spec.name,
            "transport": spec.transport.value,
            **fields,
        }
        if self.link.send(Message(MessageType.PING, header, b"")) is False:
            raise ProbeError(f"could not send {event}")

    def _start_run(self) -> None:
        if self.link is None or self.manifest is None or self.entry_index is None:
            self._fail("run started without link/offer")
            return
        spec = RUN_SEQUENCE[self.run_index]
        entry = self.manifest.entries[self.entry_index]
        file_size = entry.size if spec.measured else min(entry.size, WARMUP_BYTES)
        first_read_id = (self.run_index + 1) * RUN_READ_ID_STRIDE + 1
        self.active = WindowRequester(
            transfer_id=self.manifest.transfer_id,
            entry_index=self.entry_index,
            file_size=file_size,
            window=PER_FILE_WINDOW,
            output=self.args.output_dir / f"{spec.name}.bin",
            send=self.link.send,
            run_name=spec.name,
            first_read_id=first_read_id,
            qssl_bytes_to_write=lambda: self.link.bytes_to_write if self.link else 0,
        )
        self._send_marker("run_start", spec, first_read_id=first_read_id)
        self.lifecycle["first_request_ns"] = time.monotonic_ns()
        self.timeout.start(RUN_TIMEOUT_MS)
        self.active.start()

    def _finish_run(self) -> None:
        assert self.active is not None
        assert self.link is not None
        spec = RUN_SEQUENCE[self.run_index]
        self.lifecycle["last_response_ns"] = time.monotonic_ns()
        result = self.active.result()
        expected = deterministic_digest(self.active.file_size)
        result.update(
            {
                "expected_sha256": expected,
                "byte_exact": result["byte_exact_digest"] == expected,
                "transport": spec.transport.value,
                "measured": spec.measured,
                "socket_configuration": _socket_configuration(self.link),
                "mac_receive_boundaries": summarize_receive_boundaries(
                    self.perf.events,
                    first_read_id=int(result["first_read_id"]),
                    last_read_id=int(result["last_read_id"]),
                ),
                "lifecycle": dict(self.lifecycle),
            }
        )
        self._send_marker(
            "run_end", spec, total_chunks=int(result["total_chunks"])
        )
        self.active.close()
        self.active = None
        (self.results if spec.measured else self.warmups).append(result)
        if spec.measured:
            print(json.dumps(result, sort_keys=True), flush=True)
        self.timeout.start(CONNECT_TIMEOUT_MS)

    def _close_and_advance(self) -> None:
        assert self.link is not None
        old = self.link
        self.lifecycle["connection_close_ns"] = time.monotonic_ns()
        target = self.results if RUN_SEQUENCE[self.run_index].measured else self.warmups
        target[-1]["lifecycle"] = dict(self.lifecycle)
        self.link = None
        old.close()
        self.run_index += 1
        self.timeout.stop()
        QTimer.singleShot(200, self._connect_next)

    def _complete(self) -> None:
        if len(self.results) != len(MEASURED_RUNS) or len(self.warmups) != 2:
            self._fail("completion before all runs")
            return
        assert self.link is not None
        final_spec = RUN_SEQUENCE[self.run_index]
        self._send_marker("probe_done", final_spec)
        self.lifecycle["connection_close_ns"] = time.monotonic_ns()
        self.results[-1]["lifecycle"] = dict(self.lifecycle)
        output = {
            "file_size": self.args.size_mib * MIB,
            "chunk_size": MIB,
            "per_file_window": PER_FILE_WINDOW,
            "runs": self.results,
            "warmups": self.warmups,
        }
        _write_json(self.args.result, output)

        def close_and_quit() -> None:
            if self.link is not None:
                link = self.link
                self.link = None
                link.close()
            self.app.quit()

        QTimer.singleShot(500, close_and_quit)

    def _fail(self, reason: str) -> None:
        if self.failure is not None:
            return
        self.failure = reason
        logger.error("TLS/TCP probe failed: %s", reason)
        if self.active is not None:
            self.active.disconnect(reason)
            self.active.close()
        _write_json(
            self.args.result,
            {"failure": reason, "runs": self.results, "warmups": self.warmups},
        )
        self.app.exit(2)


def run_client(args: argparse.Namespace) -> int:
    _configure_logging(args.log)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    app = QCoreApplication.instance() or QCoreApplication([])
    client = ProbeClient(app, args)
    client.start()
    return int(app.exec())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    server = commands.add_parser("serve")
    server.add_argument("--source", type=Path, required=True)
    server.add_argument("--size-mib", type=int, default=DEFAULT_SIZE_MIB)
    server.add_argument("--tls-port", type=int, default=DEFAULT_TLS_PORT)
    server.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT)
    server.add_argument("--state-dir", type=Path, default=Path("scratchpad/transport-tls-tcp/server"))
    server.add_argument("--result", type=Path, default=Path("scratchpad/transport-tls-tcp/windows.json"))
    server.add_argument("--log", type=Path, default=Path("scratchpad/transport-tls-tcp/windows.log"))
    server.add_argument("--recreate", action="store_true")
    client = commands.add_parser("run")
    client.add_argument("--host", required=True)
    client.add_argument("--tls-port", type=int, default=DEFAULT_TLS_PORT)
    client.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT)
    client.add_argument("--fingerprint", required=True)
    client.add_argument("--size-mib", type=int, default=DEFAULT_SIZE_MIB)
    client.add_argument("--state-dir", type=Path, default=Path("scratchpad/transport-tls-tcp/client"))
    client.add_argument("--output-dir", type=Path, default=Path("scratchpad/transport-tls-tcp/output"))
    client.add_argument("--result", type=Path, default=Path("scratchpad/transport-tls-tcp/mac.json"))
    client.add_argument("--log", type=Path, default=Path("scratchpad/transport-tls-tcp/mac.log"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return serve(args) if args.command == "serve" else run_client(args)
    except (OSError, ProbeError, RuntimeError, ValueError) as error:
        logger.error("TLS/TCP probe failed: %s", error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MEASURED_RUNS",
    "PER_FILE_WINDOW",
    "RUN_SEQUENCE",
    "PlainPeerLink",
    "TransportKind",
    "bind_recording_perf",
    "make_diagnostic_link",
    "summarize_windows_processing",
]
