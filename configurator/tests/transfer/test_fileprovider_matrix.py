"""Task 19: automated regression matrix - the spec's AUTOMATABLE rows.

This is the checked-in safety net for the whole File Provider feature
(Tasks 7-18): ONE parametrized test, each row exercising REAL behaviour
against the existing backends - never a mocked internal - by reusing the
fake-link/fake-sender harnesses the individual task test files already built,
rather than rebuilding them here (see the imports below, one block per
source module). Rows that need a human, a real Finder, or a fresh macOS
account are OUT OF SCOPE for this file - they live in the MANUAL/E2E runbook
at ``docs/superpowers/records/validation/fileprovider-e2e-matrix.md``
(``CLEAN_MACHINE_REQUIRED``/``MANUAL`` rows never appear here as a failing or
``xfail`` test, per the task-19 brief's failure/rollback condition).

Two rows are deliberately asymmetric (task-19 brief):
  * File Provider: authorized + armed + NO Cmd+V -> ZERO ``FILE_READ``. This
    is the actual privacy gate (task-8 brief) - reads only ever happen on a
    real Finder paste, never merely from being copied.
  * Staging: authorized -> the download MAY begin eagerly (this backend
    predates File Provider and its semantics are intentionally unchanged) ->
    the clipboard is armed only once the transfer completes. This row must
    NOT assert zero ``FILE_READ`` - that would be testing for behaviour the
    staging backend never had.
"""

from __future__ import annotations

import types

import pytest

from duo_input.app import _ClipboardRuntime
from duo_input.clipboard.wire import CAPABILITY_FILES, Message, MessageType
from duo_input.transfer.fileprovider_backend import FileProviderBackend
from duo_input.transfer.macos_files import MacFileReceiver
from duo_input.transfer.model import ENTRY_FILE, TransferEntry, TransferManifest
from duo_input.transfer.platform_files import MacReceiveRouter
from duo_input.transfer.staging import StagingArea

# --- Task 9/10 scheduler harness (test_fileprovider_scheduler.py) -----------
from test_fileprovider_scheduler import (
    FakeClient as _SchedClient,
    FakeLink as _SchedLink,
    FakeRemote as _SchedRemote,
    _backend as _sched_backend,
    _error_code,
    _manifest as _sched_manifest,
    _open_all,
    _reply as _sched_reply,
)

# --- Task 13 tree/nesting harness (test_fileprovider_tree.py) ---------------
from test_fileprovider_tree import _open_many, _tree_manifest

# --- Task 14 disconnect/error harness (test_fileprovider_errors.py) ---------
from test_fileprovider_errors import DisconnectableLink, _file_error

# --- Task 15 generation-lifecycle harness (test_fileprovider_lifecycle.py) --
from test_fileprovider_lifecycle import (
    _backend as _lc_backend,
    _manifest as _lc_manifest,
    _publish as _lc_publish,
    _reply as _lc_reply,
)

# --- Task 8 privacy/arm-latch harness (test_fileprovider_privacy.py) --------
from test_fileprovider_privacy import (
    DeferredFakeRemote,
    FakeArm,
    FakeClient as _PrivacyClient,
    FakeDomain as _PrivacyDomain,
    FakeLink as _PrivacyLink,
    FakeResolver,
    _manifest as _privacy_manifest,
)

# --- Task 16 FP-vs-staging routing harness (test_fileprovider_backend_selection.py)
from test_fileprovider_backend_selection import (
    FakeDomain as _SelectionDomain,
    FakeFPBackend,
    FakeFPClient as _SelectionClient,
    FakeStagingBackend,
    _manifest as _selection_manifest,
)

# --- staging (pre-File-Provider) receive harness (test_macos_receiver.py) ---
from test_macos_receiver import (
    FakeLink as _ReceiverLink,
    _manifest as _receiver_manifest,
    _sent as _receiver_sent,
)


# ============================================================= matrix rows


def row_single_small_file(qapp, qtbot, tmp_path):
    backend, link, _remote, manifest = _sched_backend(qapp, _sched_manifest(sizes=(5,)))
    [token] = _open_all(backend, manifest)
    replies = []

    backend.pull_chunk(token, lambda *a: replies.append(a))
    [read] = link.sent
    backend.handle_message(_sched_reply(read, b"hello"))

    assert replies == [(b"hello", True, None)]
    assert token not in backend.by_token  # settled + cleaned up (Task 19 fix)


