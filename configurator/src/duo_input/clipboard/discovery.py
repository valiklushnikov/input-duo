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
from dataclasses import asdict, dataclass

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QHostAddress, QUdpSocket

from .wire import PROTOCOL_MAJOR

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
    """Рассылает свой маячок и слушает чужие."""

    peer_seen = Signal(object, str)

    def __init__(
        self,
        own_origin_id: str,
        parent: QObject | None = None,
        socket: QUdpSocket | None = None,
    ) -> None:
        super().__init__(parent)
        self._own_origin_id = own_origin_id
        self._beacon: Beacon | None = None
        self._socket = socket if socket is not None else QUdpSocket(self)
        self._socket.readyRead.connect(self._on_ready_read)
        self._timer = QTimer(self)
        self._timer.setInterval(BEACON_INTERVAL_MS)
        self._timer.timeout.connect(self._announce)

    def start(self, beacon: Beacon) -> bool:
        """Начать рассылку маячка и приём чужих. Вернуть True при успехе."""
        self._beacon = beacon
        bound = self._socket.bind(
            QHostAddress.SpecialAddress.AnyIPv4,
            BEACON_PORT,
            QUdpSocket.BindFlag.ShareAddress | QUdpSocket.BindFlag.ReuseAddressHint,
        )
        if not bound:
            return False
        self._socket.joinMulticastGroup(QHostAddress(GROUP_ADDRESS))
        self._announce()
        self._timer.start()
        return True

    def stop(self) -> None:
        """Остановить рассылку и приём."""
        self._timer.stop()
        self._socket.leaveMulticastGroup(QHostAddress(GROUP_ADDRESS))
        self._socket.close()

    def _announce(self) -> None:
        """Отправить свой маячок в группу."""
        if self._beacon is None:
            return
        self._socket.writeDatagram(
            encode_beacon(self._beacon), QHostAddress(GROUP_ADDRESS), BEACON_PORT
        )

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
