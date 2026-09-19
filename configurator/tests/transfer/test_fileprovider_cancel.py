"""Task 12: Finder cancellation over the EXISTING wire - no new message.

Cancellation is purely local (task-12 brief ruling #1): Swift's
``Progress.cancellationHandler`` calls the host proxy's ``cancelFetch(token)``
over the already-existing XPC surface (routed since Task 10 - see
``fileprovider_client.py``'s ``_KIND_TO_CALLBACK["cancel"]``), and Python's
``cancel_fetch`` drops the fetch entirely without telling the Windows sender
anything: there is no ``FILE_CANCEL`` in ``MessageType`` and this module must
never add one. A late ``FILE_CHUNK`` for a cancelled fetch's (now-forgotten)
``read_id`` is dropped exactly like any other stale/unknown ``read_id``
(Task 9/10's existing ``_pending_fetch`` path) - no write, no reply, no
error.

This extends, rather than duplicates, ``test_fileprovider_scheduler.py``
(per-fetch state machine) and ``test_fileprovider_streaming.py`` (byte
budget): it reuses their fixtures (``_backend``/``_manifest``/``_open_all``/
``_reply``) and focuses purely on the cancel-race matrix from the brief:
queued cancel, active cancel, cancel-with-chunk-in-flight (late chunk
dropped), and cancel-vs-completion (settle-once, whichever wins).
"""

from __future__ import annotations

from duo_input.clipboard.wire import MessageType
from test_fileprovider_scheduler import _backend, _manifest, _open_all, _reply


# --- ruling: no FILE_CANCEL, ever -------------------------------------------


def test_message_type_has_no_cancel_member_and_gained_no_new_member():
    """Spec forbids a FILE_CANCEL wire message (task-12 brief, failure/
    rollback condition). Pin the exact, closed set of members so that ANY
    future addition - not just one literally named FILE_CANCEL - fails this
    test and forces a conscious decision rather than a silent drift."""
    assert "FILE_CANCEL" not in MessageType.__members__
    assert set(MessageType.__members__) == {
        "HELLO",
        "OFFER",
        "FETCH",
        "CONTENT",
        "CONTENT_ERROR",
        "PING",
        "PONG",
        "PAIR_REQUEST",
        "PAIR_CONFIRM",
        "FILE_OFFER",
        "TRANSFER_BEGIN",
        "FILE_READ",
        "FILE_CHUNK",
        "FILE_ERROR",
        "TRANSFER_END",
    }


def test_cancelling_active_fetch_sends_nothing_over_the_link(qapp):
    """The cancellation itself never touches ``self._link`` - it is pure
    local bookkeeping over XPC, not a wire message."""
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)))
    [token] = _open_all(backend, manifest)

    backend.cancel_fetch(token)

    assert link.sent == []


# --- queued cancel -----------------------------------------------------------


def test_cancelling_a_queued_fetch_is_never_promoted_and_never_reads(qapp):
    backend, link, _remote, manifest = _backend(qapp)  # 6 entries, 4 slots
    tokens = _open_all(backend, manifest)
    queued = tokens[4]
    assert backend.by_token[queued].state == "queued"

    backend.cancel_fetch(queued)

    assert queued not in backend.by_token
    assert queued not in backend._active

    # Complete an active fetch: the FIFO successor must be the OTHER queued
    # token, never the cancelled one - and the cancelled one must never have
    # triggered a FILE_READ, before or after.
    backend.pull_chunk(tokens[0])
    [read] = link.sent
    backend.handle_message(_reply(read, b"abc"))

    assert backend.by_token[tokens[5]].state == "requesting"
    assert tokens[5] in backend._active
    assert all(m.header.get("entry_index") != 4 for m in link.sent)

    replies = []
    backend.pull_chunk(queued, lambda *a: replies.append(a))
    assert replies and replies[0][0] is None and replies[0][1] is False
    assert len(link.sent) == 1  # only tokens[0]'s legitimate read - never queued's


# --- active cancel (no read in flight yet) -----------------------------------


def test_cancelling_active_fetch_before_any_pull_stops_further_file_read(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)))
    [token] = _open_all(backend, manifest)
    assert backend.by_token[token].state == "requesting"

    backend.cancel_fetch(token)

    assert token not in backend.by_token
    assert token not in backend._active
    assert link.sent == []

    replies = []
    backend.pull_chunk(token, lambda *a: replies.append(a))
    assert replies == [(None, False, replies[0][2])]
    assert replies[0][2].code() == 7  # unknown/settled token -> generic error
    assert link.sent == []


# --- cancel with a chunk in flight: late FILE_CHUNK is dropped --------------


def test_cancelling_active_fetch_with_chunk_in_flight_settles_the_pull_reply(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(9,)))
    [token] = _open_all(backend, manifest)
    replies = []
    backend.pull_chunk(token, lambda *a: replies.append(a))
    [read] = link.sent
    read_id = backend.by_token[token].read_id
    assert backend.by_read_id[read_id] is backend.by_token[token]
    assert replies == []  # nothing settled yet - the read is in flight

    backend.cancel_fetch(token)

    # cancelling settles the outstanding pullChunk reply exactly once, with
    # an error - this is what lets Swift's own cancellationHandler-driven
    # completion win the settle-once race even if this reply arrives first.
    assert len(replies) == 1
    chunk, ok, error = replies[0]
    assert chunk is None and ok is False and error is not None
    assert read_id not in backend.by_read_id
    assert token not in backend.by_token


