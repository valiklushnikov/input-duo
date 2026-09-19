# macOS File Provider — production-интеграция с Duo Input FILE_* runtime

**Дата:** 2026-09-19
**Тип:** design document (DESIGN milestone). **Не** implementation: production-код
не пишется, `files/2` не меняется, staging не трогается, spike-код не переносится.
**Статус:** `DESIGN_STATUS = READY`. `RESEARCH_PHASE = COMPLETE` (Phase 10.2). Все
open packaging/entitlement-вопросы закрыты; эскалации владельцу не осталось.
> **REVISION 2026-09-19 (Phase 10.2 completed):** IPC-архитектура **приведена в
> соответствие с доказанными результатами** Phase 10.2
> (`records/2026-09-19-macos-file-provider-ipc-packaging-spike.md`). Доказано
> (OBSERVED, `NATIVE_FP_IPC_GATE = PASS`): штатный канал `NSFileProviderServiceSource`
> — anonymous `NSXPCListener` **в extension**, client — **в containing app**, без App
> Group, без named Mach service, без temporary-exception; двусторонний round-trip;
> PyObjC/Nuitka-host; peer-auth; extension-private manifest replica; enumeration +
> `fetchContents` **без host/Python**. Инвариант: extension **обязан** конформить
> `NSFileProviderServicing`, иначе service discovery не работает и `getService` →
> `nil`. `APP_GROUP_REQUIRED_OVERALL = NO`. Gate B/C — **CLOSED**. Старая модель
> (listener в app + app-group mach) перенесена в «Superseded architecture» ниже.
> См. итоговую сводку в разделах «Revision note (Phase 10.2)» и «Final output».
**Источники истины:**
`records/2026-09-19-macos-file-provider-ipc-packaging-spike.md` (Phase 10.1/10.2,
OBSERVED+DOCUMENTED — финальный, `NATIVE_FP_IPC_GATE = PASS`),
`records/2026-09-17-macos-lazy-file-provider-spike.md` (Phase 9/9.6, OBSERVED),
`records/2026-09-17-macos-app-packaging.md` (Nuitka-сборка; бывший entitlement-барьер
существовал только из-за App Group и теперь неактуален — см. §26),
`specs/2026-09-16-macos-file-transfer-design.md` (staging-приёмник),
и прочитанный production-код `configurator/src/duo_input/transfer/*`,
`clipboard/wire.py`, `clipboard/peer.py`, `app.py`.

Легенда достоверности та же, что в records: **DOCUMENTED / OBSERVED / INFERRED /
UNKNOWN**. Всё, что про поведение File Provider на реальном железе, взято из
OBSERVED-результатов Phase 9/9.6; всё, что про существующий runtime — прочитано в
коде и помечено (code).

---

## 1. Goals

1. Ленивая передача **Windows → macOS**: `Ctrl+C` на Windows → dataless-элементы
   в File Provider-домене → `file://` на буфере → байты идут **только** после
   `⌘V` (`fetchContents`), с нативным прогрессом и отменой Finder. Подтверждено
   OBSERVED в spike (`FILE_PROVIDER_LAZY_PASTE = PASS`).
2. Переиспользовать существующий FILE_* runtime без изменения провода: Windows —
   отправитель, `SnapshotRegistry` — источник, `files/2` — контракт.
3. Сохранить два инварианта продукта: (а) скопированное-но-не-вставленное не
   покидает машину-источник (нет `FILE_READ` до `⌘V`); (б) на приёмнике данные
   не идут без предварительной авторизации (Ask/Auto).
4. Concurrency-safe backend: несколько `fetchContents` в полёте (директории —
   OBSERVED конкурентны в Phase 9.5).
5. Корректные атрибуты вставленных копий (`0600`, без `uchg`) через API, без
   `chmod`/`chflags` (Phase 9.6).
6. Staging остаётся рабочим fallback; File Provider — второй backend того же
   стека, а не отдельный file-transfer стек.

## 2. Non-goals

- Изменение `wire.py`/`model.py`/`paths.py`/`pipe.py`/`scanner.py`/`source.py` и
  протокола `files/2`. `PROTOCOL_MAJOR` не поднимается.
- Изменение Windows-отправителя и `SnapshotRegistry` (кроме tuning-констант, и то
  как отдельное решение владельца).
- Полноценный eviction/content-policy как у Dropbox (см. §21 — v1 conservative).
- Перенос protocol runtime, networking или Python внутрь `.appex`.
- Поддержка symlink/FIFO/device/package (см. §23).
- Перенос exec-бита файлов (манифест его не несёт — см. §9, §30).
- Distribution/notarization как исполнение (это отдельный milestone; здесь только
  требования к нему).
- Implementation plan — отдельный следующий milestone.

## 3. Proven research inputs (зафиксировано, не переисследуется)

Из Phase 9/9.6 (OBSERVED, records):

- **Lazy.** Публикация реального `file://` dataless-элемента в NSPasteboard **не**
  триггерит `fetchContents`; он наступает строго на `⌘V`
  (`requestor=DesktopServicesHelper`). `PRE_PASTE_LAZY = PASS`.
- **Cancel.** `Finder Cancel → Progress.cancellationHandler → producer видит
  isCancelled → останов отдачи → temp cleanup → destination пуст → source
  остаётся dataless`.
- **Repeated paste.** После первой материализации источник обслуживает следующие
  `⌘V` локально — повторного `fetchContents` нет.
- **Multiple flat files.** Материализуются **последовательно** (не инвариант API).
- **Directories.** Дети поддерева материализуются **конкурентно** → backend
  обязан быть concurrency-safe.
- **Атрибуты.** `capabilities ⊇ [.allowsReading, .allowsWriting]` (снимает `uchg`)
  и `fileSystemFlags = [.userReadable, .userWritable]` (даёт `0600`). Оба рычага
  независимы. `chmod`/`chflags` запрещены.
- **DomainDisabled.** Сразу после `add(domain:)` есть транзиентное окно
  `NSFileProviderErrorDomainDisabled (-2011)` (~до минуты) до `state:enabled`.
- **Eviction.** `evictItem` на материализованном элементе вернул
  `-2008 NonEvictable` (элементы не purgeable без content-policy).
- **Personal Team.** Throwaway host+`.appex` собран/подписан на бесплатном
  Personal Team, App Groups **для голого File Provider не понадобились**.

Из Phase 10.1/10.2 (OBSERVED+DOCUMENTED, финальный record — `NATIVE_FP_IPC_GATE = PASS`):

- **Штатный IPC-канал.** `NSFileProviderServiceSource`: anonymous `NSXPCListener`
  создаётся **в extension**, client — **в containing app**; endpoint доставляется
  инфраструктурой File Provider (`getServiceWithName`/
  `getFileProviderServicesForItemAtURL`). App Group, named Mach service и
  temporary-exception **не нужны** (DOCUMENTED SDK + OBSERVED-подпись).
- **`NSFileProviderServicing` обязателен.** Без конформанса extension к
  `NSFileProviderServicing` фреймворк не маршрутизирует запрос в
  `supportedServiceSourcesForItemIdentifier:` → пустой список сервисов →
  `getService = nil`. Доказано root-cause-анализом.
- **Двусторонний round-trip.** По установленному `NSXPCConnection` обе стороны
  ставят `exportedObject`/`remoteObjectProxy`; extension зовёт host в `fetchContents`
  несмотря на app-инициированный bootstrap. `BIDIRECTIONAL_ROUND_TRIP = PASS`.
- **PyObjC-роль — client.** Client-side достижим на pyobjc (Foundation/Cocoa);
  `NSXPCInterface` требует clang-скомпилированный протокол (`objc.formal_protocol`
  не годится — OBSERVED) → протокол берётся из общего Objective-C заголовка/dylib.
- **Nuitka-host.** PyObjC-client, собранный Nuitka в подписанный `.app` со встроенным
  `.appex`, замкнул `getServiceWithName → connection → ping/pong/ack`. `Gate 4 = PASS`.
- **Peer auth.** listener в extension принимает соединения через
  `listener:shouldAcceptNewConnection:`; проверка `SecCodeCheckValidity` против
  code-signing requirement (Team ID). `Gate 7 = PASS` (TOCTOU-оговорка: `auditToken`
  на `NSXPCConnection` в SDK не экспонирован — pid-based `SecCode`).
- **Extension-private replica.** Метаданные-реплика живёт в контейнере extension
  (`~/Library/Containers/<ext-bundle-id>/Data/…`), без App Group. `Gate 5 = PASS`.
- **Enumeration + fetchContents без host.** Тот же extension-бинарь обслуживает
  enabled-домен при полностью отсутствующем host/Python: `ls` реплики, материализация
  dataless-элемента в собственный temp контейнера. `Gate 6 = PASS`.
- **App Group не нужен нигде.** `APP_GROUP_REQUIRED_OVERALL = NO`; подписанные
  entitlements: host — `get-task-allow`; appex — `app-sandbox` + `get-task-allow`.

## 4. Existing runtime map (прочитано в коде)

### 4.1 Модули → ответственность

