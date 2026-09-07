# Per-Interface Input Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every HID interface an input source in its own right, so a mouse's extra buttons work whatever interface, usage or report shape the manufacturer chose - and delete the per-product knowledge the firmware carries today.

**Architecture:** Replace the two fixed `InputPipeline` instances (one keyboard, one mouse) with a fixed-capacity table of per-interface sources, each owning its own normalizer and identity. Events gain a one-byte runtime source index, resolved to `(VID, PID, interface)` only where capture answers the host. A binding stores that triple in the binding record's existing reserved bytes, so the record neither grows nor realigns.

**Tech Stack:** C++17 firmware on RP2040 (Pico SDK, TinyUSB, Pico-PIO-USB), doctest native suite under `tests/firmware_native`; Python 3.12 + PySide6 configurator with pytest under `configurator/tests`; protocol constants generated from `protocol/schema.json`.

**Spec:** `docs/superpowers/specs/2026-09-07-per-interface-input-sources-design.md` - read it before Task 1. The plan argues from the spec; where they disagree, the spec wins.

## Global Constraints

- **Root cause before fixes.** Every change traces to a line. A symptom fix is a failed task.
- **Every change ships a test that fails without it,** mutation-verified: delete or invert the change, watch the new test fail, restore it, re-run green. A test that passes against unchanged code is testing nothing and must be rewritten. State the mutation you performed in the task report.
- **Ninja does not track headers.** Any native build used as evidence must be configured fresh or built with `--clean-first`. An incremental run after a header edit is not evidence.
- **Test through the real interface on the host side.** A configurator test must drive the actual widget, not set the value behind the UI's back. This project already had a test pass against a completely dead handler because it set state directly.
- **PySide6 keeps only a weak reference to a signal receiver.** A handler whose owner is not retained is garbage-collected and the signal silently stops working. Anything connecting a signal must keep the receiver alive.
- **The binding record stays 12 bytes.** Offsets 0-5 are unchanged. The source qualifier occupies offsets 6-10; offset 11 stays reserved and zero.
- **All-zero source means "any source"** and is exactly today's behaviour. Existing configurations must load and behave identically. There is no migration step and none may be added.
- **Protocol identifiers live only in `protocol/schema.json`** and are generated into both sides. Never hand-write a constant that the generator owns.
- **No new per-product knowledge.** No VID, PID, usage or endpoint number naming a specific manufacturer may be added by any task in this plan. Tasks 1 and 6 delete the existing ones.
- Commit messages end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

## File Structure

**Firmware - the source table and the pipeline**

- `firmware/u1_main/input/events.hpp` - `InputEvent` gains `source_index`.
- `firmware/u1_main/input/source.hpp` - `SourceIdentity` gains `interface_number`; documents that `SourceEvent::source_id` is now an interface slot, not a port.
- `firmware/u1_main/input/pipeline.hpp` / `.cpp` - one pipeline serves one interface. Keychron code deleted.
- `firmware/u1_main/input/source_table.hpp` / `.cpp` - **new.** Fixed-capacity map from `(device address, instance)` to a slot holding identity plus normalizer. Owns slot lifetime and the reuse rule.
- `firmware/u1_main/main.cpp`, `firmware/u1_reference/main.cpp` - instantiate the table instead of two pipelines.
- `firmware/u1_main/pio_usb/device_registry.cpp`, `firmware/u1_reference/source_adapter.cpp` - stop routing auxiliary interfaces to the mouse port; give each its own slot.
- `firmware/u1_main/pio_usb/hid_setup.cpp` - `classify_hid_layout` already classifies one interface; it stops being collapsed per device.

**Firmware - configuration and mapping**

- `firmware/common/config/format.hpp` / `.cpp` - binding record reads the source triple; validation rejects a partially-zero triple.
- `firmware/u1_main/mapping/engine.cpp` - trigger match includes the source.
- `firmware/u1_main/mapping/capture.hpp` / `.cpp` - `CapturedTrigger` carries the source.
- `firmware/u1_main/config_service.cpp` - `CAPTURE_EVENT` payload carries the source.

