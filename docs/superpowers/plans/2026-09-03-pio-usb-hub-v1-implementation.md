# Native PIO USB Host Hub V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace U1's two CH375 input channels with one Pico-PIO-USB/TinyUSB host connected through the XL334P4 hub, while preserving the verified keyboard, mouse, macro, routing, storage, configurator, PC1 USB-device and U1-to-U2 behaviours.

**Architecture:** U1 remains the only peripheral host. Core 1 runs TinyUSB host over PIO on GP0/GP1 and converts HID callbacks into a backend-neutral, fixed-capacity event boundary consumed by the existing `InputPipeline`. Core 0 continues to own native TinyUSB device output, CDC configuration, SPI output to U2, flash and watchdog service. CH375 remains a separately selectable build until the PIO backend passes hardware acceptance; U2 firmware and the configuration package format do not change.

**Tech Stack:** C++17, Raspberry Pi Pico SDK 2.3.0, TinyUSB host/device, Pico-PIO-USB 0.7.2, CMake/Ninja, RP2040 PIO and multicore SDK, Python/pytest HIL tooling, PySide6 configurator, Nuitka packaging.

**Spec:** `docs/superpowers/specs/2026-09-02-pio-usb-hub-v1-design.md`

## Global Constraints

- Start from known-good commit `af49b2f`; create `feature/pio-usb-host-hub-v1` without rewriting or deleting the CH375 implementation.
- Keep `pico-release` as the CH375 build until the final hardware gate. Add separate PIO presets and artifacts so both U1 images can be reproduced and compared.
- Do not flash U1 until the user explicitly approves the exact UF2. Do not change or flash U2 unless a failing compatibility test proves an U2 defect.
- U1 host pins are fixed: GP0 is D+, GP1 is D-. U1/U2 SPI stays GP10↔GP10, GP9↔GP9, U1 GP11→U2 GP8, U2 GP11→U1 GP8, common GND.
- Use no heap allocation, unbounded queue, unbounded descriptor, blocking wait, or callback that performs routing work directly. USB callbacks copy bounded data into fixed storage; the ordinary Core 1 pass drains it.
- Preserve current `InputEvent`, mapping, capture, macros, routes, output commands, SPI protocol, A/B storage and configuration schema. A backend may identify devices differently but must not change events above `InputPipeline`.
- On detach, reset, malformed state, queue overflow or host fault, release every held key/button before forgetting the source. A dropped release is a release-all fault, not a recoverable report loss.
- Accept exactly one logical keyboard and one logical mouse in V1. Ignore additional supported interfaces deterministically and expose the reason in diagnostics.
- Keep the current Keychron 3434:D030 side-button compatibility path; move its transport-specific wiring behind the neutral source boundary instead of deleting it.
- Run native tests after every task. Run both Pico builds whenever CMake, TinyUSB configuration, main-loop ownership or linker inputs change.

---

### Task 1: Preserve the baseline and establish the isolated migration branch

**Files:**
- Modify: `docs/superpowers/specs/2026-09-02-pio-usb-hub-v1-design.md`
- Create: `docs/superpowers/records/2026-09-03-pio-usb-hub-v1-record.md`
- Reference: `docs/superpowers/records/2026-08-29-roadmap-execution-record.md`

**Interfaces:** No firmware interface changes. This task fixes the exact base, branch, test counts and recovery command before implementation.

- [ ] Verify `git rev-parse HEAD` is `af49b2f` and save `git status --short --branch`; stop if tracked files are dirty.
- [ ] Create `feature/pio-usb-host-hub-v1` from `af49b2f`, leaving the current CH375 branch and release artifacts untouched.
- [ ] Change the spec status to `Approved for implementation` and add the approved plan path.
- [ ] Run `cmake --build --preset native` and `ctest --preset native`; record exact pass counts in the new execution record.
- [ ] Configure and build the existing `pico-release` preset; run `python -m pytest tests/build/test_firmware_artifacts.py -q` and record U1/U2 UF2 hashes.
- [ ] Commit: `git add docs/superpowers && git commit -m "Record PIO USB migration baseline"`.

