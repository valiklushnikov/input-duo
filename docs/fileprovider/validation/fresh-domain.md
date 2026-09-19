# VALIDATION_RISK_FP_FRESH_DOMAIN

**Kind:** System, MANUAL, CLEAN_MACHINE_REQUIRED
**Status:** validation-item — not run in CI, not a blocker for any other task
**Owner artifact:** `configurator/src/duo_input/transfer/fileprovider_domain.py`
  (`FileProviderDomainManager.ensure_domain()`)

## What this gate checks

On a **clean macOS user** (no prior Duo Input install, no accumulated
`fileproviderd` sync state from repeated dev `kill -9`s of the extension),
the very first `ensure_domain()` call must carry the `DuoInput` domain
through its full lifecycle without getting stuck:

```
add domain → initial import → root created → enabled
```

Observed enable window on a working extension binary: **~4 seconds**
(`docs/superpowers/records/2026-09-19-macos-file-provider-ipc-packaging-spike.md`,
`FP_DOMAIN_ENABLED = PASS (домен enabled за ~4с; ls реплики работает)`,
reconfirmed post-reboot at the same file, line ~577). That figure is this
gate's expected order of magnitude, not a hard contract enforced by code —
`FileProviderDomainManager`'s backoff schedule (`READINESS_DELAYS_MS = (250,
500, 1000, 2000, 4000)` ms, ~7.75s of cumulative polling before giving up)
is deliberately more generous than the observed 4s so a slightly slower
first-enable does not spuriously degrade.

## Why this is a MANUAL, CLEAN_MACHINE_REQUIRED gate and not a CI test

Phase 10.2 observed persistent `NSFileProviderErrorProviderNotFound` (-2011)
on a **worn dev machine** whose `fileproviderd` had accumulated sync state
from many `kill -9`s of the extension across sessions. That FS-level wedge:

- survived a reboot,
- reproduced even under a fresh bundle id,
- did **not** reproduce on a domain that had previously enabled successfully
  earlier the same day, and
- did **not** affect the XPC transport (Task 3's gates stayed green
  throughout — the wedge is FS-sync-specific, not a general extension
  failure).

There is no known safe way to reproduce or clear that dev-only wedge state
from an automated CI runner: the only fix identified so far is a clean user
or clean machine, and forcibly resetting `fileproviderd`'s persistent store
would mean deleting private/system state — explicitly out of scope (see
"Hard prohibitions" below). CI therefore cannot exercise "fresh domain, fresh
machine" honestly; this gate is deferred to manual pre-release validation
instead of being faked as a green CI check.

## Procedure

1. Use a macOS user account that has never had Duo Input's File Provider
   extension installed (a fresh user, a fresh VM, or a machine where
   `fileproviderd` has never seen the `DuoInput` domain identifier).
2. Install/launch the packaged app so the extension registers with the
   system.
3. Trigger `FileProviderDomainManager.ensure_domain()` (normal app startup
   path).
4. Observe, in order:
   - `state_changed` → `"registering"` (domain add issued),
   - `state_changed` → `"waiting_enabled"` (add succeeded, polling started),
   - the File Provider root appears (e.g. visible under the Finder sidebar
     or via `NSFileProviderManager.getDomainsWithCompletionHandler_`),
   - `state_changed` → `"ready"` and the `ready` signal fires — expected
     within a few probe attempts (~4s observed; backoff schedule allows up
     to ~7.75s before giving up).
5. Confirm the domain does **not** land in `"degraded"` and that no probe
   error other than a handful of transient `-2011`s (if any) was seen.

## Pass/fail

- **PASS:** domain reaches `READY` within the backoff schedule, root is
  browsable, no `degraded` signal fired.
- **FAIL:** domain stays in `WAITING_ENABLED` past the schedule and
  transitions to `DEGRADED`, or `ensure_domain()` never gets past
  `REGISTERING` (add itself failed).

A FAIL here on a genuinely clean machine would be a real regression worth
investigating. A FAIL only reproducible on a worn dev machine with prior
`kill -9` history is the already-known Phase 10.2 anomaly, not new
information — do not treat it as a blocker for other tasks (see Status
above), and do not attempt to fix it by touching `fileproviderd`'s state.

## Hard prohibitions (apply to this gate's investigation too)

- Never `killall`/`launchctl kickstart`/SIP-path `fileproviderd` to "fix" a
  FAIL. `FileProviderDomainManager` never does this in production, and
  neither should manual investigation of this gate — see the module's
  docstring and Task 6's brief.
- Never delete `fileproviderd`'s private/system store to reset for a retry.
  If a clean re-run is needed, use an actually-clean user/machine, not a
  privileged reset of a dirty one.

## Relationship to `DEGRADED` handling

This gate validates the happy path (`ensure_domain()` → `READY`). What
happens instead of hanging when the domain *doesn't* enable — the
`WAITING_ENABLED` → `DEGRADED` transition after
`READINESS_DELAYS_MS` is exhausted, and the `degraded(reason)` signal — is
covered by automated unit tests
(`configurator/tests/transfer/test_fileprovider_domain.py`, mocked
`NSFileProviderManager`, no real FS/XPC involved). Routing offers to staging
while `DEGRADED` is Task 16's responsibility, not this module's.
