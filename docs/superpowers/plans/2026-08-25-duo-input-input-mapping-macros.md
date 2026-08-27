# Duo Input CH375, Mapping, and Macros Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Подключить две реальные CH375B-периферии к U1, нормализовать HID, реализовать профили, назначения, независимые маршруты и автономные макросы.

**Architecture:** Core 1 U1 обслуживает два независимых неблокирующих CH375 state machines через UART0/UART1 и INT flags. Нормализованные события проходят BindingEngine и MacroScheduler, затем фиксированные OutputCommand попадают Core 0. Все аппаратно-независимые компоненты сначала тестируются native.

**Tech Stack:** Pico SDK UART/GPIO, C++17 fixed-capacity containers, scripted CH375 transport, native CTest, WCH CH375 host command set.

**Spec:** `docs/superpowers/specs/2026-08-25-duo-input-firmware-configurator-design.md`

## Global Constraints

- Сначала завершить foundation и core firmware plans.
- GP0 TX/GP1 RX/GP2 INT for the keyboard; GP4 TX/GP5 RX/GP6 INT for the mouse.
  See `docs/hardware/ch375-wiring.md` for the board as actually built.
- **Not the hardware UARTs.** CH375's serial format is nine data bits, the
  ninth marking command versus data (DS1 6.2.2). An RP2040 UART does five to
  eight. These ports are PIO, which takes the same pins.
- **TXS OE is tied to VCCA on this board, not to GP7.** The shifter is enabled
  whenever it has power and firmware cannot hold it off; GP7 is free. Both
  ends of every line idle high through pull-ups, so there is nothing for the
  OE step to have prevented.
- ISR только выставляет flag; CH375 commands/read/parse выполняются в Core 1 loop.
- Гарантия MVP: одна стандартная проводная keyboard и одна standard mouse; hubs/vendor-specific не гарантируются.
- KeyboardRoute = PC1/PC2/BOTH; MouseRoute = PC1/PC2 only.
- Один активный macro + FIFO 4; physical input никогда не блокируется задержкой.
- Текстовые исходники US/RU/UA компилируются приложением; firmware исполняет только HID steps.
- Любая ошибка/disconnect освобождает затронутые HID states на обоих ПК.

## Locked File Structure

```text
firmware/u1_main/ch375/{commands,transport,device,enumerator,hid_parser}.*
firmware/u1_main/input/{events,keyboard_normalizer,mouse_normalizer}.*
firmware/u1_main/mapping/{binding,engine,routes,profile_runtime}.*
firmware/u1_main/macros/{steps,scheduler}.*
firmware/u1_main/core1_runtime.*
tests/firmware_native/fakes/scripted_ch375.*
tests/firmware_native/test_ch375_transport.cpp
tests/firmware_native/test_ch375_device.cpp
tests/firmware_native/test_hid_parser.cpp
tests/firmware_native/test_{normalizers,binding_engine,routes,macro_scheduler}.cpp
docs/hardware/ch375-compatibility.md
```

---

### Task 1: CH375 command table and scripted transport

**Files:**
- Create: `docs/hardware/ch375-command-table.md`
- Create: `firmware/u1_main/ch375/commands.hpp`
- Create: `firmware/u1_main/ch375/transport.hpp`
- Create: `tests/firmware_native/fakes/scripted_ch375.hpp`
- Create: `tests/firmware_native/fakes/scripted_ch375.cpp`
- Create: `tests/firmware_native/test_ch375_transport.cpp`

**Interfaces:**
- Produces: `ICh375Transport::write_command`, `write_data`, `read_data`, `int_asserted`, `now_us`.
- Produces: enum command/status values transcribed once from WCH CH375 host documentation and cross-referenced by table page/section.

- [x] **Step 1: Write scripted interaction test**

```cpp
TEST_CASE(check_exist_requires_inverted_reply) {
  ScriptedCh375 io({expect_cmd(Cmd::CheckExist), expect_data(0xA5), reply(0x5A)});
  Ch375Transport t(io);
  CHECK(t.check_exist(0xA5));
  CHECK(io.complete());
}
```

Add timeout, wrong inverse, RX overflow and unexpected byte cases.

- [x] **Step 2: Verify red state**

Run native CH375 transport tests; expected missing interfaces.

- [x] **Step 3: Transcribe and implement exact command contract**

