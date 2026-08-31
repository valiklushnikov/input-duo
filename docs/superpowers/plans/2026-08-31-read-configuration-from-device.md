# Reading the Device Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the configurator read the configuration running on the device and show it at startup, so a project that exists only on the hardware is never lost again.

**Architecture:** A new domain module parses the binary package that `config_binary.py` already writes — the format is self-describing, so the parse is a mirror of the writer rather than a guess. The device side needs nothing: `DeviceService.read_config()` already fetches and verifies the package. The shell wires the two together at startup.

**Tech Stack:** Python 3.12, PySide6 (Qt 6), pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-08-31-read-configuration-from-device-design.md`

## Global Constraints

- **Working directory:** `C:\Users\Valentyn\Documents\Codex\2026-08-01\new-chat\work\duo-input-mvp`. All paths are relative to it.
- **Run pytest from the repository root:** `.venv/Scripts/python.exe -m pytest configurator/tests -q`. From inside `configurator/` an unrelated test breaks — it resolves data files relative to the root.
- **The domain may not import from `duo_input.ui` or `duo_input.device`.** `config_reader.py` is a domain module: it takes bytes and returns a project, and knows nothing about Qt or serial ports.
- **Every rejection raises `ProjectError`** (`duo_input.domain.project_store`). A malformed package arrives off a wire; it is reported, never fatal.
- **Format constants, exact** (they already exist in `configurator/src/duo_input/domain/config_binary.py`, import them rather than retyping): `MAGIC = b"DUOC"`, `HEADER_SIZE = 64`, `PROFILE_SIZE = 36`, `BINDING_SIZE = 12`, `MACRO_SIZE = 24`, `STEP_SIZE = 12`. Profile count is `PROFILES` (8) from `duo_input.generated.protocol`.
- **Header layout, little-endian**, from `protocol/config_format.md`: magic at 0 (4 bytes); schema major at 4, minor at 5; flags at 6 and reserved at 7, both zero; total length at 8 (u32); CRC-32 at 12 (u32); profile count at 16; active profile at 17; profile descriptor size at 18; reserved at 19; profile table offset at 20 (u32, exactly 64); string blob offset at 24 (u32); string blob length at 28 (u32); data blob offset at 32 (u32); data blob length at 36 (u32); 24 reserved zero bytes at 40.
- **CRC-32** is `zlib.crc32` over the whole declared package with bytes 12..15 treated as zero.
- **No `tr()` literal may be reworded or invented** without regenerating both catalogues with `.venv/Scripts/python.exe tools/update_translations.py` and filling the Russian side. Moving an existing literal to a new class also creates a new context Qt looks up separately.
- **Commit after every task.** Never use bare `git stash` — this worktree shares its stash stack with other checkouts.

---

### Task 1: Parse the header

**Files:**
- Create: `configurator/src/duo_input/domain/config_reader.py`
- Test: `configurator/tests/test_config_reader.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `config_reader.PackageHeader` — a frozen dataclass with fields `total_length: int`, `active_profile_id: int`, `profile_count: int`, `string_blob_offset: int`, `string_blob_length: int`, `data_blob_offset: int`, `data_blob_length: int`; and `config_reader.parse_header(package: bytes) -> PackageHeader`. Tasks 2 and 3 call `parse_header`.

The format is self-describing but hostile input is not, so the header is parsed and checked before a single offset in it is trusted.

- [ ] **Step 1: Write the failing tests**

Create `configurator/tests/test_config_reader.py`:

