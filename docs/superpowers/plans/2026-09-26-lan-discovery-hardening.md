# Автоматическое обнаружение Windows ↔ macOS по UDP multicast — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** доверенные Duo Input на Windows и macOS находят друг друга в одной LAN без ручного IP и без платного Apple Developer Program. Для этого существующий UDP multicast discovery получает недостающие исправления корректности, безопасности и работы с несколькими интерфейсами.

**Architecture:** вариант A из brainstorming 2026-09-26.
- Сохраняются группа `239.255.76.67:47655`, формат маячка и интервал 2 с.
- Маячок доверенного пира становится **подсказкой адреса**. Он проходит то же правило немедленного звонка, что и адреса от платы (`_may_dial_on_hint`), и хранится только в памяти: словарь «адрес → срок» (TTL — константа политики), адресов может быть несколько, если у пира несколько интерфейсов. На диск (`last_address`) попадает только адрес, по которому прошло TLS-рукопожатие с закреплённым отпечатком.
- `Discovery` вступает в группу и шлёт маячок на каждом LAN-интерфейсе, а не только через маршрут по умолчанию.
- Transport, clipboard, передача файлов и File Provider не меняются.

**Tech Stack:** Python 3.12, PySide6 6.10 (`QUdpSocket`, `QNetworkInterface`), pytest + pytest-qt. Для spike — Nuitka 4.1.3 и `codesign` (Personal Team).

**Spec:** отдельного spec-файла нет. Пользователь согласовал дизайн в чате brainstorming 2026-09-26 и сразу перевёл работу в план. Источники истины:
- этот план;
- Apple TN3179 «Understanding local network privacy» (ред. 2026-02-17): *«The multicast entitlement isn't required on macOS»*;
- `docs/superpowers/specs/2026-09-03-shared-clipboard-design.md` §9–§10: маячок не даёт доверия, доверие даёт парринг;
- `docs/superpowers/specs/2026-09-25-peer-address-exchange-design.md`: адреса от платы и приоритет ручного ввода.

## Global Constraints

- **Не трогать:** `clipboard/peer.py`, `clipboard/service.py`, `clipboard/wire.py`, `clipboard/listener.py`, `clipboard/pairing.py`, `transfer/*`, `fileprovider/*`, протокол парринга, упаковку (`packaging/*`).
- **Wire маячка не меняется:** `GROUP_ADDRESS = "239.255.76.67"`, `BEACON_PORT = 47655`, `BEACON_INTERVAL_MS = 2000`, JSON `Beacon`. Старые сборки продолжают понимать новые, и наоборот.
- **Ручной адрес:** если задан, он единственный кандидат (`_candidates() == [manual]`). Семантика не меняется.
- **Правило адресов от платы** (коммиты `5e69e37a`, `044ea688`) не меняется. Его тесты остаются зелёными без правок.
- **Не добавлять:** зависимостей, Bonjour/mDNS, `com.apple.developer.networking.multicast`, ключей Info.plist. Windows wake-hook вне scope.
- **Ветка:** обмен адресами живёт только в `feature/fileprovider-read-window-v2`, в `main` его нет. Работать в новой ветке `feature/lan-discovery-hardening` от неё.
- **Тесты на Mac** запускаются из `configurator/` командой `../.venv-mac/bin/python -m pytest … -q -p no:cacheprovider` вне sandbox: часть тестов координатора открывает настоящие UDP-сокеты. Baseline `tests/clipboard`: **367 passed, 1 skipped** (2026-09-26).
- **Тесты на Windows:** `configurator\.venv\Scripts\python.exe -m pytest …`. Голый `python` не годится.
- **Язык комментариев** — русский, как в `clipboard/`.
- **Проверка мутациями:** каждую добавленную задачей защиту нужно удалить локально и убедиться, что падает хотя бы один тест. Затем вернуть.
- **Коммиты:** один коммит на задачу, без push. Сообщение заканчивается строкой `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- **Gate:** Task 0 — жёсткий gate. Task 1 и далее начинаются только после PASS Task 0 (S1 PASS и S2 PASS после Allow) **и** явного подтверждения пользователя.

## Карта файлов

| Файл | Что меняется |
| --- | --- |
| `scratchpad/discovery-spike/probe.py`, `build-probe-app.sh` | **Одноразовое**, не коммитится (Task 0) |
| `docs/superpowers/records/2026-09-26-macos-multicast-discovery-spike.md` | Новый record с результатами spike (Task 0) |
| `docs/superpowers/records/2026-09-17-macos-app-packaging.md` | Поправка к ошибочному выводу про entitlement (Task 0) |
| `configurator/src/duo_input/clipboard/coordinator.py` | Правило подсказок, фильтр отпечатка, привязка перебора кандидатов к набранному адресу, кэш адреса маячка с TTL (Tasks 1–4), комментарий (Task 7) |
| `configurator/tests/clipboard/test_coordinator.py` | Регрессионные тесты (Tasks 1–4) |
| `configurator/src/duo_input/clipboard/local_addresses.py` | Выбор интерфейсов для multicast (Task 5) |
| `configurator/tests/clipboard/test_local_addresses.py` | Тесты выбора интерфейсов (Task 5) |
| `configurator/src/duo_input/clipboard/discovery.py` | join/send на каждом интерфейсе и журнал отказов отправки (Task 6) |
| `configurator/tests/clipboard/test_discovery_beacon.py` | Тесты multi-interface (Task 6) |
| `docs/superpowers/records/validation/discovery-win-mac-e2e.md` | Двухмашинный E2E runbook (Task 7) |

## Два решения, которые стоит проверить при ревью

### Кэш адресов маячка с TTL (Task 4)

- **Структура:** обычный словарь координатора `_discovered_addresses: dict[str, float]` вида «адрес → момент протухания, монотонные мс». Отдельного класса нет. Слот не один: пир с Wi-Fi и Ethernet шлёт маячок через оба интерфейса (Tasks 5–6), и оба адреса — равноправные свежие подсказки. Правило «последний маячок побеждает» потеряло бы один из них.
- **Порядок** — порядок вставки в dict, то есть кто услышан раньше. Повторный маячок того же адреса только продлевает срок и не двигает адрес в очереди, поэтому порядок набора не дёргается от каждого маячка.
- **Время:** инжектируемые часы `clock: Callable[[], float]` (мс, по умолчанию `time.monotonic() * 1000`), только ради детерминированных тестов. Отдельного `QTimer` нет: протухшие адреса лениво исключаются в `_fresh_discovered_addresses()` и вычищаются при следующем маячке.
- **TTL** = `DISCOVERED_ADDRESS_TTL_MS = 5 * BEACON_INTERVAL_MS` = 10 с. Это **константа политики, а не свойство протокола**: маячок о ней не знает, её можно менять без вопросов совместимости. Обоснование начального значения: пир в поиске шлёт маячок каждые 2 с; пять интервалов переживают несколько подряд потерянных multicast-датаграмм по Wi-Fi; каждый маячок продлевает срок, поэтому адрес доживает и до самого длинного повтора (30 с).
- **Предел** `MAX_DISCOVERED_ADDRESSES = MAX_HOST_ADDRESSES` (8) — тоже политика. Настоящий пир рекламируется не более чем через 8 интерфейсов, поэтому поток поддельных маячков с разными IP не раздувает перебор. Сверх предела вытесняется адрес, который протухнет раньше всех.
- **Сброс:** при успешном соединении (удачный адрес становится `last_address`), а также в `stop()`, `forget_peer()` и `recover_after_resume()`. `time.monotonic` на macOS не считает время сна, и адреса, услышанные до сна, выглядели бы свежими после пробуждения.

### Multicast на нескольких интерфейсах (Tasks 5–6)

- **Сокет один** — тот же `QUdpSocket`, bind `AnyIPv4:47655`. Приём: `joinMulticastGroup(group, iface)` на каждом выбранном интерфейсе. Отправка: `setMulticastInterface(iface)`, затем `writeDatagram` для каждого интерфейса. Это стандартный API Qt без платформенного кода.
- **Выбор интерфейсов** — чистая функция `select_multicast_entries()` рядом с существующей `select_addresses()` и с тем же фильтром: up, running, не loopback, не virtual, не vEthernet/WSL/VirtualBox/VMware. Дополнительно требуется `CanMulticast`, отсутствие `IsPointToPoint` и хотя бы один пригодный IPv4.
- **Почему `IsPointToPoint`:** на реальном Mac этого разработчика `utun0..5` заявляют `CanMulticast`. Эти интерфейсы — точка-точка, но у них нет IPv4, так что их отсекает уже правило IPv4. `IsPointToPoint` дополнительно исключает VPN-туннели (Tailscale на macOS) с IPv4.
- **Обновление списка:** на каждом маячке (раз в 2 с, только пока идёт поиск). Появившийся интерфейс получает join, исчезнувший просто забывается: ядро сняло членство вместе с интерфейсом. Отдельного наблюдателя за сетью нет.
- **Fallback:** если подходящих интерфейсов нет, остаётся прежнее поведение — join и отправка через маршрут по умолчанию.

## Review Focus

1. **Пир с двумя сетевыми картами (Wi-Fi + Ethernet).** Оба адреса маячка — свежие подсказки. Второй, услышанный во время набора первого, не создаёт вторую PeerLink и не теряется: он следующий в том же круге, раньше `last_address` и платы. Тесты в Task 4: `test_a_multi_homed_peer_gets_every_fresh_address_tried_before_the_known_ones`, `test_alternating_beacon_addresses_during_a_dial_create_no_second_link`.
2. **Маячок не обгоняет таймер повтора.** Раньше он обгонял, но плата так не делает, а пользователь потребовал одинаковых правил. После смены IP переподключение ждёт текущую паузу (1, 2, 4, 8, 30 с). Задержка сознательная; её закрепляет обновлённый `test_a_failed_last_known_address_starts_discovery_and_uses_the_new_address` (Task 1).
3. **Адрес маячка устаревает посреди перебора.** Перебор не должен пропускать `last_address`. Тест в Task 4: `test_a_beacon_address_expiring_mid_round_does_not_skip_the_last_good_address`.
4. **Пробуждение Mac.** Адреса маячков, запомненные до сна, не должны первыми набираться после пробуждения. Тест в Task 4: `test_waking_from_sleep_forgets_the_beacon_addresses`.
5. **`setMulticastInterface` на Windows с двумя сетевыми картами.** Проверяется только на реальной машине: строка E6 в runbook (Task 7).

---

### Task 0: Одноразовый spike — multicast Win ↔ Mac в `.app` с Personal Team

Задача ручная, production-код не меняется. Коммитятся только record и поправка к старому record. Probe-файлы остаются в `scratchpad/` (он не в git).

**Files:**
- Create (не коммитится): `scratchpad/discovery-spike/probe.py`, `scratchpad/discovery-spike/build-probe-app.sh`
- Create: `docs/superpowers/records/2026-09-26-macos-multicast-discovery-spike.md`
- Modify: `docs/superpowers/records/2026-09-17-macos-app-packaging.md` (дописать раздел в конец, старый текст не переписывать)

**Interfaces:** ничего не потребляет и ничего не производит для кода. Результат — таблица PASS/FAIL и строки ошибок `writeDatagram`.

- [ ] **Step 1: Создать probe**

`scratchpad/discovery-spike/probe.py` повторяет ровно те Qt-вызовы, что `Discovery` сейчас: bind с `ShareAddress|ReuseAddressHint`, `joinMulticastGroup(group)` без интерфейса, `writeDatagram` раз в 2 с. Режим `--per-interface` предварительно проверяет Task 6. Payload не является `Beacon`, поэтому работающий Duo Input его отбросит. Сам Duo Input на обеих машинах на время spike всё равно нужно закрыть.

```python
"""THROWAWAY spike: проходит ли UDP multicast 239.255.76.67:47655 Win <-> Mac.

Не production-код, не коммитится. Повторяет Qt-вызовы
duo_input.clipboard.discovery.Discovery: bind(AnyIPv4, 47655,
ShareAddress|ReuseAddressHint), joinMulticastGroup(group), writeDatagram(group).
"""

