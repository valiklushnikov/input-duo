"""Единственное место, где части подсистемы соединяются друг с другом.

Маячок рассылается только во время парринга и только тогда, когда спаренный
компьютер не найден по последнему известному адресу: постоянная рассылка
означала бы, что присутствие устройства видно всей сети всё время.

Звонит тот, чей origin_id меньше. Это решает гонку встречных соединений без
переговоров. Во время парринга доверенного пира ещё нет, поэтому тот же
порядок применяется по origin_id из маячка, - а лишнее соединение закрывается,
как только одна из сторон уже установила связь.

Приём чужого сертификата разрешён ТОЛЬКО во время связывания. Вне связывания
слушатель закреплён либо за отпечатком уже доверенного пира, либо (если пира
ещё нет) за отпечатком, которому заведомо не сможет соответствовать ни один
настоящий сертификат - так неспаренный узел не примет вообще никого. Это не
формальность записи в хранилище доверия, а контроль над тем, что происходит с
данными: соединение, до которого не дошло человеческое подтверждение обеих
сторон, никогда не доходит до обмена буфером обмена.

Само связывание - это отдельный протокол поверх TLS-соединения: HELLO в нём не
участвует, участвуют PAIR_REQUEST (кто ты) и PAIR_CONFIRM (согласен ли ты,
после того как человек сверил код на экране). Соединение времён связывания не
становится рабочим само по себе - оно становится рабочим только когда
PAIR_CONFIRM с согласием получен И отправлен, то есть когда согласны обе
стороны, а не одна.

Отказ брандмауэра отличается от отсутствия связи и сообщается отдельным
состоянием: иначе пользователь начнёт чинить сеть, которая исправна.
"""

from __future__ import annotations

import logging
from enum import StrEnum

from PySide6.QtCore import QObject, QTimer, Signal

from .discovery import Beacon, Discovery
from .identity import NodeIdentity
from .listener import PeerListener
from .pairing import PAIRING_WINDOW_MS, PairingCandidate, pairing_code
from .peer import PeerLink
from .service import SILENCE_LIMIT_MS, ClipboardService
from .trust import TrustStore, TrustedPeer
from .wire import (
    CAPABILITIES,
    CAPABILITY_CLIPBOARD,
    CAPABILITY_FILES,
    LEGACY_CAPABILITIES,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    Message,
    MessageType,
)

logger = logging.getLogger(__name__)

TCP_PORT = 47654

#: Пауза перед повторной попыткой: растёт и упирается в потолок. Бесконечно
#: частые попытки были бы фоновой работой навсегда.
RECONNECT_DELAYS_MS = (1000, 2000, 4000, 8000, 30000)

#: Отпечаток, которому не может соответствовать ни один настоящий сертификат
#: (тот всегда - непустая шестнадцатеричная строка). Используется вместо
#: None, когда мы НЕ связываемся: слушатель обязан отказать всем подряд, а не
#: принять кого угодно, как во время связывания.
_NOBODY_IS_WELCOME = ""


class LinkState(StrEnum):
    UNPAIRED = "unpaired"
    SEARCHING = "searching"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    BLOCKED = "blocked"
    # Отдельное состояние, а не обычный разрыв: соединение с этим пиром
    # никогда не заработает само по себе, поэтому вечные повторы здесь были
    # бы обманом, а не терпением - см. _drop().
    PROTOCOL_MISMATCH = "protocol_mismatch"


def reconnect_delay_ms(attempt: int) -> int:
    """Сколько ждать перед попыткой номер ``attempt``, считая с нуля.

    Отрицательный номер (защита от неверного вызова) даёт ту же паузу, что и
    самая первая попытка, а не отрицательную задержку или исключение.
    """
    if attempt < 0:
        return RECONNECT_DELAYS_MS[0]
    return RECONNECT_DELAYS_MS[min(attempt, len(RECONNECT_DELAYS_MS) - 1)]


