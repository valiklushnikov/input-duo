"""Host-owned durable registry of published File Provider generations.

Why this exists
---------------
``FileProviderBackend._generations`` is the host's in-memory bookkeeping of every
generation it can still serve. It is populated ONLY in the publish path
(``_register_active_generation``) and, before this store, was never reloaded at
start. After a Mac app restart it began empty, so ``open_fetch`` on an old
RETIRED generation raised ``generation not published`` -> XPC code 4 ->
``NSFileProviderError -1000``, even though the extension's durable ``ReplicaStore``
still held the item identity and the Windows ``SnapshotRegistry`` still held the
byte source keyed by ``transfer_id``.

This store is the host's OWN durable source of truth for generation-serving
recovery. It is deliberately NOT a cross-process read of the extension's private
``ReplicaStore`` (that store owns namespace/item identity, a different
responsibility) and it introduces no wire/XPC/Windows/extension change: everything
``open_fetch`` needs is the ``TransferManifest`` (entry kind/size) plus the live
peer link, and the manifest is exactly what we persist here.

Ownership boundary (do not conflate):
  * this store            -> host generation-serving recovery
  * Swift ``ReplicaStore`` -> extension namespace / File Provider item identity
  * Windows ``SnapshotRegistry`` -> currently retained remote byte source

Durability
----------
One JSON file per generation, ``<transfer_id>.json``, written atomically
(temp file in the same directory -> ``flush`` -> ``fsync`` -> ``os.replace``) so a
crash mid-update never leaves a torn record - the same primitive the rest of the
project relies on for durable state. ``transfer_id`` is a validated short ASCII
token (``model.require_transfer_id``: ``[0-9A-Za-z_-]{1,64}``), safe as a filename;
a defensive containment check rejects any id that would escape the directory.

Corruption is per-record: ``load_all`` skips (and reports) a single bad file and
keeps loading the rest, never crashing startup on one malformed generation.

Qt-free on purpose (like ``model``/``fileprovider_replica``): it may run on either
side of the process barrier and must not pull PySide6 in.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from .fileprovider_replica import STATE_ACTIVE, STATE_RETIRED, _STATES
from .model import TransferManifest, require_transfer_id

logger = logging.getLogger(__name__)

#: Schema of one persisted host generation record. Bumped only with a matching
#: reader change; ``load_all`` skips records whose schema it does not recognise
#: rather than guessing at a migration.
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PersistedGeneration:
    """The minimum durable state needed to reconstruct a serviceable generation.

    No runtime-only state (in-use refs, fetch counters, pending replies) is ever
    persisted - those are rebuilt fresh at zero on rehydrate.
    """

    transfer_id: str
    manifest: TransferManifest
    state: str  # STATE_ACTIVE | STATE_RETIRED
    created_ns: int
    quiesced: bool


class GenerationRegistryStore:
    """Durable, atomically-written, per-generation JSON records on disk."""

    def __init__(self, directory: Path | str) -> None:
        self._dir = Path(directory)

    def _path_for(self, transfer_id: str) -> Path:
        # transfer_id is a validated ASCII token, but keep a defence-in-depth
        # containment check (mirrors staging._contained) so a bad id can never
        # write outside the registry directory.
        require_transfer_id(transfer_id)
        target = self._dir / f"{transfer_id}.json"
        base = self._dir.resolve()
        resolved = (base / f"{transfer_id}.json").resolve()
        if resolved.parent != base:
            raise ValueError(f"transfer_id escapes the store directory: {transfer_id!r}")
        return target

    def save(self, record: PersistedGeneration) -> None:
        """Atomically create/replace the durable record for one generation.

        Callers persist BEFORE exposing the generation as serviceable (publish)
        or acknowledging a state change (retire), so a crash can lose a not-yet-
        durable generation but never surface one whose metadata was never written.
        """
        if record.state not in _STATES:
            raise ValueError(f"unknown generation state {record.state!r}")
        target = self._path_for(record.transfer_id)
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "transfer_id": record.transfer_id,
            "manifest": record.manifest.to_dict(),
            "state": record.state,
            "created_ns": record.created_ns,
            "quiesced": record.quiesced,
        }
        data = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        tmp = self._dir / f".{record.transfer_id}.{uuid.uuid4().hex}.tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, target)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def delete(self, transfer_id: str) -> None:
        """Remove the durable record - the generation is no longer serviceable
        by the host (its runtime bookkeeping was dropped). Idempotent: a missing
        file is not an error (best-effort, like the host's tombstone GC)."""
        try:
            target = self._path_for(transfer_id)
        except ValueError:
            return
        try:
            os.unlink(target)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("could not delete durable generation record %s", transfer_id)

    def load_all(self) -> tuple[list[PersistedGeneration], list[tuple[str, str]]]:
        """Read every durable record. Returns ``(loaded, skipped)`` where
        ``skipped`` is ``(id_or_filename, reason)`` per unreadable/invalid record.
        A single corrupt record never aborts the load of the rest."""
        loaded: list[PersistedGeneration] = []
        skipped: list[tuple[str, str]] = []
        if not self._dir.is_dir():
            return loaded, skipped
        for path in sorted(self._dir.glob("*.json")):
            try:
                record = self._parse(path.read_bytes())
            except (OSError, ValueError) as error:
                skipped.append((path.stem, str(error)))
                continue
            loaded.append(record)
        return loaded, skipped

    @staticmethod
    def _parse(data: bytes) -> PersistedGeneration:
        raw = json.loads(data.decode("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("record must be a JSON object")
        if raw.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {raw.get('schema_version')!r}")
        state = raw.get("state")
        if state not in _STATES:
            raise ValueError(f"unknown generation state {state!r}")
        created_ns = raw.get("created_ns")
        if not isinstance(created_ns, int) or isinstance(created_ns, bool):
            raise ValueError("created_ns must be an int")
        quiesced = raw.get("quiesced")
        if not isinstance(quiesced, bool):
            raise ValueError("quiesced must be a bool")
        manifest = TransferManifest.from_dict(raw.get("manifest"))
        transfer_id = require_transfer_id(raw.get("transfer_id"))
        if transfer_id != manifest.transfer_id:
            raise ValueError("transfer_id does not match the manifest")
        return PersistedGeneration(
            transfer_id=transfer_id,
            manifest=manifest,
            state=state,
            created_ns=created_ns,
            quiesced=quiesced,
        )


__all__ = [
    "SCHEMA_VERSION",
    "STATE_ACTIVE",
    "STATE_RETIRED",
    "GenerationRegistryStore",
    "PersistedGeneration",
]
