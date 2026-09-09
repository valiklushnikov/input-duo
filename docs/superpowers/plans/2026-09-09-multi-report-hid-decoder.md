# Multi-Report HID Decoder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decode every independently supported keyboard, consumer, and mouse report in one HID interface so the measured Keychron side button and other composite controls work without device-specific exceptions.

**Architecture:** Replace the one-role/one-layout source assumption with a bounded `HidReportSet` whose accepted and rejected candidates are keyed by role and Report ID. The descriptor parser builds that set in descriptor order, `InputPipeline` owns independent normalizer state for every accepted entry, and report dispatch uses only the descriptor-declared ID. A separate capability-gated diagnostic command exports report-set decisions without changing the frozen 1020-byte `GET_DIAGNOSTICS` response.

**Tech Stack:** C++17, RP2040/Pico SDK, TinyUSB/Pico-PIO-USB, generated JSON protocol schema, Python 3.12, PySide6, pytest/pytest-qt, CMake/Ninja/CTest, PowerShell release tooling.

**Spec:** `docs/superpowers/specs/2026-09-09-multi-report-hid-interfaces-design.md`

## Global Constraints

- No product-specific VID, PID, usage, endpoint, or Report-ID exception.
- Saved source identity remains exactly `(VID, PID, interface number)`; Report ID is not persisted in a binding.
- Fixed capacities only; firmware performs no dynamic allocation.
- One malformed/unsupported candidate cannot invalidate independently decodable Report IDs; structurally truncated descriptors remain fatal because later item boundaries are unknowable.
- Existing single-report and boot-fallback devices preserve their current behavior.
- Detach/fault releases held state from every decoder belonging to the interface.
- The 12-byte binding record, 8-byte capture payload, and existing `GET_DIAGNOSTICS` request/reply remain byte-for-byte unchanged.
- Real fixture `tests/vectors/usb_descriptors/keychron_3434_d030_interface_2_report.hex` is 164 bytes with SHA-256 `3e7a5226173a4fbe03c98934c6686d8ccd0d3ae321b5b1a266b24c4b18cddb28`.
- The measured side-button packets are press `01 01 00 4F 00 00 00 00 03` and release `01 00 00 00 00 00 00 00 03`; usage `0x03` is ErrorUndefined and must never be emitted.
- Never flash U1 without presenting the exact UF2 path and SHA-256 and receiving approval for that image. Never flash U2 in this work.

---

### Task 1: Define the bounded report-set value model

**Files:**

- Modify: `firmware/u1_main/input/hid/report_descriptor.hpp`
- Test: `tests/firmware_native/test_report_descriptor.cpp`

**Interfaces:**

- Produces: `hid::ReportRole`, `hid::HidReportEntry`, `hid::RejectedReportEntry`, and `hid::HidReportSet`.
- Capacity: `kMaxHidReportEntries == 8`, `kMaxRejectedReportEntries == 8`.
- `report_id == 0` means an unnumbered report; HID descriptors cannot declare Report ID zero.

- [ ] **Step 1: Add compile-time model tests**

Add assertions proving default construction is empty, accepted/rejected arrays have exactly eight slots, values copy without aliasing, and `HidReportSet` is trivially copyable. Use this exact public shape:

```cpp
enum class ReportRole : std::uint8_t { Keyboard = 1, Consumer = 2, Mouse = 3 };

struct HidReportEntry {
    ReportRole role = ReportRole::Keyboard;
    std::uint8_t report_id = 0;
    KeyboardReportLayout keyboard{};
    MouseReportLayout mouse{};
};

struct RejectedReportEntry {
    ReportRole role = ReportRole::Keyboard;
    std::uint8_t report_id = 0;
    ReportDescriptorError reason = ReportDescriptorError::UnsupportedLayout;
};

inline constexpr std::size_t kMaxHidReportEntries = 8;
inline constexpr std::size_t kMaxRejectedReportEntries = 8;

struct HidReportSet {
    bool uses_report_ids = false;
    std::uint8_t count = 0;
    HidReportEntry entries[kMaxHidReportEntries] = {};
    std::uint8_t rejected_count = 0;
    RejectedReportEntry rejected[kMaxRejectedReportEntries] = {};
    std::uint8_t rejected_overflow = 0;
};
```