### Task 2: Pin a reproducible PIO USB toolchain without disturbing CH375 builds

**Files:**
- Create: `cmake/pio_usb_toolchain_lock.cmake`
- Create: `tools/bootstrap_pio_usb_toolchain.ps1`
- Create: `tests/build/test_pio_usb_toolchain_lock.py`
- Modify: `.gitignore`
- Modify: `CMakeLists.txt`
- Modify: `CMakePresets.json`
- Modify: `docs/release/third-party-licenses.md`
- Modify: `docs/release/firmware-build.md`

**Interfaces:** Add CMake cache string `DUO_INPUT_BACKEND` with accepted values `CH375` and `PIO_USB`. Add presets `pico-pio-usb-release` and `pico-pio-usb-debug`. Lock Pico SDK to `98a542c1a62fb549ffb5d66a3e5892b06276b670`, TinyUSB to `86ad6e56c1700e85f1c5678607a762cfe3aa2f47`, and Pico-PIO-USB to `3c1eec341a5232640e4c00628b889b641af34b28`.

- [ ] Write `test_pio_usb_toolchain_lock.py` asserting the three full revisions, the two new preset names, `PIO_USB` selection, and that existing `pico-release` still selects `CH375`.
- [ ] Run `python -m pytest tests/build/test_pio_usb_toolchain_lock.py -q`; verify it fails because the lock and presets do not exist.
- [ ] Implement `bootstrap_pio_usb_toolchain.ps1` to clone/fetch exact revisions into ignored `.deps/`, initialize only required submodules, verify every `rev-parse HEAD`, and fail on mismatch rather than silently using `main`.
- [ ] Implement `pio_usb_toolchain_lock.cmake` so the PIO preset sets `PICO_SDK_PATH`, `PICO_TINYUSB_PATH` and `PICO_PIO_USB_PATH` to the verified `.deps` directories; keep the legacy environment-based path for CH375.
- [ ] Add strict `DUO_INPUT_BACKEND` validation and compile definitions `DUO_INPUT_BACKEND_CH375` / `DUO_INPUT_BACKEND_PIO_USB`; unknown values must fail configuration.
- [ ] Add the presets and document bootstrap, offline rebuild and licenses.
- [ ] Run the lock test, then configure both `pico-release` and `pico-pio-usb-release`; the latter may stop at the intentionally absent backend source, but dependency validation must pass.
- [ ] Commit: `git add .gitignore CMakeLists.txt CMakePresets.json cmake tools tests/build docs/release && git commit -m "Pin the PIO USB build toolchain"`.

### Task 3: Move HID report layouts out of the CH375 namespace

**Files:**
- Create: `firmware/u1_main/input/hid/report_descriptor.hpp`
- Create: `firmware/u1_main/input/hid/report_descriptor.cpp`
- Modify: `firmware/u1_main/ch375/report_descriptor.hpp`
- Modify: `firmware/u1_main/ch375/report_descriptor.cpp`
- Modify: `firmware/u1_main/ch375/descriptor_setup.hpp`
- Modify: `firmware/u1_main/ch375/descriptor_setup.cpp`
- Modify: `firmware/u1_main/input/keyboard_normalizer.hpp`
- Modify: `firmware/u1_main/input/keyboard_normalizer.cpp`
- Modify: `firmware/u1_main/input/mouse_normalizer.hpp`
- Modify: `firmware/u1_main/input/mouse_normalizer.cpp`
- Modify: `tests/firmware_native/test_report_descriptor.cpp`
- Modify: `tests/firmware_native/test_normalizers.cpp`
- Modify: `tests/firmware_native/CMakeLists.txt`

**Interfaces:** Canonical types become `input::hid::ReportField`, `MouseReportLayout`, `KeyboardReportLayout`, `ReportDescriptorError`, `boot_mouse_layout()`, `boot_keyboard_layout()`, `parse_mouse_report_descriptor()` and `parse_keyboard_report_descriptor()`. Temporary aliases in `ch375/report_descriptor.hpp` keep CH375 call sites source-compatible during this task.