**Protocol and documentation**

- `protocol/schema.json`, `protocol/config_format.md`, `docs/release/compatibility-matrix.md`.

**Host**

- `configurator/src/duo_input/domain/models.py` - `Trigger` gains `source`.
- `configurator/src/duo_input/domain/config_binary.py` - `_BINDING` struct and both directions.
- `configurator/src/duo_input/device/transactions.py` - `parse_capture_event` accepts 3 or 8 bytes.
- `configurator/src/duo_input/ui/bindings.py`, `configurator/src/duo_input/ui/mouse.py` - capture filter, labels, availability.
- `configurator/src/duo_input/ui/models/binding_table.py` - trigger labels carry the source.

---

### Task 1: The source table

Replace "one device is one kind" with "one interface is one source". This task is firmware-internal: nothing above the pipeline changes yet, and no binding, protocol or UI behaviour changes. The deliverable is that a composite device with three interfaces produces decoded events from every interface it exposes, each tagged with its own slot.

**Files:**
- Create: `firmware/u1_main/input/source_table.hpp`, `firmware/u1_main/input/source_table.cpp`
- Modify: `firmware/u1_main/input/events.hpp`, `firmware/u1_main/input/source.hpp`, `firmware/u1_main/input/pipeline.hpp`, `firmware/u1_main/input/pipeline.cpp`
- Test: `tests/firmware_native/test_source_table.cpp` (new), `tests/firmware_native/CMakeLists.txt`

**Interfaces:**
- Consumes: `SourceEvent`, `SourceIdentity`, `InputPipeline`, `IInputHandler` as they exist today.
- Produces:
  - `InputEvent::source_index` (`std::uint8_t`), zero-initialised.
  - `SourceIdentity::interface_number` (`std::uint8_t`).
  - `input::SourceTable` with:
    - `static constexpr std::size_t kMaxSources = 8;`
    - `explicit SourceTable(IInputHandler& handler);`
    - `void on_event(const SourceEvent& event, const SourceIdentity& identity, std::uint32_t now_ms);`
    - `bool resolve(std::uint8_t index, SourceIdentity& out) const;`
    - `std::uint32_t unclaimed_interfaces() const;`

- [ ] **Step 1: Write the failing test for per-interface slots**

Create `tests/firmware_native/test_source_table.cpp`. Use the existing native tests' doctest style and their recording `IInputHandler` fake - copy the fake's shape from `tests/firmware_native/test_input_pipeline.cpp` rather than inventing a new one.

```cpp
TEST_CASE("a composite device gets one slot per interface") {
    RecordingHandler handler;
    duo_input::u1::input::SourceTable table(handler);

    // Mouse interface: descriptor-derived layout, five buttons.
    duo_input::u1::input::SourceIdentity mouse{};
    mouse.kind = duo_input::u1::input::DeviceKind::Mouse;
    mouse.vendor_id = 0x1234;
    mouse.product_id = 0x5678;
    mouse.interface_number = 0;
    mouse.mouse_layout = duo_input::u1::input::hid::boot_mouse_layout();

    // Keyboard interface on the SAME device, a different instance.
    duo_input::u1::input::SourceIdentity keyboard{};
    keyboard.kind = duo_input::u1::input::DeviceKind::Keyboard;
    keyboard.vendor_id = 0x1234;
    keyboard.product_id = 0x5678;
    keyboard.interface_number = 1;
    keyboard.keyboard_layout = duo_input::u1::input::hid::boot_keyboard_layout();

    table.on_event(ready_event(/*source_id=*/0), mouse, 0);
    table.on_event(ready_event(/*source_id=*/1), keyboard, 0);

    // A left click on interface 0.
    const std::uint8_t click[3] = {0x01, 0x00, 0x00};
    table.on_event(report_event(0, click, sizeof(click)), mouse, 1);

    // Usage 0x4F held on interface 1 - a keyboard-shaped shortcut, which is
    // how the bench mouse emits its side button.
    const std::uint8_t shortcut[8] = {0x01, 0x00, 0x4F, 0, 0, 0, 0, 0};
    table.on_event(report_event(1, shortcut, sizeof(shortcut)), keyboard, 2);

    REQUIRE(handler.events.size() == 2);
    CHECK(handler.events[0].kind == duo_input::u1::input::InputEventKind::MouseButtonDown);
    CHECK(handler.events[0].source_index == 0);
    CHECK(handler.events[1].kind == duo_input::u1::input::InputEventKind::KeyDown);
    CHECK(handler.events[1].code == 0x4F);
    CHECK(handler.events[1].source_index == 1);
}
```