Append `AmbiguousReportSet` to `ReportDescriptorError`; do not renumber the
existing values. It names the case where an unnumbered descriptor yields more
than one supported role and no Report ID exists to dispatch them safely.

- [ ] **Step 2: Run the model test RED**

Run:

```powershell
cmake --build --preset native
ctest --test-dir build/native -R report_descriptor --output-on-failure
```

Expected: compilation fails because the report-set types do not exist.

- [ ] **Step 3: Add the value types and static assertions**

Add the declarations above after `ReportDescriptorError`. Add:

```cpp
static_assert(std::is_trivially_copyable<HidReportSet>::value,
              "a HID report set crosses cores only by value");
```

Include `<type_traits>`. Do not add constructors, pointers, containers, or virtual methods.

- [ ] **Step 4: Run the focused test GREEN**

Run the Step 2 command. Expected: `report_descriptor` passes.

- [ ] **Step 5: Commit**

```powershell
git add -- firmware/u1_main/input/hid/report_descriptor.hpp tests/firmware_native/test_report_descriptor.cpp
git commit -m "firmware: define bounded HID report sets"
```

### Task 2: Parse independent descriptor candidates

**Files:**

- Modify: `firmware/u1_main/input/hid/report_descriptor.hpp`
- Modify: `firmware/u1_main/input/hid/report_descriptor.cpp`
- Test: `tests/firmware_native/test_report_descriptor.cpp`

**Interfaces:**

- Consumes: `hid::HidReportSet` from Task 1.
- Produces:

```cpp
ReportDescriptorError parse_hid_report_set(protocol::ByteView descriptor,
                                           HidReportSet& out);
```

- Success returns `None` and replaces `out` atomically. Fatal descriptor truncation/global-stack corruption returns its error and leaves `out` unchanged.

- [ ] **Step 1: Add the real Keychron RED test**

Load `keychron_3434_d030_interface_2_report.hex` with the existing strict-uppercase helper and assert:

```cpp
CHECK_EQ(bytes.size(), std::size_t{164});
HidReportSet set;
CHECK_EQ(parse_hid_report_set(view(bytes), set), ReportDescriptorError::None);
CHECK(set.uses_report_ids);
CHECK_EQ(set.count, std::uint8_t{3});
check_entry(set.entries[0], ReportRole::Keyboard, 1, 8);
check_entry(set.entries[1], ReportRole::Consumer, 2, 2);
check_entry(set.entries[2], ReportRole::Keyboard, 12, 20);
```

Hash the loaded bytes with `crypto::sha256` and compare all 32 bytes against
`3e7a5226173a4fbe03c98934c6686d8ccd0d3ae321b5b1a266b24c4b18cddb28` so
the hardware evidence cannot drift while retaining the same filename.

For Report ID 1 additionally assert modifier bits `0..7`, array offset `16`, element width `8`, count `6`, and usage range `0..0xF1`. For ID 12 assert bitmap offset `8`, count `0x98`, and range `0..0x98`.

- [ ] **Step 2: Add independence and ordering RED tests**

Use small synthetic descriptors to prove:

1. unsupported keyboard ID 1 followed by valid consumer ID 2 retains ID 2 and records rejected `(Keyboard, 1, UnsupportedLayout)`;
2. valid entries are retained in descriptor order across roles;
3. the ninth valid candidate increments `rejected_overflow` and never displaces the first eight;
4. two supported unnumbered roles yield no accepted entry and explicit `AmbiguousReportSet` rejections;
5. a truncated item is fatal and leaves a sentinel `out` unchanged;
6. existing `parse_keyboard_report_descriptor`, `parse_consumer_report_descriptor`, `parse_mouse_report_descriptor`, and `classify_report_descriptor` compatibility tests remain unchanged.

- [ ] **Step 3: Run the parser tests RED**

Run:

```powershell
cmake --build --preset native
ctest --test-dir build/native -R report_descriptor --output-on-failure
```

Expected: compilation fails because `parse_hid_report_set` is absent.

- [ ] **Step 4: Implement one bounded candidate walk**

Refactor the existing keyboard/consumer state walk so state is keyed by `(ReportRole, report_id)` and carries the byte offset of the first relevant Input item. Refactor the mouse walk from one `WorkingReport current/found` pair to an eight-element candidate array keyed by Report ID. Merge completed keyboard, consumer, and mouse candidates by that saved byte offset before appending to `HidReportSet`.

