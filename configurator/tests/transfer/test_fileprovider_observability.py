"""Task 17: observability - counters + correlation-id log chain, no leaks.

Covers three things:

  * The spec's counters (a plain ``dict`` on the backend - see
    ``FileProviderBackend.counters`` - not a metrics framework, task-17
    brief ruling #2) increment on the RIGHT real transition, driven through
    real scheduler/lifecycle behaviour (open/pull/chunk/cancel/error/
    domain-state/ipc/gc), never through a mocked internal.
  * Every structured log line this module emits carries correlation ids
    (``transfer_id``/``entry_index``/``fetch_token``/``read_id``) but NEVER a
    full manifest path or blob/content bytes (ruling #1 - a HARD BLOCK). One
    test scans every record ``caplog`` captured for exactly that.
  * The Task-14-deferred, Task-17-closed per-outstanding-read watchdog
    (ruling #3): armed while a read is in flight, disarmed on chunk arrival/
    settle/cancel, and on expiry fails EXACTLY the stalled fetch with
    Timeout - using an injectable ``timer_factory`` so nothing here sleeps or
    waits on a real clock.

``FileProviderBackend.record_backend_selected(kind)`` is also exercised
directly: it is a hook exposed for ``MacReceiveRouter._select_backend``
(Task 16, ``platform_files.py`` - NOT part of this task's file list) to call
at the real selection site so ``fp_backend_selected{file_provider|staging}``
increments there; the actual call site is intentionally not wired from here
(see task-17 report).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal

from duo_input.clipboard.wire import Message, MessageType
from duo_input.transfer.fileprovider_backend import FileProviderBackend
from duo_input.transfer.model import ENTRY_FILE, TransferEntry, TransferManifest
from test_fileprovider_scheduler import _error_code

_DAY_NS = 24 * 60 * 60 * 1_000_000_000
_LOGGER_NAME = "duo_input.transfer.fileprovider_backend"


# --- fakes -------------------------------------------------------------


class FakeRemote:
    def __init__(self) -> None:
        self.published: list[str] = []
        self.retired: list[str] = []
        self.deleted: list[str] = []

    def publishGeneration_reply_(self, record: bytes, reply) -> None:
        self.published.append(json.loads(record.decode("utf-8"))["transfer_id"])
        reply(True, None)

    def retireGeneration_reply_(self, generation_id: str, reply) -> None:
        self.retired.append(generation_id)
        reply(True, None)

    def deleteGeneration_reply_(self, generation_id: str, reply) -> None:
        self.deleted.append(generation_id)
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


class DisconnectableLink(QObject):
    """A ``FakeLink`` with a REAL Qt ``disconnected`` signal - same idiom as
    ``test_fileprovider_errors.py``'s ``DisconnectableLink``, redefined here
    to keep this file import-independent."""

    disconnected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[Message] = []

    def send(self, message: Message) -> bool:
        self.sent.append(message)
        return True


class FakeDomain(QObject):
    """Minimal Task-6-shaped domain fake exposing the two signals this
    module optionally connects to (``ready``/``state_changed``)."""

    ready = Signal()
    state_changed = Signal(str)

    def __init__(self, is_ready: bool = False) -> None:
        super().__init__()
        self.is_ready = is_ready


class FakeTimer:
    """Injectable stand-in for ``QTimer`` - no real Qt event loop dependency.
    Tests fire expiry directly via ``.fire()``; ``.stop()``/a completed
    ``.fire()`` both make a later ``.fire()`` a no-op, matching a real
    single-shot timer that already fired or was stopped."""

    def __init__(self) -> None:
        self._callback = None
        self.started_ms: int | None = None
        self.running = False

    def setSingleShot(self, _single: bool) -> None:  # noqa: N802 - mirrors QTimer's selector
        pass

    @property
    def timeout(self) -> "FakeTimer":
        return self

    def connect(self, callback) -> None:
        self._callback = callback

    def start(self, ms: int) -> None:
        self.started_ms = ms
        self.running = True

    def stop(self) -> None:
        self.running = False

    def fire(self) -> None:
        if not self.running or self._callback is None:
            return
        self.running = False
        self._callback()


class FakeClock:
    def __init__(self, now: int = 1_000_000_000) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, ns: int) -> None:
        self.now += ns


def _timer_factory():
    created: list[FakeTimer] = []

    def factory() -> FakeTimer:
        timer = FakeTimer()
        created.append(timer)
        return timer

    factory.created = created
    return factory


def _manifest(
    transfer_id: str = "generation-1",
    sizes: tuple[int, ...] = (3, 5, 7, 9, 11, 13),
    paths: list[str] | None = None,
) -> TransferManifest:
    paths = paths or [f"file-{index}.bin" for index in range(len(sizes))]
    return TransferManifest(
        transfer_id=transfer_id,
        entries=tuple(
            TransferEntry(path=path, kind=ENTRY_FILE, size=size, mtime_ns=0)
            for path, size in zip(paths, sizes, strict=True)
        ),
        skipped=(),
        drop_effect=1,
    )


def _backend(
    qapp,
    manifest: TransferManifest | None = None,
    *,
    link=None,
    domain=None,
    clock=None,
    timer_factory=None,
    max_generations: int = 8,
):
    remote = FakeRemote()
    link = link if link is not None else FakeLink()
    domain = domain if domain is not None else object()
    backend = FileProviderBackend(
        FakeClient(remote),
        domain,
        lambda _urls: None,
        clock=clock,
        max_generations=max_generations,
        timer_factory=timer_factory,
    )
    backend.attach_link(link)
    manifest = manifest or _manifest()
    epoch = backend.handle_offer(manifest)
    backend.authorize(True, epoch)
    return backend, link, remote, manifest


def _open_all(backend: FileProviderBackend, manifest: TransferManifest) -> list[str]:
    opened = [
        backend.open_fetch(manifest.transfer_id, entry_index)
        for entry_index in range(len(manifest.entries))
    ]
    return [token for token, _size in opened]


def _reply(read: Message, blob: bytes, message_type=MessageType.FILE_CHUNK) -> Message:
    return Message(message_type, dict(read.header), blob)


def _file_error(read: Message, reason: str) -> Message:
    return Message(MessageType.FILE_ERROR, dict(read.header, reason=reason), b"")


# --- control RPC handshake observability ---------------------------------


def test_publish_logs_control_rpc_send_reply_and_completion(qapp, caplog):
    caplog.set_level(logging.INFO, logger=_LOGGER_NAME)

    _backend(qapp, _manifest(transfer_id="generation-handshake", sizes=(3,)))

    messages = [record.getMessage() for record in caplog.records]
    for event in (
        "fp_host_control_rpc_begin",
        "fp_host_control_rpc_sent",
        "fp_host_control_rpc_reply",
        "fp_host_control_rpc_completion",
    ):
        assert any(
            event in message and "transfer_id=generation-handshake" in message
            for message in messages
        )


# --- fetch lifecycle counters -------------------------------------------


def test_fetch_started_counter_and_active_queued_gauges(qapp):
    manifest = _manifest(sizes=(3, 5, 7, 9, 11, 13))  # 4 active + 2 queued
    backend, _link, _remote, manifest = _backend(qapp, manifest)

    _open_all(backend, manifest)

    assert backend.counters["fp_fetch_started"] == 6
    assert backend.counters["fp_active_fetches"] == 4
    assert backend.counters["fp_queued_fetches"] == 2


def test_fetch_completed_counter_increments_exactly_on_done(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)))
    [token] = _open_all(backend, manifest)
    backend.pull_chunk(token)
    [read] = link.sent

    backend.handle_message(_reply(read, b"abc"))

    assert backend.counters["fp_fetch_completed"] == 1
    assert backend.counters.get("fp_fetch_failed", 0) == 0
    assert backend.counters.get("fp_fetch_cancelled", 0) == 0
    assert backend.counters["fp_active_fetches"] == 0


def test_fetch_cancelled_counter_increments_on_cancel(qapp):
    backend, _link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)))
    [token] = _open_all(backend, manifest)

    backend.cancel_fetch(token)

    assert backend.counters["fp_fetch_cancelled"] == 1
    assert backend.counters.get("fp_fetch_completed", 0) == 0
    assert backend.counters.get("fp_fetch_failed", 0) == 0


def test_fetch_failed_counter_increments_on_wire_file_error(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)))
    [token] = _open_all(backend, manifest)
    fetch = backend.by_token[token]
    backend.pull_chunk(token)
    [read] = link.sent

    backend.handle_message(_file_error(read, "protocol"))

    assert backend.counters["fp_fetch_failed"] == 1
    assert fetch.state == "failed"


# --- bytes received -------------------------------------------------------


def test_bytes_received_counter_sums_real_chunk_bytes(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3, 4)))
    tokens = _open_all(backend, manifest)
    for token in tokens:
        backend.pull_chunk(token)
    first_read, second_read = link.sent

    backend.handle_message(_reply(first_read, b"abc"))
    backend.handle_message(_reply(second_read, b"wxyz"))

    assert backend.counters["fp_bytes_received"] == 7


# --- domain state / not-ready ----------------------------------------------


def test_domain_state_gauge_and_not_ready_counter_track_state_changed(qapp):
    domain = FakeDomain()
    backend, _link, _remote, _manifest_ = _backend(qapp, domain=domain)

    domain.state_changed.emit("registering")
    assert backend.counters["fp_domain_state"] == "registering"
    assert backend.counters["fp_domain_not_ready"] == 1

    domain.state_changed.emit("ready")
    assert backend.counters["fp_domain_state"] == "ready"
    assert backend.counters["fp_domain_not_ready"] == 1  # unchanged on ready

    domain.state_changed.emit("degraded")
    assert backend.counters["fp_domain_state"] == "degraded"
    assert backend.counters["fp_domain_not_ready"] == 2


# --- ipc connect / disconnect -----------------------------------------------


def test_ipc_connect_and_disconnect_counters(qapp):
    link = DisconnectableLink()
    backend, link, _remote, _manifest_ = _backend(qapp, link=link)

    assert backend.counters["fp_ipc_connect"] == 1

    link.disconnected.emit("peer socket closed")

    assert backend.counters["fp_ipc_disconnect"] == 1


def test_reattaching_a_link_increments_connect_again(qapp):
    backend, _link, _remote, _manifest_ = _backend(qapp)

    backend.attach_link(FakeLink())

    assert backend.counters["fp_ipc_connect"] == 2


# --- late / oversized / truncated chunk -------------------------------------


def test_late_chunk_counter_increments_for_an_unmatched_read(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)))
    [token] = _open_all(backend, manifest)
    backend.pull_chunk(token)
    [read] = link.sent
    backend.cancel_fetch(token)  # settles + drops read_id tracking

    backend.handle_message(_reply(read, b"abc"))  # arrives after settle

    assert backend.counters["fp_late_chunk"] == 1


def test_oversized_chunk_counter_increments_and_fails_the_fetch(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(3,)))
    [token] = _open_all(backend, manifest)
    fetch = backend.by_token[token]
    backend.pull_chunk(token)
    [read] = link.sent

    backend.handle_message(_reply(read, b"abcdefgh"))  # bigger than expected(3)

    assert backend.counters["fp_oversized_chunk"] == 1
    assert backend.counters.get("fp_truncated", 0) == 0
    assert fetch.state == "failed"


def test_truncated_chunk_counter_increments_and_fails_the_fetch(qapp):
    backend, link, _remote, manifest = _backend(qapp, _manifest(sizes=(5,)))
    [token] = _open_all(backend, manifest)
    fetch = backend.by_token[token]
    backend.pull_chunk(token)
    [read] = link.sent

    backend.handle_message(_reply(read, b"ab"))  # smaller than expected(5)

    assert backend.counters["fp_truncated"] == 1
    assert backend.counters.get("fp_oversized_chunk", 0) == 0
    assert fetch.state == "failed"


# --- generation lifecycle: gc + active gauge --------------------------------


def test_generation_active_gauge_and_gc_generation_counter(qapp):
    clock = FakeClock()
    backend, _link, remote, _manifest_a = _backend(
        qapp, _manifest(transfer_id="A", sizes=(3,)), clock=clock
    )
    assert backend.counters["fp_generation_active"] == 1

    manifest_b = _manifest(transfer_id="B", sizes=(3,))
    epoch = backend.handle_offer(manifest_b)
    backend.authorize(True, epoch)

    # B is the new ACTIVE_CLIPBOARD, A is retired (quiesced, ref==0) - the
    # gauge counts only ACTIVE_CLIPBOARD generations, so it stays at 1.
    assert backend.counters["fp_generation_active"] == 1
    assert backend.counters.get("fp_gc_generation", 0) == 0

    clock.advance(_DAY_NS + 1)
    backend._gc()

    assert backend.counters["fp_gc_generation"] == 1
    assert remote.deleted == ["A"]
    assert backend.counters["fp_generation_active"] == 1  # B untouched


# --- fp_backend_selected hook (Task 16 router calls this - see brief) ------


def test_record_backend_selected_increments_the_labeled_counters(qapp):
    backend, _link, _remote, _manifest_ = _backend(qapp)

    backend.record_backend_selected("file_provider")
    backend.record_backend_selected("file_provider")
    backend.record_backend_selected("staging")

    assert backend.counters["fp_backend_selected_file_provider"] == 2
    assert backend.counters["fp_backend_selected_staging"] == 1


def test_record_backend_selected_rejects_an_unknown_kind(qapp):
    backend, _link, _remote, _manifest_ = _backend(qapp)

    with pytest.raises(ValueError):
        backend.record_backend_selected("bogus")


# --- watchdog (Task 17 ruling #3, closing the Task 14 deferral) -------------


def test_watchdog_expiry_fails_exactly_the_stalled_fetch_with_timeout(qapp):
    factory = _timer_factory()
    backend, link, _remote, manifest = _backend(
        qapp, _manifest(sizes=(3, 5)), timer_factory=factory
    )
    tokens = _open_all(backend, manifest)
    fetches = {token: backend.by_token[token] for token in tokens}
    replies: dict[str, list] = {token: [] for token in tokens}
    for token in tokens:
        backend.pull_chunk(token, lambda *args, t=token: replies[t].append(args))
    assert len(factory.created) == 2
    assert all(timer.running for timer in factory.created)

    factory.created[0].fire()

    chunk, ok, error = replies[tokens[0]][0]
    assert chunk is None
    assert ok is False
    assert _error_code(error) == 5  # DuoFPErrorTimeout
    assert fetches[tokens[0]].state == "failed"
    assert backend.counters["fp_fetch_timeout"] == 1
    assert backend.counters["fp_fetch_failed"] == 1
    # the OTHER fetch is completely undisturbed
    assert fetches[tokens[1]].state == "receiving"
    assert replies[tokens[1]] == []
    assert factory.created[1].running


def test_chunk_arrival_disarms_the_watchdog_no_timeout_counted(qapp):
    factory = _timer_factory()
    backend, link, _remote, manifest = _backend(
        qapp, _manifest(sizes=(3,)), timer_factory=factory
    )
    [token] = _open_all(backend, manifest)
    fetch = backend.by_token[token]
    backend.pull_chunk(token)
    [read] = link.sent
    assert len(factory.created) == 1
    assert factory.created[0].running

    backend.handle_message(_reply(read, b"abc"))

    assert not factory.created[0].running
    factory.created[0].fire()  # stale fire after disarm must be a no-op

    assert backend.counters.get("fp_fetch_timeout", 0) == 0
    assert fetch.state == "done"


def test_cancel_disarms_the_watchdog(qapp):
    factory = _timer_factory()
    backend, _link, _remote, manifest = _backend(
        qapp, _manifest(sizes=(3,)), timer_factory=factory
    )
    [token] = _open_all(backend, manifest)
    backend.pull_chunk(token)
    assert factory.created[0].running

    backend.cancel_fetch(token)

    assert not factory.created[0].running
    factory.created[0].fire()

    assert backend.counters.get("fp_fetch_timeout", 0) == 0


def test_watchdog_only_fails_the_read_it_was_armed_for_not_a_reused_token(qapp):
    """A settled read_id's timer firing late must never touch whatever fetch
    happens to occupy ``by_read_id`` next - the guard compares BOTH the
    lookup and ``fetch.read_id``, not just presence in the dict."""
    factory = _timer_factory()
    backend, link, _remote, manifest = _backend(
        qapp, _manifest(sizes=(3, 3)), timer_factory=factory
    )
    tokens = _open_all(backend, manifest)
    fetch0 = backend.by_token[tokens[0]]
    backend.pull_chunk(tokens[0])
    first_read = link.sent[0]
    stale_timer = factory.created[0]
    backend.handle_message(_reply(first_read, b"abc"))  # settles + disarms
    assert fetch0.state == "done"

    backend.pull_chunk(tokens[1])  # a fresh read, gets a fresh read_id/timer

    stale_timer.fire()  # the OLD, already-disarmed timer firing late

    assert backend.counters.get("fp_fetch_timeout", 0) == 0
    assert backend.by_token[tokens[1]].state == "receiving"


# --- privacy hard block: never a full path, never content bytes ------------


def test_no_log_record_ever_carries_a_full_path_or_content_bytes(qapp, caplog):
    """Scans EVERY record this module emitted across a representative slice
    of transitions (open/complete, oversized-reject, cancel, domain churn,
    ipc disconnect) for a full manifest path or the transferred bytes -
    task-17 brief ruling #1, a hard block. Correlation ids and the
    privacy-safe basename (mirrors ``source.py:_name()``) ARE expected to
    appear - this proves the chain exists, not merely that it is silent.
    """
    caplog.set_level(logging.INFO, logger=_LOGGER_NAME)
    secret_blob = b"TOP-SECRET-PAYLOAD-BYTES"
    nested_dir = "confidential-clients/q3-secret-plan"
    manifest = _manifest(
        transfer_id="generation-privacy",
        sizes=(len(secret_blob), 4, 4),
        paths=[
            f"{nested_dir}/report.pdf",
            f"{nested_dir}/two.bin",
            f"{nested_dir}/three.bin",
        ],
    )
    domain = FakeDomain()
    link = DisconnectableLink()
    backend, link, _remote, manifest = _backend(qapp, manifest, link=link, domain=domain)
    tokens = _open_all(backend, manifest)

    backend.pull_chunk(tokens[0])
    backend.handle_message(_reply(link.sent[0], secret_blob))  # completes

    backend.pull_chunk(tokens[1])
    backend.handle_message(_reply(link.sent[1], b"toolong!!"))  # oversized-fails

    backend.cancel_fetch(tokens[2])

    domain.state_changed.emit("degraded")
    link.disconnected.emit("peer socket closed")

    messages = [record.getMessage() for record in caplog.records]
    joined = "\n".join(messages)

    # HARD BLOCK: neither the directory prefix nor the bare word appears.
    assert nested_dir not in joined
    assert "confidential-clients" not in joined
    # HARD BLOCK: neither the transferred bytes nor the bad chunk's bytes.
    assert secret_blob.decode() not in joined
    assert "toolong" not in joined
    # HARD BLOCK: no absolute/home-directory path of any kind.
    assert str(Path.home()) not in joined
    assert "/Users" not in joined

    # The correlation chain DOES exist: transfer_id and at least one
    # fetch_token made it into the log.
    assert manifest.transfer_id in joined
    assert any(token in joined for token in tokens)
    # The privacy-safe basename (mirrors source.py:_name()) is allowed.
    assert "report.pdf" in joined
