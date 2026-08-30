# Duo Input Windows Configurator and Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Создать русско-/англоязычный Windows-конфигуратор PySide6, собрать его Nuitka standalone, упаковать установщиком и провести полную двухплатную приёмку MVP.

**Architecture:** Qt Widgets используют ViewModels и Domain, не знают COBS/CRC. DeviceService работает асинхронно через QSerialPort и общий frame codec; проект хранится в versioned JSON, а устройство получает только проверенный binary config. UI тестируется через emulator до подключения железа.

**Tech Stack:** Python 3.12 x64, PySide6 6.10.1 Widgets/QtSerialPort, pytest 8+, pytest-qt 4+, Nuitka standalone, MSVC Build Tools, Inno Setup 6, Windows 10/11.

**Spec:** `docs/superpowers/specs/2026-08-25-duo-input-firmware-configurator-design.md`

## Global Constraints

- Сначала завершить foundation; emulator является обязательной зависимостью UI tests.
- Подключение только к U1 по VID/PID/product/serial; U2 никогда не показывается как configurable.
- Редактирование локальное; устройство меняется только после явной transactional write.
- Три состояния различимы: dirty editor, saved `.duoinput.json`, committed device hash.
- Русский UI default, English included; protocol/error IDs не локализуются.
- Обычная работа без administrator rights; сетевых запросов/телеметрии нет.
- Nuitka `standalone`, не `onefile`; installer создаёт ярлыки и uninstall.
- Diagnostic ZIP исключает config/macro text по умолчанию.

## Locked File Structure

```text
configurator/src/duo_input/app.py
configurator/src/duo_input/domain/{models,validation,project_store,text_compiler}.py
configurator/src/duo_input/device/{service,qt_transport,transactions,discovery}.py
configurator/src/duo_input/ui/{main_window,overview,bindings,macros,mouse,profiles,diagnostics,settings}.py
configurator/src/duo_input/ui/models/project_session.py
configurator/src/duo_input/ui/models/binding_table.py
configurator/src/duo_input/ui/models/macro_steps.py
configurator/src/duo_input/resources/{resources.qrc,icons,translations}.*/
configurator/tests/{domain,device,ui,integration}/
configurator/packaging/nuitka-build.ps1
configurator/packaging/duo-input.iss
tests/hil/
docs/user/
```

---

### Task 1: Domain model and versioned JSON project

**Files:**
- Modify: `configurator/pyproject.toml`
- Modify: `configurator/src/duo_input/domain/models.py`
- Create: `configurator/src/duo_input/domain/validation.py`
- Create: `configurator/src/duo_input/domain/project_store.py`
- Create: `configurator/tests/domain/test_project_store.py`
- Create: `configurator/tests/vectors/project_v1_minimal.duoinput.json`

**Interfaces:**
- Produces: frozen dataclasses `DeviceProject`, `Profile`, `Binding`, `Macro`, `MacroStep`.
- Produces: `load_project(path)`, `save_project_atomic(project,path)`, `validate_project(project) -> tuple[ValidationIssue,...]`.

- [x] **Step 1: Write JSON round-trip/migration tests**

```python
def test_atomic_round_trip_preserves_unicode(tmp_path, project):
    path = tmp_path / "test.duoinput.json"
    save_project_atomic(project, path)
    assert load_project(path) == project
    assert "КУРКУМА" in path.read_text("utf-8")

def test_unknown_major_is_read_only(tmp_path):
    with pytest.raises(ProjectVersionError):
        load_project(write_schema(tmp_path, "99.0"))
```

- [x] **Step 2: Verify red state**

Run `python -m pytest configurator/tests/domain/test_project_store.py -q`.

- [x] **Step 3: Implement explicit JSON conversion**

Do not serialize `__dict__`. Encode enums by stable generated names, UUIDs as canonical strings, UTF-8 with indent 2 and sorted keys. Save to sibling `.tmp`, fsync, then `os.replace`. Validation returns exact profile/binding/macro/step paths.

- [x] **Step 4: Run domain suite**

Include 8-profile/full-limit project, duplicate trigger, 129th binding, 33rd macro, 65th step and unsupported enum tests.

- [x] **Step 5: Commit**