def row_zero_byte_file(qapp, qtbot, tmp_path):
    backend, link, _remote, manifest = _sched_backend(qapp, _sched_manifest(sizes=(0,)))
    [token] = _open_all(backend, manifest)
    replies = []

    backend.pull_chunk(token, lambda *a: replies.append(a))

    assert replies == [(b"", True, None)]
    assert link.sent == []  # zero-byte files complete without ever reading
    assert token not in backend.by_token


def row_multiple_flat_files(qapp, qtbot, tmp_path):
    backend, link, _remote, manifest = _sched_backend(qapp, _sched_manifest(sizes=(3, 5, 7)))
    tokens = _open_all(backend, manifest)
    blobs = [b"abc", b"vwxyz", b"1234567"]

    for token, blob in zip(tokens, blobs):
        replies = []
        fetch = backend.by_token[token]
        backend.pull_chunk(token, lambda *a, r=replies: r.append(a))
        live = {r.read_id for r in fetch.ranges.values()}
        [read] = [m for m in link.sent if m.header["read_id"] in live]
        backend.handle_message(_sched_reply(read, blob))
        assert replies == [(blob, True, None)]


def row_nested_directory(qapp, qtbot, tmp_path):
    manifest = _tree_manifest()
    backend, link, _remote, manifest = _sched_backend(qapp, manifest)
    [token] = _open_many(backend, manifest.transfer_id, [5])  # dirA/sub/d.bin, depth 2
    replies = []

    backend.pull_chunk(token, lambda *a: replies.append(a))
    [read] = link.sent
    backend.handle_message(_sched_reply(read, b"deep"))

    assert replies == [(b"deep", True, None)]


def row_cancel_queued(qapp, qtbot, tmp_path):
    backend, link, _remote, manifest = _sched_backend(qapp)  # 6 entries, 4 slots
    tokens = _open_all(backend, manifest)
    queued = tokens[4]
    assert backend.by_token[queued].state == "queued"

    backend.cancel_fetch(queued)

    assert queued not in backend.by_token
    assert all(m.header.get("entry_index") != 4 for m in link.sent)  # never read


def row_cancel_active(qapp, qtbot, tmp_path):
    backend, link, _remote, manifest = _sched_backend(qapp, _sched_manifest(sizes=(3,)))
    [token] = _open_all(backend, manifest)
    assert backend.by_token[token].state == "requesting"

    backend.cancel_fetch(token)

    assert token not in backend.by_token
    assert link.sent == []


def row_cancel_in_flight(qapp, qtbot, tmp_path):
    backend, link, _remote, manifest = _sched_backend(qapp, _sched_manifest(sizes=(9,)))
    [token] = _open_all(backend, manifest)
    replies = []
    backend.pull_chunk(token, lambda *a: replies.append(a))
    [read] = link.sent

    backend.cancel_fetch(token)

    assert len(replies) == 1
    chunk, ok, error = replies[0]
    assert chunk is None and ok is False and error is not None
    # The real chunk shows up anyway (already in flight) - dropped silently.
    backend.handle_message(_sched_reply(read, b"a" * 9))
    assert len(replies) == 1


def row_source_changed(qapp, qtbot, tmp_path):
    backend, link, _remote, manifest = _sched_backend(qapp, _sched_manifest(sizes=(9,)))
    [token] = _open_all(backend, manifest)
    replies = []
    backend.pull_chunk(token, lambda *a: replies.append(a))
    [read] = link.sent

    backend.handle_message(_file_error(read, "source_changed"))

    assert replies[0][:2] == (None, False)
    assert _error_code(replies[0][2]) == 2  # DuoFPErrorSourceChanged


def row_source_missing(qapp, qtbot, tmp_path):
    backend, link, _remote, manifest = _sched_backend(qapp, _sched_manifest(sizes=(9,)))
    [token] = _open_all(backend, manifest)
    replies = []
    backend.pull_chunk(token, lambda *a: replies.append(a))
    [read] = link.sent

    backend.handle_message(_file_error(read, "source_missing"))

    assert replies[0][:2] == (None, False)
    assert _error_code(replies[0][2]) == 1  # DuoFPErrorSourceMissing


