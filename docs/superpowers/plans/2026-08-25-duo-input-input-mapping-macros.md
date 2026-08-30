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

- [x] **Step 1: Write deadline/recovery tests**

Script connect, successful host mode, disconnect, malformed response and one-second retry. Assert no transition method spins and each `tick` performs bounded work.

- [ ] **Step 2: Verify failures**

Run `test_ch375_device`.

> **EVIDENCE MISSING - not ticked.** The tests this step asks to see fail exist
> and pass, but nothing records them ever failing: the test file and the
> implementation it tests landed in the same commit (`8af5a09`, which added both `firmware/u1_main/ch375/device.cpp` and `tests/firmware_native/test_ch375_device.cpp`), and no ledger
> entry, task report or review names a red run. The step may well have been
> performed; from what survives it cannot be told, so it stays unticked rather
> than being ticked on plausibility.

- [x] **Step 3: Implement two instances**

Configure baud from the verified module protocol, **nine data bits** (the ninth is the command flag - DS1 6.2.2, so PIO rather than the hardware UART), GP2/GP6 active-low interrupts. ISR sets one atomic flag per device. Device failure schedules retry at `now+1000ms`.

The OE step from the original plan is dropped: on this board OE is tied to VCCA and no firmware can hold it low. See `docs/hardware/ch375-wiring.md`.

- [x] **Step 4: Hardware diagnostic build**

Add compile-time diagnostic that reports state codes through U1 CDC without HID actions. Connect keyboard then mouse separately and verify independent state/recovery.

- [x] **Step 5: Commit**

```powershell
git add firmware/u1_main/ch375 tests/firmware_native
git commit -m "feat: run two independent CH375 host state machines"
```

> **DONE (2026-08-27 to 2026-08-29), ticked here for the first time.** The
> ledger's own pre-flight said it: "Task 2's boxes are unticked but its transport
> and state machine exist and were the subject of a day of hardware debugging".
>
> Step 1: `tests/firmware_native/test_ch375_device.cpp` (`8af5a09`) scripts
> connect, host mode, disconnect, malformed response and the 1 s retry, and pins
> the 250 ms quiet-teardown deadline the tests forced out.
>
> Step 3: two instances exist - `g_keyboard_device` and `g_mouse_device` in
> `firmware/u1_main/main.cpp`, each with its own commands and descriptor setup.
> The nine-bit frame is in PIO as the step requires, not in a hardware UART:
> `firmware/u1_main/ch375/ch375_serial.pio`, "one start bit, nine data bits and
> one stop bit". Two deviations from the step's wording, both already recorded in
> the plan text or the ledger: the OE step was dropped because OE is tied to VCCA
> on this board, and the baud is **found by a block-read ladder rather than
> configured**, because `CHECK_EXIST` answered correctly at 115200 and 62500 while
> every multi-byte read at those rates failed - zero successes against
> twenty-four failures each - and only 37500 carried a descriptor.
>
> Step 4: the diagnostic build is `DUO_CH375_PROBE`
> (`firmware/u1_main/CMakeLists.txt`), reporting state over CDC without HID
> actions. Independent state and recovery were observed on hardware: ledger
> 2026-08-29 13:33, "BOTH DEVICES WORK, SIMULTANEOUSLY AND CONTINUOUSLY", and
> `docs/hardware/ch375-compatibility.md` records both channels reaching Ready and
> staying there, poll and interrupt counters climbing in step past twenty-two
> thousand each, one detach apiece across the session and no presence losses.
>
> Step 5: `8af5a09`, `5784654`, `ad6aa5c`, `07556ac` and the link-hardening
> commits - not the plan's literal commit message, since this branch writes prose
> subjects throughout.

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