Add a second test in the same file asserting normalizer state is not shared: two mouse interfaces on one device, button 1 held on slot 0, then a zero-buttons report on slot 1, and assert **no** release is emitted for slot 0.

Add a third asserting slot exhaustion: fill `kMaxSources` slots, offer a ninth `Ready`, and assert it is refused (no events from it, `unclaimed_interfaces() == 1`) and that no existing slot was displaced.

- [ ] **Step 2: Run the tests and watch them fail**

```
cmake --preset native
cmake --build --preset native --clean-first
ctest --preset native -R source_table --output-on-failure
```

Expected: compile failure - `SourceTable` does not exist, and `InputEvent` has no `source_index`.

- [ ] **Step 3: Add the two struct fields**

In `firmware/u1_main/input/events.hpp`, add to `InputEvent`:

```cpp
    /// Which source slot produced this. A runtime index into SourceTable,
    /// never stored in a configuration: a slot freed by unplugging one device
    /// is reused by the next, so a saved index would retarget a binding at
    /// whatever was plugged in afterwards. Bindings match on the identity
    /// SourceTable::resolve hands back, not on this.
    std::uint8_t source_index = 0;
```

In `firmware/u1_main/input/source.hpp`, add to `SourceIdentity`:

```cpp
    /// This interface's number within its device's configuration. Together
    /// with the VID and PID it is what a saved binding matches on.
    std::uint8_t interface_number = 0;
```

`SourceIdentity` must stay trivially copyable - the existing `static_assert` proves it.

- [ ] **Step 4: Write `SourceTable`**

`source_table.hpp` declares a fixed array of `kMaxSources` slots. Each slot holds `{bool occupied; std::uint8_t source_id; SourceIdentity identity; InputPipeline pipeline;}`. `InputPipeline` keeps its current single-kind shape - one pipeline now genuinely serves one interface, which is what it was always written for.

Rules the implementation must honour, each of which a test above pins:

- `Ready` claims the slot matching `event.source_id`, or the first free slot; a full table refuses and increments `unclaimed_interfaces_`, and never displaces an occupied slot.
- `Report` and `AuxiliaryReport` are both routed to their own slot's pipeline and decoded through that slot's layout. **`AuxiliaryReport` is no longer a special case** - an interface that has a slot is read as itself.
- `Detached` and `Fault` release the slot's held input through the existing `on_detached` path, then free the slot.
- Every event the slot's pipeline emits is stamped with the slot index before it reaches the handler.

- [ ] **Step 5: Delete the Keychron path**

From `pipeline.cpp` and `pipeline.hpp` remove `keychron_side_state()`, `kKeychronVendorId`, `kKeychronProductId`, `kKeychronSideUsage`, `kMouseButton4`, `on_auxiliary_report()`, `keychron_receiver_`, `keychron_side_button_held_`, `keychron_side_presses_`, `keychron_side_releases_` and their accessors, and the `on_detached` branch that synthesises a release for the latched side button.

Remove the `AuxiliaryReport` case from `InputPipeline::on_event` - the table routes it now.

