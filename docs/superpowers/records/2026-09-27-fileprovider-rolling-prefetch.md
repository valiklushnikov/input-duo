# Phase H — bounded rolling prefetch (2026-09-27)

IMPLEMENTATION 53505ef0 (Swift extension only; no wire/XPC/transport/host change).

## Policy
- Old: two genuine Finder requests open a fixed wave of 8 `requestDownloadForItem`;
  completions never open another wave; only a new genuine Finder request opens the
  next one → transport idle for one Finder step (~0.67 s) at every wave boundary.
- New: the same opening rule, then a rolling horizon: any successful fetch completion
  (or Finder taking over an in-horizon item) refills up to PREFETCH_HORIZON = 8
  coordinator-owned outstanding requests, issued asynchronously on a serial queue.
- Old guard purpose: (1) our own prefetch callbacks must never count as Finder demand
  (no chain reaction materializing a whole generation); (2) a single-file open never
  starts speculation; (3) implicitly, speculation never ran more than one wave ahead
  of Finder's observed progress.
- Replacement invariants: (1) and (2) kept verbatim; (3) replaced by
  outstanding ≤ 8 + generation must be current + demand lease (replenish only within
  10 s of the last genuine Finder request, because a Finder cancel is invisible to
  the extension) + stop on cancelled fetch / failed prefetch fetch / failed request;
  issued requests without a fetch expire after 10 s (their late fetch stays ours).

## Tests
Swift 152/0: 11 new RollingPrefetchTests (open, fill ≤ 8, replenish without Finder,
drain 1000 items with 0 duplicates and max outstanding ≤ 8, seen items skipped,
issued-without-fetch expiry, cancellation stop on a 10 000-item set, lease bound after
an unobservable cancel, retired generation cannot replenish, failure without loop or
slot leak, small set, Finder-only behaviour); 2 old wave-semantics assertions updated.
Build gate 441 passed.

## Runtime (B-mixed-22, fresh dataless generation, W4/B8, production bundle 53505ef0)
| | A2 baseline (waves) | H1 rolling | C diag (all at once) |
|---|---|---|---|
| paste → first fetch | 0.300 | 0.407 | 0.295 |
| paste → last extension done | 12.595 | **10.472** | 10.314 |
| zero-active total / max | 2.018 / 0.665 | **1.327 / 0.802** | 1.234 / 0.646 |
| >100 ms zero-active gaps | 3 | **1** (initial #0→#1) | 1 |
| Finder / coordinator fetches | 12 / 88 | 2 / 98 | 2 / 98 |
| coordinator outstanding max | 8 (wave) | 8 | 98 |
100/100 byte-exact, 131 FILE_READ, 0 failures, 0 permit leaks, 0 bound violations
(maxG 8, per-fetch 4), 0 double completions, 0 duplicate prefetch requests, 0 ack
errors, 0 suppressed claims. Single run per condition (A2 ~1 h earlier).
Former wave boundaries: item 10 requested at +6.604 s by the completion of item 3,
item 55 at +8.754 s by the completion of item 47 — no Finder request involved.
