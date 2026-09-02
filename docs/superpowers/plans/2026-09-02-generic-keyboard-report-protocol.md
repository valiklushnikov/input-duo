# Generic Keyboard Report Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route ordinary USB HID keyboards in their declared report protocol so short Aula F75 receiver keystrokes are not lost, while retaining boot fallback and all existing mouse behaviour.

**Architecture:** Extend the bounded HID report-descriptor layer with a keyboard layout, then give that layout to a generalized keyboard normalizer. `DescriptorSetup` prefers a validated report layout and selects boot only for boot-capable keyboards whose descriptor cannot be used; code above `InputPipeline` remains event-based.

**Tech Stack:** C++17, Pico SDK/RP2040, CH375 USB host, TinyUSB HID, CMake/Ninja, native doctest-style tests, UF2 probe diagnostics over CDC.

**Spec:** `docs/superpowers/specs/2026-09-02-generic-keyboard-report-protocol-design.md`

## Global Constraints

- No keyboard VID/PID branches.
- Keep the fixed boot layout as fallback for boot-capable keyboards.
- Do not change mouse parsing, Keychron M3 auxiliary routing, Trust behaviour, U1/U2 USB descriptors, or the six-key output contract.
- Reject malformed, ambiguous, truncated, oversized, or unrepresentable reports whole.
- More than six non-modifier keys preserve the previous state until a representable report arrives.
- Every production change starts with a focused test observed failing for the expected reason.
- Run native builds from the VS developer environment:

```powershell
$vs = 'C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\Tools\VsDevCmd.bat'
$command = 'call "' + $vs + '" -arch=x64 -host_arch=x64 >nul && cmake --build build\native -j 8 && ctest --test-dir build\native --output-on-failure'
& cmd.exe /d /s /c $command
```

---

### Task 1: Capture the real Aula keyboard report descriptor

**Files:**
- Modify: `firmware/u1_main/ch375/descriptor_setup.hpp`
- Modify: `firmware/u1_main/ch375/descriptor_setup.cpp`
- Modify: `firmware/u1_main/main.cpp`
- Modify: `tests/firmware_native/test_ch375_descriptor_setup.cpp`
- Modify: `tests/firmware_native/fakes/scripted_ch375.hpp`
- Modify: `tests/firmware_native/fakes/scripted_ch375.cpp`
- Create from capture: `tests/vectors/usb_descriptors/aula_f75_keyboard_report.hex`

**Interfaces:**
- Consumes: the existing bounded multi-packet report-descriptor transfer.
- Produces: exact retained keyboard descriptor bytes and a fixture; still selects boot.

- [ ] **Step 1: Extend the fake keyboard**

Add `FakeCh375Chip::serve_report_keyboard(const std::vector<std::uint8_t>& descriptor,
bool boot_capable = true)`. Its configuration HID descriptor declares `descriptor.size()`;
endpoint 1 has `wMaxPacketSize=8` and `bInterval=1`. Reuse the fake's existing report
descriptor request machinery.

- [ ] **Step 2: Write the failing setup test**

```cpp
TEST_CASE(a_keyboard_descriptor_is_captured_before_boot_is_selected) {
    Rig rig;
    const std::vector<std::uint8_t> descriptor = {
        0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00,
        0x25, 0x01, 0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
        0x75, 0x08, 0x95, 0x06, 0x19, 0x00, 0x29, 0x65,
        0x81, 0x00,
    };
    rig.chip.attach_device();
    rig.chip.serve_report_keyboard(descriptor);
    rig.begin(rig.chip.now_us());
    CHECK_EQ(static_cast<int>(rig.settle()), static_cast<int>(SetupProgress::Done));
    CHECK_EQ(rig.setup.report_descriptor_bytes(), descriptor.size());
    CHECK(rig.setup.boot_protocol_selected());
}
```

- [ ] **Step 3: Verify RED**

Build and run `duo_ch375_descriptor_setup_test.exe`. Expected: the new test fails because
the keyboard path retains zero descriptor bytes and goes directly to boot.

- [ ] **Step 4: Fetch without changing protocol**

Let `request_report_descriptor()` use the existing transfer for mice and keyboards. In
`apply_report_descriptor()`, add a temporary keyboard branch that records
`kKeyboardReportDescriptorCaptured` and calls `select_boot_protocol(now_us)`. Keep the mouse
branch semantically unchanged. In probe diagnostics print:

```text
kbd-desc=<received>/<wanted>:<uppercase hex bytes>
```

Print it only for the keyboard and retain the response-buffer truncation guard.

- [ ] **Step 5: Verify GREEN**

Run `duo_ch375_descriptor_setup_test.exe`. Expected: all tests pass and the new test proves
boot is selected after capture.

