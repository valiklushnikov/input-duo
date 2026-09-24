"""Task 14: disconnect / reconnect / error mapping.

Covers the Python half of spec §16's error-mapping table plus the two
disconnect scenarios that have no place in test_fileprovider_scheduler.py or
test_fileprovider_cancel.py: a real ``link.disconnected`` failing every
active fetch locally (ruling #4 - no new wire message, never a FILE_ERROR
sent BY us), and "host down" (no peer link at all) rejecting new fetches with
NotConnected rather than silently admitting them.

Every DuoFPErrorDomain code this backend can produce is exercised at least
once so a future change to the reason->code table or the fail-all path shows
up here first, not as a mystery generic NSError three layers away in Finder.

Reuses test_fileprovider_scheduler.py's fixtures (``_backend``/``_manifest``/
``_open_all``/``_reply``/``FakeLink``), per the existing convention
(test_fileprovider_cancel.py does the same).
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Signal

from duo_input.clipboard.wire import Message, MessageType
from test_fileprovider_scheduler import (
    _DUOFP_ERROR_DOMAIN,
    _backend,
    _error_code,
    _error_domain,
    _manifest,
    _open_all,
    _reply,
)


class DisconnectableLink(QObject):
    """A ``FakeLink`` with a REAL Qt ``disconnected`` signal.

    Plain ``PyQt``/``PySide`` signals, not a hand-rolled callback list, so
    ``attach_link``'s ``disconnected.connect(...)`` exercises the exact same
    machinery a real ``PeerLink`` (``clipboard/peer.py``) would.
    """

    disconnected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[Message] = []

    def send(self, message: Message) -> bool:
        self.sent.append(message)
        return True


def _file_error(read: Message, reason: str) -> Message:
    return Message(MessageType.FILE_ERROR, dict(read.header, reason=reason), b"")


# --- link disconnect: local fail-all with PeerLost -----------------------


def test_disconnect_fails_every_active_fetch_with_peer_lost(qapp):
    link = DisconnectableLink()
    manifest = _manifest(sizes=(3, 5, 7, 9, 11, 13))  # 4 active + 2 queued
    backend, link, _remote, manifest = _backend(qapp, manifest, link=link)
    tokens = _open_all(backend, manifest)
    fetches = {token: backend.by_token[token] for token in tokens}
    replies = {token: [] for token in tokens}
    for token in tokens:
        backend.pull_chunk(token, lambda *a, t=token: replies[t].append(a))
    # Only the 4 admitted (active) fetches actually sent a FILE_READ and have
    # a reply pending; the 2 queued ones never got that far.
    assert len(link.sent) == 4

    link.disconnected.emit("peer socket closed")

    # Every fetch - active AND queued - is settled exactly once. The 4 that
    # were actually mid-conversation with the peer get PeerLost; freeing
    # their slots promotes the 2 formerly-queued ones, which then discover
    # there is no link at all (self._link is already cleared) and settle as
    # NotConnected instead - both are "the connection is gone" per spec §16
    # and both map to the same retriable serverUnreachable on the Swift side
    # (ErrorMappingTests.swift), so either is correct here.
    for token in tokens:
        assert len(replies[token]) == 1, token
        chunk, ok, error = replies[token][0]
        assert chunk is None and ok is False
        assert _error_domain(error) == _DUOFP_ERROR_DOMAIN
        assert _error_code(error) in (3, 8)  # DuoFPErrorPeerLost / DuoFPErrorNotConnected
        assert fetches[token].state == "failed"
    assert [_error_code(replies[t][0][2]) for t in tokens[:4]] == [3, 3, 3, 3]


def test_disconnect_is_local_only_no_wire_message_sent(qapp):
    """Ruling #4: fail-all never talks to the (now-dead) peer."""
    link = DisconnectableLink()
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)), link=link)
    [token] = _open_all(backend, manifest)
    backend.pull_chunk(token, lambda *a: None)
    sent_before = list(link.sent)

    link.disconnected.emit("interrupted")

    assert link.sent == sent_before  # nothing new was ever sent


def test_disconnect_clears_the_link_so_new_fetches_see_not_connected(qapp):
    link = DisconnectableLink()
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3, 5)), link=link)

    link.disconnected.emit("invalidated")

    replies = []
    backend.open_fetch(manifest.transfer_id, 0, lambda *a: replies.append(a))
    assert replies == [(None, None, replies[0][2])]
    assert _error_code(replies[0][2]) == 8  # DuoFPErrorNotConnected


def test_disconnect_twice_is_a_harmless_no_op(qapp):
    link = DisconnectableLink()
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)), link=link)
    [token] = _open_all(backend, manifest)
    fetch = backend.by_token[token]
    backend.pull_chunk(token, lambda *a: None)

    link.disconnected.emit("first")
    link.disconnected.emit("second")  # must not raise, must not re-settle

    assert fetch.state == "failed"