def row_peer_disconnect(qapp, qtbot, tmp_path):
    link = DisconnectableLink()
    backend, link, _remote, manifest = _sched_backend(
        qapp, _sched_manifest(sizes=(3,)), link=link
    )
    [token] = _open_all(backend, manifest)
    replies = []
    backend.pull_chunk(token, lambda *a: replies.append(a))

    link.disconnected.emit("peer socket closed")

    assert len(replies) == 1
    assert _error_code(replies[0][2]) in (3, 8)  # PeerLost or NotConnected


def row_repeated_paste_of_completed_generation(qapp, qtbot, tmp_path):
    backend, link, remote = _lc_backend(qapp)
    _lc_publish(backend, "A")

    token, _size = backend.open_fetch("A", 0)
    backend.pull_chunk(token, lambda *a: None)
    [read] = [m for m in link.sent if m.type is MessageType.FILE_READ]
    backend.handle_message(_lc_reply(read, b"abc"))

    reads_before = len([m for m in link.sent if m.type is MessageType.FILE_READ])
    assert backend._gen_in_use.get("A", 0) == 0
    assert reads_before == 1  # nothing lingering re-reads on its own

    # A second, independent paste (re-open) of the SAME generation is a fresh,
    # distinct fetch that serves fine on its own - the completed one above is
    # never touched again.
    token2, _size2 = backend.open_fetch("A", 0)
    assert token2 != token
    backend.pull_chunk(token2, lambda *a: None)
    [read2] = [m for m in link.sent if m.type is MessageType.FILE_READ][1:]
    backend.handle_message(_lc_reply(read2, b"abc"))

    assert len([m for m in link.sent if m.type is MessageType.FILE_READ]) == 2


def row_new_clipboard_while_old_fetch_active(qapp, qtbot, tmp_path):
    backend, link, remote = _lc_backend(qapp)
    _lc_publish(backend, "A")
    token, _size = backend.open_fetch("A", 0)  # takes an in-use ref on A
    assert backend._gen_in_use["A"] == 1

    _lc_publish(backend, "B")  # new clipboard supersedes A mid-fetch

    assert remote.retired == ["A"]
    assert backend._generations["A"].state.name == "RETIRED"
    assert backend._generations["B"].state.name == "ACTIVE_CLIPBOARD"

    # The old, now-retired generation's already-open fetch still completes.
    backend.pull_chunk(token, lambda *a: None)
    [read] = [m for m in link.sent if m.type is MessageType.FILE_READ]
    backend.handle_message(_lc_reply(read, b"abc"))

    assert backend._gen_in_use["A"] == 0
    assert remote.deleted == []  # well under both TTL and the count budget


def row_stale_accept_after_supersede_no_publish_replica_arm_read(qapp, qtbot, tmp_path):
    """The failure/rollback-named row: a stale Accept for an offer that was
    itself superseded (by a SECOND offer) before it was ever decided must be
    a complete no-op - no publish, no replica record, no arm, no FILE_READ
    ever becomes possible for it."""
    remote = DeferredFakeRemote()
    client = _PrivacyClient(remote)
    domain = _PrivacyDomain()
    arm = FakeArm()
    resolver = FakeResolver()
    link = _PrivacyLink()
    backend = FileProviderBackend(client, domain, arm, url_resolver=resolver)
    backend.attach_link(link)

    epoch_a = backend.handle_offer(_privacy_manifest("aaa111"))
    backend.handle_offer(_privacy_manifest("bbb222"))  # supersedes A before A is decided

    backend.authorize(True, epoch_a)  # STALE accept for the superseded offer

    assert remote.published == []  # no publish attempt for A - ever
    assert arm.calls == []
    assert resolver.calls == []
    with pytest.raises(ValueError):
        backend.open_fetch("aaa111", 0)  # never registered -> no FILE_READ possible
    assert link.sent == []


def row_ask_deny(qapp, qtbot, tmp_path):
    remote = _SchedRemote()
    client = _SchedClient(remote)
    link = _SchedLink()
    backend = FileProviderBackend(client, object(), lambda _urls: None)
    backend.attach_link(link)
    cancelled = []
    backend.transfer_cancelled.connect(lambda: cancelled.append(1))

    epoch = backend.handle_offer(_sched_manifest(transfer_id="deny-1"))
    backend.authorize(False, epoch)

    assert cancelled == [1]
    with pytest.raises(ValueError):
        backend.open_fetch("deny-1", 0)  # denied -> never published, never servable
    assert link.sent == []


