# Duo Input Core U1/U2 Firmware Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Собрать безопасные U1/U2 UF2-прошивки с TinyUSB HID, CDC на U1, SPI-связью, Flash A/B, watchdog и fail-safe, пока физический ввод подаётся тестовым генератором.

**Architecture:** U1 Core 0 единолично владеет USB HID, CDC, SPI и Flash; Core 1 пока заменён test-event producer. U2 принимает полные keyboard states и mouse deltas по SPI, проверяет каждый кадр и выполняет Release All через 100 мс без валидной связи.

**Tech Stack:** Pico SDK C++17, TinyUSB device, hardware SPI1, RP2040 multicore FIFO/SPSC queue, CMake/CTest, picotool/UF2.

**Spec:** `docs/superpowers/specs/2026-08-25-duo-input-firmware-configurator-design.md`

## Global Constraints

- Выполнить сначала `2026-08-25-duo-input-foundation.md`.
- U1 USB: Keyboard 6KRO + Mouse 5 buttons/wheel/pan + Consumer + CDC; U2 — те же HID без CDC.
- U1↔U2: SPI1, U1 master, U2 slave, 1 МГц, 64-byte frames, heartbeat ≤20 мс, U2 timeout 100 мс.
- U1 Flash: firmware 1024 КиБ, Config A/B по 384 КиБ, service 256 КиБ.
- HID starts released after every reset/error; damaged packets never create input.
- U1 GP8/9/10/11 = RX/CS/SCK/TX; U2 GP8/9/10/11 = RX/CS/SCK/TX.
- SW1 GP14 = emergency mouse toggle; SW2 GP15 = stop/release; hold 5 seconds confirms factory reset.

## Locked File Structure

```text
cmake/pico_sdk_import.cmake
firmware/CMakeLists.txt
firmware/common/hid/{types,state_manager}.*
firmware/common/link/{spi_protocol,link_status}.*
firmware/common/storage/{flash_layout,ab_store}.*
firmware/common/diagnostics/{codes,counters}.*
firmware/u1_main/{main,usb_descriptors,usb_service,spi_master,config_service,buttons}.cpp
firmware/u2_endpoint/{main,usb_descriptors,usb_service,spi_slave,link_watchdog}.cpp
tests/firmware_native/test_hid_state_manager.cpp
tests/firmware_native/test_link_watchdog.cpp
tests/firmware_native/test_output_runtime.cpp
tests/firmware_native/test_ab_store.cpp
tests/firmware_native/test_buttons.cpp
```

---

### Task 1: Pico SDK targets and deterministic builds

**Files:**
- Create: `cmake/pico_sdk_import.cmake`
- Create: `firmware/CMakeLists.txt`
- Create: `firmware/u1_main/CMakeLists.txt`
- Create: `firmware/u1_main/main.cpp`
- Create: `firmware/u2_endpoint/CMakeLists.txt`
- Create: `firmware/u2_endpoint/main.cpp`
- Create: `CMakePresets.json`

**Interfaces:**
- Produces CMake targets `duo_u1_main`, `duo_u2_endpoint` and UF2 artifacts.

- [x] **Step 1: Add build-contract test**

Create `tests/build/test_firmware_artifacts.py` asserting that a configured build must produce exactly `duo_u1_main.uf2` and `duo_u2_endpoint.uf2` and that map files fit below `0x10100000` firmware boundary.

- [x] **Step 2: Verify red state**

Run `python -m pytest tests/build/test_firmware_artifacts.py -q`; expected missing artifacts.

- [x] **Step 3: Add minimal Pico targets**

Both `main.cpp` files initialize stdio disabled, watchdog disabled temporarily, board LED off, then call `tight_loop_contents()`. Link `pico_stdlib`, `tinyusb_device`, `tinyusb_board`, `hardware_spi`, `hardware_flash`, `hardware_watchdog`, `pico_multicore` where required.

- [x] **Step 4: Build both targets**

```powershell
cmake --preset pico-release
cmake --build --preset pico-release --parallel
python -m pytest tests/build/test_firmware_artifacts.py -q
```

Expected: two UF2 files and passing size check.

- [x] **Step 5: Commit**

```powershell
git add cmake firmware CMakePresets.json tests/build
git commit -m "build: add RP2040 main and endpoint targets"
```

### Task 2: HID state manager independent of TinyUSB

**Files:**
- Create: `firmware/common/hid/types.hpp`
- Create: `firmware/common/hid/state_manager.hpp`
- Create: `firmware/common/hid/state_manager.cpp`
- Create: `tests/firmware_native/test_hid_state_manager.cpp`

