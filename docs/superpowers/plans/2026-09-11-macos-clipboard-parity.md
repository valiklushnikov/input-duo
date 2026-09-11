# macOS Clipboard Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить `MacOSClipboardBackend`, чтобы общий буфер обмена Duo Input работал во всех комбинациях Windows ↔ macOS, используя тот же сетевой протокол.

**Architecture:** Весь сетевой слой (`identity`, `trust`, `offer`, `wire`, `peer`, `listener`, `discovery`, `pairing`, `service`, `coordinator`) переиспользуется без изменений. Канонизация форматов буфера выносится в общий `formats.py`. macOS получает отдельный `macos_backend.py` (детект копирования опросом `NSPasteboard.changeCount`) и единственный pyobjc-модуль `macos_pasteboard.py` (нативная host-only публикация через `NSPasteboardItemDataProvider`). Выбор бэкенда — фабрика `platform_backend.py` с ленивыми импортами.

**Tech Stack:** Python 3.14 (dev на macOS), PySide6 6.10.1 (`QtCore`, `QtGui`, `QtNetwork`), `pyobjc-framework-Cocoa` (только macOS), `cryptography`, pytest 9.1.1 + pytest-qt 4.5.0.

**Spec:** `docs/superpowers/specs/2026-09-11-macos-clipboard-parity-design.md`

## Global Constraints

- Этот milestone добавляет **только clipboard parity**. Файлы, packaging, RTF — вне scope.
- Форматы parity: `text/plain`, `text/html`, `text/uri-list` (только `http`/`https`), `image/png`.
- `text/uri-list` **никогда** не несёт `file://` — ни на Windows, ни на macOS.
- `image/png` на wire — **всегда PNG**, независимо от источника (TIFF/DIB/QImage).
- Потолок содержимого — **32 МиБ** (`MAX_CONTENT_BYTES = 33_554_432`), проверяется **после** нормализации формата.
- `clipboard/**` использует только `QtCore`, `QtGui`, `QtNetwork` — **никогда `QtWidgets`**.
- pyobjc импортируется **только** из `macos_pasteboard.py`. `backend.py` не импортирует конкретные бэкенды. `windows_backend.py` не импортирует pyobjc/AppKit/Foundation. `macos_backend.py` не импортирует AppKit/Foundation напрямую.
- Подавление петли на macOS корректно при `new != old` и `current == own_change_count`; не завязано на инкремент `+1` и не зависит от выживания `ORIGIN_MIME`.
- Содержимое буфера никогда не пишется на диск.
- Протокол `wire` не меняется: `PROTOCOL_MAJOR=1`, `PROTOCOL_MINOR=0`.
- Сетевой слой не должен знать, Windows это или macOS.
- Тесты запускаются из каталога `configurator/`: `../.venv-mac/bin/python -m pytest <путь> -q`.
- Каждая задача заканчивается коммитом. Сообщение коммита — по-английски, как в истории репозитория.

## Структура файлов

```
configurator/src/duo_input/clipboard/formats.py           каноническая нормализация форматов (общая)
configurator/src/duo_input/clipboard/windows_backend.py   ПРАВКА: переход на formats.py, +text/html, фильтр uri-list
configurator/src/duo_input/clipboard/macos_pasteboard.py  единственный pyobjc-модуль: change_count, publish_with_origin
configurator/src/duo_input/clipboard/macos_backend.py     MacOSClipboardBackend: опрос changeCount, снимок, публикация
configurator/src/duo_input/clipboard/platform_backend.py  фабрика create_backend по sys.platform
configurator/src/duo_input/app.py                         ПРАВКА: create_backend вместо WindowsClipboardBackend
```

Тесты — `configurator/tests/clipboard/`:

```
test_formats.py           нормализация web_uri_list / png_bytes / collect_payloads
test_windows_backend.py   ПРАВКА: parity-правки uri-list + text/html
test_macos_pasteboard.py  нативная обёртка (darwin + pyobjc, помечено маркером)
test_macos_backend.py     state-machine подавления петель + is_private
test_platform_backend.py  фабрика: win32/darwin/прочее, ленивые импорты
test_snapshot_parity.py   контракт: одинаковый QMimeData → одинаковые payloads
test_boundaries.py        ПРАВКА: инварианты графа импортов
```

## Подготовка окружения

- [ ] **Установить зависимости в `.venv-mac`** (разово):

Run:
```bash
cd /Users/valik/Desktop/duo/input-duo && .venv-mac/bin/python -m pip install pytest==9.1.1 pytest-qt==4.5.0 cryptography==46.0.3 pyobjc-framework-Cocoa
```

Записать точную вставшую версию `pyobjc-framework-Cocoa` — она понадобится в Task 3.

Проверить, что существующие тесты проходят на Mac (базовая линия):
```bash
cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/test_backend.py tests/clipboard/test_windows_backend.py -q
```
Expected: PASS (эти тесты используют offscreen Qt и не зависят от Windows API).

---

### Task 1: Каноническая нормализация форматов

Единственное место, определяющее, что считается содержимым конкретного MIME в Duo Input, независимо от источника. И Windows, и macOS будут звать один код.

**Files:**
- Create: `configurator/src/duo_input/clipboard/formats.py`
- Test: `configurator/tests/clipboard/test_formats.py`

**Interfaces:**
- Consumes: `MAX_CONTENT_BYTES` из `offer.py`.
- Produces:
  - `SYNCED_MIMES: tuple[str, ...] = ("text/plain", "text/html", "text/uri-list", "image/png")`
  - `web_uri_list(mime_data: QMimeData) -> bytes | None`
  - `png_bytes(mime_data: QMimeData) -> bytes | None`
  - `normalized_payload(mime_data: QMimeData, mime: str) -> bytes | None`
  - `collect_payloads(mime_data: QMimeData) -> dict[str, bytes]`

- [ ] **Step 1: Написать падающие тесты**

