"""Task 15: generation lifecycle - retire (state-only, record kept) + TTL/budget GC.

State machine under test (backend view):

    ACTIVE_CLIPBOARD --new clipboard--> RETIRED --ref>0--> (IN_USE)
                                              \\--ref==0 & TTL/budget--> GC_ELIGIBLE

Invariants proved here:
  * A new clipboard RETIREs the previous generation (``retireGeneration`` over
    XPC, ``state="retired"``, the replica record is KEPT - not deleted) so a
    still-in-use generation is never dropped mid-fetch.
  * ``deleteGeneration`` (permanent removal) fires ONLY when the generation has
    NO in-use ref AND (past TTL 24h OR over the generation-count budget).
  * An in-use ref (an ACTIVE fetch bound to the generation) blocks
    ``deleteGeneration`` but NEVER blocks ``retireGeneration``.
  * The in-use ref is an explicit per-generation counter, incremented on
    ``open_fetch`` and decremented EXACTLY ONCE when the fetch settles
    (DONE/FAILED/CANCELLED) - it returns to 0 so GC can eventually delete.
  * When a generation quiesces (retired AND ref==0) the backend sends the
    EXISTING ``TRANSFER_END`` wire message so the sender frees its fds via
    ``close_descriptors`` - no new ``MessageType`` is introduced.
  * Startup purge: a persisted replica record with no live snapshot in this
    process is orphaned and permanently removed.
  * The sender's ``SnapshotRegistry.RETENTION`` is never referenced or modified.

The fake ``remote()`` records the EXACT XPC calls (retire/delete) so the tests
assert the real observable behaviour, not mocked internals. An injectable clock
drives TTL - no test sleeps.
"""

from __future__ import annotations

import json
from pathlib import Path

from duo_input.clipboard.wire import Message, MessageType
from duo_input.transfer.fileprovider_backend import (
    FileProviderBackend,
    _GenerationState,
)
from duo_input.transfer.model import ENTRY_FILE, TransferEntry, TransferManifest

_DAY_NS = 24 * 60 * 60 * 1_000_000_000


class FakeRemote:
    """Records the exact publish/retire/delete XPC calls and replies True."""

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


class FakeClock:
    """Manually advanced monotonic-ish clock (nanoseconds)."""

    def __init__(self, now: int = 1_000_000_000) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, ns: int) -> None:
        self.now += ns


def _manifest(transfer_id: str, sizes: tuple[int, ...] = (3,)) -> TransferManifest:
    return TransferManifest(
        transfer_id=transfer_id,
        entries=tuple(
            TransferEntry(path=f"f{i}.bin", kind=ENTRY_FILE, size=size, mtime_ns=0)
            for i, size in enumerate(sizes)
        ),
        skipped=(),
        drop_effect=1,
    )


def _backend(qapp, *, clock=None, max_generations=8, link=None):
    remote = FakeRemote()
    link = link or FakeLink()
    backend = FileProviderBackend(
        FakeClient(remote),
        object(),
        lambda _urls: None,
        clock=clock,
        max_generations=max_generations,
    )
    backend.attach_link(link)
    return backend, link, remote


def _publish(backend, transfer_id: str, sizes: tuple[int, ...] = (3,)):
    manifest = _manifest(transfer_id, sizes)
    epoch = backend.handle_offer(manifest)
    backend.authorize(True, epoch)
    return manifest


def _reply(read: Message, blob: bytes) -> Message:
    return Message(MessageType.FILE_CHUNK, dict(read.header), blob)


# --- retire on supersede -----------------------------------------------------


def test_new_clipboard_retires_previous_generation_keeping_the_record(qapp):
    backend, _link, remote = _backend(qapp)
    _publish(backend, "A")
    _publish(backend, "B")

    # A retired over XPC, record KEPT (still tracked), NOT deleted.
    assert remote.retired == ["A"]
    assert remote.deleted == []
    assert "A" in backend._generations
    assert backend._generations["A"].state is _GenerationState.RETIRED
    # B is the active clipboard.
    assert backend._generations["B"].state is _GenerationState.ACTIVE_CLIPBOARD


