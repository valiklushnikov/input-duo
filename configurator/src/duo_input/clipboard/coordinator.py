"""Единственное место, где части подсистемы соединяются друг с другом.

Маячок рассылается только во время парринга и только тогда, когда спаренный
компьютер не найден по последнему известному адресу: постоянная рассылка
означала бы, что присутствие устройства видно всей сети всё время.

Звонит тот, чей origin_id меньше. Это решает гонку встречных соединений без
переговоров. Во время парринга origin_id пира ещё не известен, поэтому там
соединяются оба, и лишнее соединение закрывается, как только одна из сторон
уже установила связь.

Отказ брандмауэра отличается от отсутствия связи и сообщается отдельным
состоянием: иначе пользователь начнёт чинить сеть, которая исправна.
"""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import QObject, QTimer, Signal

from .discovery import Beacon, Discovery
from .identity import NodeIdentity
from .listener import PeerListener
from .pairing import PAIRING_WINDOW_MS, PairingCandidate, pairing_code
from .peer import PeerLink
from .service import SILENCE_LIMIT_MS, ClipboardService
from .trust import TrustStore, TrustedPeer
from .wire import PROTOCOL_MAJOR, PROTOCOL_MINOR, Message, MessageType

TCP_PORT = 47654

#: Пауза перед повторной попыткой: растёт и упирается в потолок. Бесконечно
#: частые попытки были бы фоновой работой навсегда.
RECONNECT_DELAYS_MS = (1000, 2000, 4000, 8000, 30000)


class LinkState(StrEnum):
    UNPAIRED = "unpaired"
    SEARCHING = "searching"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    BLOCKED = "blocked"


def reconnect_delay_ms(attempt: int) -> int:
    """Сколько ждать перед попыткой номер ``attempt``, считая с нуля.

    Отрицательный номер (защита от неверного вызова) даёт ту же паузу, что и
    самая первая попытка, а не отрицательную задержку или исключение.
    """
    if attempt < 0:
        return RECONNECT_DELAYS_MS[0]
    return RECONNECT_DELAYS_MS[min(attempt, len(RECONNECT_DELAYS_MS) - 1)]