- [ ] Change one descriptor and one normalizer test to include `input/hid/report_descriptor.hpp` and use `input::hid`; verify native compilation fails because the neutral header is absent.
- [ ] Move the existing parser and layout declarations mechanically into `input/hid` without changing parsing logic, limits or enum values.
- [ ] Add temporary CH375 aliases and forwarding includes; do not keep a second implementation.
- [ ] Update normalizers to accept neutral layouts and update CMake source lists.
- [ ] Run `cmake --build --preset native` and `ctest --preset native`; require every existing descriptor corpus, packed-axis, NKRO and malformed-input test to pass unchanged.
- [ ] Run `rg -n "ch375::(KeyboardReportLayout|MouseReportLayout|ReportField|ReportDescriptorError)" firmware/u1_main/input tests/firmware_native`; require no matches outside temporary CH375 compatibility tests.
- [ ] Commit: `git add firmware/u1_main tests/firmware_native && git commit -m "Make HID report layouts backend neutral"`.

### Task 4: Define the fixed-capacity input-source boundary and adapt CH375 to it

**Files:**
- Create: `firmware/u1_main/input/source.hpp`
- Create: `firmware/u1_main/input/ch375_source_adapter.hpp`
- Create: `firmware/u1_main/input/ch375_source_adapter.cpp`
- Create: `tests/firmware_native/test_input_source.cpp`
- Modify: `firmware/u1_main/input/pipeline.hpp`
- Modify: `firmware/u1_main/input/pipeline.cpp`
- Modify: `firmware/u1_main/main.cpp`
- Modify: `firmware/u1_main/CMakeLists.txt`
- Modify: `tests/firmware_native/test_input_pipeline.cpp`
- Modify: `tests/firmware_native/CMakeLists.txt`

**Interfaces:** `input::DeviceKind { Unknown, Keyboard, Mouse }`; `SourceEventKind { Ready, Report, AuxiliaryReport, Detached, Fault }`; `SourceEvent` carries source ID, endpoint/instance, `received_us`, a maximum 64-byte report and length; `SourceIdentity` carries VID, PID, neutral keyboard/mouse layouts and descriptor metadata. `InputPipeline::on_event(const SourceEvent&, const SourceIdentity&, uint32_t now_ms)` replaces the CH375-specific overload.

- [ ] Add tests that construct neutral Ready/Report/Detached events and prove keyboard presses, mouse movement, Keychron auxiliary side-button edges and detach releases are identical to the old path.
- [ ] Run `cmake --build --preset native`; verify failure on missing neutral source types.
- [ ] Implement fixed-size types with compile-time assertions that a report cannot exceed 64 bytes and no pointer to backend-owned transient memory crosses the boundary.
- [ ] Update `InputPipeline` to use neutral kinds/layouts while preserving every existing state-reset and Keychron identity check.
- [ ] Implement `Ch375SourceAdapter` as a mechanical conversion from `Ch375Event` and `DescriptorSetup` to the neutral boundary; keep latency origin from `received_us`.
- [ ] Switch the CH375 Core 1 loop to the adapter and run all native tests.
- [ ] Build `pico-release`, run artifact tests, and compare its runtime symbols for `Ch375Device::tick` and `InputPipeline::on_event` with the baseline contract.
- [ ] Commit: `git add firmware/u1_main tests/firmware_native && git commit -m "Add a backend-neutral input source boundary"`.

### Task 5: Add compile-only Pico-PIO-USB/TinyUSB dual-role scaffolding

**Files:**
- Create: `firmware/u1_main/pio_usb/backend.hpp`
- Create: `firmware/u1_main/pio_usb/backend.cpp`
- Create: `firmware/u1_main/pio_usb/tinyusb_host_callbacks.cpp`
- Create: `tests/build/test_pio_usb_firmware_contract.py`
- Modify: `firmware/u1_main/tusb_config.h`
- Modify: `firmware/u1_main/CMakeLists.txt`
- Modify: `firmware/u1_main/main.cpp`

**Interfaces:** `PioUsbBackend::begin()`, `task(uint32_t now_us)`, `take_event(SourceEvent&, SourceIdentity&)`, `logical_port(DeviceKind)`. TinyUSB RHPort 0 remains device; RHPort 1 becomes PIO host. GP0 is D+, GP1 is the adjacent D- pin. Core 1 owns `tuh_init(1)` and `tuh_task()`.