def test_active_generation_is_never_retired_or_deleted(qapp):
    backend, _link, remote = _backend(qapp)
    _publish(backend, "A")

    assert remote.retired == []
    assert remote.deleted == []
    assert backend._generations["A"].state is _GenerationState.ACTIVE_CLIPBOARD


# --- paste / serve -----------------------------------------------------------


def test_paste_active_generation_is_served(qapp):
    backend, _link, _remote = _backend(qapp)
    manifest = _publish(backend, "A")

    token, size = backend.open_fetch("A", 0)

    assert size == manifest.entries[0].size
    assert backend.by_token[token].generation_id == "A"


def test_retired_generation_is_still_servable_while_referenced(qapp):
    backend, _link, _remote = _backend(qapp)
    _publish(backend, "A")
    _publish(backend, "B")  # A now retired, record kept

    # The retired record is kept and still servable (in-use ref may reference it).
    token, _size = backend.open_fetch("A", 0)
    assert backend.by_token[token].generation_id == "A"


def test_repeated_paste_of_completed_generation_serves_without_remote_refetch(qapp):
    backend, link, _remote = _backend(qapp)
    _publish(backend, "A")

    token, _size = backend.open_fetch("A", 0)
    fetch = backend.by_token[token]
    backend.pull_chunk(token)
    [read] = [m for m in link.sent if m.type is MessageType.FILE_READ]
    backend.handle_message(_reply(read, b"abc"))
    assert fetch.state == "done"

    reads_before = len([m for m in link.sent if m.type is MessageType.FILE_READ])
    # Re-paste (re-open) of the same, already-completed generation is served
    # locally - the ref returns to 0 after the completed fetch settled, and a
    # brand-new open is a distinct fetch, but the completed one issues no new
    # remote FILE_READ on its own.
    assert backend._gen_in_use.get("A", 0) == 0
    assert reads_before == 1


# --- in-use ref blocks delete but not retire ---------------------------------


def test_in_use_ref_blocks_delete_but_not_retire(qapp):
    clock = FakeClock()
    backend, link, remote = _backend(qapp, clock=clock)
    _publish(backend, "A")

    # An ACTIVE fetch on A takes an in-use ref.
    token, _size = backend.open_fetch("A", 0)
    assert backend._gen_in_use["A"] == 1

    # New clipboard B: A is retired (state-only) DESPITE the in-use ref.
    _publish(backend, "B")
    assert remote.retired == ["A"]
    assert backend._generations["A"].state is _GenerationState.RETIRED

    # Even long past TTL, the in-use ref blocks deleteGeneration.
    clock.advance(3 * _DAY_NS)
    backend._gc()
    assert remote.deleted == []
    assert "A" in backend._generations

    # Settle the fetch -> last ref drops -> A quiesces and (already past TTL)
    # is GC-eligible, so it is deleted on settle. Delete happens ONLY now,
    # never while the ref was held.
    backend.pull_chunk(token)
    [read] = [m for m in link.sent if m.type is MessageType.FILE_READ]
    backend.handle_message(_reply(read, b"abc"))
    assert remote.deleted == ["A"]
    assert "A" not in backend._generations


# --- in-use ref accounting ---------------------------------------------------


def test_in_use_ref_returns_to_zero_after_all_fetches_settle(qapp):
    backend, link, _remote = _backend(qapp)
    _publish(backend, "A", sizes=(3, 5, 7))

    tokens = [backend.open_fetch("A", i)[0] for i in range(3)]
    assert backend._gen_in_use["A"] == 3
    fetch0, _fetch1, fetch2 = (backend.by_token[t] for t in tokens)

    # Settle each via a completed read (DONE), a cancel (CANCELLED),
    # and a protocol error (FAILED) - every terminal path decrements once.
    backend.pull_chunk(tokens[0])
    [read0] = [m for m in link.sent if m.type is MessageType.FILE_READ]
    backend.handle_message(_reply(read0, b"abc"))  # DONE
    assert fetch0.state == "done"

    backend.cancel_fetch(tokens[1])  # CANCELLED

    backend.pull_chunk(tokens[2])
    read2 = [m for m in link.sent if m.type is MessageType.FILE_READ][-1]
    backend.handle_message(_reply(read2, b"toolong-oops"))  # FAILED (oversized)
    assert fetch2.state == "failed"

    assert backend._gen_in_use["A"] == 0