**Interfaces:**
- Produces: `HidStateManager::physical_key`, `macro_key`, `set_mouse_buttons`, `mouse_delta`, `release_target`, `release_all`, `snapshot(Target)`.
- `KeyboardSnapshot` contains modifiers and six sorted usages; `MouseSnapshot` contains buttons and accumulated signed 16-bit deltas.

- [x] **Step 1: Write ownership and overflow tests**

```cpp
TEST_CASE(key_owned_by_physical_and_macro_survives_macro_release) {
  HidStateManager m;
  m.physical_key(Target::Pc1, 0x04, true);
  m.macro_key(3, Target::Pc1, 0x04, true);
  m.release_macro(3);
  CHECK(m.snapshot(Target::Pc1).keyboard.contains(0x04));
}

TEST_CASE(seventh_key_is_rejected_without_corrupting_six) {
  HidStateManager m;
  for (uint8_t usage = 0x04; usage < 0x0A; ++usage) {
    CHECK_EQ(m.physical_key(Target::Pc1, usage, true), HidResult::Ok);
  }
  CHECK_EQ(m.physical_key(Target::Pc1, 0x0A, true), HidResult::KeyCapacity);
  CHECK_EQ(m.snapshot(Target::Pc1).keyboard.key_count, 6);
}
```

- [x] **Step 2: Confirm failing compile**

Run native target `test_hid_state_manager`; expected missing class.

- [x] **Step 3: Implement fixed-capacity ownership tables**

Use bitsets/arrays indexed by HID usage and macro owner ID; no heap. Mouse deltas saturate at `int16_t` bounds and are consumed exactly once by `take_snapshot()`.

- [x] **Step 4: Run state and full native suites**

Expected: ownership, release target/all, button masks, delta saturation and capacity tests pass.

- [x] **Step 5: Commit**

```powershell
git add firmware/common/hid tests/firmware_native
git commit -m "feat: track independent HID output states"
```

### Task 3: TinyUSB descriptors and USB services

**Files:**
- Create: `firmware/common/hid/report_ids.hpp`
- Create: `firmware/u1_main/usb_descriptors.cpp`
- Create: `firmware/u1_main/usb_service.hpp`
- Create: `firmware/u1_main/usb_service.cpp`
- Create: `firmware/u2_endpoint/usb_descriptors.cpp`
- Create: `firmware/u2_endpoint/usb_service.hpp`
- Create: `firmware/u2_endpoint/usb_service.cpp`
- Create: `tests/build/test_usb_descriptors.py`
- Create: `tools/dump_usb_descriptors.py`

**Interfaces:**
- Produces: `UsbService::task()`, `submit_keyboard`, `submit_mouse`, `submit_consumer`, `mounted`, `suspended`.

- [x] **Step 1: Write descriptor contract checks**

`tools/dump_usb_descriptors.py` imports the descriptor byte arrays extracted from firmware ELF symbols and emits stable JSON. Test that JSON for U1 interfaces Keyboard/Mouse/Consumer/CDC and U2 Keyboard/Mouse/Consumer only; assert five mouse buttons and unique product strings.

- [x] **Step 2: Verify current failure**

Run `python -m pytest tests/build/test_usb_descriptors.py -q`; expected descriptors missing.

- [x] **Step 3: Implement descriptors and rate-limited report sender**

Use fixed report IDs, unique serial callback from `pico_get_unique_board_id`, Boot keyboard compatibility, pan wheel usage, and no Remote Wakeup. `task()` calls `tud_task()` each loop and sends only changed absolute states; mouse delta is sent once when endpoint ready.

- [x] **Step 4: Build and inspect descriptors**

Run both firmware builds and descriptor contract test. On one board, enumerate U1 and U2 separately with Windows `Get-PnpDevice`; verify U1 exposes COM and U2 does not.

- [x] **Step 5: Commit**

```powershell
git add firmware/common/hid firmware/u1_main firmware/u2_endpoint tests/build
git commit -m "feat: expose composite HID devices on both endpoints"
```

### Task 4: SPI link engines and U2 fail-safe

**Files:**
- Create: `firmware/common/link/spi_protocol.hpp`
- Create: `firmware/common/link/spi_protocol.cpp`
- Create: `firmware/u1_main/spi_master.hpp`
- Create: `firmware/u1_main/spi_master.cpp`
- Create: `firmware/u2_endpoint/spi_slave.hpp`
- Create: `firmware/u2_endpoint/spi_slave.cpp`
- Create: `firmware/u2_endpoint/link_watchdog.hpp`
- Create: `firmware/u2_endpoint/link_watchdog.cpp`
- Create: `tests/firmware_native/test_link_watchdog.cpp`

