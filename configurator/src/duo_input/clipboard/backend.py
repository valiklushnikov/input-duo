"""Граница между операционной системой и всем остальным.

RemoteMimeData - это обещание, а не данные: она объявляет форматы, которые
второй компьютер согласился отдать, и идёт за содержимым только тогда, когда
кто-то действительно вставляет. Поэтому скопированный и не вставленный пароль
не покидает машину, на которой его скопировали.

Маркер происхождения объявляется рядом с настоящими форматами: по нему
собственный наблюдатель узнаёт свою же вставку и не объявляет её обратно.
"""

from __future__ import annotations

import logging
from typing import Callable, Protocol, runtime_checkable

from PySide6.QtCore import QByteArray, QMimeData

from .offer import ClipboardOffer

logger = logging.getLogger(__name__)

#: Формат-метка: чьё это содержимое и под каким номером.
ORIGIN_MIME = "application/x-duo-input-origin"

#: Как достать содержимое одного формата. Может бросить - это нормально.
ContentFetcher = Callable[[str], bytes]


def base_mime(mime_type: str) -> str:
    """Имя формата без параметров: ``text/plain;charset=utf-8`` -> ``text/plain``.

    Qt спрашивает содержимое под именем с параметром, а объявляли мы имя без
    него. Сравнение строк целиком означало бы вставку, которая молча не
    работает: ни ошибки, ни записи в журнал, просто пустой буфер.
    """
    return mime_type.split(";", 1)[0].strip()


class ClipboardSnapshot:
    """Локальный снимок буфера: что скопировали на этой машине."""

    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = dict(payloads)

    def payload(self, mime: str) -> bytes | None:
        return self.payloads.get(mime)


class RemoteMimeData(QMimeData):
    """Содержимое второго компьютера, которого здесь ещё нет."""

    def __init__(self, offer: ClipboardOffer, fetcher: ContentFetcher) -> None:
        super().__init__()
        self._offer = offer
        self._fetcher = fetcher
        self._cache: dict[str, bytes] = {}
        self._marker = f"{offer.origin_id}:{offer.seq}".encode("ascii")

    def formats(self):  # noqa: N802 - Qt API
        return [*self._offer.mimes(), ORIGIN_MIME]

    def retrieveData(self, mime_type: str, preferred_type):  # noqa: N802 - Qt API
        requested = base_mime(mime_type)
        if requested == ORIGIN_MIME:
            return QByteArray(self._marker)
        if requested not in self._offer.mimes():
            return QByteArray()
        if requested in self._cache:
            return QByteArray(self._cache[requested])
        try:
            payload = self._fetcher(requested)
        except Exception:  # noqa: BLE001 - пустая вставка честнее, чем падение
            logger.warning(f"Failed to fetch clipboard format {requested!r}", exc_info=True)
            return QByteArray()
        self._cache[requested] = payload
        return QByteArray(payload)


@runtime_checkable
class ClipboardBackend(Protocol):
    """Всё, что подсистеме нужно знать об операционной системе."""

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def publish(self, offer: ClipboardOffer, fetcher: ContentFetcher) -> None: ...

    def payload(self, mime: str) -> bytes | None: ...


__all__ = [
    "ORIGIN_MIME",
    "ClipboardBackend",
    "ClipboardSnapshot",
    "ContentFetcher",
    "RemoteMimeData",
    "base_mime",
]
