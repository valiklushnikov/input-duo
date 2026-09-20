# File Provider — полная регрессионная матрица (AUTOMATABLE + MANUAL/E2E)

**Дата:** 2026-09-20
**Задание:** Task 19 (`.superpowers/sdd/2026-09-19-macos-file-provider-production-implementation/task-19-brief.md`).
**Статус:** COMPLETE. AUTOMATABLE-часть — исполняемый тест
(`configurator/tests/transfer/test_fileprovider_matrix.py`, 21 параметризованных
строк, все GREEN). Этот файл — её напарник: то же покрытие спецификации, но
для строк, которым нужен человек, реальный Finder, реальный `fileproviderd`
или чистая машина, — то, что `pytest` в принципе не может проверить.

## Легенда тегов

| Тег | Значение |
|---|---|
| **AUTOMATABLE** | Уже исполняется в `test_fileprovider_matrix.py`. Здесь — только ссылка на id строки, без отдельной ручной процедуры. |
| **MANUAL** | Нужен человек за реальным Finder/macOS UI на РАБОЧЕЙ (не обязательно чистой) машине с уже установленным и авторизованным File Provider доменом. Не входит и не должна входить в автоматический suite. |
| **CLEAN_MACHINE_REQUIRED** | Как MANUAL, но дополнительно требует ЧИСТОГО macOS-аккаунта/машины без ранее зарегистрированного domain — см. отдельный раздел ниже. |

**Важное ограничение (controller ruling, task-19):** ни одна MANUAL- или
CLEAN_MACHINE_REQUIRED-строка никогда не должна появляться как падающий или
`xfail` автоматический тест. Если строка требует человека — она живёт только
здесь, прозой, а не в `pytest`.

**Явно вне рамок (out of scope):** воспроизведение или починка "загрязнённого"
(polluted) dev-инстанса `fileproviderd` (см.
`docs/superpowers/records/fileprovider-xpc-servicing.md` — "reboot НЕ лечит
FS-wedge"). Если во время ручного прогона `fileproviderd` оказывается в таком
состоянии — это самостоятельный инцидент, а не провал этой матрицы; чинить его
здесь не пытаемся, а фиксируем как blocked и продолжаем на другой машине/после
переустановки.

---

## AUTOMATABLE строки (см. `test_fileprovider_matrix.py`)

Все ниже — реальное поведение реальных backend'ов (`FileProviderBackend`,
`MacFileReceiver`, `MacReceiveRouter`, `_ClipboardRuntime`) через
fake-link/fake-sender харнессы из Tasks 8/9/13/14/15/16 (никаких мокнутых
внутренностей). Полный список id строк и их файл:
`configurator/tests/transfer/test_fileprovider_matrix.py::test_matrix_row`.

| Строка | id в матрице |
|---|---|
| Один маленький файл | `single_small_file` |
| Нулевого размера | `zero_byte_file` |
| Несколько плоских файлов | `multiple_flat_files` |
| Вложенная директория (depth ≥ 2) | `nested_directory` |
| Cancel — в очереди (queued) | `cancel_queued` |
| Cancel — активный (admitted, чтение не начато) | `cancel_active` |
| Cancel — с чтением "в полёте" (in-flight) | `cancel_in_flight` |
| Источник изменился (`source_changed`) | `source_changed` |
| Источник исчез (`source_missing`) | `source_missing` |
| Разрыв связи с peer (`link.disconnected`) | `peer_disconnect` |
| Повторный paste уже завершённой generation | `repeated_paste` |
| Новый clipboard, пока старый fetch ещё активен | `new_clipboard_while_old_fetch_active` |
| Протухший Accept после supersede → ни publish, ни replica, ни arm, ни read (FP) | `stale_accept_after_supersede_no_publish_replica_arm_read` |
| Ask — отказ (deny) | `ask_deny` |
| Ask — согласие (accept) | `ask_accept` |
| Auto (авто-приём без вопроса) | `auto_accept_mode` |
| File Provider недоступен → staging | `fp_unavailable_routes_to_staging` |
| Unicode-имена | `unicode_names` |
| Одинаковое имя файла в разных директориях | `duplicate_names_in_different_directories` |
| **FP privacy:** авторизовано + armed + БЕЗ ⌘V → **ноль** `FILE_READ` | `fp_privacy_no_cmd_v_zero_file_read` |
| **Staging privacy** (сознательно eager, семантика не менялась): авторизовано → скачивание МОЖЕТ начаться сразу → armed только после завершения. Здесь **не** проверяется "ноль `FILE_READ`" — это было бы проверкой поведения, которого у staging никогда не было. | `staging_privacy_may_begin_then_arm_after_complete` |

---

## MANUAL строки

Общие предусловия для всех MANUAL-строк, если не сказано иное:

- Duo Input собран через боевой gate
  (`configurator/scripts/nuitka-build-macos.sh`), подписан, установлен как
  `.app` + `.appex`, File Provider domain уже зарегистрирован и READY
  (см. `fileprovider-xpc-servicing.md` — Phase 10.2 gates PASS).
