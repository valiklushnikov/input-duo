"""Маячок: что он несёт, чего не несёт и чьи маячки игнорируются."""

from __future__ import annotations

import json
from unittest.mock import Mock, MagicMock, call

from PySide6.QtCore import QByteArray, QObject, Signal
from PySide6.QtNetwork import QHostAddress, QUdpSocket

from duo_input.clipboard.discovery import Beacon, Discovery, decode_beacon, encode_beacon
from duo_input.clipboard.wire import PROTOCOL_MAJOR

OURS = "1" * 32
THEIRS = "2" * 32

BEACON = Beacon(
    origin_id=THEIRS,
    machine_name="LAPTOP-TWO",
    fingerprint="f" * 64,
    port=47654,
    protocol_major=PROTOCOL_MAJOR,
)


def test_a_beacon_survives_a_round_trip():
    assert decode_beacon(encode_beacon(BEACON), OURS) == BEACON


def test_our_own_beacon_is_ignored():
    ours = Beacon(OURS, "LAPTOP-ONE", "a" * 64, 47654, PROTOCOL_MAJOR)

    assert decode_beacon(encode_beacon(ours), OURS) is None


def test_a_beacon_from_another_protocol_generation_is_ignored():
    stranger = Beacon(THEIRS, "LAPTOP-TWO", "f" * 64, 47654, PROTOCOL_MAJOR + 1)

    assert decode_beacon(encode_beacon(stranger), OURS) is None


def test_rubbish_on_the_wire_is_ignored_rather_than_raising():
    assert decode_beacon(b"\x00\x01 not json", OURS) is None
    assert decode_beacon(json.dumps({"origin_id": THEIRS}).encode("utf-8"), OURS) is None


def test_a_beacon_never_carries_clipboard_content():
    raw = json.loads(encode_beacon(BEACON).decode("utf-8"))

    assert set(raw) == {"origin_id", "machine_name", "fingerprint", "port", "protocol_major"}


def test_beacon_with_wrong_origin_id_type_is_ignored():
    malformed = json.dumps({
        "origin_id": None,
        "machine_name": "LAPTOP-TWO",
        "fingerprint": "f" * 64,
        "port": 47654,
        "protocol_major": PROTOCOL_MAJOR,
    }).encode("utf-8")

    assert decode_beacon(malformed, OURS) is None


def test_beacon_with_wrong_port_type_is_ignored():
    malformed = json.dumps({
        "origin_id": THEIRS,
        "machine_name": "LAPTOP-TWO",
        "fingerprint": "f" * 64,
        "port": "not_a_number",
        "protocol_major": PROTOCOL_MAJOR,
    }).encode("utf-8")

    assert decode_beacon(malformed, OURS) is None


def test_beacon_with_wrong_machine_name_type_is_ignored():
    malformed = json.dumps({
        "origin_id": THEIRS,
        "machine_name": None,
        "fingerprint": "f" * 64,
        "port": 47654,
        "protocol_major": PROTOCOL_MAJOR,
    }).encode("utf-8")

    assert decode_beacon(malformed, OURS) is None


def test_beacon_with_wrong_fingerprint_type_is_ignored():
    malformed = json.dumps({
        "origin_id": THEIRS,
        "machine_name": "LAPTOP-TWO",
        "fingerprint": None,
        "port": 47654,
        "protocol_major": PROTOCOL_MAJOR,
    }).encode("utf-8")

    assert decode_beacon(malformed, OURS) is None


def test_beacon_with_wrong_protocol_major_type_is_ignored():
    malformed = json.dumps({
        "origin_id": THEIRS,
        "machine_name": "LAPTOP-TWO",
        "fingerprint": "f" * 64,
        "port": 47654,
        "protocol_major": "not_a_number",
    }).encode("utf-8")

    assert decode_beacon(malformed, OURS) is None


