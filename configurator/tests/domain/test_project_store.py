from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from duo_input.domain.models import (
    Action,
    ActionKind,
    Binding,
    BindingMode,
    DeviceProject,
    KeyboardRoute,
    Macro,
    MacroStep,
    MouseRoute,
    Profile,
    TargetMode,
    TextLayout,
    Trigger,
    TriggerKind,
    TriggerSource,
)
from duo_input.domain.project_store import (
    PROJECT_SCHEMA_VERSION,
    ProjectError,
    ProjectValidationError,
    ProjectVersionError,
    load_project,
    save_project_atomic,
)
from duo_input.domain.validation import validate_project
from duo_input.generated.protocol import MacroStepType


VECTOR = Path(__file__).parents[1] / "vectors/project_v1_minimal.duoinput.json"


def _profile(profile_id: int, **changes: object) -> Profile:
    values: dict[str, object] = {
        "id": profile_id,
        "name": f"Profile {profile_id}",
        "color_rgb": (profile_id, profile_id + 1, profile_id + 2),
        "keyboard_route": KeyboardRoute.PC1,
        "mouse_route": MouseRoute.PC1,
        "text_layout": TextLayout.US,
        "bindings": (),
        "macros": (),
    }
    values.update(changes)
    return Profile(**values)


@pytest.fixture
def project() -> DeviceProject:
    macro = Macro(
        id=1,
        name="КУРКУМА",
        target=TargetMode.INHERIT,
        steps=(
            MacroStep(MacroStepType.TEXT, b"", source_text="КУРКУМА"),
            MacroStep(MacroStepType.DELAY, b"\x0a\x00\x14\x00"),
        ),
        uuid=UUID("11111111-1111-4111-8111-111111111111"),
    )
    binding = Binding(
        trigger=Trigger(TriggerKind.KEYBOARD_USAGE, 4),
        mode=BindingMode.REPLACE,
        action=Action(ActionKind.RUN_MACRO, 1),
        uuid=UUID("22222222-2222-4222-8222-222222222222"),
    )
    first = _profile(1, name="Профіль 1", bindings=(binding,), macros=(macro,))
    return DeviceProject(
        schema_version=PROJECT_SCHEMA_VERSION,
        active_profile_id=1,
        profiles=(first,) + tuple(_profile(profile_id) for profile_id in range(2, 9)),
    )


def _write_schema(tmp_path: Path, version: str) -> Path:
    path = tmp_path / "version.duoinput.json"
    path.write_text(json.dumps({"schema_version": version}), encoding="utf-8")
    return path


def test_atomic_round_trip_preserves_unicode(tmp_path: Path, project: DeviceProject):
    path = tmp_path / "test.duoinput.json"

    save_project_atomic(project, path)

    restored = load_project(path)

    assert restored == project
    assert restored.profiles[0].bindings[0].uuid == project.profiles[0].bindings[0].uuid
    assert restored.profiles[0].macros[0].uuid == project.profiles[0].macros[0].uuid
    assert "КУРКУМА" in path.read_text("utf-8")
    assert not path.with_suffix(path.suffix + ".tmp").exists()


def test_committed_minimal_vector_loads_as_a_valid_project(project: DeviceProject):
    assert load_project(VECTOR) == project
    assert validate_project(load_project(VECTOR)) == ()


def test_unknown_major_is_read_only(tmp_path: Path):
    with pytest.raises(ProjectVersionError, match="major"):
        load_project(_write_schema(tmp_path, "99.0"))


def test_supported_older_minor_migrates_in_memory(tmp_path: Path, project: DeviceProject):
    path = tmp_path / "old.duoinput.json"
    save_project_atomic(project, path)
    document = json.loads(path.read_text("utf-8"))
    document["schema_version"] = "1.0"
    path.write_text(json.dumps(document), encoding="utf-8")

    migrated = load_project(path)

    assert migrated == project
    assert migrated.schema_version == PROJECT_SCHEMA_VERSION
    assert json.loads(path.read_text("utf-8"))["schema_version"] == "1.0"


