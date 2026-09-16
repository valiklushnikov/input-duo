# Передача файлов Windows ↔ macOS — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** дать передачу файлов в обе кросс-платформенные стороны — Mac→Windows (Mac отдаёт) и Windows→Mac (Mac принимает через staging и `⌘V`), — не трогая сетевой протокол, валидацию путей и уже работающий Windows↔Windows.

**Architecture:** ядро transfer/ (protocol, `paths`, `model`, `pipe`, `scanner`, `source`) и `FileTransferService` переиспользуются без изменений. Отправная сторона Mac — одна строка симметрии в `macos_backend.snapshot_from`. Приёмная сторона Mac — новый событийный `MacFileReceiver`, который слушает `FileTransferService.offer_received` (санитизированный манифест) и сам ведёт последовательный цикл `FILE_READ → FILE_CHUNK → запись в staging`, а по завершении вооружает `NSPasteboard` списком `file://` URL. `ChunkPipe`, COM, STA-поток на приёмнике Mac не используются (потолок памяти = один запрос в полёте). pyobjc изолирован в единственном `transfer/macos_pasteboard.py`.

**Tech Stack:** Python 3.14 (`.venv-mac`), PySide6 6.10, pyobjc-framework-Cocoa (только darwin, только `macos_pasteboard.py`), pytest, QSettings.

**Spec:** `docs/superpowers/specs/2026-09-16-macos-file-transfer-design.md` (шлагбаум: `docs/superpowers/records/2026-09-16-macos-file-promise-spike.md`).

## Global Constraints

- **Протокол не меняется:** шесть `FILE_*`, `files/2`, `PROTOCOL_MAJOR` не поднимается. `wire.py`, `model.py`, `paths.py`, `pipe.py`, `scanner.py`, `source.py`, `coordinator.py`, `peer.py`, `windows_files.py`, `windows_com.py` — **не трогать ни строкой**.
- **`paths.py` остаётся Windows-строгим и на macOS.** Манифест на Mac проходит тот же `sanitize_manifest`.
- **Изоляция pyobjc:** только `transfer/macos_pasteboard.py` импортирует AppKit/Foundation/objc. `transfer/macos_files.py` и `transfer/staging.py` — без pyobjc и без `PySide6.QtWidgets`. `transfer/staging.py` — вообще без Qt.
- **Ленивые импорты фабрики:** `create_file_backend` импортирует `macos_files` только внутри darwin-ветки; сборка Windows не тянет `macos_*`, сборка macOS не тянет `windows_files`/`windows_com`/ctypes.
- **Авторизация приёмника:** без предварительной авторизации (Accept для offer либо ранее включённый режим Auto) ни один `FILE_READ` не уходит.
- **Жизненный цикл staging:** каждый transfer — свой каталог `<transfer_id>`; READY переживает новый offer и нормальный выход; incomplete удаляется на cancel/error/startup-recovery; GC по TTL (24 ч) + disk-budget/LRU, best-effort; перед transfer — проверка `free_space >= total_bytes + reserve`. «Новый offer → удалить предыдущий staging» **запрещено**.
- **Потолок памяти приёмника = один чанк** (один `FILE_READ` в полёте).
- **host-only** при вооружении `NSPasteboard` (`NSPasteboardContentsCurrentHostOnly`).
- **Dev/тесты на Mac:** `.venv-mac/bin/python -m pytest`. Packaging (`.app`/подпись/нотаризация) вне scope.
- **Вариант C (ленивый file-url провайдер) не реализуется.**

## File Structure

| Файл | Ответственность | Действие |
|---|---|---|
| `transfer/staging.py` | Каталоги staging: begin/write/finish/abort, disk-space, GC (TTL+LRU), startup recovery. Чистый Python, без Qt/pyobjc | Create |
| `transfer/macos_pasteboard.py` | Единственный pyobjc-модуль подсистемы: host-only вооружение `NSPasteboard` списком `file://` URL | Create |
| `transfer/macos_files.py` | `MacFileReceiver(QObject)`: авторизация → событийный цикл скачивания → staging → вооружение буфера; прогресс/отмена. Qt, без pyobjc | Create |
| `transfer/platform_files.py` | +darwin ветка `create_file_backend` (ленивый импорт) | Modify |
| `clipboard/macos_backend.py` | `snapshot_from` добавляет `local_file_paths` (Mac-отправитель) | Modify |
| `app.py` | darwin-ветка проводки приёмника; настройка ask/auto; согласие в UI; тумблер трея на Mac | Modify |
| `tests/clipboard/test_boundaries.py` | правила границ на `transfer/macos_*`, `transfer/staging.py` | Modify |
| `tests/transfer/test_staging.py` | тесты staging | Create |
| `tests/transfer/test_macos_receiver.py` | тесты `MacFileReceiver` на фейковой связи | Create |
| `tests/clipboard/test_macos_backend.py` | тест симметрии отправителя | Modify |

---

### Task 1: Ранний шлагбаум — host-only `NSPasteboard` с несколькими `file://` URL

Спайк-задача (не TDD): это последняя OS-specific предпосылка цепочки staging→Finder (спец §3). Выполнить **до** написания приёмника.

**Files:**
- Throwaway-скрипт в scratchpad; результат — дописать в `docs/superpowers/records/2026-09-16-macos-file-promise-spike.md`.

- [ ] **Step 1: Написать throwaway-проверку**

```python
# scratchpad/spike_multi_fileurl.py — выбрасывается после записи результата
from AppKit import NSPasteboard, NSPasteboardContentsCurrentHostOnly
from Foundation import NSURL

paths = []
for name in ("duo-a.txt", "duo-b.txt"):
    p = f"/tmp/{name}"
    open(p, "w").write(f"{name} host-only multi-url spike\n")
    paths.append(p)

pb = NSPasteboard.generalPasteboard()
pb.prepareForNewContentsWithOptions_(NSPasteboardContentsCurrentHostOnly)
urls = [NSURL.fileURLWithPath_(p) for p in paths]
ok = pb.writeObjects_(urls)
print("writeObjects ok=", ok)
print("types=", [str(t) for t in (pb.types() or [])])
print("changeCount=", pb.changeCount())
```

