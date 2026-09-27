# Dead-generation retry blockage — root cause, fix, runtime evidence (2026-09-25)

Branch `feature/fileprovider-read-window-v2`. FIX 16d0c4ed (host only; no wire/XPC/Swift change).

## Symptom
Instrumented run T1: first fetch of a new paste started 9.2 s after the paste. Four
fetches of an older, dead generation (D2, killed by a >30 s link outage) occupied the
fileproviderd fetchContents slots: extension FetchController retried them with
`code=5` (Timeout) — attempts 3/4/5 — and T1's first fetch entered 13 ms after the
last one failed. Same chain after the 11:04 restart: 5 attempts x 30 s ≈ 2 min 40 s.

## Chain (one item, post host restart)
fileproviderd → extension fetchContents → XPC open/pull → host FILE_READ sent
(`file_read_send` logged) → Windows: snapshot unknown (the source calls
`SnapshotRegistry.release_all()` on every `attach_link` / link loss) →
`FILE_ERROR reason=source_missing` in ~10 ms → **Mac `MacReceiveRouter.handle_message`
drops it**: it forwarded file messages only to `_active_backend`, which is None until
the first FILE_OFFER of the session → no correlation → 30 s read watchdog →
`DuoFPErrorTimeout` (5) → extension treats 5 as transient → retry (maxAttempts) while
holding a fetchContents slot.

Semantic loss point: the Mac receive router. The wire already carried the truth
(`source_missing`), and host → Swift already mapped it correctly
(code 1 → `NSFileProviderError.noSuchItem`, never retried by FetchController).

## Fix
Router delivers FILE_CHUNK/FILE_ERROR to the FP backend when it owns the in-flight
read (`owns_reply` = `_correlate`: read_id + transfer_id + entry + offset), otherwise
to the per-offer active backend as before. New host events `fp_file_error`
(transfer_id, entry_index, fetch_token, read_id, reason, code, retryable) and
`fp_file_error_unmatched`. 8 new tests (6 RED first); transfer 818/10/0.

## Failure classification (from code)
| Failure | Class | Wire | Host code | Swift / NSError | Extension retry |
|---|---|---|---|---|---|
| link lost (TCP closed/silence) | transient | — (local) | 3 PeerLost | serverUnreachable | yes (bounded) |
| host not connected / restarting | transient | — (local) | 8 NotConnected | serverUnreachable | yes |
| read watchdog 30 s | transient | — (local) | 5 Timeout | serverUnreachable | yes |
| snapshot unknown/evicted (after reattach) | terminal for generation | FILE_ERROR source_missing | 1 | noSuchItem | no |
| entry missing / not a file | terminal | source_missing | 1 | noSuchItem | no |
| file open/read OSError on Windows | UNKNOWN (deleted = terminal; sharing violation = maybe transient) — shares `source_missing` | source_missing | 1 | noSuchItem | no |
| file changed since offer | terminal | source_changed | 2 | POSIX EBUSY (was cannotSynchronize; changed 2026-09-26 for a readable Finder message) | no |
| bad request / protocol | terminal | bad_request / other | 7 | cannotSynchronize | no |
| cancel | local | — | cancel | NSUserCancelledError | no |

## Runtime evidence (bundle 16d0c4ed, env overrides absent, W4/B8)
- Post-restart automatic re-requests of 8 dead D2 items: `fp_file_error
  source_missing code=1 retryable=false` in ~40 ms, extension `-1005`; items requested
  while the host was still down retried with code 8 (~1.7 s) then ended terminally.
- Explicit request of dead item A (1-byte read): 1 fetchContents, 13 ms to `-1005`,
  reader got ESTALE after 0.52 s.
- Valid generation B right after: T0→T1 = **20 ms** (was 9.2 s), 1/1 byte-exact,
  per-fetch 4, no leaks/violations, 0 dead-generation events in its slice.
- fileproviderd itself re-requests a `noSuchItem` item ~5 times (≈0.15/5/15/35 s
  backoff); each attempt holds a slot ~20 ms — platform behaviour, not blocking.

## Transient control and a pre-existing Windows limitation
Control CT (~10 s Wi-Fi off mid-paste): Windows closed the TCP socket (not a stall as
in D1). Mac transient path worked as designed (code 3 → 8 → 8 retries across the
1.9 s reconnect), but after re-attach Windows had released ALL snapshots, so the
CURRENT generation answered `source_missing` → Finder -36. Identical to pre-fix
behaviour (D2 at 10:43:59). A paste survives an outage only when TCP survives
(D1). Root cause of that: `service.attach_link`/`_on_link_lost` call
`SnapshotRegistry.release_all()`, which contradicts the intent documented in
`SnapshotRegistry.close_descriptors` (keep the snapshot across sessions, spec §1013).
Separate follow-up (Windows-side), not addressed here.

## Phase D — clean post-fetch timeline (2026-09-27, bundle with 16d0c4ed, W4/B8, no env)
Dataset B-mixed-22 (100 files, 42,158,741 B), gen e2254a07…, 100/100 byte-exact,
maxG 8, per-fetch 4, 0 errors, 0 dead-generation events. Gate before paste: 0
old-generation events in the last 10 s, 0 active fetches.

| boundary | s (rel. T0) |
|---|---|
| T0 first destination placeholder | 0.000 |
| T9 all 100 placeholders | 0.328 |
| T1 first extension fetch_enter | 0.309 |
| T2 first FILE_READ | 0.315 |
| T3 last FILE_READ | 12.776 |
| T4 last FILE_CHUNK | 12.796 |
| T5 last host fetch completed | 12.796 |
| T6/T7 last completionHandler invoked/returned | 12.798 / 12.798 |
| first destination byte | 12.932 (T6 + 134 ms) |
| T10 = T11 all sized / byte-stable | 15.694 |
| T12 Finder UI | not observed |

- Paste → first fetch 0.31 s (the 9.2 s in T1 was dead-generation contamination).
- Zero-active demand gaps before the final fetch survive: 0.88 s (after entry 0),
  0.69 s (after 9), 0.10 s (after 18), 0.75 s (after 90) ≈ 2.4 s total.
- Destination bytes start only after the LAST materialization completes (confirmed
  for this workload/path): 5 large files (~41 MB) land in ~1.3 s, the ~93 small
  files then trickle ~16 ms each for ~1.5 s.
- Local Finder control (same 100 files, ordinary local folder → empty folder, same
  APFS volume): first entry → stable 0.80 s (large files ~58 ms, clone-like; small
  ~7 ms/file) vs File Provider first byte → stable 2.76 s: ratio ≈ 3.5x.
  => the post-fetch tail is File Provider-backed read/copy overhead after
  materialization, not normal Finder copy cost. fileproviderd vs Finder share of it
  is not yet separated (PARTIAL).
