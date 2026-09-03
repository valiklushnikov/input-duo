# Итоговое закрытие находок финального ревью (feature/shared-clipboard)

Дата: 2026-09-03. База: `5e7b505`. Одна волна точечных правок, архитектура не менялась.

## CRITICAL

### C1. Общий буфер невозможно было включить

Причина, которую не видно было из текста ревью: даже подключив сигналы
`sharing_toggled`/`autostart_toggled` к обработчику, обработчик молчал бы -
`_ClipboardRuntime` был обычным Python-объектом без владельца, и PySide6
хранит слабую ссылку на связанный метод такого объекта. Объект собирался бы
GC сразу после возврата из `configure_runtime()`, сигнал эмитился бы, а
обработчик уже был бы мёртв - без единой ошибки. Обнаружено не чтением кода,
а прогоном: тест «переключить настоящий чекбокс» не менял состояние, хотя всё
подключение выглядело правильно. Исправлено наследованием `_ClipboardRuntime`
от `QObject` с `parent=application`.

Сделано (`configurator/src/duo_input/app.py`):
- `_ClipboardRuntime(QObject)` - единственное место, решающее, поднята ли
  подсистема. Пока `_start()` ни разу не вызван, ни `TrustStore`, ни
  `ClipboardCoordinator`, ни `TrayIcon` не существуют - ни один сокет не
  открывается, пока фича выключена (проверено тестом на реальном порту).
- `configure_runtime()` подключает `sharing_toggled`/`autostart_toggled`
  страницы ДО чтения сохранённого состояния, затем показывает состояние
  одинаково на странице и в трее (`set_sharing_checked`/`set_autostart_checked`
  с `blockSignals`, чтобы отображение не порождало новое переключение).
- Трей больше не выставляется принудительно `checked=True` - оба виджета
  читают один и тот же путь.
- `address_changed` подключён к `coordinator.set_manual_address` (метод уже
  существовал у координатора).
- Выключение действительно останавливает координатора и бэкенд, закрывает
  трей и возвращает `setQuitOnLastWindowClosed(True)` - не только меняет
  надпись.

Не сделано (сознательно, вне текста C1): `clipboard/manual_address` не
перечитывается при следующем запуске - поле пустое после перезапуска,
координатору его нужно вводить заново. C1 просил подключить поле к
координатору, а не персистентность; отмечаю на случай, если это ожидалось.

### C2. Автозапуск - мёртвый код

- `persistence/autostart.py::enable()` теперь всегда дописывает
  `HIDDEN_START_ARGUMENT = "--hidden"` в команду ярлыка (единственное
  назначение этого ярлыка - автозапуск, поэтому флаг не за отдельным
  параметром).
- `app.py::main()` читает `--hidden` из argv; `start_window(window, show=...)`
  получил параметр `show` - `False` только когда одновременно передан флаг
  скрытого старта И общий буфер включён в настройках (защита от «спрятанного
  насовсем» окна, если автозапуск включили, а буфер - нет: без общего буфера
  нет и трея, и открыть программу было бы неоткуда).
- `ClipboardPage.autostart_checkbox.toggled` подключён к
  `_ClipboardRuntime.set_autostart`, который вызывает `autostart.enable/disable`
  и ловит `OSError` (см. I6).

## IMPORTANT

### I1. Обрыв входящей связи не замечался

`_on_incoming_link()` и `_maybe_finish_pairing()` теперь тоже подписывают
`link.disconnected.connect(self._on_disconnected)` перед `_on_connected()`
(раньше это делал только `_try_connect()`). Оставлена причина, почему нельзя
просто перенести подписку внутрь `_on_connected()`: `_try_connect()` подписывается
ДО завершения TLS-рукопожатия, чтобы ловить и неудачные попытки соединения -
перенос сломал бы это.

### I2. «Забыть компьютер» не останавливал таймеры/маячок

`forget_peer()` теперь останавливает `_retry`, `_silence`, `_discovery` и
сбрасывает `_attempt`. `_try_connect()` дополнительно отказывается искать пира
вообще, если `self.peer is None` - это вторая, независимая линия защиты:
даже если какой-то таймер всё же выстрелит после `forget_peer()`, он не
включит маячок повторно.