- Два физических (или два процесса/пользователя) хоста: **Sender** (Windows
  или второй Mac с Duo Input) и **Receiver** (Mac с File Provider extension).
- Наблюдение идёт как минимум в двух местах: Finder (визуально) и
  `log stream --predicate 'subsystem == "com.duoinput.configurator"'`
  (структурные строки из `_log_event`/`counters`, см. Task 17) — как для
  wire-уровня, так и для XPC.

### 1. Большой файл (MANUAL)

**Зачем автомату не даётся:** нужен реальный Finder-paste и визуальное
наблюдение прогресса/`NSProgress` в Finder — сам факт правильного числа и
скорости не проверяется юнит-тестом.

1. На Sender подготовить файл ~500 МБ - 1 ГБ.
2. Скопировать его (⌘C) на Sender.
3. На Receiver вставить (⌘V) в Finder.
4. Ожидаемое: Finder показывает прогресс, файл появляется по частям
   (`NSFileProviderManager` materializes lazily), итоговые байты совпадают
   (`shasum` до/после).
5. В логе — монотонно растущий `fp_bytes_received`, ровно один
   `fp_fetch_completed` на файл, `fp_active_fetches` возвращается к 0.

### 2. Много-гигабайтный файл (MANUAL)

**Зачем автомату не даётся:** реальное время передачи (десятки минут-часы),
реальная память хоста, реальный `MAX_TOTAL_BUFFERED_BYTES`-backpressure под
настоящей нагрузкой ОС, а не фейковым `send()`.

1. Файл ≥ 4 ГБ (можно синтетический, `dd`/`fsutil`).
2. Тот же сценарий, что и "большой файл", но дополнительно:
   - следить за RSS процесса Duo Input (`Activity Monitor`/`ps`) — должен
     оставаться ограниченным (`MAX_TOTAL_BUFFERED_BYTES`, по умолчанию 8 МБ
     буфера "в полёте", а не размер файла) — это ручная проверка того, что
     `test_outstanding_bytes_stay_bounded_regardless_of_total_file_size`
     (Task 11, автоматический) верно отражает поведение под реальной ОС;
   - убедиться, что копирование можно отменить из Finder на середине
     (`NSProgress.cancellationHandler` → `cancelFetch`) без зависания UI.
3. Ожидаемое: RSS не растёт линейно с размером файла; итоговые байты (для
   некансельнутого прогона) совпадают побайтово.

### 3. Перезапуск хоста (host restart) (MANUAL)

**Зачем автомату не даётся:** требует реальной перезагрузки ОС/сна — то, что
юнит-тест с фейковым `time_ns`/`QTimer` не воспроизводит.

1. Начать копирование большого файла (см. п.1), не дожидаясь завершения.
2. Перезагрузить Sender (или усыпить и разбудить).
3. Ожидаемое на Receiver: `link.disconnected` срабатывает (реальный
   `PeerLink`, не `FakeLink`) → `fp_ipc_disconnect` в логе, все активные
   fetch settle'ятся с `PeerLost`/`NotConnected` (см. AUTOMATABLE
   `peer_disconnect` — здесь то же самое, но по-настоящему), Finder не
   зависает и не показывает файл как "готовый" с неполными байтами.
4. После того как Sender снова онлайн — новое копирование должно работать
   штатно (без необходимости перезапускать Receiver).

### 4. Перезапуск extension (extension restart) (MANUAL, СИСТЕМНО-УПРАВЛЯЕМЫЙ)

**Критично:** перезапуск `.appex`-процесса **никогда** не делается через
`kill -9` вручную — это не то, что происходит в реальной эксплуатации, и
`kill -9` тестирует другой (более грубый и менее реалистичный) отказ, чем тот,
что действительно случается на проде. Перезапуск должен быть
СИСТЕМНО-ИНИЦИИРОВАННЫМ: `killall -TERM "Duo Input FileProvider"` (graceful),
пересборка/переустановка `.appex` через `pluginkit`/System Settings → File
Provider extensions → toggle off/on, либо естественный memory-pressure jetsam
со стороны `fileproviderd`. Причина — `kill -9` не даёт extension шанса
отпустить XPC-listener штатно, и наблюдаемое поведение перестаёт
соответствовать тому, что реально происходит при graceful supervision
restart, которым управляет сама macOS.

1. Начать копирование (или просто иметь armed clipboard с активным доменом).
2. System Settings → General → Login Items & Extensions → File Providers →
   выключить и снова включить расширение Duo Input (или
   `pluginkit -e ignore -i <bundle-id>` затем `-e use`).
3. Ожидаемое: активные fetch'и на этот момент settle'ятся как `PeerLost`
   (реальный XPC-disconnect, а не wire-уровень) или Finder честно показывает
   ошибку — никакого зависшего "крутящегося" прогресса навсегда.
4. После повторного включения — новый offer/paste должен снова публиковать
   generation и работать штатно (домен переходит в READY заново, см.
   `fp_domain_state`/`fp_domain_not_ready` в логе).

### 5. Диск переполнен (disk full) (MANUAL)