Delete or rewrite any test asserting the deleted behaviour. `tests/firmware_native/test_pio_usb_mouse.cpp:778` and `tests/firmware_native/test_pio_usb_recovery.cpp:936` both reference it by name; read each and decide whether the test still means something without the quirk. A test kept alive by editing its comment is a test that no longer tests anything.

- [ ] **Step 6: Run the tests**

```
cmake --build --preset native --clean-first
ctest --preset native --output-on-failure
```

Expected: all pass.

- [ ] **Step 7: Mutation-verify**

Invert the slot-stamping (`source_index` always 0) and confirm the first test fails. Restore. Then share one normalizer across slots and confirm the second test fails. Restore. Re-run green. Record both in the task report.

- [ ] **Step 8: Commit**

```bash
git add firmware/u1_main/input tests/firmware_native
git commit
```

---

### Task 2: Wire both backends to the table

Task 1 built the table; nothing feeds it yet. This task replaces the two fixed pipelines in both firmware targets and removes the magic endpoint number that the two backends fill in differently.

**Files:**
- Modify: `firmware/u1_main/main.cpp:202-203,470,545-550`, `firmware/u1_reference/main.cpp:352-353`, `firmware/u1_main/pio_usb/device_registry.cpp`, `firmware/u1_reference/source_adapter.cpp`, `firmware/u1_main/pio_usb/hid_setup.cpp`, `firmware/u1_main/pio_usb/hid_setup.hpp`
- Test: `tests/firmware_native/test_pio_usb_device_registry.cpp`, `tests/firmware_native/test_reference_source_adapter.cpp` (whichever exists; create the reference one if it does not)

**Interfaces:**
- Consumes: `SourceTable` from Task 1.
- Produces: both adapters emit one `Ready` per claimed HID interface, each carrying `interface_number`, and no longer emit `AuxiliaryReport` at all.

- [ ] **Step 1: Write the failing test**

In the registry test, mount a three-interface composite device (mouse, keyboard, and one interface whose descriptor does not classify) and assert:

- three `Ready` events, with `interface_number` 0, 1, 2 and distinct `source_id`s;
- the unclassifiable interface still produces `Report` events - it is claimed and polled, only not decoded;
- no `AuxiliaryReport` is emitted by either adapter.

The third assertion is the load-bearing one: the 2026-09-02 Keychron resolution records that polling every auxiliary endpoint is what stopped the receiver wedging, so an interface being unreadable must not stop it being serviced.

- [ ] **Step 2: Run and watch it fail**

```
cmake --build --preset native --clean-first
ctest --preset native -R "device_registry|source_adapter" --output-on-failure
```

- [ ] **Step 3: Delete the auxiliary special-casing**

In `firmware/u1_reference/source_adapter.cpp`: delete the `is_keychron_auxiliary_interface` branch in `on_mount` and the `Role::Auxiliary` arm that pushes `AuxiliaryReport` with `record.instance` as the endpoint. Every mounted interface gets its own slot and its own `Ready`.

In `firmware/u1_main/pio_usb/device_registry.cpp`: delete `kKeychronAuxiliaryEndpoint` and the branch that pushes `AuxiliaryReport` for it.

In `firmware/u1_main/pio_usb/hid_setup.cpp` / `.hpp`: delete `is_keychron_auxiliary_interface` and `kKeychronAuxiliaryVendorId` / `kKeychronAuxiliaryProductId`.

An interface `classify_hid_layout` cannot read keeps `DeviceKind::Unknown` and is still claimed, still polled, and simply produces no events.

- [ ] **Step 4: Replace the two pipelines**

In both `main.cpp` files, replace `g_keyboard_pipeline` / `g_mouse_pipeline` with a single `duo_input::u1::input::SourceTable g_sources(g_input);` and route every source event to it. Delete the `pipelines[2]` arrays and the `DeviceKind`-to-pipeline mapping around `main.cpp:545`.

- [ ] **Step 5: Run the full native suite**