import json
import os
import socket as pysocket
import sys
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtNetwork import QAbstractSocket, QHostAddress, QNetworkInterface, QUdpSocket

GROUP = "239.255.76.67"
PORT = 47655
INTERVAL_MS = 2000
BUILD_VARIANT = "__VARIANT__"  # build-probe-app.sh подставляет вариант сборки

args = [a for a in sys.argv[1:] if not a.startswith("-")]
if args:
    LABEL = args[0]
elif not BUILD_VARIANT.startswith("__"):
    LABEL = f"mac-app-{BUILD_VARIANT}"
else:
    LABEL = pysocket.gethostname()
PER_INTERFACE = "--per-interface" in sys.argv
LOG = Path.home() / "duo-mcast-probe.log"


def log(line: str) -> None:
    text = f"{time.strftime('%H:%M:%S')} [{LABEL}] {line}"
    print(text, flush=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(text + "\n")


def lan_interfaces() -> list[QNetworkInterface]:
    flags = QNetworkInterface.InterfaceFlag
    result = []
    for interface in QNetworkInterface.allInterfaces():
        state = interface.flags()
        if not (state & flags.IsUp and state & flags.IsRunning and state & flags.CanMulticast):
            continue
        if state & flags.IsLoopBack or state & flags.IsPointToPoint:
            continue
        ipv4 = [
            e.ip().toString()
            for e in interface.addressEntries()
            if e.ip().protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol
            and not e.ip().toString().startswith("169.254.")
        ]
        if ipv4:
            result.append(interface)
    return result


app = QCoreApplication(sys.argv)
sock = QUdpSocket()
bound = sock.bind(
    QHostAddress.SpecialAddress.AnyIPv4,
    PORT,
    QUdpSocket.BindFlag.ShareAddress | QUdpSocket.BindFlag.ReuseAddressHint,
)
log(
    f"START pid={os.getpid()} exe={sys.executable} bound={bound} "
    f"err={sock.errorString()!r} mode={'per-interface' if PER_INTERFACE else 'default-route'}"
)
if PER_INTERFACE:
    for interface in lan_interfaces():
        joined = sock.joinMulticastGroup(QHostAddress(GROUP), interface)
        log(f"JOIN {interface.name()} -> {joined}")
else:
    joined = sock.joinMulticastGroup(QHostAddress(GROUP))
    log(f"JOIN default -> {joined} err={sock.errorString()!r}")

seq = 0


def send() -> None:
    global seq
    seq += 1
    payload = json.dumps({"probe": "duo-mcast-spike", "from": LABEL, "seq": seq}).encode()
    targets = lan_interfaces() if PER_INTERFACE else [None]
    for interface in targets:
        if interface is not None:
            sock.setMulticastInterface(interface)
        written = sock.writeDatagram(payload, QHostAddress(GROUP), PORT)
        via = interface.name() if interface is not None else "default"
        if written == -1:
            log(f"SEND seq={seq} via={via} FAIL error={sock.error().name} {sock.errorString()!r}")
        else:
            log(f"SEND seq={seq} via={via} ok bytes={written}")


def read() -> None:
    while sock.hasPendingDatagrams():
        datagram = sock.receiveDatagram()
        raw = bytes(datagram.data())
        try:
            message = json.loads(raw.decode())
        except (UnicodeDecodeError, ValueError):
            message = {"raw": raw[:60].hex()}
        if message.get("from") == LABEL:
            log(f"RECV-SELF seq={message.get('seq')}")
            continue
        log(f"RECV from={datagram.senderAddress().toString()} payload={message}")


sock.readyRead.connect(read)
timer = QTimer()
timer.timeout.connect(send)
timer.start(INTERVAL_MS)
send()
QTimer.singleShot(int(os.environ.get("PROBE_SECONDS", "180")) * 1000, app.quit)
app.exec()
```

- [ ] **Step 2: Создать сборщик `.app`**

`scratchpad/discovery-spike/build-probe-app.sh` использует ту же подпись, что `packaging/nuitka-build-macos.sh`: Personal Team, hardened runtime, пустые entitlements и **без** multicast-entitlement. Аргументы: вариант (`with-usage` или `no-usage`) и суффикс bundle id. Новый суффикс даёт новое TCC-состояние «не определено»: на macOS разрешение нельзя сбросить (TN3179). Разный текст исходника даёт разный UUID главного бинаря, как требует TN3179.

```bash
#!/usr/bin/env bash
# THROWAWAY: probe.py -> .app, Personal Team + hardened runtime, БЕЗ multicast-entitlement.
set -euo pipefail
VARIANT="$1"   # with-usage | no-usage
SUFFIX="$2"    # a1, a2, b1 ... - новый суффикс = новое состояние Local Network
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$HERE/../../configurator/.venv-build/bin/python"
BUNDLE_ID="com.duoinput.spike.mcast.$SUFFIX"
WORK="$HERE/build-$VARIANT-$SUFFIX"
rm -rf "$WORK"; mkdir -p "$WORK"
sed "s/__VARIANT__/$VARIANT-$SUFFIX/" "$HERE/probe.py" > "$WORK/probe.py"
PLIST_ARGS=()
if [ "$VARIANT" = "with-usage" ]; then
    PLIST_ARGS+=("--macos-app-protected-resource=NSLocalNetworkUsageDescription:Duo multicast spike probes the local network.")
fi
( cd "$WORK" && "$PY" -m nuitka --standalone --macos-create-app-bundle --assume-yes-for-downloads \
    --enable-plugin=pyside6 --macos-app-name=DuoMcastProbe --macos-signed-app-name="$BUNDLE_ID" \
    --output-dir=dist ${PLIST_ARGS[@]+"${PLIST_ARGS[@]}"} probe.py )
APP="$(ls -d "$WORK"/dist/*.app | head -1)"
EXE="$APP/Contents/MacOS/$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist")"
IDENTITY="$(security find-identity -v -p codesigning | sed -n '1s/^[[:space:]]*[0-9]*)[[:space:]]*\([0-9A-F]*\).*/\1/p')"
ENT="$WORK/empty.entitlements"
printf '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict/></plist>\n' > "$ENT"
find "$APP/Contents" -type f | while IFS= read -r f; do
    [ "$f" = "$EXE" ] && continue
    if file "$f" | grep -q "Mach-O"; then
        codesign --force --sign "$IDENTITY" --options runtime --timestamp=none "$f"
    fi
done
codesign --force --sign "$IDENTITY" --options runtime --entitlements "$ENT" --identifier "$BUNDLE_ID" "$APP"
codesign --verify --strict "$APP"
codesign -dv --entitlements - "$APP" 2>&1 | grep -E "^Identifier|TeamIdentifier|flags=|multicast" || true
/usr/libexec/PlistBuddy -c "Print :NSLocalNetworkUsageDescription" "$APP/Contents/Info.plist" \
    || echo "NSLocalNetworkUsageDescription: ОТСУТСТВУЕТ"
dwarfdump --uuid "$EXE"
echo "APP=$APP"
```

Запуск: `bash scratchpad/discovery-spike/build-probe-app.sh with-usage a1`. Ожидается:
- `TeamIdentifier=4YKVN22BMX`;
- `flags=0x10000(runtime)`;
- в выводе нет `multicast`;
- ключ `NSLocalNetworkUsageDescription` напечатан;
- приложение открывается двойным кликом.

- [ ] **Step 3: Подготовить Windows**

1. Скопировать `probe.py` на Windows-ПК.
2. В PowerShell выполнить `Get-NetConnectionProfile`. `NetworkCategory` должен быть `Private`: профиль Public режет входящие.
3. Запустить `configurator\.venv\Scripts\python.exe probe.py WIN`.
4. Если Windows покажет запрос брандмауэра для `python.exe`, разрешить для частных сетей и записать это в record.

- [ ] **Step 4: Прогнать матрицу**

Windows-probe всё время работает параллельно. Каждая строка длится не меньше 60 с. После каждой строки сохранить `~/duo-mcast-probe.log` (Mac) и `%USERPROFILE%\duo-mcast-probe.log` (Windows).

| # | Запуск на Mac | Local Network | Что зафиксировать |
|---|---|---|---|
| S1 | Terminal: `.venv-mac/bin/python scratchpad/discovery-spike/probe.py MAC-TERM` (контроль; Terminal освобождён от LNP) | — | Win→Mac RECV? Mac→Win RECV? Если FAIL, проблема в сети, а не в .app |
| S2 | `with-usage a1`, двойной клик в Finder | первый запуск → **Allow** | появился ли запрос; SEND до и после ответа в **том же** процессе; RECV в обе стороны; затем Quit, повторный запуск и снова RECV в обе стороны |
| S3 | `a1` после выключения в System Settings → Конфиденциальность и безопасность → Локальная сеть, затем перезапуск | Denied | точная строка `SEND … FAIL error=… '…'`; RECV в обе стороны |
| S4 | `with-usage a2`, Finder | первый запуск → **Don't Allow** | то же, что в S3 |
| S5 | `no-usage b1`, Finder | первый запуск | показан ли запрос вообще; SEND/RECV |
| S6 | `a1` (Allowed), главный бинарь запущен из Terminal: `…/DuoMcastProbe.app/Contents/MacOS/<exe>` | — | справочно: работает ли под ответственностью Terminal |
| S7 | Terminal, `--per-interface`; на Mac одновременно Wi-Fi и Ethernet (или поднят VPN) | — | на каких интерфейсах JOIN и SEND; RECV на Windows |

- [ ] **Step 5: Записать record**

Создать `docs/superpowers/records/2026-09-26-macos-multicast-discovery-spike.md` со следующим содержимым:
- дата, версии macOS и Windows, сеть (Wi-Fi/Ethernet, модель роутера);
- вывод `codesign -dv` из шага 2;
- таблица S1–S7 с колонками «Запрос LNP», «SEND Mac», «Win→Mac», «Mac→Win», «Ошибка writeDatagram», «Примечания»;
- дословные строки ошибок из журналов;
- вывод (шаг 6).

В конец `2026-09-17-macos-app-packaging.md` добавить раздел:

```markdown
## Поправка 2026-09-26: multicast-entitlement на macOS не нужен