> **PARTIAL - the recording half is done, the reconnect half was never run.**
>
> Recorded, and citable: `docs/release/compatibility-matrix.md` carries VID
> `0x1BCF` PID `0x0005` with report-descriptor SHA-256
> `f93525fdfa9ca2d7c1639bf5bf2b1b06452a52f3fdc858f5ba198fb5d74ad7c8` and five
> buttons declared by the device, and VID `0x258A` PID `0x010C` with no hash
> because that device gave up no report descriptor.
> `docs/hardware/ch375-compatibility.md` records the selected endpoint -
> interrupt IN endpoint 1 on both devices, read from the endpoint descriptor
> rather than inferred from the interface number - and `wMaxPacketSize` 8 and 7.
> Evidence file: `tests/hil/artifacts/peripherals.json`, from the 2026-08-30
> 12:49 run, which the matrix cites.
>
> **Not run: "reconnect 20 times each without reset".** No count of reconnections
> exists in any ledger, report or artifact. What is recorded is one detach per
> device across a long continuous session with no presence losses - a different
> and weaker claim than twenty deliberate reconnects.
>
> Devices are recorded by class, identifier and behaviour rather than by make and
> model, on the operator's instruction: every user's peripherals differ, and a
> document naming one bench's hardware invites a reader to read the absence of
> their own device as incompatibility.

- [x] **Step 5: Commit**

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

> **EVIDENCE MISSING - not ticked.** The tests this step asks to see fail exist
> and pass, but nothing records them ever failing: the test file and the
> implementation it tests landed in the same commit (`102571e` "Turn HID reports into events without inventing any", which added `firmware/u1_main/input/` and `tests/firmware_native/test_normalizers.cpp` together), and no ledger
> entry, task report or review names a red run. The step may well have been
> performed; from what survives it cannot be told, so it stays unticked rather
> than being ticked on plausibility.

- [x] **Step 3: Implement previous-state diff**

Use descriptor-generated field maps and fixed arrays. Mouse motion emits one delta per accepted report; buttons emit edges. On disconnect emit release events for all remembered keys/buttons, then clear state.

- [x] **Step 4: Replay captured reports**

Needs a trace off the hardware, which needs a device that stays attached long
enough to record one. The reports themselves are already arriving - 218 in a
run - so this is waiting on the link settling rather than on anything here.

Capture at least one keyboard and one 5-button mouse report trace from hardware diagnostic, add anonymized binary vectors, and verify deterministic normalized event stream.

- [x] **Step 5: Commit**

```powershell
git add firmware/u1_main/input tests/firmware_native tests/vectors
git commit -m "feat: normalize CH375 HID reports"
```