```python
"""Reading back the package that config_binary writes.

The vectors are the specification: these are the exact bytes the host tooling
generates and the firmware accepts, so a parse that disagrees with them is
wrong no matter how reasonable it looks.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from duo_input.domain.config_binary import HEADER_SIZE, MAGIC
from duo_input.domain.config_reader import parse_header
from duo_input.domain.project_store import ProjectError

VECTORS = Path("tests") / "vectors" / "config_vectors"


def _vector(name: str) -> bytes:
    return (VECTORS / name).read_bytes()


def _recrc(package: bytearray) -> bytes:
    """Repair the CRC after editing a package, so a test hits its real target."""
    struct.pack_into("<I", package, 12, 0)
    struct.pack_into("<I", package, 12, zlib.crc32(bytes(package)))
    return bytes(package)


def test_the_header_of_a_real_package_reads_back():
    header = parse_header(_vector("valid_full.bin"))

    assert header.total_length == len(_vector("valid_full.bin"))
    assert 1 <= header.active_profile_id <= 8
    assert header.profile_count == 8
    assert header.string_blob_offset >= HEADER_SIZE
    assert header.data_blob_offset >= header.string_blob_offset


def test_a_package_that_is_not_ours_is_refused():
    package = bytearray(_vector("valid_minimal.bin"))
    package[0:4] = b"XXXX"

    with pytest.raises(ProjectError):
        parse_header(_recrc(package))


def test_a_truncated_package_is_refused_not_indexed_past():
    package = _vector("valid_minimal.bin")

    for length in (0, 1, HEADER_SIZE - 1):
        with pytest.raises(ProjectError):
            parse_header(package[:length])


def test_a_corrupted_package_is_refused():
    package = bytearray(_vector("valid_minimal.bin"))
    package[HEADER_SIZE] ^= 0xFF  # flip a byte and leave the CRC stale

    with pytest.raises(ProjectError):
        parse_header(bytes(package))


def test_a_length_that_disagrees_with_the_bytes_is_refused():
    package = bytearray(_vector("valid_minimal.bin"))
    struct.pack_into("<I", package, 8, len(package) + 16)

    with pytest.raises(ProjectError):
        parse_header(_recrc(package))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/test_config_reader.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'duo_input.domain.config_reader'`

- [ ] **Step 3: Write the header parse**

Create `configurator/src/duo_input/domain/config_reader.py`:

```python
"""Turning the binary configuration package back into a project.

This is the mirror of :mod:`duo_input.domain.config_binary`, which writes the
same format. The package is self-describing - the header carries every offset,
count and record size - so nothing here has to guess at a layout. What it does
have to do is distrust: these bytes arrive over a serial link from a device,
and `protocol/config_format.md` requires a reader to validate every
``offset + count * record_size`` with checked arithmetic before reading it.

Every refusal raises :class:`ProjectError`. A package that cannot be read is a
thing to tell the operator about, never a reason to fall over.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from duo_input.domain.config_binary import (
    HEADER_SIZE,
    MAGIC,
    PROFILE_SIZE,
)
from duo_input.domain.project_store import ProjectError
from duo_input.generated.protocol import (
    PROFILES,
    SCHEMA_VERSION_MAJOR,
)


@dataclass(frozen=True)
class PackageHeader:
    """The sixty-four bytes that say where everything else is."""

    total_length: int
    active_profile_id: int
    profile_count: int
    string_blob_offset: int
    string_blob_length: int
    data_blob_offset: int
    data_blob_length: int


def _region(package: bytes, offset: int, length: int, what: str) -> None:
    """Refuse a region that runs past the end, in checked arithmetic."""
    if offset < 0 or length < 0 or offset + length > len(package):
        raise ProjectError(
            f"{what} runs past the end of the package "
            f"(offset {offset}, length {length}, package {len(package)})"
        )


def parse_header(package: bytes) -> PackageHeader:
    """Read and check the package header. Raises ProjectError if it is not one."""
    if len(package) < HEADER_SIZE:
        raise ProjectError(
            f"package is {len(package)} bytes, shorter than the {HEADER_SIZE}-byte header"
        )
    if package[0:4] != MAGIC:
        raise ProjectError("not a Duo Input configuration package")

    major = package[4]
    if major != SCHEMA_VERSION_MAJOR:
        raise ProjectError(
            f"schema major {major} cannot be read; this build understands "
            f"{SCHEMA_VERSION_MAJOR}"
        )
    if package[6] != 0 or package[7] != 0:
        raise ProjectError("header flags and reserved byte must be zero")

    total_length, stored_crc = struct.unpack_from("<II", package, 8)
    if total_length != len(package):
        raise ProjectError(
            f"header declares {total_length} bytes, package is {len(package)}"
        )

    computed = bytearray(package)
    struct.pack_into("<I", computed, 12, 0)
    if zlib.crc32(bytes(computed)) != stored_crc:
        raise ProjectError("package CRC does not match its contents")

    profile_count = package[16]
    active_profile_id = package[17]
    descriptor_size = package[18]
    if package[19] != 0:
        raise ProjectError("header reserved byte must be zero")
    if profile_count != PROFILES:
        raise ProjectError(f"profile count is {profile_count}, expected {PROFILES}")
    if not 1 <= active_profile_id <= PROFILES:
        raise ProjectError(f"active profile {active_profile_id} is outside 1..{PROFILES}")
    if descriptor_size != PROFILE_SIZE:
        raise ProjectError(
            f"profile descriptor is {descriptor_size} bytes, expected {PROFILE_SIZE}"
        )

    (
        profile_table_offset,
        string_blob_offset,
        string_blob_length,
        data_blob_offset,
        data_blob_length,
    ) = struct.unpack_from("<IIIII", package, 20)
    if profile_table_offset != HEADER_SIZE:
        raise ProjectError(
            f"profile table starts at {profile_table_offset}, expected {HEADER_SIZE}"
        )
    if any(package[40:64]):
        raise ProjectError("header reserved area must be zero")

    _region(package, profile_table_offset, profile_count * PROFILE_SIZE, "profile table")
    _region(package, string_blob_offset, string_blob_length, "string blob")
    _region(package, data_blob_offset, data_blob_length, "data blob")
    if data_blob_offset + data_blob_length != total_length:
        raise ProjectError("the data blob does not end where the package does")

    return PackageHeader(
        total_length=total_length,
        active_profile_id=active_profile_id,
        profile_count=profile_count,
        string_blob_offset=string_blob_offset,
        string_blob_length=string_blob_length,
        data_blob_offset=data_blob_offset,
        data_blob_length=data_blob_length,
    )


__all__ = ["PackageHeader", "parse_header"]
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/test_config_reader.py -q`
Expected: PASS

