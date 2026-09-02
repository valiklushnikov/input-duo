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
the exact `ParseError` it returns and why. The second is what has actually been
observed with real hardware attached to a real board. The corpus is what
defines the supported set; the bench is two devices, and two devices are an
existence proof rather than a compatibility list.

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

## What has been observed on real hardware

Two devices have been driven end to end, at the same time and continuously,
each on its own CH375 channel: **a wired boot-protocol keyboard** and **a wired
boot-protocol mouse**. Both are described here by device class and by behaviour
rather than by make or model, deliberately. Every user's peripherals are
different, and a document that named one bench's hardware would invite a reader
to read the absence of their own device as incompatibility, which it is not.

A third has since been driven the same way: **a 2.4 GHz receiver presenting a
boot-protocol keyboard**, `VID 0x3554 PID 0xFA09`. It is the one thing on this
page carried by its identifiers rather than by its class, and only because the
last observation below is true of *it* and was not true of the wired keyboard
in the same socket. A reader with a different receiver learns from that
observation what to look for, not what they have.

### A channel's name is not the device on it

**U1's two CH375 channels are called the keyboard channel and the mouse
channel after the pins they use, and neither name is a claim about what is
plugged into it.** `kPinKeyboardTx/Rx` and `kPinMouseTx/Rx` name the wiring;
`describe_port` fills `keyboard_port_` and `mouse_port_` from whatever
enumerated there. On this bench the two are crossed - the mouse is on the
keyboard channel - and that is an ordinary bench, not a fault to correct.

What a device *is* comes from one place only: `DescriptorSetup::kind()`, read
out of the device's own configuration descriptor during enumeration and
reported per port in `GET_DIAGNOSTICS`. Anything that wants to know whether a
keyboard is attached reads that field. Nothing may infer it from the channel.

This is written down because the crossing has now misled three separate pieces
of work: a diagnostic read as "the keyboard is not enumerating" when the
keyboard was enumerating on the other channel, a wiring check that went looking
for a fault that was not there, and a HIL report whose `role` column was taken
from the channel and so printed `role: keyboard` beside `reason: enumerated as
mouse`, in the artifact whose whole purpose is to say which device is which.
The HIL runner now reports the descriptor's answer as `role` and the channel as
a separate `port` field, and keeps both: the channel is how a crossed or dead
cable is diagnosed, so it is worth reporting - it is simply never the identity.

### What was exercised

The whole chain, on both devices at once, with the operator using them normally
throughout.

| Stage | Observed |
|---|---|
| Enumeration | Both channels reached Ready and stayed there; poll and interrupt counters climbed in step past twenty-two thousand each, with one detach apiece across the session and no presence losses. |
| Descriptor parse | Keyboard and mouse each recognised as its own kind, from the configuration descriptor alone. |
| `SET_PROTOCOL(boot)` | Issued on both, and confirmed accepted on both. |
| Report delivery | Reports arriving continuously on both channels while the devices were in use. |
| Routing to both computers | Keys and pointer routed to PC1 alone, to PC2 alone, and — for the keyboard — to both at once, switched by bindings while the devices stayed up. |
| Macro playback | A text macro typed in full on both computers, with no movement stall and no key left down. |
| Profile switching across a power cycle | Eight distinct stored profiles, all eight surviving loss of power, and the device coming back up in the profile the configuration names as active. |
| Release on disconnect, and recovery | A key held with auto-repeat running, its keyboard then pulled from the CH375 mid-repeat: the repeat stopped by itself and the far computer was told to let go. A modifier held, keyboard pulled and reconnected: typing afterwards was unmodified — no stranded modifier — and the device came back without intervention. |

### What was observed, as distinct from what was assumed

The four facts below are recorded separately because each was, at some point
in this project, believed on weaker evidence than it deserved.

- **Interrupt IN endpoint 1, on both devices.** Not inferred from the interface
  number and not defaulted to; read from the endpoint descriptor.
