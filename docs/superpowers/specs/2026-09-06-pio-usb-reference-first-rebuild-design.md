# PIO USB reference-first rebuild

**Date:** 2026-09-06  
**Status:** Approved for implementation
**Target branch:** `feature/pio-usb-host-hub-v1`
**Approved plan:** `docs/superpowers/plans/2026-09-06-pio-usb-reference-first-rebuild.md`

## Problem

The current Duo Input PIO USB firmware does not enumerate the keyboard and
mouse behind the XL334P4 hub. Instrumentation narrowed the persistent failure
to a downstream address-zero control transfer whose zero-length status-OUT
stage remains active, but repeated diagnosis and bounded recovery did not
establish or remove the root cause.

The pinned Pico-PIO-USB `host_hid_to_device_cdc` example does enumerate the
same hub, keyboard and mouse on the same U1 hardware and receives their input
reports. It is therefore the known-good executable baseline. Further work
shall grow Duo Input from that baseline instead of continuing to modify the
non-working integration.

## Goal

Build a new, separately selectable U1 firmware path whose USB host lifecycle
starts as the working pinned upstream example and remains unchanged while the
verified Duo Input behaviour is restored in measured layers:

- keyboard and mouse input through the XL334P4 hub;
- normalized `SourceEvent` and `InputEvent` behaviour;
- mapping, macros and PC1 output;
- SPI output to U2 and all route modes;
- configuration, storage and diagnostics;
- the already implemented shared clipboard from `feature/shared-clipboard`;
- bounded recovery only after ordinary enumeration is proven stable.

## Binding constraints

- Keep the existing CH375 and current PIO USB firmware targets reproducible.
- Add a separate reference-first target and artifact; do not silently replace
  either existing image during development.
- Use the pinned Pico SDK, TinyUSB and Pico-PIO-USB revisions already locked by
  the repository.
- GP0 is host D+, GP1 is host D-. Preserve the existing U1/U2 SPI pinout.
- U1 remains the only peripheral host. U2 remains an endpoint and is not
  flashed unless its unchanged compatibility contract fails.
- No heap allocation, unbounded queue, backend-owned pointer crossing a core
  boundary, blocking service-loop wait, or routing work in a USB callback.
- A callback may only copy bounded metadata/report bytes and request the next
  report where the upstream lifecycle requires it.
- Detach, fault, overflow and malformed state must release held input.
- Accept one logical keyboard and one logical mouse; ignore extras
  deterministically and expose the reason.
- Every hardware image is identified by exact size and SHA-256 before flashing
  U1. No U2 image is copied as part of these gates.

## Chosen architecture

### Separate reference-first target

Add a new backend/target selection named `PIO_USB_REFERENCE`, with its own
CMake preset and distinctly named U1 UF2.
The current `PIO_USB` target remains available as the failed/instrumented
comparison image; `CH375` remains the release fallback.

The new target begins with the pinned upstream host lifecycle in the same
order and on the same cores:

```text
Core 0: set 120 MHz -> settle -> launch Core 1 -> tud_init(0) -> tud_task()
Core 1: settle -> tuh_configure(1, PIO config) -> tuh_init(1) -> tuh_task()
```

No Duo service may be inserted into that sequence until the unmodified
reference target has passed the hub/keyboard/mouse hardware gate.

### Stable host boundary

The upstream callbacks remain the only host entry points. Their first Duo
extension is a fixed-capacity event bridge:

```text
TinyUSB host callback
  -> copy bounded mount/unmount/report record
  -> fixed SPSC queue
  -> ordinary Core 1 drain
  -> SourceEvent + SourceIdentity
  -> existing InputPipeline
```

The queue and neutral types may reuse the current implementation only after a
code comparison proves they do not alter upstream enumeration, callback
re-arm, task cadence or host state. The new path does not inherit current
recovery or observability merely because those files already exist.

### Incremental Core 0 integration

Core 0 gains existing Duo services one layer at a time. A layer that breaks
enumeration is reverted or isolated before the next layer is added. Later
layers never compensate for an earlier failed gate.

## Implementation and hardware slices

### Slice 0 — frozen golden reference

- Build the pinned upstream example from a maintained source location, not
  from generated files under `build/`.
- Preserve its host lifecycle and callbacks verbatim except for project naming
  and deterministic build plumbing.
- Record source revisions, ELF symbols, UF2 size and SHA-256.
- Hardware gate: hub mounts; keyboard and mouse both mount; typing and mouse
  reports appear simultaneously; unplug/replug works.

This hash is the golden rollback point for all later slices.

### Slice 1 — bounded neutral event bridge

- Replace CDC text formatting in callbacks with bounded copies into the
  neutral event boundary.
- Keep `tuh_task()` ownership, initialization order and report re-arm semantics
  identical to Slice 0.
- Provide a minimal read-only CDC trace outside the callback so the bench can
  prove the same mount and report stream.
- Hardware gate: all Slice 0 checks still pass and queue overflow fails safe.

### Slice 2 — PC1 input parity

- Connect the existing descriptor parser, keyboard/mouse normalizers,
  `InputPipeline`, mapping/macro engine and output runtime.
