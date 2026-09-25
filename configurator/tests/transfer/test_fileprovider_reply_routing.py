"""Dead-generation retry blockage (Phase C): replies to File Provider FILE_READs
must reach the FileProviderBackend even when the router has no active offer.

Runtime root cause (2026-09-25): after a host restart, fileproviderd re-requests
items of a rehydrated generation whose Windows snapshot is gone (the source
releases every snapshot on link attach). The FP backend emits FILE_READ, Windows
answers ``FILE_ERROR reason=source_missing`` within ~10 ms, but
``MacReceiveRouter.handle_message`` forwarded file messages only to
``_active_backend`` - None until the first FILE_OFFER of the session - so the
reply was dropped. The read watchdog then failed the fetch with Timeout (5), a
TRANSIENT code the extension retries (5 attempts, ~2.5 min) while holding
fileproviderd fetch slots and delaying unrelated pastes.

Here: replies are routed by read ownership; ``source_missing`` still maps to the
existing terminal code 1 (-> NSFileProviderError.noSuchItem, never retried);
link loss still maps to the transient code 3.
"""

from __future__ import annotations

import logging

from duo_input.clipboard.wire import Message, MessageType
from duo_input.transfer.fileprovider_backend import FileProviderBackend
from duo_input.transfer.macos_files import MacFileReceiver
from duo_input.transfer.model import ENTRY_FILE, TransferEntry, TransferManifest
from duo_input.transfer.platform_files import MacReceiveRouter
from duo_input.transfer.staging import StagingArea

CHUNK = 1024 * 1024


class _Remote:
    def publishGeneration_reply_(self, record, reply):
        reply(True, None)

    def retireGeneration_reply_(self, transfer_id, reply):
        reply(True, None)


class _Client:
    def remote(self):
        return _Remote()


class _Domain:
    is_ready = True


class _Link:
    def __init__(self) -> None:
        self.sent: list[Message] = []

    def send(self, message: Message) -> bool:
        self.sent.append(message)
        return True


class _Collector:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, chunk, eof, error) -> None:
        self.calls.append((chunk, eof, error))


def _code(error) -> int | None:
    if error is None:
        return None
    code = getattr(error, "code", None)
    if callable(code):
        return int(code())
    return int(str(error).rsplit(":", 1)[-1])


def _manifest(transfer_id: str, sizes=(5,)) -> TransferManifest:
    return TransferManifest(
        transfer_id=transfer_id,
        entries=tuple(
            TransferEntry(path=f"f{i}.bin", kind=ENTRY_FILE, size=s, mtime_ns=0)
            for i, s in enumerate(sizes)
        ),
        skipped=(),
        drop_effect=1,
    )


def _stack(qapp, tmp_path):
    fp = FileProviderBackend(
        _Client(), object(), lambda _urls: None,
        url_resolver=lambda root_id, reply: reply(f"file:///{root_id}", None),
    )
    fp.on_domain_ready()
    staging = MacFileReceiver(StagingArea(tmp_path), pasteboard_arm=lambda paths: None)
    router = MacReceiveRouter(staging, fp, domain=_Domain(), client=_Client(),
                              flag_enabled=lambda: True)
    link = _Link()
    router.attach_link(link)
    return router, fp, link


def _known_generation(fp: FileProviderBackend, manifest: TransferManifest) -> None:
    """A generation the FP backend serves WITHOUT a live router offer (the
    post-restart rehydrated case): registered directly on the backend."""
    epoch = fp.handle_offer(manifest)
    fp.authorize(True, epoch)


def _reads(link: _Link) -> list[Message]:
    return [m for m in link.sent if m.type is MessageType.FILE_READ]


def _file_error(read: Message, reason: str) -> Message:
    h = read.header
    return Message(MessageType.FILE_ERROR, {
        "transfer_id": h["transfer_id"], "entry_index": h["entry_index"],
        "offset": h["offset"], "read_id": h["read_id"], "reason": reason,
    }, b"")


def test_file_error_reaches_fp_backend_without_active_offer(qapp, tmp_path):
    router, fp, link = _stack(qapp, tmp_path)
    _known_generation(fp, _manifest("gen-dead"))
    assert router._active_backend is None  # no offer this session (post-restart)

    token, _ = fp.open_fetch("gen-dead", 0)
    pulled = _Collector()
    fp.pull_chunk(token, pulled)
    (read,) = _reads(link)

    router.handle_message(_file_error(read, "source_missing"))

    # settled once, immediately, with the TERMINAL source-missing code (1),
    # not left for the 30 s watchdog to fail as transient Timeout (5)
    assert len(pulled.calls) == 1
    assert _code(pulled.calls[0][2]) == 1
    assert fp.by_read_id == {}
    assert token not in fp.by_token
    assert int(fp.counters.get("fp_fetch_failed", 0)) == 1
    assert int(fp.counters.get("fp_fetch_timeout", 0)) == 0


