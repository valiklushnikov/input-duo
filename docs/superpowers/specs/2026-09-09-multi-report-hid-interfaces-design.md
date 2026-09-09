# Multiple HID reports in one input interface

**Status:** approved in chat on 2026-09-09, including the separate descriptor
diagnostics command amendment.

## Problem

The per-interface source model assumes that one HID interface has one decoded
role and one report layout. Live Keychron M3 diagnostics disprove that
assumption. Interface 2 receives reports and its descriptor exposes several
top-level collections. Firmware selected its consumer layout (Report ID 2),
while the side button arrives as a keyboard-shaped Report ID 1 packet. The
measured result is `reports=8`, `decoded=0`, and no capture event.

The repository does not contain the interface's 164-byte report descriptor.
Earlier tests therefore used a synthetic descriptor and could not reproduce
this failure. Implementing another decoder from the report bytes alone would
repeat that mistake.

## Constraints

- No product-specific VID, PID, usage, endpoint, or report-ID exception.
- The saved source identity remains `(VID, PID, interface number)`; Report ID
  is a wire-format discriminator, not a new saved source.
- Existing single-report keyboards, mice, and consumer controls behave exactly
  as before.
- Fixed capacity only; firmware must not allocate dynamically.
- A malformed or unsupported report layout is ignored without invalidating
  other independently decodable reports in the same descriptor.
- Disconnect releases held state from every decoder owned by the interface.
- The 12-byte binding record and the 8-byte capture payload do not change.
- The existing `GET_DIAGNOSTICS` payload remains byte-for-byte compatible. Its
  maximum is already 1020 of the protocol's 1024 payload bytes, so descriptor
  bytes travel through a separate optional command and capability.

## Stage 1: capture the missing evidence

Add a bounded, backend-neutral descriptor observation. At HID mount, retain
one complete report descriptor up to the existing 256-byte supported descriptor
limit together with its `(VID, PID, interface)` identity and actual length.
Expose it through a new `GET_HID_DESCRIPTOR_CAPTURE` request/reply guarded by a
new `HID_DESCRIPTOR_DIAGNOSTICS` capability. An older configurator never sends
the request; an older firmware never advertises the capability. The existing
`GET_DIAGNOSTICS` payload does not change.

The configurator requests this observation after ordinary diagnostics and
includes the bytes as hexadecimal in `diagnostics.json`. There is no
device-specific selection: the observation records the most recently mounted
descriptor, and states explicitly when a descriptor was longer than the
capture capacity. Unit tests cover exact round-trip bytes, absence, truncation
signalling, payload bounds, and old/new compatibility.

After this diagnostic firmware is flashed, one exported report supplies the
real 164-byte fixture. That fixture is committed under `tests/vectors/` with
its SHA-256 and hardware provenance.

## Stage 2: decode a report set

Replace the single role/layout result with a fixed-capacity report set. Each
entry contains:

- report role: keyboard, consumer, or mouse;
- Report ID, including the no-ID case;
- the role's existing compact layout;
- its own normalizer state.

The descriptor walker evaluates report IDs independently. An unsupported
report marks only its own candidate unusable; it does not discard valid
candidates belonging to other Report IDs. Capacity exhaustion is explicit in
diagnostics and never displaces an accepted entry.

`SourceIdentity` carries the bounded report-set description across the current
backend boundary. `InputPipeline` owns one normalizer per accepted entry. For
each input report it selects entries by Report ID and invokes only the matching
decoder. A descriptor without Report IDs may have only one accepted input
layout, preserving unambiguous handling of legacy reports.

All emitted events retain the interface's existing source index. Consequently
capture still resolves the event to the same `(VID, PID, interface)` triple,
and source-qualified bindings need no migration.

## Failure handling and diagnostics

Diagnostics reports, per interface, the accepted `(role, Report ID, minimum
bytes)` entries and rejected candidates/reasons. Received-report and decoded-
edge counters stay per interface. A packet with no accepted Report ID is
counted as received but produces no event.

If the report-set capacity is exhausted, the earliest descriptor-order entries
are retained and the overflow counter is incremented. No layout is guessed
from packet contents.

## Verification

The implementation follows RED/GREEN tests and mutation checks.

1. The captured Keychron descriptor produces a keyboard entry for Report ID 1
   and a consumer entry for Report ID 2.
2. The exact captured side-button press
   `01 01 00 4F 00 00 00 00 03` emits modifier-down before `Right`-down; the
   release packet releases both.
3. Consumer reports on the same interface still decode through their own
   normalizer and cannot release keyboard state.
4. Detach releases held state from every report entry.
5. Existing single-report descriptor and boot-fallback suites remain green.
6. Both PIO USB firmware targets build clean, host and native suites pass, and
   the final U1 image is flashed for bench acceptance through the real Mouse
   capture control.

## Rejected alternatives

- **Keychron-specific mapping:** fastest, but restores the product knowledge
  the per-interface design intentionally deleted.
- **Prefer keyboard over consumer:** would make this button work only by
  discarding valid consumer reports and still fails interfaces with multiple
  keyboard Report IDs.
- **Infer boot keyboard layout from a nine-byte packet:** guesses semantics
  absent descriptor evidence and risks emitting arbitrary input.
