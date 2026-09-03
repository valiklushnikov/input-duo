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

---

# Fix round 1/5: security rendering, owned translations, working Windows TLS

## Findings fixed

1. The security prompt is now a real `QMessageBox` configured with
   `Qt.TextFormat.PlainText`. An untrusted candidate name such as
   `<b>NOT THE REAL NAME</b>` is rendered literally and cannot turn the prompt
   into rich text that changes or hides the six-digit code.
2. The prompt no longer uses Qt's unowned standard Yes/No labels. It creates
   application-owned AcceptRole/RejectRole buttons named `Связать` / `Отказать`,
   with `Pair` / `Reject` in the English catalogue. The reject button is both
   default and Escape; only the exact accept button object produces consent.
3. Node identities now use RSA-2048 certificates/private keys, which the Qt
   Schannel backend shipped in the Windows development environment can import.
   Persisted EC identities from earlier builds are rotated as one unit before
   TLS uses them; this changes origin/fingerprint and intentionally requires a
   new pairing instead of leaving clipboard sharing permanently unable to
   connect. `ssl_configuration` loads the generated key as
   `QSsl.KeyAlgorithm.Rsa`.

## TLS root-cause investigation

The smallest failing real test consistently timed out only after Schannel
reported the private-key import error. Runtime inspection on the exact pinned
PySide6 6.10.1 environment produced:

```text
Qt 6.10.1
available ['cert-only', 'schannel']
active schannel
supportsSsl True
build Secure Channel (NTDDI: 0xA00000C)
runtime Secure Channel, Windows 10.0.19045
features ['SupportedFeature.ClientSideAlpn', 'SupportedFeature.ServerSideAlpn']
protocols [..., 'SslProtocol.TlsV1_2OrLater']
key_header b'-----BEGIN PRIVATE KEY-----'
key_is_null False algorithm KeyAlgorithm.Ec length 256
```

The native code `-2146885630` is `0x80092002` (`CRYPT_E_BAD_ENCODE`). Changing
the same EC key from PKCS#8 to SEC1 did not alter the result:

```text
qt.tlsbackend.schannel: Failed to import private key:
"Unknown error occurred: -2146885630"
1 failed in 5.30s
```

One controlled diagnostic changed only the generated key/certificate to
RSA-2048 and the `QSslKey` algorithm to RSA, then ran the unchanged smallest
real handshake test:

```text
.
1 passed in 0.26s
```

This isolates the root cause to Schannel's import of the generated EC private
identity, not discovery, sockets, certificate pinning, timeout length, or PEM
container choice.

Packaged-context review found the current Nuitka dist already contains:

```text
qcertonlybackend.dll
qopensslbackend.dll
qschannelbackend.dll
libcrypto-3.dll
libssl-3.dll
```

Thus no forced backend selection or packaging-script change is necessary:
RSA works with Schannel in development and is also supported by the packaged
OpenSSL/Schannel options. The real dist contract remains green (`16 passed`).

## TDD RED on FIX_BASE `b054cfd`

### Real message-box behavior

Tests were changed first to exercise a real `QMessageBox`, its `textFormat`,
its real buttons/roles, and translations loaded through `TranslationManager`.

```text
Command (cwd configurator):
../.venv/Scripts/python.exe -m pytest tests/ui/test_runtime_wiring.py -q -k "pairing_dialog"

F.FFF                                                                    [100%]
FAILED tests/ui/test_runtime_wiring.py::test_pairing_dialog_accepts_the_exact_candidate_and_six_digit_code
  IndexError: list index out of range
FAILED tests/ui/test_runtime_wiring.py::test_pairing_dialog_renders_an_untrusted_machine_name_as_plain_text
  AttributeError: module 'duo_input.app' has no attribute '_pairing_confirmation_dialog'
FAILED tests/ui/test_runtime_wiring.py::test_pairing_dialog_owns_localized_button_labels[ru-...]
  AttributeError: module 'duo_input.app' has no attribute '_pairing_confirmation_dialog'
FAILED tests/ui/test_runtime_wiring.py::test_pairing_dialog_owns_localized_button_labels[en-Pair-Reject]
  AttributeError: module 'duo_input.app' has no attribute '_pairing_confirmation_dialog'
4 failed, 1 passed, 5 deselected in 1.40s
Exit: 1
```

After the real box existed but before the new button sources were added to the
catalogues, the actual English rendering stayed Russian as predicted:

```text
....F                                                                    [100%]
E       AssertionError: assert 'Связать' == 'Pair'
1 failed, 4 passed, 5 deselected in 1.55s
Exit: 1
```

### Identity/backend compatibility

