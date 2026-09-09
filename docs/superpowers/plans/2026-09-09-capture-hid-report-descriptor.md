# HID Report Descriptor Capture Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a backward-compatible diagnostic path that exports the real HID report descriptor of the most recently mounted interface, then build and flash U1 so the Keychron `3434:D030` interface 2 descriptor can be collected without guessing its layout.

**Architecture:** Keep the existing 1020-byte `GET_DIAGNOSTICS` reply unchanged. The PIO USB registry retains one bounded descriptor observation (identity, original length, captured prefix, truncation flag), core 1 publishes it through the existing `SourceInventory` snapshot, and `ConfigService` exposes it through a new capability-gated `GET_HID_DESCRIPTOR_CAPTURE` request. The configurator requests that command only when negotiated, parses it independently, and includes it in `diagnostics.json`; old hosts and old firmware continue using the existing diagnostics path.

**Tech Stack:** C++17, RP2040/Pico SDK, TinyUSB/Pico-PIO-USB, generated JSON protocol schema, Python 3.12, PySide6, pytest/pytest-qt, CMake/Ninja/CTest, PowerShell release tooling.

---

## Scope and fixed wire contract

This plan is Stage 1 of the approved design in
`docs/superpowers/specs/2026-09-09-multi-report-hid-interfaces-design.md`.
It must not add a device-specific Keychron decoder.

Add these schema values:

```json
"HID_DESCRIPTOR_DIAGNOSTICS": 2048
"GET_HID_DESCRIPTOR_CAPTURE": 22
```

`GET_HID_DESCRIPTOR_CAPTURE` has an empty request payload. Its successful reply
is little-endian and has this exact layout:

| Offset | Size | Meaning |
|---:|---:|---|
| 0 | 1 | `CdcError` (`0` for success) |
| 1 | 1 | observation format version (`1`) |
| 2 | 1 | flags: bit 0 `present`, bit 1 `truncated`; all other bits zero |
| 3 | 2 | vendor ID |
| 5 | 2 | product ID |
| 7 | 1 | HID interface number |
| 8 | 2 | original descriptor size |
| 10 | 2 | captured descriptor size |
| 12 | N | captured descriptor bytes, at most 256 |

An absent observation is a successful 12-byte reply with version 1, flags 0,
zero identity/sizes, and no byte tail. A present observation must satisfy
`captured_size == len(tail)`, `captured_size <= 256`, and
`truncated == (original_size > captured_size)`. The maximum successful reply is
268 bytes, below `CDC_MAX_PAYLOAD == 1024`.

The capture is deliberately the most recently *processed* HID mount carrying a
non-empty descriptor. A later mount with no descriptor does not erase useful
evidence. The observation survives unmount until another descriptor replaces
it or U1 resets. During hardware collection, replug the Keychron receiver last
and verify the returned identity is `3434:D030`, interface 2.

## Task 0: Checkpoint the inherited diagnostic fix

The worktree starts with tested but uncommitted changes from the preceding
debugging session. Preserve them as their own checkpoint before introducing the
new protocol so later commits remain reviewable.

**Files:** all currently modified tracked files reported by `git status --short`;
do not add build outputs, downloads, `.deps`, or untracked files.

**Step 1: Inspect the inherited diff and whitespace**

Run:

```powershell
git status --short
git diff --check
git diff --stat
```

Expected: only the known per-interface diagnostics, modifier-order fix, tests,
translations, and compatibility notes are modified; `git diff --check` exits 0.
If any unrelated file appears, leave it unstaged and record it in the execution
notes.

**Step 2: Re-run the focused inherited tests**

