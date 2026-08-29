"""Build and deploy the configurations the hardware acceptances are run against.

Two configurations live here, chosen with ``--config``:

``step4``
    The Task 7 Step 4 acceptance configuration, described below. This is the
    default, and the one already written to a board.

``toggle``
    The roadmap's Task 3 Step 3 evidence: route toggles in quantity, performed
    by the device rather than by a person's forefinger, from ten arrow-key
    presses a tired operator will not miscount. See :data:`TOGGLE_CYCLES` for
    the arithmetic and the honest shortfall against the roadmap's thousand.

They share everything below the project itself - the round-trip check, the
backup, the write and the read-back - because a configuration that was built
by one path and deployed by another has not been tested by either.

Step 4 still owes three criteria on hardware:

  S4a  eight profiles surviving a power cycle
  S4b  the ``/target KYPKYMA`` text macro, typed without movement stalls
  S4c  disconnect release and reconnect recovery

None of them can be observed against the eight empty profiles a pristine
project starts with: an empty profile types nothing, so "profile 5 survived the
power cycle" would have to be inferred rather than seen. This module builds a
configuration in which every profile *says its own name*, and carries the text
macro the acceptance names.

The project is assembled through :mod:`duo_input.ui.models.project_session`
commands - the same objects the GUI applies when somebody edits a profile - and
compiled by :func:`compile_project_to_binary`. Nothing here assembles bytes by
hand, so what the acceptance runs against is what the configurator produces.

``deploy`` drives :class:`~duo_input.device.service.DeviceService` over a link.
It refuses to write until it has read and saved what was already on the device:
a backup taken afterwards is not a backup.

Usage::

    python tools/step4_acceptance_config.py describe [--config step4|toggle]
    python tools/step4_acceptance_config.py build --output PATH.bin \\
        [--config step4|toggle]
    python tools/step4_acceptance_config.py deploy --package PATH.bin \\
        --backup-dir hardware-backups [--config step4|toggle] [--port COMn]
    python tools/step4_acceptance_config.py diagnostics [--port COMn]
"""

from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

_REPOSITORY = Path(__file__).resolve().parent.parent
_SOURCE = _REPOSITORY / "configurator" / "src"
if str(_SOURCE) not in sys.path:  # pragma: no cover - exercised by the CLI, not the suite
    sys.path.insert(0, str(_SOURCE))

from duo_input.domain.config_binary import decode_device_config  # noqa: E402
from duo_input.domain.models import Action, Binding, MacroStep, Trigger  # noqa: E402
from duo_input.domain.text_compiler import compile_project_to_binary  # noqa: E402
from duo_input.generated.protocol import (  # noqa: E402
    ActionKind,
    BindingMode,
    KeyboardRoute,
    MACRO_STEPS_PER_MACRO,
    MacroStepType,
    MouseRoute,
    PROFILES,
    TargetMode,
    TextLayout,
    TriggerKind,
)
from duo_input.ui.models.macro_steps import (  # noqa: E402
    set_keyboard_route_step,
    text_step,
)
from duo_input.ui.models.project_session import (  # noqa: E402
    AddBinding,
    AddMacro,
    ProjectSession,
    RenameProfile,
    SetActiveProfile,
    SetMacroSteps,
)

# --------------------------------------------------------------- the design

#: The profile the device must come up in after a power cycle.
#:
#: Deliberately not 1. The firmware falls back to profile 0 when a stored
#: configuration fails to load, and a project's own default is 1, so a device
#: that boots typing ``PROFILE-3`` has demonstrably read the eight profiles out
#: of flash rather than defaulted to anything.
BOOT_PROFILE_ID = 3

#: The text the acceptance names, exactly as the brief words it.
TARGET_TEXT = "/target KYPKYMA"

#: Adjacent duplicates, which ``TARGET_TEXT`` happens not to contain.
#:
#: The defect S4b exists to catch collapsed a press and its release into one
#: state. Two identical characters in a row are where that shows up as *one*
#: character instead of two rather than as nothing at all, so a run of them is
#: the sharpest partial-failure probe available.
REPEAT_TEXT = "a" * 10

#: How many times the burst macro types ``TARGET_TEXT`` on its own line.
BURST_REPEATS = 10

#: Sustained typing, so "no movement stalls" has a window to be observed in.
#: One pass of ``TARGET_TEXT`` is about 45 macro events at one per millisecond,
#: which is over before a hand could notice it.
BURST_TEXT = (TARGET_TEXT + "\n") * BURST_REPEATS

MACRO_IDENT = 1
MACRO_TARGET = 2
MACRO_REPEAT = 3
MACRO_BURST = 4

#: HID usages, from ``duo_input.ui.models.binding_table``'s own naming.
_F1 = 0x3A
_F9, _F10, _F11, _F12 = 0x42, 0x43, 0x44, 0x45
_INSERT, _HOME, _PAGE_UP, _DELETE = 0x49, 0x4A, 0x4B, 0x4C
_RIGHT, _LEFT, _DOWN, _UP = 0x4F, 0x50, 0x51, 0x52


def ident_text(profile_id: int) -> str:
    """What profile ``profile_id`` types when asked to name itself."""
    return f"PROFILE-{profile_id} "


