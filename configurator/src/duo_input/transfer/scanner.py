"""Обход того, что пользователь скопировал в Проводнике.

На выходе две вещи, и разделение между ними существенно: манифест уходит на
провод и содержит только относительные пути, а карта абсолютных путей остаётся
на этой машине. Если бы абсолютные пути лежали в манифесте, они уехали бы
второму компьютеру просто потому, что оказались в том же объекте.

По reparse point (junction, symlink) обход НЕ идёт. Папка с junction на
C:\\Windows иначе выгрузила бы операционную систему целиком. Такие записи
попадают в skipped с причиной, чтобы получатель мог о них сказать.

Потолки paths.sanitize_manifest (число записей, глубина, суммарный размер)
проверяются здесь же, ПО ХОДУ обхода. Проверка только после обхода означала
бы, что каталог на миллион файлов сперва целиком перечисляется, сортируется и
stat-ится - и лишь потом получает отказ, который был известен на 65 537-м.
"""

from __future__ import annotations

import logging
import os
import stat
from collections.abc import Sequence
from pathlib import Path

from .model import ENTRY_DIRECTORY, ENTRY_FILE, SkippedEntry, TransferEntry, TransferManifest
from .paths import MAX_DEPTH, MAX_ENTRIES, MAX_TOTAL_BYTES, UnsafePath, sanitize_manifest

logger = logging.getLogger(__name__)

REASON_REPARSE_POINT = "reparse_point"
REASON_UNREADABLE = "unreadable"


def _is_reparse_point(entry_stat: os.stat_result) -> bool:
    """Junction и symlink на Windows, symlink где угодно.

    st_file_attributes есть только на Windows, поэтому проверяется наличие
    атрибута, а не платформа: так функция остаётся тестируемой и на другой ОС.
    """
    attributes = getattr(entry_stat, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse)


class _Walk:
    """Накопитель обхода вместе с его потолками."""

    def __init__(self) -> None:
        self.entries: list[TransferEntry] = []
        self.skipped: list[SkippedEntry] = []
        self.sources: dict[str, Path] = {}
        self.total_bytes = 0

    @property
    def count(self) -> int:
        return len(self.entries) + len(self.skipped)

    def reserve_one(self) -> None:
        """Место ещё под одну запись - или отказ до того, как её трогать."""
        if self.count >= MAX_ENTRIES:
            raise UnsafePath(f"записей больше {MAX_ENTRIES}")

    def room_left(self) -> int:
        return MAX_ENTRIES - self.count

    def add_bytes(self, size: int) -> None:
        self.total_bytes += size
        if self.total_bytes > MAX_TOTAL_BYTES:
            raise UnsafePath(f"суммарный размер больше {MAX_TOTAL_BYTES}")


def scan(
    roots: Sequence[Path], transfer_id: str, drop_effect: int = 1
) -> tuple[TransferManifest, dict[str, Path]]:
    """Манифест плюс карта ``относительный путь -> абсолютный источник``."""
    walk = _Walk()

    for root in roots:
        _walk(Path(root), Path(root).name, walk)

    manifest = sanitize_manifest(
        TransferManifest(
            transfer_id=transfer_id,
            entries=tuple(walk.entries),
            skipped=tuple(walk.skipped),
            drop_effect=drop_effect,
        )
    )
    # sanitize_manifest канонизирует пути, поэтому карта пересобирается по
    # каноническим ключам - иначе поиск источника по пути из манифеста
    # промахнулся бы на любом пути, который нормализация изменила.
    canonical = {
        entry.path: walk.sources[original]
        for entry, original in zip(manifest.entries, (e.path for e in walk.entries), strict=True)
        if entry.kind == ENTRY_FILE
    }
    return manifest, canonical


def _walk(absolute: Path, relative: str, walk: _Walk) -> None:
    if relative.count("/") + 1 > MAX_DEPTH:
        raise UnsafePath(f"вложенность больше {MAX_DEPTH}")
    walk.reserve_one()
    try:
        entry_stat = os.stat(absolute, follow_symlinks=False)
    except OSError:
        # Полный путь в журнал не идёт - только имя (спека §15).
        logger.warning("не удалось прочитать %s", absolute.name)
        walk.skipped.append(SkippedEntry(path=relative, reason=REASON_UNREADABLE))
        return

    if _is_reparse_point(entry_stat):
        walk.skipped.append(SkippedEntry(path=relative, reason=REASON_REPARSE_POINT))
        return

    if stat.S_ISDIR(entry_stat.st_mode):
        walk.entries.append(
            TransferEntry(
                path=relative, kind=ENTRY_DIRECTORY, size=0, mtime_ns=entry_stat.st_mtime_ns
            )
        )
        try:
            children = _bounded_names(absolute, walk.room_left())
        except OSError:
            logger.warning("не удалось перечислить %s", absolute.name)
            walk.skipped.append(SkippedEntry(path=relative, reason=REASON_UNREADABLE))
            return
        for child in children:
            _walk(absolute / child, f"{relative}/{child}", walk)
        return

    walk.add_bytes(entry_stat.st_size)
    walk.entries.append(
        TransferEntry(
            path=relative,
            kind=ENTRY_FILE,
            size=entry_stat.st_size,
            mtime_ns=entry_stat.st_mtime_ns,
        )
    )
    walk.sources[relative] = absolute


def _bounded_names(directory: Path, room: int) -> list[str]:
    """Имена детей по порядку - но не больше, чем ещё поместится в манифест.

    Сортировка нужна для воспроизводимого порядка, а сортировать можно только
    целый список. Поэтому перечисление обрывается, как только имён стало
    больше, чем осталось места: дальше отказ известен и без них.
    """
    names: list[str] = []
    with os.scandir(directory) as entries:
        for entry in entries:
            names.append(entry.name)
            if len(names) > room:
                raise UnsafePath(f"записей больше {MAX_ENTRIES}")
    return sorted(names)


__all__ = ["REASON_REPARSE_POINT", "REASON_UNREADABLE", "scan"]