def row_ask_accept(qapp, qtbot, tmp_path):
    backend, link, remote = _lc_backend(qapp)
    cancelled = []
    backend.transfer_cancelled.connect(lambda: cancelled.append(1))

    epoch = backend.handle_offer(_lc_manifest("accept-1"))
    backend.authorize(True, epoch)

    assert remote.published == ["accept-1"]
    assert cancelled == []
    token, size = backend.open_fetch("accept-1", 0)  # now servable
    assert size == 3


def row_auto_accept_mode_skips_the_prompt(qapp, qtbot, tmp_path):
    """``_ClipboardRuntime._on_file_authorization_needed`` in "auto" mode must
    call ``authorize(True)`` directly and never reach the interactive prompt
    (``_prompt_file_authorization`` - see its own docstring: "a distinct
    method so tests can monkeypatch it instead of driving a real modal
    dialog"). Exercised via a duck-typed stub rather than the full app/window
    stack - the method only touches ``self._settings``/``self._file_receiver``
    on this path."""

    class _Recorder:
        def __init__(self) -> None:
            self.calls: list[bool] = []

        def authorize(self, accepted: bool) -> None:
            self.calls.append(accepted)

    class _FakeSettings:
        def value(self, key, default, type=None):  # noqa: A002 - mirrors QSettings.value
            assert key == "clipboard/incoming_files"
            return "auto"

    prompted = []
    stub = types.SimpleNamespace(
        _settings=_FakeSettings(),
        _file_receiver=_Recorder(),
        _prompt_file_authorization=lambda manifest: prompted.append(manifest),
    )

    _ClipboardRuntime._on_file_authorization_needed(stub, object())

    assert stub._file_receiver.calls == [True]
    assert prompted == []  # the operator was never asked


def row_fp_unavailable_routes_to_staging(qapp, qtbot, tmp_path):
    staging, fp = FakeStagingBackend(), FakeFPBackend()
    router = MacReceiveRouter(
        staging,
        fp,
        domain=_SelectionDomain(False),  # FP domain not READY
        client=_SelectionClient(),
        flag_enabled=lambda: True,
    )
    manifest = _selection_manifest()

    router.handle_offer(manifest)

    assert staging.offers == [manifest]
    assert fp.offers == []


def row_unicode_names(qapp, qtbot, tmp_path):
    manifest = TransferManifest(
        transfer_id="unicode-1",
        entries=(
            TransferEntry(path="файл-Ω-日本語.bin", kind=ENTRY_FILE, size=4, mtime_ns=0),
        ),
        skipped=(),
        drop_effect=1,
    )
    backend, link, _remote, manifest = _sched_backend(qapp, manifest)
    [token] = _open_all(backend, manifest)
    replies = []

    backend.pull_chunk(token, lambda *a: replies.append(a))
    [read] = link.sent
    backend.handle_message(_sched_reply(read, b"data"))

    assert replies == [(b"data", True, None)]


def row_duplicate_names_in_different_directories(qapp, qtbot, tmp_path):
    manifest = _tree_manifest()
    backend, link, _remote, manifest = _sched_backend(qapp, manifest)
    a_in_dirA, a_in_dirB = _open_many(backend, manifest.transfer_id, [1, 7])  # both "a.bin"
    assert a_in_dirA != a_in_dirB

    replies_a, replies_b = [], []
    backend.pull_chunk(a_in_dirA, lambda *a: replies_a.append(a))
    backend.pull_chunk(a_in_dirB, lambda *a: replies_b.append(a))
    [read_a] = [m for m in link.sent if m.header["entry_index"] == 1]
    [read_b] = [m for m in link.sent if m.header["entry_index"] == 7]

    backend.handle_message(_sched_reply(read_a, b"AAA"))
    backend.handle_message(_sched_reply(read_b, b"BBBBBBBBB"))

    assert replies_a == [(b"AAA", True, None)]
    assert replies_b == [(b"BBBBBBBBB", True, None)]