- [ ] **Step 6: Capture hardware evidence**

Build `build\pico-ch375`, hash and flash only U1 after verifying the destination label is
`RPI-RP2`, wait for COM18, then run `tools\keychron_probe.py COM18`. Decode `kbd-desc`, verify
the byte count and SHA-256, and use `apply_patch` to place the exact continuous hex string
plus a final newline in the fixture. Never infer or hand-edit missing bytes. Add a local test
helper that rejects non-hex characters or an odd digit count and converts pairs to bytes.

- [ ] **Step 7: Commit**

```powershell
git add firmware/u1_main/ch375/descriptor_setup.hpp firmware/u1_main/ch375/descriptor_setup.cpp firmware/u1_main/main.cpp tests/firmware_native/test_ch375_descriptor_setup.cpp tests/firmware_native/fakes/scripted_ch375.hpp tests/firmware_native/fakes/scripted_ch375.cpp tests/vectors/usb_descriptors/aula_f75_keyboard_report.hex
git commit -m "Capture keyboard HID report descriptors"
```

---

### Task 2: Parse array and NKRO keyboard layouts

**Files:**
- Modify: `firmware/u1_main/ch375/report_descriptor.hpp`
- Modify: `firmware/u1_main/ch375/report_descriptor.cpp`
- Modify: `tests/firmware_native/test_report_descriptor.cpp`
- Modify: `tests/firmware_native/CMakeLists.txt`

**Interfaces:**
- Produces: `boot_keyboard_layout()` and
  `parse_keyboard_report_descriptor(protocol::ByteView, KeyboardReportLayout&)`.

- [ ] **Step 1: Add RED tests against this desired API**

```cpp
enum class KeyboardFieldKind : std::uint8_t { None, Array, Bitmap };
inline constexpr std::uint16_t kNoKeyboardBit = 0xFFFF;
struct KeyboardReportLayout {
    bool report_id = false;
    std::uint8_t report_id_value = 0;
    std::uint16_t modifier_bits[8] = {
        kNoKeyboardBit, kNoKeyboardBit, kNoKeyboardBit, kNoKeyboardBit,
        kNoKeyboardBit, kNoKeyboardBit, kNoKeyboardBit, kNoKeyboardBit,
    };
    KeyboardFieldKind key_kind = KeyboardFieldKind::None;
    std::uint16_t key_bit_offset = 0;
    std::uint8_t key_element_bits = 0;
    std::uint8_t key_element_count = 0;
    std::uint16_t key_usage_minimum = 0;
    std::uint16_t key_usage_maximum = 0;
    std::uint8_t minimum_body_bytes = 0;
};
```

Append, without renumbering existing errors: `NoKeyboardReport`,
`AmbiguousKeyboardReport`, and `MalformedGlobalState`.

- [ ] **Step 2: Cover exact layouts and refusals**

Add focused tests named:

```cpp
the_fixed_boot_keyboard_layout_is_explicit
the_captured_aula_keyboard_descriptor_has_one_usable_layout
a_report_id_keyboard_keeps_offsets_inside_its_own_report
an_eight_byte_array_keyboard_records_modifiers_and_six_slots
an_nkro_bitmap_records_its_usage_range
global_push_and_pop_restore_keyboard_report_size_and_count
output_feature_and_long_items_do_not_move_the_keyboard_input_cursor
two_competing_keyboard_input_reports_are_refused
a_truncated_keyboard_item_leaves_the_callers_layout_unchanged
global_stack_underflow_and_overflow_are_refused
a_non_keyboard_descriptor_is_refused_by_name
```

Expected offsets for Aula come from the captured fixture, not a standard-layout assumption.

- [ ] **Step 3: Verify RED**

Run `duo_report_descriptor_test.exe`. Expected: the keyboard parser API or its assertions
fail; existing mouse assertions remain green.

- [ ] **Step 4: Implement the bounded parser**

Track usage page, logical bounds, report size/count, and independent Input bit cursors for at
most eight Report IDs. Use a checked four-entry global Push/Pop stack. Recognize page `0x07`;
map variable one-bit `E0-E7` usages to modifier offsets; accept one Data/Array key field or
one Data/Variable one-bit usage range. Reject offsets beyond a 64-byte report, array elements
wider than 16 bits, Report ID zero, invalid ranges, and a second keyboard report. Assign
`out` only after the entire descriptor succeeds. Skip bounded long items, advance offsets
only for Input Main items, and clear local usages/ranges after every Main item.

- [ ] **Step 5: Verify GREEN and commit**