@dataclass(frozen=True)
class MacroPlan:
    """One macro every profile carries, and what the operator should see."""

    macro_id: int
    name: str
    trigger_names: tuple[str, ...]
    text: str

    @property
    def characters(self) -> int:
        return len(self.text)


def macro_plans(profile_id: int) -> tuple[MacroPlan, ...]:
    """The four macros of one profile, in the order they are stored."""
    return (
        MacroPlan(MACRO_IDENT, "IDENT", ("Insert", "Up"), ident_text(profile_id)),
        MacroPlan(MACRO_TARGET, "TARGET", ("Home", "Left"), TARGET_TEXT),
        MacroPlan(MACRO_REPEAT, "REPEAT", ("PageUp", "Right"), REPEAT_TEXT),
        MacroPlan(MACRO_BURST, "TARGET-BURST", ("Delete", "Down"), BURST_TEXT),
    )


def _profile_and_route_bindings() -> list[tuple[Trigger, Action]]:
    """Triggers every profile carries, so no profile is a dead end.

    Every profile binds the same keys to the same meanings. A profile that
    could be reached but not left would strand the acceptance on whichever one
    the operator pressed last. Both configurations start from these, because
    an operator who has learned one keyboard should not have to learn another.
    """
    bindings: list[tuple[Trigger, Action]] = []
    for index in range(PROFILES):
        bindings.append(
            (
                Trigger(TriggerKind.KEYBOARD_USAGE, _F1 + index),
                Action(ActionKind.SET_PROFILE, index + 1),
            )
        )
    bindings += [
        (
            Trigger(TriggerKind.KEYBOARD_USAGE, _F9),
            Action(ActionKind.SET_KEYBOARD_ROUTE, int(KeyboardRoute.PC1)),
        ),
        (
            Trigger(TriggerKind.KEYBOARD_USAGE, _F10),
            Action(ActionKind.SET_KEYBOARD_ROUTE, int(KeyboardRoute.PC2)),
        ),
        (
            Trigger(TriggerKind.KEYBOARD_USAGE, _F11),
            Action(ActionKind.SET_KEYBOARD_ROUTE, int(KeyboardRoute.BOTH)),
        ),
        (Trigger(TriggerKind.KEYBOARD_USAGE, _F12), Action(ActionKind.TOGGLE_MOUSE_ROUTE)),
        (Trigger(TriggerKind.MOUSE_BUTTON, 4), Action(ActionKind.TOGGLE_MOUSE_ROUTE)),
    ]
    return bindings


def _macro_bindings(
    assignments: tuple[tuple[int, int], ...]
) -> list[tuple[Trigger, Action]]:
    """Bind each usage to the macro it runs."""
    return [
        (Trigger(TriggerKind.KEYBOARD_USAGE, usage), Action(ActionKind.RUN_MACRO, macro_id))
        for usage, macro_id in assignments
    ]


def _navigation_bindings() -> tuple[tuple[Trigger, Action], ...]:
    """The Step 4 acceptance's own trigger set."""
    bindings = _profile_and_route_bindings()
    # The navigation cluster is the primary way to run a macro; the arrow keys
    # repeat it, because a keyboard without Insert/Home/PageUp is common enough
    # that discovering it during the acceptance would cost a whole pass.
    bindings += _macro_bindings(
        (
            (_INSERT, MACRO_IDENT),
            (_HOME, MACRO_TARGET),
            (_PAGE_UP, MACRO_REPEAT),
            (_DELETE, MACRO_BURST),
            (_UP, MACRO_IDENT),
            (_LEFT, MACRO_TARGET),
            (_RIGHT, MACRO_REPEAT),
            (_DOWN, MACRO_BURST),
        )
    )
    return tuple(bindings)


def build_session() -> ProjectSession:
    """The acceptance project, built the way the GUI builds one."""
    session = ProjectSession.new().apply(SetActiveProfile(BOOT_PROFILE_ID))
    for profile_id in range(1, PROFILES + 1):
        session = session.apply(RenameProfile(profile_id, f"S4 Profile {profile_id}"))
        for plan in macro_plans(profile_id):
            session = session.apply(AddMacro(profile_id, plan.name, TargetMode.INHERIT))
            macro = session.project.profiles[profile_id - 1].macros[-1]
            if macro.id != plan.macro_id:  # pragma: no cover - guards a renumbering
                raise AssertionError(f"macro {plan.name} was given id {macro.id}")
            session = session.apply(
                SetMacroSteps(
                    profile_id,
                    macro.uuid,
                    (MacroStep(MacroStepType.TEXT, b"", plan.text),),
                )
            )
        for trigger, action in _navigation_bindings():
            session = session.apply(
                AddBinding(profile_id, Binding(trigger, BindingMode.REPLACE, action))
            )
    return session


def build_package() -> bytes:
    """Compile the acceptance project to the package a write would send."""
    return _compile(build_session(), "the acceptance project")


def _compile(session: ProjectSession, what: str) -> bytes:
    issues = session.issues
    if issues:  # pragma: no cover - a defect here, not an operator error
        raise ValueError(f"{what} does not validate: {issues}")
    return compile_project_to_binary(session.project)


