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

import logging

from PySide6.QtCore import QObject, QTimer, Signal

from .backend import ORIGIN_MIME, ClipboardSnapshot, ContentFetcher
from .formats import collect_payloads, local_file_paths
from .offer import ClipboardOffer

logger = logging.getLogger(__name__)

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
    #: The native workspace reports wake before a user can make the first
    #: post-sleep copy.  Runtime uses this edge to discard the half-dead TCP
    #: session instead of letting that copy disappear into it.
    resume_detected = Signal()

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
        self._wake_observer = None

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)

    def start(self) -> None:
        if self._running:
            return
        self._last_seen_change_count = self._pasteboard.change_count()
        self._own_change_count = None
        self._wake_observer = self._pasteboard.observe_wake(self._on_wake)
        self._timer.start()
        self._running = True

    def stop(self) -> None:
        if not self._running:
            return
        self._timer.stop()
        if self._wake_observer is not None:
            self._pasteboard.stop_observing_wake(self._wake_observer)
            self._wake_observer = None
        self._last_seen_change_count = None
        self._own_change_count = None
        self._local = ClipboardSnapshot({})
        self._running = False

    def publish(self, offer: ClipboardOffer, fetcher: ContentFetcher) -> bool:
        """Объявить в локальном буфере то, что лежит на втором компьютере.

        Provider удерживается живым внутри macos_pasteboard; здесь нужен только
        новый changeCount, чтобы не принять собственную публикацию за локальное
        копирование.
        """
        count = self._pasteboard.publish_with_origin(offer, fetcher)
        self._own_change_count = count
        self._last_seen_change_count = count
        return True

    def payload(self, mime: str) -> bytes | None:
        return self._local.payload(mime)

    # ------------------------------------------------------------------ внутреннее

    def _on_wake(self) -> None:
        # Re-arm the periodic observer as part of the same recovery edge.
        # QTimer normally resumes by itself, but explicitly restarting it
        # avoids depending on a timer deadline inherited from before sleep.
        if not self._running:
            return
        self._timer.stop()
        self._timer.start()
        self.resume_detected.emit()

    def _poll(self) -> None:
        current = self._pasteboard.change_count()
        if current == self._last_seen_change_count:
            return
        if current == self._own_change_count:
            self._last_seen_change_count = current
            self._own_change_count = None
            _log_skipped(current, "own_publish")
            return
        self._last_seen_change_count = current
        if self._pasteboard.is_concealed():
            # Qt на macOS не отдаёт org.nspasteboard.Concealed/TransientType
            # через QMimeData.formats(), поэтому единственный надёжный гейт -
            # нативный, до чтения mimeData() и снятия снапшота.
            _log_skipped(current, "concealed")
            return
        mime_data = self._clipboard.mimeData()
        formats = list(mime_data.formats())
        if is_private(formats):
            _log_skipped(current, "own_marker")
            return
        snapshot = snapshot_from(mime_data)
        if not snapshot.payloads:
            # Только файлы - их везёт передача файлов, а не буфер обмена.
            # Иначе - типы, а не содержимое: по ним видно, почему копия не ушла.
            if snapshot.file_paths:
                _log_skipped(current, "files_only")
            else:
                _log_skipped(current, "no_supported_formats", formats)
            return
        logger.info(
            "clipboard_local_change platform=macos change_count=%d mimes=%s bytes=%d",
            current,
            ",".join(snapshot.payloads),
            sum(len(payload) for payload in snapshot.payloads.values()),
        )
        self._local = snapshot
        self.snapshot_taken.emit(snapshot)


def _log_skipped(change_count: int, reason: str, formats: list[str] | None = None) -> None:
    if formats is None:
        logger.info(
            "clipboard_local_skipped platform=macos change_count=%d reason=%s",
            change_count,
            reason,
        )
        return
    logger.info(
        "clipboard_local_skipped platform=macos change_count=%d reason=%s formats=%s",
        change_count,
        reason,
        ",".join(formats) or "-",
    )


__all__ = [
    "POLL_MS",
    "MacOSClipboardBackend",
    "is_private",
    "snapshot_from",
]