> **Step 4 DONE (2026-08-29).** The traces were recorded off the real devices and
> committed as `tests/vectors/hid_reports/` in `37b5afc` - 26 distinct keyboard
> reports and 59 distinct mouse reports, keyed on `found=<kind>` from each
> device's own descriptor rather than on the channel name, because this bench's
> two channel names are crossed and keying on them would mislabel every trace.
> `f565504` added `tests/firmware_native/test_trace_replay.cpp` (suite
> `trace_replay`), which replays all 85 reports and asserts a golden stream of 54
> and 62 events - index, kind, code, signed deltas. Four production mutations
> were run to prove the suite can fail (73, 28, 60 and 72 failures), each
> reverted and the suite green again.
>
> Stated limit, carried from the ledger: the diagnostics carry a packet's size
> and its **first four bytes only**, so the corpus asserts nothing past byte 3.
> That is a whole boot mouse report, and a boot keyboard report's modifier,
> reserved byte and first two usages - the fields the normalizers read.
>
> **Step 5:** `102571e`, `51a7bf8` ("Release what a device was holding when its
> cable is pulled"), `37b5afc`, `f565504`.

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

> **EVIDENCE MISSING - not ticked.** The tests this step asks to see fail exist
> and pass, but nothing records them ever failing: the test file and the
> implementation it tests landed in the same commit (`2ab40e7` "Apply bindings, and move the routes without stranding a key", which added `firmware/u1_main/mapping/` and `tests/firmware_native/test_binding_engine.cpp` together), and no ledger
> entry, task report or review names a red run. The step may well have been
> performed; from what survives it cannot be told, so it stays unticked rather
> than being ticked on plausibility.

- [x] **Step 3: Implement indexed lookup and safe route transition**

Build an active-profile index in SRAM. Route change emits release commands for old target before setting new route; held physical inputs are marked `wait_for_release` and never transferred.

- [x] **Step 4: Run route stress simulation**

Generate 1000 toggles with random held keys/buttons and assert final snapshots released, no duplicate key downs, mouse never Both.

- [x] **Step 5: Commit**

```powershell
git add firmware/u1_main/mapping tests/firmware_native
git commit -m "feat: apply profile bindings and independent routes"
```

> **Step 5 DONE:** `2ab40e7`, and `36ff0e0` ("Ask which key to bind without
> pressing it on the far computer") for the capture path over the same tables.

### Task 6: Non-blocking macro scheduler

**Files:**
- Create: `firmware/u1_main/macros/steps.hpp`
- Create: `firmware/u1_main/macros/scheduler.hpp`
- Create: `firmware/u1_main/macros/scheduler.cpp`
- Create: `tests/firmware_native/test_macro_scheduler.cpp`

**Interfaces:**
- Produces: `MacroScheduler::enqueue(macro_id,target,now_ms)`, `tick(now_ms)`, `stop_all()`, `active()`, `queued_count()`.

- [x] **Step 1: Write timeline tests**

Test fixed/random delay boundaries with injected RNG, KeyDown/Up ownership, Text compiled usages, ConsumerTap, SetProfile, route steps, FIFO 4, fifth rejection, physical event progress during delay and runtime 6KRO abort.

- [x] **Step 2: Verify failures**

Run macro scheduler target.

- [x] **Step 3: Implement one active job plus four-slot FIFO**

Use absolute unsigned deadlines safe across wrap, injected `IRandom`, fixed step cursor and unique macro owner token. On stop/error release only current macro owner, clear FIFO, return structured reason.

- [x] **Step 4: Run deterministic 10,000-sequence simulation**

Seed RNG, interleave physical events, disconnects, stops and route changes; assert no residual macro ownership and scheduler never blocks.

- [x] **Step 5: Commit**

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

- [x] **Step 1: Write capture/profile tests**

Assert first eligible press is swallowed/reported, motion and unrelated events continue, 10-second timeout, captured event does not execute binding, profile swap releases outputs and held inputs wait for re-press.

- [ ] **Step 2: Verify red state**

Run core runtime tests.

> **EVIDENCE AMBIGUOUS - not ticked.** The ledger's pre-flight ruling says "Task 7
> Steps 1-2 and the natively testable half of Step 3 were implemented in the
> controller session before this skill was invoked (commits `36ff0e0`, `51a7bf8`,
> `80e84bb`)". That is a claim this step happened, but "implemented" is not a red
> run and it names no failing output; nothing else records one. Left unticked on
> the same rule applied to Tasks 4 and 5 above - a summary line is not a
> measurement.

- [x] **Step 3: Replace test producer with Core 1 runtime**

Tick both CH375 devices, normalizers, capture, binding and macro scheduler with bounded per-loop budgets; enqueue OutputCommand to existing SPSC queue. Implement config swap acknowledgement.

- [x] **Step 4: Full hardware acceptance for this phase**

Verify keyboard/mouse input on PC1/PC2/Both, F12 and Button4 switching, eight profiles across power cycle, `/target KYPKYMA` test macro without movement stalls, disconnect release and reconnect recovery.

- [x] **Step 5: Commit**

```powershell
git add firmware/u1_main tests/firmware_native
git commit -m "feat: integrate physical input mapping and macro runtime"
```

> **Steps 1, 3 and 5 DONE; Step 4 accepted on hardware 2026-08-29.**
>
> Step 1: `tests/firmware_native/test_core1_runtime.cpp` and
> `test_stored_profiles.cpp` (`36ff0e0`, `80e84bb`), plus
> `tests/firmware_native/test_capture.cpp` (`e331555`, "Ask capture whether it
> swallowed the release").
>
> Step 3: `16afc0b`, "Give Core 1 the real input runtime instead of a test
> pattern" - Core 1 ticks both CH375 controllers, both pipelines, capture,
> bindings and macros, and submits into the queue Core 0 already drains. The
> review ruling behind it is in the ledger as C1: "the input path ships in the
> release build, and only the plain-text bring-up reporting stays behind
> `DUO_CH375_PROBE`."
>
> Step 4: all four criteria passed on real hardware, with a real keyboard and a
> real mouse across two computers.
>
> | Criterion | Result |
> |---|---|
> | Routing to PC1 / PC2 / Both, by key and by mouse button (F9/F10/F11, F12, mouse button 4) | PASSED 16:1x |
> | The text macro, complete on both computers, no movement stall | PASSED 18:0x (S4b) |
> | A key held while its keyboard is unplugged released rather than stranded; modifier not stranded across a reconnect | PASSED 18:1x (S4c) |
> | Eight distinct profiles surviving a power cycle, booting into the configured active profile | PASSED 18:2x (S4a) |
>
> S4a's discriminating detail is worth keeping: the device came back up **in
> profile 3**, the profile the configuration names as active. The firmware falls
> back to profile 0 when a load fails, so booting into 3 is positive evidence
> that the flash read succeeded rather than merely that something loaded.
>
> One deviation from the step's wording, noted rather than rewritten: it names
> the test macro `/target KYPKYMA`; what ran was the text macro built for the
> acceptance by `tools/step4_acceptance_config.py`. The criterion met is the one
> the step states - a text macro typing completely on both computers without
> movement stalls or stranded keys.
>
> **Step 5:** `80e84bb`, `16afc0b`, `0aa78aa`, `25d3730`, `18e379e`, and the
> repairs that followed them.

## Plan Completion Gate

- Two CH375 devices enumerate independently and recover after reconnect.
- Captured standard keyboard/mouse vectors normalize deterministically.
- 1000 simulated and 1000 physical route toggles leave no stuck state.
- Macro FIFO, cancellation, layout-compiled text and 6KRO errors are safe.
- Profile/capture behavior matches CDC contract.
- Compatibility document lists verified devices and exact unsupported reasons.

> **GATE STATUS, reassessed 2026-08-30.** The whole-branch review of 2026-08-29
> assessed these bullets and found three unmet; all three were discharged after
> it. Where a bullet is met with a limit, the limit is stated rather than rounded
> off.
>
> 1. **Two CH375 devices enumerate independently and recover after reconnect —
>    MET.** Ledger 2026-08-29 13:33, both devices working simultaneously and
>    continuously; both channels reached Ready and stayed there, one detach apiece
>    and no presence losses. Reconnect recovery is the S4c acceptance.
> 2. **Captured standard keyboard/mouse vectors normalize deterministically —
>    MET (2026-08-29).** `tests/vectors/hid_reports/` (`37b5afc`) and the
>    `trace_replay` suite (`f565504`). Stated limit: the corpus asserts nothing
>    past byte 3, because that is all the diagnostics carry.
> 3. **1000 simulated and 1000 physical route toggles leave no stuck state — the
>    simulated half MET, the physical half MET AT 320, NOT 1000.** Task 5 Step 4's
>    stress simulation runs the full 1000. On hardware the operator ran ten
>    device-driven runs of 32 route changes each - 320 - with Left Shift held
>    throughout: `dropped_commands` 0 to 0, `runtime_fault` 0 to 0, `bad_crc` 0,
>    `timeout` 0, and 154 030 SPI frames sent with no CRC error, no stranded
>    modifier on either computer. 32 changes per run is a ceiling and not a
>    choice - 64 steps is the hard macro limit, macros do not chain, and a held
>    key does not re-trigger a binding - so 1000 would have meant 32 presses, and
>    32 lines is where the operator stops being able to count what they saw. The
>    shortfall is real and is recorded as a shortfall.
> 4. **Macro FIFO, cancellation, layout-compiled text and 6KRO errors are safe —
>    MET.** Task 6's deterministic 10 000-sequence simulation, plus S4b on
>    hardware: the text macro typed completely on both computers, which closed the
>    prior review's Critical C2.
> 5. **Profile/capture behavior matches CDC contract — MET.** The two deviations
>    the 2026-08-29 review raised (I1 hollow ordering test, I3 stored routes with
>    no caller) were closed by `ec7e4d1`, `e331555` and `d34286d`; the configurator
>    exercised capture and profiles against the real U1 at 9/9 on 2026-08-30.
> 6. **Compatibility document lists verified devices and exact unsupported
>    reasons — MET, by identifier rather than by model.** `d7e4cfa` and
>    `04deb89`: eight descriptor vectors with the exact `ParseError` each returns,
>    and the two bench devices by VID, PID and report-descriptor hash. The
>    operator's ruling stands - no model names, because every user's peripherals
>    differ and a named list dates immediately and teaches the wrong thing.
