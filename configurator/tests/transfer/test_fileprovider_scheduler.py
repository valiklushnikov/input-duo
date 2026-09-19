"""Task 9: per-fetch File Provider scheduling without shared cursors."""

from __future__ import annotations

import json

import pytest

from duo_input.clipboard.wire import MAX_FILE_CHUNK_BYTES, Message, MessageType
from duo_input.transfer.fileprovider_backend import FileProviderBackend
from duo_input.transfer.model import ENTRY_FILE, TransferEntry, TransferManifest


class FakeRemote:
    def __init__(self, *, deferred: bool = False) -> None:
        self.deferred = deferred
        self.replies: list[object] = []

    def publishGeneration_reply_(self, record: bytes, reply) -> None:
        json.loads(record.decode("utf-8"))
        if self.deferred:
            self.replies.append(reply)
        else:
            reply(True, None)


class FakeClient:
    def __init__(self, remote: FakeRemote) -> None:
        self._remote = remote

    def remote(self):
        return self._remote


class FakeLink:
    def __init__(self) -> None:
        self.sent: list[Message] = []

    def send(self, message: Message) -> bool:
        self.sent.append(message)
        return True


def _manifest(
    transfer_id: str = "generation-1", sizes: tuple[int, ...] = (3, 5, 7, 9, 11, 13)
) -> TransferManifest:
    return TransferManifest(
        transfer_id=transfer_id,
        entries=tuple(
            TransferEntry(
                path=f"file-{index}.bin", kind=ENTRY_FILE, size=size, mtime_ns=0
            )
            for index, size in enumerate(sizes)
        ),
        skipped=(),
        drop_effect=1,
    )


def _backend(qapp, manifest: TransferManifest | None = None, *, deferred=False):
    remote = FakeRemote(deferred=deferred)
    link = FakeLink()
    backend = FileProviderBackend(FakeClient(remote), object(), lambda _urls: None)
    backend.attach_link(link)
    manifest = manifest or _manifest()
    epoch = backend.handle_offer(manifest)
    backend.authorize(True, epoch)
    return backend, link, remote, manifest


def _open_all(backend: FileProviderBackend, manifest: TransferManifest):
    opened = [
        backend.open_fetch(manifest.transfer_id, entry_index)
        for entry_index in range(len(manifest.entries))
    ]
    return [token for token, _size in opened]


def _reply(read: Message, blob: bytes, message_type=MessageType.FILE_CHUNK) -> Message:
    return Message(message_type, dict(read.header), blob)


def test_opening_six_fetches_admits_four_and_queues_two_without_reading(qapp):
    backend, link, _remote, manifest = _backend(qapp)

    tokens = _open_all(backend, manifest)

    assert [backend.by_token[token].state for token in tokens] == [
        "requesting",
        "requesting",
        "requesting",
        "requesting",
        "queued",
        "queued",
    ]
    assert backend._active == set(tokens[:4])
    assert list(backend._queue) == tokens[4:]
    assert link.sent == []


def test_completing_fetch_admits_fifo_successor(qapp):
    backend, link, _remote, manifest = _backend(qapp)
    tokens = _open_all(backend, manifest)
    backend.pull_chunk(tokens[0])
    [read] = link.sent

    backend.handle_message(_reply(read, b"abc"))

    assert backend.by_token[tokens[0]].state == "done"
    assert backend.by_token[tokens[4]].state == "requesting"
    assert backend.by_token[tokens[5]].state == "queued"
    assert tokens[4] in backend._active
    assert list(backend._queue) == [tokens[5]]


