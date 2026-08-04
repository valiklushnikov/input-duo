# Keyboard F8/8 target macro design

## Goal

Replace the shared physical `F8`/`8` button with a target-command macro on the first keyboard Arduino while making the same button inert on the second keyboard Arduino. Both boards currently run the same source and see the same parallel-wired button inputs, so they require distinct firmware images to produce different behavior.

## Deliverables

- Primary firmware at `C:\Users\Valentyn\Desktop\fromPC\keyboard\keyboard.ino` for the Arduino connected to laptop 1. Preserve the pre-change source as `keyboard.ino.bak-2026-08-04` before editing.
- Secondary firmware at `C:\Users\Valentyn\Desktop\fromPC\keyboard_secondary\keyboard_secondary.ino` for the Arduino connected to laptop 2.
- All existing pin mappings, selector behavior, GUI shortcuts, and non-target mapped keys remain unchanged.

## Shared physical button

Mapped-button index `7` is the shared trigger:

- `FUNCTION_MODE`: it currently sends `KEY_F8`.
- `NUMERIC_MODE`: it currently sends ASCII `8`.

In both new firmwares, index `7` no longer sends either original key.

## Primary firmware behavior

A stable new press of mapped-button index `7` starts this exact sequence on laptop 1:

1. Press and release `Enter`.
2. Wait a pseudo-random 80–140 ms pause.
3. Type the exact ASCII text `/target KYPKYMA` with pseudo-random 35–80 ms delays between characters.
4. Wait a pseudo-random 80–140 ms pause.
5. Press and release `Enter`.

Laptop 1 must already use the English keyboard layout. Existing 20 ms input debounce provides one activation per stable press; holding the button does not repeat the macro, and another activation requires release followed by a new press. If the trigger is already held while primary starts, the macro remains disarmed until that button is released once.

Before the opening `Enter`, the firmware calls `Keyboard.releaseAll()` and clears its internal active-key and GUI-shortcut tracking. While the macro is active, it continues scanning and debouncing every input but ignores new keyboard actions from all panel buttons. This isolation applies only to laptop 1. Events observed during the macro are not queued; a physical button must be released and pressed again after the macro to create a new action.

Macro timing is implemented as a non-blocking state machine driven by `millis()`. It must not use `delay()` for typing or pauses.

## Secondary firmware behavior

Mapped-button index `7` is ignored on both press and release in both modes. It never sends `KEY_F8`, ASCII `8`, or the macro. All other buttons continue to use the existing 20 ms debounce and current press/release behavior without any macro-related lockout.

The secondary firmware lives in its own folder whose `.ino` filename matches the folder name so Arduino IDE can open and compile it directly.

## Failure and startup behavior

- Primary never starts the macro merely because its trigger is held during startup; it arms after the first release. Existing startup behavior for every other primary button remains unchanged.
- Secondary ignores the trigger in all states, including startup. Existing startup behavior for its other buttons remains unchanged.
- Every emitted macro character and `Enter` is a complete HID press-and-release event.
- A macro already started on primary completes even if the physical trigger remains held.
- Secondary never participates in macro timing and remains responsive to its other buttons.

## Verification

Automated or compile-time checks cover:

- exact payload `/target KYPKYMA` and the two surrounding `Enter` events;
- 35–80 ms character-delay bounds and 80–140 ms command-pause bounds;
- mapped-button index `7` as the trigger in both modes;
- original `F8`/`8` suppression in both firmware variants;
- successful AVR compilation of primary and secondary for ATmega32U4/Leonardo.

Physical acceptance verifies:

- pressing `F8/8` runs exactly one complete command on laptop 1;
- holding the trigger does not repeat the command;
- other buttons on laptop 1 are ignored only while the macro is typing and work normally afterward;
- pressing `F8/8` produces no input at all on laptop 2;
- all other laptop-2 panel buttons remain responsive during the laptop-1 macro;
- selectors, GUI+1, GUI+2, and every non-target mapped key retain their current behavior.
