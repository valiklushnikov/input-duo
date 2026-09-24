"""Diagnostic-only QSslSocket buffer A/B/C probe.

The application never imports this module.  It is kept beside the existing
transport window harness so it can reuse the production PeerLink/files/2 path
without adding a production socket-buffer setting.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtNetwork import QAbstractSocket

from duo_input.clipboard.identity import load_or_create
from duo_input.clipboard.listener import PeerListener
from duo_input.clipboard.peer import PeerLink
from duo_input.clipboard.wire import CAPABILITY_FILES, Message, MessageType
from duo_input.transfer.fileprovider_perf import PerfEmitter
from duo_input.transfer.model import ENTRY_FILE, decode_manifest
from duo_input.transfer.service import FileTransferService

from transport_window_probe import (
    RUN_READ_ID_STRIDE,
    WindowRequester,
    _configure_logging,
    _write_json,
    create_deterministic_file,
    deterministic_digest,
    file_digest,
)


MIB = 1024 * 1024
MAX_DIAGNOSTIC_BUFFER_BYTES = 16 * MIB
DEFAULT_PORT = 24865
DEFAULT_SIZE_MIB = 128
CONNECT_TIMEOUT_MS = 30_000
OFFER_TIMEOUT_MS = 30_000
RUN_TIMEOUT_MS = 10 * 60_000
CAPS = frozenset({CAPABILITY_FILES})

logger = logging.getLogger("duo_input.transport_socket_buffer_probe")


@dataclass(frozen=True)
class BufferRunSpec:
    name: str
    profile: str


BUFFER_RUNS = (
    BufferRunSpec("A1", "current"),
    BufferRunSpec("B1", "1m"),
    BufferRunSpec("C1", "4m"),
    BufferRunSpec("A2", "current"),
    BufferRunSpec("B2", "1m"),
    BufferRunSpec("C2", "4m"),
)


class SocketBufferError(RuntimeError):
    """The diagnostic override cannot be applied safely."""


@dataclass(frozen=True)
class SocketBufferConfiguration:
    descriptor: int
    sndbuf_requested: int | None
    sndbuf_effective: int
    rcvbuf_requested: int | None
    rcvbuf_effective: int

    def to_dict(self) -> dict[str, int | None]:
        return {
            "descriptor": self.descriptor,
            "sndbuf_requested": self.sndbuf_requested,
            "sndbuf_effective": self.sndbuf_effective,
            "rcvbuf_requested": self.rcvbuf_requested,
            "rcvbuf_effective": self.rcvbuf_effective,
        }


def requested_buffers_for(
    run: BufferRunSpec, baseline: SocketBufferConfiguration
) -> tuple[int | None, int | None]:
    if run.profile == "current":
        if run.name == "A1":
            return None, None
        return baseline.sndbuf_effective, baseline.rcvbuf_effective
    if run.profile == "1m":
        return MIB, MIB
    if run.profile == "4m":
        return 4 * MIB, 4 * MIB
    raise SocketBufferError(f"unknown diagnostic buffer profile: {run.profile}")


def _validated_buffer(value: int | None, name: str) -> int | None:
    if value is None:
        return None
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= MAX_DIAGNOSTIC_BUFFER_BYTES
    ):
        raise SocketBufferError(
            f"{name} must be between 1 and {MAX_DIAGNOSTIC_BUFFER_BYTES} bytes"
        )
    return value


def configure_socket_buffers(
    link, sndbuf_requested: int | None, rcvbuf_requested: int | None
) -> SocketBufferConfiguration:
    """Optionally set both native socket buffers, then report Qt readback.

    ``None, None`` is the untouched control.  A one-sided ``None`` is refused:
    every experimental B/C run must vary the same declared matrix on both
    directions rather than silently leaving half of it at the prior value.
    """

    sndbuf_requested = _validated_buffer(sndbuf_requested, "SO_SNDBUF")
    rcvbuf_requested = _validated_buffer(rcvbuf_requested, "SO_RCVBUF")
    if (sndbuf_requested is None) != (rcvbuf_requested is None):
        raise SocketBufferError("SO_SNDBUF and SO_RCVBUF overrides must be paired")

    socket = getattr(link, "_socket", None)
    if socket is None:
        raise SocketBufferError("connected QSslSocket is unavailable")
    try:
        descriptor = int(socket.socketDescriptor())
    except (AttributeError, TypeError, ValueError, RuntimeError) as error:
        raise SocketBufferError("connected QSslSocket descriptor is unavailable") from error
    if descriptor < 0:
        raise SocketBufferError("connected QSslSocket descriptor is invalid")

    send_option = QAbstractSocket.SocketOption.SendBufferSizeSocketOption
    receive_option = QAbstractSocket.SocketOption.ReceiveBufferSizeSocketOption
    if sndbuf_requested is not None:
        socket.setSocketOption(send_option, sndbuf_requested)
        socket.setSocketOption(receive_option, rcvbuf_requested)
    try:
        sndbuf_effective = int(socket.socketOption(send_option))
        rcvbuf_effective = int(socket.socketOption(receive_option))
    except (AttributeError, TypeError, ValueError, RuntimeError) as error:
        raise SocketBufferError("socket buffer readback is unavailable") from error
    if sndbuf_effective <= 0 or rcvbuf_effective <= 0:
        raise SocketBufferError("socket buffer readback returned a non-positive value")
    return SocketBufferConfiguration(
        descriptor=descriptor,
        sndbuf_requested=sndbuf_requested,
        sndbuf_effective=sndbuf_effective,
        rcvbuf_requested=rcvbuf_requested,
        rcvbuf_effective=rcvbuf_effective,
    )


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, math.ceil(fraction * len(ordered)) - 1)
    return float(ordered[rank])


def _summary(values: Sequence[float]) -> dict[str, float]:
    return {
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "max": max(values, default=0.0),
    }


def summarize_receive_boundaries(
    events: Sequence[dict[str, object]], *, first_read_id: int, last_read_id: int
) -> dict[str, object]:
    by_read: dict[int, dict[str, int]] = {}
    for event in events:
        read_id = event.get("read_id")
        stamp = event.get("mono_ns")
        name = event.get("event")
        if (
            not isinstance(read_id, int)
            or isinstance(read_id, bool)
            or not first_read_id <= read_id <= last_read_id
            or not isinstance(stamp, int)
            or isinstance(stamp, bool)
            or name
            not in {
                "file_chunk_bytes_available",
                "file_chunk_frame_complete",
                "file_chunk_deliver",
            }
        ):
            continue
        by_read.setdefault(read_id, {})[str(name)] = stamp

    socket_to_frame: list[float] = []
    frame_to_chunk: list[float] = []
    for boundaries in by_read.values():
        available = boundaries.get("file_chunk_bytes_available")
        complete = boundaries.get("file_chunk_frame_complete")
        deliver = boundaries.get("file_chunk_deliver")
        if available is None or complete is None or deliver is None:
            continue
        socket_to_frame.append(float(max(0, complete - available)))
        frame_to_chunk.append(float(max(0, deliver - complete)))
    return {
        "complete_samples": len(socket_to_frame),
        "socket_to_frame_ns": _summary(socket_to_frame),
        "frame_to_chunk_ns": _summary(frame_to_chunk),
    }


class QsslDrainTracker:
    """Event-driven approximation of the QSslSocket plaintext queue drain."""

    def __init__(self, *, clock: Callable[[], int] | None = None) -> None:
        self._clock = clock or time.monotonic_ns
        self._active_name: str | None = None
        self._last_ns: int | None = None
        self._queue_bytes = 0
        self._queue_samples: list[float] = []
        self._nonzero_ns = 0
        self._bytes_written = 0
        self._write_ns: list[int] = []
        self._encrypted_bytes_written = 0
        self._encrypted_write_ns: list[int] = []

    def start(self, name: str, *, queue_bytes: int) -> None:
        if self._active_name is not None:
            raise RuntimeError(f"run {self._active_name} is already active")
        self._validate_observation(queue_bytes)
        self._active_name = name
        self._last_ns = self._clock()
        self._queue_bytes = queue_bytes
        self._queue_samples = [float(queue_bytes)]
        self._nonzero_ns = 0
        self._bytes_written = 0
        self._write_ns = []
        self._encrypted_bytes_written = 0
        self._encrypted_write_ns = []

    @staticmethod
    def _validate_observation(value: int) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("queue and byte counts must be non-negative integers")

    def _advance(self, now: int) -> None:
        if self._active_name is None or self._last_ns is None:
            raise RuntimeError("no drain run is active")
        if self._queue_bytes > 0:
            self._nonzero_ns += max(0, now - self._last_ns)
        self._last_ns = now

    def observe_enqueue(self, queue_bytes: int) -> None:
        self._validate_observation(queue_bytes)
        now = self._clock()
        self._advance(now)
        self._queue_bytes = queue_bytes
        self._queue_samples.append(float(queue_bytes))

    def observe_bytes_written(self, count: int, *, queue_bytes: int) -> None:
        self._validate_observation(count)
        self._validate_observation(queue_bytes)
        now = self._clock()
        self._advance(now)
        self._bytes_written += count
        self._write_ns.append(now)
        self._queue_bytes = queue_bytes
        self._queue_samples.append(float(queue_bytes))

    def observe_encrypted_bytes_written(self, count: int) -> None:
        self._validate_observation(count)
        now = self._clock()
        self._advance(now)
        self._encrypted_bytes_written += count
        self._encrypted_write_ns.append(now)

    def finish(self, *, queue_bytes: int) -> dict[str, object]:
        self._validate_observation(queue_bytes)
        now = self._clock()
        self._advance(now)
        self._queue_bytes = queue_bytes
        self._queue_samples.append(float(queue_bytes))
        nonzero_seconds = self._nonzero_ns / 1_000_000_000.0
        cadence_ms = [
            (right - left) / 1_000_000.0
            for left, right in zip(self._write_ns, self._write_ns[1:])
        ]
        encrypted_cadence_ms = [
            (right - left) / 1_000_000.0
            for left, right in zip(
                self._encrypted_write_ns, self._encrypted_write_ns[1:]
            )
        ]
        result = {
            "run": self._active_name,
            "bytes_written": self._bytes_written,
            "encrypted_bytes_written": self._encrypted_bytes_written,
            "time_qssl_queue_nonzero_seconds": nonzero_seconds,
            "qssl_drain_rate_bytes_per_second": (
                self._bytes_written / nonzero_seconds if nonzero_seconds > 0 else 0.0
            ),
            "qssl_queue_high_water": int(max(self._queue_samples, default=0.0)),
            "qssl_bytes_to_write": _summary(self._queue_samples),
            "bytes_written_cadence_ms": _summary(cadence_ms),
            "encrypted_bytes_written_cadence_ms": _summary(encrypted_cadence_ms),
        }
        self._active_name = None
        self._last_ns = None
        return result


class RecordingPerfEmitter:
    """Delegate normal perf logging while retaining bounded diagnostic events."""

    _RECORDED = frozenset(
        {
            "file_chunk_bytes_available",
            "file_chunk_frame_complete",
            "file_chunk_deliver",
            "file_chunk_send",
            "event_loop_lag",
        }
    )

    def __init__(self, log: logging.Logger, clock_domain: str) -> None:
        self._delegate = PerfEmitter(log, clock_domain)
        self.events: list[dict[str, object]] = []

    @property
    def clock_name(self) -> str:
        return self._delegate.clock_name

    @property
    def clock_resolution_ns(self) -> int | None:
        return self._delegate.clock_resolution_ns

    @property
    def clock_implementation(self) -> str:
        return self._delegate.clock_implementation

    def now(self) -> int:
        return self._delegate.now()

    def emit(self, event: str, **fields: object) -> int:
        return self.emit_at(self.now(), event, **fields)

    def emit_at(self, stamp: int, event: str, **fields: object) -> int:
        if event in self._RECORDED:
            self.events.append({"event": event, "mono_ns": int(stamp), **fields})
        return self._delegate.emit_at(stamp, event, **fields)


def _run_number(read_id: object) -> int | None:
    if not isinstance(read_id, int) or isinstance(read_id, bool):
        return None
    number = read_id // RUN_READ_ID_STRIDE
    if not 1 <= number <= len(BUFFER_RUNS):
        return None
    return number


def _events_for_run(
    events: Sequence[dict[str, object]], run_number: int, event_name: str
) -> list[dict[str, object]]:
    return [
        event
        for event in events
        if event.get("event") == event_name
        and _run_number(event.get("read_id")) == run_number
    ]


def _gap_summary(events: Sequence[dict[str, object]]) -> dict[str, float]:
    stamps = sorted(
        int(event["mono_ns"])
        for event in events
        if isinstance(event.get("mono_ns"), int)
    )
    return _summary(
        [(right - left) / 1_000_000.0 for left, right in zip(stamps, stamps[1:])]
    )


def _lag_summary(events: Sequence[dict[str, object]]) -> dict[str, float]:
    return _summary(
        [
            int(event["lag_ns"]) / 1_000_000.0
            for event in events
            if isinstance(event.get("lag_ns"), int)
        ]
    )


def _same_effective_buffers(
    left: SocketBufferConfiguration, right: SocketBufferConfiguration
) -> bool:
    return (
        left.sndbuf_effective == right.sndbuf_effective
        and left.rcvbuf_effective == right.rcvbuf_effective
    )


def _source_digest(source: Path, size: int, recreate: bool) -> str:
    if not source.exists() or source.stat().st_size != size or recreate:
        return create_deterministic_file(source, size)
    digest = file_digest(source)
    if digest != deterministic_digest(size):
        raise RuntimeError(
            "existing source is not the deterministic probe payload; "
            "choose a disposable --source and pass --recreate"
        )
    return digest


def serve(args: argparse.Namespace) -> int:
    _configure_logging(args.log)
    size = args.size_mib * MIB
    source = args.source.resolve()
    digest = _source_digest(source, size, args.recreate)
    app = QCoreApplication.instance() or QCoreApplication([])
    identity = load_or_create(args.state_dir / "identity")
    listener = PeerListener(identity)
    listener.expect(None)
    if not listener.listen(args.port):
        raise RuntimeError(f"cannot listen on port {args.port}")

    state: dict[str, object] = {
        "source": str(source),
        "size": size,
        "sha256": digest,
        "fingerprint": identity.fingerprint,
        "port": listener.port,
        "socket_configurations": {},
        "runs": {},
        "max_application_outbound_queue": 0,
    }
    services: list[FileTransferService] = []

    def on_link(link: PeerLink) -> None:
        initial_configuration = configure_socket_buffers(link, None, None)
        control_baseline = {"value": initial_configuration}
        state["initial_socket_configuration"] = initial_configuration.to_dict()
        perf = RecordingPerfEmitter(logger, "windows_python_monotonic")
        service = FileTransferService(perf=perf)
        services.append(service)
        service.attach_link(link)
        service.set_peer_capabilities(CAPS)
        link.message_received.connect(service.handle_message)
        tracker = QsslDrainTracker(clock=time.perf_counter_ns)
        active_run: dict[str, object] = {"number": None, "name": None}
        socket = link._socket
        if socket is None:
            raise RuntimeError("accepted link has no QSslSocket")

        def on_bytes_written(count: int) -> None:
            if active_run["name"] is not None:
                tracker.observe_bytes_written(int(count), queue_bytes=link.bytes_to_write)

        socket.bytesWritten.connect(on_bytes_written)

        def on_encrypted_bytes_written(count: int) -> None:
            if active_run["name"] is not None:
                tracker.observe_encrypted_bytes_written(int(count))

        socket.encryptedBytesWritten.connect(on_encrypted_bytes_written)

        def fail(reason: str) -> None:
            state["failure"] = reason
            logger.error("socket buffer probe failed: %s", reason)
            _write_json(args.result, state)
            link.close()
            app.exit(2)

        def observe(message: Message) -> None:
            event = message.header.get("probe_event") if message.type is MessageType.PING else None
            if event == "run_start":
                run_name = message.header.get("run")
                profile = message.header.get("buffer_profile")
                run_number = next(
                    (
                        index
                        for index, spec in enumerate(BUFFER_RUNS, start=1)
                        if spec.name == run_name and spec.profile == profile
                    ),
                    None,
                )
                if run_number is None or active_run["name"] is not None:
                    fail("invalid or overlapping run_start marker")
                    return
                spec = BUFFER_RUNS[run_number - 1]
                requested = requested_buffers_for(spec, control_baseline["value"])
                configuration = configure_socket_buffers(link, *requested)
                if spec.name == "A1":
                    control_baseline["value"] = configuration
                    state["baseline_socket_configuration"] = configuration.to_dict()
                if spec.name == "A2" and not _same_effective_buffers(
                    configuration, control_baseline["value"]
                ):
                    fail("A2 did not restore the Windows baseline buffers")
                    return
                state["socket_configurations"][spec.name] = configuration.to_dict()
                active_run["number"] = run_number
                active_run["name"] = spec.name
                tracker.start(spec.name, queue_bytes=link.bytes_to_write)
                logger.info(
                    "socket_buffer_run_start run=%s profile=%s sndbuf_requested=%s "
                    "sndbuf_effective=%d rcvbuf_requested=%s rcvbuf_effective=%d",
                    spec.name,
                    spec.profile,
                    configuration.sndbuf_requested,
                    configuration.sndbuf_effective,
                    configuration.rcvbuf_requested,
                    configuration.rcvbuf_effective,
                )
                return

            if message.type is MessageType.FILE_READ and active_run["name"] is not None:
                tracker.observe_enqueue(link.bytes_to_write)
                return

            if event == "run_end":
                run_name = message.header.get("run")
                if run_name != active_run["name"] or not isinstance(active_run["number"], int):
                    fail("run_end does not match the active run")
                    return
                run_number = int(active_run["number"])
                drain = tracker.finish(queue_bytes=link.bytes_to_write)
                sends = _events_for_run(perf.events, run_number, "file_chunk_send")
                lags = _events_for_run(perf.events, run_number, "event_loop_lag")
                state["runs"][str(run_name)] = {
                    "file_chunk_send_count": len(sends),
                    "windows_chunk_send_gap_ms": _gap_summary(sends),
                    "windows_event_loop_lag_ms": _lag_summary(lags),
                    **drain,
                }
                active_run["number"] = None
                active_run["name"] = None
                return

            if event == "probe_done":
                if active_run["name"] is not None or len(state["runs"]) != len(BUFFER_RUNS):
                    fail("probe_done arrived before all six runs completed")
                    return
                _write_json(args.result, state)
                QTimer.singleShot(500, app.quit)

        link.message_received.connect(observe)
        transfer_id = service.offer_local_files([source])
        if transfer_id is None:
            fail("offer_local_files refused the diagnostic source")
            return
        state["transfer_id"] = transfer_id

    listener.link_ready.connect(on_link)
    print(
        json.dumps(
            {
                "status": "ready",
                "port": listener.port,
                "fingerprint": identity.fingerprint,
                "file_size": size,
                "sha256": digest,
                "runs": [spec.name for spec in BUFFER_RUNS],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    exit_code = app.exec()
    _write_json(args.result, state)
    listener.stop()
    return int(exit_code)


class BufferProbeClient:
    def __init__(self, app: QCoreApplication, args: argparse.Namespace) -> None:
        self.app = app
        self.args = args
        self.identity = load_or_create(args.state_dir / "identity")
        self.perf = RecordingPerfEmitter(logger, "python_monotonic")
        self.link = PeerLink(self.identity, perf=self.perf)
        self.link.message_received.connect(self._on_message)
        self.link.disconnected.connect(self._on_disconnected)
        self.link.connected.connect(self._on_connected)
        self.baseline: SocketBufferConfiguration | None = None
        self.manifest = None
        self.entry_index: int | None = None
        self.run_index = 0
        self.active: WindowRequester | None = None
        self.active_configuration: SocketBufferConfiguration | None = None
        self.results: list[dict[str, object]] = []
        self.failure: str | None = None
        self.timeout = QTimer()
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(lambda: self._fail("probe timeout"))

    def start(self) -> None:
        self.timeout.start(CONNECT_TIMEOUT_MS)
        self.link.connect_to(self.args.host, self.args.port, self.args.fingerprint)

    def _on_connected(self, _fingerprint: str) -> None:
        try:
            self.baseline = configure_socket_buffers(self.link, None, None)
        except SocketBufferError as error:
            self._fail(str(error))
            return
        self.timeout.start(OFFER_TIMEOUT_MS)

    def _on_disconnected(self, reason: str) -> None:
        if self.active is not None:
            self.active.disconnect(reason)
        if len(self.results) != len(BUFFER_RUNS):
            self._fail(f"link disconnected: {reason}")

    def _on_message(self, message: Message) -> None:
        try:
            if message.type is MessageType.FILE_OFFER:
                if self.manifest is not None:
                    raise SocketBufferError("duplicate FILE_OFFER")
                self.manifest = decode_manifest(message.blob)
                files = [
                    index
                    for index, entry in enumerate(self.manifest.entries)
                    if entry.kind == ENTRY_FILE
                ]
                if len(files) != 1:
                    raise SocketBufferError("probe offer must contain exactly one file")
                self.entry_index = files[0]
                self.timeout.stop()
                QTimer.singleShot(0, self._start_next_run)
                return
            if self.active is not None:
                self.active.handle_message(message)
                if self.active.complete:
                    self._finish_active()
        except (SocketBufferError, ValueError, OSError, RuntimeError) as error:
            self._fail(str(error))

    def _send_marker(self, event: str, spec: BufferRunSpec | None = None) -> None:
        header: dict[str, object] = {"probe_event": event}
        if spec is not None:
            header.update({"run": spec.name, "buffer_profile": spec.profile})
        if self.link.send(Message(MessageType.PING, header, b"")) is False:
            self._fail(f"could not send {event} marker")

    def _start_next_run(self) -> None:
        if self.manifest is None or self.entry_index is None or self.baseline is None:
            self._fail("offer or baseline socket configuration unavailable")
            return
        if self.run_index >= len(BUFFER_RUNS):
            self._complete()
            return
        spec = BUFFER_RUNS[self.run_index]
        try:
            requested = requested_buffers_for(spec, self.baseline)
            configuration = configure_socket_buffers(self.link, *requested)
        except SocketBufferError as error:
            self._fail(str(error))
            return
        if spec.name == "A1":
            self.baseline = configuration
        if spec.name == "A2" and not _same_effective_buffers(configuration, self.baseline):
            self._fail("A2 did not restore the Mac baseline buffers")
            return
        self.active_configuration = configuration
        entry = self.manifest.entries[self.entry_index]
        first_read_id = (self.run_index + 1) * RUN_READ_ID_STRIDE + 1
        self.active = WindowRequester(
            transfer_id=self.manifest.transfer_id,
            entry_index=self.entry_index,
            file_size=entry.size,
            window=1,
            output=self.args.output_dir / f"{spec.name}.bin",
            send=self.link.send,
            run_name=spec.name,
            first_read_id=first_read_id,
            qssl_bytes_to_write=lambda: self.link.bytes_to_write,
        )
        self._send_marker("run_start", spec)
        self.timeout.start(RUN_TIMEOUT_MS)
        self.active.start()

    def _finish_active(self) -> None:
        assert self.active is not None
        assert self.active_configuration is not None
        spec = BUFFER_RUNS[self.run_index]
        result = self.active.result()
        expected = deterministic_digest(self.active.file_size)
        result["expected_sha256"] = expected
        result["byte_exact"] = result["byte_exact_digest"] == expected
        result["socket_configuration"] = self.active_configuration.to_dict()
        result["mac_receive_boundaries"] = summarize_receive_boundaries(
            self.perf.events,
            first_read_id=int(result["first_read_id"]),
            last_read_id=int(result["last_read_id"]),
        )
        self._send_marker("run_end", spec)
        self.active.close()
        self.results.append(result)
        print(json.dumps(result, sort_keys=True), flush=True)
        self.active = None
        self.active_configuration = None
        self.run_index += 1
        self.timeout.stop()
        QTimer.singleShot(500, self._start_next_run)

    def _complete(self) -> None:
        self._send_marker("probe_done")
        medians = {
            profile: statistics.median(
                float(run["throughput_bytes_per_second"])
                for run, spec in zip(self.results, BUFFER_RUNS)
                if spec.profile == profile
            )
            for profile in ("current", "1m", "4m")
        }
        output = {
            "chunk_size": MIB,
            "per_file_window": 1,
            "baseline_socket_configuration": self.baseline.to_dict() if self.baseline else None,
            "median_throughput_by_profile": medians,
            "runs": self.results,
        }
        _write_json(self.args.result, output)

        def close_and_quit() -> None:
            self.link.close()
            self.app.quit()

        QTimer.singleShot(500, close_and_quit)

    def _fail(self, reason: str) -> None:
        if self.failure is not None:
            return
        self.failure = reason
        logger.error("socket buffer probe failed: %s", reason)
        if self.active is not None:
            self.active.disconnect(reason)
            self.active.close()
        _write_json(self.args.result, {"failure": reason, "runs": self.results})
        self.app.exit(2)


def run_client(args: argparse.Namespace) -> int:
    _configure_logging(args.log)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    app = QCoreApplication.instance() or QCoreApplication([])
    client = BufferProbeClient(app, args)
    client.start()
    return int(app.exec())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    server = subparsers.add_parser("serve", help="run the Windows buffer responder")
    server.add_argument("--source", type=Path, required=True)
    server.add_argument("--size-mib", type=int, default=DEFAULT_SIZE_MIB)
    server.add_argument("--port", type=int, default=DEFAULT_PORT)
    server.add_argument(
        "--state-dir", type=Path, default=Path("scratchpad/transport-socket-buffer/server")
    )
    server.add_argument(
        "--result", type=Path, default=Path("scratchpad/transport-socket-buffer/windows.json")
    )
    server.add_argument(
        "--log", type=Path, default=Path("scratchpad/transport-socket-buffer/windows.log")
    )
    server.add_argument("--recreate", action="store_true")
    server.set_defaults(func=serve)

    client = subparsers.add_parser("run", help="run A/B/C/A/B/C from macOS")
    client.add_argument("--host", required=True)
    client.add_argument("--port", type=int, default=DEFAULT_PORT)
    client.add_argument("--fingerprint")
    client.add_argument(
        "--state-dir", type=Path, default=Path("scratchpad/transport-socket-buffer/client")
    )
    client.add_argument(
        "--output-dir", type=Path, default=Path("scratchpad/transport-socket-buffer/output")
    )
    client.add_argument(
        "--result", type=Path, default=Path("scratchpad/transport-socket-buffer/mac.json")
    )
    client.add_argument(
        "--log", type=Path, default=Path("scratchpad/transport-socket-buffer/mac.log")
    )
    client.set_defaults(func=run_client)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "size_mib", 1) <= 0:
        raise SystemExit("--size-mib must be positive")
    return int(args.func(args))


__all__ = [
    "BUFFER_RUNS",
    "MAX_DIAGNOSTIC_BUFFER_BYTES",
    "BufferRunSpec",
    "QsslDrainTracker",
    "SocketBufferConfiguration",
    "SocketBufferError",
    "configure_socket_buffers",
    "requested_buffers_for",
    "summarize_receive_boundaries",
]


if __name__ == "__main__":
    raise SystemExit(main())
