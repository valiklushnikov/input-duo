# Every HID interface is an input source

**Status:** design approved 2026-09-07. Supersedes the device-specific Keychron
side-button handling added in `c7b72a0`.

## The problem

A mouse's extra buttons frequently do not travel on its mouse endpoint. The
bench Keychron M3 is the worked example: twelve hundred samples of its mouse
report and the buttons byte was never once non-zero. Its side button is emitted
by the receiver's *keyboard* interface, as a shortcut - `Ctrl` plus usage `0x4F`
on endpoint 1, report ID 1.

The firmware already knows this about this one mouse. `input/pipeline.cpp`
carries `keychron_side_state()`, the constants `kKeychronVendorId`,
`kKeychronProductId` and `kKeychronSideUsage`, and an `on_auxiliary_report()`
that maps that usage onto mouse button 4. It is roughly eighty lines of
knowledge about a single product.

That path is live - `on_event()` dispatches `AuxiliaryReport` to it and
`keychron_receiver_` is latched from the identity's VID/PID on `Ready` - but the
two backends disagree about what reaches it. `on_auxiliary_report()` gates on
`endpoint != 1`. The shipping backend feeds it the constant
`kKeychronAuxiliaryEndpoint = 1` and says why in its own comment: *"Not this
interface's own TinyUSB instance number - enumeration order does not guarantee
that is 1."* The reference target passes `record.instance`, the raw TinyUSB
instance, straight through (`u1_reference/source_adapter.cpp`, `Role::Auxiliary`).
So on the reference build the side button reaches the guard and is dropped
unless enumeration happens to hand that interface instance 1.

A magic endpoint number that two backends fill in differently is the defect
underneath the defect. Extending this approach does not scale either: every
mouse chooses its own interface, its own usage and its own report shape, and the
operator cannot be asked to wait for us to add theirs.

The generic design removes the magic number rather than aligning it. There is no
privileged endpoint, so there is nothing for two backends to disagree about.

## The shape of the fix

**The unit of input becomes the interface, not the device.**

Today `classify_hid_layout()` returns one verdict per device - keyboard, mouse,
or unrecognised - and `InputPipeline` holds a single `kind_`. A composite
receiver with three interfaces is squeezed into one slot and two thirds of it is
discarded into `unclaimed_`.

Instead: claim every HID interface a device exposes, parse each through its own
report descriptor, and let all of them feed one event stream in which every
event is tagged with the interface it came from. A side button that speaks
keyboard becomes a keyboard trigger; one that speaks consumer usages becomes a
consumer trigger; one that is a real button bit becomes a mouse-button trigger.
No line of the firmware needs to know which product it is talking to.

## Scope

**In scope.** Every interface with a readable HID report descriptor, and every
interface without one that boot protocol describes.

**Out of scope, deliberately.** Interfaces whose payload is genuinely opaque -
USB class `0xFF`, or a HID interface on a vendor-defined usage page where the
bits carry no declared meaning. Reading those needs a different mechanism
(observe which bit changed while the operator presses) and a new trigger kind
that stores an opaque bit position. It is a later increment, taken when a mouse
that actually requires it turns up. Nothing in this design forecloses it.

## Firmware

### Sources

A table of input sources keyed by `(device address, instance)`, each holding
`{VID, PID, interface number, role, layout}`. `SourceIdentity` and
`pio_usb/device_registry.cpp` already hold exactly this; what changes is that
the result is no longer collapsed to one verdict per device.

Each source owns **its own normalizer instance**. This is not tidiness: the
mouse normalizer holds `buttons_` across reports, and two interfaces sharing one
instance would overwrite each other's held-button state and emit releases nobody
performed.

### Interfaces that cannot be decoded

They are still claimed and still polled. Only decoding is skipped.

This is load-bearing. The 2026-09-02 resolution of the Keychron receiver
investigation records that polling **both** auxiliary endpoints is what stopped
the receiver wedging; attempts that drained only the service endpoint could not.
An unpolled endpoint on a composite receiver is not merely a missed input, it is
what hangs the device.

### Event tagging

`InputEvent` gains a source index - a one-byte index into the source table, not
the identity itself. Events cross between cores through a queue, and widening
every event by five bytes to carry a VID/PID that only one consumer reads is
waste. The index is resolved to `(VID, PID, interface)` in exactly one place:
where capture builds its reply to the host.

