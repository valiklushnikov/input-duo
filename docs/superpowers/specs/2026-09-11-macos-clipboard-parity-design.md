# Дизайн: общий буфер обмена Windows ↔ macOS (Mac clipboard parity)

**Дата:** 2026-09-11
**Статус:** дизайн утверждён, ожидает плана реализации
**Предшествует:** `docs/superpowers/specs/2026-09-03-shared-clipboard-design.md`,
`docs/superpowers/plans/2026-09-03-shared-clipboard-milestone-1.md`
**Спайк:** `docs/superpowers/records/2026-09-11-macos-lazy-clipboard-spike.md`

## Цель

Дать Duo Input общий буфер обмена между Windows и macOS: `Ctrl+C`/`⌘C` на одном
компьютере, `Ctrl+V`/`⌘V` на другом, содержимое уходит по локальной сети в момент
вставки. Milestone 1 уже реализовал Windows ↔ Windows. Этот этап добавляет
`MacOSClipboardBackend`, чтобы работали все три комбинации:

```
Windows ←→ Windows   (готово)
Windows ←→ macOS      (этот этап)
macOS   ←→ macOS      (этот этап)
```

Сетевой протокол остаётся **один и тот же**, независимо от ОС. Платформенной
границей служит `ClipboardBackend`; macOS реализуется отдельным
`macos_backend.py` рядом с существующим `windows_backend.py`.

## Границы

**Входит в этот spec:**
- `MacOSClipboardBackend` — снимок локального буфера, ленивая публикация
  удалённого, подавление петель.
- Форматы parity: `text/plain`, `text/html`, `text/uri-list` (только веб-URL),
  `image/png`.
- Канонизация форматов, общая для обеих платформ (`formats.py`), с симметричной
  правкой Windows-бэкенда.
- Фабрика выбора бэкенда по платформе.
- Защита от Universal Clipboard / Handoff (host-only публикация).

**НЕ входит (отдельные последующие milestone):**
- Копирование файлов (Finder / Explorer), файловый протокол, streaming, manifest.
- macOS packaging: `.app`, подпись, нотаризация, `.dmg`.
- Изменение сетевого протокола (`wire` остаётся 1.0).
- RTF, конвертация RTF→HTML, форматы за пределами четырёх перечисленных.

## Результат спайка (шлагбаум)

Спайк на macOS 15.1.1 (PySide6 6.10.1) **подтвердил возможность ленивой семантики**:
реальные данные (`text/plain`) не материализуются при `QClipboard.setMimeData` —
только при первом фактическом чтении pasteboard. Скопированное и не вставленное
содержимое машину не покидает.

Важное уточнение контракта публикации: спайк доказал, что Qt/macOS *способен*
сохранять lazy-семантику, но **production-публикация на macOS использует нативный
`NSPasteboardItemDataProvider`, а не `RemoteMimeData`** — потому что host-only
(защита от Handoff) достижим только через нативный `NSPasteboard`. `RemoteMimeData`
(Qt) на macOS не используется вовсе; она остаётся путём публикации только на
Windows. Контракт по платформам:

```
Windows publish → RemoteMimeData → QClipboard
macOS   publish → NSPasteboardItemDataProvider → ContentFetcher → NSPasteboard (host-only)
```

Снимок локального буфера (чтение) ни на одной платформе `RemoteMimeData` не
использует — он читает `QMimeData` напрямую через `formats.py`.

Три уточнения из спайка формируют дизайн:

1. Qt при регистрации типа делает служебный вызов
   `retrieveData('application/x-qt-mime-type-name')` — не наши данные, обрабатывать
   без обращения к сети.
2. Данные материализуются при **первом внешнем чтении** pasteboard, затем
   кэшируются системой. Гарантия «уходит только при вставке» слабее, чем на
   Windows: пассивный читатель (менеджер буфера, Universal Clipboard) может дёрнуть
   provider без участия пользователя. Отсюда — host-only публикация.
3. Провайдер должен отвечать быстро; сетевой fetch синхронный, с таймаутом; при
   просрочке — пустая вставка, а не зависание.

## Секция 1 — Архитектура и граница модулей