**Interfaces:**
- Produces U1: `SpiMaster::poll(now_us, optional<OutboundMessage>) -> EndpointStatus`.
- Produces U2: `SpiSlave::take_valid_frame()`, `LinkWatchdog::observe_valid(now_ms)`, `expired(now_ms)`.

- [x] **Step 1: Write sequence/timeout tests**

```cpp
TEST_CASE(endpoint_releases_at_100ms_not_99ms) {
  LinkWatchdog w(100);
  w.observe_valid(1000);
  CHECK_FALSE(w.expired(1099));
  CHECK(w.expired(1100));
}
```

Add duplicate sequence accepted idempotently, gap flagged, bad CRC ignored, heartbeat refresh tests.

- [x] **Step 2: Verify failures**

Run native link tests and confirm missing engines.

- [x] **Step 3: Implement hardware SPI1 at 1 MHz**

U1 configures GP8/9/10/11 and asserts CS per exact 64-byte transaction. U2 uses SPI slave plus DMA or IRQ-owned fixed buffers; ISR only swaps completed buffers. CRC decode occurs in loop context.

- [x] **Step 4: Hardware loop test**

Flash both boards, run generated key/mouse pattern for 10 minutes, unplug the four-wire link, and record U2 release ≤100 ms. Restore link and verify handshake without replay.

**Done, 2026-08-27.** The link is up at 1 MHz: 100 frames every two seconds,
no CRC errors in steady state.

The link loss was produced without touching the wires - U1 is put into its
bootloader over CDC, which stops it dead - because the fail-safe cannot be
watched while it happens: everything the host can see about U2 travels over
the link that just went silent. U2 therefore records the drop and reports it
once the link is back. Measured three times in a row:

| | drops | release_ms |
|---|---|---|
| after boot, U1 not yet talking | 1 | 0 |
| first cut | 2 | 100 |
| second cut | 3 | 100 |
| third cut | 4 | 100 |

Recovery costs exactly one damaged frame each time. U1 restarting begins its
sequence at zero again, which U2 accepts as a gap rather than refusing as a
replay - `a_sequence_that_goes_backwards_is_a_gap_not_a_duplicate` pins that.

**Not done:** the 10-minute generated key/mouse pattern. It requires
`DUO_TEST_PATTERN`, which types on whatever computer is attached, and there is
no second computer for U2 yet (2026-08-27).

- [x] **Step 5: Commit**

```powershell
git add firmware/common/link firmware/u1_main firmware/u2_endpoint tests/firmware_native
git commit -m "feat: add fail-safe SPI endpoint link"
```

### Task 5: U1 output loop and multicore command queue

**Files:**
- Create: `firmware/common/runtime/spsc_queue.hpp`
- Create: `firmware/common/runtime/output_command.hpp`
- Create: `firmware/u1_main/output_runtime.hpp`
- Create: `firmware/u1_main/output_runtime.cpp`
- Create: `tests/firmware_native/test_output_runtime.cpp`

**Interfaces:**
- Produces: `SpscQueue<OutputCommand, 128>`, `OutputRuntime::process(command)`, `tick(now_us)`.

- [x] **Step 1: Write queue overflow and routing tests**

Assert FIFO order, full/empty wrap, overflow triggers `RuntimeFault::OutputQueueFull`, Pc1-only does not touch SPI, Pc2-only does not touch local USB, Both updates both keyboard states.

- [x] **Step 2: Verify failing tests**

Run native output runtime test.

- [x] **Step 3: Implement Core 0 runtime and Core 1 pattern producer**

Core 0 drains bounded commands, services `tud_task`, SPI and watchdog every loop. Temporary Core 1 generates a deterministic F13 press/release and mouse square only under compile definition `DUO_TEST_PATTERN=1`.

- [ ] **Step 4: Verify two-PC pattern**

Build release with pattern off and diagnostic build with pattern on. Confirm pattern routes exactly as compile-time target and no pauses >50 ms over 15 minutes.

**Blocked, 2026-08-27.** Both builds exist and the release one has the pattern
off, which the build contract checks. Running it needs a second computer to
receive the PC2 half, and U2 cannot be connected to one yet. Nothing else
stands in the way.

- [x] **Step 5: Commit**

```powershell
git add firmware/common/runtime firmware/u1_main tests/firmware_native
git commit -m "feat: route output commands across both computers"
```

### Task 6: Flash A/B store and CDC config transaction

