# PIO USB Reference-First Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a separately selectable U1 firmware from the proven Pico-PIO-USB upstream lifecycle, restore Duo Input one measured layer at a time, and preserve the implemented shared clipboard.

**Architecture:** A new `PIO_USB_REFERENCE` backend starts as a maintained byte-for-byte copy of the pinned `host_hid_to_device_cdc` example. TinyUSB host ownership and ordering stay fixed while a bounded callback queue, the existing neutral input pipeline, PC1 output, U2 routing, configuration, diagnostics and the existing clipboard branch are admitted sequentially; each firmware layer must pass a real-hardware gate before the next begins.

**Tech Stack:** C/C++17, Raspberry Pi Pico SDK 2.3.0, TinyUSB, Pico-PIO-USB 0.7.2, CMake/Ninja, Python/pytest, PySide6, Windows PowerShell, RP2040 UF2/HIL.

**Spec:** `docs/superpowers/specs/2026-09-06-pio-usb-reference-first-rebuild-design.md`

## Global Constraints

- Keep `CH375`, the current `PIO_USB`, and the new `PIO_USB_REFERENCE` builds separately reproducible.
- Use the revisions already pinned in `cmake/pio_usb_toolchain_lock.cmake`: Pico SDK `98a542c1a62fb549ffb5d66a3e5892b06276b670`, TinyUSB `86ad6e56c1700e85f1c5678607a762cfe3aa2f47`, Pico-PIO-USB `3c1eec341a5232640e4c00628b889b641af34b28`.
- Use `SOURCE_DATE_EPOCH=1788691431` for byte-identity comparisons across task commits; otherwise the repository intentionally stamps each commit's date into the UF2 and unchanged U2/legacy sources will not hash identically.
- U1 host pins stay GP0 D+ and GP1 D-. U1/U2 SPI stays GP10↔GP10, GP9↔GP9, U1 GP11→U2 GP8, U2 GP11→U1 GP8, common GND.
- U1 is the only peripheral host. Do not change or flash U2 unless the unchanged compatibility contract demonstrably fails.
- No heap allocation, unbounded queue/descriptor, backend-owned pointer across a core boundary, blocking service-loop wait, or routing work in a USB callback.
- USB callbacks may only copy bounded records and perform the upstream-required report re-arm. Parsing, normalization and routing happen in ordinary task context.
- Detach, malformed state, overflow and host fault release every held key/button.
- Accept exactly one logical keyboard and one logical mouse; ignore extras deterministically and expose the reason.
- Every new hardware image requires a clean tree, exact byte size/SHA-256 shown to the user, explicit permission for that exact U1 UF2, and verification that exactly one `RPI-RP2` volume belongs to U1. Never copy a U2 UF2 during these gates.
- A failed hardware slice stops the sequence. Compare only that slice against the immediately preceding passing commit; do not stack the next subsystem on a failed image.
- Do not import the current address-zero recovery or endpoint-pool instrumentation into the reference target unless the new target independently reproduces the same failure.
- Import the shared clipboard from completed milestone commit `ef38e56`; do not reimplement or weaken mutual confirmation, TLS identity, trust, lazy offers or loop prevention.

---

### Task 1: Create and prove the frozen golden reference target

**Files:**
- Create: `firmware/u1_reference/CMakeLists.txt`
- Create: `firmware/u1_reference/main.c`
- Create: `firmware/u1_reference/tusb_config.h`
- Create: `firmware/u1_reference/usb_descriptors.c`
- Create: `tests/build/test_pio_usb_reference_contract.py`
- Modify: `CMakeLists.txt`
- Modify: `CMakePresets.json`
- Modify: `firmware/CMakeLists.txt`
- Modify: `tests/build/test_backend_artifacts.py`
- Modify: `docs/release/firmware-build.md`
- Modify after measurement: `docs/superpowers/records/2026-09-03-pio-usb-hub-v1-record.md`