Document and encode at least `CHECK_EXIST`, `SET_USB_MODE`, `GET_STATUS`, `RD_USB_DATA0`, `SET_USB_ADDR`, `SET_USB_SPEED`, endpoint toggle/control-transfer commands and token issue commands used by enumeration/polling. Each constant must cite WCH document section in `ch375-command-table.md`; do not copy constants from random Arduino libraries.

- [x] **Step 4: Run scripted suite**

Expected: every script fully consumed; no production transport waits forever; all waits accept absolute deadline.

- [x] **Step 5: Commit**

```powershell
git add docs/hardware firmware/u1_main/ch375 tests/firmware_native
git commit -m "feat: define bounded CH375 host transport"
```

### Task 2: Dual UART hardware transport and device state machine

**Files:**
- Create: `firmware/u1_main/ch375/uart_transport.hpp`
- Create: `firmware/u1_main/ch375/uart_transport.cpp`
- Create: `firmware/u1_main/ch375/device.hpp`
- Create: `firmware/u1_main/ch375/device.cpp`
- Create: `tests/firmware_native/test_ch375_device.cpp`

**Interfaces:**
- Produces: `Ch375Device::tick(now_us)`, `state()`, `take_event()`, `request_reenumeration()`.
- States: `Absent`, `Resetting`, `HostMode`, `Enumerating`, `Ready`, `RecoverWait`, `Fault`.

- [ ] **Step 1: Write deadline/recovery tests**

Script connect, successful host mode, disconnect, malformed response and one-second retry. Assert no transition method spins and each `tick` performs bounded work.

- [ ] **Step 2: Verify failures**

Run `test_ch375_device`.

- [ ] **Step 3: Implement two instances**

Configure baud from the verified module protocol, **nine data bits** (the ninth is the command flag - DS1 6.2.2, so PIO rather than the hardware UART), GP2/GP6 active-low interrupts. ISR sets one atomic flag per device. Device failure schedules retry at `now+1000ms`.

The OE step from the original plan is dropped: on this board OE is tied to VCCA and no firmware can hold it low. See `docs/hardware/ch375-wiring.md`.

- [ ] **Step 4: Hardware diagnostic build**

Add compile-time diagnostic that reports state codes through U1 CDC without HID actions. Connect keyboard then mouse separately and verify independent state/recovery.

- [ ] **Step 5: Commit**

```powershell
git add firmware/u1_main/ch375 tests/firmware_native
git commit -m "feat: run two independent CH375 host state machines"
```

### Task 3: Enumeration and bounded HID descriptor parser

**Why this is now blocking, 2026-08-28.** Task 2's lifecycle reaches Ready on
hardware - AUTO_SETUP answers 0x14 - and then every poll of the device's
endpoint produces nothing. Not a timeout, not a NAK: no interrupt at all,
across 120 consecutive tokens, where DS2 1.15 says a token always ends in one.

Two things AUTO_SETUP does not tell the caller, and both are needed to talk to
the device afterwards:

- **The address it assigned.** It performs the device's SET_ADDRESS, and DS2
  1.5 requires the host to be given the same address separately, or it goes on
  addressing one nobody answers to. AUTO_SETUP does not report which address
  it used, so this cannot be done without guessing.
- **Which endpoint the reports arrive on.** Endpoint 1 is assumed today,
  because nearly every wired keyboard and mouse uses it. "Nearly every" is not
  a thing to build on.

Doing the enumeration by hand - GET_DESCR, SET_ADDRESS, SET_USB_ADDR,
GET_DESCR again, SET_CONFIG - answers both, which is why this task exists.


**Files:**
- Create: `firmware/u1_main/ch375/enumerator.hpp`
- Create: `firmware/u1_main/ch375/enumerator.cpp`
- Create: `firmware/u1_main/ch375/hid_parser.hpp`
- Create: `firmware/u1_main/ch375/hid_parser.cpp`
- Create: `tests/vectors/hid_descriptors/boot_keyboard.bin`
- Create: `tests/vectors/hid_descriptors/boot_mouse.bin`
- Create: `tests/vectors/hid_descriptors/mouse_5_button.bin`
- Create: `tests/vectors/hid_descriptors/consumer_composite.bin`
- Create: `tests/vectors/hid_descriptors/truncated_item.bin`
- Create: `tests/vectors/hid_descriptors/impossible_report_size.bin`
- Create: `tests/vectors/hid_descriptors/vendor_only.bin`
- Create: `tests/vectors/hid_descriptors/hub.bin`
- Create: `tests/firmware_native/test_hid_parser.cpp`
- Create: `docs/hardware/ch375-compatibility.md`