```powershell
git add configurator/pyproject.toml configurator/src/duo_input/domain configurator/tests/domain configurator/tests/vectors
git commit -m "feat: persist versioned Duo Input projects"
```

### Task 2: US/RU/UA text compiler

**Files:**
- Create: `configurator/src/duo_input/domain/layouts.py`
- Create: `configurator/src/duo_input/domain/text_compiler.py`
- Create: `configurator/tests/domain/test_text_compiler.py`
- Create: `configurator/tests/vectors/text_layout_vectors.json`

**Interfaces:**
- Produces: `compile_text(text: str, layout: LayoutId) -> tuple[HidChord,...]` and `UnsupportedCharacter(index,char,layout)`.

- [x] **Step 1: Add golden strings**

Vectors must include ASCII punctuation, `/target KYPKYMA`, Russian uppercase/lowercase/`ё`, Ukrainian `і/ї/є/ґ`, newline/tab and one emoji rejection with exact index.

- [x] **Step 2: Verify missing compiler failure**

Run text compiler tests.

- [x] **Step 3: Implement explicit physical-key tables**

Map characters to HID usage plus Shift/AltGr modifiers for US/RU/UA layouts. No Windows keyboard APIs or current OS layout may affect compilation. Newline maps Enter, tab maps Tab; control characters other than `\n`/`\t` reject.

- [x] **Step 4: Compile project to binary**

Integrate with existing `compile_device_config`; assert source JSON retains Unicode while binary contains HID chords only.

- [x] **Step 5: Commit**

```powershell
git add configurator/src/duo_input/domain configurator/tests/domain configurator/tests/vectors
git commit -m "feat: compile US Russian and Ukrainian macro text"
```

### Task 3: Asynchronous QSerialPort DeviceService

**Files:**
- Create: `configurator/src/duo_input/device/discovery.py`
- Create: `configurator/src/duo_input/device/qt_transport.py`
- Create: `configurator/src/duo_input/device/transactions.py`
- Create: `configurator/src/duo_input/device/service.py`
- Create: `configurator/tests/device/test_device_service.py`

**Interfaces:**
- Produces QObject `DeviceService` signals: `state_changed`, `status_changed`, `capture_received`, `progress_changed`, `operation_failed`.
- Methods: `connect_device`, `disconnect_device`, `read_config`, `write_config`, `begin_capture`, `stop_and_release_all`, `get_diagnostics`.

- [x] **Step 1: Write qtbot state-machine tests**

```python
def test_disconnect_during_write_returns_ready_with_old_hash(qtbot, service, emulator):
    service.connect_device(emulator.transport)
    emulator.disconnect_on_chunk(2)
    with qtbot.waitSignal(service.operation_failed):
        service.write_config(b"x" * 1500)
    assert service.device_hash == emulator.active_hash
```

- [x] **Step 2: Verify red state**

Run `python -m pytest configurator/tests/device/test_device_service.py -q`.

- [x] **Step 3: Implement discovery/framing/transactions**

Use `QSerialPortInfo.availablePorts`, whitelist identity, retain partial COBS bytes across `readyRead`, one in-flight request, per-command timeout and sequence matching. Never block Qt event loop; chunk progress emits 0…100.

- [x] **Step 4: Run emulator fault matrix**

Test bad CRC, wrong sequence, major mismatch, disconnect, timeout, abort, readback mismatch and successful 360-KiB transfer.

- [x] **Step 5: Commit**

```powershell
git add configurator/src/duo_input/device configurator/tests/device
git commit -m "feat: communicate with U1 over Qt serial port"
```

### Task 4: Application shell, dirty state and Overview

**Files:**
- Create: `configurator/src/duo_input/app.py`
- Create: `configurator/src/duo_input/ui/main_window.py`
- Create: `configurator/src/duo_input/ui/overview.py`
- Create: `configurator/src/duo_input/ui/models/project_session.py`
- Create: `configurator/tests/ui/test_main_window.py`

**Interfaces:**
- Produces: `ProjectSession` properties `dirty`, `file_hash`, `compiled_hash`, `device_hash`, `can_write`.
- Produces application entry point `duo-input-configurator`.

- [x] **Step 1: Write three-state UX tests**

