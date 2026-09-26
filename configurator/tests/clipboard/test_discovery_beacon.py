"""Маячок: что он несёт, чего не несёт и чьи маячки игнорируются."""

from __future__ import annotations

import json
import logging
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
        self.bind_count = 0
        self.join_count = 0
        self.leave_count = 0
        self.close_count = 0
        self.operations = []
        self.multicast_interface = None
        self.sent_via = []
        self.fail_writes = False

    def bind(self, host, port, flags):
        self.bind_count += 1
        self.operations.append("bind")
        if self.should_bind_fail:
            return False
        self.is_bound = True
        self.is_closed = False
        return True

    def joinMulticastGroup(self, address, iface=None):
        self.join_count += 1
        self.operations.append("join" if iface is None else f"join:{iface.name()}")
        self.multicast_groups.add(str(address.toString()))
        return True

    def leaveMulticastGroup(self, address, iface=None):
        self.leave_count += 1
        self.operations.append("leave" if iface is None else f"leave:{iface.name()}")
        self.multicast_groups.discard(str(address.toString()))
        return True

    def setMulticastInterface(self, iface):
        self.operations.append(f"via:{iface.name()}")
        self.multicast_interface = iface.name()

    def close(self):
        self.close_count += 1
        self.operations.append("close")
        self.is_bound = False
        self.is_closed = True

    def writeDatagram(self, data, address, port):
        self.operations.append("announce")
        self.sent_datagrams.append((bytes(data), str(address.toString()), port))
        self.sent_via.append(self.multicast_interface)
        return -1 if self.fail_writes else len(bytes(data))

    def errorString(self):
        return "No route to host"

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


class _FakeIface:
    def __init__(self, name: str) -> None:
        self._name = name

    def name(self) -> str:
        return self._name


OUR_BEACON = Beacon(OURS, "LAPTOP-ONE", "a" * 64, 47654, PROTOCOL_MAJOR)


def test_discovery_sends_beacon_on_start():
    from duo_input.clipboard.discovery import GROUP_ADDRESS
    socket = MockUdpSocket()
    beacon = Beacon(OURS, "LAPTOP-ONE", "a" * 64, 47654, PROTOCOL_MAJOR)

    discovery = Discovery(OURS)
    discovery._socket = socket

    assert discovery.start(beacon) is True
    assert socket.is_bound is True
    assert len(socket.sent_datagrams) >= 1


def test_restarting_discovery_rebinds_and_rejoins_multicast(qapp):
    """A network adapter can invalidate multicast membership across sleep.

    The coordinator calls start() again while disconnected, so that call must
    rebuild the UDP session instead of binding over an already-bound socket.
    """
    socket = MockUdpSocket()
    first = Beacon(OURS, "LAPTOP-ONE", "a" * 64, 47654, PROTOCOL_MAJOR)
    refreshed = Beacon(OURS, "LAPTOP-ONE", "b" * 64, 47654, PROTOCOL_MAJOR)
    discovery = Discovery(OURS, socket=socket, interfaces=lambda: [])

    assert discovery.start(first) is True
    socket.operations.clear()
    assert discovery.start(refreshed) is True

    assert socket.bind_count == 2
    assert socket.join_count == 2
    assert socket.leave_count == 1
    assert socket.close_count == 1
    assert socket.operations == ["leave", "close", "bind", "join", "announce"]
    assert decode_beacon(socket.sent_datagrams[-1][0], THEIRS) == refreshed
    assert discovery._timer.isActive() is True


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


def test_the_beacon_is_joined_and_sent_on_every_interface(qapp):
    socket = MockUdpSocket()
    discovery = Discovery(
        OURS, socket=socket, interfaces=lambda: [_FakeIface("en0"), _FakeIface("en7")]
    )

    assert discovery.start(OUR_BEACON) is True

    assert socket.operations == [
        "bind", "join:en0", "join:en7", "via:en0", "announce", "via:en7", "announce",
    ]
    assert socket.sent_via == ["en0", "en7"]
    discovery.stop()


def test_an_interface_that_appears_later_is_joined_on_the_next_beacon(qapp):
    """Воткнули Ethernet или переподключился Wi-Fi - рестарт discovery не нужен."""
    socket = MockUdpSocket()
    current = [_FakeIface("en0")]
    discovery = Discovery(OURS, socket=socket, interfaces=lambda: list(current))
    discovery.start(OUR_BEACON)
    current.append(_FakeIface("en7"))
    socket.operations.clear()

    discovery._announce()

    assert socket.operations == ["join:en7", "via:en0", "announce", "via:en7", "announce"]
    discovery.stop()


def test_an_interface_that_went_away_is_rejoined_when_it_returns(qapp):
    socket = MockUdpSocket()
    current = [_FakeIface("en0"), _FakeIface("en7")]
    discovery = Discovery(OURS, socket=socket, interfaces=lambda: list(current))
    discovery.start(OUR_BEACON)

    current[:] = [_FakeIface("en0")]
    discovery._announce()
    current[:] = [_FakeIface("en0"), _FakeIface("en7")]
    socket.operations.clear()
    discovery._announce()

    assert socket.operations[0] == "join:en7"
    discovery.stop()


def test_without_any_lan_interface_the_default_route_is_used_as_before(qapp):
    socket = MockUdpSocket()
    discovery = Discovery(OURS, socket=socket, interfaces=lambda: [])

    discovery.start(OUR_BEACON)

    assert socket.operations == ["bind", "join", "announce"]
    assert socket.sent_via == [None]
    discovery.stop()


def test_stop_leaves_every_joined_group(qapp):
    socket = MockUdpSocket()
    discovery = Discovery(
        OURS, socket=socket, interfaces=lambda: [_FakeIface("en0"), _FakeIface("en7")]
    )
    discovery.start(OUR_BEACON)
    socket.operations.clear()

    discovery.stop()

    assert socket.operations == ["leave:en0", "leave:en7", "close"]


def test_a_failed_send_is_logged_once_per_interface_until_it_recovers(qapp, caplog):
    """На macOS без доступа к локальной сети отправка отказывает каждые 2 с -
    журнал получает одну строку, а не поток."""
    caplog.set_level(logging.INFO, logger="duo_input.clipboard.discovery")
    socket = MockUdpSocket()
    socket.fail_writes = True
    discovery = Discovery(OURS, socket=socket, interfaces=lambda: [_FakeIface("en0")])

    discovery.start(OUR_BEACON)
    discovery._announce()
    socket.fail_writes = False
    discovery._announce()
    socket.fail_writes = True
    discovery._announce()

    failures = [r.getMessage() for r in caplog.records if "beacon_send_failed" in r.getMessage()]
    recoveries = [r.getMessage() for r in caplog.records if "beacon_send_recovered" in r.getMessage()]
    assert len(failures) == 2
    assert "interface=en0" in failures[0] and "No route to host" in failures[0]
    assert len(recoveries) == 1
    discovery.stop()
