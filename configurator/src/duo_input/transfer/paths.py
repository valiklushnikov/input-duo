"""Проверка относительных путей, пришедших со второго компьютера.

Проводник записывает файлы сам, но имена в FILEGROUPDESCRIPTORW строим мы -
и это единственное место в этой функции, где безопасность целиком наша. Ни
один пир, даже доверенный, не имеет права указать, куда писать.

Отвергается МАНИФЕСТ ЦЕЛИКОМ, а не отдельная запись (см. sanitize_manifest):
пользователь, получивший девять файлов из десяти, об этом не узнает, а честный
отказ он увидит.

Нормализация Unicode идёт ПЕРЕД остальными проверками, и это не косметика.
NFC - каноническая форма: она не разворачивает совместимые варианты вроде
полноширинной точки (для этого нужен NFKC, а не NFC - мы сознательно
остаёмся на NFC, см. sanitize_relative_path), но она может и УДЛИНИТЬ
строку в единицах UTF-16. Например, U+0344 (COMBINING GREEK DIALYTIKA
TONOS) исключён из повторной композиции: после NFC он становится двумя
отдельными знаками вместо одного. Если бы потолок длины мерили до
нормализации, такое имя проскочило бы мимо него.

Правила здесь - надмножество и для macOS тоже. Ослаблять их под другую
платформу незачем: запрет лишнего имени никого не ломает, а разные правила на
двух концах ломают ровно то, что должно совпадать.
"""

from __future__ import annotations

import unicodedata

#: cFileName - это WCHAR[260], то есть 259 значимых единиц UTF-16 плюс ноль.
MAX_PATH_UTF16 = 259

#: Глубина, за которой дерево перестаёт быть похожим на копирование файлов.
MAX_DEPTH = 32

_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*')

_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{digit}" for digit in "123456789"}
    | {f"LPT{digit}" for digit in "123456789"}
)


class UnsafePath(ValueError):
    """Путь, которого не мог бы прислать исправный второй компьютер."""


def _utf16_units(text: str) -> int:
    """Длина в единицах UTF-16, а не в символах Python.

    Символ вне BMP занимает две единицы, а поле cFileName считает именно их.
    len() здесь соврал бы вдвое в пользу злоумышленника.
    """
    return len(text.encode("utf-16-le")) // 2


def sanitize_relative_path(raw: str) -> str:
    """Каноническая относительная форма, либо ``UnsafePath``."""
    if not isinstance(raw, str):
        raise UnsafePath("путь должен быть str")
    if not raw:
        raise UnsafePath("пустой путь")

    # 1. Нормализация Unicode - первым делом, до всех остальных проверок.
    # Именно NFC, не NFKC: коллизии в манифесте (Task 1.3) определены как
    # "совпали после NFC", и NFKC складывал бы разные удалённые имена в один
    # локальный файл сильнее, чем нужно для безопасности.
    text = unicodedata.normalize("NFC", raw)

    # 2. Единый разделитель. Проверки ниже смотрят уже на сегменты.
    text = text.replace("\\", "/")

    if text.startswith("/"):
        raise UnsafePath("путь абсолютный")
    if _utf16_units(text) > MAX_PATH_UTF16:
        raise UnsafePath(f"путь длиннее {MAX_PATH_UTF16} единиц UTF-16")

    segments = text.split("/")
    if len(segments) > MAX_DEPTH:
        raise UnsafePath(f"вложенность больше {MAX_DEPTH}")

    for segment in segments:
        _check_segment(segment)

    return "/".join(segments)


def _check_segment(segment: str) -> None:
    if not segment:
        raise UnsafePath("пустой сегмент пути")
    if segment in {".", ".."}:
        raise UnsafePath("сегмент выхода за корень")
    if ":" in segment:
        # Ловит и "C:\..." и "C:file" и поток NTFS "file:stream" - все три
        # являются способом уйти не туда, куда получатель разрешил.
        raise UnsafePath("двоеточие в имени")
    if segment[-1] in {".", " "}:
        # Windows отбрасывает завершающую точку и пробел, поэтому "a." и "a"
        # столкнулись бы в одном файле, а "a .exe" перестало бы быть тем,
        # что видел пользователь.
        raise UnsafePath("сегмент заканчивается точкой или пробелом")
    if any(character in _FORBIDDEN_CHARACTERS for character in segment):
        raise UnsafePath("запрещённый символ в имени")
    if any(ord(character) < 0x20 for character in segment):
        raise UnsafePath("управляющий символ в имени")
    stem = segment.split(".", 1)[0].upper()
    if stem in _RESERVED_STEMS:
        raise UnsafePath("зарезервированное имя устройства")


__all__ = ["MAX_DEPTH", "MAX_PATH_UTF16", "UnsafePath", "sanitize_relative_path"]
