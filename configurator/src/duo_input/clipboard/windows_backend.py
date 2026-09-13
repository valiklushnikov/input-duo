"""Наблюдение за буфером обмена Windows и публикация в него.

Одна операция Ctrl+C порождает несколько событий: приложения выкладывают
форматы по очереди. Поэтому события собираются дебаунсом и берётся последнее
состояние, а не каждое промежуточное.

Буфер бывает заперт другим процессом, и Qt тогда отдаёт пустоту. Пустой снимок
означает не "пользователь скопировал ничего", а "прочитать не удалось", и
попытка повторяется.

Маркеры приватности Windows уважаются: их ставят менеджеры паролей, и на них же
смотрит собственная "История буфера обмена" системы. Содержимое, помеченное
так, не объявляется никогда.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Signal

from .backend import ORIGIN_MIME, ClipboardSnapshot, ContentFetcher, RemoteMimeData
from .formats import SYNCED_MIMES, collect_payloads, local_file_paths
from .offer import ClipboardOffer

logger = logging.getLogger(__name__)

#: Просьбы не запоминать это содержимое, в том виде, в каком их показывает Qt.
PRIVATE_MARKERS = (
    'application/x-qt-windows-mime;value="ExcludeClipboardContentFromMonitorProcessing"',
    'application/x-qt-windows-mime;value="CanIncludeInClipboardHistory"',
)

#: Сколько ждать, пока приложение доложит все форматы одной операции.
DEBOUNCE_MS = 200

#: Сколько раз перечитывать буфер, если он оказался заперт.
RETRY_LIMIT = 3


def is_private(formats: list[str]) -> bool:
    """Просило ли содержимое, чтобы его не запоминали и не пересылали."""
    if ORIGIN_MIME in formats:
        return True
    return any(marker in formats for marker in PRIVATE_MARKERS)


def snapshot_from(mime_data) -> ClipboardSnapshot:
    """Взять из буфера то, что мы умеем синхронизировать и передавать.

    Маркер приватности гасит и payload, и пути: менеджер паролей, положивший
    в буфер файл, не должен отправить его на вторую машину.
    """
    if is_private(list(mime_data.formats())):
        return ClipboardSnapshot({})
    return ClipboardSnapshot(collect_payloads(mime_data), local_file_paths(mime_data))


class WindowsClipboardBackend(QObject):
    """Единственная реализация границы платформы в milestone 1."""

    snapshot_taken = Signal(object)

    def __init__(self, clipboard, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._clipboard = clipboard
        self._running = False
        self._suspended = False
        self._attempts = 0
        self._published: RemoteMimeData | None = None
        self._local: ClipboardSnapshot = ClipboardSnapshot({})

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._take_snapshot)

    def start(self) -> None:
        if self._running:
            return
        self._clipboard.dataChanged.connect(self._on_data_changed)
        self._running = True

    def stop(self) -> None:
        if not self._running:
            return
        self._clipboard.dataChanged.disconnect(self._on_data_changed)
        self._debounce.stop()
        self._published = None
        self._local = ClipboardSnapshot({})
        self._running = False

    def publish(self, offer: ClipboardOffer, fetcher: ContentFetcher) -> None:
        """Объявить в локальном буфере то, что лежит на втором компьютере."""
        self._published = RemoteMimeData(offer, fetcher)
        self._suspended = True
        try:
            self._clipboard.setMimeData(self._published)
        finally:
            self._suspended = False

    def payload(self, mime: str) -> bytes | None:
        return self._local.payload(mime)

    # ------------------------------------------------------------------ внутреннее

    def _on_data_changed(self) -> None:
        if self._suspended:
            return
        self._attempts = 0
        self._debounce.start()

    def _take_snapshot(self) -> None:
        snapshot = snapshot_from(self._clipboard.mimeData())
        # is_empty, а не `not payloads`: копирование файлов в Проводнике не
        # даёт ни одного синхронизируемого формата, поэтому прежнее условие
        # трижды перечитывало буфер и замолкало - копирование файла не
        # порождало ни одного события вовсе.
        if snapshot.is_empty and self._attempts < RETRY_LIMIT:
            self._attempts += 1
            self._debounce.start()
            return
        if snapshot.is_empty:
            return
        self._local = snapshot
        self.snapshot_taken.emit(snapshot)


__all__ = [
    "DEBOUNCE_MS",
    "PRIVATE_MARKERS",
    "RETRY_LIMIT",
    "SYNCED_MIMES",
    "WindowsClipboardBackend",
    "is_private",
    "snapshot_from",
]
