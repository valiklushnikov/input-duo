# Side-Button Finder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Создать временный master-скетч, который сохраняет поведение стабильной v3 и печатает фактическую маску нажатой боковой кнопки мыши.

**Architecture:** Декодирование дополнительных кнопок оформляется чистой функцией для четвёртого PS/2-байта и проверяется compile-time тестами. Определитель является отдельной Arduino-папкой с копией v3, добавляет только Serial-вывод Mouse ID и изменений маски `0x10/0x20`; рабочие v3-файлы не изменяются, кроме общего тестируемого helper-заголовка.

**Tech Stack:** Arduino AVR core 1.8.8, ATmega32U4/Leonardo 5V 16 MHz, `Mouse`, `Wire`, USB CDC Serial 115200, avr-g++ 7.3.0.

## Global Constraints

- Подтверждённая физически v3 остаётся доступной и не заменяется определителем.
- Определитель использует исправленный PS/2-транспорт и порог из трёх последовательных ошибок v3.
- Средняя кнопка во временном определителе продолжает переключать ноутбуки.
- Боковые кнопки во временном определителе только печатаются и не меняют активный ноутбук.
- Serial выводится только при инициализации и изменении боковой маски, чтобы печать не влияла на плавность движения.
- Receiver и семибайтовый I2C-протокол не изменяются.

---

### Task 1: Декодирование дополнительных кнопок

**Files:**
- Modify: `tests/protocol_compile_test/protocol_compile_test.ino`
- Modify: `improved/mouse_switch_master/mouse_switch_ps2_logic.h`

**Interfaces:**
- Produces: `constexpr uint8_t decodePs2SideButtons(uint8_t deviceId, uint8_t rawFourthByte)`.
- Contract: only ID `0x04` exposes bits `0x10` and `0x20`; all other IDs return zero.

- [ ] **Step 1: Add failing compile-time assertions**

```cpp
static_assert(decodePs2SideButtons(0x04, 0x10) == 0x10,
              "Explorer button 4 must be visible");
static_assert(decodePs2SideButtons(0x04, 0x20) == 0x20,
              "Explorer button 5 must be visible");
static_assert(decodePs2SideButtons(0x04, 0x3F) == 0x30,
              "Wheel nibble must not enter the side-button mask");
static_assert(decodePs2SideButtons(0x03, 0x30) == 0,
              "IntelliMouse reports do not expose Explorer buttons");
static_assert(decodePs2SideButtons(0x00, 0x30) == 0,
              "Standard reports do not contain a fourth byte");
```