**Interfaces:**
- Produces: `HidDeviceCapabilities {vid,pid,descriptor_hash,protocol,endpoint,max_packet,button_count}`.
- Produces: `ParseResult parse_hid_descriptor(ByteView, ExpectedDeviceKind)` using the common C++17 pointer+length view.

**Reached on hardware, 2026-08-28.** A real mouse is enumerated by hand and its
reports arrive: `found=mouse endpoint=1 packet=7 boot=yes parse=0`, and two
reports read from the endpoint when it was moved. Every part of that path is
this firmware's - the nine-bit PIO port, the command layer, the lifecycle, the
enumeration and the descriptor parser - and the endpoint it polls came out of
the device's own descriptor rather than an assumption.

What is not yet right is that it does not stay: the device is lost and brought
up again repeatedly, and thirty of thirty-three enumeration attempts fail. So
this is the path working, not the path working reliably.

- [x] **Step 1: Add descriptor corpus and failing tests**

Include standard boot keyboard, boot mouse, 5-button wheel mouse, consumer composite, truncated item, impossible report size, vendor-only device and hub. Assert accepted capabilities or exact rejection code.

- [x] **Step 2: Verify parser tests fail**

Run native HID parser target.

- [x] **Step 3: Implement enumeration and parser limits**

Read device/config/HID/report descriptors through CH375 control transfers; cap total descriptor bytes at 4096, report fields at 64, report bits at 512. Prefer Boot interface; otherwise accept only keyboard/mouse usages representable by normalizers.

- [ ] **Step 4: Test vectors and two real devices**

Record VID/PID, hash, selected endpoint and button count in compatibility doc. Reconnect 20 times each without reset.

- [ ] **Step 5: Commit**

```powershell
git add firmware/u1_main/ch375 tests/vectors/hid_descriptors tests/firmware_native docs/hardware/ch375-compatibility.md
git commit -m "feat: enumerate supported keyboard and mouse HID devices"
```

### Task 4: Keyboard and mouse normalizers

**Files:**
- Create: `firmware/u1_main/input/events.hpp`
- Create: `firmware/u1_main/input/keyboard_normalizer.hpp`
- Create: `firmware/u1_main/input/keyboard_normalizer.cpp`
- Create: `firmware/u1_main/input/mouse_normalizer.hpp`
- Create: `firmware/u1_main/input/mouse_normalizer.cpp`
- Create: `tests/firmware_native/test_normalizers.cpp`

**Interfaces:**
- Produces fixed `InputEvent` variants KeyDown/Up, ConsumerDown/Up, MouseButtonDown/Up, MouseMove, Wheel, DeviceConnected/Disconnected.

- [x] **Step 1: Write report-diff tests**

Test modifier transitions, six-key reorder without false edges, rollover report, button 4/5, signed X/Y, wheel/pan, repeated identical report and disconnect release synthesis.

- [ ] **Step 2: Verify failures**

Run native normalizer tests.

- [x] **Step 3: Implement previous-state diff**

Use descriptor-generated field maps and fixed arrays. Mouse motion emits one delta per accepted report; buttons emit edges. On disconnect emit release events for all remembered keys/buttons, then clear state.

- [ ] **Step 4: Replay captured reports**

Needs a trace off the hardware, which needs a device that stays attached long
enough to record one. The reports themselves are already arriving - 218 in a
run - so this is waiting on the link settling rather than on anything here.

Capture at least one keyboard and one 5-button mouse report trace from hardware diagnostic, add anonymized binary vectors, and verify deterministic normalized event stream.

- [ ] **Step 5: Commit**

```powershell
git add firmware/u1_main/input tests/firmware_native tests/vectors
git commit -m "feat: normalize CH375 HID reports"
```

### Task 5: Profile runtime, bindings and independent routes

**Files:**
- Create: `firmware/u1_main/mapping/binding.hpp`
- Create: `firmware/u1_main/mapping/profile_runtime.hpp`
- Create: `firmware/u1_main/mapping/profile_runtime.cpp`
- Create: `firmware/u1_main/mapping/routes.hpp`
- Create: `firmware/u1_main/mapping/routes.cpp`
- Create: `firmware/u1_main/mapping/engine.hpp`
- Create: `firmware/u1_main/mapping/engine.cpp`
- Create: `tests/firmware_native/test_binding_engine.cpp`

**Interfaces:**
- Produces: `BindingEngine::handle(InputEvent) -> BoundedVector<ActionRequest,4>`.
- Produces route actions and `Replace`/`Add` semantics from active `ConfigView`.

- [x] **Step 1: Write binding contract tests**