Run from the repository root:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m pytest configurator/tests/device/test_transactions.py configurator/tests/ui/test_bindings.py configurator/tests/ui/test_diagnostics.py configurator/tests/ui/test_mouse_switch.py -q
cmake --build --preset native
ctest --preset native --output-on-failure
```

Expected: all selected pytest tests and all 47 native tests pass.

**Step 3: Commit only the inherited changes**

```powershell
git add -- configurator/src/duo_input/device/service.py configurator/src/duo_input/device/transactions.py configurator/src/duo_input/persistence/diagnostic_export.py configurator/src/duo_input/resources/translations/duo_input_en.qm configurator/src/duo_input/resources/translations/duo_input_en.ts configurator/src/duo_input/resources/translations/duo_input_ru.qm configurator/src/duo_input/resources/translations/duo_input_ru.ts configurator/src/duo_input/ui/bindings.py configurator/src/duo_input/ui/diagnostics.py configurator/tests/device/test_transactions.py configurator/tests/ui/test_bindings.py configurator/tests/ui/test_diagnostics.py configurator/tests/ui/test_mouse_switch.py docs/release/compatibility-matrix.md firmware/u1_main/config_service.cpp firmware/u1_main/config_service.hpp firmware/u1_main/input/keyboard_normalizer.cpp firmware/u1_main/input/source.hpp firmware/u1_main/input/source_inventory.hpp firmware/u1_main/input/source_table.cpp firmware/u1_main/input/source_table.hpp firmware/u1_main/pio_usb/device_registry.cpp firmware/u1_main/pio_usb/device_registry.hpp firmware/u1_main/pio_usb/hid_setup.cpp firmware/u1_main/pio_usb/hid_setup.hpp firmware/u1_reference/source_adapter.hpp tests/firmware_native/fakes/tinyusb_host.cpp tests/firmware_native/fakes/tinyusb_host.hpp tests/firmware_native/test_config_service.cpp tests/firmware_native/test_normalizers.cpp tests/firmware_native/test_pio_usb_device_registry.cpp tests/firmware_native/test_pio_usb_hid_setup.cpp tests/firmware_native/test_source_table.cpp tests/firmware_native/test_trace_replay.cpp
git diff --cached --check
git commit -m "fix: diagnose per-interface capture failures"
```

Expected: one commit and a clean tracked worktree.

## Task 1: Generate the optional protocol command

**Files:**

- Modify: `protocol/schema.json`
- Modify (generated): `firmware/common/protocol/generated.hpp`
- Modify (generated): `configurator/src/duo_input/generated/protocol.py`
- Modify (generated): any other file changed by `tools/generate_protocol.py`
- Test: `tests/test_generate_protocol.py`
- Test: `tests/test_protocol_docs.py`
- Test: `tests/test_protocol_copies.py`
- Test: `configurator/tests/test_frames.py`

**Step 1: Write failing schema-contract tests**

Add assertions that:

```python
assert int(Capability.HID_DESCRIPTOR_DIAGNOSTICS) == 2048
assert int(CdcMessageType.GET_HID_DESCRIPTOR_CAPTURE) == 22
```

Also assert the new capability is included by the generated capability mask and
the new message round-trips through the existing frame encoder/decoder. Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_generate_protocol.py tests/test_protocol_docs.py tests/test_protocol_copies.py configurator/tests/test_frames.py -q
```

Expected: RED because the generated enums do not contain the names.

**Step 2: Add schema values and regenerate**

Edit `protocol/schema.json`, retaining numeric stability of every existing
value, then run:

```powershell
.\.venv\Scripts\python.exe tools/generate_protocol.py
.\.venv\Scripts\python.exe tools/generate_protocol.py --check
```

Expected: generated C++ and Python enums contain values 2048 and 22; check exits
0.