def test_validation_reports_exact_nested_paths(project: DeviceProject):
    first = project.profiles[0]
    duplicate = replace(first.bindings[0], uuid=UUID("33333333-3333-4333-8333-333333333333"))
    too_many_bindings = tuple(
        replace(
            first.bindings[0],
            uuid=UUID(f"00000000-0000-4000-8000-{index:012d}"),
            trigger=Trigger(TriggerKind.KEYBOARD_USAGE, index + 1),
        )
        for index in range(129)
    )
    too_many_macros = tuple(
        Macro(
            id=index + 1,
            name=f"Macro {index + 1}",
            target=TargetMode.PC1,
            steps=(),
            uuid=UUID(f"00000000-0000-4000-8001-{index:012d}"),
        )
        for index in range(33)
    )
    too_many_steps = Macro(
        id=1,
        name="Large",
        target=TargetMode.PC1,
        steps=tuple(MacroStep(MacroStepType.KEY_TAP, b"\x00\x04") for _ in range(65)),
        uuid=UUID("44444444-4444-4444-8444-444444444444"),
    )
    invalid = replace(
        project,
        profiles=(
            replace(
                first,
                bindings=(first.bindings[0], duplicate) + too_many_bindings,
                macros=(too_many_steps,) + too_many_macros,
            ),
        )
        + project.profiles[1:],
    )

    paths = {issue.path for issue in validate_project(invalid)}

    assert "/profiles/0/bindings" in paths
    assert "/profiles/0/bindings/1/trigger" in paths
    assert "/profiles/0/macros" in paths
    assert "/profiles/0/macros/0/steps" in paths


def test_validation_rejects_unsupported_enums_at_their_positions(project: DeviceProject):
    first = project.profiles[0]
    unsupported_binding = replace(first.bindings[0], mode=99)
    unsupported_step = MacroStep(99, b"")
    unsupported_macro = replace(first.macros[0], target=99, steps=(unsupported_step,))
    invalid = replace(
        project,
        profiles=(replace(first, bindings=(unsupported_binding,), macros=(unsupported_macro,)),)
        + project.profiles[1:],
    )

    paths = {issue.path for issue in validate_project(invalid)}

    assert "/profiles/0/bindings/0/mode" in paths
    assert "/profiles/0/macros/0/target" in paths
    assert "/profiles/0/macros/0/steps/0/type" in paths


@pytest.mark.parametrize(
    "other_source",
    (
        None,
        TriggerSource(0x1234, 0xD030, 1),
        TriggerSource(0x3434, 0x5678, 1),
        TriggerSource(0x3434, 0xD030, 2),
    ),
)
def test_two_triggers_that_differ_only_by_their_source_are_not_duplicates(
    project: DeviceProject, other_source: TriggerSource | None,
):
    """Ctrl+Right from the mouse and Ctrl+Right from the keyboard are two
    bindings, and being able to have both is the whole point of qualifying a
    trigger. The binary decoder has always keyed duplicates on the full
    six-tuple; the validator was still keying on three."""
    first = project.profiles[0]
    trigger = Trigger(TriggerKind.KEYBOARD_USAGE, 0x4F, 0x01)
    qualified = replace(
        first.bindings[0],
        uuid=UUID("55555555-5555-4555-8555-555555555555"),
        trigger=replace(trigger, source=TriggerSource(0x3434, 0xD030, 1)),
    )
    unqualified = replace(
        first.bindings[0],
        uuid=UUID("66666666-6666-4666-8666-666666666666"),
        trigger=replace(trigger, source=other_source),
    )
    target = replace(
        project,
        profiles=(replace(first, bindings=(qualified, unqualified)),)
        + project.profiles[1:],
    )

    assert validate_project(target) == ()