#: What ``build_package`` produced when the toggle configuration was added.
#:
#: The Step 4 acceptance ran on hardware against these exact bytes. Anything
#: that moves them invalidates a passed acceptance, so the acceptance suite
#: compares against this rather than trusting that a refactor was harmless.
S4_PACKAGE_SHA256 = "2f64e548dbad3f46b96c0f6e6336a9e229815834686cd86b37bd3468307cfcaa"


# ------------------------------------------------ the route-toggle configuration
#
# The roadmap asks for a thousand physical route toggles. Nobody presses a key
# a thousand times and stays attentive, and an inattentive operator is not an
# observer, so the toggling is device-driven: one macro performs as many route
# changes as a macro is allowed steps for, and the operator supplies the
# starts.
#
# The arithmetic, in full, because "about a thousand" is not evidence:
#
#     a macro holds 64 steps                          (MACRO_STEPS_PER_MACRO)
#     one cycle is a route change and one character   2 steps
#     so one run is                                   32 route changes
#     10 runs is                                      320 route changes
#
# 320, not 1000, and that is a ruling rather than an accident. 32 route
# changes is the ceiling one macro can hold - 64 steps is a hard protocol
# limit, macros do not chain, and a held key does not re-trigger a binding -
# so reaching a thousand means 32 presses, and 32 presses is where the
# procedure stopped being runnable. The run of each press is one full line of
# text, so ten runs are ten lines: they fit on one screen, they count
# themselves, and the operator never counts a character. Thirty-two lines do
# not fit on a screen, and an operator who has lost count is a worse
# instrument than a smaller number honestly reported. The shortfall is
# recorded in the roadmap ledger, with this arithmetic.
#
# Why a character after every route change: it is the load. A route change on
# its own queues almost nothing, and a queue that is never pressed cannot
# refuse a command. It is also the outward evidence - every character goes to
# both computers, so each ends with exactly 32 per run, on one line, and a
# short line or a missing line is the sign that a start was refused or a
# command was dropped.

#: Route changes one run of the toggle macro performs.
TOGGLE_CYCLES = MACRO_STEPS_PER_MACRO // 2

#: What each cycle types. One character, so the count is the toggle count.
TOGGLE_CHARACTER = "a"

#: What the last cycle of a run adds, so one press is one line.
#:
#: This is the whole counting design. A run that left its line open would run
#: into the next one and two presses would read as one; a run that closes its
#: line makes every press a fixed-width group the operator can count at a
#: glance, and makes a dropped character show up as a line shorter than its
#: neighbours instead of as a total that has to be counted to be doubted.
TOGGLE_LINE_END = "\n"

#: How many times the operator starts the macro.
#:
#: Ten, because ten lines fit on one screen and thirty-two do not. See the
#: arithmetic above for why this is 320 route changes and not the roadmap's
#: thousand, and the ledger for the ruling.
TOGGLE_RUNS = 10

MACRO_TOGGLE = 1
MACRO_MARK = 2
MACRO_HOME = 3

#: Bracketing text, so the operator can find the run in a scrolled editor.
MARK_TEXT = "[MARK]\n"


def toggle_route_changes() -> int:
    """Route changes the whole procedure performs, if no run is lost."""
    return TOGGLE_CYCLES * TOGGLE_RUNS


def toggle_characters_per_computer() -> int:
    """Characters each computer must end with, if nothing is dropped.

    The line endings are not counted: they are the group separators, not the
    load, and the operator counts groups.
    """
    return TOGGLE_CYCLES * TOGGLE_RUNS


def toggle_lines() -> int:
    """Lines the whole procedure writes - one per press, so one per run."""
    return TOGGLE_RUNS


def toggle_steps() -> tuple[MacroStep, ...]:
    """One route change and one character, over and over, to the last step.

    The route alternates every cycle, because setting the route it is already
    on changes nothing and releases nothing - the count would be a fiction.
    It starts on PC2 and so ends on PC1, which leaves the device reachable
    from the near computer and makes every run start where the last one did.

    The last cycle closes the line. It costs no extra step - a text step holds
    far more than one character - and it is what turns a press into a group.
    """
    steps: list[MacroStep] = []
    for cycle in range(TOGGLE_CYCLES):
        route = KeyboardRoute.PC2 if cycle % 2 == 0 else KeyboardRoute.PC1
        last = cycle == TOGGLE_CYCLES - 1
        steps.append(set_keyboard_route_step(route))
        steps.append(text_step(TOGGLE_CHARACTER + (TOGGLE_LINE_END if last else "")))
    return tuple(steps)


def _toggle_macro_plans() -> tuple[tuple[int, str, TargetMode, tuple[MacroStep, ...]], ...]:
    """The three macros of a toggle profile, in the order they are stored."""
    return (
        # TargetMode.BOTH and not INHERIT. A macro's route is fixed when it is
        # enqueued: its SET_KEYBOARD_ROUTE steps move where the *operator's*
        # keys go, not where the rest of its own text goes. Under INHERIT every
        # character of a run would land on one computer, and which one would
        # depend on where the previous run happened to finish. BOTH makes the
        # count the same on each computer and therefore countable at all.
        (MACRO_TOGGLE, f"TOGGLE-{TOGGLE_CYCLES}", TargetMode.BOTH, toggle_steps()),
        (MACRO_MARK, "MARK", TargetMode.BOTH, (text_step(MARK_TEXT),)),
        (
            MACRO_HOME,
            "HOME",
            TargetMode.BOTH,
            (set_keyboard_route_step(KeyboardRoute.PC1), text_step("\n")),
        ),
    )


