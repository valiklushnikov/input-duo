"""Единственное место, где безопасность целиком наша.

Проводник пишет файлы, но cFileName строим мы. Всё, что здесь пропущено,
Проводник запишет послушно и туда, куда сказано.
"""

from __future__ import annotations

import pytest

from duo_input.transfer.paths import MAX_PATH_UTF16, UnsafePath, sanitize_relative_path


def test_a_plain_relative_path_passes_through_unchanged():
    assert sanitize_relative_path("Photos/img1.jpg") == "Photos/img1.jpg"


def test_backslashes_are_normalised_to_the_wire_separator():
    assert sanitize_relative_path("Photos\\img1.jpg") == "Photos/img1.jpg"


@pytest.mark.parametrize(
    "raw",
    [
        "../evil.exe",
        "..\\evil.exe",
        "Photos/../../evil.exe",
        "Photos/..",
        "..",
    ],
)
def test_any_parent_segment_is_refused(raw):
    with pytest.raises(UnsafePath):
        sanitize_relative_path(raw)


@pytest.mark.parametrize("raw", ["./a.txt", "Photos/./a.txt", "."])
def test_any_current_directory_segment_is_refused(raw):
    with pytest.raises(UnsafePath):
        sanitize_relative_path(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "C:\\evil.exe",
        "C:evil.exe",
        "\\evil.exe",
        "/foo",
        "\\\\server\\share\\evil.exe",
        "//server/share/evil.exe",
    ],
)
def test_anything_absolute_is_refused(raw):
    with pytest.raises(UnsafePath):
        sanitize_relative_path(raw)


def test_an_empty_segment_is_refused_because_it_hides_a_separator_trick():
    with pytest.raises(UnsafePath):
        sanitize_relative_path("Photos//img1.jpg")


def test_an_empty_path_is_refused():
    with pytest.raises(UnsafePath):
        sanitize_relative_path("")


@pytest.mark.parametrize(
    "raw",
    ["CON", "con", "PRN.txt", "aux", "NUL", "COM1", "com9.bin", "LPT1", "lpt9.dat"],
)
def test_reserved_windows_device_names_are_refused_with_or_without_an_extension(raw):
    with pytest.raises(UnsafePath):
        sanitize_relative_path(raw)


def test_a_reserved_name_is_refused_anywhere_in_the_path_not_only_at_the_end():
    with pytest.raises(UnsafePath):
        sanitize_relative_path("Photos/NUL/img.jpg")


def test_com0_is_not_reserved_and_passes():
    assert sanitize_relative_path("COM0.txt") == "COM0.txt"


@pytest.mark.parametrize("raw", ["name.", "name ", "Photos/name./a.txt", "Photos /a.txt"])
def test_a_trailing_dot_or_space_in_a_segment_is_refused(raw):
    with pytest.raises(UnsafePath):
        sanitize_relative_path(raw)


@pytest.mark.parametrize("bad", ["<", ">", ":", '"', "|", "?", "*"])
def test_characters_windows_forbids_in_a_name_are_refused(bad):
    with pytest.raises(UnsafePath):
        sanitize_relative_path(f"na{bad}me.txt")


def test_control_characters_are_refused():
    with pytest.raises(UnsafePath):
        sanitize_relative_path("na\x01me.txt")


def test_a_null_byte_is_refused():
    with pytest.raises(UnsafePath):
        sanitize_relative_path("na\x00me.txt")


def test_a_segment_at_the_cFileName_ceiling_passes_and_one_beyond_it_does_not():
    ok = "a" * MAX_PATH_UTF16

    assert sanitize_relative_path(ok) == ok

    with pytest.raises(UnsafePath):
        sanitize_relative_path("a" * (MAX_PATH_UTF16 + 1))


def test_the_ceiling_counts_utf16_units_not_python_characters():
    # Каждый символ вне BMP занимает ДВЕ единицы UTF-16, а cFileName - это
    # WCHAR[260]. Путь из 200 таких символов - это 400 единиц, то есть он не
    # помещается, хотя len() по-питоновски равен 200.
    astral = "\U0001F600" * 200

    with pytest.raises(UnsafePath):
        sanitize_relative_path(astral)


def test_excessive_nesting_is_refused():
    with pytest.raises(UnsafePath):
        sanitize_relative_path("/".join(["d"] * 64))


def test_unicode_is_normalised_to_nfc_so_two_spellings_become_one_name():
    decomposed = "Sa\u0301nchez.txt"  # S a + combining acute
    composed = "S\u00e1nchez.txt"

    assert sanitize_relative_path(decomposed) == composed


def test_normalisation_happens_before_the_other_checks_not_after():
    # Полноширинная точка нормализуется в обычную, и только ПОСЛЕ этого
    # сегмент оказывается заканчивающимся точкой. Проверка до нормализации
    # пропустила бы это имя.
    with pytest.raises(UnsafePath):
        sanitize_relative_path("name\uff0e")


def test_a_cyrillic_name_is_allowed_because_only_windows_rules_apply():
    assert sanitize_relative_path("Отчёт/данные.txt") == "Отчёт/данные.txt"
