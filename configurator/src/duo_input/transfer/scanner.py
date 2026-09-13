"""Обход того, что пользователь скопировал в Проводнике.

На выходе две вещи, и разделение между ними существенно: манифест уходит на
провод и содержит только относительные пути, а карта абсолютных путей остаётся
на этой машине. Если бы абсолютные пути лежали в манифесте, они уехали бы
второму компьютеру просто потому, что оказались в том же объекте.

По reparse point (junction, symlink) обход НЕ идёт. Папка с junction на
C:\\Windows иначе выгрузила бы операционную систему целиком. Такие записи
попадают в skipped с причиной, чтобы получатель мог о них сказать.
"""

from __future__ import annotations

import logging
import os
import stat
from collections.abc import Sequence
from pathlib import Path

from .model import ENTRY_DIRECTORY, ENTRY_FILE, SkippedEntry, TransferEntry, TransferManifest
from .paths import sanitize_manifest

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


def scan(
    roots: Sequence[Path], transfer_id: str, drop_effect: int = 1
) -> tuple[TransferManifest, dict[str, Path]]:
    """Манифест плюс карта ``относительный путь -> абсолютный источник``."""
    entries: list[TransferEntry] = []
    skipped: list[SkippedEntry] = []
    sources: dict[str, Path] = {}

    for root in roots:
        _walk(Path(root), Path(root).name, entries, skipped, sources)

    manifest = sanitize_manifest(
        TransferManifest(
            transfer_id=transfer_id,
            entries=tuple(entries),
            skipped=tuple(skipped),
            drop_effect=drop_effect,
        )
    )
    # sanitize_manifest канонизирует пути, поэтому карта пересобирается по
    # каноническим ключам - иначе поиск источника по пути из манифеста
    # промахнулся бы на любом пути, который нормализация изменила.
    canonical = {
        entry.path: sources[original]
        for entry, original in zip(manifest.entries, (e.path for e in entries), strict=True)
        if entry.kind == ENTRY_FILE
    }
    return manifest, canonical


def _walk(
    absolute: Path,
    relative: str,
    entries: list[TransferEntry],
    skipped: list[SkippedEntry],
    sources: dict[str, Path],
) -> None:
    try:
        entry_stat = os.stat(absolute, follow_symlinks=False)
    except OSError:
        # Полный путь в журнал не идёт - только имя (спека §15).
        logger.warning("не удалось прочитать %s", absolute.name)
        skipped.append(SkippedEntry(path=relative, reason=REASON_UNREADABLE))
        return

    if _is_reparse_point(entry_stat):
        skipped.append(SkippedEntry(path=relative, reason=REASON_REPARSE_POINT))
        return

    if stat.S_ISDIR(entry_stat.st_mode):
        entries.append(
            TransferEntry(
                path=relative, kind=ENTRY_DIRECTORY, size=0, mtime_ns=entry_stat.st_mtime_ns
            )
        )
        try:
            children = sorted(os.listdir(absolute))
        except OSError:
            logger.warning("не удалось перечислить %s", absolute.name)
            skipped.append(SkippedEntry(path=relative, reason=REASON_UNREADABLE))
            return
        for child in children:
            _walk(absolute / child, f"{relative}/{child}", entries, skipped, sources)
        return

    entries.append(
        TransferEntry(
            path=relative,
            kind=ENTRY_FILE,
            size=entry_stat.st_size,
            mtime_ns=entry_stat.st_mtime_ns,
        )
    )
    sources[relative] = absolute


__all__ = ["REASON_REPARSE_POINT", "REASON_UNREADABLE", "scan"]