**Step 3: Run the protocol tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_generate_protocol.py tests/test_protocol_docs.py tests/test_protocol_copies.py configurator/tests/test_frames.py -q
```

Expected: GREEN.

**Step 4: Commit**

```powershell
git add -- protocol/schema.json firmware/common/protocol/generated.hpp configurator/src/duo_input/generated/protocol.py tests/test_generate_protocol.py tests/test_protocol_docs.py tests/test_protocol_copies.py configurator/tests/test_frames.py
git commit -m "protocol: add HID descriptor diagnostics command"
```

Include any additional generator-owned file in the same commit only if the
generator changed it.

## Task 2: Retain a bounded descriptor observation in the PIO registry

**Files:**

- Modify: `firmware/u1_main/input/source_inventory.hpp`
- Modify: `firmware/u1_main/pio_usb/device_registry.hpp`
- Modify: `firmware/u1_main/pio_usb/device_registry.cpp`
- Modify: `firmware/u1_main/main.cpp`
- Test: `tests/firmware_native/test_pio_usb_device_registry.cpp`
- Test: `tests/firmware_native/test_source_table.cpp`

**Step 1: Define the backend-neutral observation**

Add to `source_inventory.hpp`:

```cpp
inline constexpr std::size_t kHidDescriptorCaptureBytes = 256;

