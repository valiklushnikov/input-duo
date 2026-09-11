# Приёмка: macOS clipboard parity (Task 8 Step 4, локальная)

**Дата:** 2026-09-12
**Машина:** macOS 15.1.1, Python 3.14.4, PySide6 6.10.1, pyobjc-framework-Cocoa 12.2.2 (`.venv-mac`)
**Ветка:** feature/macos-clipboard-parity (база — feature/pio-usb-host-hub-v1)
**Метод:** запуск из `.venv-mac`/исходников на реальной сессии macOS (без packaging, как разрешает spec §6).

Локальная приёмка milestone. Проверки, объективно требующие второго
компьютера/paired peer, явно отмечены как **pending external E2E** и НЕ считаются
выполненными.

## 1. Privacy markers — БАГ НАЙДЕН И ИСПРАВЛЕН ✓

**Находка:** Qt на macOS НЕ отдаёт `org.nspasteboard.ConcealedType` /
`org.nspasteboard.TransientType` в `QClipboard.mimeData().formats()` — для скрытого
буфера Qt показывает только `['text/plain']`. Прежний `is_private(formats)` эти
маркеры не видел → **скрытый (concealed) пароль был бы синхронизирован**. Нативный
`NSPasteboard.generalPasteboard().types()` маркеры отдаёт:
`['public.utf8-plain-text','NSStringPboardType','org.nspasteboard.ConcealedType']`.

**Фикс (коммит 76b87b1, TDD, отдельный, task-review Approved):** детект приватности
перенесён в нативный код — `macos_pasteboard.is_concealed()` читает `types()`;
`macos_backend._poll()` вызывает натив-гейт до снятия снимка; мёртвые
nspasteboard-строки убраны из Qt-format `is_private` (оставлен только ORIGIN_MIME
как belt подавления петли).

**E2E-проверка через реальный `MacOSClipboardBackend._poll` (реальный pasteboard):**
- concealed → snapshot НЕ эмитится ✓
- transient → snapshot НЕ эмитится ✓
- обычный текст → эмитится, `payloads['text/plain'] == b'normal text'` ✓

## 2. Native publish → внешнее чтение всех форматов ✓

Публикация offer со всеми четырьмя форматами и счётчиком fetch; чтение из
**отдельного процесса** (эквивалент ⌘V — внешний читатель материализует promise),
publisher прокачивает run loop для IPC promise.

| UTI | доставлено | содержимое |
|---|---|---|
| public.utf8-plain-text | 27 б | `acceptance plain текст` (UTF-8) ✓ |
| public.html | 22 б | `<b>acceptance html</b>` ✓ |
| public.png | 103 б | валидный PNG (`\x89PNG…`) ✓ |
| public.url | 32 б | `https://example.com/acceptance\r\n` ✓ (см. нюанс) |

**Нюанс (fidelity, Important #2 ревьюера):** `text/uri-list` маппится на `public.url`
с хвостовым `\r\n`. Байты доставляются, но нативный `public.url` ожидает один URL
без переводов строки. Реальная визуальная вставка веб-URL в Safari/Chrome человеком
не проверена — оставлено как **опциональная человеческая проверка** (не блокер
доставки; при подтверждённой проблеме — тривиальный follow-up: для одиночного
web-URL публиковать `public.url` без завершающего CRLF).

## 3. Finder file:// не синхронизируется ✓

Нативно записан `public.file-url = file:///etc/hosts`. Qt отдаёт `text/uri-list`
c `file:///etc/hosts`, но `macos_backend.snapshot_from(...)` даёт **пустой**
`payloads` (`web_uri_list` вырезает не-http(s)). file:// в снимок не попадает.

## 4. Ленивость публикации ✓

Сразу после `publish_with_origin(offer, fetcher)` счётчик fetch = **0**; fetch'и
(4 шт., по одному на формат) происходят только при фактическом внешнем чтении.
Скопированный на пире и не запрошенный контент машину не покидает до запроса.

## Опциональная человеческая проверка (не блокер, не выполнена)

Визуальный рендеринг вставки в конкретных GUI-приложениях (TextEdit rich → HTML,
Preview/Notes → PNG, Safari address bar → URL). Доставка байтов провайдером на
уровне pasteboard доказана внешним читателем (эквивалент ⌘V); отдельно стоит
глазами подтвердить URL-fidelity в Safari (см. нюанс §2).

## Pending external E2E (требуют второго компьютера/paired peer)

- Полный Windows↔macOS обмен буфером (copy на одной → paste на другой).
- Полный macOS↔macOS обмен.
- Подавление петли через две реальные машины.
- host-only / Universal Clipboard: контент не всплывает на другом устройстве Apple
  через Handoff до вставки (нужен второй Apple-девайс с тем же Apple ID).

## Итог

Локальная macOS-приёмка пройдена: приватность (с фиксом), доставка всех форматов,
фильтрация file://, ленивость — подтверждены на реальной сессии. Полный
кросс-машинный E2E и host-only остаются pending external и требуют парного стенда.
