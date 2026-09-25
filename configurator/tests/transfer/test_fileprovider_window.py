"""Bounded per-file FILE_READ window (PER_FILE_READ_WINDOW = 4).

RED-first tests for the windowed fetch state machine (design doc
docs/superpowers/specs/2026-09-24-fileprovider-per-file-read-window-design.md).
These assert the windowed invariants and therefore FAIL against the previous
WINDOW=1 backend where appropriate (spec §19).
"""

from __future__ import annotations

import json

from duo_input.clipboard.wire import MAX_FILE_CHUNK_BYTES, Message, MessageType
from duo_input.transfer.fileprovider_backend import (
    PER_FILE_READ_WINDOW,
    FileProviderBackend,
)
from duo_input.transfer.model import ENTRY_FILE, TransferEntry, TransferManifest

CHUNK = MAX_FILE_CHUNK_BYTES


# --- fakes (same idiom as test_fileprovider_scheduler.py) --------------------
class FakeRemote:
    def publishGeneration_reply_(self, record: bytes, reply) -> None:
        json.loads(record.decode("utf-8"))
        reply(True, None)


class FakeClient:
    def __init__(self, remote: FakeRemote) -> None:
        self._remote = remote

    def remote(self):
        return self._remote


class FakeLink:
    def __init__(self) -> None:
        self.sent: list[Message] = []
        self.connection_generation = 1

    def send(self, message: Message) -> bool:
        self.sent.append(message)
        return True


def _manifest(sizes: tuple[int, ...], transfer_id: str = "generation-1") -> TransferManifest:
    return TransferManifest(
        transfer_id=transfer_id,
        entries=tuple(
            TransferEntry(path=f"file-{i}.bin", kind=ENTRY_FILE, size=size, mtime_ns=0)
            for i, size in enumerate(sizes)
        ),
        skipped=(),
        drop_effect=1,
    )


class FakeTimer:
    """Injectable QTimer stand-in (mirrors test_fileprovider_observability)."""

    def __init__(self) -> None:
        self._callback = None
        self.running = False

    def setSingleShot(self, _single: bool) -> None:  # noqa: N802
        pass

    @property
    def timeout(self) -> "FakeTimer":
        return self

    def connect(self, callback) -> None:
        self._callback = callback

    def start(self, _ms: int) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def fire(self) -> None:
        if not self.running or self._callback is None:
            return
        self.running = False
        self._callback()


def _timer_factory():
    created: list[FakeTimer] = []

    def factory() -> FakeTimer:
        timer = FakeTimer()
        created.append(timer)
        return timer

    factory.created = created
    return factory


def _backend(
    qapp,
    manifest: TransferManifest,
    link: FakeLink | None = None,
    timer_factory=None,
):
    link = link or FakeLink()
    backend = FileProviderBackend(
        FakeClient(FakeRemote()),
        object(),
        lambda _urls: None,
        timer_factory=timer_factory,
    )
    backend.attach_link(link)
    epoch = backend.handle_offer(manifest)
    backend.authorize(True, epoch)
    return backend, link


