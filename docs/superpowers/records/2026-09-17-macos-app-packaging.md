# macOS упаковка: Duo Input.app (development build)

**Дата:** 2026-09-17
**Цель:** development build для проверки packaged runtime — **не** production
distribution. Без Developer ID signing, без notarization, без `.dmg`, без
покупки Apple Developer Program.
**Машина:** macOS 15.1.1 (arm64), Python 3.12.14 (`.venv-build`), Nuitka 4.1.3,
PySide6 6.10.1, pyobjc-framework-Cocoa 12.2.2.

## Что переиспользовано

`configurator/packaging/nuitka-build-macos.sh` уже существовал (Sep 12) и делал
почти всё: Nuitka `--standalone --macos-create-app-bundle` (никогда onefile),
`--enable-plugin=pyside6`, Qt-plugins (platforms/styles/imageformats), pyobjc
(`objc`/`AppKit`/`Foundation` + `clipboard.macos_pasteboard`), icon, unsigned.
Build-венв — отдельный `.venv-build` на Python 3.12 (совпадает с `pyproject`
`>=3.12,<3.13`). `.venv-mac` (3.14, без Nuitka) для сборки непригоден.

## Что изменено (только packaging, архитектура приложения не тронута)

`packaging/nuitka-build-macos.sh`:
- `--include-module=duo_input.transfer.macos_pasteboard` (+ `.macos_files`,
  `.staging`) — новый pyobjc-путь приёмника, лениво импортируемый в darwin-ветке
  `create_file_backend`, которого скрипт Sep-12 ещё не знал.
- `--nofollow-import-to=duo_input.transfer.windows_files` и `...windows_com` —
  держит Windows COM вне macOS-графа (требование) и не даёт Nuitka тянуть
  `ctypes.WINFUNCTYPE`.
- Тестовый гейт `tests/clipboard` сделан устойчивым к SIGSEGV в глобальных
  деструкторах Qt/PySide6 при завершении интерпретатора **после** зелёного
  прогона: гейт судит по отчёту (`N passed`, отсутствие `failed`/`error`), а не
  по коду возврата процесса. Реальные падения по-прежнему валят сборку.

`tests/clipboard/test_windows_backend.py`:
- `test_our_own_virtual_file_publication_is_not_taken_for_a_local_copy` получил
  `@pytest.mark.skipif(sys.platform != "win32")` — он импортирует
  `transfer.windows_files → windows_com` (`ctypes.WINFUNCTYPE`) и раньше
  падал/сегфолтил на macOS. Это Windows-only тест без guard (недосмотр
  file-transfer M1), не архитектура приложения.

## Результаты сборки

- **`dist/DuoInput.app`, размер 112 MB.** Главный бинарь `Contents/MacOS/app`
  ~20 MB; бандл несёт свой Python, PySide6, Qt-модули и pyobjc
  (`AppKit`/`Foundation`/`objc`).
- **Startup errors: нет.** Запуск даёт только штатный Cocoa-шум
  (`+[IMKClient subclass]: chose IMKClient_Modern`), не ошибку. Ни ImportError,
  ни traceback.
- **Nuitka:** `Created binary that runs on macOS 15.0 (arm64) or higher.`

## Проверки (одномашинные) — пройдены

- **Двойной клик из Finder / LaunchServices:** `open dist/DuoInput.app` →
  процесс с **parent PID = 1 (launchd)**, то есть полностью отвязан от
  Terminal/venv.
- **Запуск после закрытия Terminal/.venv:** бандл self-contained — в нём нет
  ссылок на `.venv-mac`/`.venv-build`; `otool -L` главного бинаря не показывает
  внешних абсолютных зависимостей вне system/бандла; libpython вшит.
- **Lazy platform imports + macOS-модули в графе (дефинитивно):** запуск с
  `clipboard.files_enabled=true` исполнил darwin-ветку
  `create_file_backend` **без ImportError** — значит `transfer.macos_files`,
  `transfer.macos_pasteboard`, `transfer.staging` скомпилированы в бандл.
- **Windows COM вне runtime graph:** `--nofollow-import-to` для
  `windows_files`/`windows_com`; на darwin win32-ветка мертва, `windows_com`
  не загружается (app стартует чисто). В строках 20 MB бинаря COM-имена
  встречаются только как import-ссылки (3×) против 5× у реально включённых
  macos-модулей.
- **Staging `~/Library/Caches/duo-input/incoming` + recover/gc:** подложенный
  `probe-incomplete/` был **удалён `StagingArea.recover()`** на старте
  упакованного app, а `probe-ready/` **уцелел** — подтверждает и подключение
  recover/gc (Ruling H), и инвариант «READY переживает» (Ruling I) в пакете.

## Требует оператора (не автоматизируется на одной машине)

- **Визуально:** tray/menu-bar иконка и меню; clipboard text/image round-trip.
- **Две машины (как Task 9 file-transfer acceptance):** сквозной
  **Mac→Windows** и **Windows→Mac** transfer из упакованного .app; вставка
  host-only `file://` в Finder на `⌘V`; подтверждение, что Windows-отправитель
  принимает `TRANSFER_BEGIN`/`session_id` приёмника.

## Первый запуск (unsigned)

Gatekeeper: right-click → Open, либо
`xattr -dr com.apple.quarantine dist/DuoInput.app`.

