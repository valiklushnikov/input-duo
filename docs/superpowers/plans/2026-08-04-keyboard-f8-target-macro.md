# Keyboard F8/8 Target Macro Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce separate primary and secondary Pro Micro keyboard sketches so the shared `F8`/`8` button submits `/target KYPKYMA` only on laptop 1 and produces no input on laptop 2.

**Architecture:** Preserve the existing 16-input debounce and key mappings, but special-case mapped-button index `7` before the normal key dispatch. Primary uses a `millis()`-driven HID event scheduler; secondary ignores index `7` while leaving every other key path unchanged.

**Tech Stack:** Arduino ATmega32U4/Leonardo, Arduino Keyboard library, GNU AVR C++11, PowerShell build harness, Python source-contract tests.

## Global Constraints

- Primary path: `C:\Users\Valentyn\Desktop\fromPC\keyboard\keyboard.ino`.
- Backup path: `C:\Users\Valentyn\Desktop\fromPC\keyboard\keyboard.ino.bak-2026-08-04`.
- Secondary path: `C:\Users\Valentyn\Desktop\fromPC\keyboard_secondary\keyboard_secondary.ino`.
- Mapped-button index `7` replaces both `KEY_F8` and ASCII `8` in both modes.
- Primary sequence is exactly `Enter`, `/target KYPKYMA`, `Enter`.
- Character delays are inclusive pseudo-random values from 35 to 80 ms; pauses around the payload are inclusive pseudo-random values from 80 to 140 ms.
- Primary ignores all other panel actions while the macro is active but continues scanning/debouncing inputs.
- Secondary ignores index `7` only; all other buttons remain responsive.
- A held primary trigger at startup must be released before the first macro can run.
- Typing timing must not use `delay()`.
- Existing pin mapping, 20 ms debounce, mode selectors, GUI shortcuts, and non-target mapped keys remain unchanged.

---

### Task 1: Safe source staging and contract tests

**Files:**
- Read: `C:\Users\Valentyn\Desktop\fromPC\keyboard\keyboard.ino`
- Create: `C:\Users\Valentyn\Desktop\fromPC\keyboard\keyboard.ino.bak-2026-08-04`
- Create ignored local test: `build/keyboard_contract_test.py`

**Interfaces:**
- Consumes: current external keyboard sketch bytes.
- Produces: recoverable backup and source-level acceptance checks for both final sketches.

- [ ] **Step 1: Request write permission for the exact Desktop subtree**

Request write access only to:

```text
C:\Users\Valentyn\Desktop\fromPC\keyboard
C:\Users\Valentyn\Desktop\fromPC\keyboard_secondary
```

- [ ] **Step 2: Preserve the current primary source**

Verify the source and backup paths are exact and within `C:\Users\Valentyn\Desktop\fromPC`, then copy `keyboard.ino` byte-for-byte to `keyboard.ino.bak-2026-08-04`. If that exact backup already exists, compare hashes and do not overwrite it.

- [ ] **Step 3: Add failing source-contract tests**

Create `build/keyboard_contract_test.py` with checks that read both external sketches as UTF-8 and require. Keeping this machine-specific test under ignored `build/` prevents the repository suite from depending on a user's Desktop layout:

```python
PRIMARY = Path(r"C:\Users\Valentyn\Desktop\fromPC\keyboard\keyboard.ino")
SECONDARY = Path(r"C:\Users\Valentyn\Desktop\fromPC\keyboard_secondary\keyboard_secondary.ino")

self.assertIn('const char TARGET_MACRO_TEXT[] = "/target KYPKYMA";', primary)
self.assertIn("const uint8_t TARGET_BUTTON_INDEX = 7;", primary)
self.assertIn("const uint8_t TARGET_BUTTON_INDEX = 7;", secondary)
self.assertIn("startTargetMacro(now);", primary)
self.assertIn("serviceTargetMacro(now);", primary)
self.assertNotIn("Keyboard.press(KEY_F8)", primary)
self.assertNotIn("Keyboard.press('8')", primary)
self.assertNotIn("Keyboard.press(KEY_F8)", secondary)
self.assertNotIn("Keyboard.press('8')", secondary)
self.assertIn("if (i == TARGET_BUTTON_INDEX)", secondary)
```

Also assert `DEBOUNCE_MS = 20`, the 16-pin `ALL_PINS` initializer, and both 12-entry mapping tables remain present in each sketch.

- [ ] **Step 4: Run tests and verify RED**

Run:

```powershell
python build\keyboard_contract_test.py -v
```

Expected: failure because the secondary sketch does not exist and primary has no macro contract.

- [ ] **Step 5: Preserve RED evidence in the execution log**

Record the expected missing-secondary/missing-macro failure in the task commentary. Do not add the machine-specific test to Git.

---

### Task 2: Primary non-blocking macro firmware

**Files:**
- Modify: `C:\Users\Valentyn\Desktop\fromPC\keyboard\keyboard.ino`

**Interfaces:**
- Consumes: existing `changed[]`, `inputs[]`, `activeKey[]`, and `guiShortcutCount` state.
- Produces: `startTargetMacro(unsigned long)`, `serviceTargetMacro(unsigned long)`, `clearActiveKeys()`, and exact primary macro HID sequence.

- [ ] **Step 1: Add macro constants and runtime state**

Add after `MAPPED_KEY_COUNT`:

```cpp
const uint8_t TARGET_BUTTON_INDEX = 7;
const char TARGET_MACRO_TEXT[] = "/target KYPKYMA";
const uint8_t TARGET_MACRO_LENGTH = sizeof(TARGET_MACRO_TEXT) - 1;
const unsigned long CHARACTER_DELAY_MIN_MS = 35;
const unsigned long CHARACTER_DELAY_MAX_MS = 80;
const unsigned long COMMAND_PAUSE_MIN_MS = 80;
const unsigned long COMMAND_PAUSE_MAX_MS = 140;

static_assert(TARGET_MACRO_LENGTH == 15,
              "Target macro text must remain exact");

enum MacroPhase : uint8_t {
  MACRO_IDLE,
  MACRO_OPEN_CHAT,
  MACRO_TYPE_TEXT,
  MACRO_SUBMIT
};

MacroPhase macroPhase = MACRO_IDLE;
uint8_t macroCharacterIndex = 0;
unsigned long nextMacroEventAt = 0;
bool targetButtonArmed = false;
```

- [ ] **Step 2: Add complete HID release and deadline helpers**

```cpp
void clearActiveKeys() {
  Keyboard.releaseAll();
  for (uint8_t i = 0; i < MAPPED_KEY_COUNT; ++i) {
    activeKey[i] = 0;
  }
  guiShortcutCount = 0;
}

bool timeReached(unsigned long now, unsigned long deadline) {
  return (long)(now - deadline) >= 0;
}

unsigned long randomDelayInclusive(unsigned long minimumMs,
                                   unsigned long maximumMs) {
  return (unsigned long)random((long)minimumMs, (long)maximumMs + 1);
}
```

- [ ] **Step 3: Add the non-blocking scheduler**

```cpp
void startTargetMacro(unsigned long now) {
  clearActiveKeys();
  macroPhase = MACRO_OPEN_CHAT;
  macroCharacterIndex = 0;
  nextMacroEventAt = now;
}

void serviceTargetMacro(unsigned long now) {
  if (macroPhase == MACRO_IDLE || !timeReached(now, nextMacroEventAt)) {
    return;
  }

  if (macroPhase == MACRO_OPEN_CHAT) {
    Keyboard.write(KEY_RETURN);
    macroPhase = MACRO_TYPE_TEXT;
    nextMacroEventAt = now + randomDelayInclusive(
        COMMAND_PAUSE_MIN_MS, COMMAND_PAUSE_MAX_MS);
    return;
  }

  if (macroPhase == MACRO_TYPE_TEXT) {
    Keyboard.write((uint8_t)TARGET_MACRO_TEXT[macroCharacterIndex]);
    ++macroCharacterIndex;
    if (macroCharacterIndex >= TARGET_MACRO_LENGTH) {
      macroPhase = MACRO_SUBMIT;
      nextMacroEventAt = now + randomDelayInclusive(
          COMMAND_PAUSE_MIN_MS, COMMAND_PAUSE_MAX_MS);
    } else {
      nextMacroEventAt = now + randomDelayInclusive(
          CHARACTER_DELAY_MIN_MS, CHARACTER_DELAY_MAX_MS);
    }
    return;
  }

  Keyboard.write(KEY_RETURN);
  macroPhase = MACRO_IDLE;
}
```

- [ ] **Step 4: Initialize safe trigger state**

After input initialization and `Keyboard.releaseAll()` in `setup()`, add:

```cpp
targetButtonArmed = !inputs[TARGET_BUTTON_INDEX + 4].rawPressed;
randomSeed(micros());
```

- [ ] **Step 5: Special-case index 7 and isolate primary while typing**