- [ ] Write the build-contract test to inspect CMake inputs and the PIO U1 ELF for `tuh_task`, `tuh_hid_receive_report`, `InputPipeline::on_event`, while asserting the PIO ELF contains no `Ch375Device::tick`.
- [ ] Run the test against the current PIO build; verify it fails because the target does not link host support.
- [ ] Add conditional TinyUSB host configuration: `CFG_TUH_ENABLED=1`, `CFG_TUH_HUB=1`, HID host enabled, bounded device/interface counts for one hub plus keyboard and mouse, and RHPort 1 PIO support only under `DUO_INPUT_BACKEND_PIO_USB`.
- [ ] Link `tinyusb_host` and `tinyusb_pico_pio_usb` only for PIO builds; link current CH375 PIO/UART sources only for CH375 builds.
- [ ] In `begin()`, set the RP2040 system clock to 120 MHz, configure `PIO_USB_DEFAULT_CONFIG.pin_dp = 0`, call `tuh_configure(1, TUH_CFGID_RPI_PIO_USB_CONFIGURATION, ...)`, then `tuh_init(1)` from Core 1.
- [ ] Implement empty bounded callbacks and a `task()` that services `tuh_task()` but publishes no input yet. Core 0's `tud_task()`/CDC/HID device work remains unchanged.
- [ ] Build `pico-pio-usb-release`; run the build-contract and artifact-size tests. Then rebuild `pico-release` to prove the CH375 target still links.
- [ ] Commit: `git add firmware/u1_main tests/build && git commit -m "Compile the dual-role PIO USB host backend"`.

### Task 6: Build and test a bounded TinyUSB HID device registry

**Files:**
- Create: `firmware/u1_main/pio_usb/device_registry.hpp`
- Create: `firmware/u1_main/pio_usb/device_registry.cpp`
- Create: `tests/firmware_native/test_pio_usb_device_registry.cpp`
- Create: `tests/firmware_native/fakes/tinyusb_host.hpp`
- Create: `tests/firmware_native/fakes/tinyusb_host.cpp`
- Modify: `firmware/u1_main/pio_usb/backend.cpp`
- Modify: `firmware/u1_main/pio_usb/tinyusb_host_callbacks.cpp`
- Modify: `tests/firmware_native/CMakeLists.txt`

**Interfaces:** A fixed registry keyed by TinyUSB `(dev_addr, instance)` stores mounted state, VID/PID, interface protocol, report-descriptor hash, neutral layouts, logical role and report-in-flight flag. Capacity covers hub address plus at least four downstream devices and eight HID instances; overflow is counted and ignored safely.

- [ ] Write tests for hub-first/device-first mount order, keyboard and mouse on different hub ports, composite receiver instances, duplicate mount callback, registry overflow and unmount by address.
- [ ] Run the registry test and verify it fails because no registry exists.
- [ ] Implement registration and deterministic role ownership: first usable keyboard owns the keyboard slot, first usable mouse owns the mouse slot, extra interfaces remain visible as ignored diagnostics and cannot replace an active source.
- [ ] Keep callbacks constant-time: copy descriptor/report metadata, enqueue a fixed record and return. No parser, normalizer or routing call runs in the callback.
- [ ] After mount processing, arm exactly one `tuh_hid_receive_report(dev_addr, instance)` request. Re-arm only after its preceding callback record has been accepted, preventing duplicate in-flight reads.
- [ ] Run registry tests, complete native CTest, and build the PIO firmware.
- [ ] Commit: `git add firmware/u1_main/pio_usb tests/firmware_native && git commit -m "Track PIO USB HID interfaces safely"`.

### Task 7: Identify HID roles and reuse the existing report-descriptor parser

