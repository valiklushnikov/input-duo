# Native PIO USB host, single-hub prototype

**Date:** 2026-09-02  
**Status:** Approved for implementation  
**Approved plan:** `docs/superpowers/plans/2026-09-03-pio-usb-hub-v1-implementation.md`  
**Target branch after approval:** `feature/pio-usb-host-hub-v1`

## Problem

The current product reads the keyboard and mouse through two CH375 modules.
The shipped firmware and configurator now work, but CH375 compatibility and
recovery behaviour remain device-dependent. The next hardware revision shall
replace both CH375 channels with the RP2040's native PIO USB host path while
preserving the verified mapping, macro, routing, storage, configurator and
U1-to-U2 link above the input backend.

This first prototype deliberately does not solve independent failover. U1 is
the only USB host. U2 remains a USB HID endpoint for PC2 and receives input
from U1 over the existing inter-controller link. PC1 and U1 therefore have to
remain powered while PC2 is being controlled.

## Goals

- Read one keyboard and one mouse through an XL334P4 four-port USB 2.0 hub.
- Use Pico-PIO-USB on U1 instead of either CH375 module.
- Preserve the current normalised input events and every layer above them.
- Preserve U1 native USB device output to PC1 and U2 native USB device output
  to PC2.
- Preserve the current U1/U2 SPI pinout and protocol.
- Power the hub and peripherals from PC1 VBUS through a 1.1 A resettable fuse.
- Build the prototype on one HS-005 board with all three USB-C sockets
  accessible.
- Keep both RP2040-Zero boards removable and make the hub cable detachable.
- Keep the known-working CH375 release recoverable while the new backend is
  being validated.

## Non-goals

- U2 does not become an autonomous host in this revision.
- The system does not continue accepting input when PC1 or U1 is off.
- There is no FSUSB42, automatic USB-host failover, SS34 power OR-ing or
  external power supply.
- No mapping, macro, profile, configuration-protocol or PySide UI redesign is
  included.
- No promise is made for arbitrary hubs, composite vendor protocols, wireless
  receivers or devices that exceed the tested power budget.

## System topology

```text
keyboard --\
            +-- XL334P4 hub -- USB-C cable -- TYPE-C EXP -- U1 PIO USB host
mouse ------/                                            |
                                                          +-- mapping/macros
                                                          +-- native USB -> PC1
                                                          +-- SPI -> U2
                                                                       |
                                                                       +-- native USB -> PC2
```

U1 is both a PIO USB host on GP0/GP1 and a TinyUSB device through the
RP2040's native USB controller. U2 retains its current endpoint-only role.

## Prototype components

- 2 x Waveshare RP2040-Zero, U1 MAIN and U2 ENDPOINT;
- 1 x XL334P4-USB2-4P-HUB;
- 1 x HESTORE TYPE-C EXP, article 100.423.24;
- 1 x USB-C to USB-C USB 2.0 data cable;
- 1 x PTC resettable fuse, nominal hold current 1.1 A;
- 2 x 22 ohm series resistors for PIO USB D+ and D-;
- 2 x 56 kohm resistors for USB-C source-mode CC1 and CC2 pull-ups;
- 1 x 100 nF ceramic capacitor and 1 x 4.7--10 uF capacitor across 5V_HUB;
- 1 x HS-005, short insulated wire, removable headers and nylon supports.

The earlier SS34 diodes, FSUSB42, TXS0108E, CH375 modules and 470--1000 uF
bulk capacitors are not fitted.

## Pin allocation

### U1 PIO host

| Function | U1 pin | Connection |
|---|---|---|
| USB host D+ | GP0 | through 22 ohm to TYPE-C EXP D+ |
| USB host D- | GP1 | through 22 ohm to TYPE-C EXP D- |
| SPI receive from U2 | GP8 | U2 GP11 |
| SPI chip select | GP9 | U2 GP9 |
| SPI clock | GP10 | U2 GP10 |
| SPI transmit to U2 | GP11 | U2 GP8 |
| hub source power | 5V/VBUS | through PTC to 5V_HUB |
| common reference | GND | U2 and hub GND |

GP0 and GP1 are adjacent as required by the selected Pico-PIO-USB host
configuration. The two 22 ohm resistors sit near U1, before the longer wire
pair.

### U2 endpoint

U2 uses only GP8, GP9, GP10, GP11 and GND for the existing inter-controller
link. Its 5V and 3V3 pins are not connected to U1 or to the hub. U2 is powered
only by PC2 through its own USB-C connector.

