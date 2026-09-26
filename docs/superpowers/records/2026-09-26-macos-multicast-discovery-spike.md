# Spike: UDP multicast discovery Windows ↔ macOS в `.app` без платного Apple Developer Program

**Дата:** 2026-09-26
**План:** `docs/superpowers/plans/2026-09-26-lan-discovery-hardening.md`, Task 0 (одноразовый, hard gate)
**Вопрос:** проходит ли наш UDP multicast `239.255.76.67:47655` между Windows и macOS, если на Mac работает `.app`, подписанный Personal Team, без `com.apple.developer.networking.multicast`?

**Ответ: да.** S1 PASS и S2 PASS после Allow в запросе Local Network, в обе стороны.

## Стенд

- **Mac:** macOS 27.0 (26A428), arm64, Wi-Fi `en0` = 192.168.0.252. Брандмауэр macOS выключен. Маршрут `224.0.0.0/4` идёт через `en0`.
- **Windows:** версия не записана. Одновременно активны `ethernet_32774` и `wireless_32771`. Маячки к Mac приходили с 192.168.0.128. Probe работал из `.venv` проекта (Python + PySide6).
- **Часы:** на Windows время ровно на 1 ч впереди Mac (другой часовой пояс). В таблицах ниже время указано по часам той машины, чей журнал цитируется.
- **Probe:** `scratchpad/discovery-spike/probe.py`, не коммитится. Повторяет Qt-вызовы `Discovery`: `bind(AnyIPv4, 47655, ShareAddress|ReuseAddressHint)`, `joinMulticastGroup(group)`, `writeDatagram(group, 47655)` раз в 2 с. В режиме `--per-interface` вступает в группу и шлёт через каждый LAN-интерфейс (`joinMulticastGroup(group, iface)`, `setMulticastInterface(iface)`).

## Подпись `.app` (gate codesign/entitlements) — PASS

Бандлы собраны `scratchpad/discovery-spike/build-probe-app.sh`: Nuitka `--standalone --macos-create-app-bundle`, затем подпись каждого вложенного Mach-O и внешнего бандла Personal Team с `--options runtime`, как в `packaging/nuitka-build-macos.sh`.

| Бандл | Identifier | TeamIdentifier | flags | entitlements | `NSLocalNetworkUsageDescription` | UUID главного бинаря |
|---|---|---|---|---|---|---|
| `DuoProbe-a1.app` | `com.duoinput.spike.mcast.a1` | 4YKVN22BMX | `0x10000(runtime)` | пустой `<dict/>` | есть | F7B8AC47-943E-39C6-A9A7-85B4C63DAA14 |
| `DuoProbe-a2.app` | `com.duoinput.spike.mcast.a2` | 4YKVN22BMX | `0x10000(runtime)` | пустой `<dict/>` | есть | FF029288-E4E3-382E-B673-FA655666076B |
| `DuoProbe-b1.app` | `com.duoinput.spike.mcast.b1` | 4YKVN22BMX | `0x10000(runtime)` | пустой `<dict/>` | **нет** | 690FB850-1E28-3D09-8AD6-DC98E1C3C2E4 |

- `codesign --verify --strict`: `valid on disk`, `satisfies its Designated Requirement`.
- `com.apple.developer.networking.multicast` и любые другие entitlements **отсутствуют**.
- Бандлам даны разные `CFBundleName`/`CFBundleDisplayName` (DuoProbe-a1/a2/b1), чтобы различать их в «Системных настройках → Локальная сеть». После этого внешний бандл переподписан.

## Результаты

### Прогон 1 — Windows в режиме маршрута по умолчанию: FAIL в обе стороны (сетевая причина, не `.app`)

| Участник | Отправка | Принято от другой машины |
|---|---|---|
| WIN (17:07:56–17:15:40, default-route) | 233 `ok` | **0** (только свои датаграммы) |
| MAC-TERM (16:08:27–16:11:27, Terminal) | 89 `ok` | **0** от Windows |
| `DuoProbe-a1.app` (16:09:12–16:09:48) | см. ниже | **0** от Windows |

Контрольный запуск из Terminal (он освобождён от Local Network privacy) тоже ничего не принял. Значит, причина не в `.app`.