Весь сетевой слой уже платформо-независим и переиспользуется без изменений:
`identity`, `trust`, `offer`, `wire`, `peer`, `listener`, `discovery`, `pairing`,
`service`, `coordinator`, а также `backend.py` (`ClipboardBackend` Protocol,
`ClipboardSnapshot` и `RemoteMimeData`). `RemoteMimeData` остаётся в `backend.py`,
но используется **только Windows-путём публикации**; macOS публикует нативно
(Секция 4). Выноса в общий `transfer/`-слой в этом этапе **нет** — это задел под
файлы, отдельный milestone (YAGNI сейчас).

Граф зависимостей:

```
                    backend.py           formats.py
                       ▲                     ▲
                       │                     │
        ┌──────────────┴───────────────┐     │
        │                              │     │
windows_backend.py             macos_backend.py
        │                              │
        └──────── formats.py ──────────┘
                                       │
                                       ▼
                              macos_pasteboard.py
                                       │
                                       ▼
                                PyObjC / AppKit

                 platform_backend.py
                    │            │  (ленивые импорты)
                    ▼            ▼
             WindowsBackend   MacBackend
```

**Новые модули:**
- `clipboard/formats.py` — **каноническая нормализация форматов буфера** (не
  «Mac helpers»): `SYNCED_MIMES`, `web_uri_list`, `png_bytes`, диспетчер
  `normalized_payload`. Общий для обеих платформ.
- `clipboard/macos_backend.py` — `MacOSClipboardBackend(QObject)`, зеркало
  `windows_backend.py`: контракт `snapshot_taken`/`start`/`stop`/`publish`/
  `payload`, модульные `is_private`, `snapshot_from`.
- `clipboard/macos_pasteboard.py` — **единственный** модуль, зависящий от pyobjc.
  Максимально узкий: только операции, которых не хватает Qt. Наружу —
  `change_count() -> int` и `publish_with_origin(offer, fetcher, origin_id) -> int`.
- `clipboard/platform_backend.py` — фабрика `create_backend(clipboard, parent=None)
  -> ClipboardBackend`.

**Фабрика — ленивые импорты** (Windows-сборка не должна тянуть macos-модули и
pyobjc в runtime-граф; для Nuitka критично):

```python
def create_backend(clipboard, parent=None):
    if sys.platform == "win32":
        from .windows_backend import WindowsClipboardBackend
        return WindowsClipboardBackend(clipboard, parent)
    if sys.platform == "darwin":
        from .macos_backend import MacOSClipboardBackend
        return MacOSClipboardBackend(clipboard, parent)
    raise UnsupportedPlatformError(sys.platform)
```

**Точка запуска.** В `app.py` прямое создание `WindowsClipboardBackend(...)`
заменяется на `create_backend(...)`. Больше в `app.py` ветвлений по платформе нет.

**Инъекция зависимости.** `MacOSClipboardBackend(clipboard, parent=None,
pasteboard=macos_pasteboard)` — unit-тесты подставляют фейковый `pasteboard` без
настоящего AppKit.

**Инварианты (проверяются архитектурным тестом):**
- `clipboard/**` не импортирует `QtWidgets` (существующий инвариант, распространён
  на новые модули).
- `backend.py` не импортирует `windows_backend`/`macos_backend`.
- `windows_backend.py` не импортирует pyobjc/AppKit/Foundation.
- `macos_backend.py` не импортирует AppKit/Foundation напрямую.
- Только `macos_pasteboard.py` **может** импортировать AppKit/Foundation.

## Секция 2 — Устройство MacOSClipboardBackend

Детект локального копирования — не через `QClipboard.dataChanged` (на macOS
ненадёжен для чужих копирований и в фоне), а через **опрос `NSPasteboard.changeCount`**.

- `POLL_MS = 300` — константа (baseline; 250–300 мс — хороший UX/CPU-компромисс).
- Опрос по `QTimer`; на тик читает `pasteboard.change_count()`.
- Снимок читается через Qt `clipboard.mimeData()` + `snapshot_from` (Секция 3).

**Алгоритм опроса:**