Опыт выше (ad-hoc + `com.apple.developer.networking.multicast` → error 153)
верен, но вывод «мультикаст-discovery в .app без платного аккаунта недостижим»
ошибочен. Apple TN3179: «The multicast entitlement isn't required on macOS» —
entitlement нужен только iOS/iPadOS/visionOS. На macOS multicast — обычная
local-network операция под Local Network privacy; достаточно
`NSLocalNetworkUsageDescription` и разрешения пользователя. Подтверждено
spike'ом: `docs/superpowers/records/2026-09-26-macos-multicast-discovery-spike.md`.
```

- [ ] **Step 6: Gate и коммит**

- **PASS** — только если **S1 PASS и S2 PASS после Local Network Allow**: у `.app`, подписанного Personal Team, без multicast-entitlement, есть RECV в обе стороны (Win→Mac и Mac→Win). Результат записать в record.
- **Сеть:** если S1 FAIL, дело в сети (изоляция клиентов или фильтр multicast у AP). Сменить сеть или AP и повторить S1–S2. Делать вывод о `.app` в такой сети нельзя.
- **STOP:** если S1 PASS, а S2 FAIL при Allowed, никакой реализации Tasks 1+ до нового анализа.
- **В любом случае** после Task 0 остановиться. Task 1 начинается только после явного подтверждения пользователя.

```bash
git add docs/superpowers/records/2026-09-26-macos-multicast-discovery-spike.md docs/superpowers/records/2026-09-17-macos-app-packaging.md
git commit -m "docs(records): multicast discovery spike Win<->Mac; the macOS entitlement is not required"
```

---

### Task 1: Маячок подчиняется тому же правилу звонка, что и адреса от платы

**Files:**
- Modify: `configurator/src/duo_input/clipboard/coordinator.py` (`set_board_addresses` ~303–335, `_on_peer_seen` ~486–502, новый метод `_may_dial_on_hint`)
- Test: `configurator/tests/clipboard/test_coordinator.py`

**Interfaces:**
- Produces: `ClipboardCoordinator._may_dial_on_hint() -> bool`. Возвращает True только при всех условиях сразу: пир есть; мы звонящая сторона (`peer.origin_id >= own`); `state in {DISCONNECTED, SEARCHING}`; нет `_link`, `_dialing`, `_pairing`; `_retry` не активен.
- Produces (тестовый хелпер): `_peer_beacon(origin_id: str = LARGEST_ORIGIN_ID, fingerprint: str = "f" * 64) -> Beacon`.

- [ ] **Step 1: Написать падающие тесты**

Добавить после `test_pairing_blocks_a_new_board_address_dial` новый раздел:

```python
# ---------------------------------------------------------------------- маячок доверенного пира


def _peer_beacon(origin_id: str = LARGEST_ORIGIN_ID, fingerprint: str = "f" * 64):
    return coordinator_module.Beacon(
        origin_id=origin_id,
        machine_name="LAPTOP-TWO",
        fingerprint=fingerprint,
        port=TCP_PORT,
        protocol_major=PROTOCOL_MAJOR,
    )


def test_a_beacon_during_a_dial_in_flight_does_not_start_a_second_link(tmp_path, dial, qapp):
    """Маячок приходит каждые 2 с, а набор длится до 10 с: без проверки
    `_dialing` каждый маячок запускал бы ещё одну PeerLink поверх идущей."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator._try_connect()
    assert len(dial) == 1
    assert coordinator._dialing is True

    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")

    try:
        assert len(dial) == 1
        assert coordinator._dialing is True
    finally:
        coordinator.stop()


def test_a_stream_of_beacons_creates_at_most_one_link(tmp_path, dial, qapp):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)

    for _ in range(5):
        coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")

    try:
        assert len(dial) == 1
    finally:
        coordinator.stop()


def test_a_beacon_does_not_dial_while_connected(tmp_path, dial, qapp):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator._on_connected(_FakeLink("f" * 64, peer_address="192.168.1.5"))
    assert coordinator.state is LinkState.CONNECTED

    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")

    try:
        assert dial == []
        assert coordinator.state is LinkState.CONNECTED
    finally:
        coordinator.stop()


def test_a_beacon_does_not_dial_while_blocked(tmp_path, dial, monkeypatch, qapp):
    """Занятый порт слушателя маячок не чинит - BLOCKED остаётся BLOCKED."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    monkeypatch.setattr(coordinator._listener, "listen", lambda port: False)
    coordinator.start()
    assert coordinator.state is LinkState.BLOCKED

    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")

    try:
        assert dial == []
        assert coordinator.state is LinkState.BLOCKED
    finally:
        coordinator.stop()


def test_a_beacon_does_not_redial_after_protocol_mismatch(tmp_path, dial, qapp):
    """Несовместимая версия не лечится переподключением (§11)."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator._on_connected(_FakeLink())
    coordinator._on_message(
        Message(MessageType.HELLO, {"protocol_major": PROTOCOL_MAJOR + 1}, b"")
    )
    assert coordinator.state is LinkState.PROTOCOL_MISMATCH

    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")

    try:
        assert dial == []
        assert coordinator.state is LinkState.PROTOCOL_MISMATCH
    finally:
        coordinator.stop()


def test_the_waiting_side_does_not_react_to_a_beacon(tmp_path, dial, qapp):
    """Ждущая сторона не звонит и не перезапускает сторож молчания на каждый маячок."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=SMALLEST_ORIGIN_ID)

    coordinator._on_peer_seen(_peer_beacon(origin_id=SMALLEST_ORIGIN_ID), "192.168.1.99")

    try:
        assert dial == []
        assert coordinator._silence.isActive() is False
    finally:
        coordinator.stop()


def test_an_active_retry_timer_defers_a_beacon_dial(tmp_path, dial, qapp):
    """То же правило, что у платы: тикающий повтор не обгоняется."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    fake_retry = _FakeTimer()
    coordinator._retry = fake_retry
    fake_retry.start(reconnect_delay_ms(0))

    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")

    assert dial == []
```

- [ ] **Step 2: Убедиться, что тесты RED**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/test_coordinator.py -q -p no:cacheprovider -k "beacon"`

Expected: FAIL семи новых тестов. Сейчас `_on_peer_seen` сбрасывает `_dialing` и всегда вызывает `_try_connect()`: `len(dial)` равно 2 или 5, в CONNECTED/BLOCKED/MISMATCH `dial != []`, у ждущей стороны `_silence` активен.

- [ ] **Step 3: Минимальная реализация**

В `coordinator.py` добавить метод перед `set_board_addresses`:

```python
    def _may_dial_on_hint(self) -> bool:
        """Можно ли прямо сейчас звонить из-за новой подсказки адреса.

        Одно правило для обоих источников подсказок - платы и маячка. Звонит
        только звонящая сторона (peer.origin_id не меньше нашего), только из
        DISCONNECTED/SEARCHING и только в простое: ни живой связи, ни набора,
        ни связывания, ни тикающего таймера повтора. BLOCKED (порт слушателя
        занят) и PROTOCOL_MISMATCH (пир несовместим) новый адрес не чинит, а
        набор поверх идущего набора плодил бы параллельные PeerLink.
        """
        peer = self.peer
        if peer is None or peer.origin_id < self._identity.origin_id:
            return False
        if self._state not in (LinkState.DISCONNECTED, LinkState.SEARCHING):
            return False
        if self._link is not None or self._dialing or self._pairing:
            return False
        return not self._retry.isActive()
```

Хвост `set_board_addresses`, начиная с `filtered = …`, заменить (docstring оставить как есть):

```python
        filtered = [address for address in addresses if address]
        changed = filtered != self._board_addresses
        self._board_addresses = filtered
        if not changed or not self._may_dial_on_hint():
            return
        self._candidate_index = 0
        self._try_connect()
```

В `_on_peer_seen` ветку `if not self._pairing:` заменить так:

```python
        if not self._pairing:
            peer = self.peer
            if peer is None or beacon.origin_id != peer.origin_id:
                return
            if peer.last_address != address:
                self._trust.update_address(address)
            # Маячок - подсказка адреса, как и список от платы, и звонит по
            # тому же правилу: не поверх живой связи, идущего набора или
            # тикающего повтора и не из BLOCKED/PROTOCOL_MISMATCH.
            if not self._may_dial_on_hint():
                return
            self._candidate_index = 0
            self._try_connect()
            return
```

(`update_address` здесь временно остаётся, его убирает Task 4.)

- [ ] **Step 4: Обновить два существующих теста, закреплявших старое поведение**

В `test_a_failed_last_known_address_starts_discovery_and_uses_the_new_address` блок после `coordinator._on_peer_seen(...)` заменить. Вызов `_on_peer_seen` не меняется.

```python
        # После неудачи тикает таймер повтора - маячок его не обгоняет
        # (то же правило, что у платы), а лишь даёт адрес следующему набору.
        assert calls == [("192.168.1.5", TCP_PORT, "f" * 64)]

        coordinator._retry.stop()
        coordinator._try_connect()  # то, что сделал бы сработавший _retry

        assert calls == [
            ("192.168.1.5", TCP_PORT, "f" * 64),
            ("192.168.1.99", TCP_PORT, "f" * 64),
        ]
```

`test_on_peer_seen_restarts_the_dial_from_the_fresh_address` заменить целиком. Раньше он полагался на то, что маячок ломает идущий набор от платы.

```python
def test_on_peer_seen_restarts_the_dial_from_the_fresh_address(tmp_path, dial, qapp):
    """Маячок от доверенного пира в простое - набор начинается с его адреса,
    а не с середины прежнего списка, где мог застрять неудачный набор."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator._board_addresses = ["10.0.0.2", "10.0.0.3"]  # список без автонабора
    coordinator._candidate_index = 2

    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")

    try:
        assert dial[-1].address == "192.168.1.99"
    finally:
        coordinator.stop()
```

- [ ] **Step 5: GREEN и регрессия**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard -q -p no:cacheprovider`

Expected: всё PASS, в том числе восемь тестов правила платы (`test_a_new_board_address_list_*`, `test_an_active_retry_timer_blocks_*`, `test_a_dial_already_in_flight_*`, `test_pairing_blocks_*`) без правок.

- [ ] **Step 6: Проверка мутациями**

Удалять по одному условию в `_may_dial_on_hint` — origin, state, `_link`, `_dialing`, `_pairing`, `_retry` — и запускать `-k "beacon or board"`. Каждое удаление должно ронять хотя бы один тест. После проверки вернуть условие.

- [ ] **Step 7: Commit**

Готово, когда: новые тесты зелёные, весь `tests/clipboard` зелёный, мутации пойманы.

```bash
git add configurator/src/duo_input/clipboard/coordinator.py configurator/tests/clipboard/test_coordinator.py
git commit -m "fix(clipboard): a beacon obeys the same dial guard as board addresses"
```

---

### Task 2: Маячок с чужим отпечатком игнорируется

