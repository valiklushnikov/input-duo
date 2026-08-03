# Master Target Macro Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PS/2 Explorer side button `0x20` type and submit `/target KYPKYMA` on the master laptop without interrupting mouse polling or forwarding.

**Architecture:** Add a small, pure C++11 macro/debounce logic header that can be verified with compile-time tests, then connect it to Arduino `Keyboard` HID in the master sketch. A non-blocking scheduler emits one keyboard event when its deadline arrives; the receiver firmware and the inter-board mouse protocol remain unchanged.

**Tech Stack:** Arduino AVR core for ATmega32U4/Leonardo, Arduino Mouse/Keyboard/Wire libraries, C++11 `constexpr` compile tests, Python `unittest`, PowerShell AVR build and packaging scripts.

## Global Constraints

- Side button `0x10` must continue to switch the active mouse target exactly once per press.
- Side button `0x20` must always send the macro to the master laptop, independent of the active mouse target.
- The exact submitted sequence is `Enter`, `/target KYPKYMA`, `Enter`; the host must already use the English layout.
- Inter-character delays are pseudo-random values from 35 through 80 milliseconds, inclusive.
- The pauses after the opening `Enter` and after the last text character are pseudo-random values from 80 through 140 milliseconds, inclusive.
- Press and release debounce intervals are both 25 milliseconds; holding never repeats and a stable release is required to re-arm.
- A macro press received while a macro is active is ignored and is not queued.
- Macro timing must not use `delay()`; PS/2 polling, local HID mouse movement, and I2C forwarding continue while typing.
- A started macro completes across a mouse disconnect, but reconnect must not synthesize a new macro.
- Middle button remains ignored; receiver firmware and the seven-byte inter-board protocol remain unchanged.
- Deliverable archive name is `dual-laptop-mouse-switch-working-v5.zip`; only master requires reflashing.

---

### Task 1: Pure debounce and macro-event contract

**Files:**
- Create: `improved/mouse_switch_master/mouse_switch_macro_logic.h`
- Modify: `tests/protocol_compile_test/protocol_compile_test.ino:1-4,137-167,229-232`

**Interfaces:**
- Consumes: decoded Explorer side-button byte from `decodePs2SideButtons(uint8_t, uint8_t)`.
- Produces: `DebouncedButtonState`, `DebouncedButtonUpdate`, `makeInitialDebouncedButtonState()`, `updateDebouncedButton(...)`, `shouldStartTargetMacro(...)`, `TargetMacroEvent`, `targetMacroEventAt(...)`, timing constants, and exact `kTargetMacroText`.

- [ ] **Step 1: Add failing compile-time contract tests**

Add the new include after the existing PS/2 include:

```cpp
#include "../../improved/mouse_switch_master/mouse_switch_macro_logic.h"
```

Add exact payload, event, timing, and debounce tests:

