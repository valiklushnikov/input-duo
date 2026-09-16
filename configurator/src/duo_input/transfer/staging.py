"""Каталоги staging на приёмнике: скачанное дерево, готовое к ⌘V.

Чистый Python — без Qt и без pyobjc. Факт вставки в Finder недетектируем,
поэтому READY-каталоги не удаляются ни по новому offer, ни по нормальному
выходу: NSPasteboard/Finder может ещё сослаться на их file://. Удаляется только
incomplete (cancel/error/recovery), а READY чистится best-effort GC по TTL и
disk-budget/LRU.
"""
from __future__ import annotations

import logging
import shutil
from collections.abc import Sequence
from pathlib import Path

from .model import ENTRY_FILE, TransferEntry

logger = logging.getLogger(__name__)
_INCOMPLETE = ".incomplete"


class StagingSession:
    def __init__(self, directory: Path, entries: Sequence[TransferEntry]) -> None:
        self._dir = directory
        self._entries = list(entries)
        # создать всё дерево каталогов заранее; файлы пишутся по мере скачивания
        for entry in self._entries:
            target = self._contained(entry.path)
            if entry.kind == ENTRY_FILE:
                target.parent.mkdir(parents=True, exist_ok=True)
                # Пустой файл байтов не качает (size == 0 отфильтрован из цикла
                # чтения), поэтому материализуем его здесь — иначе он бы не
                # существовал на диске, а finish() всё равно вооружил бы его
                # путь в буфер обмена. touch создаёт пустой файл; последующий
                # write() к непустому файлу откроет его в r+b и перезапишет.
                target.touch(exist_ok=True)
            else:
                target.mkdir(parents=True, exist_ok=True)

    def _contained(self, rel_path: str) -> Path:
        # defense-in-depth: staging — сырой write-примитив; никогда не писать вне _dir
        target = self._dir / Path(rel_path)
        base = self._dir.resolve()
        resolved = target.resolve()
        if base != resolved and base not in resolved.parents:
            raise ValueError(f"entry path escapes staging directory: {rel_path!r}")
        return target

    @property
    def directory(self) -> Path:
        return self._dir

    def write(self, entry_index: int, offset: int, data: bytes) -> None:
        entry = self._entries[entry_index]
        target = self._contained(entry.path)
        with open(target, "r+b" if target.exists() else "wb") as fh:
            fh.seek(offset)
            fh.write(data)

    def finish(self) -> tuple[Path, ...]:
        marker = self._dir / _INCOMPLETE
        if marker.exists():
            marker.unlink()
        self._dir.touch(exist_ok=True)  # обновить mtime как момент READY
        roots = []
        seen = set()
        for entry in self._entries:
            first = Path(entry.path).parts[0]
            if first not in seen:
                seen.add(first)
                roots.append(self._dir / first)
        return tuple(roots)

    def abort(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)


class StagingArea:
    def __init__(
        self,
        root: Path,
        *,
        ttl_seconds: float = 86_400,
        disk_budget_bytes: int = 8 * 2**30,
        reserve_bytes: int = 256 * 2**20,
    ) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl_seconds
        self._budget = disk_budget_bytes
        self._reserve = reserve_bytes

    def has_room_for(self, total_bytes: int) -> bool:
        try:
            free = shutil.disk_usage(self._root).free
        except OSError:
            return False
        return free >= total_bytes + self._reserve

    def begin(self, transfer_id: str, entries: Sequence[TransferEntry]) -> StagingSession:
        directory = self._root / transfer_id
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True)
        (directory / _INCOMPLETE).touch()
        return StagingSession(directory, entries)

    def _children(self) -> list[Path]:
        return [p for p in self._root.iterdir() if p.is_dir()]

    def recover(self) -> None:
        for child in self._children():
            if (child / _INCOMPLETE).exists():
                shutil.rmtree(child, ignore_errors=True)

    def gc(self, keep: str | None = None) -> None:
        import time

        now = time.time()
        ready = [
            c for c in self._children()
            if not (c / _INCOMPLETE).exists() and c.name != keep
        ]
        # 1) TTL
        survivors = []
        for child in ready:
            try:
                age = now - child.stat().st_mtime
            except OSError:
                continue
            if age > self._ttl:
                shutil.rmtree(child, ignore_errors=True)
            else:
                survivors.append(child)
        # 2) disk budget — вытеснять самые старые READY (LRU/age)
        def size_of(path: Path) -> int:
            total_size = 0
            for f in path.rglob("*"):
                try:
                    if f.is_file():
                        total_size += f.stat().st_size
                except OSError:
                    continue  # файл исчез посреди скана — best-effort, не падать
            return total_size

        survivors.sort(key=lambda c: c.stat().st_mtime)  # старые первыми
        total = sum(size_of(c) for c in survivors)
        while total > self._budget and survivors:
            victim = survivors.pop(0)
            total -= size_of(victim)
            shutil.rmtree(victim, ignore_errors=True)