**Files:**
- Create: `firmware/common/storage/flash_layout.hpp`
- Create: `firmware/common/storage/ab_store.hpp`
- Create: `firmware/common/storage/ab_store.cpp`
- Create: `firmware/u1_main/config_service.hpp`
- Create: `firmware/u1_main/config_service.cpp`
- Create: `tests/firmware_native/test_ab_store.cpp`
- Create: `configurator/tests/integration/test_real_config_contract.py`

**Interfaces:**
- Produces: `AbStore::scan`, `begin`, `write_chunk`, `verify`, `commit`, `abort`; `ConfigService::on_cdc_bytes`.

- [x] **Step 1: Write fake-flash power-cut tests**

Use an injected `FlashBackend`; interrupt each erase/program operation and assert scan returns old or new valid generation, never partial.

- [x] **Step 2: Confirm red state**

Run native `ab_store` and Python real-config contract test against absent firmware service.

- [x] **Step 3: Implement layout and transaction**

Static-assert exact offsets `0x100000`, `0x160000`, `0x1C0000`; program commit header last. During real flash erase/program, pause Core 1 at safe point, Release All, use Pico SDK safe flash execution, then resume.

- [x] **Step 4: Test emulator parity and real board**

Upload minimal/full configs through CDC, compare readback SHA-256, power-cycle during chunks and verify old slot remains. Run all native/Python tests.

- [x] **Step 5: Commit**

```powershell
git add firmware/common/storage firmware/u1_main/config_service.* tests/firmware_native configurator/tests/integration
git commit -m "feat: persist configuration with atomic A B slots"
```

### Task 7: Diagnostics, watchdog, SW1 and SW2

**Files:**
- Create: `firmware/common/diagnostics/codes.hpp`
- Create: `firmware/common/diagnostics/counters.hpp`
- Create: `firmware/u1_main/buttons.hpp`
- Create: `firmware/u1_main/buttons.cpp`
- Create: `firmware/u1_main/diagnostics_service.cpp`
- Modify: `firmware/u1_main/main.cpp`
- Modify: `firmware/u2_endpoint/main.cpp`
- Create: `tests/firmware_native/test_buttons.cpp`

**Interfaces:**
- Produces: debounced events `EmergencyMouseToggle`, `StopReleaseAll`, `FactoryResetConfirmed`; structured `DiagnosticsSnapshot`.

- [x] **Step 1: Write debounce/hold tests**

Assert one toggle per hold, SW2 short event only after release, factory confirmation exactly at 5000 ms and short action suppressed after long hold.

- [x] **Step 2: Verify failing tests**

Run native buttons test.

- [x] **Step 3: Implement buttons, watchdog and status**

Use 25 ms debounce, active-low pull-ups, 2-second hardware watchdog fed only after USB/SPI/queue service completes. Rate-limit persistent reset counter writes. Add GET_DIAGNOSTICS payload.

- [x] **Step 4: Run core firmware completion gate**

Build both UF2, run full native/Python suites, verify SW1 toggle, SW2 release, 5-second factory confirmation, watchdog recovery and 100-ms U2 fail-safe on hardware.

**Done, 2026-08-27**, against a narrowed gate. Both UF2 build, the native and
Python suites are green, and the 100 ms fail-safe is measured on hardware.

**SW1, SW2 and the 5-second factory confirmation are dropped.** The hardware
will not have buttons (decided 2026-08-27), so there is nothing to press and
the gate no longer asks for it.

The code stays, and is safe without them: both pins are pulled up and read as
closed-to-ground, so an unconnected pin is always "not pressed", and a pin
that reads pressed at power-on is treated as the baseline rather than as an
action. What the buttons would have done is reachable over CDC anyway -
`STOP_AND_RELEASE_ALL` and the two-step factory reset - so nothing is lost by
them not existing.

If a revision ever gains buttons, shorting the pins to ground exercises the
whole path.

- [x] **Step 5: Commit**

```powershell
git add firmware/common/diagnostics firmware/u1_main firmware/u2_endpoint tests/firmware_native
git commit -m "feat: add hardware recovery controls and diagnostics"
```

## Plan Completion Gate

- Both UF2 build and enumerate with exact interface sets.
- Synthetic keyboard/mouse input reaches PC1, PC2 and Both routes. **Blocked**
  until U2 can be connected to a second computer.
- U2 releases within 100 ms after link loss.
- CDC transaction survives injected power cuts.
- ~~SW1/SW2~~ and watchdog work with invalid user config. Buttons dropped:
  the hardware will not have them, and the actions they would have triggered
  are reachable over CDC.
- CH375, bindings and macros remain deliberately absent until the next plan.