```cpp
static_assert(constexprStringsEqual(kTargetMacroText, "/target KYPKYMA"),
              "Target macro payload must remain exact");
static_assert(kTargetMacroTextLength == 15,
              "Target macro payload length changed");
static_assert(targetMacroEventAt(0).type == TargetMacroEventType::Enter,
              "The macro must open chat with Enter");
static_assert(targetMacroEventAt(1).character == '/',
              "The first payload character must be slash");
static_assert(targetMacroEventAt(15).character == 'A',
              "The final payload character must remain A");
static_assert(targetMacroEventAt(16).type == TargetMacroEventType::Enter,
              "The macro must submit with Enter");
static_assert(targetMacroEventAt(17).type == TargetMacroEventType::None,
              "Out-of-range events must be empty");
static_assert(kCharacterDelayMinMs == 35 && kCharacterDelayMaxMs == 80,
              "Human typing delay bounds changed");
static_assert(kCommandPauseMinMs == 80 && kCommandPauseMaxMs == 140,
              "Command pause bounds changed");
static_assert(delayMinAfterEvent(0) == 80 && delayMaxAfterEvent(0) == 140,
              "Opening Enter must use the longer pause");
static_assert(delayMinAfterEvent(1) == 35 && delayMaxAfterEvent(1) == 80,
              "Text characters must use human typing delays");
static_assert(delayMinAfterEvent(15) == 80 && delayMaxAfterEvent(15) == 140,
              "Last character must use the pre-submit pause");

constexpr DebouncedButtonState kDebounceInitial =
    makeInitialDebouncedButtonState();
constexpr DebouncedButtonUpdate kInitialReleased =
    updateDebouncedButton(kDebounceInitial, false, 100, 25);
static_assert(!kInitialReleased.pressedEdge && kInitialReleased.state.armed,
              "An initial released sample must arm without firing");
constexpr DebouncedButtonUpdate kPressChanged =
    updateDebouncedButton(kInitialReleased.state, true, 110, 25);
constexpr DebouncedButtonUpdate kPressTooEarly =
    updateDebouncedButton(kPressChanged.state, true, 134, 25);
constexpr DebouncedButtonUpdate kPressStable =
    updateDebouncedButton(kPressTooEarly.state, true, 135, 25);
static_assert(!kPressChanged.pressedEdge && !kPressTooEarly.pressedEdge,
              "A press must remain stable for the full debounce interval");
static_assert(kPressStable.pressedEdge && !kPressStable.state.armed,
              "A stable press must fire once and disarm");
constexpr DebouncedButtonUpdate kHeldPress =
    updateDebouncedButton(kPressStable.state, true, 500, 25);
static_assert(!kHeldPress.pressedEdge,
              "A held button must not repeat");
constexpr DebouncedButtonUpdate kReleaseChanged =
    updateDebouncedButton(kHeldPress.state, false, 510, 25);
constexpr DebouncedButtonUpdate kReleaseStable =
    updateDebouncedButton(kReleaseChanged.state, false, 535, 25);
static_assert(kReleaseStable.state.armed && !kReleaseStable.pressedEdge,
              "A stable release must re-arm without firing");
constexpr DebouncedButtonUpdate kSecondPressChanged =
    updateDebouncedButton(kReleaseStable.state, true, 540, 25);
constexpr DebouncedButtonUpdate kSecondPressStable =
    updateDebouncedButton(kSecondPressChanged.state, true, 565, 25);
static_assert(kSecondPressStable.pressedEdge,
              "A new stable press after release must fire");
constexpr DebouncedButtonUpdate kReconnectHeld =
    updateDebouncedButton(kDebounceInitial, true, 700, 25);
static_assert(!kReconnectHeld.pressedEdge && !kReconnectHeld.state.armed,
              "A held button at startup or reconnect must not synthesize a macro");
static_assert(shouldStartTargetMacro(true, false),
              "A debounced edge must start an idle macro");
static_assert(!shouldStartTargetMacro(true, true),
              "An active macro must ignore a new edge");
```

- [ ] **Step 2: Run the protocol build and verify RED**

Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build\full-avr-build.ps1
```

Expected: compilation fails at `protocol_compile_test.ino` because `mouse_switch_macro_logic.h` or its declared symbols do not exist.

- [ ] **Step 3: Implement the pure C++11 logic header**

Create `mouse_switch_macro_logic.h` with include guards and these public types/constants. Keep every `constexpr` function valid under GNU C++11 by using a single return expression.

```cpp
#ifndef MOUSE_SWITCH_MACRO_LOGIC_H
#define MOUSE_SWITCH_MACRO_LOGIC_H

#include <stddef.h>
#include <stdint.h>

constexpr char kTargetMacroText[] = "/target KYPKYMA";
constexpr uint8_t kTargetMacroTextLength =
    static_cast<uint8_t>(sizeof(kTargetMacroText) - 1);
constexpr uint16_t kCharacterDelayMinMs = 35;
constexpr uint16_t kCharacterDelayMaxMs = 80;
constexpr uint16_t kCommandPauseMinMs = 80;
constexpr uint16_t kCommandPauseMaxMs = 140;

constexpr bool constexprStringsEqual(const char* left, const char* right) {
  return *left != *right
             ? false
             : (*left == '\0' ? true
                               : constexprStringsEqual(left + 1, right + 1));
}

