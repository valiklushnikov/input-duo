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

### What the line actually does, measured

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

| lead attached to | GP1 | GP5 |
|---|---|---|
| U1 and the shifter | low 34%, transitions past the ceiling | low 31%, same |
| U1 only, hanging free | steady high, zero | steady high, zero |
| nothing | steady high, zero | steady high, zero |

**The A side of the TXS0108E is driving it.** Not a suspect any more.

That is not the same as the part being faulty or the wrong choice. TI
documents this: the one-shot accelerator can retrigger and oscillate when
there is too much capacitance on a pin, and short traces are a stated
requirement of the part. Flying leads soldered to chip pins and run across a
bench are the opposite of that. The CH375s were powered and idle throughout,
so nothing was being translated - the shifter was doing this on its own.

Cheapest thing to try first, because it costs nothing: **make the leads
short**. If the oscillation is capacitance-driven, that is the documented fix
and it settles whether the part can work here at all.

If that does not do it, then either:

- **TXB0108** in its place, which is now a decision with evidence behind it
  rather than a guess. It has its own limits - a weak output through roughly
  4 kOhm, unhappy with external pull-ups and capacitive loads - so it is not
  guaranteed either.
- Or no shifter on the receive direction: a divider per input to U1, and there
  are four of those - keyboard TXD and INT, mouse TXD and INT - so eight
  resistors. The transmit direction may be able to go straight from 3.3 V, but
  only after checking what a CH375B accepts as a logic high.

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
