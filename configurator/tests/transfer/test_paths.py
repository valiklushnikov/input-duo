"""Единственное место, где безопасность целиком наша.

Проводник пишет файлы, но cFileName строим мы. Всё, что здесь пропущено,
Проводник запишет послушно и туда, куда сказано.
"""

from __future__ import annotations

import unicodedata

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


@pytest.mark.parametrize("raw", [None, 123, 3.5, b"Photos/img1.jpg", ["Photos/img1.jpg"]])
def test_a_non_string_input_is_refused(raw):
    with pytest.raises(UnsafePath):
        sanitize_relative_path(raw)


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


@pytest.mark.parametrize("raw", ["CONIN$", "conin$", "CONOUT$", "CONOUT$.txt", "CONIN$.txt"])
def test_console_io_device_names_are_refused(raw):
    # CONIN$/CONOUT$ открываются как консольные буферы ввода/вывода, а не
    # как файлы: измерено через open() на этой машине - запись в "CONOUT$"
    # завершается без ошибки, но не создаёт файла на диске (см. §11
    # спецификации и task-1.2-report.md).
    with pytest.raises(UnsafePath):
        sanitize_relative_path(raw)


@pytest.mark.parametrize(
    "raw",
    ["COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"],
)
def test_superscript_com_and_lpt_device_names_are_refused(raw):
    # Надстрочные цифры (U+00B9/U+00B2/U+00B3) не буквы, поэтому .upper() их
    # не трогает - но парсер DOS-имён устройств в Windows приравнивает
    # "COM¹" к "COM1". Измерено на этой машине: open("COM¹", "wb") падает с
    # тем же FileNotFoundError, что и open("COM1", "wb") - устройство
    # разбирается одинаково, разница только в том, что порта физически нет
    # (см. task-1.2-report.md).
    with pytest.raises(UnsafePath):
        sanitize_relative_path(raw)


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


def test_a_lone_surrogate_is_refused_not_raised_as_a_codec_error():
    # json.loads('{"path": "a\\ud800b"}') hands back a real lone surrogate:
    # the escape is plain ASCII in the byte stream, so strict UTF-8 decoding
    # never sees it (see clipboard/wire.py). encode("utf-16-le") then raises
    # UnicodeEncodeError, not UnsafePath - a caller written as
    # "except UnsafePath: reject()" would not fail closed on it. The
    # contract is "canonical form or UnsafePath", so this must be UnsafePath
    # too, never an uncaught codec error.
    with pytest.raises(UnsafePath):
        sanitize_relative_path("Photos/a\ud800b.jpg")


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
    # NFC — каноническая форма, а не совместимая: полноширинная точка
    # U+FF0E НЕ становится обычной точкой под NFC (это работа NFKC, на
    # который эта функция сознательно не переходит — см. paths.py). Так что
    # нужен другой пример порядка "нормализация раньше остальных проверок".
    #
    # U+0344 (COMBINING GREEK DIALYTIKA TONOS) исключён из повторной
    # композиции: его каноническое разложение — <0308, 0301> (две отдельные
    # комбинирующие метки), и Unicode запрещает складывать их обратно в
    # 0344. Поэтому NFC не укорачивает этот символ, как обычно бывает при
    # композиции, а УДЛИНЯЕТ его: один символ (1 единица UTF-16)
    # становится двумя (2 единицы).
    trigger = "̈́"
    assert unicodedata.normalize("NFC", trigger) == "̈́"

    # Имя ровно на потолке ДО нормализации — тест на потолок выше уже
    # показал, что такая длина сама по себе разрешена. Но после NFC оно
    # длиннее потолка на одну единицу. Значит только порядок "сначала
    # нормализация" ловит превышение; проверка длины на сыром raw пропустила
    # бы это имя.
    raw = "_" * (MAX_PATH_UTF16 - 1) + trigger
    assert len(raw.encode("utf-16-le")) // 2 == MAX_PATH_UTF16

    with pytest.raises(UnsafePath):
        sanitize_relative_path(raw)


def test_a_cyrillic_name_is_allowed_because_only_windows_rules_apply():
    assert sanitize_relative_path("Отчёт/данные.txt") == "Отчёт/данные.txt"


@pytest.mark.parametrize(
    "bad",
    [
        "‪",  # LRE - LEFT-TO-RIGHT EMBEDDING
        "‫",  # RLE - RIGHT-TO-LEFT EMBEDDING
        "‬",  # PDF - POP DIRECTIONAL FORMATTING
        "‭",  # LRO - LEFT-TO-RIGHT OVERRIDE
        "‮",  # RLO - RIGHT-TO-LEFT OVERRIDE
        "⁦",  # LRI - LEFT-TO-RIGHT ISOLATE
        "⁧",  # RLI - RIGHT-TO-LEFT ISOLATE
        "⁨",  # FSI - FIRST STRONG ISOLATE
        "⁩",  # PDI - POP DIRECTIONAL ISOLATE
    ],
)
def test_bidi_overrides_and_isolates_are_refused(bad):
    # The classic "invoice" + RLO + "gpj.exe" trick: Explorer renders the
    # name reversed ("invoiceexe.jpg") while the bytes on disk stay exactly
    # what the sender sent. Measured with a real open() on this machine: the
    # file is created without error, so nothing else in this module catches
    # it (see task-1.2-report.md).
    with pytest.raises(UnsafePath):
        sanitize_relative_path(f"invoice{bad}gpj.exe")


@pytest.mark.parametrize(
    "bad",
    [
        "­",  # SOFT HYPHEN
        "​",  # ZERO WIDTH SPACE
        "‌",  # ZERO WIDTH NON-JOINER
        "‍",  # ZERO WIDTH JOINER
        "⁠",  # WORD JOINER
        "﻿",  # ZERO WIDTH NO-BREAK SPACE / BOM
    ],
)
def test_invisible_characters_are_refused(bad):
    # Measured with a real open() on this machine: "photo<invisible>.jpg" is
    # created as an ordinary file - nothing renders differently, but it is a
    # distinct directory entry from "photo.jpg" (see task-1.2-report.md).
    with pytest.raises(UnsafePath):
        sanitize_relative_path(f"photo{bad}.jpg")


@pytest.mark.parametrize(
    "bad",
    [
        "﷐",  # first Arabic Presentation Forms-A noncharacter
        "﷯",  # last Arabic Presentation Forms-A noncharacter
        "￾",  # BMP noncharacter
        "￿",  # BMP noncharacter
        "\U0001ffff",  # supplementary-plane noncharacter
        "\U0010ffff",  # last noncharacter of the last plane
    ],
)
def test_unicode_noncharacters_are_refused(bad):
    with pytest.raises(UnsafePath):
        sanitize_relative_path(f"name{bad}.txt")


def test_an_ordinary_arabic_or_hebrew_name_is_allowed():
    # The guard against over-blocking matters as much as the ones that
    # reject: this feature carries filenames from real people, and ordinary
    # right-to-left letters are not bidi control characters. Measured with a
    # real open()/remove() round-trip on this machine: both names are
    # created and removed as ordinary files, same as any ASCII name.
    arabic = "مرحبا.txt"
    hebrew = "שלום.txt"

    assert sanitize_relative_path(arabic) == arabic
    assert sanitize_relative_path(hebrew) == hebrew
