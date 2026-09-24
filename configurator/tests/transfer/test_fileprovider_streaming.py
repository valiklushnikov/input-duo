"""Task 11: NSProgress/streaming coverage across sizes, plus the global
byte-budget backpressure gate (``MAX_TOTAL_BUFFERED_BYTES``).

This extends, rather than duplicates, Task 9/10's per-fetch scheduler
(``test_fileprovider_scheduler.py``): the one-outstanding-read-per-fetch
invariant and the ``MAX_ACTIVE_FETCHES`` slot gate are exercised there. Here
the focus is (a) real multi-chunk reassembly across representative sizes
(zero, exactly one chunk, several chunks) and (b) proving the *sum of
outstanding reads' expected sizes* across ALL active fetches never exceeds
``MAX_TOTAL_BUFFERED_BYTES``, regardless of how large the transferred files
are - the failure this guards against is "memory grows with file size".

Chunks are delivered by reference (``reply(message.blob)``) - the backend
itself never accumulates bytes (see fileprovider_backend module docstring),
so "buffered bytes" here means the sum of *requested* (``expected``) sizes
for reads currently in flight, which is exactly what the budget bounds.
"""

from __future__ import annotations

import duo_input.transfer.fileprovider_backend as fp_backend
from duo_input.clipboard.wire import MAX_FILE_CHUNK_BYTES
from test_fileprovider_scheduler import (
    _backend,
    _error_code,
    _manifest,
    _open_all,
    _range_ids,
    _reply,
)


def test_zero_byte_fetch_completes_without_any_read(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(0,)))
    [token] = _open_all(backend, manifest)
    fetch = backend.by_token[token]
    replies = []

    backend.pull_chunk(token, lambda *a: replies.append(a))

    assert replies == [(b"", True, None)]
    assert link.sent == []
    assert fetch.state == "done"


def test_exactly_one_chunk_ceiling_completes_in_a_single_read(qapp):
    manifest = _manifest(sizes=(MAX_FILE_CHUNK_BYTES,))
    backend, link, _remote, manifest = _backend(qapp, manifest)
    [token] = _open_all(backend, manifest)
    fetch = backend.by_token[token]
    replies = []

    backend.pull_chunk(token, lambda *a: replies.append(a))
    [read] = link.sent
    assert read.header == {
        "transfer_id": manifest.transfer_id,
        "entry_index": 0,
        "offset": 0,
        "length": MAX_FILE_CHUNK_BYTES,
        "read_id": next(iter(_range_ids(fetch))),
    }
    backend.handle_message(_reply(read, b"a" * MAX_FILE_CHUNK_BYTES))

    assert len(replies) == 1
    chunk, eof, error = replies[0]
    assert len(chunk) == MAX_FILE_CHUNK_BYTES
    assert eof is True
    assert error is None
    assert fetch.state == "done"


def test_multi_chunk_file_reassembles_with_increasing_offsets(qapp):
    total = 2 * MAX_FILE_CHUNK_BYTES + 5
    manifest = _manifest(sizes=(total,))
    backend, link, _remote, manifest = _backend(qapp, manifest)
    [token] = _open_all(backend, manifest)

    reassembled = bytearray()
    replies: list = []
    # The window issues all three ranges up front (3 < WINDOW); deliver them in
    # offset order to the serial parked reply, re-pulling after each delivery.
    backend.pull_chunk(token, lambda *a: replies.append(a))
    eof = False
    guard = 0
    while not eof and guard < 100:
        guard += 1
        pending = sorted(
            (m for m in link.sent if m.header["read_id"] in backend.by_read_id),
            key=lambda m: m.header["offset"],
        )
        read = pending[0]  # lowest in-flight offset == the consume cursor
        blob = bytes([read.header["offset"] // MAX_FILE_CHUNK_BYTES % 256]) * read.header["length"]
        before = len(replies)
        backend.handle_message(_reply(read, blob))
        assert len(replies) == before + 1
        chunk, eof, error = replies[-1]
        assert error is None
        reassembled.extend(chunk)
        if not eof:
            backend.pull_chunk(token, lambda *a: replies.append(a))

    assert len(reassembled) == total
    assert [m.header["offset"] for m in link.sent] == [
        0,
        MAX_FILE_CHUNK_BYTES,
        2 * MAX_FILE_CHUNK_BYTES,
    ]
    assert [m.header["length"] for m in link.sent] == [
        MAX_FILE_CHUNK_BYTES,
        MAX_FILE_CHUNK_BYTES,
        5,
    ]


def test_oversized_reply_is_a_protocol_error(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)))
    [token] = _open_all(backend, manifest)
    fetch = backend.by_token[token]
    replies = []
    backend.pull_chunk(token, lambda *a: replies.append(a))
    [read] = link.sent

    backend.handle_message(_reply(read, b"toolong"))

    assert len(replies) == 1
    chunk, ok, error = replies[0]
    assert chunk is None and ok is False
    assert _error_code(error) == 7
    assert fetch.state == "failed"


def test_truncated_reply_is_a_protocol_error(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)))
    [token] = _open_all(backend, manifest)
    fetch = backend.by_token[token]
    replies = []
    backend.pull_chunk(token, lambda *a: replies.append(a))
    [read] = link.sent

    backend.handle_message(_reply(read, b"a"))

    assert len(replies) == 1
    chunk, ok, error = replies[0]
    assert chunk is None and ok is False
    assert _error_code(error) == 7
    assert fetch.state == "failed"


