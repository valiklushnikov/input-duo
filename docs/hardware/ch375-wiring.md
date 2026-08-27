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

### The shifter is the one to suspect if the link is flaky

The TXS0108E senses direction automatically and carries its pull-ups on both
sides, which suits open-drain buses. A serial line is driven push-pull, and
this part is more sensitive there than its push-pull sibling the TXB0108,
particularly as the rate goes up. CH375 starts at 9600 bps and this device has
no reason to push it hard, so it should be comfortable - but if bytes start
arriving damaged, the shifter is the first thing to test, not the last.

## What the serial framing needs

The CH375's serial format is nine data bits, not eight: the ninth says whether
the other eight are a command or a data byte (DS1 section 6.2.2, and
`ch375-command-table.md`). An RP2040's hardware UART cannot do nine, so these
ports have to be built from PIO - which is also why the pin assignment above
survives the change. PIO takes any GPIO, so nothing on this board has to move.