def test_beacon_with_bool_protocol_major_is_ignored():
    # True == 1 in Python, but it's not an int type, so should be rejected.
    malformed = json.dumps({
        "origin_id": THEIRS,
        "machine_name": "LAPTOP-TWO",
        "fingerprint": "f" * 64,
        "port": 47654,
        "protocol_major": True,
    }).encode("utf-8")

    assert decode_beacon(malformed, OURS) is None


def test_beacon_with_missing_required_field_is_ignored():
    malformed = json.dumps({
        "origin_id": THEIRS,
        "machine_name": "LAPTOP-TWO",
        "fingerprint": "f" * 64,
        # Missing port
        "protocol_major": PROTOCOL_MAJOR,
    }).encode("utf-8")

    assert decode_beacon(malformed, OURS) is None


# Discovery class tests

class MockUdpSocket(QObject):
    """Мок сокета для тестирования Discovery. Использует настоящий Qt-сигнал."""

    readyRead = Signal()

    def __init__(self):
        super().__init__()
        self.pending_datagrams = []
        self.sent_datagrams = []
        self.is_bound = False
        self.multicast_groups = set()
        self.is_closed = False
        self.should_bind_fail = False

    def bind(self, host, port, flags):
        if self.should_bind_fail:
            return False
        self.is_bound = True
        return True

    def joinMulticastGroup(self, address):
        self.multicast_groups.add(str(address.toString()))

    def leaveMulticastGroup(self, address):
        self.multicast_groups.discard(str(address.toString()))

    def close(self):
        self.is_closed = True

    def writeDatagram(self, data, address, port):
        self.sent_datagrams.append((bytes(data), str(address.toString()), port))

    def hasPendingDatagrams(self):
        return len(self.pending_datagrams) > 0 and not self.is_closed

    def receiveDatagram(self):
        if self.pending_datagrams:
            return self.pending_datagrams.pop(0)
        return None

    def add_pending_datagram(self, data, sender_address):
        """Добавить входящую датаграмму."""
        datagram = Mock()
        datagram.data = Mock(return_value=QByteArray(data))
        datagram.senderAddress = Mock(return_value=QHostAddress(sender_address))
        self.pending_datagrams.append(datagram)


def test_discovery_sends_beacon_on_start():
    from duo_input.clipboard.discovery import GROUP_ADDRESS
    socket = MockUdpSocket()
    beacon = Beacon(OURS, "LAPTOP-ONE", "a" * 64, 47654, PROTOCOL_MAJOR)

    discovery = Discovery(OURS)
    discovery._socket = socket

    assert discovery.start(beacon) is True
    assert socket.is_bound is True
    assert len(socket.sent_datagrams) >= 1


def test_discovery_emits_peer_seen_on_valid_beacon():
    socket = MockUdpSocket()
    beacon = Beacon(OURS, "LAPTOP-ONE", "a" * 64, 47654, PROTOCOL_MAJOR)

    discovery = Discovery(OURS)
    discovery._socket = socket

    peer_seen_spy = Mock()
    discovery.peer_seen.connect(peer_seen_spy)

    assert discovery.start(beacon) is True

    # Имитировать входящую датаграмму
    peer_beacon = Beacon(THEIRS, "LAPTOP-TWO", "f" * 64, 47654, PROTOCOL_MAJOR)
    socket.add_pending_datagram(encode_beacon(peer_beacon), "192.168.1.100")

    # Вызвать обработчик
    discovery._on_ready_read()

    # Проверить, что сигнал был испущен
    assert peer_seen_spy.called is True
    call_args = peer_seen_spy.call_args
    assert call_args[0][0] == peer_beacon
    assert call_args[0][1] == "192.168.1.100"


def test_discovery_ignores_own_beacon():
    socket = MockUdpSocket()
    beacon = Beacon(OURS, "LAPTOP-ONE", "a" * 64, 47654, PROTOCOL_MAJOR)

    discovery = Discovery(OURS)
    discovery._socket = socket

    peer_seen_spy = Mock()
    discovery.peer_seen.connect(peer_seen_spy)

    assert discovery.start(beacon) is True

    # Имитировать свой маячок, пришедший назад
    socket.add_pending_datagram(encode_beacon(beacon), "127.0.0.1")

    discovery._on_ready_read()

    # Сигнал НЕ должен быть испущен
    assert peer_seen_spy.called is False