def test_reattaching_a_new_link_after_disconnect_stops_listening_to_the_old_one(qapp):
    """Re-attach (reconnect) must not leave the old link's disconnected
    signal still wired to this backend - see _release_link_lost_slot."""
    first = DisconnectableLink()
    backend, first, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)), link=first)
    [token] = _open_all(backend, manifest)
    backend.pull_chunk(token, lambda *a: None)

    second = DisconnectableLink()
    backend.attach_link(second)

    first.disconnected.emit("stale")  # must be ignored - no longer "the" link

    assert backend.by_token[token].state == "requesting"  # untouched


# --- host-down: open_fetch / pull_chunk reply NotConnected -----------------


def test_open_fetch_without_any_link_replies_not_connected(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)))
    backend._link = None  # never attached / already gone - the "host down" case

    replies = []
    result = backend.open_fetch(manifest.transfer_id, 0, lambda *a: replies.append(a))

    assert result is None
    assert replies == [(None, None, replies[0][2])]
    assert _error_code(replies[0][2]) == 8  # DuoFPErrorNotConnected
    assert backend.by_token == {}  # never admitted - no orphaned fetch state


def test_open_fetch_without_link_and_without_reply_raises(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)))
    backend._link = None

    try:
        backend.open_fetch(manifest.transfer_id, 0)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError when link is down and reply is None")


def test_pull_chunk_with_link_down_after_open_replies_not_connected(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)))
    [token] = _open_all(backend, manifest)
    fetch = backend.by_token[token]
    backend._link = None  # link died between open_fetch and pull_chunk

    replies = []
    backend.pull_chunk(token, lambda *a: replies.append(a))

    assert replies == [(None, False, replies[0][2])]
    assert _error_code(replies[0][2]) == 8  # DuoFPErrorNotConnected
    assert fetch.state == "failed"


# --- FILE_ERROR reason -> DuoFPError code (spec §16) ------------------------


@pytest.mark.parametrize(
    ("reason", "expected_code"),
    [
        ("source_missing", 1),
        ("source_changed", 2),
        ("peer_lost", 3),
        ("unauthorized", 4),
        ("timeout", 5),
        ("disk_full", 6),
        ("protocol", 7),
        ("something-nobody-defined", 7),  # unknown reason -> Protocol fallback
    ],
)
def test_file_error_reason_maps_to_the_correct_duofperror_code(qapp, reason, expected_code):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(9,)))
    [token] = _open_all(backend, manifest)
    fetch = backend.by_token[token]
    replies = []
    backend.pull_chunk(token, lambda *a: replies.append(a))
    [read] = link.sent

    backend.handle_message(_file_error(read, reason))

    assert replies == [(None, False, replies[0][2])]
    assert _error_code(replies[0][2]) == expected_code
    assert fetch.state == "failed"


# --- session watchdog: local fail-all with Timeout --------------------------


def test_session_timeout_fails_every_active_fetch_with_timeout(qapp):
    manifest = _manifest(sizes=(3, 5, 7, 9, 11, 13))
    backend, link, _remote, manifest = _backend(qapp, manifest)
    tokens = _open_all(backend, manifest)
    fetches = {token: backend.by_token[token] for token in tokens}
    replies = {token: [] for token in tokens}
    for token in tokens:
        backend.pull_chunk(token, lambda *a, t=token: replies[t].append(a))

    backend.on_session_timeout()

    for token in tokens:
        assert len(replies[token]) == 1, token
        assert _error_code(replies[token][0][2]) == 5  # DuoFPErrorTimeout
        assert fetches[token].state == "failed"
    assert link.sent, "the active fetches really had reads in flight, not just queued"


def test_session_timeout_with_no_fetches_in_flight_is_a_no_op(qapp):
    backend, link, _remote, manifest = _backend(qapp)

    backend.on_session_timeout()  # must not raise

    assert backend.by_token == {}


# --- oversized / truncated chunk -> Protocol --------------------------------


def test_oversized_chunk_yields_protocol_code(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)))
    [token] = _open_all(backend, manifest)
    fetch = backend.by_token[token]
    backend.pull_chunk(token, lambda *a: None)
    [read] = link.sent

    backend.handle_message(_reply(read, b"toobig!!"))  # 8 bytes for a 3-byte file

    assert fetch.state == "failed"


def test_truncated_final_chunk_yields_protocol_code(qapp):
    manifest = _manifest(sizes=(5,))
    backend, link, _remote, manifest = _backend(qapp, manifest)
    [token] = _open_all(backend, manifest)
    fetch = backend.by_token[token]
    replies = []
    backend.pull_chunk(token, lambda *a: replies.append(a))
    [read] = link.sent
    assert read.header["length"] == 5

    # eof=True but fewer bytes than the fetch still needs - premature EOF.
    backend.handle_message(Message(MessageType.FILE_CHUNK, dict(read.header), b"ab"))

    assert replies == [(None, False, replies[0][2])]
    assert _error_code(replies[0][2]) == 7  # DuoFPErrorProtocol
    assert fetch.state == "failed"
