"""Compile human-typed Unicode macro text into HID key chords.

Compilation is pure: a character maps to a chord only through the fixed physical-key
tables in :mod:`duo_input.domain.layouts`, so the same text and layout always produce the
same bytes. The editable project keeps the Unicode source text; the compiled binary
configuration carries nothing but (modifier, usage) pairs.
"""

from __future__ import annotations

from dataclasses import replace

from duo_input.domain.config_binary import compile_device_config
from duo_input.domain.layouts import HidChord, LayoutId, layout_table
from duo_input.domain.models import DeviceConfig, DeviceProject, MacroStep, Profile
from duo_input.generated.protocol import TEXT_CHARACTERS_PER_STEP, MacroStepType, TextLayout


class UnsupportedCharacter(ValueError):
    """A character cannot be typed on the requested layout."""

    def __init__(self, index: int, char: str, layout: LayoutId):
        self.index = index
        self.char = char
        self.layout = layout
        super().__init__(
            f"character {char!r} at index {index} cannot be typed on the {layout.name} layout"
        )


class TextTooLong(ValueError):
    """The text of one macro step exceeds the protocol limit."""

    def __init__(self, length: int):
        self.length = length
        self.limit = TEXT_CHARACTERS_PER_STEP
        super().__init__(
            f"text step contains {length} characters, the limit is {TEXT_CHARACTERS_PER_STEP}"
        )


def compile_text(text: str, layout: LayoutId) -> tuple[HidChord, ...]:
    """Return the chords that type ``text`` on ``layout``."""

    if len(text) > TEXT_CHARACTERS_PER_STEP:
        raise TextTooLong(len(text))
    layout = TextLayout(layout)
    table = layout_table(layout)
    chords = []
    for index, char in enumerate(text):
        chord = table.get(char)
        if chord is None:
            raise UnsupportedCharacter(index, char, layout)
        chords.append(chord)
    return tuple(chords)


def compile_text_payload(text: str, layout: LayoutId) -> bytes:
    """Return the TEXT step payload for ``text``: one (modifier, usage) pair per chord."""

    return bytes(
        byte for chord in compile_text(text, layout) for byte in (chord.modifiers, chord.usage)
    )


def compile_project_to_binary(project: DeviceProject) -> bytes:
    """Compile an editable project into the binary configuration package."""

    return compile_device_config(_resolved_config(project))


def _resolved_config(project: DeviceProject) -> DeviceConfig:
    return DeviceConfig(
        active_profile_id=project.active_profile_id,
        profiles=tuple(_resolved_profile(profile) for profile in project.profiles),
        synchronised_control=project.synchronised_control,
    )


def _resolved_profile(profile: Profile) -> Profile:
    macros = tuple(
        replace(
            macro,
            steps=tuple(_resolved_step(step, profile.text_layout) for step in macro.steps),
        )
        for macro in profile.macros
    )
    return replace(profile, macros=macros)


def _resolved_step(step: MacroStep, layout: LayoutId) -> MacroStep:
    if step.type != MacroStepType.TEXT or step.source_text is None:
        return step
    return MacroStep(step.type, compile_text_payload(step.source_text, layout))