### I3. Ручной адрес открывал вторую связь

`set_manual_address()` останавливает `_retry`/`_silence`, закрывает и
детачит прежнюю связь (`self._link.close()` + `self._service.detach_link()`)
перед новой попыткой - иначе `attach_link()` подключал `offer_ready` второй
раз к тому же слоту, и объявление уходило бы дважды.

### I4. Причина разрыва выбрасывалась

`_drop(reason, *, protocol_mismatch=False)` теперь: логирует и эмитит
`event_logged` с причиной; при расхождении версии протокола уходит в новое
состояние `LinkState.PROTOCOL_MISMATCH` вместо `DISCONNECTED` и НЕ планирует
повтор (`_retry` не стартует) - обновление второй машины само не случится, и
бесконечные попытки были бы враньём. `tray.py`/`clipboard_page.py` показывают
отдельную надпись «Обновите вторую машину — версии протокола различаются».

### I5. Из `clipboard/` почти ничего не попадало в журнал

- `windows_backend.snapshot_from()` логирует `WARNING` с именем формата и
  размером, когда превышение потолка 32 МиБ отбрасывает формат.
- `service.on_local_snapshot()` ловит `ValueError` от `describe()` (её
  собственный контракт для тех, кто сам не отфильтровал) и логирует вместо
  падения слота, подключённого к сигналу Qt - оба пути теперь ведут к одному
  исходу: запись в журнал, отказ от объявления, ничего на экране не падает.
- `coordinator._drop()` логирует причину разрыва; `ClipboardService.content_failed`
  подключён к новому `coordinator.event_logged`, который проксируется на
  страницу.

### I6. Отказ identity-файлов ронял всё приложение

Два рубежа: `_ClipboardRuntime._start()` ловит `OSError` вокруг
`load_or_create()`/`TrustStore()` и деградирует до «общий буфер не
запустился» (лог + запись в список событий + выключение переключателя, без
исключения наружу); `main()` дополнительно оборачивает сам вызов
`configure_runtime()` в `try/except Exception` - второй, более широкий рубеж
на случай сбоя, который предвидеть было нельзя. Оба замутированы отдельно и
оба ловятся тестами (см. ниже).

### I7. Второй запуск не поднимал окно; событий негде увидеть

- `single_instance_lock()` возвращает тот же `QLocalServer`; `main()`
  подключает `lock.newConnection` к `_raise_existing_window()`, которая
  вычитывает входящий сокет и поднимает окно (`showNormal/raise_/activateWindow`).
- `ClipboardPage` получила `events_list` (QListWidget, до 20 записей,
  `add_event()`), подключённый к `coordinator.event_logged`.

### I8. RSA vs ECDSA в спецификации

`docs/superpowers/specs/2026-09-03-shared-clipboard-design.md` §8 переписан:
RSA-2048 вместо ECDSA P-256, с причиной (Schannel не импортирует EC-ключ,
сгенерированный `cryptography`) и указанием, что `identity.py` активно
пересоздаёт идентичность при обнаружении старого EC-ключа.

## MINOR

- **M1**: `FETCH_TIMEOUT_MS` удалён вместе с упоминанием в спецификации §5 -
  задействовать честно было нельзя: содержимое приходит одним кадром `CONTENT`,
  а не потоком, у слоя нет границы между «начал отвечать» и «закончил».
- **M2**: докстринг `ClipboardService._fetch()` больше не утверждает, что
  обработка новых снимков подавлена на время ожидания - подавление
  (`_suspended`) стоит только вокруг `publish()`, который к моменту
  `retrieveData()` уже завершился.
- **M4**: `PeerListener._links` больше не растёт без границы - удаляется
  при `disconnected` (`_forget()`), проверено тестом на реальном TLS.
- **M5**: `test_switching_sharing_on_stops_the_program_quitting_with_the_window`
  переименован в `test_switching_sharing_on_with_the_real_checkbox_stops_quitting_with_the_window`
  и переписан так, чтобы включать фичу настоящим `sharing_checkbox.setChecked(True)`,
  а не прямой записью в `QSettings`.
- **M6**: локальный `import subprocess` в `test_dist.py` убран, остался только
  модульный.
