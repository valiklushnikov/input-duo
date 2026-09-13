"""Проверка того, что защитные условия действительно защищают.

Тест, который проходит с удалённым условием, не тестирует ничего. Здесь каждое
условие подменяется на пропускающее, и соответствующая проверка обязана
упасть. Если она не падает - виновата проверка, а не этот файл.

Образец - configurator/tests/clipboard/test_guards_are_real.py.

Модуль расширен за пределы исходного задания (path guards в paths.py) на
guard'ы model.py: Global Constraints плана требует мутационный тест для
КАЖДОГО guard'а и называет именно этот файл их местом, а бриф Task 1.4
ограничивает область одним paths.py - это внутреннее противоречие плана, и
здесь оно решено в пользу Global Constraints (см. task-1.4-report.md).

Ни один патч здесь не редактирует paths.py или model.py на диске: там, где
проверку нельзя выключить, подменив атрибут модуля/класса, весь classmethod
или функция переписаны заново в этом файле и подставлены через
monkeypatch.setattr - без этого не изолировать проверку, встроенную прямо в
тело метода (см. комментарии у соответствующих тестов).
"""

from __future__ import annotations

import unicodedata
from types import MappingProxyType

import pytest

from duo_input.transfer import model, paths
from duo_input.transfer.model import (
    ENTRY_FILE,
    SkippedEntry,
    TransferEntry,
    TransferManifest,
)
from duo_input.transfer.paths import UnsafePath, sanitize_manifest, sanitize_relative_path

# ---------------------------------------------------------------------------
# paths.py
# ---------------------------------------------------------------------------


def test_without_the_reserved_name_table_a_device_name_would_pass(monkeypatch):
    monkeypatch.setattr(paths, "_RESERVED_STEMS", frozenset())

    assert sanitize_relative_path("NUL") == "NUL", (
        "таблица зарезервированных имён пуста, а NUL всё равно отвергнут"
    )


@pytest.mark.parametrize("bad", ["<", ">", '"', "|", "?", "*"])
def test_without_the_forbidden_character_set_a_colon_free_bad_name_would_pass(monkeypatch, bad):
    # ':' сюда намеренно не входит: он и без этого набора ловится отдельной
    # проверкой двоеточия в _check_segment (см. NB (Task 1.4) там же), так
    # что удаление _FORBIDDEN_CHARACTERS в одиночку не красит зелёным именно
    # этот один символ из семи - остальные шесть должны стать проходными.
    monkeypatch.setattr(paths, "_FORBIDDEN_CHARACTERS", frozenset())

    raw = f"na{bad}me.txt"
    assert sanitize_relative_path(raw) == raw, (
        "набор запрещённых символов пуст, а символ всё равно отвергнут"
    )


def test_without_the_utf16_measure_an_astral_name_would_fit(monkeypatch):
    # Подменяем меру на питоновскую len: ровно та ошибка, которую _utf16_units
    # и существует, чтобы не совершить.
    monkeypatch.setattr(paths, "_utf16_units", len)

    astral = "\U0001F600" * 200

    assert sanitize_relative_path(astral) == unicodedata.normalize("NFC", astral), (
        "мера длины подменена на len(), а имя из 400 единиц UTF-16 всё равно "
        "отвергнуто - значит потолок проверяет не то, что попадёт в cFileName"
    )


def test_without_normalisation_a_fullwidth_dot_would_pass(monkeypatch):
    monkeypatch.setattr(paths.unicodedata, "normalize", lambda _form, text: text)

    assert sanitize_relative_path("name．") == "name．", (
        "нормализация отключена, а полноширинная точка всё равно отвергнута"
    )


def test_the_entry_ceiling_is_the_reason_a_huge_manifest_is_refused():
    entries = tuple(
        TransferEntry(path=f"f{index}", kind=ENTRY_FILE, size=0, mtime_ns=1)
        for index in range(paths.MAX_ENTRIES + 1)
    )

    with pytest.raises(UnsafePath, match=str(paths.MAX_ENTRIES)):
        sanitize_manifest(TransferManifest(transfer_id="t", entries=entries))


