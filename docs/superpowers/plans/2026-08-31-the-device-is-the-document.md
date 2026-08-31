# The Device Is The Document Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the device the thing being edited and the file a copy of it, so the program stops asking about a document that no longer exists.

**Architecture:** One substitution drives everything: `ProjectSession.baseline` stops being set when a file is written and starts being set when the project and the device agree. Autosave, the last-project memory, the close prompt and two of three state chips exist only to serve the old document model and are deleted.

**Tech Stack:** Python 3.12, PySide6 (Qt 6), pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-08-31-the-device-is-the-document-design.md`

## Global Constraints

- **Working directory:** `C:\Users\Valentyn\Documents\Codex\2026-08-01\new-chat\work\duo-input-mvp`. All paths relative to it.
- **Run pytest from the repository root:** `.venv/Scripts/python.exe -m pytest configurator/tests -q`. From inside `configurator/` an unrelated test breaks.
- **"Changed" means "differs from the device", never "differs from the file."** Every decision in this plan follows from that sentence; if a step seems to contradict it, the step is wrong.
- **`dirty` stays load-bearing.** It guards `_adopt_device_project` against a device read overwriting edits in progress. Moving its reference point must not weaken that guard: a read landing while the operator is editing must still be refused.
- **New user-visible strings** need both catalogues regenerated: `.venv/Scripts/python.exe tools/update_translations.py`, fill the Russian side in `configurator/src/duo_input/resources/translations/duo_input_ru.ts`, run again until it reports `every catalogue is complete`.
- **Deleting a string is also a catalogue change** — run the tool after removals too.
- **Commit after every task.** Never use bare `git stash` — this worktree shares its stash stack.

---

### Task 1: Move the reference point

**Files:**
- Modify: `configurator/src/duo_input/ui/models/project_session.py:545-556` (`save`), and add a new method
- Modify: `configurator/src/duo_input/ui/main_window.py` (`_adopt_device_project`, and the write-success path)
- Test: `configurator/tests/ui/test_project_session.py`, `configurator/tests/ui/test_main_window.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ProjectSession.agreeing_with_device() -> ProjectSession` — returns a copy whose `baseline` is the current project, meaning "this is what the device holds". Tasks 2 and 4 rely on `dirty` following from it.

This is the whole change. Everything after it is removal.

- [ ] **Step 1: Write the failing tests**

Add to `configurator/tests/ui/test_project_session.py`:

```python
def test_agreeing_with_the_device_clears_the_change_marker():
    """After a read or a write, the project and the board are the same thing."""
    session = ProjectSession.new().apply(RenameProfile(1, "Edited"))
    assert session.dirty is True

    agreed = session.agreeing_with_device()

    assert agreed.dirty is False
    assert agreed.project == session.project


def test_an_edit_after_agreeing_reads_as_changed_again():
    session = ProjectSession.new().agreeing_with_device()

    edited = session.apply(RenameProfile(1, "Since"))

    assert edited.dirty is True


def test_saving_a_copy_does_not_clear_the_change_marker(tmp_path):
    """A file is a copy, not the truth. Writing one changes nothing about
    whether the board is up to date."""
    session = ProjectSession.new().apply(RenameProfile(1, "Edited"))

    saved = session.save(tmp_path / "copy.duoinput.json")

    assert saved.dirty is True
```

`RenameProfile` and `ProjectSession` are already imported in that file; check the existing imports rather than adding duplicates.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_project_session.py -q -k "agreeing or saving_a_copy"`
Expected: FAIL — `agreeing_with_device` does not exist, and `save` currently clears `dirty`.

- [ ] **Step 3: Add the method and stop `save` from resetting the baseline**

In `configurator/src/duo_input/ui/models/project_session.py`, add beside the other `with_*` methods:

```python
    def agreeing_with_device(self) -> ProjectSession:
        """Mark the project as being what the device holds.

        Called after a configuration is read from the device and after one is
        written to it - the two moments the two are known to be the same. The
        baseline is what ``dirty`` compares against, so this is where "changed"
        gets its meaning: changed since the board last agreed, not changed
        since a file was written.
        """
        return replace(self, baseline=self.project)
```