def _toggle_bindings() -> tuple[tuple[Trigger, Action], ...]:
    """The toggle configuration's trigger set.

    Every key the operator script names is an arrow key. The navigation
    cluster is kept beside it - the Step 4 configuration binds both, and an
    operator who has learned one keyboard should not have to learn another -
    but the arrows are the ones the script asks for, because the keyboard this
    is run on is a compact layout with no navigation cluster at all.

    ``Down`` is the odd one out: it is not a macro but the arrow twin of F10,
    the keyboard route to PC2. The run ends on PC1 and the stranded-modifier
    check has to be made on *both* computers, so the operator needs a way to
    move the keyboard across that is not an Fn layer.
    """
    bindings = _profile_and_route_bindings()
    bindings += _macro_bindings(
        (
            (_INSERT, MACRO_TOGGLE),
            (_HOME, MACRO_MARK),
            (_PAGE_UP, MACRO_HOME),
            (_UP, MACRO_TOGGLE),
            (_LEFT, MACRO_MARK),
            (_RIGHT, MACRO_HOME),
        )
    )
    bindings.append(
        (
            Trigger(TriggerKind.KEYBOARD_USAGE, _DOWN),
            Action(ActionKind.SET_KEYBOARD_ROUTE, int(KeyboardRoute.PC2)),
        )
    )
    return tuple(bindings)


def build_toggle_session() -> ProjectSession:
    """The toggle project, built the way the GUI builds one."""
    session = ProjectSession.new().apply(SetActiveProfile(1))
    for profile_id in range(1, PROFILES + 1):
        session = session.apply(RenameProfile(profile_id, f"Toggle {profile_id}"))
        for macro_id, name, target, steps in _toggle_macro_plans():
            session = session.apply(AddMacro(profile_id, name, target))
            macro = session.project.profiles[profile_id - 1].macros[-1]
            if macro.id != macro_id:  # pragma: no cover - guards a renumbering
                raise AssertionError(f"macro {name} was given id {macro.id}")
            session = session.apply(SetMacroSteps(profile_id, macro.uuid, steps))
        for trigger, action in _toggle_bindings():
            session = session.apply(
                AddBinding(profile_id, Binding(trigger, BindingMode.REPLACE, action))
            )
    return session


def build_toggle_package() -> bytes:
    """Compile the toggle project to the package a write would send."""
    return _compile(build_toggle_session(), "the toggle project")


# ---------------------------------------------------- offline verification


@dataclass(frozen=True)
class FieldCheck:
    """One field compared between what was compiled and what decoded."""

    field: str
    written: str
    read_back: str

    @property
    def matches(self) -> bool:
        return self.written == self.read_back


def verify_round_trip(package: bytes, expected=None) -> tuple[FieldCheck, ...]:
    """Compare every field of ``package`` against the project it came from.

    The package is decoded by :func:`decode_device_config`, which is the
    independent reader the emulator and the read-back path both use, and each
    field is compared against the project the compiler was handed. A comparison
    of two hashes would say only that something round-tripped; this says which
    field did.

    ``expected`` is the project the package should hold; the Step 4 acceptance
    project when it is not given.
    """
    if expected is None:
        expected = build_session().project
    decoded = decode_device_config(package)
    checks: list[FieldCheck] = [
        FieldCheck(
            "active_profile_id",
            str(expected.active_profile_id),
            str(decoded.active_profile_id),
        ),
        FieldCheck("profile_count", str(len(expected.profiles)), str(len(decoded.profiles))),
    ]
    for index, wanted in enumerate(expected.profiles):
        got = decoded.profiles[index] if index < len(decoded.profiles) else None
        prefix = f"profile[{wanted.id}]"
        if got is None:  # pragma: no cover - a decoder that lost a profile
            checks.append(FieldCheck(prefix, "present", "missing"))
            continue
        checks += [
            FieldCheck(f"{prefix}.id", str(wanted.id), str(got.id)),
            FieldCheck(f"{prefix}.name", wanted.name, got.name),
            FieldCheck(f"{prefix}.color_rgb", str(tuple(wanted.color_rgb)), str(tuple(got.color_rgb))),
            FieldCheck(
                f"{prefix}.keyboard_route",
                KeyboardRoute(wanted.keyboard_route).name,
                KeyboardRoute(got.keyboard_route).name,
            ),
            FieldCheck(
                f"{prefix}.mouse_route",
                MouseRoute(wanted.mouse_route).name,
                MouseRoute(got.mouse_route).name,
            ),
            FieldCheck(
                f"{prefix}.text_layout",
                TextLayout(wanted.text_layout).name,
                TextLayout(got.text_layout).name,
            ),
        ]
        checks += _binding_checks(prefix, wanted, got)
        checks += _macro_checks(prefix, wanted, got)
    return tuple(checks)


