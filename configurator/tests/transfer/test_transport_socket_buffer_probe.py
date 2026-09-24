from __future__ import annotations

import pytest
from PySide6.QtNetwork import QAbstractSocket

from transport_socket_buffer_probe import (
    BUFFER_RUNS,
    MAX_DIAGNOSTIC_BUFFER_BYTES,
    BufferRunSpec,
    QsslDrainTracker,
    SocketBufferConfiguration,
    SocketBufferError,
    configure_socket_buffers,
    requested_buffers_for,
    summarize_receive_boundaries,
)


MIB = 1024 * 1024


class FakeSocket:
    def __init__(self, *, sndbuf: int = 65_536, rcvbuf: int = 65_536) -> None:
        self.values = {
            QAbstractSocket.SocketOption.SendBufferSizeSocketOption: sndbuf,
            QAbstractSocket.SocketOption.ReceiveBufferSizeSocketOption: rcvbuf,
        }
        self.set_calls: list[tuple[QAbstractSocket.SocketOption, int]] = []

    def socketDescriptor(self) -> int:
        return 77

    def socketOption(self, option: QAbstractSocket.SocketOption) -> int:
        return self.values[option]

    def setSocketOption(self, option: QAbstractSocket.SocketOption, value: int) -> None:
        self.set_calls.append((option, value))
        # Readback deliberately differs from the requested value.  The probe
        # must report what the kernel/Qt returns, not echo its input.
        self.values[option] = value * 2


class FakeLink:
    def __init__(self, socket: FakeSocket | None) -> None:
        self._socket = socket


class Clock:
    def __init__(self) -> None:
        self.ns = 0

    def __call__(self) -> int:
        return self.ns

    def advance_ms(self, milliseconds: int) -> None:
        self.ns += milliseconds * 1_000_000


def test_disabled_override_preserves_existing_socket_values_without_setter_calls():
    socket = FakeSocket(sndbuf=65_536, rcvbuf=131_072)

    result = configure_socket_buffers(FakeLink(socket), None, None)

    assert socket.set_calls == []
    assert result.descriptor == 77
    assert result.sndbuf_requested is None
    assert result.rcvbuf_requested is None
    assert result.sndbuf_effective == 65_536
    assert result.rcvbuf_effective == 131_072


@pytest.mark.parametrize("requested", [MIB, 4 * MIB])
def test_override_applies_both_values_and_reports_effective_readback(requested):
    socket = FakeSocket()

    result = configure_socket_buffers(FakeLink(socket), requested, requested)

    assert socket.set_calls == [
        (QAbstractSocket.SocketOption.SendBufferSizeSocketOption, requested),
        (QAbstractSocket.SocketOption.ReceiveBufferSizeSocketOption, requested),
    ]
    assert result.sndbuf_requested == requested
    assert result.rcvbuf_requested == requested
    assert result.sndbuf_effective == requested * 2
    assert result.rcvbuf_effective == requested * 2


@pytest.mark.parametrize(
    "sndbuf,rcvbuf",
    [
        (0, MIB),
        (-1, MIB),
        (MIB, 0),
        (MAX_DIAGNOSTIC_BUFFER_BYTES + 1, MIB),
    ],
)
def test_invalid_override_fails_before_touching_the_socket(sndbuf, rcvbuf):
    socket = FakeSocket()

    with pytest.raises(SocketBufferError):
        configure_socket_buffers(FakeLink(socket), sndbuf, rcvbuf)

    assert socket.set_calls == []


def test_missing_connected_socket_fails_safely():
    with pytest.raises(SocketBufferError, match="connected QSslSocket"):
        configure_socket_buffers(FakeLink(None), MIB, MIB)