Then in `save`, remove `baseline=self.project` from the `replace(...)` call, leaving `path` and `file_hash`. Update its docstring: it writes a copy and returns a session that knows where the copy went; it does not change whether the device is up to date.

- [ ] **Step 4: Set it where the device and the project agree**

In `configurator/src/duo_input/ui/main_window.py`:

- In `_adopt_device_project`, where the read configuration becomes the session, use `ProjectSession(project=project).agreeing_with_device()` — or apply `agreeing_with_device()` to whatever session it constructs. The project just came off the board; nothing is changed.
- On a successful write, apply `agreeing_with_device()` to the session. Find the write-success path with `grep -n "write_config" configurator/src/duo_input/ui/main_window.py` and follow it to where the session is updated after the device confirms.

- [ ] **Step 5: Write the main-window tests**

Add to `configurator/tests/ui/test_main_window.py`:

```python
def test_a_project_read_from_the_device_reads_as_unchanged(qtbot, service, emulator, settings):
    window = MainWindow(service, transport_factory=lambda: emulator, settings=settings)
    qtbot.addWidget(window, before_close_func=_discard_on_teardown)
    qtbot.waitUntil(lambda: service.state is DeviceState.READY, timeout=5000)
    window.read_device_project()
    qtbot.waitUntil(lambda: window.session.dirty is False, timeout=5000)

    assert window.session.dirty is False


def test_a_successful_write_reads_as_unchanged(qtbot, window, emulator):
    _connect(qtbot, window, emulator)
    window.set_session(window.session.apply(RenameProfile(1, "To write")))
    assert window.session.dirty is True

    with qtbot.waitSignal(window.service.operation_succeeded, timeout=5000):
        window.write_to_device()

    qtbot.waitUntil(lambda: window.session.dirty is False, timeout=5000)
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q`
Expected: the new tests pass. **Several existing tests will fail** — ones asserting that saving clears the marker, or that a fresh session is clean. Do not fix them here; note which ones in your report. Task 2 removes most of what they test.

- [ ] **Step 7: Commit**

```bash
git add configurator/src/duo_input/ui/models/project_session.py configurator/src/duo_input/ui/main_window.py configurator/tests/ui/test_project_session.py configurator/tests/ui/test_main_window.py
git commit -m "Compare the project against the device, not against a file"
```

---

### Task 2: Delete autosave

**Files:**
- Delete: `configurator/src/duo_input/persistence/autosave.py`
- Delete: `configurator/tests/ui/test_autosave.py` if it exists — find it with `grep -rln "autosave" configurator/tests`
- Modify: `configurator/src/duo_input/ui/main_window.py` — the timer in `__init__`, `autosave_now`, `offer_recovery`, `_ask_recovery`, the `AutosaveService`/`Recovery` imports
- Modify: `configurator/src/duo_input/app.py` — the `offer_recovery()` call in `start_window`
- Modify: whichever tests reference it

**Interfaces:**
- Consumes: nothing.
- Produces: nothing. This task only removes.

Autosave protects unsaved edits between runs — a concept that disappears with the document. It is also already broken: `open_project` calls `autosave.discard()` before `offer_recovery` can read it, so the prompt is unreachable whenever a last-project file exists. This deletes the defect rather than fixing it.

- [ ] **Step 1: Find everything that mentions it**

```bash
grep -rn "autosave\|Autosave\|offer_recovery\|Recovery" configurator/src configurator/tests --include=*.py
```

Write the list into your report before deleting anything — it is what you will check against at the end.

- [ ] **Step 2: Write the failing test**

Add to `configurator/tests/ui/test_main_window.py`:

```python
def test_the_shell_keeps_no_autosave(window):
    """Autosave guarded edits between runs of a document that no longer exists."""
    assert not hasattr(window, "autosave")
    assert not hasattr(window, "offer_recovery")
    assert not hasattr(window, "autosave_now")
```