Assert title marker for dirty, Save clears dirty but not device mismatch, successful write aligns device hash, failed write leaves mismatch, close dirty asks Save/Discard/Cancel.

- [x] **Step 2: Verify red state**

Run UI test offscreen: `$env:QT_QPA_PLATFORM='offscreen'; python -m pytest configurator/tests/ui/test_main_window.py -q`.

- [x] **Step 3: Build shell and Overview cards**

Create left navigation, 8-profile selector, connection indicator, Save and Write buttons. Overview shows U1/U2 versions, peripherals, routes, memory usage and recent events from DeviceService.

- [x] **Step 4: Run keyboard navigation/accessibility smoke**

Verify tab order, translated accessible names and minimum 1024×700 layout without clipped controls.

- [x] **Step 5: Commit**

```powershell
git add configurator/src/duo_input/app.py configurator/src/duo_input/ui configurator/tests/ui
git commit -m "feat: add configurator shell and project state"
```

### Task 5: Profiles, bindings and mouse-switch editors

**Files:**
- Create: `configurator/src/duo_input/ui/profiles.py`
- Create: `configurator/src/duo_input/ui/bindings.py`
- Create: `configurator/src/duo_input/ui/mouse.py`
- Create: `configurator/src/duo_input/ui/models/binding_table.py`
- Create: `configurator/tests/ui/test_bindings.py`
- Create: `configurator/tests/ui/test_mouse_switch.py`

**Interfaces:**
- Produces editors operating only through immutable `ProjectSession.apply(command)` operations for undoable changes.

- [x] **Step 1: Write interaction tests**

Test 8 profile slots, copy/clear, Replace/Add, keyboard modifier trigger, mouse buttons limited by capabilities, Capture selection, duplicate conflict and Button4 unavailable warning after mouse change.

- [x] **Step 2: Verify failures**

Run binding/mouse UI tests.

- [x] **Step 3: Implement editors**

Mouse page must first choose `Keyboard key` or `Mouse button`, then action Toggle/PC1/PC2 and Replace/Add. Capture dialog has 10-second countdown/cancel; only detected buttons are enabled.

- [x] **Step 4: Validate full project before Write**

Inject conflicts and limit errors; assert Write disabled and clicking issue navigates to exact profile/binding control.

- [x] **Step 5: Commit**

```powershell
git add configurator/src/duo_input/ui configurator/tests/ui
git commit -m "feat: edit profiles bindings and mouse switching"
```

### Task 6: Macro step editor and safe test run

**Files:**
- Create: `configurator/src/duo_input/ui/macros.py`
- Create: `configurator/src/duo_input/ui/models/macro_steps.py`
- Create: `configurator/tests/ui/test_macro_editor.py`

**Interfaces:**
- Produces drag/drop `MacroStepListModel`, step-specific editors and `TestMacroDialog`.

- [x] **Step 1: Write editor tests**

Test add/delete/reorder all step types, fixed/random delay ranges, 64-step limit, 1024-char limit, target Inherit/PC1/PC2/Both, unsupported char navigation and exact `/target KYPKYMA` compilation.

- [x] **Step 2: Verify red state**

Run macro editor tests.

- [x] **Step 3: Implement step model and delegates**

Store stable step UUIDs; drag/drop uses `beginMoveRows`. Test dialog requires target and confirmation, shows persistent red `STOP AND RELEASE ALL`, and calls DeviceService without modifying ProjectSession.

- [x] **Step 4: Run emulator test macro flow**

Assert confirm→TEST_MACRO, Stop→STOP_AND_RELEASE_ALL, disconnect closes running state with warning, and no test action changes saved/device hashes.

- [x] **Step 5: Commit**

```powershell
git add configurator/src/duo_input/ui configurator/tests/ui
git commit -m "feat: edit and safely test macro sequences"
```

### Task 7: Diagnostics, autosave and privacy-safe export

**Files:**
- Create: `configurator/src/duo_input/ui/diagnostics.py`
- Create: `configurator/src/duo_input/persistence/autosave.py`
- Create: `configurator/src/duo_input/persistence/diagnostic_export.py`
- Create: `configurator/tests/integration/test_diagnostic_export.py`

**Interfaces:**
- Produces: `AutosaveService`, `build_diagnostic_zip(destination, snapshot, include_config=False)`.

