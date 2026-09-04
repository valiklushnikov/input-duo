# Task 7 report: shared HID report-descriptor classification

## RED evidence

The workspace was already an isolated linked worktree on
`feature/pio-usb-host-hub-v1` at the required base
`3636b379f42bda266e4be7cdd436d0e72a0d87e8`. The clean baseline was:

```
$ ctest --test-dir build/native --output-on-failure
39/39 passed
```

I added the focused setup test before any setup production API. From the VS
developer environment its first build failed only on the missing API header:

```
$ cmake --build build/native --target duo_pio_usb_hid_setup_test --parallel
test_pio_usb_hid_setup.cpp(1): fatal error C1083:
  cannot open include file: pio_usb/hid_setup.hpp: No such file or directory
```

After the classifier itself was green, registry integration tests were added.
The pre-integration registry run failed because a descriptor-defined mouse with
TinyUSB protocol `None` remained ignored:

```
$ cmake --build build/native --target duo_pio_usb_device_registry_test --parallel
$ ctest --test-dir build/native -R "^pio_usb_device_registry$" --output-on-failure
...test_pio_usb_device_registry.cpp:471: check failed: mouse != nullptr
...test_pio_usb_device_registry.cpp:527: check failed:
  identity.kind == DeviceKind::Mouse
```

Classification was then moved into `DeviceRegistry::process()`, after callback
capture, and the focused registry target passed.

## Implementation

`classify_hid(protocol, descriptor, length, SourceIdentity&)` clears the full
identity first. If bytes are present, it hashes exactly the supplied range and
calls the shared `classify_report_descriptor()` composition, which invokes the
existing bounded keyboard and mouse parsers. Exactly one successful parser
wins. Two successful parsers produce `Ambiguous` and are rejected without boot
fallback. Only when no usable descriptor role exists does TinyUSB boot keyboard
or mouse protocol select the explicit existing boot layout.

No parser logic was copied into the PIO backend. The shared parsers retain the
existing 64-byte keyboard report ceiling, fixed report/global/usage tables, and
bounded mouse fields. Production adds no heap allocation or unbounded stored
descriptor: the registry callback still copies at most its fixed 256 bytes.

Registry mount processing now clears a free interface record, classifies its
bounded copied descriptor, then applies fresh VID/PID. Descriptor absence stays
`descriptor_present == false` with an all-zero hash. Role ownership remains
first-usable and deterministic; ignored interfaces remain mounted and armed for
bounded servicing. Queue capacity, pending-fault gates, one-arm/re-arm flow,
host clock/SPI initialization, and Task 10's receive-refusal seam were not
changed.

The PIO-only source list now links `hid_setup.cpp`; CH375 continues to share
only the report-descriptor parser composition.

## Fixture and behavior coverage

The new setup target has 9 focused cases and explicitly reads all eight files
under `tests/vectors/hid_descriptors`: `boot_keyboard.bin`, `boot_mouse.bin`,
`consumer_composite.bin`, `hub.bin`, `impossible_report_size.bin`,
`mouse_5_button.bin`, `truncated_item.bin`, and `vendor_only.bin`. These are USB
configuration fixtures, so the report classifier hashes but does not mistake
them for report layouts.

It also covers the 77-byte Aula F75 report fixture, a Report-ID keyboard, a
five-button wheel mouse, a valid vendor-only HID report, valid consumer-only
report, truncated/malformed input, an empty callback descriptor, and a combined
keyboard-plus-mouse descriptor. Descriptor-defined layouts are tested under the
opposite boot protocol to prove they are never overwritten by boot fallback.

Task 6's deferred minor is covered in both focused and registry tests:

- absence is distinct from SHA-256(empty);
- `abc` and prefix-equal `abcd` have literal, independently known exact hashes;
- a descriptor-defined five-button wheel mouse retains its neutral button,
  X/Y, wheel, and minimum-length layout;
- unmount/reconnect replaces old mouse layout, VID/PID, descriptor presence,
  and hash with a fresh absent-descriptor boot keyboard identity.

The callback boundary test observes no registry entry and no receive arm after
`tuh_hid_mount_cb`; only the subsequent ordinary `process_pending()` call
classifies the mouse and arms its report. `tinyusb_host_callbacks.cpp` remains
bounded lookup/copy/queue-only.

## Mutation validation

Each requested realistic mutation was applied temporarily, the focused target
was rebuilt, and the mutation was restored:

```
ambiguous descriptor allowed to reach protocol fallback: 9 tests, 3 failures
non-null zero-length descriptor hashed as SHA-256(empty): 9 tests, 1 failure
initial SourceIdentity clear removed:                 9 tests, 4 failures
boot protocol applied before descriptor parsing:     9 tests, 24 failures
```

The final restored binaries report:

```
duo_pio_usb_hid_setup_test.exe:       9 tests, 0 failures
duo_pio_usb_device_registry_test.exe: 22 tests, 0 failures
```

## GREEN verification

Commands were run from the VS x64 developer environment where MSVC was needed.

```
$ cmake --build build/native --target \
    duo_pio_usb_hid_setup_test duo_report_descriptor_test \
    duo_normalizers_test duo_input_pipeline_test \
    duo_pio_usb_device_registry_test --parallel
$ ctest --test-dir build/native \
    -R "^(pio_usb_hid_setup|report_descriptor|normalizers|input_pipeline|pio_usb_device_registry)$" \
    --output-on-failure
5/5 passed

$ cmake --build build/native --parallel
$ ctest --test-dir build/native --output-on-failure
40/40 passed

$ .superpowers/runtime-venv/Scripts/python.exe tests/fuzz/generate_corpus.py --check
$ cmake -S . -B build/native-fuzz-smoke -G Ninja \
    -DDUO_NATIVE_TESTS=ON \
    -DCMAKE_MAKE_PROGRAM=.superpowers/runtime-venv/Scripts/ninja.exe
$ cmake --build build/native-fuzz-smoke --parallel
$ ctest --test-dir build/native-fuzz-smoke --output-on-failure -R "^fuzz_corpus_"
3/3 passed

$ cmake --build --preset pico-pio-usb-release --parallel
exit 0; PIO toolchain lock verified; U1 linked with hid_setup.cpp

$ cmake --build --preset pico-release --parallel
exit 0; CH375 U1 linked with the shared parser change

$ .superpowers/runtime-venv/Scripts/python.exe -m pytest \
    tests/build/test_pio_usb_firmware_contract.py \
    tests/build/test_firmware_artifacts.py \
    tests/build/test_pio_usb_toolchain_lock.py -q
47 passed

$ git diff --check
exit 0
```

The first fresh `build/native-fuzz-smoke` configure attempt could not locate
Ninja on `PATH`. The rerun above named the repository runtime environment's
exact `ninja.exe`, then configured, built all 219 objects, and passed all three
corpus tests.

## Self-review and concerns

The classification path is fixed-memory and transport-neutral. Exact hashing
happens before parsing so malformed and unsupported supplied bytes remain
diagnostically distinct, while null or zero length does not hash the empty
message. Ambiguity is terminal and cannot be rescued by a boot protocol.
Descriptor success takes precedence over protocol for both roles.

The Task 7 brief points to `docs/release/firmware-build.md` for the corpus smoke
command, but that file at BASE contains no fuzz/corpus command. The repository's
actual documented portable command is in `docs/testing/fuzzing.md`; that exact
workflow was used, with the explicit local Ninja path noted above.

No hardware was flashed or otherwise touched. COM20 remains U1 master and
COM21 remains U2 for the later hardware gate. Task 10 still owns retry policy
after `tuh_hid_receive_report()` refusal.