- **`wMaxPacketSize` 8 on the keyboard and 7 on the mouse.** The mouse's boot
  report itself arrives in three bytes; the endpoint it arrives on is seven.
  Both are well inside the controller's 64-byte transaction buffer.
- **Boot subclass *advertised*, and boot protocol *confirmed selected*, as two
  separate facts.** The diagnostic build prints them apart — `boot=adv:yes
  sel:yes` — because conflating them cost this project a week. A mouse whose
  interface advertised boot support was taken to be *in* boot protocol when
  nothing had ever selected one, so it went on sending its own seven-byte
  report led by a Report ID; the Report ID was read as the button mask, and the
  result was a left button held down for ever and sideways movement arriving as
  vertical. "It says it can" and "it is" are claims about different devices,
  and only the second byte says which one is attached.

- **A keyboard is read in the protocol it declares, and put into boot protocol
  only when it cannot be.** The report descriptor is fetched for keyboards as
  well as mice now. When it parses, the layout it gives is what the normalizer
  reads and no `SET_PROTOCOL` goes out at all; the diagnostic build prints
  `klayout=report` beside `kbits=<bits>/<count>@<offset>` and `kid=<yes|no>/<id>`.
  When it does not - refused, silent, empty, malformed, oversized, or simply
  not a keyboard - the fixed boot layout is used and `SET_PROTOCOL` is sent
  first, printed as `klayout=boot`. The fixed layout is a description of the
  *boot* report, so it is only correct once the device has actually been put
  there; a keyboard whose interface does not declare the boot subclass and
  whose descriptor cannot be used is refused as unsupported rather than read
  at offsets it never agreed to.

  Mice are not on that rule. One with no usable descriptor still moves the
  pointer on boot protocol whether or not the interface advertises it, and
  refusing such a mouse would take away a device that worked.

- **The Aula F75 declares five key slots, not six, and a vendor byte where the
  sixth would be.** Its 77-byte report descriptor
  (`tests/vectors/usb_descriptors/aula_f75_keyboard_report.hex`) is eight
  modifier bits, a constant byte, five 8-bit array slots over usages
  `0x00-0xFF`, and then one byte on vendor page `0xFF00`. Read at boot
  protocol's offsets that eighth byte is a sixth key slot, so whatever the
  keyboard puts there arrives as a keystroke nobody made - and one nothing
  will ever release, because the report that clears it looks like the release
  of something real.

- **A key slot holding `0x01` is not necessarily a rollover.** The receiver at
  `0x3554:0xFA09` sends boot reports with `0x01` in the *first* key slot and
  zeros in the other five, where the wired keyboard in the same socket sends an
  empty report: 32 of 156 reports in one twenty-second line of typing, and 14
  of 149 in another. HID 1.11 8.3 has a keyboard that has lost count put
  ErrorRollOver in *every* array field, so one of them is not that - it is the
  keyboard saying nothing is held.

  Read as a rollover, each of those reports was discarded whole, and a
  discarded report leaves the normalizer holding keys the keyboard has already
  let go. That is one defect wearing two faces, and both were reported as
  hardware: a lost *release* leaves the key down with nothing left that would
  ever lift it, so the far computer repeats it until the next keystroke - on
  the bench a space stuck for 1188 ms and put twenty-two of them in a line
  nobody typed - while a lost *press* is a letter that never arrives at all,
  because the release behind it compares equal to a state that never moved.

  What made this expensive to find is that the device's own numbers all looked
  well. Firmware-internal latency stayed at 1-2 ms throughout, because its
  clock starts when the report reaches Core 1 and a report that is discarded
  never starts one; `dropped_commands` and `runtime_fault` stayed at zero,
  because nothing was refused by any queue. The evidence that named it was the
  report bytes themselves, out of the probe build's `last=` field.

### How the serial link to the controller settles

The rate between U1 and each CH375 is not configured, it is found — and the
finding rule matters, because the obvious one is wrong.