## Пост-сборочная отладка: «Связать компьютеры» висит на «Поиск» (Win↔Mac)

**Подтверждённая корневая причина (одна):** упакованный unsigned/ad-hoc .app на
macOS Sequoia был лишён доступа к локальной сети (Local Network Privacy), потому
что в Info.plist не было `NSLocalNetworkUsageDescription`. Без него macOS не
показывает запрос доступа и **молча блокирует весь LAN-трафик .app — включая
UDP-мультикаст-маячки discovery** (`239.255.76.67:47655`). Отсюда «Поиск» висит.
Dev-версия работала, потому что запускалась под Terminal, у которого доступ к
локальной сети уже выдан. Это **не регрессия кода**: `GROUP_ADDRESS`/
`BEACON_PORT`/`PROTOCOL_MAJOR` в git не менялись, pairing/TLS-логика исправна.

Диагностическая осторожность (для протокола): промежуточный вывод «сеть режет
мультикаст между машинами» оказался **неверным** — он опирался на сырую
мультикаст-пробу с Windows, которая, по-видимому, уходила не через тот сетевой
интерфейс (ложно-отрицательный). Мультикаст на этой сети работает; блокировал
его именно недостающий доступ .app к локальной сети.

**Фикс (запечён в build-скрипт):** Nuitka теперь получает
`--macos-app-protected-resource="NSLocalNetworkUsageDescription:…"`, так что
Info.plist упакованного .app несёт usage-string и macOS показывает запрос
доступа к локальной сети. На уже собранном бандле ключ был доправлен вручную
(`PlistBuddy` + `codesign --force --deep --sign -`).

**Важный операционный шаг:** после нажатия **Allow** в запросе (или включения
Duo Input в Системные настройки → Конфиденциальность и безопасность → Локальная
сеть) **приложение нужно перезапустить** — TCC применяет разрешение к новому
процессу, а уже запущенный держит прежний отказ. После перезапуска с выданным
разрешением заработали и unicast (ручной IP), и **автоматический discovery по
кнопке** — что и подтвердило причину.

**Проверка (ground truth):** до фикса под Terminal-python идентичный Qt-мультикаст
был виден слушателю, а мультикаст .app — нет; после выдачи Local Network и
перезапуска .app кнопка «Связать компьютеры» снова находит второй компьютер.

**Штатный обходной путь** (в UI, полезен если discovery почему-то не находит):
поле «Адрес второго компьютера, если поиск не нашёл» → прямой unicast-TLS на
IP:47654, минуя мультикаст.

### Мультикаст-entitlement (ad-hoc) — проверено, не работает

Попытка бесплатно включить мультикаст для приложения: ad-hoc пере-подпись с
`com.apple.developer.networking.multicast`. Результат — **приложение перестаёт
запускаться** (`Launchd job spawn failed`, error 153): restricted-entitlement
`com.apple.developer.*` без provisioning profile (платный Apple Developer) не
авторизуется, и ядро отказывает в запуске. Вывод: **мультикаст-discovery в
упакованном .app без платного аккаунта недостижим**. В build-скрипт entitlement
НЕ добавлен (сломал бы запуск).

### `NSLocalNetworkUsageDescription` — rebuild-проверено

Пересборка подтвердила: Nuitka-флаг `--macos-app-protected-resource` кладёт ключ
в Info.plist упакованного .app из коробки (`PlistBuddy` его читает), .app ad-hoc
подписан и запускается. Прежний «непроверенный» пункт закрыт.

### Первый запуск нового бандла

Новый build → новый cdhash → macOS покажет запрос Local Network заново.
Порядок: Allow → **перезапустить .app один раз** (TCC применяется к новому
процессу) → далее discovery/unicast работают по правилам выше.

### Код-фиксы паринга/буфера (коммит a974794)

В этот же цикл вошли два фикса устойчивости (не про упаковку): «Связать» не
рушит здоровую связь и воссоединяет доверенного пира по unicast (#2); сброс
`_last_received` при разрыве, чтобы буфер не ломался после рестарта одной
стороны — «объявление устарело» (#3).

## Не в scope этого этапа

Developer ID signing, notarization, `.dmg`, Apple Developer Program — отдельный
distribution-milestone.

## Поправка 2026-09-26: multicast-entitlement на macOS не нужен

Опыт выше (ad-hoc + `com.apple.developer.networking.multicast` → error 153)
верен, но вывод «мультикаст-discovery в .app без платного аккаунта недостижим»
ошибочен. Apple TN3179: «The multicast entitlement isn't required on macOS» —
entitlement нужен только iOS/iPadOS/visionOS. На macOS multicast — обычная
local-network операция под Local Network privacy; достаточно
`NSLocalNetworkUsageDescription` и разрешения пользователя. Подтверждено
spike'ом: `docs/superpowers/records/2026-09-26-macos-multicast-discovery-spike.md`.

Там же уточнены ещё два пункта этого record:

- для `.app`, подписанного Personal Team, на macOS 27.0 разрешение Local
  Network подействовало **без перезапуска** процесса;
- мультикаст «не проходящий» с Windows объясняется выбором интерфейса на
  Windows с Ethernet и Wi-Fi одновременно: при рассылке через маршрут по
  умолчанию не доходит ничего, при рассылке через каждый интерфейс — всё.
