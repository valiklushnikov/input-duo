"""File-transfer protocol rules without transport or platform dependencies."""

from __future__ import annotations

import itertools
import logging
import re
import sys
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from PySide6.QtCore import QObject, QTimer, Signal

from ..clipboard.wire import CAPABILITY_FILES, MAX_FILE_CHUNK_BYTES, Message, MessageType
from .model import (
    ENTRY_FILE,
    MAX_MANIFEST_BYTES,
    MAX_WIRE_INTEGER,
    TransferManifest,
    decode_manifest,
    encode_manifest,
    require_transfer_id,
)
from .paths import MAX_ENTRIES, UnsafePath, sanitize_manifest
from .fileprovider_perf import PerfEmitter
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
#: Ответ короче обещанного размера: источник усечён под нами. Причина
#: локальная - она закрывает один поток, на провод не уходит.
REASON_TRUNCATED = "truncated"
#: Сессия замолчала: ни EndOperation, ни чтений, ни ответов.
REASON_SESSION_TIMEOUT = "session_timeout"
REASON_MANIFEST_TOO_LARGE = "manifest_too_large"
DROP_EFFECT_COPY = 1

#: Сколько сессия может молчать, прежде чем сторож её закончит.
#:
#: EndOperation - единственный явный сигнал конца (спека §9), и если он не
#: пришёл, а ни одного чтения в полёте нет, у IStream нет таймаута, который
#: бы сработал. Два таймаута чтения (READ_TIMEOUT_SECONDS = 30 с) с запасом:
#: Проводник вправе постоять на диалоге замены файла, и это не зависание.
SESSION_IDLE_TIMEOUT_MS = 120_000

#: Сколько имён пропущенных записей показать пользователю. Остальные - числом.
SKIPPED_NAMES_SHOWN = 5

_TERMINAL_STATUSES = frozenset({"completed", "cancelled", "failed"})

#: Причина FILE_ERROR уходит в интерфейс, поэтому это короткий идентификатор,
#: а не произвольный текст пира.
_REASON = re.compile(r"[a-z_]{1,64}")

_FILE_MESSAGES = frozenset(
    {
        MessageType.FILE_OFFER,
        MessageType.TRANSFER_BEGIN,
        MessageType.FILE_READ,
        MessageType.FILE_CHUNK,
        MessageType.FILE_ERROR,
        MessageType.TRANSFER_END,
    }
)


class TransferState(StrEnum):
    IDLE = "idle"
    OFFERED = "offered"
    TRANSFERRING = "transferring"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    DISCONNECTED = "disconnected"
    FAILED = "failed"


_ACTIVE = frozenset({TransferState.TRANSFERRING})


def _index(header: dict, key: str, maximum: int = MAX_WIRE_INTEGER) -> int:
    """Return a bounded non-negative wire integer, excluding bool."""
    value = header.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an int")
    if value < 0:
        raise ValueError(f"{key} must not be negative")
    if value > maximum:
        raise ValueError(f"{key} is out of range")
    return value


def _valid_int(value, maximum: int = MAX_WIRE_INTEGER) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= maximum
    )


@dataclass(frozen=True)
class _Read:
    """Один FILE_READ в полёте."""

    read_id: int
    offset: int
    length: int


@dataclass(eq=False)
class _Stream:
    """Один IStream Проводника. Опознаётся своей трубой, а не записью."""

    transfer_id: str
    entry_index: int
    size: int
    pipe: ChunkPipe
    read: _Read | None = None


