"""Task 9: per-fetch File Provider scheduling without shared cursors."""

from __future__ import annotations

import json
import sys

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


class RejectingLink(FakeLink):
    def send(self, message: Message) -> bool:
        self.sent.append(message)
        return False


class RaisingLink(FakeLink):
    def send(self, message: Message) -> bool:
        self.sent.append(message)
        raise OSError("link closed")


class SynchronouslyCompletingLink(FakeLink):
    def __init__(self, blob: bytes) -> None:
        super().__init__()
        self.blob = blob
        self.backend: FileProviderBackend | None = None

    def send(self, message: Message) -> bool:
        self.sent.append(message)
        assert self.backend is not None
        self.backend.handle_message(_reply(message, self.blob))
        return False


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


def _backend(
    qapp,
    manifest: TransferManifest | None = None,
    *,
    deferred=False,
    link: FakeLink | None = None,
):
    remote = FakeRemote(deferred=deferred)
    link = link or FakeLink()
    backend = FileProviderBackend(FakeClient(remote), object(), lambda _urls: None)
    backend.attach_link(link)
    if isinstance(link, SynchronouslyCompletingLink):
        link.backend = backend
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


class Collector:
    """Captures pull_chunk reply callbacks: (blob, eof, error). A production
    ``pullChunk`` always carries a reply block; the windowed backend only
    consumes a buffered range when a consumer reply is parked, so completion
    tests must pass one (unlike the old WINDOW=1 backend, where a chunk advanced
    the fetch regardless)."""

    def __init__(self) -> None:
        self.calls: list[tuple[bytes | None, bool, object]] = []

    def __call__(self, blob, eof, error) -> None:
        self.calls.append((blob, eof, error))


def _range_ids(fetch) -> set[int]:
    return {r.read_id for r in fetch.ranges.values()}


def _snap(fetch):
    """Windowed replacement for the old (offset, read_id, expected, state)
    tuple: the fetch's observable per-stream state."""
    return (
        fetch.consume_offset,
        fetch.plan_offset,
        frozenset(_range_ids(fetch)),
        fetch.state,
    )


def _error_code(error) -> int:
    """DuoFPErrorDomain code from either a real NSError (darwin) or the
    ``RuntimeError('DuoFPErrorDomain:<n>')`` fallback ``_xpc_error`` returns
    off darwin. Asserting ``error.code()`` directly makes a test darwin-only
    without saying so: off darwin it does not fail on the wrong code, it
    fails with AttributeError on every code alike."""
    code = getattr(error, "code", None)
    if callable(code):
        return int(code())
    return int(str(error).rsplit(":", 1)[1])


#: What ``_error_domain`` should return for an error ``_xpc_error`` produced.
#: Deliberately platform-dependent rather than one constant the fallback is
#: massaged into: off darwin the error really does not carry the reverse-DNS
#: domain, and pretending it does would turn the assertion into one that
#: passes no matter what ``_xpc_error`` builds.
_DUOFP_ERROR_DOMAIN = (
    "com.duoinput.configurator.fileprovider.error"
    if sys.platform == "darwin"
    else "DuoFPErrorDomain"
)


def _error_domain(error) -> str:
    """The error's DuoFPErrorDomain marker, in whichever form the platform
    produced: ``NSError.domain()`` on darwin, and off darwin the prefix of
    the fallback's ``'DuoFPErrorDomain:<n>'`` message - the only domain it
    carries. Compare against ``_DUOFP_ERROR_DOMAIN``."""
    domain = getattr(error, "domain", None)
    if callable(domain):
        return str(domain())
    return str(error).rsplit(":", 1)[0]


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
    first_fetch = backend.by_token[tokens[0]]
    c = Collector()
    backend.pull_chunk(tokens[0], c)  # 3-byte file -> exactly one range
    [read] = link.sent

    backend.handle_message(_reply(read, b"abc"))

    assert first_fetch.state == "done"
    assert c.calls[-1] == (b"abc", True, None)
    assert backend.by_token[tokens[4]].state == "requesting"
    assert backend.by_token[tokens[5]].state == "queued"
    assert tokens[4] in backend._active
    assert list(backend._queue) == [tokens[5]]