```python
def _poll(self):
    current = self._pasteboard.change_count()
    if current == self._last_seen_change_count:
        return
    if current == self._own_change_count:      # наш publish — не локальная копия
        self._last_seen_change_count = current
        self._own_change_count = None
        return
    self._last_seen_change_count = current
    snapshot = snapshot_from(self._clipboard)
    if snapshot.payloads:                       # контракт как на Windows
        self.snapshot_taken.emit(snapshot)
```

**Публикация обновляет счётчики сразу, не дожидаясь тика:**

```python
def publish(self, offer, fetcher):
    count = self._pasteboard.publish_with_origin(
        offer, fetcher, origin_id=self._own_origin_id
    )
    self._own_change_count = count
    self._last_seen_change_count = count
```

**start / stop:**

```python
def start(self):
    self._last_seen_change_count = self._pasteboard.change_count()  # baseline
    self._own_change_count = None
    self._timer.start(POLL_MS)

def stop(self):
    self._timer.stop()
    self._last_seen_change_count = None
    self._own_change_count = None
```

`start()` берёт baseline, чтобы включение (Disabled→Enabled) не выстрелило
содержимым, скопированным час назад. `stop()` инвалидирует всё transient-состояние,
чтобы следующий `start()` взял baseline заново.

**Инварианты:**
- Зависим только от `new != old`; **не** предполагаем инкремент ровно на `+1`
  (не завязываемся на внутреннюю реализацию AppKit).
- Self-detect строго по равенству `current == own_change_count` (не `<=`). Это
  корректно обрабатывает гонку «чужое копирование сразу после нашего publish»:
  `own = 101`, пользователь копирует → `current = 102 != 101` → корректно трактуется
  как новое локальное копирование.
- `snapshot_from` возвращает `ClipboardSnapshot` (пустой при пустом/приватном
  буфере), а не `None` — контракт как на Windows; emit только при непустом
  `payloads`.

**Подавление петли — иерархия:**
1. **Primary:** `changeCount` self-detect.
2. **Secondary (best-effort):** `ORIGIN_MIME`/origin-UTI.

> **Инвариант:** `ORIGIN_MIME` на macOS — best-effort defense-in-depth.
> Корректность подавления петли ДОЛЖНА сохраняться, даже если кастомный MIME не
> переживает round-trip через NSPasteboard/Qt. Это доказывается тестом №6 ниже.

## Секция 3 — Канонические форматы (formats.py)

`formats.py` определяет, что именно считается содержимым конкретного MIME в Duo
Input, независимо от источника (Windows, macOS, TIFF, DIB, QImage). Wire не должен
знать, что macOS принёс TIFF, а Windows — свой native image.

```
QMimeData / native clipboard  →  formats.py  →  canonical payloads
                                               →  ClipboardSnapshot  →  wire
```

```python
SYNCED_MIMES = ("text/plain", "text/html", "text/uri-list", "image/png")

def normalized_payload(mime_data, mime) -> bytes | None:
    if mime == "text/uri-list":
        return web_uri_list(mime_data)
    if mime == "image/png":
        return png_bytes(mime_data)
    if mime_data.hasFormat(mime):
        return bytes(mime_data.data(mime))
    return None
```

Оба бэкенда крутят один цикл:

```python
for mime in SYNCED_MIMES:
    payload = normalized_payload(mime_data, mime)
    if payload is not None and len(payload) <= MAX_CONTENT_BYTES:
        payloads[mime] = payload
```

**`web_uri_list`** — только веб-URL, `file://` вырезается (файлы — отдельный
milestone):

```python
urls = [u for u in mime_data.urls() if u.scheme().lower() in {"http", "https"}]
if not urls:
    return None                      # None, а не b"": формат отсутствует
return b"\r\n".join(bytes(u.toEncoded()) for u in urls) + b"\r\n"
```

**`png_bytes`** — порядок строгий:
1. Есть реальный `image/png` → вернуть напрямую **без перекодирования** (экономит
   CPU, не трогает метаданные/цветовой профиль).
2. PNG нет, но `hasImage()` → `imageData()`, привести Qt-тип и кодировать PNG
   (через `QBuffer` + `QImage.save(buffer, "PNG")`):
   - `QImage` → PNG напрямую;
   - `QPixmap` → `toImage()` → PNG (дешёвая нормализация, делает helper реально
     platform-neutral);