```python
"""Каноническая нормализация форматов буфера, общая для всех платформ."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QImage

from duo_input.clipboard.formats import (
    collect_payloads,
    normalized_payload,
    png_bytes,
    web_uri_list,
)
from duo_input.clipboard.offer import MAX_CONTENT_BYTES


def _mime_with_urls(*urls: str) -> QMimeData:
    data = QMimeData()
    data.setUrls([QUrl(url) for url in urls])
    return data


def test_web_uri_list_keeps_http_and_https():
    data = _mime_with_urls("http://example.com", "https://openai.com")

    result = web_uri_list(data)

    assert result == b"http://example.com\r\nhttps://openai.com\r\n"


def test_web_uri_list_drops_file_urls():
    data = _mime_with_urls("https://example.com", "file:///Users/me/a.txt")

    assert web_uri_list(data) == b"https://example.com\r\n"


def test_web_uri_list_of_only_files_is_absent():
    data = _mime_with_urls("file:///Users/me/a.txt", "file:///Users/me/b.txt")

    assert web_uri_list(data) is None


def test_web_uri_list_drops_non_http_schemes():
    data = _mime_with_urls("ftp://example.com", "mailto:me@example.com")

    assert web_uri_list(data) is None


def test_web_uri_list_without_urls_is_absent():
    assert web_uri_list(QMimeData()) is None


def test_png_bytes_returns_existing_png_unchanged():
    data = QMimeData()
    data.setData("image/png", b"\x89PNG\r\n\x1a\nMADE-UP")

    assert png_bytes(data) == b"\x89PNG\r\n\x1a\nMADE-UP"


def test_png_bytes_encodes_a_qimage_to_png():
    image = QImage(2, 2, QImage.Format.Format_RGB32)
    image.fill(0xFF0000)
    data = QMimeData()
    data.setImageData(image)

    result = png_bytes(data)

    assert result is not None
    assert result.startswith(b"\x89PNG\r\n\x1a\n")


def test_png_bytes_without_image_is_absent():
    assert png_bytes(QMimeData()) is None


def test_normalized_payload_dispatches_plain_text_directly():
    data = QMimeData()
    data.setData("text/plain", "привет".encode("utf-8"))

    assert normalized_payload(data, "text/plain") == "привет".encode("utf-8")


def test_collect_payloads_gathers_every_synced_format():
    data = QMimeData()
    data.setData("text/plain", b"hello")
    data.setData("text/html", b"<b>hello</b>")
    data.setUrls([QUrl("https://example.com")])

    payloads = collect_payloads(data)

    assert payloads == {
        "text/plain": b"hello",
        "text/html": b"<b>hello</b>",
        "text/uri-list": b"https://example.com\r\n",
    }


def test_collect_payloads_enforces_the_ceiling_after_normalisation():
    data = QMimeData()
    data.setData("text/plain", b"x" * (MAX_CONTENT_BYTES + 1))

    assert collect_payloads(data) == {}
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/test_formats.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'duo_input.clipboard.formats'`

- [ ] **Step 3: Написать реализацию**

```python
"""Каноническая форма содержимого буфера, одинаковая на всех платформах.

Wire знает только про четыре MIME из SYNCED_MIMES. Что бы ни лежало в
операционном буфере - TIFF на macOS, DIB на Windows, набор file:// в
uri-list, - наружу уходит канонический вид: PNG для картинки, только веб-URL
для ссылок. Так снимок с двух разных ОС для одного и того же содержимого
получается байт в байт одинаковым, и сетевой слой не знает, кто его снял.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QBuffer, QIODevice, QMimeData
from PySide6.QtGui import QImage, QPixmap

from .offer import MAX_CONTENT_BYTES

logger = logging.getLogger(__name__)

#: Форматы, которые синхронизирует parity. Порядок не важен: снимок - dict.
SYNCED_MIMES = ("text/plain", "text/html", "text/uri-list", "image/png")

_WEB_SCHEMES = frozenset({"http", "https"})


def web_uri_list(mime_data: QMimeData) -> bytes | None:
    """Только веб-ссылки. file:// вырезается: файлы - отдельный milestone.

    Возвращает None, а не b"", когда веб-ссылок нет: это значит "формат для
    синхронизации отсутствует", а не "формат есть, но пуст".
    """
    if not mime_data.hasUrls():
        return None
    urls = [url for url in mime_data.urls() if url.scheme().lower() in _WEB_SCHEMES]
    if not urls:
        return None
    return b"\r\n".join(bytes(url.toEncoded()) for url in urls) + b"\r\n"


def png_bytes(mime_data: QMimeData) -> bytes | None:
    """PNG в любом случае. Готовый PNG отдаём как есть, иначе кодируем.

    Готовый image/png не перекодируется - лишний проход стоил бы времени и
    мог бы изменить цветовой профиль. Если PNG нет, но картинка есть, тип Qt
    приводится к QImage и кодируется. Иной или невалидный тип - None, без
    скрытых различий между платформами.
    """
    if mime_data.hasFormat("image/png"):
        existing = bytes(mime_data.data("image/png"))
        if existing:
            return existing
    if not mime_data.hasImage():
        return None
    image = mime_data.imageData()
    if isinstance(image, QPixmap):
        image = image.toImage()
    if not isinstance(image, QImage):
        return None
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        return None
    return bytes(buffer.data())


def normalized_payload(mime_data: QMimeData, mime: str) -> bytes | None:
    """Канонический payload одного формата, либо None если его нет."""
    if mime == "text/uri-list":
        return web_uri_list(mime_data)
    if mime == "image/png":
        return png_bytes(mime_data)
    if mime_data.hasFormat(mime):
        payload = bytes(mime_data.data(mime))
        return payload or None
    return None


def collect_payloads(mime_data: QMimeData) -> dict[str, bytes]:
    """Все синхронизируемые форматы буфера в каноническом виде.

    Потолок 32 МиБ проверяется ПОСЛЕ нормализации: TIFF на 20 МиБ может стать
    PNG на 38 МиБ.
    """
    payloads: dict[str, bytes] = {}
    for mime in SYNCED_MIMES:
        payload = normalized_payload(mime_data, mime)
        if payload is None:
            continue
        if len(payload) > MAX_CONTENT_BYTES:
            logger.warning(
                "формат %s занимает %d байт, потолок 32 МиБ (%d) — не объявляется",
                mime,
                len(payload),
                MAX_CONTENT_BYTES,
            )
            continue
        payloads[mime] = payload
    return payloads


__all__ = [
    "SYNCED_MIMES",
    "collect_payloads",
    "normalized_payload",
    "png_bytes",
    "web_uri_list",
]
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/test_formats.py -q`
Expected: PASS, 11 тестов

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/clipboard/formats.py configurator/tests/clipboard/test_formats.py
git commit -m "Canonicalise clipboard formats in one shared place"
```

---

### Task 2: Перевести Windows-бэкенд на общую канонизацию

Windows начинает синхронизировать `text/html` и перестаёт слать `file://` в `text/uri-list`. Это делает снимок симметричным с macOS. Изменение поведения закрывается тестами.

