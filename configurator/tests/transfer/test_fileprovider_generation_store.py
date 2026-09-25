"""Restart durability of the host generation registry.

The extension's ReplicaStore and the Windows SnapshotRegistry both survive a Mac
app restart, and the peer link reconnects. The one thing that did NOT survive was
``FileProviderBackend._generations`` - populated only in the publish path, never
reloaded at start - so after a restart ``open_fetch`` on an old RETIRED
generation raised ``generation not published`` (-> XPC code 4 -> -1000).

These tests pin a host-owned, durable ``GenerationRegistryStore`` that is written
on every generation lifecycle transition and rehydrated into ``_generations``
when a fresh backend is constructed - with NO new publish, NO FILE_OFFER replay,
NO Windows re-advertisement, and NO wire/XPC/extension change.
"""

from __future__ import annotations

import json

from duo_input.clipboard.wire import Message, MessageType
from duo_input.transfer.fileprovider_backend import (
    FileProviderBackend,
    _GenerationState,
)
from duo_input.transfer.fileprovider_generation_store import (
    SCHEMA_VERSION,
    STATE_ACTIVE,
    STATE_RETIRED,
    GenerationRegistryStore,
    PersistedGeneration,
)
from duo_input.transfer.model import ENTRY_FILE, TransferEntry, TransferManifest
from test_fileprovider_scheduler import _error_code


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


def _manifest(transfer_id: str, sizes: tuple[int, ...] = (3,)) -> TransferManifest:
    return TransferManifest(
        transfer_id=transfer_id,
        entries=tuple(
            TransferEntry(path=f"f{i}.bin", kind=ENTRY_FILE, size=size, mtime_ns=7)
            for i, size in enumerate(sizes)
        ),
        skipped=(),
        drop_effect=1,
    )


def _backend(store, *, link=None, clock=None, generation_ttl_ns=None, max_generations=None):
    kwargs = {"generation_store": store}
    if clock is not None:
        kwargs["clock"] = clock
    if generation_ttl_ns is not None:
        kwargs["generation_ttl_ns"] = generation_ttl_ns
    if max_generations is not None:
        kwargs["max_generations"] = max_generations
    backend = FileProviderBackend(
        FakeClient(FakeRemote()),
        object(),
        lambda _urls: None,
        **kwargs,
    )
    backend.attach_link(link or FakeLink())
    return backend


def _publish(backend, transfer_id: str, sizes: tuple[int, ...] = (3,)):
    manifest = _manifest(transfer_id, sizes)
    epoch = backend.handle_offer(manifest)
    backend.authorize(True, epoch)
    return manifest


def test_retired_generation_survives_restart_and_is_fetchable(qapp, tmp_path):
    store_dir = tmp_path / "generations"

    # --- host run #1: publish A, then B (which RETIRES A, record durably kept)
    backend_a = _backend(GenerationRegistryStore(store_dir))
    _publish(backend_a, "A")
    _publish(backend_a, "B")

    # --- host restart: a brand-new backend over the SAME durable store, NO
    #     publishGeneration, NO FILE_OFFER, Windows never re-advertises.
    link = FakeLink()
    backend_b = _backend(GenerationRegistryStore(store_dir), link=link)

    # The old RETIRED generation A is rehydrated and fetchable again.
    assert backend_b._generations["A"].state is _GenerationState.RETIRED
    token, size = backend_b.open_fetch("A", 0)
    assert size == 3

    backend_b.pull_chunk(token)
    reads = [m for m in link.sent if m.type is MessageType.FILE_READ]
    assert len(reads) == 1
    assert reads[0].header["transfer_id"] == "A"
    assert reads[0].header["entry_index"] == 0
    assert reads[0].header["offset"] == 0


def test_active_generation_rehydrates_as_active(qapp, tmp_path):
    store_dir = tmp_path / "generations"
    _publish(_backend(GenerationRegistryStore(store_dir)), "A")

    backend = _backend(GenerationRegistryStore(store_dir))

    assert backend._generations["A"].state is _GenerationState.ACTIVE_CLIPBOARD
    assert backend._active_transfer_id == "A"


def test_multiple_generations_preserve_exact_state_and_manifest(qapp, tmp_path):
    store_dir = tmp_path / "generations"
    backend_a = _backend(GenerationRegistryStore(store_dir))
    _publish(backend_a, "A", sizes=(3,))
    _publish(backend_a, "B", sizes=(5, 6))
    _publish(backend_a, "C", sizes=(9,))  # A, B retired; C active

    backend = _backend(GenerationRegistryStore(store_dir))

    assert backend._generations["A"].state is _GenerationState.RETIRED
    assert backend._generations["B"].state is _GenerationState.RETIRED
    assert backend._generations["C"].state is _GenerationState.ACTIVE_CLIPBOARD
    # exact manifest reconstruction (entry sizes/paths survive the round trip)
    assert [e.size for e in backend._generations["B"].manifest.entries] == [5, 6]
    assert backend._generations["B"].manifest.entries[0].path == "f0.bin"