Отпечаток в маячке публичен, поэтому это **не аутентификация**. Её по-прежнему даёт TLS с закреплённым отпечатком (`connect_to(..., peer.fingerprint)`). Фильтр отсекает маячки прежней установки пира и случайные совпадения `origin_id`, чтобы они даже не становились подсказкой.

**Files:**
- Modify: `configurator/src/duo_input/clipboard/coordinator.py` (`_on_peer_seen`)
- Test: `configurator/tests/clipboard/test_coordinator.py`

**Interfaces:** Consumes `_peer_beacon(..., fingerprint=...)` из Task 1.

- [ ] **Step 1: Написать падающий тест**

```python
def test_a_beacon_with_a_foreign_fingerprint_is_ignored(tmp_path, dial, qapp):
    """Совпал origin_id, но не отпечаток - это не наш пир (или его прежняя
    установка): ни звонка, ни адреса."""
    coordinator, trust = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)

    coordinator._on_peer_seen(_peer_beacon(fingerprint="e" * 64), "192.168.1.66")

    try:
        assert dial == []
        assert trust.peer().last_address == "192.168.1.5"
    finally:
        coordinator.stop()
```

- [ ] **Step 2: RED**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/test_coordinator.py -q -p no:cacheprovider -k foreign_fingerprint`

Expected: FAIL, потому что `dial` содержит звонок на `192.168.1.66`, а `last_address == "192.168.1.66"`.

- [ ] **Step 3: Минимальная реализация**

В `_on_peer_seen` условие `if peer is None or beacon.origin_id != peer.origin_id:` заменить:

```python
            # Отпечаток в маячке публичен, так что это фильтр, а не
            # аутентификация (её даёт TLS с закреплённым отпечатком): маячок
            # прежней установки пира не должен становиться подсказкой вовсе.
            if (
                peer is None
                or beacon.origin_id != peer.origin_id
                or beacon.fingerprint != peer.fingerprint
            ):
                return
```

- [ ] **Step 4: GREEN и регрессия**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard -q -p no:cacheprovider`. Expected: всё PASS.

Мутация: удалить `or beacon.fingerprint != peer.fingerprint`, после чего новый тест должен упасть.

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/clipboard/coordinator.py configurator/tests/clipboard/test_coordinator.py
git commit -m "fix(clipboard): ignore a beacon whose fingerprint is not the trusted peer's"
```

---

### Task 3: Перебор кандидатов опирается на набранный адрес, а не на индекс

Это давний дефект, не связанный с маячком. Если список кандидатов меняется, пока идёт набор (плата прислала другой список), `_drop` сдвигает `_candidate_index` по индексу старого списка и пропускает кандидата. Task 4 добавит в список ещё один изменчивый элемент, поэтому исправление идёт до него.

**Files:**
- Modify: `configurator/src/duo_input/clipboard/coordinator.py` (`__init__`, `_try_connect`, `_drop`)
- Test: `configurator/tests/clipboard/test_coordinator.py`

**Interfaces:**
- Produces: поле `ClipboardCoordinator._dialing_address: str` — адрес текущего набора. Его выставляет `_try_connect`, читает `_drop`.

- [ ] **Step 1: Написать падающий тест**

Добавить в раздел «адреса от платы»:

```python
def test_a_candidate_that_vanished_mid_dial_does_not_make_the_round_skip_the_next(tmp_path, dial, qapp):
    """Пока шёл набор 10.0.0.2, плата прислала список уже без него. Следующим
    должен быть 10.0.0.3 - тот, кто занял его место, - а не конец круга с
    паузой, как было бы при сдвиге индекса по старому списку."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    timer = _FakeTimer()
    coordinator._retry = timer
    coordinator._board_addresses = ["10.0.0.2", "10.0.0.3"]
    coordinator._candidate_index = 1
    coordinator._try_connect()
    assert dial[-1].address == "10.0.0.2"

    coordinator.set_board_addresses(["10.0.0.3"])  # набор идёт - только запомнить
    dial[-1].disconnected.emit("refused")

    try:
        assert timer.starts == [0]  # следующий кандидат сразу, без паузы
        coordinator._try_connect()
        assert dial[-1].address == "10.0.0.3"
    finally:
        coordinator.stop()
```

- [ ] **Step 2: RED**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/test_coordinator.py -q -p no:cacheprovider -k vanished_mid_dial`

Expected: FAIL: `timer.starts == [1000]`. Круг закончился, потому что `1 + 1 < len(["192.168.1.5", "10.0.0.3"])` ложно.

- [ ] **Step 3: Минимальная реализация**

В `__init__` рядом с `self._dialing = False`:

```python
        self._dialing_address = ""
```

В `_try_connect` перед `self._dialing = True`:

```python
        self._dialing_address = address
```

В `_drop` блок `if self._dialing:` заменить:

```python
        if self._dialing:
            self._dialing = False
            candidates = self._candidates()
            # Список мог измениться, пока шёл набор (плата прислала другой,
            # адрес маячка устарел или появился). Следующим идёт тот, что
            # стоит сразу за набранным СЕЙЧАС; если набранного в списке уже
            # нет, его место занял следующий кандидат - он и идёт дальше.
            if self._dialing_address in candidates:
                next_index = candidates.index(self._dialing_address) + 1
            else:
                next_index = self._candidate_index
            if next_index < len(candidates):
                # Этот адрес не ответил - следующий пробуем сразу: пауза
                # переподключения нужна после неудачи всего списка, а не
                # каждого адреса в нём.
                self._candidate_index = next_index
                self._set_state(LinkState.DISCONNECTED)
                self._retry.start(0)
                return
```

- [ ] **Step 4: GREEN и регрессия**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard -q -p no:cacheprovider`

Expected: всё PASS, особенно `test_the_last_good_address_is_tried_first_then_the_boards`, `test_the_next_candidate_is_tried_without_the_backoff`, `test_the_backoff_starts_only_after_the_whole_list_failed`, `test_the_candidate_index_wraps_when_the_list_shrinks`.

Мутация: заменить ветку `if self._dialing_address in candidates` на прежнее `next_index = self._candidate_index + 1`, после чего новый тест должен упасть.

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/clipboard/coordinator.py configurator/tests/clipboard/test_coordinator.py
git commit -m "fix(clipboard): advance the dial round from the dialled address, not a stale index"
```

---

### Task 4: Адреса из маячка — только в памяти, с TTL, раньше известных адресов

**Files:**
- Modify: `configurator/src/duo_input/clipboard/coordinator.py`: импорты, две константы политики, `__init__` (параметр `clock`), `_candidates`, `_on_peer_seen`, `_on_connected`, `stop`, `forget_peer`, `recover_after_resume`; новые `_fresh_discovered_addresses`, `_remember_discovered_address`, `_forget_discovered_addresses`
- Test: `configurator/tests/clipboard/test_coordinator.py` (`_make_coordinator` получает `clock`)

**Interfaces:**
- Consumes: `_may_dial_on_hint()` (Task 1), `_dialing_address` (Task 3), `BEACON_INTERVAL_MS` из `discovery.py`.
- Produces:
  - `DISCOVERED_ADDRESS_TTL_MS: int = 5 * BEACON_INTERVAL_MS` и `MAX_DISCOVERED_ADDRESSES: int = 8` — константы **политики**, обе в `__all__`;
  - `ClipboardCoordinator.__init__(identity, trust, machine_name, service=None, parent=None, clock: Callable[[], float] | None = None)`, где `clock` возвращает монотонные миллисекунды;
  - поле `_discovered_addresses: dict[str, float]` (адрес → момент протухания, мс);
  - `_fresh_discovered_addresses() -> list[str]` (свежие, в порядке первого появления);
  - `_remember_discovered_address(address: str) -> bool` (True, если адрес новый; уже известный только продлевается);
  - `_forget_discovered_addresses() -> None`;
  - порядок `_candidates()`: `[manual]`, иначе все свежие адреса маячка → `last_address` → адреса от платы, без повторов, с сохранением порядка.

- [ ] **Step 1: Подготовить тестовые часы**

В `test_coordinator.py` после `_FakeTimer`:

```python
class _Clock:
    """Монотонные миллисекунды, которые тест двигает сам."""

    def __init__(self) -> None:
        self.now_ms = 1_000.0

    def __call__(self) -> float:
        return self.now_ms
```

`_make_coordinator` получает `clock=None` и передаёт его в конструктор:

```python
def _make_coordinator(
    tmp_path, peer_origin_id: str | None = None, clock=None
) -> tuple[ClipboardCoordinator, TrustStore]:
    identity = load_or_create(tmp_path / "id")
    trust = TrustStore(tmp_path / "peers.json")
    if peer_origin_id is not None:
        trust.remember(TrustedPeer(peer_origin_id, "LAPTOP-TWO", "f" * 64, "192.168.1.5"))
    coordinator = ClipboardCoordinator(
        identity=identity, trust=trust, machine_name="LAPTOP-ONE", clock=clock
    )
    return coordinator, trust
```

В импорт из `duo_input.clipboard.coordinator` добавить `DISCOVERED_ADDRESS_TTL_MS` и `MAX_DISCOVERED_ADDRESSES`.

- [ ] **Step 2: Написать падающие тесты**

Новый раздел после тестов Task 1–2:

```python
# ---------------------------------------------------------------------- адреса маячка: память и TTL


def _deferred(coordinator) -> None:
    """Таймер повтора тикает - маячок только запоминается, не звонит."""
    fake_retry = _FakeTimer()
    coordinator._retry = fake_retry
    fake_retry.start(reconnect_delay_ms(0))


def test_a_beacon_address_is_never_written_to_the_trust_store(tmp_path, dial, qapp):
    """Маячок - подсказка, а не доверие: на диск он не попадает."""
    coordinator, trust = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID, clock=_Clock())

    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")

    try:
        assert trust.peer().last_address == "192.168.1.5"
        assert TrustStore(tmp_path / "peers.json").peer().last_address == "192.168.1.5"
    finally:
        coordinator.stop()


def test_fresh_beacon_addresses_come_before_the_last_good_and_the_boards(tmp_path, dial, qapp):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID, clock=_Clock())
    coordinator._board_addresses = ["10.0.0.2"]
    _deferred(coordinator)

    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")

    assert coordinator._candidates() == ["192.168.1.99", "192.168.1.5", "10.0.0.2"]


def test_a_beacon_address_expires_after_the_ttl(tmp_path, dial, qapp):
    clock = _Clock()
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID, clock=clock)
    coordinator._board_addresses = ["10.0.0.2"]
    _deferred(coordinator)
    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")

    clock.now_ms += DISCOVERED_ADDRESS_TTL_MS

    assert coordinator._candidates() == ["192.168.1.5", "10.0.0.2"]


def test_a_new_address_of_the_peer_does_not_evict_its_other_fresh_addresses(tmp_path, dial, qapp):
    """DHCP выдал новый адрес или у пира поднялся второй интерфейс - прежний
    свежий адрес остаётся кандидатом, пока сам не протухнет."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID, clock=_Clock())
    _deferred(coordinator)

    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.20")
    coordinator._on_peer_seen(_peer_beacon(), "10.0.0.20")

    assert coordinator._candidates() == ["192.168.1.20", "10.0.0.20", "192.168.1.5"]


