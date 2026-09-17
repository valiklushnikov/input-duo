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

Систематическая отладка (Phase 1–4) установила **две причины**, одна из которых
не про сборку:

1. **Главная — сеть, не код.** Сырой UDP-мультикаст-пакет, посланный с Windows
   на `239.255.76.67:47655`, **не дошёл** до слушателя на Mac, у которого доступ
   к локальной сети заведомо есть (Terminal-python; приём мультикаста подтверждён
   loopback'ом). Значит текущая сеть **не доставляет мультикаст между этими
   машинами** (Wi-Fi client isolation / роутер), и автоматический discovery
   виснет на «Поиск». Это не регрессия: `GROUP_ADDRESS`/`BEACON_PORT`/
   `PROTOCOL_MAJOR` в git не менялись, а сырой пакет по сети код приложения
   объяснить не может. **Unicast TLS (порт 47654) работает** — ручной паринг по
   IP (поле «Адрес второго компьютера, если поиск не нашёл» →
   `set_manual_address` → прямой `_pairing_connect`, минуя мультикаст) **связал
   машины успешно**. Это и подтверждает, что pairing/TLS-код исправен.
2. **Вторичная — только упакованный .app.** Unsigned/ad-hoc .app на macOS
   Sequoia лишён доступа к локальной сети (Local Network Privacy), и в Info.plist
   не было `NSLocalNetworkUsageDescription`, поэтому запрос доступа не появлялся и
   весь LAN-трафик .app молча блокировался (проверено: идентичный Qt-мультикаст
   под Terminal виден слушателю, а .app-мультикаст — нет). Dev-версия работала,
   т.к. запускалась под Terminal, у которого доступ уже выдан.

**Фикс:** build-скрипт теперь передаёт Nuitka
`--macos-app-protected-resource="NSLocalNetworkUsageDescription:…"`, так что
Info.plist упакованного .app несёт usage-string, macOS показывает запрос доступа
к локальной сети, и пользователь может его выдать (после чего .app нужно
перезапустить — TCC применяется к новому процессу). На уже собранном бандле
ключ был доправлен вручную (`PlistBuddy` + `codesign --force --deep --sign -`),
и после выдачи разрешения unicast-паринг заработал.

**Обход для сетей, режущих мультикаст** (штатный, уже в UI): вписать IP второго
компьютера в поле «Адрес второго компьютера…», затем «Связать компьютеры» на
обеих машинах. Discovery-мультикаст при этом не нужен.

**Не устранено этим этапом (сеть):** автоматический discovery через мультикаст на
сетях с client-isolation работать не будет — это ограничение сети, а не
приложения. Возможное будущее улучшение (вне scope): fallback discovery через
directed broadcast или запоминание последнего адреса.

## Не в scope этого этапа

Developer ID signing, notarization, `.dmg`, Apple Developer Program — отдельный
distribution-milestone.