def test_open_fetch_after_restart_needs_no_republish(qapp, tmp_path):
    store_dir = tmp_path / "generations"
    _publish(_backend(GenerationRegistryStore(store_dir)), "A")

    backend = _backend(GenerationRegistryStore(store_dir))
    remote = backend._client.remote()

    backend.open_fetch("A", 0)

    # No publishGeneration and no FILE_OFFER replay were needed to serve A.
    assert remote.published == []


def test_gen_in_use_is_zero_after_rehydrate(qapp, tmp_path):
    store_dir = tmp_path / "generations"
    backend_a = _backend(GenerationRegistryStore(store_dir))
    _publish(backend_a, "A")
    backend_a.open_fetch("A", 0)  # takes an in-use ref in run #1
    assert backend_a._gen_in_use["A"] == 1

    backend = _backend(GenerationRegistryStore(store_dir))

    # Runtime-only state is NOT restored - it starts fresh at zero.
    assert backend._gen_in_use["A"] == 0


def test_corrupt_record_is_skipped_and_valid_ones_still_load(qapp, tmp_path):
    store_dir = tmp_path / "generations"
    _publish(_backend(GenerationRegistryStore(store_dir)), "A")
    # A second, corrupt record on disk beside the valid one.
    (store_dir / "GARBAGE.json").write_text("{ this is not json", encoding="utf-8")

    backend = _backend(GenerationRegistryStore(store_dir))

    assert "A" in backend._generations  # valid record survived
    assert backend.counters.get("fp_generation_rehydrate_skipped") == 1
    # backend is usable: the valid generation is still fetchable.
    token, _size = backend.open_fetch("A", 0)
    assert backend.by_token[token].generation_id == "A"


def test_deleted_generation_does_not_rehydrate(qapp, tmp_path):
    store_dir = tmp_path / "generations"
    # Count budget of 1 retired generation: publishing C evicts the oldest
    # retired (A) via _gc -> deleteGeneration (tombstone), keeping B and C.
    backend_a = _backend(GenerationRegistryStore(store_dir), max_generations=1)
    _publish(backend_a, "A")
    _publish(backend_a, "B")  # A retired + quiesced (ref 0)
    _publish(backend_a, "C")  # retires B, runs _gc -> deletes oldest retired A
    assert backend_a._client.remote().deleted == ["A"]

    backend = _backend(GenerationRegistryStore(store_dir))

    # A was tombstoned by host GC - it must not come back to life on restart.
    assert "A" not in backend._generations
    assert "B" in backend._generations
    assert "C" in backend._generations


def _capture():
    box = {}

    def reply(blob, eof, error) -> None:
        box["blob"], box["eof"], box["error"] = blob, eof, error

    return box, reply


def test_source_missing_is_not_reclassified_as_unauthorized(qapp, tmp_path):
    """After restart the host DOES know the generation; if the Windows snapshot
    is gone the read fails as SourceMissing (code 1), never as the old
    'generation not published' -> Unauthorized (code 4) / -1000."""
    store_dir = tmp_path / "generations"
    _publish(_backend(GenerationRegistryStore(store_dir)), "A")

    link = FakeLink()
    backend = _backend(GenerationRegistryStore(store_dir), link=link)

    # open_fetch succeeds - the generation IS found (not Unauthorized).
    token, _size = backend.open_fetch("A", 0)
    box, reply = _capture()
    backend.pull_chunk(token, reply=reply)
    [read] = [m for m in link.sent if m.type is MessageType.FILE_READ]

    # Windows replies FILE_ERROR source_missing (its snapshot was evicted).
    backend.handle_message(
        Message(
            MessageType.FILE_ERROR,
            {
                "transfer_id": "A",
                "entry_index": 0,
                "offset": 0,
                "read_id": read.header["read_id"],
                "reason": "source_missing",
            },
            b"",
        )
    )

    # SourceMissing == DuoFPErrorDomain code 1, NOT code 4 (Unauthorized).
    assert _error_code(box["error"]) == 1


# --- store unit --------------------------------------------------------------


def _record(transfer_id: str, state: str = STATE_ACTIVE) -> PersistedGeneration:
    return PersistedGeneration(
        transfer_id=transfer_id,
        manifest=_manifest(transfer_id, sizes=(4, 8)),
        state=state,
        created_ns=123,
        quiesced=(state == STATE_RETIRED),
    )


def test_store_roundtrips_a_record(tmp_path):
    store = GenerationRegistryStore(tmp_path / "gens")
    store.save(_record("A", STATE_RETIRED))

    loaded, skipped = store.load_all()

    assert skipped == []
    [rec] = loaded
    assert rec.transfer_id == "A"
    assert rec.state == STATE_RETIRED
    assert rec.quiesced is True
    assert rec.created_ns == 123
    assert [e.size for e in rec.manifest.entries] == [4, 8]