- [ ] **Step 2: Запустить и вставить в Finder**

Run: `.venv-mac/bin/python scratchpad/spike_multi_fileurl.py`, затем `⌘V` в пустой папке Finder.
Наблюдать: появились ли **оба** файла; всплывает ли контент на других устройствах Handoff (не должен).

- [ ] **Step 3: Записать результат-шлагбаум**

Дописать в record секцию «Проверка 2: host-only + несколько file:// URL» с фактом:
- вставились ли все корневые элементы одним `writeObjects_([url1, url2, …])`;
- работает ли host-only для файловых URL.

Если Finder берёт только первый URL или host-only ломает файловые URL — **остановиться и эскалировать**: это меняет форму вооружения буфера (Task 4/5) и должно быть решено до кода.

- [ ] **Step 4: Commit record**

```bash
git add docs/superpowers/records/2026-09-16-macos-file-promise-spike.md
git commit -m "Record the host-only multi-file-URL pasteboard checkpoint"
```

---

### Task 2: Mac-отправитель — снимок несёт локальные пути

**Files:**
- Modify: `configurator/src/duo_input/clipboard/macos_backend.py:53-57`
- Test: `configurator/tests/clipboard/test_macos_backend.py`

**Interfaces:**
- Consumes: `local_file_paths(mime_data) -> tuple[str, ...]` (существует, `clipboard/formats.py`), `ClipboardSnapshot(payloads, file_paths=())` (существует, `clipboard/backend.py`).
- Produces: `snapshot_from(mime_data) -> ClipboardSnapshot` теперь заполняет `file_paths`, что через существующий `_offer_files_from` запускает `offer_local_files`.

- [ ] **Step 1: Написать падающий тест**

```python
# tests/clipboard/test_macos_backend.py
from duo_input.clipboard.macos_backend import snapshot_from

def test_snapshot_from_carries_local_file_paths(make_mime):
    # make_mime — фикстура/хелпер, строящий QMimeData с file:// URL
    mime = make_mime(urls=["file:///Users/x/a.txt", "file:///Users/x/dir"])
    snap = snapshot_from(mime)
    assert snap.file_paths  # непустой канал локальных путей
    assert any(p.endswith("a.txt") for p in snap.file_paths)
```

Если хелпера `make_mime` нет — построить `QMimeData` напрямую: `m = QMimeData(); m.setUrls([QUrl("file:///Users/x/a.txt")])` (offscreen Qt, как в остальных тестах пакета).

- [ ] **Step 2: Запустить — упасть**

Run: `.venv-mac/bin/python -m pytest tests/clipboard/test_macos_backend.py::test_snapshot_from_carries_local_file_paths -v`
Expected: FAIL — `snap.file_paths` пуст (сейчас `snapshot_from` не передаёт второй аргумент).

- [ ] **Step 3: Правка симметрии**

```python
# clipboard/macos_backend.py — импорт и строка снимка
from .formats import collect_payloads, local_file_paths  # добавить local_file_paths

def snapshot_from(mime_data) -> ClipboardSnapshot:
    if mime_data is None:
        return ClipboardSnapshot({})
    return ClipboardSnapshot(collect_payloads(mime_data), local_file_paths(mime_data))
```

- [ ] **Step 4: Запустить — пройти + parity**

Run: `.venv-mac/bin/python -m pytest tests/clipboard/test_macos_backend.py -v`
Expected: PASS. Дополнительно убедиться, что существующие тесты `test_macos_backend.py` зелёные (снимок с пустым буфером всё ещё `ClipboardSnapshot({})`).

- [ ] **Step 5: Commit**

```bash
git add clipboard/macos_backend.py tests/clipboard/test_macos_backend.py
git commit -m "Carry local file paths in the macOS clipboard snapshot"
```

---

### Task 3: Staging — каталоги, disk-space, GC, recovery

**Files:**
- Create: `configurator/src/duo_input/transfer/staging.py`
- Test: `configurator/tests/transfer/test_staging.py`

**Interfaces:**
- Consumes: `TransferEntry`, `ENTRY_FILE` (`transfer/model.py`).
- Produces:
  - `StagingArea(root: Path, *, ttl_seconds: float = 86_400, disk_budget_bytes: int = 8 * 2**30, reserve_bytes: int = 256 * 2**20)`
  - `StagingArea.has_room_for(total_bytes: int) -> bool`
  - `StagingArea.begin(transfer_id: str, entries: Sequence[TransferEntry]) -> StagingSession`
  - `StagingArea.recover() -> None` (удалить incomplete-каталоги при старте)
  - `StagingArea.gc(keep: str | None = None) -> None` (TTL + disk-budget/LRU, best-effort)
  - `StagingSession.write(entry_index: int, offset: int, data: bytes) -> None`
  - `StagingSession.finish() -> tuple[Path, ...]` (корневые элементы дерева; помечает READY)
  - `StagingSession.abort() -> None`

Маркеры каталога: `.incomplete` создаётся в `begin`, удаляется в `finish`. READY = каталог без `.incomplete`. Возраст READY — по `stat().st_mtime` каталога, обновляемому в `finish`.

- [ ] **Step 1: Падающие тесты жизненного цикла**

