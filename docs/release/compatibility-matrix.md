# Duo Input compatibility matrix

Every row here is a measurement taken on real hardware. A blank row means
**untested**, not **works**. Nothing on this page may be filled in from a
simulator, an emulator run, or a reasonable expectation about a chipset.

Produce rows with:

```powershell
python tests/hil/hil_runner.py tests/hil/scenarios/peripherals.json --port COM7 --output artifacts/peripherals.json
```

and copy the `peripherals` array of the report into the tables below.

## Status

**No hardware acceptance run has been recorded yet.** The two-board rig
described in `tests/hil/scenarios/*.json` has not been built, so every table
below is empty and every metric is unmeasured. The MVP is not accepted until
they are filled in.

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
| Keyboard p95 | ≤ 20 ms | | | |
| Keyboard max | — | | | |
| Mouse p95 | ≤ 20 ms | | | |
| Mouse max | — | | | |
| Pauses over 50 ms | 0 | | | |
| U2 release after link loss | ≤ 100 ms | | | |

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