**Interfaces:**
- Consumes: the locked dependency trees and `.deps/pico-pio-usb/examples/host_hid_to_device_cdc/`.
- Produces: `DUO_INPUT_BACKEND=PIO_USB_REFERENCE`, `DUO_INPUT_BACKEND_PIO_USB_REFERENCE=1`, preset `pico-pio-usb-reference-release`, target `duo_u1_reference`, and `build/pico-pio-usb-reference-release/firmware/u1_reference/duo_u1_reference.uf2`.

- [ ] **Step 1: Write RED build-contract tests**

```python
def test_reference_preset_selects_only_the_reference_backend():
    presets = json.loads((ROOT / "CMakePresets.json").read_text(encoding="utf-8"))
    selected = next(p for p in presets["configurePresets"]
                    if p["name"] == "pico-pio-usb-reference-release")
    assert selected["cacheVariables"]["DUO_INPUT_BACKEND"] == "PIO_USB_REFERENCE"

def test_reference_sources_are_maintained_outside_build_output():
    assert (ROOT / "firmware/u1_reference/main.c").is_file()
```

Also require the reference ELF to contain `tuh_task`, `tuh_hid_receive_report`, `tud_task` and `tud_cdc_write`, while excluding `Ch375Device::tick`, `InputPipeline::on_event` and `PioUsbBackend::task`.

- [ ] **Step 2: Run RED**

```powershell
python -m pytest tests/build/test_pio_usb_reference_contract.py -q
```

Expected: FAIL because the backend, preset and sources do not exist.

- [ ] **Step 3: Add the third backend and target**

Make backend validation exact and load the locked PIO toolchain for both PIO values:

```cmake
set(_duo_input_backends CH375 PIO_USB PIO_USB_REFERENCE)
set_property(CACHE DUO_INPUT_BACKEND PROPERTY STRINGS ${_duo_input_backends})
if(NOT DUO_INPUT_BACKEND IN_LIST _duo_input_backends)
    message(FATAL_ERROR "Unknown DUO_INPUT_BACKEND '${DUO_INPUT_BACKEND}'")
endif()
```

Only `PIO_USB_REFERENCE` adds `firmware/u1_reference`; CH375/current PIO still add `firmware/u1_main`. U2 remains unchanged in every preset.

- [ ] **Step 4: Vendor the exact working reference**

Create byte-for-byte maintained copies:

```text
.deps/pico-pio-usb/examples/host_hid_to_device_cdc/host_hid_to_device_cdc.c -> firmware/u1_reference/main.c
.deps/pico-pio-usb/examples/host_hid_to_device_cdc/tusb_config.h            -> firmware/u1_reference/tusb_config.h
.deps/pico-pio-usb/examples/host_hid_to_device_cdc/usb_descriptors.c       -> firmware/u1_reference/usb_descriptors.c
```

The lifecycle must remain:

```c
void core1_main(void) {
    sleep_ms(10);
    pio_usb_configuration_t cfg = PIO_USB_DEFAULT_CONFIG;
    tuh_configure(1, TUH_CFGID_RPI_PIO_USB_CONFIGURATION, &cfg);
    tuh_init(1);
    while (true) tuh_task();
}

int main(void) {
    set_sys_clock_khz(120000, true);
    sleep_ms(10);
    multicore_reset_core1();
    multicore_launch_core1(core1_main);
    tud_init(0);
    while (true) { tud_task(); tud_cdc_write_flush(); }
}
```

Link exactly the upstream host/device implementation set. Do not link `duo_common` or U1 production sources yet.

- [ ] **Step 5: Verify GREEN and existing-build stability**

```powershell
cmake --preset pico-pio-usb-reference-release
cmake --build --preset pico-pio-usb-reference-release
python -m pytest tests/build/test_pio_usb_reference_contract.py tests/build/test_backend_artifacts.py -q
cmake --build --preset pico-release
cmake --build --preset pico-pio-usb-release
```

The contract test compares SHA-256 of the three maintained source copies to the pinned originals. Reconfigure the existing CH375/current-PIO builds with `SOURCE_DATE_EPOCH=1788691431`; their U1 hashes must match baselines built with that same epoch before the task. Record ordinary commit-stamped hashes separately and never compare them across different commits.