def _binding_checks(prefix: str, wanted, got) -> list[FieldCheck]:
    checks = [
        FieldCheck(
            f"{prefix}.binding_count", str(len(wanted.bindings)), str(len(got.bindings))
        )
    ]
    for position, expected_binding in enumerate(wanted.bindings):
        path = f"{prefix}.binding[{position}]"
        if position >= len(got.bindings):  # pragma: no cover - a truncated table
            checks.append(FieldCheck(path, _binding_text(expected_binding), "missing"))
            continue
        checks.append(
            FieldCheck(
                path, _binding_text(expected_binding), _binding_text(got.bindings[position])
            )
        )
    return checks


def _macro_checks(prefix: str, wanted, got) -> list[FieldCheck]:
    checks = [FieldCheck(f"{prefix}.macro_count", str(len(wanted.macros)), str(len(got.macros)))]
    for position, expected_macro in enumerate(wanted.macros):
        path = f"{prefix}.macro[{position}]"
        if position >= len(got.macros):  # pragma: no cover - a truncated table
            checks.append(FieldCheck(path, expected_macro.name, "missing"))
            continue
        actual_macro = got.macros[position]
        checks += [
            FieldCheck(f"{path}.id", str(expected_macro.id), str(actual_macro.id)),
            FieldCheck(f"{path}.name", expected_macro.name, actual_macro.name),
            FieldCheck(
                f"{path}.target",
                TargetMode(expected_macro.target).name,
                TargetMode(actual_macro.target).name,
            ),
            FieldCheck(
                f"{path}.step_count",
                str(len(expected_macro.steps)),
                str(len(actual_macro.steps)),
            ),
        ]
        for step_index, expected_step in enumerate(expected_macro.steps):
            step_path = f"{path}.step[{step_index}]"
            if step_index >= len(actual_macro.steps):  # pragma: no cover
                checks.append(FieldCheck(step_path, "present", "missing"))
                continue
            actual_step = actual_macro.steps[step_index]
            checks += [
                FieldCheck(
                    f"{step_path}.type",
                    MacroStepType(expected_step.type).name,
                    MacroStepType(actual_step.type).name,
                ),
                # The decoded step carries compiled chords, so the source text
                # is compared through the same compiler the write used.
                FieldCheck(
                    f"{step_path}.payload",
                    _payload_text(expected_step, wanted.text_layout),
                    actual_step.payload.hex(),
                ),
            ]
    return checks


def _payload_text(step: MacroStep, layout) -> str:
    if step.type == MacroStepType.TEXT and step.source_text is not None:
        from duo_input.domain.text_compiler import compile_text_payload

        return compile_text_payload(step.source_text, layout).hex()
    return step.payload.hex()


def _binding_text(binding: Binding) -> str:
    return (
        f"{TriggerKind(binding.trigger.kind).name}:0x{binding.trigger.code:02X}"
        f"/mod{binding.trigger.modifiers:02X} "
        f"{BindingMode(binding.mode).name} "
        f"{ActionKind(binding.action.kind).name}({binding.action.argument})"
    )


def describe(package: bytes) -> str:
    """A human-readable summary of what the acceptance configuration holds."""
    config = decode_device_config(package)
    lines = [
        f"package: {len(package)} bytes, sha256 {hashlib.sha256(package).hexdigest()}",
        f"active profile at boot: {config.active_profile_id}",
        f"profiles: {len(config.profiles)}",
        "",
    ]
    for profile in config.profiles:
        lines.append(
            f"profile {profile.id} {profile.name!r} "
            f"kbd={KeyboardRoute(profile.keyboard_route).name} "
            f"mouse={MouseRoute(profile.mouse_route).name} "
            f"layout={TextLayout(profile.text_layout).name} "
            f"bindings={len(profile.bindings)} macros={len(profile.macros)}"
        )
        for plan in macro_plans(profile.id):
            lines.append(
                f"    macro {plan.macro_id} {plan.name:<13} "
                f"{'/'.join(plan.trigger_names):<12} "
                f"{plan.characters:>4} chars  {plan.text!r}"
            )
    return "\n".join(lines)


def describe_toggle(package: bytes) -> str:
    """The toggle configuration, and the arithmetic the operator follows."""
    config = decode_device_config(package)
    routes = [
        KeyboardRoute(step.payload[0]).name
        for macro in config.profiles[0].macros
        if macro.id == MACRO_TOGGLE
        for step in macro.steps
        if MacroStepType(step.type) == MacroStepType.SET_KEYBOARD_ROUTE
    ]
    lines = [
        f"package: {len(package)} bytes, sha256 {hashlib.sha256(package).hexdigest()}",
        f"active profile at boot: {config.active_profile_id}",
        f"profiles: {len(config.profiles)}",
        "",
        "one run of TOGGLE (Up, or Insert):",
        f"    macro steps          {MACRO_STEPS_PER_MACRO}"
        f"  (the whole budget; 2 per cycle)",
        f"    route changes        {TOGGLE_CYCLES}",
        f"    characters typed     {TOGGLE_CYCLES} to BOTH computers,"
        f" then a line ending",
        f"    so one press is      one line of {TOGGLE_CYCLES} characters",
        f"    route order          {routes[0]} .. {routes[-1]}, alternating",
        "",
        "the whole procedure:",
        f"    presses              {TOGGLE_RUNS}",
        f"    route changes        {TOGGLE_CYCLES} x {TOGGLE_RUNS}"
        f" = {toggle_route_changes()}",
        f"    lines expected       {toggle_lines()} on EACH computer,"
        f" {toggle_characters_per_computer()} characters in all",
        "",
        f"the roadmap asks for 1000 physical toggles and this performs"
        f" {toggle_route_changes()}.",
        f"32 is the ceiling one macro can hold, so 1000 needs 32 presses, and 32",
        "presses is where the procedure stopped being runnable: 32 lines do not",
        "fit on a screen and the count stops counting itself. The shortfall is a",
        "recorded ruling in the roadmap ledger, not a substitution.",
        "",
        "other keys: Down moves the keyboard to PC2 (F10's twin), Right brings it",
        "back and starts a fresh line, Left writes a [MARK] bracket.",
        "",
    ]
    for profile in config.profiles:
        lines.append(
            f"profile {profile.id} {profile.name!r} "
            f"kbd={KeyboardRoute(profile.keyboard_route).name} "
            f"mouse={MouseRoute(profile.mouse_route).name} "
            f"bindings={len(profile.bindings)} macros={len(profile.macros)} "
            f"steps={[len(macro.steps) for macro in profile.macros]}"
        )
    return "\n".join(lines + ["", toggle_operator_script()])


