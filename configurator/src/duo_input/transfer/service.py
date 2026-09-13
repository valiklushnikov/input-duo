"""File-transfer protocol rules without transport or platform dependencies."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..clipboard.wire import CAPABILITY_FILES, MAX_FILE_CHUNK_BYTES, Message, MessageType
from .paths import UnsafePath
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


def _index(header: dict, key: str) -> int:
    """Return a non-negative wire integer, excluding bool."""
    value = header.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an int")
    if value < 0:
        raise ValueError(f"{key} must not be negative")
    return value


class FileTransferService(QObject):
    """The file sender; it emits chunks only as replies to FILE_READ."""

    offer_sent = Signal(str)
    send_failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._link = None
        self._peer_capabilities: frozenset[str] = frozenset()
        self._snapshots = SnapshotRegistry()

    @property
    def snapshots(self) -> SnapshotRegistry:
        return self._snapshots

    @property
    def peer_supports_files(self) -> bool:
        return CAPABILITY_FILES in self._peer_capabilities

    def attach_link(self, link) -> None:
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

    def set_peer_capabilities(self, capabilities: frozenset[str]) -> None:
        self._peer_capabilities = frozenset(capabilities)

    def _on_link_lost(self, link, reason: str) -> None:
        if link is not self._link:
            return
        logger.info("file transfer link lost (%s)", reason)
        self._link = None
        self._peer_capabilities = frozenset()
        self._snapshots.release_all()

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


__all__ = ["DROP_EFFECT_COPY", "REASON_BAD_REQUEST", "FileTransferService"]
