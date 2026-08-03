# Master-side target macro design

## Goal

Use the currently unused PS/2 Explorer side button with mask `0x20` to type a fixed command on the laptop connected directly to the master Pro Micro. The existing `0x10` side button continues to switch mouse control between laptops.

## User-visible behavior

One new press of side button `0x20` starts this exact keyboard sequence on the master laptop:

1. Press and release `Enter`.
2. Wait a short randomized pause.
3. Type the exact ASCII text `/target KYPKYMA`.
4. Wait a short randomized pause.
5. Press and release `Enter`.

The computer must already have the English keyboard layout selected. Character delays vary randomly from 35 to 80 milliseconds to resemble quick human typing. The pauses after the first `Enter` and before the final `Enter` vary randomly from 80 to 140 milliseconds.

The command always goes to the master laptop, regardless of whether mouse control currently targets the master or receiver laptop. The receiver firmware and the inter-board mouse protocol do not change.

## Button mapping

- `0x10`: switch the active mouse target exactly as in v4.
- `0x20`: start the master-side keyboard macro.
- Middle button: remain fully ignored.

The macro starts only after a new press has remained stable for at least 25 milliseconds. Holding the button produces one macro. A subsequent macro requires a release that remains stable for at least 25 milliseconds followed by another stable press. A press received while a macro is already active is ignored rather than queued.

## Architecture

The master firmware adds Arduino `Keyboard` HID support alongside the existing `Mouse` HID support. Macro execution is implemented as a non-blocking state machine advanced from `loop()` using elapsed time. It must not use blocking `delay()` calls for human-like timing because PS/2 polling, local pointer movement, and I2C forwarding must continue while text is typed.

The state machine owns:

- the current macro phase;
- the next character index;
- the scheduled time for the next keyboard event;
- the debounced `0x20` button state and re-arm state.

Each keyboard event uses a complete press-and-release operation. No key remains held between loop iterations. Startup initializes keyboard HID but never starts the macro automatically.

## Failure and recovery behavior

If the mouse disconnects after the macro has started, the already-started macro completes because keyboard scheduling is independent of PS/2 report availability. No new macro can start without a later valid `0x20` press after mouse recovery and release/re-press re-arming.

PS/2 reconnect handling must reset the side-button debounce/re-arm input state without synthesizing a macro. Existing three-consecutive-error handling and smooth mouse transport behavior remain unchanged.

## Verification

Automated checks cover:

- decoding and selecting side-button mask `0x20`;
- debounced single activation for a held press;
- re-arming only after stable release;
- ignoring activation while the macro is active;
- the exact payload `/target KYPKYMA` and the two surrounding `Enter` events;
- bounded 35–80 ms inter-character delays;
- bounded 80–140 ms pauses around the payload;
- successful AVR compilation of the protocol test, master, receiver, and diagnostic sketch.

Physical acceptance on both laptops verifies:

- `0x10` still switches the mouse target once per press;
- `0x20` types and submits the exact command on the master laptop only;
- holding `0x20` does not repeat the command;
- mouse movement remains smooth during the macro;
- middle button remains ignored;
- receiver-side mouse behavior is unchanged.

## Deliverable

Package the updated master firmware, unchanged receiver firmware, required headers, and updated README as `dual-laptop-mouse-switch-working-v5.zip`. Only the master board needs reflashing for this feature.