def test_discovery_ignores_garbage_datagram():
    socket = MockUdpSocket()
    beacon = Beacon(OURS, "LAPTOP-ONE", "a" * 64, 47654, PROTOCOL_MAJOR)

    discovery = Discovery(OURS)
    discovery._socket = socket

    peer_seen_spy = Mock()
    discovery.peer_seen.connect(peer_seen_spy)

    assert discovery.start(beacon) is True

    # Имитировать мусорную датаграмму
    socket.add_pending_datagram(b"\x00\x01 not json", "192.168.1.100")

    # Не должно быть исключения
    discovery._on_ready_read()

    # Сигнал НЕ должен быть испущен
    assert peer_seen_spy.called is False


def test_discovery_stop_closes_socket(qapp):
    socket = MockUdpSocket()
    beacon = Beacon(OURS, "LAPTOP-ONE", "a" * 64, 47654, PROTOCOL_MAJOR)

    discovery = Discovery(OURS)
    discovery._socket = socket

    assert discovery.start(beacon) is True
    assert discovery._timer.isActive() is True
    discovery.stop()

    assert socket.is_closed is True
    assert discovery._timer.isActive() is False


def test_discovery_after_stop_ignores_datagrams():
    socket = MockUdpSocket()
    beacon = Beacon(OURS, "LAPTOP-ONE", "a" * 64, 47654, PROTOCOL_MAJOR)

    discovery = Discovery(OURS)
    discovery._socket = socket

    peer_seen_spy = Mock()
    discovery.peer_seen.connect(peer_seen_spy)

    assert discovery.start(beacon) is True
    discovery.stop()

    # Попытаться добавить датаграмму после stop
    peer_beacon = Beacon(THEIRS, "LAPTOP-TWO", "f" * 64, 47654, PROTOCOL_MAJOR)
    socket.add_pending_datagram(encode_beacon(peer_beacon), "192.168.1.100")

    discovery._on_ready_read()

    # Сигнал НЕ должен быть испущен (сокет закрыт)
    assert peer_seen_spy.called is False


def test_discovery_returns_false_on_bind_failure(qapp):
    socket = MockUdpSocket()
    socket.should_bind_fail = True
    beacon = Beacon(OURS, "LAPTOP-ONE", "a" * 64, 47654, PROTOCOL_MAJOR)

    discovery = Discovery(OURS)
    discovery._socket = socket

    assert discovery.start(beacon) is False
    assert discovery._timer.isActive() is False


def test_discovery_readyread_signal_connection(qapp):
    # Тест проверяет, что readyRead сигнал действительно подключен к обработчику.
    # Сокет передается в конструктор, поэтому подключение делает сам класс.
    socket = MockUdpSocket()
    beacon = Beacon(OURS, "LAPTOP-ONE", "a" * 64, 47654, PROTOCOL_MAJOR)

    # Передать мок-сокет в конструктор - тогда подключение делает сам Discovery
    discovery = Discovery(OURS, socket=socket)

    peer_seen_spy = Mock()
    discovery.peer_seen.connect(peer_seen_spy)

    assert discovery.start(beacon) is True

    # Добавить датаграмму и испустить сигнал readyRead
    peer_beacon = Beacon(THEIRS, "LAPTOP-TWO", "f" * 64, 47654, PROTOCOL_MAJOR)
    socket.add_pending_datagram(encode_beacon(peer_beacon), "192.168.1.100")

    # Испустить сигнал вместо прямого вызова обработчика
    socket.readyRead.emit()

    # Проверить, что сигнал был испущен
    assert peer_seen_spy.called is True
    call_args = peer_seen_spy.call_args
    assert call_args[0][0] == peer_beacon
    assert call_args[0][1] == "192.168.1.100"
