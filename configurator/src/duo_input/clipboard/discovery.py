"""Как две копии Duo Input находят друг друга в локальной сети.

Маячок рассылается только тогда, когда идёт парринг или когда спаренный
компьютер не найден по последнему известному адресу. Постоянная рассылка
означала бы, что присутствие устройства видно всей сети всё время, а платят за
это удобством, которого нет: после парринга адрес уже известен.

Маячок не даёт доверия. Он говорит "здесь есть Duo Input" и ничего больше;
доверие выдаётся человеком при парринге.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QHostAddress, QUdpSocket

from .local_addresses import local_multicast_interfaces
from .wire import PROTOCOL_MAJOR

logger = logging.getLogger(__name__)

GROUP_ADDRESS = "239.255.76.67"
BEACON_PORT = 47655
BEACON_INTERVAL_MS = 2000


@dataclass(frozen=True)
class Beacon:
    """Всё, что один узел говорит о себе вслух."""

    origin_id: str
    machine_name: str
    fingerprint: str
    port: int
    protocol_major: int


def encode_beacon(beacon: Beacon) -> bytes:
    """Кодировать маячок в JSON."""
    return json.dumps(asdict(beacon), ensure_ascii=False).encode("utf-8")


def decode_beacon(raw: bytes, own_origin_id: str) -> Beacon | None:
    """Разобрать чужой маячок. Свой, чужого поколения и мусор дают None."""
    try:
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            return None
        origin_id = parsed["origin_id"]
        machine_name = parsed["machine_name"]
        fingerprint = parsed["fingerprint"]
        port_value = parsed["port"]
        protocol_major_value = parsed["protocol_major"]

        # Проверка типов, не приведение. Исключаем bool, так как bool подтип int.
        if not isinstance(origin_id, str):
            return None
        if not isinstance(machine_name, str):
            return None
        if not isinstance(fingerprint, str):
            return None
        if not isinstance(port_value, int) or isinstance(port_value, bool):
            return None
        if not isinstance(protocol_major_value, int) or isinstance(protocol_major_value, bool):
            return None

        beacon = Beacon(
            origin_id=origin_id,
            machine_name=machine_name,
            fingerprint=fingerprint,
            port=port_value,
            protocol_major=protocol_major_value,
        )
    except (UnicodeDecodeError, ValueError, KeyError, TypeError):
        return None

    if beacon.origin_id == own_origin_id:
        return None
    if beacon.protocol_major != PROTOCOL_MAJOR:
        return None
    return beacon


class Discovery(QObject):
    """Рассылает свой маячок и слушает чужие - на каждом LAN-интерфейсе.

    Один сокет: членство в группе и отправка задаются по интерфейсу
    (joinMulticastGroup(group, iface), setMulticastInterface(iface)). Без
    этого маячок уходит только через маршрут по умолчанию - а он при Wi-Fi и
    Ethernet сразу или с поднятым VPN ведёт не туда, где второй компьютер.
    """

    peer_seen = Signal(object, str)

    def __init__(
        self,
        own_origin_id: str,
        parent: QObject | None = None,
        socket: QUdpSocket | None = None,
        interfaces: Callable[[], list] | None = None,
    ) -> None:
        super().__init__(parent)
        self._own_origin_id = own_origin_id
        self._beacon: Beacon | None = None
        self._socket = socket if socket is not None else QUdpSocket(self)
        self._socket.readyRead.connect(self._on_ready_read)
        # Список интерфейсов берётся заново на каждом маячке: Wi-Fi
        # переподключается, Ethernet втыкают, VPN поднимают.
        self._interfaces = interfaces if interfaces is not None else local_multicast_interfaces
        self._joined: dict[str, object] = {}
        self._default_joined = False
        self._failing: set[str] = set()
        self._active = False
        self._timer = QTimer(self)
        self._timer.setInterval(BEACON_INTERVAL_MS)
        self._timer.timeout.connect(self._announce)

    def start(self, beacon: Beacon) -> bool:
        """Начать рассылку маячка и приём чужих. Вернуть True при успехе."""
        self._beacon = beacon
        if self._active:
            # После sleep/wake прежний fd может выглядеть открытым, но уже не
            # состоять в multicast-группе нового сетевого интерфейса. Каждый
            # повторный recovery начинает настоящую новую UDP-сессию.
            self._timer.stop()
            self._leave_all()
            self._socket.close()
            self._active = False
        bound = self._socket.bind(
            QHostAddress.SpecialAddress.AnyIPv4,
            BEACON_PORT,
            QUdpSocket.BindFlag.ShareAddress | QUdpSocket.BindFlag.ReuseAddressHint,
        )
        if not bound:
            return False
        self._active = True
        self._announce()
        self._timer.start()
        return True

    def stop(self) -> None:
        """Остановить рассылку и приём."""
        self._timer.stop()
        if self._active:
            self._leave_all()
        self._socket.close()
        self._active = False

    def _announce(self) -> None:
        """Отправить свой маячок в группу через каждый LAN-интерфейс."""
        if self._beacon is None:
            return
        payload = encode_beacon(self._beacon)
        group = QHostAddress(GROUP_ADDRESS)
        interfaces = self._sync_memberships(group)
        if not interfaces:
            self._send(payload, group, "default")
            return
        for interface in interfaces:
            self._socket.setMulticastInterface(interface)
            self._send(payload, group, interface.name())

    def _sync_memberships(self, group: QHostAddress) -> list:
        """Вступить в группу на новых интерфейсах; вернуть текущий список."""
        current = list(self._interfaces())
        names = {interface.name() for interface in current}
        # Исчезнувший интерфейс унёс членство с собой; вернётся - вступим заново.
        for name in [name for name in self._joined if name not in names]:
            del self._joined[name]
        for interface in current:
            name = interface.name()
            if name not in self._joined and self._socket.joinMulticastGroup(group, interface):
                self._joined[name] = interface
        if not current and not self._default_joined:
            # Ни одного подходящего интерфейса - прежнее поведение: группа на
            # интерфейсе маршрута по умолчанию, лучше так, чем никак.
            self._socket.joinMulticastGroup(group)
            self._default_joined = True
        return current

    def _send(self, payload: bytes, group: QHostAddress, label: str) -> None:
        written = self._socket.writeDatagram(payload, group, BEACON_PORT)
        if written == -1:
            if label not in self._failing:
                self._failing.add(label)
                # Одна строка на интерфейс, а не каждые две секунды: на macOS
                # так выглядит, например, запрет доступа к локальной сети.
                logger.warning(
                    "beacon_send_failed interface=%s error=%s",
                    label,
                    self._socket.errorString(),
                )
        elif label in self._failing:
            self._failing.discard(label)
            logger.info("beacon_send_recovered interface=%s", label)

    def _leave_all(self) -> None:
        group = QHostAddress(GROUP_ADDRESS)
        for interface in self._joined.values():
            self._socket.leaveMulticastGroup(group, interface)
        if self._default_joined:
            self._socket.leaveMulticastGroup(group)
        self._joined.clear()
        self._default_joined = False

    def _on_ready_read(self) -> None:
        """Обработать входящую датаграмму."""
        while self._socket.hasPendingDatagrams():
            datagram = self._socket.receiveDatagram()
            beacon = decode_beacon(bytes(datagram.data()), self._own_origin_id)
            if beacon is not None:
                self.peer_seen.emit(beacon, datagram.senderAddress().toString())


__all__ = [
    "BEACON_INTERVAL_MS",
    "BEACON_PORT",
    "GROUP_ADDRESS",
    "Beacon",
    "Discovery",
    "decode_beacon",
    "encode_beacon",
]
