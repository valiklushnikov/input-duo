# File Provider: rollout (флаг, fallback, телеметрия) — Task 20

**Дата:** 2026-09-20
**Статус:** SHIPPED (Stage 1) — флаг по умолчанию выключен, staging остаётся
дефолтным путём приёма файлов на macOS.

## TL;DR

`clipboard/fileprovider_enabled` — новый settings-ключ, **по умолчанию
`False`** (`app.py:383`, `QSettings.value("clipboard/fileprovider_enabled",
False, type=bool)`). С флагом выключенным поведение byte-for-byte совпадает
с тем, что было до Task 16: `_build_fileprovider_kwargs()` не строит ни
домен, ни XPC-клиент, ни `FileProviderBackend` — `MacReceiveRouter` получает
`fileprovider_backend=None` и на КАЖДОМ offer выбирает staging
(`MacFileReceiver`). Staging **не удалён и не тронут** — это по-прежнему
единственный путь при выключенном флаге и постоянный fallback при
включённом (см. Stage 3/4 ниже).

Откат = выключить флаг. Поскольку выбор backend'а фиксируется **один раз на
offer** (`MacReceiveRouter._select_backend`, Task 16), а не на "сессию"
и не на "процесс", откат не требует миграции данных: уже идущая передача
(если она была на File Provider) доигрывается на нём, а вот СЛЕДУЮЩ�ий offer
после отката уже пойдёт через staging — без перезапуска приложения
(флаг читается живьём на каждый offer, `app.py:378-383`'s docstring).

## Stage 1 — флаг выключен, staging по умолчанию (текущая поставка)

- `clipboard/fileprovider_enabled` отсутствует в settings ИЛИ явно `False`.
- `_build_fileprovider_kwargs()` возвращает только
  `{"fileprovider_flag_enabled": flag_enabled}` — ничего File
  Provider-специфичного не создаётся (ни домен, ни XPC-клиент, ни
  `FileProviderBackend`), значит нет ни одного нового процесса/сокета/домена
  на диске у обычного пользователя.
- `MacReceiveRouter` всегда выбирает `MacFileReceiver` (staging) —
  `fp_backend_selected_staging` увеличивается на КАЖДЫЙ offer через
  собственный fallback-регистр router'а (`selection_counters`, см.
  "Телеметрия" ниже), поскольку экземпляра `FileProviderBackend` для
  хранения Task 17 счётчиков ещё не существует.
- Кто получает Stage 1: 100% пользователей текущего релиза.

## Stage 2 — developer opt-in (архитектура уже доказана Phase 10.2)

- Разработчик вручную выставляет `clipboard/fileprovider_enabled=true` в
  своём `settings.ini` (или через будущий debug-toggle в UI — не сделан в
  этой задаче, см. "Не сделано" ниже).
- `_build_fileprovider_kwargs()` конструирует `FileProviderDomainManager` +
  `FileProviderServiceClient` + `FileProviderBackend`, вызывает
  `domain.ensure_domain()`/`client.connect_service()` (оба неблокирующие,
  best-effort). Любая ошибка конструирования (нет PyObjC/FileProvider
  framework, сбой обнаружения XPC, …) ловится и логируется — router молча
  падает обратно в staging-only, как будто флаг был выключен.
- Per-offer выбор (`MacReceiveRouter._select_backend`, Task 16): File
  Provider выбирается ТОЛЬКО когда ОДНОВРЕМЕННО: ОС поддерживает
  (`_FP_AVAILABLE`), флаг включён, домен `is_ready`, XPC `client.remote()`
  не `None`. Любое из условий не выполняется → offer уходит в staging
  (уже покрыто `test_fileprovider_backend_selection.py`, 4
  fallback-сценария: flag_off/domain_not_ready/service_unavailable/
  unsupported_os).
- Кто получает Stage 2: только те, кто сознательно включил флаг у себя
  (разработчики/QA на macOS с установленным `.appex`).

## Stage 3 — default ON на поддерживаемых macOS с валидным доменом

- Условие перехода: Stage 2 отработал без открытых blocker'ов на
  разработческом парке (нет неожиданных `-2011`/wedge на чистой машине —
  см. R1 в плане), packaging (Task 18) подтверждён на нескольких сборках,
  E2E-матрица (Task 19) зелёная на нескольких macOS версиях.
- Реализация перехода: `app.py:378-383` меняет дефолт `False → True`
  ТОЛЬКО когда `_FP_AVAILABLE` истинно (framework присутствует) — это
  единственная строка кода, которую нужно тронуть; сам router и телеметрия
  уже не меняются, потому что для router'а "флаг включён" не отличается по
  коду от Stage 2, отличается только то, у скольких пользователей он таким
  окажется.
