from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from duo_input.domain.models import (
    Action,
    Binding,
    DeviceProject,
    Macro,
    MacroStep,
    Profile,
    Trigger,
    TriggerSource,
)
from duo_input.domain.validation import ValidationIssue, validate_project
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


PROJECT_SCHEMA_VERSION = "1.2"
_SUPPORTED_SCHEMA_MAJOR = 1
_SUPPORTED_OLDER_MINORS = {0, 1}


class ProjectError(ValueError):
    """The project file is malformed or cannot be used."""


class ProjectVersionError(ProjectError):
    """The project schema is not supported by this application."""


class ProjectValidationError(ProjectError):
    def __init__(self, issues: tuple[ValidationIssue, ...]):
        self.issues = issues
        super().__init__("project validation failed: " + "; ".join(f"{item.path}: {item.message}" for item in issues))


def save_project_atomic(project: DeviceProject, path: str | Path) -> None:
    issues = validate_project(project)
    if issues:
        raise ProjectValidationError(issues)
    destination = _project_path(path)
    document = _project_to_json(project)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise


def load_project(path: str | Path) -> DeviceProject:
    source = _project_path(path)
    try:
        with source.open("r", encoding="utf-8") as stream:
            document = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectError(f"cannot load project: {exc}") from exc
    if not isinstance(document, dict):
        raise ProjectError("project root must be an object")
    migrated = _migrate_document(document)
    project = _project_from_json(migrated)
    issues = validate_project(project)
    if issues:
        raise ProjectValidationError(issues)
    return project


def _migrate_document(document: dict[str, Any]) -> dict[str, Any]:
    version = document.get("schema_version")
    if not isinstance(version, str):
        raise ProjectVersionError("schema_version must be a major.minor string")
    try:
        major_text, minor_text = version.split(".", 1)
        major, minor = int(major_text), int(minor_text)
    except (TypeError, ValueError):
        raise ProjectVersionError("schema_version must be a major.minor string") from None
    if major != _SUPPORTED_SCHEMA_MAJOR:
        raise ProjectVersionError(f"unsupported project schema major {major}")
    current_minor = int(PROJECT_SCHEMA_VERSION.split(".", 1)[1])
    if minor == current_minor:
        return document
    if minor not in _SUPPORTED_OLDER_MINORS or minor > current_minor:
        raise ProjectVersionError(f"unsupported project schema version {version}")
    migrated = dict(document)
    while minor < current_minor:
        if minor == 0:
            migrated = _migrate_1_0_to_1_1(migrated)
            minor = 1
        elif minor == 1:
            migrated = _migrate_1_1_to_1_2(migrated)
            minor = 2
        else:  # pragma: no cover - guarded by supported migration table
            raise ProjectVersionError(f"unsupported project schema version {version}")
    return migrated


def _project_path(path: str | Path) -> Path:
    project_path = Path(path)
    if not project_path.name.endswith(".duoinput.json"):
        raise ProjectError("project path must end with .duoinput.json")
    return project_path