- [ ] **Step 3: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_main_window.py -q -k keeps_no_autosave`
Expected: FAIL — the attributes exist.

- [ ] **Step 4: Remove it**

Delete the module and its test file. In `main_window.py` remove the import, the `self.autosave = AutosaveService(...)` line and its two timer lines, and the `autosave_now`, `offer_recovery` and `_ask_recovery` methods. Remove the `self.autosave.discard()` calls in `save_project` and `open_project`. In `app.py`, remove `window.offer_recovery()` from `start_window`.

- [ ] **Step 5: Run the full suite and fix the fallout**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q`

Tests that exercised recovery must be deleted, not adapted — the behaviour is gone. Tests that merely built a window and happened to touch autosave should keep working; if one fails for another reason, say so rather than deleting it.

- [ ] **Step 6: Confirm nothing is left**

Run the grep from Step 1 again. Expected: no hits in `configurator/src` or `configurator/tests`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "Remove the autosave that guarded a document we no longer have"
```

---

### Task 3: Save a copy, load a copy

**Files:**
- Modify: `configurator/src/duo_input/ui/main_window.py` — the two buttons, `save_project`, `open_project`, `reopen_last_project`, `last_project_path`, `_remember_project`
- Modify: `configurator/src/duo_input/app.py` — the `reopen_last_project()` call in `start_window`
- Test: `configurator/tests/ui/test_main_window.py`, `configurator/tests/ui/test_app.py`

**Interfaces:**
- Consumes: `agreeing_with_device()` from Task 1 — specifically, neither action calls it.
- Produces: `MainWindow.save_copy(path)` and `MainWindow.load_copy(path)`, replacing `save_project` and `open_project`. Nothing later depends on them.

The file stops being remembered. Both actions always ask, neither records anything, and startup no longer reaches for a file.

- [ ] **Step 1: Write the failing tests**

Add to `configurator/tests/ui/test_main_window.py`:

```python
def test_saving_a_copy_remembers_nothing(qtbot, window, tmp_path, settings):
    """A copy is a copy: it does not become "the open file"."""
    target = tmp_path / "copy.duoinput.json"
    window.set_session(window.session.apply(RenameProfile(1, "Copied")))

    assert window.save_copy(target) is True

    assert target.is_file()
    assert settings.value("projects/last") is None


def test_loading_a_copy_replaces_the_configuration(qtbot, window, tmp_path):
    target = tmp_path / "copy.duoinput.json"
    window.set_session(window.session.apply(RenameProfile(1, "From a copy")))
    assert window.save_copy(target) is True
    window.set_session(ProjectSession.new())

    assert window.load_copy(target) is True

    assert window.session.active_profile.name == "From a copy"


def test_a_copy_that_cannot_be_read_is_reported_not_fatal(qtbot, window, tmp_path):
    broken = tmp_path / "broken.duoinput.json"
    broken.write_text("{ not json", encoding="utf-8")

    assert window.load_copy(broken) is False


def test_the_shell_no_longer_reopens_anything(window):
    assert not hasattr(window, "reopen_last_project")
    assert not hasattr(window, "last_project_path")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_main_window.py -q -k "a_copy or reopens_anything"`
Expected: FAIL — `save_copy` and `load_copy` do not exist; the reopen methods do.

- [ ] **Step 3: Rename and strip**

In `main_window.py`:

- Rename `save_project` to `save_copy`. It keeps writing the file, but drops `self.autosave.discard()` (already gone after Task 2) and `self._remember_project(...)`. It must not fall back to a stored path: if `path` is None it asks, every time.
- Rename `open_project` to `load_copy`. It reads the file into the session and drops `self._remember_project(...)`. It does **not** call `agreeing_with_device()` — whether the loaded copy reads as changed follows from whether it actually differs from the board.
- Delete `reopen_last_project`, `last_project_path`, `_remember_project` and the `LAST_PROJECT_KEY` constant.
- Rename the buttons: `self.tr("Save")` becomes `self.tr("Save a copy...")`, `self.tr("Open")` becomes `self.tr("Load a copy...")`. Their accessible names follow: "Save a copy of the configuration to a file", "Load a configuration from a file".
- Update the file-dialog titles the same way.

In `app.py`, remove `window.reopen_last_project()` from `start_window`.

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_main_window.py configurator/tests/ui/test_app.py -q`