3. Изображения нет, либо тип не приводится к `QImage`/`QPixmap` → `None`
   (предсказуемо, без скрытых различий Windows/macOS).

**`text/plain` / `text/html`** — прямой путь. RTF-only (без HTML) не синхронизируем;
конвертацию RTF→HTML в этот milestone не вводим.

> **Инвариант:** лимит `MAX_CONTENT_BYTES` (32 MiB) проверяется **после**
> нормализации — TIFF 20 MiB может стать PNG 38 MiB.

**Правка Windows.** Сейчас Windows берёт сырой `text/uri-list`, включая `file://` —
это нарушает parity (`Windows→Mac` слал бы файлы, `Mac→Windows` фильтровал бы).
Windows переводится на общий `normalized_payload`; изменение закрывается тестами до
рефакторинга.

## Секция 4 — Приватность и подавление петель на macOS

**Публикация — `macos_pasteboard.publish_with_origin(offer, fetcher, origin_id) -> int`:**
- `prepareForNewContentsWithOptions_(NSPasteboardContentsCurrentHostOnly)` —
  host-only (исключает Universal Clipboard / Handoff), возвращает новый
  `changeCount` (уходит в подавление петли Секции 2).
- Регистрирует `NSPasteboardItemDataProvider`, объявляющий типы из `offer.mimes()`
  + кастомный origin-UTI; callback синхронно зовёт тот же `ContentFetcher` с
  таймаутом; при ошибке — nil/пусто, а не зависание. Ленивость обеспечивает
  нативный механизм macOS.
- `RemoteMimeData` (Qt) на macOS для публикации **не используется** — остаётся
  только для Windows. Приём/снимок локального буфера на macOS читается через Qt
  `mimeData()` напрямую.

> **Инвариант жизненного цикла:** NSPasteboard не удерживает data provider сильной
> ссылкой. `MacOSClipboardBackend` обязан хранить ссылку на текущий provider, иначе
> GC его соберёт и `⌘V` вернёт пусто.

**Три пояса против «утечки без вставки»:**
1. host-only убирает Handoff-читателя;
2. `changeCount` self-detect — primary loop suppression;
3. `ORIGIN_MIME`/origin-UTI — best-effort defense-in-depth (корректность не
   зависит).

**Приватность.** `is_private(formats)` на macOS уважает `org.nspasteboard.ConcealedType`
(пароли) и `org.nspasteboard.TransientType` (временное) + `ORIGIN_MIME`. Симметрично
Windows-версии со своими `PRIVATE_MARKERS`. Точный вид имён этих UTI через Qt
уточняется микро-проверкой при реализации.

## Секция 5 — Тестирование

Стратегия как в проекте: offscreen Qt + фейки; настоящий AppKit/сеть в unit-тестах
не участвуют (спрятаны за инъектируемым `pasteboard`).

- `test_macos_backend.py` — state-machine (6 тестов):
  1. `start` берёт baseline `changeCount` → первый poll с тем же count не emit'ит;
  2. локальное изменение (`count` вырос) → `snapshot_taken` emit;
  3. неизменный `count` → тишина;
  4. own-publish (publish вернул count, poll видит его) → не emit;
  5. локальная копия после own-publish (`current != own`) → emit;
  6. **подавление own-publish работает при полном отсутствии `ORIGIN_MIME`** —
     доказывает, что корректность держится на `changeCount`, а не на MIME.

  Плюс `is_private` (Concealed/Transient/origin). Фейковый `pasteboard` (DI) и
  фейковый `QMimeData`.
- `test_formats.py`:
  - `web_uri_list`: http проходит; https проходит; file:// вырезается; смешанное
    web/file → только web; только файлы → `None`; не-http схемы → `None`; пустой
    список URL → `None`.
  - `png_bytes`: прямой `image/png` возвращается без изменений; нет PNG + `QImage`
    → PNG; нет изображения → `None`; кривой/неудачный тип → `None`.
  - лимит 32 MiB после нормализации.
- `test_windows_backend.py` — parity-правка `text/uri-list`: только https →
  включён; https+file → только https; только file → формат отсутствует.