def toggle_operator_script() -> str:
    """What the person at the bench does, in the order they do it.

    Kept here rather than in a document so that it cannot disagree with the
    numbers above it: both are rendered from the same constants.

    Every key it names is an arrow key. Left Shift is held once, for the whole
    run, and not re-gripped: the stranded-modifier release was accepted on
    hardware by S4c, across an unplug and a reconnect, and what is being asked
    here is only that the modifier is not stranded at the end. Nothing is
    counted by eye - one press writes one line, so the lines count the presses
    and the editor's own status bar carries the total.
    """
    lines_expected = toggle_lines()
    return "\n".join(
        [
            "operator script",
            "---------------",
            "  1. Read the counters. Write down dropped_commands and runtime_fault:",
            "         python tools/step4_acceptance_config.py diagnostics",
            "     Then open an empty Notepad on BOTH computers, click in it, and",
            "     switch on View > Status Bar. While this configuration is loaded",
            "     the arrow keys drive the device and will not move the cursor.",
            "  2. Hold Left Shift down. Keep holding it until step 4 - do not let",
            "     go and do not take a fresh grip. It is the key that must not be",
            "     left stranded when the route moves out from under it.",
            f"  3. Press Up {TOGGLE_RUNS} times, about one press a second. Each press writes",
            f"     one line of {TOGGLE_CYCLES} characters on BOTH computers. If a line is",
            "     still filling, let it finish before pressing again.",
            f"  4. Let go of Left Shift. Each screen must show {lines_expected} lines of the",
            "     same length and nothing else, and the status bar must read",
            f"     Ln {lines_expected + 1}, Col 1.",
            "  5. Type `abc` here, by hand. Press Down - the keyboard moves to the",
            "     other computer - type `abc` there, then press Right to come back.",
            "  6. Read the counters again:",
            "         python tools/step4_acceptance_config.py diagnostics",
            "",
            "what a pass looks like",
            f"  - {lines_expected} lines of {TOGGLE_CYCLES} characters on each computer, so"
            f" {toggle_route_changes()} route",
            f"    changes and {toggle_characters_per_computer()} characters got through",
            f"  - the status bar read Ln {lines_expected + 1}, Col 1 on both, before step 5",
            "  - dropped_commands unchanged from step 1, runtime_fault 0",
            "  - step 5 typed `abc`, lower case, on both computers",
            "",
            "what a failure looks like, and which is which",
            "  - step 5 typed `ABC`: Left Shift is stranded on that computer. FAIL.",
            "  - step 5 typed nothing on one computer: that link is stuck. FAIL.",
            "  - dropped_commands went up, or runtime_fault is 1. FAIL.",
            "  - one line shorter than the others: a macro step was lost. FAIL,",
            "    and say which line it was.",
            f"  - fewer than {lines_expected} lines: a press was refused while runs were still",
            "    queued. NOT a firmware failure. Press Right, clear both editors",
            "    and start again at step 2, more slowly.",
        ]
    )


# ------------------------------------------------------ the two configurations


@dataclass(frozen=True)
class Configuration:
    """One configuration, and everything the CLI needs to handle it."""

    name: str
    build_session: "callable"
    describe: "callable"
    backup_label: str
    backup_reason: str

    def build_package(self) -> bytes:
        return _compile(self.build_session(), f"the {self.name} project")

    def round_trip(self, package: bytes) -> tuple[FieldCheck, ...]:
        return verify_round_trip(package, expected=self.build_session().project)


CONFIGURATIONS = {
    "step4": Configuration(
        name="step4",
        build_session=build_session,
        describe=describe,
        backup_label="before-step4-acceptance",
        backup_reason=(
            "Captured before the Task 7 Step 4 acceptance configuration "
            "(eight self-naming profiles and the /target KYPKYMA macro) was written."
        ),
    ),
    "toggle": Configuration(
        name="toggle",
        build_session=build_toggle_session,
        describe=describe_toggle,
        backup_label="before-route-toggle",
        backup_reason=(
            "Captured before the route-toggle configuration "
            "(a macro that changes the keyboard route 32 times per run, one "
            "run per line) was written."
        ),
    ),
}