enum class TargetMacroEventType : uint8_t { None, Enter, Character };

struct TargetMacroEvent {
  TargetMacroEventType type;
  char character;
  constexpr TargetMacroEvent(TargetMacroEventType eventType, char value)
      : type(eventType), character(value) {}
};

constexpr uint8_t kTargetMacroEventCount = kTargetMacroTextLength + 2;

constexpr TargetMacroEvent targetMacroEventAt(uint8_t eventIndex) {
  return eventIndex == 0 || eventIndex == kTargetMacroEventCount - 1
             ? TargetMacroEvent(TargetMacroEventType::Enter, 0)
             : (eventIndex < kTargetMacroEventCount - 1
                    ? TargetMacroEvent(TargetMacroEventType::Character,
                                       kTargetMacroText[eventIndex - 1])
                    : TargetMacroEvent(TargetMacroEventType::None, 0));
}

constexpr uint16_t delayMinAfterEvent(uint8_t eventIndex) {
  return eventIndex == 0 || eventIndex == kTargetMacroTextLength
             ? kCommandPauseMinMs
             : kCharacterDelayMinMs;
}

constexpr uint16_t delayMaxAfterEvent(uint8_t eventIndex) {
  return eventIndex == 0 || eventIndex == kTargetMacroTextLength
             ? kCommandPauseMaxMs
             : kCharacterDelayMaxMs;
}

struct DebouncedButtonState {
  bool initialized;
  bool rawPressed;
  bool stablePressed;
  bool armed;
  uint32_t rawChangedAtMs;
  constexpr DebouncedButtonState(bool isInitialized = false,
                                 bool raw = false,
                                 bool stable = false,
                                 bool isArmed = false,
                                 uint32_t changedAt = 0)
      : initialized(isInitialized),
        rawPressed(raw),
        stablePressed(stable),
        armed(isArmed),
        rawChangedAtMs(changedAt) {}
};

struct DebouncedButtonUpdate {
  DebouncedButtonState state;
  bool pressedEdge;
  constexpr DebouncedButtonUpdate(DebouncedButtonState nextState,
                                  bool edge)
      : state(nextState), pressedEdge(edge) {}
};

constexpr DebouncedButtonState makeInitialDebouncedButtonState() {
  return DebouncedButtonState();
}

constexpr bool debounceIntervalElapsed(uint32_t nowMs,
                                       uint32_t changedAtMs,
                                       uint16_t debounceMs) {
  return static_cast<uint32_t>(nowMs - changedAtMs) >= debounceMs;
}

constexpr DebouncedButtonUpdate updateDebouncedButton(
    DebouncedButtonState state,
    bool rawPressed,
    uint32_t nowMs,
    uint16_t debounceMs) {
  return !state.initialized
             ? DebouncedButtonUpdate(
                   DebouncedButtonState(true, rawPressed, rawPressed,
                                        !rawPressed, nowMs),
                   false)
         : rawPressed != state.rawPressed
             ? DebouncedButtonUpdate(
                   DebouncedButtonState(true, rawPressed,
                                        state.stablePressed, state.armed,
                                        nowMs),
                   false)
         : rawPressed != state.stablePressed &&
                   debounceIntervalElapsed(nowMs, state.rawChangedAtMs,
                                           debounceMs)
             ? DebouncedButtonUpdate(
                   DebouncedButtonState(true, rawPressed, rawPressed,
                                        rawPressed ? false : true,
                                        state.rawChangedAtMs),
                   rawPressed && state.armed)
             : DebouncedButtonUpdate(state, false);
}

constexpr bool shouldStartTargetMacro(bool debouncedPressedEdge,
                                      bool macroActive) {
  return debouncedPressedEdge && !macroActive;
}

#endif
```

- [ ] **Step 4: Run the compile tests and verify GREEN**

Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build\full-avr-build.ps1
```

Expected: exit code `0`; `protocol_compile_test` compiles and all existing firmware targets still link.

- [ ] **Step 5: Commit the pure logic and tests**

