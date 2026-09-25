"""Global bounded FILE_READ budget over the per-file W4 window.

RED-first tests for the round-robin global read-budget scheduler (design doc
docs/superpowers/specs/2026-09-24-fileprovider-global-read-budget-design.md).

The per-file window (PER_FILE_READ_WINDOW = 4) is unchanged; these tests assert
the NEW global invariant ``len(by_read_id) <= GLOBAL_READ_BUDGET`` and the
round-robin fairness that keeps it from starving an eligible fetch. Against the
current backend (which has NO global read-count gate: 4 active fetches x 4
per-file reads = up to 16 concurrent reads) the budget-bounded cases FAIL
cleanly - ``backend._global_budget = N`` is an inert attribute the current code
ignores, so it still issues 16 and the ``<= N`` assertions trip (spec §15/§19).
"""

from __future__ import annotations

import json

import pytest

from duo_input.clipboard.wire import MAX_FILE_CHUNK_BYTES, Message, MessageType
from duo_input.transfer.fileprovider_backend import (
    PER_FILE_READ_WINDOW,
    FileProviderBackend,
    RangeState,
)
from duo_input.transfer.model import ENTRY_FILE, TransferEntry, TransferManifest

CHUNK = MAX_FILE_CHUNK_BYTES


# --- fakes (same idiom as test_fileprovider_window.py) -----------------------
class FakeRemote:
    def publishGeneration_reply_(self, record: bytes, reply) -> None:  # noqa: N802
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


def _backend(qapp, manifest: TransferManifest, budget: int | None = None):
    link = FakeLink()
    backend = FileProviderBackend(
        FakeClient(FakeRemote()),
        object(),
        lambda _urls: None,
    )
    backend.attach_link(link)
    epoch = backend.handle_offer(manifest)
    backend.authorize(True, epoch)
    if budget is not None:
        # The knob the feature will honour. On the CURRENT backend this attribute
        # does not exist and is simply ignored, so the global-bound assertions
        # below fail cleanly (16 outstanding) rather than erroring.
        backend._global_budget = budget
    return backend, link