```python
# tests/transfer/test_staging.py
import time
from pathlib import Path
from duo_input.transfer.model import TransferEntry, ENTRY_FILE, ENTRY_DIRECTORY
from duo_input.transfer.staging import StagingArea

def _entries():
    return [
        TransferEntry(path="dir", kind=ENTRY_DIRECTORY, size=0, mtime_ns=0),
        TransferEntry(path="dir/a.txt", kind=ENTRY_FILE, size=5, mtime_ns=0),
        TransferEntry(path="b.txt", kind=ENTRY_FILE, size=3, mtime_ns=0),
    ]

def test_finish_marks_ready_and_returns_roots(tmp_path):
    area = StagingArea(tmp_path)
    s = area.begin("t1", _entries())
    s.write(1, 0, b"hello")
    s.write(2, 0, b"abc")
    roots = s.finish()
    names = sorted(p.name for p in roots)
    assert names == ["b.txt", "dir"]                 # корневые элементы
    assert (tmp_path / "t1" / "dir" / "a.txt").read_bytes() == b"hello"
    assert not (tmp_path / "t1" / ".incomplete").exists()

def test_abort_removes_tree(tmp_path):
    area = StagingArea(tmp_path)
    s = area.begin("t1", _entries())
    s.write(1, 0, b"he")
    s.abort()
    assert not (tmp_path / "t1").exists()

def test_recover_deletes_incomplete_only(tmp_path):
    area = StagingArea(tmp_path)
    s = area.begin("t1", _entries()); s.write(1, 0, b"hello"); s.write(2, 0, b"abc"); s.finish()
    area.begin("t2", _entries())          # оставлен incomplete (упал/убит)
    area.recover()
    assert (tmp_path / "t1").exists()      # READY жив
    assert not (tmp_path / "t2").exists()  # incomplete снесён

def test_gc_deletes_ready_older_than_ttl_but_keeps_current(tmp_path):
    area = StagingArea(tmp_path, ttl_seconds=0.0)
    a = area.begin("t1", _entries()); a.write(1,0,b"hello"); a.write(2,0,b"abc"); a.finish()
    b = area.begin("t2", _entries()); b.write(1,0,b"hello"); b.write(2,0,b"abc"); b.finish()
    time.sleep(0.01)
    area.gc(keep="t2")
    assert not (tmp_path / "t1").exists()  # старше TTL → снесён
    assert (tmp_path / "t2").exists()      # keep защищён

def test_has_room_for_uses_reserve(tmp_path):
    area = StagingArea(tmp_path, reserve_bytes=0)
    assert area.has_room_for(1) is True
    huge = 2**60
    assert area.has_room_for(huge) is False
```

- [ ] **Step 2: Запустить — упасть**

Run: `.venv-mac/bin/python -m pytest tests/transfer/test_staging.py -v`
Expected: FAIL — модуль `staging` не существует.

- [ ] **Step 3: Реализация staging.py**

```python
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
            target = self._dir / Path(entry.path)
            if entry.kind == ENTRY_FILE:
                target.parent.mkdir(parents=True, exist_ok=True)
            else:
                target.mkdir(parents=True, exist_ok=True)

    @property
    def directory(self) -> Path:
        return self._dir

    def write(self, entry_index: int, offset: int, data: bytes) -> None:
        entry = self._entries[entry_index]
        target = self._dir / Path(entry.path)
        with open(target, "r+b" if target.exists() and offset else "wb") as fh:
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
            return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

        survivors.sort(key=lambda c: c.stat().st_mtime)  # старые первыми
        total = sum(size_of(c) for c in survivors)
        while total > self._budget and survivors:
            victim = survivors.pop(0)
            total -= size_of(victim)
            shutil.rmtree(victim, ignore_errors=True)
```

- [ ] **Step 4: Запустить — пройти**

Run: `.venv-mac/bin/python -m pytest tests/transfer/test_staging.py -v`
Expected: PASS (все шесть). При необходимости добавить `tests/transfer/__init__.py`.

- [ ] **Step 5: Commit**

```bash
git add transfer/staging.py tests/transfer/test_staging.py
git commit -m "Add staging area with TTL/LRU GC and startup recovery"
```

---

### Task 4: `macos_pasteboard.py` — host-only вооружение буфера

**Files:**
- Create: `configurator/src/duo_input/transfer/macos_pasteboard.py`
- Test: `configurator/tests/transfer/test_macos_pasteboard.py` (лёгкий, платформо-guarded)

**Interfaces:**
- Produces: `arm(paths: Sequence[Path | str]) -> int` — кладёт host-only `file://` URL корневых элементов, возвращает `changeCount`; удерживает ссылку на владельца (как `_live_provider` в буфере). `PasteboardArmError` при отказе `writeObjects_`.

Форма вооружения зафиксирована Task 1 (несколько URL одним `writeObjects_`). Если Task 1 показал иное — этот модуль отражает найденную форму.

- [ ] **Step 1: Тест-заглушка списка URL (без AppKit)**

Логика построения списка тестируется отдельно от AppKit: вынести чистую функцию `_to_file_urls(paths) -> list[str]`.

```python
# tests/transfer/test_macos_pasteboard.py
import sys
import pytest
from pathlib import Path

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="pyobjc only on darwin")

def test_to_file_urls_builds_absolute_file_scheme():
    from duo_input.transfer.macos_pasteboard import _to_file_urls
    urls = _to_file_urls([Path("/tmp/a.txt"), "/tmp/dir"])
    assert urls == ["file:///tmp/a.txt", "file:///tmp/dir"]
```

- [ ] **Step 2: Запустить — упасть**

Run: `.venv-mac/bin/python -m pytest tests/transfer/test_macos_pasteboard.py -v`
Expected: FAIL — модуль не существует.

- [ ] **Step 3: Реализация**