def test_the_whole_manifest_is_refused_not_merely_the_bad_entry():
    manifest = TransferManifest(
        transfer_id="t",
        entries=(
            TransferEntry(path="good.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
            TransferEntry(path="..\\evil.exe", kind=ENTRY_FILE, size=1, mtime_ns=1),
        ),
    )

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_without_the_casefold_collision_check_two_names_would_share_one_file(monkeypatch):
    # ИЗМЕНЕНО в round 1 правок Task 1.3 (task-1.3-report.md): раньше здесь
    # стоял подкласс str с casefold()-как-тождеством поверх
    # sanitize_relative_path, потому что на тот момент свёртка регистра была
    # инлайном (path.casefold()) и не имела отдельного патчимого имени, а
    # monkeypatch.setattr(str, "casefold", ...) бросает TypeError - CPython
    # не даёт патчить неизменяемые встроенные типы (проверено напрямую - см.
    # task-1.4-report.md). Тот подкласс всё равно перестал бы работать: Task
    # 1.3 заменила path.casefold() на _collision_key(path), а она читает
    # path посимвольно (for character in path), и итератор str.__iter__
    # отдаёт обычные str, а не экземпляры подкласса - .casefold() подкласса
    # ни разу не вызвался бы. Теперь _collision_key - обычное имя в paths.py,
    # и его можно подменить впрямую, без обёрток.
    monkeypatch.setattr(paths, "_collision_key", lambda path: path)

    manifest = TransferManifest(
        transfer_id="t",
        entries=(
            TransferEntry(path="IMG.jpg", kind=ENTRY_FILE, size=1, mtime_ns=1),
            TransferEntry(path="img.jpg", kind=ENTRY_FILE, size=2, mtime_ns=2),
        ),
    )

    result = sanitize_manifest(manifest)

    assert len(result.entries) == 2, (
        "сравнение без учёта регистра снято, а коллизия всё равно найдена"
    )


# --- ".", ".." против точки/пробела в конце сегмента --------------------
#
# Удаление правила "." / ".." в одиночку оставляет сюит зелёным: оба
# заканчиваются точкой и ловятся правилом "точка/пробел в конце" ниже по
# файлу. Обратное неверно - удаление ТОЛЬКО правила точки/пробела не красит
# ".."/"." зелёным, потому что верхнее правило по-прежнему их ловит первым.
# Обе NB (Task 1.4) пометки в paths.py называют эту связь. Три теста ниже
# измеряют её явно, а не просто её же и постулируют.


def _check_segment_without_traversal_and_trailing_dot(segment: str) -> None:
    if not segment:
        raise UnsafePath("пустой сегмент пути")
    if ":" in segment:
        raise UnsafePath("двоеточие в имени")
    if any(character in paths._FORBIDDEN_CHARACTERS for character in segment):
        raise UnsafePath("запрещённый символ в имени")
    if any(ord(character) < 0x20 for character in segment):
        raise UnsafePath("управляющий символ в имени")
    if any(character in paths._FORBIDDEN_FORMATTING_CHARACTERS for character in segment):
        raise UnsafePath("управляющий или невидимый символ форматирования текста")
    stem = segment.split(".", 1)[0].rstrip(" ").upper()
    if stem in paths._RESERVED_STEMS:
        raise UnsafePath("зарезервированное имя устройства")


@pytest.mark.parametrize(
    "raw",
    ["../evil.exe", "..", "Photos/../../evil.exe", "./a.txt", "."],
)
def test_without_both_the_traversal_and_trailing_dot_rules_a_traversal_would_pass(monkeypatch, raw):
    monkeypatch.setattr(paths, "_check_segment", _check_segment_without_traversal_and_trailing_dot)

    assert sanitize_relative_path(raw) == raw, (
        "оба перекрывающих друг друга правила сняты, а путь выхода за корень "
        "всё равно отвергнут - значит отвергает его что-то третье"
    )


def _check_segment_without_traversal_only(segment: str) -> None:
    if not segment:
        raise UnsafePath("пустой сегмент пути")
    if ":" in segment:
        raise UnsafePath("двоеточие в имени")
    if segment[-1] in {".", " "}:
        raise UnsafePath("сегмент заканчивается точкой или пробелом")
    if any(character in paths._FORBIDDEN_CHARACTERS for character in segment):
        raise UnsafePath("запрещённый символ в имени")
    if any(ord(character) < 0x20 for character in segment):
        raise UnsafePath("управляющий символ в имени")
    if any(character in paths._FORBIDDEN_FORMATTING_CHARACTERS for character in segment):
        raise UnsafePath("управляющий или невидимый символ форматирования текста")
    stem = segment.split(".", 1)[0].rstrip(" ").upper()
    if stem in paths._RESERVED_STEMS:
        raise UnsafePath("зарезервированное имя устройства")


def test_without_only_the_traversal_rule_a_traversal_is_still_caught_by_the_trailing_dot_rule(
    monkeypatch,
):
    # Односторонность переноса: сняв ТОЛЬКО ".", ".." - без второго правила -
    # ".." всё ещё отвергается, просто другим правилом. Наивный мутационный
    # тест "снял одно правило -> путь должен пройти" здесь провалился бы
    # (путь по-прежнему отвергнут), и это ожидаемо: он бы доказывал не то.
    monkeypatch.setattr(paths, "_check_segment", _check_segment_without_traversal_only)

    with pytest.raises(UnsafePath):
        sanitize_relative_path("..")


def _check_segment_without_trailing_dot_only(segment: str) -> None:
    if not segment:
        raise UnsafePath("пустой сегмент пути")
    if segment in {".", ".."}:
        raise UnsafePath("сегмент выхода за корень")
    if ":" in segment:
        raise UnsafePath("двоеточие в имени")
    if any(character in paths._FORBIDDEN_CHARACTERS for character in segment):
        raise UnsafePath("запрещённый символ в имени")
    if any(ord(character) < 0x20 for character in segment):
        raise UnsafePath("управляющий символ в имени")
    if any(character in paths._FORBIDDEN_FORMATTING_CHARACTERS for character in segment):
        raise UnsafePath("управляющий или невидимый символ форматирования текста")
    stem = segment.split(".", 1)[0].rstrip(" ").upper()
    if stem in paths._RESERVED_STEMS:
        raise UnsafePath("зарезервированное имя устройства")


def test_without_only_the_trailing_dot_rule_an_ordinary_trailing_dot_name_would_pass(monkeypatch):
    # Контраст: правило точки/пробела в конце само по себе НЕ перекрыто -
    # обычное имя с точкой в конце ("notes.", не "." и не "..") ничем другим
    # не ловится.
    monkeypatch.setattr(paths, "_check_segment", _check_segment_without_trailing_dot_only)

    raw = "notes."
    assert sanitize_relative_path(raw) == raw, (
        "правило точки/пробела в конце снято одно, а обычное имя с точкой "
        "всё равно отвергнуто - значит отвергает его что-то другое"
    )


# --- isinstance(raw, str): нагружено для truthy, перекрыто для falsy ------


def _sanitize_relative_path_without_string_check(raw):
    if not raw:
        raise UnsafePath("пустой путь")
    text = unicodedata.normalize("NFC", raw)
    text = text.replace("\\", "/")
    if text.startswith("/"):
        raise UnsafePath("путь абсолютный")
    if paths._utf16_units(text) > paths.MAX_PATH_UTF16:
        raise UnsafePath(f"путь длиннее {paths.MAX_PATH_UTF16} единиц UTF-16")
    segments = text.split("/")
    if len(segments) > paths.MAX_DEPTH:
        raise UnsafePath(f"вложенность больше {paths.MAX_DEPTH}")
    for segment in segments:
        paths._check_segment(segment)
    return "/".join(segments)


def test_without_the_string_type_check_a_truthy_non_string_input_escapes_as_a_bare_crash(
    monkeypatch,
):
    # Для truthy не-str проверка нагружена не "пропуском значения" (дальше
    # по функции всё равно упадёт), а превращением сырого краша в
    # контролируемый UnsafePath. Без неё вызывающий код вида
    # "except UnsafePath: reject()" не отказал бы закрыто - наружу вышел бы
    # TypeError из unicodedata.normalize().
    monkeypatch.setattr(paths, "sanitize_relative_path", _sanitize_relative_path_without_string_check)

    with pytest.raises(TypeError):
        paths.sanitize_relative_path(["Photos", "img.jpg"])


def test_without_the_string_type_check_a_falsy_non_string_is_still_caught_by_the_empty_path_rule(
    monkeypatch,
):
    # Перекрытие для falsy: "not raw" на 0/None/False/b""/() тоже истинно, и
    # проверка пустого пути ловит их тем же UnsafePath - изолированного теста
    # "пропуск falsy значения" для этой проверки написать нельзя, он был бы
    # ложным.
    monkeypatch.setattr(paths, "sanitize_relative_path", _sanitize_relative_path_without_string_check)

    with pytest.raises(UnsafePath):
        paths.sanitize_relative_path(0)


# ---------------------------------------------------------------------------
# model.py
#
# Задание 1.4 по тексту брифа ограничено paths.py, но Global Constraints
# плана требуют мутационного теста для каждого guard'а и называют именно этот
# файл их местом - это внутреннее противоречие плана. Раз связывающее
# ограничение сильнее текста одной задачи, тесты ниже покрывают
# непротестированные guard'ы model.py (см. task-1.4-report.md о том, что из
# перечисленного в задании подтвердилось, а что оказалось перекрыто).
# ---------------------------------------------------------------------------


def _entry_raw(**overrides):
    base = {"path": "a.bin", "kind": ENTRY_FILE, "size": 1, "mtime_ns": 1}
    base.update(overrides)
    return base


def _manifest_raw(**overrides):
    base = {"transfer_id": "t", "entries": [_entry_raw()], "skipped": []}
    base.update(overrides)
    return base


# --- _require_str: isinstance(value, str) --------------------------------


def _require_str_without_type_check(raw, key):
    return raw.get(key)


def test_without_the_string_type_check_a_non_string_path_would_be_accepted(monkeypatch):
    monkeypatch.setattr(model, "_require_str", _require_str_without_type_check)

    entry = TransferEntry.from_dict(_entry_raw(path=123))

    assert entry.path == 123, "проверка типа снята, а нестроковый path всё равно отвергнут"


def test_without_the_string_type_check_a_non_string_reason_would_be_accepted(monkeypatch):
    monkeypatch.setattr(model, "_require_str", _require_str_without_type_check)

    skipped = SkippedEntry.from_dict({"path": "link", "reason": 42})

    assert skipped.reason == 42, "проверка типа снята, а нестроковая reason всё равно отвергнута"


def test_without_the_string_type_check_a_non_string_transfer_id_would_be_accepted(monkeypatch):
    monkeypatch.setattr(model, "_require_str", _require_str_without_type_check)

    manifest = TransferManifest.from_dict(_manifest_raw(transfer_id=999))

    assert manifest.transfer_id == 999, (
        "проверка типа снята, а нестроковый transfer_id всё равно отвергнут"
    )


def test_without_the_string_type_check_a_non_string_kind_is_still_caught_by_the_kind_table(
    monkeypatch,
):
    # Не то же самое, что три теста выше: у kind есть второй, независимый
    # барьер - "kind not in _KINDS". _KINDS содержит только строки "file" и
    # "directory", так что ЛЮБОЕ нестроковое значение (int, None, список)
    # неизбежно проваливает сравнение "in" и ловится этим барьером. Изолировать
    # проверку типа для kind обычными значениями нельзя - она перекрыта
    # полностью, а не частично, как isinstance(raw, str) выше. Это не
    # указано в карте задания и обнаружено измерением, а не предположено.
    monkeypatch.setattr(model, "_require_str", _require_str_without_type_check)

    with pytest.raises(ValueError):
        TransferEntry.from_dict(_entry_raw(kind=5))


# --- _require_index: тип / исключение bool / отрицательность -------------


def _require_index_without_type_check(raw, key):
    value = raw.get(key)
    if value < 0:
        raise ValueError(f"{key} не может быть отрицательной")
    return value


def _require_index_without_bool_exclusion(raw, key):
    value = raw.get(key)
    if not isinstance(value, int):
        raise ValueError(f"{key} должна быть int")
    if value < 0:
        raise ValueError(f"{key} не может быть отрицательной")
    return value


def _require_index_without_negative_check(raw, key):
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} должна быть int")
    return value