- [ ] **Step 6: Commit the reproducible golden candidate**

```powershell
git add CMakeLists.txt CMakePresets.json firmware tests/build docs/release
git commit -m "Add the golden PIO USB reference target"
```

- [ ] **Step 7: Hardware gate the clean-tree golden image and record it**

Present exact size/hash and flash only after approval. PASS requires hub
enumeration, keyboard and mouse HID mount lines, keyboard characters, mouse
movement and button reports, simultaneous use, and re-enumeration after each
device replug. Record the golden hash. A failure stops the plan at Task 1.

**Not a wheel row.** This gate originally demanded wheel reports too. It cannot
have them: TinyUSB puts HID interfaces in boot protocol by default
(`hid_host.c`, `_hidh_default_protocol = HID_PROTOCOL_BOOT`) and the upstream
example never changes that, so the mouse transmits the three-byte boot report -
buttons, X, Y - and no wheel byte exists to report. Measured 2026-09-06: 1278
consecutive reports at `len=3`, none with a non-zero fourth byte. The example
prints `report->wheel` by casting that buffer to a five-field struct, which
reads past the end of the data; the zeros it printed were an out-of-bounds read,
not a measurement. In report protocol the same mouse sends eight bytes with the
wheel at offset 6 and a report ID in byte 0, so reading it correctly requires
descriptor parsing. Wheel, side buttons and everything else beyond the boot
layout are therefore verified in Task 3, where the neutral pipeline parses
report descriptors. See the record for the captured bytes.

Commit only the measurement record:

```powershell
git add docs/superpowers/records/2026-09-03-pio-usb-hub-v1-record.md
git commit -m "Record the golden reference hardware gate"
```

---

### Task 2: Replace callback printing with a bounded event bridge

**Files:**
- Create: `firmware/u1_reference/callback_queue.hpp`
- Create: `firmware/u1_reference/callback_queue.cpp`
- Create: `firmware/u1_reference/host_callbacks.cpp`
- Create: `tests/firmware_native/test_reference_callback_queue.cpp`
- Modify: `firmware/u1_reference/main.c`
- Modify: `firmware/u1_reference/CMakeLists.txt`
- Modify: `tests/firmware_native/CMakeLists.txt`
- Modify: `tests/build/test_pio_usb_reference_contract.py`
- Modify after measurement: `docs/superpowers/records/2026-09-03-pio-usb-hub-v1-record.md`

**Interfaces:**

```cpp
enum class ReferenceCallbackKind : std::uint8_t { Mount, Unmount, Report, Overflow };
struct ReferenceCallbackRecord {
    ReferenceCallbackKind kind{};
    std::uint8_t dev_addr{};
    std::uint8_t instance{};
    std::uint16_t vid{};
    std::uint16_t pid{};
    std::uint16_t descriptor_size{};
    std::uint16_t report_size{};
    std::uint32_t received_us{};
    std::array<std::uint8_t, 256> descriptor{};
    std::array<std::uint8_t, 64> report{};
};
bool reference_capture(const ReferenceCallbackRecord& record);
bool reference_take(ReferenceCallbackRecord& record);
std::uint32_t reference_overflows();
```

- [ ] **Step 1: Write RED native tests**

Cover FIFO order, 256-byte descriptor bound, 64-byte report bound, full queue, explicit overflow, mount/unmount identity and copy-by-value.

```cpp
TEST_CASE("report bytes are owned by the callback queue") {
    std::uint8_t bytes[] = {1, 2, 3};
    ReferenceCallbackRecord captured{};
    captured.kind = ReferenceCallbackKind::Report;
    captured.dev_addr = 2;
    captured.report_size = 3;
    std::copy_n(bytes, 3, captured.report.begin());
    REQUIRE(reference_capture(captured));
    bytes[0] = 99;
    ReferenceCallbackRecord event{};
    REQUIRE(reference_take(event));
    CHECK_EQ(event.report[0], 1);
}
```

