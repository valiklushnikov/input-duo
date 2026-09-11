"""Единственное место, где Duo Input говорит с AppKit напрямую.

Qt почти всё умеет сам, поэтому здесь только то, чего у него нет: чтение
NSPasteboard.changeCount (Qt не отдаёт его, а на нём держится детект
локального копирования) и host-only ленивая публикация (Qt не умеет пометить
содержимое как "только этот компьютер", а без этого Universal Clipboard
материализовал бы удалённый буфер до вставки).

Модуль намеренно узкий. Всё преобразование типов, ownership и AppKit живут
тут; наружу выходят ровно две функции.
"""

from __future__ import annotations

import logging

import objc
from AppKit import (
    NSPasteboard,
    NSPasteboardContentsCurrentHostOnly,
    NSPasteboardItem,
)
from Foundation import NSData, NSObject

from .backend import ContentFetcher
from .offer import ClipboardOffer

logger = logging.getLogger(__name__)

#: Кастомный тип-метка происхождения (defense-in-depth, см. spec §4).
ORIGIN_UTI = "com.duo-input.origin"

#: MIME parity -> UTI, которыми pasteboard объявляет форматы.
_MIME_TO_UTI = {
    "text/plain": "public.utf8-plain-text",
    "text/html": "public.html",
    "text/uri-list": "public.url",
    "image/png": "public.png",
}
_UTI_TO_MIME = {uti: mime for mime, uti in _MIME_TO_UTI.items()}

#: AppKit проверяет formal-protocol conformance у provider'а во время runtime
#: (conformsToProtocol:), а не только совпадение имён селекторов - без явного
#: перечисления протокола при определении класса NSPasteboardItem логирует
#: "must conform to NSPasteboardItemDataProviderProtocol" и молча отдаёт
#: читателю пустые данные вместо вызова pasteboard:item:provideDataForType:.
_DATA_PROVIDER_PROTOCOL = objc.protocolNamed("NSPasteboardItemDataProvider")


def change_count() -> int:
    """Текущий счётчик изменений общего pasteboard."""
    return int(NSPasteboard.generalPasteboard().changeCount())


def _to_nsdata(payload: bytes) -> NSData:
    return NSData.dataWithBytes_length_(payload, len(payload))


class _DuoDataProvider(NSObject, protocols=[_DATA_PROVIDER_PROTOCOL]):
    """Ленивый поставщик данных: материализует формат только при чтении."""

    def initWithOffer_fetcher_(self, offer, fetcher):  # noqa: N802 - objc API
        self = objc.super(_DuoDataProvider, self).init()
        if self is None:
            return None
        self._offer = offer
        self._fetcher = fetcher
        return self

    def pasteboard_item_provideDataForType_(self, pasteboard, item, uti):  # noqa: N802
        mime = _UTI_TO_MIME.get(str(uti))
        if mime is None:
            return
        try:
            payload = self._fetcher(mime)
        except Exception:  # noqa: BLE001 - пустая вставка честнее падения
            logger.warning("не удалось отдать формат %s", mime, exc_info=True)
            return
        if payload:
            item.setData_forType_(_to_nsdata(payload), uti)


#: NSPasteboard не удерживает data provider сильной ссылкой: без ссылки на
#: стороне Python GC соберёт его и Cmd+V вернёт пусто. Держим ровно один живой
#: provider - текущий; при следующей публикации предыдущий больше не нужен.
_live_provider = None


class PasteboardPublishError(Exception):
    """NSPasteboard отказался принять опубликованный элемент."""


def publish_with_origin(offer: ClipboardOffer, fetcher: ContentFetcher) -> int:
    """Опубликовать удалённый буфер host-only и лениво. Вернуть новый changeCount.

    Возвращается changeCount ПОСЛЕ writeObjects_, а не после
    prepareForNewContentsWithOptions_: подавление петли в MacOSClipboardBackend
    сравнивает на равенство именно с итоговым счётчиком опубликованного
    состояния. Вернуть промежуточное значение означало бы, что следующий опрос
    увидит больший count и примет нашу же публикацию за локальное копирование -
    прямой источник зацикливания.

    Provider удерживается живым внутри модуля (см. _live_provider): NSPasteboard
    его сильной ссылкой не держит, а материализация ленива и произойдёт позже,
    при первой вставке.
    """
    global _live_provider

    pasteboard = NSPasteboard.generalPasteboard()
    pasteboard.prepareForNewContentsWithOptions_(NSPasteboardContentsCurrentHostOnly)

    provider = _DuoDataProvider.alloc().initWithOffer_fetcher_(offer, fetcher)
    item = NSPasteboardItem.alloc().init()

    lazy_types = [_MIME_TO_UTI[m] for m in offer.mimes() if m in _MIME_TO_UTI]
    if lazy_types:
        item.setDataProvider_forTypes_(provider, lazy_types)

    # Метка происхождения кладётся сразу (маленькая, без сети): второй пояс
    # подавления петли. Значение никем не читается - важно наличие типа.
    marker = f"{offer.origin_id}:{offer.seq}".encode("ascii")
    item.setData_forType_(_to_nsdata(marker), ORIGIN_UTI)

    if not pasteboard.writeObjects_([item]):
        raise PasteboardPublishError("NSPasteboard.writeObjects вернул false")

    _live_provider = provider  # удержать до следующей публикации
    return int(pasteboard.changeCount())


__all__ = [
    "ORIGIN_UTI",
    "PasteboardPublishError",
    "change_count",
    "publish_with_origin",
]
