# File-transfer acceptance record (Task 4.4)

**Date:** 2026-09-16
**Implementation under acceptance:** `86a0ffc971369b421491f2d41034d896dd8bad8e`
**Branch:** `feature/pio-usb-host-hub-v1`
**Specification:** `docs/superpowers/specs/2026-09-12-file-transfer-design.md`
**Plan:** `docs/superpowers/plans/2026-09-12-file-transfer-implementation.md`

## Outcome

**Hardware acceptance passed: 15 of 15 matrix rows PASS.** The operator
exercised the matrix with two real laptops on one LAN, paired and connected,
with both file-transfer toggles on. For each row the stated procedure was used:
`Ctrl+C` in Explorer on PC1, switch, and `Ctrl+V` in a folder on PC2.
Rows exercised: 1-15; rows not run: none.

The operator supplied PASS for every row. This record spells out only the
planned acceptance conditions established by those results and does not add
machine identities, transfer timings, network counters, hashes, or exact
filenames that were not supplied.

## Two-machine Explorer matrix

| # | Case | Result | What this establishes |
| --- | --- | --- | --- |
| 1 | single file | **PASS** | The file arrives byte-for-byte; its digest matches. |
| 2 | multiple files | **PASS** | All files arrive; names are unchanged. |
| 3 | nested directory | **PASS** | The structure is recreated; no file is misplaced. |
| 4 | Unicode name (Cyrillic, and one astral character) | **PASS** | The name is intact on disk. |
| 5 | zero-byte file | **PASS** | The file is created with size 0 and no error. |
| 6 | file above 1 GB | **PASS** | The transfer completes; native progress moves; memory is flat. A real 2 GB file was copied. Windows Task Manager showed the DuoInput process using up to about 50 MB memory during the run. |
| 7 | repeated `Ctrl+V` | **PASS** | The second paste works; two copies are present. |
| 8 | source changed between copy and paste | **PASS** | Explorer reports an error; no truncated file is left. After Ctrl+C, the source file was deleted; Ctrl+V on PC2 showed the Russian message `Ошибка на диске в процессе чтения`; PASS also establishes the row's required no-truncated-file condition. |
| 9 | peer disconnected mid-transfer | **PASS** | Explorer reports an error; its partial file is gone. |
| 10 | Cancel mid-transfer | **PASS** | The transfer stops promptly; no further network traffic occurs. |
| 11 | destination collision | **PASS** | Explorer's own replace/skip/keep-both dialog appears. |
| 12 | `Ctrl+X` on source | **PASS** | Paste copies; the source on PC1 still exists. |
| 13 | clipboard changed on PC1 during an active transfer | **PASS** | The running transfer completes unaffected. |
| 14 | folder containing a junction | **PASS** | The junction is skipped; the skip is reported; its target is not exported. |
| 15 | old client ↔ new client | **PASS** | Clipboard still works both ways; no file offer is sent; neither side drops the connection. |

## Packaged evidence already established by Task 4.3

This is separate automated evidence, not the hardware matrix. On implementation
commit `86a0ffc971369b421491f2d41034d896dd8bad8e`, Task 4.3 produced a fresh
Nuitka standalone build using the documented packaging-only fallback after the
standard wrapper's tests printed an all-passed summary and then hit a
pre-existing interpreter-shutdown access violation. The completed build reported:

```text
Nuitka 4.1.3 / Python 3.12 / MSVC cl 14.5
Compiled 131 C files using clcache (111 hits, 20 misses)
Successfully created configurator\dist\app.dist\DuoInput.exe
19 passed in 1.90s
Duo Input 0.1.0 built into configurator\dist\DuoInput
```

An independent run of the 19 packaged tests then reported:

```text
19 passed in 1.78s
PACKAGING_EXIT=0
```

The packaged executable's direct self-check reported:

```text
files: ok
callback: addref 2, release 1
descriptor: 592
DIRECT_EXE_EXIT=0
```

This establishes only that the packaged executable can load the transfer code,
construct its `IDataObject`, serialise the expected descriptor layout, and
invoke the generated `IUnknown::AddRef`/`IUnknown::Release` ctypes callbacks.
It does not replace the two-machine Explorer evidence above, nor does it
establish peak process memory, LAN interruption behaviour, or old/new peer
compatibility by itself.

## Final automated gate

The required command was run from the repository root with the virtual-environment
runner:

```text
.venv/Scripts/python.exe -m pytest configurator/tests tests -q
```

Exit status: **1**. The gate is **not fully green**. Its exact terminal summary
was:

```text
=========================== short test summary info ===========================
FAILED tests/build/test_build_release_plan.py::test_ch375_release_guards_only_the_ch375_directory_and_names_u1_plainly
FAILED tests/build/test_build_release_plan.py::test_pio_usb_release_guards_both_directories_and_names_u1_distinctly
FAILED tests/build/test_build_release_plan.py::test_reference_release_guards_both_directories_and_names_u1_distinctly
FAILED tests/build/test_build_release_plan.py::test_u2_source_is_identical_regardless_of_backend
FAILED tests/build/test_firmware_artifacts.py::test_the_image_dates_itself_by_the_source_not_by_the_clock[duo_u1_main.uf2]
FAILED tests/build/test_firmware_artifacts.py::test_the_image_dates_itself_by_the_source_not_by_the_clock[duo_u2_endpoint.uf2]
6 failed, 2312 passed, 11 skipped, 6 subtests passed in 133.60s (0:02:13)
```

These are the six known stale UF2-cache baseline failures. Both cached UF2
images in `build/pico-release` contain `Sep 10 2026`, while the source-derived
expected date is `Sep 14 2026`. The two firmware-artifact tests fail directly on
that mismatch. Each of the four release-plan tests invokes the same real
artifact guard during its dry run and fails because that guard encounters those
stale images. The caches were intentionally neither touched nor rebuilt.

No additional failure identity appeared. The automated gate therefore remains
non-green at the known baseline and does not indicate a file-transfer
regression; it is separate from the successful 15/15 hardware matrix.

### Eleven skipped tests

A supplementary run with `-rs` produced the same `6 failed, 2312 passed,
11 skipped, 6 subtests passed` counts and exposed these skip identities and
reasons:

- `configurator/tests/clipboard/test_macos_pasteboard.py` (module collection):
  `AppKit` is not installed on this Windows environment.
- `configurator/tests/clipboard/test_platform_backend.py::test_darwin_platform_builds_the_macos_backend`:
  the macOS backend imports the native macOS pasteboard and this host is not
  macOS.
- `configurator/tests/integration/test_real_config_contract.py::test_a_configuration_written_to_the_device_reads_back_identically`:
  `DUO_INPUT_HIL_WRITE=1` was not set, so the test did not write hardware.
- `configurator/tests/integration/test_real_config_contract.py::test_reading_the_configuration_back_returns_the_same_bytes`:
  `DUO_INPUT_HIL_WRITE=1` was not set.
- `configurator/tests/integration/test_real_config_contract.py::test_the_configuration_survives_a_reconnect`:
  `DUO_INPUT_HIL_WRITE=1` was not set.
- `configurator/tests/integration/test_real_config_contract.py::test_a_second_write_lands_in_the_other_slot_and_still_reads_back`:
  `DUO_INPUT_HIL_WRITE=1` was not set.
- `configurator/tests/transfer/test_windows_publisher.py::test_publishing_puts_our_data_object_on_the_real_clipboard`:
  `DUO_INPUT_COM_SESSION=1` was not set for a real desktop COM session.
- `configurator/tests/transfer/test_windows_publisher.py::test_the_contents_format_is_available_on_the_real_clipboard`:
  `DUO_INPUT_COM_SESSION=1` was not set for a real desktop COM session.
- `tests/build/test_backend_artifacts.py::test_pio_usb_images_link_the_irq_budgeted_core1_stack`:
  the selected build directory does not run the PIO USB host on Core 1.
- `tests/build/test_backend_artifacts.py::test_a_pio_usb_declared_build_actually_links_pio_usb_and_not_ch375`:
  the selected build directory is not configured for `PIO_USB`.
- `tests/build/test_backend_artifacts.py::test_a_reference_declared_build_links_only_the_new_input_path`:
  the selected build directory is not configured for `PIO_USB_REFERENCE`.

## What this record establishes

- The required two-machine Explorer acceptance was exercised and all 15 planned
  rows passed.
- Row 6 includes the supplied observation that a real 2 GB file was copied and
  Task Manager showed the DuoInput process using up to about 50 MB
  memory during the run.
- Row 8 includes the supplied Explorer error, `Ошибка на диске в процессе
  чтения`, after the source file was deleted following Ctrl+C, and establishes
  the required absence of a truncated destination file.
- The packaged ctypes construction/callback path had already passed its scoped
  Task 4.3 checks, with the limits stated above.
- The final repository gate remains non-green at six known stale UF2-cache
  failures: 6 failed, 2312 passed, 11 skipped, and 6 subtests passed. This
  baseline state is distinct from the successful hardware acceptance.

## Final conclusion

The file-transfer feature is **hardware-accepted: 15/15 PASS** on the two-laptop
Explorer matrix. The automated repository gate remains non-green only because
of the six known stale UF2-cache baseline failures recorded above; that separate
baseline result does not change the successful hardware matrix outcome.