If `SCHEMA_VERSION_MAJOR` or `PROFILES` is not exported from `duo_input.generated.protocol`, find its real name with `grep -n "SCHEMA_VERSION\|^PROFILES" configurator/src/duo_input/generated/protocol.py` and use that. Do not invent a constant.

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/domain/config_reader.py configurator/tests/test_config_reader.py
git commit -m "Read the header of a configuration package"
```

---

### Task 2: Parse profiles, bindings, macros and steps

**Files:**
- Modify: `configurator/src/duo_input/domain/config_reader.py`
- Test: `configurator/tests/test_config_reader.py`

**Interfaces:**
- Consumes: `parse_header(package) -> PackageHeader` from Task 1.
- Produces: `config_reader.parse_device_config(package: bytes) -> DeviceConfig` and `config_reader.binary_to_project(package: bytes) -> DeviceProject`. Task 3 calls `binary_to_project`.

**The round trip is the test that matters.** Parse a shipped vector, compile the result with the existing writer, and compare bytes. Identical bytes prove the parse is complete and lossless — no field quietly dropped, no name mis-sliced.

- [ ] **Step 1: Write the failing tests**

Append to `configurator/tests/test_config_reader.py`:

```python
# ------------------------------------------------------------- the round trip


@pytest.mark.parametrize(
    "name", ("valid_full.bin", "valid_minimal.bin", "valid_wide_usages.bin")
)
def test_a_package_survives_being_read_and_written_again(name):
    """Byte-identical output is the only proof that nothing was dropped.

    valid_full.bin is the hard case: Cyrillic and emoji in names, a macro with
    nine steps, and the maximum macro ID.
    """
    from duo_input.domain.config_binary import compile_device_config
    from duo_input.domain.config_reader import parse_device_config

    original = _vector(name)

    config = parse_device_config(original)

    assert compile_device_config(config) == original


def test_the_names_come_back_as_the_operator_typed_them():
    from duo_input.domain.config_reader import parse_device_config

    config = parse_device_config(_vector("valid_full.bin"))

    names = [profile.name for profile in config.profiles]
    assert any(name.strip() for name in names), "every profile name came back empty"
    for profile in config.profiles:
        assert isinstance(profile.name, str)


