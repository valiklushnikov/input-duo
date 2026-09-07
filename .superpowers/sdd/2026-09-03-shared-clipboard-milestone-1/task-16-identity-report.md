# Task 16 (identity persistence) — closing the "unsafe against interruption" review item

## Status

DONE.

## What was reviewed and why

`load_or_create` in `configurator/src/duo_input/clipboard/identity.py` writes
three files one after another: the private key, the certificate, then the
origin identifier. If the process is interrupted between any two of those
writes (power loss, process kill, disk error), the three files on disk can
stop agreeing with each other — for example a freshly generated key paired
with the previous run's certificate. An inconsistent pair means TLS cannot
come up at all (silent, unexplained failure), and a mismatched origin
identifier means the peer stops recognizing this machine's fingerprint.

## Chosen fix (per the ruling given for this task)

No format change, no single combined file, no atomic-write machinery.
Instead, `load_or_create` now validates the triple **on read** and, on any
disagreement, silently and completely regenerates the identity — exactly the
same outcome as when a file is simply missing. This was chosen because
read-time validation covers three failure modes at once (a torn write, a
substituted file, and a corrupted file), where atomic writes would only have
covered the first.

"Согласована" is checked as:

- all three files exist and can be parsed (garbage PEM is treated as absent);
- the private key's public key matches the certificate's public key
  (`RSAPublicNumbers` equality — the actual key material, not file bytes);
- the origin identifier file's content matches the identifier embedded in the
  certificate's subject Common Name (`"Duo Input {origin_id}"` — confirmed by
  reading `identity.py`'s existing certificate-building code, this is in fact
  where the origin id is placed);
- the certificate parses to a non-empty X.509 structure.

The origin-identifier file continues to be written **last** (this ordering
already existed in the code and is now called out explicitly in a comment and
in the module docstring): its absence alone is the cheapest signal of a torn
write, since every completed write leaves it present.

## Code changes

`configurator/src/duo_input/clipboard/identity.py`:

- Module docstring extended to describe the new consistency check alongside
  the pre-existing "missing file → regenerate" behavior, and to state that
  the origin file is written last on purpose.
- `load_or_create` now delegates the "all three files present" branch to a
  new `_read_if_consistent(...)` helper. If that helper returns `None` (any
  disagreement or corruption), execution falls through to the same
  regeneration path used when a file is missing.
- New `_read_if_consistent(key_path, certificate_path, origin_path) ->
  NodeIdentity | None`: parses all three files inside one `try/except
  ValueError` (covers malformed PEM in either the key or the certificate, and
  non-ASCII bytes in the origin file — `UnicodeDecodeError` is a `ValueError`
  subclass), keeps the existing RSA-vs-legacy-EC rejection, adds the
  public-key-match check, and adds the origin/CN match check. Any failure
  returns `None` without raising.
- Identity generation itself was extracted unchanged into
  `_create_and_persist(...)` (same file-write order as before: key, then
  certificate, then origin last) with a comment explaining why origin is
  written last.

No new dependencies. No changes to file names/formats/encodings.

`configurator/tests/clipboard/test_identity.py`:

- Added a `_generate_rsa_identity_files(origin_id=None)` helper that builds an
  independent RSA key + self-signed certificate pair, used to construct
  deliberately inconsistent on-disk states without depending on the module's
  own generation code.