```
clipboard/wire.py         MessageType {FILE_OFFER=10, TRANSFER_BEGIN=11, FILE_READ=12,
                          FILE_CHUNK=13, FILE_ERROR=14, TRANSFER_END=15}; framing;
                          CAPABILITY_FILES="files/2"; MAX_FILE_CHUNK_BYTES=1 MiB;
                          PROTOCOL_MAJOR=1/MINOR=1.
clipboard/peer.py         PeerLink: один TLS-сокет; message_received/disconnected;
                          READ_BUFFER_BYTES=4 MiB, WRITE_LIMIT (переполнение→разрыв).
clipboard/coordinator.py  Владеет связью/доверием/capabilities; .link, .peer_capabilities,
                          capabilities_known, state_changed.
transfer/model.py         TransferManifest{transfer_id, entries[], skipped[], drop_effect};
                          TransferEntry{path("/"-rel), kind(file|directory), size, mtime_ns};
                          encode/decode_manifest (JSON, ≤16 MiB).
transfer/paths.py         sanitize_manifest / sanitize_relative_path (Windows-strict,
                          NFC, коллизии, MAX_ENTRIES=65536, MAX_DEPTH=32, MAX_TOTAL_BYTES=2 TiB).
transfer/scanner.py       scan(roots)→(manifest, sources{relpath→abspath}); полное дерево,
                          reparse-point в skipped.
transfer/source.py        SnapshotRegistry (SENDER): publish (lazy), read(transfer_id,
                          entry_index, offset, length) с held-fd + сверка size/mtime_ns,
                          RETENTION=4, close_descriptors (repeated paste), release_all.
transfer/service.py       FileTransferService (QObject, Qt-thread): SENDER (_answer_read →
                          FILE_CHUNK/FILE_ERROR) + Windows-RECEIVER (open_pipe/request_read/
                          _on_chunk через ChunkPipe). offer_received после sanitize.
transfer/pipe.py          ChunkPipe: bounded (capacity_chunks), COM↔Qt граница, без Qt.
transfer/windows_files.py Explorer COM data object + ServiceCallbackGateway (STA→Qt marshalling,
                          blocking open_pipe / non-blocking request_read).
transfer/windows_com.py   COM vtables/STA-поток.
transfer/staging.py       StagingArea/StagingSession (чистый Python): пишет дерево на диск,
                          TTL/disk-budget GC, recover().
transfer/macos_files.py   MacFileReceiver (staging-backend, self-driving, ПОСЛЕДОВАТЕЛЬНЫЙ:
                          один _read_id/_cursor/_offset). handle_offer→authorize→pump→arm.
transfer/macos_pasteboard.py  arm(paths): host-only NSPasteboard.writeObjects (file:// URL).
transfer/platform_files.py    create_file_backend: win32→WindowsFileClipboardBackend,
                          darwin→MacFileReceiver, иначе UnsupportedPlatformError.
app.py::_ClipboardRuntime  Проводка: FileTransferService = общий SENDER; на darwin
                          MacFileReceiver = приёмник; оба слушают link.message_received.
```

### 4.2 Sequence — текущий Windows → Mac (staging), из кода

```
Windows Ctrl+C
  → (Windows sender) scan + SnapshotRegistry.publish (lazy) + FILE_OFFER{manifest}
      │  wire
      ▼
Mac FileTransferService.handle_message(FILE_OFFER)
  → sanitize_manifest → offer_received.emit(manifest)
  → MacFileReceiver.handle_offer → authorization_needed.emit
  → app: Ask/Auto → receiver.authorize(True)
  → has_room_for → StagingArea.begin → TRANSFER_BEGIN
  → _pump: FILE_READ(entry,offset,len,read_id)  ── ОДИН в полёте, ПОСЛЕДОВАТЕЛЬНО
      │  wire
      ▼
Windows service._answer_read → SnapshotRegistry.read (open+verify fd) → FILE_CHUNK
      │  wire
      ▼
Mac _on_chunk → _matches → StagingSession.write → следующий _pump
  ... по всем файлам ...
  → _complete: TRANSFER_END{completed} → macos_pasteboard.arm(roots) → READY
Mac ⌘V → Finder копирует из staging (байты уже на диске).
```

**Наблюдение для дизайна:** на Windows-стороне (SENDER) уже есть ровно то, что
нужно File Provider — ленивый долгоживущий снимок, повторно открываемый по
`entry_index`, обнаружение изменений, concurrency по entry_index (в
`service.py` каждый поток опознаётся своим `read_id`, `_reads: dict[read_id]`).
Разница File Provider vs staging — **только на Mac-приёмнике**: кто и когда
инициирует `FILE_READ` и куда падают байты. Отсюда весь дизайн — новый Mac-backend
+ `.appex`, без единой правки провода и отправителя.

---

## 5. Architecture overview

Тонкий native Swift `.appex` (enumeration/метаданные/`fetchContents` + владелец
`NSXPCListener` и extension-private metadata replica) + существующий Python-runtime
как владелец сети/протокола/авторизации/снимков и как NSXPC-**client**. Мост —
штатный `NSFileProviderServiceSource` (anonymous NSXPCListener в extension), без App
Group и без named Mach service.

```
┌ Finder / DesktopServicesHelper ─────────────────────────────────────────────┐
│   ⌘V → fetchContents(itemIdentifier)                                         │
└──────────────┬──────────────────────────────────────────────────────────────┘
               ▼
┌ DuoInputFileProvider.appex  (Swift, sandboxed) ─────────────────────────────┐
│  • NSFileProviderReplicatedExtension, NSFileProviderServicing (ОБЯЗАТЕЛЕН)   │
│  • NSFileProviderServiceSource → anonymous NSXPCListener (владелец listener) │
│  • enumeration/item(for:)  ← extension-private metadata replica (свой контейнер)│
│  • fetchContents           → пишет чанки в СВОЙ temp, возвращает URL         │
│  • Progress/cancel         → пробрасывает в IPC callback host               │
└──────────────┬──────────────────────────────────────────────────────────────┘
   bootstrap:  │  host (client) → FP service discovery (getServiceWithName) →
   host→ext    │  anonymous endpoint → установленное bidir NSXPCConnection
   fetch байты:▼  extension → host exportedObject callback (см. §7)
┌ Duo Input.app  (Python/PySide6, containing app) ────────────────────────────┐
│  • FileProviderServiceClient (PyObjc)  ← getServiceWithName/                 │
│       getFileProviderServicesForItemAtURL → NSXPCConnection (client)         │
│  • exportedObject (PyObjc)             ← extension зовёт его в fetchContents  │
│  • FileProviderBackend (Qt, чистый Python) ← concurrency-safe producer       │
│  • FileTransferService (общий)         ← отправляет FILE_READ по link        │
│  • FileProviderDomainManager (PyObjc)  ← add/remove домена, readiness        │
│  • replica publisher                   → шлёт update по XPC, ждёт ACK (§25)   │
└──────────────┬──────────────────────────────────────────────────────────────┘
               │  существующий PeerLink (TLS, files/2)   — БЕЗ ИЗМЕНЕНИЙ
               ▼
┌ Windows sender ─────────────────────────────────────────────────────────────┐
│  FileTransferService._answer_read ← SnapshotRegistry (lazy held-fd) — как есть│
└──────────────────────────────────────────────────────────────────────────────┘
```

Три «шва»:
- **общий** (не дублируется между staging и File Provider): авторизация, провод,
  манифест/модель, метаданные источника, FILE_* dispatch, валидация, логирование;
- **platform-specific** (File Provider backend): триггер материализации, temp,
  clipboard-публикация через getUserVisibleURL, IPC, domain lifecycle;
- **неизменяемое**: Windows sender + `SnapshotRegistry` + `files/2`.

## 6. Process boundaries

| Процесс | Кто | Sandbox | Владеет |
|---|---|---|---|
| `Duo Input.app` main | Python/PySide6/PyObjc | нет (как сейчас, Nuitka .app не sandboxed) | сеть, FILE_*, авторизация, снимок (на Windows), domain lifecycle, NSXPC-**client** + exportedObject callback, publish метаданных в реплику (по XPC, ACK) |
| `DuoInputFileProvider.appex` | Swift native | **да (обязателен)** | `NSXPCListener` (anonymous, владелец), `NSFileProviderServicing`/service source, enumeration/item metadata, extension-private metadata replica (владелец хранилища), temp-файл материализации, Progress/cancel |
| `fileproviderd` / Finder | система | — | вызывает enumeration/`fetchContents`, доставляет service endpoint, жизненный цикл extension |

Extension может быть запущен `fileproviderd` **раньше** и **независимо** от main
app (например, enumeration при логине). Отсюда §33: enumeration/`item(for:)`
обязаны работать без Python — из extension-private metadata replica (OBSERVED
Gate 6: enumeration + материализация ранее опубликованного идут без host); только
`fetchContents`, которому нужны свежие удалённые байты, требует host/Python.

## 7. Swift ↔ Python IPC — `NSFileProviderServiceSource` (штатный канал)

```
IPC_MODEL          = NSFileProviderServiceSource
XPC_LISTENER_OWNER = Swift File Provider extension (anonymous NSXPCListener)
HOST_ROLE          = PyObjC NSXPC client + exported callback object
APP_GROUP_REQUIRED = NO   (ни для IPC, ни для store)
```

Доказано в Phase 10.2 (OBSERVED, `NATIVE_FP_IPC_GATE = PASS`). Механизм —
first-class канал File Provider, а не общий app-group-mach NSXPC (старая модель
перенесена в «Superseded architecture» ниже).

### Обязательный инвариант

```
File Provider extension MUST conform to NSFileProviderServicing.
```