# ------------------------------------------------------------------ backup


def backup_document(
    package: bytes,
    *,
    serial_number: str,
    generation: int,
    reason: str,
    taken_at: str,
) -> str:
    """The backup file's text, in the shape the earlier backup already uses."""
    encoded = base64.b64encode(package).decode("ascii")
    wrapped = "\n".join(textwrap.wrap(encoded, 64))
    return (
        "# Duo Input U1 configuration backup\n"
        "\n"
        f"{reason}\n"
        "\n"
        f"- Device: U1 (`{serial_number}`)\n"
        f"- Generation: {generation}\n"
        f"- Size: {len(package)} bytes\n"
        f"- SHA-256: `{hashlib.sha256(package).hexdigest().upper()}`\n"
        f"- Taken: {taken_at}\n"
        "- Encoding: Base64 of the exact binary configuration package\n"
        "\n"
        "Restore with:\n"
        "\n"
        "```powershell\n"
        "# strip the fence, decode, and write it back through the same tool\n"
        "python tools/step4_acceptance_config.py deploy `\n"
        "    --package <decoded.bin> --backup-dir hardware-backups\n"
        "```\n"
        "\n"
        "```text\n"
        f"{wrapped}\n"
        "```\n"
    )


def package_from_backup(document: str) -> bytes:
    """The package inside a backup document, so a restore needs no guesswork."""
    fences = document.split("```")
    for block in fences:
        if not block.startswith("text\n"):
            continue
        body = "".join(block[len("text\n") :].split())
        return base64.b64decode(body, validate=True)
    raise ValueError("the backup document contains no base64 block")


# ------------------------------------------------------------------ deploy


@dataclass(frozen=True)
class DeployResult:
    """What one deployment did, in the order it did it."""

    generation_before: int
    previous_package: bytes
    written_package: bytes
    read_back_package: bytes

    @property
    def read_back_matches(self) -> bool:
        return self.read_back_package == self.written_package


class DeployError(RuntimeError):
    """The device refused a step, or never answered one."""


def _await(service, start, timeout_ms: int, what: str):
    """Run ``start`` and return the value the service reports, or raise."""
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    outcome: dict[str, object] = {}

    def succeeded(result) -> None:
        outcome["value"] = result.value
        loop.quit()

    def failed(failure) -> None:
        outcome["error"] = failure
        loop.quit()

    service.operation_succeeded.connect(succeeded)
    service.operation_failed.connect(failed)
    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(loop.quit)
    try:
        guard.start(timeout_ms)
        start()
        if not outcome:
            loop.exec()
    finally:
        guard.stop()
        service.operation_succeeded.disconnect(succeeded)
        service.operation_failed.disconnect(failed)
    if "error" in outcome:
        raise DeployError(f"{what} failed: {outcome['error']}")
    if "value" not in outcome:
        raise DeployError(f"{what} never answered within {timeout_ms} ms")
    return outcome["value"]


def deploy(link, package: bytes, *, timeout_ms: int = 5000, step_timeout_ms: int = 120000):
    """Back up, write and read back, in that order, over ``link``.

    Nothing is written until the existing configuration has been read and is in
    hand. A device that cannot be read is a device that must not be overwritten,
    so a failed read aborts before ``write_config`` is ever called.
    """
    from duo_input.device.service import DeviceService

    service = DeviceService(timeout_ms=timeout_ms)
    try:
        info = _await(
            service, lambda: service.connect_device(link), step_timeout_ms, "connect_device"
        )
        previous = _await(service, service.read_config, step_timeout_ms, "read_config (backup)")
        if not isinstance(previous, (bytes, bytearray)) or not previous:
            raise DeployError("the device returned an empty configuration; refusing to overwrite")
        _await(
            service, lambda: service.write_config(package), step_timeout_ms, "write_config"
        )
        read_back = _await(service, service.read_config, step_timeout_ms, "read_config (verify)")
    finally:
        service.disconnect_device()
    return DeployResult(
        generation_before=info.active_generation,
        previous_package=bytes(previous),
        written_package=bytes(package),
        read_back_package=bytes(read_back),
    )


# --------------------------------------------------------------------- CLI


def _selected(args: argparse.Namespace) -> Configuration:
    return CONFIGURATIONS[getattr(args, "config", "step4")]


def _report_round_trip(package: bytes, configuration: Configuration) -> int:
    checks = configuration.round_trip(package)
    bad = [check for check in checks if not check.matches]
    print(f"round-trip fields compared: {len(checks)}")
    for check in bad:
        print(f"  MISMATCH {check.field}: wrote {check.written!r}, read {check.read_back!r}")
    if bad:
        print(f"round-trip: FAILED ({len(bad)} mismatched)")
        return 1
    print("round-trip: every field matches")
    return 0


def _cmd_describe(args: argparse.Namespace) -> int:
    configuration = _selected(args)
    print(configuration.describe(configuration.build_package()))
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    configuration = _selected(args)
    package = configuration.build_package()
    status = _report_round_trip(package, configuration)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(package)
    print(f"wrote {output} ({len(package)} bytes)")
    print(f"sha256 {hashlib.sha256(package).hexdigest()}")
    return status