Use bounded helpers with these signatures:

```cpp
bool append_accepted(HidReportSet& out, const HidReportEntry& entry);
void append_rejected(HidReportSet& out, ReportRole role,
                     std::uint8_t report_id, ReportDescriptorError reason);
```

`append_accepted` returns false and saturating-increments `rejected_overflow` when count is eight. `append_rejected` retains the first eight rejection records and saturating-increments `rejected_overflow` thereafter. Do not guess a role from report bytes.

For array keyboards, treat usages `0x01`, `0x02`, and `0x03` as HID error indicators rather than key presses; the normalizer change itself is Task 4. A candidate whose fields exceed the existing bounded readers records `UnsupportedLayout` and parsing continues at the next HID item.

For descriptors with no Report IDs, accept exactly one supported role. If more than one role is supported, record ambiguity and return a successful but empty set so protocol-based boot fallback can make the same decision it makes today.

Keep the three legacy single-layout parse functions as wrappers that call the new parser and require exactly one accepted entry of their requested role. Preserve their existing error values and atomic-output behavior.

- [ ] **Step 5: Run parser and existing descriptor consumers GREEN**

```powershell
cmake --build --preset native
ctest --test-dir build/native -R "report_descriptor|pio_usb_hid_setup|ch375_descriptor_setup|reference_source_adapter" --output-on-failure
```

Expected: all selected tests pass.

- [ ] **Step 6: Run mutation checks for dispatch keys and capacity**

Apply three temporary `apply_patch` mutations: replace the parsed Report ID
with zero, reverse the final candidate-order comparison, and change the
capacity comparison `==` to `>`. After each mutation run:

```powershell
cmake --build --preset native
ctest --test-dir build/native -R report_descriptor --output-on-failure
```

Require a named new test to fail, then restore that mutation with a reverse
`apply_patch` and re-run the same command GREEN before trying the next one.

- [ ] **Step 7: Commit**

```powershell
git add -- firmware/u1_main/input/hid/report_descriptor.hpp firmware/u1_main/input/hid/report_descriptor.cpp tests/firmware_native/test_report_descriptor.cpp
git commit -m "firmware: parse independent HID report layouts"
```

### Task 3: Carry report sets through both USB backends

**Files:**

- Modify: `firmware/u1_main/input/source.hpp`
- Modify: `firmware/u1_main/pio_usb/hid_setup.cpp`
- Modify: `firmware/u1_main/ch375/descriptor_setup.cpp`
- Modify: `firmware/u1_reference/source_adapter.cpp`
- Test: `tests/firmware_native/test_pio_usb_hid_setup.cpp`
- Test: `tests/firmware_native/test_ch375_descriptor_setup.cpp`
- Test: `tests/firmware_native/test_reference_source_adapter.cpp`

**Interfaces:**

- Consumes: `parse_hid_report_set`.
- Produces: `SourceIdentity::report_set` populated for descriptor and boot layouts.
- Preserves: `SourceIdentity::kind`, `keyboard_layout`, and `mouse_layout` as a compatibility summary of the first accepted entry until downstream diagnostics migrate in Task 5.

- [ ] **Step 1: Add backend RED tests**

For all three adapters, add tests proving the Keychron fixture produces three entries in the order `(Keyboard,1)`, `(Consumer,2)`, `(Keyboard,12)`. Add existing-device assertions that a boot keyboard, boot mouse, descriptor keyboard, descriptor mouse, and descriptor consumer each produce exactly one entry with their current layout.

Add a PIO test proving protocol selection remains interface-wide: a non-empty descriptor report set returns `HidLayoutSource::ReportDescriptor` and therefore requests report protocol once; an empty descriptor with keyboard/mouse boot protocol builds one boot entry and remains in boot protocol.

- [ ] **Step 2: Run backend tests RED**

```powershell
cmake --build --preset native
ctest --test-dir build/native -R "pio_usb_hid_setup|ch375_descriptor_setup|reference_source_adapter" --output-on-failure
```

Expected: compilation fails because `SourceIdentity` has no report set.

- [ ] **Step 3: Add and populate the source field**

Add:

```cpp
hid::HidReportSet report_set{};
```