def test_one_discovered_address_expires_while_the_other_stays_fresh(tmp_path, dial, qapp):
    clock = _Clock()
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID, clock=clock)
    _deferred(coordinator)
    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.20")
    clock.now_ms += 6_000
    coordinator._on_peer_seen(_peer_beacon(), "10.0.0.20")

    clock.now_ms += DISCOVERED_ADDRESS_TTL_MS - 6_000  # первый ровно протух

    assert coordinator._candidates() == ["10.0.0.20", "192.168.1.5"]


def test_a_duplicate_beacon_only_refreshes_its_own_address(tmp_path, dial, qapp):
    """Повторный маячок продлевает срок именно этого адреса, не двигает его в
    очереди и не трогает срок других."""
    clock = _Clock()
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID, clock=clock)
    _deferred(coordinator)
    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.20")
    coordinator._on_peer_seen(_peer_beacon(), "10.0.0.20")
    clock.now_ms += DISCOVERED_ADDRESS_TTL_MS - 1

    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.20")
    assert coordinator._candidates() == ["192.168.1.20", "10.0.0.20", "192.168.1.5"]

    clock.now_ms += 2
    assert coordinator._candidates() == ["192.168.1.20", "192.168.1.5"]


def test_a_flood_of_beacon_addresses_is_capped(tmp_path, dial, qapp):
    """Поток поддельных маячков с разными IP не раздувает перебор: сверх
    предела вытесняется адрес, который протухнет раньше всех."""
    clock = _Clock()
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID, clock=clock)
    _deferred(coordinator)

    for n in range(MAX_DISCOVERED_ADDRESSES + 1):
        coordinator._on_peer_seen(_peer_beacon(), f"10.1.0.{n}")
        clock.now_ms += 1

    fresh = coordinator._fresh_discovered_addresses()
    assert len(fresh) == MAX_DISCOVERED_ADDRESSES
    assert "10.1.0.0" not in fresh
    assert fresh[-1] == f"10.1.0.{MAX_DISCOVERED_ADDRESSES}"


def test_a_manual_address_stays_the_only_candidate_despite_beacons(tmp_path, dial, qapp):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID, clock=_Clock())
    coordinator.restore_manual_address("192.168.7.7")
    _deferred(coordinator)

    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")
    coordinator._on_peer_seen(_peer_beacon(), "10.0.0.20")

    assert coordinator._candidates() == ["192.168.7.7"]


def test_the_same_beacon_address_again_does_not_redial(tmp_path, dial, qapp):
    """Как у платы: звонок - только на новую подсказку."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID, clock=_Clock())
    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")
    assert len(dial) == 1
    coordinator._dialing = False  # как будто первая попытка уже разрешилась

    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")

    try:
        assert len(dial) == 1
    finally:
        coordinator.stop()


def test_a_connection_through_a_beacon_address_persists_it_and_clears_the_cache(tmp_path, dial, qapp):
    """На диск попадает адрес, по которому прошло TLS с закреплённым отпечатком."""
    coordinator, trust = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID, clock=_Clock())
    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")
    coordinator._on_peer_seen(_peer_beacon(), "10.0.0.20")
    assert dial[-1].address == "192.168.1.99"

    dial[-1].connected.emit("f" * 64)

    try:
        assert trust.peer().last_address == "192.168.1.99"
        assert coordinator._fresh_discovered_addresses() == []
    finally:
        coordinator.stop()


def test_a_spoofed_beacon_address_costs_one_attempt_and_is_never_persisted(tmp_path, dial, qapp):
    """Чужой узел повторил публичные origin_id и отпечаток пира со своим адресом."""
    coordinator, trust = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID, clock=_Clock())
    coordinator._retry = _FakeTimer()
    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.66")
    assert dial[-1].address == "192.168.1.66"
    assert trust.peer().last_address == "192.168.1.5"

    dial[-1].disconnected.emit("отпечаток не совпал")  # TLS-закрепление отвергло
    coordinator._try_connect()  # то, что сделал бы _retry.start(0)
    assert dial[-1].address == "192.168.1.5"
    dial[-1].connected.emit("f" * 64)

    try:
        assert trust.peer().last_address == "192.168.1.5"
        assert coordinator.state is LinkState.CONNECTED
    finally:
        coordinator.stop()


def test_a_stale_beacon_address_is_not_dialled(tmp_path, dial, qapp):
    clock = _Clock()
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID, clock=clock)
    _deferred(coordinator)
    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")
    clock.now_ms += DISCOVERED_ADDRESS_TTL_MS
    coordinator._retry.stop()

    coordinator._try_connect()

    try:
        assert dial[-1].address == "192.168.1.5"
    finally:
        coordinator.stop()


def test_a_multi_homed_peer_gets_every_fresh_address_tried_before_the_known_ones(tmp_path, dial, qapp):
    """Пир на Wi-Fi и Ethernet. Второй адрес, услышанный во время набора
    первого, не создаёт вторую PeerLink, но и не теряется: он следующий в том
    же круге - раньше last_address и адресов платы."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID, clock=_Clock())
    coordinator._retry = _FakeTimer()
    coordinator._board_addresses = ["10.0.0.2"]

    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.20")
    assert [link.address for link in dial] == ["192.168.1.20"]
    coordinator._on_peer_seen(_peer_beacon(), "10.0.0.20")  # набор идёт - только запомнить
    assert len(dial) == 1

    for _ in range(3):
        dial[-1].disconnected.emit("refused")
        coordinator._try_connect()  # то, что сделал бы _retry.start(0)

    try:
        assert [link.address for link in dial] == [
            "192.168.1.20",
            "10.0.0.20",
            "192.168.1.5",
            "10.0.0.2",
        ]
    finally:
        coordinator.stop()