def test_fetches_keep_independent_cursors_and_ranges(qapp):
    # First file spans two ranges (>1 MiB); second is tiny. Each fetch owns its
    # own consume/plan cursors and read_ids - no shared sequential state.
    manifest = _manifest(sizes=(MAX_FILE_CHUNK_BYTES + 2, 7))
    backend, link, _remote, manifest = _backend(qapp, manifest)
    first, second = _open_all(backend, manifest)

    cf, cs = Collector(), Collector()
    backend.pull_chunk(first, cf)
    backend.pull_chunk(second, cs)
    first_fetch = backend.by_token[first]
    second_fetch = backend.by_token[second]

    # Independent read_ids, no overlap.
    assert _range_ids(first_fetch).isdisjoint(_range_ids(second_fetch))
    second_snapshot = (
        second_fetch.consume_offset,
        second_fetch.plan_offset,
        _range_ids(second_fetch),
        second_fetch.state,
    )

    # Deliver the first file's first range; only `first` advances.
    first_r0 = next(m for m in link.sent if m.header["read_id"] in _range_ids(first_fetch)
                    and m.header["offset"] == 0)
    backend.handle_message(_reply(first_r0, b"a" * MAX_FILE_CHUNK_BYTES))

    assert first_fetch.consume_offset == MAX_FILE_CHUNK_BYTES
    assert cf.calls[-1] == (b"a" * MAX_FILE_CHUNK_BYTES, False, None)
    assert (
        second_fetch.consume_offset,
        second_fetch.plan_offset,
        _range_ids(second_fetch),
        second_fetch.state,
    ) == second_snapshot


def test_pull_chunk_sends_window_reads_and_rejects_a_concurrent_pull(qapp):
    # A 3-byte file needs exactly one range; the window naturally shrinks to it.
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)))
    [token] = _open_all(backend, manifest)
    fetch = backend.by_token[token]

    c1 = Collector()
    backend.pull_chunk(token, c1)
    # A second concurrent pull while one reply is parked is a protocol error and
    # issues no extra read (the consumer must pull serially).
    c2 = Collector()
    backend.pull_chunk(token, c2)

    [read] = link.sent
    read_id = next(iter(_range_ids(fetch)))
    assert read.type is MessageType.FILE_READ
    assert read.header == {
        "transfer_id": manifest.transfer_id,
        "entry_index": 0,
        "offset": 0,
        "length": 3,
        "read_id": read_id,
    }
    assert backend.by_read_id == {read_id: fetch}
    assert fetch.state == "requesting"
    assert len(c2.calls) == 1 and c2.calls[0][2] is not None  # rejected once


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
    backend.pull_chunk(first, Collector())
    backend.pull_chunk(second, Collector())
    before = {token: _snap(backend.by_token[token]) for token in (first, second)}
    unknown = dict(link.sent[0].header, read_id=999_999)

    backend.handle_message(Message(MessageType.FILE_CHUNK, unknown, b"abc"))
    backend.handle_message(
        Message(MessageType.FILE_ERROR, dict(unknown, reason="source_missing"), b"")
    )

    assert {
        token: _snap(backend.by_token[token]) for token in (first, second)
    } == before


def test_boolean_response_coordinates_do_not_match_integer_fetch_coordinates(qapp):
    manifest = _manifest(sizes=(3,))
    backend, link, _remote, manifest = _backend(qapp, manifest)
    [token] = _open_all(backend, manifest)
    backend.pull_chunk(token, Collector())
    [read] = link.sent
    malformed = dict(read.header, entry_index=False, offset=False)

    backend.handle_message(Message(MessageType.FILE_CHUNK, malformed, b"abc"))

    fetch = backend.by_token[token]
    assert fetch.consume_offset == 0
    assert _range_ids(fetch) == {read.header["read_id"]}
    assert fetch.state == "requesting"


def test_zero_size_fetches_complete_and_release_slots_without_reading(qapp):
    manifest = _manifest(sizes=(0, 0, 0, 0, 3, 5))
    backend, link, _remote, manifest = _backend(qapp, manifest)
    tokens = _open_all(backend, manifest)
    zero_size_fetches = [backend.by_token[token] for token in tokens[:4]]

    for token in tokens[:4]:
        backend.pull_chunk(token, Collector())

    assert [fetch.state for fetch in zero_size_fetches] == [
        "done",
        "done",
        "done",
        "done",
    ]
    assert [backend.by_token[token].state for token in tokens[4:]] == [
        "requesting",
        "requesting",
    ]
    assert backend._active == set(tokens[4:])
    assert list(backend._queue) == []
    assert link.sent == []


@pytest.mark.parametrize("link_type", [RejectingLink, RaisingLink])
def test_send_failure_fails_only_that_fetch_and_admits_fifo_successor(qapp, link_type):
    link = link_type()
    backend, _link, _remote, manifest = _backend(qapp, link=link)
    tokens = _open_all(backend, manifest)
    failed = backend.by_token[tokens[0]]

    backend.pull_chunk(tokens[0], Collector())

    assert failed.state == "failed"
    assert failed.consume_offset == 0
    assert failed.ranges == {}
    assert backend.by_read_id == {}
    assert tokens[0] not in backend._active
    assert backend.by_token[tokens[4]].state == "requesting"
    assert list(backend._queue) == [tokens[5]]


