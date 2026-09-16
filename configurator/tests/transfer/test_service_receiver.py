# configurator/tests/transfer/test_service_receiver.py
"""Принимающая сторона: автомат, окно в один запрос, и мгновенная отмена.

Поток Проводника опознаётся по своему ChunkPipe, а не по паре
``(transfer_id, entry_index)``: два GetData на одну запись - это два потока,
и ни один не вправе закрыть, заменить или завершить другой. Ответ на чтение
опознаётся по ``read_id``, который получатель выдаёт и отправитель повторяет.
"""

from __future__ import annotations

import gc
import threading
import weakref

import pytest
from PySide6.QtCore import QObject, Signal

from duo_input.clipboard.wire import (
    CAPABILITY_CLIPBOARD,
    CAPABILITY_FILES,
    LEGACY_CAPABILITIES,
    MAX_FILE_CHUNK_BYTES,
    Message,
    MessageType,
)
from duo_input.transfer.model import (
    ENTRY_DIRECTORY,
    ENTRY_FILE,
    SkippedEntry,
    TransferEntry,
    TransferManifest,
    encode_manifest,
)
from duo_input.transfer.pipe import ChunkPipe, PipeClosed, PipeOverflow
from duo_input.transfer.service import (
    REASON_SESSION_TIMEOUT,
    REASON_TRUNCATED,
    FileTransferService,
    TransferState,
)


class _FakeLink(QObject):
    message_received = Signal(object)
    disconnected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[Message] = []

    def send(self, message: Message) -> bool:
        self.sent.append(message)
        return True

    def close(self) -> None: ...


def _offer(transfer_id="t-1", size=10):
    return TransferManifest(
        transfer_id=transfer_id,
        entries=(
            TransferEntry(path="Photos", kind=ENTRY_DIRECTORY, size=0, mtime_ns=1),
            TransferEntry(path="Photos/a.bin", kind=ENTRY_FILE, size=size, mtime_ns=2),
        ),
    )


@pytest.fixture
def receiver(qapp):
    service = FileTransferService()
    link = _FakeLink()
    service.attach_link(link)
    service.set_peer_capabilities(frozenset({CAPABILITY_CLIPBOARD, CAPABILITY_FILES}))
    return service, link


def _deliver_offer(service, manifest):
    service.handle_message(Message(MessageType.FILE_OFFER, {}, encode_manifest(manifest)))


def _sent(link, kind):
    return [message for message in link.sent if message.type is kind]


def _last_read(link):
    return _sent(link, MessageType.FILE_READ)[-1].header


def _reply(link, kind=MessageType.FILE_CHUNK, blob=b"abcd", **changes):
    """Ответ на последний отправленный FILE_READ - с его read_id."""
    read = _last_read(link)
    header = {
        "transfer_id": read["transfer_id"],
        "entry_index": read["entry_index"],
        "offset": read["offset"],
        "read_id": read["read_id"],
    }
    if kind is MessageType.FILE_ERROR:
        header["reason"] = "source_changed"
        blob = b""
    header.update(changes)
    return Message(kind, header, blob)


def test_a_fresh_service_is_idle(qapp):
    service = FileTransferService()

    assert service.state is TransferState.IDLE


def test_a_valid_offer_moves_to_offered_and_transfers_nothing(receiver):
    service, link = receiver
    received = []
    service.offer_received.connect(received.append)

    _deliver_offer(service, _offer())

    assert service.state is TransferState.OFFERED
    assert received[0].transfer_id == "t-1"
    assert _sent(link, MessageType.FILE_READ) == [], "объявление не должно ничего качать"


def test_an_offer_with_an_unsafe_path_is_refused_and_leaves_us_idle(receiver):
    service, _link = receiver
    manifest = TransferManifest(
        transfer_id="t-evil",
        entries=(TransferEntry(path="..\\evil.exe", kind=ENTRY_FILE, size=1, mtime_ns=1),),
    )

    _deliver_offer(service, manifest)

    assert service.state is TransferState.IDLE
    assert service.offered_manifest is None


@pytest.mark.parametrize(
    "blob",
    [b"", b"not json", b"[]", b'{"entries": "nope"}', b"\xff\xfe", b"[" * 100_000],
    ids=["empty", "text", "list", "entries", "not-utf8", "deep-nesting"],
)
def test_a_malformed_offer_is_refused_without_raising_inside_the_slot(receiver, blob):
    service, _link = receiver

    service.handle_message(Message(MessageType.FILE_OFFER, {}, blob))

    assert service.state is TransferState.IDLE


