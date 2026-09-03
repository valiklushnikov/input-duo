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

from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal

from .backend import ClipboardSnapshot
from .offer import ClipboardOffer, describe
from .wire import Message, MessageType

#: Как часто мы напоминаем о себе и сколько молчания считаем разрывом.
HEARTBEAT_MS = 10_000
SILENCE_LIMIT_MS = 30_000

#: Сколько ждём первого ответа на запрос содержимого и всю передачу целиком.
FETCH_TIMEOUT_MS = 5_000
TRANSFER_TIMEOUT_MS = 30_000


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
        self._last_received: ClipboardOffer | None = None
        self._link = None
        self._pending_fetch: dict[str, bytes] | None = None
        self._fetch_loop: QEventLoop | None = None
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
            return

        self._seq += 1
        offer = ClipboardOffer(
            origin_id=self._own_origin_id,
            seq=self._seq,
            descriptors=describe(snapshot.payloads),
        )
        self._last_sent = offer
        self._last_sent_payloads = dict(snapshot.payloads)
        self.offer_ready.emit(offer)

    def content_for(self, mime: str, seq: int) -> bytes | None:
        """Содержимое, которое у нас просят - но только для текущего объявления."""
        if self._last_sent is None or seq != self._last_sent.seq:
            return None
        return self._last_sent_payloads.get(mime)

    # ------------------------------------------------------------------ удалённое

    def on_remote_offer(self, offer: ClipboardOffer) -> None:
        # Третий пояс: объявление вернулось к нам же.
        if offer.origin_id == self._own_origin_id:
            return
        if self._last_received is not None and offer.seq <= self._last_received.seq:
            return
        if self._backend is None:
            return

        self._last_received = offer
        self._backend.publish(offer, lambda mime: self._fetch(mime, offer))

    def _fetch(self, mime: str, offer: ClipboardOffer) -> bytes:
        """Забрать содержимое у пира. Синхронно - этого требует буфер обмена.

        Вставляющее приложение ждёт ответа, поэтому здесь крутится вложенный
        цикл событий. Обработка новых снимков на это время не нужна и не
        выполняется: снимок делает граница платформы, а она вызвала нас сама.
        """
        if self._link is None:
            raise TimeoutError("нет связи со вторым компьютером")

        self._pending_fetch = {"mime": mime, "payload": None, "error": None}
        self._send(Message(MessageType.FETCH, {"seq": offer.seq, "mime": mime}, b""))

        loop = QEventLoop()
        self._fetch_loop = loop
        QTimer.singleShot(TRANSFER_TIMEOUT_MS, loop.quit)
        loop.exec()
        self._fetch_loop = None

        pending, self._pending_fetch = self._pending_fetch, None
        if pending["error"] is not None:
            self.content_failed.emit(pending["error"])
            raise TimeoutError(pending["error"])
        if pending["payload"] is None:
            self.content_failed.emit("второй компьютер не ответил")
            raise TimeoutError("второй компьютер не ответил")
        return pending["payload"]

    # ------------------------------------------------------------------ связь

    def attach_link(self, link) -> None:
        """Взять готовое соединение и начать по нему разговаривать."""
        self._link = link
        link.message_received.connect(self.handle_message)
        link.disconnected.connect(self._on_link_lost)
        self.offer_ready.connect(self._send_offer)
        self._heartbeat.start()
        self.link_state_changed.emit("connected")

    def detach_link(self) -> None:
        if self._link is None:
            return
        self._heartbeat.stop()
        try:
            self.offer_ready.disconnect(self._send_offer)
        except (RuntimeError, TypeError):
            pass
        self._link = None
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

    def _on_link_lost(self, reason: str) -> None:
        self._heartbeat.stop()
        self._link = None
        if self._fetch_loop is not None:
            self._fetch_loop.quit()
        self.link_state_changed.emit(f"disconnected: {reason}")

    def _answer_fetch(self, message: Message) -> None:
        mime = str(message.header.get("mime", ""))
        seq = int(message.header.get("seq", -1))
        payload = self.content_for(mime, seq)
        if payload is None:
            self._send(
                Message(
                    MessageType.CONTENT_ERROR,
                    {"seq": seq, "mime": mime, "reason": "объявление устарело"},
                    b"",
                )
            )
            return
        self._send(Message(MessageType.CONTENT, {"seq": seq, "mime": mime}, payload))

    def _receive_content(self, message: Message) -> None:
        if self._pending_fetch is None:
            return
        if str(message.header.get("mime")) != self._pending_fetch["mime"]:
            return
        self._pending_fetch["payload"] = message.blob
        if self._fetch_loop is not None:
            self._fetch_loop.quit()

    def _receive_content_error(self, message: Message) -> None:
        if self._pending_fetch is None:
            return
        self._pending_fetch["error"] = str(message.header.get("reason", "отказ"))
        if self._fetch_loop is not None:
            self._fetch_loop.quit()

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