struct HidDescriptorCapture {
    bool present = false;
    bool truncated = false;
    std::uint16_t vendor_id = 0;
    std::uint16_t product_id = 0;
    std::uint8_t interface_number = 0;
    std::uint16_t original_size = 0;
    std::uint16_t captured_size = 0;
    std::uint8_t bytes[kHidDescriptorCaptureBytes] = {};
};
```

Add `HidDescriptorCapture hid_descriptor_capture{}` to `SourceInventory` and
retain the existing compile-time trivial-copyability checks used by the core
queue.

**Step 2: Write failing registry tests**

In `test_pio_usb_device_registry.cpp`, add tests proving:

1. A 164-byte mount is copied byte-for-byte with its VID/PID/interface.
2. A 300-byte mount retains the first 256 bytes, reports original size 300,
   captured size 256, and `truncated=true`.
3. A later mount with `nullptr, 0` does not erase the previous observation.
4. The truncated prefix is never passed to `classify_hid_layout`; classification
   retains the pre-existing oversized-descriptor fallback behavior.
5. Mutating the callback's original buffer after `capture_hid_mount` cannot
   mutate the stored observation.

Expose a const registry accessor solely for publishing/testing:

```cpp
const input::HidDescriptorCapture& hid_descriptor_capture() const;
```

Run:

```powershell
cmake --build --preset native
ctest --test-dir build/native -R pio_usb_device_registry --output-on-failure
```

Expected: RED because no observation exists.

**Step 3: Make callback capture explicit and safe**

Extend `CallbackRecord` with `payload_size` and `payload_truncated`. In
`capture_hid_mount`, set `size` to the original descriptor length, copy
`min(descriptor_size, kMaxDescriptorBytes)` into the record, and mark truncation.
In `process(HidMount)`, update the retained observation only for non-empty
descriptor callbacks. Pass descriptor bytes to classification only when the
record is not truncated; never parse a prefix as a complete HID descriptor.

Do not change report callback semantics: report records continue using `size`
as their actual report size.

**Step 4: Publish the observation across cores**

Immediately after `g_sources.inventory(snapshot, ...)` in `u1_main/main.cpp`,
copy the registry accessor into `snapshot.hid_descriptor_capture` before pushing
the snapshot. The reference backend leaves the default absent observation, so
it remains source-compatible and advertises no false descriptor.

Add/update source inventory tests to verify default absence and value-copy
semantics through `SourceInventory`.

**Step 5: Run native tests**

```powershell
cmake --build --preset native
ctest --preset native --output-on-failure
```

Expected: all 47 native tests pass.

**Step 6: Commit**

```powershell
git add -- firmware/u1_main/input/source_inventory.hpp firmware/u1_main/pio_usb/device_registry.hpp firmware/u1_main/pio_usb/device_registry.cpp firmware/u1_main/main.cpp tests/firmware_native/test_pio_usb_device_registry.cpp tests/firmware_native/test_source_table.cpp
git commit -m "firmware: retain bounded HID descriptor evidence"
```

## Task 3: Serve the descriptor through the separate CDC request

**Files:**

- Modify: `firmware/u1_main/config_service.hpp`
- Modify: `firmware/u1_main/config_service.cpp`
- Modify: `firmware/common/protocol/frame.cpp`
- Test: `tests/firmware_native/test_config_service.cpp`
- Test: `tests/firmware_native/test_frames.cpp`

**Step 1: Write failing service tests**

Add native tests for:

- `device_capabilities()` advertises `HID_DESCRIPTOR_DIAGNOSTICS`.
- The command requires exactly zero request bytes.
- The command is rejected with `UNSUPPORTED_CAPABILITY` when it was not
  negotiated.
- Default/absent observation produces the exact 12-byte payload above.
- A three-byte example produces a 15-byte exact payload, including LE identity
  and bytes.
- A 256-byte truncated observation produces exactly 268 bytes.
- Existing maximum `GET_DIAGNOSTICS` response remains byte-for-byte unchanged
  and stays 1020 bytes.
- `CdcMessageType::GET_HID_DESCRIPTOR_CAPTURE` is accepted by frame validation.

Run:

```powershell
cmake --build --preset native
ctest --test-dir build/native -R "config_service|firmware_native" --output-on-failure
```

Expected: RED until the new command is dispatched.

**Step 2: Implement the firmware reply**

Add the new capability to `device_capabilities()`, zero request size to
`expected_request_size`, and the new capability to `required_capability`.
Implement one bounded payload writer that validates/clamps internal sizes before
copying and emits the fixed version/flags header. Route both successful dispatch
and `reply_error` through it so the reply type and fixed fields are deterministic.

Do not alter `diagnostics_payload`, `kDiagnosticsPayloadSize`, or the existing
`GET_DIAGNOSTICS` switch body.

**Step 3: Run native tests and payload guard**

```powershell
cmake --build --preset native
ctest --preset native --output-on-failure
rg -n "kDiagnosticsPayloadSize|1020" firmware/u1_main/config_service.hpp tests/firmware_native/test_config_service.cpp
```

Expected: all native tests pass and the frozen 1020-byte assertion remains.

**Step 4: Commit**

```powershell
git add -- firmware/u1_main/config_service.hpp firmware/u1_main/config_service.cpp firmware/common/protocol/frame.cpp tests/firmware_native/test_config_service.cpp tests/firmware_native/test_frames.cpp
git commit -m "firmware: expose HID descriptor diagnostic capture"
```

## Task 4: Parse, request, emulate, and export the observation

**Files:**

- Modify: `configurator/src/duo_input/device/transactions.py`
- Modify: `configurator/src/duo_input/device/service.py`
- Modify: `configurator/src/duo_input/device/emulator.py`
- Modify: `configurator/src/duo_input/persistence/diagnostic_export.py`
- Test: `configurator/tests/device/test_transactions.py`
- Test: `configurator/tests/device/test_device_service.py`
- Test: `configurator/tests/test_device_emulator.py`
- Test: `configurator/tests/integration/test_diagnostic_export.py`

**Step 1: Write failing pure-parser tests**

Introduce an immutable `HidDescriptorCapture` value and
`parse_hid_descriptor_capture(payload)` in `transactions.py`. Test exact parsing
of absent, complete, and truncated replies. Reject:

- payloads shorter than 12 bytes;
- a version other than 1;
- unknown flag bits;
- `captured_size > 256`;
- a tail whose length differs from `captured_size`;
- present=false with non-zero identity/sizes/tail;
- complete observations where original and captured sizes differ;
- truncated observations where original size is not greater than captured size.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest configurator/tests/device/test_transactions.py -q -k descriptor
```

Expected: RED.

**Step 2: Implement the pure parser**

Use `struct.Struct("<BBBHHBHH")` for the 12-byte header, convert the tail to
immutable `bytes`, and keep parsing independent from `parse_diagnostics`.
Export the type and parser in the module's `__all__` if that module uses one.

Re-run the Step 1 command; expected GREEN.

