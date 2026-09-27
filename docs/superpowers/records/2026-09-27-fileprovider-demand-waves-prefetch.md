# Phase G — demand waves and the effect of sibling prefetch (2026-09-27)

Diagnostic only. B-mixed-22, a fresh dataless generation per run (100/100 SF_DATALESS
before each paste), W4/B8 defaults, 0 stale-generation events, every run 100/100
byte-exact, 0 fetch failures, 0 permit leaks, 0 bound violations.
Condition B and C are local diagnostic builds (one line
`FinderBurstDownloadCoordinator(burstWaveSize: N)`), never committed; the production
bundle was reinstalled afterwards.
Waves are mechanical: extension active fetches (fetch_enter .. fetch_contents_returned)
0 → >0 → 0. Demand origin from the extension's own classification
(`scheduler_origin` FINDER/BURST, `KNOWN_PREFETCH_REQUESTED` vs
`NO_PREFETCH_REQUEST_RECORDED`) plus `request_download_call` timestamps.

| metric | A prod (wave 8) | A2 prod (control) | B no prefetch (0) | C prefetch all (100) |
|---|---|---|---|---|
| paste → first fetch | n/a | 0.300 | 0.441 | 0.295 |
| first fetch → last extension done | 11.367 | 12.294 | 83.277 | 10.019 |
| **paste → last extension done** | n/a | **12.595** | **83.718** | **10.314** |
| fetches / FILE_READs | 100/131 | 100/131 | 100/131 | 100/131 |
| origin FINDER / BURST | 12/88 | 12/88 | 100/0 | 2/98 |
| waves | 24 | 25 | 100 (all size 1) | 22 |
| zero-active gaps total / max / >100 ms | 3.906 / 1.047 / 4 | 2.018 / 0.665 / 3 | 65.821 / 0.790 / 99 | 1.234 / 0.646 / 1 |
| fetch concurrency p50 / p95 / max | 2/4/4 | 2/4/4 | 1/1/1 | 3/4/4 |

- B: Finder alone demands strictly one item at a time; every inter-item gap is
  0.59–0.79 s (p50 0.670) regardless of size — a Finder/fileproviderd per-item cadence.
- Every >100 ms gap in every condition: host active fetches 0, permits 0, host queue 0,
  budget not saturated, window not saturated, 0 outstanding requestDownloadForItem;
  the gap ends with a genuine FINDER demand (NO_PREFETCH_REQUEST_RECORDED). Transport
  backpressure: none.
- A/A2: the >100 ms gaps sit exactly at prefetch-wave boundaries — the bounded wave (8)
  is exhausted and, by design, only a new genuine Finder demand opens the next wave, so
  Finder's ~0.67 s step is exposed each time. C removes all of them except the first
  (items 0→1, before the second genuine demand opens the wave).
- fileproviderd bookkeeping (~800 enumerator notifications, ~200 owner-changed,
  200–270 NSProgress unpublishing, ~200 provides) is the same in all four conditions,
  including B with zero requestDownloadForItem — not induced by prefetch.

Classification: prefetch is strongly beneficial (B 83.7 s vs ~10–12.6 s). The
remaining BETWEEN_DEMAND_WAVES idle is Finder's demand cadence exposed at our bounded
wave boundaries (demand-side, not transport). Single runs per condition; network drift
between runs not bracketed (C and A2 were consecutive, ~1 min apart).
