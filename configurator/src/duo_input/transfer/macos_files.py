"""Приёмник Windows->Mac: offer -> авторизация -> скачивание в staging -> ⌘V.

Событийный, на потоке Qt. Без ChunkPipe, COM и STA: Finder не тянет байты —
тянем мы, последовательно, один FILE_READ в полёте (потолок памяти = один
чанк). pyobjc здесь не импортируется вовсе: вооружение буфера обмена живёт в
macos_pasteboard.py, а сюда попадает только через инъекцию ``pasteboard_arm``
в конструкторе — фабрика подставляет настоящий ``arm``, тесты — свой стаб.
"""

from __future__ import annotations

import itertools
import logging
from enum import Enum, auto

from PySide6.QtCore import QObject, Signal

from ..clipboard.wire import CAPABILITY_FILES, MAX_FILE_CHUNK_BYTES, Message, MessageType
from .model import ENTRY_FILE, TransferManifest
from .staging import StagingArea

logger = logging.getLogger(__name__)


class _State(Enum):
    IDLE = auto()
    AWAITING_AUTH = auto()
    DOWNLOADING = auto()
    READY = auto()


class MacFileReceiver(QObject):
    authorization_needed = Signal(object)
    transfer_started = Signal(object)
    transfer_progress = Signal("qlonglong", "qlonglong")
    transfer_completed = Signal()
    transfer_failed = Signal(str)
    transfer_cancelled = Signal()

    def __init__(self, staging: StagingArea, pasteboard_arm=None, parent=None) -> None:
        super().__init__(parent)
        self._staging = staging
        self._arm = pasteboard_arm
        self._link = None
        self._peer_caps: frozenset[str] = frozenset()
        self._state = _State.IDLE
        self._manifest: TransferManifest | None = None
        self._session = None
        self._file_indices: list[int] = []
        self._cursor = 0  # позиция в _file_indices
        self._offset = 0  # смещение внутри текущего файла
        self._received = 0
        self._read_ids = itertools.count(1)
        self._read_id: int | None = None

    # --- проводка
    def attach_link(self, link) -> None:
        self._link = link

    def set_peer_capabilities(self, caps) -> None:
        self._peer_caps = frozenset(caps)

    def stop(self) -> None:
        self._abort_session()
        self._state = _State.IDLE

    # --- offer/авторизация
    def handle_offer(self, manifest: TransferManifest) -> None:
        # READY-staging прошлого transfer не трогаем: на него может ссылаться буфер.
        self._manifest = manifest
        self._state = _State.AWAITING_AUTH
        self.authorization_needed.emit(manifest)

    def authorize(self, accepted: bool) -> None:
        if self._state is not _State.AWAITING_AUTH or self._manifest is None:
            return
        if not accepted:
            self._state = _State.IDLE
            self.transfer_cancelled.emit()
            return
        manifest = self._manifest
        if not self._staging.has_room_for(manifest.total_bytes):
            self._fail("no_disk_space")
            return
        self._session = self._staging.begin(manifest.transfer_id, manifest.entries)
        self._file_indices = [
            i for i, e in enumerate(manifest.entries) if e.kind == ENTRY_FILE and e.size > 0
        ]
        self._cursor = 0
        self._offset = 0
        self._received = 0
        self._state = _State.DOWNLOADING
        self.transfer_started.emit(manifest)
        self._send(
            Message(
                MessageType.TRANSFER_BEGIN,
                {"transfer_id": manifest.transfer_id, "session_id": manifest.transfer_id},
                b"",
            )
        )
        self._pump()

    # --- цикл
    def _pump(self) -> None:
        """Запросить следующий кусок либо, если файлов больше нет, завершить."""
        if self._cursor >= len(self._file_indices):
            self._complete()
            return
        entry_index = self._file_indices[self._cursor]
        entry = self._manifest.entries[entry_index]
        length = min(MAX_FILE_CHUNK_BYTES, entry.size - self._offset)
        self._read_id = next(self._read_ids)
        self._send(
            Message(
                MessageType.FILE_READ,
                {
                    "transfer_id": self._manifest.transfer_id,
                    "entry_index": entry_index,
                    "offset": self._offset,
                    "length": length,
                    "read_id": self._read_id,
                },
                b"",
            )
        )

    def handle_message(self, message: Message) -> None:
        if self._state is not _State.DOWNLOADING:
            return
        if message.type is MessageType.FILE_CHUNK:
            self._on_chunk(message)
        elif message.type is MessageType.FILE_ERROR:
            self._on_error(message)

    def _matches(self, message: Message) -> bool:
        if self._read_id is None:
            return False
        # Защита: чужой/повторный чанк после того, как курсор уже продвинулся
        # мимо конца списка файлов, не должен индексировать _file_indices.
        if self._cursor >= len(self._file_indices):
            return False
        h = message.header
        return (
            h.get("read_id") == self._read_id
            and h.get("transfer_id") == self._manifest.transfer_id
            and h.get("entry_index") == self._file_indices[self._cursor]
            and h.get("offset") == self._offset
        )

    def _on_chunk(self, message: Message) -> None:
        if not self._matches(message):
            return
        entry_index = self._file_indices[self._cursor]
        entry = self._manifest.entries[entry_index]
        expected = min(MAX_FILE_CHUNK_BYTES, entry.size - self._offset)
        blob = message.blob
        if len(blob) > expected:
            # Ответ крупнее запроса — нарушение протокола. Отбросить его молча
            # значило бы навсегда зависнуть в DOWNLOADING: read_id остался бы в
            # полёте, а второго ответа на него не будет. Отказываем, как и на
            # усечении ниже.
            self._fail("oversized_chunk")
            return
        if len(blob) < expected:
            # запрос не выходит за размер файла -> короткий ответ = усечённый источник
            self._fail("truncated")
            return
        self._session.write(entry_index, self._offset, blob)
        self._read_id = None
        self._offset += len(blob)
        self._received += len(blob)
        self.transfer_progress.emit(self._received, self._manifest.total_bytes)
        if self._offset >= entry.size:
            self._cursor += 1
            self._offset = 0
        self._pump()

    def _on_error(self, message: Message) -> None:
        if self._matches(message):
            reason = message.header.get("reason")
            self._fail(reason if isinstance(reason, str) else "file_error")

    def _complete(self) -> None:
        roots = self._session.finish()
        self._send(
            Message(
                MessageType.TRANSFER_END,
                {
                    "transfer_id": self._manifest.transfer_id,
                    "session_id": self._manifest.transfer_id,
                    "status": "completed",
                },
                b"",
            )
        )
        if self._arm is not None:
            try:
                self._arm(list(roots))
            except Exception as error:  # noqa: BLE001 — не рушить приложение
                logger.warning("буфер не принял файлы: %r", error)
                self._fail("pasteboard_refused")
                return
        self._state = _State.READY
        self._staging.gc(keep=self._manifest.transfer_id)
        self.transfer_completed.emit()

    # --- завершение
    def cancel(self) -> None:
        if self._state is _State.DOWNLOADING:
            self._send(
                Message(
                    MessageType.TRANSFER_END,
                    {
                        "transfer_id": self._manifest.transfer_id,
                        "session_id": self._manifest.transfer_id,
                        "status": "cancelled",
                    },
                    b"",
                )
            )
            self._abort_session()
            self._state = _State.IDLE
            self.transfer_cancelled.emit()

    def _fail(self, reason: str) -> None:
        if self._manifest is not None and self._link is not None:
            self._send(
                Message(
                    MessageType.TRANSFER_END,
                    {
                        "transfer_id": self._manifest.transfer_id,
                        "session_id": self._manifest.transfer_id,
                        "status": "failed",
                    },
                    b"",
                )
            )
        self._abort_session()
        self._state = _State.IDLE
        self.transfer_failed.emit(reason)

    def _abort_session(self) -> None:
        if self._session is not None:
            self._session.abort()
            self._session = None
        self._read_id = None

    def _send(self, message: Message) -> None:
        if self._link is not None and CAPABILITY_FILES in self._peer_caps:
            self._link.send(message)


__all__ = ["MacFileReceiver"]