def test_pull_chunk_queues_when_it_would_exceed_the_byte_budget_even_with_free_active_slots(
    qapp, monkeypatch
):
    """Distinct from the MAX_ACTIVE_FETCHES slot gate (test_fileprovider_
    scheduler.py): all four fetches fit the slot gate, but the byte budget
    (5) only fits one 3-byte outstanding read at a time (3+3=6 > 5) - so
    three of the four ``pull_chunk`` calls must be queued rather than
    immediately sent, despite every fetch already being an admitted "active"
    slot."""
    monkeypatch.setattr(fp_backend, "MAX_FILE_CHUNK_BYTES", 3)
    monkeypatch.setattr(fp_backend, "MAX_TOTAL_BUFFERED_BYTES", 5)
    manifest = _manifest(sizes=(9, 9, 9, 9))
    backend, link, _remote, manifest = _backend(qapp, manifest)
    tokens = _open_all(backend, manifest)
    assert backend._active == set(tokens)

    replies = [[] for _ in tokens]
    for token, bucket in zip(tokens, replies):
        backend.pull_chunk(token, lambda *a, b=bucket: b.append(a))

    # Only one 3-byte read fits the 5-byte budget; every other range parks.
    assert len(link.sent) == 1, "only one 3-byte read fits the 5-byte budget"
    assert backend._outstanding_bytes() <= 5
    assert len(backend._pull_queue) >= 1, "the rest are budget-parked"
    assert all(bucket == [] for bucket in replies), "nothing settled yet"

    backend.handle_message(_reply(link.sent[0], b"abc"))

    # The delivered chunk frees budget; exactly one more read is admitted.
    assert len(replies[0]) == 1 and replies[0][0][:2] == (b"abc", False)
    assert len(link.sent) == 2, "freeing the budget admits exactly one more read"
    assert backend._outstanding_bytes() <= 5


def test_admitting_from_the_active_slot_queue_still_respects_the_byte_budget(
    qapp, monkeypatch
):
    """A fetch promoted out of the MAX_ACTIVE_FETCHES slot queue (Task 9)
    must still pass through the byte-budget gate rather than bypassing it -
    the two admission gates are independent and both apply."""
    monkeypatch.setattr(fp_backend, "MAX_ACTIVE_FETCHES", 1)
    monkeypatch.setattr(fp_backend, "MAX_FILE_CHUNK_BYTES", 3)
    monkeypatch.setattr(fp_backend, "MAX_TOTAL_BUFFERED_BYTES", 3)
    manifest = _manifest(sizes=(3, 9))
    backend, link, _remote, manifest = _backend(qapp, manifest)
    first, second = _open_all(backend, manifest)
    assert backend._active == {first}
    assert list(backend._queue) == [second]

    first_replies, second_replies = [], []
    backend.pull_chunk(first, lambda *a: first_replies.append(a))
    backend.pull_chunk(second, lambda *a: second_replies.append(a))
    [read] = link.sent

    backend.handle_message(_reply(read, b"abc"))

    # `second` is promoted into the active slot here, but the lone read still
    # in flight for it must go through `_admit_pull`/the budget queue exactly
    # like any other pull - not skip straight to `_request_chunk`.
    assert backend.by_token[second].state in ("requesting", "receiving")
    assert backend._outstanding_bytes() <= 3


def test_outstanding_bytes_stay_bounded_regardless_of_total_file_size(qapp, monkeypatch):
    """The failure this guards against: outstanding/buffered bytes scaling
    with total file size. Four fetches share a budget that only fits two
    4-byte reads in flight at once; the loop below drives all four to
    completion for two very different total sizes while asserting the
    budget is never exceeded - the bound must not depend on file size."""
    monkeypatch.setattr(fp_backend, "MAX_FILE_CHUNK_BYTES", 4)
    monkeypatch.setattr(fp_backend, "MAX_TOTAL_BUFFERED_BYTES", 8)

    for total_size in (20, 400):
        manifest = _manifest(sizes=(total_size,) * 4)
        backend, link, _remote, manifest = _backend(qapp, manifest)
        tokens = _open_all(backend, manifest)
        assert backend._active == set(tokens)

        received = {token: 0 for token in tokens}
        boxes: dict[str, list] = {token: [] for token in tokens}
        done: set[str] = set()
        peak_outstanding = 0

        guard = 0
        while len(done) < len(tokens):
            guard += 1
            assert guard < 50_000, "livelock: a fetch never progressed"
            # Ensure every unfinished fetch has a serial consumer reply parked.
            for token in tokens:
                if token in done:
                    continue
                fetch = backend.by_token.get(token)
                if fetch is None:
                    done.add(token)
                    continue
                if fetch.reply is None:
                    boxes[token] = []
                    backend.pull_chunk(
                        token, lambda *a, t=token: boxes[t].append(a)
                    )
            # The budget bound must hold at every point, independent of size.
            outstanding = backend._outstanding_bytes()
            peak_outstanding = max(peak_outstanding, outstanding)
            assert outstanding <= 8, "byte budget exceeded"
            # Deliver each in-flight range currently at its fetch's consume
            # cursor (that is what a parked reply is waiting on).
            for token in tokens:
                if token in done:
                    continue
                fetch = backend.by_token.get(token)
                if fetch is None:
                    done.add(token)
                    continue
                rng = fetch.ranges.get(fetch.consume_offset)
                if rng is not None and rng.state is fp_backend.RangeState.IN_FLIGHT:
                    [read] = [
                        m for m in link.sent if m.header["read_id"] == rng.read_id
                    ]
                    backend.handle_message(_reply(read, b"x" * rng.length))
            # Collect deliveries.
            for token in tokens:
                while boxes[token]:
                    chunk, eof, error = boxes[token].pop(0)
                    assert error is None
                    received[token] += len(chunk)
                    if eof:
                        done.add(token)

        assert peak_outstanding <= 8
        assert all(count == total_size for count in received.values())