- `test_snapshot_parity.py` — **контракт кросс-платформенного равенства**: один
  абстрактный `QMimeData` → `windows_snapshot.payloads == macos_snapshot.payloads`.
  Фиксирует «wire не знает платформы».
- `test_platform_backend.py` — фабрика: `win32`→Windows, `darwin`→Mac (с моком, без
  требования pyobjc), прочее → `UnsupportedPlatformError`; ленивость импортов.
- Архитектурный тест зависимостей (расширение существующего no-QtWidgets) —
  инварианты графа из Секции 1.
- **Ручная процедура** (не unit, записывается как record): round-trip
  html/png/web-URL через `publish` + `⌘V` в реальных приложениях (TextEdit, Notes,
  Safari); подтверждение host-only (контент не всплывает на других устройствах
  Handoff).

## Секция 6 — Зависимости, протокол, сборка

- **Новая зависимость — только macOS:** `pyobjc-framework-Cocoa` (AppKit/Foundation/
  NSPasteboard), **не** метапакет `pyobjc`. Environment-маркер `; sys_platform ==
  "darwin"`. Точное место (строка в `requirements-build.txt` с маркером либо
  отдельный `requirements-macos.txt`) закрепляется в плане. Ленивые импорты
  гарантируют, что Windows-граф пакет не трогает.
- **Протокол не меняется:** `wire` = `PROTOCOL_MAJOR=1, MINOR=0`. Parity не
  добавляет типов сообщений. Win↔Mac совместимость — на уровне канонических
  payload'ов (Секция 3). Файловый milestone определит собственные protocol/
  capability requirements отдельно; этот spec **не принимает** решения о
  необходимости `PROTOCOL_MAJOR=2`.
- **Packaging вне scope** этого этапа (`.app`/подпись/нотаризация/`.dmg` — этап 5).
  Достаточно запуска из `.venv-mac`/исходников для разработки и ручной проверки.
  Apple Developer Program, Developer ID signing и notarization **не являются
  prerequisite** этого milestone; разработка и acceptance выполняются из
  `.venv-mac`.
- **Dev-окружение:** `.venv-mac` (Python 3.14.4, PySide6 6.10.1 universal2). Тесты
  на Mac: `.venv-mac/bin/python -m pytest`.

## Сводка инвариантов

1. `clipboard/**` не импортирует `QtWidgets`.
2. Граф зависимостей платформенной границы (Секция 1) — pyobjc только в
   `macos_pasteboard.py`; фабрика с ленивыми импортами.
3. Содержимое буфера никогда не пишется на диск.
4. Подавление петли корректно при `new != old` и `current == own_change_count`; не
   зависит от инкремента `+1` и от выживания `ORIGIN_MIME`.
5. Лимит 32 MiB — после нормализации.
6. `text/uri-list` — только веб-URL (`http`/`https`) на обеих платформах.
7. `image/png` на wire — всегда PNG, независимо от источника.
8. host-only при публикации на macOS; ссылка на data provider удерживается.
9. Протокол `wire` не меняется (1.0).

## Проверить при реализации (микро-проверки, не блокеры дизайна)

- Переживают ли `text/html` и `image/png` round-trip при `publish` + `⌘V`
  (для текста спайк это доказал).
- Точный вид имён `org.nspasteboard.ConcealedType`/`TransientType` и origin-UTI,
  как их отдаёт Qt в `mime_data.formats()`.
- Переживает ли кастомный `ORIGIN_MIME` round-trip через NSPasteboard/Qt (если нет
  — работает только пояс `changeCount`, что и заложено инвариантом).
- Что фактически возвращает `imageData()` на macOS (ожидаем `QImage` или
  `QPixmap` — оба поддержаны Секцией 3; иной тип → `None`).

## Технический долг (не в этом milestone)

- `is_private` смешивает две семантики: `origin ≠ private` (разные причины
  игнорировать). Разделить на `is_private` / `is_own_origin` — позже, если Windows-
  контракт будет рефакториться.
- `application_directory()` на darwin использует `~/.local/share`; каноничнее
  `~/Library/Application Support`. Опциональное улучшение.
- Общий `transfer/`-слой — вводится в файловом milestone, когда появятся реальные
  требования от Finder, а не предположения.
