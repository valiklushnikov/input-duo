# Two field defects: side buttons and the write freeze

## Context

Reported from the bench on 2026-09-07 against `DuoInput.exe` and the U1 image
built at 14:37 from `build/pico-pio-usb-reference-release`
(`duo_u1_reference.uf2`, the `PIO_USB_REFERENCE` backend). Both defects are
observed on hardware; neither has a reproduction in the suite yet.

The mouse is **not** in boot protocol. Since `2cc7570` an interface that has a
readable report descriptor is switched to report protocol, and this mouse's
reports carry a Report ID: the measured report is
`03 00 08 00 FC FF 00 00` — id `0x03`, one button byte, 16-bit X and Y, a
wheel byte. Wheel and buttons 1-3 work. So every hypothesis that begins "the
mouse is in boot protocol" is already refuted; do not spend a turn on it.

## Global Constraints

- **Root cause before fixes.** No task may propose or land a fix that is not
  traced to a specific line. A symptom fix is a failed task.
- **Every fix ships with a test that fails without it.** Mutation-verify it:
  delete or invert the fix, watch the new test fail, restore. A test that
  passes against the unfixed code is testing nothing and must be rewritten.
- **Ninja does not track headers.** Any native build used as evidence must be
  configured fresh or built with `--clean-first`; an incremental run after a
  header edit is not evidence.
- Do not reflash, power-cycle, or otherwise touch the bench hardware. Both
  tasks are to be settled from source and the native suite. Where an answer
  genuinely requires the physical device, say so and stop rather than guess.
- The native suite lives in `tests/firmware_native`; the host suite in
  `configurator/tests` and `tests/`.

## Task 1: Why no side button reaches anything

Buttons 4 and 5 do not work through the device at all: they do not reach the
attached PC as back/forward, and `Мышь → определить кнопку` never sees them.
Buttons 1-3 and the wheel do work.

Trace the button bits from the wire to both consumers and find the line that
drops them. The whole chain is:

- `firmware/u1_main/input/hid/report_descriptor.cpp` — builds
  `MouseReportLayout.buttons` from the descriptor. Note `record()` at line 83:
  a second Button-page input item is discarded because the first declaration
  wins. A descriptor that declares buttons 1-3 in one input item and 4-5 in a
  second would therefore yield `bits = 3`.
- `firmware/u1_main/input/mouse_normalizer.cpp:112` — masks the button byte to
  `layout_.buttons.bits`, so a `bits = 3` layout silently erases 4 and 5.
- `firmware/u1_main/core1_runtime.cpp:108` — folds the bit into `buttons_`.
- `firmware/u1_main/mapping/capture.cpp:113` — the capture path.
- `firmware/u2_endpoint/usb_descriptors.cpp:67` — `TUD_HID_REPORT_DESC_MOUSE()`
  declares five buttons, so U2 is not the ceiling.
- `firmware/u1_reference/source_adapter.cpp` — the reference target's own
  adapter, which is what is actually running on the bench.

Deliverables: the exact line that drops buttons 4-5, with the descriptor shape
that triggers it; a native test that reproduces it; and the smallest fix.
Every parser test in the tree today declares buttons as a single
`Usage Min 1 / Usage Max 5` run — a split run is uncovered, so check whether
`record()` is the defect before looking further afield.

## Task 2: Why writing a mapping freezes input until a power cycle

Writing a new mapping from the configurator left the device passing no input
to the PC. The configurator's write itself appeared to succeed. Only a power
cycle restored it; the device did not recover on its own.

`PicoFlash::erase` and `PicoFlash::program`
(`firmware/u1_main/pico_flash.cpp`) stop Core 1 with
`multicore_lockout_start_blocking()` for the whole erase, and on this backend
Core 1 is what bit-bangs the entire PIO USB host. `WRITE_COMMIT`
(`firmware/u1_main/config_service.cpp:797`) then hands the new view to Core 1
through `g_config_handoff` and spins until Core 1 answers
(`firmware/u1_main/main.cpp:360`).

Establish what actually dies and why nothing re-arms it. In particular:
whether the host stack, the root port, or a device's interrupt endpoint is
left with no transfer outstanding after the freeze, and whether any existing
recovery path can re-arm it. The hub status re-arm is already patched
(`patches/tinyusb/0001-duo-input-host-fixes.patch`), so that specific path is
covered — establish whether the equivalent hole exists for HID interrupt
endpoints or the root port.

Deliverables: the mechanism, named at the line; a test at whatever level can
hold it; and a fix proposal. If the true answer needs a measurement only the
bench can give, say exactly which measurement and stop.