**Files:**
- Modify: `configurator/src/duo_input/clipboard/windows_backend.py`
- Modify: `configurator/tests/clipboard/test_windows_backend.py`

**Interfaces:**
- Consumes: `SYNCED_MIMES`, `collect_payloads` из `formats.py`.
- Produces: `snapshot_from` и `is_private` с прежними сигнатурами; `SYNCED_MIMES` больше не определяется в `windows_backend` (реэкспортируется из `formats`).

- [ ] **Step 1: Написать падающие тесты** (добавить в `test_windows_backend.py`)

```python
from PySide6.QtCore import QMimeData, QUrl


def test_snapshot_keeps_text_html():
    data = QMimeData()
    data.setData("text/plain", b"hello")
    data.setData("text/html", b"<b>hello</b>")

    snapshot = snapshot_from(data)

    assert snapshot.payloads["text/html"] == b"<b>hello</b>"


def test_snapshot_uri_list_keeps_only_web_urls():
    data = QMimeData()
    data.setUrls([QUrl("https://example.com"), QUrl("file:///C:/secret.txt")])

    snapshot = snapshot_from(data)

    assert snapshot.payloads["text/uri-list"] == b"https://example.com\r\n"


def test_snapshot_of_only_file_urls_has_no_uri_list():
    data = QMimeData()
    data.setUrls([QUrl("file:///C:/a.txt"), QUrl("file:///C:/b.txt")])

    snapshot = snapshot_from(data)

    assert "text/uri-list" not in snapshot.payloads
```

Первый из существующих тестов (`test_snapshot_keeps_only_the_formats_we_synchronise`) сейчас ждёт `{"text/plain"}` для данных с `text/plain` + `text/html`. Обновить его: теперь синхронизируются оба.

```python
def test_snapshot_keeps_only_the_formats_we_synchronise():
    data = _FakeMimeData({"text/plain": b"hello", "text/rtf": b"{\\rtf1}"})

    snapshot = snapshot_from(data)

    assert set(snapshot.payloads) == {"text/plain"}
```

- [ ] **Step 2: Убедиться, что новые тесты падают**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/test_windows_backend.py -q`
Expected: FAIL — `text/html` отсутствует в снимке; фильтрация `file://` не работает.

Примечание: `_FakeMimeData` в тесте не реализует `hasUrls`/`urls`/`hasImage`. Для новых тестов используется настоящий `QMimeData`. Существующие тесты с `_FakeMimeData` должны продолжать работать: расширить `_FakeMimeData`, добавив заглушки `hasUrls()`/`urls()`/`hasImage()`/`hasFormat()`, чтобы `collect_payloads` мог их звать.

```python
class _FakeMimeData:
    """Утиная замена QMimeData: только то, что читает снимок."""

    def __init__(self, payloads: dict[str, bytes]) -> None:
        self._payloads = payloads

    def formats(self) -> list[str]:
        return list(self._payloads)

    def hasFormat(self, mime: str) -> bool:  # noqa: N802 - Qt API
        return mime in self._payloads

    def data(self, mime: str) -> bytes:
        return self._payloads.get(mime, b"")

    def hasUrls(self) -> bool:  # noqa: N802 - Qt API
        return False

    def urls(self) -> list:
        return []

    def hasImage(self) -> bool:  # noqa: N802 - Qt API
        return False
```

- [ ] **Step 3: Переписать `windows_backend.py` на `formats.py`**

Заменить локальные `SYNCED_MIMES` и тело `snapshot_from`:

```python
from .backend import ORIGIN_MIME, ClipboardSnapshot, ContentFetcher, RemoteMimeData
from .formats import SYNCED_MIMES, collect_payloads
from .offer import ClipboardOffer

# ... PRIVATE_MARKERS, DEBOUNCE_MS, RETRY_LIMIT без изменений ...


def is_private(formats: list[str]) -> bool:
    """Просило ли содержимое, чтобы его не запоминали и не пересылали."""
    if ORIGIN_MIME in formats:
        return True
    return any(marker in formats for marker in PRIVATE_MARKERS)


def snapshot_from(mime_data) -> ClipboardSnapshot:
    """Взять из буфера то, что мы умеем синхронизировать, и ничего сверх."""
    if is_private(list(mime_data.formats())):
        return ClipboardSnapshot({})
    return ClipboardSnapshot(collect_payloads(mime_data))
```

Убрать импорт `MAX_CONTENT_BYTES` (теперь лимит внутри `collect_payloads`). Оставить `SYNCED_MIMES` в `__all__` (реэкспорт из `formats`), чтобы существующие импорты не сломались.

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/test_windows_backend.py -q`
Expected: PASS (включая новые тесты). Также прогнать `test_service_rules.py` и `test_end_to_end.py`, чтобы убедиться, что смена набора форматов ничего не сломала:

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/clipboard/windows_backend.py configurator/tests/clipboard/test_windows_backend.py
git commit -m "Route the Windows snapshot through the shared format rules"
```

---

### Task 3: Нативная обёртка pasteboard (единственный pyobjc-модуль)

Всё нативное живёт здесь и больше нигде. Наружу — ровно две операции, которых не хватает Qt.