def _blob(read: Message) -> bytes:
    offset = read.header["offset"]
    length = read.header["length"]
    marker = (offset // CHUNK) % 256
    return bytes([marker]) * length


def _chunk_for(read: Message) -> Message:
    return Message(MessageType.FILE_CHUNK, dict(read.header), _blob(read))


def _reads(link: FakeLink) -> list[Message]:
    return [m for m in link.sent if m.type is MessageType.FILE_READ]


def _expected(size: int) -> bytes:
    out = bytearray()
    off = 0
    while off < size:
        length = min(CHUNK, size - off)
        out += bytes([(off // CHUNK) % 256]) * length
        off += length
    return bytes(out)


class Collector:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes | None, bool, object]] = []

    def __call__(self, blob, eof, error) -> None:
        self.calls.append((blob, eof, error))


def _drive_to_completion(backend, link, tokens, budget=None):
    """Drive every fetch to EOF with a strictly-serial parked consumer per
    fetch, delivering the in-order cursor range each pass. Asserts the global
    budget bound at every step and returns each token's assembled bytes."""
    boxes: dict[str, list] = {t: [] for t in tokens}
    assembled: dict[str, bytearray] = {t: bytearray() for t in tokens}
    done: set[str] = set()

    def park(t):
        backend.pull_chunk(t, lambda *a, tt=t: boxes[tt].append(a))

    for t in tokens:
        park(t)

    max_global = 0
    guard = 0
    while len(done) < len(tokens):
        guard += 1
        assert guard < 200_000, "livelock: no fetch progressed"
        max_global = max(max_global, len(backend.by_read_id))
        if budget is not None:
            assert (
                len(backend.by_read_id) <= budget
            ), f"global budget {budget} exceeded: {len(backend.by_read_id)}"

        # Drain any completed replies, then re-park a serial consumer.
        for t in list(tokens):
            if t in done:
                continue
            for blob, eof, err in boxes[t]:
                assert err is None, f"unexpected error for {t}: {err}"
                if blob:
                    assembled[t] += blob
                if eof:
                    done.add(t)
            boxes[t] = []
            if t not in done:
                f = backend.by_token.get(t)
                if f is None:
                    done.add(t)
                elif f.reply is None:
                    park(t)
        if len(done) == len(tokens):
            break

        # Deliver exactly one in-flight cursor range this pass.
        delivered = False
        for t in tokens:
            if t in done:
                continue
            f = backend.by_token.get(t)
            if f is None:
                done.add(t)
                continue
            rng = f.ranges.get(f.consume_offset)
            if rng is not None and rng.state is RangeState.IN_FLIGHT:
                read = next(m for m in _reads(link) if m.header["read_id"] == rng.read_id)
                backend.handle_message(_chunk_for(read))
                delivered = True
                break
        if not delivered:
            # Fallback: any in-flight range (keeps the drive from wedging if a
            # cursor range is momentarily still RECEIVED awaiting the re-park).
            for t in tokens:
                if t in done:
                    continue
                f = backend.by_token.get(t)
                if f is None:
                    continue
                inflight = [r for r in f.ranges.values() if r.state is RangeState.IN_FLIGHT]
                if inflight:
                    read = next(
                        m for m in _reads(link) if m.header["read_id"] == inflight[0].read_id
                    )
                    backend.handle_message(_chunk_for(read))
                    delivered = True
                    break
            assert delivered, "no deliverable in-flight range - livelock"

    return {t: bytes(assembled[t]) for t in tokens}, max_global


# --- budget bounds: 4 / 6 / 8 (RED: current issues up to 16) -----------------
@pytest.mark.parametrize("budget", [4, 6, 8])
def test_global_budget_never_exceeded(qapp, budget):
    backend, link = _backend(qapp, _manifest((8 * CHUNK,) * 4), budget=budget)
    tokens = [backend.open_fetch("generation-1", i)[0] for i in range(4)]

    assembled, max_global = _drive_to_completion(backend, link, tokens, budget=budget)

    for i, t in enumerate(tokens):
        assert assembled[t] == _expected(8 * CHUNK), f"fetch {i} not byte-exact"
    assert max_global <= budget
    assert int(backend.counters.get("fp_max_global_outstanding_reads", 0)) <= budget


# --- four large fetches all progress under the tightest budget (RED) ---------
def test_four_large_fetches_all_progress_under_budget_four(qapp):
    backend, link = _backend(qapp, _manifest((4 * CHUNK,) * 4), budget=4)
    tokens = [backend.open_fetch("generation-1", i)[0] for i in range(4)]

    assembled, max_global = _drive_to_completion(backend, link, tokens, budget=4)

    # every stream completed, byte-exact, none starved
    assert set(assembled) == set(tokens)
    for t in tokens:
        assert assembled[t] == _expected(4 * CHUNK)
    # every fetch actually issued reads (nobody permanently starved)
    entries_read = {m.header["entry_index"] for m in _reads(link)}
    assert entries_read == {0, 1, 2, 3}
    assert max_global <= 4


# --- scheduler issues one read per fetch per rotation, not one fetch's whole
# --- window first (RED: no round-robin scheduler exists yet) -----------------
def test_scheduler_issues_one_read_per_fetch_per_rotation(qapp):
    # With four fetches ALL eligible at the same schedule pass and a budget of
    # 8, round-robin must hand out one read per fetch per rotation
    # (0 1 2 3 0 1 2 3), never greedily fill fetch 0's whole window first
    # (0 0 0 0 ...). This is the fairness that keeps one large fetch from
    # monopolising the shared budget while peers wait (spec §7).
    backend, link = _backend(qapp, _manifest((8 * CHUNK,) * 4), budget=8)
    tokens = [backend.open_fetch("generation-1", i)[0] for i in range(4)]
    # Make all four eligible simultaneously WITHOUT a greedy per-pull borrow, so
    # the very first scheduler pass has to choose between competing fetches.
    for t in tokens:
        backend.by_token[t].pulled = True

    backend._schedule_reads()

    seq = [m.header["entry_index"] for m in _reads(link)]
    assert seq[:4] == [0, 1, 2, 3], f"first rotation not round-robin: {seq}"
    assert seq == [0, 1, 2, 3, 0, 1, 2, 3], f"not interleaved rotations: {seq}"
    assert len(backend.by_read_id) == 8


# --- releasing a permit wakes a starved eligible fetch (RED) -----------------
def test_permit_release_wakes_starved_fetch(qapp):
    backend, link = _backend(qapp, _manifest((4 * CHUNK, 4 * CHUNK)), budget=4)
    a, b = (backend.open_fetch("generation-1", i)[0] for i in range(2))

    ca = Collector()
    backend.pull_chunk(a, ca)
    # A borrows all four permits; B is eligible but budget-starved.
    assert len(backend.by_read_id) == 4
    backend.pull_chunk(b, Collector())
    assert not any(m.header["entry_index"] == 1 for m in _reads(link)), (
        "B must not read while the budget is fully held by A"
    )

    # A's first range completes -> one permit frees -> B wakes and reads.
    a_read0 = next(m for m in _reads(link) if m.header["entry_index"] == 0)
    backend.handle_message(_chunk_for(a_read0))

    assert any(m.header["entry_index"] == 1 for m in _reads(link)), (
        "freeing a permit must wake the starved fetch B"
    )
    assert len(backend.by_read_id) <= 4


# --- no budget-violation telemetry across a full drive (RED via <=4) ---------
def test_no_budget_violation_across_full_drive(qapp):
    backend, link = _backend(qapp, _manifest((5 * CHUNK, 2 * CHUNK, 8 * CHUNK, CHUNK)), budget=4)
    tokens = [backend.open_fetch("generation-1", i)[0] for i in range(4)]

    assembled, max_global = _drive_to_completion(backend, link, tokens, budget=4)

    sizes = (5 * CHUNK, 2 * CHUNK, 8 * CHUNK, CHUNK)
    for t, size in zip(tokens, sizes):
        assert assembled[t] == _expected(size)
    assert max_global <= 4
    assert int(backend.counters.get("fp_read_budget_violation", 0)) == 0
    assert int(backend.counters.get("fp_window_bound_violation", 0)) == 0


# --- borrowing: an isolated large fetch keeps the full W4 (GUARD) ------------
def test_isolated_large_fetch_keeps_full_window(qapp):
    # Critical acceptance gate (spec §8/§28): fairness must NOT shrink an
    # isolated large fetch below the per-file window.
    backend, link = _backend(qapp, _manifest((8 * CHUNK,)), budget=4)
    (token, _), = [backend.open_fetch("generation-1", 0)]

    backend.pull_chunk(token, Collector())

    fetch = backend.by_token[token]
    assert fetch.in_flight() == PER_FILE_READ_WINDOW == 4
    assert len(backend.by_read_id) == 4


# --- out-of-order RECEIVED frees its transport permit (GUARD, case 8) --------
def test_out_of_order_received_releases_transport_permit(qapp):
    backend, link = _backend(qapp, _manifest((8 * CHUNK,)), budget=4)
    (token, _), = [backend.open_fetch("generation-1", 0)]
    backend.pull_chunk(token, Collector())
    reads = _reads(link)
    assert len(reads) == 4
    assert len(backend.by_read_id) == 4

    # Deliver R2 out of order (R0 still pending): its permit releases, but the
    # window stays full (R2 held in the reorder buffer), so no new read issues.
    backend.handle_message(_chunk_for(reads[2]))

    assert len(backend.by_read_id) == 3  # R2's transport permit released
    assert len(_reads(link)) == 4  # window still full (4 live ranges) -> no refill


# --- default budget = measured production B8 --------------------------------
def test_default_budget_clamps_to_production_8(qapp):
    # No override: four active fetches x per-file window 4 would be 16
    # outstanding reads; the measured production default budget (8) clamps the
    # global in-flight set to 8.
    backend, link = _backend(qapp, _manifest((8 * CHUNK,) * 4))
    tokens = [backend.open_fetch("generation-1", i)[0] for i in range(4)]
    for t in tokens:
        backend.pull_chunk(t, Collector())

    assert len(backend.by_read_id) == 8
    assert int(backend.counters.get("fp_max_global_outstanding_reads", 0)) == 8
    assert int(backend.counters.get("fp_read_budget_violation", 0)) == 0


# --- round-robin membership carries no duplicate active token (case 23) ------
def test_rr_membership_has_no_duplicate_active_token(qapp):
    backend, link = _backend(qapp, _manifest((8 * CHUNK,) * 3), budget=6)
    tokens = [backend.open_fetch("generation-1", i)[0] for i in range(3)]
    for t in tokens:
        backend.pull_chunk(t, Collector())
    # consume a couple of ranges to re-drive the scheduler repeatedly
    for _ in range(3):
        for t in tokens:
            f = backend.by_token.get(t)
            if f is None:
                continue
            rng = f.ranges.get(f.consume_offset)
            if rng is not None and rng.state is RangeState.IN_FLIGHT:
                read = next(m for m in _reads(link) if m.header["read_id"] == rng.read_id)
                backend.handle_message(_chunk_for(read))
                backend.pull_chunk(t, Collector())

    rr = list(backend._rr)
    assert len(rr) == len(set(rr)), f"duplicate token in round-robin deque: {rr}"


# --- terminal fetch is dropped from the scheduler (case 24) ------------------
def test_terminal_fetch_removed_from_scheduler(qapp):
    backend, link = _backend(qapp, _manifest((4 * CHUNK, 4 * CHUNK)), budget=8)
    a, b = (backend.open_fetch("generation-1", i)[0] for i in range(2))
    backend.pull_chunk(a, Collector())
    backend.pull_chunk(b, Collector())
    assert a in backend._rr and b in backend._rr

    backend.cancel_fetch(a)

    assert a not in backend._rr, "cancelled fetch must leave the round-robin pool"
    assert backend.by_token.get(a) is None
    # B keeps making progress and never inherits A's stale reads.
    reads_after = len(_reads(link))
    b_read0 = next(m for m in _reads(link) if m.header["entry_index"] == 1)
    backend.handle_message(_chunk_for(b_read0))
    assert len(_reads(link)) >= reads_after  # B refilled, no crash


# --- scheduler reentrancy: a synchronous link completing a read inside send()
# --- must not corrupt round-robin state or breach the budget (spec §18) ------
class SynchronouslyCompletingLink(FakeLink):
    """send() answers each FILE_READ synchronously (inside the call), driving
    _on_chunk -> _try_deliver -> _schedule_reads reentrantly, then returns
    False (the range was already retired, not a genuine failure)."""

    def __init__(self, backend_box: list) -> None:
        super().__init__()
        self.backend_box = backend_box

    def send(self, message: Message) -> bool:
        self.sent.append(message)
        if message.type is MessageType.FILE_READ:
            self.backend_box[0].handle_message(_chunk_for(message))
        return False


def test_synchronous_link_reentrancy_stays_bounded_and_byte_exact(qapp):
    box: list = []
    link = SynchronouslyCompletingLink(box)
    backend = FileProviderBackend(
        FakeClient(FakeRemote()), object(), lambda _urls: None
    )
    box.append(backend)
    backend.attach_link(link)
    epoch = backend.handle_offer(_manifest((3 * CHUNK, 2 * CHUNK)))
    backend.authorize(True, epoch)
    backend._global_budget = 4
    a, b = (backend.open_fetch("generation-1", i)[0] for i in range(2))

    out_a, out_b = Collector(), Collector()
    # Each pull drives that whole file to EOF synchronously via the reentrant
    # completion; a serial consumer re-pulls until eof.
    for token, out, size in ((a, out_a, 3 * CHUNK), (b, out_b, 2 * CHUNK)):
        guard = 0
        while not (out.calls and out.calls[-1][1]):
            guard += 1
            assert guard < 1000
            backend.pull_chunk(token, out)
            assert len(backend.by_read_id) <= 4  # never breached mid-reentrancy

    assert b"".join(c[0] for c in out_a.calls if c[0]) == _expected(3 * CHUNK)
    assert b"".join(c[0] for c in out_b.calls if c[0]) == _expected(2 * CHUNK)
    assert backend.by_read_id == {}
    assert list(backend._rr) == []  # both fetches cleanly removed
    assert int(backend.counters.get("fp_read_budget_violation", 0)) == 0


# --- runtime acceptance selector (DUO_FP_READ_BUDGET) ------------------------
#: Measured production default (runtime 4/6/8 benchmark, 2026-09-25: B8 won).
_DEFAULT_BUDGET = 8


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        (None, _DEFAULT_BUDGET),  # unset -> measured production default
        ("4", 4),
        ("6", 6),
        ("8", 8),
        ("16", 16),  # >=16 == old unbounded-W4 control
        ("0", _DEFAULT_BUDGET),  # <1 -> fallback
        ("-2", _DEFAULT_BUDGET),
        ("abc", _DEFAULT_BUDGET),  # non-integer -> fallback
    ],
)
def test_read_budget_from_env_selector(monkeypatch, env, expected):
    # Imported inside the test so this new symbol's absence does not abort
    # collection of the (assertion-based) budget-bound RED tests above.
    from duo_input.transfer.fileprovider_backend import _read_budget_from_env

    if env is None:
        monkeypatch.delenv("DUO_FP_READ_BUDGET", raising=False)
    else:
        monkeypatch.setenv("DUO_FP_READ_BUDGET", env)
    assert _read_budget_from_env() == expected


def test_production_defaults_window_4_budget_8(monkeypatch):
    # Environment unset -> the measured production configuration W4 + B8.
    from duo_input.transfer.fileprovider_backend import (
        _read_budget_from_env,
        _read_window_from_env,
    )

    monkeypatch.delenv("DUO_FP_READ_WINDOW", raising=False)
    monkeypatch.delenv("DUO_FP_READ_BUDGET", raising=False)
    assert _read_window_from_env() == 4
    assert _read_budget_from_env() == 8
