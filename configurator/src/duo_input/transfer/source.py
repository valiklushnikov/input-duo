"""Снимки на отправляющей машине: что обещано и откуда это читать.

Модель называется УДЕРЖИВАЕМЫМ ДЕСКРИПТОРОМ С ОБНАРУЖЕНИЕМ ИЗМЕНЕНИЙ, а не
"неизменяемым снимком", и разница существенна. Измерено на целевой платформе
(спека §2, факт 7): пока мы держим дескриптор, другой процесс МОЖЕТ писать в
файл и усекать его, но НЕ МОЖЕТ его удалить или переименовать.

Отсюда две половины:

- чего модель НЕ даёт: байтовой неизменяемости. Мы обнаруживаем чужую запись
  и отказываем, но предотвратить её не можем. Изменение той же длины с
  восстановленным mtime не обнаруживается вовсе - это названное остаточное
  ограничение, а не недосмотр. То же верно и без всякого умысла: запись той же
  длины, попавшая в тот же тик часов файловой системы, что и снимок, тоже
  неотличима по st_mtime_ns (спека §10, дополнение по задаче 1.9).
- что модель даёт: подмена пути невозможна. Путь открывается ОДИН раз, дальше
  работа идёт с файловым объектом. Классического TOCTOU "проверили путь, потом
  открыли другой файл" здесь не существует.

Сверка идёт через os.fstat(fd) - через НАШ дескриптор, а не через путь.
Поэтому TOCTOU отсутствует и внутри самой проверки.

Неизменяемым здесь является МАНИФЕСТ, а не байты.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from .model import ENTRY_FILE, TransferManifest

logger = logging.getLogger(__name__)

REASON_SOURCE_CHANGED = "source_changed"
REASON_SOURCE_MISSING = "source_missing"

#: Сколько снимков держим. Снимки - это пути и метаданные, не байты, поэтому
#: потолок дешёвый. Последний плюс все обслуживаемые сохраняются всегда.
RETENTION = 4


class SourceChanged(Exception):
    """Файл изменился между объявлением и чтением."""


class SourceMissing(Exception):
    """Снимка, записи или файла нет."""


@dataclass
class _Snapshot:
    manifest: TransferManifest
    sources: dict[str, Path]
    handles: dict[int, int] = field(default_factory=dict)
    serving: bool = False

    def close(self) -> None:
        for descriptor in self.handles.values():
            try:
                os.close(descriptor)
            except OSError:  # pragma: no cover - закрытие дважды не должно ронять
                logger.debug("дескриптор уже закрыт")
        self.handles.clear()


class SnapshotRegistry:
    """Снимки по transfer_id. Живёт в GUI-потоке и только в нём."""

    def __init__(self) -> None:
        self._snapshots: dict[str, _Snapshot] = {}
        self._order: list[str] = []

    @property
    def transfer_ids(self) -> tuple[str, ...]:
        return tuple(self._order)

    @property
    def serving(self) -> frozenset[str]:
        return frozenset(key for key, snap in self._snapshots.items() if snap.serving)

    def publish(self, manifest: TransferManifest, sources: dict[str, Path]) -> None:
        """Запомнить обещанное. Файлы НЕ открываются - объявление ленивое.

        Повторная публикация уже известного transfer_id ЗАМЕНЯЕТ снимок, а не
        отказывает: именно это уже подразумевал сброс позиции в `_order` ниже.
        Но замена обязана сперва закрыть дескрипторы прежнего снимка - иначе
        файл остаётся заблокирован для удаления до конца жизни процесса
        (спека §15), а прежний объект теряется без единого os.close.
        """
        previous = self._snapshots.get(manifest.transfer_id)
        if previous is not None:
            previous.close()
        self._snapshots[manifest.transfer_id] = _Snapshot(
            manifest=manifest, sources=dict(sources)
        )
        if manifest.transfer_id in self._order:
            self._order.remove(manifest.transfer_id)
        self._order.append(manifest.transfer_id)
        self._evict()

    def read(self, transfer_id: str, entry_index: int, offset: int, length: int) -> bytes:
        """Байты из удерживаемого дескриптора, со сверкой на каждом чтении."""
        snapshot = self._snapshots.get(transfer_id)
        if snapshot is None:
            raise SourceMissing(f"снимок {transfer_id!r} неизвестен или вытеснен")
        if not 0 <= entry_index < len(snapshot.manifest.entries):
            raise SourceMissing(f"записи {entry_index} нет в манифесте")
        entry = snapshot.manifest.entries[entry_index]
        if entry.kind != ENTRY_FILE:
            raise SourceMissing(f"запись {entry.path!r} - не файл")

        descriptor = snapshot.handles.get(entry_index)
        if descriptor is None:
            descriptor = self._open_and_verify(snapshot, entry_index, entry)
            snapshot.handles[entry_index] = descriptor
            snapshot.serving = True

        self._verify_unchanged(descriptor, entry)
        return self._read_at(descriptor, offset, length)

    def release(self, transfer_id: str) -> None:
        snapshot = self._snapshots.pop(transfer_id, None)
        if snapshot is not None:
            snapshot.close()
        if transfer_id in self._order:
            self._order.remove(transfer_id)

    def release_all(self) -> None:
        for transfer_id in list(self._order):
            self.release(transfer_id)

    # ------------------------------------------------------------------ внутреннее

    def _open_and_verify(self, snapshot: _Snapshot, entry_index: int, entry) -> int:
        path = snapshot.sources.get(entry.path)
        if path is None:
            raise SourceMissing(f"для записи {entry.path!r} нет источника")
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        except OSError as error:
            # Полное имя пути в журнал не идёт (спека §15).
            raise SourceMissing(f"не удалось открыть {path.name!r}: {error.strerror}") from error
        try:
            self._verify_unchanged(descriptor, entry)
        except Exception:
            # Не только SourceChanged: любое исключение здесь означает, что
            # дескриптор не будет записан в snapshot.handles и станет
            # недостижимым, если не закрыть его сейчас же.
            os.close(descriptor)
            raise
        return descriptor

    def _verify_unchanged(self, descriptor: int, entry) -> None:
        """Сверка через НАШ дескриптор, не через путь."""
        stat_result = os.fstat(descriptor)
        if stat_result.st_size != entry.size:
            raise SourceChanged(f"размер {entry.path!r} изменился")
        if stat_result.st_mtime_ns != entry.mtime_ns:
            raise SourceChanged(f"время изменения {entry.path!r} изменилось")

    @staticmethod
    def _read_at(descriptor: int, offset: int, length: int) -> bytes:
        """Чтение по смещению через lseek плюс read.

        os.pread был бы уместнее, но на целевой платформе его нет: проверено
        на CPython 3.12.10 под Windows - hasattr(os, "pread") равно False.
        Ветка "pread, если он есть" была бы мёртвым кодом, а мёртвый код здесь
        уже находили мутационные прогоны.

        lseek плюс read безопасны без атомарности потому, что дескриптор
        принадлежит GUI-потоку и только ему: COM-поток до него не дотягивается
        вовсе (граница §7 спецификации, правило 3 boundary-теста).
        """
        os.lseek(descriptor, offset, os.SEEK_SET)
        return os.read(descriptor, length)

    def _evict(self) -> None:
        """Последний снимок и все обслуживаемые остаются всегда.

        Без этого смена буфера обмена во время передачи оборвала бы её - и
        сценарий 1 спецификации перестал бы держаться структурно.
        """
        while len(self._order) > RETENTION:
            for transfer_id in list(self._order):
                if transfer_id == self._order[-1]:
                    continue
                if self._snapshots[transfer_id].serving:
                    continue
                self.release(transfer_id)
                break
            else:
                # Всё оставшееся либо самое новое, либо обслуживается: вытеснять
                # нечего, и превышение потолка здесь законно.
                return


__all__ = [
    "REASON_SOURCE_CHANGED",
    "REASON_SOURCE_MISSING",
    "RETENTION",
    "SnapshotRegistry",
    "SourceChanged",
    "SourceMissing",
]
