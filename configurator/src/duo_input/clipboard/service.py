"""Правила общего буфера: что объявлять, что публиковать, что игнорировать.

Здесь нет ни сокетов, ни Win32. Сервис получает снимки от границы платформы и
объявления от связи, а наружу выдаёт сигналы: "вот что стоит объявить" и "вот
что у нас попросили". Благодаря этому все правила проверяются без сети.

Три пояса против петли, потому что ни один не полон в одиночку. Первый - маркер
происхождения, его ставит и проверяет граница платформы. Второй - сравнение
отпечатков: то, что мы только что приняли, не уходит обратно. Третий - отказ от
объявления с нашим собственным origin_id.
"""

from __future__ import annotations

import hashlib
import logging
import time

from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal

from .backend import ClipboardSnapshot
from .offer import ClipboardOffer, describe
from .wire import Message, MessageType

logger = logging.getLogger(__name__)

#: Как часто мы напоминаем о себе и сколько молчания считаем разрывом.
HEARTBEAT_MS = 10_000
SILENCE_LIMIT_MS = 30_000

#: Сколько ждём всю передачу целиком.
#:
#: Отдельного срока на "первый ответ" здесь нет: содержимое приходит одним
#: кадром CONTENT, а не потоком, - у этого слоя нет наблюдаемой границы между
#: "пир начал отвечать" и "пир закончил отвечать", поэтому второй срок
#: (M1 ревью) был объявлен и не мог быть задействован честно. См. правку
#: спецификации §5.
TRANSFER_TIMEOUT_MS = 30_000


def _mimes(mimes) -> str:
    """Типы через запятую - для журнала. Только типы, не содержимое."""
    return ",".join(mimes) or "-"


