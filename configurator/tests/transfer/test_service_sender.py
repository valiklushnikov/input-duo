"""Sender-side file transfer behavior."""

from __future__ import annotations

import os
import logging
import time

import pytest
from PySide6.QtCore import QObject, Signal

from duo_input.clipboard.wire import (
    CAPABILITY_CLIPBOARD,
    CAPABILITY_FILES,
    MAX_FILE_CHUNK_BYTES,
    Message,
    MessageType,
)
from duo_input.transfer.model import MAX_MANIFEST_BYTES, TransferManifest, decode_manifest
from duo_input.transfer.fileprovider_perf import PerfEmitter
from duo_input.transfer.service import FileTransferService


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


@pytest.fixture
def sender(qapp):
    service = FileTransferService()
    link = _FakeLink()
    service.attach_link(link)
    service.set_peer_capabilities(frozenset({CAPABILITY_CLIPBOARD, CAPABILITY_FILES}))
    return service, link


@pytest.fixture
def sender_with_perf(qapp, caplog):
    logger = logging.getLogger("duo_input.transfer.service")
    ticks = iter(range(100, 10_000))
    perf = PerfEmitter(logger, "windows_python_monotonic", clock=lambda: next(ticks))
    caplog.set_level(logging.INFO, logger=logger.name)
    service = FileTransferService(perf=perf)
    link = _FakeLink()
    service.attach_link(link)
    service.set_peer_capabilities(frozenset({CAPABILITY_CLIPBOARD, CAPABILITY_FILES}))
    return service, link, caplog


def _sent(link, kind):
    return [message for message in link.sent if message.type is kind]


def _read(transfer_id, entry_index=0, offset=0, length=4, read_id=7):
    return Message(
        MessageType.FILE_READ,
        {
            "transfer_id": transfer_id,
            "entry_index": entry_index,
            "offset": offset,
            "length": length,
            "read_id": read_id,
        },
        b"",
    )


def _offered_manifest(offer: Message) -> TransferManifest:
    return decode_manifest(offer.blob)


def _perf_events(caplog) -> list[dict[str, str]]:
    events = []
    for record in caplog.records:
        message = record.getMessage()
        if "fp_perf " not in message:
            continue
        atoms = message[message.index("fp_perf ") + len("fp_perf ") :].split()
        events.append(dict(atom.split("=", 1) for atom in atoms))
    return events


def test_sender_records_lookup_read_and_send_for_one_read(sender_with_perf, tmp_path):
    service, link, records = sender_with_perf
    source = tmp_path / "a.bin"
    source.write_bytes(b"0123456789")
    transfer_id = service.offer_local_files([source])

    service.handle_message(_read(transfer_id, offset=3, length=4, read_id=7))

    events = _perf_events(records)
    assert [event["event"] for event in events] == [
        "file_read_handler_enter",
        "file_read_receive",
        "snapshot_lookup_begin",
        "snapshot_lookup_end",
        "source_open_begin",
        "source_open_end",
        "source_read_begin",
        "source_read_end",
        "file_chunk_constructed",
        "file_chunk_send",
        "file_read_handler_exit",
    ]
    assert all(event["read_id"] == "7" for event in events)
    assert events[-1]["bytes"] == "4"
    assert next(event for event in events if event["event"] == "source_open_end")[
        "fd_reused"
    ] == "false"
    assert _sent(link, MessageType.FILE_CHUNK)[0].blob == b"3456"


def test_sender_records_descriptor_reuse_without_reopening(sender_with_perf, tmp_path):
    service, _link, records = sender_with_perf
    source = tmp_path / "a.bin"
    source.write_bytes(b"0123456789")
    transfer_id = service.offer_local_files([source])

    service.handle_message(_read(transfer_id, offset=0, length=4, read_id=7))
    service.handle_message(_read(transfer_id, offset=4, length=4, read_id=8))

    opens = [
        event
        for event in _perf_events(records)
        if event["event"] == "source_open_end"
    ]
    assert [event["fd_reused"] for event in opens] == ["false", "true"]
    assert all(event["seek_required"] == "true" for event in opens)