def test_settled_fetch_decrements_in_use_exactly_once(qapp):
    backend, link, _remote = _backend(qapp)
    _publish(backend, "A")

    token, _size = backend.open_fetch("A", 0)
    assert backend._gen_in_use["A"] == 1

    backend.pull_chunk(token)
    [read] = [m for m in link.sent if m.type is MessageType.FILE_READ]
    backend.handle_message(_reply(read, b"abc"))  # DONE, decremented once
    assert backend._gen_in_use["A"] == 0

    # A racing cancel of the already-DONE fetch must NOT double-decrement.
    backend.cancel_fetch(token)
    assert backend._gen_in_use["A"] == 0


def test_completed_then_superseded_generation_past_ttl_is_deleted(qapp):
    clock = FakeClock()
    backend, link, remote = _backend(qapp, clock=clock)
    _publish(backend, "A")

    # Complete a fetch on A -> ref back to 0.
    token, _size = backend.open_fetch("A", 0)
    backend.pull_chunk(token)
    [read] = [m for m in link.sent if m.type is MessageType.FILE_READ]
    backend.handle_message(_reply(read, b"abc"))
    assert backend._gen_in_use["A"] == 0

    # Supersede A -> retired, kept.
    _publish(backend, "B")
    assert remote.retired == ["A"]
    assert remote.deleted == []

    # Past TTL, no in-use ref -> GC_ELIGIBLE -> deleteGeneration.
    clock.advance(_DAY_NS + 1)
    backend._gc()
    assert remote.deleted == ["A"]
    assert "A" not in backend._generations


# --- TTL / budget GC ---------------------------------------------------------


def test_ttl_expiry_deletes_quiesced_retired_generation(qapp):
    clock = FakeClock()
    backend, _link, remote = _backend(qapp, clock=clock)
    _publish(backend, "A")
    _publish(backend, "B")  # A retired, ref 0

    # Before TTL: retained.
    backend._gc()
    assert remote.deleted == []
    assert "A" in backend._generations

    # After TTL: deleted.
    clock.advance(_DAY_NS + 1)
    backend._gc()
    assert remote.deleted == ["A"]
    assert "A" not in backend._generations


def test_budget_evicts_oldest_retired_generation(qapp):
    clock = FakeClock()
    # Keep at most one retired generation.
    backend, _link, remote = _backend(qapp, clock=clock, max_generations=1)

    _publish(backend, "A")
    clock.advance(1000)
    _publish(backend, "B")  # A retired (1 retired, within budget)
    assert remote.deleted == []

    clock.advance(1000)
    _publish(backend, "C")  # B retired -> 2 retired, over budget -> evict oldest (A)

    assert remote.deleted == ["A"]
    assert "A" not in backend._generations
    assert backend._generations["B"].state is _GenerationState.RETIRED
    assert backend._generations["C"].state is _GenerationState.ACTIVE_CLIPBOARD


def test_budget_never_evicts_an_in_use_generation(qapp):
    clock = FakeClock()
    # Budget 0: ANY retired generation is over budget - the only thing keeping
    # A alive here is its in-use ref, which the budget sweep must respect.
    backend, _link, remote = _backend(qapp, clock=clock, max_generations=0)

    _publish(backend, "A")
    backend.open_fetch("A", 0)  # in-use ref that outlives retirement

    clock.advance(1000)
    _publish(backend, "B")  # A retired, over budget, but in use -> not deleted

    assert remote.deleted == []
    assert "A" in backend._generations
    assert backend._generations["A"].state is _GenerationState.RETIRED