```powershell
git add -- improved/mouse_switch_master/mouse_switch_macro_logic.h tests/protocol_compile_test/protocol_compile_test.ino
git commit -m "test: define target macro behavior"
```

---

### Task 2: Non-blocking Keyboard HID integration on master

**Files:**
- Modify: `improved/mouse_switch_master/mouse_switch_master.ino:1-17,30-40,393-456`
- Modify: `build/full-avr-build.ps1:3-40,84-89`

**Interfaces:**
- Consumes: all types/constants/functions from `mouse_switch_macro_logic.h`; Arduino `Keyboard.write(uint8_t)`; existing `MouseReport.sideButtons`.
- Produces: `TargetMacroRuntime`, `startTargetMacro(uint32_t)`, `serviceTargetMacro(uint32_t)`, and `updateTargetMacroButton(uint8_t, uint32_t)` inside the master sketch.

- [ ] **Step 1: Add the master integration before updating the build harness**

Add headers and constants:

```cpp
#include <Keyboard.h>
#include <Mouse.h>
#include <Wire.h>

#include "mouse_switch_macro_logic.h"
#include "mouse_switch_protocol.h"
#include "mouse_switch_ps2_logic.h"

constexpr uint8_t kSwitchSideButtonMask = 0x10;
constexpr uint8_t kTargetMacroSideButtonMask = 0x20;
constexpr uint16_t kTargetMacroDebounceMs = 25;
```

Add runtime state next to the existing globals:

```cpp
struct TargetMacroRuntime {
  bool active;
  uint8_t eventIndex;
  uint32_t nextEventAtMs;
};

DebouncedButtonState targetMacroButtonState =
    makeInitialDebouncedButtonState();
TargetMacroRuntime targetMacro = {false, 0, 0};
```

Add the non-blocking scheduler before `handleMouseFailure()`:

```cpp
bool timeReached(uint32_t nowMs, uint32_t deadlineMs) {
  return static_cast<int32_t>(nowMs - deadlineMs) >= 0;
}

uint16_t randomDelayInclusive(uint16_t minimumMs, uint16_t maximumMs) {
  return static_cast<uint16_t>(
      random(minimumMs, static_cast<long>(maximumMs) + 1));
}

void startTargetMacro(uint32_t nowMs) {
  targetMacro.active = true;
  targetMacro.eventIndex = 0;
  targetMacro.nextEventAtMs = nowMs;
}

void serviceTargetMacro(uint32_t nowMs) {
  if (!targetMacro.active ||
      !timeReached(nowMs, targetMacro.nextEventAtMs)) {
    return;
  }

  const uint8_t currentIndex = targetMacro.eventIndex;
  const TargetMacroEvent event = targetMacroEventAt(currentIndex);
  if (event.type == TargetMacroEventType::Enter) {
    Keyboard.write(KEY_RETURN);
  } else if (event.type == TargetMacroEventType::Character) {
    Keyboard.write(static_cast<uint8_t>(event.character));
  }

  ++targetMacro.eventIndex;
  if (targetMacro.eventIndex >= kTargetMacroEventCount) {
    targetMacro.active = false;
    return;
  }

  targetMacro.nextEventAtMs =
      nowMs + randomDelayInclusive(delayMinAfterEvent(currentIndex),
                                   delayMaxAfterEvent(currentIndex));
}

void updateTargetMacroButton(uint8_t sideButtons, uint32_t nowMs) {
  const bool rawPressed =
      isPs2SideButtonPressed(sideButtons, kTargetMacroSideButtonMask);
  const DebouncedButtonUpdate update = updateDebouncedButton(
      targetMacroButtonState, rawPressed, nowMs, kTargetMacroDebounceMs);
  targetMacroButtonState = update.state;
  if (shouldStartTargetMacro(update.pressedEdge, targetMacro.active)) {
    startTargetMacro(nowMs);
  }
}
```

In `setup()`, initialize keyboard HID and seed timing variation without sending keys:

```cpp
Mouse.begin();
Keyboard.begin();
randomSeed(micros());
Wire.begin();
```