def test_sender_records_zero_delay_event_loop_lag_probe(
    sender_with_perf, tmp_path, qapp
):
    service, _link, records = sender_with_perf
    source = tmp_path / "a.bin"
    source.write_bytes(b"0123456789")
    transfer_id = service.offer_local_files([source])

    service.handle_message(_read(transfer_id, offset=0, length=4, read_id=7))
    qapp.processEvents()

    [lag] = [
        event for event in _perf_events(records) if event["event"] == "event_loop_lag"
    ]
    assert lag["read_id"] == "7"
    assert int(lag["lag_ns"]) >= 0


def test_changed_source_records_failed_read_without_chunk_send(
    sender_with_perf, tmp_path
):
    service, link, records = sender_with_perf
    source = tmp_path / "a.bin"
    source.write_bytes(b"0123456789")
    transfer_id = service.offer_local_files([source])
    service.handle_message(_read(transfer_id, length=2, read_id=6))
    baseline = source.stat().st_mtime_ns
    deadline = time.monotonic() + 2
    while source.stat().st_mtime_ns == baseline:
        with open(source, "r+b") as handle:
            handle.write(b"X")
            handle.flush()
            os.fsync(handle.fileno())
        if time.monotonic() >= deadline:
            pytest.fail("source mtime_ns did not advance before the bounded deadline")

    service.handle_message(_read(transfer_id, offset=2, length=2, read_id=7))

    events = [event for event in _perf_events(records) if event.get("read_id") == "7"]
    assert next(event for event in events if event["event"] == "source_open_end")[
        "status"
    ] == "source_changed"
    assert not any(event["event"] == "file_chunk_send" for event in events)


def test_copying_a_file_sends_one_offer_describing_it(sender, tmp_path):
    service, link = sender
    source = tmp_path / "notes.txt"
    source.write_bytes(b"hello")

    transfer_id = service.offer_local_files([source])

    [offer] = _sent(link, MessageType.FILE_OFFER)
    manifest = _offered_manifest(offer)
    assert manifest.transfer_id == transfer_id
    assert [entry.path for entry in manifest.entries] == ["notes.txt"]
    assert manifest.total_bytes == 5


def test_the_offer_carries_no_bytes_at_all(sender, tmp_path):
    service, link = sender
    source = tmp_path / "secret.bin"
    source.write_bytes(b"CLASSIFIED")

    service.offer_local_files([source])

    [offer] = _sent(link, MessageType.FILE_OFFER)
    assert b"CLASSIFIED" not in offer.blob
    assert b"CLASSIFIED" not in repr(offer.header).encode()


def test_nothing_is_offered_to_a_peer_that_does_not_advertise_files(qapp, tmp_path):
    service = FileTransferService()
    link = _FakeLink()
    service.attach_link(link)
    service.set_peer_capabilities(frozenset({CAPABILITY_CLIPBOARD}))
    source = tmp_path / "a.txt"
    source.write_bytes(b"x")

    assert service.offer_local_files([source]) is None
    assert link.sent == []


def test_nothing_is_offered_when_there_is_no_link(qapp, tmp_path):
    service = FileTransferService()
    source = tmp_path / "a.txt"
    source.write_bytes(b"x")

    assert service.offer_local_files([source]) is None


def test_a_read_request_is_answered_with_exactly_those_bytes(sender, tmp_path):
    service, link = sender
    source = tmp_path / "a.bin"
    source.write_bytes(b"0123456789")
    transfer_id = service.offer_local_files([source])

    service.handle_message(_read(transfer_id, offset=3, length=4))

    [chunk] = _sent(link, MessageType.FILE_CHUNK)
    assert chunk.blob == b"3456"
    assert chunk.header == {
        "transfer_id": transfer_id,
        "entry_index": 0,
        "offset": 3,
        "read_id": 7,
    }


def test_no_chunk_is_ever_sent_without_a_request(sender, tmp_path):
    service, link = sender
    source = tmp_path / "a.bin"
    source.write_bytes(b"x" * 4096)

    service.offer_local_files([source])

    assert _sent(link, MessageType.FILE_CHUNK) == []


