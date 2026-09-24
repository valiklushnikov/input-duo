from __future__ import annotations

from PySide6.QtNetwork import QSslSocket, QTcpSocket

from duo_input.clipboard.identity import load_or_create
from duo_input.clipboard.peer import PeerLink
from duo_input.clipboard.wire import FrameAssembler, Message, MessageType, encode
from transport_tls_tcp_probe import (
    MEASURED_RUNS,
    PER_FILE_WINDOW,
    RUN_SEQUENCE,
    PlainPeerLink,
    ProbeClient,
    TransportKind,
    bind_recording_perf,
    make_diagnostic_link,
    summarize_windows_processing,
)


def test_diagnostic_factory_keeps_tls_on_the_production_peer_link(tmp_path):
    tls = make_diagnostic_link(TransportKind.TLS, load_or_create(tmp_path / "tls"))

    assert type(tls) is PeerLink
    socket = QSslSocket()
    assert isinstance(socket, QSslSocket)


def test_diagnostic_plain_link_uses_qtcp_and_inherits_production_framing(tmp_path):
    plain = make_diagnostic_link(TransportKind.PLAIN, load_or_create(tmp_path / "plain"))

    assert type(plain) is PlainPeerLink
    assert PlainPeerLink.send is PeerLink.send
    assert PlainPeerLink._on_ready_read is PeerLink._on_ready_read
    socket = plain.create_outbound_socket()
    assert isinstance(socket, QTcpSocket)
    assert not isinstance(socket, QSslSocket)


def test_tls_tcp_run_order_is_balanced_and_every_measured_run_is_fresh():
    assert [(run.name, run.transport.value, run.measured) for run in RUN_SEQUENCE] == [
        ("TLS_WARMUP", "tls", False),
        ("TCP_WARMUP", "plain", False),
        ("A1", "tls", True),
        ("B1", "plain", True),
        ("B2", "plain", True),
        ("A2", "tls", True),
        ("A3", "tls", True),
        ("B3", "plain", True),
    ]
    assert [run.name for run in MEASURED_RUNS] == ["A1", "B1", "B2", "A2", "A3", "B3"]
    assert len({run.connection_number for run in RUN_SEQUENCE}) == len(RUN_SEQUENCE)


def test_diagnostic_contract_keeps_window_one_and_production_wire_bytes():
    assert PER_FILE_WINDOW == 1
    message = Message(
        MessageType.FILE_CHUNK,
        {"transfer_id": "transfer", "entry_index": 0, "read_id": 7, "offset": 0},
        b"payload",
    )

    assert FrameAssembler().feed(encode(message)) == [message]


def test_completed_connection_is_detached_before_local_close(monkeypatch):
    client = object.__new__(ProbeClient)
    observed_link_values = []

    class Link:
        def close(self):
            observed_link_values.append(client.link)

    class Timeout:
        def stop(self):
            pass

    client.link = Link()
    client.lifecycle = {"connection_close_ns": None}
    client.run_index = 0
    client.timeout = Timeout()
    client.warmups = [{"lifecycle": {}}]
    client.results = []
    monkeypatch.setattr(
        "transport_tls_tcp_probe.QTimer.singleShot", lambda _delay, _callback: None
    )

    client._close_and_advance()

    assert observed_link_values == [None]
    assert client.run_index == 1


def test_windows_processing_summary_matches_receive_to_write_on_same_read_id():
    events = [
        {"event": "file_read_bytes_available", "read_id": 10, "mono_ns": 1_000},
        {"event": "socket_write_complete", "read_id": 10, "mono_ns": 2_001_000},
        {"event": "file_read_bytes_available", "read_id": 11, "mono_ns": 5_000},
        {"event": "socket_write_complete", "read_id": 11, "mono_ns": 4_005_000},
        {"event": "socket_write_complete", "read_id": 99, "mono_ns": 99_000_000},
    ]

    assert summarize_windows_processing(events, 10, 11) == {
        "p50": 2.0,
        "p95": 4.0,
        "max": 4.0,
    }


def test_server_link_and_file_service_share_one_recording_emitter(tmp_path):
    link = make_diagnostic_link(
        TransportKind.TLS, load_or_create(tmp_path / "shared-perf")
    )
    emitter = object()

    bind_recording_perf(link, emitter)

    assert link._perf is emitter


def test_peer_close_after_all_results_does_not_invalidate_completed_probe():
    client = object.__new__(ProbeClient)
    source = object()
    failures = []
    client.link = source
    client.active = None
    client.results = [{} for _ in MEASURED_RUNS]
    client.warmups = [{}, {}]
    client._fail = failures.append

    client._on_disconnected(source, "peer closed after probe_done")

    assert failures == []