At the first line of `loop()`, service an already-running macro before any PS/2 reconnect early return:

```cpp
const uint32_t loopNowMs = millis();
serviceTargetMacro(loopNowMs);
```

After a valid report and the unchanged `0x10` switching edge handling, call:

```cpp
updateTargetMacroButton(report.sideButtons, loopNowMs);
```

In both `handleMouseFailure()` and successful reconnect initialization, reset only the input debounce state, not `targetMacro`, so an active macro completes but a held reconnect button cannot synthesize a press:

```cpp
targetMacroButtonState = makeInitialDebouncedButtonState();
```

- [ ] **Step 2: Run the full build and verify the expected Keyboard harness failure**

Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build\full-avr-build.ps1
```

Expected: master compilation fails because the custom harness does not yet include the installed Keyboard library path and `Keyboard.cpp` object.

- [ ] **Step 3: Add Keyboard library compilation to the AVR harness**

Add the installed library root:

```powershell
$keyboardRoot = 'C:\Users\Valentyn\AppData\Local\Arduino15\libraries\Keyboard\src'
$mouseRoot = 'C:\Users\Valentyn\AppData\Local\Arduino15\libraries\Mouse\src'
```

Add `"-I$keyboardRoot"` to `$includes`, and add this entry to `$librarySources`:

```powershell
@{ Path = (Join-Path $keyboardRoot 'Keyboard.cpp'); Kind = 'cpp'; Object = 'lib_Keyboard.cpp.o' },
```

Keep the existing HID, Mouse, and Wire entries unchanged.

- [ ] **Step 4: Run all automated tests and the full AVR build**

Run:

```powershell
python -m unittest discover -s tests -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build\full-avr-build.ps1
```

Expected: six Python tests report `OK`; all five AVR targets compile and link with exit code `0`. Confirm the master `.text + .data` remains below the Leonardo flash limit and `.data + .bss` remains below 2560 bytes SRAM.

- [ ] **Step 5: Inspect the integration for blocking timing and routing regressions**

Run:

```powershell
rg -n "delay\(|delayMicroseconds\(|Keyboard|targetMacro|kTargetMacroSideButtonMask|sendRemotePacket" improved/mouse_switch_master/mouse_switch_master.ino
git diff --check
```

Expected: macro timing uses deadlines and `random(...)`, not `delay()`; existing PS/2-only `delayMicroseconds(...)` calls remain; keyboard calls occur only in master; `git diff --check` exits `0`.

- [ ] **Step 6: Commit master integration and build support**

```powershell
git add -- improved/mouse_switch_master/mouse_switch_master.ino build/full-avr-build.ps1
git commit -m "feat: type target macro from side button"
```

---

### Task 3: Documentation, final verification, and v5 package

**Files:**
- Modify: `README.md:5-10,35-55`
- Create outside repository: `outputs/dual-laptop-mouse-switch-working-v5/`
- Create outside repository: `outputs/dual-laptop-mouse-switch-working-v5.zip`

**Interfaces:**
- Consumes: verified master/receiver source tree from Tasks 1–2.
- Produces: user-facing flashing instructions, physical acceptance checklist, and an eight-file source archive.

- [ ] **Step 1: Update README behavior and flashing instructions**

Document all of the following explicitly:

```text
0x10 side button: switch laptops once per press.
0x20 side button: on master, Enter -> /target KYPKYMA -> Enter.
Typing delays: 35-80 ms per character; command pauses: 80-140 ms.
The host must already use the English keyboard layout.
The macro always targets the master laptop and does not pause mouse movement.
Middle button remains ignored.
Only master needs reflashing when upgrading from v4.
```

Extend the physical checklist with one command per press, no repeat while held, exact text, two `Enter` events, and smooth mouse movement during typing.

- [ ] **Step 2: Run fresh pre-commit verification**

Run:

```powershell
python -m unittest discover -s tests -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build\full-avr-build.ps1
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
git diff --check
```

Expected: six Python tests pass, every AVR target links, and diff checking exits `0`.

- [ ] **Step 3: Commit the documentation**

```powershell
git add -- README.md
git commit -m "docs: explain master target macro"
```

- [ ] **Step 4: Create the v5 source folder and archive**

From the repository root, run this exact PowerShell packaging script. If the two exact v5 targets already exist, inspect them first and remove only those targets before rerunning; do not delete or overwrite any v3/v4 output.

```powershell
$repo = (Get-Location).Path
$outRoot = 'C:\Users\Valentyn\Documents\Codex\2026-08-01\new-chat\outputs'
$folder = Join-Path $outRoot 'dual-laptop-mouse-switch-working-v5'
$master = Join-Path $folder 'mouse_switch_master'
$receiver = Join-Path $folder 'mouse_switch_receiver'
$zip = Join-Path $outRoot 'dual-laptop-mouse-switch-working-v5.zip'