to `SourceIdentity`. In each backend, parse a complete descriptor once into this field. Use this helper for boot fallback:

```cpp
hid::HidReportSet boot_report_set(DeviceKind kind) {
    hid::HidReportSet set;
    set.count = 1;
    set.entries[0].role = kind == DeviceKind::Mouse
                              ? hid::ReportRole::Mouse
                              : hid::ReportRole::Keyboard;
    set.entries[0].keyboard = hid::boot_keyboard_layout();
    set.entries[0].mouse = hid::boot_mouse_layout();
    return set;
}
```

Only call it for matching boot protocol. Do not create fallback entries for protocol zero. Mirror the first accepted entry into the legacy summary fields without selecting by VID/PID or preferring a hard-coded Report ID.

- [ ] **Step 4: Rebuild clean and run backend tests GREEN**

Because `SourceIdentity` changes object layout, use a clean build:

```powershell
cmd.exe /d /c 'call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\Tools\VsDevCmd.bat" -arch=x64 >nul && cmake --build --preset native --clean-first && ctest --test-dir build/native -R "pio_usb_hid_setup|ch375_descriptor_setup|reference_source_adapter|pio_usb_device_registry" --output-on-failure'
```

Expected: all selected tests pass and no stale ODR/layout failure remains.

- [ ] **Step 5: Commit**

```powershell
git add -- firmware/u1_main/input/source.hpp firmware/u1_main/pio_usb/hid_setup.cpp firmware/u1_main/ch375/descriptor_setup.cpp firmware/u1_reference/source_adapter.cpp tests/firmware_native/test_pio_usb_hid_setup.cpp tests/firmware_native/test_ch375_descriptor_setup.cpp tests/firmware_native/test_reference_source_adapter.cpp
git commit -m "firmware: publish HID report sets from USB backends"
```

### Task 4: Dispatch reports to independent normalizers

**Files:**

- Modify: `firmware/u1_main/input/keyboard_normalizer.cpp`
- Modify: `firmware/u1_main/input/pipeline.hpp`
- Modify: `firmware/u1_main/input/pipeline.cpp`
- Modify: `firmware/u1_main/input/source_table.cpp`
- Test: `tests/firmware_native/test_normalizers.cpp`
- Test: `tests/firmware_native/test_input_pipeline.cpp`
- Test: `tests/firmware_native/test_source_table.cpp`
- Test: `tests/firmware_native/test_pio_usb_keyboard.cpp`
- Test: `tests/firmware_native/test_trace_replay.cpp`

**Interfaces:**

- Consumes: `SourceIdentity::report_set`.
- Produces:

```cpp
void InputPipeline::set_report_set(const hid::HidReportSet& reports);
```

- Keeps `set_kind(...)` as a one-entry compatibility wrapper for existing callers during this task.

- [ ] **Step 1: Add the exact Keychron side-button RED test**

Load the real descriptor, parse its set, configure an `InputPipeline`, and apply the exact press/release packets. Assert this complete event order:

```text
press:   KeyDown 0xE0, then KeyDown 0x4F
release: KeyUp   0x4F, then KeyUp   0xE0
```

Assert no event for usage `0x03`. Stamp the same source index on every event through `SourceTable` and prove capture still resolves `(0x3434, 0xD030, interface 2)`.

- [ ] **Step 2: Add state-isolation and detach RED tests**

Add tests proving:

1. consumer ID 2 packets never release Ctrl/Right held by keyboard ID 1;
2. keyboard ID 12 has state independent from keyboard ID 1;
3. an unknown Report ID increments the interface's received count but emits nothing;
4. detach while ID 1 and ID 12 hold keys releases both sets;
5. reconnect/reset clears every decoder slot;
6. unnumbered boot and descriptor devices still dispatch exactly once.

- [ ] **Step 3: Run the pipeline tests RED**

```powershell
cmake --build --preset native
ctest --test-dir build/native -R "normalizers|input_pipeline|source_table|pio_usb_keyboard|trace_replay" --output-on-failure
```

Expected: the Keychron press emits no event because the current pipeline owns only consumer ID 2.

- [ ] **Step 4: Ignore all HID keyboard error usages**

In the array branch of `KeyboardNormalizer::apply`, replace the single `usage == kRollover` check with:

```cpp
const bool error_usage = !layout_.consumer && usage >= 0x01 && usage <= 0x03;
if (error_usage) {
    if (usage == kRollover) ++rollover_slots;
    continue;
}
```

Replace `error_slots` with a separate `rollover_slots` count. Set
`rollover = element_count != 0 && rollover_slots == element_count`: freeze only
when every declared array slot carries `0x01`; `0x02`/`0x03` are ignored but do
not turn a mixed report into rollover. Update probe counting to retain a
separate bounded count of all three error usages without changing production
decisions.

- [ ] **Step 5: Implement bounded decoder slots**

Use this private shape in `InputPipeline`:

```cpp
struct DecoderSlot {
    bool active = false;
    hid::ReportRole role = hid::ReportRole::Keyboard;
    std::uint8_t report_id = 0;
    KeyboardNormalizer keyboard;
    MouseNormalizer mouse;
};

DecoderSlot decoders_[hid::kMaxHidReportEntries] = {};
std::uint8_t decoder_count_ = 0;
bool uses_report_ids_ = false;
```

`set_report_set` resets every decoder slot with `DecoderSlot{}`, copies at most
eight accepted entries, and assigns the appropriate layout to each normalizer.
The preceding Detached/Fault event remains responsible for emitting releases;
a Ready event never invents an input release. `on_report` rejects an empty
numbered packet, selects only slots whose `report_id == report.data[0]`, and
passes the original packet to existing normalizers so they continue
validating/stripping their own Report ID. Handle both `Report` and
`AuxiliaryReport` identically. For an unnumbered set, invoke its sole entry
once. Never infer a fallback from packet length or contents.

`on_detached` calls `release_all` on every active keyboard/consumer/mouse slot and emits all returned events before clearing the array. `SourceTable::Ready` calls `set_report_set(identity.report_set)`.

- [ ] **Step 6: Run the focused tests GREEN**

Run the Step 3 command. Expected: all selected tests pass.

- [ ] **Step 7: Run mutation tests**

Apply one temporary `apply_patch` at a time to invert the ID equality, dispatch
consumer packets to every keyboard slot, stop detach iteration after its first
slot, and remove the `usage <= 0x03` guard. After each mutation run the Step 3
command, require a named new test to fail, reverse the patch, and re-run GREEN
before applying the next mutation.

- [ ] **Step 8: Commit**

```powershell
git add -- firmware/u1_main/input/keyboard_normalizer.cpp firmware/u1_main/input/pipeline.hpp firmware/u1_main/input/pipeline.cpp firmware/u1_main/input/source_table.cpp tests/firmware_native/test_normalizers.cpp tests/firmware_native/test_input_pipeline.cpp tests/firmware_native/test_source_table.cpp tests/firmware_native/test_pio_usb_keyboard.cpp tests/firmware_native/test_trace_replay.cpp
git commit -m "fix: decode multiple HID reports per interface"
```

### Task 5: Export report-set decisions without growing GET_DIAGNOSTICS

**Files:**

- Modify: `protocol/schema.json`
- Modify (generated): `firmware/common/protocol/generated.hpp`
- Modify (generated): `configurator/src/duo_input/generated/protocol.py`
- Modify: `firmware/u1_main/input/source_inventory.hpp`
- Modify: `firmware/u1_main/input/source_table.cpp`
- Modify: `firmware/u1_main/config_service.hpp`
- Modify: `firmware/u1_main/config_service.cpp`
- Modify: `firmware/common/protocol/frame.cpp`
- Modify: `configurator/src/duo_input/device/transactions.py`
- Modify: `configurator/src/duo_input/device/service.py`
- Modify: `configurator/src/duo_input/device/emulator.py`
- Modify: `configurator/src/duo_input/persistence/diagnostic_export.py`
- Modify: `configurator/src/duo_input/ui/diagnostics.py`
- Test: `tests/test_generate_protocol.py`
- Test: `tests/test_protocol_docs.py`
- Test: `configurator/tests/test_frames.py`
- Test: `tests/firmware_native/test_config_service.cpp`
- Test: `tests/firmware_native/test_source_table.cpp`
- Test: `configurator/tests/device/test_transactions.py`
- Test: `configurator/tests/device/test_device_service.py`
- Test: `configurator/tests/test_device_emulator.py`
- Test: `configurator/tests/integration/test_diagnostic_export.py`
- Test: `configurator/tests/ui/test_diagnostics.py`