def test_a_beacon_heard_during_a_board_dial_is_tried_first_on_the_next_round(tmp_path, dial, qapp):
    """Плата и маячок одновременно: одна PeerLink за раз, ни один адрес не
    набирается дважды за круг, адрес маячка - первым в следующем круге."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID, clock=_Clock())
    timer = _FakeTimer()
    coordinator._retry = timer
    coordinator.set_board_addresses(["10.0.0.2"])  # звонит 192.168.1.5 сразу
    assert dial[-1].address == "192.168.1.5"

    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")
    assert len(dial) == 1

    dial[-1].disconnected.emit("refused")
    coordinator._try_connect()
    assert dial[-1].address == "10.0.0.2"
    dial[-1].disconnected.emit("refused")
    assert timer.starts[-1] == reconnect_delay_ms(0)  # круг закончен

    coordinator._try_connect()
    try:
        assert dial[-1].address == "192.168.1.99"
    finally:
        coordinator.stop()


def test_alternating_beacon_addresses_during_a_dial_create_no_second_link(tmp_path, dial, qapp):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID, clock=_Clock())

    for address in ["192.168.1.20", "10.0.0.20"] * 3:
        coordinator._on_peer_seen(_peer_beacon(), address)

    try:
        assert len(dial) == 1
    finally:
        coordinator.stop()


def test_a_beacon_address_expiring_mid_round_does_not_skip_the_last_good_address(tmp_path, dial, qapp):
    clock = _Clock()
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID, clock=clock)
    coordinator._retry = _FakeTimer()
    coordinator._board_addresses = ["10.0.0.2"]
    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")
    assert dial[-1].address == "192.168.1.99"

    clock.now_ms += DISCOVERED_ADDRESS_TTL_MS
    dial[-1].disconnected.emit("время подключения истекло")
    coordinator._try_connect()

    try:
        assert dial[-1].address == "192.168.1.5"
    finally:
        coordinator.stop()


def test_waking_from_sleep_forgets_the_beacon_addresses(tmp_path, dial, qapp):
    """time.monotonic на macOS не считает сон: без сброса адреса из маячков,
    услышанных до сна, выглядели бы свежими и набирались первыми."""
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID, clock=_Clock())
    _deferred(coordinator)
    coordinator._on_peer_seen(_peer_beacon(), "192.168.1.99")
    coordinator._on_peer_seen(_peer_beacon(), "10.0.0.20")

    coordinator.recover_after_resume()

    try:
        assert coordinator._candidates()[0] == "192.168.1.5"
        assert coordinator._fresh_discovered_addresses() == []
    finally:
        coordinator.stop()
```

- [ ] **Step 3: RED**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/test_coordinator.py -q -p no:cacheprovider`

Expected: сбор падает с `ImportError: cannot import name 'DISCOVERED_ADDRESS_TTL_MS'`, после добавления констант — `TypeError: unexpected keyword argument 'clock'`. Когда оба есть, тесты раздела всё ещё FAIL: маячок пишет `last_address`, а у `_candidates()` нет адресов маячка.

- [ ] **Step 4: Минимальная реализация**

Импорты в `coordinator.py`:

```python
import logging
import time
from collections.abc import Callable
from enum import StrEnum

from PySide6.QtCore import QObject, QTimer, Signal

from .discovery import BEACON_INTERVAL_MS, Beacon, Discovery
```

После `RECONNECT_DELAYS_MS`:

```python
#: Сколько живёт адрес из маячка без подтверждения следующим маячком.
#: Политика, а не протокол: маячок о ней не знает, значение можно менять без
#: вопросов совместимости. Пять интервалов маячка: multicast по Wi-Fi теряется,
#: и несколько пропущенных маячков подряд не должны выбрасывать живой адрес;
#: каждый новый маячок продлевает срок своего адреса.
DISCOVERED_ADDRESS_TTL_MS = 5 * BEACON_INTERVAL_MS

#: Тоже политика: сколько адресов маячка держать одновременно. Настоящий пир
#: рекламируется не более чем через 8 интерфейсов (как MAX_HOST_ADDRESSES у
#: обмена через плату); поток поддельных маячков с разными IP не должен
#: раздувать перебор кандидатов.
MAX_DISCOVERED_ADDRESSES = 8


def _monotonic_ms() -> float:
    return time.monotonic() * 1000.0
```

Сигнатура `__init__`:

```python
    def __init__(
        self,
        identity: NodeIdentity,
        trust: TrustStore,
        machine_name: str,
        service: ClipboardService | None = None,
        parent: QObject | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
```

После `self._board_addresses: list[str] = []`:

```python
        # Адреса из маячков доверенного пира - подсказки, а не доверие: только
        # в памяти, каждый до своего срока (адрес -> момент протухания, мс).
        # Адресов может быть несколько: пир с Wi-Fi и Ethernet рекламируется
        # через оба. На диск (last_address) попадает лишь адрес, по которому
        # прошло TLS с закреплённым отпечатком - см. _on_connected.
        self._clock = clock if clock is not None else _monotonic_ms
        self._discovered_addresses: dict[str, float] = {}
```

Новые методы рядом с `_candidates`:

```python
    def _fresh_discovered_addresses(self) -> list[str]:
        """Свежие адреса маячка - в порядке, в каком они впервые услышаны."""
        now = self._clock()
        return [
            address for address, until in self._discovered_addresses.items() if now < until
        ]

    def _remember_discovered_address(self, address: str) -> bool:
        """Запомнить адрес маячка или продлить его срок. True - адрес новый.

        Протухшие вычищаются здесь же, лениво, без отдельного таймера.
        Повторный маячок продлевает срок, не двигая адрес в очереди.
        """
        now = self._clock()
        self._discovered_addresses = {
            known: until for known, until in self._discovered_addresses.items() if now < until
        }
        is_new = address not in self._discovered_addresses
        self._discovered_addresses[address] = now + DISCOVERED_ADDRESS_TTL_MS
        if len(self._discovered_addresses) > MAX_DISCOVERED_ADDRESSES:
            soonest = min(self._discovered_addresses, key=self._discovered_addresses.__getitem__)
            del self._discovered_addresses[soonest]
        return is_new

    def _forget_discovered_addresses(self) -> None:
        self._discovered_addresses = {}
```

`_candidates` целиком:

```python
    def _candidates(self) -> list[str]:
        """Куда звонить, по порядку: ручной адрес - единственный кандидат;
        иначе свежие адреса из маячков (пир виден прямо сейчас), последний
        удачный, затем адреса от платы, без повторов."""
        if self._manual_address:
            return [self._manual_address]
        peer = self.peer
        head = self._fresh_discovered_addresses() + ([peer.last_address] if peer else [])
        result: list[str] = []
        for address in head + self._board_addresses:
            if address and address not in result:
                result.append(address)
        return result
```

Ветка `if not self._pairing:` в `_on_peer_seen` целиком:

```python
        if not self._pairing:
            peer = self.peer
            # Отпечаток в маячке публичен, так что это фильтр, а не
            # аутентификация (её даёт TLS с закреплённым отпечатком): маячок
            # прежней установки пира не должен становиться подсказкой вовсе.
            if (
                peer is None
                or beacon.origin_id != peer.origin_id
                or beacon.fingerprint != peer.fingerprint
            ):
                return
            is_new = self._remember_discovered_address(address)
            if is_new:
                logger.info("discovery_hint address=%s", address)
            # Маячок - подсказка адреса, как и список от платы, и звонит по
            # тому же правилу: только на новую подсказку, не поверх живой
            # связи, набора или тикающего повтора и не из
            # BLOCKED/PROTOCOL_MISMATCH.
            if not is_new or not self._may_dial_on_hint():
                return
            self._candidate_index = 0
            self._try_connect()
            return
```

Сброс кэша:
- в `_on_connected` сразу после `self._candidate_index = 0` добавить `self._forget_discovered_addresses()`: удачный адрес уже стал `last_address`;
- в `stop()` после `self._candidate_index = 0` добавить `self._forget_discovered_addresses()`;
- в `forget_peer()` после `self._candidate_index = 0` добавить `self._forget_discovered_addresses()`;
- в `recover_after_resume()` **первой строкой**, до `if self._link is None: return`:

```python
        # time.monotonic на macOS не считает сон: адреса из маячков,
        # услышанных до сна, выглядели бы свежими. После сна они не
        # свидетельство ни о чём.
        self._forget_discovered_addresses()
```

В `__all__` добавить `"DISCOVERED_ADDRESS_TTL_MS"` и `"MAX_DISCOVERED_ADDRESSES"`.

- [ ] **Step 5: Обновить тест, который закреплял запись маячка на диск**

В `test_a_failed_last_known_address_starts_discovery_and_uses_the_new_address` после второго `assert calls == [...]` добавить:

```python
        # Адрес из маячка набран первым, но на диск не записан: это делает
        # только успешное соединение (_on_connected).
        assert trust.peer().last_address == "192.168.1.5"
```

Проверить `grep -n "last_address == \"192.168.1.99\"" configurator/tests/clipboard/test_coordinator.py`. Других утверждений, что маячок пишет на диск, быть не должно; найденное нужно переписать так же.

- [ ] **Step 6: GREEN и регрессия**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard -q -p no:cacheprovider`. Expected: всё PASS.

Мутации, каждая должна ронять хотя бы один тест:
- вернуть `self._trust.update_address(address)` в `_on_peer_seen`;
- убрать `if now < until` в `_fresh_discovered_addresses`;
- заменить словарь одним слотом: перед записью очищать `self._discovered_addresses = {}`;
- убрать вытеснение сверх `MAX_DISCOVERED_ADDRESSES`;
- убрать `_forget_discovered_addresses()` из `recover_after_resume` и из `_on_connected`;
- убрать `not is_new or`;
- поставить адреса маячка после `last_address`.

- [ ] **Step 7: Commit**

Готово, когда: маячок не пишет в `peers.json`, все свежие адреса multi-homed пира перебираются раньше известных, порядок и TTL закреплены тестами, регрессии зелёные.

```bash
git add configurator/src/duo_input/clipboard/coordinator.py configurator/tests/clipboard/test_coordinator.py
git commit -m "fix(clipboard): keep beacon addresses in memory with a TTL instead of persisting one"
```

---

### Task 5: Выбор интерфейсов для multicast

**Files:**
- Modify: `configurator/src/duo_input/clipboard/local_addresses.py`
- Test: `configurator/tests/clipboard/test_local_addresses.py`

**Interfaces:**
- Produces:
  - `InterfaceEntry` получает два поля с умолчаниями: `multicast: bool = True`, `point_to_point: bool = False`. Существующие позиционные вызовы не меняются;
  - `select_multicast_entries(entries: Iterable[InterfaceEntry]) -> list[InterfaceEntry]`;
  - `local_multicast_interfaces() -> list[QNetworkInterface]` (для Task 6);
  - `local_ipv4_addresses()` ведёт себя как раньше.

- [ ] **Step 1: Написать падающие тесты**

В `test_local_addresses.py` изменить импорт и `_nic`:

```python
from duo_input.clipboard.local_addresses import (
    InterfaceEntry,
    select_addresses,
    select_multicast_entries,
)


def _nic(
    name, kind, *addresses, up=True, running=True, loopback=False,
    multicast=True, point_to_point=False,
):
    return InterfaceEntry(
        name, kind, up, running, loopback, tuple(addresses), multicast, point_to_point
    )
```

Добавить тесты:

```python
# ---------------------------------------------------------------------- интерфейсы для маячка


def test_the_beacon_uses_every_lan_interface_not_just_the_default_route():
    wifi = _nic("Wi-Fi", "wifi", "192.168.1.20")
    wired = _nic("Ethernet", "ethernet", "192.168.1.10")
    assert select_multicast_entries([wifi, wired]) == [wifi, wired]


def test_point_to_point_vpn_tunnels_do_not_carry_the_beacon():
    """macOS utun (Tailscale и т.п.) заявляет CanMulticast, но это точка-точка:
    маячок туда уходит впустую."""
    wifi = _nic("Wi-Fi", "wifi", "192.168.1.20")
    tunnel = _nic("utun4", "other", "100.64.0.5", point_to_point=True)
    assert select_multicast_entries([tunnel, wifi]) == [wifi]


def test_an_interface_without_a_usable_ipv4_is_not_used():
    """Как на реальном Mac: anpi*/en1..en6 подняты и CanMulticast, но без
    адресов; awdl0 - только IPv6 link-local."""
    wifi = _nic("Wi-Fi", "wifi", "192.168.1.20")
    entries = [
        _nic("en1", "ethernet"),
        _nic("awdl0", "wifi", "fe80::1"),
        _nic("Ethernet", "ethernet", "169.254.3.4"),
        wifi,
    ]
    assert select_multicast_entries(entries) == [wifi]


def test_an_interface_that_cannot_multicast_is_skipped():
    wifi = _nic("Wi-Fi", "wifi", "192.168.1.20")
    assert select_multicast_entries([_nic("Ethernet", "ethernet", "192.168.1.10", multicast=False), wifi]) == [wifi]


def test_loopback_virtual_and_down_interfaces_never_carry_the_beacon():
    wifi = _nic("Wi-Fi", "wifi", "192.168.1.20")
    entries = [
        _nic("lo0", "other", "127.0.0.1", loopback=True),
        _nic("vEthernet (WSL)", "ethernet", "172.20.0.1"),
        _nic("docker0", "virtual", "172.17.0.1"),
        _nic("Ethernet", "ethernet", "192.168.1.10", up=False),
        wifi,
    ]
    assert select_multicast_entries(entries) == [wifi]


def test_a_non_tunnel_adapter_with_ipv4_is_kept():
    """Адаптер, который не точка-точка (например, Tailscale на Windows),
    остаётся: лишняя датаграмма безвредна, а угадывать по имени хуже."""
    other = _nic("Tailscale", "other", "100.64.0.5")
    assert select_multicast_entries([other]) == [other]


def test_local_multicast_interfaces_returns_qt_interfaces_of_this_machine():
    from PySide6.QtNetwork import QNetworkInterface

    from duo_input.clipboard.local_addresses import local_multicast_interfaces

    result = local_multicast_interfaces()

    assert all(isinstance(interface, QNetworkInterface) for interface in result)
    assert all(interface.isValid() for interface in result)
```

- [ ] **Step 2: RED**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/test_local_addresses.py -q -p no:cacheprovider`

Expected: сбор тестов падает с `ImportError: cannot import name 'select_multicast_entries'`.

- [ ] **Step 3: Минимальная реализация**

`local_addresses.py`. Docstring модуля дополнить абзацем:

```python
"""…(существующий текст)…