class FileTransferService(QObject):
    """Qt-owned sender and receiver state; only ChunkPipe crosses threads.

    Поток Проводника опознаётся его ChunkPipe. Пара ``(transfer_id,
    entry_index)`` именует запись, а не открытие: два GetData по одной записи
    - это два потока, и ни один не вправе заменить, закрыть или завершить
    другой. Ответ на FILE_READ опознаётся по ``read_id``, который получатель
    выдаёт, а отправитель повторяет.
    """

    offer_sent = Signal(str)
    send_failed = Signal(str)
    offer_received = Signal(object)
    #: (сколько записей пропущено, первые SKIPPED_NAMES_SHOWN имён)
    entries_skipped = Signal(int, object)
    transfer_started = Signal(object)
    transfer_progress = Signal("qlonglong", "qlonglong")
    transfer_completed = Signal()
    transfer_failed = Signal(str)
    transfer_cancelled = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        idle_timeout_ms: int = SESSION_IDLE_TIMEOUT_MS,
        *,
        perf: PerfEmitter | None = None,
    ) -> None:
        super().__init__(parent)
        clock_domain = (
            "windows_python_monotonic" if sys.platform == "win32" else "python_monotonic"
        )
        self._perf = perf or PerfEmitter(logger, clock_domain)
        self._link = None
        self._link_lost_slot = None
        self._peer_capabilities: frozenset[str] = frozenset()
        self._snapshots = SnapshotRegistry(perf=self._perf)
        self._state = TransferState.IDLE
        self._offered: TransferManifest | None = None
        self._session_id: str | None = None
        self._active_transfer_id: str | None = None
        self._active_manifest: TransferManifest | None = None
        #: Открытые потоки по их трубе.
        self._streams: dict[ChunkPipe, _Stream] = {}
        #: Все трубы, открытые в текущей сессии, включая уже отпущенные: по
        #: ним EndOperation доказывает, что он про ЭТУ сессию.
        self._session_pipes: set[ChunkPipe] = set()
        #: read_id -> поток, чей FILE_READ в полёте.
        self._reads: dict[int, _Stream] = {}
        self._read_ids = itertools.count(1)
        self._received_bytes = 0
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(idle_timeout_ms)
        self._watchdog.timeout.connect(self._on_session_idle)

    @property
    def snapshots(self) -> SnapshotRegistry:
        return self._snapshots

    @property
    def session_watchdog(self) -> QTimer:
        return self._watchdog

    @property
    def peer_supports_files(self) -> bool:
        return CAPABILITY_FILES in self._peer_capabilities

    # ------------------------------------------------------------------ связь

    def attach_link(self, link) -> None:
        self._release_link_lost_slot()
        self._reset_receiver(TransferState.IDLE)
        self._peer_capabilities = frozenset()
        self._snapshots.release_all()
        self._link = link

        def on_disconnected(reason, attached_link=link):
            self._on_link_lost(attached_link, reason)

        link.disconnected.connect(on_disconnected)
        self._link_lost_slot = (link, on_disconnected)

    def detach_link(self) -> None:
        """Отсоединиться и разбудить каждый поток - даже без связи.

        Остановка подсистемы зовёт это ДО того, как ждать поток STA: Read,
        заблокированный на трубе, обязан проснуться раньше, чем начнётся
        ожидание, иначе остановка замерла бы на весь его таймаут.
        """
        self._release_link_lost_slot()
        self._link = None
        self._peer_capabilities = frozenset()
        self._snapshots.release_all()
        self._reset_receiver(TransferState.DISCONNECTED)

    def set_peer_capabilities(self, capabilities: frozenset[str]) -> None:
        self._peer_capabilities = frozenset(capabilities)

    def _release_link_lost_slot(self) -> None:
        """Отсоединить обработчик разрыва от прежней связи.

        Без этого связь держит замыкание, а замыкание - сервис: отсоединённый
        сервис не собирается, а каждое повторное присоединение добавляет ещё
        одну подписку на тот же сигнал.
        """
        slot, self._link_lost_slot = self._link_lost_slot, None
        if slot is None:
            return
        link, handler = slot
        try:
            link.disconnected.disconnect(handler)
        except (RuntimeError, TypeError):
            pass

    def _on_link_lost(self, link, reason: str) -> None:
        if link is not self._link:
            return
        logger.info("file transfer link lost (%s)", reason)
        self._release_link_lost_slot()
        self._link = None
        self._peer_capabilities = frozenset()
        self._snapshots.release_all()
        self._reset_receiver(TransferState.DISCONNECTED)

    def _reset_receiver(self, state: TransferState) -> None:
        self._close_all_pipes("link lost")
        self._watchdog.stop()
        self._offered = None
        self._active_manifest = None
        self._active_transfer_id = None
        self._session_id = None
        self._received_bytes = 0
        self._state = state

    def _send(self, message: Message) -> bool:
        """Отправить файловое сообщение - только пиру, который их понимает."""
        if self._link is None or not self.peer_supports_files:
            return False
        return self._link.send(message) is not False

    # ------------------------------------------------------------------ отправитель

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

        encoded = encode_manifest(manifest)
        if len(encoded) > MAX_MANIFEST_BYTES:
            # До публикации снимка: объявлять то, что не доедет, незачем.
            logger.warning(
                "files were not offered: manifest of %d bytes exceeds %d",
                len(encoded),
                MAX_MANIFEST_BYTES,
            )
            self.send_failed.emit(REASON_MANIFEST_TOO_LARGE)
            return None

        self._snapshots.publish(manifest, sources)
        self._send(Message(MessageType.FILE_OFFER, {}, encoded))
        self.offer_sent.emit(transfer_id)
        self._report_skipped(manifest)
        return transfer_id

    def handle_message(self, message: Message) -> None:
        if message.type in _FILE_MESSAGES and not self.peer_supports_files:
            # Пир не объявил файловую возможность, значит и прислать файловое
            # сообщение не мог. Ответить на него значило бы самим начать
            # файловый трафик с тем, кто его не понимает.
            logger.warning("file message %s refused: capability not negotiated", message.type.name)
            return
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
        received_ns = self._perf.now()
        header = message.header
        try:
            transfer_id = require_transfer_id(header.get("transfer_id"))
            entry_index = _index(header, "entry_index", MAX_ENTRIES - 1)
            offset = _index(header, "offset")
            length = _index(header, "length")
            read_id = _index(header, "read_id")
            if length == 0:
                raise ValueError("length must not be zero")
            if length > MAX_FILE_CHUNK_BYTES:
                raise ValueError("length exceeds the chunk ceiling")
            if read_id == 0:
                raise ValueError("read_id must be positive")
        except ValueError as error:
            logger.warning("read request refused: %s", error)
            self._send_error(header, REASON_BAD_REQUEST)
            return

        self._perf.emit_at(
            received_ns,
            "file_read_receive",
            transfer_id=transfer_id,
            entry_index=entry_index,
            read_id=read_id,
            offset=offset,
            length=length,
        )

        try:
            payload = self._snapshots.read(
                transfer_id, entry_index, offset, length, read_id=read_id
            )
        except SourceChanged as error:
            logger.warning("source changed: %s", error)
            self._send_error(header, REASON_SOURCE_CHANGED)
            return
        except (SourceMissing, OSError) as error:
            logger.warning("source unavailable: %s", type(error).__name__)
            self._send_error(header, REASON_SOURCE_MISSING)
            return
        except (ValueError, OverflowError) as error:
            logger.warning("read request refused: %s", type(error).__name__)
            self._send_error(header, REASON_BAD_REQUEST)
            return

        self._perf.emit(
            "file_chunk_send",
            transfer_id=transfer_id,
            entry_index=entry_index,
            read_id=read_id,
            offset=offset,
            bytes=len(payload),
        )
        self._send(
            Message(
                MessageType.FILE_CHUNK,
                {
                    "transfer_id": transfer_id,
                    "entry_index": entry_index,
                    "offset": offset,
                    "read_id": read_id,
                },
                payload,
            )
        )

    def _send_error(self, header: dict, reason: str) -> None:
        """FILE_ERROR, повторяющий только те поля запроса, что прошли проверку.

        Эхо сырого заголовка отправило бы пиру обратно всё, что он прислал, -
        включая строку в семьдесят килобайт, которая не поместится в кадр.
        """
        transfer_id = header.get("transfer_id")
        try:
            transfer_id = require_transfer_id(transfer_id)
        except ValueError:
            transfer_id = ""
        entry_index = header.get("entry_index")
        offset = header.get("offset")
        read_id = header.get("read_id")
        self._send(
            Message(
                MessageType.FILE_ERROR,
                {
                    "transfer_id": transfer_id,
                    "entry_index": entry_index if _valid_int(entry_index, MAX_ENTRIES - 1) else -1,
                    "offset": offset if _valid_int(offset) else -1,
                    "read_id": read_id if _valid_int(read_id) else -1,
                    "reason": reason,
                },
                b"",
            )
        )

    def _on_transfer_end(self, message: Message) -> None:
        """Сессия закончена, но не обязательно предложение.

        release() удалил бы весь снимок - манифест и всё - что противоречит
        сценарию 3 спеки (§1016-1018): повторный Ctrl+V - не новая передача,
        а новые чтения того же манифеста, и никакого отказа
        "дублирующийся transfer_id" быть не должно. close_descriptors()
        отпускает файловые дескрипторы (снимая блокировку на удаление,
        спека §15), не трогая сам снимок - следующее чтение того же
        transfer_id откроет его заново лениво и застанет либо тот же файл,
        либо SourceChanged, если он успел измениться.
        """
        transfer_id = message.header.get("transfer_id")
        if isinstance(transfer_id, str):
            self._snapshots.close_descriptors(transfer_id)

    @property
    def state(self) -> TransferState:
        return self._state

    @property
    def offered_manifest(self) -> TransferManifest | None:
        return self._offered

    def _report_skipped(self, manifest: TransferManifest) -> None:
        """Сказать, что дерево неполное: сколько записей и как их зовут.

        Только имена, не пути (спека §15), и не больше SKIPPED_NAMES_SHOWN:
        строка события - не список.
        """
        if not manifest.skipped:
            return
        names = tuple(
            PurePosixPath(skip.path).name for skip in manifest.skipped[:SKIPPED_NAMES_SHOWN]
        )
        logger.info("%d entries were skipped", len(manifest.skipped))
        self.entries_skipped.emit(len(manifest.skipped), names)

    # ------------------------------------------------------------------ получатель

    def _on_remote_offer(self, message: Message) -> None:
        try:
            manifest = sanitize_manifest(decode_manifest(message.blob))
        except (UnsafePath, ValueError):
            # Ни падения внутри слота Qt, ни частичного дерева: объявление
            # отвергается целиком, и это записано в журнал.
            logger.warning("file offer refused: invalid manifest")
            return
        self._offered = manifest
        if self._state not in _ACTIVE:
            self._state = TransferState.OFFERED
        self.offer_received.emit(manifest)
        self._report_skipped(manifest)

    def open_pipe(self, transfer_id: str, entry_index: int) -> ChunkPipe:
        """Проводник запросил содержимое записи - завести под неё поток."""
        manifest = self._active_manifest if self._state in _ACTIVE else self._offered
        if manifest is None or manifest.transfer_id != transfer_id:
            raise ValueError(f"объявление {transfer_id!r} неизвестно")
        if (
            not isinstance(entry_index, int)
            or isinstance(entry_index, bool)
            or not 0 <= entry_index < len(manifest.entries)
        ):
            raise ValueError(f"записи {entry_index} нет в объявлении")
        entry = manifest.entries[entry_index]
        if entry.kind != ENTRY_FILE:
            raise ValueError("у каталога нет содержимого")

        pipe = ChunkPipe(capacity_chunks=1)

        if self._state is not TransferState.TRANSFERRING:
            self._state = TransferState.TRANSFERRING
            self._session_id = uuid.uuid4().hex
            self._active_transfer_id = transfer_id
            self._active_manifest = manifest
            self._received_bytes = 0
            self._session_pipes = set()
            self._send(
                Message(
                    MessageType.TRANSFER_BEGIN,
                    {"transfer_id": transfer_id, "session_id": self._session_id},
                    b"",
                )
            )
            self.transfer_started.emit(manifest)
        self._streams[pipe] = _Stream(transfer_id, entry_index, entry.size, pipe)
        self._session_pipes.add(pipe)
        self._touch()
        return pipe

    def request_read(self, pipe, offset: int, length: int) -> None:
        """Запросить байты для потока. Зовётся на потоке Qt - через шлюз."""
        stream = self._stream_of(pipe)
        if stream is None:
            return
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in (offset, length)
        ) or length == 0:
            return
        if pipe.finished or pipe.closed_reason is not None:
            return
        if stream.read is not None:
            # Окно в один запрос на поток. Второй запрос означал бы
            # предвыборку, а с ней Seek потребовал бы инвалидации устаревших
            # чанков - пласт состояния, который фаза 1 не заводит (спека §8).
            return
        if offset >= stream.size:
            # За концом файла спрашивать нечего: IStream отвечает S_FALSE сам,
            # а отправитель счёл бы такое чтение ошибкой запроса.
            return
        effective_length = min(length, MAX_FILE_CHUNK_BYTES, stream.size - offset)
        read = _Read(next(self._read_ids), offset, effective_length)
        stream.read = read
        self._reads[read.read_id] = stream
        self._touch()
        self._send(
            Message(
                MessageType.FILE_READ,
                {
                    "transfer_id": stream.transfer_id,
                    "entry_index": stream.entry_index,
                    "offset": offset,
                    "length": effective_length,
                    "read_id": read.read_id,
                },
                b"",
            )
        )

    def _stream_of(self, pipe) -> _Stream | None:
        if not isinstance(pipe, ChunkPipe):
            return None
        return self._streams.get(pipe)

    def _pending_reply(self, message: Message) -> _Stream | None:
        """Поток, чей запрос в полёте совпадает с этим ответом полностью."""
        header = message.header
        read_id = header.get("read_id")
        if not _valid_int(read_id):
            return None
        stream = self._reads.get(read_id)
        if stream is None or stream.read is None:
            return None
        if (
            header.get("transfer_id") != stream.transfer_id
            or not _valid_int(header.get("entry_index"))
            or header.get("entry_index") != stream.entry_index
            or not _valid_int(header.get("offset"))
            or header.get("offset") != stream.read.offset
        ):
            return None
        return stream

    def _on_chunk(self, message: Message) -> None:
        stream = self._pending_reply(message)
        if stream is None:
            # Чанк, которого мы не просили (или просили и передумали после
            # Seek, или для отпущенного потока). Отдать его Проводнику значило
            # бы записать байты не туда.
            logger.debug("чанк без запроса в полёте отброшен")
            return
        read = stream.read
        if len(message.blob) > read.length:
            # Проверка отправителя на MAX_FILE_CHUNK_BYTES работает на ЕГО
            # стороне провода и нам не гарантия. Слот чтения остаётся занятым
            # - настоящий ответ, таймаут потребителя или разрыв связи всё ещё
            # разрешат этот запрос.
            logger.warning(
                "чанк на %d байт крупнее запрошенных %d - отброшен",
                len(message.blob),
                read.length,
            )
            return
        self._clear_read(stream)
        self._touch()

        if len(message.blob) < read.length:
            # Запрос никогда не выходит за размер файла, поэтому короткий или
            # пустой ответ - это усечённый источник, а не конец файла. Признать
            # его концом значило бы оставить у получателя короткий файл.
            logger.warning(
                "ответ на %d байт вместо %d - поток закрыт как усечённый",
                len(message.blob),
                read.length,
            )
            self._drop_stream(stream, REASON_TRUNCATED)
            return
        try:
            stream.pipe.push(message.blob)
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
        stream = self._pending_reply(message)
        if stream is None:
            return
        reason = message.header.get("reason")
        if not isinstance(reason, str) or _REASON.fullmatch(reason) is None:
            # reason уходит в интерфейс: чужой тип, пустая строка или абзац
            # текста сессию не завершают и строкой "None"/"12" не становятся.
            logger.debug("ошибка отброшена: reason не короткий идентификатор")
            return
        self._terminate("failed", reason)

    def close_pipe(self, pipe, reason: str | None = None) -> None:
        """Проводник отпустил ЭТОТ поток. Сессию это само по себе не завершает."""
        stream = self._stream_of(pipe)
        if stream is None:
            return
        self._drop_stream(stream, reason)
        self._touch()

    def finish_session(self, status: str, pipes: Iterable | None = None) -> None:
        """EndOperation completes the session; releasing a stream does not.

        ``pipes`` - трубы операции, о завершении которой пришёл EndOperation.
        Если ни одна из них не открывалась в текущей сессии, это запоздавшее
        завершение прежней операции, и новую оно не трогает. ``None`` - отмена
        пользователем: она про текущую сессию, какой бы она ни была.

        Repeated terminal calls have no effect.
        """
        if self._state not in _ACTIVE:
            return
        if pipes is not None and not any(
            isinstance(pipe, ChunkPipe) and pipe in self._session_pipes for pipe in pipes
        ):
            logger.info("завершение операции не относится к текущей сессии - пропущено")
            return
        if status not in _TERMINAL_STATUSES:
            status = "failed"
        self._terminate(status, status)

    def _on_session_idle(self) -> None:
        if self._state not in _ACTIVE:
            return
        logger.warning(
            "сессия молчала %d с - завершена сторожем", self._watchdog.interval() // 1000
        )
        self._terminate("failed", REASON_SESSION_TIMEOUT)

    def _touch(self) -> None:
        """Любая активность сессии перезапускает сторожа."""
        if self._state in _ACTIVE:
            self._watchdog.start()

    def _terminate(self, status: str, reason: str) -> None:
        self._state = {
            "completed": TransferState.COMPLETED,
            "cancelled": TransferState.CANCELLED,
        }.get(status, TransferState.FAILED)
        self._watchdog.stop()
        self._close_all_pipes(None if status == "completed" else reason)
        self._send_transfer_end(status)
        if status == "completed":
            self.transfer_completed.emit()
        elif status == "cancelled":
            self.transfer_cancelled.emit()
        else:
            self.transfer_failed.emit(reason)

    def _send_transfer_end(self, status: str) -> None:
        if self._active_transfer_id is None:
            return
        transfer_id = self._active_transfer_id
        session_id = self._session_id or ""
        self._active_transfer_id = None
        self._session_id = None
        self._active_manifest = None
        self._session_pipes = set()
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

    def _clear_read(self, stream: _Stream) -> None:
        if stream.read is not None:
            self._reads.pop(stream.read.read_id, None)
            stream.read = None

    def _drop_stream(self, stream: _Stream, reason: str | None) -> None:
        self._streams.pop(stream.pipe, None)
        self._clear_read(stream)
        if reason is None:
            stream.pipe.finish()
        else:
            stream.pipe.close(reason)

    def _close_all_pipes(self, reason: str | None) -> None:
        streams = list(self._streams.values())
        self._streams.clear()
        self._reads.clear()
        for stream in streams:
            stream.read = None
            if reason is None:
                stream.pipe.finish()
            else:
                stream.pipe.close(reason)


__all__ = [
    "DROP_EFFECT_COPY",
    "REASON_BAD_REQUEST",
    "REASON_MANIFEST_TOO_LARGE",
    "REASON_SESSION_TIMEOUT",
    "REASON_TRUNCATED",
    "SESSION_IDLE_TIMEOUT_MS",
    "FileTransferService",
    "TransferState",
]