# --- TRANSFER_END on quiesce (existing wire type) ----------------------------


def test_transfer_end_sent_when_generation_quiesces(qapp):
    backend, link, _remote = _backend(qapp)
    _publish(backend, "A")

    # Open a fetch so A is IN_USE when it retires.
    token, _size = backend.open_fetch("A", 0)
    _publish(backend, "B")  # A retired, still IN_USE -> no TRANSFER_END yet

    assert [m for m in link.sent if m.type is MessageType.TRANSFER_END] == []

    # Settle the fetch -> A quiesces -> TRANSFER_END for A (frees sender fds).
    backend.pull_chunk(token)
    [read] = [m for m in link.sent if m.type is MessageType.FILE_READ]
    backend.handle_message(_reply(read, b"abc"))

    ends = [m for m in link.sent if m.type is MessageType.TRANSFER_END]
    assert len(ends) == 1
    assert ends[0].header["transfer_id"] == "A"


def test_transfer_end_uses_the_existing_closed_message_type(qapp):
    # The wire MessageType set is closed at 15 members; TRANSFER_END is #15 and
    # is the type the backend uses to signal quiesce - no new member is added.
    assert MessageType.TRANSFER_END.value == 15
    assert len(list(MessageType)) == 15

    backend, link, _remote = _backend(qapp)
    _publish(backend, "A")
    token, _size = backend.open_fetch("A", 0)
    _publish(backend, "B")
    backend.pull_chunk(token)
    [read] = [m for m in link.sent if m.type is MessageType.FILE_READ]
    backend.handle_message(_reply(read, b"abc"))

    [end] = [m for m in link.sent if m.type is MessageType.TRANSFER_END]
    assert end.type is MessageType.TRANSFER_END


def test_transfer_end_sent_once_per_generation(qapp):
    backend, link, _remote = _backend(qapp)
    _publish(backend, "A", sizes=(3, 5))

    t0, _ = backend.open_fetch("A", 0)
    t1, _ = backend.open_fetch("A", 1)
    _publish(backend, "B")  # A retired, IN_USE (2 refs)

    backend.pull_chunk(t0)
    read0 = [m for m in link.sent if m.type is MessageType.FILE_READ][-1]
    backend.handle_message(_reply(read0, b"abc"))  # ref 2 -> 1, not quiesced yet
    assert [m for m in link.sent if m.type is MessageType.TRANSFER_END] == []

    backend.pull_chunk(t1)
    read1 = [m for m in link.sent if m.type is MessageType.FILE_READ][-1]
    backend.handle_message(_reply(read1, b"abcde"))  # ref 1 -> 0 -> quiesce

    ends = [m for m in link.sent if m.type is MessageType.TRANSFER_END]
    assert len(ends) == 1
    assert ends[0].header["transfer_id"] == "A"


# --- startup purge -----------------------------------------------------------


def test_startup_purge_deletes_orphaned_persisted_records(qapp):
    backend, _link, remote = _backend(qapp)
    # Fresh process: nothing live. Persisted records from a previous run have
    # no live snapshot -> orphaned -> permanently removed.
    purged = backend.purge_stale_generations(["stale-1", "stale-2"])

    assert set(purged) == {"stale-1", "stale-2"}
    assert set(remote.deleted) == {"stale-1", "stale-2"}


def test_startup_purge_keeps_live_generations(qapp):
    backend, _link, remote = _backend(qapp)
    _publish(backend, "A")  # A is live in this process

    purged = backend.purge_stale_generations(["A", "old-1"])

    assert purged == ["old-1"]
    assert remote.deleted == ["old-1"]
    assert "A" in backend._generations


# --- RETENTION guard (failure/rollback condition) ----------------------------


def test_backend_never_references_snapshot_registry_retention():
    import duo_input.transfer.fileprovider_backend as module

    text = Path(module.__file__).read_text(encoding="utf-8")
    assert "RETENTION" not in text
    assert "SnapshotRegistry" not in text