Маячку обнаружения нужен не список адресов, а список интерфейсов: тот же
фильтр, плюс интерфейс должен уметь multicast, не быть туннелем точка-точка
и иметь хотя бы один пригодный IPv4.
"""
```

Изменить dataclass и функции:

```python
@dataclass(frozen=True)
class InterfaceEntry:
    name: str
    kind: str
    up: bool
    running: bool
    loopback: bool
    addresses: tuple[str, ...]
    multicast: bool = True
    point_to_point: bool = False


def _usable(entries: Iterable[InterfaceEntry]) -> list[InterfaceEntry]:
    return [
        entry
        for entry in entries
        if entry.up
        and entry.running
        and not entry.loopback
        and entry.kind != "virtual"
        and not entry.name.lower().startswith(_VIRTUAL_NAME_PREFIXES)
    ]


def select_addresses(entries: Iterable[InterfaceEntry]) -> list[str]:
    usable = _usable(entries)
    # sorted() устойчива: внутри группы остаётся порядок перечисления.
    usable.sort(key=lambda entry: _ORDER.get(entry.kind, 2))
    result: list[str] = []
    for entry in usable:
        for address in entry.addresses:
            if _reachable(address) and address not in result:
                result.append(address)
    return result[:MAX_HOST_ADDRESSES]


def select_multicast_entries(entries: Iterable[InterfaceEntry]) -> list[InterfaceEntry]:
    """Интерфейсы, на которых маячок стоит слушать и через которые слать."""
    return [
        entry
        for entry in _usable(entries)
        if entry.multicast
        and not entry.point_to_point
        and any(_reachable(address) for address in entry.addresses)
    ]


def _interfaces_with_entries() -> list[tuple[object, InterfaceEntry]]:
    from PySide6.QtNetwork import QAbstractSocket, QNetworkInterface

    kinds = {
        QNetworkInterface.InterfaceType.Ethernet: "ethernet",
        QNetworkInterface.InterfaceType.Wifi: "wifi",
        QNetworkInterface.InterfaceType.Virtual: "virtual",
    }
    flags = QNetworkInterface.InterfaceFlag
    pairs = []
    for interface in QNetworkInterface.allInterfaces():
        state = interface.flags()
        pairs.append(
            (
                interface,
                InterfaceEntry(
                    name=interface.humanReadableName(),
                    kind=kinds.get(interface.type(), "other"),
                    up=bool(state & flags.IsUp),
                    running=bool(state & flags.IsRunning),
                    loopback=bool(state & flags.IsLoopBack),
                    addresses=tuple(
                        entry.ip().toString()
                        for entry in interface.addressEntries()
                        if entry.ip().protocol()
                        == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol
                    ),
                    multicast=bool(state & flags.CanMulticast),
                    point_to_point=bool(state & flags.IsPointToPoint),
                ),
            )
        )
    return pairs


def local_ipv4_addresses() -> list[str]:
    return select_addresses([entry for _, entry in _interfaces_with_entries()])


def local_multicast_interfaces() -> list:
    """QNetworkInterface для маячка - см. select_multicast_entries."""
    pairs = _interfaces_with_entries()
    chosen = {id(entry) for entry in select_multicast_entries([entry for _, entry in pairs])}
    return [interface for interface, entry in pairs if id(entry) in chosen]


__all__ = [
    "InterfaceEntry",
    "local_ipv4_addresses",
    "local_multicast_interfaces",
    "select_addresses",
    "select_multicast_entries",
]
```

- [ ] **Step 4: GREEN и регрессия**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard -q -p no:cacheprovider`

Expected: всё PASS, существующие тесты `select_addresses` без правок.

Ручная проверка на этом Mac:

```bash
../.venv-mac/bin/python -c "from duo_input.clipboard.local_addresses import local_multicast_interfaces as f; print([i.name() for i in f()])"
```

Ожидается `['en0']`: `utun*`, `awdl0`, `anpi*`, `bridge0` отсечены.

Мутации: убрать `not entry.point_to_point`, `entry.multicast`, `any(_reachable…)` — каждая должна ронять тест.

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/clipboard/local_addresses.py configurator/tests/clipboard/test_local_addresses.py
git commit -m "feat(clipboard): pick the LAN interfaces a discovery beacon should use"
```

---

### Task 6: `Discovery` вступает в группу и шлёт маячок на каждом LAN-интерфейсе

**Files:**
- Modify: `configurator/src/duo_input/clipboard/discovery.py` (класс `Discovery`; `Beacon`, `encode_beacon`, `decode_beacon` и константы не трогать)
- Test: `configurator/tests/clipboard/test_discovery_beacon.py`

**Interfaces:**
- Consumes: `local_multicast_interfaces()` из Task 5.
- Produces: `Discovery.__init__(own_origin_id, parent=None, socket=None, interfaces: Callable[[], list] | None = None)`. `interfaces` возвращает объекты с методом `name() -> str` (в проде это `QNetworkInterface`). Сигнал `peer_seen` и методы `start` и `stop` не меняются, поэтому координатор ничего не правит.

- [ ] **Step 1: Доработать `MockUdpSocket` и написать падающие тесты**

В `MockUdpSocket.__init__` добавить:

```python
        self.multicast_interface = None
        self.sent_via = []
        self.fail_writes = False
```

Методы `joinMulticastGroup`, `leaveMulticastGroup` и `writeDatagram` заменить, добавить `setMulticastInterface` и `errorString`:

```python
    def joinMulticastGroup(self, address, iface=None):
        self.join_count += 1
        self.operations.append("join" if iface is None else f"join:{iface.name()}")
        self.multicast_groups.add(str(address.toString()))
        return True

    def leaveMulticastGroup(self, address, iface=None):
        self.leave_count += 1
        self.operations.append("leave" if iface is None else f"leave:{iface.name()}")
        self.multicast_groups.discard(str(address.toString()))
        return True

    def setMulticastInterface(self, iface):
        self.operations.append(f"via:{iface.name()}")
        self.multicast_interface = iface.name()

    def writeDatagram(self, data, address, port):
        self.operations.append("announce")
        self.sent_datagrams.append((bytes(data), str(address.toString()), port))
        self.sent_via.append(self.multicast_interface)
        return -1 if self.fail_writes else len(bytes(data))

    def errorString(self):
        return "No route to host"
```

После `MockUdpSocket` добавить:

```python
class _FakeIface:
    def __init__(self, name: str) -> None:
        self._name = name

    def name(self) -> str:
        return self._name


OUR_BEACON = Beacon(OURS, "LAPTOP-ONE", "a" * 64, 47654, PROTOCOL_MAJOR)
```

В `test_restarting_discovery_rebinds_and_rejoins_multicast` заменить конструктор, иначе порядок операций зависит от сетевых карт машины, на которой идут тесты:

```python
    discovery = Discovery(OURS, socket=socket, interfaces=lambda: [])
```

Новые тесты:

```python
def test_the_beacon_is_joined_and_sent_on_every_interface(qapp):
    socket = MockUdpSocket()
    discovery = Discovery(
        OURS, socket=socket, interfaces=lambda: [_FakeIface("en0"), _FakeIface("en7")]
    )

    assert discovery.start(OUR_BEACON) is True

    assert socket.operations == [
        "bind", "join:en0", "join:en7", "via:en0", "announce", "via:en7", "announce",
    ]
    assert socket.sent_via == ["en0", "en7"]
    discovery.stop()


def test_an_interface_that_appears_later_is_joined_on_the_next_beacon(qapp):
    """Воткнули Ethernet или переподключился Wi-Fi - рестарт discovery не нужен."""
    socket = MockUdpSocket()
    current = [_FakeIface("en0")]
    discovery = Discovery(OURS, socket=socket, interfaces=lambda: list(current))
    discovery.start(OUR_BEACON)
    current.append(_FakeIface("en7"))
    socket.operations.clear()

    discovery._announce()

    assert socket.operations == ["join:en7", "via:en0", "announce", "via:en7", "announce"]
    discovery.stop()


def test_an_interface_that_went_away_is_rejoined_when_it_returns(qapp):
    socket = MockUdpSocket()
    current = [_FakeIface("en0"), _FakeIface("en7")]
    discovery = Discovery(OURS, socket=socket, interfaces=lambda: list(current))
    discovery.start(OUR_BEACON)

    current[:] = [_FakeIface("en0")]
    discovery._announce()
    current[:] = [_FakeIface("en0"), _FakeIface("en7")]
    socket.operations.clear()
    discovery._announce()

    assert socket.operations[0] == "join:en7"
    discovery.stop()


def test_without_any_lan_interface_the_default_route_is_used_as_before(qapp):
    socket = MockUdpSocket()
    discovery = Discovery(OURS, socket=socket, interfaces=lambda: [])

    discovery.start(OUR_BEACON)

    assert socket.operations == ["bind", "join", "announce"]
    assert socket.sent_via == [None]
    discovery.stop()


def test_stop_leaves_every_joined_group(qapp):
    socket = MockUdpSocket()
    discovery = Discovery(
        OURS, socket=socket, interfaces=lambda: [_FakeIface("en0"), _FakeIface("en7")]
    )
    discovery.start(OUR_BEACON)
    socket.operations.clear()

    discovery.stop()

    assert socket.operations == ["leave:en0", "leave:en7", "close"]


def test_a_failed_send_is_logged_once_per_interface_until_it_recovers(qapp, caplog):
    """На macOS без доступа к локальной сети отправка отказывает каждые 2 с -
    журнал получает одну строку, а не поток."""
    caplog.set_level(logging.INFO, logger="duo_input.clipboard.discovery")
    socket = MockUdpSocket()
    socket.fail_writes = True
    discovery = Discovery(OURS, socket=socket, interfaces=lambda: [_FakeIface("en0")])

    discovery.start(OUR_BEACON)
    discovery._announce()
    socket.fail_writes = False
    discovery._announce()
    socket.fail_writes = True
    discovery._announce()

    failures = [r.getMessage() for r in caplog.records if "beacon_send_failed" in r.getMessage()]
    recoveries = [r.getMessage() for r in caplog.records if "beacon_send_recovered" in r.getMessage()]
    assert len(failures) == 2
    assert "interface=en0" in failures[0] and "No route to host" in failures[0]
    assert len(recoveries) == 1
    discovery.stop()
```

В импорты теста добавить `import logging`.

- [ ] **Step 2: RED**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard/test_discovery_beacon.py -q -p no:cacheprovider`

Expected: FAIL с `TypeError: … unexpected keyword argument 'interfaces'`, в том числе у обновлённого `test_restarting_…`.

- [ ] **Step 3: Минимальная реализация**

В `discovery.py` импорты:

```python
import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QHostAddress, QUdpSocket

from .local_addresses import local_multicast_interfaces
from .wire import PROTOCOL_MAJOR

logger = logging.getLogger(__name__)
```

Класс `Discovery` заменить целиком:

```python
class Discovery(QObject):
    """Рассылает свой маячок и слушает чужие - на каждом LAN-интерфейсе.

    Один сокет: членство в группе и отправка задаются по интерфейсу
    (joinMulticastGroup(group, iface), setMulticastInterface(iface)). Без
    этого маячок уходит только через маршрут по умолчанию - а он при Wi-Fi и
    Ethernet сразу или с поднятым VPN ведёт не туда, где второй компьютер.
    """

    peer_seen = Signal(object, str)

    def __init__(
        self,
        own_origin_id: str,
        parent: QObject | None = None,
        socket: QUdpSocket | None = None,
        interfaces: Callable[[], list] | None = None,
    ) -> None:
        super().__init__(parent)
        self._own_origin_id = own_origin_id
        self._beacon: Beacon | None = None
        self._socket = socket if socket is not None else QUdpSocket(self)
        self._socket.readyRead.connect(self._on_ready_read)
        # Список интерфейсов берётся заново на каждом маячке: Wi-Fi
        # переподключается, Ethernet втыкают, VPN поднимают.
        self._interfaces = interfaces if interfaces is not None else local_multicast_interfaces
        self._joined: dict[str, object] = {}
        self._default_joined = False
        self._failing: set[str] = set()
        self._active = False
        self._timer = QTimer(self)
        self._timer.setInterval(BEACON_INTERVAL_MS)
        self._timer.timeout.connect(self._announce)

    def start(self, beacon: Beacon) -> bool:
        """Начать рассылку маячка и приём чужих. Вернуть True при успехе."""
        self._beacon = beacon
        if self._active:
            # После sleep/wake прежний fd может выглядеть открытым, но уже не
            # состоять в multicast-группе нового сетевого интерфейса. Каждый
            # повторный recovery начинает настоящую новую UDP-сессию.
            self._timer.stop()
            self._leave_all()
            self._socket.close()
            self._active = False
        bound = self._socket.bind(
            QHostAddress.SpecialAddress.AnyIPv4,
            BEACON_PORT,
            QUdpSocket.BindFlag.ShareAddress | QUdpSocket.BindFlag.ReuseAddressHint,
        )
        if not bound:
            return False
        self._active = True
        self._announce()
        self._timer.start()
        return True

    def stop(self) -> None:
        """Остановить рассылку и приём."""
        self._timer.stop()
        if self._active:
            self._leave_all()
        self._socket.close()
        self._active = False

    def _announce(self) -> None:
        """Отправить свой маячок в группу через каждый LAN-интерфейс."""
        if self._beacon is None:
            return
        payload = encode_beacon(self._beacon)
        group = QHostAddress(GROUP_ADDRESS)
        interfaces = self._sync_memberships(group)
        if not interfaces:
            self._send(payload, group, "default")
            return
        for interface in interfaces:
            self._socket.setMulticastInterface(interface)
            self._send(payload, group, interface.name())

    def _sync_memberships(self, group: QHostAddress) -> list:
        """Вступить в группу на новых интерфейсах; вернуть текущий список."""
        current = list(self._interfaces())
        names = {interface.name() for interface in current}
        # Исчезнувший интерфейс унёс членство с собой; вернётся - вступим заново.
        for name in [name for name in self._joined if name not in names]:
            del self._joined[name]
        for interface in current:
            name = interface.name()
            if name not in self._joined and self._socket.joinMulticastGroup(group, interface):
                self._joined[name] = interface
        if not current and not self._default_joined:
            # Ни одного подходящего интерфейса - прежнее поведение: группа на
            # интерфейсе маршрута по умолчанию, лучше так, чем никак.
            self._socket.joinMulticastGroup(group)
            self._default_joined = True
        return current

    def _send(self, payload: bytes, group: QHostAddress, label: str) -> None:
        written = self._socket.writeDatagram(payload, group, BEACON_PORT)
        if written == -1:
            if label not in self._failing:
                self._failing.add(label)
                # Одна строка на интерфейс, а не каждые две секунды: на macOS
                # так выглядит, например, запрет доступа к локальной сети.
                logger.warning(
                    "beacon_send_failed interface=%s error=%s",
                    label,
                    self._socket.errorString(),
                )
        elif label in self._failing:
            self._failing.discard(label)
            logger.info("beacon_send_recovered interface=%s", label)

    def _leave_all(self) -> None:
        group = QHostAddress(GROUP_ADDRESS)
        for interface in self._joined.values():
            self._socket.leaveMulticastGroup(group, interface)
        if self._default_joined:
            self._socket.leaveMulticastGroup(group)
        self._joined.clear()
        self._default_joined = False

    def _on_ready_read(self) -> None:
        """Обработать входящую датаграмму."""
        while self._socket.hasPendingDatagrams():
            datagram = self._socket.receiveDatagram()
            beacon = decode_beacon(bytes(datagram.data()), self._own_origin_id)
            if beacon is not None:
                self.peer_seen.emit(beacon, datagram.senderAddress().toString())
```

- [ ] **Step 4: GREEN и регрессия**

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard -q -p no:cacheprovider`

Expected: всё PASS, в том числе `test_restarting_discovery_rebinds_and_rejoins_multicast` с прежним ожиданием `["leave", "close", "bind", "join", "announce"]` и тесты координатора с настоящим сокетом.

Проверить границы подсистемы: `../.venv-mac/bin/python -m pytest tests/clipboard/test_boundaries.py -q -p no:cacheprovider`. Импорт `local_addresses` из `discovery` не должен нарушать запрет QtWidgets.

Мутации, каждая должна ронять тест:
- убрать `setMulticastInterface`;
- убрать удаление исчезнувших из `_joined`;
- убрать fallback `if not current and not self._default_joined`;
- убрать `if label not in self._failing`.

- [ ] **Step 5: Живая проверка на одной машине (Mac)**

Из `configurator/` вне sandbox запустить два процесса-слушателя. Оба используют `ShareAddress`, и каждый должен увидеть маячок другого:

```bash
../.venv-mac/bin/python - <<'EOF'
import sys
from PySide6.QtCore import QCoreApplication, QTimer
from duo_input.clipboard.discovery import Beacon, Discovery
from duo_input.clipboard.wire import PROTOCOL_MAJOR
app = QCoreApplication(sys.argv)
a, b = Discovery("1" * 32), Discovery("2" * 32)
seen = []
b.peer_seen.connect(lambda beacon, addr: seen.append(addr))
a.start(Beacon("1" * 32, "A", "a" * 64, 47654, PROTOCOL_MAJOR))
b.start(Beacon("2" * 32, "B", "b" * 64, 47654, PROTOCOL_MAJOR))
QTimer.singleShot(5000, app.quit)
app.exec()
print("seen:", sorted(set(seen)))
EOF
```

Ожидается `seen: ['<IPv4 en0>']` — один или несколько адресов этой машины.

- [ ] **Step 6: Commit**

Готово, когда: все тесты зелёные, мутации пойманы, живая проверка показала приём.

```bash
git add configurator/src/duo_input/clipboard/discovery.py configurator/tests/clipboard/test_discovery_beacon.py
git commit -m "fix(clipboard): join and announce the discovery beacon on every LAN interface"
```

---

### Task 7: Двухмашинный E2E runbook Windows ↔ macOS и исправление комментария

**Files:**
- Create: `docs/superpowers/records/validation/discovery-win-mac-e2e.md`
- Modify: `configurator/src/duo_input/clipboard/coordinator.py` (комментарий в `begin_pairing`, ~345–349; поведение не меняется)

**Interfaces:** Consumes строки журнала `discovery_hint address=…` (Task 4), `beacon_send_failed` и `beacon_send_recovered` (Task 6), существующие `coordinator_link_attached` и `address_in_use`.

- [ ] **Step 1: Исправить комментарий**

В `begin_pairing` фразу «на упакованной сборке он к тому же может быть недоступен (Local Network / multicast), тогда как unicast к известному адресу работает» заменить:

```python
        # Пир уже доверен, но связь не поднята: воссоединяемся по сохранённому
        # адресу (unicast), а не запускаем discovery. Мультикаст-поиск нужен
        # только для ПЕРВИЧНОГО связывания; для доверенного пира маячок
        # включается сам, когда известные адреса не ответили (см. _drop).
        # Нового пира связываем только после forget_peer(), когда self.peer
        # снова None.
```

Run: `cd configurator && ../.venv-mac/bin/python -m pytest tests/clipboard -q -p no:cacheprovider`. Expected: всё PASS, меняется только комментарий.

- [ ] **Step 2: Написать runbook**

`docs/superpowers/records/validation/discovery-win-mac-e2e.md` содержит:

- **Предусловия:**
  - на Mac установлен `DuoInput.app` из `packaging/nuitka-build-macos.sh` с этой ветки, Local Network разрешён, после разрешения выполнен перезапуск;
  - на Windows — сборка той же ветки, профиль сети `Private`;
  - платы U1/U2 **отключены**, если строка не требует иного;
  - ручной адрес пуст на обеих машинах.
- **Где смотреть:**
  - журнал: Mac `~/.local/share/DuoInput/logs/`, Windows `%LOCALAPPDATA%\DuoInput\logs\`;
  - доверие: `peers.json` в `~/.local/share/DuoInput/` и `%LOCALAPPDATA%\DuoInput\`;
  - состояние: трей и страница «Общий буфер».
- **Матрица.** Для каждой строки записать «ожидается / фактически / PASS-FAIL / время до CONNECTED»:

| # | Сценарий | Ожидается |
|---|---|---|
| E1 | «Связать компьютеры» на обеих машинах, без платы и без ручного IP | коды совпали, после подтверждения — CONNECTED; `peers.json` содержит фактический адрес |
| E2 | Mac сменил IP (Wi-Fi → другой DHCP-адрес или ручная смена IPv4 в настройках сети) при CONNECTED | обрыв → SEARCHING → `discovery_hint address=<новый>` в журнале Windows → CONNECTED в пределах текущей паузы повтора; `last_address` на Windows обновился **только после** `coordinator_link_attached` |
| E3 | Windows сменил IP (то же с другой стороны) | как E2 зеркально |
| E4 | Mac: сон 2 мин → пробуждение | CONNECTED восстановлен; первый набор идёт не на адрес маячка, услышанного до сна |
| E5 | Windows: сон 2 мин → пробуждение (wake-hook вне scope) | CONNECTED восстановлен; зафиксировать время, оно не является gate |
| E6 | На Mac **и** Windows одновременно Wi-Fi и Ethernet; отключить кабель | маячки уходят через оба интерфейса (без `beacon_send_failed`), связь восстанавливается через оставшийся |
| E7 | На Mac поднят VPN (Tailscale/WireGuard) с default route | discovery по LAN работает; в журнале нет попытки слать через `utun*` |
| E8 | Платы подключены и discovery одновременно; перезапустить приложение на одной стороне | ровно одна связь: одна строка `coordinator_link_attached` на поколение, без параллельных наборов |
| E9 | Ввести ручной адрес, затем очистить поле | с ручным адресом набирается только он; после очистки — автоматический режим, маячок и плата снова в деле |
| E10 | Mac: выключить Duo Input в «Локальная сеть» и перезапустить | SEARCHING; одна строка `beacon_send_failed … error=…` (дословно в запись); связь не появляется. Известное ограничение: UX-подсказка — отдельная задача |
| E11 | Третья машина шлёт поддельный маячок с `origin_id` и отпечатком пира (payload из `peers.json`/журнала, свой IP) | `peers.json` не содержит IP третьей машины; связь с настоящим пиром поднимается, в худшем случае одна неудачная попытка |
| E12 | После E2/E4: скопировать текст и файл в обе стороны | clipboard и передача файла работают |

- **Итог:** PASS, если E1–E4 и E6–E12 PASS. E5 — информационная строка.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/records/validation/discovery-win-mac-e2e.md configurator/src/duo_input/clipboard/coordinator.py
git commit -m "docs(discovery): Windows<->macOS E2E runbook; drop the stale multicast caveat"
```

- [ ] **Step 4: Прогон runbook**

Нужна двухмашинная установка. Пользователь или оператор прогоняет матрицу и дописывает результаты в тот же файл. Отдельный коммит: `docs(discovery): E2E results <дата>`.

---

## Вне этого плана (сознательно)

- Маячок несовместимой версии протокола в парринге: сейчас `decode_beacon` его отбрасывает, и пользователь видит «Поиск». UX — отдельная задача.
- Подсказка «разрешите доступ к локальной сети» на macOS. Опирается на строку `beacon_send_failed`, появившуюся в Task 6, и на ошибку, зафиксированную в Task 0.
- Wake-hook на Windows.
- Правило брандмауэра Windows в установщике.