Existing tests calling `save_project`/`open_project` must be updated to the new names. A test asserting the last project reopens at startup must be deleted — that behaviour is gone on purpose.

- [ ] **Step 5: Regenerate the catalogues**

Run: `.venv/Scripts/python.exe tools/update_translations.py`
Fill the Russian side:
- "Save a copy..." → `Сохранить копию…`
- "Load a copy..." → `Загрузить копию…`
- "Save a copy of the configuration to a file" → `Сохранить копию конфигурации в файл`
- "Load a configuration from a file" → `Загрузить конфигурацию из файла`
- "Save a copy" (dialog title) → `Сохранение копии`
- "Load a copy" (dialog title) → `Загрузка копии`

Run again until `every catalogue is complete`.

- [ ] **Step 6: Run the full suite and commit**

```bash
git add -A
git commit -m "Make the file a copy the operator asks for, not a document"
```

---

### Task 4: One honest chip, and a title without a file

**Files:**
- Modify: `configurator/src/duo_input/ui/main_window.py` — `_build_state_strip` (~line 316), `_refresh_state_strip` (~line 559), `_refresh_title` (~line 542)
- Test: `configurator/tests/ui/test_main_window.py`

**Interfaces:**
- Consumes: `dirty` with its new meaning, from Task 1.
- Produces: nothing.

Three chips said one thing three ways and could contradict each other. After Task 1 they are the same fact.

- [ ] **Step 1: Write the failing test**

Add to `configurator/tests/ui/test_main_window.py`:

```python
def test_the_strip_states_the_device_once(window):
    """Two chips saying the same thing differently is how the confusion began."""
    assert set(window.state_chips) == {"device"}


def test_the_title_names_the_program_not_a_file(qtbot, window, tmp_path):
    window.save_copy(tmp_path / "copy.duoinput.json")

    assert "copy" not in window.windowTitle()
    assert "duoinput" not in window.windowTitle()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_main_window.py -q -k "states_the_device_once or names_the_program"`
Expected: FAIL — three chips exist, and the title carries the file name.

- [ ] **Step 3: Reduce the strip**

In `_build_state_strip`, keep only the `("device", self.tr("Configuration on the device"))` entry. In `_refresh_state_strip`, delete the `changes` and `stored` blocks, keeping the `device` block as it is.

- [ ] **Step 4: Simplify the title**

Replace `_refresh_title` with one that names the program and marks unwritten changes:

```python
    def _refresh_title(self) -> None:
        """The program's name, and whether the board is behind.

        No file name: there is no open document to name. The marker means
        "not yet written to the device", which is the only kind of pending
        change there is now.
        """
        marker = f" {DIRTY_MARKER}" if self._session.dirty else ""
        self.setWindowTitle(f"{APPLICATION_NAME}{marker}")
```

- [ ] **Step 5: Run the suite**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q`

Tests asserting on the removed chips or on a file name in the title must be updated or deleted — say which in your report.

- [ ] **Step 6: Regenerate the catalogues**

The removed chips took their strings with them.

Run: `.venv/Scripts/python.exe tools/update_translations.py`
Expected: `every catalogue is complete`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "Say once whether the device is up to date"
```

---

### Task 5: Close without asking, and say what to do with no device

**Files:**
- Modify: `configurator/src/duo_input/ui/main_window.py` — `_confirm_close` (~line 889), `closeEvent` (~line 900), and `try_autoconnect`
- Test: `configurator/tests/ui/test_main_window.py`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing.

- [ ] **Step 1: Write the failing tests**

Add to `configurator/tests/ui/test_main_window.py`:

```python
def test_closing_never_asks_about_saving(qtbot, window):
    """There is no document to lose: the configuration lives on the board."""
    from PySide6.QtGui import QCloseEvent

    window.set_session(window.session.apply(RenameProfile(1, "Unwritten")))
    assert window.session.dirty is True
    assert not hasattr(window, "_confirm_close")

    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted() is True


def test_an_empty_start_says_where_a_configuration_comes_from(qtbot, service, settings):
    """With no board there is nothing to show, so say what to do about it."""
    window = MainWindow(service, transport_factory=lambda: None, settings=settings)
    qtbot.addWidget(window)
    window.try_autoconnect()

    message = window.statusBar().currentMessage()
    assert message
    assert "копи" in message.lower() or "copy" in message.lower()
```