def test_a_project_can_be_built_from_a_package():
    from duo_input.domain.config_reader import binary_to_project

    project = binary_to_project(_vector("valid_full.bin"))

    assert len(project.profiles) == 8
    assert 1 <= project.active_profile_id <= 8


def test_a_binding_table_that_overflows_its_package_is_refused():
    from duo_input.domain.config_reader import parse_device_config

    package = bytearray(_vector("valid_full.bin"))
    # The first profile descriptor sits at HEADER_SIZE; its binding count is at
    # descriptor offset 14. A count this large cannot fit whatever follows.
    struct.pack_into("<H", package, HEADER_SIZE + 14, 0xFFFF)

    with pytest.raises(ProjectError):
        parse_device_config(_recrc(package))


def test_a_name_that_is_not_utf8_is_refused():
    from duo_input.domain.config_reader import parse_device_config

    package = bytearray(_vector("valid_full.bin"))
    header = parse_header(bytes(package))
    package[header.string_blob_offset] = 0xFF  # a lone continuation byte

    with pytest.raises(ProjectError):
        parse_device_config(_recrc(package))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/test_config_reader.py -q -k "round_trip or survives or names_come_back or project_can_be_built or overflows or not_utf8"`
Expected: FAIL — `parse_device_config` does not exist.

- [ ] **Step 3: Read the writer before writing the reader**

Read `configurator/src/duo_input/domain/config_binary.py` from `compile_device_config` down. The reader must mirror it exactly: the order it lays out tables, how it aligns them, and how it slices the string blob. Anywhere the two disagree, the round-trip test fails and the writer is right.

Note in particular how the writer orders the string blob — profile names first, then macro names in profile and macro order — and that tables start on four-byte boundaries.

- [ ] **Step 4: Write the parse**

Add to `configurator/src/duo_input/domain/config_reader.py`. Take the enum types and the model classes from the same places the writer does:

```python
from duo_input.domain.models import (
    Binding,
    DeviceConfig,
    DeviceProject,
    Macro,
    MacroStep,
    Profile,
    Trigger,
    Action,
)
from duo_input.generated.protocol import (
    ActionKind,
    BindingMode,
    KeyboardRoute,
    MacroStepType,
    MouseRoute,
    TargetMode,
    TextLayout,
    TriggerKind,
)
```

Write these helpers, then the three parse functions:

```python
def _text(package: bytes, offset: int, length: int) -> str:
    """One UTF-8 name out of the string blob."""
    _region(package, offset, length, "name")
    try:
        return package[offset : offset + length].decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProjectError(f"a name is not valid UTF-8: {error}") from error


def _enum(enum_type: type, value: int, what: str):
    try:
        return enum_type(value)
    except ValueError as error:
        raise ProjectError(f"{what} {value} is not one this build knows") from error
```

Then the parse itself. Fill each `_read_*` from the tables in `protocol/config_format.md`:

```python
def parse_device_config(package: bytes) -> DeviceConfig:
    """Read a whole package. Raises ProjectError on anything that does not add up."""
    header = parse_header(package)
    profiles = tuple(
        _read_profile(package, HEADER_SIZE + index * PROFILE_SIZE)
        for index in range(header.profile_count)
    )
    return DeviceConfig(active_profile_id=header.active_profile_id, profiles=profiles)


def binary_to_project(package: bytes) -> DeviceProject:
    """The same, as the editable project the interface works with."""
    config = parse_device_config(package)
    return DeviceProject(
        schema_version=PROJECT_SCHEMA_VERSION,
        active_profile_id=config.active_profile_id,
        profiles=config.profiles,
    )


