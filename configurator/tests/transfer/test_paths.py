"""Единственное место, где безопасность целиком наша.

Проводник пишет файлы, но cFileName строим мы. Всё, что здесь пропущено,
Проводник запишет послушно и туда, куда сказано.
"""

from __future__ import annotations

import unicodedata

import pytest

from duo_input.transfer.model import (
    ENTRY_DIRECTORY,
    ENTRY_FILE,
    SkippedEntry,
    TransferEntry,
    TransferManifest,
)
from duo_input.transfer.paths import (
    MAX_ENTRIES,
    MAX_PATH_UTF16,
    MAX_TOTAL_BYTES,
    UnsafePath,
    sanitize_manifest,
    sanitize_relative_path,
)


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


def _with(entries) -> TransferManifest:
    return TransferManifest(transfer_id="t", entries=tuple(entries))


def test_a_clean_manifest_comes_back_with_canonical_paths():
    manifest = _with([
        TransferEntry(path="Photos", kind=ENTRY_DIRECTORY, size=0, mtime_ns=1),
        TransferEntry(path="Photos\\img.jpg", kind=ENTRY_FILE, size=4, mtime_ns=2),
    ])

    result = sanitize_manifest(manifest)

    assert [entry.path for entry in result.entries] == ["Photos", "Photos/img.jpg"]