- [ ] **Step 2: Run RED**

Run `cmake --build --preset native`; expect compilation failure on the missing queue API.

- [ ] **Step 3: Implement bounded callbacks**

Use fixed power-of-two storage with acquire/release indices. Move only the
upstream callback definitions from `main.c` into `host_callbacks.cpp`, exporting
them with `extern "C"`; leave `main()` and `core1_main()` in C and unchanged.
Callbacks copy only bounded data; reports timestamp with `time_us_32()` and
then perform upstream's `tuh_hid_receive_report(dev_addr, instance)` re-arm. No
parser, CDC write, allocation, normalization or routing runs in a callback.

- [ ] **Step 4: Drain a minimal CDC trace in task context**

Keep host initialization and Core 1 `tuh_task()` ownership unchanged. After each `tuh_task()` return, move at most one record to a fixed Core1→Core0 trace queue. Core 0 prints kind/address/instance/length and a bounded hex report prefix.

- [ ] **Step 5: Verify GREEN**

```powershell
cmake --build --preset native
ctest --preset native --output-on-failure
cmake --build --preset pico-pio-usb-reference-release
python -m pytest tests/build/test_pio_usb_reference_contract.py -q
```

The ELF still excludes production pipeline/SPI/config symbols.
Replace Task 1's whole-file `main.c` hash assertion with executable lifecycle
and ELF-order assertions, because extracting callbacks intentionally changes
that file. Continue hashing `tusb_config.h` and `usb_descriptors.c` against the
pinned originals.

- [ ] **Step 6: Commit the event-bridge candidate**

```powershell
git add firmware/u1_reference tests
git commit -m "Add a bounded bridge to the working USB host"
```

- [ ] **Step 7: Hardware gate the clean-tree bridge and record it**

After exact-hash approval, repeat Task 1's mount/report/replug matrix and rapid simultaneous input. `reference_overflows()` must remain zero. Failure rolls back to the Task 1 hash and stops.

```powershell
git add docs/superpowers/records/2026-09-03-pio-usb-hub-v1-record.md
git commit -m "Record the reference event-bridge hardware gate"
```

---

### Task 3: Restore the neutral pipeline and PC1 output

**Files:**
- Create: `firmware/u1_reference/source_adapter.hpp`
- Create: `firmware/u1_reference/source_adapter.cpp`
- Create: `firmware/u1_reference/main.cpp`
- Create: `tests/firmware_native/test_reference_source_adapter.cpp`
- Delete: `firmware/u1_reference/main.c`
- Modify: `firmware/u1_reference/CMakeLists.txt`
- Modify: `tests/firmware_native/CMakeLists.txt`
- Modify: `tests/build/test_pio_usb_reference_contract.py`
- Modify after measurement: `docs/superpowers/records/2026-09-03-pio-usb-hub-v1-record.md`

**Interfaces:**

```cpp
class ReferenceSourceAdapter {
public:
    void consume(const ReferenceCallbackRecord&, std::uint32_t now_us);
    bool take_event(input::SourceEvent&, input::SourceIdentity&);
    std::uint8_t logical_port(input::DeviceKind) const;
};
```

- [ ] **Step 1: Write RED equivalence tests**

Use literal boot/report-ID/NKRO keyboard, five-button/wheel/pan/packed-axis mouse, composite-extra, Keychron `3434:D030`, malformed/oversized and detach-while-held fixtures. Assert exact `SourceEvent`/identity values independently of adapter helpers.

- [ ] **Step 2: Run RED**

Run the focused native target; expect failure on missing `ReferenceSourceAdapter`.

- [ ] **Step 3: Implement task-context conversion**

Reuse the neutral descriptor parsers. First supported keyboard/mouse owns its role; mount emits `Ready`, report emits `Report`/`AuxiliaryReport`, and unmount/overflow emits `Detached`/`Fault` before identity is cleared.

- [ ] **Step 4: Add existing PC1 runtime without changing host lifecycle**