Без конформанса `NSFileProviderServicing` фреймворк **не** вызывает
`supportedServiceSourcesForItemIdentifier:completionHandler:`, service discovery
не работает, и `getService`/`getFileProviderServicesForItemAtURL` возвращают `nil`
(root-cause Phase 10.2). Extension объявляется как
`NSFileProviderReplicatedExtension, NSFileProviderServicing`.

### Топология

```
                       владелец listener
Swift .appex ── NSFileProviderServiceSource.makeListenerEndpointAndReturnError
              → anonymous NSXPCListener  (App Group / mach-имя НЕ нужны)
                       ▲ endpoint через FP-инфраструктуру
Duo Input.app (PyObjc) ── getServiceWithName(.rootContainer) /
              getFileProviderServicesForItemAtURL → NSXPCConnection (client) → resume
```

### Bootstrap (host → File Provider service → extension)

```
host старт / по необходимости
  → NSFileProviderManager домен READY
  → getServiceWithName(name, itemIdentifier:.rootContainer)   (host управляет доменом)
     [или getFileProviderServicesForItemAtURL для item-level]
  → NSFileProviderService → getFileProviderConnection → NSXPCConnection
  → host ставит remoteObjectInterface + свой exportedObject → resume
  → extension принимает соединение в listener:shouldAcceptNewConnection: (peer-auth)
  → установлено двустороннее соединение
```

Bootstrap инициирует **host** (client). Дальше соединение bidirectional: обе
стороны держат `exportedObject`/`remoteObjectProxy`, и **extension зовёт host** в
`fetchContents`, несмотря на app-инициированный bootstrap (OBSERVED
`BIDIRECTIONAL_ROUND_TRIP = PASS`).

### Runtime data/control после соединения

```
extension → host callback   (openFetch / pullChunk / cancelFetch)
host      → extension reply  (totalSize / chunkBytes / eof / error)
host      → extension        (publish/retire generation replica update; §25)
extension → host reply       (ACK)
```

### Peer authentication

listener в extension проверяет пира в `listener:shouldAcceptNewConnection:` через
`SecCodeCheckValidity` против code-signing requirement (Team ID).
`XPC_PEER_AUTHENTICATION = PASS` (OBSERVED, Gate 7; TOCTOU-оговорка: `auditToken` на
`NSXPCConnection` в SDK не экспонирован → используется pid-based `SecCode`).

### PyObjC-ограничение (зафиксировано для implementation plan)

`NSXPCInterface` **не** принимает `objc.formal_protocol` (падает
`Use of clang is required for NSXPCInterface`, OBSERVED — воспроизведено и для
reply-блока, и для void-метода). XPC-протокол определяет Swift-`.appex`;
Python-сторона получает тот же протокол из скомпилированного Objective-C
заголовка/shim-dylib. `formal_protocol` для XPC-интерфейсов использовать нельзя.

### Контракт IPC (protocol-объекты NSXPC)

```
host exports (зовёт extension):      openFetch(generationId, entryId) → reply(totalSize | error)
                                     pullChunk(fetchToken)            → reply(chunkBytes | eof | error)
                                     cancelFetch(fetchToken)
extension exports (зовёт host):      publishGeneration(replicaUpdate) → reply(ACK)   (§25)
                                     retireGeneration(generationId)   → reply(ACK)
```

`pullChunk` — pull-based backpressure end-to-end (см. §16): extension просит
следующий кусок только записав предыдущий; каждый `pullChunk` инициирует ровно
один `FILE_READ`.

Направление listener/client **инвертировано** относительно исходного анализа:
раньше предполагалось, что host держит listener и extension — client; штатный
механизм делает наоборот (listener в extension, host — client). Двунаправленность
сохранена через `exportedObject`, поэтому смысловые роли контракта (кто что
запрашивает) не изменились — изменился только владелец listener и способ доставки
endpoint (anonymous через FP-инфраструктуру, а не по mach-имени).

## 8. Ownership model

Ни одно authoritative-состояние не живёт одновременно в Swift и Python.

| State | Owner | Persistent? | Reconstructable? | Cleanup trigger |
|---|---|---|---|---|
| Offer/manifest (authorized) | Python (authoritative) → publish в extension-private replica по XPC (ACK) | **да** (extension-private JSON) | из FILE_OFFER (пока снимок жив на Windows) | generation retire/GC |
| File tree / entries | extension-private metadata replica (owner: extension) | да | из manifest | вместе с generation |
| itemIdentifier ↔ (generationId, entryId) | детерминированный маппинг (§9), не хранимое состояние | n/a | всегда (чистая функция) | — |
| remote snapshot (transfer_id) | **Windows SnapshotRegistry** | нет (RETENTION=4, held-fd) | да (lazy re-open) | close_descriptors/release/evict на Windows |
| remote entry (path/size/mtime) | manifest (immutable) | да | из manifest | — |
| authorization (ask/auto, accepted) | Python (`clipboard/incoming_files` + per-offer решение) | настройка — да; решение — нет | — | новый offer |
| materialization state (dataless/local) | **система/File Provider** (Swift читает) | да (реплика ОС) | ОС | eviction ОС |
| active fetch (token, offset, received) | Python `FileProviderBackend` + Swift `FetchContext` (зеркало) | нет | нет | completion/cancel/disconnect |
| temp URL материализации | **Swift extension** | нет | нет | completion(success) отдаёт ОС; на cancel/err — удаляется |
| Progress | Swift (создаёт, отдаёт Finder), Python шлёт байты-события | нет | нет | завершение fetch |
| cancellation token | Swift `Progress.cancellationHandler` → IPC `cancelFetch` → Python | нет | нет | — |
| error | по месту (маппинг §16) | нет | нет | — |
| domain readiness | Python `FileProviderDomainManager` | нет | да (опрос) | — |
| generation lifecycle (active/retired/GC) | Python | да (в extension-private replica: поле state) | да | §20 |
| clipboard generation (changeCount) | Python (arm) | нет | нет | новый arm |
| lease/TTL/cleanup deadline | Python | да (в store) | да | §20 |

## 9. Identity and versions

### itemIdentifier

```
itemIdentifier(generation, entry) = f"{transfer_id}:{entry_index}"
```

- `transfer_id` — уже уникален per-offer (uuid4.hex, code) → нет cross-offer
  aliasing; одинаковые имена в разных offers различаются `transfer_id`.
- `entry_index` — индекс в `manifest.entries` (детерминирован: scanner сортирует
  имена, code) → одинаковые имена в разных папках различаются индексом; стабилен
  между enumeration и app/extension restart (манифест immutable, хранится).
- Корневой контейнер домена — `NSFileProviderRootContainerItemIdentifier`; под
  ним синтетический контейнер-generation с id `transfer_id`; его дети — записи.
- `parentItemIdentifier`: для записи с путём `a/b/c.txt` — родитель это запись-
  директория с путём `a/b` (её `entry_index`); для корневой записи generation —
  контейнер `transfer_id`.

Никаких новых wire-полей: `transfer_id` и `entry_index` уже на проводе и в
манифесте. Существующих идентификаторов **достаточно** — доказано тем, что
`_answer_read`/`SnapshotRegistry.read` уже адресуют источник ровно парой
`(transfer_id, entry_index)` (code).

### itemVersion (детерминизм обязателен)

```
contentVersion  = bytes(f"{size}:{mtime_ns}")          # меняется только при смене содержимого
metadataVersion = bytes(f"{name}:{size}:{mtime_ns}:{caps_flags_rev}")
```

Детерминировано из immutable-манифеста → повторная enumeration даёт ту же версию
→ ОС не считает элемент изменившимся (иначе — лишние ре-фетчи). `caps_flags_rev` —
константа схемы прав (§30), меняется только при осознанном обновлении маппинга.

## 10. Domain lifecycle

Один стабильный домен (см. §12). State machine (Python `FileProviderDomainManager`):

```
ABSENT ──add(domain:)──▶ REGISTERING ──ok──▶ WAITING_ENABLED ──state:enabled──▶ READY
   ▲                          │ err                   │ таймаут/-2011 retry (backoff)     │
   │                          ▼                        └──────────────▲                   │
   └───────── REMOVING ◀── (files toggle off / fatal) │              │        service unavail
                                                        └─ DEGRADED ◀─┴──────────────┘
```

- **Определение READY:** опрос `NSFileProviderManager` / отсутствие `-2011` при
  пробном `getUserVisibleURL`/enumerator-signal; не `sleep(60)`. Backoff-ретраи
  (переиспользовать паттерн `RECONNECT_DELAYS_MS` из coordinator, code).
- **DomainDisabled (-2011):** OBSERVED транзиентно после `add`. Пока не READY:
  authorized-offer **публикуется в extension-private replica сразу** (publish по
  XPC, ACK; generation существует), но **arm буфера откладывается** до READY
  (getUserVisibleURL требует enabled).
- **Incoming FILE_OFFER пока не READY:** backend выбирается per-offer (§27). Если
  домен не станет READY за окно (напр. 10 c) — этот offer уходит в **staging**
  (fallback), semantics generation не меняется по ходу.
- **Startup/restart:** домен переживает рестарт app (ОС хранит домен). На старте:
  ABSENT→…→READY, затем purge stale generations (§32).
- **Domain removed externally / service unavailable:** DEGRADED → новые offers в
  staging; периодический re-add.