**Files:**
- Create: `configurator/src/duo_input/clipboard/macos_pasteboard.py`
- Test: `configurator/tests/clipboard/test_macos_pasteboard.py`
- Modify: `configurator/requirements-build.txt`

**Interfaces:**
- Consumes: `ClipboardOffer` из `offer.py`; `ContentFetcher` из `backend.py`; `ORIGIN_MIME` из `backend.py`.
- Produces:
  - `change_count() -> int`
  - `publish_with_origin(offer: ClipboardOffer, fetcher: ContentFetcher) -> int` — публикует host-only лениво и возвращает новый `changeCount`. Держать ссылку на data provider обязан вызывающий (см. Task 4).

- [ ] **Step 1: Записать зависимость**

Добавить в `configurator/requirements-build.txt` (после блока Windows-инструментов) с environment-маркером и точным пином из «Подготовки окружения»:

```
# macOS clipboard parity: NSPasteboard.changeCount и host-only lazy provider,
# которых нет в Qt. Только для сборки/разработки под macOS.
pyobjc-framework-Cocoa==<pin>; sys_platform == "darwin"
```

- [ ] **Step 2: Написать падающие тесты** (интеграционные, только darwin + pyobjc)

```python
"""Нативная обёртка pasteboard. Требует настоящей сессии macOS и pyobjc."""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="нативный pasteboard есть только на macOS"
)

pytest.importorskip("AppKit")

from duo_input.clipboard.macos_pasteboard import change_count, publish_with_origin
from duo_input.clipboard.offer import ClipboardOffer, describe


def _offer() -> ClipboardOffer:
    return ClipboardOffer("a" * 32, 7, describe({"text/plain": b"lazy from peer"}))


def test_change_count_is_an_int():
    assert isinstance(change_count(), int)


def test_change_count_grows_after_a_native_write():
    before = change_count()
    subprocess.run(["/usr/bin/pbcopy"], input=b"nudge", check=True)

    assert change_count() != before


def test_publish_returns_a_new_change_count_and_serves_the_fetcher():
    before = change_count()
    provider_ref = []

    def fetch(mime: str) -> bytes:
        return b"lazy from peer" if mime == "text/plain" else b""

    count = publish_with_origin(_offer(), fetch)
    provider_ref.append(count)  # держим что-нибудь живым на время теста

    assert isinstance(count, int)
    assert count != before
    # Первый внешний читатель материализует ленивый payload.
    pasted = subprocess.run(["/usr/bin/pbpaste"], capture_output=True).stdout
    assert pasted == b"lazy from peer"
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/test_macos_pasteboard.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'duo_input.clipboard.macos_pasteboard'`

- [ ] **Step 4: Написать реализацию**

```python
"""Единственное место, где Duo Input говорит с AppKit напрямую.

Qt почти всё умеет сам, поэтому здесь только то, чего у него нет: чтение
NSPasteboard.changeCount (Qt не отдаёт его, а на нём держится детект
локального копирования) и host-only ленивая публикация (Qt не умеет пометить
содержимое как "только этот компьютер", а без этого Universal Clipboard
материализовал бы удалённый буфер до вставки).

Модуль намеренно узкий. Всё преобразование типов, ownership и AppKit живут
тут; наружу выходят ровно две функции.
"""

from __future__ import annotations

import logging

from AppKit import (
    NSPasteboard,
    NSPasteboardContentsCurrentHostOnly,
    NSPasteboardItem,
)
from Foundation import NSData, NSObject

from .backend import ORIGIN_MIME, ContentFetcher
from .offer import ClipboardOffer

logger = logging.getLogger(__name__)

#: Кастомный тип-метка происхождения (defense-in-depth, см. spec §4).
ORIGIN_UTI = "com.duo-input.origin"

#: MIME parity -> UTI, которыми pasteboard объявляет форматы.
_MIME_TO_UTI = {
    "text/plain": "public.utf8-plain-text",
    "text/html": "public.html",
    "text/uri-list": "public.url",
    "image/png": "public.png",
}
_UTI_TO_MIME = {uti: mime for mime, uti in _MIME_TO_UTI.items()}


def change_count() -> int:
    """Текущий счётчик изменений общего pasteboard."""
    return int(NSPasteboard.generalPasteboard().changeCount())


def _to_nsdata(payload: bytes) -> NSData:
    return NSData.dataWithBytes_length_(payload, len(payload))


class _DuoDataProvider(NSObject):
    """Ленивый поставщик данных: материализует формат только при чтении."""

    def initWithOffer_fetcher_(self, offer, fetcher):  # noqa: N802 - objc API
        self = objc_super_init(self)
        if self is None:
            return None
        self._offer = offer
        self._fetcher = fetcher
        return self

    def pasteboard_item_provideDataForType_(self, pasteboard, item, uti):  # noqa: N802
        mime = _UTI_TO_MIME.get(str(uti))
        if mime is None:
            return
        try:
            payload = self._fetcher(mime)
        except Exception:  # noqa: BLE001 - пустая вставка честнее падения
            logger.warning("не удалось отдать формат %s", mime, exc_info=True)
            return
        if payload:
            item.setData_forType_(_to_nsdata(payload), uti)


def objc_super_init(instance):
    """objc.super(...).init() — вынесено, чтобы initWithOffer читался ровно."""
    import objc

    return objc.super(_DuoDataProvider, instance).init()


#: NSPasteboard не удерживает data provider сильной ссылкой: без ссылки на
#: стороне Python GC соберёт его и Cmd+V вернёт пусто. Держим ровно один живой
#: provider - текущий; при следующей публикации предыдущий больше не нужен.
_live_provider = None


def publish_with_origin(offer: ClipboardOffer, fetcher: ContentFetcher) -> int:
    """Опубликовать удалённый буфер host-only и лениво. Вернуть новый changeCount.

    Provider удерживается живым внутри модуля (см. _live_provider): NSPasteboard
    его сильной ссылкой не держит, а материализация ленива и произойдёт позже,
    при первой вставке.
    """
    global _live_provider

    pasteboard = NSPasteboard.generalPasteboard()
    count = int(
        pasteboard.prepareForNewContentsWithOptions_(NSPasteboardContentsCurrentHostOnly)
    )

    provider = _DuoDataProvider.alloc().initWithOffer_fetcher_(offer, fetcher)
    item = NSPasteboardItem.alloc().init()

    lazy_types = [_MIME_TO_UTI[m] for m in offer.mimes() if m in _MIME_TO_UTI]
    if lazy_types:
        item.setDataProvider_forTypes_(provider, lazy_types)

    # Метка происхождения кладётся сразу (маленькая, без сети): второй пояс
    # подавления петли. Значение никем не читается - важно наличие типа.
    marker = f"{offer.origin_id}:{offer.seq}".encode("ascii")
    item.setData_forType_(_to_nsdata(marker), ORIGIN_UTI)

    pasteboard.writeObjects_([item])

    _live_provider = provider  # удержать до следующей публикации
    return count


__all__ = ["ORIGIN_UTI", "change_count", "publish_with_origin"]
```