**Files:**
- Create: `firmware/u1_main/pio_usb/hid_setup.hpp`
- Create: `firmware/u1_main/pio_usb/hid_setup.cpp`
- Create: `tests/firmware_native/test_pio_usb_hid_setup.cpp`
- Modify: `firmware/u1_main/input/hid/report_descriptor.hpp`
- Modify: `firmware/u1_main/input/hid/report_descriptor.cpp`
- Modify: `firmware/u1_main/pio_usb/device_registry.cpp`
- Modify: `tests/firmware_native/CMakeLists.txt`
- Reference fixtures: `tests/vectors/hid_descriptors/*`, `tests/vectors/usb_descriptors/aula_f75_keyboard_report.hex`

**Interfaces:** `classify_hid(protocol, descriptor, length, SourceIdentity&)` tries the neutral keyboard and mouse parsers, accepts one unambiguous supported role, and uses explicit boot layouts only when TinyUSB reports boot keyboard/mouse protocol and a usable descriptor layout is unavailable.

- [ ] Add tests using every existing descriptor fixture plus: empty callback descriptor, report-ID keyboard, five-button wheel mouse, vendor-only HID, malformed descriptor and one descriptor that exposes neither supported role.
- [ ] Run the new test and verify it fails on the missing setup API.
- [ ] Implement classification without copying or forking descriptor parsers. Preserve the parser's current 64-byte report-size and bounded-field rules.
- [ ] Hash the exact report descriptor supplied by TinyUSB; represent no descriptor as absent rather than as SHA-256 of an empty buffer.
- [ ] Ensure a reconnect clears the old identity/layout before classifying the new interface.
- [ ] Run descriptor, normalizer, pipeline and new setup tests; run the fuzz corpus smoke command documented in `docs/release/firmware-build.md`.
- [ ] Commit: `git add firmware/u1_main/pio_usb firmware/u1_main/input tests/firmware_native && git commit -m "Classify TinyUSB HID interfaces with shared parsers"`.

### Task 8: Route keyboard reports through the unchanged mapping/runtime path

**Files:**
- Create: `tests/firmware_native/test_pio_usb_keyboard.cpp`
- Modify: `firmware/u1_main/pio_usb/backend.cpp`
- Modify: `firmware/u1_main/pio_usb/tinyusb_host_callbacks.cpp`
- Modify: `firmware/u1_main/main.cpp`
- Modify: `tests/firmware_native/CMakeLists.txt`

**Interfaces:** TinyUSB keyboard reports become neutral Ready then Report events with callback capture timestamp. Core 1 passes them to the existing keyboard `InputPipeline`, then `Core1Runtime`; no direct HID output occurs in the backend.

- [ ] Add fake-host tests for boot 6KRO press/release, report-ID keyboard, NKRO bitmap, modifiers, Fn-dependent F9–F12 usages, rapid reports, rollover/error usages and a report larger than the declared layout.
- [ ] Verify tests fail because reports are not yet published.
- [ ] Implement report copying, source lookup, timestamp propagation and one-event-per-Core-1-pass draining; re-arm the TinyUSB receive only after the copied report is safely queued.
- [ ] On queue full, synthesize a Fault for the logical keyboard and count overflow; do not discard an owed release and continue as if safe.
- [ ] Prove with tests that bindings, capture, macros and routes still consume identical `InputEvent` values and that U2-bound output uses the existing SPI command path.
- [ ] Run all native tests and build both firmware variants.
- [ ] Commit: `git add firmware/u1_main tests/firmware_native && git commit -m "Route PIO USB keyboard reports"`.

### Task 9: Route mouse movement, wheel, buttons and the Keychron side control

**Files:**
- Create: `tests/firmware_native/test_pio_usb_mouse.cpp`
- Modify: `firmware/u1_main/pio_usb/backend.cpp`
- Modify: `firmware/u1_main/input/pipeline.cpp`
- Modify: `firmware/u1_main/input/pipeline.hpp`
- Modify: `tests/firmware_native/test_input_pipeline.cpp`
- Modify: `tests/firmware_native/CMakeLists.txt`

**Interfaces:** Mouse reports use the existing neutral `MouseReportLayout`. Composite auxiliary reports carry the same VID/PID and instance/endpoint identity used by the current Keychron quirk, without exposing TinyUSB types above the source boundary.

