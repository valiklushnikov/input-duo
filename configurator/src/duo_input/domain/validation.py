from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from duo_input.domain.models import (
    Action,
    Binding,
    DeviceProject,
    Macro,
    MacroStep,
    Profile,
    Trigger,
)
from duo_input.generated.protocol import (
    ActionKind,
    BINDINGS_PER_PROFILE,
    BindingMode,
    KeyboardRoute,
    MACRO_STEPS_PER_MACRO,
    MACROS_PER_PROFILE,
    MacroStepType,
    MouseRoute,
    PROFILES,
    TargetMode,
    TextLayout,
    TriggerKind,
)


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str


def _is_enum(value: object, enum_type: type[object]) -> bool:
    try:
        enum_type(value)
    except (TypeError, ValueError):
        return False
    return True


def _is_u8(value: object, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= 0xFF


def _name_is_valid(value: object) -> bool:
    if not isinstance(value, str) or "\0" in value or len(value) > 48:
        return False
    return _is_utf8_encodable(value)


def _is_utf8_encodable(value: str) -> bool:
    try:
        return len(value.encode("utf-8")) <= 0xFFFF
    except UnicodeEncodeError:
        return False


def validate_project(project: DeviceProject) -> tuple[ValidationIssue, ...]:
    """Return all user-fixable project errors with JSON-pointer-like locations."""

    issues: list[ValidationIssue] = []
    if not isinstance(project, DeviceProject):
        return (ValidationIssue("", "project must be a DeviceProject"),)
    if not isinstance(project.profiles, tuple):
        return (ValidationIssue("/profiles", "profiles must be an immutable tuple"),)
    if len(project.profiles) != PROFILES:
        issues.append(ValidationIssue("/profiles", f"project must contain exactly {PROFILES} profiles"))

    profile_ids = [
        profile.id
        for profile in project.profiles
        if isinstance(profile, Profile) and _is_u8(profile.id, minimum=1)
    ]
    macro_uuids: set[UUID] = set()
    binding_uuids: set[UUID] = set()
    for profile_index, profile in enumerate(project.profiles):
        profile_path = f"/profiles/{profile_index}"
        if not isinstance(profile, Profile):
            issues.append(ValidationIssue(profile_path, "profile must be a Profile"))
            continue
        if not _is_u8(profile.id, minimum=1):
            issues.append(ValidationIssue(f"{profile_path}/id", "profile ID must be 1..255"))
        if not _name_is_valid(profile.name):
            issues.append(ValidationIssue(f"{profile_path}/name", "profile name is invalid"))
        if (
            not isinstance(profile.color_rgb, tuple)
            or len(profile.color_rgb) != 3
            or any(not _is_u8(component) for component in profile.color_rgb)
        ):
            issues.append(ValidationIssue(f"{profile_path}/color_rgb", "profile color must contain three u8 values"))
        for field, value, enum_type in (
            ("keyboard_route", profile.keyboard_route, KeyboardRoute),
            ("mouse_route", profile.mouse_route, MouseRoute),
            ("text_layout", profile.text_layout, TextLayout),
        ):
            if not _is_enum(value, enum_type):
                issues.append(ValidationIssue(f"{profile_path}/{field}", f"unsupported {field}"))

        _validate_macros(profile, profile_path, macro_uuids, issues)
        _validate_bindings(profile, profile_path, profile_ids, binding_uuids, issues)

    expected_profile_ids = list(range(1, PROFILES + 1))
    if profile_ids != expected_profile_ids:
        issues.append(ValidationIssue("/profiles", "profile IDs must be exactly 1..8 in order"))
    if not _is_u8(project.active_profile_id, minimum=1) or project.active_profile_id not in profile_ids:
        issues.append(ValidationIssue("/active_profile_id", "active profile references an unknown profile"))
    return tuple(issues)


def _validate_macros(
    profile: Profile, profile_path: str, macro_uuids: set[UUID], issues: list[ValidationIssue]
) -> None:
    macros_path = f"{profile_path}/macros"
    if not isinstance(profile.macros, tuple):
        issues.append(ValidationIssue(macros_path, "macros must be an immutable tuple"))
        return
    if len(profile.macros) > MACROS_PER_PROFILE:
        issues.append(ValidationIssue(macros_path, f"profile may contain at most {MACROS_PER_PROFILE} macros"))
    macro_ids: set[int] = set()
    for macro_index, macro in enumerate(profile.macros):
        path = f"{macros_path}/{macro_index}"
        if not isinstance(macro, Macro):
            issues.append(ValidationIssue(path, "macro must be a Macro"))
            continue
        if not isinstance(macro.uuid, UUID):
            issues.append(ValidationIssue(f"{path}/uuid", "macro UUID is invalid"))
        elif macro.uuid in macro_uuids:
            issues.append(ValidationIssue(f"{path}/uuid", "macro UUID must be unique"))
        else:
            macro_uuids.add(macro.uuid)
        if not _is_u8(macro.id, minimum=1):
            issues.append(ValidationIssue(f"{path}/id", "macro ID must be 1..255"))
        elif macro.id in macro_ids:
            issues.append(ValidationIssue(f"{path}/id", "macro ID must be unique within a profile"))
        else:
            macro_ids.add(macro.id)
        if not _name_is_valid(macro.name):
            issues.append(ValidationIssue(f"{path}/name", "macro name is invalid"))
        if not _is_enum(macro.target, TargetMode):
            issues.append(ValidationIssue(f"{path}/target", "unsupported macro target"))
        _validate_steps(macro, path, issues)


def _validate_steps(macro: Macro, macro_path: str, issues: list[ValidationIssue]) -> None:
    steps_path = f"{macro_path}/steps"
    if not isinstance(macro.steps, tuple):
        issues.append(ValidationIssue(steps_path, "steps must be an immutable tuple"))
        return
    if len(macro.steps) > MACRO_STEPS_PER_MACRO:
        issues.append(ValidationIssue(steps_path, f"macro may contain at most {MACRO_STEPS_PER_MACRO} steps"))
    for step_index, step in enumerate(macro.steps):
        path = f"{steps_path}/{step_index}"
        if not isinstance(step, MacroStep):
            issues.append(ValidationIssue(path, "step must be a MacroStep"))
            continue
        if not _is_enum(step.type, MacroStepType):
            issues.append(ValidationIssue(f"{path}/type", "unsupported macro step type"))
        if not isinstance(step.payload, bytes):
            issues.append(ValidationIssue(f"{path}/payload", "step payload must be bytes"))
        if step.source_text is not None:
            if not isinstance(step.source_text, str) or not _is_utf8_encodable(step.source_text):
                issues.append(ValidationIssue(f"{path}/source_text", "source text must be valid UTF-8 Unicode"))
            elif step.type != MacroStepType.TEXT:
                issues.append(ValidationIssue(f"{path}/source_text", "only TEXT steps may contain source text"))


def _validate_bindings(
    profile: Profile,
    profile_path: str,
    profile_ids: list[int],
    binding_uuids: set[UUID],
    issues: list[ValidationIssue],
) -> None:
    bindings_path = f"{profile_path}/bindings"
    if not isinstance(profile.bindings, tuple):
        issues.append(ValidationIssue(bindings_path, "bindings must be an immutable tuple"))
        return
    if len(profile.bindings) > BINDINGS_PER_PROFILE:
        issues.append(ValidationIssue(bindings_path, f"profile may contain at most {BINDINGS_PER_PROFILE} bindings"))
    triggers: set[tuple[int, int, int, int, int, int]] = set()
    macro_ids = {macro.id for macro in profile.macros if isinstance(macro, Macro) and _is_u8(macro.id, minimum=1)}
    for binding_index, binding in enumerate(profile.bindings):
        path = f"{bindings_path}/{binding_index}"
        if not isinstance(binding, Binding):
            issues.append(ValidationIssue(path, "binding must be a Binding"))
            continue
        if not isinstance(binding.uuid, UUID):
            issues.append(ValidationIssue(f"{path}/uuid", "binding UUID is invalid"))
        elif binding.uuid in binding_uuids:
            issues.append(ValidationIssue(f"{path}/uuid", "binding UUID must be unique"))
        else:
            binding_uuids.add(binding.uuid)
        _validate_trigger(binding.trigger, path, triggers, issues)
        if not _is_enum(binding.mode, BindingMode):
            issues.append(ValidationIssue(f"{path}/mode", "unsupported binding mode"))
        _validate_action(binding.action, path, macro_ids, profile_ids, issues)


def _validate_trigger(trigger: object, path: str, triggers: set[tuple[int, int, int, int, int, int]], issues: list[ValidationIssue]) -> None:
    if not isinstance(trigger, Trigger):
        issues.append(ValidationIssue(f"{path}/trigger", "trigger must be a Trigger"))
        return
    consumer = trigger.kind == TriggerKind.CONSUMER_USAGE
    valid_code = type(trigger.code) is int and 1 <= trigger.code <= (0xFFFF if consumer else 0xFF)
    if not _is_enum(trigger.kind, TriggerKind) or not valid_code or not _is_u8(trigger.modifiers) or (consumer and trigger.modifiers != 0):
        issues.append(ValidationIssue(f"{path}/trigger", "trigger is invalid"))
        return
    if trigger.kind == TriggerKind.MOUSE_BUTTON and (trigger.code > 5 or trigger.modifiers != 0):
        issues.append(ValidationIssue(f"{path}/trigger", "mouse trigger must be button 1..5 without modifiers"))
        return
    # Uniqueness is keyed on the source as well, because Ctrl+Right from the
    # mouse and Ctrl+Right from the keyboard are two bindings and being able
    # to have both is the whole point of qualifying a trigger. The key is the
    # same six-tuple the binary decoder uses (config_binary._decode), so the
    # two layers cannot disagree about what a duplicate is - and "no source"
    # is spelled as the all-zero triple there, so it is spelled that way here.
    source = trigger.source
    if source is not None and (
        not all(type(value) is int for value in (source.vendor_id, source.product_id, source.interface_number))
        or not 0 <= source.vendor_id <= 0xFFFF
        or not 0 <= source.product_id <= 0xFFFF
        or not 0 <= source.interface_number <= 0xFF
        or ((source.vendor_id, source.product_id, source.interface_number) != (0, 0, 0)
            and (source.vendor_id == 0 or source.product_id == 0))
    ):
        issues.append(ValidationIssue(f"{path}/trigger/source", "trigger source is invalid"))
        return
    key = (
        int(trigger.kind),
        trigger.code,
        trigger.modifiers,
        0 if source is None else int(source.vendor_id),
        0 if source is None else int(source.product_id),
        0 if source is None else int(source.interface_number),
    )
    if key in triggers:
        issues.append(ValidationIssue(f"{path}/trigger", "duplicate trigger within profile"))
    else:
        triggers.add(key)


def _validate_action(action: object, path: str, macro_ids: set[int], profile_ids: list[int], issues: list[ValidationIssue]) -> None:
    if not isinstance(action, Action) or not _is_enum(action.kind, ActionKind) or not _is_u8(action.argument):
        issues.append(ValidationIssue(f"{path}/action", "action is invalid"))
        return
    if action.kind == ActionKind.RUN_MACRO and action.argument not in macro_ids:
        issues.append(ValidationIssue(f"{path}/action", "run-macro action references an unknown macro"))
    elif action.kind in (ActionKind.TOGGLE_KEYBOARD_ROUTE, ActionKind.TOGGLE_MOUSE_ROUTE) and action.argument != 0:
        issues.append(ValidationIssue(f"{path}/action", "toggle action argument must be zero"))
    elif action.kind == ActionKind.SET_KEYBOARD_ROUTE and not _is_enum(action.argument, KeyboardRoute):
        issues.append(ValidationIssue(f"{path}/action", "unsupported keyboard route"))
    elif action.kind == ActionKind.SET_MOUSE_ROUTE and not _is_enum(action.argument, MouseRoute):
        issues.append(ValidationIssue(f"{path}/action", "unsupported mouse route"))
    elif action.kind == ActionKind.SET_PROFILE and action.argument not in profile_ids:
        issues.append(ValidationIssue(f"{path}/action", "set-profile action references an unknown profile"))
