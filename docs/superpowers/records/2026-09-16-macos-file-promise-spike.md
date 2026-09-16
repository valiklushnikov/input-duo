# Спайк: материализация файлов в Finder на ⌘V (macOS)

**Дата:** 2026-09-16
**Машина:** macOS 15.1.1, Python 3.14.4 (`.venv-mac`), pyobjc 12.2.2
**Статус:** throwaway-спайк, код выброшен; результат — шлагбаум для дизайна
Windows→Mac (передача файлов, milestone 2).

## Вопрос

Чтит ли Finder ленивое обещание файла (`NSFilePromiseProvider`) на `⌘V`
(вставка в папку), а не только на drag-and-drop? От этого зависит, можно ли
построить приёмник Windows→Mac на ленивых промисах, сохранив инвариант
«скопированное и не вставленное не покидает машину-источник».

## Метод

Минимальный throwaway-харнесс на pyobjc:

1. `NSFilePromiseProvider(fileType="public.plain-text", delegate=…)` с делегатом,
   реализующим `filePromiseProvider:fileNameForType:`,
   `operationQueueForFilePromiseProvider:`,
   `filePromiseProvider:writePromiseToURL:completionHandler:`.
2. `prepareForNewContentsWithOptions_(NSPasteboardContentsCurrentHostOnly)` +
   `writeObjects_([provider])` на `generalPasteboard`.
3. `NSApplication.run()` + `NSTimer` — качаем run loop, логируем каждый callback
   с thread id и временем.
4. Оператор переключается в Finder, открывает папку, жмёт `⌘V`.

Контрольная проверка: тем же способом положить на буфер настоящий
`NSURL.fileURLWithPath_` (`public.file-url`) и вставить в Finder.

## Результат

**1. `NSFilePromiseProvider` на `⌘V` — не работает.**
Промис объявлен корректно: `writeObjects ok=True`, `pasteboard.types()` содержит
`com.apple.pasteboard.promised-file-name`,
`com.apple.pasteboard.promised-file-content-type`,
`com.apple.NSFilePromiseItemMetaData` и др. Оператор нажал `⌘V` в Finder —
**файл не появился**, и делегат **не был вызван ни разу**: в логе нет ни
`fileNameForType`, ни `writePromiseToURL` за все 60 с наблюдения. Finder не
инициирует материализацию обещания на вставку.

**2. Настоящий `file://` URL на буфере — Finder вставляет копию.**
`writeObjects_([NSURL.fileURLWithPath_(path)])` даёт `public.file-url`,
`NSFilenamesPboardType`, `Apple URL pasteboard type`. Оператор нажал `⌘V` —
**файл-копия появился** в целевой папке. Данные на буфере лежат eager (не
ленивы) и переживают выход процесса-владельца.

## Вывод (шлагбаум)

Ленивый промис-путь (аналог Windows `IDataObject`+`IStream`, где Проводник
тянет байты на вставку) на macOS **недоступен для `⌘V`**. Приёмник Windows→Mac
обязан **материализовать настоящие файлы на диск (staging)** и класть на буфер
их `file://` URL; иного механизма, который Finder вставляет, спайк не нашёл.

Прямое следствие для дизайна: инвариант «скопированное и не вставленное не
покидает машину-источник» на приёмнике Mac **нельзя сохранить нативным
ленивым чтением Finder'а**. Он либо приносится в жертву (eager staging по
FILE_OFFER), либо заменяется явным согласием пользователя в UI Duo Input перед
staging (transfer стартует по действию пользователя, а не по `Ctrl+C`). Выбор
между этими вариантами — предмет дизайна, а не спайка.

## Не установлено (за рамками этого спайка)

- Читает ли Finder **лениво предоставленный** `public.file-url` (через
  `NSPasteboardItem.setDataProvider_forTypes_`) — то есть вызывает ли он наш
  провайдер на вставку, дав нам paste-hook. Если да — это третий вариант
  (ленивый staging на чтение file-url), но с синхронной блокировкой Finder на
  время скачивания и без нативного прогресса/отмены. Требует отдельного спайка,
  если этот вариант войдёт в дизайн.