def test_a_request_longer_than_the_chunk_ceiling_is_refused(sender, tmp_path):
    service, link = sender
    source = tmp_path / "a.bin"
    source.write_bytes(b"x" * 16)
    transfer_id = service.offer_local_files([source])

    service.handle_message(_read(transfer_id, length=MAX_FILE_CHUNK_BYTES + 1))

    assert _sent(link, MessageType.FILE_CHUNK) == []
    [error] = _sent(link, MessageType.FILE_ERROR)
    assert error.header["reason"] == "bad_request"


@pytest.mark.parametrize(
    "header",
    [
        {"transfer_id": "t", "entry_index": 0, "offset": -1, "length": 4},
        {"transfer_id": "t", "entry_index": 0, "offset": 0, "length": 0},
        {"transfer_id": "t", "entry_index": 0, "offset": 0, "length": -4},
        {"transfer_id": "t", "entry_index": -1, "offset": 0, "length": 4},
        {"transfer_id": "t", "entry_index": 0, "offset": True, "length": 4},
        {"transfer_id": "t", "entry_index": 0, "length": 4},
        {"transfer_id": "t", "entry_index": 0, "offset": 0, "length": 4},
        {"transfer_id": "t", "entry_index": 0, "offset": 0, "length": 4, "read_id": 0},
        {"transfer_id": "t", "entry_index": 0, "offset": 10**30, "length": 4, "read_id": 1},
        {"transfer_id": "t", "entry_index": 10**30, "offset": 0, "length": 4, "read_id": 1},
        {"transfer_id": "тест", "entry_index": 0, "offset": 0, "length": 4, "read_id": 1},
        {"transfer_id": ["t"], "entry_index": 0, "offset": 0, "length": 4, "read_id": 1},
        {"transfer_id": "t" * 65, "entry_index": 0, "offset": 0, "length": 4, "read_id": 1},
    ],
)
def test_a_malformed_request_is_refused_without_reading_anything(sender, header):
    service, link = sender

    service.handle_message(Message(MessageType.FILE_READ, header, b""))

    assert _sent(link, MessageType.FILE_CHUNK) == []
    assert _sent(link, MessageType.FILE_ERROR)


def test_a_request_for_an_unknown_transfer_is_answered_with_source_missing(sender):
    service, link = sender

    service.handle_message(_read("never-offered"))

    [error] = _sent(link, MessageType.FILE_ERROR)
    assert error.header["reason"] == "source_missing"


def test_a_source_changed_under_us_is_answered_with_source_changed(sender, tmp_path):
    service, link = sender
    source = tmp_path / "a.bin"
    source.write_bytes(b"0123456789")
    transfer_id = service.offer_local_files([source])
    service.handle_message(_read(transfer_id, length=2))
    before = source.stat().st_mtime_ns
    deadline = time.monotonic() + 2
    while True:
        with open(source, "r+b") as handle:
            handle.write(b"X")
            handle.flush()
            os.fsync(handle.fileno())
        if source.stat().st_mtime_ns != before:
            break
        if time.monotonic() >= deadline:
            pytest.fail("source mtime_ns did not advance before the bounded deadline")

    service.handle_message(_read(transfer_id, offset=2, length=2))

    [error] = _sent(link, MessageType.FILE_ERROR)
    assert error.header["reason"] == "source_changed"


def test_a_second_copy_gets_its_own_transfer_id(sender, tmp_path):
    service, _link = sender
    first_file = tmp_path / "a.bin"
    first_file.write_bytes(b"a")
    second_file = tmp_path / "b.bin"
    second_file.write_bytes(b"b")

    first = service.offer_local_files([first_file])
    second = service.offer_local_files([second_file])

    assert first != second