**Interfaces:**

- Adds capability `HID_REPORT_SET_DIAGNOSTICS = 4096` and message `GET_HID_REPORT_SETS = 23`; existing numeric values stay unchanged.
- Empty request; successful little-endian reply:

```text
error:u8, version:u8 (=1), source_count:u8
source[source_count]:
  device_address:u8, vid:u16, pid:u16, interface:u8,
  accepted_count:u8, rejected_count:u8, rejected_overflow:u8
  accepted[accepted_count]: role:u8, report_id:u8, minimum_body_bytes:u8
  rejected[rejected_count]: role:u8, report_id:u8, reason:u8
```

- Maximum is `3 + 8 * (9 + 8*3 + 8*3) == 459` bytes, below `CDC_MAX_PAYLOAD`.

- [ ] **Step 1: Add schema and frame RED tests**

Assert enum values 4096 and 23, generated masks, and frame round-trip. Run the protocol test group. Expected: missing enum failures.

- [ ] **Step 2: Generate protocol values**

Add the values without renumbering any existing item, run:

```powershell
.\.venv\Scripts\python.exe tools/generate_protocol.py
.\.venv\Scripts\python.exe tools/generate_protocol.py --check
```

- [ ] **Step 3: Add native serialization RED tests**

Test exact absent three-byte response, one source with three accepted Keychron entries, rejected candidates, eight-entry bounds, capability gating, non-empty request rejection, and the 459-byte maximum. Retain the existing assertion that maximum `GET_DIAGNOSTICS` is exactly 1020 bytes and compare its complete payload before/after this task.

- [ ] **Step 4: Publish and serialize report-set summaries**

Add fixed accepted/rejected diagnostic arrays to each `SourceInfo`, copy them from `SourceIdentity::report_set` in `SourceTable::inventory`, and implement the wire contract above in `ConfigService`. Validate every count before copying and clamp only corrupted internal counts; never read beyond fixed arrays. Advertise/require the new capability and add frame validation.

- [ ] **Step 5: Add host parser/service RED tests**

Define immutable Python values `HidReportEntry`, `RejectedHidReportEntry`, `HidReportSource`, and `HidReportSets`. Reject wrong version, count/tail mismatch, invalid role/reason values, duplicate source identities, and more than eight accepted/rejected entries.

Service ordering when all capabilities are present must be:

```text
GET_DIAGNOSTICS -> GET_HID_DESCRIPTOR_CAPTURE -> GET_HID_REPORT_SETS
```

The operation emits one success after all negotiated replies. Old firmware missing either optional capability skips only that request. Malformed report-set data fails the same `get_diagnostics` operation with `BAD_PAYLOAD`.

- [ ] **Step 6: Implement parser, chaining, and emulator**

Use `struct.Struct("<BBB")`, `struct.Struct("<BHHBBBB")`, and `struct.Struct("<BBB")`. Reset retained report sets on connect/disconnect. Extend the emulator with deterministic absent and configurable Keychron fixtures.

- [ ] **Step 7: Export and display decisions**

Add `hid_report_sets` to `DiagnosticSnapshot`. Old firmware exports `unknown`; supported/empty exports `[]`; Keychron exports three accepted entries and any rejected entries with symbolic role/reason names. Add a read-only Diagnostics row per source such as:

```text
3434:D030 interface 2: keyboard id=1 min=8; consumer id=2 min=2; keyboard id=12 min=20
```

Do not add these fields to saved configuration or capture payloads.