- [x] **Step 1: Write privacy tests**

Create project containing secret macro text; assert default ZIP byte content excludes it and includes versions, reset reason, CH375 identity/hash, SPI counters and application log. `include_config=True` includes explicitly named project file.

- [x] **Step 2: Verify failure**

Run integration export test.

- [x] **Step 3: Implement diagnostics and autosave recovery**

Use `%LOCALAPPDATA%\DuoInput\autosave` and `logs`; rotate 5×2 MiB logs. On startup offer Recover/Discard only when autosave is newer than project. Never log Text step content.

- [x] **Step 4: Run recovery/export tests**

Test corrupted autosave, unwritable destination, missing device fields and opt-in config export.

- [x] **Step 5: Commit**

```powershell
git add configurator/src/duo_input/ui/diagnostics.py configurator/src/duo_input/persistence configurator/tests/integration
git commit -m "feat: add private diagnostics and autosave recovery"
```

### Task 8: Russian/English localization and resources

**Files:**
- Create: `configurator/src/duo_input/resources/resources.qrc`
- Create: `configurator/src/duo_input/resources/translations/duo_input_ru.ts`
- Create: `configurator/src/duo_input/resources/translations/duo_input_en.ts`
- Create: `configurator/src/duo_input/i18n.py`
- Create: `configurator/src/duo_input/ui/settings.py`
- Create: `configurator/tests/ui/test_localization.py`

**Interfaces:**
- Produces: `TranslationManager.set_language("ru"|"en")`, compiled `.qm` resources.

- [x] **Step 1: Write translation coverage test**

Extract all `tr()` source strings and assert both catalogs contain finished entries; protocol enum names/error numeric IDs are excluded.

- [x] **Step 2: Verify missing catalogs failure**

Run localization test.

- [x] **Step 3: Add resources and runtime switch**

Russian loads by default on first run. Settings page controls RU/EN, default project directory and log level. Changing language persists in QSettings and retranslates open top-level widgets or requests application restart with explicit message.

- [x] **Step 4: Screenshot smoke at 100%/150% DPI**

Capture all pages in RU/EN at 1280×800; assert no clipped button text and no untranslated source markers.

- [x] **Step 5: Commit**

```powershell
git add configurator/src/duo_input/resources configurator/src/duo_input/i18n.py configurator/tests/ui
git commit -m "feat: localize configurator in Russian and English"
```

### Task 9: Nuitka standalone and Inno Setup

**Files:**
- Create: `configurator/requirements-build.txt`
- Create: `configurator/packaging/nuitka-build.ps1`
- Create: `configurator/packaging/duo-input.iss`
- Create: `configurator/tests/packaging/test_dist.py`
- Create: `docs/release/windows-build.md`
- Create: `docs/release/third-party-licenses.md`

**Interfaces:**
- Produces: `dist/DuoInput/DuoInput.exe` and `dist/DuoInput-Setup-<version>-x64.exe`.

- [x] **Step 1: Write dist contract test**

Assert EXE, Qt platform plugin, QtSerialPort DLL, translations, license notices and version metadata exist; assert no `.py`, test files or macro projects are bundled.

- [x] **Step 2: Verify red state**

Run `python -m pytest configurator/tests/packaging/test_dist.py -q`; expected missing dist.

- [x] **Step 3: Implement reproducible build scripts**

`nuitka-build.ps1` creates clean Python 3.12 venv, installs locked build requirements, runs tests, then:

```powershell
python -m nuitka --standalone --enable-plugin=pyside6 --windows-console-mode=disable `
  --output-dir=dist --output-filename=DuoInput.exe `
  --include-qt-plugins=platforms,styles `
  src/duo_input/app.py
