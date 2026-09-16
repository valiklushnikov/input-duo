# configurator/tests/transfer/test_macos_receiver.py
"""Приёмник Windows->Mac: offer -> авторизация -> скачивание в staging -> ⌘V.

MacFileReceiver — событийный, без ChunkPipe/COM/потока: FILE_READ/FILE_CHUNK
гоняются напрямую через handle_message в тестах (без сокета и без pumping
событийного цикла), а staging — настоящий StagingArea на tmp_path, чтобы
проверять байты на диске, а не мок записи.
"""

from __future__ import annotations

import pytest

from duo_input.clipboard.wire import CAPABILITY_FILES, Message, MessageType
from duo_input.transfer.macos_files import MacFileReceiver
from duo_input.transfer.model import ENTRY_FILE, TransferEntry, TransferManifest
from duo_input.transfer.staging import StagingArea


class FakeLink:
    """Минимальная фейковая связь — всё, чего требует MacFileReceiver._send."""

    def __init__(self) -> None:
        self.sent: list[Message] = []

    def send(self, message: Message) -> bool:
        self.sent.append(message)
        return True


def _manifest(entries=None, transfer_id="t1"):
    if entries is None:
        entries = (TransferEntry(path="a.txt", kind=ENTRY_FILE, size=5, mtime_ns=0),)
    return TransferManifest(
        transfer_id=transfer_id, entries=tuple(entries), skipped=(), drop_effect=1
    )


def _sent(link, kind):
    return [message for message in link.sent if message.type is kind]


@pytest.fixture
def receiver(tmp_path, qapp):
    calls = {"armed": None}
    r = MacFileReceiver(
        StagingArea(tmp_path),
        pasteboard_arm=lambda paths: calls.__setitem__("armed", list(paths)),
    )
    r.set_peer_capabilities(frozenset({CAPABILITY_FILES}))
    link = FakeLink()
    r.attach_link(link)
    return r, link, calls


def _reply_to_last_read(link, blob, **overrides):
    """FILE_CHUNK отвечающий на последний отправленный FILE_READ."""
    read = _sent(link, MessageType.FILE_READ)[-1].header
    header = {
        "transfer_id": read["transfer_id"],
        "entry_index": read["entry_index"],
        "offset": read["offset"],
        "read_id": read["read_id"],
    }
    header.update(overrides)
    return Message(MessageType.FILE_CHUNK, header, blob)


def test_authorize_false_sends_no_read(receiver):
    r, link, _calls = receiver
    r.handle_offer(_manifest())

    r.authorize(False)

    assert _sent(link, MessageType.FILE_READ) == []


def test_authorized_download_writes_staging_and_arms(receiver, tmp_path):
    r, link, calls = receiver
    completed = []
    r.transfer_completed.connect(lambda: completed.append(1))
    r.handle_offer(_manifest())

    r.authorize(True)
    read = _sent(link, MessageType.FILE_READ)[-1]
    rid = read.header["read_id"]
    r.handle_message(
        Message(
            MessageType.FILE_CHUNK,
            {"transfer_id": "t1", "entry_index": 0, "offset": 0, "read_id": rid},
            b"hello",
        )
    )

    assert (tmp_path / "t1" / "a.txt").read_bytes() == b"hello"
    assert calls["armed"] and calls["armed"][0].name == "a.txt"
    assert completed == [1]
    [begin] = _sent(link, MessageType.TRANSFER_BEGIN)
    assert begin.header["transfer_id"] == "t1"
    [end] = _sent(link, MessageType.TRANSFER_END)
    assert end.header["status"] == "completed"


def test_truncated_chunk_fails_transfer(receiver, tmp_path):
    r, link, calls = receiver
    failed = []
    r.transfer_failed.connect(failed.append)
    r.handle_offer(_manifest())
    r.authorize(True)

    r.handle_message(_reply_to_last_read(link, b"he"))

    assert failed == ["truncated"]
    assert calls["armed"] is None
    assert not (tmp_path / "t1").exists(), "неполная staging-директория должна быть удалена"


def test_no_room_declines(receiver, monkeypatch):
    r, link, _calls = receiver
    monkeypatch.setattr(r._staging, "has_room_for", lambda total: False)
    failed = []
    r.transfer_failed.connect(failed.append)
    r.handle_offer(_manifest())

    r.authorize(True)

    assert failed == ["no_disk_space"]
    assert _sent(link, MessageType.FILE_READ) == []


def test_multiple_files_download_sequentially(receiver, tmp_path):
    r, link, calls = receiver
    entries = (
        TransferEntry(path="a.txt", kind=ENTRY_FILE, size=3, mtime_ns=0),
        TransferEntry(path="b.txt", kind=ENTRY_FILE, size=4, mtime_ns=0),
    )
    r.handle_offer(_manifest(entries=entries))

    r.authorize(True)
    r.handle_message(_reply_to_last_read(link, b"abc"))
    r.handle_message(_reply_to_last_read(link, b"wxyz"))

    assert (tmp_path / "t1" / "a.txt").read_bytes() == b"abc"
    assert (tmp_path / "t1" / "b.txt").read_bytes() == b"wxyz"
    assert {p.name for p in calls["armed"]} == {"a.txt", "b.txt"}
    assert len(_sent(link, MessageType.FILE_READ)) == 2


