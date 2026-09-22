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


def _error_code(error) -> int:
    """DuoFPErrorDomain code from either a real NSError (darwin) or the
    RuntimeError('DuoFPErrorDomain:<n>') fallback used off darwin."""
    code = getattr(error, "code", None)
    if callable(code):
        return int(code())
    return int(str(error).rsplit(":", 1)[1])


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


def test_store_rejects_path_traversal_id(tmp_path):
    store = GenerationRegistryStore(tmp_path / "gens")
    import pytest

    with pytest.raises(ValueError):
        store.save(_record("../escape"))