def test_false_send_result_does_not_undo_synchronous_completion(qapp):
    link = SynchronouslyCompletingLink(b"abc")
    manifest = _manifest(sizes=(3,))
    backend, _link, _remote, manifest = _backend(qapp, manifest, link=link)
    [token] = _open_all(backend, manifest)
    fetch = backend.by_token[token]

    backend.pull_chunk(token, Collector())

    assert fetch.state == "done"
    assert fetch.consume_offset == 3
    assert fetch.ranges == {}
    assert backend.by_read_id == {}
    assert backend._active == set()


@pytest.mark.parametrize("blob", [b"", b"abcd"])
def test_malformed_chunk_fails_exact_fetch_without_advancing_offset(qapp, blob):
    manifest = _manifest(sizes=(3, 5, 7, 9, 11))
    backend, link, _remote, manifest = _backend(qapp, manifest)
    tokens = _open_all(backend, manifest)
    backend.pull_chunk(tokens[0], Collector())
    [read] = link.sent
    failed = backend.by_token[tokens[0]]

    backend.handle_message(_reply(read, blob))

    assert failed.state == "failed"
    assert failed.consume_offset == 0
    assert failed.ranges == {}
    assert backend.by_read_id == {}
    assert backend.by_token[tokens[4]].state == "requesting"


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("transfer_id", "different-generation"),
        ("entry_index", 1),
        ("offset", 1),
    ],
)
def test_response_coordinate_mismatch_does_not_mutate_indexed_fetch(
    qapp, field, bad_value
):
    manifest = _manifest(sizes=(3, 5))
    backend, link, _remote, manifest = _backend(qapp, manifest)
    first, _second = _open_all(backend, manifest)
    backend.pull_chunk(first, Collector())
    [read] = link.sent
    mismatched = dict(read.header, **{field: bad_value})

    backend.handle_message(Message(MessageType.FILE_CHUNK, mismatched, b"abc"))

    fetch = backend.by_token[first]
    assert fetch.consume_offset == 0
    assert _range_ids(fetch) == {read.header["read_id"]}
    assert fetch.ranges[0].length == 3
    assert fetch.state == "requesting"
    assert backend.by_read_id == {read.header["read_id"]: fetch}


def test_stale_known_read_id_does_not_mutate_another_fetch(qapp):
    manifest = _manifest(sizes=(3, 5))
    backend, link, _remote, manifest = _backend(qapp, manifest)
    first, second = _open_all(backend, manifest)
    backend.pull_chunk(first, Collector())
    backend.pull_chunk(second, Collector())
    first_read, second_read = link.sent
    # Receiving first's chunk retires its read_id from by_read_id (whether or
    # not it is consumed), so reusing it on second's header is a stale/unknown id.
    backend.handle_message(_reply(first_read, b"abc"))
    second_before = backend.by_token[second]
    snapshot = _snap(second_before)
    stale_for_second = dict(second_read.header, read_id=first_read.header["read_id"])

    backend.handle_message(Message(MessageType.FILE_CHUNK, stale_for_second, b"abcde"))

    second_fetch = backend.by_token[second]
    assert _snap(second_fetch) == snapshot
    assert backend.by_read_id == {second_read.header["read_id"]: second_fetch}


def test_backend_has_no_shared_sequential_cursor_fields(qapp):
    backend, _link, _remote, _manifest_value = _backend(qapp)
    forbidden = {"_cursor", "_offset", "_read_id"}

    assert forbidden.isdisjoint(FileProviderBackend.__dict__)
    assert forbidden.isdisjoint(vars(backend))


def test_by_token_does_not_retain_a_done_or_a_failed_fetch(qapp):
    """Task 19 fix: by_token must drop a fetch the instant it settles
    (DONE/FAILED/CANCELLED alike) - previously only CANCELLED was popped
    (cancel_fetch's own explicit pop), so a long-lived extension leaked one
    dict entry per completed or failed fetch forever. See _finish_fetch."""
    manifest = _manifest(sizes=(3, 5))
    backend, link, _remote, manifest = _backend(qapp, manifest)
    done_token, failed_token = _open_all(backend, manifest)

    # Settle done_token to DONE via a full, well-formed chunk.
    backend.pull_chunk(done_token, Collector())
    [done_read] = link.sent
    backend.handle_message(_reply(done_read, b"abc"))
    assert done_token not in backend.by_token

    # Settle failed_token to FAILED via a malformed (oversized) chunk.
    backend.pull_chunk(failed_token, Collector())
    [failed_read] = [m for m in link.sent if m is not done_read]
    backend.handle_message(_reply(failed_read, b"toolong!!"))
    assert failed_token not in backend.by_token

    assert backend.by_token == {}