Preserve `clock → settle → launch → configure/init → tuh_task`. Link existing USB descriptors/service, `InputPipeline`, normalizers, mapping, macros, `Core1Runtime` and `OutputRuntime`, but no SPI, config, flash, watchdog or current `pio_usb/backend.cpp`.

```cpp
for (;;) {
    tuh_task();
    drain_one_callback_into_source();
    drain_one_source_into_pipeline();
}
```

- [ ] **Step 5: Verify GREEN**

Run native CTest, focused adapter/pipeline/runtime tests, all three Pico builds and the reference ELF contract. The reference ELF now contains `InputPipeline::on_event` but excludes `SpiMaster`, `ConfigService`, `PioUsbBackend` and CH375.

- [ ] **Step 6: Commit the PC1 candidate**

```powershell
git add firmware/u1_reference tests
git commit -m "Route the reference host through PC1 input runtime"
```

- [ ] **Step 7: Hardware gate PC1 parity and record it**

After hash approval, verify typing, modifiers, Fn F9–F12, movement, wheel, five buttons/side buttons, macros, rapid simultaneous input and detach-held release on PC1. All queue/host error counters remain zero.

```powershell
git add docs/superpowers/records/2026-09-03-pio-usb-hub-v1-record.md
git commit -m "Record reference-host PC1 parity"
```

---

### Task 4: Restore SPI, U2 and all route modes

**Files:**
- Modify: `firmware/u1_reference/main.cpp`
- Modify: `firmware/u1_reference/CMakeLists.txt`
- Modify: `tests/firmware_native/test_core1_runtime.cpp`
- Modify: `tests/firmware_native/test_output_runtime.cpp`
- Create: `tests/build/test_reference_routing_contract.py`
- Modify after measurement: `docs/superpowers/records/2026-09-03-pio-usb-hub-v1-record.md`

**Interfaces:**
- Consumes: existing `CoreBridge`, `SpiMaster`, output commands and route state.
- Produces: the unchanged PC1/PC2/both routing and U1→U2 SPI contract.

- [ ] **Step 1: Write RED routing/build tests**

Extend native tests with literal event sequences for PC1-only, PC2-only and both routes, route changes while held, U2 loss while PC1 continues, and one release-all on reconnect. Require the reference ELF to contain existing SPI send/poll symbols and output handlers.

```python
def test_reference_elf_links_routing_but_not_old_host_backends(symbols):
    assert contains(symbols, "SpiMaster")
    assert contains(symbols, "CoreBridge")
    assert not contains(symbols, "Ch375Device")
    assert not contains(symbols, "PioUsbBackend")
```

- [ ] **Step 2: Run RED**

Run focused native/build tests; expect failure because the reference target has no SPI bridge.

- [ ] **Step 3: Add existing SPI/CoreBridge on Core 0**

Core 1 publishes the existing bounded output commands. Core 0 alone owns `SpiMaster::begin/poll` and U1 device output. Do not move `tuh_task()`, host init or callback work to Core 0. Initialize SPI after the 120 MHz clock is final; do not recreate the old clock handoff barrier.

- [ ] **Step 4: Verify GREEN**

```powershell
cmake --build --preset native
ctest --preset native --output-on-failure
cmake --build --preset pico-pio-usb-reference-release
python -m pytest tests/build/test_reference_routing_contract.py tests/build/test_backend_artifacts.py -q
```

Reconfigure/rebuild U2 with `SOURCE_DATE_EPOCH=1788691431` and prove its UF2 hash equals Task 1's fixed-epoch U2 hash. Also verify `git diff` from Task 1 changes no `firmware/u2_endpoint` source.

- [ ] **Step 5: Commit the routing candidate**

```powershell
git add firmware/u1_reference tests
git commit -m "Restore dual-PC routing on the reference host"
```

- [ ] **Step 6: Hardware gate dual-PC routing and record it**