def test_drain_tracker_uses_event_time_and_qssl_readback():
    clock = Clock()
    tracker = QsslDrainTracker(clock=clock)
    tracker.start("A1", queue_bytes=0)
    clock.advance_ms(10)
    tracker.observe_enqueue(1 * MIB)
    clock.advance_ms(40)
    tracker.observe_encrypted_bytes_written(600_000)
    clock.advance_ms(100)
    tracker.observe_bytes_written(1 * MIB, queue_bytes=0)
    clock.advance_ms(10)

    result = tracker.finish(queue_bytes=0)

    assert result["time_qssl_queue_nonzero_seconds"] == pytest.approx(0.140)
    assert result["qssl_drain_rate_bytes_per_second"] == pytest.approx(MIB / 0.140)
    assert result["qssl_queue_high_water"] == MIB
    assert result["bytes_written"] == MIB
    assert result["encrypted_bytes_written"] == 600_000
    assert result["qssl_bytes_to_write"]["max"] == MIB


def test_drain_tracker_rejects_overlapping_runs_and_negative_observations():
    tracker = QsslDrainTracker(clock=lambda: 1)
    tracker.start("A1", queue_bytes=0)

    with pytest.raises(RuntimeError, match="already active"):
        tracker.start("B1", queue_bytes=0)
    with pytest.raises(ValueError):
        tracker.observe_enqueue(-1)
    with pytest.raises(ValueError):
        tracker.observe_bytes_written(-1, queue_bytes=0)
    with pytest.raises(ValueError):
        tracker.observe_encrypted_bytes_written(-1)


def test_socket_configuration_does_not_send_or_mutate_wire_messages():
    socket = FakeSocket()
    link = FakeLink(socket)
    assert not hasattr(link, "sent")

    configure_socket_buffers(link, MIB, MIB)

    assert not hasattr(link, "sent")


def test_buffer_matrix_order_and_values_are_fixed():
    assert [(run.name, run.profile) for run in BUFFER_RUNS] == [
        ("A1", "current"),
        ("B1", "1m"),
        ("C1", "4m"),
        ("A2", "current"),
        ("B2", "1m"),
        ("C2", "4m"),
    ]


def test_each_current_profile_is_untouched_on_its_fresh_connection():
    baseline = SocketBufferConfiguration(
        descriptor=77,
        sndbuf_requested=None,
        sndbuf_effective=65_536,
        rcvbuf_requested=None,
        rcvbuf_effective=131_072,
    )

    assert requested_buffers_for(BufferRunSpec("A1", "current"), baseline) == (
        None,
        None,
    )
    assert requested_buffers_for(BufferRunSpec("A2", "current"), baseline) == (
        None,
        None,
    )
    assert requested_buffers_for(BufferRunSpec("B1", "1m"), baseline) == (MIB, MIB)
    assert requested_buffers_for(BufferRunSpec("C1", "4m"), baseline) == (
        4 * MIB,
        4 * MIB,
    )


def test_receive_boundary_summary_uses_only_same_read_id_and_clock_durations():
    events = [
        {"event": "file_chunk_bytes_available", "mono_ns": 100, "read_id": 1},
        {"event": "file_chunk_frame_complete", "mono_ns": 130, "read_id": 1},
        {"event": "file_chunk_deliver", "mono_ns": 150, "read_id": 1},
        {"event": "file_chunk_bytes_available", "mono_ns": 200, "read_id": 2},
        {"event": "file_chunk_frame_complete", "mono_ns": 260, "read_id": 2},
        {"event": "file_chunk_deliver", "mono_ns": 290, "read_id": 2},
        {"event": "file_chunk_deliver", "mono_ns": 999, "read_id": 3},
    ]

    result = summarize_receive_boundaries(events, first_read_id=1, last_read_id=2)

    assert result["complete_samples"] == 2
    assert result["socket_to_frame_ns"]["p50"] == 30
    assert result["socket_to_frame_ns"]["p95"] == 60
    assert result["frame_to_chunk_ns"]["p50"] == 20
    assert result["frame_to_chunk_ns"]["p95"] == 30