- **M7**: `ClipboardOffer.from_dict()` теперь исключает `bool` для `seq` и
  `size` (bool - подтип int), тем же способом, что уже принят в
  `discovery.decode_beacon()`.

## Тесты

Новые/изменённые тестовые файлы: `test_coordinator.py`, `test_peer_link.py`,
`test_offer.py`, `test_service_rules.py`, `test_windows_backend.py`,
`test_autostart.py`, `test_runtime_wiring.py`, `test_dist.py`.

Полный прогон из корня:

```
.venv/Scripts/python.exe -m pytest configurator/tests -q -c configurator/pyproject.toml
```

Результат: **1031 passed, 4 skipped** (было 1010 passed, 4 skipped; +21 тест,
падений ноль).

## Мутационная проверка (обязательные C1, C2, I1, I2, I3, I7; дополнительно I4, I6, M4)

Каждая проверка: возврат правки к до-фикс поведению точечной правкой →
целевой тест красный → правка восстановлена → полный файл снова зелёный.

| Пункт | Мутация | Результат |
|---|---|---|
| C1 | убраны `sharing_toggled`/`autostart_toggled`.connect(...) в `configure_runtime` | 5 тестов упали (`test_switching_sharing_on_with_the_real_checkbox...`, `test_switching_off_actually_releases_the_listening_socket`, `test_the_tray_checkbox_toggle_stops...`, `test_toggling_autostart_on_the_page_writes_the_shortcut`, `test_identity_failure_degrades...`) |
| C2 | `set_autostart()` перестал звать `autostart.enable/disable` | `test_toggling_autostart_on_the_page_writes_the_shortcut` упал |
| I1 | убраны обе новые подписки `link.disconnected.connect(self._on_disconnected)` | `test_a_dropped_incoming_link_is_noticed...`, `test_a_dropped_link_from_a_finished_pairing_is_noticed` упали |
| I2a | убраны `_retry.stop()/_silence.stop()/_discovery.stop()/_attempt=0` из `forget_peer` | `test_forgetting_a_peer_stops_the_silence_watchdog_and_retry_timer` упал |
| I2b | убран guard `if peer is None: return` из `_try_connect` | `test_forgetting_a_peer_stops_the_beacon_from_announcing_forever`, `test_try_connect_refuses_to_search_without_a_trusted_peer` упали |
| I3 | убраны закрытие/детач прежней связи из `set_manual_address` | `test_setting_a_manual_address_while_connected_closes_the_previous_link` упал |
| I4 | убрана ветка `protocol_mismatch` в `_drop()` | `test_a_different_protocol_major_drops_the_link` упал |
| I6a | убран `try/except OSError` в `_start()` | `test_identity_failure_degrades_to_sharing_disabled_without_crashing` упал (и бросил необработанное исключение) |
| I6b | убран внешний `try/except Exception` вокруг `configure_runtime()` в `main()` | `test_main_survives_an_unexpected_configure_runtime_failure` упал |
| I7 | убраны `showNormal/raise_/activateWindow` из `_raise_existing_window` | `test_a_second_instance_connecting_raises_the_first_window` упал (таймаут) |
| M4 | убрана подписка на `disconnected` в `PeerListener._on_pending` | `test_the_listener_forgets_a_link_once_it_disconnects` упал (таймаут) |

После каждой проверки правка восстановлена и полный набор `configurator/tests/clipboard`
(и, для app.py, `configurator/tests/ui/test_runtime_wiring.py`) перепройден
зелёным.

## Что не успел / чего не хватает

- Персистентность `clipboard/manual_address` между запусками (см. C1) - вне
  буквального текста находки, оставлено как открытый вопрос.
- `clipboard/peer_origin_id` и `clipboard/files_enabled` из §12 спецификации
  по-прежнему не заведены ключами `QSettings` - доверенный пир целиком хранится
  в `peers.json` (`TrustStore`), а передача файлов вне milestone 1; не трогал,
  чтобы не расширять периметр правки.
- Мутационная проверка I5 и I8 не проводилась (не входят в обязательный список
  C1/C2/I1/I2/I3/I7); I5 покрыта обычными тестами с `caplog`.