**The index is runtime-only and is never stored.** Matching a saved binding is
done on `(VID, PID, interface)`, never on the index, because a table slot freed
by unplugging one device is reused by the next and a stored index would silently
retarget a binding at whatever was plugged in afterwards. For the same reason a
source's index must not be reused while any event carrying it is still in
flight: a slot is released only after the queue has drained past it.

When the table is full, a further interface is not claimed at all rather than
displacing an existing source. A device that cannot be claimed is counted and
reported in diagnostics; silently dropping it is how an operator ends up
debugging a mouse that the firmware decided not to look at.

### Deletions

`on_auxiliary_report()`, `keychron_side_state()`, `kKeychronVendorId`,
`kKeychronProductId`, `kKeychronSideUsage`, `keychron_receiver_` and its
counters. All of it, not most of it: leaving a dormant product-specific path
beside a generic one invites the two to disagree.

## Configuration format

The binding record is twelve bytes and six of them are reserved and zero - two
at offset 6, four at offset 8. The source qualifier is VID (2) + PID (2) +
interface number (1) = **five bytes**, which fits the existing reserve with a
byte spare.

So the record does not change size, nothing realigns, and the schema advances by
**minor**, not major.

**All-zero means "any source"** - which is precisely how every binding behaves
today. Existing configurations are read unchanged and keep working, and there is
no migration step. That is a consequence of the reserved bytes having been
honestly zeroed, not a lucky escape.

The uniqueness rule widens from `(kind, code, modifiers)` to
`(kind, code, modifiers, source)`. Without that widening, `Ctrl+Right` from the
mouse and `Ctrl+Right` from the keyboard could not both exist in one profile,
which is the entire point of qualifying the source.

## Protocol

`CAPTURE_EVENT`'s payload grows from three bytes to eight.

The host's decoder **accepts both lengths**. Three bytes means the source is
unknown, which is read as "any". This is required, not courteous: the emulator
and the compatibility matrix are live in this repository and a host that refused
the short form would break both.

## Host and UI

### The filter that would have hidden the whole feature

`ui/mouse.py` opens its capture dialog with `accepted_kind=MOUSE_BUTTON`, and
`CaptureDialog._on_capture_received` discards any trigger of another kind and
silently re-arms. A Keychron side button arrives as a keyboard trigger, so it
would land in that filter and be thrown away - the firmware working perfectly
and the screen showing nothing.

The filter must instead accept any trigger **whose source belongs to the mouse**.
That question is now answerable, because the event carries its source and the
source table knows which device it sits on.

### Presentation

A bound trigger is labelled by what it is and where it came from:
`Ctrl+Right - Keychron Link (interface 1)`. A binding whose source is not
attached shows as unavailable, through the same mechanism that greys out
Button 4 today when no mouse is present.

## Verification

Three levels, all required.

**Native.** A composite device with three interfaces; events carry the right
source; normalizers do not share state across interfaces.

**Host, through the real interface.** Tests press the actual control rather than
setting the value behind the UI's back. A test that sets a setting directly has
already passed in this project against a completely dead handler.

**Bench acceptance.** The Keychron side button binds and switches. And the
**per-endpoint poll rate is measured before and after** - see the risk below.

Every change ships with a test that fails without it, mutation-verified by
deleting or inverting the change and watching the new test fail.

## Risks

**Bus load is the real one.** The poll interval is per endpoint while the token
budget is per device. A mouse that had one endpoint polled will have three. The
effective rate per endpoint therefore falls, and by how much is a hardware
question, not an arithmetic one - the more so next to the known behaviour of a
wired Aula flooding the bus. This is an acceptance measurement, not an
assumption, and it may force a policy where undecodable interfaces are polled at
a lower rate than input ones.

**Slot budget is not a risk.** `CFG_TUH_HID` is already 8 in the reference
target and `2 * CFG_TUH_DEVICE_MAX` = 8 in `u1_main`. No increase is needed.

## Non-goals

- Opaque vendor interfaces (see Scope).
- Naming an extra button prettily. It is labelled by what it actually sends.
  A device-specific pretty name is the knowledge this design exists to delete.
- Any change to how mouse buttons 1-5 on the mouse endpoint already work.