Run `duo_report_descriptor_test.exe`, then commit parser source, tests, CMake change, and the
fixture reference with message `Parse generic keyboard HID report layouts`.

---

### Task 3: Normalize declared keyboard reports atomically

**Files:**
- Modify: `firmware/u1_main/input/keyboard_normalizer.hpp`
- Modify: `firmware/u1_main/input/keyboard_normalizer.cpp`
- Modify: `tests/firmware_native/test_normalizers.cpp`

**Interfaces:**
- Consumes: `ch375::KeyboardReportLayout`.
- Produces: `void KeyboardNormalizer::set_layout(const ch375::KeyboardReportLayout&)`;
  existing `apply()` and `release_all()` signatures stay unchanged.

- [ ] **Step 1: Write array/report-ID RED tests**

Assert literal event sequences for Report-ID press/release, modifier changes, reordered
arrays, release-before-press ordering, modifier ordering, wrong ID, short report, zero/error
usage filtering, lone Aula `0x01`, six-slot ErrorRollOver, and native-layout `release_all()`.
Wrong-ID and short-report cases must prove the following valid release still sees the prior
state.

- [ ] **Step 2: Write NKRO/capacity RED tests**

Use a bitmap covering usages `0x04..0x73`. Assert non-adjacent bits, a one-key release, seven
held keys producing no events while preserving state, and the following representable full
transition. Retain the insufficient-output-capacity test.

- [ ] **Step 3: Verify RED**

Run `duo_normalizers_test.exe`. Expected: failure because `set_layout` and native extraction
do not exist.

- [ ] **Step 4: Implement extraction**

Default to `boot_keyboard_layout()`. Validate/strip Report ID; read modifier bits; extract and
deduplicate bounded little-endian array elements or scan the bitmap usage range. Accumulate
seven distinct keys so overflow seventh is detected, not truncated. Only six `0x01` array values
mean rollover. Validate report length, layout, key count, and event capacity before emitting
or mutating held state.

- [ ] **Step 5: Verify GREEN and commit**

Run `duo_normalizers_test.exe`. Commit the three files with message
`Normalize keyboard report protocol layouts`.

---

### Task 4: Prefer report protocol with safe boot fallback

**Files:**
- Modify: `firmware/u1_main/ch375/descriptor_setup.hpp`
- Modify: `firmware/u1_main/ch375/descriptor_setup.cpp`
- Modify: `tests/firmware_native/test_ch375_descriptor_setup.cpp`
- Modify: `tests/firmware_native/fakes/scripted_ch375.hpp`
- Modify: `tests/firmware_native/fakes/scripted_ch375.cpp`

**Interfaces:**
- Consumes: `parse_keyboard_report_descriptor()` and `boot_keyboard_layout()`.
- Produces: `keyboard_layout()` and `has_keyboard_layout()` accessors.

- [ ] **Step 1: Write report-selection RED tests**

Prove a parseable report keyboard reaches `Done`, retains its layout, and sends no request
`0x0B` with value 0. Prove a second `begin()` clears the first keyboard layout.

- [ ] **Step 2: Write fallback RED tests**

For a boot-capable keyboard cover refused, repeatedly silent, empty, malformed, and oversized
descriptors; each must finish with the fixed boot layout only after successful boot selection.
The same malformed descriptor on a non-boot keyboard must fail as unsupported.
Also retain tests for a boot `SET_PROTOCOL` STALL and silence: the keyboard remains usable,
and the existing distinct diagnostic status is preserved.

- [ ] **Step 3: Verify RED**

Run `duo_ch375_descriptor_setup_test.exe`. Expected: a parseable keyboard still selects boot,
and malformed non-boot keyboards still finish.

- [ ] **Step 4: Implement selection**

Reset keyboard layout to boot and `have_keyboard_layout_` to false in `begin()`. Dispatch
descriptor parsing by kind. A valid keyboard layout calls `finish(kReportDescriptorUsed)`
without `SET_PROTOCOL`. A keyboard descriptor failure uses:

```cpp
SetupProgress DescriptorSetup::fallback_keyboard_to_boot(std::uint32_t now_us) {
    if (!capabilities_.boot_protocol) return fail(kEndedUnsupported);
    keyboard_layout_ = boot_keyboard_layout();
    have_keyboard_layout_ = false;
    return select_boot_protocol(now_us);
}
```

Do not send mouse failures through this helper.

- [ ] **Step 5: Verify GREEN and commit**

Run `duo_ch375_descriptor_setup_test.exe` and `duo_ch375_device_test.exe`. Commit with message
`Prefer keyboard HID report protocol`.

---

### Task 5: Carry keyboard layouts through the pipeline

