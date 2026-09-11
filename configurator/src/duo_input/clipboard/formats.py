"""Каноническая форма содержимого буфера, одинаковая на всех платформах.

Wire знает только про четыре MIME из SYNCED_MIMES. Что бы ни лежало в
операционном буфере - TIFF на macOS, DIB на Windows, набор file:// в
uri-list, - наружу уходит канонический вид: PNG для картинки, только веб-URL
для ссылок. Так снимок с двух разных ОС для одного и того же содержимого
получается байт в байт одинаковым, и сетевой слой не знает, кто его снял.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QBuffer, QIODevice, QMimeData
from PySide6.QtGui import QImage, QPixmap

from .offer import MAX_CONTENT_BYTES

logger = logging.getLogger(__name__)

#: Форматы, которые синхронизирует parity. Порядок не важен: снимок - dict.
SYNCED_MIMES = ("text/plain", "text/html", "text/uri-list", "image/png")

_WEB_SCHEMES = frozenset({"http", "https"})


def web_uri_list(mime_data: QMimeData) -> bytes | None:
    """Только веб-ссылки. file:// вырезается: файлы - отдельный milestone.

    Возвращает None, а не b"", когда веб-ссылок нет: это значит "формат для
    синхронизации отсутствует", а не "формат есть, но пуст".
    """
    if not mime_data.hasUrls():
        return None
    urls = [url for url in mime_data.urls() if url.scheme().lower() in _WEB_SCHEMES]
    if not urls:
        return None
    return b"\r\n".join(bytes(url.toEncoded()) for url in urls) + b"\r\n"


def png_bytes(mime_data: QMimeData) -> bytes | None:
    """PNG в любом случае. Готовый PNG отдаём как есть, иначе кодируем.

    Готовый image/png не перекодируется - лишний проход стоил бы времени и
    мог бы изменить цветовой профиль. Если PNG нет, но картинка есть, тип Qt
    приводится к QImage и кодируется. Иной или невалидный тип - None, без
    скрытых различий между платформами.
    """
    if mime_data.hasFormat("image/png"):
        existing = bytes(mime_data.data("image/png"))
        if existing:
            return existing
    if not mime_data.hasImage():
        return None
    image = mime_data.imageData()
    if isinstance(image, QPixmap):
        image = image.toImage()
    if not isinstance(image, QImage):
        return None
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        return None
    return bytes(buffer.data())


def normalized_payload(mime_data: QMimeData, mime: str) -> bytes | None:
    """Канонический payload одного формата, либо None если его нет."""
    if mime == "text/uri-list":
        return web_uri_list(mime_data)
    if mime == "image/png":
        return png_bytes(mime_data)
    if mime_data.hasFormat(mime):
        payload = bytes(mime_data.data(mime))
        return payload or None
    return None


def collect_payloads(mime_data: QMimeData) -> dict[str, bytes]:
    """Все синхронизируемые форматы буфера в каноническом виде.

    Потолок 32 МиБ проверяется ПОСЛЕ нормализации: TIFF на 20 МиБ может стать
    PNG на 38 МиБ.
    """
    payloads: dict[str, bytes] = {}
    for mime in SYNCED_MIMES:
        payload = normalized_payload(mime_data, mime)
        if payload is None:
            continue
        if len(payload) > MAX_CONTENT_BYTES:
            logger.warning(
                "формат %s занимает %d байт, потолок 32 МиБ (%d) — не объявляется",
                mime,
                len(payload),
                MAX_CONTENT_BYTES,
            )
            continue
        payloads[mime] = payload
    return payloads


__all__ = [
    "SYNCED_MIMES",
    "collect_payloads",
    "normalized_payload",
    "png_bytes",
    "web_uri_list",
]