```
cmake --build --preset native --clean-first
ctest --preset native --output-on-failure
```

- [ ] **Step 6: Mutation-verify**

Make the unclassifiable interface unclaimed again and confirm the "still polled" assertion fails. Restore, re-run green.

- [ ] **Step 7: Commit**

---

### Task 3: The source qualifier in the binding record

**Files:**
- Modify: `protocol/config_format.md`, `firmware/common/config/format.hpp`, `firmware/common/config/format.cpp`, `configurator/src/duo_input/domain/models.py`, `configurator/src/duo_input/domain/config_binary.py`
- Test: `tests/firmware_native/test_config_format.cpp`, `configurator/tests/domain/test_config_binary.py`

**Interfaces:**
- Produces:
  - Firmware `config::TriggerSource { std::uint16_t vendor_id; std::uint16_t product_id; std::uint8_t interface_number; }`, and `config::BindingView::source()`.
  - Host `duo_input.domain.models.TriggerSource` frozen dataclass with the same three fields, and `Trigger.source: TriggerSource | None = None`. `None` means "any source".

- [ ] **Step 1: Write the failing round-trip test on the host**

```python
def test_binding_round_trips_its_source():
    source = TriggerSource(vendor_id=0x3434, product_id=0xD030, interface_number=1)
    trigger = Trigger(TriggerKind.KEYBOARD_USAGE, 0x4F, modifiers=0x01, source=source)
    ...
    assert decoded.bindings[0].trigger.source == source


def test_all_zero_source_decodes_as_any():
    # A configuration written before this field existed has six zero bytes
    # there, and must keep meaning "any source".
    blob = build_config_with_zero_reserved_bytes()
    assert decode(blob).bindings[0].trigger.source is None
```

The second test is the compatibility guarantee and must be written against a byte blob built the old way, not against the new encoder.

- [ ] **Step 2: Run and watch both fail**

```
python -m pytest configurator/tests/domain/test_config_binary.py -v
```

- [ ] **Step 3: Change the struct on the host**

In `config_binary.py`, `_BINDING` becomes:

```python
# kind, code, modifiers, mode, action kind, argument, VID, PID, interface,
# reserved. Twelve bytes, as before: the source occupies bytes 6-10, which
# were reserved and zero, and byte 11 stays reserved.
_BINDING = struct.Struct("<BBBBBBHHBB")
assert _BINDING.size == BINDING_SIZE
```

Pack `0, 0, 0` for a `None` source; decode all-zero back to `None`. A partially-zero triple - a VID with no PID, say - is a corrupt record and must raise the same error the file already raises for a malformed binding, not be silently coerced.

- [ ] **Step 4: Mirror it in the firmware and the format document**

`format.cpp` reads the three fields at offsets 6, 8 and 10 and validation rejects a partially-zero triple. Update the binding-record table in `protocol/config_format.md`, and change the duplicate rule there from `(kind, code, modifiers)` to `(kind, code, modifiers, source)`.

- [ ] **Step 5: Widen the duplicate rule in both implementations**

Firmware validation and host validation both currently reject exact `(kind, code, modifiers)` duplicates. Add the source to the comparison in both, with a test on each side proving that the same key from two different sources is now accepted and that a true duplicate is still rejected.

- [ ] **Step 6: Run both suites**

```
cmake --build --preset native --clean-first
ctest --preset native --output-on-failure
python -m pytest configurator/tests -q
```

- [ ] **Step 7: Mutation-verify**

Make the decoder return a zero-filled `TriggerSource` instead of `None` and confirm `test_all_zero_source_decodes_as_any` fails. Restore. Remove the source from the duplicate comparison and confirm the two-sources test fails. Restore, re-run green.

- [ ] **Step 8: Commit**

---

### Task 4: Match bindings on their source

**Files:**
- Modify: `firmware/u1_main/mapping/engine.cpp`, `firmware/u1_main/mapping/engine.hpp`
- Test: `tests/firmware_native/test_mapping_engine.cpp`