- [ ] Add fake-host tests for X/Y movement, vertical wheel, horizontal pan, buttons 1–5, simultaneous movement+wheel, report IDs, packed 12-bit axes and the exact Keychron 3434:D030 auxiliary report traces already covered by pipeline tests.
- [ ] Verify the tests fail because the PIO backend does not publish mouse reports.
- [ ] Implement mouse publication through the existing mouse pipeline; do not apply a boot layout to a descriptor-defined seven-byte report.
- [ ] Move only the Keychron transport association into the neutral identity path; retain the current report-shape validation so unrelated composite keyboards cannot invent mouse button 4.
- [ ] Test detach while a side button and an ordinary button are held; both releases must be emitted once.
- [ ] Run all native tests and both Pico builds.
- [ ] Commit: `git add firmware/u1_main tests/firmware_native && git commit -m "Route PIO USB mouse reports and wheel"`.

### Task 10: Make detach, stalls, hub resets and reconnects fail safe

**Files:**
- Create: `tests/firmware_native/test_pio_usb_recovery.cpp`
- Modify: `firmware/u1_main/pio_usb/backend.hpp`
- Modify: `firmware/u1_main/pio_usb/backend.cpp`
- Modify: `firmware/u1_main/pio_usb/device_registry.cpp`
- Modify: `firmware/u1_main/main.cpp`
- Modify: `tests/firmware_native/CMakeLists.txt`

**Interfaces:** Every source has a generation counter. Events from an older generation are discarded after its detach releases are delivered. Recovery uses bounded backoff and TinyUSB re-enumeration; it never blocks Core 1 or Core 0.

- [ ] Write state-machine tests for device unmount mid-key, mouse unmount mid-button, whole-hub unmount, report receive refusal, endpoint stall, repeated mount/unmount, stale queued report after reconnect and registry overflow.
- [ ] Verify failure on missing recovery transitions.
- [ ] Implement ordered teardown: stop accepting reports, enqueue exactly one Detached/Fault, drain releases, clear layout/held state, increment generation, then free the registry slot.
- [ ] Add bounded retry/backoff for `tuh_hid_receive_report()` failure. Escalate persistent failure to logical Fault and re-enumeration rather than spinning.
- [ ] Assert in tests that keyboard and mouse sources recover independently, loss of U2 never stops PC1 input, and no backend failure touches flash/profile state.
- [ ] Run native tests under the existing sanitizer/corpus procedure and build both firmware variants.
- [ ] Commit: `git add firmware/u1_main/pio_usb tests/firmware_native && git commit -m "Recover PIO USB input without stuck state"`.

### Task 11: Publish backend-neutral diagnostics without breaking the configurator

**Files:**
- Modify: `protocol/schema.json`
- Modify generated files using: `tools/generate_protocol.py`
- Modify: `firmware/u1_main/config_service.hpp`
- Modify: `firmware/u1_main/config_service.cpp`
- Modify: `firmware/u1_main/main.cpp`
- Modify: `configurator/src/duo_input/device/transactions.py`
- Modify: `configurator/src/duo_input/ui/diagnostics.py`
- Modify: `configurator/src/duo_input/persistence/diagnostic_export.py`
- Modify: `tests/firmware_native/test_config_service.cpp`
- Modify: `configurator/tests/ui/test_diagnostics.py`
- Modify: `configurator/tests/integration/test_diagnostic_export.py`
- Modify: `tests/hil/hil_runner.py`

**Interfaces:** Add an append-only backend identifier (`CH375` or `PIO_USB`) and backend counters to diagnostics. Preserve the existing two `PeripheralPort` records as logical keyboard and mouse slots with the same kind numbering and identity fields. Older configurators must continue parsing the unchanged prefix; the new configurator must accept older firmware with no backend suffix.