def _read_profile(package: bytes, at: int) -> Profile:
    _region(package, at, PROFILE_SIZE, "profile descriptor")
    if package[at + 7] != 0:
        raise ProjectError("profile reserved byte must be zero")
    name_offset, name_length = struct.unpack_from("<IH", package, at + 8)
    binding_count = struct.unpack_from("<H", package, at + 14)[0]
    binding_offset = struct.unpack_from("<I", package, at + 16)[0]
    binding_size = struct.unpack_from("<H", package, at + 20)[0]
    macro_count = struct.unpack_from("<H", package, at + 22)[0]
    macro_offset = struct.unpack_from("<I", package, at + 24)[0]
    macro_size = struct.unpack_from("<H", package, at + 28)[0]
    if binding_size != BINDING_SIZE or macro_size != MACRO_SIZE:
        raise ProjectError("a record size in the profile descriptor is not the format's")
    _region(package, binding_offset, binding_count * BINDING_SIZE, "binding table")
    _region(package, macro_offset, macro_count * MACRO_SIZE, "macro table")

    return Profile(
        id=package[at],
        name=_text(package, name_offset, name_length),
        color_rgb=(package[at + 4], package[at + 5], package[at + 6]),
        keyboard_route=_enum(KeyboardRoute, package[at + 1], "keyboard route"),
        mouse_route=_enum(MouseRoute, package[at + 2], "mouse route"),
        text_layout=_enum(TextLayout, package[at + 3], "text layout"),
        bindings=tuple(
            _read_binding(package, binding_offset + index * BINDING_SIZE)
            for index in range(binding_count)
        ),
        macros=tuple(
            _read_macro(package, macro_offset + index * MACRO_SIZE)
            for index in range(macro_count)
        ),
    )
```

`PROJECT_SCHEMA_VERSION` comes from `duo_input.domain.project_store` — verified; a
project needs it and a `DeviceConfig` does not carry one.

`_read_binding` reads the twelve bytes at "Binding record": trigger kind, code, modifiers,
mode, action kind, action argument, then reserved bytes that must be zero. It returns
`Binding(trigger=Trigger(kind, code, modifiers), mode=..., action=Action(kind, argument))`.

`_read_macro` reads the twenty-four bytes at "Macro descriptor": ID, target mode, name
offset and length, step count, step table offset, step record size. Check the step region,
then return `Macro(id=..., name=_text(...), target=..., steps=tuple(...))`.

`_read_step` reads the twelve bytes at "Step descriptor": type, payload length, payload
offset. Check the payload region and return
`MacroStep(type=..., payload=..., source_text=None)`.

The `Profile` fields above are verified against `configurator/src/duo_input/domain/models.py`.
Check `Binding`, `Macro`, `MacroStep`, `Trigger` and `Action` there the same way before
writing their readers.

Check the exact field offsets against `protocol/config_format.md` — profile descriptor at "Profile descriptor (36 bytes)", binding at "Binding record (12 bytes)", macro at "Macro descriptor (24 bytes)", step at "Step descriptor (12 bytes)". They are tabulated there; do not work them out from the writer alone.

A TEXT step keeps its compiled `payload` and gets `source_text=None`: the source string is not in the package, and inventing one would rewrite the macro on the next save.

- [ ] **Step 5: Run the round trip until it is byte-identical**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/test_config_reader.py -q`
Expected: PASS, all three vectors.

If a vector differs, do not adjust the test. Find the field the parse dropped or misread — comparing `compile_device_config(config)` against the original byte by byte will point at the offset.

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q`
Expected: everything passes.

- [ ] **Step 7: Commit**

```bash
git add configurator/src/duo_input/domain/config_reader.py configurator/tests/test_config_reader.py
git commit -m "Turn a configuration package back into a project"
```

---

### Task 3: Read the device at startup

**Files:**
- Modify: `configurator/src/duo_input/ui/main_window.py`
- Test: `configurator/tests/ui/test_main_window.py`

**Interfaces:**
- Consumes: `binary_to_project(package: bytes) -> DeviceProject` from Task 2.
- Produces: `MainWindow.read_device_project() -> None` and the wiring that calls it when a device becomes ready.

`DeviceService.read_config()` already fetches the package in chunks and verifies its SHA-256 before reporting success; the package arrives on `operation_succeeded` with `result.operation == "read_config"`. Nothing on the device side needs writing.

**The protection that matters:** a read that finishes after the operator has started editing must not discard their work.

- [ ] **Step 1: Write the failing tests**

Add to `configurator/tests/ui/test_main_window.py`:

```python
def test_a_device_that_answers_supplies_the_project(qtbot, service, emulator, settings):
    """Opening the program answers "what is my device doing?" without being asked."""
    from duo_input.domain.text_compiler import compile_project_to_binary
    from duo_input.ui.models.project_session import RenameProfile, default_project

    wanted = ProjectSession.new().apply(RenameProfile(1, "On the board")).project
    emulator.install_active(compile_project_to_binary(wanted))

    window = MainWindow(service, transport_factory=lambda: emulator, settings=settings)
    qtbot.addWidget(window, before_close_func=_discard_on_teardown)
    qtbot.waitUntil(lambda: service.state is DeviceState.READY, timeout=5000)
    window.read_device_project()

    qtbot.waitUntil(
        lambda: window.session.active_profile.name == "On the board", timeout=5000
    )
    assert window.session.path is None