def test_late_file_chunk_after_cancel_is_dropped_silently(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(9,)))
    [token] = _open_all(backend, manifest)
    replies = []
    backend.pull_chunk(token, lambda *a: replies.append(a))
    [read] = link.sent

    backend.cancel_fetch(token)
    replies.clear()  # discard the cancel's own settle-reply; test only the late chunk

    # The real chunk shows up anyway (network already in flight) - must be a
    # complete, silent no-op: no crash, no reply, no resurrection of the
    # fetch, no partial state anywhere.
    backend.handle_message(_reply(read, b"a" * read.header["length"]))

    assert replies == []
    assert token not in backend.by_token
    assert backend.by_read_id == {}
    assert link.sent == [read]  # nothing new was ever sent for this fetch


def test_late_file_error_after_cancel_is_dropped_silently(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(9,)))
    [token] = _open_all(backend, manifest)
    backend.pull_chunk(token)
    [read] = link.sent

    backend.cancel_fetch(token)
    backend.handle_message(_file_error(read))

    assert token not in backend.by_token
    assert backend.by_read_id == {}


def _file_error(read):
    from duo_input.clipboard.wire import Message

    return Message(MessageType.FILE_ERROR, dict(read.header, reason="source_missing"), b"")


# --- cancel-vs-completion race: settle-once, whichever wins ------------------


def test_completion_winning_the_race_makes_a_later_cancel_a_no_op(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)))
    [token] = _open_all(backend, manifest)
    replies = []
    backend.pull_chunk(token, lambda *a: replies.append(a))
    [read] = link.sent

    backend.handle_message(_reply(read, b"abc"))  # completion wins
    assert backend.by_token[token].state == "done"
    assert len(replies) == 1

    backend.cancel_fetch(token)  # loser: must be a total no-op

    assert backend.by_token[token].state == "done"  # unchanged, not clobbered
    assert len(replies) == 1  # no second settle


def test_cancel_winning_the_race_makes_a_later_completion_a_no_op(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)))
    [token] = _open_all(backend, manifest)
    replies = []
    backend.pull_chunk(token, lambda *a: replies.append(a))
    [read] = link.sent

    backend.cancel_fetch(token)  # cancel wins
    assert len(replies) == 1
    assert token not in backend.by_token

    # loser: the real chunk shows up anyway - must be a complete no-op
    backend.handle_message(_reply(read, b"abc"))

    assert len(replies) == 1  # no second settle
    assert token not in backend.by_token


# --- idempotence / unknown tokens --------------------------------------------


def test_cancel_of_unknown_token_never_crashes(qapp):
    backend, link, _remote, _manifest_value = _backend(qapp)

    backend.cancel_fetch("does-not-exist")  # must not raise

    assert link.sent == []


def test_cancel_twice_on_the_same_token_is_idempotent(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)))
    [token] = _open_all(backend, manifest)

    backend.cancel_fetch(token)
    backend.cancel_fetch(token)  # second call: no-op, no crash, no double-free

    assert token not in backend.by_token


# --- interaction with Task 11's byte-budget queue ----------------------------


def test_cancelling_a_fetch_holding_budget_frees_it_for_the_next_queued_pull(
    qapp, monkeypatch
):
    import duo_input.transfer.fileprovider_backend as fp_backend

    monkeypatch.setattr(fp_backend, "MAX_FILE_CHUNK_BYTES", 3)
    monkeypatch.setattr(fp_backend, "MAX_TOTAL_BUFFERED_BYTES", 3)
    manifest = _manifest(sizes=(9, 9))
    backend, link, _remote, manifest = _backend(qapp, manifest)
    first, second = _open_all(backend, manifest)

    first_replies, second_replies = [], []
    backend.pull_chunk(first, lambda *a: first_replies.append(a))
    backend.pull_chunk(second, lambda *a: second_replies.append(a))

    assert len(link.sent) == 1, "only one 3-byte read fits the 3-byte budget"
    assert list(backend._pull_queue) == [second]
    assert second_replies == []

    backend.cancel_fetch(first)  # frees the only budget slot it held

    assert len(link.sent) == 2, "cancelling the budget holder admits the FIFO successor"
    assert list(backend._pull_queue) == []
    assert first not in backend.by_token


def test_cancelling_a_fetch_still_in_the_pull_queue_drops_it_without_reading(
    qapp, monkeypatch
):
    import duo_input.transfer.fileprovider_backend as fp_backend

    monkeypatch.setattr(fp_backend, "MAX_FILE_CHUNK_BYTES", 3)
    monkeypatch.setattr(fp_backend, "MAX_TOTAL_BUFFERED_BYTES", 3)
    manifest = _manifest(sizes=(9, 9))
    backend, link, _remote, manifest = _backend(qapp, manifest)
    first, second = _open_all(backend, manifest)
    backend.pull_chunk(first, lambda *a: None)
    backend.pull_chunk(second, lambda *a: None)
    assert list(backend._pull_queue) == [second]

    backend.cancel_fetch(second)  # cancel the one still parked in the byte queue

    assert second not in backend.by_token
    # Freeing the (never-consumed) budget of `second` changes nothing - it
    # held none - and it must never be admitted from the pull queue now.
    backend.handle_message(_reply(link.sent[0], b"abc"))
    assert len(link.sent) == 1, "the cancelled fetch must never get a FILE_READ"
    assert list(backend._pull_queue) == []