@pytest.mark.parametrize(
    "transfer_id",
    ["тест", "t 1", "", "t/1", "x" * 65, "té"],
)
def test_an_offer_whose_transfer_id_is_not_a_short_ascii_token_is_refused(receiver, transfer_id):
    service, _link = receiver
    received = []
    service.offer_received.connect(received.append)
    raw = _offer().to_dict()
    raw["transfer_id"] = transfer_id

    service.handle_message(
        Message(MessageType.FILE_OFFER, {}, encode_manifest_dict(raw))
    )

    assert received == []
    assert service.state is TransferState.IDLE


def encode_manifest_dict(raw: dict) -> bytes:
    import json

    return json.dumps(raw, ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize("field", ["size", "mtime_ns"])
def test_an_offer_with_an_integer_beyond_the_wire_range_is_refused(receiver, field):
    service, _link = receiver
    raw = _offer().to_dict()
    raw["entries"][1][field] = 10**30

    service.handle_message(Message(MessageType.FILE_OFFER, {}, encode_manifest_dict(raw)))

    assert service.state is TransferState.IDLE


def test_skipped_entries_of_a_remote_offer_are_reported_by_count_and_basename(receiver):
    service, _link = receiver
    reported = []
    service.entries_skipped.connect(lambda count, names: reported.append((count, names)))
    manifest = TransferManifest(
        transfer_id="t-1",
        entries=_offer().entries,
        skipped=(
            SkippedEntry(path="Photos/private/link", reason="reparse_point"),
            SkippedEntry(path="Photos/locked", reason="unreadable"),
        ),
    )

    _deliver_offer(service, manifest)

    assert reported == [(2, ("link", "locked"))]


def test_an_offer_without_skipped_entries_reports_nothing(receiver):
    service, _link = receiver
    reported = []
    service.entries_skipped.connect(lambda count, names: reported.append((count, names)))

    _deliver_offer(service, _offer())

    assert reported == []


def test_opening_a_pipe_moves_to_transferring_and_announces_the_session(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    started = []
    service.transfer_started.connect(started.append)

    service.open_pipe("t-1", 1)

    assert service.state is TransferState.TRANSFERRING
    assert started[0].transfer_id == "t-1"
    [begin] = _sent(link, MessageType.TRANSFER_BEGIN)
    assert begin.header["transfer_id"] == "t-1"


def test_the_pipe_open_pipe_returns_refuses_a_second_queued_chunk(receiver):
    """Пин литерала ChunkPipe(capacity_chunks=1) в open_pipe через поведение.

    test_pipe.py уже доказывает, что ChunkPipe сам отказывает сверх своей
    настроенной ёмкости - это механизм. Какую ёмкость выбирает СЕРВИС на
    месте вызова open_pipe, проверяет только этот тест: подняв
    capacity_chunks с 1 до 2 там, весь остальной набор проходил целиком
    (измерено при разборе фазы 1.13).
    """
    service, _link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)

    pipe.push(b"first")

    with pytest.raises(PipeOverflow):
        pipe.push(b"second")


def test_requesting_a_read_sends_exactly_one_file_read_with_a_read_id(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)

    service.request_read(pipe, 0, 4)

    [read] = _sent(link, MessageType.FILE_READ)
    read_id = read.header.pop("read_id")
    assert isinstance(read_id, int) and read_id > 0
    assert read.header == {
        "transfer_id": "t-1",
        "entry_index": 1,
        "offset": 0,
        "length": 4,
    }


def test_a_second_request_while_one_is_in_flight_is_refused(receiver):
    # Окно в один запрос на поток - это correctness baseline фазы 1. Второй
    # запрос означал бы предвыборку, а с ней Seek потребовал бы инвалидации.
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    service.request_read(pipe, 0, 4)

    service.request_read(pipe, 4, 4)

    assert len(_sent(link, MessageType.FILE_READ)) == 1


def test_a_chunk_lands_in_the_pipe_and_clears_the_in_flight_slot(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    service.request_read(pipe, 0, 4)

    service.handle_message(_reply(link))

    assert pipe.take(4) == b"abcd"
    service.request_read(pipe, 4, 4)
    assert len(_sent(link, MessageType.FILE_READ)) == 2


def test_a_read_never_asks_past_the_advertised_size(receiver):
    service, link = receiver
    _deliver_offer(service, _offer(size=10))
    pipe = service.open_pipe("t-1", 1)

    service.request_read(pipe, 10, 4)
    service.request_read(pipe, 7, 8)

    [read] = _sent(link, MessageType.FILE_READ)
    assert (read.header["offset"], read.header["length"]) == (7, 3)


@pytest.mark.parametrize(
    "changes",
    [{"offset": 999}, {"transfer_id": "other"}, {"entry_index": 0}, {"read_id": 10**6}],
)
def test_a_chunk_that_does_not_match_the_read_in_flight_is_dropped(receiver, changes):
    # Без этой проверки устаревший чанк после Seek попал бы в поток, и
    # Проводник записал бы байты не туда.
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    service.request_read(pipe, 0, 4)

    service.handle_message(_reply(link, **changes))

    assert pipe.take(10) == b""
    service.handle_message(_reply(link))
    assert pipe.take(10) == b"abcd"


def test_progress_counts_the_bytes_that_actually_arrived(receiver):
    service, link = receiver
    _deliver_offer(service, _offer(size=10))
    pipe = service.open_pipe("t-1", 1)
    seen: list[tuple[int, int]] = []
    service.transfer_progress.connect(lambda done, total: seen.append((done, total)))

    service.request_read(pipe, 0, 4)
    service.handle_message(_reply(link))

    assert seen == [(4, 10)]


def test_a_chunk_larger_than_the_requested_length_is_discarded(receiver):
    # Проверка отправителя работает на ЕГО стороне провода и получателю не
    # гарантия: чужой или неисправный отправитель мог бы прислать больше, чем
    # мы попросили.
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    progress: list[tuple[int, int]] = []
    service.transfer_progress.connect(lambda done, total: progress.append((done, total)))
    failures: list[str] = []
    service.transfer_failed.connect(failures.append)
    service.request_read(pipe, 0, 4)

    service.handle_message(_reply(link, blob=b"abcde"))

    assert service.state is TransferState.TRANSFERRING
    assert pipe.take(10) == b"", "ответ крупнее запроса дошёл до очереди"
    assert progress == []
    assert failures == []
    # Слот чтения не освобождён: второй request_read всё ещё отклоняется...
    service.request_read(pipe, 0, 4)
    assert len(_sent(link, MessageType.FILE_READ)) == 1
    # ...а настоящий ответ на тот же запрос по-прежнему его разрешает.
    service.handle_message(_reply(link))
    assert pipe.take(4) == b"abcd"
    assert progress == [(4, 10)], "отброшенный ответ должен был не прибавить ни байта"


def test_a_chunk_past_the_max_chunk_ceiling_is_discarded_and_does_not_inflate_progress(receiver):
    service, link = receiver
    _deliver_offer(service, _offer(size=MAX_FILE_CHUNK_BYTES * 2))
    pipe = service.open_pipe("t-1", 1)
    progress: list[tuple[int, int]] = []
    service.transfer_progress.connect(lambda done, total: progress.append((done, total)))
    service.request_read(pipe, 0, MAX_FILE_CHUNK_BYTES + 1000)
    [read] = _sent(link, MessageType.FILE_READ)
    assert read.header["length"] == MAX_FILE_CHUNK_BYTES

    service.handle_message(_reply(link, blob=bytes(MAX_FILE_CHUNK_BYTES + 1)))

    assert service.state is TransferState.TRANSFERRING
    assert pipe.take(MAX_FILE_CHUNK_BYTES + 1) == b""
    assert progress == []
    service.request_read(pipe, 0, MAX_FILE_CHUNK_BYTES + 1000)
    assert len(_sent(link, MessageType.FILE_READ)) == 1
    service.handle_message(_reply(link, blob=bytes(MAX_FILE_CHUNK_BYTES)))
    assert len(pipe.take(MAX_FILE_CHUNK_BYTES)) == MAX_FILE_CHUNK_BYTES
    assert progress == [(MAX_FILE_CHUNK_BYTES, MAX_FILE_CHUNK_BYTES * 2)]


def test_a_tail_chunk_exactly_reaching_the_end_of_file_is_accepted(receiver):
    service, link = receiver
    _deliver_offer(service, _offer(size=10))
    pipe = service.open_pipe("t-1", 1)
    progress: list[tuple[int, int]] = []
    service.transfer_progress.connect(lambda done, total: progress.append((done, total)))
    service.request_read(pipe, 7, 8)

    service.handle_message(_reply(link, blob=b"xyz"))

    assert pipe.take(10) == b"xyz"
    assert progress == [(3, 10)]


@pytest.mark.parametrize("blob", [b"", b"ab"])
def test_a_chunk_shorter_than_the_advertised_file_closes_that_stream_as_truncated(
    receiver, blob
):
    # Пустой или короткий ответ посреди файла - это усечение источника, а не
    # конец файла: размер уже обещан Проводнику в FILEDESCRIPTOR. Признать его
    # концом значило бы оставить у получателя короткий файл без ошибки.
    service, link = receiver
    _deliver_offer(service, _offer(size=10))
    pipe = service.open_pipe("t-1", 1)
    progress: list[tuple[int, int]] = []
    service.transfer_progress.connect(lambda done, total: progress.append((done, total)))
    service.request_read(pipe, 0, 4)

    service.handle_message(_reply(link, blob=blob))

    assert pipe.finished is False
    assert pipe.closed_reason == REASON_TRUNCATED
    assert progress == []
    service.request_read(pipe, 0, 4)
    assert len(_sent(link, MessageType.FILE_READ)) == 1, "усечённый поток спросил снова"


def test_a_file_error_fails_the_session_and_closes_the_pipe(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    service.request_read(pipe, 0, 4)
    failures: list[str] = []
    service.transfer_failed.connect(failures.append)

    service.handle_message(_reply(link, MessageType.FILE_ERROR))

    assert service.state is TransferState.FAILED
    assert failures == ["source_changed"]
    with pytest.raises(PipeClosed):
        pipe.take(4)


@pytest.mark.parametrize("reason", [None, 12, {}, [], "", "x" * 65, "два слова"])
def test_a_file_error_with_a_malformed_reason_does_not_end_the_session(receiver, reason):
    # reason уходит в интерфейс, поэтому это короткий ASCII-идентификатор или
    # ничего: чужой тип, пустая строка или абзац текста сессию не завершают.
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    service.request_read(pipe, 0, 4)
    failures: list[str] = []
    service.transfer_failed.connect(failures.append)

    service.handle_message(_reply(link, MessageType.FILE_ERROR, reason=reason))

    assert service.state is TransferState.TRANSFERRING
    assert failures == []
    assert _sent(link, MessageType.TRANSFER_END) == []
    assert pipe.closed_reason is None
    service.request_read(pipe, 0, 4)
    assert len(_sent(link, MessageType.FILE_READ)) == 1
    service.handle_message(_reply(link))
    assert pipe.take(4) == b"abcd"


def test_losing_the_link_moves_to_disconnected_and_wakes_every_pipe(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)

    link.disconnected.emit("соединение закрыто")

    assert service.state is TransferState.DISCONNECTED
    with pytest.raises(PipeClosed):
        pipe.take(4)


def test_cancelling_closes_the_pipe_and_reports_the_status_to_the_sender(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)

    service.finish_session("cancelled")

    assert service.state is TransferState.CANCELLED
    with pytest.raises(PipeClosed):
        pipe.take(4)
    [end] = _sent(link, MessageType.TRANSFER_END)
    assert end.header["status"] == "cancelled"


def test_completing_reports_completed_exactly_once(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    service.open_pipe("t-1", 1)
    completions: list[int] = []
    service.transfer_completed.connect(lambda: completions.append(1))

    service.finish_session("completed")
    service.finish_session("completed")

    assert completions == [1], "второй вызов завершил сессию повторно"
    assert len(_sent(link, MessageType.TRANSFER_END)) == 1


def test_an_unknown_terminal_status_is_reported_as_failed(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    service.open_pipe("t-1", 1)

    service.finish_session("weird")

    assert service.state is TransferState.FAILED
    assert _sent(link, MessageType.TRANSFER_END)[0].header["status"] == "failed"


def test_a_newer_offer_replaces_an_idle_one(receiver):
    service, _link = receiver
    _deliver_offer(service, _offer(transfer_id="first"))

    _deliver_offer(service, _offer(transfer_id="second"))

    assert service.offered_manifest.transfer_id == "second"


def test_a_newer_offer_does_not_interrupt_an_active_transfer(receiver):
    # Сценарий 1 спецификации, на стороне получателя.
    service, link = receiver
    _deliver_offer(service, _offer(transfer_id="first"))
    pipe = service.open_pipe("first", 1)

    _deliver_offer(service, _offer(transfer_id="second"))

    assert service.state is TransferState.TRANSFERRING
    service.request_read(pipe, 0, 4)
    service.handle_message(_reply(link))
    assert pipe.take(4) == b"abcd", "новое объявление оборвало идущую передачу"


def test_opening_a_pipe_for_a_directory_entry_is_refused(receiver):
    service, _link = receiver
    _deliver_offer(service, _offer())

    with pytest.raises(ValueError):
        service.open_pipe("t-1", 0)


def test_opening_a_pipe_for_an_unknown_transfer_is_refused(receiver):
    service, _link = receiver
    _deliver_offer(service, _offer())

    with pytest.raises(ValueError):
        service.open_pipe("no-such", 1)


@pytest.mark.parametrize("kind", [MessageType.FILE_ERROR, MessageType.FILE_CHUNK])
@pytest.mark.parametrize("changes", [
    {"transfer_id": "other"}, {"entry_index": 0}, {"offset": 9},
    {"entry_index": True}, {"offset": False}, {"offset": None},
    {"read_id": None}, {"read_id": True}, {"read_id": -1}, {"read_id": 10**30},
    {"offset": 10**30},
])
def test_unrelated_or_malformed_replies_preserve_the_active_read(receiver, kind, changes):
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    service.request_read(pipe, 0, 4)
    failures = []
    service.transfer_failed.connect(failures.append)

    service.handle_message(_reply(link, kind, **changes))

    assert service.state is TransferState.TRANSFERRING
    assert pipe.take(4) == b""
    assert pipe.closed_reason is None
    assert failures == []
    assert _sent(link, MessageType.TRANSFER_END) == []
    service.handle_message(_reply(link))
    assert pipe.take(4) == b"abcd"


def test_active_manifest_controls_progress_and_additional_pipes(receiver):
    service, link = receiver
    first = TransferManifest("t-1", (
        TransferEntry("a", ENTRY_FILE, 10, 1),
        TransferEntry("b", ENTRY_FILE, 20, 1),
    ))
    _deliver_offer(service, first)
    pipe = service.open_pipe("t-1", 1)
    progress = []
    service.transfer_progress.connect(lambda done, total: progress.append((done, total)))
    _deliver_offer(service, _offer("next", 100))
    service.request_read(pipe, 0, 4)
    service.handle_message(_reply(link))

    assert pipe.take(4) == b"abcd"
    assert progress == [(4, 30)]
    service.open_pipe("t-1", 0)
    with pytest.raises(ValueError):
        service.open_pipe("next", 1)
    assert len(_sent(link, MessageType.TRANSFER_BEGIN)) == 1
    service.finish_session("completed")
    service.open_pipe("next", 1)
    assert _sent(link, MessageType.TRANSFER_BEGIN)[-1].header["transfer_id"] == "next"


def _start_waiter(pipe):
    entered = threading.Event()
    results = []

    def consume():
        entered.set()
        try:
            results.append(pipe.wait(2))
        except PipeClosed as error:
            results.append(error)

    worker = threading.Thread(target=consume)
    worker.start()
    assert entered.wait(1)
    return worker, results


def test_direct_reattach_wakes_old_pipe_and_starts_a_clean_receiver(receiver):
    service, old_link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    service.request_read(pipe, 0, 4)
    worker, results = _start_waiter(pipe)
    new_link = _FakeLink()

    service.attach_link(new_link)

    worker.join(0.5)
    assert not worker.is_alive(), "old pipe remained blocked across peers"
    assert isinstance(results[0], PipeClosed)
    assert service.state is TransferState.IDLE
    assert service.offered_manifest is None
    assert not service.peer_supports_files
    service.finish_session("completed")
    service.request_read(pipe, 0, 4)
    assert new_link.sent == []
    with pytest.raises(ValueError):
        service.open_pipe("t-1", 1)
    old_link.disconnected.emit("late disconnect")
    assert service.state is TransferState.IDLE
    service.set_peer_capabilities(frozenset({CAPABILITY_FILES}))
    _deliver_offer(service, _offer())
    fresh = service.open_pipe("t-1", 1)
    service.request_read(fresh, 0, 4)
    service.handle_message(_reply(new_link))
    assert fresh.take(4) == b"abcd"


def test_two_streams_of_the_same_entry_are_independent(receiver):
    # Второй GetData по тому же lindex - это второй поток, а не замена
    # первого: Проводник держит оба указателя, и оба обязаны читать.
    service, link = receiver
    _deliver_offer(service, _offer())
    first = service.open_pipe("t-1", 1)
    second = service.open_pipe("t-1", 1)

    service.request_read(first, 0, 4)
    first_read = _last_read(link)
    service.request_read(second, 0, 4)
    second_read = _last_read(link)

    assert first_read["read_id"] != second_read["read_id"]
    service.handle_message(_reply(link, blob=b"2222"))
    assert second.take(4) == b"2222"
    assert first.take(4) == b""
    service.handle_message(
        Message(MessageType.FILE_CHUNK, {**{k: first_read[k] for k in (
            "transfer_id", "entry_index", "offset", "read_id")}}, b"1111")
    )
    assert first.take(4) == b"1111"
    assert first.closed_reason is None and second.closed_reason is None
    assert len(_sent(link, MessageType.TRANSFER_BEGIN)) == 1


def test_releasing_one_stream_does_not_touch_its_sibling(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    first = service.open_pipe("t-1", 1)
    second = service.open_pipe("t-1", 1)
    service.request_read(second, 0, 4)
    worker, results = _start_waiter(first)

    service.close_pipe(first)

    worker.join(0.5)
    assert not worker.is_alive()
    assert results == [True]
    assert first.finished
    assert not second.finished and second.closed_reason is None
    service.handle_message(_reply(link))
    assert second.take(4) == b"abcd"


def test_a_released_stream_answer_does_not_reach_a_newer_stream(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    old = service.open_pipe("t-1", 1)
    service.request_read(old, 0, 4)
    stale = _reply(link, blob=b"OLD!")
    service.close_pipe(old)
    fresh = service.open_pipe("t-1", 1)
    service.request_read(fresh, 0, 4)

    service.handle_message(stale)

    assert fresh.take(4) == b""
    service.handle_message(_reply(link, blob=b"NEW!"))
    assert fresh.take(4) == b"NEW!"


def test_a_late_completion_from_an_earlier_operation_does_not_finish_a_new_one(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    old = service.open_pipe("t-1", 1)
    service.finish_session("completed", (old,))
    fresh = service.open_pipe("t-1", 1)
    events = []
    service.transfer_completed.connect(lambda: events.append("completed"))
    service.transfer_cancelled.connect(lambda: events.append("cancelled"))

    service.finish_session("cancelled", (old,))

    assert service.state is TransferState.TRANSFERRING
    assert events == []
    assert fresh.closed_reason is None
    assert len(_sent(link, MessageType.TRANSFER_END)) == 1
    service.finish_session("completed", (fresh,))
    assert events == ["completed"]


def test_a_completion_naming_no_stream_of_the_session_is_ignored(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)

    service.finish_session("completed", ())
    service.finish_session("completed", (ChunkPipe(),))

    assert service.state is TransferState.TRANSFERRING
    assert pipe.closed_reason is None and not pipe.finished
    assert _sent(link, MessageType.TRANSFER_END) == []


@pytest.mark.parametrize("args", [
    (-1, 4), (0, 0), (0, -1), (False, 4), (0, True), (0.5, 4), (0, "4"),
    (10**30, 4),
])
def test_invalid_read_never_goes_in_flight(receiver, args):
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)

    service.request_read(pipe, *args)

    assert _sent(link, MessageType.FILE_READ) == []
    service.request_read(pipe, 0, 4)
    service.handle_message(_reply(link))
    assert pipe.take(4) == b"abcd"


@pytest.mark.parametrize("stranger", [None, [], "t-1", ChunkPipe()])
def test_a_read_for_a_stream_the_service_did_not_open_is_ignored(receiver, stranger):
    service, link = receiver
    _deliver_offer(service, _offer())
    service.open_pipe("t-1", 1)

    service.request_read(stranger, 0, 4)
    service.close_pipe(stranger)

    assert _sent(link, MessageType.FILE_READ) == []


@pytest.mark.parametrize("terminal", ["completed", "cancelled", "failed", "disconnect", "detach"])
def test_terminal_paths_wake_all_pipes_and_notify_at_most_once(receiver, terminal):
    service, link = receiver
    _deliver_offer(service, TransferManifest("t-1", (
        TransferEntry("a", ENTRY_FILE, 10, 1),
        TransferEntry("b", ENTRY_FILE, 10, 1),
    )))
    pipes = [service.open_pipe("t-1", index) for index in (0, 1)]
    for pipe in pipes:
        service.request_read(pipe, 0, 4)
    error = _reply(link, MessageType.FILE_ERROR)
    waiters = [_start_waiter(pipe) for pipe in pipes]
    events = []
    service.transfer_completed.connect(lambda: events.append("completed"))
    service.transfer_cancelled.connect(lambda: events.append("cancelled"))
    service.transfer_failed.connect(lambda reason: events.append("failed"))

    for _ in range(2):
        if terminal == "disconnect":
            link.disconnected.emit("gone")
        elif terminal == "detach":
            service.detach_link()
        elif terminal == "failed":
            service.handle_message(error)
        else:
            service.finish_session(terminal)
    service.finish_session("completed")
    service.handle_message(error)

    for worker, results in waiters:
        worker.join(0.5)
        assert not worker.is_alive()
        assert results == [True] if terminal == "completed" else isinstance(results[0], PipeClosed)
    expected = "disconnected" if terminal in {"disconnect", "detach"} else terminal
    assert service.state.value == expected
    expected_events = [] if expected == "disconnected" else [expected]
    assert events == expected_events
    ends = _sent(link, MessageType.TRANSFER_END)
    assert len(ends) == len(expected_events)
    if ends:
        assert ends[0].header["status"] == terminal
        assert ends[0].header["session_id"] == _sent(link, MessageType.TRANSFER_BEGIN)[0].header["session_id"]


def test_releasing_a_stream_wakes_it_without_completing_the_session(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    worker, results = _start_waiter(pipe)

    service.close_pipe(pipe)

    worker.join(0.5)
    assert not worker.is_alive()
    assert results == [True]
    assert service.state is TransferState.TRANSFERRING
    assert _sent(link, MessageType.TRANSFER_END) == []
    service.finish_session("completed")
    assert len(_sent(link, MessageType.TRANSFER_END)) == 1


@pytest.mark.parametrize("status", ["completed", "cancelled", "failed"])
def test_reentrant_terminal_delivery_cannot_finish_twice(receiver, status):
    service, link = receiver
    _deliver_offer(service, _offer())
    service.open_pipe("t-1", 1)
    events = []
    service.transfer_completed.connect(lambda: events.append("completed"))
    service.transfer_cancelled.connect(lambda: events.append("cancelled"))
    service.transfer_failed.connect(lambda reason: events.append("failed"))
    original_send = link.send
    reentered = False

    def send(message):
        nonlocal reentered
        original_send(message)
        if message.type is MessageType.TRANSFER_END and not reentered:
            reentered = True
            service.finish_session(status)
        return True

    link.send = send

    service.finish_session(status)

    assert events == [status]
    assert len(_sent(link, MessageType.TRANSFER_END)) == 1


@pytest.mark.parametrize("disconnect", [True, False])
def test_a_disconnected_receiver_cannot_reopen_the_previous_offer(receiver, disconnect):
    service, link = receiver
    _deliver_offer(service, _offer())
    service.open_pipe("t-1", 1)
    if disconnect:
        link.disconnected.emit("gone")
    else:
        service.detach_link()

    assert service.offered_manifest is None
    with pytest.raises(ValueError):
        service.open_pipe("t-1", 1)
    service.finish_session("completed")
    assert _sent(link, MessageType.TRANSFER_END) == []


def test_an_unsolicited_error_does_not_abort_an_open_pipe(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)

    service.handle_message(
        Message(
            MessageType.FILE_ERROR,
            {"transfer_id": "t-1", "entry_index": 1, "offset": 0, "read_id": 1,
             "reason": "source_changed"},
            b"",
        )
    )

    assert service.state is TransferState.TRANSFERRING
    assert pipe.take(4) == b""
    assert _sent(link, MessageType.TRANSFER_END) == []
    service.request_read(pipe, 0, 4)
    service.handle_message(_reply(link))
    assert pipe.take(4) == b"abcd"


def test_progress_preserves_a_total_above_the_qt_int_range(receiver):
    service, link = receiver
    _deliver_offer(service, _offer(size=8_800_000_000))
    pipe = service.open_pipe("t-1", 1)
    seen = []
    service.transfer_progress.connect(lambda done, total: seen.append((done, total)))
    service.request_read(pipe, 4_400_000_000, 4)

    service.handle_message(_reply(link))

    assert _sent(link, MessageType.FILE_READ)[0].header["offset"] == 4_400_000_000
    assert seen == [(4, 8_800_000_000)]


# --------------------------------------------------------------- сторож сессии


def test_the_session_watchdog_is_armed_only_while_a_session_is_active(receiver):
    service, _link = receiver
    _deliver_offer(service, _offer())
    assert not service.session_watchdog.isActive()

    service.open_pipe("t-1", 1)
    assert service.session_watchdog.isActive()

    service.finish_session("completed")
    assert not service.session_watchdog.isActive()


def test_the_watchdog_is_restarted_by_every_kind_of_session_activity(receiver, qtbot):
    service = FileTransferService(idle_timeout_ms=400)
    link = _FakeLink()
    service.attach_link(link)
    service.set_peer_capabilities(frozenset({CAPABILITY_FILES}))
    _deliver_offer(service, _offer(size=100))
    pipe = service.open_pipe("t-1", 1)
    other = service.open_pipe("t-1", 1)

    def restarted_by(action) -> bool:
        qtbot.wait(150)
        before = service.session_watchdog.remainingTime()
        action()
        return service.session_watchdog.remainingTime() > before

    assert restarted_by(lambda: service.open_pipe("t-1", 1))
    assert restarted_by(lambda: service.request_read(pipe, 0, 4))
    assert restarted_by(lambda: service.handle_message(_reply(link)))
    assert restarted_by(lambda: service.close_pipe(other))
    assert service.state is TransferState.TRANSFERRING


def test_an_idle_session_times_out_frees_its_streams_and_permits_a_new_offer(receiver):
    # EndOperation, который не пришёл, и ни одного чтения в полёте: без
    # сторожа сессия осталась бы TRANSFERRING навсегда, отправитель держал бы
    # дескрипторы, а новое объявление не могло бы начать передачу.
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    failures = []
    service.transfer_failed.connect(failures.append)

    service.session_watchdog.timeout.emit()

    assert service.state is TransferState.FAILED
    assert failures == [REASON_SESSION_TIMEOUT]
    assert isinstance(pipe.closed_reason, str)
    [end] = _sent(link, MessageType.TRANSFER_END)
    assert end.header["status"] == "failed"
    assert not service.session_watchdog.isActive()

    _deliver_offer(service, _offer("t-2"))
    assert service.state is TransferState.OFFERED
    service.open_pipe("t-2", 1)
    assert service.state is TransferState.TRANSFERRING
    assert _sent(link, MessageType.TRANSFER_BEGIN)[-1].header["transfer_id"] == "t-2"


def test_a_watchdog_that_fires_after_the_session_ended_does_nothing(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    service.open_pipe("t-1", 1)
    service.finish_session("completed")

    service.session_watchdog.timeout.emit()

    assert service.state is TransferState.COMPLETED
    assert len(_sent(link, MessageType.TRANSFER_END)) == 1


def test_the_watchdog_really_fires_on_the_qt_event_loop(qapp, qtbot):
    service = FileTransferService(idle_timeout_ms=50)
    link = _FakeLink()
    service.attach_link(link)
    service.set_peer_capabilities(frozenset({CAPABILITY_FILES}))
    _deliver_offer(service, _offer())
    service.open_pipe("t-1", 1)

    qtbot.waitUntil(lambda: service.state is TransferState.FAILED, timeout=2000)


# ------------------------------------------------------ согласованная возможность


def _legacy_receiver():
    service = FileTransferService()
    link = _FakeLink()
    service.attach_link(link)
    service.set_peer_capabilities(LEGACY_CAPABILITIES)
    return service, link


def test_a_peer_without_the_file_capability_cannot_inject_an_offer(qapp):
    service, link = _legacy_receiver()
    offers = []
    service.offer_received.connect(offers.append)

    _deliver_offer(service, _offer())

    assert offers == []
    assert service.state is TransferState.IDLE
    assert service.offered_manifest is None
    assert link.sent == []


@pytest.mark.parametrize(
    "kind",
    [MessageType.FILE_READ, MessageType.FILE_CHUNK, MessageType.FILE_ERROR,
     MessageType.TRANSFER_END, MessageType.TRANSFER_BEGIN],
)
def test_no_file_message_from_a_legacy_peer_produces_any_file_traffic(qapp, kind):
    service, link = _legacy_receiver()

    service.handle_message(
        Message(kind, {"transfer_id": "t-1", "entry_index": 1, "offset": 0, "length": 4,
                       "read_id": 1, "reason": "source_changed", "status": "completed"}, b"")
    )

    assert link.sent == []


def test_a_capability_withdrawn_mid_session_silences_every_outbound_file_message(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    sent_before = len(link.sent)

    service.set_peer_capabilities(LEGACY_CAPABILITIES)
    service.request_read(pipe, 0, 4)
    service.finish_session("cancelled")

    assert len(link.sent) == sent_before


# ------------------------------------------------------------ жизненный цикл связи


def test_detaching_disconnects_the_link_lost_slot_so_the_service_can_be_collected(qapp):
    link = _FakeLink()
    service = FileTransferService()
    service.attach_link(link)
    service.detach_link()
    alive = weakref.ref(service)

    del service
    gc.collect()

    assert alive() is None, "соединение disconnected удерживает отсоединённый сервис"


def test_reattaching_keeps_exactly_one_link_lost_connection(qapp):
    link = _FakeLink()
    other = _FakeLink()
    service = FileTransferService()
    lost = []
    service._on_link_lost = lambda attached, reason: lost.append(reason)

    service.attach_link(link)
    service.attach_link(other)
    service.attach_link(link)
    link.disconnected.emit("gone")
    other.disconnected.emit("stale")

    assert lost == ["gone"]