Flash only the approved U1. Connect the unchanged U2/PC2 image. Verify keyboard, mouse, wheel, buttons, macros and simultaneous input in PC1-only, PC2-only and both modes; disconnect/reconnect PC2 and detach while held. PC1 must remain live throughout U2 loss.

```powershell
git add docs/superpowers/records/2026-09-03-pio-usb-hub-v1-record.md
git commit -m "Record reference-host dual-PC routing"
```

---

### Task 5: Restore configuration, storage and bounded diagnostics

**Files:**
- Modify: `firmware/u1_reference/main.cpp`
- Modify: `firmware/u1_reference/CMakeLists.txt`
- Modify: `firmware/u1_main/config_service.hpp`
- Modify: `firmware/u1_main/config_service.cpp`
- Modify: `protocol/schema.json`
- Modify generated protocol files using: `python tools/generate_protocol.py`
- Modify: `configurator/src/duo_input/device/transactions.py`
- Modify: `configurator/src/duo_input/persistence/diagnostic_export.py`
- Modify: `configurator/src/duo_input/ui/diagnostics.py`
- Modify: `tests/firmware_native/test_config_service.cpp`
- Modify: `configurator/tests/device/test_device_service.py`
- Modify: `configurator/tests/integration/test_diagnostic_export.py`
- Modify: `configurator/tests/ui/test_diagnostics.py`
- Modify after measurement: `docs/superpowers/records/2026-09-03-pio-usb-hub-v1-record.md`

**Interfaces:**
- Consumes: existing config/profile/A-B flash services and the diagnostics prefix.
- Produces: backend `PIO_USB_REFERENCE` plus appended fixed fields `callback_overflows`, `ignored_interfaces`, `keyboard_ready`, `mouse_ready`; all preceding bytes keep their offsets and meaning.

- [ ] **Step 1: Write RED compatibility tests**

Use a literal current diagnostics reply as the prefix. Assert reference diagnostics begin with the exact bytes, append only the four fields, remain below 1024 bytes, and parse both new-configurator/old-firmware and old-prefix/new-firmware directions.

```cpp
CHECK(std::equal(old_reply.begin(), old_reply.end(), reference_reply.begin()));
CHECK(reference_reply.size() <= 1024);
```

- [ ] **Step 2: Run RED**

Run focused firmware/configurator tests; expect failure because the reference target exposes none of these services.

- [ ] **Step 3: Add existing services on Core 0**

Link the existing config, profile, flash and diagnostics services. A configuration write must send release-all before the existing safe flash lockout. No config/flash action runs in a host callback. Do not link old endpoint-pool recovery/observability code.

- [ ] **Step 4: Generate and verify GREEN**

```powershell
python tools/generate_protocol.py
python tools/generate_protocol.py --check
cmake --build --preset native
ctest --preset native --output-on-failure
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest configurator/tests tests -q
cmake --build --preset pico-pio-usb-reference-release
```

- [ ] **Step 5: Commit the configuration candidate**

```powershell
git add firmware protocol configurator tests tools
git commit -m "Restore configuration on the reference host"
```

- [ ] **Step 6: Hardware gate configuration parity and record it**

Flash the approved U1, read profiles, write a temporary mapping/macro/route, reboot, prove persistence, restore the original profile, and repeat Task 4 input while polling diagnostics. No drop/stuck state is allowed.

```powershell
git add docs/superpowers/records/2026-09-03-pio-usb-hub-v1-record.md
git commit -m "Record reference-host configuration parity"
```

---

### Task 6: Integrate the completed shared clipboard milestone

**Files:**
- Merge through: `ef38e56 Cover the always-tray behavior and retire the stale off-quits test`
- Add: `configurator/src/duo_input/clipboard/*`
- Add: `configurator/src/duo_input/persistence/autostart.py`
- Add: `configurator/src/duo_input/ui/clipboard_page.py`
- Add: `configurator/src/duo_input/ui/tray.py`
- Add: `configurator/tests/clipboard/*`
- Modify via merge: `configurator/src/duo_input/app.py`
- Modify via merge: `configurator/src/duo_input/ui/main_window.py`
- Modify via merge: translations, build requirements, packaging/UI tests and `docs/user/clipboard-ru.md`
- Preserve current: `docs/superpowers/specs/2026-09-02-pio-usb-hub-v1-design.md`
- Modify after measurement: `docs/superpowers/records/2026-09-03-pio-usb-hub-v1-record.md`

