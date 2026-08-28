# What the CH375 HID parser accepts, and what it refuses

`firmware/u1_main/ch375/hid_parser.cpp` decides, from a device's USB
configuration descriptor alone, whether U1 can route it. It looks for exactly
one thing: a HID interface whose class/subclass/protocol say keyboard or
mouse, with an interrupt IN endpoint the CH375 can actually read in one
transaction. Everything else is refused by name — `ParseError` says why —
rather than half-enumerated. Half a keyboard is not a keyboard: a device
accepted with one field misread would type the wrong thing on somebody's
computer, which is worse than a device that says it is unsupported.

This document has two parts. The first is the descriptor corpus the parser is
tested against — what each vector represents, and, for the ones it refuses,
the exact `ParseError` it returns and why. The second is what has actually
been observed with real hardware attached to a real board, which is far
smaller than the first part and is not a claim that anything has passed
acceptance.

## The descriptor corpus

Every vector below lives in `tests/vectors/hid_descriptors/` and is exercised
by `tests/firmware_native/test_hid_parser.cpp`, which pins the exact
`ParseError` (or success) each one produces. There are eight.

| Vector | What it is | Result |
|---|---|---|
| `boot_keyboard.bin` | A single-interface boot keyboard: class HID, subclass boot, protocol keyboard, one interrupt IN endpoint. | Supported. `DeviceKind::Keyboard`, endpoint 1, 8-byte packets, interface 0. |
| `boot_mouse.bin` | A single-interface boot mouse, boot subclass declared. | Supported. `DeviceKind::Mouse`, endpoint 2, 4-byte packets, `boot_protocol = true`. |
| `mouse_5_button.bin` | A mouse that declares the mouse protocol but not the boot subclass — most mice sold today. | Supported. `DeviceKind::Mouse`, `boot_protocol = false`. The parser does not require the boot subclass; requiring it would refuse most wired mice on the market. Enumeration does not ask this interface to switch to boot protocol either — there is no boot report behind an interface that does not declare the subclass — so it is read in whatever format it sends, and one that leads its reports with a Report ID is read one byte out of place. |
| `consumer_composite.bin` | A composite device whose *first* HID interface is consumer controls (protocol neither keyboard nor mouse) and whose *second* is a boot keyboard. | Supported. The parser keeps walking past an interface it cannot route; it finds the keyboard at interface 1, endpoint 1. A parser that stopped at the first HID interface would call this device unsupported. |
| `hub.bin` | A USB hub descriptor — no HID interface at all. | Refused: `ParseError::NoUsableInterface`. Hubs are outside what U1 promises to route; enumerating one and then appearing to work until a second device is plugged into it would be worse than refusing it up front. |
| `vendor_only.bin` | An interface with a vendor-specific class, no HID interface present. | Refused: `ParseError::NoUsableInterface`. Same code path as `hub.bin` — nothing in the descriptor was a keyboard or a mouse this firmware can route. |
| `truncated_item.bin` | A well-formed header followed by a record whose declared length runs past what the buffer actually holds. | Refused: `ParseError::Truncated`. The parser checks every record's length against what is left in the buffer, never against what the descriptor claims to have; believing the claim would walk past the end of memory the CH375 handed back. |
| `impossible_report_size.bin` | A HID interface with a valid interrupt IN endpoint whose `wMaxPacketSize` exceeds what the CH375 can read in one transaction. | Refused: `ParseError::PacketTooLarge`. The controller's own buffer is 64 bytes (`kMaxReadablePacket`, DS1 section 5.13); accepting a bigger endpoint would mean reports arriving silently cut in half, which is a keystroke that is not the one somebody made — worse than refusing the device. |

Two further `ParseError` cases are pinned by inline byte arrays in the test
file rather than corpus vectors, because they are one-record edge cases too
small to justify a fixture file: a device descriptor offered where a
configuration descriptor belongs (`ParseError::NotAConfiguration`), and a
record that declares zero length, which would otherwise leave the parser
reading the same bytes forever (`ParseError::Truncated`). A HID interface
whose only endpoint is bulk rather than interrupt is also refused as
`ParseError::NoUsableInterface` by the same inline-vector test — the class and
protocol say keyboard, but there is nowhere for a report to arrive from.

## Physically verified devices — pending hardware acceptance

**Nothing described in this section has passed Task 7's Step 4 acceptance
criteria.** Two CH375 link defects are open (a channel that does not notice
its chip lost power and re-enumerate, and a collapse after a burst of
reports) and are tracked in
`docs/superpowers/plans/2026-08-28-ch375-serial-link-hardening.md`, not here.
What follows is a plain record of what was actually observed on the bench on
the night of 2026-08-28, against the firmware built from commit `25d3730` and
later diagnostic builds in a separate worktree — not a compatibility claim.

**Mouse.** A wired USB mouse enumerated successfully: recognised as a mouse
whose interface *advertises* boot support (`found=mouse endpoint=1 packet=7
boot=yes`), setup returned success (`0x14`), and it delivered HID reports (observed as few as
one and as many as 68 in different sessions). In every session observed, the
channel then lost the device after a burst of reports — `detach_lost`/
`collapses` incremented and the channel re-entered `RecoverWait` — without
anything being unplugged. This loss-after-a-burst is the open link defect
above, not a parser or enumeration failure; the descriptor itself was read
and routed correctly.

That `boot=yes` was read at the time as meaning the device was *in* boot
protocol. It never was: nothing selected a protocol, so the mouse went on
sending its own seven-byte report led by a Report ID, which the normalizer
read as the buttons — a left button held down for ever and sideways movement
arriving as vertical. Enumeration now issues SET_PROTOCOL, and the probe line
reports the two facts separately as `boot=adv:yes sel:yes`.

**Keyboard.** A wired USB keyboard attached (the CH375 saw connect
interrupts, `con=2`) but never completed enumeration: `Ch375Enumerator` timed
out waiting for an interrupt during setup and reported status `0xFD`, "no
interrupt before the deadline" (`enumerator.cpp:50-55`), leaving
`found=nothing`. No descriptor was ever read for this device, so the HID
parser was never reached. The leading hypothesis recorded in the project
ledger is a bus-speed mismatch: `device_is_low_speed_` is derived from
`transport_.get_device_rate(low_speed) && low_speed`
(`firmware/u1_main/ch375/device.cpp:195-196`), so a failed rate read is
indistinguishable from "the device is full-speed" and silently drives the bus
at full speed; most keyboards are low-speed (1.5 Mbps) devices, and a
low-speed device on a full-speed bus answers nothing — which is exactly
`0xFD`. This is untested, not fixed, and belongs to the CH375 link
hardening plan.

Both channels were confirmed electrically sound that night (each enumerated
a device and exchanged data at least once); the two failures above are
software, not wiring, and are being pursued outside this task.