def test_two_triggers_from_the_same_device_are_still_duplicates(
    project: DeviceProject,
):
    first = project.profiles[0]
    trigger = Trigger(
        TriggerKind.KEYBOARD_USAGE, 0x4F, 0x01, TriggerSource(0x3434, 0xD030, 1)
    )
    one = replace(
        first.bindings[0],
        uuid=UUID("77777777-7777-4777-8777-777777777777"),
        trigger=trigger,
    )
    two = replace(
        first.bindings[0],
        uuid=UUID("88888888-8888-4888-8888-888888888888"),
        trigger=trigger,
    )
    invalid = replace(
        project,
        profiles=(replace(first, bindings=(one, two)),) + project.profiles[1:],
    )

    paths = {issue.path for issue in validate_project(invalid)}

    assert "/profiles/0/bindings/1/trigger" in paths


def test_an_all_zero_source_is_the_same_key_as_no_source(project: DeviceProject):
    """The wire spells "unknown" as the all-zero triple; the two spellings
    must not become two different bindings on the same key."""
    first = project.profiles[0]
    trigger = Trigger(TriggerKind.KEYBOARD_USAGE, 0x4F, 0x01)
    spelled = replace(
        first.bindings[0],
        uuid=UUID("99999999-9999-4999-8999-999999999999"),
        trigger=replace(trigger, source=TriggerSource(0, 0, 0)),
    )
    unspelled = replace(
        first.bindings[0],
        uuid=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        trigger=trigger,
    )
    invalid = replace(
        project,
        profiles=(replace(first, bindings=(spelled, unspelled)),) + project.profiles[1:],
    )

    paths = {issue.path for issue in validate_project(invalid)}

    assert "/profiles/0/bindings/1/trigger" in paths


def test_validation_accepts_an_action_targeting_a_later_profile(project: DeviceProject):
    first = project.profiles[0]
    binding = replace(first.bindings[0], action=Action(ActionKind.SET_PROFILE, 8))
    target = replace(project, profiles=(replace(first, bindings=(binding,)),) + project.profiles[1:])

    assert validate_project(target) == ()


def test_validation_accepts_all_persisted_limits(project: DeviceProject):
    macros = tuple(
        Macro(
            id=macro_id,
            name=f"Macro {macro_id}",
            target=TargetMode.PC1,
            steps=tuple(MacroStep(MacroStepType.KEY_TAP, b"\x00\x04") for _ in range(64)),
            uuid=UUID(f"10000000-0000-4000-8000-{macro_id:012d}"),
        )
        for macro_id in range(1, 33)
    )
    bindings = tuple(
        Binding(
            trigger=Trigger(TriggerKind.KEYBOARD_USAGE, code),
            mode=BindingMode.REPLACE,
            action=Action(ActionKind.RUN_MACRO, 1),
            uuid=UUID(f"20000000-0000-4000-8000-{code:012d}"),
        )
        for code in range(1, 129)
    )
    first = replace(project.profiles[0], bindings=bindings, macros=macros)
    full_limit_project = replace(project, profiles=(first,) + project.profiles[1:])

    assert validate_project(full_limit_project) == ()


def test_unencodable_source_text_is_a_positional_validation_error(
    tmp_path: Path, project: DeviceProject
):
    first = project.profiles[0]
    macro = replace(first.macros[0], steps=(replace(first.macros[0].steps[0], source_text="\ud800"),))
    invalid = replace(project, profiles=(replace(first, macros=(macro,)),) + project.profiles[1:])

    issues = validate_project(invalid)

    assert [issue.path for issue in issues] == ["/profiles/0/macros/0/steps/0/source_text"]
    with pytest.raises(ProjectValidationError) as error:
        save_project_atomic(invalid, tmp_path / "invalid.duoinput.json")
    assert error.value.issues == issues


def test_project_store_rejects_non_project_extensions(tmp_path: Path, project: DeviceProject):
    wrong_path = tmp_path / "project.json"
    wrong_path.write_text(VECTOR.read_text("utf-8"), encoding="utf-8")

    with pytest.raises(ProjectError, match=".duoinput.json"):
        save_project_atomic(project, wrong_path)
    with pytest.raises(ProjectError, match=".duoinput.json"):
        load_project(wrong_path)