def test_file_chunk_reaches_fp_backend_without_active_offer(qapp, tmp_path):
    router, fp, link = _stack(qapp, tmp_path)
    _known_generation(fp, _manifest("gen-live"))
    token, _ = fp.open_fetch("gen-live", 0)
    pulled = _Collector()
    fp.pull_chunk(token, pulled)
    (read,) = _reads(link)

    router.handle_message(Message(MessageType.FILE_CHUNK, dict(read.header), b"hello"))

    assert pulled.calls and pulled.calls[-1][0] == b"hello" and pulled.calls[-1][1] is True


def test_fp_reply_is_not_swallowed_by_active_staging_offer(qapp, tmp_path):
    router, fp, link = _stack(qapp, tmp_path)
    _known_generation(fp, _manifest("gen-fp"))
    # a staging offer is active: FP selection disabled for this offer
    router._flag_enabled = lambda: False
    router.handle_offer(_manifest("gen-staging"))
    assert router._active_backend is router._staging

    token, _ = fp.open_fetch("gen-fp", 0)
    pulled = _Collector()
    fp.pull_chunk(token, pulled)
    read = [m for m in _reads(link) if m.header["transfer_id"] == "gen-fp"][0]

    router.handle_message(_file_error(read, "source_missing"))

    assert len(pulled.calls) == 1 and _code(pulled.calls[0][2]) == 1


def test_unowned_reply_still_goes_to_active_backend(qapp, tmp_path):
    router, fp, link = _stack(qapp, tmp_path)
    seen = []
    router._flag_enabled = lambda: False
    router.handle_offer(_manifest("gen-staging"))
    router._staging.handle_message = seen.append  # observe routing only
    msg = Message(MessageType.FILE_CHUNK, {"transfer_id": "gen-staging", "entry_index": 0,
                                           "offset": 0, "read_id": 77}, b"x")
    router.handle_message(msg)
    assert seen == [msg]


def test_late_fp_error_after_settle_is_dropped_without_double_completion(qapp, tmp_path):
    router, fp, link = _stack(qapp, tmp_path)
    _known_generation(fp, _manifest("gen-dead"))
    token, _ = fp.open_fetch("gen-dead", 0)
    pulled = _Collector()
    fp.pull_chunk(token, pulled)
    (read,) = _reads(link)
    router.handle_message(_file_error(read, "source_missing"))
    router.handle_message(_file_error(read, "source_missing"))  # duplicate/late
    router.handle_message(Message(MessageType.FILE_CHUNK, dict(read.header), b"hello"))
    assert len(pulled.calls) == 1
    assert fp.by_read_id == {}


def test_link_loss_stays_transient_peer_lost(qapp, tmp_path):
    router, fp, link = _stack(qapp, tmp_path)
    _known_generation(fp, _manifest("gen-a"))
    token, _ = fp.open_fetch("gen-a", 0)
    pulled = _Collector()
    fp.pull_chunk(token, pulled)
    fp._on_link_lost(link, "test")
    assert _code(pulled.calls[-1][2]) == 3  # DuoFPErrorPeerLost: retryable, NOT item loss
    assert fp.by_read_id == {}


def test_terminal_failure_of_a_does_not_delay_generation_b(qapp, tmp_path):
    router, fp, link = _stack(qapp, tmp_path)
    _known_generation(fp, _manifest("gen-a", sizes=(5,) * 4))
    _known_generation(fp, _manifest("gen-b", sizes=(8 * CHUNK,)))
    dead = []
    for i in range(4):
        token, _ = fp.open_fetch("gen-a", i)
        c = _Collector()
        fp.pull_chunk(token, c)
        dead.append(c)
    for read in [m for m in _reads(link) if m.header["transfer_id"] == "gen-a"]:
        router.handle_message(_file_error(read, "source_missing"))
    assert all(len(c.calls) == 1 and _code(c.calls[0][2]) == 1 for c in dead)
    assert fp.by_read_id == {}

    token_b, _ = fp.open_fetch("gen-b", 0)
    fp.pull_chunk(token_b, _Collector())
    reads_b = [m for m in _reads(link) if m.header["transfer_id"] == "gen-b"]
    assert len(reads_b) == 4  # full per-file window immediately, no stale permits


def test_file_error_reason_is_logged(qapp, tmp_path, caplog):
    router, fp, link = _stack(qapp, tmp_path)
    _known_generation(fp, _manifest("gen-dead"))
    token, _ = fp.open_fetch("gen-dead", 0)
    fp.pull_chunk(token, _Collector())
    (read,) = _reads(link)
    with caplog.at_level(logging.INFO):
        router.handle_message(_file_error(read, "source_missing"))
    lines = [r.getMessage() for r in caplog.records if "fp_file_error" in r.getMessage()]
    assert len(lines) == 1
    line = lines[0]
    for part in ("transfer_id=gen-dead", "entry_index=0", f"read_id={read.header['read_id']}",
                 f"fetch_token={token}", "reason=source_missing", "code=1", "retryable=false"):
        assert part in line