def _migrate_1_0_to_1_1(document: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(document)
    migrated["schema_version"] = "1.1"
    return migrated


def _migrate_1_1_to_1_2(document: dict[str, Any]) -> dict[str, Any]:
    # 1.1 had no such setting, and a project written then meant the devices
    # switched apart - which is what its absence says here.
    migrated = dict(document)
    migrated["synchronised_control"] = False
    migrated["schema_version"] = PROJECT_SCHEMA_VERSION
    return migrated


def _project_to_json(project: DeviceProject) -> dict[str, Any]:
    return {
        "active_profile_id": project.active_profile_id,
        "profiles": [_profile_to_json(profile) for profile in project.profiles],
        "schema_version": PROJECT_SCHEMA_VERSION,
        "synchronised_control": project.synchronised_control,
    }


def _profile_to_json(profile: Profile) -> dict[str, Any]:
    return {
        "bindings": [_binding_to_json(binding) for binding in profile.bindings],
        "color_rgb": list(profile.color_rgb),
        "id": profile.id,
        "keyboard_route": _enum_name(profile.keyboard_route),
        "macros": [_macro_to_json(macro) for macro in profile.macros],
        "mouse_route": _enum_name(profile.mouse_route),
        "name": profile.name,
        "text_layout": _enum_name(profile.text_layout),
    }


def _binding_to_json(binding: Binding) -> dict[str, Any]:
    return {
        "action": {"argument": binding.action.argument, "kind": _enum_name(binding.action.kind)},
        "mode": _enum_name(binding.mode),
        "trigger": {
            "code": binding.trigger.code,
            "kind": _enum_name(binding.trigger.kind),
            "modifiers": binding.trigger.modifiers,
            "source": None if binding.trigger.source is None else {
                "vendor_id": binding.trigger.source.vendor_id,
                "product_id": binding.trigger.source.product_id,
                "interface_number": binding.trigger.source.interface_number,
            },
        },
        "uuid": str(binding.uuid),
    }


def _macro_to_json(macro: Macro) -> dict[str, Any]:
    return {
        "id": macro.id,
        "name": macro.name,
        "steps": [_step_to_json(step) for step in macro.steps],
        "target": _enum_name(macro.target),
        "uuid": str(macro.uuid),
    }


def _step_to_json(step: MacroStep) -> dict[str, Any]:
    result = {
        "payload": base64.b64encode(step.payload).decode("ascii"),
        "type": _enum_name(step.type),
    }
    if step.source_text is not None:
        result["source_text"] = step.source_text
    return result


def _enum_name(value: object) -> str:
    if not hasattr(value, "name"):
        raise ProjectError("cannot serialize unsupported enum")
    return value.name


def _project_from_json(document: dict[str, Any]) -> DeviceProject:
    try:
        return DeviceProject(
            schema_version=_required(document, "schema_version", str),
            active_profile_id=_required(document, "active_profile_id", int),
            profiles=tuple(_profile_from_json(value) for value in _required(document, "profiles", list)),
            synchronised_control=bool(document.get("synchronised_control", False)),
        )
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise ProjectError(f"malformed project: {exc}") from exc


def _profile_from_json(value: object) -> Profile:
    data = _object(value, "profile")
    color = _required(data, "color_rgb", list)
    return Profile(
        id=_required(data, "id", int),
        name=_required(data, "name", str),
        color_rgb=tuple(color),
        keyboard_route=_enum_from_name(KeyboardRoute, _required(data, "keyboard_route", str)),
        mouse_route=_enum_from_name(MouseRoute, _required(data, "mouse_route", str)),
        text_layout=_enum_from_name(TextLayout, _required(data, "text_layout", str)),
        bindings=tuple(_binding_from_json(item) for item in _required(data, "bindings", list)),
        macros=tuple(_macro_from_json(item) for item in _required(data, "macros", list)),
    )


def _binding_from_json(value: object) -> Binding:
    data = _object(value, "binding")
    trigger = _object(_required(data, "trigger", dict), "trigger")
    action = _object(_required(data, "action", dict), "action")
    return Binding(
        trigger=Trigger(
            _enum_from_name(TriggerKind, _required(trigger, "kind", str)),
            _required(trigger, "code", int),
            _required(trigger, "modifiers", int),
            _source_from_json(trigger.get("source")),
        ),
        mode=_enum_from_name(BindingMode, _required(data, "mode", str)),
        action=Action(
            _enum_from_name(ActionKind, _required(action, "kind", str)),
            _required(action, "argument", int),
        ),
        uuid=UUID(_required(data, "uuid", str)),
    )


def _source_from_json(value: object) -> TriggerSource | None:
    if value is None:
        return None
    data = _object(value, "trigger source")
    source = TriggerSource(
        _required(data, "vendor_id", int),
        _required(data, "product_id", int),
        _required(data, "interface_number", int),
    )
    return None if source == TriggerSource(0, 0, 0) else source


def _macro_from_json(value: object) -> Macro:
    data = _object(value, "macro")
    return Macro(
        id=_required(data, "id", int),
        name=_required(data, "name", str),
        target=_enum_from_name(TargetMode, _required(data, "target", str)),
        steps=tuple(_step_from_json(item) for item in _required(data, "steps", list)),
        uuid=UUID(_required(data, "uuid", str)),
    )


def _step_from_json(value: object) -> MacroStep:
    data = _object(value, "step")
    encoded_payload = _required(data, "payload", str)
    try:
        payload = base64.b64decode(encoded_payload, validate=True)
    except ValueError as exc:
        raise ProjectError("step payload is not base64") from exc
    source_text = data.get("source_text")
    if source_text is not None and not isinstance(source_text, str):
        raise ProjectError("step source_text must be a string")
    return MacroStep(
        type=_enum_from_name(MacroStepType, _required(data, "type", str)),
        payload=payload,
        source_text=source_text,
    )


def _required(data: dict[str, Any], key: str, value_type: type) -> Any:
    value = data[key]
    if not isinstance(value, value_type) or isinstance(value, bool) and value_type is int:
        raise TypeError(f"{key} must be a {value_type.__name__}")
    return value


def _object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object")
    return value


def _enum_from_name(enum_type: type, name: str):
    try:
        return enum_type[name]
    except KeyError as exc:
        raise ProjectError(f"unsupported {enum_type.__name__} value {name!r}") from exc