def row_fp_privacy_no_cmd_v_zero_file_read(qapp, qtbot, tmp_path):
    """File Provider: authorized + armed + NO Cmd+V -> ZERO FILE_READ. This is
    the actual privacy gate (task-8 brief) - verbatim shape of
    ``test_arm_then_wait_sends_no_file_read``."""
    remote = DeferredFakeRemote()
    client = _PrivacyClient(remote)
    domain = _PrivacyDomain()
    arm = FakeArm()
    resolver = FakeResolver()
    link = _PrivacyLink()
    backend = FileProviderBackend(client, domain, arm, url_resolver=resolver)
    backend.attach_link(link)

    epoch = backend.handle_offer(_privacy_manifest())
    backend.authorize(True, epoch)
    backend.on_ack("abc123")
    backend.on_domain_ready()
    qtbot.wait(200)

    assert arm.calls == [["abc123-root"]]  # authorized AND armed
    assert not any(m.type.name == "FILE_READ" for m in link.sent)  # zero FILE_READ


def row_staging_privacy_may_begin_then_arm_after_complete(qapp, qtbot, tmp_path):
    """Staging (intentionally eager, semantics unchanged): authorized -> the
    download MAY begin immediately -> the clipboard is armed only once the
    transfer completes. Deliberately does NOT assert zero FILE_READ - that
    would test for behaviour this backend never had (see module docstring)."""
    calls = {"armed": None}
    receiver = MacFileReceiver(
        StagingArea(tmp_path),
        pasteboard_arm=lambda paths: calls.__setitem__("armed", list(paths)),
    )
    receiver.set_peer_capabilities(frozenset({CAPABILITY_FILES}))
    link = _ReceiverLink()
    receiver.attach_link(link)
    completed = []
    receiver.transfer_completed.connect(lambda: completed.append(1))

    receiver.handle_offer(_receiver_manifest())
    receiver.authorize(True)  # authorized: staging is free to start eagerly

    reads = _receiver_sent(link, MessageType.FILE_READ)
    assert reads, "staging is intentionally eager - download starts on authorize"
    assert calls["armed"] is None  # not armed yet - only after completion

    read = reads[-1]
    receiver.handle_message(
        Message(
            MessageType.FILE_CHUNK,
            {
                "transfer_id": read.header["transfer_id"],
                "entry_index": read.header["entry_index"],
                "offset": read.header["offset"],
                "read_id": read.header["read_id"],
            },
            b"hello",
        )
    )

    assert completed == [1]
    assert calls["armed"] is not None  # armed only AFTER the transfer completed


ROWS = [
    ("single_small_file", row_single_small_file),
    ("zero_byte_file", row_zero_byte_file),
    ("multiple_flat_files", row_multiple_flat_files),
    ("nested_directory", row_nested_directory),
    ("cancel_queued", row_cancel_queued),
    ("cancel_active", row_cancel_active),
    ("cancel_in_flight", row_cancel_in_flight),
    ("source_changed", row_source_changed),
    ("source_missing", row_source_missing),
    ("peer_disconnect", row_peer_disconnect),
    ("repeated_paste", row_repeated_paste_of_completed_generation),
    ("new_clipboard_while_old_fetch_active", row_new_clipboard_while_old_fetch_active),
    (
        "stale_accept_after_supersede_no_publish_replica_arm_read",
        row_stale_accept_after_supersede_no_publish_replica_arm_read,
    ),
    ("ask_deny", row_ask_deny),
    ("ask_accept", row_ask_accept),
    ("auto_accept_mode", row_auto_accept_mode_skips_the_prompt),
    ("fp_unavailable_routes_to_staging", row_fp_unavailable_routes_to_staging),
    ("unicode_names", row_unicode_names),
    ("duplicate_names_in_different_directories", row_duplicate_names_in_different_directories),
    ("fp_privacy_no_cmd_v_zero_file_read", row_fp_privacy_no_cmd_v_zero_file_read),
    (
        "staging_privacy_may_begin_then_arm_after_complete",
        row_staging_privacy_may_begin_then_arm_after_complete,
    ),
]


@pytest.mark.parametrize("row", [fn for _id, fn in ROWS], ids=[row_id for row_id, _fn in ROWS])
def test_matrix_row(qapp, qtbot, tmp_path, row):
    row(qapp, qtbot, tmp_path)
