# Follow-up: FILE_PROVIDER_TRACKED_WORKING_SET (deferred)

**Date:** 2026-09-21
**Status:** DEFERRED — separate architectural task, after E2E A–F are green.

## Context

Real E2E A exposed that the working set as "all namespace-live ACTIVE + RETIRED
items" is expensive: with many/large generations it is thousands of items, and
a one-shot enumeration saturated the extension session. The immediate fix
(this phase) keeps that **membership** but paginates delivery (128/page) and
soft-migrates the legacy `"v1"` anchor instead of expiring it.

Pagination stops the session-kill, but the working set still *conceptually*
contains thousands of retired placeholders that the system will keep trying to
sync.

## The proper model (deferred)

Apple provides first-class support for a *tracked* working set:

- `NSFileProviderManager.enumeratorForMaterializedItems()` — the set of items
  the system has actually materialized.
- `NSFileProviderManagerMaterializedSetDidChange` / `materializedItemsDidChange`
  notification — observe changes to that set.

Target working-set membership:

```
working set =
  materialized items
  + recently used / relevant items
  + items needed for reconciliation (tombstone deletes)
```

This stops treating thousands of retired placeholders as the working set at all,
rather than only learning to hand them out in pages.

## Constraints when it is picked up

- Requires threading `NSFileProviderManager` into the enumerator (currently the
  enumerator takes only `ReplicaStore` + `ChangeJournal`; the manager is
  available on the extension but not injected into the enumerator's test seam).
- Deletion reconciliation must still flow through the journal
  `enumerateChanges` (didDelete) so materialized items are dropped correctly.
- Do not regress the pagination correctness or the legacy-anchor soft migration
  shipped this phase.
