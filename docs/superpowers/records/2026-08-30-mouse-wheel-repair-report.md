# Making the wheel work — the caller for the report-descriptor parser

2026-08-30. Branch `feature/duo-input-foundation`, from `ddbf8c9`.

The previous implementer left `parse_mouse_report_descriptor` finished, tested
and called by nothing. This is the caller, plus a re-check of the tests that
were inherited unverified.

Commits, in order:

| SHA | What |
| --- | --- |
| `1e6babd` | Make the report-descriptor tests able to fail |
| `ed45d93` | Read a mouse report through the layout the mouse declared |
| `c3e1a67` | Read how long the report descriptor is while the configuration is open |
| `ed282be` | Fetch a mouse's report descriptor and run it in its own protocol |
| `965621a` | Hand the layout to the normalizer that reads the reports |
| `66e7987` | Say in the probe report where the wheel is, or why there is not one |

---

## The design

Five pieces, each committed on its own.

### 1. The configuration descriptor yields the report descriptor's length

`HidCapabilities` gains `report_descriptor_length`, read from the HID class
descriptor (type 0x21) that sits between an interface and its endpoints
(HID 1.11 6.2.1). That is the only place the number exists, and
`GET_DESCRIPTOR` has to say how many bytes to ask for — a device asked for
fewer than it has answers with the descriptor cut short.

It belongs to an **interface**, not to a device. The composite in
`tests/vectors/hid_descriptors/consumer_composite.bin` declares 0x19 bytes on
its consumer interface and 0x3F on the keyboard behind it. So the length is
collected per open interface, reset when a new interface record begins, and
carried onto the candidate only when its endpoint is found.

Both of the HID record's lengths are checked. `bNumDescriptors` counts
three-byte entries after a six-byte header; a record that counts more than its
`bLength` can hold has its last entries read out of whatever record follows it,
and the outer walk cannot notice because `bLength` is what the outer walk steps
by.

### 2. The fetch, inside `DescriptorSetup`

The step order is now:

```
read the device descriptor          (and keep bMaxPacketSize0)
give the device an address
tell the controller the same address
read the configuration descriptor
choose a configuration
read a mouse's report descriptor    if it said it has one     <- new
ask a boot-capable interface for boot protocol  if that failed
```

The last two are **alternatives**. Which one runs is what decides whether the
mouse has a wheel.

The controller's own `GET_DESCRIPTOR` command knows only the device and
configuration descriptors (DS2 1.11), so the request is assembled as a setup
packet the way `SET_PROTOCOL` already was:

```
bmRequestType 0x81   device to host, standard, to an interface
bRequest      0x06   GET_DESCRIPTOR
wValue        0x2200 report descriptor, index 0   (HID 1.11 7.1.1)
wIndex        the interface the configuration parser chose
wLength       what the HID record declared
```

The recipient is the interface deliberately: a report descriptor belongs to one,
and asked of the device a composite has no way to know which is meant. Three new
states carry it — `RequestingReportDescriptor` (setup packet gone),
`ReadingReportDescriptor` (an IN token gone), `FinishingReportDescriptor` (the
empty status packet gone).

**Bounded work per tick.** Each tick issues at most one transaction and reads at
most one block. The reply is never waited for in place: the interrupt is read by
the caller above, one per pass, exactly as every other step here works. A
fifty-byte descriptor on an eight-byte control endpoint is seven ticks, not a
seven-transaction spin on a core that owes the other channel a poll every 8 ms.

Both data toggles are set by hand, because the CH375 tracks neither (DS2 1.6,
1.7). The data stage starts at DATA1 and alternates (USB 2.0 8.6). The transfer
is closed the way a **read** is closed — an empty DATA1 the host *sends*, via a
new `Ch375Transport::finish_control_read()` issuing an OUT token, not the
existing `finish_control_request()` which asks for one (USB 2.0 8.5.3).

**Never abandoning a read with bytes in flight.** Every packet is collected with
`read_block`, which drains the port itself when it cannot complete — the
existing guarantee, unchanged. The only new exposure is a *token* left
outstanding, and that is handled by the silence rule below.

### 3. The protocol decision

When a layout parses, **no `SET_PROTOCOL` is sent at all**. That is the whole
repair. Boot protocol's report is three bytes and has no wheel in it, so asking
for boot after fetching the descriptor would throw away exactly what the fetch
went and got.

Everything else ends where every mouse is today — configured, working, on boot
protocol, without a wheel:

| Situation | Status byte | What happens |
| --- | --- | --- |
| not a mouse (any keyboard) | — | straight to `SET_PROTOCOL(boot)`, never asked |
| interface declares no report descriptor | `0xF8` | never asked, boot |
| declares more than 256 bytes | `0xF7` | never asked, boot (see below) |
| chip refuses the setup packet | `0xF6` | boot |
| device STALLs the request | `0xF6` | boot, in the same attempt |
| a packet cannot be collected off the chip | `0xF4` | boot, in the same attempt |
| bytes arrive and will not parse | `0xF5` | boot |
| device silent, 1st–3rd time | `0xF6` | **attempt fails**, bus reset, retry |
| device silent, 4th time | `0xF3` | never asked, boot |
| descriptor read and used | `0xF2` | left in its own protocol |

**Why silence is different from a STALL.** A STALL is an *answer*: its interrupt
has been read and nothing is outstanding, so the next control transfer can go
out immediately and safely. Silence means a token was issued and never
completed — the chip may still finish it and raise an interrupt, and that
interrupt would be read as the answer to whatever went out next. Issuing
`SET_PROTOCOL` into that is the exact class of bug this codebase has been bitten
by. So a silent request ends the attempt instead, which is what gets
`abort_nak()` issued and the bus reset before anything else is said
(`device.cpp`, the `Enumerating` → `Failed` path that already exists for every
other step that goes quiet).

That alone would let a device that is silent *every* time re-enumerate for ever
over a control it never had — a regression from "works without a wheel" to
"never comes up". So consecutive silences are counted, and after three the
device is simply not asked again and comes up on boot. The counter is cleared on
any completed bring-up, so a different mouse plugged in later gets its own three
tries.

**Keyboards are not asked at all**, and this is the simplest correct design as
the brief allowed. A boot keyboard's report is fixed by HID 1.11 Appendix B.1 —
modifiers, a reserved byte, six key slots — which is what `KeyboardNormalizer`
is written against and what `tests/vectors/hid_reports/keyboard_boot_reports.json`
replays. There is no wheel to recover on a keyboard and nothing to gain by
reading its descriptor, so it keeps `SET_PROTOCOL(boot)` exactly as before.

### 4. The normalizer takes a layout

`MouseNormalizer::set_report_id(bool)` is **gone**, not joined by a third
variant. In its place is `set_layout(const MouseReportLayout&)`, defaulting to
`boot_mouse_layout()`. Every field is read through the layout: presence, offset,
width, and a bounds check against how many bytes actually arrived.

