# Спайк: ленивость clipboard на macOS

**Дата:** 2026-09-11
**Машина:** macOS 15.1.1 (build 24B91)
**Окружение:** Python 3.14.4, PySide6 6.10.1 (universal2 wheel, cp39-abi3), venv `.venv-mac`
**Скрипт:** `configurator/tests/clipboard/spike_lazy_mimedata_macos.py`

## Вопрос

Вызывает ли на macOS `QClipboard.setMimeData(RemoteMimeData(...))` метод
`retrieveData()` сразу (материализует данные в момент копирования), или лениво —
только когда содержимое действительно кто-то читает (⌘V)? От этого зависит, можно
ли перенести существующую `RemoteMimeData` на macOS, или нужен нативный путь через
`NSPasteboard` / file promises.

## Метод

Как и в Windows-спайке, наивная проверка в том же процессе ничего не доказывает:
Qt отдаёт тот же Python-объект напрямую. Ленивость проверялась чтением pasteboard
**из другого процесса** — встроенной утилитой `/usr/bin/pbpaste` (читает
NSPasteboard через AppKit) — и **ручной вставкой** в реальные приложения. Popen +
poll, чтобы не заблокировать цикл событий Qt (владелец pasteboard должен крутить
runloop, чтобы ответить на запрос отложенных данных).

Три прогона:
1. Авто (pbpaste).
2. Авто (pbpaste) + ручная вставка в TextEdit/Notes/Safari.
3. **Чистый** ручной прогон без pbpaste (`--manual-only`).

## Наблюдения

**Прогон 1 и 2 (с pbpaste):**
```
retrieveData('application/x-qt-mime-type-name') called   ← при setMimeData
calls right after setMimeData: 1
retrieveData('text/plain') called                        ← только при чтении pbpaste
DELTA caused by the cross-process read: 1
pbpaste got our payload: True
```
В прогоне 2 ручные вставки в TextEdit/Notes/Safari **после** pbpaste новых вызовов
`retrieveData` не породили.

**Прогон 3 (чистый, без pbpaste):**
```
retrieveData('application/x-qt-mime-type-name') called   ← при setMimeData
calls right after setMimeData: 1
retrieveData('text/plain') called                        ← при ПЕРВОЙ вставке (TextEdit), ~13с спустя
```
`text/plain` дёрнулся ровно один раз — в момент первой вставки в TextEdit.
Последующие вставки в Notes и Safari вызовов не добавили.

## Вывод: ЛЕНИВОСТЬ ПОДТВЕРЖДЕНА

1. **Реальные данные тянутся лениво.** `text/plain` не материализуется при
   `setMimeData` — только при первом фактическом чтении pasteboard. Скопированное
   и не вставленное содержимое машину не покидает. `RemoteMimeData` переносится на
   macOS **без нативного NSPasteboard-моста** для clipboard parity (текст/HTML/PNG).

2. **Служебный вызов при setMimeData.** Qt при регистрации типа делает один вызов
   `retrieveData('application/x-qt-mime-type-name')`. Это не наши данные.
   `RemoteMimeData` обязана отвечать на него **без обращения к сети** (текущая
   реализация через `base_mime` вернёт `b""`, т.к. тип не входит в `offer.mimes()` —
   безвредно, но должно быть покрыто тестом на macOS).

3. **Материализация — при ПЕРВОМ внешнем чтении, затем кэш.** После того как любой
   первый читатель получил данные, macOS кэширует их в pasteboard; provider больше
   не дёргается. Это отличие от Windows-модели, важное для дизайна.

## Риски и ограничения для дизайна MacOSClipboardBackend

- **Гарантия «уходит только при вставке» слабее, чем на Windows.** Материализацию
  запускает *первый внешний доступ к pasteboard*, а не строго ⌘V. На этой машине
  пассивные читатели provider не дёрнули (данные ждали реальной вставки), но
  менеджеры буфера, превьюшники и **Universal Clipboard / Handoff** потенциально
  могут прочитать pasteboard и материализовать данные без участия пользователя.
  Надо: задокументировать; рассмотреть отключение записи в Universal Clipboard
  (не помечать типы для синхронизации Handoff).
- **Тайм-аут провайдера.** macOS отводит провайдеру ограниченное время на ответ
  (данные тянет уже другой процесс через IPC, не наш event loop как на Windows).
  Сетевой fetch должен быть синхронным и быстрым, с таймаутом; при просрочке —
  пустая вставка, а не зависание.
- **Служебный тип `application/x-qt-mime-type-name`** — покрыть тестом бэкенда.

## Статус кода

`spike_lazy_mimedata_macos.py` — throwaway-проба, оставлена в `tests/clipboard/`
рядом с Windows-аналогом по конвенции репозитория. Production-кода не порождает.
