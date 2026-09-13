"""Sender-side file transfer behavior."""

from __future__ import annotations

import os
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
from duo_input.transfer.service import FileTransferService


class _FakeLink(QObject):
    message_received = Signal(object)
    disconnected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[Message] = []

    def send(self, message: Message) -> None:
        self.sent.append(message)

    def close(self) -> None: ...


@pytest.fixture
def sender(qapp):
    service = FileTransferService()
    link = _FakeLink()
    service.attach_link(link)
    service.set_peer_capabilities(frozenset({CAPABILITY_CLIPBOARD, CAPABILITY_FILES}))
    return service, link


def _sent(link, kind):
    return [message for message in link.sent if message.type is kind]


def _read(transfer_id, entry_index=0, offset=0, length=4):
    return Message(
        MessageType.FILE_READ,
        {"transfer_id": transfer_id, "entry_index": entry_index, "offset": offset, "length": length},
        b"",
    )


def test_copying_a_file_sends_one_offer_describing_it(sender, tmp_path):
    service, link = sender
    source = tmp_path / "notes.txt"
    source.write_bytes(b"hello")

    transfer_id = service.offer_local_files([source])

    [offer] = _sent(link, MessageType.FILE_OFFER)
    assert offer.header["transfer_id"] == transfer_id
    assert [entry["path"] for entry in offer.header["entries"]] == ["notes.txt"]
    assert offer.header["total_bytes"] == 5


def test_the_offer_carries_no_bytes_at_all(sender, tmp_path):
    service, link = sender
    source = tmp_path / "secret.bin"
    source.write_bytes(b"CLASSIFIED")

    service.offer_local_files([source])

    [offer] = _sent(link, MessageType.FILE_OFFER)
    assert offer.blob == b""
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
    assert chunk.header == {"transfer_id": transfer_id, "entry_index": 0, "offset": 3}


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


def test_a_transfer_end_releases_the_snapshot_and_its_descriptor(sender, tmp_path):
    service, _link = sender
    source = tmp_path / "a.bin"
    source.write_bytes(b"0123456789")
    transfer_id = service.offer_local_files([source])
    service.handle_message(_read(transfer_id, length=2))

    service.handle_message(
        Message(MessageType.TRANSFER_END, {"transfer_id": transfer_id, "session_id": "s-1", "status": "completed"}, b"")
    )

    assert transfer_id not in service.snapshots.transfer_ids
    os.remove(source)


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