- [ ] **Step 2: Run the AVR compile test and verify RED**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File build/full-avr-build.ps1`

Expected: FAIL because `decodePs2SideButtons` is not declared.

- [ ] **Step 3: Implement the minimal helper**

```cpp
constexpr uint8_t decodePs2SideButtons(uint8_t deviceId,
                                       uint8_t rawFourthByte) {
  return deviceId == 0x04 ? static_cast<uint8_t>(rawFourthByte & 0x30) : 0;
}
```

- [ ] **Step 4: Run the full test suite and verify GREEN**

Run:

```powershell
python -m unittest discover -s tests -p 'test_*.py' -v
powershell -NoProfile -ExecutionPolicy Bypass -File build/full-avr-build.ps1
```

Expected: all Python tests PASS and all existing AVR targets link with exit code 0.

- [ ] **Step 5: Commit**

```powershell
git add tests/protocol_compile_test/protocol_compile_test.ino improved/mouse_switch_master/mouse_switch_ps2_logic.h
git commit -m "test: decode PS2 Explorer side buttons"
```

### Task 2: Временный master-определитель

**Files:**
- Create: `diagnostics/side_button_finder/side_button_finder.ino`
- Create: `diagnostics/side_button_finder/mouse_switch_protocol.h`
- Create: `diagnostics/side_button_finder/mouse_switch_ps2_logic.h`
- Modify: `build/full-avr-build.ps1`
- Modify: `tests/test_protocol_copies.py`

**Interfaces:**
- Consumes: стабильную v3 и `decodePs2SideButtons` из Task 1.
- Produces: Serial lines `MOUSE ID: 0xNN` and `SIDE raw=0xNN mask=0xNN` at 115200.

- [ ] **Step 1: Extend the header-copy test before creating finder files**

Add these paths and assertions to `tests/test_protocol_copies.py`:

```python
finder = root / "diagnostics" / "side_button_finder"
self.assertEqual(
    (root / "improved" / "mouse_switch_master" /
     "mouse_switch_protocol.h").read_bytes(),
    (finder / "mouse_switch_protocol.h").read_bytes(),
)
self.assertEqual(
    (root / "improved" / "mouse_switch_master" /
     "mouse_switch_ps2_logic.h").read_bytes(),
    (finder / "mouse_switch_ps2_logic.h").read_bytes(),
)
```

Run:

```powershell
python -m unittest tests.test_protocol_copies -v
```

Expected: FAIL because the finder header copies are absent.

- [ ] **Step 2: Copy the two exact headers and v3 master body**

Create the folder and copy the exact v3 files before making finder-only edits:

```powershell
New-Item -ItemType Directory -Path diagnostics/side_button_finder
Copy-Item improved/mouse_switch_master/mouse_switch_master.ino diagnostics/side_button_finder/side_button_finder.ino
Copy-Item improved/mouse_switch_master/mouse_switch_protocol.h diagnostics/side_button_finder/mouse_switch_protocol.h
Copy-Item improved/mouse_switch_master/mouse_switch_ps2_logic.h diagnostics/side_button_finder/mouse_switch_ps2_logic.h
```

This preserves PS/2 framing, BAT timeout, three-failure reconnect policy, target switching, HID handling and I2C packet flow. Add a build target:

```powershell
@{ Name = 'side_button_finder'; Sketch = 'diagnostics\side_button_finder\side_button_finder.ino' }
```

- [ ] **Step 3: Add bounded Serial instrumentation**

Extend `MouseReport` with `uint8_t rawFourthByte` and `uint8_t sideButtons`. In `pollMouse` assign:

```cpp
report.rawFourthByte = wheelAvailable ? rawWheel : 0;
report.sideButtons = decodePs2SideButtons(mouseId, rawWheel);
```

Start `Serial.begin(115200)` before mouse initialization. After every successful initialization print the ID with a leading zero. Keep `previousSideButtons`; only when the mask changes print:

```text
SIDE raw=0x10 mask=0x10
SIDE raw=0x00 mask=0x00
```

For IDs other than `0x04`, print once: `SIDE BUTTONS UNAVAILABLE FOR THIS ID`. Do not use side bits in `toggleTarget()`.

- [ ] **Step 4: Build and inspect warnings**

Run:

```powershell
python -m unittest discover -s tests -p 'test_*.py' -v
powershell -NoProfile -ExecutionPolicy Bypass -File build/full-avr-build.ps1
```

Expected: all tests PASS, `SIZE side_button_finder` appears, and compilation/linking exits 0.

- [ ] **Step 5: Commit**

```powershell
git add diagnostics/side_button_finder tests/test_protocol_copies.py build/full-avr-build.ps1
git commit -m "feat: add PS2 side-button finder"
```

### Task 3: Архив и инструкция измерения

**Files:**
- Create: `diagnostics/side_button_finder/README_RU.md`
- Create output: `outputs/side-button-finder.zip`

**Interfaces:**
- Consumes: compiled finder from Task 2.
- Produces: one self-contained Arduino folder and exact user steps.

- [ ] **Step 1: Write the Russian instruction**

State that only master is reflashed; receiver remains unchanged. Specify `Arduino Leonardo` or `Pro Micro 5V/16 MHz`, Serial Monitor 115200, pressing and releasing only the desired thumb button, and copying the complete `MOUSE ID` plus `SIDE ...` lines back to Codex. Explain that v3 can be reflashed from its saved archive at any time.

- [ ] **Step 2: Run fresh verification**

Run:

```powershell
python -m unittest discover -s tests -p 'test_*.py' -v
powershell -NoProfile -ExecutionPolicy Bypass -File build/full-avr-build.ps1
git diff --check
```

Expected: all tests and targets pass; `git diff --check` returns no errors.

- [ ] **Step 3: Commit the instruction**

```powershell
git add diagnostics/side_button_finder/README_RU.md
git commit -m "docs: explain side-button identification"
```

- [ ] **Step 4: Package and audit**

Create `outputs/side-button-finder.zip` containing exactly:

```text
side_button_finder/
  README_RU.md
  side_button_finder.ino
  mouse_switch_protocol.h
  mouse_switch_ps2_logic.h
```

Open the ZIP, verify four entries and compute SHA-256. Do not include receiver, legacy sketches, build artifacts or the unfinished working v4.