```text
Command (cwd configurator):
../.venv/Scripts/python.exe -m pytest tests/clipboard/test_identity.py -q -k "schannel or persisted_legacy"

FF                                                                       [100%]
FAILED tests/clipboard/test_identity.py::test_certificate_uses_an_rsa_key_that_qt_schannel_can_import
FAILED tests/clipboard/test_identity.py::test_a_persisted_legacy_ec_identity_is_replaced_before_tls_uses_it
2 failed, 6 deselected in 0.22s
Exit: 1
```

The added load-bearing real-network regression failed on EC exactly like the
seven previously waived tests:

```text
Command (cwd configurator):
../.venv/Scripts/python.exe -m pytest tests/clipboard/test_peer_link.py::test_a_fresh_identity_completes_a_real_tls_handshake_on_the_active_backend -q -s

qt.tlsbackend.schannel: Failed to import private key: "Unknown error occurred: -2146885630"
FAILED tests/clipboard/test_peer_link.py::test_a_fresh_identity_completes_a_real_tls_handshake_on_the_active_backend
1 failed in 5.25s
Exit: 1
```

## GREEN / verification

```text
Command (cwd configurator):
../.venv/Scripts/python.exe -m pytest tests/ui/test_runtime_wiring.py -q -k "pairing_dialog"
.....                                                                    [100%]
5 passed, 5 deselected in 1.46s
Exit: 0
```

```text
Command (cwd configurator):
../.venv/Scripts/python.exe -m pytest tests/clipboard/test_identity.py -q -k "schannel or persisted_legacy"
..                                                                       [100%]
2 passed, 6 deselected in 0.31s
Exit: 0
```

```text
Command (cwd configurator):
../.venv/Scripts/python.exe -m pytest tests/clipboard/test_peer_link.py::test_a_fresh_identity_completes_a_real_tls_handshake_on_the_active_backend -q -s
.
1 passed in 0.32s
Exit: 0
```

All security/identity/real-TLS coordinator paths together:

```text
Command (cwd configurator):
../.venv/Scripts/python.exe -m pytest tests/clipboard/test_identity.py tests/clipboard/test_peer_link.py tests/clipboard/test_end_to_end.py tests/clipboard/test_coordinator.py -q
...................................................................      [100%]
67 passed in 8.01s
Exit: 0
```

Runtime wiring plus the full localization tests:

```text
Command (cwd configurator):
../.venv/Scripts/python.exe -m pytest tests/ui/test_runtime_wiring.py tests/ui/test_localization.py -q
...............................................                          [100%]
47 passed in 12.10s
Exit: 0
```

Full configurator suite, with no TLS exclusions/xfails/mocks:

```text
Command (repo root):
.venv/Scripts/python.exe -m pytest configurator/tests -q -c configurator/pyproject.toml
........................................................................ [  7%]
........................................................................ [ 14%]
........................................................................ [ 21%]
........................................................................ [ 28%]
........................................................................ [ 35%]
................ssss.................................................... [ 43%]
........................................................................ [ 50%]
........................................................................ [ 57%]
........................................................................ [ 64%]
........................................................................ [ 71%]
........................................................................ [ 79%]
........................................................................ [ 86%]
........................................................................ [ 93%]
.................................................................        [100%]
997 passed, 4 skipped in 59.35s
Exit: 0
```

Translation generation/check completed with 312 finished Russian sources and
347 finished English entries; both `.qm` files were regenerated.

## Fix-round self-review

- Security rendering: `setTextFormat(PlainText)` is set on the real dialog
  before its text. The regression uses an HTML-looking untrusted name and
  reads the real dialog property/text, not arguments to a mocked static helper.
- Consent controls: no standard buttons exist (`standardButtons() == NoButton`);
  both application-owned labels are asserted from real buttons under real
  Russian and English translators. Closing/Escape/default all choose rejection;
  acceptance requires identity with the one AcceptRole button.
- TLS: fresh identity, persisted-EC migration, pinning/refusal, mutual message
  transfer, clipboard end-to-end, and coordinator pairing now use real
  Schannel TLS and pass. No assertion was skipped, mocked, weakened, or xfailed.
- Backend contexts: dev has only active Schannel and passes RSA; the existing
  standalone dist carries Schannel and OpenSSL plugins plus OpenSSL libraries.
  RSA avoids backend-specific identity generation and requires no global Qt
  backend override.
- Pairing security consequence: legacy EC rotation changes both fingerprint
  and origin ID, so existing trust cannot silently survive a new key. A user
  must explicitly pair again, which is the safe failure mode.

## Fix-round concerns

None open. The four suite skips are unchanged pre-existing conditional tests;
all 997 collected runnable tests, including every real-TLS test, pass.
