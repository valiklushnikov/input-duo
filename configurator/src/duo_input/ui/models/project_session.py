"""The editing session: one immutable project plus the three hashes around it.

Three hashes describe three different things and are never conflated:

``file_hash``
    SHA-256 of the ``.duoinput.json`` bytes that are currently on disk. Empty
    while the session has never been saved or loaded.
``compiled_hash``
    SHA-256 of ``compile_project_to_binary`` applied to the project **as it is
    in memory right now**. This is the package a write would send.
``device_hash``
    What :class:`~duo_input.device.service.DeviceService` reports the device is
    holding. The session never derives it, it only carries it.

``dirty`` compares the in-memory project against the baseline - the project
last known to agree with the device (see ``agreeing_with_device``), or the
pristine project of a brand-new session. Saving to a file sets ``file_hash``
but never touches the baseline: a file is a copy, not the truth, so writing
one leaves ``dirty`` exactly as it was.

Every mutation returns a *new* session: :meth:`ProjectSession.apply` takes a
command object and hands back a fresh session, so editors never mutate shared
state and undo is a matter of keeping old sessions.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from duo_input.domain.models import (
    Binding,
    DeviceProject,
    Macro,
    MacroStep,
    Profile,
    Trigger,
)
from duo_input.domain.project_store import (
    PROJECT_SCHEMA_VERSION,
    load_project,
    save_project_atomic,
)
from duo_input.domain.text_compiler import compile_project_to_binary
from duo_input.domain.validation import ValidationIssue, validate_project
from duo_input.generated.protocol import (
    BINDINGS_PER_PROFILE,
    MACRO_STEPS_PER_MACRO,
    MACROS_PER_PROFILE,
    PROFILES,
    ActionKind,
    KeyboardRoute,
    MouseRoute,
    TargetMode,
    TextLayout,
)

# Default profile accent colours. These are project *data*, not UI strings.
_DEFAULT_COLORS: tuple[tuple[int, int, int], ...] = (
    (0xE6, 0x39, 0x46),
    (0xF7, 0x7F, 0x00),
    (0xFC, 0xBF, 0x49),
    (0x43, 0xAA, 0x8B),
    (0x27, 0x7D, 0xA1),
    (0x57, 0x75, 0x90),
    (0x9D, 0x4E, 0xDD),
    (0x8D, 0x99, 0xAE),
)


def default_project() -> DeviceProject:
    """A pristine, valid eight-profile project for a brand-new session."""

    profiles = tuple(
        Profile(
            id=index + 1,
            name=f"Profile {index + 1}",
            color_rgb=_DEFAULT_COLORS[index % len(_DEFAULT_COLORS)],
            keyboard_route=KeyboardRoute.PC1,
            mouse_route=MouseRoute.PC1,
            text_layout=TextLayout.US,
            bindings=(),
            macros=(),
        )
        for index in range(PROFILES)
    )
    return DeviceProject(
        schema_version=PROJECT_SCHEMA_VERSION,
        active_profile_id=1,
        profiles=profiles,
    )


# --------------------------------------------------------------------- commands


@runtime_checkable
class ProjectCommand(Protocol):
    """One editing step. Pure: it maps a project onto a new project."""

    def apply_to(self, project: DeviceProject) -> DeviceProject: ...


@dataclass(frozen=True)
class SetActiveProfile:
    """Select which profile the device starts in."""

    profile_id: int

    def apply_to(self, project: DeviceProject) -> DeviceProject:
        return replace(project, active_profile_id=self.profile_id)


def _replace_profile(
    project: DeviceProject, profile_id: int, change: Callable[[Profile], Profile]
) -> DeviceProject:
    """Rebuild the project with ``change`` applied to exactly one profile."""
    for profile in project.profiles:
        if profile.id == profile_id:
            break
    else:
        raise ValueError(f"no profile with ID {profile_id}")
    profiles = tuple(
        change(profile) if profile.id == profile_id else profile for profile in project.profiles
    )
    return replace(project, profiles=profiles)


def _find_profile(project: DeviceProject, profile_id: int) -> Profile:
    for profile in project.profiles:
        if profile.id == profile_id:
            return profile
    raise ValueError(f"no profile with ID {profile_id}")


def _trigger_key(trigger: Trigger) -> tuple[int, int, int]:
    """The identity the device matches on; two bindings may not share it."""
    return (int(trigger.kind), int(trigger.code), int(trigger.modifiers))


@dataclass(frozen=True)
class RenameProfile:
    """Rename exactly one profile, leaving every other profile identical."""

    profile_id: int
    name: str

    def apply_to(self, project: DeviceProject) -> DeviceProject:
        return _replace_profile(
            project, self.profile_id, lambda profile: replace(profile, name=self.name)
        )


@dataclass(frozen=True)
class SetProfileColor:
    """Recolour one profile slot."""

    profile_id: int
    color_rgb: tuple[int, int, int]

    def apply_to(self, project: DeviceProject) -> DeviceProject:
        color = tuple(int(component) for component in self.color_rgb)
        if len(color) != 3:
            raise ValueError("a profile colour needs exactly three components")
        return _replace_profile(
            project, self.profile_id, lambda profile: replace(profile, color_rgb=color)
        )


@dataclass(frozen=True)
class SetProfileRoutes:
    """Choose the routes one profile starts in.

    Spec section 11: a profile stores where its keyboard and its mouse point
    when it becomes the active one, and the firmware applies them at boot and
    at every profile change. A pristine profile starts on PC1, so this exists
    for the projects that mean something else - and for the ones that mean PC1
    and would rather say so than inherit it.
    """

    profile_id: int
    keyboard_route: KeyboardRoute
    mouse_route: MouseRoute

    def apply_to(self, project: DeviceProject) -> DeviceProject:
        return _replace_profile(
            project,
            self.profile_id,
            lambda profile: replace(
                profile,
                keyboard_route=KeyboardRoute(self.keyboard_route),
                mouse_route=MouseRoute(self.mouse_route),
            ),
        )


@dataclass(frozen=True)
class CopyProfile:
    """Duplicate one slot into another, keeping the target's own ID.

    Bindings and macros carry project-wide unique UUIDs, so the copies are
    given fresh ones; macro *IDs* are per-profile and are copied unchanged, so
    a run-macro binding keeps pointing at the same macro inside the new slot.
    """

    source_id: int
    target_id: int

    def apply_to(self, project: DeviceProject) -> DeviceProject:
        if self.source_id == self.target_id:
            raise ValueError("a profile cannot be copied onto itself")
        source = _find_profile(project, self.source_id)
        return _replace_profile(
            project,
            self.target_id,
            lambda target: replace(
                source,
                id=target.id,
                bindings=tuple(replace(binding, uuid=uuid4()) for binding in source.bindings),
                macros=tuple(replace(macro, uuid=uuid4()) for macro in source.macros),
            ),
        )


@dataclass(frozen=True)
class ClearProfile:
    """Reset one slot to the pristine profile a new project starts with."""

    profile_id: int

    def apply_to(self, project: DeviceProject) -> DeviceProject:
        pristine = _find_profile(default_project(), self.profile_id)
        return _replace_profile(project, self.profile_id, lambda _: pristine)


@dataclass(frozen=True)
class AddBinding:
    """Append one binding to a profile, refusing a trigger it already uses."""

    profile_id: int
    binding: Binding

    def apply_to(self, project: DeviceProject) -> DeviceProject:
        def add(profile: Profile) -> Profile:
            if len(profile.bindings) >= BINDINGS_PER_PROFILE:
                raise ValueError(
                    f"a profile may hold at most {BINDINGS_PER_PROFILE} bindings"
                )
            taken = {_trigger_key(existing.trigger) for existing in profile.bindings}
            if _trigger_key(self.binding.trigger) in taken:
                raise ValueError("that trigger is already bound in this profile")
            return replace(profile, bindings=profile.bindings + (self.binding,))

        return _replace_profile(project, self.profile_id, add)


@dataclass(frozen=True)
class UpdateBinding:
    """Replace the binding carrying ``uuid``, keeping its position and UUID."""

    profile_id: int
    uuid: UUID
    binding: Binding

    def apply_to(self, project: DeviceProject) -> DeviceProject:
        def update(profile: Profile) -> Profile:
            index = _binding_index(profile, self.uuid)
            taken = {
                _trigger_key(existing.trigger)
                for position, existing in enumerate(profile.bindings)
                if position != index
            }
            if _trigger_key(self.binding.trigger) in taken:
                raise ValueError("that trigger is already bound in this profile")
            edited = replace(self.binding, uuid=self.uuid)
            bindings = list(profile.bindings)
            bindings[index] = edited
            return replace(profile, bindings=tuple(bindings))

        return _replace_profile(project, self.profile_id, update)


@dataclass(frozen=True)
class RemoveBinding:
    """Drop the binding carrying ``uuid``."""

    profile_id: int
    uuid: UUID

    def apply_to(self, project: DeviceProject) -> DeviceProject:
        def remove(profile: Profile) -> Profile:
            index = _binding_index(profile, self.uuid)
            bindings = list(profile.bindings)
            del bindings[index]
            return replace(profile, bindings=tuple(bindings))

        return _replace_profile(project, self.profile_id, remove)


@dataclass(frozen=True)
class AddMacro:
    """Append an empty macro; the command chooses its per-profile ID."""

    profile_id: int
    name: str
    target: TargetMode = TargetMode.INHERIT

    def apply_to(self, project: DeviceProject) -> DeviceProject:
        def add(profile: Profile) -> Profile:
            if len(profile.macros) >= MACROS_PER_PROFILE:
                raise ValueError(f"a profile may hold at most {MACROS_PER_PROFILE} macros")
            macro = Macro(
                id=_free_macro_id(profile),
                name=self.name,
                target=TargetMode(self.target),
                steps=(),
            )
            return replace(profile, macros=profile.macros + (macro,))

        return _replace_profile(project, self.profile_id, add)


@dataclass(frozen=True)
class RemoveMacro:
    """Drop one macro, unless a binding in the same profile still runs it."""

    profile_id: int
    uuid: UUID

    def apply_to(self, project: DeviceProject) -> DeviceProject:
        def remove(profile: Profile) -> Profile:
            index = _macro_index(profile, self.uuid)
            macro = profile.macros[index]
            for binding in profile.bindings:
                if (
                    ActionKind(binding.action.kind) is ActionKind.RUN_MACRO
                    and binding.action.argument == macro.id
                ):
                    raise ValueError(
                        f"macro {macro.id} is still run by a binding in this profile"
                    )
            macros = list(profile.macros)
            del macros[index]
            return replace(profile, macros=tuple(macros))

        return _replace_profile(project, self.profile_id, remove)


@dataclass(frozen=True)
class RenameMacro:
    """Rename one macro; its ID and UUID stay as they are."""

    profile_id: int
    uuid: UUID
    name: str

    def apply_to(self, project: DeviceProject) -> DeviceProject:
        return _replace_macro(project, self.profile_id, self.uuid, name=self.name)


@dataclass(frozen=True)
class SetMacroTarget:
    """Choose which computer the macro types on: inherit, PC1, PC2 or both."""

    profile_id: int
    uuid: UUID
    target: TargetMode

    def apply_to(self, project: DeviceProject) -> DeviceProject:
        return _replace_macro(
            project, self.profile_id, self.uuid, target=TargetMode(self.target)
        )


@dataclass(frozen=True)
class SetMacroSteps:
    """Replace the whole step list of one macro."""

    profile_id: int
    uuid: UUID
    steps: tuple[MacroStep, ...]

    def apply_to(self, project: DeviceProject) -> DeviceProject:
        steps = tuple(self.steps)
        if len(steps) > MACRO_STEPS_PER_MACRO:
            raise ValueError(f"a macro may hold at most {MACRO_STEPS_PER_MACRO} steps")
        if any(not isinstance(step, MacroStep) for step in steps):
            raise ValueError("every macro step must be a MacroStep")
        return _replace_macro(project, self.profile_id, self.uuid, steps=steps)


def _free_macro_id(profile: Profile) -> int:
    """The lowest ID this profile is not already using."""
    taken = {macro.id for macro in profile.macros}
    for candidate in range(1, MACROS_PER_PROFILE + 1):
        if candidate not in taken:
            return candidate
    raise ValueError("this profile has no free macro ID left")


def _macro_index(profile: Profile, uuid: UUID) -> int:
    for index, macro in enumerate(profile.macros):
        if macro.uuid == uuid:
            return index
    raise ValueError(f"profile {profile.id} has no macro {uuid}")


def _replace_macro(
    project: DeviceProject, profile_id: int, uuid: UUID, **changes: object
) -> DeviceProject:
    def change(profile: Profile) -> Profile:
        index = _macro_index(profile, uuid)
        macros = list(profile.macros)
        macros[index] = replace(macros[index], **changes)
        return replace(profile, macros=tuple(macros))

    return _replace_profile(project, profile_id, change)


def _binding_index(profile: Profile, uuid: UUID) -> int:
    for index, binding in enumerate(profile.bindings):
        if binding.uuid == uuid:
            return index
    raise ValueError(f"profile {profile.id} has no binding {uuid}")


# ---------------------------------------------------------------------- session


def _as_hex(value: bytes | str) -> str:
    if isinstance(value, str):
        return value
    return bytes(value).hex()


@dataclass(frozen=True)
class ProjectSession:
    """An immutable snapshot of everything the shell needs to know."""

    project: DeviceProject = field(default_factory=default_project)
    path: Path | None = None
    file_hash: str = ""
    device_hash: str = ""
    connected: bool = False
    baseline: DeviceProject | None = None

    def __post_init__(self) -> None:
        if self.baseline is None:
            object.__setattr__(self, "baseline", self.project)
        object.__setattr__(self, "_compiled", None)

    # -------------------------------------------------------------- factories

    @classmethod
    def new(cls) -> ProjectSession:
        """A clean session on a pristine project that has never been saved."""
        return cls(project=default_project())

    @classmethod
    def load(cls, path: str | Path) -> ProjectSession:
        """Read ``path`` and return a clean session anchored to that file."""
        location = Path(path)
        project = load_project(location)
        return cls(
            project=project,
            path=location,
            file_hash=_file_hash(location),
        )

    # ---------------------------------------------------------- derived state

    @property
    def active_profile(self) -> Profile:
        for profile in self.project.profiles:
            if profile.id == self.project.active_profile_id:
                return profile
        return self.project.profiles[0]

    @property
    def dirty(self) -> bool:
        """Does the in-memory project differ from what was last persisted?"""
        return self.project != self.baseline

    @property
    def issues(self) -> tuple[ValidationIssue, ...]:
        return validate_project(self.project)

    @property
    def is_valid(self) -> bool:
        return not self.issues

    @property
    def compiled_hash(self) -> str:
        """SHA-256 of the package this project compiles to; empty if it cannot."""
        return self._compiled_package()[0]

    @property
    def compiled_size(self) -> int | None:
        """Length of that package in bytes, or ``None`` if it cannot compile."""
        return self._compiled_package()[1]

    @property
    def device_matches(self) -> bool:
        """Is the device holding exactly the package this project compiles to?"""
        return bool(self.device_hash) and self.device_hash == self.compiled_hash

    @property
    def can_write(self) -> bool:
        """A write is offered only for a valid project on a connected device."""
        return self.connected and self.is_valid and bool(self.compiled_hash)

    def _compiled_package(self) -> tuple[str, int | None]:
        cached = getattr(self, "_compiled", None)
        if cached is None:
            try:
                package = compile_project_to_binary(self.project)
            except ValueError:
                # An invalid project has no package; can_write already blocks it.
                cached = ("", None)
            else:
                cached = (hashlib.sha256(package).hexdigest(), len(package))
            object.__setattr__(self, "_compiled", cached)
        return cached

    # ------------------------------------------------------------ transitions

    def apply(self, command: ProjectCommand) -> ProjectSession:
        """Return a new session with ``command`` applied to the project."""
        project = command.apply_to(self.project)
        if not isinstance(project, DeviceProject):
            raise TypeError("a project command must return a DeviceProject")
        return replace(self, project=project)

    def with_device_hash(self, value: bytes | str) -> ProjectSession:
        """Record what the device reports it is holding; nothing else changes."""
        return replace(self, device_hash=_as_hex(value))

    def with_connection(self, connected: bool) -> ProjectSession:
        return replace(self, connected=bool(connected))

    def agreeing_with_device(self) -> ProjectSession:
        """Mark the project as being what the device holds.

        Called after a configuration is read from the device and after one is
        written to it - the two moments the two are known to be the same. The
        baseline is what ``dirty`` compares against, so this is where "changed"
        gets its meaning: changed since the board last agreed, not changed
        since a file was written.
        """
        return replace(self, baseline=self.project)

    def save(self, path: str | Path | None = None) -> ProjectSession:
        """Write a copy of the project to disk and return a session that knows
        where it went; it does not change whether the device is up to date.
        """
        location = Path(path) if path is not None else self.path
        if location is None:
            raise ValueError("the session has no file to save to")
        save_project_atomic(self.project, location)
        return replace(
            self,
            path=location,
            file_hash=_file_hash(location),
        )


def _file_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


__all__ = [
    "AddBinding",
    "AddMacro",
    "ClearProfile",
    "CopyProfile",
    "ProjectCommand",
    "ProjectSession",
    "RemoveBinding",
    "RemoveMacro",
    "RenameMacro",
    "RenameProfile",
    "SetActiveProfile",
    "SetMacroSteps",
    "SetMacroTarget",
    "SetProfileColor",
    "SetProfileRoutes",
    "UpdateBinding",
    "default_project",
]