Примечание для исполнителя: точные имена UTI (`public.html`, `public.png`, `public.url`) и то, как Qt показывает `ORIGIN_UTI` при обратном чтении, — предмет микро-проверки из spec. Если интеграционный тест по `text/plain` проходит, база верна; расхождения по html/png/url правятся здесь же, в `_MIME_TO_UTI`.

- [ ] **Step 5: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/test_macos_pasteboard.py -q`
Expected: PASS, 3 теста (на реальной сессии macOS).

- [ ] **Step 6: Commit**

```bash
git add configurator/src/duo_input/clipboard/macos_pasteboard.py configurator/tests/clipboard/test_macos_pasteboard.py configurator/requirements-build.txt
git commit -m "Wrap the two NSPasteboard operations Qt lacks"
```

---

### Task 4: MacOSClipboardBackend — детект копирования и публикация

Зеркало Windows-бэкенда, но детект локального копирования — опросом `changeCount`, а публикация — нативная.

**Files:**
- Create: `configurator/src/duo_input/clipboard/macos_backend.py`
- Test: `configurator/tests/clipboard/test_macos_backend.py`

**Interfaces:**
- Consumes: `ORIGIN_MIME`, `ClipboardSnapshot`, `ContentFetcher` из `backend.py`; `collect_payloads` из `formats.py`; `ClipboardOffer` из `offer.py`; модуль `macos_pasteboard` (инъектируется).
- Produces: `MacOSClipboardBackend(QObject)` — сигнал `snapshot_taken(object)`; методы `start`/`stop`/`publish`/`payload`; модульные `is_private(formats: list[str]) -> bool`, `snapshot_from(mime_data) -> ClipboardSnapshot`; константы `PRIVATE_MARKERS`, `POLL_MS = 300`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""State-machine подавления петель на macOS и правила приватности."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from duo_input.clipboard.backend import ORIGIN_MIME, ClipboardSnapshot
from duo_input.clipboard.macos_backend import (
    MacOSClipboardBackend,
    is_private,
    snapshot_from,
)
from duo_input.clipboard.offer import ClipboardOffer, describe


class _FakePasteboard:
    """Инъектируемая замена macos_pasteboard: без AppKit."""

    def __init__(self, count: int = 10) -> None:
        self._count = count
        self.published: list[ClipboardOffer] = []

    def change_count(self) -> int:
        return self._count

    def set_count(self, value: int) -> None:
        self._count = value

    def publish_with_origin(self, offer, fetcher) -> int:
        self.published.append(offer)
        self._count += 1
        return self._count


class _FakeMimeData:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self._payloads = payloads

    def formats(self) -> list[str]:
        return list(self._payloads)

    def hasFormat(self, mime: str) -> bool:  # noqa: N802 - Qt API
        return mime in self._payloads

    def data(self, mime: str) -> bytes:
        return self._payloads.get(mime, b"")

    def hasUrls(self) -> bool:  # noqa: N802 - Qt API
        return False

    def urls(self) -> list:
        return []

    def hasImage(self) -> bool:  # noqa: N802 - Qt API
        return False


class _FakeClipboard:
    def __init__(self, mime_data: _FakeMimeData) -> None:
        self._mime_data = mime_data

    def mimeData(self):  # noqa: N802 - Qt API
        return self._mime_data


def _backend(count=10, payloads=None) -> tuple[MacOSClipboardBackend, _FakePasteboard]:
    pasteboard = _FakePasteboard(count)
    clipboard = _FakeClipboard(_FakeMimeData(payloads or {"text/plain": b"hello"}))
    backend = MacOSClipboardBackend(clipboard, pasteboard=pasteboard)
    return backend, pasteboard


def test_is_private_respects_the_concealed_marker():
    assert is_private(["text/plain", "org.nspasteboard.ConcealedType"]) is True


def test_is_private_respects_our_own_origin():
    assert is_private(["text/plain", ORIGIN_MIME]) is True


def test_an_ordinary_clipboard_is_not_private():
    assert is_private(["text/plain", "text/html"]) is False


def test_start_takes_a_baseline_and_does_not_emit():
    backend, pasteboard = _backend(count=10)
    emitted: list = []
    backend.snapshot_taken.connect(emitted.append)

    backend.start()
    backend._poll()  # первый опрос с тем же count

    assert emitted == []


def test_a_local_change_emits_a_snapshot():
    backend, pasteboard = _backend(count=10)
    emitted: list = []
    backend.snapshot_taken.connect(emitted.append)
    backend.start()

    pasteboard.set_count(11)
    backend._poll()

    assert len(emitted) == 1
    assert emitted[0].payloads["text/plain"] == b"hello"


def test_an_unchanged_count_does_nothing():
    backend, pasteboard = _backend(count=11)
    emitted: list = []
    backend.snapshot_taken.connect(emitted.append)
    backend.start()

    backend._poll()

    assert emitted == []


def test_our_own_publish_is_suppressed():
    backend, pasteboard = _backend(count=11)
    emitted: list = []
    backend.snapshot_taken.connect(emitted.append)
    backend.start()

    offer = ClipboardOffer("b" * 32, 1, describe({"text/plain": b"peer"}))
    backend.publish(offer, lambda mime: b"peer")
    backend._poll()  # видит наш собственный changeCount

    assert emitted == []


def test_a_local_copy_after_our_publish_is_not_suppressed():
    backend, pasteboard = _backend(count=11)
    emitted: list = []
    backend.snapshot_taken.connect(emitted.append)
    backend.start()

    offer = ClipboardOffer("b" * 32, 1, describe({"text/plain": b"peer"}))
    backend.publish(offer, lambda mime: b"peer")  # own -> count 12
    pasteboard.set_count(13)  # пользователь скопировал что-то своё
    backend._poll()

    assert len(emitted) == 1


def test_suppression_holds_without_any_origin_marker():
    """Главный инвариант: корректность держится на changeCount, не на MIME.

    FakeMimeData вообще не содержит ORIGIN_MIME, но own-publish обязан
    подавляться - потому что current == own_change_count.
    """
    backend, pasteboard = _backend(count=11)
    emitted: list = []
    backend.snapshot_taken.connect(emitted.append)
    backend.start()

    offer = ClipboardOffer("b" * 32, 1, describe({"text/plain": b"peer"}))
    backend.publish(offer, lambda mime: b"peer")
    backend._poll()

    assert emitted == []
    assert pasteboard.published == [offer]
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/test_macos_backend.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'duo_input.clipboard.macos_backend'`