- Added `_assert_returns_healthy_and_stable_identity(directory)` that calls
  `load_or_create`, asserts the returned identity is actually usable (key
  matches certificate's public key, fingerprint computable, origin matches
  the certificate's CN), and asserts a second call returns the identical
  identity (no further silent rotation).
- Seven new tests, one per inconsistency case:
  - `test_key_without_certificate_recreates_the_identity`
  - `test_certificate_without_key_recreates_the_identity`
  - `test_missing_origin_file_recreates_the_identity`
  - `test_key_from_one_identity_with_certificate_from_another_recreates_the_identity`
  - `test_origin_file_disagreeing_with_certificate_recreates_the_identity`
  - `test_corrupted_certificate_recreates_the_identity`
  - `test_corrupted_key_recreates_the_identity`
- The pre-existing `test_second_call_returns_the_same_identity` (and the
  other 8 pre-existing tests) were left untouched and re-verified green.

## Test run: scoped file

```text
Command (cwd configurator):
../.venv/Scripts/python.exe -m pytest tests/clipboard/test_identity.py -q

...............                                                          [100%]
15 passed in 1.83s
Exit: 0
```

(8 pre-existing tests + 7 new tests = 15.)

## Test run: full suite from repo root

```text
Command (repo root):
.venv/Scripts/python.exe -m pytest configurator/tests -q -c configurator/pyproject.toml

........................................................................ [  7%]
........................................................................ [ 14%]
........................................................................ [ 21%]
........................................................................ [ 28%]
........................................................................ [ 35%]
.......................ssss............................................. [ 42%]
........................................................................ [ 50%]
........................................................................ [ 57%]
........................................................................ [ 64%]
........................................................................ [ 71%]
........................................................................ [ 78%]
........................................................................ [ 85%]
........................................................................ [ 92%]
........................................................................ [100%]
1004 passed, 4 skipped in 58.23s
Exit: 0
```

997 → 1004 passed (+7, matching the seven new tests). Skip count unchanged.
Zero failures.

## Mutation verification (guard removed → test fails → guard restored → test passes)

Each mutation was applied with `sed`/a small Python patch script directly on
`configurator/src/duo_input/clipboard/identity.py`, verified against the
relevant test(s), then the file was restored byte-for-byte from a known-good
backup copy and `diff` was used to confirm the restore was exact before
re-running the full scoped suite (green each time).

1. **All-three-files existence guard** (covers: key-without-cert,
   cert-without-key, missing-origin-file). Mutated
   `if key_path.is_file() and certificate_path.is_file() and
   origin_path.is_file():` to `if True:`.
   Result: all three tests failed/errored (`FileNotFoundError` reading the
   missing file inside `_read_if_consistent`).
   ```text
   FAILED tests/clipboard/test_identity.py::test_key_without_certificate_recreates_the_identity
   FAILED tests/clipboard/test_identity.py::test_certificate_without_key_recreates_the_identity
   FAILED tests/clipboard/test_identity.py::test_missing_origin_file_recreates_the_identity
   3 failed, 12 deselected
   ```
   Restored → `15 passed`.

2. **Public-key match check** (covers: key from one identity + certificate
   from another). Removed the
   `if key.public_key().public_numbers() != certificate.public_key()...`
   block entirely.
   Result: test failed on the very first health assertion (key no longer
   matches certificate, because the mismatched pair was returned as-is).
   ```text
   FAILED tests/clipboard/test_identity.py::test_key_from_one_identity_with_certificate_from_another_recreates_the_identity
   AssertionError: assert <RSAPublicNumbers(...)> == <RSAPublicNumbers(...)>
   1 failed, 14 deselected
   ```
   Restored → `15 passed`.

3. **Origin/CN match check** (covers: origin file disagreeing with
   certificate). Removed the
   `if [attribute.value for ...] != [f"Duo Input {origin_id}"]:` block.
   Result: test failed because the returned identity's `origin_id` was the
   stale value from the disagreeing origin file, not a freshly generated one.
   ```text
   FAILED tests/clipboard/test_identity.py::test_origin_file_disagreeing_with_certificate_recreates_the_identity
   AssertionError: assert ['Duo Input b...'] == ['Duo Input 0...']
   1 failed, 14 deselected
   ```
   Restored → `15 passed`.

4. **Corruption catch** (covers: corrupted certificate, corrupted key).
   Changed `except ValueError:` to `except KeyError:` so the parse failure
   is no longer swallowed.
   Result: both tests failed with an uncaught `ValueError` propagating out of
   `_read_if_consistent`/`load_or_create` instead of triggering silent
   regeneration.
   ```text
   FAILED tests/clipboard/test_identity.py::test_corrupted_certificate_recreates_the_identity
   FAILED tests/clipboard/test_identity.py::test_corrupted_key_recreates_the_identity
   2 failed, 13 deselected
   ```
   Restored → `15 passed`, and a final `diff` against the known-good backup
   confirmed the source file was restored exactly (no leftover mutation
   artifacts before the final commit).

Every one of the seven required inconsistency cases is backed by a guard
whose removal makes its test fail, confirming the tests exercise real
production logic and not just an accepted-input path.

## Encoding confirmation

Both changed files were read back as raw bytes and decoded as UTF-8, with an
explicit check for a UTF-8 BOM:

```text
configurator/src/duo_input/clipboard/identity.py       BOM=False  len=7297
configurator/tests/clipboard/test_identity.py          BOM=False  len=10329
```

Both decode cleanly as UTF-8 and carry no BOM. Comments and docstrings in the
new code are in Russian, matching the surrounding module.

## Files touched

- `configurator/src/duo_input/clipboard/identity.py`
- `configurator/tests/clipboard/test_identity.py`
- this report

No firmware/keyboard file or unrelated production area was changed. No new
third-party dependency was introduced. `duo_input/clipboard/**` still does
not import `PySide6.QtWidgets` or `PySide6.QtGui` (verified with a grep over
`configurator/src/duo_input/clipboard/`).

## Concerns

None open.
