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

from PySide6.QtCore import QObject, Signal

from .backend import ClipboardSnapshot
from .offer import ClipboardOffer, describe


class ClipboardService(QObject):
    """Мозг подсистемы. Ничего не знает ни об ОС, ни о сети."""

    offer_ready = Signal(object)
    content_requested = Signal(object)

    def __init__(self, own_origin_id: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._own_origin_id = own_origin_id
        self._backend = None
        self._seq = 0
        self._last_sent: ClipboardOffer | None = None
        self._last_sent_payloads: dict[str, bytes] = {}
        self._last_received: ClipboardOffer | None = None

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
        request = {"seq": offer.seq, "mime": mime, "result": None}
        self.content_requested.emit(request)
        payload = request.get("result")
        if payload is None:
            raise TimeoutError(f"содержимое {mime} не получено")
        return payload

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
