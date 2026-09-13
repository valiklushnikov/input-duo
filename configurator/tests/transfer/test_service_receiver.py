# configurator/tests/transfer/test_service_receiver.py
"""Принимающая сторона: автомат, окно в один запрос, и мгновенная отмена."""

from __future__ import annotations

import threading

import pytest
from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt, Signal

from duo_input.clipboard.wire import (
    CAPABILITY_CLIPBOARD,
    CAPABILITY_FILES,
    MAX_FILE_CHUNK_BYTES,
    Message,
    MessageType,
)
from duo_input.transfer.model import ENTRY_DIRECTORY, ENTRY_FILE, TransferEntry, TransferManifest
from duo_input.transfer.pipe import PipeClosed
from duo_input.transfer.service import FileTransferService, TransferState


class _FakeLink(QObject):
    message_received = Signal(object)
    disconnected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[Message] = []

    def send(self, message: Message) -> None:
        self.sent.append(message)

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
    service.handle_message(Message(MessageType.FILE_OFFER, manifest.to_dict(), b""))


def _sent(link, kind):
    return [message for message in link.sent if message.type is kind]


def test_a_fresh_service_is_idle():
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


def test_a_malformed_offer_is_refused_without_raising_inside_the_slot(receiver):
    service, _link = receiver

    service.handle_message(Message(MessageType.FILE_OFFER, {"entries": "nope"}, b""))

    assert service.state is TransferState.IDLE


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


