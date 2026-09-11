# Unified Pico-PIO-USB SOF Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ordinary and flash-time USB service share one 1 ms SOF deadline, expose measured frame intervals, and prove repeated configuration writes do not strand HID input.

**Architecture:** Pico-PIO-USB owns one SRAM-resident frame scheduler used by both its repeating timer and the flash keepalive entry point. U1 continuously offers flash service while XIP is unavailable; the dependency suppresses early calls, records actual minimum/maximum intervals, and exposes those readings through the append-only diagnostics protocol.

**Tech Stack:** RP2040 Pico SDK, Pico-PIO-USB C, TinyUSB, C++17 firmware, Python/PySide6 configurator, CMake/Ninja, pytest/CTest.

**Spec:** `docs/superpowers/specs/2026-09-11-unified-pio-usb-sof-scheduler-design.md`

## Global Constraints

- All flash-time reachable code and data must reside in SRAM or boot ROM.
- No TinyUSB callback may execute while XIP is unavailable.
- A late scheduler observation emits at most one frame; missed frames are never replayed as a burst.
- Host observation is length-prefixed and append-only; older block sizes remain readable.
- Do not change the separate `u1_reference/host_callbacks.cpp` receive-retry defect in this task.
- Hardware flashing requires approval of the exact UF2 SHA-256.

---

### Task 1: Give Pico-PIO-USB sole ownership of the SOF deadline

**Files:**
- Create: `patches/pico-pio-usb/0006-duo-input-unified-sof-scheduler.patch`
- Modify: `.deps/pico-pio-usb/src/pio_usb_host.c`
- Modify: `.deps/pico-pio-usb/src/pio_usb.h`
- Modify: `firmware/u1_main/pico_flash.cpp`
- Modify: `firmware/u1_main/flash_park.hpp`
- Modify: `tests/firmware_native/test_flash_park.cpp`
- Modify: `tests/build/test_pio_usb_toolchain_patches.py`
- Modify: `tests/build/test_pio_usb_firmware_contract.py`
- Modify: `tests/build/pio_usb_flash_contract.py`

**Interfaces:**
- Consumes: `pio_usb_host_frame()`, `pio_usb_host_flash_keepalive()`, and RP2040 `timer_hw->timerawl`.
- Produces: `uint32_t pio_usb_host_sof_interval_min_us(void)` and `uint32_t pio_usb_host_sof_interval_max_us(void)`; both return zero until two wire-frame service invocations have occurred.

- [ ] **Step 1: Write failing scheduler ownership tests**

Add a patch-contract test that reconstructs the `pio_usb_host.c` post-image and verifies a single RAM function is reached by both entry points, plus an ELF/source contract that rejects application-owned cadence:

```python
def test_ordinary_and_flash_frames_share_one_due_gate():
    source = _patched_file(UNIFIED_SOF_PATCH, "src/pio_usb_host.c")
    gate = "pio_usb_host_service_frame_if_due"
    ordinary = source[source.index("(pio_usb_host_frame)("):source.index("(pio_usb_host_flash_keepalive)(")]
    flash = source[source.index("(pio_usb_host_flash_keepalive)("):source.index("(sof_timer)(")]
    assert gate in ordinary
    assert gate in flash

def test_application_flash_loop_owns_no_sof_clock():
    source = (REPOSITORY_ROOT / "firmware/u1_main/pico_flash.cpp").read_text(encoding="utf-8")
    service = source[source.index("service_core1_flash_window"):source.index("finish_core1_flash_window")]
    assert "FlashSofCadence" not in source
    assert "pio_usb_host_flash_keepalive();" in service
```