### Прогон 2 — Windows `--per-interface`: S1 PASS

- Windows: `JOIN ethernet_32774 -> True`, `JOIN wireless_32771 -> True`; по 450 отправок через каждый интерфейс, все `ok`.
- **Win→Mac:** MAC-CLI принял **46**, MAC-TERM — **15** маячков `'from': 'WIN'` с 192.168.0.128.
- **Mac→Win:** Windows принял **51** маячок от MAC-CLI и **14** от MAC-TERM, все с 192.168.0.252.

Маячки Windows приходили на Mac только с одного адреса (192.168.0.128), хотя уходили через оба интерфейса. В сеть Mac смотрит один интерфейс Windows. В прогоне 1 Windows отправлял через маршрут по умолчанию, то есть через другой интерфейс, и не дошло ничего. **Корневая причина прогона 1 — выбор интерфейса для multicast на multi-homed Windows.** Это тот же механизм, что дал ложный вывод «сеть режет multicast» в record `2026-09-17-macos-app-packaging.md`.

### S2 — `DuoProbe-a1.app` (Personal Team, без multicast-entitlement): PASS после Allow

**Первый запуск (двойной клик в Finder, состояние Local Network «не определено»), 16:09 по Mac:**
- macOS **показал запрос Local Network**.
- `SEND seq=1` и `seq=2` (16:09:12 и 16:09:14): `FAIL error=NetworkError 'Unable to send a message'` — отправку отклонили, пока запрос висел без ответа. `writeDatagram` вернул −1.
- Пользователь нажал **Allow** около 16:09:15. С `seq=3` (16:09:16) отправка `ok`, приём и отправка работают в обе стороны с процессом из Terminal на том же Mac. **Перезапуск не понадобился**: разрешение подействовало в том же процессе.
- У probe нет окон (`QCoreApplication`), поэтому после запуска «ничего не видно». Так и задумано.

**Межмашинный прогон (Local Network = Allowed), запуск через `open` (LaunchServices, `PPID=1`), 16:42:29–16:45:29 по Mac:**
- Запроса нет (разрешение уже выдано). `SEND` **91 `ok` / 0 `FAIL`**.
- **Win→.app:** принято **90** маячков `'from': 'WIN'` с 192.168.0.128.
- **.app→Win:** Windows (запуск 17:41:54, `--per-interface`) принял **91 из 91** маячка `'from': 'mac-app-with-usage-a1'` (seq 1–91, 17:42:28–17:45:28 по часам Windows) с 192.168.0.252.

### Не прогонялись (для gate не нужны)

S3 (a1 после Deny в System Settings), S4 (a2 с «Don't Allow»), S5 (b1 без `NSLocalNetworkUsageDescription`), S6 (главный бинарь `.app` из Terminal), S7 (`--per-interface` на Mac с двумя интерфейсами). Бандлы a2 и b1 собраны и лежат в `scratchpad/discovery-spike/`.

## Вывод

1. **Gate PASS.** Существующий UDP multicast discovery работает между Windows и macOS в `.app`, подписанном Personal Team, с hardened runtime и `NSLocalNetworkUsageDescription`, **без** `com.apple.developer.networking.multicast` и без платного Apple Developer Program. Нужно только разрешение пользователя в запросе Local Network. Это совпадает с Apple TN3179: «The multicast entitlement isn't required on macOS».
2. **Пока запрос Local Network без ответа, первые отправки отклоняются** с `NetworkError 'Unable to send a message'`. Discovery должен переживать это молча и повторять отправку. Текущий таймер 2 с так и делает. Строки `beacon_send_failed` / `beacon_send_recovered` из Task 6 сделают эту ситуацию видимой в журнале.
3. **Разрешение действует без перезапуска процесса** — для этой сборки на macOS 27.0. Утверждение из record 09-17 «нужно перезапустить» здесь не подтвердилось.
4. **Главный практический риск — не macOS, а multi-homed Windows.** Текущий production-`Discovery` шлёт и слушает только через маршрут по умолчанию. На этой паре машин это даёт 0 маячков в обе стороны, а при рассылке через каждый интерфейс всё работает. Это напрямую обосновывает Tasks 5–6 плана.
