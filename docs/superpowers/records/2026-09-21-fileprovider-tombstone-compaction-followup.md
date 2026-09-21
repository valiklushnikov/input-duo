# Follow-up: FILE_PROVIDER_TOMBSTONE_COMPACTION (Phase 2, deferred)

**Date:** 2026-09-21
**Status:** DEFERRED — do not implement without proving the condition below.
**Context:** generation-lifetime / tombstone phase
(`docs/superpowers/specs/2026-09-21-fileprovider-generation-lifetime-tombstone-design.md`).

## What the tombstone phase shipped

`deleteGeneration` now **tombstones** instead of physically deleting: a durable
delete change in the `ChangeJournal` plus a working-set signal, while the
backing `ReplicaStore` record is **kept indefinitely**
(`PHYSICAL_REPLICA_DELETE = DISABLED`). This closed the `-1005 → Finder -36`
class of bug by guaranteeing a File Provider item identity never outlives the
metadata needed to resolve it.

The journal records an `observed.json` high-water mark (the max `from:` anchor
the system has passed to `enumerateChanges`). This phase it is **telemetry
only** — never a permission to delete.

## The open question

> What public-API-observable condition is **sufficient** to safely compact /
> physically delete an old tombstone + its `ReplicaStore` record?

Known facts (from SDK headers, verified this phase):

- There is **no explicit per-deletion "applied" acknowledgement** in the public
  File Provider API.
- The system durably persists the sync anchor from
  `finishEnumeratingChangesUpToSyncAnchor:` and passes it back as `from:` on the
  next `enumerateChanges`, so `enumerateChanges(from: A ≥ N)` is a strong
  *progress* signal that the deletion at revision `N` was delivered — but it is
  **not sufficient**: a quiet channel can leave the anchor un-advanced even
  after the deletion was applied.
- `enumeratorForMaterializedItems()` proves a specific item is not currently
  materialized — a necessary safety AND, not sufficient on its own.

## Constraints for any future implementation

- **Do NOT physically delete** until a sufficient condition is proven.
- No arbitrary TTL. No arbitrary generation count. No `removeDomain` GC.
- Metadata is tiny (~0.6 MB for several large generations) — consistency beats
  reclaiming a few hundred KB.
- `observedAnchor` telemetry gathered this phase is the input to this research.
- `purge_stale_generations` (Python) is also gated on this: it is currently
  unwired because a host restart empties `_generations` and would otherwise
  tombstone records whose item identities File Provider still holds.

## Candidate direction (unproven)

Physically delete a tombstone only when BOTH hold, plus a conservative grace:
`observedAnchor ≥ tombstoneRevision` AND
`materialized ∩ itemIdentifiers(generation) == ∅`. Validate against real
multi-generation reboot behaviour before trusting it.