def _capabilities_from(header: dict) -> frozenset[str]:
    """Что пир объявил - и ничего, чего он не объявил.

    Проверка типа, а не приведение: строка возможности содержит саму себя как
    подстроку, поэтому проверка через `in` по строке приняла бы её, и мы
    послали бы FILE_* туда, где их не ждут. Тот же порядок строгости, что у
    ClipboardOffer.from_dict.
    """
    announced = header.get("capabilities")
    if announced is None:
        return LEGACY_CAPABILITIES
    if not isinstance(announced, list):
        logger.warning("capabilities пришли не списком - считаем пира устаревшим")
        return LEGACY_CAPABILITIES
    return frozenset(item for item in announced if isinstance(item, str))


def _plain_ipv4(address: str) -> str:
    """Входящий сокет на двухстековом слушателе даёт "::ffff:1.2.3.4" -
    звонить обратно и показывать человеку нужно "1.2.3.4"."""
    prefix = "::ffff:"
    return address[len(prefix):] if address.lower().startswith(prefix) else address


class ClipboardCoordinator(QObject):
    """Владеет связью, обнаружением, доверием и сервисом правил."""

    state_changed = Signal(str)
    pairing_code_ready = Signal(str, object)
    peer_changed = Signal(object)
    #: Короткая, человекочитаемая строка для журнала и для списка последних
    #: событий на странице - см. §12 спецификации.
    event_logged = Signal(str)
    #: Пир объявил себя в HELLO. Аргумент - frozenset[str] возможностей.
    capabilities_known = Signal(object)
    #: Адрес, по которому связь действительно установилась - для журнала и
    #: для того, чтобы показать оператору, что используется на самом деле.
    address_in_use = Signal(str)

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
        self._service.content_failed.connect(self._on_content_failed)

        self._listener = PeerListener(identity, self)
        # Безопасное состояние по умолчанию, ещё до первого start(): чужой
        # сертификат не принимается, пока явно не начато связывание.
        self._listener.expect(self.peer.fingerprint if self.peer else _NOBODY_IS_WELCOME)
        self._listener.link_ready.connect(self._on_incoming_link)
        self._discovery = Discovery(identity.origin_id, self)
        self._discovery.peer_seen.connect(self._on_peer_seen)

        self._link: PeerLink | None = None
        self._peer_capabilities: frozenset[str] = frozenset()
        self._attempt = 0
        self._manual_address = ""
        self._board_addresses: list[str] = []
        # Какой по счёту кандидат набирается сейчас и идёт ли набор вообще:
        # неудача набора ведёт к следующему кандидату без паузы, а разрыв
        # живой связи - к обычной паузе переподключения.
        self._candidate_index = 0
        self._dialing = False
        self._state = LinkState.UNPAIRED if trust.peer() is None else LinkState.DISCONNECTED

        # Состояние самого связывания - отдельное от рабочей связи: рабочая
        # связь (_link) не появляется, пока обе стороны не согласились.
        self._pairing = False
        self._pairing_link: PeerLink | None = None
        self._pairing_message_slot = None
        self._pairing_lost_slot = None
        self._pairing_candidate: PairingCandidate | None = None
        self._local_agreed = False
        self._remote_agreed = False

        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._try_connect)

        self._pairing_window = QTimer(self)
        self._pairing_window.setSingleShot(True)
        self._pairing_window.setInterval(PAIRING_WINDOW_MS)
        self._pairing_window.timeout.connect(self._abort_pairing)

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

    @property
    def link(self) -> PeerLink | None:
        """Живая связь, либо None. Нужна передаче файлов - а частный атрибут,
        пересекающий границу модуля, это та форма, которая гниёт первой."""
        return self._link

    @property
    def peer_capabilities(self) -> frozenset[str]:
        """Что умеет пир. Пустое множество означает "ещё не знаем"."""
        return self._peer_capabilities

    def peer_supports(self, capability: str) -> bool:
        return capability in self._peer_capabilities

    def _set_state(self, state: LinkState) -> None:
        if state is self._state:
            return
        self._state = state
        self.state_changed.emit(state.value)

    def _on_content_failed(self, reason: str) -> None:
        """Пир исчез между копированием и вставкой - §11: журнал и разрыв в трее.

        Сама связь уже реагирует на разрыв отдельно (см. _on_disconnected);
        здесь фиксируется именно неудачная попытка отдать содержимое, чтобы
        она осталась видна в журнале и в списке последних событий.
        """
        message = f"не удалось передать содержимое буфера: {reason}"
        logger.warning(message)
        self.event_logged.emit(message)

    # ------------------------------------------------------------------ жизненный цикл

    def start(self) -> None:
        self._listener.expect(self.peer.fingerprint if self.peer else _NOBODY_IS_WELCOME)
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
        self._service.cancel_reconnect()
        self._dialing = False
        self._candidate_index = 0
        self._clear_pairing_attempt(close_link=True)
        self._end_pairing()
        self._listener.stop()
        if self._link is not None:
            self._link.close()
            self._link = None
        self._service.detach_link()
        self._set_state(LinkState.UNPAIRED if self.peer is None else LinkState.DISCONNECTED)

    def recover_after_resume(self) -> None:
        """Replace a pre-sleep link before it can swallow a clipboard offer.

        TCP can remain ESTABLISHED locally across sleep even though the peer
        has already discarded the session.  Waiting for its eventual socket
        error leaves a window in which the UI says connected and writes are
        accepted locally but never reach the peer.
        """
        if self._link is None:
            return
        self._service.prepare_for_reconnect()
        self._drop("компьютер вышел из сна")

    def set_manual_address(self, address: str) -> None:
        """Оператор ввёл адрес вручную - соединиться по нему заново.

        Если связь уже жива, её нужно сперва закрыть: иначе прежняя связь
        продолжает разговаривать (heartbeat, объявления) одновременно с
        новой попыткой, а attach_link() у сервиса подключает offer_ready
        второй раз к тому же слоту - каждое копирование уходило бы на
        второй компьютер дважды.
        """
        self._manual_address = address.strip()
        if self.peer is None:
            return
        self._retry.stop()
        self._silence.stop()
        if self._link is not None:
            self._link.close()
            self._link = None
            self._service.detach_link()
        self._attempt = 0
        self._try_connect()

    def restore_manual_address(self, address: str) -> None:
        """Сохранённый ручной адрес при запуске - без немедленного звонка."""
        self._manual_address = address.strip()

    def set_board_addresses(self, addresses: list[str]) -> None:
        """Адреса второго компьютера, которые сообщила плата.

        Живую связь это не трогает: список нужен только следующему набору. Но
        если список действительно изменился, эта сторона звонящая (пир уже
        доверен, и это НЕ ветка ожидания - peer.origin_id < наш origin_id), и
        сейчас нет ни живой связи, ни набора в процессе, ни связывания, ни
        тикающего таймера повтора - ждать до пяти секунд следующего такта
        обмена незачем: набор начинается заново прямо сейчас, с первого
        кандидата.

        Немедленный набор разрешён только из состояний, в которых соединяться
        и так уместно прямо сейчас (DISCONNECTED/SEARCHING). BLOCKED (порт
        слушателя занят) и PROTOCOL_MISMATCH (пир уже признан несовместимым)
        обязаны остаться собой: новый список адресов не чинит ни то, ни
        другое, а набор поверх них выглядел бы так, будто чинит.
        """
        filtered = [address for address in addresses if address]
        changed = filtered != self._board_addresses
        self._board_addresses = filtered
        if not changed:
            return
        peer = self.peer
        if peer is None or peer.origin_id < self._identity.origin_id:
            return
        if self._state not in (LinkState.DISCONNECTED, LinkState.SEARCHING):
            return
        if self._link is not None or self._dialing or self._pairing:
            return
        if self._retry.isActive():
            return
        self._candidate_index = 0
        self._try_connect()

    # ------------------------------------------------------------------ парринг: вход и код

    def begin_pairing(self) -> None:
        # Уже есть живая связь: «Связать» не должно её рвать ради нового поиска.
        # Смена пира - через forget_peer() («Забыть компьютер»).
        if self._link is not None:
            return
        # Пир уже доверен, но связь не поднята: воссоединяемся по сохранённому
        # адресу (unicast), а не запускаем discovery. Мультикаст-поиск нужен
        # только для ПЕРВИЧНОГО связывания; на упакованной сборке он к тому же
        # может быть недоступен (Local Network / multicast), тогда как unicast
        # к известному адресу работает. Нового пира связываем только после
        # forget_peer(), когда self.peer снова None.
        if self.peer is not None:
            self._retry.stop()
            self._silence.stop()
            self._attempt = 0
            # Набор начинается заново, не оттуда, где мог застрять прежний
            # неудачный набор: оператор нажал «Связать» именно для того,
            # чтобы попробовать снова, начиная с последнего удачного адреса.
            self._candidate_index = 0
            self._dialing = False
            self._try_connect()
            return
        # Повторный запуск начинает новый lifecycle. Согласия и callbacks
        # прежней TLS-связи не имеют права перейти в него.
        self._clear_pairing_attempt(close_link=True)
        self._pairing = True
        # Только теперь и ровно на время связывания - чужой сертификат.
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
        if self._manual_address:
            self._pairing_connect(self._manual_address, TCP_PORT)
        self._pairing_window.start()
        self._set_state(LinkState.SEARCHING)

    def confirm_pairing(self, candidate: PairingCandidate) -> None:
        """Человек сверил код и подтвердил свою половину.

        Доверие закрепляется и обмен включается только тогда, когда согласны
        ОБЕ стороны: наша половина - здесь и сейчас, вторая половина - в
        PAIR_CONFIRM, который должен прийти от пира. Одностороннее
        подтверждение не решает ничего само по себе - это ровно та щель, из-за
        которой раньше чужой сертификат проходил без единого подтверждения.
        """
        if self._pairing_link is None or candidate is not self._pairing_candidate:
            return
        self._local_agreed = True
        self._pairing_link.send(Message(MessageType.PAIR_CONFIRM, {"agree": True}, b""))
        self._maybe_finish_pairing()

    def reject_pairing(self, candidate: PairingCandidate) -> None:
        """Refuse only the attempt that emitted ``candidate``.

        A modal dialog runs a nested event loop, so its answer can arrive
        after a disconnect and a new attempt. Object identity makes the
        emitted candidate an attempt token even when the same computer comes
        back with identical fields.
        """
        link = self._pairing_link
        if link is None or candidate is not self._pairing_candidate:
            return
        try:
            link.send(Message(MessageType.PAIR_CONFIRM, {"agree": False}, b""))
        finally:
            # Sending can synchronously pump a disconnect callback. Never let
            # an old dialog close a replacement attempt installed meanwhile.
            if link is self._pairing_link and candidate is self._pairing_candidate:
                self._abort_pairing()

    def forget_peer(self) -> None:
        """Разорвать всё, что держало доверие к прежнему пиру - не только запись о нём.

        Раньше здесь останавливалось хранилище доверия, но не таймеры: сторож
        молчания продолжал тикать по уже закрытой связи, его срабатывание
        запускало _drop() -> _retry, а _try_connect() (peer уже None, адреса
        нет) включал маячок навсегда - забытый узел начинал бессрочно
        объявлять себя всей сети, что прямо запрещено §9. Останавливать нужно
        всё, что могло бы само себя перезавести.
        """
        self._retry.stop()
        self._silence.stop()
        self._discovery.stop()
        self._attempt = 0
        self._dialing = False
        self._candidate_index = 0
        self._service.cancel_reconnect()
        self._trust.forget()
        if self._link is not None:
            self._link.close()
            self._link = None
        self._service.detach_link()
        self._listener.expect(_NOBODY_IS_WELCOME)
        self.peer_changed.emit(None)
        self._set_state(LinkState.UNPAIRED)

    def _end_pairing(self) -> None:
        self._pairing = False
        self._pairing_window.stop()
        self._discovery.stop()
        # Дверь, открытая на время связывания, обязана закрыться: связывание
        # закончилось (успехом или нет), а self.peer уже отражает новое
        # доверие, если закрепление произошло раньше этого вызова.
        self._listener.expect(self.peer.fingerprint if self.peer else _NOBODY_IS_WELCOME)

    def _abort_pairing(self) -> None:
        """Окно связывания истекло, либо пир отказался подтверждать - в обоих
        случаях связывание прекращается, а не продолжается в надежде на чудо.
        """
        self._clear_pairing_attempt(close_link=True)
        self._end_pairing()
        self._set_state(LinkState.UNPAIRED if self.peer is None else LinkState.DISCONNECTED)

    def _clear_pairing_attempt(self, *, close_link: bool) -> None:
        """Отвязать конкретную попытку, не меняя состояние pairing-window."""
        link = self._pairing_link
        message_slot = self._pairing_message_slot
        lost_slot = self._pairing_lost_slot

        # Сначала делаем link неактуальным: close()/disconnect могут синхронно
        # или уже из очереди доставить старый сигнал.
        self._pairing_link = None
        self._pairing_message_slot = None
        self._pairing_lost_slot = None
        self._pairing_candidate = None
        self._local_agreed = False
        self._remote_agreed = False

        if link is None:
            return
        if message_slot is not None:
            link.message_received.disconnect(message_slot)
        if lost_slot is not None:
            link.disconnected.disconnect(lost_slot)
        if close_link:
            link.close()

    def _offer_pairing(self, candidate: PairingCandidate) -> None:
        code = pairing_code(self._identity.fingerprint, candidate.fingerprint)
        self.pairing_code_ready.emit(code, candidate)

    def _on_peer_seen(self, beacon: Beacon, address: str) -> None:
        if not self._pairing:
            # Спаренный компьютер сменил адрес - этого достаточно, чтобы позвонить.
            if self.peer is not None and beacon.origin_id == self.peer.origin_id:
                self._trust.update_address(address)
                # Свежий адрес из маячка - самый достоверный кандидат сейчас;
                # набор должен начаться с него, а не продолжаться с середины
                # прежнего списка, в которой мог застрять неудачный набор.
                self._candidate_index = 0
                self._dialing = False
                self._try_connect()
            return
        if self._pairing_link is not None:
            return  # кандидат на связывание уже есть - маячки остальных не трогаем
        if self._identity.origin_id < beacon.origin_id:
            self._pairing_connect(address, beacon.port)
        # Иначе звонят они: origin_id из маячка меньше нашего, ждём их звонка.

    # ------------------------------------------------------------------ парринг: TLS-рукопожатие

    def _pairing_connect(self, address: str, port: int) -> None:
        link = PeerLink(self._identity, self)
        link.connected.connect(lambda _fingerprint: self._on_pairing_link_ready(link))
        link.connect_to(address, port, None)

    def _on_pairing_link_ready(self, link: PeerLink) -> None:
        if not self._pairing:
            # Связывание уже закончилось (окно истекло) раньше, чем
            # рукопожатие успело завершиться - такому соединению тоже нет места.
            link.close()
            return
        if self._pairing_link is not None:
            link.close()
            return
        self._pairing_link = link
        message_slot = lambda message, source=link: self._on_pairing_message(source, message)
        lost_slot = lambda reason, source=link: self._on_pairing_link_lost(source, reason)
        self._pairing_message_slot = message_slot
        self._pairing_lost_slot = lost_slot
        link.message_received.connect(message_slot)
        link.disconnected.connect(lost_slot)
        link.send(
            Message(
                MessageType.PAIR_REQUEST,
                {
                    "origin_id": self._identity.origin_id,
                    "machine_name": self._machine_name,
                    "protocol_major": PROTOCOL_MAJOR,
                    "protocol_minor": PROTOCOL_MINOR,
                },
                b"",
            )
        )

    def _on_pairing_message(self, link: PeerLink, message: Message) -> None:
        if link is not self._pairing_link:
            return
        if message.type is MessageType.PAIR_REQUEST:
            candidate = PairingCandidate(
                origin_id=str(message.header.get("origin_id", "")),
                machine_name=str(message.header.get("machine_name", "")),
                fingerprint=link.peer_fingerprint,
                address=link.peer_address,
                port=TCP_PORT,
            )
            self._pairing_candidate = candidate
            self._offer_pairing(candidate)
        elif message.type is MessageType.PAIR_CONFIRM:
            if not bool(message.header.get("agree", False)):
                self._abort_pairing()
                return
            self._remote_agreed = True
            self._maybe_finish_pairing()

    def _on_pairing_link_lost(self, link: PeerLink, _reason: str) -> None:
        if link is not self._pairing_link:
            return
        # Связь во время связывания оборвалась раньше согласия обеих сторон -
        # сбрасываем кандидата, но НЕ выходим из режима связывания целиком:
        # маячок ещё может привести к новой попытке до истечения окна.
        self._clear_pairing_attempt(close_link=False)

    def _maybe_finish_pairing(self) -> None:
        if not (self._local_agreed and self._remote_agreed):
            return
        candidate = self._pairing_candidate
        link = self._pairing_link
        if candidate is None or link is None:
            return
        self._clear_pairing_attempt(close_link=False)
        self._trust.remember(candidate.as_trusted())
        self._end_pairing()
        self.peer_changed.emit(self._trust.peer())
        # Та же подписка, что и для входящей связи вне парринга: связь,
        # рождённая связыванием, не проходит через _try_connect() и без
        # этого не получила бы обработчик разрыва вовсе.
        link.disconnected.connect(self._on_disconnected)
        self._on_connected(link)
        self.event_logged.emit(f"связано с «{candidate.machine_name}»")

    # ------------------------------------------------------------------ рабочая связь

    def _candidates(self) -> list[str]:
        """Куда звонить, по порядку: ручной адрес - единственный кандидат;
        иначе последний удачный, затем адреса от платы, без повторов."""
        if self._manual_address:
            return [self._manual_address]
        peer = self.peer
        result: list[str] = []
        for address in ([peer.last_address] if peer else []) + self._board_addresses:
            if address and address not in result:
                result.append(address)
        return result

    def _try_connect(self) -> None:
        peer = self.peer
        if peer is None:
            # Без доверенного пира соединяться не с кем, а искать его маячком
            # означало бы объявлять себя всей сети бессрочно - см. §9 и
            # forget_peer(). Единственный законный способ снова оказаться
            # здесь без пира - забытый узел; тот обязан молчать.
            return
        candidates = self._candidates()
        if not candidates:
            self._start_looking()
            return
        if peer.origin_id < self._identity.origin_id:
            # Звонит меньший. Мы больше - ждём звонка, но следим за молчанием,
            # чтобы вечно повисшее ожидание тоже когда-нибудь стало разрывом.
            self._silence.start()
            return

        address = candidates[self._candidate_index % len(candidates)]
        self._dialing = True
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
        if self._pairing:
            self._on_pairing_link_ready(link)
            return
        peer = self.peer
        if peer is None or link.peer_fingerprint != peer.fingerprint:
            # Второй пояс защиты: даже если слушатель по ошибке пропустил
            # чужого (см. expect() выше), координатор сам не пускает его
            # дальше рукопожатия - до обмена данными он не должен дойти ни
            # при каких обстоятельствах.
            link.close()
            return
        if self._link is not None:
            link.close()
            return
        # Без этой подписки обрыв входящей связи проходил незамеченным:
        # _try_connect() подписывает disconnected сам, но эта связь пришла не
        # оттуда - разрыв не приводил ни к _drop(), ни к переподключению, а
        # координатор так и оставался в CONNECTED со связью, которой уже нет.
        link.disconnected.connect(self._on_disconnected)
        self._on_connected(link)

    def _on_connected(self, link: PeerLink) -> None:
        self._link = link
        self._dialing = False
        self._candidate_index = 0
        address = _plain_ipv4(getattr(link, "peer_address", "") or "")
        if address:
            peer = self.peer
            if peer is not None and peer.last_address != address:
                self._trust.update_address(address)
            self.address_in_use.emit(address)
        generation = getattr(link, "connection_generation", "unknown")
        # Переподключение может привести на другую сборку пира - забыть, что
        # умел прежний пир, чтобы не унести его возможности на нового.
        self._peer_capabilities = frozenset()
        self._attempt = 0
        self._retry.stop()
        self._discovery.stop()
        sent = link.send(
            Message(
                MessageType.HELLO,
                {
                    "protocol_major": PROTOCOL_MAJOR,
                    "protocol_minor": PROTOCOL_MINOR,
                    "origin_id": self._identity.origin_id,
                    "machine_name": self._machine_name,
                    "capabilities": list(CAPABILITIES),
                },
                b"",
            )
        )
        logger.info(
            "peer_hello_sent connection_generation=%s success=%s",
            generation,
            sent is not False,
        )
        link.message_received.connect(self._on_message)
        self._service.attach_link(link)
        logger.info(
            "coordinator_link_attached connection_generation=%s",
            generation,
        )
        self._silence.start()
        self._set_state(LinkState.CONNECTED)

    def _on_message(self, message: Message) -> None:
        self._silence.start()
        if message.type is MessageType.HELLO:
            generation = getattr(self._link, "connection_generation", "unknown")
            logger.info(
                "peer_hello_received connection_generation=%s protocol_major=%s",
                generation,
                message.header.get("protocol_major"),
            )
            if int(message.header.get("protocol_major", -1)) != PROTOCOL_MAJOR:
                self._drop(
                    "вторая машина говорит на другой версии протокола",
                    protocol_mismatch=True,
                )
                return
            self._peer_capabilities = _capabilities_from(message.header)
            logger.info(
                "peer_capabilities_known connection_generation=%s capabilities=%s",
                generation,
                ",".join(sorted(self._peer_capabilities)),
            )
            self.capabilities_known.emit(self._peer_capabilities)

    def _on_disconnected(self, reason: str) -> None:
        self._drop(reason)

    def _drop(self, reason: str, *, protocol_mismatch: bool = False) -> None:
        """Разорвать рабочую связь. ``reason`` больше не исчезает молча -

        §11 требует отдельного сообщения при расхождении старшей версии
        протокола: обновление не случится само, поэтому бесконечные повторы
        были бы враньём о том, что проблема временная, а трей всё равно
        писал бы "нет связи", хотя чинить нужно не сеть.
        """
        self._silence.stop()
        if self._link is not None:
            self._link.close()
            self._link = None
        self._service.detach_link()
        logger.info("связь разорвана: %s", reason)
        self.event_logged.emit(f"связь разорвана: {reason}")
        if protocol_mismatch:
            self._set_state(LinkState.PROTOCOL_MISMATCH)
            return
        if self._dialing:
            self._dialing = False
            if self._candidate_index + 1 < len(self._candidates()):
                # Этот адрес не ответил - следующий пробуем сразу: пауза
                # переподключения нужна после неудачи всего списка, а не
                # каждого адреса в нём.
                self._candidate_index += 1
                self._set_state(LinkState.DISCONNECTED)
                self._retry.start(0)
                return
        self._candidate_index = 0
        if not self._manual_address:
            self._start_looking()
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
