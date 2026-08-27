# How the CH375 pair is wired to U1

Two CH375 host controllers hang off U1, one for the keyboard and one for the
mouse, each on its own serial port. They run at 5 V and the RP2040 runs at
3.3 V, so everything between them goes through a TXS0108E level shifter.

**U1 never touches the 5 V side.** The RP2040 connects only to the shifter's A
port; the B port is the CH375 side. Wiring a GPIO to the B side puts 5 V on a
3.3 V pin.

## Signals

Coordinates are the board positions of each shifter pin, given so a wire can
be traced without counting.

| U1 | A port | B port | CH375 | Direction |
|---|---|---|---|---|
| GP0 | A1 · C12 | B1 · G12 | keyboard RXD | U1 → keyboard |
| GP1 | A2 · C13 | B2 · G13 | keyboard TXD | keyboard → U1 |
| GP2 | A3 · C14 | B3 · G14 | keyboard INT | keyboard → U1 |
| GP4 | A4 · C15 | B4 · G15 | mouse RXD | U1 → mouse |
| GP5 | A5 · C16 | B5 · G16 | mouse TXD | mouse → U1 |
| GP6 | A6 · C17 | B6 · G17 | mouse INT | mouse → U1 |
| — | A7 · C18 | B7 · G18 | — | unused |
| — | A8 · C19 | B8 · G19 | — | unused |

So one full channel reads:

```
U1 GP0 → C12/A1 ↕ G12/B1 → keyboard RXD     (3.3 V ↔ 5 V across the shifter)
U1 GP1 ← C13/A2 ↕ G13/B2 ← keyboard TXD
U1 GP2 ← C14/A3 ↕ G14/B3 ← keyboard INT
```

The mouse channel is the same shape on GP4, GP5, GP6.

## Power

| From | To | Coordinate |
|---|---|---|
| U1 3V3 | VCCA | C11 |
| external +5 V | VCCB | G11 |
| common ground | GND | G20 |
| VCCA (C11) | OE | C20 |

Decoupling: 100 nF from VCCA to ground (C5), 100 nF from VCCB to ground (C6).

## Two things this changes about the plan

### The output enable is not under firmware control

The plan's constraints say `TXS OE = GP7`, and Task 2 asks for OE to be held
low until both ports are configured. **On this board OE is tied to VCCA**, so
the shifter is enabled from the moment it has power and no firmware can hold
it off. GP7 is free.

Nothing is lost by that. The TXS0108E has pull-up resistors on both ports, and
the CH375's RXD pin has a weak pull-up of its own (DS1 section 4, pin 6). An
RP2040 comes out of reset with its GPIOs as inputs, so before the firmware
configures anything, both ends of every line are held high - which is exactly
the idle state of a serial line. The glitch that holding OE low would have
prevented cannot happen here.

Task 2 should drop the OE step rather than pretend to perform it.

### It works with the shifter removed

**2026-08-27.** With the TXS0108E taken out and the CH375s wired to U1
directly, both chips answer CHECK_EXIST with 0xA8 - the inverse of the 0x57
they were sent (DS1 5.5). First time either of them has answered anything.

That also settles two questions the level readings could not: the data lines
are not swapped, and both chips really are in serial mode. A chip in parallel
mode, or one wired backwards, cannot return that byte.

So the shifter was the cause. The proof was taking it out, not the reasoning
that led there - which had already named it once on evidence that fitted a
part relaying noise just as well as one making it.

**This leaves an electrical problem that has to be solved before the wiring is
finished.** A CH375 running at 5 V drives its TXD to 5 V, and an RP2040 pin is
not 5 V tolerant: the protection diode into the 3.3 V rail conducts, and the
pin degrades. It works today and that is not the same as being safe. Either:

- run the CH375s from 3.3 V - the part supports it, with V3 tied to VCC
  (DS1 section 6.2.3) - which removes the mismatch entirely; or
- put a divider on each input to U1. There are four of them: keyboard TXD and
  INT, mouse TXD and INT. Two resistors each, so eight.