def test_store_save_is_atomic_and_leaves_no_temp_files(tmp_path):
    directory = tmp_path / "gens"
    store = GenerationRegistryStore(directory)
    store.save(_record("A"))
    store.save(_record("A", STATE_RETIRED))  # replace in place

    json_files = list(directory.glob("*.json"))
    tmp_files = list(directory.glob(".*.tmp"))
    assert len(json_files) == 1
    assert tmp_files == []
    loaded, _skipped = store.load_all()
    assert loaded[0].state == STATE_RETIRED  # last write won


def test_store_delete_is_idempotent(tmp_path):
    store = GenerationRegistryStore(tmp_path / "gens")
    store.save(_record("A"))

    store.delete("A")
    store.delete("A")  # no error on a missing record

    loaded, _skipped = store.load_all()
    assert loaded == []


def test_store_skips_wrong_schema_version(tmp_path):
    directory = tmp_path / "gens"
    directory.mkdir(parents=True)
    (directory / "A.json").write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION + 1,
                "transfer_id": "A",
                "manifest": _manifest("A").to_dict(),
                "state": STATE_ACTIVE,
                "created_ns": 1,
                "quiesced": False,
            }
        ),
        encoding="utf-8",
    )

    loaded, skipped = GenerationRegistryStore(directory).load_all()

    assert loaded == []
    assert len(skipped) == 1


def test_store_skips_deeply_nested_corrupt_record(tmp_path):
    directory = tmp_path / "gens"
    directory.mkdir(parents=True)
    (directory / "A.json").write_text("[" * 100_000, encoding="utf-8")

    loaded, skipped = GenerationRegistryStore(directory).load_all()

    assert loaded == []
    assert len(skipped) == 1


def test_store_rejects_path_traversal_id(tmp_path):
    store = GenerationRegistryStore(tmp_path / "gens")
    import pytest

    with pytest.raises(ValueError):
        store.save(_record("../escape"))


# --- restart-path namespace GC (RETIRED -> TOMBSTONED on extension connect) ---
def _connectable_client(remote):
    """A client fake that, unlike the module ``FakeClient``, exposes the real
    ``connected`` Qt signal the production client emits when the extension
    control connection is (re)established - the hook the restart namespace GC
    rides on so ``deleteGeneration`` reaches a LIVE extension XPC."""
    from PySide6.QtCore import QObject, Signal

    class ConnectableFakeClient(QObject):
        connected = Signal()

        def __init__(self, remote_):
            super().__init__()
            self._remote = remote_

        def remote(self):
            return self._remote

    return ConnectableFakeClient(remote)


_TTL_NS = 24 * 3600 * 1_000_000_000


def test_restart_gc_tombstones_ttl_expired_retired_on_extension_connect(qapp, tmp_path):
    """After a restart the RETIRED->TOMBSTONED transition (TTL/budget) must run
    once the extension is reachable: ``_rehydrate_generations`` never invoked
    ``_gc``, so TTL-expired retired generations lingered in the namespace until
    the next live publish. They must instead be tombstoned when the extension
    control connection is established."""
    store_dir = tmp_path / "generations"
    t = [1_000_000_000]
    clock = lambda: t[0]  # noqa: E731

    # Live path: publish A..I -> I active, A..H retired (8 = at budget, none GC'd).
    backend_a = _backend(
        GenerationRegistryStore(store_dir), clock=clock,
        generation_ttl_ns=_TTL_NS, max_generations=8,
    )
    for name in ["A", "B", "C", "D", "E", "F", "G", "H", "I"]:
        _publish(backend_a, name)

    # Restart 25h later (past the 24h TTL). Fresh backend + connectable client.
    t[0] = 1_000_000_000 + 25 * 3600 * 1_000_000_000
    remote = FakeRemote()
    client = _connectable_client(remote)
    backend_b = FileProviderBackend(
        client, object(), lambda _urls: None,
        generation_store=GenerationRegistryStore(store_dir),
        clock=clock, generation_ttl_ns=_TTL_NS, max_generations=8,
    )
    retired_ids = {"A", "B", "C", "D", "E", "F", "G", "H"}
    # Rehydrated, but nothing tombstoned yet (extension not connected).
    assert retired_ids <= set(backend_b._generations)
    assert remote.deleted == []

    # Extension connects -> restart namespace GC sweeps the TTL-expired retired.
    client.connected.emit()

    assert set(remote.deleted) == retired_ids           # all 8 tombstoned
    assert "I" not in remote.deleted                    # active never deleted
    assert backend_b._generations["I"].state is _GenerationState.ACTIVE_CLIPBOARD
    for gid in retired_ids:
        assert gid not in backend_b._generations        # dropped from host tracking