Note: the existing `_discard_on_teardown` helper installs a fake `_confirm_close` for pytest-qt's teardown. Once the method is gone that helper is pointless — remove it and the `before_close_func=` arguments that use it, or the fixtures will be installing an attribute nothing reads.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_main_window.py -q -k "closing_never_asks or empty_start"`
Expected: FAIL — `_confirm_close` exists; no message names a copy.

- [ ] **Step 3: Delete the prompt**

Remove `_confirm_close` entirely. `closeEvent` becomes:

```python
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Close without asking. The configuration lives on the device.

        There was a prompt here about unsaved changes. It belonged to a
        document model where the file was the truth; now an edit that was
        never written to the board is simply an edit that was never written,
        and the title bar says so while the window is open.
        """
        event.accept()
```

- [ ] **Step 4: Say what to do when no device answers**

`try_autoconnect` is silent when the factory returns nothing, which is right for a retry that runs every two seconds — but the operator who just started the program with no board needs to know why the window is empty. Say it once:

```python
    def try_autoconnect(self) -> None:
        if self._service.is_connected:
            return
        link = self.transport_factory()
        if link is None:
            # Said once, not every two seconds: an empty socket is the normal
            # state to retry through, but an operator looking at an empty
            # window needs to know why it is empty and what to do about it.
            if not self._said_no_device:
                self._said_no_device = True
                self.statusBar().showMessage(
                    self.tr(
                        "No device found. Load a copy from a file, or plug the device in."
                    )
                )
            return
        self._said_no_device = False
        self._service.connect_device(link)
```

Initialise `self._said_no_device = False` beside the other state in `__init__`. Keep whatever else the current `try_autoconnect` does — read it first and preserve it; the body above shows the shape, not necessarily every line it already has.

- [ ] **Step 5: Run the suite**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q`

The close-flow tests that exercised the prompt must be deleted — that behaviour is gone deliberately. Say which in your report.

- [ ] **Step 6: Regenerate the catalogues**

Run: `.venv/Scripts/python.exe tools/update_translations.py`
Russian for the new string: `Устройство не найдено. Загрузите копию из файла или подключите устройство.`
Run again until `every catalogue is complete`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "Close without asking about a document that is gone"
```

---

### Task 6: Build it and use it

**Files:**
- Modify: none expected.

- [ ] **Step 1: Free the serial port**

`Get-Process -Name DuoInput -ErrorAction SilentlyContinue | Stop-Process -Force`

- [ ] **Step 2: Build**

```powershell
powershell -ExecutionPolicy Bypass -File configurator/packaging/nuitka-build.ps1
```

- [ ] **Step 3: Walk it through, and report what you see**

With the board attached:

1. Open the program. The board's configuration appears; the title has no `*` and the chip says the device matches.
2. Add a binding. The title gains `*`; the chip says the device differs.
3. Press *Write to device*. The `*` goes; the chip says it matches again.
4. Save a copy to a file. The `*` does **not** come back, the title does not change, and nothing about the device's state changes.
5. Close the window. **No prompt appears.**
6. Open again. The binding is there, read from the board.

Then unplug the board and open the program: the window is empty and the status bar names loading a copy.

If you cannot drive the GUI, say so and use the scripted equivalent against the real board — but say which you did.

- [ ] **Step 4: Report**

Each step as an observation, not an expectation.

---

## Notes for the executor

**The one-sentence test.** If a change makes the program compare the project against a file, it is wrong. Everything here follows from "changed" meaning "differs from the device".

**`dirty` still guards the device read.** `_adopt_device_project` refuses to overwrite a session with unsaved edits. Task 1 changes what `dirty` measures, not whether that guard exists — check it still refuses after your change, because a read landing on an operator mid-edit is the one case where losing work is unrecoverable.

**Deleting tests is expected here, and dangerous.** Several test the behaviours being removed and must go. A test that fails because it exercised a deleted prompt should be deleted; a test that fails because something else broke must be understood, not deleted. When in doubt, say so in the report rather than deciding quietly.