- [ ] Add firmware and Python tests for old payload, CH375 payload, PIO payload, no device, ignored extra device, malformed suffix and diagnostic export.
- [ ] Run focused C++ and Python tests; verify failure because no backend suffix exists.
- [ ] Extend `protocol/schema.json`, regenerate identifiers, and make the PIO backend publish mounted/ready/VID/PID/layout/button/descriptor data for the two logical roles.
- [ ] Replace CH375-specific UI labels such as “keyboard channel” with backend-neutral “Keyboard” and “Mouse”; show backend identity only in diagnostics, not in mapping screens.
- [ ] Keep diagnostic responses below the protocol's 1024-byte payload limit and retain the 2048-byte CDC TX FIFO contract.
- [ ] Run `python tools/generate_protocol.py --check`, native CTest, and `python -m pytest configurator/tests tests -q` with `QT_QPA_PLATFORM=offscreen`.
- [ ] Commit: `git add protocol firmware/u1_main configurator tests tools && git commit -m "Expose PIO USB host diagnostics compatibly"`.

### Task 12: Make release tooling build and distinguish both U1 backends

**Files:**
- Modify: `tools/build_release.ps1`
- Modify: `tests/build/test_firmware_artifacts.py`
- Create: `tests/build/test_backend_artifacts.py`
- Modify: `docs/release/firmware-build.md`
- Modify: `docs/release/third-party-licenses.md`

**Interfaces:** `build_release.ps1` gains `-InputBackend CH375|PIO_USB`, defaulting to `CH375` until acceptance. PIO artifacts are named `duo-input-u1-pio-usb-<version>.uf2`; U2 remains the same endpoint artifact. Release notes record backend and all three dependency revisions.

- [ ] Add tests proving CH375 and PIO builds each produce exactly one U1 plus one U2 UF2, stay below configuration flash slots, contain the real input path and exclude synthetic input.
- [ ] Run focused build tests and verify failure on absent backend-aware artifact selection.
- [ ] Implement preset selection and artifact naming without allowing a PIO U1 image to be labelled as CH375 or vice versa.
- [ ] Build `pico-release` and `pico-pio-usb-release`; run artifact tests once with each `DUO_INPUT_PICO_BUILD` directory.
- [ ] Run complete native and Python suites. Build the PySide/Nuitka configurator with `configurator/packaging/nuitka-build.ps1`; this task changes no ordinary UI behaviour.
- [ ] Commit: `git add tools tests/build docs/release && git commit -m "Package selectable CH375 and PIO USB firmware"`.

### Task 13: Add explicit PIO hub hardware-acceptance scenarios

**Files:**
- Create: `tests/hil/scenarios/pio_usb_hub_enumeration.json`
- Create: `tests/hil/scenarios/pio_usb_hub_recovery.json`
- Create: `tests/hil/scenarios/pio_usb_dual_pc_routes.json`
- Create: `docs/release/pio-usb-hardware-checklist-ru.md`
- Modify: `tests/hil/hil_runner.py`
- Modify: `tests/hil/test_hil_runner.py`
- Modify: `docs/release/compatibility-matrix.md`

**Interfaces:** HIL results record backend, Pico SDK/TinyUSB/Pico-PIO-USB revisions, hub model, exact VID/PID and descriptor hash, PC1/PC2 route observations, detach-release observations, power/RGB symptoms and test duration. Unobserved items remain `unmeasured`, never pass.

- [ ] Add validation tests requiring every PIO scenario to state physical setup, action, measurable checks, failure criteria and manual observations that this rig cannot infer.
- [ ] Run HIL runner tests and verify failure on absent scenarios/fields.
- [ ] Implement backend-aware logical role labels and checks for both devices ready, no input gap over 50 ms, latency p95 ≤20 ms, detach releases, endpoint reconnect and stable error counters.
- [ ] Write the Russian checklist in exact order: power-off inspection; continuity/no-short checks; connect PC1 and U1; inspect hub enumeration; connect keyboard; connect mouse; connect U2/PC2; route tests; detach/replug; RGB power gate.
- [ ] Add explicit manual checks for current mouse movement/wheel/two side buttons, keyboard typing/Fn+F9–F12, simultaneous use, every downstream port, hub cable replug, PC2 disconnect/reconnect, Keychron receiver if available, macros and all three route modes.
- [ ] Run `python -m pytest tests/hil -q` and validate all new scenario JSON files with `hil_runner.py --validate-only`.
- [ ] Commit: `git add tests/hil docs/release && git commit -m "Define PIO USB hub hardware acceptance"`.

