"""Diagnostic-only same-file FILE_READ window probe over the production transport.

This module is deliberately outside ``duo_input`` and is never imported by the
application.  It reuses PeerLink, the files/2 codec, FileTransferService, and
SnapshotRegistry, while replacing only the receiving IStream/File Provider
consumer with a bounded diagnostic requester.

Run from ``configurator`` with ``PYTHONPATH=src``::

    python tests/transfer/transport_window_probe.py serve --source C:\\temp\\duo-window.bin
    python tests/transfer/transport_window_probe.py run --host 192.0.2.10
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer

from duo_input.clipboard.identity import load_or_create
from duo_input.clipboard.listener import PeerListener
from duo_input.clipboard.peer import PeerLink
from duo_input.clipboard.wire import (
    CAPABILITY_FILES,
    MAX_FILE_CHUNK_BYTES,
    Message,
    MessageType,
)
from duo_input.transfer.model import ENTRY_FILE, decode_manifest
from duo_input.transfer.service import FileTransferService


MIB = 1024 * 1024
DEFAULT_PORT = 24864
DEFAULT_SIZE_MIB = 128
WINDOW_SEQUENCE = (1, 4, 1, 4)
RUN_NAMES = ("A1", "B1", "A2", "B2")
RUN_READ_ID_STRIDE = 1_000_000
CONNECT_TIMEOUT_MS = 30_000
OFFER_TIMEOUT_MS = 30_000
RUN_TIMEOUT_MS = 10 * 60_000
CAPS = frozenset({CAPABILITY_FILES})

logger = logging.getLogger("duo_input.transport_window_probe")


class ProbeProtocolError(RuntimeError):
    """The diagnostic peer violated the bounded request/response contract."""


@dataclass(frozen=True)
class PendingRead:
    read_id: int
    offset: int
    length: int
    sent_ns: int


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


def deterministic_block(chunk_index: int, length: int) -> bytes:
    """Return a chunk-index-specific byte pattern without storing a fixture."""
    digest = hashlib.sha256(f"duo-window-probe:{chunk_index}".encode("ascii")).digest()
    return (digest * math.ceil(length / len(digest)))[:length]


def create_deterministic_file(path: Path, size: int) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    with path.open("wb") as handle:
        offset = 0
        while offset < size:
            length = min(MAX_FILE_CHUNK_BYTES, size - offset)
            block = deterministic_block(offset // MAX_FILE_CHUNK_BYTES, length)
            handle.write(block)
            hasher.update(block)
            offset += length
    return hasher.hexdigest()


def deterministic_digest(size: int) -> str:
    hasher = hashlib.sha256()
    offset = 0
    while offset < size:
        length = min(MAX_FILE_CHUNK_BYTES, size - offset)
        hasher.update(deterministic_block(offset // MAX_FILE_CHUNK_BYTES, length))
        offset += length
    return hasher.hexdigest()


def file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(MIB):
            hasher.update(block)
    return hasher.hexdigest()


class WindowRequester:
    """One diagnostic file stream with a strictly bounded FILE_READ window."""

    def __init__(
        self,
        *,
        transfer_id: str,
        entry_index: int,
        file_size: int,
        window: int,
        output: Path,
        send: Callable[[Message], object],
        clock: Callable[[], int] | None = None,
        run_name: str,
        first_read_id: int = 1,
        qssl_bytes_to_write: Callable[[], int] | None = None,
    ) -> None:
        if file_size <= 0:
            raise ValueError("file_size must be positive")
        if window not in (1, 4):
            raise ValueError("diagnostic window must be 1 or 4")
        if first_read_id <= 0:
            raise ValueError("first_read_id must be positive")
        self.transfer_id = transfer_id
        self.entry_index = entry_index
        self.file_size = file_size
        self.window = window
        self.output = output
        self._send = send
        self._clock = clock or time.monotonic_ns
        self.run_name = run_name
        self._next_read_id = first_read_id
        self._next_offset = 0
        self._pending: dict[int, PendingRead] = {}
        self._completed_read_ids: set[int] = set()
        self._received_ranges: dict[int, int] = {}
        self._rtt_ms: list[float] = []
        self._arrival_gap_ms: list[float] = []
        self._last_arrival_ns: int | None = None
        self._started_ns: int | None = None
        self._finished_ns: int | None = None
        self._occupancy_ns = {count: 0 for count in range(window + 1)}
        self._occupancy_count = 0
        self._occupancy_changed_ns: int | None = None
        self._max_outstanding_reads = 0
        self._max_outstanding_bytes = 0
        self._qssl_bytes_to_write = qssl_bytes_to_write or (lambda: 0)
        self._max_qssl_bytes_to_write = 0
        self._duplicate_ranges = 0
        self._overlapping_ranges = 0
        self._unexpected_chunks = 0
        self._error_read_id: int | None = None
        self._failure: str | None = None
        self._cancelled_read_count = 0
        output.parent.mkdir(parents=True, exist_ok=True)
        self._handle = output.open("w+b")
        self._handle.truncate(file_size)

    def start(self) -> None:
        if self._started_ns is not None:
            raise RuntimeError("requester already started")
        now = self._clock()
        self._started_ns = now
        self._occupancy_changed_ns = now
        self._fill_window(now)

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    @property
    def complete(self) -> bool:
        return self._finished_ns is not None and self._failure is None

    @property
    def failed(self) -> bool:
        return self._failure is not None

    @property
    def outstanding_reads(self) -> int:
        return len(self._pending)

    @property
    def pending_read_ids(self) -> frozenset[int]:
        return frozenset(self._pending)

    @property
    def max_outstanding_reads(self) -> int:
        return self._max_outstanding_reads

    @property
    def max_outstanding_bytes(self) -> int:
        return self._max_outstanding_bytes

    @property
    def duplicate_ranges(self) -> int:
        return self._duplicate_ranges

    @property
    def overlapping_ranges(self) -> int:
        return self._overlapping_ranges

    @property
    def unexpected_chunks(self) -> int:
        return self._unexpected_chunks

    @property
    def missing_ranges(self) -> int:
        expected = math.ceil(self.file_size / MAX_FILE_CHUNK_BYTES)
        return max(0, expected - len(self._received_ranges))

    @property
    def error_read_id(self) -> int | None:
        return self._error_read_id

    @property
    def cancelled_read_count(self) -> int:
        return self._cancelled_read_count

    @property
    def occupancy_ns(self) -> dict[int, int]:
        result = dict(self._occupancy_ns)
        if self._occupancy_changed_ns is not None and self._finished_ns is None:
            result[self._occupancy_count] += self._clock() - self._occupancy_changed_ns
        return result

    def _transition_occupancy(self, new_count: int, now: int) -> None:
        if self._occupancy_changed_ns is not None:
            self._occupancy_ns[self._occupancy_count] += now - self._occupancy_changed_ns
        self._occupancy_count = new_count
        self._occupancy_changed_ns = now

    def _fill_window(self, now: int) -> None:
        while len(self._pending) < self.window and self._next_offset < self.file_size:
            offset = self._next_offset
            length = min(MAX_FILE_CHUNK_BYTES, self.file_size - offset)
            read_id = self._next_read_id
            self._next_read_id += 1
            pending = PendingRead(read_id, offset, length, now)
            self._pending[read_id] = pending
            self._next_offset += length
            self._transition_occupancy(len(self._pending), now)
            outstanding_bytes = sum(item.length for item in self._pending.values())
            self._max_outstanding_reads = max(self._max_outstanding_reads, len(self._pending))
            self._max_outstanding_bytes = max(self._max_outstanding_bytes, outstanding_bytes)
            message = Message(
                MessageType.FILE_READ,
                {
                    "transfer_id": self.transfer_id,
                    "entry_index": self.entry_index,
                    "read_id": read_id,
                    "offset": offset,
                    "length": length,
                },
                b"",
            )
            if self._send(message) is False:
                self.disconnect("send refused")
                return
            self._max_qssl_bytes_to_write = max(
                self._max_qssl_bytes_to_write, int(self._qssl_bytes_to_write())
            )

    def _matching_pending(self, message: Message) -> PendingRead:
        header = message.header
        read_id = header.get("read_id")
        if not isinstance(read_id, int) or isinstance(read_id, bool):
            self._unexpected_chunks += 1
            raise ProbeProtocolError("response has no valid read_id")
        if read_id in self._completed_read_ids:
            self._duplicate_ranges += 1
            raise ProbeProtocolError(f"duplicate response read_id={read_id}")
        pending = self._pending.get(read_id)
        if pending is None:
            self._unexpected_chunks += 1
            raise ProbeProtocolError(f"unknown response read_id={read_id}")
        if (
            header.get("transfer_id") != self.transfer_id
            or header.get("entry_index") != self.entry_index
            or header.get("offset") != pending.offset
        ):
            self._unexpected_chunks += 1
            raise ProbeProtocolError(f"response correlation mismatch read_id={read_id}")
        return pending

    def handle_message(self, message: Message) -> None:
        if self.complete or self.failed:
            raise ProbeProtocolError("response arrived after run termination")
        if message.type not in (MessageType.FILE_CHUNK, MessageType.FILE_ERROR):
            return
        pending = self._matching_pending(message)
        if message.type is MessageType.FILE_ERROR:
            self._error_read_id = pending.read_id
            self._failure = str(message.header.get("reason", "file_error"))
            self._cancel_pending(self._clock())
            return

        if len(message.blob) != pending.length:
            self._failure = "short_or_oversized_chunk"
            self._cancel_pending(self._clock())
            raise ProbeProtocolError(
                f"read_id={pending.read_id} returned {len(message.blob)}, expected {pending.length}"
            )
        now = self._clock()
        del self._pending[pending.read_id]
        self._completed_read_ids.add(pending.read_id)
        self._transition_occupancy(len(self._pending), now)
        self._rtt_ms.append((now - pending.sent_ns) / 1_000_000.0)
        if self._last_arrival_ns is not None:
            self._arrival_gap_ms.append((now - self._last_arrival_ns) / 1_000_000.0)
        self._last_arrival_ns = now

        new_start = pending.offset
        new_end = pending.offset + pending.length
        for offset, length in self._received_ranges.items():
            if max(new_start, offset) < min(new_end, offset + length):
                self._overlapping_ranges += 1
                self._failure = "overlapping_range"
                self._cancel_pending(now)
                raise ProbeProtocolError(f"overlapping response offset={pending.offset}")
        self._received_ranges[pending.offset] = pending.length
        # Refill the transport window before doing local file I/O.  Both
        # window variants therefore measure the same network path rather than
        # folding the diagnostic machine's storage latency into stop-and-wait.
        self._fill_window(now)
        self._handle.seek(pending.offset)
        self._handle.write(message.blob)

        if not self._pending and self._next_offset >= self.file_size:
            self._handle.flush()
            # Wall time stops at last-chunk receipt.  Flush is required for
            # byte-exact verification but is intentionally not transport time.
            finished = now
            self._transition_occupancy(0, finished)
            self._finished_ns = finished

    def _cancel_pending(self, now: int) -> None:
        self._cancelled_read_count += len(self._pending)
        self._pending.clear()
        self._transition_occupancy(0, now)
        self._finished_ns = now

    def disconnect(self, reason: str) -> None:
        if self.complete or self.failed:
            return
        self._failure = reason
        self._cancel_pending(self._clock())

    def result(self) -> dict[str, object]:
        if self._started_ns is None or self._finished_ns is None:
            raise RuntimeError("run has not terminated")
        self._handle.flush()
        elapsed_ns = self._finished_ns - self._started_ns
        self._handle.seek(0)
        hasher = hashlib.sha256()
        while True:
            block = self._handle.read(MIB)
            if not block:
                break
            hasher.update(block)
        occupancy = dict(self._occupancy_ns)
        return {
            "run": self.run_name,
            "window": self.window,
            "byte_exact_digest": hasher.hexdigest(),
            "total_bytes": self.file_size,
            "total_chunks": len(self._received_ranges),
            "wall_time_seconds": elapsed_ns / 1_000_000_000.0,
            "throughput_bytes_per_second": (
                self.file_size / (elapsed_ns / 1_000_000_000.0) if elapsed_ns > 0 else 0.0
            ),
            "rtt_ms": _summary(self._rtt_ms),
            "chunk_arrival_gap_ms": _summary(self._arrival_gap_ms),
            "max_outstanding_reads": self._max_outstanding_reads,
            "max_outstanding_bytes": self._max_outstanding_bytes,
            "occupancy_seconds": {
                str(count): duration / 1_000_000_000.0
                for count, duration in occupancy.items()
            },
            "missing_ranges": self.missing_ranges,
            "duplicate_ranges": self._duplicate_ranges,
            "overlapping_ranges": self._overlapping_ranges,
            "unexpected_chunks": self._unexpected_chunks,
            "file_error": self._error_read_id is not None,
            "failure": self._failure,
            "max_qssl_bytes_to_write": self._max_qssl_bytes_to_write,
            "max_application_outbound_queue": 0,
            "first_read_id": min(self._completed_read_ids, default=0),
            "last_read_id": max(self._completed_read_ids, default=0),
        }


def _configure_logging(log_path: Path | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def serve(args: argparse.Namespace) -> int:
    _configure_logging(args.log)
    size = args.size_mib * MIB
    source = args.source.resolve()
    if not source.exists() or source.stat().st_size != size or args.recreate:
        digest = create_deterministic_file(source, size)
    else:
        digest = file_digest(source)
        expected = deterministic_digest(size)
        if digest != expected:
            raise RuntimeError(
                "existing source is not the deterministic probe payload; "
                "choose a disposable --source and pass --recreate"
            )

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
        "max_qssl_bytes_to_write": 0,
        "max_qssl_bytes_to_write_by_run": {name: 0 for name in RUN_NAMES},
        "max_application_outbound_queue": 0,
        "run_markers": [],
        "post_enqueue_ns": {name: [] for name in RUN_NAMES},
    }
    services: list[FileTransferService] = []

    def on_link(link: PeerLink) -> None:
        service = FileTransferService()
        services.append(service)
        service.attach_link(link)
        service.set_peer_capabilities(CAPS)
        link.message_received.connect(service.handle_message)

        def observe(message: Message) -> None:
            state["max_qssl_bytes_to_write"] = max(
                int(state["max_qssl_bytes_to_write"]), link.bytes_to_write
            )
            if message.type is MessageType.FILE_READ:
                read_id = message.header.get("read_id")
                if isinstance(read_id, int) and not isinstance(read_id, bool):
                    run_number = read_id // RUN_READ_ID_STRIDE
                    if 1 <= run_number <= len(RUN_NAMES):
                        run_name = RUN_NAMES[run_number - 1]
                        state["post_enqueue_ns"][run_name].append(time.perf_counter_ns())
                        state["max_qssl_bytes_to_write_by_run"][run_name] = max(
                            int(state["max_qssl_bytes_to_write_by_run"][run_name]),
                            link.bytes_to_write,
                        )
            if message.type is MessageType.PING and message.header.get("probe_event"):
                marker = {
                    "event": message.header.get("probe_event"),
                    "run": message.header.get("run"),
                    "mono_ns": time.perf_counter_ns(),
                    "max_qssl_bytes_to_write": link.bytes_to_write,
                }
                state["run_markers"].append(marker)
                logger.info("transport_window_marker %s", json.dumps(marker, sort_keys=True))
                if message.header.get("probe_event") == "probe_done":
                    state["windows_chunk_send_gap_ms"] = {
                        run_name: _summary(
                            [
                                (right - left) / 1_000_000.0
                                for left, right in zip(stamps, stamps[1:])
                            ]
                        )
                        for run_name, stamps in state["post_enqueue_ns"].items()
                    }
                    _write_json(args.result, state)
                    QTimer.singleShot(500, app.quit)

        link.message_received.connect(observe)
        transfer_id = service.offer_local_files([source])
        if transfer_id is None:
            raise RuntimeError("offer_local_files refused the diagnostic source")
        state["transfer_id"] = transfer_id
        logger.info("transport_window_offer transfer_id=%s size=%d", transfer_id, size)

    listener.link_ready.connect(on_link)
    ready = {
        "status": "ready",
        "port": listener.port,
        "fingerprint": identity.fingerprint,
        "file_size": size,
        "sha256": digest,
    }
    print(json.dumps(ready, sort_keys=True), flush=True)
    exit_code = app.exec()
    _write_json(args.result, state)
    listener.stop()
    return int(exit_code)


class ProbeClient:
    def __init__(self, app: QCoreApplication, args: argparse.Namespace) -> None:
        self.app = app
        self.args = args
        self.identity = load_or_create(args.state_dir / "identity")
        self.link = PeerLink(self.identity)
        self.link.message_received.connect(self._on_message)
        self.link.disconnected.connect(self._on_disconnected)
        self.link.connected.connect(self._on_connected)
        self.manifest = None
        self.file_entry_index: int | None = None
        self.results: list[dict[str, object]] = []
        self.active: WindowRequester | None = None
        self.run_index = 0
        self.failure: str | None = None
        self.timeout = QTimer()
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(lambda: self._fail("probe timeout"))

    def start(self) -> None:
        self.timeout.start(CONNECT_TIMEOUT_MS)
        self.link.connect_to(self.args.host, self.args.port, self.args.fingerprint)

    def _on_connected(self, _fingerprint: str) -> None:
        self.timeout.start(OFFER_TIMEOUT_MS)

    def _on_disconnected(self, reason: str) -> None:
        if self.active is not None:
            self.active.disconnect(reason)
        if len(self.results) != len(WINDOW_SEQUENCE):
            self._fail(f"link disconnected: {reason}")

    def _on_message(self, message: Message) -> None:
        try:
            if message.type is MessageType.FILE_OFFER:
                if self.manifest is not None:
                    raise ProbeProtocolError("duplicate FILE_OFFER")
                self.manifest = decode_manifest(message.blob)
                file_entries = [
                    index
                    for index, entry in enumerate(self.manifest.entries)
                    if entry.kind == ENTRY_FILE
                ]
                if len(file_entries) != 1:
                    raise ProbeProtocolError("probe offer must contain exactly one file")
                self.file_entry_index = file_entries[0]
                self.timeout.stop()
                QTimer.singleShot(0, self._start_next_run)
                return
            if self.active is not None:
                self.active.handle_message(message)
                if self.active.complete:
                    self._finish_active()
        except (ProbeProtocolError, ValueError, OSError) as error:
            self._fail(str(error))

    def _send_marker(self, event: str, run: str | None = None) -> None:
        header: dict[str, object] = {"probe_event": event}
        if run is not None:
            header["run"] = run
        self.link.send(Message(MessageType.PING, header, b""))

    def _start_next_run(self) -> None:
        if self.manifest is None or self.file_entry_index is None:
            self._fail("offer unavailable")
            return
        if self.run_index >= len(WINDOW_SEQUENCE):
            self._complete()
            return
        run_name = RUN_NAMES[self.run_index]
        window = WINDOW_SEQUENCE[self.run_index]
        entry = self.manifest.entries[self.file_entry_index]
        output = self.args.output_dir / f"{run_name}.bin"
        first_read_id = (self.run_index + 1) * RUN_READ_ID_STRIDE + 1
        self.active = WindowRequester(
            transfer_id=self.manifest.transfer_id,
            entry_index=self.file_entry_index,
            file_size=entry.size,
            window=window,
            output=output,
            send=self.link.send,
            run_name=run_name,
            first_read_id=first_read_id,
            qssl_bytes_to_write=lambda: self.link.bytes_to_write,
        )
        self._send_marker("run_start", run_name)
        self.timeout.start(RUN_TIMEOUT_MS)
        self.active.start()

    def _finish_active(self) -> None:
        assert self.active is not None
        result = self.active.result()
        expected = deterministic_digest(self.active.file_size)
        result["expected_sha256"] = expected
        result["byte_exact"] = result["byte_exact_digest"] == expected
        self._send_marker("run_end", self.active.run_name)
        self.active.close()
        self.results.append(result)
        print(json.dumps(result, sort_keys=True), flush=True)
        self.active = None
        self.run_index += 1
        self.timeout.stop()
        QTimer.singleShot(500, self._start_next_run)

    def _complete(self) -> None:
        self._send_marker("probe_done")
        output = {
            "protocol_change_required": False,
            "chunk_size": MAX_FILE_CHUNK_BYTES,
            "runs": self.results,
            "window_1_median_throughput": statistics.median(
                float(run["throughput_bytes_per_second"])
                for run in self.results
                if run["window"] == 1
            ),
            "window_4_median_throughput": statistics.median(
                float(run["throughput_bytes_per_second"])
                for run in self.results
                if run["window"] == 4
            ),
        }
        window1 = float(output["window_1_median_throughput"])
        window4 = float(output["window_4_median_throughput"])
        output["throughput_improvement_ratio"] = window4 / window1 if window1 else 0.0
        _write_json(self.args.result, output)

        def close_and_quit() -> None:
            self.link.close()
            self.app.quit()

        QTimer.singleShot(500, close_and_quit)

    def _fail(self, reason: str) -> None:
        if self.failure is not None:
            return
        self.failure = reason
        logger.error("transport window probe failed: %s", reason)
        if self.active is not None:
            self.active.disconnect(reason)
            self.active.close()
        _write_json(self.args.result, {"failure": reason, "runs": self.results})
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
    subparsers = parser.add_subparsers(dest="command", required=True)

    server = subparsers.add_parser("serve", help="run the Windows production-path responder")
    server.add_argument("--source", type=Path, required=True)
    server.add_argument("--size-mib", type=int, default=DEFAULT_SIZE_MIB)
    server.add_argument("--port", type=int, default=DEFAULT_PORT)
    server.add_argument("--state-dir", type=Path, default=Path("scratchpad/transport-window/server"))
    server.add_argument("--result", type=Path, default=Path("scratchpad/transport-window/windows.json"))
    server.add_argument("--log", type=Path, default=Path("scratchpad/transport-window/windows.log"))
    server.add_argument("--recreate", action="store_true")
    server.set_defaults(func=serve)

    client = subparsers.add_parser("run", help="run A1/B1/A2/B2 from macOS")
    client.add_argument("--host", required=True)
    client.add_argument("--port", type=int, default=DEFAULT_PORT)
    client.add_argument("--fingerprint")
    client.add_argument("--state-dir", type=Path, default=Path("scratchpad/transport-window/client"))
    client.add_argument("--output-dir", type=Path, default=Path("scratchpad/transport-window/output"))
    client.add_argument("--result", type=Path, default=Path("scratchpad/transport-window/mac.json"))
    client.add_argument("--log", type=Path, default=Path("scratchpad/transport-window/mac.log"))
    client.set_defaults(func=run_client)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "size_mib", 1) <= 0:
        raise SystemExit("--size-mib must be positive")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