- **A rung is proved by a block read, not by `CHECK_EXIST`.** On this bench
  `CHECK_EXIST` answered correctly at 115200 and at 62500 while every
  multi-byte read at those rates failed — zero successful block reads against
  twenty-four failures on each — and completed at 37500. A two-byte exchange
  proves a clock divider; it does not prove the link can carry a descriptor.
  So a rate is trusted only once traffic has actually gone through it, and a
  rate that fails repeated block reads steps the ladder down and re-runs chip
  setup.
- **A collapse steps the rate down, but never below what the device's own
  packet size needs.** The floor is derived from the report rather than chosen
  (`report_rate_floor`): a report of *n* bytes costs *n* plus eight frames of
  overhead per poll interval, so at 9600 a seven-byte mouse report costs
  17.2 ms against the 8 ms a moving hand produces one in. That deficit never
  closes, the device is torn down for a silence this side is causing, and the
  old rule read the teardown as another collapse and stepped down again. A
  channel with an attached device now rests at the slowest rung that still
  clears its own floor, and no lower.

### The honest limits

This is where the section earns its keep. All of the following is current
behaviour, not speculation.

- **A device that does not declare the boot subclass is read in whatever format
  it sends.** The parser accepts it — requiring the boot subclass would refuse
  most wired mice on the market — but there is no boot report behind an
  interface that does not declare one, so `SET_PROTOCOL` is not attempted for
  it and the report arrives in the device's own layout.
- **A device that leads its reports with a Report ID is read one byte out of
  place.** Every field lands one position late: on a mouse the Report ID
  becomes the buttons and the axes shift behind it.
- **A device that STALLs or ignores `SET_PROTOCOL` is accepted anyway**, with
  the same consequence. The refusal is recorded as a setup status — `0xFA` for
  a refusal, `0xF9` for no answer at all — and enumeration carries on to Ready.
  The release image has no counter or status field for it, so in the field this
  failure looks like a peripheral that types the wrong thing rather than like
  one that was not understood.
- **Boot protocol costs the scroll wheel on every mouse.** The boot-protocol
  mouse report is three bytes — buttons, dX, dY — and there is no wheel byte in
  it, so a mouse asked for boot protocol cannot scroll at all. This was seen on
  hardware before it was understood: the operator reported the wheel dead, and
  every one of the 59 distinct reports captured in
  `tests/vectors/hid_reports/mouse_boot_reports.json` is three bytes long, with
  the fourth byte of the capture record — the record's own padding — zero. The
  normalizer reads a wheel when the report is long enough to carry one; in this
  mode no report ever is, and `tests/firmware_native/test_trace_replay.cpp`
  asserts that the whole corpus produces no wheel event. `SET_PROTOCOL` is
  asked for precisely because boot protocol makes the layout knowable, so the
  wheel is what that knowledge currently costs, on every mouse, not only on
  awkward ones. The repair is the one the Report ID limit above also needs:
  read the HID report descriptor and run the device in its own protocol instead
  of forcing boot. One change buys back the wheel and the devices that lead
  their reports with a Report ID.
- **Three kinds of device are refused up front, each by the name of its
  reason.** A hub, as `ParseError::NoUsableInterface`: U1 does not promise to
  route through one, and enumerating it so that it appears to work until
  something is plugged into it would be worse than saying no. A composite
  device whose only routable interface is neither keyboard nor mouse, as
  `ParseError::NoUsableInterface` — note that a composite device which *does*
  carry a keyboard or mouse interface is supported, and the parser walks past
  the interfaces it cannot route to find it. And an interrupt endpoint whose
  `wMaxPacketSize` exceeds 64 bytes, as `ParseError::PacketTooLarge`, because
  the controller reads at most 64 in one transaction and a report arriving
  silently cut in half is a keystroke nobody made.

**Three devices verified is a sample of three.** What this firmware supports is
defined by the descriptor corpus in the first part of this document and by the
parser that is tested against it — not by this bench. A device absent from this
section is not a device known to fail.

The third one is the argument for keeping this section honest. Two devices had
been driven for weeks, and the report format they both sent was taken as the
report format keyboards send; the receiver was the first thing to disagree, and
it disagreed on a byte no test had ever been given.