The production mutation these tests catch is reintroducing independent normal/flash deadlines, which permits compressed adjacent frames.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m pytest tests/build/test_pio_usb_toolchain_patches.py tests/build/test_pio_usb_firmware_contract.py -q
```

Expected: FAIL because patch `0006` and the common due gate do not exist, and because `pico_flash.cpp` still owns `FlashSofCadence`.

- [ ] **Step 3: Implement the common RAM-resident scheduler**

In `pio_usb_host.c`, introduce SRAM state for `next_sof_us`, the preceding actual frame timestamp, and min/max interval. Implement one no-inline RAM helper with this behavior:

```c
static bool __no_inline_not_in_flash_func(pio_usb_host_service_frame_if_due)(void) {
  uint32_t const now_us = timer_hw->timerawl;
  if (sof_deadline_started && (int32_t)(now_us - next_sof_us) < 0) return false;
  if (sof_deadline_started) {
    uint32_t const interval = now_us - last_sof_us;
    if (sof_interval_min_us == 0 || interval < sof_interval_min_us) sof_interval_min_us = interval;
    if (interval > sof_interval_max_us) sof_interval_max_us = interval;
  }
  sof_deadline_started = true;
  last_sof_us = now_us;
  next_sof_us = now_us + 1000u;
  /* existing SOF/keepalive send, endpoint service, count and packet update */
  return true;
}
```

Make both `pio_usb_host_frame()` and `pio_usb_host_flash_keepalive()` call this helper. Preserve connection checking in ordinary service if required by extracting only the common due decision and completed frame accounting; the flash closure must not acquire an XIP call. Remove `FlashSofCadence` and its tests, and call `pio_usb_host_flash_keepalive()` on every pass of the RAM park loop.

- [ ] **Step 4: Verify GREEN and the recursive SRAM closure**

Run:

```powershell
cmake --build build/pico-pio-usb-release --target duo_u1_main -j 6
cmake --build build/pico-pio-usb-reference-release --target duo_u1_reference -j 6
python -m pytest tests/build/test_pio_usb_toolchain_patches.py tests/build/test_pio_usb_firmware_contract.py tests/build/test_pio_usb_reference_contract.py -q
```

Expected: PASS, including `assert_flash_path_sram_safe` for both ELFs.

- [ ] **Step 5: Commit the scheduler behavior**

```powershell
git add patches/pico-pio-usb/0006-duo-input-unified-sof-scheduler.patch firmware/u1_main/pico_flash.cpp firmware/u1_main/flash_park.hpp tests/firmware_native/test_flash_park.cpp tests/build/test_pio_usb_toolchain_patches.py tests/build/test_pio_usb_firmware_contract.py tests/build/pio_usb_flash_contract.py
git commit -m "fix: unify normal and flash USB frame scheduling"
```

---

### Task 2: Carry actual SOF interval measurements into diagnostics

**Files:**
- Modify: `firmware/u1_main/pio_usb/backend.hpp`
- Modify: `firmware/u1_main/pio_usb/backend.cpp`
- Modify: `firmware/u1_main/pio_usb/host_observation_mapping.hpp`
- Modify: `firmware/u1_main/config_service.hpp`
- Modify: `firmware/u1_main/config_service.cpp`
- Modify: `configurator/src/duo_input/device/transactions.py`
- Modify: `configurator/src/duo_input/persistence/diagnostic_export.py`
- Modify: `configurator/tests/device/test_device_service.py`
- Modify: `configurator/tests/integration/test_diagnostic_export.py`
- Modify: `configurator/tests/ui/test_diagnostics.py`
- Modify: `tests/test_protocol_docs.py`
- Modify: `docs/protocol/compatibility.md`

**Interfaces:**
- Consumes: `pio_usb_host_sof_interval_min_us()` and `pio_usb_host_sof_interval_max_us()` from Task 1.
- Produces: append-only `HostObservation.sof_interval_min_us` and `HostObservation.sof_interval_max_us`, serialized as trailing little-endian `u32` values and represented as `int | None` in Python.

- [ ] **Step 1: Write failing parser and export tests**

Append literal values `975` and `1128` to an existing full host-observation fixture and assert the consumer-visible model and exported labels:

```python
assert observation.sof_interval_min_us == 975
assert observation.sof_interval_max_us == 1128
assert report.host_stack["Shortest actual SOF interval (us)"] == "975"
assert report.host_stack["Longest actual SOF interval (us)"] == "1128"
```

Also assert an old-length fixture yields `None` for both fields and extend the protocol serializer-size test so two missing `put_u32` calls fail it.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m pytest configurator/tests/device/test_device_service.py configurator/tests/integration/test_diagnostic_export.py configurator/tests/ui/test_diagnostics.py tests/test_protocol_docs.py -q
```

Expected: FAIL because the dataclass, parser, serializer, and export labels do not contain the two fields.

- [ ] **Step 3: Append firmware and configurator fields**

Append two `uint32_t` members to both firmware observation structs, populate them from the Pico-PIO-USB getters in `PioUsbBackend::observation()`, copy them in `to_wire_host_observation`, add two `put_u32` calls at the end of `write_host_observation`, and increase `kHostObservationBytes` by eight. Extend the Python parser format loop with two trailing `"<I"` entries and map the last two extension indices to the dataclass. Add the exact labels from Step 1 to `_HOST_STACK_ROWS` and document the append-only wire fields.

- [ ] **Step 4: Verify GREEN and compatibility**

Run the focused command from Step 2. Expected: PASS, including older host-block fixtures, CH375's zero-length host block, diagnostics UI rows, and exact serializer width.

- [ ] **Step 5: Commit diagnostics**