def test_multi_chunk_file_reassembles_and_keeps_one_read_in_flight(receiver, tmp_path, monkeypatch):
    import duo_input.transfer.macos_files as macos_files_module

    monkeypatch.setattr(macos_files_module, "MAX_FILE_CHUNK_BYTES", 4)
    r, link, calls = receiver
    r.handle_offer(
        _manifest(entries=(TransferEntry(path="big.bin", kind=ENTRY_FILE, size=10, mtime_ns=0),))
    )

    r.authorize(True)
    assert len(_sent(link, MessageType.FILE_READ)) == 1, "только один запрос в полёте"

    r.handle_message(_reply_to_last_read(link, b"1234"))
    assert len(_sent(link, MessageType.FILE_READ)) == 2, "ровно один новый запрос после ответа"

    r.handle_message(_reply_to_last_read(link, b"5678"))
    assert len(_sent(link, MessageType.FILE_READ)) == 3

    r.handle_message(_reply_to_last_read(link, b"90"))

    assert (tmp_path / "t1" / "big.bin").read_bytes() == b"1234567890"
    assert calls["armed"]


def test_cancel_mid_download_sends_transfer_end_and_removes_incomplete(receiver, tmp_path):
    r, link, calls = receiver
    cancelled = []
    r.transfer_cancelled.connect(lambda: cancelled.append(1))
    r.handle_offer(_manifest())
    r.authorize(True)
    assert (tmp_path / "t1").exists()

    r.cancel()

    assert cancelled == [1]
    [end] = _sent(link, MessageType.TRANSFER_END)
    assert end.header["status"] == "cancelled"
    assert not (tmp_path / "t1").exists()
    assert calls["armed"] is None


def test_zero_byte_file_completes_without_a_read(receiver, tmp_path):
    r, link, calls = receiver
    entries = (
        TransferEntry(path="empty.txt", kind=ENTRY_FILE, size=0, mtime_ns=0),
        TransferEntry(path="a.txt", kind=ENTRY_FILE, size=3, mtime_ns=0),
    )
    r.handle_offer(_manifest(entries=entries))

    r.authorize(True)
    # ровно один FILE_READ — за непустой файл; пустой read не запрашивает
    assert len(_sent(link, MessageType.FILE_READ)) == 1
    assert _sent(link, MessageType.FILE_READ)[0].header["entry_index"] == 1
    r.handle_message(_reply_to_last_read(link, b"abc"))

    assert (tmp_path / "t1" / "empty.txt").is_file()
    assert (tmp_path / "t1" / "empty.txt").read_bytes() == b""
    assert (tmp_path / "t1" / "a.txt").read_bytes() == b"abc"
    assert {p.name for p in calls["armed"]} == {"empty.txt", "a.txt"}


def test_oversized_chunk_fails_transfer(receiver, tmp_path):
    r, link, calls = receiver
    failed = []
    r.transfer_failed.connect(failed.append)
    r.handle_offer(_manifest())
    r.authorize(True)

    # запросили size=5, ответ длиннее запрошенного -> нарушение протокола
    r.handle_message(_reply_to_last_read(link, b"toolong"))

    assert failed == ["oversized_chunk"]
    assert calls["armed"] is None
    assert not (tmp_path / "t1").exists(), "неполная staging-директория должна быть удалена"


def test_new_offer_aborts_an_in_progress_download_and_removes_its_incomplete_dir(
    receiver, tmp_path
):
    r, link, calls = receiver
    # Первая передача: авторизована, один FILE_READ в полёте, ничего не дописано.
    r.handle_offer(_manifest(transfer_id="t1"))
    r.authorize(True)
    assert (tmp_path / "t1").exists()
    assert (tmp_path / "t1" / ".incomplete").exists()
    assert len(_sent(link, MessageType.FILE_READ)) == 1

    # Второй offer приходит, пока первая передача всё ещё качается.
    manifest2 = _manifest(transfer_id="t2")
    r.handle_offer(manifest2)

    # Незавершённая staging-директория первой передачи снесена, а не оставлена висеть.
    assert not (tmp_path / "t1").exists()
    # Приёмник ждёт решения по новому offer, а не застрял в DOWNLOADING старого.
    assert r._state.name == "AWAITING_AUTH"
    assert r._manifest is manifest2
    assert r._session is None
    assert calls["armed"] is None


def test_a_foreign_or_stale_read_id_chunk_is_ignored(receiver, tmp_path):
    r, link, calls = receiver
    r.handle_offer(_manifest())
    r.authorize(True)
    read = _sent(link, MessageType.FILE_READ)[-1]

    r.handle_message(
        Message(
            MessageType.FILE_CHUNK,
            {
                "transfer_id": "t1",
                "entry_index": 0,
                "offset": 0,
                "read_id": read.header["read_id"] + 999,
            },
            b"XXXXX",
        )
    )

    # a.txt существует как пустой файл (materialized в begin), но чужой чанк
    # не должен был записать в него байты.
    assert (tmp_path / "t1" / "a.txt").read_bytes() == b""
    assert len(_sent(link, MessageType.FILE_READ)) == 1, "чужой чанк не должен продвинуть цикл"

    r.handle_message(
        Message(
            MessageType.FILE_CHUNK,
            {
                "transfer_id": "t1",
                "entry_index": 0,
                "offset": 0,
                "read_id": read.header["read_id"],
            },
            b"hello",
        )
    )

    assert (tmp_path / "t1" / "a.txt").read_bytes() == b"hello"
    assert calls["armed"]