@pytest.mark.parametrize(
    "build",
    [
        lambda value: TransferEntry.from_dict(_entry_raw(size=value)),
        lambda value: TransferEntry.from_dict(_entry_raw(mtime_ns=value)),
        lambda value: TransferManifest.from_dict(_manifest_raw(drop_effect=value)),
    ],
    ids=["size", "mtime_ns", "drop_effect"],
)
def test_without_the_index_type_check_a_float_value_would_be_accepted(monkeypatch, build):
    monkeypatch.setattr(model, "_require_index", _require_index_without_type_check)

    result = build(5.5)

    # Значение 5.5 попало ровно в одно из трёх полей - найдём его без
    # привязки к тому, какой параметр сейчас выполняется.
    value = next(
        v
        for v in (getattr(result, "size", None), getattr(result, "mtime_ns", None), getattr(result, "drop_effect", None))
        if v == 5.5
    )

    assert value == 5.5 and isinstance(value, float), (
        "проверка типа снята, а вещественное значение всё равно отвергнуто"
    )


def test_without_the_bool_exclusion_a_bool_drop_effect_would_be_accepted(monkeypatch):
    monkeypatch.setattr(model, "_require_index", _require_index_without_bool_exclusion)

    manifest = TransferManifest.from_dict(_manifest_raw(drop_effect=True))

    assert manifest.drop_effect is True, (
        "исключение bool снято, а drop_effect=True всё равно отвергнут"
    )