**Step 3: Write failing service compatibility tests**

Add service tests proving:

1. With both diagnostic capabilities negotiated, `get_diagnostics()` sends
   `GET_DIAGNOSTICS`, then `GET_HID_DESCRIPTOR_CAPTURE`, and emits one successful
   operation only after both replies.
2. The operation result remains the ordinary `DeviceDiagnostics` object, while
   `service.hid_descriptor_capture` exposes the second result.
3. Without the new capability, only the old request is sent and the operation
   succeeds exactly as before.
4. Disconnect/reconnect clears stale descriptor evidence.
5. A malformed descriptor reply reports `BAD_PAYLOAD` for the same
   `get_diagnostics` operation.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest configurator/tests/device/test_device_service.py -q -k "diagnostic and descriptor"
```

Expected: RED.

**Step 4: Chain the optional service request**

Add `_hid_descriptor_capture = None`, a read-only property, and reset it during
connect/disconnect teardown. In `_on_diagnostics`, parse the existing reply;
when `device_info.capabilities & Capability.HID_DESCRIPTOR_DIAGNOSTICS`, send the
new request and finish in a new continuation after parsing it. Otherwise finish
immediately. Do not add the extra request to `begin_capture()`; capture startup
needs only the existing source inventory and should not gain latency.

**Step 5: Update the emulator**

Advertise the capability, accept the new zero-byte request, and return a
deterministic absent observation by default. Add a configurable descriptor
fixture for the complete-response test. Verify old capability filtering still
rejects unnegotiated commands.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest configurator/tests/device/test_device_service.py configurator/tests/test_device_emulator.py -q
```

Expected: GREEN.

**Step 6: Export actionable JSON**

Extend `DiagnosticSnapshot` with `hid_descriptor_capture`. For a present
observation, serialize:

```json
{
  "present": true,
  "truncated": false,
  "vendor_id": "0x3434",
  "product_id": "0xD030",
  "interface_number": 2,
  "original_size": 164,
  "captured_size": 164,
  "sha256": "<64 lowercase hex characters>",
  "hex": "<uppercase two-digit bytes separated by spaces>"
}
```

For a supported firmware with no observation, export `{"present": false}`.
For an old firmware or a snapshot made before refresh, preserve the project's
existing `"unknown"` convention. Compute SHA-256 host-side from the captured
bytes so copied reports are self-checking.

Add integration tests for all three states and an exact byte round-trip through
the ZIP's `diagnostics.json`.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest configurator/tests/integration/test_diagnostic_export.py -q -k descriptor
```

Expected: GREEN.

**Step 7: Commit**

```powershell
git add -- configurator/src/duo_input/device/transactions.py configurator/src/duo_input/device/service.py configurator/src/duo_input/device/emulator.py configurator/src/duo_input/persistence/diagnostic_export.py configurator/tests/device/test_transactions.py configurator/tests/device/test_device_service.py configurator/tests/test_device_emulator.py configurator/tests/integration/test_diagnostic_export.py
git commit -m "configurator: export captured HID report descriptor"
```

## Task 5: Verify both compatibility directions

**Files:**

- Modify: `docs/release/compatibility-matrix.md`
- Test: `configurator/tests/integration/test_real_config_contract.py`
- Test: `tests/build/test_firmware_artifacts.py`

**Step 1: Add compatibility tests and notes**

Record and test the two supported combinations:

- New configurator + old firmware: firmware omits capability 2048, so no new
  request is sent and diagnostics/export remain usable with descriptor state
  `unknown`.
- Old configurator + new firmware: the host requests only its known capability
  bits and never sends message 22; every existing command/reply is unchanged.

Also document the new command's maximum 268-byte reply and the unchanged
1020-byte maximum for `GET_DIAGNOSTICS`.

**Step 2: Run host, protocol, and native suites**

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe tools/generate_protocol.py --check
.\.venv\Scripts\python.exe -m pytest configurator/tests tests -q
cmake --build --preset native
ctest --preset native --output-on-failure
git diff --check
```