def _blob(read: Message) -> bytes:
    """Deterministic per-offset payload so byte-exactness is checkable: the
    chunk for range at ``offset`` is that offset's chunk-index byte repeated."""
    offset = read.header["offset"]
    length = read.header["length"]
    marker = (offset // CHUNK) % 256
    return bytes([marker]) * length


def _chunk_for(read: Message) -> Message:
    return Message(MessageType.FILE_CHUNK, dict(read.header), _blob(read))


def _reads(link: FakeLink) -> list[Message]:
    return [m for m in link.sent if m.type is MessageType.FILE_READ]


class Collector:
    """Captures pull_chunk reply callbacks: (blob, eof, error)."""

    def __init__(self) -> None:
        self.calls: list[tuple[bytes | None, bool, object]] = []

    def __call__(self, blob, eof, error) -> None:
        self.calls.append((blob, eof, error))


# --- 1. initial window fills to exactly 4 ------------------------------------
def test_initial_window_fills_to_exactly_four(qapp):
    backend, link = _backend(qapp, _manifest((8 * CHUNK,)))
    (token, _size), = [backend.open_fetch("generation-1", 0)]

    backend.pull_chunk(token, Collector())

    reads = _reads(link)
    assert len(reads) == PER_FILE_READ_WINDOW == 4
    assert [r.header["offset"] for r in reads] == [0, CHUNK, 2 * CHUNK, 3 * CHUNK]
    assert all(r.header["length"] == CHUNK for r in reads)
    # every read_id distinct and tracked
    assert len({r.header["read_id"] for r in reads}) == 4
    assert len(backend.by_read_id) == 4


# --- 2. one completed range refills exactly one slot -------------------------
def test_completed_range_refills_exactly_one_slot(qapp):
    backend, link = _backend(qapp, _manifest((8 * CHUNK,)))
    (token, _), = [backend.open_fetch("generation-1", 0)]
    backend.pull_chunk(token, Collector())
    reads = _reads(link)
    assert len(reads) == 4

    # R0 (offset 0) completes -> consumed by the parked reply -> one refill
    backend.handle_message(_chunk_for(reads[0]))

    reads_after = _reads(link)
    assert len(reads_after) == 5  # exactly one new read
    assert reads_after[4].header["offset"] == 4 * CHUNK
    assert len(backend.by_read_id) == 4  # still exactly 4 outstanding


# --- 3. never more than 4 outstanding ----------------------------------------
def test_never_more_than_four_outstanding(qapp):
    backend, link = _backend(qapp, _manifest((16 * CHUNK,)))
    (token, _), = [backend.open_fetch("generation-1", 0)]
    c = Collector()
    backend.pull_chunk(token, c)

    # Drive the whole file, re-parking a reply after each delivery, and assert
    # the outstanding count never exceeds the window.
    delivered = 0
    guard = 0
    while delivered < 16 and guard < 1000:
        guard += 1
        assert len(backend.by_read_id) <= PER_FILE_READ_WINDOW
        in_flight = [
            m
            for m in _reads(link)
            if m.header["read_id"] in backend.by_read_id
        ]
        assert len(in_flight) <= PER_FILE_READ_WINDOW
        if in_flight:
            backend.handle_message(_chunk_for(in_flight[0]))
        if len(c.calls) > delivered:
            delivered = len(c.calls)
            if not c.calls[-1][1]:  # not eof -> next pull
                backend.pull_chunk(token, c)
    assert delivered == 16
    assert c.calls[-1][1] is True  # eof
    assert int(backend.counters.get("fp_max_global_outstanding_reads", 0)) <= 4


# --- 4. EOF shrinks the final window correctly -------------------------------
def test_eof_shrinks_final_window(qapp):
    # 6 MiB: after R0..R3 in flight, plan_offset=4M; only 2 ranges remain.
    backend, link = _backend(qapp, _manifest((6 * CHUNK,)))
    (token, _), = [backend.open_fetch("generation-1", 0)]
    c = Collector()
    backend.pull_chunk(token, c)
    reads = _reads(link)
    assert [r.header["offset"] for r in reads] == [0, CHUNK, 2 * CHUNK, 3 * CHUNK]

    # consume R0, R1 -> two refills at 4M, 5M, then planner is exhausted.
    backend.handle_message(_chunk_for(reads[0]))
    backend.pull_chunk(token, c)
    backend.handle_message(_chunk_for(reads[1]))
    backend.pull_chunk(token, c)
    offsets = [r.header["offset"] for r in _reads(link)]
    assert offsets == [0, CHUNK, 2 * CHUNK, 3 * CHUNK, 4 * CHUNK, 5 * CHUNK]
    # no read is ever issued past EOF
    assert all(o < 6 * CHUNK for o in offsets)


# --- 5. out-of-order chunks produce byte-exact ordered output ----------------
def test_out_of_order_chunks_byte_exact(qapp):
    backend, link = _backend(qapp, _manifest((4 * CHUNK,)))
    (token, _), = [backend.open_fetch("generation-1", 0)]
    c = Collector()
    backend.pull_chunk(token, c)
    reads = _reads(link)
    assert len(reads) == 4

    # Deliver R0, R2, R1, R3 (out of order). Re-park a reply ONLY when a new
    # chunk was actually delivered and it was not EOF (the consumer pulls
    # serially - a second pull while one is parked is a protocol error).
    def deliver(idx):
        before = len(c.calls)
        backend.handle_message(_chunk_for(reads[idx]))
        while len(c.calls) > before and not c.calls[-1][1] and fetch_reply_free():
            before = len(c.calls)
            backend.pull_chunk(token, c)

    def fetch_reply_free():
        f = backend.by_token.get(token)
        return f is not None and f.reply is None

    # park is already set by first pull; deliver R0 -> served immediately
    deliver(0)
    deliver(2)  # buffered, not delivered yet (waiting on R1)
    deliver(1)  # now R1 served; the re-pull then also drains buffered R2
    deliver(3)

    blobs = [blob for blob, _eof, _err in c.calls]
    expected = [bytes([i]) * CHUNK for i in range(4)]
    assert blobs == expected
    assert c.calls[-1][1] is True
    assert all(err is None for _b, _e, err in c.calls)


# --- 6. duplicate chunk ignored ----------------------------------------------
def test_duplicate_chunk_ignored(qapp):
    backend, link = _backend(qapp, _manifest((4 * CHUNK,)))
    (token, _), = [backend.open_fetch("generation-1", 0)]
    c = Collector()
    backend.pull_chunk(token, c)
    reads = _reads(link)

    backend.handle_message(_chunk_for(reads[0]))  # consumed
    calls_after_first = len(c.calls)
    backend.handle_message(_chunk_for(reads[0]))  # duplicate: read_id gone

    assert len(c.calls) == calls_after_first  # no extra delivery
    assert int(backend.counters.get("fp_late_chunk", 0)) >= 1


# --- 7. unexpected range rejected --------------------------------------------
def test_unexpected_range_rejected(qapp):
    backend, link = _backend(qapp, _manifest((4 * CHUNK,)))
    (token, _), = [backend.open_fetch("generation-1", 0)]
    c = Collector()
    backend.pull_chunk(token, c)

    bogus = Message(
        MessageType.FILE_CHUNK,
        {
            "transfer_id": "generation-1",
            "entry_index": 0,
            "offset": 99 * CHUNK,  # never requested
            "length": CHUNK,
            "read_id": 999999,  # not in by_read_id
        },
        b"x" * CHUNK,
    )
    backend.handle_message(bogus)

    assert c.calls == []  # nothing delivered
    assert int(backend.counters.get("fp_late_chunk", 0)) >= 1


# --- 8 & 9. cancellation with four outstanding + late chunks -----------------
def test_cancel_with_four_outstanding_then_late_chunks(qapp):
    backend, link = _backend(qapp, _manifest((8 * CHUNK,)))
    (token, _), = [backend.open_fetch("generation-1", 0)]
    c = Collector()
    backend.pull_chunk(token, c)
    reads = _reads(link)
    assert len(reads) == 4
    assert len(backend.by_read_id) == 4

    reads_before_cancel = len(_reads(link))
    backend.cancel_fetch(token)

    # parked reply settled exactly once with an error
    assert len(c.calls) == 1
    assert c.calls[0][2] is not None
    # no ranges left tracked
    assert len(backend.by_read_id) == 0
    assert backend.by_token.get(token) is None

    # all 4 responses arrive late
    for read in reads:
        backend.handle_message(_chunk_for(read))

    assert len(_reads(link)) == reads_before_cancel  # 0 new reads
    assert len(c.calls) == 1  # no duplicate completion
    assert int(backend.counters.get("fp_late_chunk", 0)) == 4


# --- 10. FILE_ERROR with other ranges outstanding ----------------------------
def test_file_error_with_others_outstanding(qapp):
    backend, link = _backend(qapp, _manifest((8 * CHUNK,)))
    (token, _), = [backend.open_fetch("generation-1", 0)]
    c = Collector()
    backend.pull_chunk(token, c)
    reads = _reads(link)
    assert len(reads) == 4

    # R0 success (consumed), R1 error, R2/R3 arrive late
    backend.handle_message(_chunk_for(reads[0]))
    backend.pull_chunk(token, c)  # re-park for the next expected (offset CHUNK)
    err = Message(MessageType.FILE_ERROR, dict(reads[1].header) | {"reason": "protocol"}, b"")
    reads_at_error = len(_reads(link))
    backend.handle_message(err)

    # fetch failed exactly once, no refill
    assert backend.by_token.get(token) is None
    assert len(_reads(link)) == reads_at_error
    # last reply carried the error, and there is exactly one error delivery
    errors = [call for call in c.calls if call[2] is not None]
    assert len(errors) == 1

    backend.handle_message(_chunk_for(reads[2]))
    backend.handle_message(_chunk_for(reads[3]))
    assert len(_reads(link)) == reads_at_error  # still no refill
    assert len([call for call in c.calls if call[2] is not None]) == 1


# --- 11. disconnect with outstanding ranges ----------------------------------
def test_disconnect_with_outstanding_ranges(qapp):
    backend, link = _backend(qapp, _manifest((8 * CHUNK,)))
    (token, _), = [backend.open_fetch("generation-1", 0)]
    c = Collector()
    backend.pull_chunk(token, c)
    assert len(backend.by_read_id) == 4

    backend.on_session_timeout()  # supported local fail-all (as a disconnect)

    assert len(backend.by_read_id) == 0
    assert backend.by_token.get(token) is None
    assert len(c.calls) == 1 and c.calls[0][2] is not None  # settled once, error


# --- 12. retry does not accept a stale response ------------------------------
def test_retry_does_not_accept_stale_response(qapp):
    backend, link = _backend(qapp, _manifest((4 * CHUNK,)))
    (token_a, _), = [backend.open_fetch("generation-1", 0)]
    c_a = Collector()
    backend.pull_chunk(token_a, c_a)
    reads_a = _reads(link)
    assert len(reads_a) == 4

    # attempt A is cancelled (Swift teardown before retry)
    backend.cancel_fetch(token_a)

    # attempt B: fresh fetch, fresh read_ids
    (token_b, _), = [backend.open_fetch("generation-1", 0)]
    c_b = Collector()
    backend.pull_chunk(token_b, c_b)
    reads_b = [m for m in _reads(link) if m.header["read_id"] not in
               {r.header["read_id"] for r in reads_a}]
    assert len(reads_b) == 4
    b_read_ids = {r.header["read_id"] for r in reads_b}
    a_read_ids = {r.header["read_id"] for r in reads_a}
    assert a_read_ids.isdisjoint(b_read_ids)

    # stale attempt-A chunk arrives: must not be accepted by attempt B
    backend.handle_message(_chunk_for(reads_a[0]))
    assert c_b.calls == []  # B got nothing from A's stale chunk
    # B still healthy: deliver its R0
    backend.handle_message(_chunk_for(reads_b[0]))
    assert len(c_b.calls) == 1 and c_b.calls[0][2] is None


# --- 13. zero-byte file ------------------------------------------------------
def test_zero_byte_file(qapp):
    backend, link = _backend(qapp, _manifest((0,)))
    (token, size), = [backend.open_fetch("generation-1", 0)]
    assert size == 0
    c = Collector()
    backend.pull_chunk(token, c)

    assert _reads(link) == []  # no read for an empty file
    assert len(c.calls) == 1
    blob, eof, err = c.calls[0]
    assert blob == b"" and eof is True and err is None


# --- 14. final partial range -------------------------------------------------
def test_final_partial_range(qapp):
    size = 3 * CHUNK + 123  # 3 full + one 123-byte tail
    backend, link = _backend(qapp, _manifest((size,)))
    (token, _), = [backend.open_fetch("generation-1", 0)]
    c = Collector()
    backend.pull_chunk(token, c)
    reads = _reads(link)
    assert len(reads) == 4
    assert reads[3].header["offset"] == 3 * CHUNK
    assert reads[3].header["length"] == 123  # correct tail length, not a full MiB

    # drive to completion, byte-exact total
    guard = 0
    delivered = 0
    while delivered < 4 and guard < 100:
        guard += 1
        in_flight = [m for m in reads if m.header["read_id"] in backend.by_read_id]
        # include any refills (none here) then deliver the next expected in order
        pending = [m for m in _reads(link) if m.header["read_id"] in backend.by_read_id]
        backend.handle_message(_chunk_for(pending[0]))
        if c.calls and not c.calls[-1][1]:
            backend.pull_chunk(token, c)
        delivered = len(c.calls)
    total = sum(len(blob) for blob, _e, _err in c.calls)
    assert total == size
    assert c.calls[-1][1] is True


# --- 15. four simultaneous fetch streams remain independently bounded --------
def test_four_streams_independently_bounded(qapp):
    backend, link = _backend(qapp, _manifest((8 * CHUNK,) * 4))
    # Per-file window property in isolation: lift the global budget to the old
    # unbounded ceiling (16) - the production default 8 is covered by
    # test_fileprovider_global_budget.py (same pattern as its _backend(budget=)).
    backend._global_budget = 16
    tokens = [backend.open_fetch("generation-1", i)[0] for i in range(4)]
    for t in tokens:
        backend.pull_chunk(t, Collector())

    # 4 streams x 4 ranges = 16 outstanding, each stream capped at 4
    per_stream: dict[str, int] = {}
    for read_id, fetch in backend.by_read_id.items():
        per_stream[fetch.fetch_token] = per_stream.get(fetch.fetch_token, 0) + 1
    assert set(per_stream) == set(tokens)
    assert all(count <= PER_FILE_READ_WINDOW for count in per_stream.values())
    assert len(backend.by_read_id) == 16
    assert int(backend.counters.get("fp_max_outstanding_reads_per_stream", 0)) <= 4
    assert int(backend.counters.get("fp_max_global_outstanding_reads", 0)) <= 16


# --- review-fix: watchdog deadline vs serial sender (rearm on progress) -------
def test_chunk_arrival_rearms_sibling_inflight_watchdogs(qapp):
    factory = _timer_factory()
    backend, link = _backend(qapp, _manifest((8 * CHUNK,)), timer_factory=factory)
    (token, _size), = [backend.open_fetch("generation-1", 0)]
    backend.pull_chunk(token, Collector())
    reads = _reads(link)
    assert len(reads) == 4
    assert len(factory.created) == 4 and all(t.running for t in factory.created)

    # Deliver R0: its own timer disarms and the 3 still-in-flight reads' timers
    # are restarted (fresh timers), so a slow/serial sender cannot falsely time
    # out a later read whose clock was ticking since issue.
    backend.handle_message(_chunk_for(reads[0]))

    # every original per-read timer (R0..R3) is now stopped...
    assert not any(factory.created[i].running for i in range(4))
    # ...replaced by 4 running timers: R1/R2/R3 rearmed + the refill read R4.
    assert sum(1 for t in factory.created if t.running) == 4
    assert len(backend.by_read_id) == 4


# --- review-fix: zero-byte QUEUED fetch must not settle before admission ------
def test_zero_byte_queued_fetch_completes_only_after_admission(qapp):
    backend, link = _backend(qapp, _manifest((CHUNK, CHUNK, CHUNK, CHUNK, 0)))
    tokens = [backend.open_fetch("generation-1", i)[0] for i in range(5)]
    zero = tokens[4]
    assert backend.by_token[zero].state == "queued"

    c = Collector()
    backend.pull_chunk(zero, c)
    assert c.calls == []           # must NOT complete out of turn while queued
    assert zero in backend._queue

    # Complete an active fetch -> a slot frees -> the zero-byte fetch is promoted
    # and only now completes (empty EOF), exactly once.
    backend.pull_chunk(tokens[0], Collector())
    r0 = next(m for m in _reads(link) if m.header["entry_index"] == 0)
    backend.handle_message(_chunk_for(r0))

    assert c.calls == [(b"", True, None)]
    assert zero not in backend.by_token


# --- runtime acceptance selector (DUO_FP_READ_WINDOW) ------------------------
import pytest

from duo_input.transfer.fileprovider_backend import _read_window_from_env


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        (None, 4),      # unset -> production default
        ("1", 1),       # W1 acceptance variant
        ("4", 4),       # W4 acceptance variant
        ("0", 4),       # <1 -> fallback
        ("-3", 4),      # <1 -> fallback
        ("abc", 4),     # non-integer -> fallback
    ],
)
def test_read_window_from_env_selector(monkeypatch, env, expected):
    if env is None:
        monkeypatch.delenv("DUO_FP_READ_WINDOW", raising=False)
    else:
        monkeypatch.setenv("DUO_FP_READ_WINDOW", env)
    assert _read_window_from_env() == expected