- staging остаётся ЖИВЫМ и работающим as-is: он не "второй сорт", а
  штатный automatic fallback для каждого случая, когда File Provider
  недоступен/деградировал (та же 4-условная проверка, что и в Stage 2) — то
  есть Stage 3 отличается от Stage 2 только ЗНАЧЕНИЕМ дефолта, не логикой.
- Кто получает Stage 3: все пользователи на поддерживаемой версии macOS с
  успешно поднятым доменом; все остальные (старая macOS, сбой домена,
  выключенный flag через явный opt-out) — staging.

## Stage 4 — staging остаётся постоянным fallback'ом навсегда

- Даже после Stage 3 staging НЕ удаляется. Он — единственная сеть
  безопасности на:
  - macOS ниже минимальной поддерживаемой версии File Provider API;
  - отсутствие/повреждение `.appex` (переустановка, повреждённый пакет);
  - деградацию домена/XPC в рантайме (`fileproviderd` завис, extension
    рестартовал — Task 14 error mapping, Task 16 pre-publication fallback);
  - явный operator opt-out (пользователь выключил флаг руками).
- Никакого "N релизов и staging удаляем" здесь не заявляется как решённое —
  см. критерии ниже.

## Критерии пересмотра удаления staging

Staging **не удаляется** этой задачей и не планируется к удалению
автоматически. Пересмотр (не решение об удалении — решение о том, чтобы
ЗАВЕСТИ ОТДЕЛЬНУЮ задачу на обсуждение) уместен только когда ВСЕ верны:

1. **N ≥ 3 релиза подряд** с File Provider default-ON (Stage 3) в проде.
2. **Fallback rate ниже порога** (предлагается < 1% offer'ов за релиз
   попадают в `fp_backend_selected_staging` НЕ из-за выключенного флага —
   то есть считать нужно долю офферов с флагом включённым, что упало в
   staging из-за деградации, а не «выключенный по умолчанию» шум). Порог и
   точная формула — предмет отдельного обсуждения с продуктом/аналитикой
   на момент, когда телеметрия Stage 3 накопится; здесь фиксируется только
   САМ факт, что порог обязателен и должен быть измерим через уже
   существующие Task 17/20 счётчики (`fp_backend_selected_file_provider`,
   `fp_backend_selected_staging`, `fp_domain_not_ready`, `fp_ipc_disconnect`
   и т.д. — все уже логируются структурированной строкой на событие,
   `fileprovider_backend.py`'s `_log_event`).
3. **Ни одного открытого privacy/regression инцидента** на File Provider
   пути (Task 19's zero-`FILE_READ`-before-`Cmd+V` инвариант держится в
   production telemetry, не только в тестах).
4. Явное решение product owner'а — это НЕ инженерный порог, staging — это
   ещё и офлайн/деградационный путь для сценариев, которые метрики не
   обязательно покрывают (например, полностью офлайн-машина без
   fileproviderd вообще).

До выполнения всех четырёх пунктов staging остаётся частью кодовой базы и
частью каждого релиза.

## Телеметрия: `fp_backend_selected_{file_provider,staging}`

Task 17 уже определил счётчики и хук
`FileProviderBackend.record_backend_selected(kind)`
(`fileprovider_backend.py:478-490`), но ничего его не вызывало — это и есть
задача Task 20 (ruling #2).

Реальная точка выбора — `MacReceiveRouter._select_backend`
(`platform_files.py`), вызываемая ровно один раз на `handle_offer`
(не на внутренний "тихий" pre-publication fallback —
`_fallback_to_staging` не проходит через `_select_backend` повторно, это не
новое per-offer решение, см. докстринг метода). Новый приватный метод
`_record_selection(kind)`:

- Если у router'а есть экземпляр `FileProviderBackend` (`self._fp is not
  None`) И у него есть метод `record_backend_selected` — вызывается ОН,
  так что счётчик ложится в ТОТ ЖЕ `backend.counters`, что и все остальные
  Task 17 счётчики того же прогона (в том числе когда FP-backend
  сконструирован, но САМ offer ушёл в staging — деградация домена/XPC:
  тот же экземпляр держит оба счётчика).
- Если экземпляра `FileProviderBackend` нет вовсе (Stage 1 default: флаг
  выключен — самый частый случай) — считать на FP-стороне негде, поэтому
  router держит собственный маленький словарь `self.selection_counters`
  под ТЕМ ЖЕ ключом (`fp_backend_selected_staging`). Никакого нового
  metrics-фреймворка не заведено — это ровно тот же паттерн "plain dict",
  что и `FileProviderBackend.counters` (Task 17 ruling #2).

Обе ветки покрыты тестами в `tests/ui/test_runtime_wiring.py` (реальная
проводка `app.py → create_file_backend → MacReceiveRouter`, `create_file_backend`
НЕ замокан — только XPC/domain I/O-граница застаблена, как и в
`test_macos_receiver_wiring.py`):

- `test_flag_on_with_fileprovider_ready_routes_the_offer_to_file_provider_and_counts_it`
  — флаг включён, домен `is_ready`, XPC `remote()` не `None` → offer уходит
  на реальный `FileProviderBackend`, `fp_backend.counters["fp_backend_selected_file_provider"] == 1`.
- `test_flag_off_routes_the_offer_to_staging_without_building_file_provider_and_counts_it`
  — флаг выключен (Stage 1 default) → `router._fp is None`, offer уходит на
  реальный `MacFileReceiver`, `router.selection_counters["fp_backend_selected_staging"] == 1`.

## UX-подтверждение (вооружение буфера обмена) — НЕ реализовано в этой задаче

Task 16 brief упоминал опциональную UX-обратную связь оператору о том, что
буфер обмена вооружён File Provider'ом. Ruling #3 этой задачи делает её
опциональной и явно требует НЕ строить её, если она не ложится чисто в уже
существующую tray/notification-проводку `app.py`.

Решение: **не реализовано, задокументировано как Stage-2 follow-up.**
Причина: с флагом по умолчанию выключенным (Stage 1) File Provider путь
запускается только на сознательном developer opt-in — аудитория нулевая для
обычного релиза, значит приоритет минимальный. `app.py` уже вооружает буфер
молча (`pasteboard_arm`, Task 8) без какого-либо тred-уведомления даже для
staging-успеха — добавлять асимметричный "успех-тост" только для FP-пути
потребовало бы либо (а) нового UI-примитива, которого сегодня в
`clipboard_page`/`TrayIcon` нет для этого события, либо (б) переиспользования
существующего `page.add_event(...)` канала способом, который не был
спроектирован и не review'ился для privacy-инварианта модуля (сообщение не
должно содержать путей/контента — см. модульный докстринг
`fileprovider_backend.py`). Оба варианта — самостоятельная работа с
собственным UI/i18n-ревью, а не "минимальная доработка", которую можно
безопасно вставить сюда без нарушения scope discipline этой задачи.

Follow-up для Stage 2 (developer opt-in, когда живых пользователей File
Provider пути станет больше нуля): добавить `page.add_event(...)` строку на
`FileProviderBackend`'s успешное вооружение (симметрично уже существующему
`transfer_failed → page.add_event(...)` в `app.py`), basename-only, без пути
— в отдельной задаче с собственным тестом на privacy-инвариант сообщения.

## Проверка (regression)

- `tests/ui/test_runtime_wiring.py` — 4 новых теста (см. выше) зелёные;
  остальные тесты файла — pre-existing baseline (см. ниже), не тронуты этой
  задачей.
- `tests/transfer/` (без Windows-only спайков) — 690 passed, 10 skipped,
  без изменений относительно baseline до Task 20.
- `tests/clipboard/ tests/ui/` — 860 passed (+4 к baseline 856), 30 failed
  (тот же набор имён, что и baseline ДО Task 20 — pre-existing, не в файлах
  из scope этой задачи, подробности в task-20-report.md), 1 skipped, 4
  errors (тот же набор, что и baseline).
- `ruff check` — чисто на всех трёх изменённых файлах
  (`app.py` не менялся кодово в этой задаче, кроме отсутствия изменений;
  `platform_files.py`, `tests/ui/test_runtime_wiring.py` — 0 замечаний).

## Файлы

- `configurator/src/duo_input/transfer/platform_files.py` —
  `MacReceiveRouter._record_selection`/`self.selection_counters` (телеметрия
  выбора backend'а, ruling #2).
- `configurator/tests/ui/test_runtime_wiring.py` — 4 новых теста (флаг по
  умолчанию False, FP-выбор+счётчик, staging-выбор+счётчик, toggle
  mid-generation не мигрирует активную передачу) + попутная чистка неиспользуемого
  импорта `Slot` (ruff, тот же файл).
- `configurator/src/duo_input/app.py` — без изменений: флаг уже был
  спроектирован как default-False в рамках Task 16 (`app.py:378-383`), Task
  20 подтверждает и фиксирует это решение, не меняя код.