- [ ] **Step 3: Написать реализацию**

```python
"""Наблюдение за буфером macOS опросом changeCount и публикация в него.

У Qt на macOS нет надёжного уведомления об изменении чужого буфера, поэтому
локальное копирование ловится опросом NSPasteboard.changeCount по таймеру -
так же, как это делают нативные менеджеры буфера. Публикация удалённого
содержимого идёт через нативный host-only ленивый provider, а не через
QClipboard: только так Universal Clipboard не материализует чужой буфер до
вставки.

Подавление собственной петли держится на changeCount: наш publish
инкрементирует счётчик, мы запоминаем это значение и не принимаем его за
локальное копирование. Метка происхождения - лишь второй пояс: корректность
не должна зависеть от того, переживёт ли кастомный тип round-trip Qt.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from . import macos_pasteboard as _macos_pasteboard
from .backend import ORIGIN_MIME, ClipboardSnapshot, ContentFetcher
from .formats import collect_payloads
from .offer import ClipboardOffer

#: Метки конвенции nspasteboard.org: не запоминать и не пересылать.
PRIVATE_MARKERS = (
    "org.nspasteboard.ConcealedType",
    "org.nspasteboard.TransientType",
)

#: Как часто опрашивать changeCount. 300 мс - хороший баланс отзывчивости и CPU.
POLL_MS = 300


def is_private(formats: list[str]) -> bool:
    """Просило ли содержимое, чтобы его не запоминали и не пересылали."""
    if ORIGIN_MIME in formats:
        return True
    return any(marker in formats for marker in PRIVATE_MARKERS)


def snapshot_from(mime_data) -> ClipboardSnapshot:
    """Взять из буфера то, что мы умеем синхронизировать, и ничего сверх."""
    if is_private(list(mime_data.formats())):
        return ClipboardSnapshot({})
    return ClipboardSnapshot(collect_payloads(mime_data))


class MacOSClipboardBackend(QObject):
    """Граница платформы для macOS."""

    snapshot_taken = Signal(object)

    def __init__(self, clipboard, parent: QObject | None = None, pasteboard=_macos_pasteboard) -> None:
        super().__init__(parent)
        self._clipboard = clipboard
        self._pasteboard = pasteboard
        self._running = False
        self._last_seen_change_count: int | None = None
        self._own_change_count: int | None = None
        self._local: ClipboardSnapshot = ClipboardSnapshot({})

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)

    def start(self) -> None:
        if self._running:
            return
        self._last_seen_change_count = self._pasteboard.change_count()
        self._own_change_count = None
        self._timer.start()
        self._running = True

    def stop(self) -> None:
        if not self._running:
            return
        self._timer.stop()
        self._last_seen_change_count = None
        self._own_change_count = None
        self._local = ClipboardSnapshot({})
        self._running = False

    def publish(self, offer: ClipboardOffer, fetcher: ContentFetcher) -> None:
        """Объявить в локальном буфере то, что лежит на втором компьютере.

        Provider удерживается живым внутри macos_pasteboard; здесь нужен только
        новый changeCount, чтобы не принять собственную публикацию за локальное
        копирование.
        """
        count = self._pasteboard.publish_with_origin(offer, fetcher)
        self._own_change_count = count
        self._last_seen_change_count = count

    def payload(self, mime: str) -> bytes | None:
        return self._local.payload(mime)

    # ------------------------------------------------------------------ внутреннее

    def _poll(self) -> None:
        current = self._pasteboard.change_count()
        if current == self._last_seen_change_count:
            return
        if current == self._own_change_count:
            self._last_seen_change_count = current
            self._own_change_count = None
            return
        self._last_seen_change_count = current
        snapshot = snapshot_from(self._clipboard.mimeData())
        if snapshot.payloads:
            self._local = snapshot
            self.snapshot_taken.emit(snapshot)


__all__ = [
    "POLL_MS",
    "PRIVATE_MARKERS",
    "MacOSClipboardBackend",
    "is_private",
    "snapshot_from",
]
```

Примечание: удержание нативного provider живым — забота `macos_pasteboard` (модульная ссылка `_live_provider`), поэтому `MacOSClipboardBackend` про provider ничего не знает и одинаково работает с настоящим модулем и с фейком. Реальная жизнеспособность provider проверяется интеграционным тестом Task 3 и ручной проверкой Task 8.

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/test_macos_backend.py -q`
Expected: PASS, 9 тестов

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/clipboard/macos_backend.py configurator/tests/clipboard/test_macos_backend.py
git commit -m "Detect macOS copies by polling the pasteboard change count"
```

---

### Task 5: Фабрика выбора бэкенда

Единственная точка ветвления по платформе, с ленивыми импортами, чтобы Windows-сборка не трогала macos-модули и pyobjc.

