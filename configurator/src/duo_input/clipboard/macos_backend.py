"""Наблюдение за буфером macOS опросом changeCount и публикация в него.

У Qt на macOS нет надёжного уведомления об изменении чужого буфера, поэтому
локальное копирование ловится опросом NSPasteboard.changeCount по таймеру -
так же, как это делают нативные менеджеры буфера. Публикация удалённого
содержимого идёт через нативный host-only ленивый provider, а не через
QClipboard: только так Universal Clipboard не материализует чужой буфер до
вставки.

Подавление собственной петли держится на changeCount: наш publish
инкрементирует счётчик, мы запоминаем это значение и не принимаем его за
локальное копирование. Метка происхождения - лишь второй пояс: корректность
не должна зависеть от того, переживёт ли кастомный тип round-trip Qt.

Модуль macos_pasteboard импортируется лениво (внутри __init__, а не на уровне
модуля): он тянет за собой AppKit/Foundation через pyobjc, а этот файл должен
оставаться импортируемым на любой платформе и в юнит-тестах без нативных
зависимостей - тесты подставляют собственный фейк через параметр pasteboard.

Детект приватного контента (org.nspasteboard.Concealed/TransientType) идёт
ЧЕРЕЗ pasteboard.is_concealed() в _poll, а не по QMimeData.formats(): Qt на
macOS эти UTI из formats() не отдаёт (проверено вручную - буфер с системным
паролем даёт formats() = ['text/plain']), а NSPasteboard.types() их честно
показывает. Проверка меток по строкам formats() здесь была бы мёртвым кодом.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from .backend import ORIGIN_MIME, ClipboardSnapshot, ContentFetcher
from .formats import collect_payloads, local_file_paths
from .offer import ClipboardOffer

#: Как часто опрашивать changeCount. 300 мс - хороший баланс отзывчивости и CPU.
POLL_MS = 300


def is_private(formats: list[str]) -> bool:
    """Наш ли это собственный round-trip (второй, best-effort пояс подавления петли).

    Детект приватности контента (org.nspasteboard.Concealed/TransientType)
    здесь НЕ живёт: Qt на macOS эти UTI из QMimeData.formats() не отдаёт
    (проверено вручную - буфер с системным паролем даёт formats() =
    ['text/plain']), так что проверка строк была бы мёртвым кодом,
    создающим ложное чувство защищённости. Настоящий гейт - нативный,
    через MacOSClipboardBackend._pasteboard.is_concealed() в _poll, потому
    что NSPasteboard.types() эти маркеры честно показывает.
    """
    return ORIGIN_MIME in formats


def snapshot_from(mime_data) -> ClipboardSnapshot:
    """Взять из буфера то, что мы умеем синхронизировать, и ничего сверх."""
    if is_private(list(mime_data.formats())):
        return ClipboardSnapshot({})
    return ClipboardSnapshot(collect_payloads(mime_data), local_file_paths(mime_data))


class MacOSClipboardBackend(QObject):
    """Граница платформы для macOS."""

    snapshot_taken = Signal(object)

    def __init__(self, clipboard, parent: QObject | None = None, pasteboard=None) -> None:
        super().__init__(parent)
        if pasteboard is None:
            from . import macos_pasteboard as pasteboard
        self._clipboard = clipboard
        self._pasteboard = pasteboard
        self._running = False
        self._last_seen_change_count: int | None = None
        self._own_change_count: int | None = None
        self._local: ClipboardSnapshot = ClipboardSnapshot({})

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)

    def start(self) -> None:
        if self._running:
            return
        self._last_seen_change_count = self._pasteboard.change_count()
        self._own_change_count = None
        self._timer.start()
        self._running = True

    def stop(self) -> None:
        if not self._running:
            return
        self._timer.stop()
        self._last_seen_change_count = None
        self._own_change_count = None
        self._local = ClipboardSnapshot({})
        self._running = False

    def publish(self, offer: ClipboardOffer, fetcher: ContentFetcher) -> None:
        """Объявить в локальном буфере то, что лежит на втором компьютере.

        Provider удерживается живым внутри macos_pasteboard; здесь нужен только
        новый changeCount, чтобы не принять собственную публикацию за локальное
        копирование.
        """
        count = self._pasteboard.publish_with_origin(offer, fetcher)
        self._own_change_count = count
        self._last_seen_change_count = count

    def payload(self, mime: str) -> bytes | None:
        return self._local.payload(mime)

    # ------------------------------------------------------------------ внутреннее

    def _poll(self) -> None:
        current = self._pasteboard.change_count()
        if current == self._last_seen_change_count:
            return
        if current == self._own_change_count:
            self._last_seen_change_count = current
            self._own_change_count = None
            return
        self._last_seen_change_count = current
        if self._pasteboard.is_concealed():
            # Qt на macOS не отдаёт org.nspasteboard.Concealed/TransientType
            # через QMimeData.formats(), поэтому единственный надёжный гейт -
            # нативный, до чтения mimeData() и снятия снапшота.
            return
        snapshot = snapshot_from(self._clipboard.mimeData())
        if snapshot.payloads:
            self._local = snapshot
            self.snapshot_taken.emit(snapshot)


__all__ = [
    "POLL_MS",
    "MacOSClipboardBackend",
    "is_private",
    "snapshot_from",
]