def test_a_transfer_end_closes_descriptors_but_keeps_the_snapshot_for_a_repeat_paste(sender, tmp_path):
    """Спека §1016-1018 (сценарий 3), §537, §1013: повторный Ctrl+V - не новая
    передача, а новые чтения ТОГО ЖЕ манифеста; отдельного отказа
    "дублирующийся transfer_id" не существует. TRANSFER_END поэтому обязан
    закрыть дескрипторы (снять блокировку на удаление, спека §15) и НЕ
    трогать сам снимок - иначе вторая сессия того же transfer_id получила бы
    "снимок неизвестен" вместо новых чтений.
    """
    service, link = sender
    source = tmp_path / "a.bin"
    source.write_bytes(b"0123456789")
    transfer_id = service.offer_local_files([source])
    service.handle_message(_read(transfer_id, length=2))

    service.handle_message(
        Message(MessageType.TRANSFER_END, {"transfer_id": transfer_id, "session_id": "s-1", "status": "completed"}, b"")
    )

    # Дескриптор закрыт - никто больше не "обслуживает" эту запись...
    assert service.snapshots.serving == frozenset()
    # ...но снимок остаётся в реестре, а не удалён вместе с манифестом.
    assert transfer_id in service.snapshots.transfer_ids

    # Вторая сессия того же transfer_id - настоящий повторный Ctrl+V - обязана
    # снова читать, а не получать отказ.
    service.handle_message(_read(transfer_id, offset=0, length=2))
    chunks = _sent(link, MessageType.FILE_CHUNK)
    assert chunks[-1].blob == b"01"
    assert not _sent(link, MessageType.FILE_ERROR)

    # Вторая сессия тоже заканчивается своим TRANSFER_END (у каждой вставки -
    # свой EndOperation), и это тоже обязано закрыть дескриптор, а не оставить
    # файл заблокированным до конца жизни процесса.
    service.handle_message(
        Message(MessageType.TRANSFER_END, {"transfer_id": transfer_id, "session_id": "s-2", "status": "completed"}, b"")
    )
    assert service.snapshots.serving == frozenset()
    os.remove(source)  # не бросает: обе сессии закрыли свои дескрипторы


def test_a_copy_during_an_active_transfer_does_not_release_the_earlier_snapshot(sender, tmp_path):
    service, _link = sender
    first_file = tmp_path / "a.bin"
    first_file.write_bytes(b"0123456789")
    second_file = tmp_path / "b.bin"
    second_file.write_bytes(b"x")
    first = service.offer_local_files([first_file])
    service.handle_message(_read(first, length=2))

    service.offer_local_files([second_file])

    assert first in service.snapshots.transfer_ids
    assert first in service.snapshots.serving


def test_losing_the_link_releases_every_snapshot(sender, tmp_path):
    service, link = sender
    source = tmp_path / "a.bin"
    source.write_bytes(b"0123456789")
    transfer_id = service.offer_local_files([source])
    service.handle_message(_read(transfer_id, length=2))

    link.disconnected.emit("connection closed")

    assert service.snapshots.transfer_ids == ()
    os.remove(source)


def test_a_stale_disconnected_signal_does_not_tear_down_a_reconnected_link(sender, tmp_path):
    service, old_link = sender
    new_link = _FakeLink()
    service.detach_link()
    service.attach_link(new_link)
    service.set_peer_capabilities(frozenset({CAPABILITY_FILES}))
    source = tmp_path / "a.bin"
    source.write_bytes(b"x")
    transfer_id = service.offer_local_files([source])

    old_link.disconnected.emit("stale")

    assert service.offer_local_files([source]) is not None
    assert transfer_id in service.snapshots.transfer_ids
    assert len(_sent(new_link, MessageType.FILE_OFFER)) == 2


def test_direct_reattach_starts_a_clean_unauthorized_session(sender, tmp_path):
    service, _old_link = sender
    old_source = tmp_path / "old.bin"
    old_source.write_bytes(b"old")
    transfer_id = service.offer_local_files([old_source])
    service.handle_message(_read(transfer_id, length=2))
    legacy_link = _FakeLink()

    service.attach_link(legacy_link)

    assert service.snapshots.transfer_ids == ()
    os.remove(old_source)
    new_source = tmp_path / "new.bin"
    new_source.write_bytes(b"new")
    assert service.offer_local_files([new_source]) is None
    assert legacy_link.sent == []


def test_an_unsafe_source_name_is_not_offered_and_is_reported(sender, tmp_path):
    service, link = sender
    source = tmp_path / "nul.txt"
    source.write_bytes(b"x")
    failures: list[str] = []
    service.send_failed.connect(failures.append)

    assert service.offer_local_files([source]) is None
    assert link.sent == []
    assert failures