**Files:**
- Create: `configurator/src/duo_input/clipboard/platform_backend.py`
- Test: `configurator/tests/clipboard/test_platform_backend.py`

**Interfaces:**
- Consumes: `WindowsClipboardBackend`, `MacOSClipboardBackend` (ленивыми импортами внутри веток).
- Produces: `UnsupportedPlatformError(Exception)`; `create_backend(clipboard, parent=None) -> ClipboardBackend`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Фабрика выбирает бэкенд по платформе и не тянет чужие импорты."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from duo_input.clipboard import platform_backend
from duo_input.clipboard.platform_backend import (
    UnsupportedPlatformError,
    create_backend,
)


def test_windows_platform_builds_the_windows_backend(monkeypatch):
    monkeypatch.setattr(platform_backend.sys, "platform", "win32")
    from duo_input.clipboard.windows_backend import WindowsClipboardBackend

    backend = create_backend(clipboard=object())

    assert isinstance(backend, WindowsClipboardBackend)


def test_darwin_platform_builds_the_macos_backend(monkeypatch):
    monkeypatch.setattr(platform_backend.sys, "platform", "darwin")
    from duo_input.clipboard.macos_backend import MacOSClipboardBackend

    backend = create_backend(clipboard=object())

    assert isinstance(backend, MacOSClipboardBackend)


def test_an_unknown_platform_is_refused(monkeypatch):
    monkeypatch.setattr(platform_backend.sys, "platform", "sunos5")

    with pytest.raises(UnsupportedPlatformError, match="sunos5"):
        create_backend(clipboard=object())
```

Примечание: `test_darwin_platform_builds_the_macos_backend` создаёт `MacOSClipboardBackend` с настоящим `macos_pasteboard` по умолчанию, но конструктор его не вызывает (импорт модуля происходит, вызовов AppKit нет). На darwin с установленным pyobjc это безопасно. На не-darwin тест пропустится вместе с невозможностью импортировать AppKit — обернуть его `@pytest.mark.skipif(sys.platform != "darwin")`, чтобы Windows-CI не падал на импорте `macos_pasteboard`.

```python
import sys as _sys

@pytest.mark.skipif(_sys.platform != "darwin", reason="macos_backend тянет macos_pasteboard")
def test_darwin_platform_builds_the_macos_backend(monkeypatch):
    ...
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/test_platform_backend.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'duo_input.clipboard.platform_backend'`

- [ ] **Step 3: Написать реализацию**

```python
"""Выбор реализации границы платформы. Единственный sys.platform в подсистеме.

Импорты ленивые и внутри веток: Windows-сборка никогда не импортирует
macos_backend, а значит и macos_pasteboard, а значит и pyobjc. В runtime-графе
Windows нативного macOS-кода нет вовсе - это важно для Nuitka.
"""

from __future__ import annotations

import sys

from .backend import ClipboardBackend


class UnsupportedPlatformError(Exception):
    """Платформа, для которой нет реализации буфера обмена."""


def create_backend(clipboard, parent=None) -> ClipboardBackend:
    if sys.platform == "win32":
        from .windows_backend import WindowsClipboardBackend

        return WindowsClipboardBackend(clipboard, parent)
    if sys.platform == "darwin":
        from .macos_backend import MacOSClipboardBackend

        return MacOSClipboardBackend(clipboard, parent)
    raise UnsupportedPlatformError(sys.platform)


__all__ = ["UnsupportedPlatformError", "create_backend"]
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/test_platform_backend.py -q`
Expected: PASS (на macOS — 3 теста; на Windows один пропущен).

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/clipboard/platform_backend.py configurator/tests/clipboard/test_platform_backend.py
git commit -m "Pick the clipboard backend by platform with lazy imports"
```

---

### Task 6: Контракт кросс-платформенного равенства снимков

Один абстрактный `QMimeData` должен давать одинаковый снимок на обеих платформах — иначе Windows и macOS разъедутся, а сетевой слой этого не заметит.

**Files:**
- Test: `configurator/tests/clipboard/test_snapshot_parity.py`

**Interfaces:**
- Consumes: `snapshot_from` из `windows_backend` и `macos_backend`.
- Produces: только тест.

- [ ] **Step 1: Написать тест**

```python
"""Снимок одного и того же буфера одинаков на Windows и на macOS."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QImage

from duo_input.clipboard.macos_backend import snapshot_from as mac_snapshot
from duo_input.clipboard.windows_backend import snapshot_from as win_snapshot


def _rich_mime_data() -> QMimeData:
    data = QMimeData()
    data.setData("text/plain", "привет".encode("utf-8"))
    data.setData("text/html", b"<b>hi</b>")
    data.setUrls([QUrl("https://example.com"), QUrl("file:///tmp/secret")])
    image = QImage(2, 2, QImage.Format.Format_RGB32)
    image.fill(0x00FF00)
    data.setImageData(image)
    return data


def test_windows_and_macos_snapshots_are_byte_identical():
    data = _rich_mime_data()

    win = win_snapshot(data).payloads
    mac = mac_snapshot(data).payloads

    assert win == mac
    assert set(win) == {"text/plain", "text/html", "text/uri-list", "image/png"}
    assert win["text/uri-list"] == b"https://example.com\r\n"  # file:// вырезан
```

- [ ] **Step 2: Убедиться, что тест проходит**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/test_snapshot_parity.py -q`
Expected: PASS.

Примечание: обе `snapshot_from` зовут один `collect_payloads`; различаются только `is_private` (наборы маркеров), а обычный `QMimeData` не приватен ни там, ни там — значит payloads совпадут. Если тест падает — разошлись пути нормализации, чинить в `formats.py`, а не в бэкендах.

- [ ] **Step 3: Commit**

```bash
git add configurator/tests/clipboard/test_snapshot_parity.py
git commit -m "Pin cross-platform snapshot equality as a contract"
```

---

### Task 7: Инварианты графа импортов

Не дать платформенной границе размыться со временем. Расширяет существующий `test_boundaries.py`.

**Files:**
- Modify: `configurator/tests/clipboard/test_boundaries.py`

**Interfaces:**
- Consumes: ничего.
- Produces: только тесты.

- [ ] **Step 1: Написать падающие тесты** (добавить в `test_boundaries.py`)

```python
_NATIVE_PREFIXES = ("AppKit", "Foundation", "objc", "PyObjCTools", "Cocoa")