### Task 14: Flash only with approval and execute the hardware gate

**Files:**
- Modify after measurements: `docs/superpowers/records/2026-09-03-pio-usb-hub-v1-record.md`
- Modify after measurements: `docs/release/compatibility-matrix.md`
- Modify after decision: `CMakePresets.json`
- Modify after decision: `tools/build_release.ps1`

**Interfaces:** No new code before measurement. This task decides whether PIO USB becomes the default; it does not infer success from LEDs or enumeration alone.

- [ ] Re-run native, Python, CH375 Pico, PIO Pico and artifact suites from a clean tree; record commands, counts and hashes.
- [ ] Present the exact PIO U1 UF2 hash to the user and obtain explicit permission to flash U1. Do not flash U2.
- [ ] With power removed, verify the built wiring matches the approved schematic: GP0 D+ and GP1 D- through 22 Ω; PC1 VBUS through 1.1 A PTC; U2 5V absent; common GND; two independent 56 kΩ CC pull-ups.
- [ ] Flash U1, boot with only PC1, and run `pio_usb_hub_enumeration.json`. LEDs count only as power evidence; diagnostics and actual input prove function.
- [ ] Connect U2/PC2 and run keyboard, mouse, wheel, both side buttons, Fn+F9–F12, macros, PC1/PC2/both route tests and `pio_usb_dual_pc_routes.json`.
- [ ] Run `pio_usb_hub_recovery.json`: each peripheral port replug, hub cable replug, U2 disconnect/reconnect, detach while held, rapid mixed input, RGB enabled and disabled.
- [ ] Record failures verbatim. If any stuck input, periodic disconnect, wheel/layout regression, hub reset or brownout occurs, restore the known CH375 U1 image and leave `pico-release` defaulting to CH375.
- [ ] If every acceptance row passes, change the default release backend to `PIO_USB`, retain `-InputBackend CH375`, rebuild both artifacts and run the full verification again.
- [ ] Commit measurements and decision: `git add docs CMakePresets.json tools/build_release.ps1 && git commit -m "Record PIO USB hub hardware acceptance"`.

## Final Verification Gate

- [ ] `git status --short` contains no unexpected changes and no `.deps`, build directory, UF2 or installer binary is tracked.
- [ ] `python tools/generate_protocol.py --check` passes.
- [ ] `cmake --build --preset native` and `ctest --preset native --output-on-failure` pass.
- [ ] The documented Clang ASan/UBSan/libFuzzer campaigns pass for HID/config parsers; corpus smoke alone is not substituted for the campaign.
- [ ] `python -m pytest configurator/tests tests -q` passes with `QT_QPA_PLATFORM=offscreen`.
- [ ] Both `pico-release` and `pico-pio-usb-release` configure and build from their documented locked dependencies.
- [ ] Artifact tests pass against both build directories and both U1 images contain a real input path.
- [ ] Nuitka packaging and installer build pass without redesigning mapping/macro screens.
- [ ] Hardware acceptance has measured keyboard, mouse, wheel, buttons, switching, macros, dual-PC routing, detach/reconnect and power/RGB behaviour.
- [ ] The execution record links every acceptance claim to a command output, artifact hash or hardware observation; no open item is described as passed.

## Reference Basis

- Pico-PIO-USB 0.7.2 release and commit: <https://github.com/sekigon-gonnoc/Pico-PIO-USB/releases/tag/0.7.2>
- Pico-PIO-USB dual host/device example: <https://github.com/sekigon-gonnoc/Pico-PIO-USB/tree/0.7.2/examples/host_hid_to_device_cdc>
- Raspberry Pi Pico SDK 2.3.0 release: <https://github.com/raspberrypi/pico-sdk/releases/tag/2.3.0>
- TinyUSB RP2040 PIO host configuration: <https://github.com/hathach/tinyusb/blob/master/hw/bsp/rp2040/family.c>
- TinyUSB RP2040 PIO build integration: <https://github.com/hathach/tinyusb/blob/master/hw/bsp/rp2040/family.cmake>