## USB-C upstream breakout

The TYPE-C EXP board is wired as a USB 2.0 source-facing receptacle:

| USB-C contacts | Net |
|---|---|
| A6 and B6 | D+ |
| A7 and B7 | D- |
| A5 / CC1 | 56 kohm to 5V_HUB |
| B5 / CC2 | 56 kohm to 5V_HUB |
| A4, A9, B4 and B9 | 5V_HUB |
| all GND contacts | common GND |
| shield | common GND at the connector in the prototype |
| SBU and SuperSpeed contacts | no connection |

CC1 and CC2 are not tied together. Each gets its own 56 kohm pull-up. The
four-contact USBC-EXP-MINI-4P is not used because it does not expose CC1 and
CC2 and its built-in CC behaviour is undocumented.

## Power

```text
PC1 VBUS -> U1 5V pad -> PTC 1.1 A -> 5V_HUB -> TYPE-C EXP VBUS -> hub
PC1 GND -------------------------------------> common GND ------> hub
```

Only PC1 supplies 5V_HUB. PC2 VBUS is isolated by omission: no wire is fitted
from U2 5V. The PTC protects the branch but cannot make the PC port provide
more current than its own limit. The XL334P4 module's on-board protection may
remain the effective 500 mA bottleneck and must not be bypassed in the first
prototype.

The prototype does not add a 470--1000 uF capacitor to bus power. Such a load
would increase plug-in inrush. The hub retains its factory input network; the
HS-005 adds only 100 nF and 4.7--10 uF after the PTC.

## HS-005 placement

All coordinates refer to the component-side labels printed on HS-005. Each
five-hole group in one numbered column is already connected by factory copper.
All long connections are insulated wires soldered only at their endpoints.

The modules are mechanical passengers on nylon supports; they are not pushed
directly into arbitrary HS-005 rows. Short removable harnesses terminate at
three horizontal headers:

### P1, U1 harness: A1:A8

| P1 | HS-005 | U1 pad |
|---:|---|---|
| 1 | A1 | GND |
| 2 | A2 | 5V |
| 3 | A3 | GP0 / D+ |
| 4 | A4 | GP1 / D- |
| 5 | A5 | GP8 |
| 6 | A6 | GP9 |
| 7 | A7 | GP10 |
| 8 | A8 | GP11 |

### P2, U2 harness: A23:A27

| P2 | HS-005 | U2 pad |
|---:|---|---|
| 1 | A23 | GND |
| 2 | A24 | GP8 |
| 3 | A25 | GP9 |
| 4 | A26 | GP10 |
| 5 | A27 | GP11 |

### JH, TYPE-C EXP harness: J13:J18

| JH | HS-005 | TYPE-C EXP net |
|---:|---|---|
| 1 | J13 | VBUS |
| 2 | J14 | GND and shield |
| 3 | J15 | D+ |
| 4 | J16 | D- |
| 5 | J17 | CC1 |
| 6 | J18 | CC2 |

The TYPE-C EXP module is mounted above the upper centre of the HS-005 with its
socket facing outwards. U1 occupies the lower left and U2 the lower right,
both with their native USB-C sockets facing the lower edge.

### Passive placement and point-to-point wiring

- D+ series resistor: F8 to F9. U1 A3 is wired to F8; F9 is wired to J15.
- D- series resistor: F10 to F11. U1 A4 is wired to F10; F11 is wired to J16.
- PTC: F19 to F21. U1 A2 is wired to F19; F21 feeds the upper positive rail.
- J13/VBUS is connected to the upper positive rail at column 13.
- J14/GND is connected to the upper negative rail at column 14.
- CC1 resistor runs from J17 to the upper positive rail at column 17.
- CC2 resistor runs from J18 to the upper positive rail at column 18.
- The 100 nF and 4.7--10 uF capacitors bridge the upper positive and negative
  rails at columns 28 and 29. A polar capacitor has positive to 5V_HUB.
- A1/U1 GND and A23/U2 GND connect to the lower negative rail; the upper and
  lower negative rails are bridged at column 30.
- The lower positive rail remains unconnected.

SPI uses four parallel insulated wires on the component side:

| Function | From | To |
|---|---|---|
| U2 to U1 | U1 GP8 / column 5 | U2 GP11 / column 27 |
| chip select | U1 GP9 / column 6 | U2 GP9 / column 25 |
| clock | U1 GP10 / column 7 | U2 GP10 / column 26 |
| U1 to U2 | U1 GP11 / column 8 | U2 GP8 / column 24 |