```

Inno installer uses per-user install by default, Start Menu shortcut, uninstall entry and no driver installation. `third-party-licenses.md` records exact PySide6/Qt/Nuitka/Inno licenses and bundled notices. Before public commercial distribution, record the chosen Qt for Python licensing route (commercial license or reviewed LGPLv3 compliance); do not treat the technical build as legal approval.

- [ ] **Step 4: Clean VM smoke**

> **NOT DONE:** clean Windows VM without Python or Qt was not available in this environment.

Install on clean Windows 10/11 x64 without Python/Qt, launch, connect emulator/real U1, save/open project, uninstall and verify user projects remain.

- [x] **Step 5: Commit**

```powershell
git add configurator/requirements-build.txt configurator/packaging configurator/tests/packaging docs/release/windows-build.md docs/release/third-party-licenses.md
git commit -m "build: package configurator with Nuitka and Inno Setup"
```

### Task 10: HIL, compatibility matrix and release candidate

**Files:**
- Create: `tests/hil/hil_runner.py`
- Create: `tests/hil/scenarios/peripherals.json`
- Create: `tests/hil/scenarios/route_toggle.json`
- Create: `tests/hil/scenarios/link_fault.json`
- Create: `tests/hil/scenarios/config_power_cut.json`
- Create: `tests/hil/scenarios/profile_power_cycle.json`
- Create: `tests/hil/scenarios/soak_24h.json`
- Create: `docs/release/compatibility-matrix.md`
- Create: `docs/user/quick-start-ru.md`
- Create: `docs/user/uf2-update-ru.md`
- Create: `tools/build_release.ps1`

**Interfaces:**
- Produces release folder containing two versioned UF2, installer, SHA256SUMS and release notes.

- [x] **Step 1: Encode acceptance scenarios**

Scenarios cover 5 keyboards/5 mice, 1000 route toggles, U2 link cut, independent resets, 100 config writes, 20 write power cuts, eight-profile power cycle, 24-hour soak and macro Stop.

- [ ] **Step 2: Run automated suites before HIL**

> **PARTIAL:** `generate_protocol.py --check`, the native build, `ctest` (7/7) and
> `pytest configurator/tests tests` (565 passed) all pass. The Pico firmware build
> did not run: PICO_SDK_PATH is unset and the arm-none-eabi toolchain is not installed.

```powershell
python tools/generate_protocol.py --check
cmake --build build/native
ctest --test-dir build/native --output-on-failure
python -m pytest configurator/tests tests/build -q
cmake --build --preset pico-release --parallel
```

Expected: all pass before touching hardware.

- [ ] **Step 3: Execute and record HIL metrics**

> **RUNNER READY, RUN NOT YET TAKEN:** `hil_runner.py` now measures rather than
> refusing. It records the firmware-internal input latency U1 counts of itself
> (p95 against 20 ms and stalls against 50 ms, both exact because those are
> bucket edges), U2's release against 100 ms, and a device row per peripheral
> port carrying VID, PID, descriptor hash, buttons and a reason. **The
> specification's end-to-end keystroke p95 is not measurable on this rig** -
> nothing timestamps a finger and nothing injects HID into U1's CH375 ports -
> and every check needing a logger at PC1/PC2, a switchable supply or a
> configuration write is recorded as unmeasured with the rig it would take,
> never skipped. See `task-10-hil-report.md`; the controller's commands are in
> its section 5.

`hil_runner.py` timestamps injected/observed events, calculates keyboard/mouse p95 ≤20 ms, flags pauses >50 ms and records U2 release ≤100 ms. Each device row records VID/PID/hash, buttons and pass/fail reason.

- [ ] **Step 4: Build signed release directory**

> **NOT DONE:** `build_release.ps1` is written and its version and dirty-tree guards
> are verified, but no release folder can be assembled without the two UF2 files
> from Step 2 and the metrics from Step 3.

`build_release.ps1` refuses dirty tree, reads one SemVer, builds/tests artifacts, names them per spec, calculates SHA-256 and writes compatibility versions. Code-signing is optional for prototype; commercial distribution gate requires a trusted Windows signing certificate and legitimate USB VID/PID.

- [x] **Step 5: Commit release tooling/docs**

```powershell
git add tests/hil docs/release docs/user tools/build_release.ps1
git commit -m "test: add Duo Input release acceptance workflow"
```

## Plan Completion Gate

- UI passes pytest/pytest-qt against emulator and real U1.
- RU/EN pages are complete at 100%/150% DPI.
- Nuitka installer works on clean Windows 10/11 without Python.
- All spec acceptance metrics are recorded, not estimated.
- Release folder contains exact versioned artifacts and hashes.
- Commercial release remains blocked until USB VID/PID and Windows code-signing are legitimate.