**Interfaces:**
- Consumes: `InputEvent::source_index` (Task 1), `SourceTable::resolve` (Task 1), `config::BindingView::source()` (Task 3).

- [ ] **Step 1: Write the failing test**

Two bindings on the same key, one qualified to source A and one to source B, plus a third with no source. Assert: an event from A fires A's binding and the unqualified one, and never B's; an event from an unknown source fires only the unqualified one.

- [ ] **Step 2: Run and watch it fail**

- [ ] **Step 3: Implement the match**

The engine resolves the event's `source_index` through the table once per event, not once per binding. A binding with an all-zero source matches any event. A qualified binding matches only when VID, PID and interface number all agree.

- [ ] **Step 4: Run, mutation-verify, commit**

Invert the "all-zero matches anything" rule and confirm the unqualified-binding assertion fails. Restore.

---

### Task 5: Carry the source through capture

**Files:**
- Modify: `protocol/schema.json`, `firmware/u1_main/mapping/capture.hpp`, `firmware/u1_main/mapping/capture.cpp`, `firmware/u1_main/config_service.cpp`, `configurator/src/duo_input/device/transactions.py`
- Test: `tests/firmware_native/test_capture.cpp`, `tests/firmware_native/test_config_service.cpp`, `configurator/tests/device/test_transactions.py`

**Interfaces:**
- Produces: `CAPTURE_EVENT` payload of 8 bytes - kind, code, modifiers, VID (2), PID (2), interface - with the 3-byte form still accepted by the host as "source unknown".

- [ ] **Step 1: Write the failing host test**

```python
def test_capture_event_carries_its_source():
    payload = struct.pack("<BBBHHB", TriggerKind.KEYBOARD_USAGE, 0x4F, 0x01,
                          0x3434, 0xD030, 1)
    trigger = parse_capture_event(payload)
    assert trigger.source == TriggerSource(0x3434, 0xD030, 1)


def test_short_capture_event_still_parses():
    # The emulator and older firmware send three bytes; refusing them would
    # break the compatibility matrix this repository ships.
    trigger = parse_capture_event(struct.pack("<BBB", TriggerKind.MOUSE_BUTTON, 4, 0))
    assert trigger.source is None
```

Keep the existing range check: a mouse-button trigger outside 1..5, or one carrying modifiers, is still refused.

- [ ] **Step 2: Run and watch them fail**

- [ ] **Step 3: Widen the payload**

`CapturedTrigger` gains the three fields; `CaptureController::handle` fills them from the event's source, resolved through the table. `config_service.cpp` writes the 8-byte payload. Update the payload size in `protocol/schema.json` and regenerate both sides with `tools/generate_protocol.py` - do not hand-edit generated files.

- [ ] **Step 4: Run both suites, mutation-verify, commit**

Make the host reject the 3-byte form and confirm `test_short_capture_event_still_parses` fails. Restore.

---

### Task 6: The configurator

The task that makes the feature visible. Its first step fixes the filter that would otherwise hide everything the previous five tasks built.

**Files:**
- Modify: `configurator/src/duo_input/ui/bindings.py:83-200`, `configurator/src/duo_input/ui/mouse.py:286-330`, `configurator/src/duo_input/ui/models/binding_table.py`
- Test: `configurator/tests/ui/test_mouse_switch.py`, `configurator/tests/ui/test_bindings.py`

- [ ] **Step 1: Write the failing UI test - through the real button**

Drive the actual control: click the "Detect button" widget with `QTest.mouseClick`, feed the service a capture event carrying a keyboard usage from the mouse's own source, and assert the dialog accepts it and the binding editor selects it. Do not call `apply_captured_trigger` directly - that is the test this project has already been burned by.

Keep the assertions ASCII. `QTest.keyClicks` crashes with `0xC0000409` on Cyrillic input; type ASCII and put any Russian on the assertion side.