def test_one_bad_entry_refuses_the_whole_manifest_rather_than_dropping_it():
    manifest = _with([
        TransferEntry(path="good.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="../evil.exe", kind=ENTRY_FILE, size=1, mtime_ns=1),
    ])

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_two_entries_differing_only_in_case_are_refused_as_a_collision():
    # Файловая система Windows регистронезависима: эти две записи попали бы в
    # один файл, и вторая молча затёрла бы первую.
    manifest = _with([
        TransferEntry(path="Photos/IMG.jpg", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="Photos/img.jpg", kind=ENTRY_FILE, size=2, mtime_ns=2),
    ])

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_two_entries_colliding_only_after_normalisation_are_refused():
    manifest = _with([
        TransferEntry(path="Sa\u0301nchez.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="S\u00e1nchez.txt", kind=ENTRY_FILE, size=2, mtime_ns=2),
    ])

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_an_exact_duplicate_path_is_refused():
    manifest = _with([
        TransferEntry(path="a.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="a.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
    ])

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_an_empty_manifest_is_refused_because_there_is_nothing_to_offer():
    with pytest.raises(UnsafePath):
        sanitize_manifest(_with([]))


def test_too_many_entries_are_refused():
    entries = [
        TransferEntry(path=f"f{index}", kind=ENTRY_FILE, size=0, mtime_ns=1)
        for index in range(MAX_ENTRIES + 1)
    ]

    with pytest.raises(UnsafePath):
        sanitize_manifest(_with(entries))


def test_a_total_size_above_the_ceiling_is_refused():
    manifest = _with([
        TransferEntry(path="huge.bin", kind=ENTRY_FILE, size=MAX_TOTAL_BYTES + 1, mtime_ns=1),
    ])

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_skipped_entry_paths_are_sanitised_too_because_they_reach_the_screen():
    manifest = TransferManifest(
        transfer_id="t",
        entries=(TransferEntry(path="a.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),),
        skipped=(SkippedEntry(path="../../etc/passwd", reason="reparse_point"),),
    )

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_skipped_entries_count_toward_the_entry_ceiling_too():
    # MAX_ENTRIES считает entries и skipped ВМЕСТЕ (round 1 ревью): без этого
    # манифест с одной настоящей записью и неограниченным skipped обошёл бы
    # потолок, который существует именно для ограничения работы над
    # недоверенным вводом.
    manifest = TransferManifest(
        transfer_id="t",
        entries=(TransferEntry(path="a.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),),
        skipped=tuple(
            SkippedEntry(path=f"s{index}", reason="reparse_point")
            for index in range(MAX_ENTRIES)
        ),
    )

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


# --- casefold() ложно сближал эту пару - _collision_key() её развела -----
#
# Измерено реальным open() на этой машине (round 1 ревью,
# task-1.3-report.md): "ß.txt" и "SS.txt" - два РАЗНЫХ файла. Старое
# сравнение по чистому path.casefold() ложно отказывало этот манифест:
# casefold() разворачивает "ß" в "ss" и свёл бы их в одну. _collision_key()
# этот ложный отказ убирает.


def test_sharp_s_and_ss_are_not_treated_as_a_collision():
    # "ß.txt" и "SS.txt" - два РАЗНЫХ файла на этой машине. casefold()
    # разворачивает "ß" в "ss" и свёл бы их в один; upper() делает то же
    # самое в другую сторону ("ß" -> "SS"). Оба неверны здесь.
    manifest = _with([
        TransferEntry(path="ß.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="SS.txt", kind=ENTRY_FILE, size=2, mtime_ns=2),
    ])

    result = sanitize_manifest(manifest)

    assert len(result.entries) == 2


# --- Открытый класс ложных срабатываний - оставлены сознательно ----------
#
# round 3 ревью (task-1.3-report.md): пять пар ниже - НЕ полный список, а
# образец открытого класса. NTFS сравнивает регистр по таблице, которая (а)
# не выходит за пределы BMP и (б) заморожена на старой версии Unicode.
# casefold() - живой, полный Unicode-фолдинг, обновляемый каждый релиз.
# Каждая новая пара регистра, добавленная в Unicode для письменности вне
# старой таблицы NTFS, - это новое расхождение, которое ни casefold(), ни
# upper(), ни любая другая функция из stdlib не закроет раз и навсегда:
# сам класс новых букв не закрыт, потому что Unicode их всё ещё добавляет.
#
# Каждая пара измерена реальным open() в свежем временном каталоге на этой
# машине, коллизия подтверждена номером inode, воспроизведено трижды,
# подтверждено целиком через sanitize_manifest (не только через
# _collision_key изолированно):
#
# - Черокки "Ꭰ" (U+13A0, заглавная) / "ꭰ" (U+AB70, строчная) - строчные
#   черокки добавлены в Unicode 8.0 (2015)
# - Дезерет "𐐀" (U+10400) / "𐐨" (U+10428) - дополнительная плоскость,
#   таблица NTFS её не видит вовсе
# - Грузинская мтаврули "Ა" (U+1C90) / мхедрули "ა" (U+10D0) - мтаврули
#   добавлена в Unicode 11.0 (2018)
# - Долгая s "ſ" (U+017F) / "s" - СОВМЕСТИМЫЙ (не канонический) фолд: NFC
#   его не трогает (NFC - только канонические разложения), а casefold()
#   сворачивает
# - Микро-знак "µ" (U+00B5) / греческая "μ" (U+03BC) - тоже совместимый
#   фолд, тоже не тронут NFC
#
# Мы СОЗНАТЕЛЬНО оставляем эти манифесты отказанными, а не гоняемся за
# точным повторением таблицы NTFS - таблица не выводится из stdlib (см.
# docstring _collision_key). Два направления ошибки не симметричны: ложное
# срабатывание отказывает МАНИФЕСТ ЦЕЛИКОМ с видимой ошибкой, которую
# пользователь может исправить переименованием одного файла; пропущенная
# коллизия молча пишет две записи в один файл, и вторая затирает первую без
# единого сообщения. Отказ - видимая и обратимая ошибка, пропуск -
# необратимая потеря данных без следа. Пара, различающаяся только одним из
# этих символов, достаточно редка, чтобы цена отказа была приемлемой.


def test_cherokee_capital_and_small_letters_are_refused_as_a_collision():
    manifest = _with([
        TransferEntry(path="Ꭰ.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="ꭰ.txt", kind=ENTRY_FILE, size=2, mtime_ns=2),
    ])

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_deseret_capital_and_small_letters_are_refused_as_a_collision():
    # Дополнительная плоскость (U+10400/U+10428) - вне BMP, где
    # останавливается таблица NTFS.
    manifest = _with([
        TransferEntry(path="𐐀.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="𐐨.txt", kind=ENTRY_FILE, size=2, mtime_ns=2),
    ])

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_georgian_mtavruli_and_mkhedruli_letters_are_refused_as_a_collision():
    manifest = _with([
        TransferEntry(path="Ა.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="ა.txt", kind=ENTRY_FILE, size=2, mtime_ns=2),
    ])

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_long_s_and_ascii_s_are_refused_as_a_collision():
    # Совместимое (не каноническое) разложение - NFC его не трогает.
    manifest = _with([
        TransferEntry(path="ſ.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="s.txt", kind=ENTRY_FILE, size=2, mtime_ns=2),
    ])

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_micro_sign_and_greek_mu_are_refused_as_a_collision():
    # Тоже совместимое разложение - тоже не тронуто NFC.
    manifest = _with([
        TransferEntry(path="µ.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="μ.txt", kind=ENTRY_FILE, size=2, mtime_ns=2),
    ])

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


# --- ЗНАК КЕЛЬВИНА, ОМЕГА-ЗНАК, АНГСТРЕМ-ЗНАК: отказ здесь ВЕРНЫЙ --------
#
# Не часть открытого класса выше. Каждый из трёх символов ниже имеет
# КАНОНИЧЕСКОЕ (не совместимое) разложение в одну обычную букву (K, Ω, Å
# соответственно) - NFC в sanitize_relative_path сворачивает его туда ДО
# того, как манифест вообще видит два разных символа. Обе записи в каждой
# паре приходят в sanitize_manifest уже побитово одинаковыми строками, и
# отказ - это ПРАВИЛЬНОЕ поведение: обе записи были бы записаны Проводником
# как один и тот же файл, и одна молча затёрла бы другую. Ни одному из трёх
# не нужен и не помог бы специальный случай в _collision_key - к моменту,
# когда _collision_key видит строку, различие уже стёрто NFC.


def test_kelvin_sign_and_ascii_k_are_correctly_refused_as_the_same_wire_path():
    # ИЗМЕРЕНО реальным open() на этой машине (round 1 ревью): "K.txt"
    # (ASCII) и "K.txt" (ЗНАК КЕЛЬВИНА, U+212A) САМИ ПО СЕБЕ - два
    # РАЗНЫХ файла на диске. Но sanitize_manifest() никогда не видит эти
    # байты напрямую: они сначала проходят через
    # sanitize_relative_path (Task 1.2), а там - через
    # unicodedata.normalize("NFC", ...). У ЗНАКА КЕЛЬВИНА КАНОНИЧЕСКОЕ
    # (не совместимое) разложение в ASCII "K"
    # (unicodedata.decomposition("\u212a") == "004B", без тега <...> -
    # тег означает "совместимое", отсутствие тега значит
    # "каноническое"), так что обе записи приходят в sanitize_manifest
    # уже как "K.txt" и "K.txt" - побитово одинаковыми строками.
    #
    # Это значит ОБЕ записи будут записаны Проводником как один и тот
    # же файл "K.txt", и вторая запись молча затрёт первую. Санитизация
    # отказывает манифест целиком именно поэтому - две записи
    # нормализуются в один и тот же путь, и это ПРАВИЛЬНОЕ поведение, а
    # не известный недостаток или случай, ожидающий лучшей функции
    # свёртки регистра: что бы её ни выбрать, обе записи всё равно
    # пришли бы одинаковыми строками.
    manifest = _with([
        TransferEntry(path="K.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="K.txt", kind=ENTRY_FILE, size=2, mtime_ns=2),
    ])

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_ohm_sign_and_greek_capital_omega_are_correctly_refused_as_the_same_wire_path():
    # ИЗМЕРЕНО реальным open() на этой машине (round 3 ревью): ОМЕГА-ЗНАК
    # (U+2126) и греческая заглавная "Ω" (U+03A9) САМИ ПО СЕБЕ - два
    # РАЗНЫХ файла на диске. Тот же механизм, что у ЗНАКА КЕЛЬВИНА выше:
    # У ОМЕГА-ЗНАКА КАНОНИЧЕСКОЕ разложение в "Ω"
    # (unicodedata.decomposition("Ω") == "03A9", без тега <...>), NFC
    # сворачивает его туда до того, как sanitize_manifest сравнивает
    # регистр, и отказ здесь - правильный результат по той же причине.
    manifest = _with([
        TransferEntry(path="Ω.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="Ω.txt", kind=ENTRY_FILE, size=2, mtime_ns=2),
    ])

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_angstrom_sign_and_a_with_ring_above_are_correctly_refused_as_the_same_wire_path():
    # ИЗМЕРЕНО реальным open() на этой машине (round 3 ревью): АНГСТРЕМ-ЗНАК
    # (U+212B) и латинская "Å" (U+00C5) САМИ ПО СЕБЕ - два РАЗНЫХ файла на
    # диске. Тот же механизм: КАНОНИЧЕСКОЕ разложение
    # (unicodedata.decomposition("Å") == "00C5", без тега <...>), NFC
    # сворачивает его в "Å" раньше, чем sanitize_manifest сравнивает
    # регистр, и отказ здесь - правильный результат по той же причине.
    manifest = _with([
        TransferEntry(path="Å.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="Å.txt", kind=ENTRY_FILE, size=2, mtime_ns=2),
    ])

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_greek_sigma_and_final_sigma_are_refused_as_a_collision():
    # Строчная сигма "σ" и конечная сигма "ς" - ОДИН файл на этой машине:
    # NTFS сворачивает их в один регистронезависимый ключ.
    manifest = _with([
        TransferEntry(path="σ.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="ς.txt", kind=ENTRY_FILE, size=2, mtime_ns=2),
    ])

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_greek_capital_sigma_and_final_sigma_are_refused_as_a_collision():
    manifest = _with([
        TransferEntry(path="Σ.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="ς.txt", kind=ENTRY_FILE, size=2, mtime_ns=2),
    ])

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_dotless_i_and_ascii_capital_i_are_not_treated_as_a_collision():
    # Турецкая раздельная "ı" (без точки) и ASCII "I" - два РАЗНЫХ файла на
    # этой машине. Уже верно под обычным casefold(); тест защищает от
    # регресса при будущей правке _collision_key.
    manifest = _with([
        TransferEntry(path="ı.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="I.txt", kind=ENTRY_FILE, size=2, mtime_ns=2),
    ])

    result = sanitize_manifest(manifest)

    assert len(result.entries) == 2


def test_ascii_i_and_dotted_capital_i_are_not_treated_as_a_collision():
    # ASCII "i" и турецкая "İ" (с точкой) - два РАЗНЫХ файла на этой машине.
    # Тоже уже верно под обычным casefold(); тот же регрессионный смысл.
    manifest = _with([
        TransferEntry(path="i.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="İ.txt", kind=ENTRY_FILE, size=2, mtime_ns=2),
    ])

    result = sanitize_manifest(manifest)

    assert len(result.entries) == 2