def _cmd_deploy(args: argparse.Namespace) -> int:
    from PySide6.QtCore import QCoreApplication

    from duo_input.device.discovery import find_u1_ports
    from duo_input.device.qt_transport import QSerialPortTransport

    application = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    configuration = _selected(args)
    if args.restore_from:
        package = package_from_backup(Path(args.restore_from).read_text(encoding="utf-8"))
        label = "before-restore"
        reason = f"Captured before restoring {Path(args.restore_from).name}."
    else:
        package = (
            Path(args.package).read_bytes() if args.package else configuration.build_package()
        )
        label = configuration.backup_label
        reason = configuration.backup_reason

    candidates = {candidate.port_name: candidate.serial_number for candidate in find_u1_ports()}
    port_name = args.port
    if port_name is None:
        if not candidates:
            print("no U1 found on the bus", file=sys.stderr)
            return 2
        port_name = next(iter(candidates))
    serial_number = candidates.get(port_name, "unknown")
    print(f"port: {port_name} ({serial_number})")

    try:
        result = deploy(QSerialPortTransport(port_name), package)
    except DeployError as error:
        print(str(error), file=sys.stderr)
        return 1

    backup_directory = Path(args.backup_dir)
    backup_directory.mkdir(parents=True, exist_ok=True)
    backup = backup_directory / (
        f"u1-config-generation-{result.generation_before}-{label}.b64"
    )
    backup.write_text(
        backup_document(
            result.previous_package,
            serial_number=serial_number,
            generation=result.generation_before,
            reason=reason,
            taken_at=datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        ),
        encoding="utf-8",
    )
    print(f"backup: {backup} ({len(result.previous_package)} bytes)")
    print(f"written: {len(result.written_package)} bytes, "
          f"sha256 {hashlib.sha256(result.written_package).hexdigest()}")
    print(f"read back: {len(result.read_back_package)} bytes, "
          f"sha256 {hashlib.sha256(result.read_back_package).hexdigest()}")
    if not result.read_back_matches:
        print("read-back: MISMATCH", file=sys.stderr)
        return 1
    print("read-back: identical to what was written")
    assert application is not None  # kept alive for the whole exchange
    if package != configuration.build_package():
        # A restored package is somebody else's configuration; the field
        # comparison only means anything against the one this module builds.
        return 0
    return _report_round_trip(result.read_back_package, configuration)


def _cmd_diagnostics(args: argparse.Namespace) -> int:
    """Read the device's counters, before and after a run.

    ``dropped_commands`` is cumulative and never goes down, so it is read
    before and after and the difference is what matters. ``runtime_fault``
    says what the output queue is doing at this instant, which the cumulative
    counter cannot: a burst that has passed and one that is still going on
    look the same to it.
    """
    from PySide6.QtCore import QCoreApplication

    from duo_input.device.discovery import find_u1_ports
    from duo_input.device.qt_transport import QSerialPortTransport
    from duo_input.device.service import DeviceService

    application = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    candidates = {candidate.port_name: candidate.serial_number for candidate in find_u1_ports()}
    port_name = args.port
    if port_name is None:
        if not candidates:
            print("no U1 found on the bus", file=sys.stderr)
            return 2
        port_name = next(iter(candidates))
    print(f"port: {port_name} ({candidates.get(port_name, 'unknown')})")

    service = DeviceService(timeout_ms=5000)
    try:
        _await(
            service,
            lambda: service.connect_device(QSerialPortTransport(port_name)),
            120000,
            "connect_device",
        )
        counters = _await(service, service.get_diagnostics, 120000, "get_diagnostics")
    except DeployError as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        service.disconnect_device()
    assert application is not None  # kept alive for the whole exchange

    for field, value in sorted(vars(counters).items()):
        print(f"{field:<22} {value}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    def add_config_option(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--config",
            choices=sorted(CONFIGURATIONS),
            default="step4",
            help="which configuration to work with (default: step4)",
        )

    describe_command = commands.add_parser(
        "describe", help="print what the configuration holds"
    )
    add_config_option(describe_command)
    describe_command.set_defaults(handler=_cmd_describe)

    build = commands.add_parser("build", help="compile the package to a file")
    build.add_argument("--output", required=True, help="where to write the .bin")
    add_config_option(build)
    build.set_defaults(handler=_cmd_build)

    diagnostics = commands.add_parser(
        "diagnostics", help="read the device's counters, including dropped_commands"
    )
    diagnostics.add_argument("--port", default=None, help="serial port; discovered if omitted")
    diagnostics.set_defaults(handler=_cmd_diagnostics)

    deploy_command = commands.add_parser(
        "deploy", help="back up, write and read back over a serial port"
    )
    add_config_option(deploy_command)
    deploy_command.add_argument("--port", default=None, help="serial port; discovered if omitted")
    deploy_command.add_argument("--package", default=None, help="package to write; built if omitted")
    deploy_command.add_argument(
        "--restore-from",
        default=None,
        help="a .b64 backup document to put back on the device instead",
    )
    deploy_command.add_argument(
        "--backup-dir", default="hardware-backups", help="where the backup file is written"
    )
    deploy_command.set_defaults(handler=_cmd_deploy)

    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