def _has_native_import(path: Path) -> bool:
    return any(
        name.split(".")[0] in _NATIVE_PREFIXES for name in _imported_modules(path)
    )


def test_only_macos_pasteboard_touches_pyobjc():
    offenders = {
        path.name
        for path in PACKAGE.glob("*.py")
        if path.name != "macos_pasteboard.py" and _has_native_import(path)
    }

    assert offenders == set(), (
        "pyobjc/AppKit разрешён только в macos_pasteboard.py — вся нативная "
        "грязь должна быть в одном месте"
    )


def test_backend_module_does_not_import_concrete_backends():
    imported = _imported_modules(PACKAGE / "backend.py")

    assert not any(
        name.endswith("windows_backend") or name.endswith("macos_backend")
        for name in imported
    ), "backend.py — нейтральный контракт, он не должен знать реализации"


def test_windows_backend_stays_free_of_native_macos_imports():
    assert not _has_native_import(PACKAGE / "windows_backend.py")
```

- [ ] **Step 2: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/test_boundaries.py -q`
Expected: PASS (при корректной реализации Task 3–5 нарушений нет).

Примечание: тест `test_the_clipboard_package_never_imports_qtwidgets` остаётся без изменений — QtGui в `formats.py` его не нарушает (запрещён только QtWidgets). Обновить комментарий-докстринг модуля на «только QtCore/QtGui/QtNetwork, никогда QtWidgets».

- [ ] **Step 3: Commit**

```bash
git add configurator/tests/clipboard/test_boundaries.py
git commit -m "Guard the platform boundary import graph"
```

---

### Task 8: Подключить фабрику в точке запуска

Заменить прямое создание Windows-бэкенда на `create_backend`. После этого `app.py` не содержит ветвлений по платформе.

**Files:**
- Modify: `configurator/src/duo_input/app.py`

**Interfaces:**
- Consumes: `create_backend` из `platform_backend`.
- Produces: ничего нового.

- [ ] **Step 1: Заменить импорт и создание бэкенда**

В `configurator/src/duo_input/app.py`:

Заменить импорт (около строки 26):
```python
from duo_input.clipboard.windows_backend import WindowsClipboardBackend
```
на:
```python
from duo_input.clipboard.platform_backend import create_backend
```

Заменить аннотацию поля (около строки 205):
```python
        self._backend: WindowsClipboardBackend | None = None
```
на:
```python
        from duo_input.clipboard.backend import ClipboardBackend

        self._backend: ClipboardBackend | None = None
```

Заменить создание (около строки 269):
```python
        backend = WindowsClipboardBackend(application.clipboard(), coordinator)
```
на:
```python
        backend = create_backend(application.clipboard(), coordinator)
```

- [ ] **Step 2: Проверить, что приложение импортируется и стартует на Mac**

Run:
```bash
cd configurator && QT_QPA_PLATFORM=offscreen ../.venv-mac/bin/python -c "
import duo_input.app as app
from duo_input.clipboard.platform_backend import create_backend
print('import OK; create_backend:', create_backend)
"
```
Expected: печатает `import OK; ...` без ошибок.

- [ ] **Step 3: Прогнать весь набор тестов clipboard**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/ -q`
Expected: PASS (на macOS все, включая нативные Task 3; на Windows нативные/darwin-тесты пропускаются).

- [ ] **Step 4: Ручная проверка на реальной сессии macOS** (записать результат в `docs/superpowers/records/`)

1. Запустить приложение из `.venv-mac`, включить общий буфер, спарить с вторым компьютером (или Mac↔Mac).
2. Скопировать на первом текст → на втором `⌘V`/`Ctrl+V` → появляется текст.
3. Повторить для HTML (из браузера), веб-URL (адресная строка), PNG (скриншот).
4. Убедиться, что `file://` из Finder **не** уходит (copy файла в Finder → на втором ничего для вставки как текст/URL).
5. Убедиться в отсутствии петли: скопировать на A, вставить на B, затем скопировать на B — на A приходит именно новое содержимое B, без зацикливания.
6. Проверить host-only: скопированный remote-контент не всплывает на iPhone/другом Mac через Universal Clipboard до вставки.

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/app.py
git commit -m "Select the clipboard backend by platform at startup"
```

---

## Самопроверка плана

- **Покрытие спека:** Секция 1 (граница/фабрика) → Task 5, 7, 8; Секция 2 (state-machine) → Task 4; Секция 3 (форматы) → Task 1, 2, 6; Секция 4 (приватность/host-only/lazy provider) → Task 3, 4; Секция 5 (тесты) → Task 1–7; Секция 6 (зависимости/протокол/сборка) → Task 3 (pyobjc), «Подготовка окружения». Пробелов нет.
- **Плейсхолдеров нет:** весь код приведён; «микро-проверки» из spec (UTI-имена) отмечены как ожидаемая TDD-итерация внутри Task 3, а не как отложенная работа.
- **Согласованность типов:** `create_backend(clipboard, parent=None)`, `publish_with_origin(offer, fetcher) -> int`, `change_count() -> int`, `collect_payloads(mime_data) -> dict[str,bytes]`, `snapshot_from(mime_data) -> ClipboardSnapshot`, `is_private(formats) -> bool` — совпадают во всех задачах и с существующим `ClipboardBackend` Protocol.

## Известные риски исполнения

- Нативный `NSPasteboardItemDataProvider` через pyobjc — самая тонкая часть (Task 3). Интеграционный тест по `text/plain` — шлагбаум; UTI для html/png/url правятся в `_MIME_TO_UTI` по месту.
- Удержание provider (`self._provider`) — если Cmd+V на настоящей сессии возвращает пусто, первым делом проверять именно живучесть ссылки (ручная проверка Task 8, шаг 4).