**Files:**
- Modify: `firmware/u1_main/input/pipeline.hpp`
- Modify: `firmware/u1_main/input/pipeline.cpp`
- Modify: `firmware/u1_main/main.cpp`
- Modify: `tests/firmware_native/test_input_pipeline.cpp`
- Modify: `docs/hardware/ch375-compatibility.md`

**Interfaces:**
- Consumes: both layouts from `DescriptorSetup`.
- Produces: layout-aware `InputPipeline::set_kind()` and `on_event()`.

- [ ] **Step 1: Write pipeline RED tests**

Add a Report-ID keyboard Ready/report/release sequence. Add regressions for existing boot
traces and Keychron M3 endpoint-1 auxiliary side-button reports.

- [ ] **Step 2: Verify RED**

Run `duo_input_pipeline_test.exe`. Expected: report-ID keyboard input is interpreted through
the old boot layout or cannot be passed through the old API.

- [ ] **Step 3: Implement these signatures**

```cpp
void set_kind(ch375::DeviceKind kind,
              const ch375::KeyboardReportLayout& keyboard_layout,
              const ch375::MouseReportLayout& mouse_layout);
void on_event(const ch375::Ch375Event& event, ch375::DeviceKind kind,
              const ch375::KeyboardReportLayout& keyboard_layout,
              const ch375::MouseReportLayout& mouse_layout,
              std::uint32_t now_ms, std::uint16_t vendor_id = 0,
              std::uint16_t product_id = 0);
```

Set both normalizer layouts on every Ready and update both Core 1 call sites with layouts
from the matching `DescriptorSetup`.

- [ ] **Step 4: Update diagnostics and compatibility docs**

Print `klayout=report` or `klayout=boot`, retaining descriptor status, lengths, and hash.
Document report-first/boot-fallback behaviour and preserve the lone-`0x01` observation.

- [ ] **Step 5: Verify GREEN and commit**

Run pipeline, normalizer, descriptor-setup, trace-replay, and report-descriptor executables.
Commit with message `Route native keyboard HID reports`.

---

### Task 6: Full verification and hardware acceptance

**Files:**
- Create: `docs/superpowers/records/2026-09-02-keyboard-report-protocol-execution.md`
- Modify only if evidence requires: `docs/hardware/ch375-compatibility.md`

**Interfaces:**
- Consumes: feature branch, Aula receiver, wired keyboard, Trust mouse, Keychron M3, U1 COM18,
  U2, and two computers.
- Produces: verified hashes and an evidence record ready for integration.

- [ ] **Step 1: Run all software gates**

From the VS environment run the full native build and
`ctest --test-dir build\native --output-on-failure`. Then run:

```powershell
.\.venv\Scripts\python.exe -m pytest configurator/tests tests -q
.\.venv\Scripts\python.exe -m pytest tests/build/test_firmware_artifacts.py -q
```

Record exact pass/skip counts rather than describing a partial run as the full suite.

- [ ] **Step 2: Build and hash firmware**

Build `build\pico-release` and `build\pico-ch375`; hash release U1/U2 and probe U1 UF2 files.

- [ ] **Step 3: Gate hardware typing on probe diagnostics**

Flash probe U1 after checking `RPI-RP2`. Require the Aula row to show VID/PID `3554:FA09`,
keyboard, boot advertised but not selected, `klayout=report`, complete descriptor length,
nonzero hash, zero failed polls, and zero dropped commands. Stop if any field fails.

- [ ] **Step 4: Run keyboard acceptance**

Through CH375 test repeated fixed groups and sustained rapid/ordinary typing on PC1; Shift,
Ctrl, Alt, repeat, release, receiver reconnect, U2 switching/return, and BOTH routing. The
operator must observe no missing or stuck keys. Repeat basic typing and disconnect recovery
with the wired keyboard.

- [ ] **Step 5: Run mouse regression acceptance**

Verify Trust movement/buttons/wheel. Verify Keychron M3 smooth movement, wheel, side-button
switch to U2 and return to U1, receiver reconnect, and continued movement.

- [ ] **Step 6: Verify the release candidate**

Flash release U1 and repeat a shorter keyboard/routing and mouse smoke pass. Read release
diagnostics. If report protocol does not remove loss, restore pre-feature release SHA-256
`D2072DFF3CF958F6FDC9B43C5278E81F316F7EB529B1B78809E9D37F2518B81B` and record the
hypothesis as disproved.

- [ ] **Step 7: Record and commit evidence**

Write commands, exact test counts, hashes, diagnostics, user observations, and remaining
limits to the execution record. Commit it with message
`Record keyboard report protocol verification`. Require a clean branch, then use
`verification-before-completion`, `requesting-code-review`, and
`finishing-a-development-branch`.
