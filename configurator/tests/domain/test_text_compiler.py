from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from duo_input.domain import layouts as layouts_module
from duo_input.domain import text_compiler as text_compiler_module
from duo_input.domain.config_binary import decode_device_config
from duo_input.domain.layouts import HidChord
from duo_input.domain.models import DeviceProject, Macro, MacroStep, Profile
from duo_input.domain.project_store import PROJECT_SCHEMA_VERSION, save_project_atomic
from duo_input.domain.text_compiler import (
    TextTooLong,
    UnsupportedCharacter,
    compile_project_to_binary,
    compile_text,
    compile_text_payload,
)
from duo_input.generated.protocol import (
    TEXT_CHARACTERS_PER_STEP,
    KeyboardRoute,
    MacroStepType,
    MouseRoute,
    TargetMode,
    TextLayout,
)


VECTORS = json.loads(
    (Path(__file__).parents[1] / "vectors/text_layout_vectors.json").read_text(encoding="utf-8")
)
CASES = VECTORS["cases"]
REJECTIONS = VECTORS["rejections"]


def _profile(profile_id: int, **changes: object) -> Profile:
    values: dict[str, object] = {
        "id": profile_id,
        "name": f"Profile {profile_id}",
        "color_rgb": (profile_id, profile_id, profile_id),
        "keyboard_route": KeyboardRoute.PC1,
        "mouse_route": MouseRoute.PC1,
        "text_layout": TextLayout.US,
        "bindings": (),
        "macros": (),
    }
    values.update(changes)
    return Profile(**values)


def _project(*profiles: Profile) -> DeviceProject:
    filled = {profile.id: profile for profile in profiles}
    return DeviceProject(
        schema_version=PROJECT_SCHEMA_VERSION,
        active_profile_id=1,
        profiles=tuple(filled.get(profile_id, _profile(profile_id)) for profile_id in range(1, 9)),
    )


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_golden_vectors_compile_to_expected_chords(case: dict) -> None:
    expected = tuple(HidChord(modifiers, usage) for modifiers, usage in case["chords"])
    assert compile_text(case["text"], TextLayout[case["layout"]]) == expected


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_golden_vectors_serialize_to_modifier_usage_pairs(case: dict) -> None:
    expected = bytes(byte for chord in case["chords"] for byte in chord)
    payload = compile_text_payload(case["text"], TextLayout[case["layout"]])
    assert payload == expected
    assert len(payload) % 2 == 0
    assert all(payload[index] != 0 for index in range(1, len(payload), 2))


@pytest.mark.parametrize("case", REJECTIONS, ids=[case["name"] for case in REJECTIONS])
def test_untypable_characters_are_reported_with_position(case: dict) -> None:
    layout = TextLayout[case["layout"]]
    with pytest.raises(UnsupportedCharacter) as error:
        compile_text(case["text"], layout)
    assert error.value.index == case["index"]
    assert error.value.char == case["char"]
    assert error.value.layout is layout


def test_compilation_never_consults_the_operating_system() -> None:
    forbidden = ("ctypes", "win32", "windll", "GetKeyboardLayout", "VkKeyScan", "ToUnicode")
    for module in (layouts_module, text_compiler_module):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{module.__name__} must not use {token}"


def test_text_at_the_step_limit_compiles_and_longer_text_is_rejected() -> None:
    assert len(compile_text("a" * TEXT_CHARACTERS_PER_STEP, TextLayout.US)) == TEXT_CHARACTERS_PER_STEP
    with pytest.raises(TextTooLong):
        compile_text("a" * (TEXT_CHARACTERS_PER_STEP + 1), TextLayout.US)


def test_project_keeps_unicode_while_binary_carries_only_chords(tmp_path: Path) -> None:
    russian = Macro(
        id=1,
        name="kurkuma",
        target=TargetMode.INHERIT,
        steps=(
            MacroStep(MacroStepType.TEXT, b"", source_text="КУРКУМА"),
            MacroStep(MacroStepType.TEXT, bytes((0x02, 0x15))),
        ),
        uuid=UUID("11111111-1111-4111-8111-111111111111"),
    )
    latin = Macro(
        id=1,
        name="ab",
        target=TargetMode.INHERIT,
        steps=(MacroStep(MacroStepType.TEXT, b"", source_text="ab"),),
        uuid=UUID("22222222-2222-4222-8222-222222222222"),
    )
    project = _project(
        _profile(1, text_layout=TextLayout.RU, macros=(russian,)),
        _profile(2, text_layout=TextLayout.US, macros=(latin,)),
    )

    path = tmp_path / "project.duoinput.json"
    save_project_atomic(project, path)
    assert "КУРКУМА" in path.read_text(encoding="utf-8")

    binary = compile_project_to_binary(project)
    assert "КУРКУМА".encode("utf-8") not in binary

    decoded = decode_device_config(binary)
    russian_steps = decoded.profiles[0].macros[0].steps
    assert russian_steps[0].payload == bytes((0x02, 0x15, 0x02, 0x08, 0x02, 0x0B, 0x02, 0x15,
                                              0x02, 0x08, 0x02, 0x19, 0x02, 0x09))
    assert russian_steps[0].source_text is None
    assert russian_steps[1].payload == bytes((0x02, 0x15))
    assert decoded.profiles[1].macros[0].steps[0].payload == bytes((0x00, 0x04, 0x00, 0x05))


def test_project_compilation_reports_text_untypable_in_its_profile_layout() -> None:
    macro = Macro(
        id=1,
        name="latin",
        target=TargetMode.INHERIT,
        steps=(MacroStep(MacroStepType.TEXT, b"", source_text="abc"),),
        uuid=UUID("33333333-3333-4333-8333-333333333333"),
    )
    project = _project(_profile(1, text_layout=TextLayout.RU, macros=(macro,)))
    with pytest.raises(UnsupportedCharacter) as error:
        compile_project_to_binary(project)
    assert error.value.index == 0
    assert error.value.char == "a"
    assert error.value.layout is TextLayout.RU