```python
"""Единственное место подсистемы передачи файлов, где мы говорим с AppKit.

Только host-only вооружение NSPasteboard списком file:// URL уже
существующих (staged) файлов. Ленивости здесь нет: файлы на диске, Finder
копирует их на ⌘V (спайк records/2026-09-16-macos-file-promise-spike.md).
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from AppKit import NSPasteboard, NSPasteboardContentsCurrentHostOnly
from Foundation import NSURL

logger = logging.getLogger(__name__)


class PasteboardArmError(Exception):
    """NSPasteboard отказался принять file:// URL."""


def _to_file_urls(paths: Sequence[Path | str]) -> list[str]:
    return [Path(p).absolute().as_uri() for p in paths]


def arm(paths: Sequence[Path | str]) -> int:
    pb = NSPasteboard.generalPasteboard()
    pb.prepareForNewContentsWithOptions_(NSPasteboardContentsCurrentHostOnly)
    urls = [NSURL.fileURLWithPath_(str(Path(p).absolute())) for p in paths]
    if not pb.writeObjects_(urls):
        raise PasteboardArmError("NSPasteboard.writeObjects вернул false")
    return int(pb.changeCount())
```

- [ ] **Step 4: Запустить — пройти**

Run: `.venv-mac/bin/python -m pytest tests/transfer/test_macos_pasteboard.py -v`
Expected: PASS на darwin (skip на другой платформе).

- [ ] **Step 5: Commit**

```bash
git add transfer/macos_pasteboard.py tests/transfer/test_macos_pasteboard.py
git commit -m "Add host-only NSPasteboard arming for staged files"
```

---

### Task 5: `MacFileReceiver` — событийный цикл скачивания

**Files:**
- Create: `configurator/src/duo_input/transfer/macos_files.py`
- Test: `configurator/tests/transfer/test_macos_receiver.py`

**Interfaces:**
- Consumes: `Message`, `MessageType`, `MAX_FILE_CHUNK_BYTES`, `CAPABILITY_FILES` (`clipboard/wire.py`); `TransferManifest`, `ENTRY_FILE` (`transfer/model.py`); `StagingArea` (Task 3); `arm(paths)` (Task 4, ленивый импорт внутри метода вооружения).
- Produces: `MacFileReceiver(QObject, staging: StagingArea, pasteboard_arm=arm, parent=None)` с:
  - методы: `attach_link(link)`, `set_peer_capabilities(frozenset[str])`, `handle_offer(manifest)`, `authorize(accepted: bool)`, `handle_message(message)`, `cancel()`, `stop()`
  - сигналы: `authorization_needed(object)`, `transfer_started(object)`, `transfer_progress("qlonglong","qlonglong")`, `transfer_completed()`, `transfer_failed(str)`, `transfer_cancelled()`

Модель состояний: `IDLE → AWAITING_AUTH → DOWNLOADING → READY`. Один `FILE_READ` в полёте; `read_id` — собственный счётчик. `handle_message` реагирует только на `FILE_CHUNK`/`FILE_ERROR`, совпадающие с текущим `read_id` (остальное — чужое/сендерское, игнор). Запись чанка в staging на потоке Qt (чанки — сотни КБ; при деградации p99 GUI-тика — вынести запись в поток, как названный запасной выход спеки milestone 1 §7).

- [ ] **Step 1: Падающие тесты на фейковой связи**

```python
# tests/transfer/test_macos_receiver.py
from pathlib import Path
import pytest
from duo_input.clipboard.wire import Message, MessageType, CAPABILITY_FILES
from duo_input.transfer.model import TransferManifest, TransferEntry, ENTRY_FILE, ENTRY_DIRECTORY
from duo_input.transfer.staging import StagingArea
from duo_input.transfer.macos_files import MacFileReceiver

class FakeLink:
    def __init__(self):
        self.sent = []
        from PySide6.QtCore import QObject, Signal
    def send(self, message):
        self.sent.append(message)
        return True

def _manifest():
    return TransferManifest(
        transfer_id="t1",
        entries=[
            TransferEntry(path="a.txt", kind=ENTRY_FILE, size=5, mtime_ns=0),
        ],
        total_bytes=5, skipped=[], drop_effect=1,
    )

@pytest.fixture
def receiver(tmp_path):
    calls = {"armed": None}
    r = MacFileReceiver(StagingArea(tmp_path), pasteboard_arm=lambda paths: calls.__setitem__("armed", list(paths)))
    r.set_peer_capabilities(frozenset({CAPABILITY_FILES}))
    link = _RecordingLink()
    r.attach_link(link)
    return r, link, calls

def test_authorize_false_sends_no_read(receiver):
    r, link, _ = receiver
    r.handle_offer(_manifest())
    r.authorize(False)
    assert all(m.type is not MessageType.FILE_READ for m in link.sent)

def test_authorized_download_writes_staging_and_arms(receiver, tmp_path):
    r, link, calls = receiver
    r.handle_offer(_manifest())
    r.authorize(True)
    # первый FILE_READ ушёл
    read = next(m for m in link.sent if m.type is MessageType.FILE_READ)
    rid = read.header["read_id"]
    # отвечаем полным чанком
    r.handle_message(Message(MessageType.FILE_CHUNK,
        {"transfer_id": "t1", "entry_index": 0, "offset": 0, "read_id": rid}, b"hello"))
    assert (tmp_path / "t1" / "a.txt").read_bytes() == b"hello"
    assert calls["armed"] and calls["armed"][0].name == "a.txt"

def test_truncated_chunk_fails_transfer(receiver):
    r, link, calls = receiver
    failed = []
    r.transfer_failed.connect(failed.append)
    r.handle_offer(_manifest())
    r.authorize(True)
    read = next(m for m in link.sent if m.type is MessageType.FILE_READ)
    r.handle_message(Message(MessageType.FILE_CHUNK,
        {"transfer_id": "t1", "entry_index": 0, "offset": 0, "read_id": read.header["read_id"]}, b"he"))
    assert failed and calls["armed"] is None

def test_no_room_declines(receiver, monkeypatch):
    r, link, _ = receiver
    monkeypatch.setattr(r._staging, "has_room_for", lambda total: False)
    failed = []
    r.transfer_failed.connect(failed.append)
    r.handle_offer(_manifest())
    r.authorize(True)
    assert failed and all(m.type is not MessageType.FILE_READ for m in link.sent)
```