- Replace the temporary CDC trace as the primary output with the existing PC1
  HID device reports while retaining bounded diagnostics.
- Hardware gate: typing, movement, wheel, ordinary buttons, both side buttons,
  Fn-dependent F9–F12, macros and rapid simultaneous input work on PC1.

### Slice 3 — U2 and routing parity

- Add the existing SPI link without changing the host lifecycle.
- Preserve PC1-only, PC2-only and both route modes, held-input release and U2
  disconnect/reconnect behaviour.
- Build U2 for compatibility but do not flash it unless the unchanged U2 image
  demonstrably fails.
- Hardware gate: all Slice 2 checks pass on PC1 and PC2 in every route mode.

### Slice 4 — configuration, storage and diagnostics

- Add CDC configuration service, profiles, A/B flash storage and existing
  backend-neutral diagnostics.
- Append reference-backend diagnostics without changing the existing protocol
  prefix or exceeding payload/FIFO bounds.
- Do not carry over speculative endpoint-pool instrumentation unless it is
  still necessary for a concrete acceptance check.
- Hardware gate: read/write/reboot persistence works and continuous input is
  unaffected during ordinary diagnostics.

### Slice 5 — shared clipboard parity

The shared clipboard is an existing product feature, not a rewrite target.
Its production implementation and tests live on `feature/shared-clipboard`,
including:

- `configurator/src/duo_input/clipboard/*`;
- UI, tray, persistence/autostart and runtime wiring;
- discovery, pairing, mutual confirmation, trust and TLS peer link;
- lazy Windows clipboard offers and loop prevention;
- the corresponding clipboard, UI and runtime test suites.

Integrate the verified production commits/modules from that branch after USB,
PC1/U2 routing and configuration are stable. Resolve branch conflicts at the
integration boundary; do not reimplement the protocol from memory and do not
weaken its trust or pairing model.

Hardware gate on two PCs:

- pairing and trust survive restart;
- text/image clipboard transfer works in both directions;
- no echo loop or unsolicited transfer occurs;
- private/unsupported content remains refused as designed;
- tray/autostart behaviour remains the approved behaviour;
- clipboard activity does not interrupt keyboard/mouse routing.

### Slice 6 — recovery and release candidate

- First run detach/replug, hub replug, rapid mixed input and a bounded soak on
  the ordinary reference-first path.
- Add only recovery required by an observed failure. Each recovery transition
  needs an independently derived failing test before production code.
- Never import the current address-zero restart policy by default; it becomes
  eligible only if the same status-OUT wedge is reproduced in the
  reference-first path.
- Hardware gate: no stuck input, periodic disconnect, enumeration loss,
  brownout/reset, route regression or clipboard interruption.

## Build and artifact strategy

Development exposes three explicit U1 choices:

```text
CH375               known release fallback
PIO_USB             current instrumented but failing integration
PIO_USB_REFERENCE   new reference-first path
```

Their build directories and U1 artifact names must be distinct. Artifact tests
must verify backend identity from CMake cache and linked symbols so one image
cannot be mislabeled as another. The release default does not change until the
reference-first path passes every hardware slice and final acceptance.

## Testing strategy

- TDD for each new boundary or behaviour: focused RED, minimal GREEN, then the
  full relevant suite once.
- Native tests protect fixed capacity, callback constant-time behaviour,
  SourceEvent equivalence, detach releases and routing.
- Build tests protect upstream lifecycle/order, target separation, dependency
  locks and artifact identity.
- Each hardware slice records the exact UF2 hash and observations before the
  next subsystem is admitted.
- Reviews are scoped to the current slice. Diagnostic speculation and repeated
  mutation campaigns are not substitutes for the hardware gate.
- If a slice fails, compare it only with the immediately preceding passing
  slice. Do not continue stacking features on a failed image.

## Failure handling and rollback

- Slice 0 failure means the maintained reference is not equivalent to the
  already working diagnostic image; repair build equivalence before proceeding.
- Slice N failure with Slice N-1 passing identifies the newly admitted layer as
  the search space. Restore the passing image and investigate only that diff.
- CH375 remains available throughout.
- Current PIO USB diagnostics and reports remain historical evidence but are
  not foundational code for the new target.

## Acceptance

The reference-first firmware may replace the current PIO attempt only when:

- keyboard and mouse enumerate together behind XL334P4 on repeated cold boots;
- all keyboard, mouse, macro and routing behaviour matches the verified
  baseline on both PCs;
- detach/reconnect never leaves held state;
- configuration and storage remain compatible;
- the existing shared clipboard works bidirectionally and concurrently with
  routed input;
- complete native, Python, firmware, artifact and packaging gates pass;
- the hardware record contains measured results and exact artifact hashes;
- CH375 remains reproducible as a fallback.

Only after that acceptance may release tooling default to the new backend.

## Non-goals

- Do not repair the old PIO implementation in parallel.
- Do not redesign mapping, macros, clipboard UX or clipboard protocol.
- Do not make U2 an independent USB host.
- Do not patch pinned TinyUSB/Pico-PIO-USB without a failure reproduced in the
  new reference-first path and a separate, evidence-backed decision.