**Interfaces:**
- Consumes: completed clipboard milestone `ef38e56`, current configurator and stable Tasks 1–5.
- Produces: existing mutual-confirmation/TLS/trust clipboard service, lazy Windows offers, UI/tray/autostart and loop prevention on the combined branch.

- [ ] **Step 1: Establish baseline and preview the merge**

Run current suites and save counts, then:

```powershell
git merge-tree --write-tree --messages HEAD ef38e56
```

Expected conflicts are exactly:

```text
configurator/src/duo_input/resources/translations/duo_input_en.qm
configurator/src/duo_input/resources/translations/duo_input_ru.qm
docs/superpowers/specs/2026-09-02-pio-usb-hub-v1-design.md
```

Stop and record any additional conflict before resolving it.

- [ ] **Step 2: Merge only the completed clipboard milestone**

```powershell
git merge --no-ff ef38e56
```

Do not merge `f190557`, `04f6dc5` or `bd59c49`; those are later file-transfer design/spike commits.

- [ ] **Step 3: Resolve known conflicts deterministically**

- Preserve the current PIO USB design document.
- Check the automatically merged `.ts` sources contain both clipboard and current diagnostics strings.
- Regenerate `.qm` using `python tools/update_translations.py`; never select a stale binary side.
- Require `git diff --name-only --diff-filter=U` to print nothing.

- [ ] **Step 4: Run clipboard and integration gates**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest configurator/tests/clipboard configurator/tests/ui/test_clipboard_page.py configurator/tests/ui/test_runtime_wiring.py configurator/tests/ui/test_tray_lifecycle.py -q
python -m pytest configurator/tests tests -q
python tools/update_translations.py --check
python tools/generate_protocol.py --check
cmake --build --preset native
ctest --preset native --output-on-failure
cmake --build --preset pico-pio-usb-reference-release
```

The UI test must click the real clipboard toggle and observe runtime state; it must not write `clipboard/enabled` directly. Verify the runtime environment remains owned by a live `QObject` so PySide6 cannot collect signal receivers.

- [ ] **Step 5: Commit the resolved clipboard merge**

```powershell
git add configurator docs/superpowers/specs/2026-09-02-pio-usb-hub-v1-design.md
git commit -m "Integrate shared clipboard with the reference host"
```

- [ ] **Step 6: Hardware gate clipboard concurrently with routing**

On both PCs verify mutual pairing confirmation, pinned identity after restart, text/image transfer both directions, no echo loop, refusal of private/unsupported content, approved tray/autostart behaviour, recovery after link drop, and uninterrupted PC1/PC2/both keyboard/mouse routing during clipboard transfers.

```powershell
git add docs/superpowers/records/2026-09-03-pio-usb-hub-v1-record.md
git commit -m "Record clipboard parity on the reference host"
```

---

### Task 7: Run recovery/soak acceptance and package the backend

**Files:**
- Modify: `tools/build_release.ps1`
- Modify: `tests/build/test_build_release_plan.py`
- Modify: `tests/build/test_backend_artifacts.py`
- Modify: `tests/build/test_firmware_artifacts.py`
- Modify: `docs/release/firmware-build.md`
- Modify: `docs/release/compatibility-matrix.md`
- Modify: `docs/release/pio-usb-hardware-checklist-ru.md`
- Modify: `docs/superpowers/records/2026-09-03-pio-usb-hub-v1-record.md`
- Modify after complete acceptance only: `CMakePresets.json`

**Interfaces:**
- Consumes: accepted Tasks 1–6 and existing HIL scenarios.
- Produces: `build_release.ps1 -InputBackend PIO_USB_REFERENCE`, `duo-input-u1-pio-usb-reference-<version>.uf2`, the complete record, and the release-default decision.

- [ ] **Step 1: Write RED release/artifact tests**

```python
def test_reference_release_dry_run_names_the_reference_image():
    result = subprocess.run([
        "pwsh", "-File", "tools/build_release.ps1", "-DryRun",
        "-Version", "1.2.3", "-InputBackend", "PIO_USB_REFERENCE",
    ], cwd=ROOT, text=True, capture_output=True, check=True)
    assert "duo-input-u1-pio-usb-reference-1.2.3.uf2" in result.stdout

