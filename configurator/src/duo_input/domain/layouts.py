"""Fixed physical-key tables that turn Unicode text into HID key chords.

The tables below are the only source of truth for text compilation. They describe the
physical key a character sits on for each supported layout, so compilation is pure and
produces the same bytes on every machine: the host operating system, its installed
keyboard layouts, and the layout that happens to be active are never consulted.

Sources: US ANSI (HID usage table 0x04..0x38), the Windows "Russian" layout, and the
Windows "Ukrainian (Enhanced)" layout.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from duo_input.generated.protocol import TextLayout


LayoutId = TextLayout

_SHIFT = 0x02  # left Shift bit of the HID keyboard modifier byte


@dataclass(frozen=True)
class HidChord:
    """One keystroke: a HID modifier byte plus the keyboard usage to tap."""

    modifiers: int
    usage: int


# Each row is (HID usage, unshifted character, shifted character); None means the level
# produces nothing this compiler can type.
_Row = tuple[int, str | None, str | None]

# Keys every layout shares, in HID usage order.
_COMMON_KEYS: tuple[_Row, ...] = (
    (0x28, "\n", None),  # Enter
    (0x2B, "\t", None),  # Tab
    (0x2C, " ", None),  # Space
)

# Physical key positions, named after the US ANSI legends they carry.
_DIGIT_KEYS = (0x1E, 0x1F, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27)  # 1234567890
_TOP_KEYS = (0x14, 0x1A, 0x08, 0x15, 0x17, 0x1C, 0x18, 0x0C, 0x12, 0x13, 0x2F, 0x30)  # qwertyuiop[]
_HOME_KEYS = (0x04, 0x16, 0x07, 0x09, 0x0A, 0x0B, 0x0D, 0x0E, 0x0F, 0x33, 0x34)  # asdfghjkl;'
_BOTTOM_KEYS = (0x1D, 0x1B, 0x06, 0x19, 0x05, 0x11, 0x10, 0x36, 0x37)  # zxcvbnm,.


def _rows(usages: tuple[int, ...], unshifted: str, shifted: str) -> tuple[_Row, ...]:
    if not len(usages) == len(unshifted) == len(shifted):
        raise ValueError("physical key table rows must be the same length")
    return tuple(zip(usages, unshifted, shifted))


def _letters(usages: tuple[int, ...], lowercase: str) -> tuple[_Row, ...]:
    return _rows(usages, lowercase, lowercase.upper())


_US_KEYS: tuple[_Row, ...] = (
    _rows(_DIGIT_KEYS, "1234567890", "!@#$%^&*()")
    + _letters(_TOP_KEYS[:10], "qwertyuiop")
    + _letters(_HOME_KEYS[:9], "asdfghjkl")
    + _letters(_BOTTOM_KEYS[:7], "zxcvbnm")
    + (
        (0x35, "`", "~"),
        (0x2D, "-", "_"),
        (0x2E, "=", "+"),
        (0x2F, "[", "{"),
        (0x30, "]", "}"),
        (0x31, "\\", "|"),
        (0x33, ";", ":"),
        (0x34, "'", '"'),
        (0x36, ",", "<"),
        (0x37, ".", ">"),
        (0x38, "/", "?"),
    )
)

_RU_KEYS: tuple[_Row, ...] = (
    _rows(_DIGIT_KEYS, "1234567890", '!"№;%:?*()')
    + _letters(_TOP_KEYS, "йцукенгшщзхъ")
    + _letters(_HOME_KEYS, "фывапролджэ")
    + _letters(_BOTTOM_KEYS, "ячсмитьбю")
    + (
        (0x35, "ё", "Ё"),
        (0x2D, "-", "_"),
        (0x2E, "=", "+"),
        (0x31, "\\", "/"),
        (0x38, ".", ","),
    )
)

_UA_KEYS: tuple[_Row, ...] = (
    _rows(_DIGIT_KEYS, "1234567890", '!"№;%:?*()')
    + _letters(_TOP_KEYS, "йцукенгшщзхї")
    + _letters(_HOME_KEYS, "фівапролджє")
    + _letters(_BOTTOM_KEYS, "ячсмитьбю")
    + (
        (0x35, "'", None),
        (0x2D, "-", "_"),
        (0x2E, "=", "+"),
        (0x31, "ґ", "Ґ"),
        (0x38, ".", ","),
    )
)


def _table(keys: tuple[_Row, ...]) -> Mapping[str, HidChord]:
    mapping: dict[str, HidChord] = {}
    for usage, unshifted, shifted in _COMMON_KEYS + keys:
        for character, modifiers in ((unshifted, 0x00), (shifted, _SHIFT)):
            if character is None:
                continue
            if character in mapping:  # guards typos when a table is edited
                raise ValueError(f"character {character!r} appears on two physical keys")
            mapping[character] = HidChord(modifiers, usage)
    return mapping


_LAYOUTS: Mapping[TextLayout, Mapping[str, HidChord]] = {
    TextLayout.US: _table(_US_KEYS),
    TextLayout.RU: _table(_RU_KEYS),
    TextLayout.UA: _table(_UA_KEYS),
}


def layout_table(layout: LayoutId) -> Mapping[str, HidChord]:
    """Return the character-to-chord table of one layout."""

    return _LAYOUTS[TextLayout(layout)]
