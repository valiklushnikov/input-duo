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
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .model import ENTRY_FILE, TransferManifest
from .fileprovider_perf import PerfEmitter

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


def _name(entry) -> str:
    """Только имя записи: текст исключения уходит в журнал (спека §15).

    Относительный путь - это та же структура каталогов пользователя, что и
    абсолютный, только короче, поэтому и его в тексте быть не должно.
    """
    return PurePosixPath(entry.path).name


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

    def __init__(self, perf: PerfEmitter | None = None) -> None:
        self._snapshots: dict[str, _Snapshot] = {}
        self._order: list[str] = []
        clock_domain = (
            "windows_python_monotonic" if sys.platform == "win32" else "python_monotonic"
        )
        self._perf = perf or PerfEmitter(logger, clock_domain)
        self._active_source_reads = 0

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

    def read(
        self,
        transfer_id: str,
        entry_index: int,
        offset: int,
        length: int,
        *,
        read_id: int | None = None,
    ) -> bytes:
        """Байты из удерживаемого дескриптора, со сверкой на каждом чтении."""
        correlation = {
            "transfer_id": transfer_id,
            "entry_index": entry_index,
            "read_id": read_id,
        }
        self._perf.emit("snapshot_lookup_begin", **correlation)
        snapshot = self._snapshots.get(transfer_id)
        if snapshot is None:
            raise SourceMissing(f"снимок {transfer_id!r} неизвестен или вытеснен")
        if not 0 <= entry_index < len(snapshot.manifest.entries):
            raise SourceMissing(f"записи {entry_index} нет в манифесте")
        entry = snapshot.manifest.entries[entry_index]
        if entry.kind != ENTRY_FILE:
            raise SourceMissing(f"запись {_name(entry)!r} - не файл")

        if not 0 <= offset < entry.size:
            # Отдельно от SourceMissing: это ошибка запроса, а не источника.
            # Без проверки смещение 10**30 уходило в lseek и поднимало
            # OverflowError мимо всех протокольных веток.
            raise ValueError("смещение за пределами файла")
        length = min(length, entry.size - offset)
        self._perf.emit("snapshot_lookup_end", **correlation)
        descriptor = snapshot.handles.get(entry_index)
        reused = descriptor is not None
        self._perf.emit(
            "source_open_begin", fd_reused=reused, **correlation
        )
        try:
            if descriptor is None:
                descriptor = self._open_and_verify(snapshot, entry_index, entry)
                snapshot.handles[entry_index] = descriptor
                snapshot.serving = True

            self._verify_unchanged(descriptor, entry)
        except (SourceChanged, SourceMissing, OSError) as error:
            status = (
                REASON_SOURCE_CHANGED
                if isinstance(error, SourceChanged)
                else REASON_SOURCE_MISSING
            )
            self._perf.emit(
                "source_open_end",
                status=status,
                fd_reused=reused,
                seek_required=True,
                **correlation,
            )
            raise
        self._perf.emit(
            "source_open_end",
            status="ok",
            fd_reused=reused,
            seek_required=True,
            **correlation,
        )
        self._active_source_reads += 1
        self._perf.emit(
            "source_read_begin",
            offset=offset,
            length=length,
            active_source_reads=self._active_source_reads,
            **correlation,
        )
        try:
            payload = self._read_at(descriptor, offset, length)
        except OSError:
            self._perf.emit(
                "source_read_end", status=REASON_SOURCE_MISSING, **correlation
            )
            raise
        finally:
            self._active_source_reads -= 1
        self._perf.emit(
            "source_read_end", status="ok", bytes=len(payload), **correlation
        )
        return payload

    def release(self, transfer_id: str) -> None:
        snapshot = self._snapshots.pop(transfer_id, None)
        if snapshot is not None:
            snapshot.close()
        if transfer_id in self._order:
            self._order.remove(transfer_id)

    def close_descriptors(self, transfer_id: str) -> None:
        """Сессия закончилась - дескрипторы отпустить, снимок оставить.

        В отличие от release(), снимок из реестра НЕ удаляется: повторный
        Ctrl+V - не новая передача, а новые чтения того же манифеста (спека
        §537, сценарий 3 - §1016-1018, и §1013 про восстановление связи).
        Отдельного отказа "дублирующийся transfer_id" не существует - вторая
        сессия того же transfer_id обязана снова сработать.

        Держать дескрипторы открытыми между вставками означало бы держать
        файл заблокированным для удаления на весь срок жизни предложения
        буфера обмена - ровно то, из-за чего задача 1.9 уже платила раунд
        (см. спека §15, на которую ссылается publish()). read() открывает
        дескриптор заново лениво через _open_and_verify, так что закрытие
        здесь не только безопасно, но и даёт побочную пользу: файл,
        изменённый между двумя вставками, будет пойман как SourceChanged
        при повторном чтении, а не отдан по устаревшему дескриптору.
        """
        snapshot = self._snapshots.get(transfer_id)
        if snapshot is not None:
            snapshot.close()
            snapshot.serving = False

    def release_all(self) -> None:
        for transfer_id in list(self._order):
            self.release(transfer_id)

    def close_all_descriptors(self) -> None:
        """Конец сессии для всех снимков сразу (разрыв связи): дескрипторы
        закрыты, файлы больше не заблокированы, снимки остаются PUBLISHED.
        Повторные чтения того же transfer_id после восстановления связи
        откроют файл заново через _open_and_verify (спека §9, сценарий 2)."""
        for transfer_id in list(self._order):
            self.close_descriptors(transfer_id)

    # ------------------------------------------------------------------ внутреннее

    def _open_and_verify(self, snapshot: _Snapshot, entry_index: int, entry) -> int:
        path = snapshot.sources.get(entry.path)
        if path is None:
            raise SourceMissing(f"для записи {_name(entry)!r} нет источника")
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
            raise SourceChanged(f"размер {_name(entry)!r} изменился")
        if stat_result.st_mtime_ns != entry.mtime_ns:
            raise SourceChanged(f"время изменения {_name(entry)!r} изменилось")

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