def test_restart_gc_keeps_active_and_within_budget_recent_retired(qapp, tmp_path):
    """The restart sweep must NOT tombstone the active generation nor recent
    (within-TTL, within-budget) retired ones - it applies exactly the existing
    TTL/budget policy, never an aggressive wipe."""
    store_dir = tmp_path / "generations"
    t = [1_000_000_000]
    clock = lambda: t[0]  # noqa: E731

    backend_a = _backend(
        GenerationRegistryStore(store_dir), clock=clock,
        generation_ttl_ns=_TTL_NS, max_generations=8,
    )
    for name in ["A", "B", "C"]:  # C active, A/B retired (within budget + TTL)
        _publish(backend_a, name)

    # Restart only 1h later (well within the 24h TTL).
    t[0] = 1_000_000_000 + 3600 * 1_000_000_000
    remote = FakeRemote()
    client = _connectable_client(remote)
    backend_b = FileProviderBackend(
        client, object(), lambda _urls: None,
        generation_store=GenerationRegistryStore(store_dir),
        clock=clock, generation_ttl_ns=_TTL_NS, max_generations=8,
    )

    client.connected.emit()

    # Nothing tombstoned: 2 retired <= budget 8 and both < TTL.
    assert remote.deleted == []
    assert backend_b._generations["C"].state is _GenerationState.ACTIVE_CLIPBOARD
    assert set(backend_b._generations) == {"A", "B", "C"}


def test_ten_sequential_generations_stay_bounded_across_restart_no_resurrection(qapp, tmp_path):
    """Offline acceptance (§14): ten unique 'cold' generations published in a
    row, then a restart, must leave a BOUNDED namespace (<= budget retired + one
    active), the current generation still directly fetchable (no -1000/-1005),
    and tombstoned generations must NOT resurrect after another restart."""
    store_dir = tmp_path / "generations"
    hour = 3600 * 1_000_000_000
    t = [hour]
    clock = lambda: t[0]  # noqa: E731
    ttl = _TTL_NS  # 24h

    backend = _backend(
        GenerationRegistryStore(store_dir), clock=clock,
        generation_ttl_ns=ttl, max_generations=8,
    )
    names = [f"G{i:02d}" for i in range(10)]
    for name in names:
        _publish(backend, name)
        t[0] += 5 * hour  # 5h between generations -> total span 45h

    # Live path already bounds the durable set (budget + TTL GC on each publish).
    active = [g for g in backend._generations.values()
              if g.state is _GenerationState.ACTIVE_CLIPBOARD]
    retired = [g for g in backend._generations.values()
               if g.state is _GenerationState.RETIRED]
    assert len(active) == 1
    assert active[0].transfer_id == names[-1]
    assert len(retired) <= 8
    # The active generation is directly fetchable - no -1000 (ValueError) path.
    token, size = backend.open_fetch(names[-1], 0)
    assert token and size == 3

    deleted_before_restart = set(backend._client.remote().deleted)
    assert deleted_before_restart, "old generations past TTL were tombstoned live"

    # --- restart: fresh backend + connectable client, a bit later.
    t[0] += hour
    remote2 = FakeRemote()
    client2 = _connectable_client(remote2)
    backend2 = FileProviderBackend(
        client2, object(), lambda _urls: None,
        generation_store=GenerationRegistryStore(store_dir),
        clock=clock, generation_ttl_ns=ttl, max_generations=8,
    )
    backend2.attach_link(FakeLink())
    # Tombstoned-then-store-deleted generations never rehydrate (no resurrection).
    for gid in deleted_before_restart:
        assert gid not in backend2._generations

    client2.connected.emit()  # restart namespace GC sweep

    active2 = [g for g in backend2._generations.values()
               if g.state is _GenerationState.ACTIVE_CLIPBOARD]
    retired2 = [g for g in backend2._generations.values()
                if g.state is _GenerationState.RETIRED]
    assert len(active2) == 1 and active2[0].transfer_id == names[-1]
    assert len(retired2) <= 8
    # Current generation still fetchable after restart + sweep (no -1000/-1005).
    token2, size2 = backend2.open_fetch(names[-1], 0)
    assert token2 and size2 == 3

    # --- second restart: nothing the sweep tombstoned comes back.
    swept = set(remote2.deleted)
    t[0] += hour
    backend3 = FileProviderBackend(
        _connectable_client(FakeRemote()), object(), lambda _urls: None,
        generation_store=GenerationRegistryStore(store_dir),
        clock=clock, generation_ttl_ns=ttl, max_generations=8,
    )
    backend3.attach_link(FakeLink())
    for gid in swept:
        assert gid not in backend3._generations       # no resurrection
    assert names[-1] in backend3._generations         # active preserved