@pytest.mark.parametrize(
    "build",
    [
        lambda value: TransferEntry.from_dict(_entry_raw(size=value)),
        lambda value: TransferEntry.from_dict(_entry_raw(mtime_ns=value)),
        lambda value: TransferManifest.from_dict(_manifest_raw(drop_effect=value)),
    ],
    ids=["size", "mtime_ns", "drop_effect"],
)
def test_without_the_negative_check_a_negative_value_would_be_accepted(monkeypatch, build):
    monkeypatch.setattr(model, "_require_index", _require_index_without_negative_check)

    result = build(-5)

    value = next(
        v
        for v in (getattr(result, "size", None), getattr(result, "mtime_ns", None), getattr(result, "drop_effect", None))
        if v == -5
    )

    assert value == -5, "проверка отрицательности снята, а отрицательное значение всё равно отвергнуто"


# --- isinstance(raw, dict) в TransferEntry/SkippedEntry/TransferManifest --
#
# Эти три проверки не вынесены в общую функцию - они встроены прямо в тело
# каждого from_dict. Подменить их атрибутом модуля нельзя, поэтому здесь
# заменяется целиком classmethod (тем же приёмом, что и third belt в
# clipboard/test_guards_are_real.py: он тоже оборачивает целый метод, чтобы
# убрать одну его часть). Значение для проверки - MappingProxyType: у него
# есть .get(), как у dict, но isinstance(x, dict) для него ложно - настоящий,
# не выдуманный кандидат на обход.