```powershell
git add firmware/u1_main/pio_usb/backend.hpp firmware/u1_main/pio_usb/backend.cpp firmware/u1_main/pio_usb/host_observation_mapping.hpp firmware/u1_main/config_service.hpp firmware/u1_main/config_service.cpp configurator/src/duo_input/device/transactions.py configurator/src/duo_input/persistence/diagnostic_export.py configurator/tests/device/test_device_service.py configurator/tests/integration/test_diagnostic_export.py configurator/tests/ui/test_diagnostics.py tests/test_protocol_docs.py docs/protocol/compatibility.md
git commit -m "feat: report actual PIO USB frame intervals"
```

---

### Task 3: Reproduce the dependency commit and run release gates

**Files:**
- Modify: `cmake/pio_usb_toolchain_lock.cmake`
- Modify: `tools/bootstrap_pio_usb_toolchain.ps1`
- Modify: `tests/build/test_pio_usb_toolchain_patches.py`
- Modify: `tests/build/test_pio_usb_ack_turnaround_backport.py`
- Modify: `tests/build/test_pio_usb_reference_contract.py`
- Modify: `docs/release/firmware-build.md`
- Modify: `docs/release/third-party-licenses.md`

**Interfaces:**
- Consumes: sorted patches `0001` through `0006` and deterministic identity/date/message already defined by the bootstrap.
- Produces: one reproducible 40-character Pico-PIO-USB patched revision pinned identically in bootstrap, CMake lock, tests, and release documentation.

- [ ] **Step 1: Demonstrate the old pin rejects the new patch stack**

Apply all six patches to a fresh checkout at `3c1eec341a5232640e4c00628b889b641af34b28` using the bootstrap's fixed Git metadata, record the resulting commit, and run:

```powershell
python -m pytest tests/build/test_pio_usb_toolchain_patches.py -q
```

Expected: FAIL because `e2119238c35f7f16d7e25f5608dc56aa0971db3d` no longer identifies the six-patch tree.

- [ ] **Step 2: Update every pin and verify reproducibility**

Replace the old Pico-PIO-USB patched revision with the freshly reproduced hash in every file listed above. Recreate the commit a second time from the upstream base and assert the second hash is byte-for-byte identical. Point `.deps/pico-pio-usb` at that clean commit.

- [ ] **Step 3: Run all software and build gates**

```powershell
cmake --build build/native -j 6
ctest --test-dir build/native --output-on-failure
cmake --build build/pico-pio-usb-release --target duo_u1_main -j 6
cmake --build build/pico-pio-usb-reference-release --target duo_u1_reference -j 6
cmake --build build/pico-ch375 --target duo_u1_main -j 6
python -m pytest tests/build/test_pio_usb_ack_turnaround_backport.py tests/build/test_pio_usb_firmware_contract.py tests/build/test_pio_usb_reference_contract.py tests/build/test_pio_usb_toolchain_patches.py tests/test_protocol_docs.py -q
git -C .deps/pico-pio-usb status --short
git diff --check
```

Expected: all tests pass, all three firmware targets link, the dependency status is empty, and `git diff --check` reports no errors.

- [ ] **Step 4: Review and commit the deterministic pin**

Use `superpowers:requesting-code-review`, resolve Critical/Important findings, then commit only the pin/bootstrap/docs/test changes:

```powershell
git add cmake/pio_usb_toolchain_lock.cmake tools/bootstrap_pio_usb_toolchain.ps1 tests/build/test_pio_usb_toolchain_patches.py tests/build/test_pio_usb_ack_turnaround_backport.py tests/build/test_pio_usb_reference_contract.py docs/release/firmware-build.md docs/release/third-party-licenses.md
git commit -m "build: pin unified PIO USB scheduler patch"
```

- [ ] **Step 5: Build, hash, approve, and flash the hardware candidate**

Print the exact size and SHA-256 of `build/pico-pio-usb-release/firmware/u1_main/duo_u1_main.uf2`. Obtain explicit approval for that hash, copy it to the verified `RPI-RP2` volume, and wait for U1 to return as a serial device.

- [ ] **Step 6: Run repeated-write hardware acceptance**

Read the installed package once, then perform at least 20 same-package writes so both A/B slots are exercised ten times. After every write, fetch diagnostics and stop immediately if either HID role is not ready, `stall_signals` or `arm_escalations` increases, the root port disconnects/suspends, or the SOF/Core 1 counters stop advancing. At the end, wait 20 seconds and verify again. Require `sof_interval_max_us < 3000`; inspect `sof_interval_min_us` for compressed adjacent frames and obtain the user's physical mouse/keyboard confirmation.

- [ ] **Step 7: Record final evidence**

Report the UF2 hash, write count, before/after fault counters, interval min/max, readiness, read-back equality, and the user's physical result. Do not mark the bug fixed if any gate fails.