The transmit direction, U1 to CH375 RXD, is 3.3 V into a 5 V input and is the
easier half, but check what the chip accepts as a logic high rather than
assuming.

### What the line looked like with the shifter in place



The measurement that counts does not involve PIO at all. Both receive pads are
read as ordinary inputs with the internal pull-up, for one second, before any
state machine starts:

| | leads unsoldered | leads soldered on |
|---|---|---|
| GP1 | high the whole second, zero transitions | low 34% of the time, transitions past the counter's ceiling |
| GP5 | high the whole second, zero transitions | low 31% of the time, transitions past the counter's ceiling |

A disconnected pad is perfectly steady. A connected one is in motion the whole
time. Nothing in this firmware can produce that: it is a loop reading a pin.

**So the disturbance arrives through the leads, from outside U1.** The board,
the PIO port and the firmware are all cleared by it, and so is the earlier
suspicion of USB traffic - the mouse was unplugged for this and both channels
look the same.

And the wire is not an antenna. Unsoldered at the shifter's A-side pads but
still attached to U1 - same wire, same length, same place on the bench - both
pads go back to high the whole second with zero transitions.

Nor is the shifter generating it. With the A side still wired to U1 and the B
side lifted off the CH375's TXD pins, both pads are steady again.

| what the lead is attached to | GP1 | GP5 |
|---|---|---|
| nothing | steady high, zero transitions | steady high, zero |
| U1 only, hanging free | steady high, zero | steady high, zero |
| U1 and the shifter, B side lifted | steady high, zero | steady high, zero |
| the whole chain, through to CH375 TXD | low 34%, past the ceiling | low 31%, same |

**The disturbance starts on the B side** - at the CH375's TXD pin or the short
lead soldered to it. The shifter carries it faithfully; it is in the path, not
the cause.

That distinction cost a wrong conclusion here once already. The measurement
before this one showed the A side going quiet when the lead was lifted, and it
was written up as the shifter driving the line - which the same evidence
supports exactly as well when the shifter is only relaying. One more
disconnection, one pad further along, and it separates. Do not replace the
part on the strength of the earlier row.

What is left to look at, in order of what costs nothing:

- **The solder at pin 5 itself.** It is fine-pitch and the leads were soldered
  to the chip by hand, twice, identically. A bridge to pin 4 (RD#) or pin 6
  (RXD) would put the chip's own signals on this wire. Both channels behave
  the same, which argues either for a repeated mistake or for something
  intrinsic to the module.
- **The 12 MHz crystal, which is millimetres away.** A lead soldered to pin 5
  runs past XI and XO on pins 13 and 14. That is the one strong, fast signal in
  the neighbourhood.
- **The chip actually transmitting.** After reset in serial mode it should say
  nothing until asked, but it has never yet answered anything either.

The way to narrow it is one channel at a time: put the keyboard's B-side lead
back and leave the mouse's off. If only GP1 goes noisy, the two chips can be
compared against each other instead of guessed at together.

### What was measured before this, and why none of it counted

Every earlier reading of "the line is noisy" was taken after reflashing over
USB, which restarts the processor and leaves every peripheral running. State
machines from the previous firmware run kept executing while the new program
was loaded underneath their program counters, and pushed the results into the
queues this code then read as frames. The port now resets its PIO block on
startup, and the difference that made is the whole table above.

Two other faults in the instrument were found in the same stretch, both by
review rather than by measurement:

- The receive program pushed by hand while autopush was also enabled, so every
  real frame was followed by an empty one. Half the "traffic" was that.
- The framing-error flag was read from the wrong PIO interrupt. `irq 4 rel`
  raises flag 4 + n for state machine n; flag n was being read instead, so the
  count was zero no matter what arrived - and that zero was very nearly used
  as evidence that the frames were real data.

## What the serial framing needs

The CH375's serial format is nine data bits, not eight: the ninth says whether
the other eight are a command or a data byte (DS1 section 6.2.2, and
`ch375-command-table.md`). An RP2040's hardware UART cannot do nine, so these
ports have to be built from PIO - which is also why the pin assignment above
survives the change. PIO takes any GPIO, so nothing on this board has to move.