def _entry_from_dict_without_dict_check(cls, raw):
    kind = model._require_str(raw, "kind")
    if kind not in model._KINDS:
        raise ValueError(f"неизвестный вид записи {kind!r}")
    return cls(
        path=model._require_str(raw, "path"),
        kind=kind,
        size=model._require_index(raw, "size"),
        mtime_ns=model._require_index(raw, "mtime_ns"),
    )


def _skipped_from_dict_without_dict_check(cls, raw):
    return cls(path=model._require_str(raw, "path"), reason=model._require_str(raw, "reason"))


def _manifest_from_dict_without_dict_check(cls, raw):
    entries = raw.get("entries")
    if not isinstance(entries, list):
        raise ValueError("entries должна быть list")
    skipped = raw.get("skipped", [])
    if not isinstance(skipped, list):
        raise ValueError("skipped должна быть list")
    return cls(
        transfer_id=model._require_str(raw, "transfer_id"),
        entries=tuple(TransferEntry.from_dict(entry) for entry in entries),
        skipped=tuple(SkippedEntry.from_dict(skip) for skip in skipped),
        drop_effect=model._require_index(raw, "drop_effect") if "drop_effect" in raw else 1,
    )


def test_without_the_dict_check_a_non_dict_entry_item_would_be_accepted(monkeypatch):
    # Одновременно и "raw не dict" для TransferEntry.from_dict, и "нестроковый
    # по форме элемент внутри entries", вызванный через настоящий,
    # непропатченный TransferManifest.from_dict.
    monkeypatch.setattr(TransferEntry, "from_dict", classmethod(_entry_from_dict_without_dict_check))

    proxy_entry = MappingProxyType({"path": "a.bin", "kind": ENTRY_FILE, "size": 1, "mtime_ns": 1})
    manifest = TransferManifest.from_dict(
        {"transfer_id": "t", "entries": [proxy_entry], "skipped": []}
    )

    assert len(manifest.entries) == 1 and manifest.entries[0].path == "a.bin", (
        "проверка dict снята, а не-dict элемент entries всё равно отвергнут"
    )


