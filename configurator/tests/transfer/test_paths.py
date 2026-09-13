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


@pytest.mark.parametrize(
    "raw",
    [
        "NUL .txt",
        "CON .txt",
        "con .txt",
        "CONOUT$ .txt",
        "COM1 .txt",
        "COM¹ .txt",
        "LPT³ .txt",
        "PRN .jpg",
        "AUX .bin",
        "NUL   .a",
        "NUL  ..txt",
        "NUL . txt",
        "x/NUL .txt",
    ],
)
def test_a_reserved_device_name_with_a_trailing_space_before_the_extension_is_refused(raw):
    # segment.split(".", 1)[0] оставляет пробел перед точкой ("NUL " не
    # равно "NUL"), а проверка "точка/пробел в конце" смотрит только на
    # segment[-1], то есть на последний символ РАСШИРЕНИЯ - здесь это "t", а
    # не пробел. Без .rstrip(" ") в сравнении со списком устройств оба
    # правила проходят мимо. Измерено GetFullPathNameW: "NUL .txt" и все
    # варианты ниже разбираются как "\\.\NUL" (или соответствующее
    # устройство) - Windows сам отбрасывает пробел перед расширением при
    # разборе DOS-имени (см. §11 спецификации и task-1.2-report.md).
    with pytest.raises(UnsafePath):
        sanitize_relative_path(raw)


def test_a_trailing_space_before_the_extension_is_an_ordinary_filename_form():
    # Контроль к предыдущему тесту: пробел перед расширением - легитимная
    # форма имени файла сама по себе, и её нельзя случайно задеть, ужесточая
    # проверку зарезервированных имён. Измерено GetFullPathNameW и реальным
    # open(): "ordinary .txt" - обычный путь, не устройство.
    raw = "ordinary .txt"
    assert sanitize_relative_path(raw) == raw


def test_a_non_breaking_space_before_the_extension_is_not_stripped_and_passes():
    # .rstrip(" ") должен снимать только ASCII-пробел (U+0020), а не NBSP
    # (U+00A0): NBSP - обычная буква с точки зрения разбора DOS-имени, а не
    # часть синтаксиса устройства. Измерено GetFullPathNameW и реальным
    # open(): "NUL\u00a0.txt" - обычный путь, а не "\\.\NUL". Если бы
    # .rstrip() снимал NBSP тоже, это имя стало бы ложно отклоняться.
    raw = "NUL\u00a0.txt"
    assert sanitize_relative_path(raw) == raw


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
    # json.loads('{"path": "a\\ud800b"}') отдаёт настоящий одинокий суррогат:
    # экранирование - обычный ASCII в байтовом потоке, и строгое
    # UTF-8-декодирование его не видит (см. clipboard/wire.py). Дальше
    # encode("utf-16-le") бросает UnicodeEncodeError, а не UnsafePath - и
    # вызывающий код вида "except UnsafePath: reject()" не отказал бы
    # закрыто. Контракт функции - "каноническая форма или UnsafePath", а не
    # необработанное исключение кодека.
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
    # Классический трюк "invoice" + RLO + "gpj.exe": Проводник рисует имя
    # перевёрнутым ("invoiceexe.jpg"), а байты на диске остаются ровно
    # такими, какими их прислал отправитель. Измерено реальным open() на
    # этой машине: файл создаётся без ошибки, то есть ничего другого в этом
    # модуле его не ловит (см. task-1.2-report.md).
    with pytest.raises(UnsafePath):
        sanitize_relative_path(f"invoice{bad}gpj.exe")


@pytest.mark.parametrize(
    "bad",
    [
        "­",  # SOFT HYPHEN
        "​",  # ZERO WIDTH SPACE
        "⁠",  # WORD JOINER
        "﻿",  # ZERO WIDTH NO-BREAK SPACE / BOM
    ],
)
def test_invisible_characters_are_refused(bad):
    # Измерено реальным open() на этой машине: "photo<invisible>.jpg"
    # создаётся как обычный файл - визуально ничего не меняется, но на
    # диске это отдельная от "photo.jpg" запись.
    with pytest.raises(UnsafePath):
        sanitize_relative_path(f"photo{bad}.jpg")


@pytest.mark.parametrize(
    "bad",
    [
        "﷐",  # первый нехарактер Arabic Presentation Forms-A
        "﷯",  # последний нехарактер Arabic Presentation Forms-A
        "￾",  # нехарактер BMP
        "￿",  # нехарактер BMP
        "\U0001ffff",  # нехарактер дополнительной плоскости
        "\U0010ffff",  # последний нехарактер последней плоскости
    ],
)
def test_unicode_noncharacters_are_refused(bad):
    with pytest.raises(UnsafePath):
        sanitize_relative_path(f"name{bad}.txt")


def test_an_ordinary_arabic_or_hebrew_name_is_allowed():
    # Защита от чрезмерной блокировки важна не меньше отказов: этой функцией
    # пользуются настоящие люди с настоящими именами файлов, а обычные
    # право-налево-буквы - не управляющие знаки направления. Измерено
    # циклом open()/remove() на этой машине: оба имени создаются и
    # удаляются как обычный файл, как и любое ASCII-имя.
    arabic = "مرحبا.txt"
    hebrew = "שלום.txt"

    assert sanitize_relative_path(arabic) == arabic
    assert sanitize_relative_path(hebrew) == hebrew


def test_zero_width_joiner_and_non_joiner_are_allowed_because_they_change_shaping():
    # U+200C (ZWNJ) и U+200D (ZWJ) сознательно исключены из
    # _INVISIBLE_CHARACTERS (см. paths.py): в отличие от ZWSP/WJ/BOM/SHY они
    # меняют форму соседних символов, а не только зазор между ними, и это и
    # есть причина, по которой их печатают. Измерено реальным open() на этой
    # машине: оба имени создаются как обычный файл.
    #
    # ZWJ склеивает составные эмодзи - без него "👨‍👩‍👧.jpg" распался бы на
    # три отдельных эмодзи вместо одного семейного.
    family_emoji = "👨‍👩‍👧.jpg"
    assert sanitize_relative_path(family_emoji) == family_emoji

    # ZWNJ обязателен в персидской орфографии, а не декоративен: "می‌روم"
    # ("я иду") без ZWNJ читается и пишется иначе.
    persian = "می‌روم.txt"
    assert sanitize_relative_path(persian) == persian