def test_a_read_that_lands_late_does_not_discard_unsaved_edits(
    qtbot, service, emulator, settings
):
    """The read is several chunks over a serial link. Work started meanwhile stays."""
    from duo_input.domain.text_compiler import compile_project_to_binary
    from duo_input.ui.models.project_session import RenameProfile

    on_board = ProjectSession.new().apply(RenameProfile(1, "From device")).project
    emulator.install_active(compile_project_to_binary(on_board))

    window = MainWindow(service, transport_factory=lambda: emulator, settings=settings)
    qtbot.addWidget(window, before_close_func=_discard_on_teardown)
    qtbot.waitUntil(lambda: service.state is DeviceState.READY, timeout=5000)

    window.set_session(window.session.apply(RenameProfile(1, "Being typed")))
    assert window.session.dirty

    window.read_device_project()
    qtbot.wait(300)

    assert window.session.active_profile.name == "Being typed"
```

Check what the session calls its unsaved-changes flag before writing the second test:
The session's unsaved-changes flag is the property `dirty`
(project_session.py:483) — verified, not guessed.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_main_window.py -q -k "device_that_answers or lands_late"`
Expected: FAIL — `read_device_project` does not exist.

- [ ] **Step 3: Write the read**

In `configurator/src/duo_input/ui/main_window.py`, add beside `try_autoconnect`:

```python
    def read_device_project(self) -> None:
        """Ask the device for the configuration it is running.

        The answer arrives later, on ``operation_succeeded``; see
        ``_on_operation_succeeded``.
        """
        if not self._service.is_connected:
            return
        self._reading_device = True
        self._service.read_config()
```

Initialise `self._reading_device = False` beside the other state in `__init__`.

In `_on_operation_succeeded`, before the existing body, handle the package:

```python
        if getattr(result, "operation", "") == "read_config" and self._reading_device:
            self._reading_device = False
            self._adopt_device_project(getattr(result, "package", None))
```

and add:

```python
    def _adopt_device_project(self, package: bytes | None) -> None:
        """Show what the device is running, unless the operator is mid-edit.

        The read takes several chunks over a serial link. Somebody who started
        typing while it was in flight must not have that thrown away by an
        answer that arrives afterwards - the device is authoritative at
        startup, not at every moment.
        """
        if package is None:
            return
        if self._session.dirty:
            self.statusBar().showMessage(
                self.tr("The device has a different configuration; your edits were kept.")
            )
            return
        try:
            project = binary_to_project(package)
        except ProjectError as error:
            self.overview.append_event("read_config", type(error).__name__)
            self.statusBar().showMessage(
                self.tr("The device's configuration could not be read: {0}").format(error)
            )
            return
        self.set_session(ProjectSession.from_project(project))
        self.statusBar().showMessage(self.tr("Configuration read from the device"))
```

Import `binary_to_project` from `duo_input.domain.config_reader`.

Two things to check rather than assume, because the plan cannot see them:
- What `operation_succeeded` actually carries. Run
  `grep -n "_finish_success" -A 8 configurator/src/duo_input/device/service.py`
  and use the real attribute names for the operation and the payload.
- Whether `ProjectSession` has a `from_project` constructor. Run
  `grep -n "def new\|def load\|def from_" configurator/src/duo_input/ui/models/project_session.py`.
  If it does not, build the session the way `load` does, minus the path.

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_main_window.py -q`
Expected: PASS

- [ ] **Step 5: Regenerate the catalogues**

Three new `tr()` strings were added.

Run: `.venv/Scripts/python.exe tools/update_translations.py`
Fill the Russian side in `configurator/src/duo_input/resources/translations/duo_input_ru.ts`:
- "The device has a different configuration; your edits were kept." → `На устройстве другая конфигурация; ваши правки сохранены.`
- "The device's configuration could not be read: {0}" → `Не удалось прочитать конфигурацию устройства: {0}`
- "Configuration read from the device" → `Конфигурация прочитана с устройства`

Run it again until it reports `every catalogue is complete`.

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q`
Expected: everything passes.