Добавить хелпер `_RecordingLink` (QObject с `disconnected` Signal и `send`), по образцу фейков в `tests/transfer/`/`device/emulator.py`. Если в репозитории уже есть фейковая связь для файловых тестов — переиспользовать её.

- [ ] **Step 2: Запустить — упасть**

Run: `.venv-mac/bin/python -m pytest tests/transfer/test_macos_receiver.py -v`
Expected: FAIL — модуль `macos_files` не существует.

- [ ] **Step 3: Реализация `macos_files.py`**

```python
"""Приёмник Windows→Mac: offer → авторизация → скачивание в staging → ⌘V.

Событийный, на потоке Qt. Без ChunkPipe, COM и STA: Finder не тянет байты —
тянем мы, последовательно, один FILE_READ в полёте (потолок памяти = один
чанк). pyobjc здесь не импортируется: вооружение буфера — в macos_pasteboard.
"""
from __future__ import annotations

import itertools
import logging
from enum import Enum, auto

from PySide6.QtCore import QObject, Signal

from ..clipboard.wire import CAPABILITY_FILES, MAX_FILE_CHUNK_BYTES, Message, MessageType
from .model import ENTRY_FILE, TransferManifest
from .staging import StagingArea

logger = logging.getLogger(__name__)


class _State(Enum):
    IDLE = auto()
    AWAITING_AUTH = auto()
    DOWNLOADING = auto()
    READY = auto()


class MacFileReceiver(QObject):
    authorization_needed = Signal(object)
    transfer_started = Signal(object)
    transfer_progress = Signal("qlonglong", "qlonglong")
    transfer_completed = Signal()
    transfer_failed = Signal(str)
    transfer_cancelled = Signal()

    def __init__(self, staging: StagingArea, pasteboard_arm=None, parent=None) -> None:
        super().__init__(parent)
        self._staging = staging
        self._arm = pasteboard_arm
        self._link = None
        self._peer_caps: frozenset[str] = frozenset()
        self._state = _State.IDLE
        self._manifest: TransferManifest | None = None
        self._session = None
        self._file_indices: list[int] = []
        self._cursor = 0            # позиция в _file_indices
        self._offset = 0            # смещение внутри текущего файла
        self._received = 0
        self._read_ids = itertools.count(1)
        self._read_id: int | None = None

    # --- проводка
    def attach_link(self, link) -> None:
        self._link = link

    def set_peer_capabilities(self, caps) -> None:
        self._peer_caps = frozenset(caps)

    def stop(self) -> None:
        self._abort_session()
        self._state = _State.IDLE

    # --- offer/авторизация
    def handle_offer(self, manifest: TransferManifest) -> None:
        # READY-staging прошлого transfer не трогаем: на него может ссылаться буфер.
        self._manifest = manifest
        self._state = _State.AWAITING_AUTH
        self.authorization_needed.emit(manifest)

    def authorize(self, accepted: bool) -> None:
        if self._state is not _State.AWAITING_AUTH or self._manifest is None:
            return
        if not accepted:
            self._state = _State.IDLE
            self.transfer_cancelled.emit()
            return
        manifest = self._manifest
        if not self._staging.has_room_for(manifest.total_bytes):
            self._fail("no_disk_space")
            return
        self._session = self._staging.begin(manifest.transfer_id, manifest.entries)
        self._file_indices = [
            i for i, e in enumerate(manifest.entries) if e.kind == ENTRY_FILE and e.size > 0
        ]
        self._cursor = 0
        self._offset = 0
        self._received = 0
        self._state = _State.DOWNLOADING
        self.transfer_started.emit(manifest)
        self._send(Message(MessageType.TRANSFER_BEGIN,
                           {"transfer_id": manifest.transfer_id, "session_id": manifest.transfer_id}, b""))
        self._pump()

    # --- цикл
    def _pump(self) -> None:
        """Запросить следующий кусок либо, если файлов больше нет, завершить."""
        if self._cursor >= len(self._file_indices):
            self._complete()
            return
        entry_index = self._file_indices[self._cursor]
        entry = self._manifest.entries[entry_index]
        length = min(MAX_FILE_CHUNK_BYTES, entry.size - self._offset)
        self._read_id = next(self._read_ids)
        self._send(Message(MessageType.FILE_READ, {
            "transfer_id": self._manifest.transfer_id,
            "entry_index": entry_index,
            "offset": self._offset,
            "length": length,
            "read_id": self._read_id,
        }, b""))

    def handle_message(self, message: Message) -> None:
        if self._state is not _State.DOWNLOADING:
            return
        if message.type is MessageType.FILE_CHUNK:
            self._on_chunk(message)
        elif message.type is MessageType.FILE_ERROR:
            self._on_error(message)

    def _matches(self, message: Message) -> bool:
        h = message.header
        return (self._read_id is not None
                and h.get("read_id") == self._read_id
                and h.get("transfer_id") == self._manifest.transfer_id
                and h.get("entry_index") == self._file_indices[self._cursor]
                and h.get("offset") == self._offset)

    def _on_chunk(self, message: Message) -> None:
        if not self._matches(message):
            return
        entry_index = self._file_indices[self._cursor]
        entry = self._manifest.entries[entry_index]
        expected = min(MAX_FILE_CHUNK_BYTES, entry.size - self._offset)
        blob = message.blob
        if len(blob) > expected:
            logger.warning("чанк крупнее запрошенного — отброшен")
            return
        if len(blob) < expected:
            # запрос не выходит за размер файла → короткий ответ = усечённый источник
            self._fail("truncated")
            return
        self._session.write(entry_index, self._offset, blob)
        self._read_id = None
        self._offset += len(blob)
        self._received += len(blob)
        self.transfer_progress.emit(self._received, self._manifest.total_bytes)
        if self._offset >= entry.size:
            self._cursor += 1
            self._offset = 0
        self._pump()

    def _on_error(self, message: Message) -> None:
        if self._matches(message):
            reason = message.header.get("reason")
            self._fail(reason if isinstance(reason, str) else "file_error")

    def _complete(self) -> None:
        roots = self._session.finish()
        self._send(Message(MessageType.TRANSFER_END,
                           {"transfer_id": self._manifest.transfer_id,
                            "session_id": self._manifest.transfer_id, "status": "completed"}, b""))
        if self._arm is not None:
            try:
                self._arm(list(roots))
            except Exception as error:  # noqa: BLE001 — не рушить приложение
                logger.warning("буфер не принял файлы: %r", error)
                self._fail("pasteboard_refused")
                return
        self._state = _State.READY
        self._staging.gc(keep=self._manifest.transfer_id)
        self.transfer_completed.emit()

    # --- завершение
    def cancel(self) -> None:
        if self._state is _State.DOWNLOADING:
            self._send(Message(MessageType.TRANSFER_END,
                               {"transfer_id": self._manifest.transfer_id,
                                "session_id": self._manifest.transfer_id, "status": "cancelled"}, b""))
            self._abort_session()
            self._state = _State.IDLE
            self.transfer_cancelled.emit()

    def _fail(self, reason: str) -> None:
        if self._manifest is not None and self._link is not None:
            self._send(Message(MessageType.TRANSFER_END,
                               {"transfer_id": self._manifest.transfer_id,
                                "session_id": self._manifest.transfer_id, "status": "failed"}, b""))
        self._abort_session()
        self._state = _State.IDLE
        self.transfer_failed.emit(reason)

    def _abort_session(self) -> None:
        if self._session is not None:
            self._session.abort()
            self._session = None
        self._read_id = None

    def _send(self, message: Message) -> None:
        if self._link is not None and CAPABILITY_FILES in self._peer_caps:
            self._link.send(message)
```