At the start of `loop()`, call `serviceTargetMacro(now)` immediately after all `updateInput(...)` calls. If `macroPhase != MACRO_IDLE`, return after scanning so no panel action is emitted.

In the mapped-button loop, before normal dispatch, use:

```cpp
if (i == TARGET_BUTTON_INDEX) {
  if (inputs[inputIndex].stablePressed) {
    if (targetButtonArmed) {
      targetButtonArmed = false;
      startTargetMacro(now);
    }
  } else {
    targetButtonArmed = true;
  }
  continue;
}
```

The early macro return must occur after debounce scanning but before selector, shortcut, or mapped-key dispatch.

- [ ] **Step 6: Run the contract test**

Run:

```powershell
python build\keyboard_contract_test.py -v
```

Expected: still RED only because secondary is absent; all primary-specific assertions pass.

---

### Task 3: Secondary ignore-only firmware

**Files:**
- Create: `C:\Users\Valentyn\Desktop\fromPC\keyboard_secondary\keyboard_secondary.ino`

**Interfaces:**
- Consumes: original backup as the behavioral baseline.
- Produces: Arduino-compatible secondary sketch with index `7` suppressed in both modes.

- [ ] **Step 1: Create an Arduino-compatible secondary folder and source**

Start from the original backup, not the primary macro source. Keep all original declarations and functions, add:

```cpp
const uint8_t TARGET_BUTTON_INDEX = 7;
```

In the mapped-button loop, before the press/release branch, add:

```cpp
if (i == TARGET_BUTTON_INDEX) {
  continue;
}
```

Do not add macro state, timing, or global action suppression to secondary.

- [ ] **Step 2: Run source-contract tests and verify GREEN**

Run:

```powershell
python build\keyboard_contract_test.py -v
```

Expected: all role, payload, suppression, pin, debounce, and mapping assertions pass.

- [ ] **Step 3: Record final external source hashes**

No external Desktop files or machine-specific tests are added to the mouse-switch repository. Record the tested source hashes in `docs/superpowers/specs/2026-08-04-keyboard-f8-target-macro-design.md`, then commit that documentation update.

---

### Task 4: AVR compilation and handoff

**Files:**
- Verify: `C:\Users\Valentyn\Desktop\fromPC\keyboard\keyboard.ino`
- Verify: `C:\Users\Valentyn\Desktop\fromPC\keyboard_secondary\keyboard_secondary.ino`
- Create ignored helper/logs under: `build/keyboard-avr/`

**Interfaces:**
- Consumes: final primary and secondary sketches.
- Produces: two successful ATmega32U4 ELF builds, final SHA-256 hashes, and flashing instructions.

- [ ] **Step 1: Compile each sketch with the installed AVR toolchain**

Use the installed Arduino AVR 1.8.8 core, Leonardo variant, HID library, Keyboard library, `Keyboard.cpp`, and `KeyboardLayout_en_US.cpp`. Compile and link primary and secondary separately with the same `atmega32u4`, `F_CPU=16000000L`, and Leonardo USB defines used by `build/full-avr-build.ps1`.

- [ ] **Step 2: Verify flash and SRAM limits**

Run `avr-size.exe -A` on both ELF files. Require `.text + .data < 28672` and `.data + .bss < 2560` for each target.

- [ ] **Step 3: Run final source and regression checks**

```powershell
python -m unittest discover -s tests -p 'test_*.py' -v
python build\keyboard_contract_test.py -v
git diff --check
```

Expected: all keyboard role tests and existing repository tests pass.

- [ ] **Step 4: Hash all three recoverable artifacts**

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath `
  'C:\Users\Valentyn\Desktop\fromPC\keyboard\keyboard.ino', `
  'C:\Users\Valentyn\Desktop\fromPC\keyboard\keyboard.ino.bak-2026-08-04', `
  'C:\Users\Valentyn\Desktop\fromPC\keyboard_secondary\keyboard_secondary.ino'
```

- [ ] **Step 5: Hand off exact flashing roles**

Tell the user:

```text
Laptop 1 Arduino: flash C:\Users\Valentyn\Desktop\fromPC\keyboard\keyboard.ino
Laptop 2 Arduino: flash C:\Users\Valentyn\Desktop\fromPC\keyboard_secondary\keyboard_secondary.ino
```

Request physical validation of one macro on laptop 1, silence of `F8/8` on laptop 2, hold suppression, primary-only lockout during typing, and unchanged operation of every other key.