- [ ] **Step 7: Commit**

```bash
git add configurator/src/duo_input/ui/main_window.py configurator/tests/ui/test_main_window.py configurator/src/duo_input/resources/translations
git commit -m "Show what the device is running when the program opens"
```

---

### Task 4: Put the read into the startup order

**Files:**
- Modify: `configurator/src/duo_input/app.py`
- Test: `configurator/tests/ui/test_app.py`

**Interfaces:**
- Consumes: `MainWindow.read_device_project()` from Task 3, `MainWindow.reopen_last_project()` which already exists.
- Produces: nothing new; `start_window` gains a step.

The spec's order: device first, file only when no device answered, autosave recovery last.

- [ ] **Step 1: Write the failing test**

Add to `configurator/tests/ui/test_app.py`:

```python
def test_startup_prefers_the_device_over_the_last_file(qtbot, tmp_path):
    """The device wins: the question on opening is what the hardware is doing.

    A file is only reached for when nothing answered.
    """
    from PySide6.QtCore import QSettings

    from duo_input.app import build_main_window, start_window
    from duo_input.device.emulator import U1Emulator
    from duo_input.device.service import DeviceService, DeviceState
    from duo_input.domain.text_compiler import compile_project_to_binary
    from duo_input.ui.models.project_session import ProjectSession, RenameProfile

    store = QSettings(str(tmp_path / "duo-input.ini"), QSettings.Format.IniFormat)
    store.clear()
    saved = tmp_path / "on-disk.duoinput.json"

    on_disk = build_main_window(DeviceService(), transport_factory=lambda: None, settings=store)
    qtbot.addWidget(on_disk)
    on_disk.set_session(on_disk.session.apply(RenameProfile(1, "On disk")))
    assert on_disk.save_project(saved) is True

    emulator = U1Emulator()
    on_board = ProjectSession.new().apply(RenameProfile(1, "On the board")).project
    emulator.install_active(compile_project_to_binary(on_board))

    window = build_main_window(
        DeviceService(timeout_ms=5000),
        transport_factory=lambda: emulator,
        settings=store,
    )
    qtbot.addWidget(window)
    window._confirm_close = lambda: None
    start_window(window)

    qtbot.waitUntil(
        lambda: window.session.active_profile.name == "On the board", timeout=5000
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_app.py -q -k prefers_the_device`
Expected: FAIL — the last file opens instead, so the name is "On disk".

- [ ] **Step 3: Change the order**

In `configurator/src/duo_input/app.py`, `start_window` becomes:

```python
def start_window(window: MainWindow) -> None:
    """Show the shell and restore what the operator was working on.

    The device comes first: the question someone opens this program with is
    what their hardware is currently doing. A file is reached for only when
    nothing answered - and the autosave, which holds edits that were never
    written anywhere, is offered last so it can override either.
    """
    window.show()
    window.try_autoconnect()
    if window.service.is_connected:
        window.read_device_project()
    else:
        window.reopen_last_project()
    window.offer_recovery()
```

Confirm `window.service` is the public name with
`grep -n "def service" configurator/src/duo_input/ui/main_window.py`; use `_service` only if there is no property.