def test_an_os_failure_does_not_report_its_absolute_source_path(sender, tmp_path, monkeypatch):
    service, _link = sender
    source = tmp_path / "private.bin"
    source.write_bytes(b"x")
    failures: list[str] = []
    service.send_failed.connect(failures.append)

    def fail_scan(*_args):
        raise OSError(5, "access denied", str(source))

    monkeypatch.setattr("duo_input.transfer.service.scan", fail_scan)

    assert service.offer_local_files([source]) is None
    assert failures == ["access denied"]
    assert str(source) not in failures[0]


def test_a_refusal_echoes_only_the_fields_that_passed_validation(sender):
    service, link = sender

    service.handle_message(
        Message(
            MessageType.FILE_READ,
            {"transfer_id": "x" * 70_000, "entry_index": 10**30, "offset": [1],
             "length": 4, "read_id": 9},
            b"",
        )
    )

    [error] = _sent(link, MessageType.FILE_ERROR)
    assert error.header == {
        "transfer_id": "",
        "entry_index": -1,
        "offset": -1,
        "read_id": 9,
        "reason": "bad_request",
    }


def test_a_read_that_starts_past_the_end_of_the_file_is_a_bad_request(sender, tmp_path):
    service, link = sender
    source = tmp_path / "a.bin"
    source.write_bytes(b"0123456789")
    transfer_id = service.offer_local_files([source])

    service.handle_message(_read(transfer_id, offset=10, length=4))
    service.handle_message(_read(transfer_id, offset=2**62, length=4))

    assert _sent(link, MessageType.FILE_CHUNK) == []
    assert [error.header["reason"] for error in _sent(link, MessageType.FILE_ERROR)] == [
        "bad_request",
        "bad_request",
    ]


def test_a_read_running_past_the_end_is_answered_with_the_tail_only(sender, tmp_path):
    service, link = sender
    source = tmp_path / "a.bin"
    source.write_bytes(b"0123456789")
    transfer_id = service.offer_local_files([source])

    service.handle_message(_read(transfer_id, offset=8, length=4))

    [chunk] = _sent(link, MessageType.FILE_CHUNK)
    assert chunk.blob == b"89"


def test_skipped_entries_of_a_local_offer_are_reported(sender, tmp_path, monkeypatch):
    service, _link = sender
    folder = tmp_path / "Photos"
    folder.mkdir()
    (folder / "a.txt").write_bytes(b"a")
    (folder / "hidden").write_bytes(b"b")
    reported = []
    service.entries_skipped.connect(lambda count, names: reported.append((count, names)))
    real_stat = os.stat

    def refuse_hidden(path, *args, **kwargs):
        if os.fspath(path).endswith("hidden"):
            raise PermissionError("denied")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", refuse_hidden)

    service.offer_local_files([folder])

    assert reported == [(1, ("hidden",))]


def test_a_manifest_too_large_for_one_frame_is_refused_before_anything_is_published(
    sender, tmp_path, monkeypatch
):
    service, link = sender
    source = tmp_path / "a.bin"
    source.write_bytes(b"x")
    failures: list[str] = []
    service.send_failed.connect(failures.append)
    monkeypatch.setattr(
        "duo_input.transfer.service.encode_manifest",
        lambda manifest: b" " * (MAX_MANIFEST_BYTES + 1),
    )

    assert service.offer_local_files([source]) is None

    assert link.sent == []
    assert service.snapshots.transfer_ids == ()
    assert failures and "a.bin" not in failures[0]


def test_a_manifest_exactly_at_the_ceiling_is_offered(sender, tmp_path, monkeypatch):
    service, link = sender
    source = tmp_path / "a.bin"
    source.write_bytes(b"x")
    real = encode_manifest_for_test()

    def padded(manifest):
        encoded = real(manifest)
        return encoded + b" " * (MAX_MANIFEST_BYTES - len(encoded))

    monkeypatch.setattr("duo_input.transfer.service.encode_manifest", padded)

    transfer_id = service.offer_local_files([source])

    [offer] = _sent(link, MessageType.FILE_OFFER)
    assert len(offer.blob) == MAX_MANIFEST_BYTES
    assert decode_manifest(offer.blob).transfer_id == transfer_id


def encode_manifest_for_test():
    from duo_input.transfer.model import encode_manifest

    return encode_manifest