New-Item -ItemType Directory -Path $master -Force | Out-Null
New-Item -ItemType Directory -Path $receiver -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $repo 'README.md') -Destination $folder
Copy-Item -LiteralPath (Join-Path $repo 'improved\mouse_switch_master\mouse_switch_master.ino') -Destination $master
Copy-Item -LiteralPath (Join-Path $repo 'improved\mouse_switch_master\mouse_switch_protocol.h') -Destination $master
Copy-Item -LiteralPath (Join-Path $repo 'improved\mouse_switch_master\mouse_switch_ps2_logic.h') -Destination $master
Copy-Item -LiteralPath (Join-Path $repo 'improved\mouse_switch_master\mouse_switch_macro_logic.h') -Destination $master
Copy-Item -LiteralPath (Join-Path $repo 'improved\mouse_switch_receiver\mouse_switch_receiver.ino') -Destination $receiver
Copy-Item -LiteralPath (Join-Path $repo 'improved\mouse_switch_receiver\mouse_switch_protocol.h') -Destination $receiver
Copy-Item -LiteralPath (Join-Path $repo 'improved\mouse_switch_receiver\mouse_switch_packet_queue.h') -Destination $receiver
Compress-Archive -LiteralPath $folder -DestinationPath $zip -CompressionLevel Optimal
```

- [ ] **Step 5: Verify archive layout, source identity, and checksum**

Open the ZIP with `System.IO.Compression.ZipFile`, list entries, and assert exactly these eight files exist beneath the top-level v5 folder:

```text
README.md
mouse_switch_master/mouse_switch_master.ino
mouse_switch_master/mouse_switch_protocol.h
mouse_switch_master/mouse_switch_ps2_logic.h
mouse_switch_master/mouse_switch_macro_logic.h
mouse_switch_receiver/mouse_switch_receiver.ino
mouse_switch_receiver/mouse_switch_protocol.h
mouse_switch_receiver/mouse_switch_packet_queue.h
```

Hash each staged file and its repository source with `Get-FileHash -Algorithm SHA256`; require every pair to match. Then record the final archive hash:

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\Users\Valentyn\Documents\Codex\2026-08-01\new-chat\outputs\dual-laptop-mouse-switch-working-v5.zip'
```

Expected: eight entries, all staged/source hashes identical, and one SHA-256 value for the ZIP.

- [ ] **Step 6: Perform final repository audit**

Run:

```powershell
git log -4 --oneline
git status --short
```

Expected: the macro logic, integration, and README commits are present. Preserve the pre-existing untracked `diagnostics/ps2_mouse_diagnostic/` directory; it is unrelated to this feature and must not be added, modified, or deleted.

- [ ] **Step 7: Hand off for physical validation**

Provide the v5 ZIP path and SHA-256, tell the user to flash only `mouse_switch_master.ino`, and request these physical checks before claiming hardware completion:

```text
1. 0x10 still switches between both laptops once per press.
2. 0x20 opens chat, types exactly /target KYPKYMA, and submits it on master.
3. Holding 0x20 does not repeat the macro.
4. Releasing and pressing 0x20 again runs exactly one new macro.
5. Cursor motion stays smooth on both laptop targets while the macro runs.
6. Middle button remains ignored.
```