def test_fetches_keep_independent_offset_read_id_and_expected(qapp):
    manifest = _manifest(sizes=(MAX_FILE_CHUNK_BYTES + 2, 7))
    backend, link, _remote, manifest = _backend(qapp, manifest)
    first, second = _open_all(backend, manifest)

    backend.pull_chunk(first)
    backend.pull_chunk(second)
    first_read, second_read = link.sent
    second_before = backend.by_token[second]
    second_snapshot = (
        second_before.offset,
        second_before.read_id,
        second_before.expected,
        second_before.state,
    )

    backend.handle_message(_reply(first_read, b"a" * MAX_FILE_CHUNK_BYTES))

    first_fetch = backend.by_token[first]
    second_fetch = backend.by_token[second]
    assert first_fetch.offset == MAX_FILE_CHUNK_BYTES
    assert first_fetch.read_id is None
    assert first_fetch.expected == 0
    assert first_fetch.state == "requesting"
    assert (
        second_fetch.offset,
        second_fetch.read_id,
        second_fetch.expected,
        second_fetch.state,
    ) == second_snapshot
    assert first_read.header["read_id"] != second_read.header["read_id"]


def test_pull_chunk_sends_exactly_one_read_for_that_fetch(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)))
    [token] = _open_all(backend, manifest)

    backend.pull_chunk(token)
    backend.pull_chunk(token)

    [read] = link.sent
    fetch = backend.by_token[token]
    assert read.type is MessageType.FILE_READ
    assert read.header == {
        "transfer_id": manifest.transfer_id,
        "entry_index": 0,
        "offset": 0,
        "length": 3,
        "read_id": fetch.read_id,
    }
    assert backend.by_read_id == {fetch.read_id: fetch}
    assert fetch.state == "receiving"


def test_open_fetch_requires_the_acked_active_generation(qapp):
    backend, link, remote, manifest = _backend(qapp, deferred=True)

    with pytest.raises(ValueError):
        backend.open_fetch(manifest.transfer_id, 0)
    with pytest.raises(ValueError):
        backend.open_fetch("another-generation", 0)
    assert link.sent == []

    [reply] = remote.replies
    reply(True, None)
    token, size = backend.open_fetch(manifest.transfer_id, 0)
    assert size == 3
    assert backend.by_token[token].generation_id == manifest.transfer_id


def test_unindexed_chunk_and_error_do_not_mutate_any_fetch(qapp):
    manifest = _manifest(sizes=(3, 5))
    backend, link, _remote, manifest = _backend(qapp, manifest)
    first, second = _open_all(backend, manifest)
    backend.pull_chunk(first)
    backend.pull_chunk(second)
    before = {
        token: (
            backend.by_token[token].offset,
            backend.by_token[token].read_id,
            backend.by_token[token].expected,
            backend.by_token[token].state,
        )
        for token in (first, second)
    }
    unknown = dict(link.sent[0].header, read_id=999_999)

    backend.handle_message(Message(MessageType.FILE_CHUNK, unknown, b"abc"))
    backend.handle_message(
        Message(MessageType.FILE_ERROR, dict(unknown, reason="source_missing"), b"")
    )

    assert {
        token: (
            backend.by_token[token].offset,
            backend.by_token[token].read_id,
            backend.by_token[token].expected,
            backend.by_token[token].state,
        )
        for token in (first, second)
    } == before


def test_boolean_response_coordinates_do_not_match_integer_fetch_coordinates(qapp):
    manifest = _manifest(sizes=(3,))
    backend, link, _remote, manifest = _backend(qapp, manifest)
    [token] = _open_all(backend, manifest)
    backend.pull_chunk(token)
    [read] = link.sent
    malformed = dict(read.header, entry_index=False, offset=False)

    backend.handle_message(Message(MessageType.FILE_CHUNK, malformed, b"abc"))

    fetch = backend.by_token[token]
    assert fetch.offset == 0
    assert fetch.read_id == read.header["read_id"]
    assert fetch.state == "receiving"


def test_backend_has_no_shared_sequential_cursor_fields(qapp):
    backend, _link, _remote, _manifest_value = _backend(qapp)
    forbidden = {"_cursor", "_offset", "_read_id"}

    assert forbidden.isdisjoint(FileProviderBackend.__dict__)
    assert forbidden.isdisjoint(vars(backend))