def test_without_the_dict_check_a_non_dict_skipped_item_would_be_accepted(monkeypatch):
    monkeypatch.setattr(SkippedEntry, "from_dict", classmethod(_skipped_from_dict_without_dict_check))

    proxy_skip = MappingProxyType({"path": "link", "reason": "reparse_point"})
    manifest = TransferManifest.from_dict(
        {"transfer_id": "t", "entries": [_entry_raw()], "skipped": [proxy_skip]}
    )

    assert len(manifest.skipped) == 1 and manifest.skipped[0].reason == "reparse_point", (
        "проверка dict снята, а не-dict элемент skipped всё равно отвергнут"
    )


def test_without_the_dict_check_a_non_dict_manifest_would_be_accepted(monkeypatch):
    monkeypatch.setattr(
        TransferManifest, "from_dict", classmethod(_manifest_from_dict_without_dict_check)
    )

    proxy_manifest = MappingProxyType(_manifest_raw())
    manifest = TransferManifest.from_dict(proxy_manifest)

    assert manifest.transfer_id == "t", "проверка dict снята, а не-dict манифест всё равно отвергнут"


# --- entries/skipped должны быть list, а не просто перебираемыми ---------
#
# Значение для проверки - кортеж настоящих dict-записей, а не dict/строка:
# итерация по dict отдаёт ключи, а по строке - символы, и они всё равно
# упадут на проверке "raw должен быть dict" внутри TransferEntry.from_dict /
# SkippedEntry.from_dict - то есть окажутся перекрыты ЕЮ, а не докажут
# ничего про проверку list. Кортеж из настоящих dict проходит их проверки, и
# только тип контейнера отличается от list.


def _manifest_from_dict_without_entries_list_check(cls, raw):
    if not isinstance(raw, dict):
        raise ValueError("манифест должен быть dict")
    entries = raw.get("entries")
    skipped = raw.get("skipped", [])
    if not isinstance(skipped, list):
        raise ValueError("skipped должна быть list")
    return cls(
        transfer_id=model._require_str(raw, "transfer_id"),
        entries=tuple(TransferEntry.from_dict(entry) for entry in entries),
        skipped=tuple(SkippedEntry.from_dict(skip) for skip in skipped),
        drop_effect=model._require_index(raw, "drop_effect") if "drop_effect" in raw else 1,
    )


def _manifest_from_dict_without_skipped_list_check(cls, raw):
    if not isinstance(raw, dict):
        raise ValueError("манифест должен быть dict")
    entries = raw.get("entries")
    if not isinstance(entries, list):
        raise ValueError("entries должна быть list")
    skipped = raw.get("skipped", [])
    return cls(
        transfer_id=model._require_str(raw, "transfer_id"),
        entries=tuple(TransferEntry.from_dict(entry) for entry in entries),
        skipped=tuple(SkippedEntry.from_dict(skip) for skip in skipped),
        drop_effect=model._require_index(raw, "drop_effect") if "drop_effect" in raw else 1,
    )


def test_without_the_entries_list_check_a_tuple_of_real_entries_would_be_accepted(monkeypatch):
    monkeypatch.setattr(
        TransferManifest, "from_dict", classmethod(_manifest_from_dict_without_entries_list_check)
    )

    manifest = TransferManifest.from_dict(
        {"transfer_id": "t", "entries": (_entry_raw(),), "skipped": []}
    )

    assert len(manifest.entries) == 1, "проверка list для entries снята, а кортеж всё равно отвергнут"


def test_without_the_skipped_list_check_a_tuple_of_real_entries_would_be_accepted(monkeypatch):
    monkeypatch.setattr(
        TransferManifest, "from_dict", classmethod(_manifest_from_dict_without_skipped_list_check)
    )

    manifest = TransferManifest.from_dict(
        {
            "transfer_id": "t",
            "entries": [_entry_raw()],
            "skipped": ({"path": "link", "reason": "reparse_point"},),
        }
    )

    assert len(manifest.skipped) == 1, "проверка list для skipped снята, а кортеж всё равно отвергнут"
