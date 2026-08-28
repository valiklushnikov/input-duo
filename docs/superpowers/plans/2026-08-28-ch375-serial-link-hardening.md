# CH375 serial link hardening

The work already landed on `feature/duo-input-foundation` as ten commits
(`17071bd..e3323b4`). This file exists so that work has requirements to be
judged against, because it was written and shipped without independent review
and the session that produced it made repeated errors.

## Context

U1 (RP2040-Zero) talks to two CH375B USB host controllers over a bit-banged
nine-bit serial port built in PIO — one start bit, nine data bits (the ninth
is the command/data flag), one stop bit. Each controller drives one USB
peripheral. The observed faults, in the order they were attacked:

1. Moving the mouse made the peripheral re-enumerate repeatedly.
2. A channel would go permanently silent and only a module power cycle
   appeared to recover it.
3. The state machine declared devices lost that had never disconnected.

## Global Constraints

- **Nothing may block the shared loop for long.** Core 0 services USB, the
  SPI link to U2 and the command queue on the same loop. The watchdog is
  2000 ms. Any added blocking path must be bounded and justified.
- **Never write a command at a port rate that has not just been proved.**
  CH375 parses commands positionally: a byte half-heard at the wrong rate is
  swallowed as some command's parameter and every byte after it is out of
  step. This is the mechanism behind "the chip stopped answering".
- **Never abandon a read with bytes still in flight.** The read command has
  already gone; the chip sends the block regardless. Unread bytes become the
  answers to later commands, permanently.
- **A PIO state machine cannot adopt a new clock divider while running.** It
  holds a program counter mid-frame, a shift counter mid-byte and a divider
  mid-bit. Changing the rate requires stopping, clearing, restarting and
  re-entering the program.
- **Diagnostics must not invent readings.** Counters that never move must not
  be indistinguishable from counters that cannot move; a silent failure path
  is a reporting defect, not merely a missing feature.
- Native tests (`ctest`, 27 suites) must stay green. Firmware must build for
  `waveshare_rp2040_zero` under the `DUO_CH375_PROBE` configuration.

## What the ten commits claim to do

- `a1bcfdb` Raise the port from 9600, where one mouse report costs fifteen
  bytes at 1.15 ms each — 17 ms for something a moving hand produces every
  8 ms.
- `511e5f1`, `d81678e`, `daf18c4` Establish the rate safely: a ladder of
  rates, each proved by CHECK_EXIST answering twice, one rung per recovery
  cycle, with recovery that never writes at an unproved rate.
- `b43cbbe`, `eaeb657` Make the invisible visible: count both previously
  silent `set_usb_mode` failure paths; search for a chip stranded at an
  abandoned rate; distinguish a deaf chip from one refusing modes.
- `a733a57` Restart the PIO state machines on a rate change.
- `7c987ff` One outstanding token at a time, with a cap so a lost interrupt
  does not silence the device for good.
- `7c133fd` Drain the port when a block read is abandoned.
- `e3323b4` Sweep the receiver's sampling rate on a dead channel to separate
  a silent chip from a mis-sampling receiver.

## What review must establish

- Whether each constraint above is actually held by the code as written.
- Whether any added path can block the shared loop past what the watchdog or
  the state machine's own timeouts tolerate. `slowest pass round the loop`
  grew from 75 ms to 361 ms across this work, which is a measured regression
  and not yet explained.
- Whether the tests added with each change would fail if the behaviour they
  describe were removed, or whether any assert something that cannot fail.
- Whether any of these changes could itself cause a channel to go silent.