> Примечание: `TRANSFER_BEGIN`/`TRANSFER_END` используют `session_id = transfer_id` для простоты; если Windows-отправитель (`_answer_read`) требует иной семантики `session_id` — сверить с `service.py` при реализации и подставить `uuid4().hex`, сохранив соответствие в `TRANSFER_END`.

- [ ] **Step 4: Запустить — пройти**

Run: `.venv-mac/bin/python -m pytest tests/transfer/test_macos_receiver.py -v`
Expected: PASS. Добавить тесты: несколько файлов; многочанковый файл (`size > MAX_FILE_CHUNK_BYTES`, через маленький monkeypatch константы или файл нужного размера); `cancel()` в середине шлёт `TRANSFER_END{cancelled}` и удаляет incomplete; чужой `read_id` игнорируется.

- [ ] **Step 5: Commit**

```bash
git add transfer/macos_files.py tests/transfer/test_macos_receiver.py
git commit -m "Add the event-driven macOS file receiver"
```

---

### Task 6: Фабрика — darwin-ветка

**Files:**
- Modify: `configurator/src/duo_input/transfer/platform_files.py`
- Test: `configurator/tests/transfer/test_platform_files.py`

**Interfaces:**
- Produces: `create_file_backend(parent=None)` на darwin возвращает `MacFileReceiver` (с `StagingArea` под `~/Library/Caches/duo-input/incoming/`); win32 — прежний `WindowsFileClipboardBackend`; иное — `UnsupportedPlatformError`.

- [ ] **Step 1: Падающий тест**

```python
# tests/transfer/test_platform_files.py
import sys
import pytest
from duo_input.transfer.platform_files import create_file_backend, UnsupportedPlatformError

@pytest.mark.skipif(sys.platform != "darwin", reason="darwin only")
def test_darwin_returns_mac_receiver():
    from duo_input.transfer.macos_files import MacFileReceiver
    assert isinstance(create_file_backend(), MacFileReceiver)
```

- [ ] **Step 2: Запустить — упасть**

Run: `.venv-mac/bin/python -m pytest tests/transfer/test_platform_files.py -v`
Expected: FAIL — `UnsupportedPlatformError('darwin')`.

- [ ] **Step 3: Правка фабрики**

```python
def create_file_backend(parent=None):
    if sys.platform == "win32":
        from .windows_files import WindowsFileClipboardBackend
        return WindowsFileClipboardBackend(parent)
    if sys.platform == "darwin":
        from pathlib import Path
        from .macos_files import MacFileReceiver
        from .macos_pasteboard import arm
        from .staging import StagingArea
        root = Path.home() / "Library" / "Caches" / "duo-input" / "incoming"
        return MacFileReceiver(StagingArea(root), pasteboard_arm=arm, parent=parent)
    raise UnsupportedPlatformError(sys.platform)
```

- [ ] **Step 4: Запустить — пройти**

Run: `.venv-mac/bin/python -m pytest tests/transfer/test_platform_files.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add transfer/platform_files.py tests/transfer/test_platform_files.py
git commit -m "Select the macOS receiver from the file-backend factory"
```

---

### Task 7: Границы — `transfer/macos_*` и `staging.py`

**Files:**
- Modify: `configurator/tests/clipboard/test_boundaries.py`

**Interfaces:**
- Consumes: `_package_modules`, `_has_native_import`, `_imported_modules`, `TRANSFER_PACKAGE`, `SRC_ROOT` (существуют в тесте).

- [ ] **Step 1: Новые падающие правила**