Under the boot layout the behaviour is byte-for-byte what it was — buttons at 0,
dX at 1, dY at 2, and the wheel branch skipped because a three-byte report
cannot hold offset 3. Two things the old reader could not do and a real mouse
needs: sixteen-bit axes read little-endian and whole rather than rounded to
their low byte; and a report whose leading identifier belongs to another
collection on the same endpoint (media keys down the mouse's own pipe) dropped
rather than routed as a click and a jump across the screen.

### 5. The layout reaches the pipeline

`InputPipeline::on_event` and `set_kind` now take the layout alongside the
device kind, and `main.cpp` passes `setups[index]->mouse_layout()`. It is
applied on every `Ready`, not only when a descriptor was read — a device that
would not describe itself hands over boot protocol's layout explicitly, which is
what stops the previous mouse's layout being applied to this one.

`DescriptorSetup::mouse_layout()` is always answerable (boot when nothing was
read), so a caller cannot forget to check `has_mouse_layout()` first and hand the
normalizer nothing.

### Diagnostics

The probe build prints one new line per device:

```
  layout=own rd=0xF2 bytes=52 id=no/0  b@0 x@1/1 y@2/1 w@3 p@0 min=3
```

and `describe_setup_status` names all seven new status bytes. The report buffer
was 900 bytes for two devices' worth of text and did not have room for the extra
line — it stops mid-device when it runs out, which is a diagnostic that lies by
omission — so `link_debug_` is now `CDC_MAX_PAYLOAD - 1`, the real ceiling
(the payload leads with an error code).

---

## How an oversized descriptor is handled

**Both**, at two different sizes, and the boundary is named.

- **Longer than one 64-byte transaction: handled.** This is not the exotic case,
  it is the ordinary one — endpoint zero carries eight bytes on a low-speed
  mouse, so a fifty-byte descriptor is seven transactions. The data stage loops:
  one IN token per tick, appending into a member buffer, until either everything
  asked for has arrived or a packet shorter than `bMaxPacketSize0` says the data
  is over (USB 2.0 8.5.3.2). `bMaxPacketSize0` is taken from byte 7 of the device
  descriptor and only the four legal values (8/16/32/64) are believed; anything
  else leaves it at 8, which is the safe direction — too small merely costs an
  extra transaction, too large mistakes a full packet for the last one.

- **Longer than 256 bytes total: refused by name**, status `0xF7`, before a
  single byte goes on the wire. The length is known from the HID record, so
  there is no need to start and abandon a transfer. 256 covers a mouse
  comfortably (the five-button one in the corpus declares 94); a device with a
  dozen collections beyond that comes up on boot protocol, working, without a
  wheel. Collecting *part* of such a descriptor would be worse than refusing it:
  half a descriptor parses as a different device, and a different device is the
  wrong offsets on every report it will ever send.

The buffer is a member of `DescriptorSetup`, two of them, 512 bytes of BSS
total — not stack. Core 1's worst-case stack path measured on the release ELF is
512 bytes of its 2048, unchanged in shape.

---

## Mutation results

Every mutation below was applied to the source, built, and run. "Caught" means
the suite actually failed; nothing here is asserted from reading the code.

### Re-check of the inherited parser tests (`test_report_descriptor.cpp`)

The brief was right to call them unverified. **The first pass killed 6 of 15.**

| # | Mutation | Before | After |
| --- | --- | --- | --- |
| M1 | short-item stored size 3 read as 3 bytes, not 4 | SURVIVED | CAUGHT |
| M2 | accept a field that does not start on a byte | CAUGHT | CAUGHT |
| M3 | accept any axis width, round down to bytes | SURVIVED | CAUGHT |
| M4 | never report a Report ID | CAUGHT | CAUGHT |
| M5 | input bit cursor never advances | CAUGHT | CAUGHT |
| M6 | count constant padding as a real field | SURVIVED | SURVIVED |
| M7 | a repeated usage overwrites the first | SURVIVED | SURVIVED |
| M8 | the last report wins over the first pointer | SURVIVED | SURVIVED |
| M9 | AC Pan looked for on the wrong usage page | CAUGHT | CAUGHT |
| M10 | short-item length not checked against what is left | CAUGHT | CAUGHT |
| M11 | local usages survive the main item | CAUGHT | CAUGHT |
| M12 | a pointer with no buttons is accepted | SURVIVED | SURVIVED |
| M13 | `out` written before the layout is validated | SURVIVED | CAUGHT |
| M14 | an empty (non-null) descriptor walked rather than refused | SURVIVED | CAUGHT |
| M15 | a long item stepped over as a short one | SURVIVED | SURVIVED |

Three of the survivors were tests that **named** the behaviour they guard and
did not reach it — worse than a missing test, because they read as coverage:

- `an_axis_that_is_not_one_or_two_bytes_wide_is_refused` packs both 12-bit axes
  back to back, so Y lands on bit 20 and is refused for not starting on a byte.
  The width check behind it never executed. Fixed by
  `a_twelve_bit_axis_on_a_byte_boundary_is_still_refused`, which pads to bit 24.
- `a_refused_descriptor_leaves_the_caller_nothing_half_filled` refuses at the
  first step, where no layout has been built. The one path where a finished
  layout exists and could be written out by mistake was uncovered. Fixed by
  `a_layout_refused_at_the_last_step_still_leaves_the_caller_its_own`.
- `nothing_at_all_is_refused_rather_than_read` passes a null pointer, so the
  length half of the guard was never exercised. Fixed in place.

The fourth (M1) had no test at all and is load-bearing for this work: a stored
size of 3 means four data bytes, and that is how a device names a usage on a page
other than the global one — which is exactly how AC Pan arrives. Fixed by
`a_four_byte_usage_item_carries_its_own_page`.

**Four survive and are stated rather than fixed.** Each has no test at all
rather than a test that misses it, and none is on the path this repair takes:
constant padding counted as a field (M6), a repeated usage taken from its second
declaration (M7), the last report winning over the first pointer (M8), a pointer
with no buttons accepted (M12), a long item stepped over as a short one (M15).
M8 is the one I would write next: the parser's comment claims the first pointer
wins, and nothing proves it.

### `MouseNormalizer` — 12 of 12, then 2 more to close survivors

First pass caught 10; N3 (a two-byte axis read as one byte) and N11 (the buttons
field's offset ignored) survived because every axis value in the tests fitted a
byte and every layout put the buttons at offset 0. Closed by
`a_sixteen_bit_axis_carries_more_than_a_byte_could` (X = +300, Y = -300) and
`the_buttons_are_read_where_the_layout_puts_them`. Final: **12/12 caught.**

N1 identifier not skipped · N2 any identifier accepted · N3 two-byte axis read
as one · N4 two-byte axis read big-endian · N5 field past the end read anyway ·
N6 wheel read unsigned · N7 pan never read · N8 wheel never read · N9 short
report read to whatever arrived · N10 boot is not the default layout · N11
buttons offset ignored · N12 X and Y swapped.

### `parse_configuration` — 6 of 7 caught, one equivalent

H1 HID record walked past · H2 length not carried onto the candidate · H3 a
previous interface's length carried forward · H4 subordinate type not checked ·
H5 length read big-endian · H6 entries not required to fit the record — all
caught. H3 needed a decisive test (a *usable* mouse interface followed by the
keyboard that wins, since an unusable one never opens the record).

**H7 — dropping the `bLength < 6` header check — is an equivalent mutant and is
recorded as such in the source.** A record shorter than its header can never
hold the entries it counts either, so H6 refuses exactly the same records with
exactly the same error. What H7's check actually does is stop `bNumDescriptors`
being *read* out of a two-byte record at the end of the buffer. Not observable
from a passing test; kept, and the comment says why.

### `DescriptorSetup` + the control-read transport — 23 of 23

First pass caught 18 of 21 (two were spec errors on my side — an ambiguous
match and a mutation aimed at the wrong file). S1 (keyboards asked too) survived
because `serve_composite_keyboard` declares no HID record, so the length-zero
branch stopped it regardless; closed by
`a_keyboard_that_declares_a_report_descriptor_is_still_not_asked`. S6 (wIndex
forced to zero) survived because the only mouse fixture was on interface 0;
closed by a composite mouse fixture on interface 1. Final: **23/23 caught.**

S1 keyboards asked · S2 an interface declaring none asked anyway · S3 oversized
descriptor fetched · S4 wrong descriptor type · S5 addressed to the device not
the interface · S6 wIndex forced to zero · S7 asks for one packet's worth · S8 a
full packet taken for the last · S9 collected bytes not counted · S10 toggle
never alternates · S11 transfer left unfinished · S12 status packet sent as
DATA0 · S13 silences not counted · S14 asked for ever · S15 a silent request
continues into the next control transfer · S16 a refused request ends the whole
attempt · S17 a layout survives into the next device · S18 an unparseable
descriptor used anyway · S19 endpoint zero assumed to be 64 bytes · S20
SET_PROTOCOL sent even when a layout was read · S21 a read-block failure walked
away from · S22 the read closed with an IN instead of an OUT · S23 every data
packet asked for as DATA1.

S10, S22 and S23 are only catchable because the fake chip was taught to
*enforce* the data toggle — a mismatched toggle now yields no data, no error and
no interrupt, which is what the real chip does and what "120 polls producing
nothing" looked like on the bench.

### `InputPipeline` — 3 of 3

P1 the layout never reaches the normalizer · P2 applied only to a keyboard ·
P3 a fresh Ready does not replace the old layout.

### The captured traces — 4 of 4

Proof that `tests/firmware_native/test_trace_replay.cpp` is live rather than
merely green:

T1 boot is not the normalizer's default layout · T2 the boot layout puts X where
Y is · T3 the boot layout needs four bytes not three · T4 a keyboard modifier
byte read from the wrong index — all caught, against
`keyboard_boot_reports.json` and `mouse_boot_reports.json` unchanged.

**Total: 65 mutations run, 60 caught, 4 stated survivors in inherited parser
behaviour with no test at all, 1 documented equivalent mutant.**

---

## What stays synthetic

**Every report-protocol fixture in this change.** Plainly:

- The two report descriptors in `test_ch375_descriptor_setup.cpp`
  (`plain_wheel_mouse_descriptor`, `report_id_wheel_mouse_descriptor`) are
  written from HID 1.11 Appendix E.10 and from the *shape* the bench's mouse
  must have — seven-byte packets, a Report ID, sixteen-bit axes, a wheel. They
  are not copied off any device.
- The `MouseReportLayout` fixtures in `test_normalizers.cpp` and
  `test_input_pipeline.cpp` likewise.
- Every report body fed through them — the wheel notches, the `-10 / +79`
  movement, the `+300 / -300` — is hand-written.

The corpus in `tests/vectors/hid_reports/` holds **boot** reports only, from
both real devices, because boot protocol is the only thing anything on this
bench has ever been asked for. There is no report-protocol mouse report with a
wheel anywhere in this repository, and there cannot be until a device is asked
for one.

`tests/vectors/hid_descriptors/*.bin` are real configuration descriptors and the
`report_descriptor_length` values asserted against them (52, 63, 94, and 63 for
the composite's keyboard) are read out of those committed bytes — that part is
not synthetic.

**What would confirm the fixtures on hardware**, in order of value:

1. The probe build's new line, with the mouse attached: `layout=own`, a wheel
   offset, and `min=` no larger than the `last report (N bytes)` on the line
   below it. That confirms the fetch, the parse and the sizes agree with what
   the device is actually sending.
2. Scrolling the wheel and seeing it move on the far computer, both directions.
3. `last report (N bytes)` becoming 7 rather than 3 — the device is now in its
   own protocol, which is the thing that carries the wheel at all.
4. Capturing that mouse's real report descriptor bytes into
   `tests/vectors/hid_descriptors/` and its report-protocol reports into
   `tests/vectors/hid_reports/`, then replacing the synthetic fixtures with
   them. Until that exists, these tests prove the firmware does what the HID
   specification says, not what this particular mouse does.

---

## Commands, verbatim

Baseline, before any change (`build/native` deleted first):

```
100% tests passed, 0 tests failed out of 35

Total Test time (real) =   0.69 sec
```

Final, `build/native` deleted and rebuilt from scratch:

```
      Start 35: fuzz_option_combination
35/35 Test #35: fuzz_option_combination ..........   Passed    0.05 sec

100% tests passed, 0 tests failed out of 35

Total Test time (real) =   0.72 sec
NATIVE EXIT: 0
```

`ch375.bat` — the probe image. The **first** run after the descriptor fetch
landed, before `report_descriptor.cpp` was added to `firmware/u1_main/CMakeLists.txt`,
with the native suite fully green at the time:

```
C:/.../firmware/u1_main/ch375/descriptor_setup.hpp:95: undefined reference to `duo_input::u1::ch375::boot_mouse_layout()'
C:/.../firmware/u1_main/input/mouse_normalizer.hpp:35: undefined reference to `duo_input::u1::ch375::boot_mouse_layout()'
C:/.../firmware/u1_main/ch375/descriptor_setup.cpp:79: undefined reference to `duo_input::u1::ch375::boot_mouse_layout()'
C:/.../firmware/u1_main/ch375/descriptor_setup.cpp:222: undefined reference to `duo_input::u1::ch375::boot_mouse_layout()'
C:/.../firmware/u1_main/ch375/descriptor_setup.cpp:223: undefined reference to `duo_input::u1::ch375::parse_mouse_report_descriptor(duo_input::protocol::ByteView, duo_input::u1::ch375::MouseReportLayout&)'
collect2.exe: error: ld returned 1 exit status
ninja: build stopped: subcommand failed.
CH375 EXIT: 1
```

Four native targets had the same gap. That is exactly the failure the brief
warned about, and only building the images found it.

Final `ch375.bat`:

```
-- Configuring done (0.4s)
-- Generating done (0.2s)
-- Build files have been written to: C:/Users/Valentyn/Documents/Codex/2026-08-01/new-chat/work/duo-input-mvp/build/pico-ch375
ninja: no work to do.
CH375 EXIT: 0
```

Final `pico.bat`:

```
-- Configuring done (0.4s)
-- Generating done (0.2s)
-- Build files have been written to: C:/Users/Valentyn/Documents/Codex/2026-08-01/new-chat/work/duo-input-mvp/build/pico-release
ninja: no work to do.
PICO EXIT: 0
```

(The preceding runs, which did the actual compiling, ended
`[11/11] Linking CXX executable firmware\u1_main\duo_u1_main.elf` and
`[6/6] Linking CXX executable firmware\u1_main\duo_u1_main.elf`, both exit 0.)

`python -m pytest tests -q`:

```
.....................................                                    [100%]
37 passed in 0.19s
PYTEST EXIT: 0
```

Core 1 stack, from the release ELF (`stackdepth.py`):

```
### _ZN12_GLOBAL__N_111core1_entryEv: 512 bytes
      64  (cum    64)  core1_entry
      24  (cum    88)  InputPipeline::on_event
     312  (cum   400)  InputPipeline::on_report
      64  (cum   464)  KeyboardNormalizer::apply
      48  (cum   512)  emit
```

512 of 2048. The descriptor path is not the deepest; the 256-byte buffer is a
member, not a frame.

The full configurator suite was **not** run, as instructed — it opens the
board's serial port. No hardware was touched.
