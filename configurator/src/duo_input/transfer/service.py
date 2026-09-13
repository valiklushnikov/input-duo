"""File-transfer protocol rules without transport or platform dependencies."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from ..clipboard.wire import CAPABILITY_FILES, MAX_FILE_CHUNK_BYTES, Message, MessageType
from .model import ENTRY_FILE, TransferManifest
from .paths import UnsafePath, sanitize_manifest
from .pipe import ChunkPipe, PipeClosed, PipeOverflow
from .scanner import scan
from .source import (
    REASON_SOURCE_CHANGED,
    REASON_SOURCE_MISSING,
    SnapshotRegistry,
    SourceChanged,
    SourceMissing,
)

logger = logging.getLogger(__name__)

REASON_BAD_REQUEST = "bad_request"
DROP_EFFECT_COPY = 1


class TransferState(StrEnum):
    IDLE = "idle"
    OFFERED = "offered"
    TRANSFERRING = "transferring"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    DISCONNECTED = "disconnected"
    FAILED = "failed"


_ACTIVE = frozenset({TransferState.TRANSFERRING})


def _index(header: dict, key: str) -> int:
    """Return a non-negative wire integer, excluding bool."""
    value = header.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an int")
    if value < 0:
        raise ValueError(f"{key} must not be negative")
    return value


class FileTransferService(QObject):
    """Qt-owned sender and receiver state; only ChunkPipe crosses threads."""

    offer_sent = Signal(str)
    send_failed = Signal(str)
    offer_received = Signal(object)
    transfer_started = Signal(object)
    transfer_progress = Signal("qlonglong", "qlonglong")
    transfer_completed = Signal()
    transfer_failed = Signal(str)
    transfer_cancelled = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._link = None
        self._peer_capabilities: frozenset[str] = frozenset()
        self._snapshots = SnapshotRegistry()
        self._state = TransferState.IDLE
        self._offered: TransferManifest | None = None
        self._session_id: str | None = None
        self._active_transfer_id: str | None = None
        self._active_manifest: TransferManifest | None = None
        self._pipes: dict[tuple[str, int], ChunkPipe] = {}
        #: (transfer_id, entry_index) -> (offset, effective length actually
        #: sent on the wire in FILE_READ). The length is kept alongside the
        #: offset so a reply can be checked against what we asked for, not
        #: just against where it claims to start.
        self._in_flight: dict[tuple[str, int], tuple[int, int]] = {}
        self._received_bytes = 0

    @property
    def snapshots(self) -> SnapshotRegistry:
        return self._snapshots

    @property
    def peer_supports_files(self) -> bool:
        return CAPABILITY_FILES in self._peer_capabilities

    def attach_link(self, link) -> None:
        self._reset_receiver(TransferState.IDLE)
        self._peer_capabilities = frozenset()
        self._snapshots.release_all()
        self._link = link
        link.disconnected.connect(
            lambda reason, attached_link=link: self._on_link_lost(attached_link, reason)
        )

    def detach_link(self) -> None:
        if self._link is None:
            return
        self._link = None
        self._peer_capabilities = frozenset()
        self._snapshots.release_all()
        self._reset_receiver(TransferState.DISCONNECTED)

    def set_peer_capabilities(self, capabilities: frozenset[str]) -> None:
        self._peer_capabilities = frozenset(capabilities)

    def _on_link_lost(self, link, reason: str) -> None:
        if link is not self._link:
            return
        logger.info("file transfer link lost (%s)", reason)
        self._link = None
        self._peer_capabilities = frozenset()
        self._snapshots.release_all()
        self._reset_receiver(TransferState.DISCONNECTED)

    def _reset_receiver(self, state: TransferState) -> None:
        self._close_all_pipes("link lost")
        self._offered = None
        self._active_manifest = None
        self._active_transfer_id = None
        self._session_id = None
        self._received_bytes = 0
        self._state = state

    def _send(self, message: Message) -> None:
        if self._link is not None:
            self._link.send(message)

    def offer_local_files(self, paths: Sequence[Path]) -> str | None:
        if self._link is None or not self.peer_supports_files or not paths:
            return None

        transfer_id = uuid.uuid4().hex
        try:
            manifest, sources = scan(paths, transfer_id, DROP_EFFECT_COPY)
        except UnsafePath as error:
            logger.warning("files were not offered: %s", error)
            self.send_failed.emit(str(error))
            return None
        except OSError as error:
            reason = error.strerror or "source unavailable"
            logger.warning("files were not offered: %s", reason)
            self.send_failed.emit(reason)
            return None

        self._snapshots.publish(manifest, sources)
        self._send(Message(MessageType.FILE_OFFER, manifest.to_dict(), b""))
        self.offer_sent.emit(transfer_id)
        return transfer_id

    def handle_message(self, message: Message) -> None:
        if message.type is MessageType.FILE_READ:
            self._answer_read(message)
        elif message.type is MessageType.TRANSFER_END:
            self._on_transfer_end(message)
        elif message.type is MessageType.FILE_OFFER:
            self._on_remote_offer(message)
        elif message.type is MessageType.FILE_CHUNK:
            self._on_chunk(message)
        elif message.type is MessageType.FILE_ERROR:
            self._on_file_error(message)

    def _answer_read(self, message: Message) -> None:
        header = message.header
        try:
            transfer_id = header["transfer_id"]
            if not isinstance(transfer_id, str):
                raise ValueError("transfer_id must be a string")
            entry_index = _index(header, "entry_index")
            offset = _index(header, "offset")
            length = _index(header, "length")
            if length == 0:
                raise ValueError("length must not be zero")
            if length > MAX_FILE_CHUNK_BYTES:
                raise ValueError("length exceeds the chunk ceiling")
        except (KeyError, ValueError) as error:
            logger.warning("read request refused: %s", error)
            self._send_error(header, REASON_BAD_REQUEST)
            return

        try:
            payload = self._snapshots.read(transfer_id, entry_index, offset, length)
        except SourceChanged as error:
            logger.warning("source changed: %s", error)
            self._send_error(header, REASON_SOURCE_CHANGED)
            return
        except (SourceMissing, OSError) as error:
            logger.warning("source unavailable: %s", error)
            self._send_error(header, REASON_SOURCE_MISSING)
            return

        self._send(
            Message(
                MessageType.FILE_CHUNK,
                {"transfer_id": transfer_id, "entry_index": entry_index, "offset": offset},
                payload,
            )
        )

    def _send_error(self, header: dict, reason: str) -> None:
        self._send(
            Message(
                MessageType.FILE_ERROR,
                {
                    "transfer_id": header.get("transfer_id", ""),
                    "entry_index": header.get("entry_index", -1),
                    "offset": header.get("offset", -1),
                    "reason": reason,
                },
                b"",
            )
        )

    def _on_transfer_end(self, message: Message) -> None:
        transfer_id = message.header.get("transfer_id")
        if isinstance(transfer_id, str):
            self._snapshots.release(transfer_id)

    @property
    def state(self) -> TransferState:
        return self._state

    @property
    def offered_manifest(self) -> TransferManifest | None:
        return self._offered

    # ------------------------------------------------------------------ получатель

    def _on_remote_offer(self, message: Message) -> None:
        try:
            manifest = sanitize_manifest(TransferManifest.from_dict(message.header))
        except (UnsafePath, ValueError):
            # Ни падения внутри слота Qt, ни частичного дерева: объявление
            # отвергается целиком, и это записано в журнал.
            logger.warning("file offer refused: invalid manifest")
            return
        self._offered = manifest
        if self._state not in _ACTIVE:
            self._state = TransferState.OFFERED
        self.offer_received.emit(manifest)

    def open_pipe(self, transfer_id: str, entry_index: int) -> ChunkPipe:
        """Проводник запросил содержимое записи - завести под неё очередь."""
        manifest = self._active_manifest if self._state in _ACTIVE else self._offered
        if manifest is None or manifest.transfer_id != transfer_id:
            raise ValueError(f"объявление {transfer_id!r} неизвестно")
        if (
            not isinstance(entry_index, int)
            or isinstance(entry_index, bool)
            or not 0 <= entry_index < len(manifest.entries)
        ):
            raise ValueError(f"записи {entry_index} нет в объявлении")
        if manifest.entries[entry_index].kind != ENTRY_FILE:
            raise ValueError("у каталога нет содержимого")

        key = (transfer_id, entry_index)
        self.close_pipe(transfer_id, entry_index, "pipe replaced")
        pipe = ChunkPipe(capacity_chunks=1)
        self._pipes[key] = pipe

        if self._state is not TransferState.TRANSFERRING:
            self._state = TransferState.TRANSFERRING
            self._session_id = uuid.uuid4().hex
            self._active_transfer_id = transfer_id
            self._active_manifest = manifest
            self._received_bytes = 0
            self._send(
                Message(
                    MessageType.TRANSFER_BEGIN,
                    {"transfer_id": transfer_id, "session_id": self._session_id},
                    b"",
                )
            )
            self.transfer_started.emit(manifest)
        return pipe

    @Slot(str, int, "qlonglong", int)
    def request_read(self, transfer_id: str, entry_index: int, offset: int, length: int) -> None:
        """Запросить байты. Вызывается из COM-потока через invokeMethod.

        Слот, а не обычный метод, именно поэтому: QMetaObject.invokeMethod с
        QueuedConnection - единственный способ, которым COM-поток вправе
        тронуть этот объект, и он требует слота.
        """
        if not isinstance(transfer_id, str):
            return
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in (entry_index, offset, length)
        ) or length == 0:
            return
        key = (transfer_id, entry_index)
        pipe = self._pipes.get(key)
        if pipe is None or pipe.finished or pipe.closed_reason is not None:
            return
        if key in self._in_flight:
            # Окно в один запрос. Второй запрос означал бы предвыборку, а с
            # ней Seek потребовал бы инвалидации устаревших чанков - пласт
            # состояния, который фаза 1 не заводит (спека §8).
            return
        effective_length = min(length, MAX_FILE_CHUNK_BYTES)
        self._in_flight[key] = (offset, effective_length)
        self._send(
            Message(
                MessageType.FILE_READ,
                {
                    "transfer_id": transfer_id,
                    "entry_index": entry_index,
                    "offset": offset,
                    "length": effective_length,
                },
                b"",
            )
        )

    def _on_chunk(self, message: Message) -> None:
        transfer_id = message.header.get("transfer_id")
        entry_index = message.header.get("entry_index")
        offset = message.header.get("offset")
        if (
            not isinstance(transfer_id, str)
            or not isinstance(entry_index, int)
            or isinstance(entry_index, bool)
            or not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
        ):
            return
        key = (transfer_id, entry_index)
        pipe = self._pipes.get(key)
        if pipe is None:
            return
        pending = self._in_flight.get(key)
        if pending is None or pending[0] != offset:
            # Чанк, которого мы не просили (или просили и передумали после
            # Seek). Отдать его Проводнику значило бы записать байты не туда.
            logger.debug("чанк на смещение %r отброшен как неожидаемый", offset)
            return
        _, requested_length = pending
        if len(message.blob) > requested_length:
            # Проверка отправителя на MAX_FILE_CHUNK_BYTES (см. _answer_read)
            # работает на ЕГО стороне провода и нам не гарантия. Слот
            # чтения остаётся занятым - не освобождён и не удалён - поэтому
            # настоящий ответ, таймаут потребителя или разрыв связи всё ещё
            # разрешат этот запрос; освобождать слот здесь означало бы
            # застрять навсегда, потому что верного ответа больше не ждут.
            logger.warning(
                "чанк на %d байт крупнее запрошенных %d - отброшен",
                len(message.blob),
                requested_length,
            )
            return
        del self._in_flight[key]

        if not message.blob:
            pipe.finish()
            return
        try:
            pipe.push(message.blob)
        except PipeOverflow:
            logger.warning("очередь переполнена - чанк отброшен")
            return
        except PipeClosed:
            return
        self._received_bytes += len(message.blob)
        total = self._active_manifest.total_bytes if self._active_manifest is not None else 0
        self.transfer_progress.emit(self._received_bytes, total)

    def _on_file_error(self, message: Message) -> None:
        if self._state not in _ACTIVE:
            return
        transfer_id = message.header.get("transfer_id")
        try:
            entry_index = _index(message.header, "entry_index")
            offset = _index(message.header, "offset")
        except ValueError:
            return
        if not isinstance(transfer_id, str):
            return
        reason = message.header.get("reason")
        if not isinstance(reason, str) or not reason:
            # entry_index/offset проходят через _index; reason - единственное
            # поле FILE_ERROR, которое не проверено так же, и чужой тип
            # (None/число/словарь) или пустая строка не должны заканчивать
            # сессию текстом "None"/"12"/"{}"/"" в интерфейсе Проводника.
            logger.debug("ошибка отброшена: reason не непустая строка")
            return
        key = (transfer_id, entry_index)
        pending = self._in_flight.get(key)
        if key not in self._pipes or pending is None or offset != pending[0]:
            return
        self._close_all_pipes(reason)
        self._state = TransferState.FAILED
        self._send_transfer_end("failed")
        self.transfer_failed.emit(reason)

    def close_pipe(self, transfer_id: str, entry_index: int, reason: str | None = None) -> None:
        """Проводник отпустил ЭТОТ поток. Сессию это само по себе не завершает."""
        key = (transfer_id, entry_index)
        pipe = self._pipes.pop(key, None)
        self._in_flight.pop(key, None)
        if pipe is not None:
            if reason is None:
                pipe.finish()
            else:
                pipe.close(reason)

    def finish_session(self, status: str) -> None:
        """EndOperation completes the session; releasing a stream does not.

        Repeated terminal calls have no effect.
        """
        if self._state not in _ACTIVE:
            return
        self._state = {
            "completed": TransferState.COMPLETED,
            "cancelled": TransferState.CANCELLED,
        }.get(status, TransferState.FAILED)
        self._close_all_pipes(status if status != "completed" else None)
        self._send_transfer_end(status)
        if status == "completed":
            self.transfer_completed.emit()
        elif status == "cancelled":
            self.transfer_cancelled.emit()
        else:
            self.transfer_failed.emit(status)

    def _send_transfer_end(self, status: str) -> None:
        if self._active_transfer_id is None:
            return
        transfer_id = self._active_transfer_id
        session_id = self._session_id or ""
        self._active_transfer_id = None
        self._session_id = None
        self._active_manifest = None
        self._send(
            Message(
                MessageType.TRANSFER_END,
                {
                    "transfer_id": transfer_id,
                    "session_id": session_id,
                    "status": status,
                },
                b"",
            )
        )

    def _close_all_pipes(self, reason: str | None) -> None:
        for pipe in self._pipes.values():
            if reason is None:
                pipe.finish()
            else:
                pipe.close(reason)
        self._pipes.clear()
        self._in_flight.clear()


__all__ = ["DROP_EFFECT_COPY", "REASON_BAD_REQUEST", "FileTransferService", "TransferState"]