```python
def test_only_transfer_macos_pasteboard_touches_pyobjc():
    offenders = [
        path.name for path in _package_modules()
        if path.parent.name == "transfer"
        and path.name != "macos_pasteboard.py"
        and _has_native_import(path)
    ]
    assert offenders == [], (
        "pyobjc/AppKit разрешён только в transfer/macos_pasteboard.py — "
        f"нарушители: {offenders}"
    )

def test_macos_files_is_free_of_pyobjc_and_qtwidgets():
    from pathlib import Path
    module = TRANSFER_PACKAGE / "macos_files.py"
    names = _imported_modules(module)  # при необходимости — с корнем/пакетом, как в соседних тестах
    assert not _has_native_import(module)
    assert not any(n.startswith("PySide6.QtWidgets") for n in names)

def test_staging_is_pure_python():
    module = TRANSFER_PACKAGE / "staging.py"
    names = _imported_modules(module)
    assert not _has_native_import(module)
    assert not any(n.split(".")[0] == "PySide6" for n in names)
```

Если сигнатуры хелперов (`_imported_modules` требует `root`/`package`) отличаются — вызвать их так же, как соседние тесты `test_the_com_module_never_imports_pyside` (там пример вызова с `SRC_ROOT, "duo_input"`).

- [ ] **Step 2: Запустить — упасть (или сразу пройти)**

Run: `.venv-mac/bin/python -m pytest tests/clipboard/test_boundaries.py -k "macos or staging" -v`
Expected: если `macos_files.py`/`staging.py` чисты (Tasks 3–5), тесты **проходят сразу** — это защита от регрессий. Достаточно убедиться, что они не падают ложно (например, `_has_native_import` не считает `from .macos_pasteboard import arm` нативным импортом — это относительный импорт соседа, не AppKit).

- [ ] **Step 3: Commit**

```bash
git add tests/clipboard/test_boundaries.py
git commit -m "Extend import boundaries to the macOS transfer adapters"
```

---

### Task 8: Проводка в `app.py` — настройка ask/auto, согласие, тумблер трея

**Files:**
- Modify: `configurator/src/duo_input/app.py:370-446` (файловая подсистема), `app.py:237-260` (set_enabled/set_files_enabled)
- Test: `configurator/tests/test_app_runtime.py` (или существующий тест конфигурации рантайма)

**Interfaces:**
- Consumes: `create_file_backend` (Task 6), `MacFileReceiver` сигналы (Task 5), `QSettings` (`self._settings`), `FileTransferService` (сендер-роль Mac→Windows), `self._window.clipboard_page` (прогресс/отмена — уже есть).
- Настройка: `self._settings.value("clipboard/incoming_files", "ask", type=str)` ∈ {"ask","auto"}.

- [ ] **Step 1: Тест production call path (darwin)**

```python
# tests/test_app_runtime.py (или рядом)
import sys, pytest
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="darwin receiver")

def test_files_enabled_creates_mac_receiver(app_under_test):
    app = app_under_test
    app._settings.setValue("clipboard/files_enabled", True)
    app.set_files_enabled(True)
    from duo_input.transfer.macos_files import MacFileReceiver
    assert isinstance(app.file_backend, MacFileReceiver)

def test_auto_mode_authorizes_without_prompt(app_under_test, monkeypatch):
    app = app_under_test
    app._settings.setValue("clipboard/incoming_files", "auto")
    app.set_files_enabled(True)
    prompted = []
    monkeypatch.setattr(app, "_prompt_file_authorization", lambda m: prompted.append(m))
    # эмулировать offer:
    from tests.transfer.helpers import a_manifest  # или построить локально
    app.file_backend.handle_offer(a_manifest())
    assert prompted == []   # auto не спрашивает
```

Адаптировать под существующую фикстуру рантайма (как проверяется Windows-подсистема сегодня — тест «файловая подсистема действительно создаётся в configure_runtime»).

- [ ] **Step 2: Запустить — упасть**

Run: `.venv-mac/bin/python -m pytest tests/test_app_runtime.py -k mac -v`
Expected: FAIL — darwin-ветка проводки отсутствует (сейчас `configure_runtime` строит Windows-специфичную проводку и/или `create_file_backend` не вызывается на darwin).

- [ ] **Step 3: Реализация darwin-ветки проводки**

В методе, поднимающем файловую подсистему (`app.py:370+`), развести платформы:

```python
backend = create_file_backend(coordinator)  # уже возвращает нужный объект per-platform
transfer = FileTransferService(coordinator)  # сендер-роль нужна на обеих платформах
# ... общие подключения transfer.* к page (send_failed, entries_skipped) ...
transfer.set_peer_capabilities(coordinator.peer_capabilities)
clipboard_backend.snapshot_taken.connect(self._offer_files_from)  # Mac-отправитель заработал в Task 2

if sys.platform == "darwin":
    receiver = backend  # MacFileReceiver
    # прогресс/отмена — те же хуки страницы, что у Windows
    receiver.transfer_started.connect(lambda m: page.set_transfer_progress(0, m.total_bytes))
    receiver.transfer_progress.connect(page.set_transfer_progress)
    receiver.transfer_completed.connect(page.clear_transfer)
    receiver.transfer_cancelled.connect(page.clear_transfer)
    receiver.transfer_failed.connect(lambda _r: page.clear_transfer())
    receiver.transfer_failed.connect(lambda r: page.add_event(f"передача файлов не удалась: {r}"))
    page.cancel_requested.connect(receiver.cancel)
    # приёмник берёт санитизированный манифест у сервиса и слушает связь за чанками
    transfer.offer_received.connect(receiver.handle_offer)
    receiver.authorization_needed.connect(self._on_file_authorization_needed)
    self._file_receiver = receiver
    # чанки/ошибки приёмнику — тем же message_received, что и сервису:
    # сервис их безвредно отбрасывает (у него нет reads в полёте на Mac).
    if coordinator.link is not None:
        coordinator.link.message_received.connect(receiver.handle_message)
        receiver.attach_link(coordinator.link)
    # при пере-подключении связи — привязать и приёмник (в _attach_file_link)
else:
    # существующая Windows-проводка без изменений
    from duo_input.transfer.windows_files import ServiceCallbackGateway
    ...
```

Согласие и авто:

```python
def _on_file_authorization_needed(self, manifest) -> None:
    mode = self._settings.value("clipboard/incoming_files", "ask", type=str)
    if mode == "auto":
        self._file_receiver.authorize(True)
        return
    self._prompt_file_authorization(manifest)

def _prompt_file_authorization(self, manifest) -> None:
    from PySide6.QtWidgets import QMessageBox
    total_mb = manifest.total_bytes / (1024 * 1024)
    box = QMessageBox(self._window)
    box.setWindowTitle(self.tr("Входящие файлы"))
    box.setText(self.tr(f"Другой компьютер хочет передать {len(manifest.entries)} "
                        f"объект(ов) ({total_mb:.1f} МБ)."))
    box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
    accepted = box.exec() == QMessageBox.Ok
    self._file_receiver.authorize(accepted)
```

`_attach_file_link` расширить: на darwin после `transfer.attach_link(link)` также `link.message_received.connect(self._file_receiver.handle_message)` и `self._file_receiver.attach_link(link)` (и отключать старую связь симметрично).

Настройка ask/auto — прочитать в UI (страница настроек/трей). Минимум: значение по умолчанию `"ask"`, переключатель добавить в существующую страницу настроек рядом с тумблером «Передача файлов» (radio: «Спрашивать перед загрузкой» / «Загружать автоматически»), пишущий `clipboard/incoming_files`.

- [ ] **Step 4: Запустить — пройти + весь пакет**

Run: `.venv-mac/bin/python -m pytest tests/ -q`
Expected: PASS. Особое внимание — тесты, что тумблер трея «Передача файлов» поднимает/останавливает подсистему на darwin (`stop()` приёмника вызывается в `_stop_files`).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app_runtime.py ui/
git commit -m "Wire the macOS receiver, consent prompt, and ask/auto setting into the app"
```

---

### Task 9: Двухмашинная приёмка и запись

**Files:**
- Create: `docs/superpowers/records/2026-09-16-macos-file-transfer-acceptance.md`

Ручные проверки на Windows-PC + этом Mac (unit-тесты их не заменяют).

- [ ] **Step 1: Mac→Windows**

`⌘C` в Finder → `Ctrl+V` в Explorer: один файл; дерево папок; Unicode-имя; крупный файл (>1 ГБ). Наблюдать прогресс/отмену на Windows-приёмнике. Записать факты.

- [ ] **Step 2: Windows→Mac (ask)**

`Ctrl+C` в Explorer → на Mac появляется промпт «Принять» → прогресс/отмена в Duo Input → `⌘V` в Finder создаёт копии. Проверить: повторный `⌘V` в другую папку из того же READY staging работает; **новый offer не инвалидирует** предыдущий READY (скопировать A, принять; скопировать B, принять; вставить A — должно работать, пока не сработал GC/TTL).

- [ ] **Step 3: Windows→Mac (auto)**

Переключить настройку в «Загружать автоматически»: `Ctrl+C` на Windows материализует staging без промпта; `⌘V` вставляет.

- [ ] **Step 4: Границы и память**

Проверить host-only (контент не всплывает на других устройствах Handoff). Прогнать медленный тест памяти на ГиБ-файле (рост RSS ограничен). Записать пик RSS.

- [ ] **Step 5: Commit record**

```bash
git add docs/superpowers/records/2026-09-16-macos-file-transfer-acceptance.md
git commit -m "Record the two-machine Windows↔macOS file-transfer acceptance"
```

---

## Self-Review

**Spec coverage:**
- §2 Mac-отправитель → Task 2. §3 offer→Accept→staging→⌘V → Tasks 5, 8. Согласие ask/auto → Task 8. Ранний host-only checkpoint → Task 1. Жизненный цикл staging (READY переживает offer/выход, incomplete на cancel/error/recovery, GC TTL+LRU, disk-space) → Task 3, интеграция в Task 5. §4 модули и изоляция pyobjc → Tasks 3–6, границы Task 7. §5 изменения в существующем коде → Tasks 2, 6, 8. §6 вариант C не реализуется → нет задачи (корректно). §7 протокол не меняется → Global Constraints, ни одна задача не трогает `wire`/`model`/`paths`/`source`. §8 тестирование (модульные, память, границы, production call path, двухмашинная приёмка) → Tasks 3–9.
- Инвариант «авторизация до FILE_READ» → Task 5 (`authorize`), Task 8 (auto/ask). Потолок памяти один чанк → Task 5 (`length = min(MAX_FILE_CHUNK_BYTES, …)`, один `read_id`). host-only → Task 4.

**Placeholder scan:** конкретный код в каждом шаге; «адаптировать под фикстуру» в Task 8 указывает на существующий тест-образец, а не на пропуск.

**Type consistency:** `StagingArea.begin/has_room_for/gc/recover`, `StagingSession.write(entry_index, offset, data)/finish()->tuple[Path,...]/abort()`, `arm(paths)->int`, `MacFileReceiver(staging, pasteboard_arm, parent)` с методами `handle_offer/authorize/handle_message/cancel/attach_link/set_peer_capabilities/stop` — используются согласованно между Tasks 3–8. `MessageType.FILE_READ/FILE_CHUNK/TRANSFER_BEGIN/TRANSFER_END/FILE_ERROR` и `MAX_FILE_CHUNK_BYTES`, `CAPABILITY_FILES` — из `clipboard/wire.py` без изменений.

## Открытые сверки при реализации (не блокеры)

- Точная семантика `session_id` у Windows-отправителя (`service._answer_read`) — Task 5 Step 3 примечание.
- Требует ли Windows-отправитель `TRANSFER_BEGIN` перед первым `FILE_READ` (иначе — не слать).
- Форма конструктора `TransferManifest`/`TransferEntry` в тестах (позиционные/ключевые поля) — сверить с `transfer/model.py` при написании фикстур.
- Сигнатуры хелперов `test_boundaries.py` (`_imported_modules`, `_has_native_import`) — вызвать как соседние тесты.