def test_requesting_a_read_sends_exactly_one_file_read(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    service.open_pipe("t-1", 1)

    service.request_read("t-1", 1, 0, 4)

    [read] = _sent(link, MessageType.FILE_READ)
    assert read.header == {
        "transfer_id": "t-1",
        "entry_index": 1,
        "offset": 0,
        "length": 4,
    }


def test_a_second_request_while_one_is_in_flight_is_refused(receiver):
    # Окно в один запрос - это correctness baseline фазы 1. Второй запрос
    # означал бы предвыборку, а с ней Seek потребовал бы инвалидации.
    service, link = receiver
    _deliver_offer(service, _offer())
    service.open_pipe("t-1", 1)
    service.request_read("t-1", 1, 0, 4)

    service.request_read("t-1", 1, 4, 4)

    assert len(_sent(link, MessageType.FILE_READ)) == 1


def test_a_chunk_lands_in_the_pipe_and_clears_the_in_flight_slot(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    service.request_read("t-1", 1, 0, 4)

    service.handle_message(
        Message(
            MessageType.FILE_CHUNK,
            {"transfer_id": "t-1", "entry_index": 1, "offset": 0},
            b"0123",
        )
    )

    assert pipe.take(4) == b"0123"
    service.request_read("t-1", 1, 4, 4)
    assert len(_sent(link, MessageType.FILE_READ)) == 2


def test_a_chunk_for_an_offset_we_did_not_request_is_dropped(receiver):
    # Без этой проверки устаревший чанк после Seek попал бы в поток, и
    # Проводник записал бы байты не туда.
    service, _link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    service.request_read("t-1", 1, 0, 4)

    service.handle_message(
        Message(
            MessageType.FILE_CHUNK,
            {"transfer_id": "t-1", "entry_index": 1, "offset": 999},
            b"stale",
        )
    )

    assert pipe.take(10) == b""


def test_a_chunk_for_another_transfer_is_dropped(receiver):
    service, _link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    service.request_read("t-1", 1, 0, 4)

    service.handle_message(
        Message(
            MessageType.FILE_CHUNK,
            {"transfer_id": "other", "entry_index": 1, "offset": 0},
            b"wrong",
        )
    )

    assert pipe.take(10) == b""


def test_an_empty_chunk_finishes_the_pipe_rather_than_hanging(receiver):
    service, _link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    service.request_read("t-1", 1, 10, 4)

    service.handle_message(
        Message(
            MessageType.FILE_CHUNK,
            {"transfer_id": "t-1", "entry_index": 1, "offset": 10},
            b"",
        )
    )

    assert pipe.finished is True


def test_progress_counts_the_bytes_that_actually_arrived(receiver):
    service, _link = receiver
    _deliver_offer(service, _offer(size=10))
    service.open_pipe("t-1", 1)
    seen: list[tuple[int, int]] = []
    service.transfer_progress.connect(lambda done, total: seen.append((done, total)))

    service.request_read("t-1", 1, 0, 4)
    service.handle_message(
        Message(
            MessageType.FILE_CHUNK,
            {"transfer_id": "t-1", "entry_index": 1, "offset": 0},
            b"0123",
        )
    )

    assert seen == [(4, 10)]


def test_a_file_error_fails_the_session_and_closes_the_pipe(receiver):
    service, _link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    service.request_read("t-1", 1, 0, 4)
    failures: list[str] = []
    service.transfer_failed.connect(failures.append)

    service.handle_message(
        Message(
            MessageType.FILE_ERROR,
            {
                "transfer_id": "t-1",
                "entry_index": 1,
                "offset": 0,
                "reason": "source_changed",
            },
            b"",
        )
    )

    assert service.state is TransferState.FAILED
    assert failures == ["source_changed"]
    with pytest.raises(PipeClosed):
        pipe.take(4)


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


def test_a_newer_offer_replaces_an_idle_one(receiver):
    service, _link = receiver
    _deliver_offer(service, _offer(transfer_id="first"))

    _deliver_offer(service, _offer(transfer_id="second"))

    assert service.offered_manifest.transfer_id == "second"


def test_a_newer_offer_does_not_interrupt_an_active_transfer(receiver):
    # Сценарий 1 спецификации, на стороне получателя.
    service, _link = receiver
    _deliver_offer(service, _offer(transfer_id="first"))
    pipe = service.open_pipe("first", 1)

    _deliver_offer(service, _offer(transfer_id="second"))

    assert service.state is TransferState.TRANSFERRING
    service.request_read("first", 1, 0, 4)
    service.handle_message(
        Message(
            MessageType.FILE_CHUNK,
            {"transfer_id": "first", "entry_index": 1, "offset": 0},
            b"abcd",
        )
    )
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


def _reply(kind=MessageType.FILE_CHUNK, **changes):
    header = {"transfer_id": "t-1", "entry_index": 1, "offset": 0}
    header.update(changes)
    return Message(kind, header, b"abcd" if kind is MessageType.FILE_CHUNK else b"")


@pytest.mark.parametrize("kind", [MessageType.FILE_ERROR, MessageType.FILE_CHUNK])
@pytest.mark.parametrize("changes", [
    {"transfer_id": "other"}, {"entry_index": 0}, {"offset": 9},
    {"entry_index": True}, {"offset": False}, {"offset": None},
])
def test_unrelated_or_malformed_replies_preserve_the_active_read(receiver, kind, changes):
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    service.request_read("t-1", 1, 0, 4)
    failures = []
    service.transfer_failed.connect(failures.append)

    service.handle_message(_reply(kind, **changes))

    assert service.state is TransferState.TRANSFERRING
    assert pipe.take(4) == b""
    assert failures == []
    assert _sent(link, MessageType.TRANSFER_END) == []
    service.handle_message(_reply())
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
    service.request_read("t-1", 1, 0, 4)
    service.handle_message(_reply())

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
    service.request_read("t-1", 1, 0, 4)
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
    service.request_read("t-1", 1, 0, 4)
    assert new_link.sent == []
    with pytest.raises(ValueError):
        service.open_pipe("t-1", 1)
    old_link.disconnected.emit("late disconnect")
    assert service.state is TransferState.IDLE
    _deliver_offer(service, _offer())
    fresh = service.open_pipe("t-1", 1)
    service.request_read("t-1", 1, 0, 4)
    service.handle_message(_reply())
    assert fresh.take(4) == b"abcd"


def test_replacing_a_pipe_wakes_old_waiter_and_clears_its_read(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    old = service.open_pipe("t-1", 1)
    service.request_read("t-1", 1, 0, 4)
    worker, results = _start_waiter(old)

    fresh = service.open_pipe("t-1", 1)

    worker.join(0.5)
    assert not worker.is_alive()
    assert isinstance(results[0], PipeClosed)
    service.request_read("t-1", 1, 4, 4)
    service.handle_message(_reply())
    assert fresh.take(4) == b""
    service.handle_message(_reply(offset=4))
    assert fresh.take(4) == b"abcd"
    assert len(_sent(link, MessageType.TRANSFER_BEGIN)) == 1


@pytest.mark.parametrize("args", [
    ("t-1", 1, -1, 4), ("t-1", 1, 0, 0), ("t-1", 1, 0, -1),
    ("t-1", True, 0, 4), ("t-1", 1, False, 4), ("t-1", 1, 0, True),
    ("t-1", 1, 0.5, 4), ("t-1", 1, 0, "4"), ([], 1, 0, 4),
])
def test_invalid_read_never_goes_in_flight(receiver, args):
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)

    service.request_read(*args)

    assert _sent(link, MessageType.FILE_READ) == []
    service.request_read("t-1", 1, 0, 4)
    service.handle_message(_reply())
    assert pipe.take(4) == b"abcd"


@pytest.mark.parametrize("terminal", ["completed", "cancelled", "failed", "disconnect", "detach"])
def test_terminal_paths_wake_all_pipes_and_notify_at_most_once(receiver, terminal):
    service, link = receiver
    _deliver_offer(service, TransferManifest("t-1", (
        TransferEntry("a", ENTRY_FILE, 10, 1),
        TransferEntry("b", ENTRY_FILE, 10, 1),
    )))
    pipes = [service.open_pipe("t-1", index) for index in (0, 1)]
    for index in (0, 1):
        service.request_read("t-1", index, 0, 4)
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
            service.handle_message(_reply(MessageType.FILE_ERROR, reason="source_changed"))
        else:
            service.finish_session(terminal)
    service.finish_session("completed")
    service.handle_message(_reply(MessageType.FILE_ERROR, reason="late"))

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

    service.close_pipe("t-1", 1)

    worker.join(0.5)
    assert not worker.is_alive()
    assert results == [True]
    assert service.state is TransferState.TRANSFERRING
    assert _sent(link, MessageType.TRANSFER_END) == []
    service.finish_session("completed")
    assert len(_sent(link, MessageType.TRANSFER_END)) == 1


def test_queued_read_is_dispatched_on_the_service_thread_and_capped(receiver, qapp):
    service, link = receiver
    _deliver_offer(service, _offer())
    service.open_pipe("t-1", 1)
    invoked = []

    def enqueue():
        invoked.append(QMetaObject.invokeMethod(
            service, "request_read", Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, "t-1"), Q_ARG(int, 1), Q_ARG("qlonglong", 0),
            Q_ARG(int, MAX_FILE_CHUNK_BYTES + 1),
        ))

    worker = threading.Thread(target=enqueue)
    worker.start()
    worker.join(1)
    assert invoked == [True]
    assert _sent(link, MessageType.FILE_READ) == []
    qapp.processEvents()
    assert _sent(link, MessageType.FILE_READ)[0].header["length"] == MAX_FILE_CHUNK_BYTES


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

    link.send = send

    service.finish_session(status)

    assert events == [status]
    assert len(_sent(link, MessageType.TRANSFER_END)) == 1


def test_reading_an_eof_pipe_does_not_put_more_work_in_flight(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    service.request_read("t-1", 1, 10, 4)
    service.handle_message(Message(MessageType.FILE_CHUNK,
                                  {"transfer_id": "t-1", "entry_index": 1, "offset": 10}, b""))

    service.request_read("t-1", 1, 10, 4)

    assert pipe.finished
    assert len(_sent(link, MessageType.FILE_READ)) == 1


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

    service.handle_message(_reply(MessageType.FILE_ERROR, reason="source_changed"))

    assert service.state is TransferState.TRANSFERRING
    assert pipe.take(4) == b""
    assert _sent(link, MessageType.TRANSFER_END) == []
    service.request_read("t-1", 1, 0, 4)
    service.handle_message(_reply())
    assert pipe.take(4) == b"abcd"


def test_progress_preserves_a_total_above_the_qt_int_range(receiver):
    service, _link = receiver
    _deliver_offer(service, _offer(size=8_800_000_000))
    service.open_pipe("t-1", 1)
    seen = []
    service.transfer_progress.connect(lambda done, total: seen.append((done, total)))
    service.request_read("t-1", 1, 0, 4)

    service.handle_message(_reply())

    assert seen == [(4, 8_800_000_000)]


def test_queued_read_preserves_an_offset_above_the_qt_int_range(receiver, qapp):
    service, link = receiver
    _deliver_offer(service, _offer(size=8_800_000_000))
    service.open_pipe("t-1", 1)
    invoked = []

    def enqueue():
        try:
            invoked.append(QMetaObject.invokeMethod(
                service, "request_read", Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, "t-1"), Q_ARG(int, 1), Q_ARG("qlonglong", 4_400_000_000),
                Q_ARG(int, 4),
            ))
        except RuntimeError as error:
            invoked.append(error)

    worker = threading.Thread(target=enqueue)
    worker.start()
    worker.join(1)
    assert invoked == [True]
    assert _sent(link, MessageType.FILE_READ) == []
    qapp.processEvents()
    assert _sent(link, MessageType.FILE_READ)[0].header["offset"] == 4_400_000_000
