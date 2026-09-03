# Task 16: production wiring — report

## Status

DONE. `app.main` now acquires the single-instance lock and calls the shared
clipboard runtime assembler. The assembler returns before constructing any
clipboard identity/trust/coordinator/backend/tray object when
`clipboard/enabled=False`; when enabled it owns the coordinator, backend, and
tray through Qt parents, wires the page/tray/service signals, starts the
backend/coordinator, and stops them from `aboutToQuit`.

The approved ruling is included: `pairing_code_ready` opens a modal dialog
showing the candidate machine name and the exact six-character code (leading
zeroes preserved). Yes calls `confirm_pairing(candidate)` and No calls the new
public `reject_pairing(candidate)`. Both paths require object identity with the
currently active candidate, so an answer from a stale nested dialog cannot act
on a replacement link even when every candidate field, including fingerprint,
is identical. Active rejection sends `PAIR_CONFIRM {"agree": false}` before it
closes and ends the attempt.

## Files

- `configurator/src/duo_input/app.py`
- `configurator/src/duo_input/clipboard/coordinator.py`
- `configurator/tests/ui/test_runtime_wiring.py`
- `configurator/tests/clipboard/test_coordinator.py`
- `configurator/src/duo_input/resources/translations/duo_input_{ru,en}.ts`
- `configurator/src/duo_input/resources/translations/duo_input_{ru,en}.qm`
- this report

No firmware/keyboard file or unrelated production area was changed.

## TDD RED (BASE `449d5ca`)

Runtime tests were added before production changes.

```text
Command (cwd configurator):
../.venv/Scripts/python.exe -m pytest tests/ui/test_runtime_wiring.py -q

ERROR tests/ui/test_runtime_wiring.py
E   ImportError: cannot import name 'configure_runtime' from 'duo_input.app'
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.38s
Exit: 1
```

Coordinator refusal and same-fingerprint stale-dialog regressions were also
added before production changes.

```text
Command (cwd configurator):
../.venv/Scripts/python.exe -m pytest tests/clipboard/test_coordinator.py -q -k "local_rejection or stale_dialog"

FFF                                                                      [100%]
FAILED tests/clipboard/test_coordinator.py::test_local_rejection_sends_a_negative_confirmation_before_closing
  AttributeError: 'ClipboardCoordinator' object has no attribute 'reject_pairing'
FAILED tests/clipboard/test_coordinator.py::test_a_stale_dialog_cannot_confirm_a_new_link_with_the_same_candidate
  assert not True
FAILED tests/clipboard/test_coordinator.py::test_a_stale_dialog_cannot_reject_a_new_link_with_the_same_candidate
  AttributeError: 'ClipboardCoordinator' object has no attribute 'reject_pairing'
3 failed, 30 deselected in 0.40s
Exit: 1
```

The existing catalogue coverage test supplied the translation RED after the
new dialog strings entered production source and before either catalogue was
updated:

```text
Command (cwd configurator):
../.venv/Scripts/python.exe -m pytest tests/ui/test_localization.py -q -k "every_interface_string_is_in_the_catalogue"

FF                                                                       [100%]
FAILED tests/ui/test_localization.py::test_every_interface_string_is_in_the_catalogue[ru]
FAILED tests/ui/test_localization.py::test_every_interface_string_is_in_the_catalogue[en]
2 failed, 35 deselected in 0.38s
Exit: 1
```

## GREEN / focused verification

```text
Command (cwd configurator):
../.venv/Scripts/python.exe -m pytest tests/ui/test_runtime_wiring.py -q
.......                                                                  [100%]
7 passed in 4.33s
Exit: 0
```

```text
Command (cwd configurator):
../.venv/Scripts/python.exe -m pytest tests/clipboard/test_coordinator.py -q -k "local_rejection or stale_dialog"
...                                                                      [100%]
3 passed, 30 deselected in 0.18s
Exit: 0
```

```text
Command (cwd configurator):
../.venv/Scripts/python.exe -m pytest tests/clipboard/test_coordinator.py -q -k "not real_incoming_connection_during_pairing"
................................                                         [100%]
32 passed, 1 deselected in 1.92s
Exit: 0
```

```text
Command (cwd configurator):
../.venv/Scripts/python.exe -m pytest tests/ui/test_localization.py -q
.....................................                                    [100%]
37 passed in 6.75s
Exit: 0
```

```text
Command (repo root):
.venv/Scripts/python.exe tools/update_translations.py

Found 310 source text(s) (0 new and 310 already existing) [ru]
Found 310 source text(s) (0 new and 310 already existing) [en]
Generated 310 translation(s) (310 finished and 0 unfinished) [ru]
Generated 345 translation(s) (345 finished and 0 unfinished) [en]
every catalogue is complete
Exit: 0
```

## Full configurator suite

The required full suite was run from the repository root so the pre-existing
root-relative `tests/vectors/transport_vectors.json` fixture resolves:

```text
Command:
.venv/Scripts/python.exe -m pytest configurator/tests -q -c configurator/pyproject.toml

4 failed, 985 passed, 4 skipped, 3 errors in 87.74s (0:01:27)
Exit: 1
```

All seven non-green cases are the documented Windows/Qt real-TLS baseline:

- coordinator real incoming pairing TLS: 1 failure;
- `test_peer_link.py` real TLS: 3 failures;
- `test_end_to_end.py` real TLS fixture: 3 errors.

Each times out only after Qt reports:

```text
QtWarningMsg: Failed to import private key: "Unknown error occurred: -2146885630"
```

No new Task 16 test or non-real-TLS test failed. A literal run of the brief's
`cd configurator && ../.venv/... -m pytest -q` also cannot collect the
pre-existing `test_transport_vectors.py`, because that test opens the fixture
relative to the repository root; the root command above is the established
full-suite invocation and reaches all tests.

## Self-review

- Lifecycle: coordinator and tray are children of `QApplication`; backend is
  a child of coordinator and is also retained by `ClipboardService`. Shutdown
  calls backend stop before coordinator stop. The single-instance server stays
  in `main` scope through `application.exec()`.
- Signals: local snapshots flow backend -> service; state and peer changes flow
  coordinator -> tray/page; page pair/forget/address actions flow back to the
  coordinator; tray open/quit go to window/application; pairing code goes to
  the modal confirmation handler.
- Refusal ordering: `reject_pairing` sends negative `PAIR_CONFIRM` before
  `_abort_pairing()` closes the active link. A `finally` ensures cleanup if
  send raises; a second link/candidate identity check prevents cleanup from
  touching a replacement attempt after a synchronous callback.
- Stale safety: positive and negative responses compare the exact emitted
  candidate object, not fingerprint/value equality. Tests deliberately replace
  the link with an identical-field candidate and prove the stale answer cannot
  send, close, set agreement, or store trust.
- Disabled path: the first branch precedes identity creation and every runtime
  constructor; its test replaces all constructors with an exception to catch
  any future side effect.
- Localization: both source catalogues contain finished PairingDialog entries;
  both compiled catalogues were regenerated. Russian remains the first-run
  default (`DEFAULT_LANGUAGE == "ru"`, covered by localization suite).
- Hygiene: `git diff --check` is clean; `tools/update_translations.py --check`
  reports `every catalogue is complete`.

## Concerns

- The full suite remains non-green solely because this Windows Qt build cannot
  import the generated EC private keys (`-2146885630`); the same baseline is
  recorded by Task 15 and is outside Task 16 wiring.
- The brief's full-suite cwd is inconsistent with the older transport-vector
  test's root-relative fixture path; the root invocation is required on this
  checkout.
