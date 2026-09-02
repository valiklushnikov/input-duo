# Generic keyboard report-protocol support

**Date:** 2026-09-02  
**Status:** approved design, awaiting implementation plan

## Problem

The Aula F75 receiver (`3554:FA09`) types without loss when connected directly
to Windows, but intermittently loses characters through U1 and CH375. The
failure was reproduced while the probe showed a healthy 115200-baud CH375
link, no failed reads, no detach, no command-queue drops, and ordinary
eight-byte boot reports reaching the input pipeline.

The earlier receiver-specific defect is already fixed: a lone `0x01` in one
key slot is not treated as ErrorRollOver. A separate 1 ms polling spike did not
increase the realised rate beyond roughly 500 polls/s, so changing the timer
constant is not a repair. Windows differs in the remaining material respect:
it reads the keyboard's HID report descriptor and leaves the device in report
protocol, while U1 forces every boot-capable keyboard into boot protocol.

## Goal

Read ordinary USB HID keyboards in their declared report format without
device-specific VID/PID rules. Preserve the existing boot-keyboard path as a
safe fallback and leave all current mouse behaviour unchanged, including the
Trust wired mouse and Keychron M3 composite receiver.

This change does not promise hubs, vendor-defined encrypted input, or more
than six simultaneous non-modifier keys at the two computers. U1 and U2 expose
6KRO boot keyboards, so input beyond that limit cannot be represented without
changing their public USB contract.

## Evidence-first capture

Before changing keyboard behaviour, a probe build will fetch the Aula
keyboard report descriptor, expose its received length, hash and complete
bytes over CDC, and then continue selecting boot protocol. The captured bytes
will be committed as an anonymised test vector. This separates knowledge of
the device's actual format from guesses based on common keyboard descriptors.

The probe-only capture must not become the production parser. Its purpose is
to create a reproducible failing fixture and confirm that the descriptor fits
the existing bounded control-transfer reader.

## Report layout

`ch375/report_descriptor` will gain a `KeyboardReportLayout` beside the mouse
layout. It describes one routable keyboard input report:

- whether reports carry an ID and the accepted Report ID;
- the bit positions of the eight modifier usages `E0` through `E7`;
- one Keyboard/Keypad usage-page key representation: either an array of usage
  values or a one-bit-per-usage NKRO bitmap;
- the usage bounds and the minimum complete report-body size.

The parser will walk HID short and long items with bounded arithmetic. It will
track Input offsets independently per Report ID, honour global Push/Pop with a
fixed-depth checked stack, clear local usages after every Main item, and
understand both explicit usages and Usage Minimum/Maximum ranges. Output and
Feature items do not advance the Input bit cursor.

A layout is accepted only when it unambiguously identifies keyboard modifiers
and at least one keyboard key field. Unsupported widths, overflowing offsets,
invalid report IDs, malformed ranges, truncated items, stack underflow or
overflow, and ambiguous competing keyboard reports reject the whole layout.
The caller's previous layout is not modified on failure.

## Normalisation

`KeyboardNormalizer` will accept a layout selected at enumeration. The current
boot layout remains an explicit layout rather than an implicit special case.

For every report the normalizer will:

1. validate the Report ID and full minimum length;
2. extract the modifier bitmap;
3. build a deduplicated set of currently held Keyboard/Keypad usages from the
   array or NKRO bitmap;
4. emit releases before presses, followed by modifier edges, using the
   existing all-or-nothing event-capacity rule;
5. commit its remembered state only after the complete report is valid and
   representable.

Zero and HID error usages are not emitted as keys. A true array rollover is
recognised only when every array slot carries an error usage, preserving the
existing Aula fix. If more than six non-modifier keys are held, the report is
treated as unrepresentable and the previous state is preserved until a later
report returns to six or fewer; a partial six-key state must never be invented.
Reports with unrelated IDs are ignored without changing held state.

Detach and fault handling continue to synthesize releases from the
normalizer's remembered state, regardless of whether the source layout was
boot, array report-protocol, or NKRO.

## Enumeration and fallback

`DescriptorSetup` will request the selected keyboard interface's report
descriptor using the same bounded multi-packet control reader already used for
mice.

- If the descriptor is fetched and parsed, U1 leaves the freshly reset device
  in its default report protocol and publishes the keyboard layout with the
  Ready event.
- If fetching or parsing fails and the interface advertises boot support, U1
  issues `SET_PROTOCOL(boot)` and publishes the fixed boot layout.
- If no usable report layout exists and the interface does not advertise boot
  support, enumeration fails as unsupported. It must not route unknown bytes
  as keystrokes.
- A refused or silent `SET_PROTOCOL(boot)` retains the existing diagnostics
  and fallback semantics; this work does not weaken current recovery.

The selected layout belongs to one enumeration attempt. It is cleared on a new
attempt so reconnecting a different keyboard can never reuse stale offsets.
Diagnostics will distinguish `layout=report` from `layout=boot` and record the
descriptor status, received/wanted lengths and hash.

## Data flow

The configuration parser continues to choose the HID interface and interrupt
IN endpoint. `DescriptorSetup` adds the keyboard descriptor/layout decision.
The Ready event hands both mouse and keyboard layouts to `InputPipeline`, which
selects the matching normalizer. Above normalisation, the existing
`InputEvent`, mapping, macro, U1 USB and U1-to-U2 paths remain unchanged.

No VID/PID branch is permitted in the keyboard descriptor parser,
normalizer, or setup decision.

## TDD and verification

Implementation follows red-green-refactor in this order:

1. capture the Aula descriptor without changing its protocol and add it as a
   fixture;
2. write failing parser tests for the captured array or bitmap layout, Report
   IDs, multiple reports, Push/Pop, malformed/truncated descriptors and
   unchanged output on failure;
3. write failing normalizer tests for presses, releases, modifiers, reordered
   arrays, NKRO, unrelated IDs, short reports, true rollover, the Aula lone
   `0x01`, more than six keys, and detach release-all;
4. write failing setup tests proving report-protocol selection and boot
   fallback on fetch, parse and protocol failures;
5. write failing pipeline tests proving the selected keyboard layout reaches
   the normalizer while existing mouse layouts and Keychron auxiliary reports
   are unchanged.

Verification requires the complete native test suite, both Pico firmware
builds, descriptor/artifact checks, and clean diagnostics. Hardware acceptance
then checks:

- the Aula receiver enumerates with `layout=report` and no boot selection;
- sustained normal and rapid typing no longer loses characters on PC1;
- modifiers, holds, repeat, release, receiver reconnect and U1/U2 keyboard
  routing work;
- the existing wired keyboard still works through report protocol or the boot
  fallback it actually earns;
- the Trust wired mouse and Keychron M3 movement, wheel, side-button routing
  and reconnect behaviour remain unchanged.

If report protocol does not remove the measured loss, the release image is
restored and the result is treated as a disproved hypothesis, not supplemented
with timing or device-specific fixes.