- [ ] **Step 4: Run the test**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_app.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q`
Expected: everything passes. If `test_starting_a_window_reopens_what_was_open_last` now fails, it is because its window has no device — check it passes `transport_factory=lambda: None`, which it does; if the failure is real, the branch is wrong.

- [ ] **Step 6: Commit**

```bash
git add configurator/src/duo_input/app.py configurator/tests/ui/test_app.py
git commit -m "Ask the device first, the file second"
```

---

### Task 5: Say when a macro step came from the device

**Files:**
- Modify: `configurator/src/duo_input/ui/macros.py`
- Test: `configurator/tests/ui/test_macro_editor.py`

**Interfaces:**
- Consumes: nothing from earlier tasks; it reacts to `MacroStep(type=TEXT, payload=..., source_text=None)`, which Task 2 produces.
- Produces: nothing later tasks use.

A text step read from the device has its keystrokes but not the sentence that produced them, because the format never stored it. The editor currently renders such a step as `step.source_text or ""` — an empty box that looks like a bug.

- [ ] **Step 1: Write the failing test**

Add to `configurator/tests/ui/test_macro_editor.py`:

```python
def test_a_text_step_read_from_the_device_says_where_it_came_from(qtbot):
    """The source text was never stored on the device, only the keystrokes.

    An empty text box would read as data loss; the truth is that this step can
    still be run and can no longer be edited as a sentence.
    """
    from duo_input.domain.models import MacroStep
    from duo_input.generated.protocol import MacroStepType
    from duo_input.ui.models.macro_steps import step_label

    recovered = MacroStep(MacroStepType.TEXT, bytes((0x00, 0x04, 0x00, 0x05)), None)

    summary = step_label(recovered)

    assert summary
    assert summary != ""
    assert "устройств" in summary.lower() or "device" in summary.lower()
```

Find the function that renders a step for the list first:
`grep -n "def .*summary\|def .*label\|source_text" configurator/src/duo_input/ui/models/macro_steps.py`.
It is `step_label(step: MacroStep) -> str` (macro_steps.py:132) - verified.

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_macro_editor.py -q -k read_from_the_device`
Expected: FAIL — an empty string, or the function does not exist under that name.

- [ ] **Step 3: Say it in the renderer**

In the function that renders a TEXT step, when `source_text` is None, say the step came from the device and how many keystrokes it holds — the payload is `(modifier, usage)` pairs, so the count is `len(payload) // 2`. Use `self.tr()` or `QT_TRANSLATE_NOOP` in the same style as the surrounding code.

Suggested English string: `"From the device: {0} keystrokes"`.

- [ ] **Step 4: Run the test**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_macro_editor.py -q`
Expected: PASS

- [ ] **Step 5: Regenerate the catalogues**

Run: `.venv/Scripts/python.exe tools/update_translations.py`
Russian for the suggested string: `С устройства: нажатий — {0}`
Run again until `every catalogue is complete`.

- [ ] **Step 6: Run the full suite and commit**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q`

```bash
git add configurator/src/duo_input/ui/macros.py configurator/src/duo_input/ui/models/macro_steps.py configurator/tests/ui/test_macro_editor.py configurator/src/duo_input/resources/translations
git commit -m "Say when a macro step came back from the device"
```

---

### Task 6: Build it and try it on the hardware

**Files:**
- Modify: none expected.

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Free the serial port**

The configurator holds the port while it is open, and the build runs the hardware tests. Close any running DuoInput first.

- [ ] **Step 2: Build**

```powershell
powershell -ExecutionPolicy Bypass -File configurator/packaging/nuitka-build.ps1
```

Expected: suite green, Nuitka compiles, packaging contract passes.

- [ ] **Step 3: Prove the round trip on real hardware**

1. Open `configurator/dist/DuoInput/DuoInput.exe`.
2. Add a binding you will recognise — a key bound to switching the keyboard.
3. Press **Записать в устройство**.
4. Close the program entirely.
5. Open it again.

The binding must be there, having come from the board rather than from any
file. Confirm the state strip says the device matches the project.

- [ ] **Step 4: Prove it without a device**

Unplug the board, open the program, and confirm the last project file opens
instead — the fallback still works.

- [ ] **Step 5: Report what you saw**

Both outcomes, in the report, as observations rather than expectations.

---

## Notes for the executor

**The round trip in Task 2 is the whole plan's load-bearing test.** If it passes on all three vectors, the parse is right. If it fails, the writer is correct and the reader is wrong — never the other way round, and never fix it by changing the test.

**Do not add a "Read from device" button.** The spec rules it out: the point is that this happens on its own.

**Do not try to recover the source text of a macro.** The mapping from keystrokes back to characters is not one-to-one, and a wrong guess would rewrite the operator's macro on the next save without anyone noticing.
