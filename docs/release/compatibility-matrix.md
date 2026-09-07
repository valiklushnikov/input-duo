# Duo Input compatibility matrix

Every row here is a measurement taken on real hardware. A blank row means
**untested**, not **works**. Nothing on this page may be filled in from a
simulator, an emulator run, or a reasonable expectation about a chipset.

Produce rows with:

```powershell
python tests/hil/hil_runner.py tests/hil/scenarios/peripherals.json --port COM7 `
    --phase baseline --state artifacts/peripherals.state.json
# attach the devices and exercise them
python tests/hil/hil_runner.py tests/hil/scenarios/peripherals.json --port COM7 `
    --phase measure --state artifacts/peripherals.state.json `
    --output artifacts/peripherals.json
```

and copy the `peripherals` array of the report into the tables below. The
baseline phase is what makes the counters describe one run rather than the
device's whole life since it was plugged in.

## How a device is identified here, and why not by name

Every row is keyed by **VID, PID and the SHA-256 of the report descriptor the
device handed over**. Those are what a reader can match against their own
hardware: `lsusb`, Device Manager and `tools/dump_usb_descriptors.py` all print
the first two, and the third distinguishes two devices that share them. A brand
and a model number cannot be matched against anything - the same model ships
with different silicon across revisions, and a reader whose box says the same
words has learnt nothing about whether their device is this one.

A device that gave up no report descriptor has **no hash**, and the cell says
so rather than carrying the hash of an empty buffer, which every such device
would share.

**A `role` on this page is what the device said it is** - the kind read out of
its own configuration descriptor by `DescriptorSetup::kind()` - and never the
U1 channel it was plugged into. U1's two channels are named after their pins,
not their contents, and on the bench these rows came from the two are crossed:
the mouse is on the channel U1's firmware calls the keyboard channel. See
`docs/hardware/ch375-compatibility.md`, "A channel's name is not the device on
it".

## What the latency figures on this page are

**Firmware-internal latency**, and only that: the interval from a peripheral
report reaching U1's input core to the command that report produced being
applied on U1's output core. Both ends are on the same board and read the same
clock, so the figure is measured rather than estimated.

It is **not** end-to-end keystroke latency. Nothing on this rig can timestamp a
human finger, and nothing injects HID into U1's peripheral ports, so the
journey from a keypress to a far screen has no measurable start. This page must
never present these numbers as if it did. The peripheral's own polling interval
ahead of U1, and USB, the SPI link and the far computer's own handling behind
it, are all outside what is recorded here.

The device counts into buckets rather than keeping samples, with 20 ms and
50 ms as bucket edges. That makes "p95 at or below 20 ms" and "no sample over
50 ms" exact counts. It also means a p95 is reported as the edge it is at or
below - `p95 ≤ 2 ms` - and never as a single number invented from inside a
bucket.

## Status

Task 7's software packaging slice now accepts the explicit backend
`PIO_USB_REFERENCE` and produces
`duo-input-u1-pio-usb-reference-<version>.uf2`. This is not a hardware result:
the postponed two-PC clipboard gate and the Task 7 recovery/soak checklist are
still unmeasured, so CH375 remains the release default and the reference
backend remains experimental.

**One hardware acceptance run has been recorded: the `peripherals` scenario,
against U1 on COM18, 2026-08-30 12:49 +0300, verdict `partial`, exit code 3.**
The report is `artifacts/peripherals.json` and every figure below is copied
from it.

Two peripherals exist on this bench and the `peripherals` scenario requires
ten, so this run filled one keyboard row and one mouse row. The remaining eight
are untested, not failing. The other scenarios stand as follows, and each
scenario file now carries an `on_this_rig` field saying the same thing in its
own terms:

| Scenario | State |
|---|---|
| `peripherals` | Run. Four checks measured and passed, `no_stuck_keys` unmeasured, 2 devices of 10. |
| `route_toggle` | Runnable, but decides none of its own three questions on this rig - a route change is only visible at the far computer. A run measures latency and stalls and nothing about routing. |
| `link_fault` | Not run. Meaningful only if SPI1 is physically interrupted between the two phases; the runner refuses to decide the release from U2's lifetime counters. |
| `config_power_cut` | Nothing decidable here. All four checks come back unmeasured; needs a switchable supply and a write-enabled run. |
| `profile_power_cycle` | Nothing decidable here. All three checks come back unmeasured; needs a switchable supply and a logger at each computer. |
| `soak_24h` | Not opened yet. `--phase baseline` starts it; a measure phase before 24 hours records every check as unmeasured. |
| `pio_usb_hub_enumeration` | Not run. Scenario written and validated (`--validate-only`) in Task 13; needs a U1 flashed with the PIO USB backend and explicit flash approval, which Task 14 has not yet obtained. |
| `pio_usb_hub_recovery` | Not run, same reason. |
| `pio_usb_dual_pc_routes` | Not run, same reason. |

**The MVP is not accepted on this page as it stands.** What it now holds is one
honest run rather than an empty form.

## The PIO USB backend: not yet run on hardware

Task 13 wrote the three scenarios above and `docs/release/pio-usb-hardware-checklist-ru.md`,
but wrote no hardware acceptance row: Task 14 is the one that flashes U1,
with the user's explicit approval of the exact UF2 hash, and records what
happens. Nothing below is a measurement - it is what Task 13's own bench
session against the *current* CH375 U1 found while preparing those
scenarios, recorded here because it bears directly on how the PIO run's
results must be read once it exists.

**The Keychron 2.4GHz receiver's side button is a PIO-only capability, not a
CH375 regression to check against.** Read from the receiver's live USB
configuration descriptor: interface 0 is a HID boot mouse (81-byte report
descriptor, EP 0x82 IN); interface 1 is HID, no boot protocol (115-byte
report descriptor, EP 0x84 IN + 0x05 OUT); interface 2 is a HID boot keyboard
(164-byte report descriptor, EP 0x81 IN) carrying four top-level collections
- Keyboard (9 bytes), Consumer, System Control, and a second Keyboard
(21 bytes) - and the side button's report lives in the first of those, on
interface 2. Diagnostics read from U1 running its current CH375 firmware
with this same receiver attached show it enumerating **only** interface 0 -
VID `0x3434`, PID `0xD030`, 5 buttons, 81-byte descriptor - with the
keyboard-role port empty. CH375 is one device per socket and never reaches
interface 2 on this receiver, so the side button has never worked through
CH375 here; there is no CH375 baseline for it to regress from. When a PIO
run records this button working, that is a new capability being measured for
the first time, and the row must say so rather than reading as a fix for
something that was previously broken.

**A U1↔U2 link-counter caveat that applies to every PIO run recorded here as
much as it did to the CH375 rows above:** `link_crc_errors` grows at exactly
the same rate as `link_frames_sent` when U2 is simply absent - measured on
this bench at 7428/7428, zero echoed frames. A row that reads those two
counters as evidence of link quality without first confirming U2 was
actually attached for the interval in question would record an absent U2 as
a catastrophically broken one. `pio_usb_hub_enumeration` deliberately runs
before U2 is connected at all, `pio_usb_hub_recovery` and
`pio_usb_dual_pc_routes` name no check that reads either counter for exactly
this reason - see `backend_error_counters_stable` in `tests/hil/hil_runner.py`,
which watches the backend's own counters instead.

## Keyboards

Five devices from different vendors, at least one behind a built-in hub.

| VID | PID | Descriptor SHA-256 | Boot protocol | NKRO | U1 channel | Result | Reason |
|---|---|---|---|---|---|---|---|
| `0x258A` | `0x010C` | none - this device gave up no report descriptor | Yes - `SET_PROTOCOL(boot)` issued and accepted, see `docs/hardware/ch375-compatibility.md` | Not recorded by this run | mouse channel | **Pass** | enumerated as keyboard, boot protocol, no report descriptor read |

Four rows short of the five the scenario asks for. Those four are **untested**.

## Mice

Five devices from different vendors, at least one with five buttons and one
with three.

| VID | PID | Descriptor SHA-256 | Buttons | Wheel | U1 channel | Result | Reason |
|---|---|---|---|---|---|---|---|
| `0x1BCF` | `0x0005` | `f93525fdfa9ca2d7c1639bf5bf2b1b06452a52f3fdc858f5ba198fb5d74ad7c8` | 5, declared by the device | Not recorded by this run; boot protocol carries no wheel byte at all, see `docs/hardware/ch375-compatibility.md` | keyboard channel | **Pass** | enumerated as mouse, 75 bytes of report descriptor, 5 buttons declared |

Four rows short of the five the scenario asks for. Those four are **untested**.

`Reason` is required on a pass as well as a failure. "Works" is not a result
anyone can act on a year from now; "enumerated as a boot keyboard, 6KRO, no
hub" is.

## Latency

The budget is p95 ≤ 20 ms, and any sample over 50 ms is recorded even when the
p95 passes.

From the `peripherals` run of 2026-08-30 12:49, scoped to that run by its
baseline phase. `route_toggle` and `soak_24h` have not contributed rows yet.

| Metric | Budget | Measured | Samples | Result |
|---|---|---|---|---|
| Keyboard p95 (firmware-internal) | ≤ 20 ms | **≤ 2 ms** — every sample landed in the 1–2 ms bucket | 304 | **Pass** |
| Keyboard max (firmware-internal) | — | 1.899 ms | 304 | — |
| Mouse p95 (firmware-internal) | ≤ 20 ms | **≤ 2 ms** — 9 268 samples at 0.5–1 ms, 24 053 at 1–2 ms | 33 321 | **Pass** |
| Mouse max (firmware-internal) | — | 1.916 ms | 33 321 | — |
| Input samples over 50 ms | 0 | **0** — every sample on both streams landed at or below the 2 ms edge, seven buckets short of 50 | 33 625 | **Pass** |
| U2 release after link loss | ≤ 100 ms | **Not yet measured.** U2 reports a lifetime drop count and the time of its *last* release; this run read 7 and 100 ms, neither caused by it. A figure belongs here only from a `link_fault` run that interrupted SPI1 between its two phases. | — | — |
| Configuration-link round trip | — | 2.632 ms, one exchange. Named for what it is: a host-side CDC request and its reply, not the input path. A p95 over one sample is that sample. | 1 | — |
| End-to-end keystroke p95 | ≤ 20 ms | **not measurable on this rig** | — | — |

A p95 is written as the bucket edge it is at or below, because the device counts
into buckets and keeps no samples. "≤ 2 ms" is an exact count of samples at or
below 2 ms, not a number chosen from inside a bucket.

The last row stays as it is until a rig exists that can inject HID into U1's
peripheral ports with a timestamp and observe PC1 and PC2 with the same clock.
Leaving it out would let the rows above be read as the figure the specification
names, which they are not.

## Endurance and recovery

| Scenario | Requirement | Measured | Result |
|---|---|---|---|
| `route_toggle` | 1000 toggles, none lost or misrouted | Nothing. A toggle is observable only as an event arriving at the other computer, and no logger watches PC1 or PC2. | **Unmeasured** — needs a logger at each computer |
| `link_fault` | U2 releases within 100 ms; a damaged frame produces no report | Not run. The release and the recovery become measurable the moment SPI1 is interrupted between the two phases; whether a damaged frame produced a report is only visible at PC2. | **Unmeasured** |
| `config_power_cut` | 100 writes verified; 20 power cuts leave no torn configuration | Nothing. The runner does not write configuration and does not cut power. | **Unmeasured** — needs a switchable supply and a write-enabled run |
| `profile_power_cycle` | all 8 profiles survive a power cycle | Nothing. Reading a profile back proves nothing about a power cycle nobody caused. | **Unmeasured** — needs a switchable supply and a logger at each computer |
| `soak_24h` | 24 h with no watchdog reset and stable error counters | Not opened. | **Unmeasured** |
| — | no keys left held at either computer | Nothing, in every scenario that asks it. | **Unmeasured** — needs a logger at each computer |
| — | watchdog resets over a run | The firmware counts them; `GET_DIAGNOSTICS` does not carry the count. | **Unmeasured** — needs a U1 build that reports it |

Counters that *were* read on 2026-08-30, scoped to the run: `dropped_commands`
0, `runtime_fault` 0, `link_crc_errors` 1 over the whole session, both
peripheral channels attached and ready throughout.

## Firmware and host versions the rows were taken against

| Component | Version | Hash |
|---|---|---|
| U1 firmware | `pico-release`, branch `feature/duo-input-foundation` at `b242b09` | Not recorded. The image flashed for this run was not hashed at the time, and the build tree present now is `pico-ch375`, a different image. The next run records the `.uf2` SHA-256 before flashing. |
| U2 firmware | `pico-release`, same commit | Not recorded, same reason |
| Configurator | same commit | — |
| CDC protocol | 1.0 | — |
| Binary schema | 1.0 | — |

The latency block lives in the **release** build only. The probe build answers
`GET_DIAGNOSTICS` with free text, which the runner reports as a refused request
rather than as a measurement - so a row taken against the probe image cannot
reach this page by accident.

## Known incompatibilities

Record devices that do **not** work here, with the reason, rather than leaving
them out. A device missing from this page reads as untested; a device listed
as failing with "CH375B does not enumerate composite devices behind an
internal hub" saves the next person the afternoon.

| VID | PID | Symptom | Cause |
|---|---|---|---|
| | | | |

None recorded. Two devices have been tried and both passed, so this table is
empty because nothing has failed yet - not because nothing was checked.