class ClipboardCoordinator(QObject):
    """Владеет связью, обнаружением, доверием и сервисом правил."""

    state_changed = Signal(str)
    pairing_code_ready = Signal(str, object)
    peer_changed = Signal(object)

    def __init__(
        self,
        identity: NodeIdentity,
        trust: TrustStore,
        machine_name: str,
        service: ClipboardService | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._identity = identity
        self._trust = trust
        self._machine_name = machine_name
        self._service = service if service is not None else ClipboardService(identity.origin_id)

        self._listener = PeerListener(identity, self)
        self._listener.link_ready.connect(self._on_incoming_link)
        self._discovery = Discovery(identity.origin_id, self)
        self._discovery.peer_seen.connect(self._on_peer_seen)

        self._link: PeerLink | None = None
        self._attempt = 0
        self._pairing = False
        self._manual_address = ""
        self._state = LinkState.UNPAIRED if trust.peer() is None else LinkState.DISCONNECTED

        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._try_connect)

        self._pairing_window = QTimer(self)
        self._pairing_window.setSingleShot(True)
        self._pairing_window.setInterval(PAIRING_WINDOW_MS)
        self._pairing_window.timeout.connect(self._end_pairing)

        self._silence = QTimer(self)
        self._silence.setSingleShot(True)
        self._silence.setInterval(SILENCE_LIMIT_MS)
        self._silence.timeout.connect(lambda: self._drop("второй компьютер молчит"))

    # ------------------------------------------------------------------ состояние

    @property
    def state(self) -> LinkState:
        return self._state

    @property
    def peer(self) -> TrustedPeer | None:
        return self._trust.peer()

    @property
    def service(self) -> ClipboardService:
        return self._service

    def _set_state(self, state: LinkState) -> None:
        if state is self._state:
            return
        self._state = state
        self.state_changed.emit(state.value)

    # ------------------------------------------------------------------ жизненный цикл

    def start(self) -> None:
        self._listener.expect(self.peer.fingerprint if self.peer else None)
        if not self._listener.listen(TCP_PORT):
            self._set_state(LinkState.BLOCKED)
            return
        if self.peer is None:
            self._set_state(LinkState.UNPAIRED)
            return
        self._attempt = 0
        self._try_connect()

    def stop(self) -> None:
        self._retry.stop()
        self._silence.stop()
        self._pairing_window.stop()
        self._discovery.stop()
        self._listener.stop()
        if self._link is not None:
            self._link.close()
            self._link = None
        self._service.detach_link()
        self._set_state(LinkState.UNPAIRED if self.peer is None else LinkState.DISCONNECTED)

    def set_manual_address(self, address: str) -> None:
        self._manual_address = address.strip()
        if self.peer is not None:
            self._attempt = 0
            self._try_connect()

    # ------------------------------------------------------------------ парринг

    def begin_pairing(self) -> None:
        self._pairing = True
        self._listener.expect(None)
        self._discovery.start(
            Beacon(
                origin_id=self._identity.origin_id,
                machine_name=self._machine_name,
                fingerprint=self._identity.fingerprint,
                port=TCP_PORT,
                protocol_major=PROTOCOL_MAJOR,
            )
        )
        self._pairing_window.start()
        self._set_state(LinkState.SEARCHING)

    def confirm_pairing(self, candidate: PairingCandidate) -> None:
        """Человек сверил код и подтвердил. Только теперь появляется доверие."""
        self._trust.remember(candidate.as_trusted())
        self._end_pairing()
        self._listener.expect(candidate.fingerprint)
        self.peer_changed.emit(self._trust.peer())
        self._attempt = 0
        self._try_connect()

    def forget_peer(self) -> None:
        self._trust.forget()
        if self._link is not None:
            self._link.close()
            self._link = None
        self._service.detach_link()
        self._listener.expect(None)
        self.peer_changed.emit(None)
        self._set_state(LinkState.UNPAIRED)

    def _end_pairing(self) -> None:
        self._pairing = False
        self._pairing_window.stop()
        self._discovery.stop()

    def _offer_pairing(self, candidate: PairingCandidate) -> None:
        code = pairing_code(self._identity.fingerprint, candidate.fingerprint)
        self.pairing_code_ready.emit(code, candidate)

    def _on_peer_seen(self, beacon: Beacon, address: str) -> None:
        if not self._pairing:
            # Спаренный компьютер сменил адрес - этого достаточно, чтобы позвонить.
            if self.peer is not None and beacon.origin_id == self.peer.origin_id:
                self._trust.update_address(address)
                self._try_connect()
            return
        self._offer_pairing(
            PairingCandidate(
                origin_id=beacon.origin_id,
                machine_name=beacon.machine_name,
                fingerprint=beacon.fingerprint,
                address=address,
                port=beacon.port,
            )
        )

    # ------------------------------------------------------------------ соединение

    def _address(self) -> str:
        if self._manual_address:
            return self._manual_address
        peer = self.peer
        return peer.last_address if peer else ""

    def _try_connect(self) -> None:
        peer = self.peer
        address = self._address()
        if peer is None or not address:
            self._start_looking()
            return
        if peer.origin_id < self._identity.origin_id:
            # Звонит меньший. Мы больше - ждём звонка, но следим за молчанием,
            # чтобы вечно повисшее ожидание тоже когда-нибудь стало разрывом.
            self._silence.start()
            return

        link = PeerLink(self._identity, self)
        link.connected.connect(lambda _fingerprint: self._on_connected(link))
        link.disconnected.connect(self._on_disconnected)
        link.connect_to(address, TCP_PORT, peer.fingerprint)

    def _start_looking(self) -> None:
        """Адрес неизвестен: включить маячок, пока пир не найдётся."""
        self._set_state(LinkState.SEARCHING)
        self._discovery.start(
            Beacon(
                origin_id=self._identity.origin_id,
                machine_name=self._machine_name,
                fingerprint=self._identity.fingerprint,
                port=TCP_PORT,
                protocol_major=PROTOCOL_MAJOR,
            )
        )

    def _on_incoming_link(self, link: PeerLink) -> None:
        if self._link is not None:
            link.close()
            return
        self._on_connected(link)

    def _on_connected(self, link: PeerLink) -> None:
        self._link = link
        self._attempt = 0
        self._retry.stop()
        self._discovery.stop()
        link.send(
            Message(
                MessageType.HELLO,
                {
                    "protocol_major": PROTOCOL_MAJOR,
                    "protocol_minor": PROTOCOL_MINOR,
                    "origin_id": self._identity.origin_id,
                    "machine_name": self._machine_name,
                },
                b"",
            )
        )
        link.message_received.connect(self._on_message)
        self._service.attach_link(link)
        self._silence.start()
        self._set_state(LinkState.CONNECTED)

    def _on_message(self, message: Message) -> None:
        self._silence.start()
        if message.type is MessageType.HELLO:
            if int(message.header.get("protocol_major", -1)) != PROTOCOL_MAJOR:
                self._drop("вторая машина говорит на другой версии протокола")

    def _on_disconnected(self, reason: str) -> None:
        self._drop(reason)

    def _drop(self, reason: str) -> None:
        self._silence.stop()
        if self._link is not None:
            self._link.close()
            self._link = None
        self._service.detach_link()
        self._set_state(LinkState.DISCONNECTED)
        self._retry.start(reconnect_delay_ms(self._attempt))
        self._attempt += 1


__all__ = [
    "RECONNECT_DELAYS_MS",
    "TCP_PORT",
    "ClipboardCoordinator",
    "LinkState",
    "reconnect_delay_ms",
]