Retain a reference to any object whose signal you connect: PySide6 holds the receiver weakly and a garbage-collected handler makes the signal silently stop.

- [ ] **Step 2: Run and watch it fail**

```
python -m pytest configurator/tests/ui/test_mouse_switch.py -v
```

Expected: the trigger is rejected - `_on_capture_received` discards a non-`MOUSE_BUTTON` kind and re-arms.

- [ ] **Step 3: Replace the kind filter with a source filter**

`CaptureDialog` currently takes `accepted_kind: TriggerKind | None`. Replace it on the mouse page's call with an acceptance predicate that asks whether the trigger's source belongs to the attached mouse, whatever its kind. A trigger with no source keeps today's behaviour, so the general bindings page is unaffected.

- [ ] **Step 4: Label triggers by what they are and where they came from**

`_mouse_button_label` and the binding table's trigger column render `Ctrl+Right - Keychron Link (interface 1)`. The device name comes from the peripheral report the diagnostics already carry; fall back to `VID:PID` when the device reported no product string.

- [ ] **Step 5: Availability**

A binding whose source is not attached shows as unavailable, through the same path `MouseCapabilities.allows()` uses today. Add a test that a binding qualified to an absent source is greyed out and one qualified to the attached mouse is not.

- [ ] **Step 6: Run the host suite, mutation-verify, commit**

Restore the kind filter and confirm the Step 1 test fails. Restore.

---

### Task 7: Documentation and the acceptance procedure

**Files:**
- Modify: `docs/release/compatibility-matrix.md`, `docs/testing/` (the acceptance document covering input), `README.md` if it describes binding triggers
- Create: nothing

- [ ] **Step 1: Record the wire and format changes**

The compatibility matrix gains: `CAPTURE_EVENT` 8-byte payload with the 3-byte form accepted; binding record schema minor bump; all-zero source means any source.

- [ ] **Step 2: Write the bench acceptance procedure**

Two checks, both required, both with the expected result written down before anyone runs them:

1. **Function.** With the Keychron M3 attached, bind its side button through `Мышь → определить кнопку` and confirm it switches the mouse between PC1 and PC2. Confirm buttons 1-3 and the wheel are unaffected.
2. **Cost.** Measure the **per-endpoint poll rate** before and after this branch, on the same hardware with the same devices. The poll interval is per endpoint while the token budget is per device, and this branch takes a mouse from one polled endpoint to three. Record both numbers. If the per-endpoint rate falls far enough to be felt, the spec's fallback applies: poll undecodable interfaces at a lower rate than input ones. That is a real possible outcome, not a formality - do not write "no regression observed" without the two numbers beside it.

- [ ] **Step 3: Commit**

---

## Self-Review

**Spec coverage.** Scope and the interface-as-source model - Tasks 1 and 2. Sources table, per-source normalizers, runtime-only index, table-full behaviour - Task 1. Undecodable interfaces still polled - Task 2 Step 1, asserted. Deletions - Tasks 1 and 2. Configuration format, reserved bytes, all-zero semantics, uniqueness rule - Task 3. Protocol, both payload lengths - Task 5. UI filter, presentation, availability - Task 6. Three levels of verification - native throughout, host in Tasks 3, 5 and 6, bench in Task 7. Bus-load risk - Task 7 Step 2. No gaps found.

**Placeholders.** None. Task 2 Step 1 says "whichever exists; create the reference one if it does not", which is a real instruction with a defined outcome, not a deferral.

**Type consistency.** `TriggerSource` carries `vendor_id`, `product_id`, `interface_number` on both sides, and is spelled that way in Tasks 3, 4, 5 and 6. `InputEvent::source_index` and `SourceIdentity::interface_number` are introduced in Task 1 and used under those names in Tasks 2 and 4. `SourceTable::resolve` is declared in Task 1 and consumed in Tasks 4 and 5.

**One known ordering constraint.** Task 6's Step 3 depends on Task 5's source reaching the host. Do not reorder them.
