# PIO USB Core 1 stack budget

The production PIO USB and frozen PIO USB reference images reserve exactly
4096 bytes for the Pico SDK Core 1 stack. The shipping CH375 image keeps the
SDK default of 2048 bytes. `cmake/pio_usb_core1_stack.cmake` applies the larger
definition only to the two PIO USB executables, and
`cmake/pio_usb_core1_stack_guard.ld` refuses either link if the SDK's actual
`.stack1_dummy` allocation is smaller than 4096 bytes.

## Analytical budget

This is a compiler- and link-derived budget, not a hardware stack high-water
measurement. GCC 10.3 `-fstack-usage` output from clean MinSizeRel builds gives
the following reachable SOF path on RP2040:

| Frame | Bytes |
| --- | ---: |
| Cortex-M0+ hardware exception frame | 32 |
| `alarm_pool_irq_handler` | 64 |
| `sof_timer` | 8 |
| `pio_usb_host_frame` | 40 |
| deepest transaction subtree | 176 |
| **single deepest SOF path** | **320** |

The 176-byte subtree is `usb_in_transaction` (40) plus
`pio_usb_ll_transfer_continue` (96) plus `pio_usb_ll_encode_tx_data` (40).
The endpoint-notification alternative is 136 bytes:
`pio_usb_host_irq_handler` (32), its endpoint helper (64),
`hcd_event_handler` (16), and `tu_fifo_write` (24). Its complete IRQ path is
therefore 280 bytes, below the 320-byte transaction path.

The SDK installs the alarm-pool handler as an exclusive handler and neither
firmware target changes the default IRQ priority, so the timer IRQ does not
normally pre-empt itself. The sign-off budget nevertheless assumes one
additional, equally deep 320-byte IRQ can nest at the deepest point. Fresh
Task 4 clean links include the full synchronous input and discovery callers.
The reference maximum is now 2112 bytes: Core 1 wrapper (8), entry (64),
service_input (416), consume (8), on_mount (1032), classification (40),
SHA-256 (128), finish (56), padding update (32), and block transform (328).
The hash-padding branch is deeper than the descriptor parser branch.

An additional 64 bytes covers ROM memory routines, veneers and alignment.
The remaining reference analytical headroom is:

```text
4096 - 2112 - 64 - (2 * 320) = 1280 bytes
```

That conservative allowance is not evidence of measured hardware use. A bench
high-water run may refine it, but is not required to prove the linked capacity.

The production PIO maximum audited source-processing path is 1616 bytes:
wrapper (8), entry (48), backend task (56), process_pending (296), process
(48), remove_device (1080), latch_fault (32), and push_event (48). With the
same allowances it leaves 1776 bytes. Its report-to-route-binding path is
1128 bytes; capture, detach releases and ordinary discovery are smaller.

CH375's deepest input path is report-to-route-binding at 1144 bytes, including
the wrapper, entry, SourceTable, pipeline, both handlers, runtime, binding
engine, route change and release helpers. Core 1 installs only the FIFO
lockout IRQ: its 16-byte handler plus the 32-byte exception frame fits a
64-byte allowance including alignment. Budgeting two such IRQs, plus the
same extra 64 bytes, leaves `2048 - 1144 - 128 - 64 = 712` bytes. CH375 does
not run the PIO SOF IRQ. Its descriptor setup poll frame is 208 bytes after
building boot report sets directly into its owned storage.

`tests/build/test_input_stack_budget.py` recompiles the current callers with
each configured preset's ARM flags into fresh temporary objects and checks
frame ceilings; stale ELF or stack-usage files cannot satisfy those tests.
The complete Task 4 report records the call-path sums, mutations, clean-link
commands and SRAM regions.

## Verification

Rebuild both PIO targets from clean objects, then inspect the actual linked
section and the SDK allocation symbol:

```powershell
cmake --build --preset pico-pio-usb-release --clean-first
cmake --build --preset pico-pio-usb-reference-release --clean-first
arm-none-eabi-readelf -SW build/pico-pio-usb-release/firmware/u1_main/duo_u1_main.elf
arm-none-eabi-nm -C -S build/pico-pio-usb-release/firmware/u1_main/duo_u1_main.elf
arm-none-eabi-readelf -SW build/pico-pio-usb-reference-release/firmware/u1_reference/duo_u1_reference.elf
arm-none-eabi-nm -C -S build/pico-pio-usb-reference-release/firmware/u1_reference/duo_u1_reference.elf
```

Both ELFs must report `.stack1_dummy` and `core1_stack` as `0x1000` bytes.
`tests/build/test_backend_artifacts.py` checks the linked symbol directly for
both PIO backends and separately checks that CH375 remains `0x800` bytes.

To mutation-test the link guard, temporarily change only the compile definition
in `cmake/pio_usb_core1_stack.cmake` to 2048, leave the linker assertion at
4096, and rebuild each PIO target. Both links must fail with
`PIO USB Core 1 needs at least 4096 bytes for discovery plus nested IRQs`.
Restore 4096 and repeat the clean builds before using any artifacts.
