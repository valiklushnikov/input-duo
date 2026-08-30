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

**No hardware acceptance run has been recorded yet.** The two-board rig
described in `tests/hil/scenarios/*.json` has not been built, so every table
below is empty and every metric is unmeasured. The MVP is not accepted until
they are filled in.

Two peripherals exist on this bench and the `peripherals` scenario requires
ten, so a run here fills at most one keyboard row and one mouse row. The
runner's report carries a `coverage` block saying so; copy it, rather than
leaving eight blank rows to be read as an oversight.

## Keyboards

Five devices from different vendors, at least one behind a built-in hub.

| Vendor | Model | VID | PID | Descriptor SHA-256 | Boot protocol | NKRO | Result | Reason |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |

## Mice

Five devices from different vendors, at least one with five buttons and one
with three.

| Vendor | Model | VID | PID | Descriptor SHA-256 | Buttons | Wheel | Result | Reason |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |

`Reason` is required on a pass as well as a failure. "Works" is not a result
anyone can act on a year from now; "enumerated as a boot keyboard, 6KRO, no
hub" is.

## Latency

From `route_toggle` and `soak_24h`. The budget is p95 ≤ 20 ms, and any pause
over 50 ms is recorded even when the p95 passes.

| Metric | Budget | Measured | Samples | Result |
|---|---|---|---|---|
| Keyboard p95 (firmware-internal) | ≤ 20 ms | | | |
| Keyboard max (firmware-internal) | — | | | |
| Mouse p95 (firmware-internal) | ≤ 20 ms | | | |
| Mouse max (firmware-internal) | — | | | |
| Input samples over 50 ms | 0 | | | |
| U2 release after link loss | ≤ 100 ms | | | |
| End-to-end keystroke p95 | ≤ 20 ms | **not measurable on this rig** | — | — |

The last row stays as it is until a rig exists that can inject HID into U1's
peripheral ports with a timestamp and observe PC1 and PC2 with the same clock.
Leaving it out would let the rows above be read as the figure the specification
names, which they are not.

## Endurance and recovery

| Scenario | Requirement | Measured | Result |
|---|---|---|---|
| `route_toggle` | 1000 toggles, none lost or misrouted | | |
| `link_fault` | U2 releases within 100 ms; a damaged frame produces no report | | |
| `config_power_cut` | 100 writes verified; 20 power cuts leave no torn configuration | | |
| `profile_power_cycle` | all 8 profiles survive a power cycle | | |
| `soak_24h` | 24 h with no watchdog reset and stable error counters | | |

## Firmware and host versions the rows were taken against

| Component | Version | Hash |
|---|---|---|
| U1 firmware | | |
| U2 firmware | | |
| Configurator | | |
| CDC protocol | 1.0 | — |
| Binary schema | 1.0 | — |

## Known incompatibilities

Record devices that do **not** work here, with the reason, rather than leaving
them out. A device missing from this page reads as untested; a device listed
as failing with "CH375B does not enumerate composite devices behind an
internal hub" saves the next person the afternoon.

| Vendor | Model | VID | PID | Symptom | Cause |
|---|---|---|---|---|---|
| | | | | | |