## 11. Offer/generation lifecycle

```
FILE_OFFER → sanitize → authorization → (домен READY?) 
   ├─ да  → publish generation (XPC → private replica, ACK, state=ACTIVE) → getUserVisibleURL(roots)
   │        → host-only NSPasteboard.arm(root URLs) → ACTIVE_CLIPBOARD
   └─ нет  → staging fallback (§27)
```

Состояния generation (в extension-private replica):

```
ACTIVE_CLIPBOARD ── новый arm ──▶ RETIRED ── нет активных fetch и нет OS-ref ──▶ GC_ELIGIBLE ──▶ removed
       │                              ▲
       └── активный fetch / OS держит URL ── IN_USE (не удалять)
```

Новый `Ctrl+C` (offer B) не удаляет generation A немедленно (Finder мог начать
paste A, fetch A активен, старый URL ещё жив, A частично materialized). См. §20.

## 12. File Provider namespace

```
DOMAIN_MODEL = один стабильный домен "Duo Input"; generations = внутренние контейнеры
```

- **Один домен**, а не domain-per-offer. Причина: `add/remove(domain:)`
  тяжёлые и несут транзиентный `-2011` (OBSERVED) — платить эту латентность на
  каждый `Ctrl+C` нельзя. Один домен добавляется при включении файлов, generations
  живут как контейнеры внутри.
- **Когда создаётся namespace элемента:** только **после авторизации**. До Accept
  (в режиме Ask) generation в replica не публикуется и элементы не показываются —
  сохраняет security-инвариант (§21): нет pasteable URL до авторизации.
- **Где root items:** top-level `entries[]` generation. Пользователь их не ищет в
  Finder — он делает `⌘V` тем, что мы положили на буфер (getUserVisibleURL их
  root URL). Технический домен **виден** в Finder sidebar
  (`~/Library/CloudStorage/DuoInput-...`, OBSERVED) — принимаем как UX v1 (имя
  «Duo Input»). Скрытие transport-namespace — open question (§32-doc, не блокер).

## 13. Clipboard publication

Ответственность — **containing app** (Python), не extension:

```
backend.publish_generation → domain READY → NSFileProviderManager.getUserVisibleURL(rootItemId)
  → собрать список root file:// URL → transfer/macos_pasteboard.arm(urls)  (host-only, как сейчас)
```

- Extension **не** трогает general pasteboard.
- Переиспользуется существующий `transfer/macos_pasteboard.arm` (host-only,
  `writeObjects`) — расширяется, второй clipboard-subsystem не создаётся. Разница
  с staging: URL берутся из `getUserVisibleURL` File-Provider-элементов, а не из
  staging-путей.
- host-only совместим (OBSERVED в spike: write ok, `NSPasteboardContentsCurrentHostOnly`).

## 14. fetchContents state machine (ядро)

На один fetch (Python `FileProviderBackend` + зеркальный Swift `FetchContext`):

```
                       Swift extension                    Python backend / wire
IDLE
 │ fetchContents(itemId, version)
OPENING ───IPC openFetch(gen,entry)──────────▶  map itemId→(transfer_id,entry_index)
 │                                              validate generation ACTIVE/RETIRED-IN_USE
 │◀──reply totalSize | error────────────────   (нет generation → error)
REQUESTING
 │ создать temp, Progress(totalUnitCount=size)
 │ pullChunk ─────────────────────────────────▶  request FILE_READ(offset,len,read_id)
RECEIVING ◀──chunkBytes──── FILE_CHUNK ────────  _on_chunk: verify read_id/offset → отдать байты
 │ write(temp); Progress.completed += n
 │ offset<size ? pullChunk : eof                (повтор до size)
 │◀──eof───────────────────────────────────────  все байты отданы
FINALIZING  fsync/close temp
COMPLETING  completionHandler(tempURL, item(caps/flags §30))
DONE

ветки:
CANCELLING  Progress.cancellationHandler → IPC cancelFetch → backend: стоп FILE_READ;
            удалить temp; completion(NSUserCancelledError)
FAILED      IPC error (source_changed/…) → удалить temp; completion(mapped error §16)
PEER_LOST   link.disconnected → backend fail все fetch → IPC error → completion
TIMED_OUT   watchdog молчания сессии → fail → completion
```

Сопоставление с существующими объектами: `read_id` (code, `_reads`) = per-fetch
идентификатор ответа; `offset` — курсор внутри файла fetch; байты идут через IPC,
не через `ChunkPipe` (ChunkPipe — Windows-only COM-граница; File Provider граница
— NSXPC).

## 15. FILE_* mapping

```
FILE_OFFER      → generation в extension-private replica (publish по XPC, ACK) + dataless-элементы (метаданные из manifest)
TRANSFER_BEGIN  → отправляется приёмником один раз при первом fetch generation (Windows
                  sender его игнорирует — handle_message не обрабатывает TRANSFER_BEGIN, code;
                  оставлен для симметрии/диагностики)
FILE_READ       → один на pullChunk: (transfer_id, entry_index, offset, length≤1 MiB, read_id)
FILE_CHUNK      → байты чанка → IPC chunkBytes → temp
FILE_ERROR      → source_changed/source_missing/bad_request → fail fetch → File Provider error
TRANSFER_END    → приёмник шлёт {status} когда generation retire/GC или после «тихого» окна,
                  чтобы Windows освободил fd (close_descriptors, снимок остаётся, code)
```

```
FILES2_PROTOCOL_CHANGE_REQUIRED = NO
```

Доказательство достаточности (см. также §18, gate F):
- **Concurrent fetches** обслуживаются существующим sender'ом: `_answer_read`
  адресует `(transfer_id, entry_index, offset, length, read_id)` и отвечает
  синхронно; `SnapshotRegistry.read` держит fd **по entry_index** — разные записи
  читаются независимо и параллельно (code). Sender не хранит per-read состояние
  сверх синхронного ответа → нет гонок между конкурентными FILE_READ.
- **Cancel** возможен существующими сообщениями: приёмник просто перестаёт слать
  FILE_READ; ≤1 FILE_READ в полёте на fetch отвечается синхронно; поздний
  FILE_CHUNK отбрасывается приёмником (не совпал read_id/offset). Sender-state не
  течёт (см. §18). Отдельный `FILE_CANCEL` **не нужен** — добавлять запрещено.

## 16. Streaming / backpressure

```
TEMP_FILE_OWNER = Swift extension
```

- **Option B выбран:** extension пишет полученные по IPC чанки в **свой**
  sandbox-temp и возвращает этот URL в `completionHandler`. Python **не** пишет на
  Mac-диск для File Provider (staging по-прежнему пишет — это другой backend).
- Почему B, не A (Python пишет) и не C (helper): (а) URL должен быть доступен
  sandboxed-extension — свой temp гарантированно доступен, а Python-путь потребовал
  бы общего контейнера/копии; (б) sandbox-граница пересекается только каналом IPC,
  не путём в ФС; (в) pull-based backpressure получается естественно.

Backpressure — pull end-to-end, опираясь на pull-природу провода
`FILE_READ→FILE_CHUNK`:

```
per-fetch:  один FILE_READ в полёте; следующий pullChunk только после write предыдущего
global:     MAX_ACTIVE_FETCHES (default 4), MAX_TOTAL_BUFFERED_BYTES (default 8 MiB
            = 4×chunk×2 запас); сверх лимита fetch ставится в очередь, Progress
            остаётся (fetch «открыт», но ещё не тянет)
chunk size: MAX_FILE_CHUNK_BYTES (1 MiB, существующий — без изменения провода/бенчмарка)
```

Значения — стартовые константы, уточняются бенчмарком при реализации (§35), но
модель (один-в-полёте на fetch + глобальный потолок активных) фиксирована.

## 17. Concurrency

Директории → конкурентные `fetchContents` (OBSERVED 9.5). Каждый fetch имеет
собственный `FetchID`/`itemIdentifier`/entry/offset/temp/Progress/cancel-token/read_id.
**Никакого** глобального `current_file/current_offset/current_temp` — это главное
отличие нового `FileProviderBackend` от последовательного `MacFileReceiver` (code:
у staging один `_read_id/_cursor/_offset` — его переиспользовать нельзя).

Scheduler:

```
OS запрашивает N fetch → backend принимает все логически (Progress создаётся сразу)
   → scheduler держит ≤ MAX_ACTIVE_FETCHES активных (шлёт FILE_READ)
   → остальные ждут в очереди, не теряя Progress/cancel
   → на cancel очередного (ещё не активного) fetch — просто снять из очереди, completion(cancel)
   → на disconnect всех активных — fail каждого (§19), очередь очищается
```

