# File Transfer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать общему буферу обмена файлы и папки: `Ctrl+C` в Проводнике на одном компьютере, `Ctrl+V` на другом, включая гигабайтные файлы, с прогрессом и отменой от системы.

**Architecture:** В буфер получателя кладётся объект COM, который для Проводника неотличим от настоящих файлов; содержимое тянется с первой машины кусками в момент вставки. Оболочка обращается к объекту из своих потоков, а сеть живёт в цикле событий Qt, поэтому между ними ставится единственный мост — и он единственное место в проекте, где есть потоки.

**Tech Stack:** Python 3.12, PySide6 6.10.1 (`QtCore`, `QtNetwork`), `ctypes` (без `pywin32` и `comtypes`), pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-09-04-file-transfer-design.md`

**Выверенный спайком код:** `configurator/tests/clipboard/spike_com.py` (189 строк, базовый объект COM на `ctypes`), `spike_dataobject.py` (391 строка, `IDataObject` с описанием файлов и содержимым), `spike_filecontents.py`, `spike_manual_paste.py`. Это одноразовый код пробы, но он ПРОВЕРЕН на настоящей оболочке Windows и на живом `Ctrl+V`. Переносить в production его надо осмысленно, а не копированием: убрать печать в консоль, добавить типы и docstring по-русски, покрыть тестами.

## Global Constraints

- Платформа — **только Windows**. macOS отдельный этап, ветвлений по macOS в этом плане нет.
- `duo_input/clipboard/**` (включая новый подпакет `files/`) **не импортирует `QtWidgets` и `QtGui`** — только `QtCore` и `QtNetwork`. Это проверяется существующим тестом `configurator/tests/clipboard/test_boundaries.py`.
- **Новых зависимостей нет.** Только `ctypes` и стандартная библиотека. `pywin32` и `comtypes` спайком признаны ненужными.
- **Потоки допустимы ТОЛЬКО в `clipboard/files/bridge.py`.** Любой другой модуль пакета остаётся однопоточным. Это проверяется тестом в задаче 2.
- Потолок манифеста — **65 536 записей** на одну операцию копирования.
- Размер куска чтения — **262 144 байта (256 КиБ)**, ровно столько запрашивает оболочка (замерено спайком).
- Имя формата манифеста — `application/x-duo-input-files`.
- Содержимое файлов **не пишется на диск у отправителя** и не копируется во временные файлы у получателя.
- Комментарии и docstrings — **по-русски**. Файлы в **UTF-8 без BOM** (в предыдущей работе один файл был испорчен двойной перекодировкой — проверяйте чтением как UTF-8, редактор может показывать правильно испорченное).
- Один файл тестов запускается из каталога `configurator/`: `../.venv/Scripts/python.exe -m pytest <путь> -q`
- ПОЛНЫЙ набор запускается **ИЗ КОРНЯ репозитория**: `.venv/Scripts/python.exe -m pytest configurator/tests -q -c configurator/pyproject.toml`. Исходное состояние — **1034 passed, 4 skipped**. После каждой задачи тестов больше, падений ноль.
- Четыре теста в `tests/integration/` работают с настоящим устройством на COM-порту. Ошибка «port did not open» означает занятый порт (например запущенное приложение), а не регресс.
- Каждое защитное условие покрывается тестом, который **падает при снятии этого условия**; по каждому проводится мутационная проверка, результат приводится в отчёте.
- Сообщения коммитов по-английски, каждое заканчивается строкой:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`

## Структура файлов

```
configurator/src/duo_input/clipboard/files/__init__.py       пустой маркер пакета
configurator/src/duo_input/clipboard/files/manifest.py       что копируют: записи и сериализация
configurator/src/duo_input/clipboard/files/bridge.py         мост поток COM <-> цикл событий Qt
configurator/src/duo_input/clipboard/files/com_types.py      GUID, константы, базовый объект на ctypes
configurator/src/duo_input/clipboard/files/stream.py         IStream на один файл: Read, Seek, Stat
configurator/src/duo_input/clipboard/files/data_object.py    IDataObject для буфера обмена
configurator/src/duo_input/clipboard/files/source.py         сторона-источник: чтение локальных файлов
configurator/src/duo_input/clipboard/files/sink.py           сторона-получатель: связывает объект и сеть
```

Тесты — `configurator/tests/clipboard/files/`, по файлу на модуль.

---

### Task 1: Манифест

**Files:**
- Create: `configurator/src/duo_input/clipboard/files/__init__.py`
- Create: `configurator/src/duo_input/clipboard/files/manifest.py`
- Create: `configurator/tests/clipboard/files/test_manifest.py`

**Interfaces:**
- Consumes: ничего.
- Produces: `FileEntry` — датакласс `path: str`, `size: int`, `is_directory: bool`, `modified_ns: int`; `FileManifest` — датакласс `entries: tuple[FileEntry, ...]` со свойствами `total_bytes: int`, `file_count: int`, `directory_count: int`, методами `to_dict() -> dict` и classmethod `from_dict(raw: dict) -> FileManifest`; функция `build_manifest(paths: Sequence[Path]) -> FileManifest`; константы `MAX_ENTRIES = 65_536`, `FILES_MIME = "application/x-duo-input-files"`; исключение `ManifestError`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Манифест: что именно копируют, до того как хоть один байт уехал."""

from __future__ import annotations

import pytest

from duo_input.clipboard.files.manifest import (
    FILES_MIME,
    MAX_ENTRIES,
    FileEntry,
    FileManifest,
    ManifestError,
    build_manifest,
)


def test_a_single_file_becomes_one_entry(tmp_path):
    target = tmp_path / "заметка.txt"
    target.write_bytes(b"hello")

    manifest = build_manifest([target])

    assert manifest.file_count == 1
    assert manifest.directory_count == 0
    assert manifest.total_bytes == 5
    assert manifest.entries[0].path == "заметка.txt"
    assert manifest.entries[0].is_directory is False


def test_a_directory_brings_its_contents_with_relative_paths(tmp_path):
    root = tmp_path / "папка"
    (root / "вложенная").mkdir(parents=True)
    (root / "один.txt").write_bytes(b"a")
    (root / "вложенная" / "два.txt").write_bytes(b"bb")

    manifest = build_manifest([root])
    paths = sorted(entry.path for entry in manifest.entries)

    assert paths == ["папка", "папка\\вложенная", "папка\\вложенная\\два.txt", "папка\\один.txt"]
    assert manifest.total_bytes == 3


def test_an_empty_directory_is_kept(tmp_path):
    """Пустая папка — часть того, что копировали, и должна воссоздаться."""
    root = tmp_path / "пустая"
    root.mkdir()

    manifest = build_manifest([root])

    assert manifest.entries[0].is_directory is True
    assert manifest.directory_count == 1
    assert manifest.file_count == 0


def test_several_files_keep_their_own_names(tmp_path):
    first = tmp_path / "первый.bin"
    second = tmp_path / "второй.bin"
    first.write_bytes(b"1")
    second.write_bytes(b"22")

    manifest = build_manifest([first, second])

    assert sorted(entry.path for entry in manifest.entries) == ["второй.bin", "первый.bin"]
    assert manifest.total_bytes == 3


def test_a_manifest_survives_a_round_trip(tmp_path):
    target = tmp_path / "файл.txt"
    target.write_bytes(b"data")
    manifest = build_manifest([target])

    assert FileManifest.from_dict(manifest.to_dict()) == manifest


def test_too_many_entries_are_refused_rather_than_sent(tmp_path):
    """Потолок существует, чтобы манифест не превращался в мегабайты."""
    entries = tuple(
        FileEntry(path=f"файл{index}.txt", size=1, is_directory=False, modified_ns=0)
        for index in range(MAX_ENTRIES + 1)
    )

    with pytest.raises(ManifestError, match="65"):
        FileManifest(entries=entries).validate()


def test_from_dict_refuses_a_wrong_type_instead_of_coercing():
    """Манифест приходит по сети: доверять его форме нельзя."""
    with pytest.raises(ManifestError):
        FileManifest.from_dict(
            {"entries": [{"path": 123, "size": 1, "is_directory": False, "modified_ns": 0}]}
        )


def test_from_dict_refuses_a_missing_field():
    with pytest.raises(ManifestError):
        FileManifest.from_dict({"entries": [{"path": "a.txt", "size": 1}]})


def test_the_mime_name_is_stable():
    """Имя формата — часть протокола: его смена ломает совместимость."""
    assert FILES_MIME == "application/x-duo-input-files"
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/files/test_manifest.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'duo_input.clipboard.files'`

- [ ] **Step 3: Написать реализацию**

`configurator/src/duo_input/clipboard/files/__init__.py` — пустой файл.

```python
"""Что именно копируют - список записей, уходящий второй машине при Ctrl+C.

Манифест едет ВМЕСТО содержимого: он нужен получателю сразу, чтобы объявить в
буфере правильный набор файлов, а сами байты тянутся потом, при вставке. В этом
и состоит ленивость: скопированный и не вставленный гигабайт машину не покидает.

Пути внутри манифеста относительные и разделены обратным слэшем - именно в
таком виде их ждёт оболочка Windows, когда воссоздаёт структуру каталогов.
Пустые папки в манифест попадают наравне с файлами: они часть того, что
пользователь копировал, и должны появиться на другой стороне.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

#: Имя формата, под которым манифест едет в объявлении. Часть протокола.
FILES_MIME = "application/x-duo-input-files"

#: Больше этого за одну операцию копирования не берём.
MAX_ENTRIES = 65_536


class ManifestError(Exception):
    """Манифест, которого не могло получиться из исправной операции копирования."""


@dataclass(frozen=True)
class FileEntry:
    """Один файл или одна папка из того, что копировали."""

    path: str
    size: int
    is_directory: bool
    modified_ns: int


@dataclass(frozen=True)
class FileManifest:
    """Полный список того, что копировали, с итогами для показа человеку."""

    entries: tuple[FileEntry, ...]

    @property
    def file_count(self) -> int:
        return sum(1 for entry in self.entries if not entry.is_directory)

    @property
    def directory_count(self) -> int:
        return sum(1 for entry in self.entries if entry.is_directory)

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries if not entry.is_directory)

    def validate(self) -> FileManifest:
        if len(self.entries) > MAX_ENTRIES:
            raise ManifestError(
                f"записей {len(self.entries)}, потолок {MAX_ENTRIES}: "
                "такую операцию копирования мы не берём"
            )
        return self

    def to_dict(self) -> dict:
        return {
            "entries": [
                {
                    "path": entry.path,
                    "size": entry.size,
                    "is_directory": entry.is_directory,
                    "modified_ns": entry.modified_ns,
                }
                for entry in self.entries
            ]
        }

    @classmethod
    def from_dict(cls, raw: dict) -> FileManifest:
        """Разобрать манифест, пришедший от второй машины.

        Типы проверяются, а не приводятся: это данные из сети, и `str(x)`
        принял бы что угодно, превратив `None` в строку "None".
        """
        try:
            if not isinstance(raw, dict):
                raise TypeError("манифест должен быть объектом")
            rows = raw["entries"]
            if not isinstance(rows, list):
                raise TypeError("entries должен быть списком")

            entries = []
            for row in rows:
                if not isinstance(row, dict):
                    raise TypeError("каждая запись должна быть объектом")
                path = row["path"]
                size = row["size"]
                is_directory = row["is_directory"]
                modified_ns = row["modified_ns"]
                if not isinstance(path, str) or not path:
                    raise TypeError("path должен быть непустой строкой")
                if type(size) is not int or size < 0:
                    raise TypeError("size должен быть неотрицательным целым")
                if type(is_directory) is not bool:
                    raise TypeError("is_directory должен быть логическим")
                if type(modified_ns) is not int:
                    raise TypeError("modified_ns должен быть целым")
                entries.append(
                    FileEntry(
                        path=path,
                        size=size,
                        is_directory=is_directory,
                        modified_ns=modified_ns,
                    )
                )
            return cls(entries=tuple(entries)).validate()
        except (KeyError, TypeError) as error:
            raise ManifestError(f"манифест неполон: {error}") from error


def build_manifest(paths: Sequence[Path]) -> FileManifest:
    """Обойти то, что выделил пользователь, и описать это списком записей."""
    entries: list[FileEntry] = []

    for target in paths:
        base = target.parent
        if target.is_dir():
            entries.append(_entry_for(target, base))
            for child in sorted(target.rglob("*")):
                entries.append(_entry_for(child, base))
        else:
            entries.append(_entry_for(target, base))

    return FileManifest(entries=tuple(entries)).validate()


def _entry_for(target: Path, base: Path) -> FileEntry:
    is_directory = target.is_dir()
    stat = target.stat()
    return FileEntry(
        path=str(target.relative_to(base)),
        size=0 if is_directory else stat.st_size,
        is_directory=is_directory,
        modified_ns=stat.st_mtime_ns,
    )


__all__ = [
    "FILES_MIME",
    "MAX_ENTRIES",
    "FileEntry",
    "FileManifest",
    "ManifestError",
    "build_manifest",
]
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/files/test_manifest.py -q`
Expected: PASS, 9 тестов

- [ ] **Step 5: Мутационная проверка**

Снимите проверку потолка в `validate` (`if len(self.entries) > MAX_ENTRIES`) — тест `test_too_many_entries_are_refused_rather_than_sent` обязан упасть. Верните, убедитесь что проходит.
Замените `if not isinstance(path, str)` на `path = str(path)` — тест `test_from_dict_refuses_a_wrong_type_instead_of_coercing` обязан упасть. Верните.
Уберите добавление самой папки (`entries.append(_entry_for(target, base))` в ветке `is_dir`) — тест `test_an_empty_directory_is_kept` обязан упасть. Верните.
Результаты приведите в отчёте.

- [ ] **Step 6: Полный прогон и коммит**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q -c configurator/pyproject.toml` из корня. Ожидается 1034 + 9 = 1043 passed, 4 skipped.

```bash
git add configurator/src/duo_input/clipboard/files configurator/tests/clipboard/files
git commit -m "Describe what was copied before a single byte moves"
```

---

### Task 2: Мост между потоком COM и циклом событий Qt

Самая рискованная задача плана, поэтому она идёт второй, а не последней. Всё остальное на ней стоит.

**Files:**
- Create: `configurator/src/duo_input/clipboard/files/bridge.py`
- Create: `configurator/tests/clipboard/files/test_bridge.py`

**Interfaces:**
- Consumes: ничего.
- Produces: `RequestBridge(QObject)` — сигнал `request_posted(object)`; методы `ask(payload: dict, timeout_ms: int) -> bytes` (вызывается ИЗ ЧУЖОГО ПОТОКА, блокирует его), `answer(token: int, data: bytes) -> None` и `fail(token: int, reason: str) -> None` (вызываются из потока Qt), `close() -> None`; исключение `BridgeError`; константа `DEFAULT_TIMEOUT_MS = 30_000`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Мост: единственное место проекта, где есть потоки.

Здесь проверяется не удобство, а то, ради чего мост существует: чужой поток
блокируется и просыпается ровно тогда, когда должен, и никогда не остаётся
висеть навсегда.
"""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from duo_input.clipboard.files.bridge import BridgeError, RequestBridge


def test_a_request_from_another_thread_gets_its_answer(qapp):
    bridge = RequestBridge()
    seen: list[dict] = []

    def serve(request):
        seen.append(request)
        bridge.answer(request["token"], b"ответ")

    bridge.request_posted.connect(serve)

    result: list[bytes] = []

    def worker():
        result.append(bridge.ask({"what": "кусок"}, timeout_ms=5000))

    thread = threading.Thread(target=worker)
    thread.start()
    deadline = time.monotonic() + 5
    while thread.is_alive() and time.monotonic() < deadline:
        qapp.processEvents()
    thread.join(timeout=5)

    assert result == [b"ответ"]
    assert seen[0]["what"] == "кусок"


def test_two_threads_do_not_get_each_others_answers(qapp):
    """Ответ адресуется своему запросу, а не первому попавшемуся."""
    bridge = RequestBridge()

    def serve(request):
        bridge.answer(request["token"], request["what"].encode("utf-8"))

    bridge.request_posted.connect(serve)

    results: dict[str, bytes] = {}

    def worker(name: str):
        results[name] = bridge.ask({"what": name}, timeout_ms=5000)

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("один", "два", "три")]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 5
    while any(t.is_alive() for t in threads) and time.monotonic() < deadline:
        qapp.processEvents()
    for thread in threads:
        thread.join(timeout=5)

    assert results == {"один": "один".encode("utf-8"), "два": "два".encode("utf-8"), "три": "три".encode("utf-8")}


def test_a_failure_wakes_the_waiting_thread(qapp):
    """Отказ сети обязан будить поток, а не оставлять его висеть."""
    bridge = RequestBridge()

    def serve(request):
        bridge.fail(request["token"], "связи нет")

    bridge.request_posted.connect(serve)

    error: list[str] = []

    def worker():
        try:
            bridge.ask({"what": "кусок"}, timeout_ms=5000)
        except BridgeError as exc:
            error.append(str(exc))

    thread = threading.Thread(target=worker)
    thread.start()
    deadline = time.monotonic() + 5
    while thread.is_alive() and time.monotonic() < deadline:
        qapp.processEvents()
    thread.join(timeout=5)

    assert error and "связи нет" in error[0]


def test_closing_the_bridge_wakes_everyone_waiting(qapp):
    """Остановка подсистемы не должна оставлять заблокированных потоков."""
    bridge = RequestBridge()
    bridge.request_posted.connect(lambda request: None)  # никто не отвечает

    errors: list[str] = []

    def worker():
        try:
            bridge.ask({"what": "кусок"}, timeout_ms=30_000)
        except BridgeError as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    time.sleep(0.2)
    qapp.processEvents()

    bridge.close()

    deadline = time.monotonic() + 5
    while any(t.is_alive() for t in threads) and time.monotonic() < deadline:
        qapp.processEvents()
    for thread in threads:
        thread.join(timeout=5)

    assert len(errors) == 3
    assert all(not t.is_alive() for t in threads)


def test_a_timeout_releases_the_thread(qapp):
    """Молчание не должно держать поток дольше срока."""
    bridge = RequestBridge()
    bridge.request_posted.connect(lambda request: None)

    error: list[str] = []

    def worker():
        try:
            bridge.ask({"what": "кусок"}, timeout_ms=300)
        except BridgeError as exc:
            error.append(str(exc))

    thread = threading.Thread(target=worker)
    thread.start()
    deadline = time.monotonic() + 5
    while thread.is_alive() and time.monotonic() < deadline:
        qapp.processEvents()
    thread.join(timeout=5)

    assert error and "не дожд" in error[0].lower()


def test_a_late_answer_after_close_is_ignored(qapp):
    """Запоздавший ответ не должен воскрешать закрытый запрос."""
    bridge = RequestBridge()
    tokens: list[int] = []
    bridge.request_posted.connect(lambda request: tokens.append(request["token"]))

    def worker():
        try:
            bridge.ask({"what": "кусок"}, timeout_ms=300)
        except BridgeError:
            pass

    thread = threading.Thread(target=worker)
    thread.start()
    deadline = time.monotonic() + 5
    while thread.is_alive() and time.monotonic() < deadline:
        qapp.processEvents()
    thread.join(timeout=5)

    bridge.answer(tokens[0], b"поздно")  # не должно ничего сломать
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/files/test_bridge.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'duo_input.clipboard.files.bridge'`

- [ ] **Step 3: Написать реализацию**

```python
"""Единственное место проекта, где встречаются два мира.

Оболочка Windows обращается к нам из СВОИХ потоков и ждёт ответа синхронно.
Сеть живёт в цикле событий Qt и синхронно отвечать не умеет. Мост соединяет их
самым скучным способом из возможных: чужой поток кладёт запрос и засыпает, цикл
событий его подбирает, отвечает, и поток просыпается.

Почему это того стоит: без моста пришлось бы выносить передачу файлов в
отдельный процесс со своим запуском, обновлением и журналом. Спайк показал, что
COM сам создаёт рабочие потоки внутри нашего процесса, и цикл событий при этом
не встаёт - остаётся только аккуратно передать данные между ними.

Правило, которое держит остальной проект однопоточным: НИКАКОЙ код вне этого
модуля не трогает данные из потока COM напрямую. Всё проходит здесь.

Ни один поток не должен остаться висеть навсегда - поэтому у каждого ожидания
есть срок, а закрытие моста будит всех, кто ещё ждёт.
"""

from __future__ import annotations

import itertools
import threading

from PySide6.QtCore import QObject, Qt, Signal

#: Сколько ждать ответа, если вызывающий не сказал иначе.
DEFAULT_TIMEOUT_MS = 30_000


class BridgeError(Exception):
    """Ответа не будет: отказ, срок вышел или мост закрыт."""


class _Pending:
    """Одно ожидание: событие, ответ и причина отказа."""

    __slots__ = ("event", "data", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.data: bytes | None = None
        self.error: str | None = None


class RequestBridge(QObject):
    """Просьба из чужого потока, ответ из цикла событий Qt."""

    request_posted = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._pending: dict[int, _Pending] = {}
        self._tokens = itertools.count(1)
        self._closed = False

    # ------------------------------------------------------- чужой поток

    def ask(self, payload: dict, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> bytes:
        """Задать вопрос и ЗАБЛОКИРОВАТЬ вызывающий поток до ответа.

        Вызывается из потока COM. Сигнал испускается с очередью соединения,
        поэтому обработчик выполнится в потоке, которому принадлежит мост.
        """
        with self._lock:
            if self._closed:
                raise BridgeError("мост закрыт")
            token = next(self._tokens)
            pending = _Pending()
            self._pending[token] = pending

        request = dict(payload)
        request["token"] = token
        self.request_posted.emit(request)

        if not pending.event.wait(timeout_ms / 1000):
            with self._lock:
                self._pending.pop(token, None)
            raise BridgeError(f"не дождались ответа за {timeout_ms} мс")

        with self._lock:
            self._pending.pop(token, None)

        if pending.error is not None:
            raise BridgeError(pending.error)
        return pending.data if pending.data is not None else b""

    # --------------------------------------------------------- поток Qt

    def answer(self, token: int, data: bytes) -> None:
        """Отдать ответ ожидающему потоку. Неизвестный номер - молча ничей."""
        with self._lock:
            pending = self._pending.get(token)
        if pending is None:
            return
        pending.data = data
        pending.event.set()

    def fail(self, token: int, reason: str) -> None:
        """Разбудить ожидающий поток отказом."""
        with self._lock:
            pending = self._pending.get(token)
        if pending is None:
            return
        pending.error = reason
        pending.event.set()

    def close(self) -> None:
        """Разбудить всех, кто ещё ждёт, и больше никого не принимать."""
        with self._lock:
            self._closed = True
            waiting = list(self._pending.values())
            self._pending.clear()
        for pending in waiting:
            pending.error = "мост закрыт"
            pending.event.set()


__all__ = ["DEFAULT_TIMEOUT_MS", "BridgeError", "RequestBridge"]
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/files/test_bridge.py -q`
Expected: PASS, 6 тестов

Если тесты виснут: проверьте, что сигнал испускается ДО ожидания события, а не после, и что обработчик вызывает `answer` с тем же номером, который пришёл в запросе.

- [ ] **Step 5: Мутационная проверка — здесь она важнее, чем где-либо**

Гонки не воспроизводятся по требованию, поэтому каждая защита проверяется отдельно:
1. Уберите `pending.event.set()` из `fail` — тест `test_a_failure_wakes_the_waiting_thread` обязан упасть по сроку. Верните.
2. Уберите пробуждение в `close` (цикл `for pending in waiting`) — тест `test_closing_the_bridge_wakes_everyone_waiting` обязан упасть. Верните.
3. Замените в `answer` поиск по номеру на выдачу первому попавшемуся ожиданию — тест `test_two_threads_do_not_get_each_others_answers` обязан упасть. Верните.
4. Уберите проверку срока (`if not pending.event.wait(...)`), заменив на безусловное ожидание — тест `test_a_timeout_releases_the_thread` обязан упасть (повиснет). Верните.
Результаты приведите в отчёте.

- [ ] **Step 6: Тест правила про потоки**

Добавьте в `configurator/tests/clipboard/files/test_bridge.py`:

```python
def test_threads_live_only_in_the_bridge():
    """Потоки разрешены ровно в одном модуле пакета.

    Правило, которое ничто не проверяет, перестаёт действовать через месяц.
    """
    import ast
    from pathlib import Path

    package = Path("src", "duo_input", "clipboard")
    offenders = set()
    for path in package.rglob("*.py"):
        if path.name == "bridge.py":
            continue
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders.update(
                    path.name for alias in node.names if alias.name == "threading"
                )
            elif isinstance(node, ast.ImportFrom) and node.module == "threading":
                offenders.add(path.name)

    assert offenders == set(), f"потоки за пределами моста: {offenders}"
```

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/files/test_bridge.py -q`
Expected: PASS, 7 тестов

- [ ] **Step 7: Полный прогон и коммит**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q -c configurator/pyproject.toml` из корня. Ожидается 1050 passed, 4 skipped.

```bash
git add configurator/src/duo_input/clipboard/files/bridge.py configurator/tests/clipboard/files/test_bridge.py
git commit -m "Bridge the shell's threads to the event loop, and wake everyone on close"
```

---

### Task 3: Описания COM на ctypes

Перенос выверенного спайком кода в production-вид. Не изобретайте заново: `configurator/tests/clipboard/spike_com.py` проверен на настоящей оболочке.

**Files:**
- Create: `configurator/src/duo_input/clipboard/files/com_types.py`
- Create: `configurator/tests/clipboard/files/test_com_types.py`

**Interfaces:**
- Consumes: ничего.
- Produces: `GUID` (структура) и `guid(text: str) -> GUID`; константы `S_OK = 0`, `E_FAIL`, `E_NOINTERFACE`, `E_INVALIDARG`, `DV_E_FORMATETC`, `DV_E_TYMED`, `DV_E_LINDEX`, `DV_E_DVASPECT`, `STG_E_MEDIUMFULL`; идентификаторы `IID_IUnknown`, `IID_IMarshal`, `IID_IDataObject`, `IID_IEnumFORMATETC`, `IID_ISequentialStream`, `IID_IStream`; класс `ComObject` с полями `pointer: int`, методами `enable_free_threaded_marshaler()`, `on_final_release()` и атрибутами класса `IIDS`, `METHODS`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Основание для объектов COM: счётчик ссылок и опрос интерфейсов."""

from __future__ import annotations

import ctypes

from duo_input.clipboard.files.com_types import (
    E_NOINTERFACE,
    S_OK,
    ComObject,
    GUID,
    IID_IUnknown,
    guid,
)

IID_TEST = guid("{11111111-2222-3333-4444-555555555555}")


class _Probe(ComObject):
    IIDS = (IID_TEST,)
    METHODS = ()


def test_a_guid_round_trips_through_text():
    assert str(guid("{00000000-0000-0000-C000-000000000046}")).upper().strip("{}") == (
        "00000000-0000-0000-C000-000000000046"
    )


def test_asking_for_a_supported_interface_succeeds():
    probe = _Probe()
    out = ctypes.c_void_p()

    result = probe._query_interface(None, ctypes.byref(IID_TEST), ctypes.byref(out))

    assert result == S_OK
    assert out.value == probe.pointer


def test_asking_for_iunknown_succeeds():
    """Каждый объект COM обязан отвечать на IUnknown, иначе он не объект."""
    probe = _Probe()
    out = ctypes.c_void_p()

    assert probe._query_interface(None, ctypes.byref(IID_IUnknown), ctypes.byref(out)) == S_OK


def test_asking_for_an_unsupported_interface_is_refused():
    probe = _Probe()
    other = guid("{99999999-8888-7777-6666-555555555555}")
    out = ctypes.c_void_p()

    result = probe._query_interface(None, ctypes.byref(other), ctypes.byref(out))

    assert result == E_NOINTERFACE
    assert out.value is None


def test_asking_for_an_interface_raises_the_reference_count():
    """Правило COM: выдал указатель - увеличил счётчик."""
    probe = _Probe()
    before = probe._refcount
    out = ctypes.c_void_p()

    probe._query_interface(None, ctypes.byref(IID_TEST), ctypes.byref(out))

    assert probe._refcount == before + 1


def test_the_object_is_released_exactly_once():
    """Освобождение вызывается один раз: второе означало бы падение оболочки."""
    released: list[int] = []

    class _Counting(_Probe):
        def on_final_release(self) -> None:
            released.append(1)

    probe = _Counting()
    probe._add_ref(None)
    probe._release(None)
    assert released == []
    probe._release(None)
    assert released == [1]
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/files/test_com_types.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'duo_input.clipboard.files.com_types'`

- [ ] **Step 3: Перенести код из спайка**

Возьмите `configurator/tests/clipboard/spike_com.py` как основу и приведите в production-вид:

1. Docstring модуля напишите заново, по-русски, объяснив ПОЧЕМУ так: почему vtable собирается вручную из `WINFUNCTYPE`, почему объект хранит ссылки на свои реализации (иначе сборщик мусора их уничтожит, и оболочка вызовет освобождённую память), и что такое free-threaded marshaler — он уводит вызовы оболочки в рабочие потоки вместо цикла событий Qt.
2. Уберите всю печать в консоль и параметр логирования: это была отладка пробы.
3. Оставьте: `GUID`, `guid`, коды ошибок, идентификаторы интерфейсов, базовый класс с ручной vtable, счётчик ссылок, `enable_free_threaded_marshaler`, реестр живых объектов с блокировкой.
4. Добавьте аннотации типов на публичные методы.
5. `_wrap` обязан продолжать перехватывать исключения и возвращать `E_FAIL`: исключение Python, вылетевшее в оболочку, роняет Проводник. Но вместо печати трассировки пишите её в журнал через `logging.getLogger(__name__)` с `exc_info=True` — в этом проекте тихий сбой без диагностики уже был отдельной находкой ревью.

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/files/test_com_types.py -q`
Expected: PASS, 6 тестов

- [ ] **Step 5: Мутационная проверка**

Уберите увеличение счётчика в `_query_interface` — тест `test_asking_for_an_interface_raises_the_reference_count` обязан упасть. Верните.
Сделайте так, чтобы `_query_interface` отвечал успехом на любой идентификатор — тест `test_asking_for_an_unsupported_interface_is_refused` обязан упасть. Верните.
Уберите сохранение `self._impls` — объект может пережить сборку мусора и тесты пройдут, но это опасно; вместо мутации напишите в отчёте, почему эта ссылка обязана существовать.

- [ ] **Step 6: Полный прогон и коммит**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q -c configurator/pyproject.toml` из корня. Ожидается 1056 passed, 4 skipped.

```bash
git add configurator/src/duo_input/clipboard/files/com_types.py configurator/tests/clipboard/files/test_com_types.py
git commit -m "Carry the spike's COM scaffolding into production shape"
```

---

### Task 4: Поток на один файл

**Files:**
- Create: `configurator/src/duo_input/clipboard/files/stream.py`
- Create: `configurator/tests/clipboard/files/test_stream.py`

**Interfaces:**
- Consumes: `ComObject`, `IID_IStream`, `IID_ISequentialStream`, `S_OK`, `E_FAIL` из `com_types.py`; `RequestBridge`, `BridgeError` из `bridge.py`; `FileEntry` из `manifest.py`.
- Produces: `FileStream(ComObject)` — конструктор `(entry: FileEntry, index: int, seq: int, bridge: RequestBridge)`; методы COM `Read`, `Seek`, `Stat`, `Write`, `SetSize`, `CopyTo`, `Commit`, `Revert`, `LockRegion`, `UnlockRegion`, `Clone`; свойство `bytes_delivered: int`; константа `CHUNK_BYTES = 262_144`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Поток на один файл: то, из чего оболочка читает содержимое."""

from __future__ import annotations

import ctypes
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from duo_input.clipboard.files.bridge import RequestBridge
from duo_input.clipboard.files.manifest import FileEntry
from duo_input.clipboard.files.stream import CHUNK_BYTES, FileStream

ENTRY = FileEntry(path="файл.bin", size=1000, is_directory=False, modified_ns=0)


def _bridge_serving(payload: bytes, qapp) -> RequestBridge:
    """Мост, который на любой запрос отдаёт срез из заранее готовых байтов."""
    bridge = RequestBridge()

    def serve(request):
        start = request["offset"]
        length = request["length"]
        bridge.answer(request["token"], payload[start : start + length])

    bridge.request_posted.connect(serve)
    return bridge


def test_stat_answers_from_the_manifest_without_touching_the_network(qapp):
    """Размер уже известен: спрашивать за ним сеть незачем."""
    asked: list[dict] = []
    bridge = RequestBridge()
    bridge.request_posted.connect(asked.append)
    stream = FileStream(ENTRY, index=0, seq=7, bridge=bridge)

    stat = _call_stat(stream)

    assert stat == 1000
    assert asked == []


def test_seek_to_the_end_and_back_costs_no_network(qapp):
    """Оболочка ходит к концу за размером и обратно - это чистая арифметика."""
    asked: list[dict] = []
    bridge = RequestBridge()
    bridge.request_posted.connect(asked.append)
    stream = FileStream(ENTRY, index=0, seq=7, bridge=bridge)

    assert _call_seek(stream, 0, 2) == 1000   # STREAM_SEEK_END
    assert _call_seek(stream, 0, 0) == 0      # STREAM_SEEK_SET
    assert asked == []


def test_reading_returns_the_bytes_and_advances(qapp):
    payload = bytes(range(256)) * 4
    entry = FileEntry(path="a.bin", size=len(payload), is_directory=False, modified_ns=0)
    stream = FileStream(entry, index=0, seq=7, bridge=_bridge_serving(payload, qapp))

    first = _call_read(stream, 100, qapp)
    second = _call_read(stream, 100, qapp)

    assert first == payload[:100]
    assert second == payload[100:200]
    assert stream.bytes_delivered == 200


def test_reading_past_the_end_returns_what_is_left(qapp):
    payload = b"12345"
    entry = FileEntry(path="a.bin", size=5, is_directory=False, modified_ns=0)
    stream = FileStream(entry, index=0, seq=7, bridge=_bridge_serving(payload, qapp))

    assert _call_read(stream, 100, qapp) == b"12345"
    assert _call_read(stream, 100, qapp) == b""


def test_a_network_failure_becomes_a_com_error_not_an_exception(qapp):
    """Исключение Python, вылетевшее в оболочку, роняет Проводник."""
    bridge = RequestBridge()
    bridge.request_posted.connect(lambda request: bridge.fail(request["token"], "связи нет"))
    stream = FileStream(ENTRY, index=0, seq=7, bridge=bridge)

    result, data = _call_read_raw(stream, 10, qapp)

    assert result != 0
    assert data == b""


def test_the_chunk_size_matches_what_the_shell_asks_for():
    """Замерено спайком: оболочка просит ровно столько."""
    assert CHUNK_BYTES == 262_144
```

Вспомогательные вызовы (`_call_stat`, `_call_seek`, `_call_read`, `_call_read_raw`) напишите сами по образцу `configurator/tests/clipboard/spike_filecontents.py`: они собирают структуры `ctypes` и вызывают методы объекта напрямую, без оболочки. Каждый из них должен крутить `qapp.processEvents()` в ожидании, потому что мост отвечает из цикла событий.

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/files/test_stream.py -q`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Написать реализацию**

Возьмите за основу поток из `configurator/tests/clipboard/spike_dataobject.py` и приведите в production-вид. Обязательные свойства:

- `Stat` отвечает из манифеста: размер, имя, тип потока. Сети не касается вовсе.
- `Seek` меняет смещение арифметикой, поддерживая отсчёт от начала, от текущего места и от конца. Сети не касается.
- `Read` запрашивает у моста кусок `{"seq": ..., "index": ..., "offset": ..., "length": ...}`, длина не больше `CHUNK_BYTES` и не больше остатка файла.
- Отказ моста (`BridgeError`) превращается в код ошибки COM, а НЕ в исключение Python: исключение, вылетевшее в оболочку, роняет Проводник.
- Методы записи (`Write`, `SetSize`, `Commit`, `Revert`, `LockRegion`, `UnlockRegion`) возвращают отказ: поток только для чтения.
- `on_final_release` сообщает владельцу, что оболочка отпустила поток — это единственный сигнал отмены, другого не будет.

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/files/test_stream.py -q`
Expected: PASS, 6 тестов

- [ ] **Step 5: Мутационная проверка**

1. Сделайте так, чтобы `Stat` запрашивал размер у моста — тест `test_stat_answers_from_the_manifest_without_touching_the_network` обязан упасть. Верните.
2. Уберите ограничение длины остатком файла — тест `test_reading_past_the_end_returns_what_is_left` обязан упасть. Верните.
3. Уберите перехват `BridgeError` — тест `test_a_network_failure_becomes_a_com_error_not_an_exception` обязан упасть с исключением вместо кода ошибки. Верните.
Результаты приведите в отчёте.

- [ ] **Step 6: Полный прогон и коммит**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q -c configurator/pyproject.toml` из корня. Ожидается 1062 passed, 4 skipped.

```bash
git add configurator/src/duo_input/clipboard/files/stream.py configurator/tests/clipboard/files/test_stream.py
git commit -m "Serve one file as a stream the shell can seek and read"
```

---

### Task 5: Объект для буфера обмена

**Files:**
- Create: `configurator/src/duo_input/clipboard/files/data_object.py`
- Create: `configurator/tests/clipboard/files/test_data_object.py`

**Interfaces:**
- Consumes: `ComObject`, идентификаторы и коды из `com_types.py`; `FileStream` из `stream.py`; `FileManifest` из `manifest.py`; `RequestBridge` из `bridge.py`.
- Produces: `FilesDataObject(ComObject)` — конструктор `(manifest: FileManifest, seq: int, bridge: RequestBridge)`; методы COM `GetData`, `GetDataHere`, `QueryGetData`, `EnumFormatEtc`, `SetData`, `GetCanonicalFormatEtc`, `DAdvise`, `DUnadvise`, `EnumDAdvise`; функция `put_on_clipboard(obj: FilesDataObject) -> None`; функция `clear_clipboard() -> None`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Объект, который для Проводника неотличим от настоящих файлов."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from duo_input.clipboard.files.bridge import RequestBridge
from duo_input.clipboard.files.data_object import FilesDataObject
from duo_input.clipboard.files.manifest import FileEntry, FileManifest

MANIFEST = FileManifest(
    entries=(
        FileEntry(path="папка", size=0, is_directory=True, modified_ns=0),
        FileEntry(path="папка\\один.txt", size=5, is_directory=False, modified_ns=0),
        FileEntry(path="два.bin", size=10, is_directory=False, modified_ns=0),
    )
)


def test_the_descriptor_lists_every_entry_including_the_directory(qapp):
    obj = FilesDataObject(MANIFEST, seq=1, bridge=RequestBridge())

    descriptor = _get_descriptor(obj)

    assert descriptor["count"] == 3
    names = [item["name"] for item in descriptor["items"]]
    assert names == ["папка", "папка\\один.txt", "два.bin"]


def test_the_directory_carries_the_directory_attribute(qapp):
    """Без этого признака оболочка создаст файл вместо папки."""
    obj = FilesDataObject(MANIFEST, seq=1, bridge=RequestBridge())

    descriptor = _get_descriptor(obj)

    assert descriptor["items"][0]["is_directory"] is True
    assert descriptor["items"][1]["is_directory"] is False


def test_asking_for_content_of_a_file_gives_a_stream(qapp):
    obj = FilesDataObject(MANIFEST, seq=1, bridge=RequestBridge())

    assert _get_contents(obj, index=1) is not None


def test_asking_for_content_of_a_directory_is_refused(qapp):
    """У папки нет содержимого: оболочка его и не спрашивает, но спросить может кто угодно."""
    obj = FilesDataObject(MANIFEST, seq=1, bridge=RequestBridge())

    assert _get_contents(obj, index=0) is None


def test_asking_for_an_index_out_of_range_is_refused(qapp):
    obj = FilesDataObject(MANIFEST, seq=1, bridge=RequestBridge())

    assert _get_contents(obj, index=99) is None


def test_an_unknown_format_is_refused(qapp):
    obj = FilesDataObject(MANIFEST, seq=1, bridge=RequestBridge())

    assert _query_unknown_format(obj) != 0
```

Вспомогательные функции (`_get_descriptor`, `_get_contents`, `_query_unknown_format`) напишите по образцу потребителя из `configurator/tests/clipboard/spike_read_dataobject.py`: он уже умеет вызывать эти методы и разбирать структуру описания файлов.

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/files/test_data_object.py -q`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Написать реализацию**

За основу — `configurator/tests/clipboard/spike_dataobject.py`. Обязательные свойства:

- Отдаёт два формата: описание файлов (`CFSTR_FILEDESCRIPTORW`) и содержимое (`CFSTR_FILECONTENTS`).
- В описании каждая запись несёт имя с относительным путём через обратный слэш, размер и признак каталога. Пустые папки перечисляются наравне с файлами.
- Содержимое отдаётся по номеру записи: на файл создаётся `FileStream`, на папку и на несуществующий номер — отказ.
- Перечисление форматов включает содержимое и с номером каждого файла, и с `-1`. Спайк установил, что Проводнику это не нужно, но сторонние потребители сверяют номер точно; лишняя запись ничего не стоит.
- `SetData` отвечает отказом: объект только на чтение.
- Включает free-threaded marshaler — иначе чтения пойдут через цикл событий Qt и подвесят его (замерено спайком: паузы до полусекунды).
- `put_on_clipboard` кладёт объект в буфер через `OleSetClipboard` и `OleFlushClipboard`; `clear_clipboard` очищает.

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/files/test_data_object.py -q`
Expected: PASS, 6 тестов

- [ ] **Step 5: Мутационная проверка**

1. Уберите признак каталога — тест `test_the_directory_carries_the_directory_attribute` обязан упасть. Верните.
2. Разрешите отдавать содержимое папки — тест `test_asking_for_content_of_a_directory_is_refused` обязан упасть. Верните.
3. Уберите проверку границ номера — тест `test_asking_for_an_index_out_of_range_is_refused` обязан упасть. Верните.
4. Отключите free-threaded marshaler и напишите в отчёте, что при этом меняется (тестом это не ловится, но замерено спайком).

- [ ] **Step 6: Полный прогон и коммит**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q -c configurator/pyproject.toml` из корня. Ожидается 1068 passed, 4 skipped.

```bash
git add configurator/src/duo_input/clipboard/files/data_object.py configurator/tests/clipboard/files/test_data_object.py
git commit -m "Offer the shell an object it cannot tell from real files"
```

---

### Task 6: Сторона источника

**Files:**
- Create: `configurator/src/duo_input/clipboard/files/source.py`
- Create: `configurator/tests/clipboard/files/test_source.py`

**Interfaces:**
- Consumes: `FileManifest`, `FileEntry` из `manifest.py`.
- Produces: `FileSource(QObject)` — конструктор `(root: Path, manifest: FileManifest)`; методы `read(index: int, offset: int, length: int) -> bytes`, `close(index: int) -> None`, `close_all() -> None`; исключение `SourceError`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Сторона источника: отдаёт куски локальных файлов, не копируя их на диск."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from duo_input.clipboard.files.manifest import build_manifest
from duo_input.clipboard.files.source import FileSource, SourceError


def _source(tmp_path, payload: bytes = b"0123456789"):
    target = tmp_path / "файл.bin"
    target.write_bytes(payload)
    manifest = build_manifest([target])
    return FileSource(root=tmp_path, manifest=manifest), target


def test_reading_a_range_returns_exactly_that_range(tmp_path):
    source, _ = _source(tmp_path)

    assert source.read(index=0, offset=3, length=4) == b"3456"


def test_reading_past_the_end_returns_what_is_left(tmp_path):
    source, _ = _source(tmp_path)

    assert source.read(index=0, offset=8, length=100) == b"89"


def test_a_file_deleted_after_copying_is_refused(tmp_path):
    """Пользователь удалил исходник между Ctrl+C и Ctrl+V - это ошибка, не пустота."""
    source, target = _source(tmp_path)
    target.unlink()

    with pytest.raises(SourceError):
        source.read(index=0, offset=0, length=4)


def test_a_file_that_changed_size_is_refused(tmp_path):
    """Размер разошёлся с манифестом: отдавать другое содержимое нельзя."""
    source, target = _source(tmp_path)
    target.write_bytes(b"короче")

    with pytest.raises(SourceError):
        source.read(index=0, offset=0, length=4)


def test_a_directory_has_no_content(tmp_path):
    root = tmp_path / "папка"
    root.mkdir()
    manifest = build_manifest([root])
    source = FileSource(root=tmp_path, manifest=manifest)

    with pytest.raises(SourceError):
        source.read(index=0, offset=0, length=4)


def test_an_index_out_of_range_is_refused(tmp_path):
    source, _ = _source(tmp_path)

    with pytest.raises(SourceError):
        source.read(index=42, offset=0, length=4)


def test_closing_releases_the_file_so_it_can_be_renamed(tmp_path):
    """Отменённая передача не должна держать файл открытым."""
    source, target = _source(tmp_path)
    source.read(index=0, offset=0, length=4)

    source.close(index=0)

    target.rename(target.with_name("новое.bin"))  # не должно бросить


def test_reading_does_not_copy_the_file_anywhere(tmp_path):
    """Содержимое не проходит через диск: временных копий быть не должно."""
    source, _ = _source(tmp_path)
    before = set(tmp_path.rglob("*"))

    source.read(index=0, offset=0, length=4)

    assert set(tmp_path.rglob("*")) == before
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/files/test_source.py -q`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Написать реализацию**

Обязательные свойства:

- Файл открывается на чтение с разделением, как это делает сам Проводник, и держится открытым между запросами: открывать заново на каждый кусок при гигабайте — четыре тысячи открытий.
- Перед первым чтением сверяется размер с манифестом. Разошёлся — отказ: файл изменился между копированием и вставкой, и отдавать другое содержимое нельзя.
- Отсутствующий файл, папка, номер за границами — отказ с внятной причиной.
- `close` закрывает дескриптор конкретного файла, `close_all` — все. Без этого отменённая передача держала бы файл открытым до разрыва связи, и пользователь не смог бы переименовать папку, из которой копировал.
- Содержимое никуда не копируется: читаем и отдаём.

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/files/test_source.py -q`
Expected: PASS, 8 тестов

- [ ] **Step 5: Мутационная проверка**

1. Уберите сверку размера — тест `test_a_file_that_changed_size_is_refused` обязан упасть. Верните.
2. Уберите проверку признака каталога — тест `test_a_directory_has_no_content` обязан упасть. Верните.
3. Уберите проверку границ номера — тест `test_an_index_out_of_range_is_refused` обязан упасть. Верните.
4. Сделайте `close` пустым — тест `test_closing_releases_the_file_so_it_can_be_renamed` обязан упасть. Верните.

- [ ] **Step 6: Полный прогон и коммит**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q -c configurator/pyproject.toml` из корня. Ожидается 1076 passed, 4 skipped.

```bash
git add configurator/src/duo_input/clipboard/files/source.py configurator/tests/clipboard/files/test_source.py
git commit -m "Read the copied files in place, and let go when asked"
```

---

### Task 7: Сетевые сообщения и связывание сторон

**Files:**
- Modify: `configurator/src/duo_input/clipboard/wire.py`
- Create: `configurator/src/duo_input/clipboard/files/sink.py`
- Modify: `configurator/src/duo_input/clipboard/service.py`
- Create: `configurator/tests/clipboard/files/test_file_transfer.py`

**Interfaces:**
- Consumes: всё из задач 1-6; `Message`, `MessageType`, `PROTOCOL_MAJOR` из `wire.py`; `ClipboardService` из `service.py`.
- Produces: в `MessageType` добавляются `FILE_READ = 10`, `FILE_CHUNK = 11`, `FILE_ERROR = 12`, `FILE_CLOSE = 13`; `PROTOCOL_MAJOR` становится `2`; `FileSink(QObject)` — конструктор `(bridge: RequestBridge, service)`; методы `publish(manifest, seq)`, `handle_message(message)`, `stop()`; у `ClipboardService` появляются `attach_file_source(source)` и обработка четырёх новых сообщений.

- [ ] **Step 1: Написать падающий сквозной тест**

```python
"""Сквозной путь файла: манифест туда, куски обратно, байты сходятся."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from duo_input.clipboard.files.bridge import RequestBridge
from duo_input.clipboard.files.manifest import build_manifest
from duo_input.clipboard.files.sink import FileSink
from duo_input.clipboard.files.source import FileSource
from duo_input.clipboard.wire import MessageType


def test_a_file_travels_through_the_sink_byte_for_byte(qapp, tmp_path):
    """Настоящий путь данных: мост -> сообщение -> источник -> мост.

    Транспорт подменён на прямую доставку, потому что проверяется путь данных,
    а не сокет; всё остальное - настоящее, включая блокировку чужого потока.
    """
    import threading

    payload = bytes(range(256)) * 500  # 128 000 байт
    target = tmp_path / "большой.bin"
    target.write_bytes(payload)
    manifest = build_manifest([target])
    source = FileSource(root=tmp_path, manifest=manifest)

    bridge = RequestBridge()
    sink = FileSink(bridge=bridge, service=None)

    def deliver(request):
        """Изображаем сеть: запрос уходит источнику, ответ возвращается мосту."""
        try:
            data = source.read(
                index=request["index"], offset=request["offset"], length=request["length"]
            )
        except Exception as error:  # noqa: BLE001 - изображаем FILE_ERROR
            bridge.fail(request["token"], str(error))
            return
        bridge.answer(request["token"], data)

    bridge.request_posted.connect(deliver)

    collected = bytearray()

    def reader():
        offset = 0
        while offset < len(payload):
            chunk = bridge.ask(
                {"seq": 1, "index": 0, "offset": offset, "length": 262_144},
                timeout_ms=5000,
            )
            if not chunk:
                break
            collected.extend(chunk)
            offset += len(chunk)

    thread = threading.Thread(target=reader)
    thread.start()
    import time

    deadline = time.monotonic() + 10
    while thread.is_alive() and time.monotonic() < deadline:
        qapp.processEvents()
    thread.join(timeout=5)

    assert bytes(collected) == payload
    sink.stop()


def test_the_protocol_major_version_was_raised():
    """Новые типы сообщений старая версия не поймёт - об этом надо сказать вслух."""
    from duo_input.clipboard.wire import PROTOCOL_MAJOR

    assert PROTOCOL_MAJOR == 2


def test_the_new_message_types_do_not_disturb_the_old_numbers():
    """Смена номера существующего типа рассинхронизировала бы версии."""
    assert MessageType.HELLO == 1
    assert MessageType.OFFER == 2
    assert MessageType.PAIR_CONFIRM == 9
    assert MessageType.FILE_READ == 10
    assert MessageType.FILE_CHUNK == 11
    assert MessageType.FILE_ERROR == 12
    assert MessageType.FILE_CLOSE == 13
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/files/test_file_transfer.py -q`
Expected: FAIL, `AttributeError: FILE_READ` либо `ModuleNotFoundError: sink`

- [ ] **Step 3: Добавить типы сообщений и поднять версию**

В `configurator/src/duo_input/clipboard/wire.py`: добавьте в конец перечисления четыре значения с номерами 10-13, НЕ меняя существующих; поднимите `PROTOCOL_MAJOR` с 1 на 2 и допишите в docstring модуля, почему: добавление типов не ломает разбор кадров, но получатель прежней версии не поймёт объявление с файлами, а расхождение старшей версии уже даёт внятное сообщение «обновите вторую машину».

- [ ] **Step 4: Написать `sink.py` и подключить к сервису**

`FileSink` связывает три вещи: получает манифест, кладёт объект в буфер, и обслуживает запросы моста, превращая их в сетевые сообщения.

- Запрос из моста (`request_posted`) превращается в `FILE_READ` и уходит пиру.
- Пришедший `FILE_CHUNK` отдаётся мосту через `answer` по номеру запроса.
- Пришедший `FILE_ERROR` отдаётся через `fail` — ожидающий поток просыпается отказом.
- Когда оболочка отпускает поток, уходит `FILE_CLOSE`.
- `stop` закрывает мост, будя всех ожидающих: остановка подсистемы не должна оставлять заблокированных потоков.

В `ClipboardService` добавьте обработку четырёх новых сообщений: `FILE_READ` обслуживается источником (`attach_file_source`), ответ уходит `FILE_CHUNK` либо `FILE_ERROR`; `FILE_CLOSE` закрывает файл у источника.

Соблюдайте существующее правило адресации: ответ адресуется по номеру объявления и номеру записи, а не «первому попавшемуся ожиданию» — в этом проекте уже была правка ровно на эту тему.

- [ ] **Step 5: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/files/test_file_transfer.py -q`
Expected: PASS, 3 теста

- [ ] **Step 6: Мутационная проверка**

1. Поменяйте номер `MessageType.HELLO` — тест `test_the_new_message_types_do_not_disturb_the_old_numbers` обязан упасть. Верните.
2. Оставьте `PROTOCOL_MAJOR` равным 1 — тест `test_the_protocol_major_version_was_raised` обязан упасть. Верните.
3. Сделайте так, чтобы `FILE_ERROR` не будил мост — напишите тест, в котором ожидающий поток обязан проснуться отказом, и убедитесь, что без этой обработки он падает по сроку.

- [ ] **Step 7: Полный прогон и коммит**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q -c configurator/pyproject.toml` из корня. Ожидается 1079 passed, 4 skipped.

```bash
git add configurator/src/duo_input/clipboard/wire.py configurator/src/duo_input/clipboard/files/sink.py configurator/src/duo_input/clipboard/service.py configurator/tests/clipboard/files/test_file_transfer.py
git commit -m "Carry file ranges over the link, and raise the protocol major"
```

---

### Task 8: Подключение, приёмка, документация

**Files:**
- Modify: `configurator/src/duo_input/clipboard/windows_backend.py`
- Modify: `configurator/src/duo_input/app.py`
- Modify: `configurator/src/duo_input/ui/tray.py`
- Create: `configurator/tests/clipboard/files/test_wiring.py`
- Create: `docs/user/clipboard-files-ru.md`
- Modify: `docs/user/clipboard-ru.md`

**Interfaces:**
- Consumes: всё предыдущее.
- Produces: рабочая функция целиком.

- [ ] **Step 1: Написать падающие тесты подключения**

```python
"""То, до чего production действительно дотягивается.

Работающий модуль, до которого приложение не дотягивается, - это неработающая
функция. В этом проекте так уже случалось.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from duo_input.clipboard.windows_backend import SYNCED_MIMES


def test_local_file_paths_are_no_longer_synchronised_as_text():
    """Раньше Ctrl+C файла отправлял ПУТЬ, который на другой машине ни на что
    не указывает. Теперь файлы идут своим путём, а формат путей из текстовой
    синхронизации убран."""
    assert "text/uri-list" not in SYNCED_MIMES


def test_the_files_toggle_is_available_in_the_tray(qapp):
    from duo_input.ui.tray import TrayIcon

    tray = TrayIcon()

    assert tray.files_action.isEnabled() is True


def test_files_marked_private_are_not_announced(qapp, tmp_path):
    """Обещание приватности одинаково для текста, картинок и файлов.

    Менеджеры паролей и подобные программы помечают своё содержимое просьбой
    не запоминать его. Для файлов это правило действует ровно так же.
    """
    from duo_input.clipboard.windows_backend import PRIVATE_MARKERS, is_private

    formats = ["text/uri-list", PRIVATE_MARKERS[0]]

    assert is_private(formats) is True
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/files/test_wiring.py -q`
Expected: FAIL

- [ ] **Step 3: Подключить**

1. Уберите `text/uri-list` из синхронизируемых текстовых форматов: копирование файла больше не отправляет бесполезный путь.
2. При копировании, если в буфере есть файлы, стройте манифест и объявляйте его; при получении такого объявления — кладите объект в буфер через `FileSink`. **Проверка маркеров приватности действует и здесь**: если буфер помечен как не подлежащий запоминанию, манифест не строится и не объявляется — правило общее для текста, изображений и файлов, и обходить его для файлов было бы худшим из возможных решений, потому что файл несёт больше, чем строка.
3. Включите пункт «Передача файлов» в меню трея: он был заготовлен выключенным с первого этапа. Выключенный переключатель означает, что манифесты не объявляются и не принимаются.
4. Свяжите остановку подсистемы с закрытием моста и источника: при выходе из программы не должно остаться заблокированных потоков и открытых файлов.

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/files/test_wiring.py -q`
Expected: PASS, 2 теста

- [ ] **Step 5: Написать документацию**

`docs/user/clipboard-files-ru.md`, по образцу `docs/user/clipboard-ru.md`: что копирование файлов требует включённого переключателя на обеих машинах; что при вставке появится обычное окно прогресса Windows с кнопкой отмены; что при обрыве связи передача начинается заново, а недокачанный файл не остаётся; что папки копируются целиком; что при ошибке посреди передачи Windows показывает своё окно с вопросом и ждёт ответа человека; что очень большие папки с тысячами мелких файлов передаются медленно. В `docs/user/clipboard-ru.md` добавьте ссылку на новый документ.

- [ ] **Step 6: Ручная приёмка**

Автотесты этого не заменяют. Проверьте на двух машинах и запишите результат в `docs/superpowers/records/2026-09-04-file-transfer-acceptance.md`:

- [ ] Один небольшой файл: `Ctrl+C` на первой, `Ctrl+V` на второй, содержимое совпадает.
- [ ] Файл больше гигабайта: видно окно прогресса, идёт до конца, файл открывается.
- [ ] Отмена на середине большого файла: передача прекращается, недокачанного файла на диске нет.
- [ ] Папка с вложенными папками: структура воссоздана полностью, включая пустые папки.
- [ ] Несколько файлов, выделенных сразу.
- [ ] Обрыв Wi-Fi посреди передачи: показана ошибка, недокачанного файла нет.
- [ ] Исходный файл удалён между копированием и вставкой: внятная ошибка, а не пустой файл.
- [ ] После отмены исходную папку можно переименовать (файл не остался открытым).
- [ ] Копирование в обратную сторону.
- [ ] Выход из программы во время передачи не оставляет висящих процессов.

- [ ] **Step 7: Полный прогон и коммит**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q -c configurator/pyproject.toml` из корня. Ожидается 1081 passed, 4 skipped.

```bash
git add configurator/src/duo_input/clipboard/windows_backend.py configurator/src/duo_input/app.py configurator/src/duo_input/ui/tray.py configurator/tests/clipboard/files/test_wiring.py docs/user/clipboard-files-ru.md docs/user/clipboard-ru.md docs/superpowers/records/2026-09-04-file-transfer-acceptance.md
git commit -m "Turn the file transfer on, and tell people how it behaves"
```

---

## Что этот план намеренно не делает

Возобновление после обрыва, macOS, вставку из архивов и виртуальных папок, права доступа и альтернативные потоки NTFS, одновременные встречные передачи, объединение мелких файлов в один поток. Каждое разобрано в спецификации с причиной.

## Судьба кода спайка

Файлы `configurator/tests/clipboard/spike_*.py` — одноразовый код пробы. После задачи 5 они больше ни для чего не нужны: всё ценное перенесено в production и покрыто тестами. Удалять их или оставить как исторический след — решение владельца продукта; по умолчанию оставляем, они не участвуют в прогоне тестов и никому не мешают.