Test unbound pass-through, Replace suppress trigger key only, Add passes immediately plus action, modifiers as conditions, one trigger per hold, conflict rejected by validator, keyboard Both and mouse Pc1/Pc2.

- [ ] **Step 2: Verify failures**

Run binding tests.

- [x] **Step 3: Implement indexed lookup and safe route transition**

Build an active-profile index in SRAM. Route change emits release commands for old target before setting new route; held physical inputs are marked `wait_for_release` and never transferred.

- [x] **Step 4: Run route stress simulation**

Generate 1000 toggles with random held keys/buttons and assert final snapshots released, no duplicate key downs, mouse never Both.

- [ ] **Step 5: Commit**

```powershell
git add firmware/u1_main/mapping tests/firmware_native
git commit -m "feat: apply profile bindings and independent routes"
```

### Task 6: Non-blocking macro scheduler

**Files:**
- Create: `firmware/u1_main/macros/steps.hpp`
- Create: `firmware/u1_main/macros/scheduler.hpp`
- Create: `firmware/u1_main/macros/scheduler.cpp`
- Create: `tests/firmware_native/test_macro_scheduler.cpp`

**Interfaces:**
- Produces: `MacroScheduler::enqueue(macro_id,target,now_ms)`, `tick(now_ms)`, `stop_all()`, `active()`, `queued_count()`.

- [ ] **Step 1: Write timeline tests**

Test fixed/random delay boundaries with injected RNG, KeyDown/Up ownership, Text compiled usages, ConsumerTap, SetProfile, route steps, FIFO 4, fifth rejection, physical event progress during delay and runtime 6KRO abort.

- [ ] **Step 2: Verify failures**

Run macro scheduler target.

- [ ] **Step 3: Implement one active job plus four-slot FIFO**

Use absolute unsigned deadlines safe across wrap, injected `IRandom`, fixed step cursor and unique macro owner token. On stop/error release only current macro owner, clear FIFO, return structured reason.

- [ ] **Step 4: Run deterministic 10,000-sequence simulation**

Seed RNG, interleave physical events, disconnects, stops and route changes; assert no residual macro ownership and scheduler never blocks.

- [ ] **Step 5: Commit**

```powershell
git add firmware/u1_main/macros tests/firmware_native
git commit -m "feat: execute bounded non blocking macros"
```

### Task 7: Capture mode, active profiles and Core 1 integration

**Files:**
- Create: `firmware/u1_main/mapping/capture.hpp`
- Create: `firmware/u1_main/mapping/capture.cpp`
- Create: `firmware/u1_main/core1_runtime.hpp`
- Create: `firmware/u1_main/core1_runtime.cpp`
- Modify: `firmware/u1_main/config_service.cpp`
- Modify: `firmware/u1_main/main.cpp`
- Create: `tests/firmware_native/test_core1_runtime.cpp`

**Interfaces:**
- Produces: `CaptureController::begin(kind,deadline)`, `handle(event)`, `cancel`; CDC CAPTURE_EVENT.
- Produces: safe active-profile swap handshake Core0↔Core1.

- [ ] **Step 1: Write capture/profile tests**

Assert first eligible press is swallowed/reported, motion and unrelated events continue, 10-second timeout, captured event does not execute binding, profile swap releases outputs and held inputs wait for re-press.

- [ ] **Step 2: Verify red state**

Run core runtime tests.

- [ ] **Step 3: Replace test producer with Core 1 runtime**

Tick both CH375 devices, normalizers, capture, binding and macro scheduler with bounded per-loop budgets; enqueue OutputCommand to existing SPSC queue. Implement config swap acknowledgement.

- [ ] **Step 4: Full hardware acceptance for this phase**

Verify keyboard/mouse input on PC1/PC2/Both, F12 and Button4 switching, eight profiles across power cycle, `/target KYPKYMA` test macro without movement stalls, disconnect release and reconnect recovery.

- [ ] **Step 5: Commit**

```powershell
git add firmware/u1_main tests/firmware_native
git commit -m "feat: integrate physical input mapping and macro runtime"
```

## Plan Completion Gate

- Two CH375 devices enumerate independently and recover after reconnect.
- Captured standard keyboard/mouse vectors normalize deterministically.
- 1000 simulated and 1000 physical route toggles leave no stuck state.
- Macro FIFO, cancellation, layout-compiled text and 6KRO errors are safe.
- Profile/capture behavior matches CDC contract.
- Compatibility document lists verified devices and exact unsupported reasons.