Bookkeeping — словари `by_token`, `by_read_id` (как sender'ский `_reads`, code),
живут в Qt-потоке; IPC-вызовы маршалятся в Qt-поток паттерном
`ServiceCallbackGateway` (code) — тот же приём COM→Qt применяется NSXPC→Qt.

## 18. Cancellation

```
Finder Cancel → Progress.cancellationHandler → Swift FetchContext.cancel()
  → IPC cancelFetch(token) → Python: удалить fetch из by_token/by_read_id,
     прекратить слать FILE_READ → (опц.) TRANSFER_END когда generation стихнет
  → поздний FILE_CHUNK отброшен (не совпал read_id) → temp удаляется в extension
  → completion(NSUserCancelledError)
```

Разбор существующего провода (gate F):
- **Локально прекратить FILE_READ** — да, приёмник сам решает, слать ли следующий.
- **Один outstanding FILE_READ** — sender ответит на него FILE_CHUNK синхронно;
  приёмник его отбросит. Никакого зависшего запроса.
- **Освобождение sender-state** — sender не держит per-read state (ответ
  синхронный); fd остаётся открытым до `close_descriptors`/`release`/eviction —
  это не утечка на fetch, а нормальный lifecycle снимка (code). TRANSFER_END по
  затиханию generation закрывает fd.
- **Новый protocol message нужен?** Нет. `TRANSFER_END{cancelled}` покрывает
  завершение сессии; per-fetch cancel — чисто локальное решение приёмника.

## 19. Errors and races

| Гонка | Идемпотентное поведение |
|---|---|
| Cancel vs FILE_CHUNK в полёте | chunk придёт → не совпал read_id (fetch удалён) → отброшен; temp уже удалён |
| Disconnect vs completion | link.disconnected → fail всех fetch до completion; если completion уже ушёл — no-op |
| File completed vs Cancel | completion победил → cancel — no-op (fetch уже DONE) |
| Extension killed mid-fetch | temp в его контейнере брошен → ОС/наш GC подчистит; Python fetch осиротел → watchdog fail + IPC-disconnect чистит by_token |
| Python exits mid-fetch | extension теряет NSXPC → completion(retriable «not connected»); source остаётся dataless |
| Source исчез на Windows | FILE_ERROR source_missing → fail → File Provider error (§16) |
| Offer superseded новым clipboard | старая generation → RETIRED/IN_USE, не удаляется пока есть fetch/OS-ref (§20) |
| Один item fetch дважды / повтор до завершения | ОС обычно не делает; backend дедуплицирует по itemId — второй присоединяется к тому же fetch или отклоняется как busy |
| Temp готов, но completion IPC потерян | fetch не завершён с точки зрения ОС → ретрай fetchContents; идемпотентно (новый temp) |

## 20. SnapshotRegistry lifetime

```
SNAPSHOT_LIFETIME_MODEL = существующий Windows SnapshotRegistry (lazy held-fd,
   RETENTION=4, newest+serving переживают eviction) + generation-lease на Mac (TTL + бюджет)
```

Изучено в коде (`source.py`):
- **Когда:** `publish` при `Ctrl+C`, lazy (файлы не открываются).
- **Что гарантирует:** «удерживаемый дескриптор с обнаружением изменений» — путь
  открывается один раз (нет TOCTOU), сверка size/mtime_ns на каждом чтении;
  байтовой неизменяемости не гарантирует (обнаруживает и отказывает
  `SourceChanged`).
- **Может ли исходник измениться:** да → `FILE_ERROR source_changed` при fetch.
- **Сколько живёт:** до `release`/`release_all` (link attach/detach/lost) или
  вытеснения по `RETENTION=4` (newest + все `serving` всегда переживают).
- **После disconnect:** `release_all` (code) — снимок пропадает.
- **После нового `Ctrl+V`:** `close_descriptors` держит снимок (repeated paste),
  fd переоткрываются лениво.

Соответствие ленивому окну copy→paste: **уже обеспечено** — окно произвольно
велико (OBSERVED 38 c+), снимок ленивый и переоткрываемый. Это ровно то, что нужно
File Provider (gate E — **не** блокер).

Ограничение и v1-решение: если между copy и (отложенным) paste пришло ≥4 новых
offer, старый снимок может вытесниться по `RETENTION` → `fetchContents` вернёт
`source_missing`. Это degrade, не corruption, маппится в «source unavailable»
(§16). **v1: не менять `RETENTION`** (это sender-side, вне scope). Generation-lease
на Mac: `ACTIVE_CLIPBOARD` держит lease; активный fetch инкрементит ref; TTL
(24 ч, как staging) + бюджет ограничивают число живых generations. Поднять
`RETENTION`/добавить lease-awareness на sender — **отдельное решение владельца**,
если UX «очень старой вставки» окажется важен.

## 21. Materialization and eviction

```
v1 = CONSERVATIVE
```

- После первой материализации источник обслуживает повторный `⌘V` локально
  (OBSERVED 9.3) — повторного remote-fetch нет. Lease generation остаётся, пока
  элементы существуют + TTL/бюджет.
- **Eviction:** OBSERVED `evictItem → -2008 NonEvictable` — элементы сейчас не
  purgeable. v1 **не** реализует content-policy/eviction: элементы остаются
  материализованными. Это осознанно (не «Dropbox»).
- Если ОС всё же сделает элемент dataless снова и вызовет `fetchContents` позже:
  backend ищет снимок по `transfer_id`; **если снимок жив на Windows** — обычный
  fetch; **если умер** (disconnect/eviction/RETENTION) — `source_missing` →
  File Provider error «файл недоступен» (не удаляем item молча, не инвалидируем
  generation целиком). Durable local cache в v1 **не** держим.

## 22. Authorization

Security-инвариант усилен:

```
FILE_OFFER → sanitize → authorization (Ask prompt / Auto) 
           → ТОЛЬКО ПОСЛЕ → publish generation + pasteable URL
```

- Нет `FILE_READ` до авторизации **и** нет File-Provider-публикации/pasteable URL
  до авторизации (generation не публикуется в extension-private replica до Accept).
- Переиспользуется существующая модель (`clipboard/incoming_files ∈ {ask, auto}`,
  `_on_file_authorization_needed`, code).
- **User denies:** generation не публикуется, offer отброшен.
- **Диалог висит 5 мин:** generation не публикуется до ответа; снимок на Windows
  живёт (ленивый). При ответе Accept — публикуем.
- **Clipboard сменился до ответа:** ответ на устаревший offer всё равно может
  опубликовать generation (RETIRED сразу, если уже пришёл новый) — но безопаснее
  **не** arm'ить буфер для offer, который уже не актуален; решение: если во время
  диалога пришёл новый offer, старый по Accept публикуется как generation, но
  буфер не переармливается (актуальный буфер — у нового). Мягкий tradeoff,
  зафиксирован.

## 23. Security

Threat model (валидацию не ослабляем):
- **Malformed FILE_OFFER / huge sizes / много записей** — `sanitize_manifest`
  (MAX_ENTRIES/DEPTH/TOTAL_BYTES/manifest≤16 MiB) как есть (code).
- **Path traversal / dup ids** — `sanitize_relative_path` (Windows-strict, NFC,
  коллизии) + staging `_contained` defense-in-depth; File Provider temp — в
  sandbox-контейнере extension (`entry_index`-адресация, имена из
  sanitized-манифеста).
- **Symlink/special** — scanner помечает reparse-point в skipped; `files/2`
  несёт только file/directory (code) → File Provider v1 сохраняет ограничение (§29).
- **Directory cycles** — MAX_DEPTH=32 + scanner не идёт по reparse (code).
- **Extension IPC spoofing / посторонний процесс на IPC** — listener в extension
  принимает соединения только через `listener:shouldAcceptNewConnection:` с
  `SecCodeCheckValidity` по code-signing requirement (Team ID); endpoint anonymous,
  доставляется доверенной FP-инфраструктурой (не по mach-имени). App Group / UDS не
  используются. `XPC_PEER_AUTHENTICATION = PASS` (OBSERVED, §7).
- **Stale fetch tokens** — backend валидирует token в by_token; неизвестный →
  игнор.
- **Unexpected FILE_CHUNK offset/length** — `_on_chunk`-подобная проверка
  read_id/offset/expected (code-паттерн) → oversized/truncated → fail.
- **Disk exhaustion** — temp в extension: проверять свободное место контейнера;
  ошибка записи → fail (§16 disk full). Staging уже имеет `has_room_for`.
- **Source changed между metadata и read** — `SourceChanged` (code) → source_changed.

`paths.py` остаётся Windows-strict и на macOS (не переписываем ради File Provider).

## 24. Restart / recovery

| Сценарий | v1-гарантия |
|---|---|
| Extension killed (НЕ kill -9; система рестартит), Python alive | ОС перезапустит extension; connection invalidated → host по необходимости rediscover/reconnect (OBSERVED `EXTENSION_RESTART_RECONNECT = PASS`); enumeration из extension-private replica; активные fetch осиротели → watchdog fail; повторный fetchContents переустановит соединение |
| Python restarted, extension alive | extension теряет NSXPC-client → `fetchContents` (свежие байты) отдаёт retriable «not connected»; enumeration/item metadata работают **без host** (private replica, OBSERVED `HOST_ABSENT_NO_CRASH`/Gate 6 = PASS); host на старте: rediscover service → reconnect |
| Весь app restart | домен переживает; generations из private replica; host рестарт → discover existing domain/service → establish new connection (OBSERVED `HOST_RESTART_REDISCOVERY = PASS`); snapshots на Windows возможно уже `release_all` (disconnect) → fetch старых → source_missing |
| Mac reboot | домен переживает; связь переустанавливается; старые snapshots на Windows мертвы → старые generations degrade → purge stale на старте |
| Windows disconnect/reconnect | link.disconnected → release_all на Windows; активные fetch fail; generations остаются как метаданные (private replica), но fetch → source_missing до нового offer |
| Domain содержит старые generations после потери Python state | extension-private replica — источник истины для enumeration; на старте host purge generations без живого снимка (publish retire по XPC) |

Допустимо: **не** сохранять remote-clipboard через reboot. На старте — purge
stale generations (нет активных OS-ref/URL) + освежить буфер только для актуальной.

## 25. Storage / GC

```
PERSISTENT_MANIFEST = YES
MANIFEST_STORAGE    = extension-private durable replica (App Group НЕ используется)
```

- **Что:** durable metadata replica в **собственном контейнере extension**
  (`~/Library/Containers/<ext-bundle-id>/Data/Library/Application Support/…`,
  OBSERVED Gate 5 = PASS) — по одному JSON на generation, формат = существующий
  `encode_manifest` (sanitized) + служебные поля `{state, created_ns,
  lease_deadline}`. **Owner хранилища — extension**; **Python — authoritative
  owner генерации/манифеста** и публикует обновления реплики по XPC.
- **Реплика содержит только метаданные:** stable itemIdentifier, itemVersion,
  filename, parent, size, kind, capabilities, generation-метаданные, необходимые
  File Provider — достаточные для enumeration/`item(for:)`/реконструкции дерева.
  **Не** содержит: байты файлов, FILE_*-состояние, peer/TLS-состояние, authorization
  policy, `SnapshotRegistry`, remote transfer scheduler.
- **Atomic update semantics (инвариант):**
  ```
  Python публикует authorized generation
    → publishGeneration(replicaUpdate) по XPC
    → extension persists реплику АТОМАРНО (temp+rename в своём контейнере)
    → ACK
    → только после ACK generation considered publishable/ready для clipboard URL (§13)
  ```
  Точную сериализацию (единый JSON vs per-generation файлы, схема diff) оставляем
  implementation plan — research её окончательно не фиксировал.
- **Почему replica, а не App Group:** extension запускается независимо/раньше Python
  (§6, §33) и обязан отвечать на enumeration/`fetchContents` без host — доказано
  OBSERVED (Gate 6: enumeration + материализация ранее опубликованного при
  отсутствующем host/Python). Replicated extension и так владеет собственным
  контейнером → App Group не нужен нигде (`APP_GROUP_REQUIRED_OVERALL = NO`).
- **GC:** на старте + периодически (переиспользовать TTL/бюджет идею из
  `StagingArea.gc`, code): RETIRED без активных fetch/OS-ref и старше TTL (24 ч) →
  host publish retire по XPC → extension удаляет запись реплики + сигнал ОС удалить
  элементы generation; бюджет на число generations. ACTIVE_CLIPBOARD и IN_USE не трогаем.

## 26. Packaging / signing

```
PACKAGING_MODEL        = Duo Input.app (Nuitka) + DuoInputFileProvider.appex (Swift,
                         Xcode/SwiftPM) в Contents/PlugIns/ + NSFileProviderServiceSource
PACKAGING_ARCHITECTURE = UNBLOCKED   (Phase 10.2: собираемость/подпись/встраивание/
                         domain-add + XPC round-trip доказаны на бесплатном Personal Team)
APP_GROUP_REQUIRED     = NO
```

```
Duo Input.app/Contents/
  MacOS/app                            (Nuitka Python бинарь — как сейчас)
  Frameworks/, Resources/              (как сейчас)
  PlugIns/DuoInputFileProvider.appex   (Swift native, sandboxed; владелец listener)
  (XPCServices/ не нужен — listener хостит extension, host — client)
```

Кто что собирает:
- **Python runtime** — Nuitka `--standalone --macos-create-app-bundle` (как сейчас,
  record 2026-09-17; OBSERVED Gate 4: Nuitka-host со встроенным `.appex` замкнул
  XPC round-trip).
- **Swift `.appex`** — Xcode/SwiftPM отдельным шагом; затем встраивание в
  `Contents/PlugIns` уже собранного `.app`.
- **Порядок:** собрать Nuitka host `.app` → собрать Swift `.appex` → встроить →
  **подписать вложенный extension первым, затем внешний app** (`--options runtime`)
  → verify (`codesign --verify --deep --strict`) → (дистрибуция) notarize.

Entitlements (OBSERVED-подпись Phase 10.2, минимально-необходимые):
```
host  (.app)   : пусто в проде (dev: get-task-allow). НЕТ restricted entitlement.
appex (.appex) : com.apple.security.app-sandbox = true   (dev: + get-task-allow)
```

**Убрано из production architecture (Phase 10.2), т.к. штатный service-source не
требует ничего из этого:**
- App Group (`com.apple.security.application-groups`) — не нужен ни для IPC, ни для store;
- named Mach service — listener anonymous;
- temporary-exception entitlement;
- host restricted `com.apple.developer.*` entitlement, существовавший ради App Group
  (именно он ронял ad-hoc Nuitka-`.app` с error 153 — риск снят, т.к. entitlement
  больше не нужен);
- shared filesystem container между host и extension.

`.appex` (app-sandbox) собирается и подписывается на **бесплатном Personal Team**
(OBSERVED 2026-09-17 + 2026-09-19). Ни App Group, ни Developer Program для сборки не
требуются; Developer ID + notarization нужны только для **дистрибуции** (отдельный
milestone, независимо от File Provider). Утверждение «App Group — packaging-blocker
для Nuitka host» **снято** (Phase 10.2, `PACKAGING_ARCHITECTURE = UNBLOCKED`).

## 27. Staging fallback

```
STAGING_FALLBACK = per-offer выбор backend при получении offer; без смены semantics
                   внутри generation
```

- File Provider недоступен (домен не READY за окно / extension отсутствует / IPC
  недоступен / unsupported macOS / ошибка регистрации/подписи) → **этот offer**
  обслуживается staging (существующий `MacFileReceiver`).
- Backend выбирается **один раз на offer** и не меняется по ходу generation
  (избегаем неожиданной смены семантики посреди clipboard-generation).
- Staging **не** удаляется — остаётся дефолтом там, где File Provider недоступен,
  и safety-net на весь rollout (§30-rollout).

## 28. Observability

Correlation-id через весь стек: `transfer_id` (=generationId), `entry_index`
(=entryId), `fetch_token`, `read_id`. Лог-цепочка:

```
Finder ⌘V → fetchContents(itemId) → IPC openFetch → backend map → FILE_READ(read_id)
  → Windows _answer_read → FILE_CHUNK → _on_chunk → IPC chunkBytes → temp write → completion
```

Без логирования сырых имён/содержимого пользователя (правило §15 spec staging,
code: только имя, не путь). Counters:

```
fp_fetch_started / _completed / _cancelled / _failed
fp_active_fetches (gauge) / fp_queued_fetches
fp_bytes_received
fp_domain_not_ready / fp_domain_state
fp_ipc_connect / fp_ipc_disconnect
fp_late_chunk / fp_oversized_chunk / fp_truncated
fp_gc_generation / fp_generation_active
fp_backend_selected{file_provider|staging}
```

## 29. Symlinks / special files

`files/2` несёт только `ENTRY_FILE`/`ENTRY_DIRECTORY` (code: `_KINDS`); scanner
кладёт reparse-point в skipped (code). File Provider v1 **сохраняет** это:
supported entry kinds = `{regular file, directory}`. Symlink/socket/device/FIFO/
package — **не** добавляются. Директория `fetchContents` не требует (enumerator
реконструирует из манифеста).

## 30. Metadata mapping

| Duo Input (manifest) | NSFileProviderItem | Примечание |
|---|---|---|
| `transfer_id`+`entry_index` | `itemIdentifier` | §9 |
| родитель по path | `parentItemIdentifier` | директория-предок или контейнер generation |
| `PurePosixPath(path).name` | `filename` | имя из sanitized-пути |
| `size` | `documentSize` | из манифеста |
| `kind` | `contentType` (UTType) | file→по расширению/`public.data`; directory→`public.folder` |
| `size`+`mtime_ns` | `itemVersion` | детерминированно, §9 |
| — | `capabilities` | `[.allowsReading, .allowsWriting]` (§3, снять `uchg`) |
| — | `fileSystemFlags` | `[.userReadable, .userWritable]` (→0600) |
| (нет exec в манифесте) | `.userExecutable` | **не** ставим v1 — манифест не несёт mode (code); документированное ограничение |

`itemVersion` детерминирован (не random per-enumeration) — иначе ОС считает item
изменившимся.

## 31. Directories

Всё дерево метаданных **уже** в `FILE_OFFER` (manifest несёт file **и** directory
записи с size/mtime, code) → enumerator реконструирует дерево из extension-private replica,
lazy-directory-metadata протокол **не нужен**. Директория fetch не требует; при
`⌘V` папки материализуются все файлы поддерева (OBSERVED 9.5, конкурентно →
concurrency §17). **Gate D — НЕ блокер:** метаданных достаточно.

## 32. Restart recovery — см. §24. Startup: purge stale generations допустим.

Дополнительно (open, doc-level): скрытие transport-namespace из Finder sidebar —
исследовать при реализации, не блокер.

## 33. Persistent metadata — см. §25 (`PERSISTENT_MANIFEST = YES`, extension-private replica).

Extension **не** должен crash/врать, если ОС зовёт enumeration до старта Python:
enumeration/`item(for:)` — из extension-private replica (OBSERVED Gate 6: работает
при полностью отсутствующем host); `fetchContents`, требующий свежих удалённых байт,
без host → retriable-ошибка, не crash.

---

## Alternatives rejected (§31 задания)

| Отклонено | В пользу | Причина |
|---|---|---|
| domain-per-offer | один стабильный домен | `-2011` и стоимость add/remove на каждый `Ctrl+C` (OBSERVED) |
| Python пишет temp (Option A) / helper (C) | Swift пишет temp (B) | URL должен быть доступен sandboxed-extension без общего ФС-пути; pull-backpressure |
| app-group-mach NSXPC (listener в app) | `NSFileProviderServiceSource` (listener в extension) | штатный канал FP без App Group/mach-имени; доказан Phase 10.2 (см. «Superseded architecture») |
| UDS в App Group container как IPC | `NSFileProviderServiceSource` | App Group исключён; anonymous endpoint достаточно |
| IPC-only модель (без реплики) | extension-private metadata replica | extension стартует раньше/без Python — enumeration обязана работать без него (OBSERVED Gate 6) |
| Вариант C (lazy `public.file-url`) | File Provider | records: эагерное чтение буфера через ~100 мс, синхронно, ломает privacy-инвариант |
| Новый `FILE_CANCEL` | существующий TRANSFER_END + локальный стоп | cancel достижим без изменения провода (§18) |
| Поднять RETENTION / lease на sender сейчас | оставить как есть | sender вне scope; degrade «старой вставки» приемлем v1 |
| Перенос exec-бита (wire change) | не переносить | вне scope; Windows-источник без POSIX exec |

## Decision log (§41 задания)

**IPC = NSFileProviderServiceSource (anonymous NSXPCListener в extension, host —
client).** Why: штатный first-class канал FP; App Group/mach-имя не нужны;
bidir/streaming/cancel/concurrency нативно через `exportedObject`. Доказано
Phase 10.2 (`NATIVE_FP_IPC_GATE = PASS`). Инвариант: extension обязан конформить
`NSFileProviderServicing`. Alternatives (Superseded): app-group-mach NSXPC (listener
в app), UDS в App Group. Failure mode: host недоступен → `fetchContents` retriable;
enumeration остаётся из private replica. Tradeoff: XPC-протокол должен быть
clang-скомпилирован (не `formal_protocol`).

**temp-file owner = Swift extension.** Why: URL доступен sandbox; ФС-путь не
пересекает границу. Alt: Python пишет (нужен общий контейнер). Failure: диск полон
→ fail. Tradeoff: байты дважды в памяти extension кратко — приемлемо (1 MiB чанк).

**App Group = NO.** Why: штатный service-source не использует mach-имя (IPC), а
durable store заменён extension-private replica (§25) — App Group не нужен нигде
(`APP_GROUP_REQUIRED_OVERALL = NO`, OBSERVED). Alt (Superseded): App Group для
mach-IPC + shared store. Failure: н/д (restricted entitlement устранён → риск
error-153 снят). Tradeoff: Python не пишет напрямую в store — публикует по XPC с ACK.

**persistent manifest = YES (extension-private replica).** Why: enumeration/fetch
без Python (OBSERVED Gate 6). Owner хранилища — extension; Python authoritative,
публикует по XPC атомарно с ACK (§25). Alt: IPC-only (ломает early-enumeration),
App Group store (не нужен). Failure: replica corrupt → generation пропущена (degrade).
Tradeoff: дублирование метаданных в контейнер extension (дёшево, только описание дерева).

**domain model = один стабильный домен.** Why: `-2011`/стоимость. Alt: per-offer.
Failure: домен disabled → DEGRADED→staging. Tradeoff: transport-namespace виден в
Finder.

**snapshot lifetime = существующий SnapshotRegistry + Mac-lease.** Why: уже
ленивый/долгоживущий/переоткрываемый (code). Alt: менять sender (вне scope).
Failure: RETENTION вытеснил старый → source_missing (degrade). Tradeoff: «очень
старая вставка» может не пройти.

**eviction v1 = conservative (без content-policy).** Why: OBSERVED NonEvictable;
не «Dropbox». Alt: purgeable+refetch. Failure: диск не освобождается автоматически.
Tradeoff: место расходуется до GC generation.

**concurrency = per-fetch один-в-полёте + global MAX_ACTIVE_FETCHES.** Why:
директории конкурентны (OBSERVED); backpressure. Alt: строгая сериализация (сломает
директории). Failure: слишком мало параллелизма → медленнее (tuning). Tradeoff:
сложнее bookkeeping (новый backend, не staging).

**staging fallback = per-offer, без смены внутри generation.** Why: не менять
семантику по ходу. Alt: startup-only выбор. Failure: домен не READY → staging.
Tradeoff: два пути сосуществуют.

## Диаграммы (§40 задания)

### Copy (байты НЕ идут)

```
Windows Ctrl+C
  → scan + SnapshotRegistry.publish (lazy, файлы не открыты)
  → FILE_OFFER{manifest}  ──wire──▶  Mac
Mac: sanitize → authorization (Ask/Auto)
  → [Accept] publish generation (XPC → extension-private replica, ACK) → domain READY
  → getUserVisibleURL(root items) → host-only NSPasteboard.arm(root URLs)
  ✗ НИ ОДНОГО FILE_READ. Источник dataless. Байты на месте, на Windows.
```

### Paste

```
Finder ⌘V (папка вне домена)
  → fetchContents(itemId)                       [extension]
  → по установленному bidir NSXPCConnection: extension → host callback
      openFetch(gen,entry)                       [→ Python]
  → map itemId→(transfer_id,entry_index); reply totalSize
  → loop: pullChunk → FILE_READ(offset,len,read_id) ──wire──▶ Windows
        Windows _answer_read → SnapshotRegistry.read(open+verify fd) → FILE_CHUNK ──wire──▶
        _on_chunk(verify) → IPC chunkBytes → extension.write(temp); Progress += n
     (до offset==size)
  → eof → fsync/close temp → completionHandler(tempURL, item{caps/flags})
  → Finder создаёт независимую копию (0600, без uchg).
```

### Cancel (end-to-end)

```
Finder Cancel → Progress.cancellationHandler → FetchContext.cancel()   [extension]
  → IPC cancelFetch(token)                                              [→ Python]
  → backend: drop by_token/by_read_id; стоп FILE_READ
  → поздний FILE_CHUNK (на outstanding read) отброшен (read_id не совпал)
  → extension удаляет temp → completion(NSUserCancelledError)
  → destination пуст; source остаётся dataless; snapshot на Windows жив (repeated paste)
```

### Directory (конкурентные дети)

```
Finder ⌘V (папка) → fetchContents параллельно для folder/a.txt и folder/nested/b.txt
  fetch A: openFetch(gen, idx_a)  ─┐
  fetch B: openFetch(gen, idx_b)  ─┤ scheduler ≤ MAX_ACTIVE_FETCHES
      каждый: свой token/offset/temp/Progress/read_id
      FILE_READ(read_id=Ra) и FILE_READ(read_id=Rb) в полёте одновременно
      Windows читает разные entry_index независимо (held-fd по entry_index)
  → два completionHandler, дерево воспроизведено.
```

### Failure — потеря Python/IPC во время fetch

```
fetch активен → Python.app падает / IPC рвётся
  extension: NSXPC invalidated в середине pullChunk
  → нет байтов → удалить temp → completion(NSError, retriable "not connected")
  → source остаётся dataless (ре-fetch возможен, когда Python вернётся)
Python сторона (если жив, но link упал): link.disconnected → release_all (Windows),
  fail всех fetch, IPC error каждому → completion(mapped §16).
```

## Error mapping (§16 задания)

| Duo Input/Python failure | IPC result | File Provider error |
|---|---|---|
| source missing (SourceMissing/RETENTION вытеснил) | error `source_missing` | `NSFileProviderError.noSuchItem` или `cannotSynchronize` |
| source changed (`SourceChanged`) | error `source_changed` | `NSFileProviderError.serverUnreachable`/`cannotSynchronize` (ре-enumerate версии) |
| peer disconnected | error `peer_lost` | `NSFileProviderError.serverUnreachable` |
| auth revoked / files off | error `unauthorized` | `NSFileProviderError.notAuthenticated` |
| session timeout (watchdog) | error `timeout` | `NSFileProviderError.serverUnreachable` |
| disk full (temp) | error `disk_full` | `NSError POSIX ENOSPC` |
| protocol error (oversized/truncated/bad_request) | error `protocol` | `NSFileProviderError.cannotSynchronize` |
| cancelled | (cancelFetch) | `NSUserCancelledError` (Cocoa) |
| IPC/service unavailable (Python down) | (нет соединения) | retriable `NSError` (напр. `serverUnreachable`) — **не** удалять item |

(Точные константы `NSFileProviderError.*` подтвердить по SDK-заголовкам на этапе
реализации — не выдумывать; таблица фиксирует намерение.)

## Test strategy (§36 задания)

**Pure Python** (без AppKit/Swift/сети): scheduler (лимиты активных/очередь);
cancellation races (§19); late/oversized/truncated chunks; generation GC/lease;
itemId↔(transfer_id,entry_index) маппинг + версии; backend selection (READY vs
staging); disconnect fail-all. По образцу существующих `tests/transfer/*` на
фейковой связи (`device/emulator.py`, code).

**Swift unit:** item metadata/identifiers/versions (детерминизм); enumerator из
manifest JSON; error mapping; IPC-client state machine; caps/flags → ожидаемые
0600/без uchg (по матрице 9.6).

**IPC integration:** фейковый Python-listener ↔ extension (или наоборот) —
openFetch/pullChunk/cancel/eof/error, disconnect в середине.

**End-to-end macOS (human-in-the-loop, как spike):** FILE_OFFER-симуляция →
generation → буфер → `⌘V` → fetch → байты; прогресс/отмена/атрибуты.

**Cross-platform E2E (2 машины):** обязательные сценарии — single small / 100 MB /
multi-GB / cancel / disconnect / source deleted / multiple flat / directory
concurrent / repeated paste / new clipboard while old fetch active / app restart /
extension restart / disk full / domain not ready / unicode names / duplicate names
in different dirs / zero-byte.

## Rollout / migration (§37 задания)

```
Stage 1: FileProviderBackend за feature-flag (по умолчанию OFF), staging = дефолт
Stage 2: developer opt-in (flag ON вручную); архитектура уже подтверждена
         Phase 10.2 (`NATIVE_FP_IPC_GATE = PASS`)
Stage 3: default ON на supported macOS с валидным доменом; staging = автоматический fallback
Stage 4: staging остаётся постоянным fallback (не удаляется)
```

Rollback тривиален: выключить flag → весь трафик снова через staging (backend
выбирается per-offer, §27). Никакой миграции данных.

## Supported macOS (§25 задания)

`NSFileProviderReplicatedExtension` — macOS 11+. Nuitka-бандл уже таргетит
«macOS 15.0+ (arm64)» (record 2026-09-17). v1:

```
FileProviderBackend доступен при: macOS ≥ 11, домен зарегистрирован и READY,
                                  extension присутствует, IPC установлен.
иначе → StagingBackend.
```

Точный минимум подтвердить по используемым File Provider API на этапе реализации.

## Implementation boundaries (§33 задания — что НЕ решается «по ходу»)

Решено в этом документе: IPC (NSFileProviderServiceSource, §7), temp-owner (Swift), stable itemId (§9),
itemVersion (§9), generation lifecycle (§11/§25), snapshot-lease (§20), cancel-mapping
(§18), concurrency (§17), domain readiness (§10), extension/Python restart (§24),
persistent metadata (§25/§33), GC (§25), fallback (§27), packaging-граница (§26).
Оставлено на бенчмарк реализации (tuning): `MAX_ACTIVE_FETCHES`,
`MAX_TOTAL_BUFFERED_BYTES`, TTL/бюджет generations, точные `NSFileProviderError.*`
константы, окно ожидания READY.

---

## Wire protocol gate (§38 задания)

```
FILES2_PROTOCOL_CHANGE_REQUIRED = NO
```

Подтверждено чтением кода (не только документации):
`FILE_OFFER` (полное дерево в манифесте) → §31; `TRANSFER_BEGIN` (sender
игнорирует, симметрия) → §15; `FILE_READ` (адресация `(transfer_id, entry_index,
offset, length, read_id)`, concurrency по entry_index) → §15/§17; `FILE_CHUNK`
(байты → temp) → §16; `FILE_ERROR` (маппинг) → §16; `TRANSFER_END`
(close_descriptors, repeated paste) → §15/§20. Ни одного impossible race, требующего
нового сообщения (§18). Добавлять `FILE_CANCEL` **запрещено**.

## Stop conditions (§43 задания) — оценка

| Gate | Вердикт | Обоснование |
|---|---|---|
| A: `files/2` не может обслужить concurrent fetch без protocol change | **НЕ hit** | sender concurrency-safe по entry_index/read_id (code, §15/§17) |
| B: extension не может общаться с Python без entitlement/арх., меняющей packaging | **НЕ hit → CLOSED** | штатный `NSFileProviderServiceSource` без App Group/mach-имени; собираемость/подпись/встраивание/domain-add + XPC round-trip доказаны на Personal Team (Phase 10.2, `PRODUCTION_DESIGN_GATE_B = CLOSED`) |
| C: нужен App Group/XPC entitlement, недоступный текущей distribution model | **НЕ hit → CLOSED** | App Group исключён; на Nuitka-хосте restricted entitlement не требуется — только обычная Personal-Team подпись (`PRODUCTION_DESIGN_GATE_C = CLOSED`) |
| D: `FILE_OFFER` метаданных недостаточно для directory enumeration | **НЕ hit** | манифест несёт полное дерево (§31) |
| E: SnapshotRegistry не даёт lazy lifetime без фундаментального изменения sender | **НЕ hit** | уже ленивый/переоткрываемый (§20); degrade старой вставки — не corruption |
| F: невозможно корректно отменить существующим FILE_* без утечки sender | **НЕ hit** | cancel локален + TRANSFER_END; sender-state не течёт (§18) |
| G: выбор между двумя архитектурами с разными product/security tradeoffs | **НЕ hit** | packaging-вопрос закрыт (B/C CLOSED); эскалации не осталось |

Эскалаций не осталось: B/C **CLOSED** (Phase 10.2), провод не меняется
(`FILES2_PROTOCOL_CHANGE_REQUIRED = NO`), `RESEARCH_PHASE = COMPLETE`.

---

## Superseded architecture (Phase 10.2 — НЕ реализовывать)

Ниже — исходная (до Phase 10.2) IPC-модель, **опровергнутая** как требуемый механизм.
Оставлено только для истории; production architecture — §7/§25/§26.

```
SUPERSEDED:
  extension → app-group-scoped named NSXPC → NSXPCListener в Python host
  APP_GROUP_REQUIRED = YES
```

Почему отклонено:
- Штатный канал FP — `NSFileProviderServiceSource`: anonymous `NSXPCListener` **в
  extension**, client — **в app**; App Group и named Mach service не нужны
  (DOCUMENTED SDK + OBSERVED Phase 10.2).
- Направление listener/client в старой модели **инвертировано** относительно штатного.
- App Group как restricted capability роняла ad-hoc Nuitka-host (error 153) —
  устранением App Group риск снят.
- Durable store в App Group container заменён extension-private replica.

Исходный сравнительный анализ кандидатов (NSXPC app-group-mach / UDS в App Group /
global-mach + temp-exception / App-Group-file-watch / distributed notifications)
относился к этой superseded-модели и более **не** является основанием для решения.

## VALIDATION_RISK_FP_FRESH_DOMAIN (dev-only, НЕ blocker)

Отдельно от production architecture. НЕ смешивать с архитектурными выводами.

OBSERVED в изношенном dev-окружении Phase 10.2:

```
fresh File Provider domain
  → -2011 "Синхронизация не включена для приложения «(null)»"
  → initial import/create root wedge
```

Это состояние: пережило reboot; наблюдалось даже под новым bundle id; **не** является
доказанным production-failure архитектуры (идентичный extension-бинарь безупречно
обслуживал ранее enabled домен). Накопленный dev FS-sync state fileproviderd —
подозреваемый фактор (множественные `kill -9` extension в прошлых сессиях заклинивают
FS-sync; XPC при этом продолжает работать — поэтому XPC-gates PASS, а FS-gate
требовал чистого fileproviderd).

Production validation requirements:
1. **НЕ** использовать `kill -9` extension как нормальную dev/prod lifecycle-операцию.
2. Перед release проверить first-run domain creation на **чистой** macOS
   machine/user environment:
   ```
   add domain → initial import → root created → enabled
   ```
3. Отдельно позже документировать безопасный dev-reset FS-state fileproviderd
   (reboot недостаточно; удаление persistent store под SIP — отдельная задача). **НЕ**
   решать через SIP/private-store manipulation в production implementation.

Статус: `VALIDATION_RISK_FP_FRESH_DOMAIN` — validation-item, **НЕ** blocker текущего
implementation.

## Bundle ID hygiene (build/test invariant)

```
host bundle identifier
extension bundle identifier
File Provider domain identifier
```

должны быть **уникальны и согласованы**. Не переиспользовать bundle ID параллельно
установленными spike/prod приложениями: Phase 10.2 OBSERVED реальную collision между
spike-сборками (`com.duoinput.DuoNuitka` уже занят `/Applications/DuoNuitka.app` →
identity-poisoning), что диктует уникальную identity в dev/test packaging.

## Revision note (Phase 10.2)

```
Phase 10.2 revision:
- native NSFileProviderServiceSource IPC proven
- NSFileProviderServicing requirement proven
- bidirectional round-trip proven
- extension-private operation without host proven
- App Group removed
- gates B/C closed
- research complete
```

## Final output

```
DESIGN_STATUS                    = READY
RESEARCH_PHASE                   = COMPLETE

NATIVE_FP_IPC_GATE               = PASS
FILES2_PROTOCOL_CHANGE_REQUIRED  = NO

IPC_MODEL                        = NSFileProviderServiceSource
XPC_LISTENER_OWNER               = Swift File Provider extension
HOST_ROLE                        = PyObjC NSXPC client + exported callbacks

APP_GROUP_REQUIRED               = NO
MANIFEST_STORAGE                 = extension-private durable replica
TEMP_FILE_OWNER                  = Swift extension

PRODUCTION_DESIGN_GATE_B         = CLOSED
PRODUCTION_DESIGN_GATE_C         = CLOSED

STAGING_FALLBACK                 = PRESERVED
FRESH_DOMAIN_VALIDATION          = REQUIRED_ON_CLEAN_MACHINE

NEXT_MILESTONE                   = production implementation plan
```