The exact row used for each long SPI wire may be B, C, D or E, one wire per
row. Its insulation must remain intact over every intermediate copper strip.
USB D+ and D- are routed together and away from the SPI wires and power rail.

## Firmware architecture

The existing CH375 backend remains intact during migration. A new input
backend implements the same boundary consumed by `InputPipeline`:

```text
Pico-PIO-USB/TinyUSB host reports
  -> HID interface discovery and report-descriptor parsing
  -> existing keyboard and mouse normalisers
  -> existing InputEvent stream
  -> existing mapping/macro/routing engine
  -> U1 native HID and U1-to-U2 protocol
```

No CH375 command, retry or UART semantics leak into the new backend. Common HID
descriptor parsing and normalisation are reused where their existing contracts
fit. Backend selection is explicit at build time until hardware acceptance is
complete; the known-good CH375 release remains reproducible.

Pico-PIO-USB and TinyUSB revisions are pinned. U1 owns the hub for its entire
uptime. U2 firmware and the desktop configuration protocol should require no
behavioural change for the first milestone.

## Enumeration and runtime behaviour

- U1 starts native USB device and PIO USB host without blocking the routing
  loop indefinitely.
- The hub may enumerate before or after either peripheral.
- Keyboard and mouse are selected by HID interface usage, not port number or
  VID/PID.
- Composite devices and unrelated HID interfaces are ignored unless they
  expose a supported keyboard or mouse input report.
- Detach, malformed reports, endpoint stalls and host reset release all held
  keyboard buttons and mouse buttons before the source is forgotten.
- Reconnect rebuilds layouts from the new descriptors; stale layouts and held
  state are never reused.
- A hub or device failure must not corrupt saved profiles or the U1/U2 link.
- Loss of U2 does not stop input to PC1. Loss of U1 stops all physical input,
  which is an accepted limitation of this revision.

## Compatibility and power gates

PIO USB hub support is treated as a compatibility target, not an assumption.
The release gate records the exact Pico-PIO-USB/TinyUSB revisions and tests at
least:

- the current wired mouse, including movement, wheel and both side buttons;
- the current keyboard, including Fn-dependent F keys and rapid typing;
- the existing Keychron receiver if available;
- simultaneous keyboard and mouse activity through XL334P4;
- unplug/replug in each downstream hub port;
- hub unplug/replug at the TYPE-C EXP socket;
- PC2 disconnect while PC1 remains active;
- U2 reconnect with held-key release safety;
- RGB enabled and disabled, watching for brownout/reset symptoms.

If RGB operation causes resets or enumeration loss, the result is a failed
power gate. A larger PTC does not override the PC port or the hub module's own
limit. The first response is to reduce peripheral power or use a powered-hub
revision, not to bypass protection.

## Implementation sequence

1. Create the feature branch without changing the known-good release tag.
2. Pin Pico-PIO-USB and add compile-only U1 host scaffolding.
3. Add host/backend contract tests and captured HID descriptor fixtures.
4. Enumerate the XL334P4 and expose bounded diagnostics without routing input.
5. Route keyboard reports into the existing normaliser and verify PC1/U2.
6. Route mouse movement, wheel and buttons and verify PC1/U2.
7. Add detach, stall, reconnect and release-all recovery tests.
8. Run the complete native suite, both firmware builds and artifact checks.
9. Flash U1 only after explicit approval and perform the hardware matrix.
10. Update the configurator/device information only where backend identity and
    diagnostics need to be shown; do not redesign mapping behaviour.
11. After hardware acceptance, decide whether the new backend replaces CH375
    by default or remains an alternative build.

## Acceptance

The prototype is accepted only when keyboard typing, mouse movement, wheel,
buttons, switching, macros and routing match the current working baseline on
both computers, with no periodic disconnects or stuck inputs. The desktop UI
must read and write the existing configuration without migration or data loss.

The design is not evidence for the later failover revision. Independent U2
operation remains a separate project requiring a data multiplexer, safe
arbitration, replicated or reduced runtime logic, and independent power.

## References

- Pico-PIO-USB: <https://github.com/sekigon-gonnoc/Pico-PIO-USB>
- Waveshare RP2040-Zero: <https://www.waveshare.com/wiki/RP2040-Zero>
- HESTORE XL334P4 hub: <https://www.hestore.eu/en/prod_10049006.html>
- HESTORE TYPE-C EXP: <https://www.hestore.hu/prod_10042324.html>