- [ ] **Step 8: Run protocol/native/host tests GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_generate_protocol.py tests/test_protocol_docs.py configurator/tests/test_frames.py configurator/tests/device/test_transactions.py configurator/tests/device/test_device_service.py configurator/tests/test_device_emulator.py configurator/tests/integration/test_diagnostic_export.py configurator/tests/ui/test_diagnostics.py -q
cmd.exe /d /c 'call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\Tools\VsDevCmd.bat" -arch=x64 >nul && cmake --build --preset native --clean-first && ctest --preset native --output-on-failure'
```

Expected: all selected host tests and all 47 native tests pass.

- [ ] **Step 9: Commit**

```powershell
git add -- protocol/schema.json firmware/common/protocol/generated.hpp configurator/src/duo_input/generated/protocol.py firmware/u1_main/input/source_inventory.hpp firmware/u1_main/input/source_table.cpp firmware/u1_main/config_service.hpp firmware/u1_main/config_service.cpp firmware/common/protocol/frame.cpp configurator/src/duo_input/device/transactions.py configurator/src/duo_input/device/service.py configurator/src/duo_input/device/emulator.py configurator/src/duo_input/persistence/diagnostic_export.py configurator/src/duo_input/ui/diagnostics.py tests/test_generate_protocol.py tests/test_protocol_docs.py configurator/tests/test_frames.py tests/firmware_native/test_config_service.cpp tests/firmware_native/test_source_table.cpp configurator/tests/device/test_transactions.py configurator/tests/device/test_device_service.py configurator/tests/test_device_emulator.py configurator/tests/integration/test_diagnostic_export.py configurator/tests/ui/test_diagnostics.py
git commit -m "diagnostics: expose per-interface HID report sets"
```

### Task 6: Full verification, package, and bench acceptance

**Files:**

- Modify: `docs/protocol/compatibility.md`
- Modify: `docs/release/compatibility-matrix.md`
- Build: `build/pico-pio-usb-release/firmware/u1_main/duo_u1_main.uf2`
- Build: release package from `tools/build_release.ps1`

**Interfaces:**

- Consumes: all Stage 2 firmware/configurator behavior.
- Produces: one approved production PIO U1 image and measured Keychron/Trust acceptance results.

- [ ] **Step 1: Document compatibility and universality**

Document command 23, capability 4096, maximum 459-byte reply, unchanged 1020-byte diagnostics maximum, and both compatibility directions. State explicitly that dispatch uses parsed role/Report ID entries and contains no VID/PID allowlist.

- [ ] **Step 2: Run complete verification**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe tools/generate_protocol.py --check
.\.venv\Scripts\python.exe -m pytest configurator/tests tests -q
cmd.exe /d /c 'call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\Tools\VsDevCmd.bat" -arch=x64 >nul && cmake --build --preset native --clean-first && ctest --preset native --output-on-failure'
cmake --preset pico-pio-usb-release
cmake --build --preset pico-pio-usb-release --clean-first
cmake --preset pico-pio-usb-reference-release
cmake --build --preset pico-pio-usb-reference-release --clean-first
git diff --check
```

Expected: all host/native tests pass; production and reference firmware link; no generated/whitespace drift.

- [ ] **Step 3: Commit verification documentation**

```powershell
git add -- docs/protocol/compatibility.md docs/release/compatibility-matrix.md
git commit -m "docs: specify multi-report HID compatibility"
```

- [ ] **Step 4: Build the distributable**

Use version `0.1.0-rc1`, backend `PIO_USB`, and a new non-overwriting diagnostic output directory. Run the release script and its own contract tests. Record the packaged U1 path, byte length, and SHA-256; verify it matches the source U1 UF2.

- [ ] **Step 5: Stop for exact-image approval**

Present the exact U1 UF2 path and SHA-256. Do not write any RP2040 volume until the user approves that exact pair. Do not flash U2.

- [ ] **Step 6: Flash only approved U1 and verify enumeration**

Require a removable FAT `RPI-RP2` volume with `INFO_UF2.TXT` naming `Raspberry Pi RP2`, copy only the approved U1 file, require the volume to disappear, and require `VID_1209/PID_D101` to re-enumerate on a COM port.

- [ ] **Step 7: Run bench acceptance**

With Keychron replugged last:

1. refresh/export diagnostics and require interface 2 accepted entries `(keyboard,1,8)`, `(consumer,2,2)`, `(keyboard,12,20)`;
2. use Mouse -> Detect and require the side button to capture as the same source-qualified interface;
3. bind the side button and switch mouse routing PC1 -> PC2 -> PC1;
4. verify Keychron primary buttons, pointer, wheel, and consumer controls remain functional;
5. attach Trust GTX105 and repeat side-button detection plus primary controls to prove the generic path did not regress a different vendor;
6. record actual results in `docs/release/compatibility-matrix.md`; do not label unmeasured checks as passed.

- [ ] **Step 8: Commit measured results**

```powershell
git add -- docs/release/compatibility-matrix.md
git commit -m "docs: record multi-report HID bench acceptance"
```
