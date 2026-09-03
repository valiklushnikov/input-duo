# Спайк: ленивая ли отдача данных из буфера обмена на Windows

Дата: 2026-09-03
Окружение: Windows 10 Pro, версия 10.0.19045; Python 3.12.10; PySide6 6.10.1 (Qt 6.10.1)
Скрипты: `configurator/tests/clipboard/spike_lazy_mimedata.py` (процесс A, Qt),
`configurator/tests/clipboard/spike_read_clipboard.py` (процесс B, голый ctypes)

## Поправка к методике брифа

Бриф предписывал ручную проверку: запустить скрипт и вставить в Блокнот
руками. От этого отказались по решению контроллера: если положить в буфер
подкласс `QMimeData` и прочитать его же в **том же процессе** (а именно так
скорее всего повёл бы себя Блокнот через Qt-совместимый путь, и уж точно так
ведёт себя любой in-process тест), Qt может отдать тот же Python-объект
напрямую, не проходя через отложенную отрисовку Windows. Отложенную
отрисовку (`WM_RENDERFORMAT`) запускает только чтение буфера **из другого
процесса** через голый Win32 API. Поэтому проверка сделана автоматической и
воспроизводимой:

- Процесс A (Qt) кладёт в буфер обмена подкласс `QMimeData` со счётчиком
  вызовов `retrieveData`.
- Внутри уже работающего цикла событий Qt (через `QTimer.singleShot`, не до
  `application.exec()`) процесс A запускает процесс B.
- Процесс B — обычный Python, только `ctypes` и стандартная библиотека, без
  Qt — открывает буфер обмена Windows напрямую (`OpenClipboard` /
  `GetClipboardData(CF_UNICODETEXT)` / `GlobalLock` / `CloseClipboard`) и
  печатает прочитанный текст.
- Процесс A печатает, сколько раз `retrieveData` был вызван до запуска
  процесса B и сколько — после.

## Найденная попутная проблема: взаимная блокировка

Первая версия процесса A запускала процесс B через блокирующий
`subprocess.run(..., timeout=15)`, вызванный из колбэка `QTimer.singleShot`.
Результат — гарантированный дедлок: `GetClipboardData` в процессе B
блокируется внутри Windows в ожидании, что процесс A ответит на
`WM_RENDERFORMAT`, а процесс A не может обработать это оконное сообщение,
пока его поток занят в `subprocess.run`, ожидая завершения процесса B.
Процесс B провалился по `subprocess.TimeoutExpired` через 15 секунд:

```
calls right after setMimeData: 0
calls right before launching process B: 0
Traceback (most recent call last):
  ...
  File "...\Lib\subprocess.py", line 1630, in _communicate
    raise TimeoutExpired(self.args, orig_timeout)
subprocess.TimeoutExpired: Command '[...python.exe, ...spike_read_clipboard.py]' timed out after 15 seconds
```

Исправление: процесс B запускается через `subprocess.Popen` (неблокирующий),
а завершение отслеживается `QTimer` с интервалом 50 мс, который на каждом
тике возвращает управление в цикл событий Qt между проверками
`process.poll()`. Это даёт Qt возможность обработать `WM_RENDERFORMAT`, пока
процесс B ждёт ответа. После исправления скрипт стабильно завершается.

## Точный вывод скрипта (два независимых запуска)

Запуск 1:
```
calls right after setMimeData: 0
calls right before launching process B: 0
[688367.453] retrieveData('text/plain;charset=utf-8') called, total calls so far: 1
--- process B stdout ---
process B read from Win32 clipboard: 'lazy payload'
--- process B exit code: 0 ---
calls right after process B read: 1
DELTA caused by the cross-process read: 1
total retrieveData calls: 1
[688367.484] retrieveData('text/plain;charset=utf-8') called, total calls so far: 2
```

Запуск 2 (повтор для проверки воспроизводимости):
```
calls right after setMimeData: 0
calls right before launching process B: 0
[688386.515] retrieveData('text/plain;charset=utf-8') called, total calls so far: 1
--- process B stdout ---
process B read from Win32 clipboard: 'lazy payload'
--- process B exit code: 0 ---
calls right after process B read: 1
DELTA caused by the cross-process read: 1
total retrieveData calls: 1
[688386.531] retrieveData('text/plain;charset=utf-8') called, total calls so far: 2
```

Оба запуска идентичны по структуре и результату.

## Разбор результата

- Вызовов `retrieveData` сразу после `setMimeData`: **0**.
- Вызовов, вызванных именно межпроцессным чтением буфера (`DELTA`): **1**.
  Процесс B получил ровно то содержимое, которое `retrieveData` вернул
  (`'lazy payload'`) — то есть данные были материализованы по требованию
  Windows в момент запроса из чужого процесса, а не заранее.
- Qt запросил `mime_type` как `'text/plain;charset=utf-8'`, а не буквально
  `'text/plain'` — это нормализация Qt-обёртки над `CF_UNICODETEXT`; на
  результат проверки не влияет, поскольку `LazyMimeData.formats()`
  анонсирует `text/plain`, а `retrieveData` в реализации Qt вызывается для
  запрошенного MIME-типа с указанием кодировки.
- После завершения цикла событий (`application.exec()` уже вернул
  управление, `"total retrieveData calls: 1"` уже напечатано) произошёл ещё
  один вызов `retrieveData` — второй и последний. Он приходится на
  уничтожение `QApplication` при завершении процесса и объясняется
  стандартным поведением OLE-буфера обмена на Windows: когда процесс,
  владеющий буфером с отложенно отрисованными форматами, завершается, он
  обязан отрисовать (`OleFlushClipboard`) все анонсированные форматы, чтобы
  содержимое буфера пережило завершение процесса-владельца. Это отдельный,
  ожидаемый триггер (закрытие процесса-источника), а не нарушение ленивости
  при `setMimeData`. Он не проверялся руками (само его наличие видно по
  логу), важно лишь, что он произошёл **после** `setMimeData` и **после**
  наблюдаемого межпроцессного чтения, а не вместо ленивости.

## Ответ на ключевой вопрос

**Ленивая отдача подтверждена.** `retrieveData` вызывается ноль раз в момент
`setMimeData` и ровно один раз, когда буфер читает другой процесс через
Win32 API. Воспроизведено в двух независимых запусках с идентичным
результатом.

## Решение по шлагбауму

Допущение спеки (лениво отдавать данные через `QMimeData`, не тратя ресурсы
до тех пор, пока их кто-то не запросит) подтверждено экспериментально на
целевой платформе (Windows 10 Pro 10.0.19045, PySide6 6.10.1). Задачу 2
можно начинать. Дополнительно стоит учесть в дизайне: процесс-источник
должен либо оставаться живым, пока данные могут понадобиться, либо явно
сбрасывать буфер (`QClipboard` делает `OleFlushClipboard` автоматически при
завершении Qt-приложения) — иначе последний живой держатель определяет, что
именно останется в буфере после его закрытия.
