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
additional, equally deep 320-byte IRQ can nest at the deepest point. Against
the reviewed 1808-byte reference application path, the remaining analytical
headroom is:

```text
4096 - 1808 - (2 * 320) = 1648 bytes
```

That conservative allowance is not evidence of measured hardware use. A bench
high-water run may refine it, but is not required to prove the linked capacity.

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