def test_reference_elf_has_only_the_new_input_path(reference_symbols):
    assert contains(reference_symbols, "InputPipeline::on_event")
    assert not contains(reference_symbols, "Ch375Device::tick")
    assert not contains(reference_symbols, "PioUsbBackend::task")
```

Run focused tests; expect failure because release tooling accepts only `CH375|PIO_USB`.

- [ ] **Step 2: Add reference artifact selection and guards**

Extend `-InputBackend` to the exact three values. Select the reference preset/name only for `PIO_USB_REFERENCE`. U2 continues to ship from the CH375 `pico-release` build so its deployed image remains byte-identical.

- [ ] **Step 3: Run full software verification**

```powershell
python tools/generate_protocol.py --check
python tools/update_translations.py --check
cmake --build --preset native
ctest --preset native --output-on-failure
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest configurator/tests tests -q
cmake --preset pico-release
cmake --build --preset pico-release
cmake --preset pico-pio-usb-release
cmake --build --preset pico-pio-usb-release
cmake --preset pico-pio-usb-reference-release
cmake --build --preset pico-pio-usb-reference-release
python -m pytest tests/build -q
```

Run documented ASan/UBSan/libFuzzer campaigns plus Nuitka/installer packaging. Confirm dependencies, build trees, UF2s and installers are untracked.

- [ ] **Step 4: Commit the release-tooling candidate**

```powershell
git add tools tests/build docs/release
git commit -m "Package the PIO USB reference backend"
```

- [ ] **Step 5: Run hardware recovery/soak without speculative recovery code**

Measure 20 cold boots with both devices attached; every hub port with each device; keyboard, mouse and hub replug; detach while held; 30 minutes rapid simultaneous input; PC2/U2 disconnect/reconnect; RGB on/off; and clipboard transfer during routing.

If all rows pass, add no recovery. If any failure occurs, record it verbatim and stop this plan to create a focused bug plan with a failing test from that observation. Do not import the old address-zero recovery.

- [ ] **Step 6: Decide the release default and commit the record**

Only after every software/hardware row passes, make `PIO_USB_REFERENCE` the default while retaining explicit CH375/current-PIO choices. Rebuild and guard all artifacts. Otherwise leave CH375 default and record reference PIO as experimental.

```powershell
git add docs CMakePresets.json
git commit -m "Record PIO USB reference acceptance"
```

## Final Verification Gate

- [ ] `git status --short` contains no unexpected changes and no dependency/build/artifact output is tracked.
- [ ] Task 1's golden source/hash and every later hardware-tested U1 hash are recorded.
- [ ] Each hardware slice passed before the next task began.
- [ ] Native CTest, Python suites, protocol/translation checks, all three Pico builds and artifact tests pass.
- [ ] Sanitizer/fuzzer campaigns and configurator packaging pass.
- [ ] Keyboard, mouse, wheel, buttons, Fn keys, macros, detach/replug and all routes pass on both PCs. Wheel and side buttons are verified from Task 3 onward, once report descriptors are parsed; the boot-protocol reference in Task 1 cannot carry them.
- [ ] Shared clipboard pairing, trust, bidirectional transfer, loop prevention, tray/autostart and link recovery pass concurrently with input routing.
- [ ] U2 source and deployed image remain unchanged unless a separately recorded compatibility defect required action.
- [ ] No speculative recovery from the failed PIO path is present in the reference target.
- [ ] Release default changes only after every measured row passes.