**Зачем автомату не даётся:** нужен реальный ENOSPC от файловой системы —
фейковый `send()`/`StagingArea` в юнит-тестах никогда не пишет на настоящий
диск объёмом, который можно исчерпать управляемо.

1. На Receiver ограничить свободное место (например, создать APFS
   sparse-image небольшого размера и работать внутри него как `$HOME`, либо
   заполнить диск балластным файлом до нескольких оставшихся сотен МБ).
2. Скопировать файл, который заведомо не помещается.
3. Ожидаемое: сценарий деградирует НАБЛЮДАЕМО (ошибка в Finder,
   `DuoFPErrorDiskFull`/код 6 — see `_FILE_ERROR_REASON_CODES["disk_full"] = 6`
   — или staging-путь честно репортит `transfer_failed`), а не тихо
   обрезанный/повреждённый файл.
4. После освобождения места — повторная попытка (новый offer) должна пройти
   штатно.

### 6. Domain not ready (MANUAL)

**Зачем автомату не даётся:** сам факт "домен ещё не зарегистрирован
`fileproviderd`" на верхнем уровне ОС в момент реального старта приложения —
таймингово-зависимая гонка с самим `fileproviderd`, не воспроизводимая через
фейковый `FakeDomain(is_ready=False)` (это АВТОМАТИЗИРОВАНО отдельно, см.
`fp_unavailable_routes_to_staging` в AUTOMATABLE-разделе — но там домен уже
инстанцирован как объект, просто `is_ready=False`; здесь домен буквально ещё
не существует в `fileproviderd` на диске).

1. Свежий (или предварительно unregister'нутый через
   `NSFileProviderManager.removeDomain`) домен.
2. Запустить Duo Input и СРАЗУ (до появления домена в
   `NSFileProviderManager.getDomainsWithCompletionHandler_`) инициировать
   входящий transfer с Sender.
3. Ожидаемое: `MacReceiveRouter` выбирает staging (домен не READY →
   `fp_backend_selected_staging`), файл приходит через staging-путь без
   ошибок пользователю, и как только домен становится READY — ПОСЛЕДУЮЩИЕ
   (не эта же) передачи используют File Provider.

---

## CLEAN_MACHINE_REQUIRED

### 7. Чистая машина, свежий domain (CLEAN_MACHINE_REQUIRED)

**Зачем нужна именно чистая машина:** это единственный способ доказать, что
ПЕРВАЯ РЕГИСТРАЦИЯ домена (`NSFileProviderManager.add(domain:...)`),
ПЕРВОЕ разрешение прав `NSFileProviderExtension`/полного доступа к диску и
ПЕРВОЕ появление тома в Finder работают без опоры на состояние, случайно
оставшееся от предыдущих ручных прогонов этой же матрицы (протухшие
replica-записи, зарегистрированный, но "грязный" domain, старые XPC-listener'ы
и т.п.). Любая уже использовавшаяся для разработки/тестирования машина по
определению НЕ подходит — на ней `fileproviderd` мог накопить состояние,
которое случайно маскирует реальный баг первого запуска либо, наоборот,
создаёт ложный сбой, не связанный с кодом Duo Input (см. "вне рамок" ниже).

**Как получить чистую машину:** новый пользователь macOS (`System Settings →
Users & Groups → Add`) на существующем железе — это ДОСТАТОЧНО чисто для
File Provider (домены/расширения per-user), полная переустановка ОС не
обязательна, если только предыдущие прогоны не трогали общесистемные
(root/LaunchDaemon) части.

1. Новый пользователь macOS, ранее НИКОГДА не запускавший Duo Input.
2. Установить Duo Input (подписанный `.pkg`/`.app` из боевого gate).
3. Первый запуск: разрешить File Provider extension при системном запросе,
   дождаться, что домен появляется в Finder sidebar САМ (без ручного
   вмешательства кроме стандартного macOS-разрешения).
4. Провести один полный round-trip (offer → Ask accept → paste → чтение
   байт) — покрывает то же, что и AUTOMATABLE `single_small_file`, но через
   ПОЛНЫЙ, ни разу не тронутый стек: `fileproviderd` регистрация +
   XPC-listener bootstrap + `NSFileProviderReplicatedExtension` conformance
   (см. `docs/superpowers/records/fileprovider-xpc-servicing.md` —
   "extension обязан конформить NSFileProviderServicing иначе getService=nil").
5. Ожидаемое: без ручных обходов (никаких `pluginkit -e reset`, никакой
   ручной регистрации домена в Terminal) — том появляется, paste работает,
   байты совпадают.

**Явно вне рамок этой строки:** если на шаге 3 домен НЕ появляется потому,
что `fileproviderd` этого конкретного макоса уже "заклинило" (wedge) до
Duo Input — это не баг Duo Input и не повод чинить `fileproviderd`
(см. общий раздел "out of scope" выше и
`fileprovider-xpc-servicing.md`: "reboot НЕ лечит FS-wedge"). В этом случае —
взять другую чистую машину/пользователя, а инцидент зафиксировать отдельно.
