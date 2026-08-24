# Duo Input MVP Implementation Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Провести реализацию Duo Input от чистого общего протокола до проверенного комплекта из двух UF2 и Windows-установщика.

**Architecture:** Работа разделена на четыре последовательных плана с самостоятельными completion gates. Foundation блокирует все остальные этапы; core firmware и input runtime дают отдельно тестируемые аппаратные инкременты; configurator/release начинается на emulator и завершается HIL.

**Tech Stack:** Pico SDK C++17, TinyUSB, CH375B UART host, SPI1, Python 3.12, PySide6, QtSerialPort, pytest/CTest, Nuitka, Inno Setup.

**Spec:** `docs/superpowers/specs/2026-08-25-duo-input-firmware-configurator-design.md`

## Global Constraints

- Старые `legacy/`, `improved/` и `diagnostics/` не удалять и не переписывать.
- Каждый нижестоящий план начинается только после completion gate предыдущего.
- Все изменения выполняются TDD, маленькими коммитами из соответствующего task.
- Hardware claims подтверждаются измерениями; они не отмечаются выполненными по результатам симулятора.
- Рабочая ветка создаётся через `superpowers:using-git-worktrees` перед реализацией.

## Spec Coverage Map

| Spec sections | Implementation plan/tasks |
|---|---|
| 3–7 Hardware, U1/U2, USB | Core Firmware Tasks 1–5, 7 |
| 8–9 CH375 compatibility/events | Input/Mapping Tasks 1–4 |
| 10–13 routes/profiles/mouse/macros | Input/Mapping Tasks 5–7; Configurator Tasks 1–2, 5–6 |
| 14 binary config/Flash | Foundation Task 5; Core Firmware Task 6 |
| 15 CDC | Foundation Tasks 3–6; Configurator Task 3 |
| 16 SPI | Foundation Task 4; Core Firmware Task 4 |
| 17 fail-safe | Core Firmware Tasks 2, 4, 5, 7; Input/Mapping Task 7 |
| 18 Windows UI | Configurator Tasks 1–8 |
| 19–20 diagnostics/security | Foundation Task 7; Core Firmware Task 7; Configurator Task 7 |
| 21 testing | all completion gates; Configurator Task 10 |
| 22–24 versioning/release/sources | Foundation Task 2; Configurator Tasks 9–10 |

---

### Task 1: Execute protocol/config foundation

**Files:**
- Read/execute: `docs/superpowers/plans/2026-08-25-duo-input-foundation.md`

**Interfaces:**
- Produces generated C++/Python protocol IDs, shared vectors, strict codecs, binary config and U1 emulator.

- [ ] **Step 1: Create isolated worktree and baseline**

Run the using-git-worktrees skill, then run existing repository tests and record their passing/failing baseline without modifying unrelated diagnostic files.

- [ ] **Step 2: Execute Foundation Tasks 1–7 in order**

Use the exact red/green commands and commits in the foundation plan.

- [ ] **Step 3: Run foundation completion gate**

```powershell
python tools/generate_protocol.py --check
cmake --build build/native
ctest --test-dir build/native --output-on-failure
python -m pytest configurator/tests -q
```

- [ ] **Step 4: Review protocol artifacts**

Confirm IDs occur only in `protocol/schema.json` and generated files; compare C++/Python golden vectors.

- [ ] **Step 5: Create phase checkpoint commit/tag**

```powershell
git tag duo-input-foundation-v0.1
```

### Task 2: Execute core U1/U2 firmware

**Files:**
- Read/execute: `docs/superpowers/plans/2026-08-25-duo-input-core-firmware.md`

**Interfaces:**
- Consumes foundation codecs/config/emulator.
- Produces two UF2 with USB HID, CDC, SPI fail-safe, Flash A/B and hardware recovery controls.

- [ ] **Step 1: Execute Core Firmware Tasks 1–7 in order**

Use synthetic input only; do not begin CH375 integration inside this phase.

- [ ] **Step 2: Run native/build suites**

Run all commands from the core completion gate.

- [ ] **Step 3: Perform two-board link test**

Record USB descriptors, 10-minute pattern result, U2 measured release latency and power-cut config recovery.

- [ ] **Step 4: Review fail-safe invariants**

Verify every reset/error path reaches released HID state and damaged frames cause no reports.

- [ ] **Step 5: Create phase checkpoint tag**

```powershell
git tag duo-input-core-firmware-v0.1
```

### Task 3: Execute CH375, mapping and macros

**Files:**
- Read/execute: `docs/superpowers/plans/2026-08-25-duo-input-input-mapping-macros.md`

**Interfaces:**
- Consumes U1 Core 1 queue boundary and output runtime.
- Produces physical USB input, profiles, bindings, routes, capture and autonomous macros.

- [ ] **Step 1: Execute Input/Mapping Tasks 1–7 in order**

Start from WCH command-table verification and scripted transport before enabling TXS OE.

- [ ] **Step 2: Run native trace replays**

Verify committed keyboard/mouse traces generate exact normalized events.

- [ ] **Step 3: Perform physical input acceptance**

Run 1000 toggles, disconnect/reconnect, eight-profile power cycle and macro/physical concurrency checks.

- [ ] **Step 4: Update compatibility evidence**

Record every tested VID/PID/hash and exact pass/fail reason.

- [ ] **Step 5: Create phase checkpoint tag**

```powershell
git tag duo-input-input-runtime-v0.1
```

### Task 4: Execute configurator and release

**Files:**
- Read/execute: `docs/superpowers/plans/2026-08-25-duo-input-configurator-release.md`

**Interfaces:**
- Consumes emulator, real U1 CDC and binary compiler.
- Produces Windows installer, release UF2 files, compatibility matrix, hashes and user documentation.

- [ ] **Step 1: Execute Configurator Tasks 1–9 against emulator first**

No UI task may wait for physical hardware when the emulator contract covers it.

- [ ] **Step 2: Run real-device integration**

Repeat connect/read/write/capture/test/diagnostics flows against U1.

- [ ] **Step 3: Execute Task 10 HIL and release workflow**

All latency, soak, power-cut and compatibility metrics must be recorded.

- [ ] **Step 4: Run final clean verification**

Run native, Python, Pico build, clean-VM installer and SHA-256 checks from a clean checkout.

- [ ] **Step 5: Create release candidate tag**

```powershell
git tag duo-input-v0.1.0-rc1
```

## Overall Completion Gate

- Four subordinate completion gates pass.
- No open test failure is waived without a written spec change.
- Two versioned UF2 and one Windows x64 installer are reproducible from clean checkout.
- Compatibility, latency and 24-hour soak results are attached to release notes.
- Prototype RC may ship for private testing; commercial shipment remains blocked by legitimate USB VID/PID and Windows code-signing certificate.