class ClipboardService(QObject):
    """Мозг подсистемы. Ничего не знает ни об ОС, ни о сети."""

    offer_ready = Signal(object)
    link_state_changed = Signal(str)
    content_failed = Signal(str)

    def __init__(self, own_origin_id: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._own_origin_id = own_origin_id
        self._backend = None
        self._seq = 0
        self._last_sent: ClipboardOffer | None = None
        self._last_sent_payloads: dict[str, bytes] = {}
        # A copy made while the transport is being rebuilt (notably just
        # after wake) must be announced on the replacement link.  Keep only
        # the newest offer: its payload cache already supersedes older ones.
        self._pending_local_offer: ClipboardOffer | None = None
        self._buffer_local_offers = False
        self._last_received: ClipboardOffer | None = None
        self._link = None
        # Ожидания содержимого - по ключу (seq, mime), а не одно на весь объект.
        #
        # Windows запрашивает форматы одной вставки последовательно, но
        # вложенный цикл событий внутри _fetch прокачивает и нативные
        # сообщения тоже - поэтому второй _fetch во время первого не теория,
        # а обычный порядок событий (второй запрос форматов той же вставки
        # приходит, пока первый ещё ждёт ответа по сети). Общее поле на весь
        # объект здесь было ошибкой: второй вызов переписывал бы состояние
        # первого, и пришедший позже ответ на первый запрос терялся бы молча.
        # Список значений на ключ - на случай, если два запроса одного и того
        # же (seq, mime) идут параллельно: оба ждут один и тот же ответ, и
        # оба должны его получить.
        self._pending_fetches: dict[tuple[int, str], list[dict]] = {}
        self._heartbeat = QTimer(self)
        self._heartbeat.setInterval(HEARTBEAT_MS)
        self._heartbeat.timeout.connect(self._send_ping)

    @property
    def own_origin_id(self) -> str:
        return self._own_origin_id

    @property
    def last_sent_offer(self) -> ClipboardOffer | None:
        return self._last_sent

    @property
    def last_received_offer(self) -> ClipboardOffer | None:
        return self._last_received

    def attach_backend(self, backend) -> None:
        self._backend = backend

    # ------------------------------------------------------------------ локальное

    def on_local_snapshot(self, snapshot: ClipboardSnapshot) -> None:
        if not snapshot.payloads:
            return
        if self._echoes_what_we_received(snapshot):
            logger.info(
                "clipboard_offer_skipped reason=echo mimes=%s", _mimes(snapshot.payloads)
            )
            return

        try:
            descriptors = describe(snapshot.payloads)
        except ValueError as error:
            # Тот же исход, что и у платформенной границы, которая обычно
            # отфильтровывает такой снимок раньше, чем он сюда дойдёт: запись
            # в журнал и отказ от объявления, а не падение (§5). describe()
            # продолжает бросать исключение - это её собственный контракт для
            # вызывающих, которые сами ничего не отфильтровали, - но здесь
            # оно ловится и превращается в то же самое "молча не объявлено",
            # что и на границе платформы, а не в необработанное исключение
            # посреди слота, подключённого к сигналу Qt.
            logger.warning("снимок буфера не объявлен: %s", error)
            logger.info(
                "clipboard_offer_skipped reason=undescribable mimes=%s",
                _mimes(snapshot.payloads),
            )
            return

        self._seq += 1
        offer = ClipboardOffer(
            origin_id=self._own_origin_id,
            seq=self._seq,
            descriptors=descriptors,
        )
        self._last_sent = offer
        self._last_sent_payloads = dict(snapshot.payloads)
        logger.info(
            "clipboard_offer_created seq=%d mimes=%s bytes=%d linked=%s",
            offer.seq,
            _mimes(offer.mimes()),
            sum(len(payload) for payload in snapshot.payloads.values()),
            self._link is not None,
        )
        if self._link is None:
            # Без связи offer_ready ни к чему не подключён, и объявление
            # пропало бы молча - именно такую потерю раньше было не увидеть.
            if self._buffer_local_offers:
                self._pending_local_offer = offer
                logger.info("clipboard_offer_buffered seq=%d", offer.seq)
            else:
                logger.info("clipboard_offer_dropped seq=%d reason=no_link", offer.seq)
        self.offer_ready.emit(offer)

    def content_for(self, mime: str, seq: int) -> bytes | None:
        """Содержимое, которое у нас просят - но только для текущего объявления."""
        if self._last_sent is None or seq != self._last_sent.seq:
            return None
        return self._last_sent_payloads.get(mime)

    # ------------------------------------------------------------------ удалённое

    def on_remote_offer(self, offer: ClipboardOffer) -> None:
        logger.info(
            "clipboard_offer_received seq=%d mimes=%s", offer.seq, _mimes(offer.mimes())
        )
        # Третий пояс: объявление вернулось к нам же.
        if offer.origin_id == self._own_origin_id:
            logger.info("clipboard_offer_ignored seq=%d reason=own", offer.seq)
            return
        if self._last_received is not None and offer.seq <= self._last_received.seq:
            logger.info(
                "clipboard_offer_ignored seq=%d reason=stale last_seq=%d",
                offer.seq,
                self._last_received.seq,
            )
            return
        if self._backend is None:
            logger.info("clipboard_offer_ignored seq=%d reason=no_backend", offer.seq)
            return

        self._last_received = offer
        self._backend.publish(offer, lambda mime: self._fetch(mime, offer))
        logger.info("clipboard_offer_published seq=%d", offer.seq)

    def _fetch(self, mime: str, offer: ClipboardOffer) -> bytes:
        """Забрать содержимое у пира. Синхронно - этого требует буфер обмена.

        Вставляющее приложение ждёт ответа, поэтому здесь крутится вложенный
        цикл событий. Признак подавления (``_suspended`` у платформенной
        границы) на это время НЕ выставлен - он выставлен только вокруг
        собственного ``setMimeData`` внутри ``publish()``, а тот вызов уже
        завершился к моменту, когда ОС дёргает ``retrieveData`` и приходит
        сюда. Поэтому локальное копирование, случившееся прямо во время
        ожидания, обрабатывается как обычно - вложенный цикл лишь прокачивает
        и эти события тоже, наравне с сетевыми.

        Состояние ожидания (`entry`) - локальная переменная этого вызова, а
        не поле объекта: параллельный вызов _fetch (для другого формата той
        же вставки, пока этот ещё ждёт) заводит свою собственную запись и
        свой собственный вложенный цикл, не трогая этот. Ответ приходит по
        сети с указанием (seq, mime) и адресуется по этому же ключу - см.
        _receive_content и _receive_content_error.
        """
        if self._link is None:
            logger.info(
                "clipboard_fetch_failed seq=%d mime=%s reason=no_link ms=0", offer.seq, mime
            )
            raise TimeoutError("нет связи со вторым компьютером")

        key = (offer.seq, mime)
        entry: dict = {"payload": None, "error": None, "loop": None}
        self._pending_fetches.setdefault(key, []).append(entry)

        self._send(Message(MessageType.FETCH, {"seq": offer.seq, "mime": mime}, b""))
        logger.info("clipboard_fetch_sent seq=%d mime=%s", offer.seq, mime)
        started = time.monotonic()

        loop = QEventLoop()
        entry["loop"] = loop
        QTimer.singleShot(TRANSFER_TIMEOUT_MS, loop.quit)
        loop.exec()

        waiters = self._pending_fetches.get(key)
        if waiters is not None and entry in waiters:
            waiters.remove(entry)
            if not waiters:
                del self._pending_fetches[key]

        elapsed_ms = int((time.monotonic() - started) * 1000)
        if entry["error"] is not None:
            logger.info(
                "clipboard_fetch_failed seq=%d mime=%s reason=%r ms=%d",
                offer.seq,
                mime,
                entry["error"],
                elapsed_ms,
            )
            self.content_failed.emit(entry["error"])
            raise TimeoutError(entry["error"])
        if entry["payload"] is None:
            logger.info(
                "clipboard_fetch_failed seq=%d mime=%s reason=timeout ms=%d",
                offer.seq,
                mime,
                elapsed_ms,
            )
            self.content_failed.emit("второй компьютер не ответил")
            raise TimeoutError("второй компьютер не ответил")
        logger.info(
            "clipboard_fetch_done seq=%d mime=%s bytes=%d ms=%d",
            offer.seq,
            mime,
            len(entry["payload"]),
            elapsed_ms,
        )
        return entry["payload"]

    # ------------------------------------------------------------------ связь

    def prepare_for_reconnect(self) -> None:
        """Buffer copies made during the upcoming, explicitly requested reconnect.

        This is deliberately opt-in rather than the default disconnected
        behavior: clipboard history from before first pairing must never be
        sent to a newly trusted computer.
        """
        self._pending_local_offer = None
        self._buffer_local_offers = True

    def cancel_reconnect(self) -> None:
        """Discard wake-recovery state before stopping or changing trust."""
        self._pending_local_offer = None
        self._buffer_local_offers = False

    def attach_link(self, link) -> None:
        """Взять готовое соединение и начать по нему разговаривать."""
        self._link = link
        link.message_received.connect(self.handle_message)
        link.disconnected.connect(self._on_link_lost)
        self.offer_ready.connect(self._send_offer)
        self._heartbeat.start()
        pending = self._pending_local_offer
        self._pending_local_offer = None
        self._buffer_local_offers = False
        if pending is not None:
            self._send_offer(pending)
        self.link_state_changed.emit("connected")

    def detach_link(self) -> None:
        """Отсоединиться от связи по собственной инициативе (не по её разрыву).

        Симметрично _on_link_lost и в части остановки heartbeat, и в части
        пробуждения ожиданий содержимого: тому, кто ждёт ответа по сети, всё
        равно, почему связи больше нет. Без этого явный detach_link во время
        ожидания не отпускал бы вложенный цикл, и вызов вернулся бы только
        через TRANSFER_TIMEOUT_MS вместо немедленного отказа - а именно
        detach_link является тем методом, которым связь рвут программно (по
        сторожу молчания, по несовпадению версии протокола и так далее).
        """
        if self._link is None:
            return
        self._heartbeat.stop()
        try:
            self.offer_ready.disconnect(self._send_offer)
        except (RuntimeError, TypeError):
            pass
        self._link = None
        # Как и в _on_link_lost: следующая сессия начинается с чистого seq.
        self._last_received = None
        self._fail_all_pending_fetches("связь закрыта")
        self.link_state_changed.emit("disconnected")

    def handle_message(self, message: Message) -> None:
        if message.type is MessageType.OFFER:
            self.on_remote_offer(ClipboardOffer.from_dict(message.header))
        elif message.type is MessageType.FETCH:
            self._answer_fetch(message)
        elif message.type is MessageType.CONTENT:
            self._receive_content(message)
        elif message.type is MessageType.CONTENT_ERROR:
            self._receive_content_error(message)
        elif message.type is MessageType.PING:
            self._send(Message(MessageType.PONG, {}, b""))

    def _send(self, message: Message) -> None:
        if self._link is not None:
            self._link.send(message)

    def _send_ping(self) -> None:
        self._send(Message(MessageType.PING, {}, b""))

    def _send_offer(self, offer: ClipboardOffer) -> None:
        self._send(Message(MessageType.OFFER, offer.to_dict(), b""))
        logger.info(
            "clipboard_offer_sent seq=%d connection_generation=%s",
            offer.seq,
            getattr(self._link, "connection_generation", "unknown"),
        )

    def _on_link_lost(self, reason: str) -> None:
        self._heartbeat.stop()
        self._link = None
        # Связь оборвалась - возможно, второй компьютер перезапустился, и его
        # счётчик объявлений начнётся заново с малого числа. Забываем последний
        # принятый seq, иначе следующая сессия отвергала бы свежие объявления
        # как "устаревшие" (offer.seq <= _last_received.seq), пока не
        # перезапустят и нашу сторону.
        self._last_received = None
        self._fail_all_pending_fetches(f"связь потеряна: {reason}")
        self.link_state_changed.emit(f"disconnected: {reason}")

    def _fail_all_pending_fetches(self, reason: str) -> None:
        """Разбудить все ожидания содержимого сразу - связи больше нет ни для одного из них.

        Каждое ожидание получает свой отказ и отпускает свой собственный
        вложенный цикл; список очищает сам вызвавший его _fetch после
        выхода из loop.exec().
        """
        for entries in self._pending_fetches.values():
            for entry in entries:
                entry["error"] = reason
                loop = entry["loop"]
                if loop is not None:
                    loop.quit()

    def _answer_fetch(self, message: Message) -> None:
        mime = str(message.header.get("mime", ""))
        seq = int(message.header.get("seq", -1))
        payload = self.content_for(mime, seq)
        if payload is None:
            last_sent_seq = self._last_sent.seq if self._last_sent is not None else -1
            reason = "stale_seq" if seq != last_sent_seq else "unknown_mime"
            logger.info(
                "clipboard_fetch_refused seq=%d mime=%s reason=%s last_sent_seq=%d",
                seq,
                mime,
                reason,
                last_sent_seq,
            )
            self._send(
                Message(
                    MessageType.CONTENT_ERROR,
                    {"seq": seq, "mime": mime, "reason": "объявление устарело"},
                    b"",
                )
            )
            return
        self._send(Message(MessageType.CONTENT, {"seq": seq, "mime": mime}, payload))
        logger.info("clipboard_fetch_served seq=%d mime=%s bytes=%d", seq, mime, len(payload))

    def _receive_content(self, message: Message) -> None:
        key = (int(message.header.get("seq", -1)), str(message.header.get("mime", "")))
        for entry in self._pending_fetches.get(key, []):
            entry["payload"] = message.blob
            loop = entry["loop"]
            if loop is not None:
                loop.quit()

    def _receive_content_error(self, message: Message) -> None:
        key = (int(message.header.get("seq", -1)), str(message.header.get("mime", "")))
        reason = str(message.header.get("reason", "отказ"))
        for entry in self._pending_fetches.get(key, []):
            entry["error"] = reason
            loop = entry["loop"]
            if loop is not None:
                loop.quit()

    # ------------------------------------------------------------------ пояса

    def _echoes_what_we_received(self, snapshot: ClipboardSnapshot) -> bool:
        """Второй пояс: это то, что нам только что прислали.

        Снимок является эхом только если он не несёт ничего нового: каждый формат
        снимка присутствует в последнем принятом объявлении и имеет тот же отпечаток.
        """
        if self._last_received is None:
            return False
        if not snapshot.payloads:
            return False

        # Снимок - это эхо только если каждый его формат точно совпадает с принятым
        for mime, payload in snapshot.payloads.items():
            expected = self._last_received.digest_of(mime)
            payload_hash = hashlib.sha256(payload).hexdigest()
            # Формат должен быть в объявлении И отпечаток должен совпадать
            if not (expected is not None and expected == payload_hash):
                return False

        return True


__all__ = ["ClipboardService"]