Expected: the current host total grows from the recorded 1172 passes by the new
tests; all native tests pass; no generator or whitespace drift.

**Step 3: Build both PIO firmware targets cleanly**

```powershell
cmake --build --preset pico-pio-usb-release --clean-first
cmake --build --preset pico-pio-usb-reference-release --clean-first
.\.venv\Scripts\python.exe -m pytest tests/build/test_firmware_artifacts.py -q
```

Expected: both firmware targets link successfully. Only the production PIO U1
advertises/captures real registry descriptors; the reference target builds with
an absent observation.

**Step 4: Commit**

```powershell
git add -- docs/release/compatibility-matrix.md configurator/tests/integration/test_real_config_contract.py tests/build/test_firmware_artifacts.py
git commit -m "docs: specify HID descriptor diagnostics compatibility"
```

If a listed test file needed no change, do not stage it.

## Task 6: Package, approve the exact image, flash U1, and collect Keychron evidence

**Files/artifacts:**

- Build: `build/pico-pio-usb-release/firmware/u1_main/duo_u1_main.uf2`
- Build: release package produced by `tools/build_release.ps1`
- Read: `C:\Users\Valentyn\Downloads\duo-input-report\diagnostics.json`
- Create later, not in this task: the Keychron descriptor fixture and the Stage 2 plan

**Step 1: Build the distributable diagnostic package**

Run the release command using the same version/package arguments as the previous
accepted PIO USB diagnostic build, then run its packaging contract tests. Record
the exact output paths and SHA-256 values:

```powershell
Get-FileHash build/pico-pio-usb-release/firmware/u1_main/duo_u1_main.uf2 -Algorithm SHA256
```

Expected: one production PIO U1 UF2 and a passing configurator package.

**Step 2: Approval checkpoint before physical write**

Present the exact U1 UF2 path and SHA-256 to the user. Obtain explicit approval
for that exact image. Do not flash U2.

**Step 3: Flash only U1**

After U1 is placed in BOOTSEL mode, resolve the exact `RPI-RP2` volume, verify it
is a removable RP2040 boot volume, and copy only the approved
`duo_u1_main.uf2`. Confirm the volume disappears and U1 re-enumerates as
VID `1209`, PID `D101` on a COM port.

**Step 4: Make the desired interface the latest observation**

With flashed U1 running, unplug and replug the Keychron receiver after all other
input receivers are stable. Wait for its interfaces to mount. In DuoInput press
Diagnostics -> Refresh, then export the report to:

```text
C:\Users\Valentyn\Downloads\duo-input-report
```

No side-button press is required to collect the descriptor, but press/release it
once so the existing per-interface last-report evidence is refreshed too.

**Step 5: Validate the exported evidence**

Read `diagnostics.json` and require:

```text
hid_descriptor_capture.present = true
hid_descriptor_capture.truncated = false
hid_descriptor_capture.vendor_id = 0x3434
hid_descriptor_capture.product_id = 0xD030
hid_descriptor_capture.interface_number = 2
hid_descriptor_capture.original_size = 164
hid_descriptor_capture.captured_size = 164
```

Recompute SHA-256 from the hexadecimal bytes and require it matches the exported
hash. Also preserve the side-button report pair:

```text
press   01 01 00 4F 00 00 00 00 03
release 01 00 00 00 00 00 00 00 03
```

If identity/interface is wrong, do not change code: replug Keychron last and
export again. If size/truncation is wrong, stop and diagnose Stage 1 before
proceeding.

**Step 6: Start Stage 2 from evidence**

Save the verified 164 descriptor bytes under `tests/vectors/usb_descriptors/`
with its VID/PID/interface and SHA-256 provenance. Then invoke
`superpowers:writing-plans` to write a second plan for the multi-report decoder,
using the real descriptor and captured press/release reports as the first RED
test. Do not implement that decoder inside this Stage 1 plan.
