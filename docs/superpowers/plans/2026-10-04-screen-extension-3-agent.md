# Расширение экрана, план 3 из 3 — агент и интерфейс

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** приложение Duo Input на каждом компьютере сообщает своей плате касание края, ставит курсор при входе с соседнего компьютера и даёт оператору страницу «Расширение экрана». После плана мышь и клавиатура переходят между компьютерами через край экрана.

**Architecture:**
- **Чистая геометрия** (`screen/geometry.py`): обращённый край и перевод позиции в `t` и обратно.
- **Узкий интерфейс ОС** `DesktopProbe` с реализациями на ctypes (Windows) и pyobjc (macOS).
- **Связь с платой** `BoardEdgeLink`: через `DeviceService` (U1) на ПК1 и через общий для приложения `EndpointService` (U2) на ПК2.
- **Автомат** `EdgeAgent` (INACTIVE/FREE/CONTACT).
- **`ScreenRuntime`** держит общую раскладку (`QSettings` + блоб через плату) и связывает всё со страницей `ScreenPage`.
- **Прошивка** готова после плана 2; здесь она не меняется.

**Tech Stack:** Python 3.12, PySide6 (QtCore/QtGui/QtWidgets), ctypes (user32, shcore, shell32), pyobjc (AppKit, Quartz), pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-10-04-screen-extension-design.md` (раздел «Уточнения после чтения кода» главнее основного текста). **Предусловия:** план 2 выполнен (`edge_messages.py`, CDC 25–30 в прошивке и схеме); записка спайков `docs/superpowers/records/2026-10-04-screen-extension-spikes.md` существует. Задачи 6 и 7 читают её ответы W1–W3 и M1–M6.

## Global Constraints

- **Порог давления**, ступени «Нет / Низкое / Среднее / Высокое» = `(1, 40, 120, 300)` отсчётов; по умолчанию «Среднее» (индекс 2).
- **Агент:**

  | Параметр | Значение |
  | --- | --- |
  | Опрос курсора | 8 мс |
  | Повтор касания | 100 мс |
  | Обновление при смене `t` | не чаще 1/60 с |
  | Угловая зона | 10 логических единиц |
  | Отступ при входе | 2 логические единицы |
  | Обмен раскладкой | каждые 5000 мс и сразу после правки |
  | «Сосед молчит» | нет блоба соседа 15 с |

- **Блоб** (не больше 51 байта, little-endian):
  - `version u32, origin u64, flags u8 (bit0 включено, bit1 полноэкранная защита), resistance u8 (0..3), pc2_side u8 (EdgeSide: где ПК2 относительно ПК1), pc2_offset i16` — 17 байт;
  - затем `os u8 (1 Windows, 2 macOS), count u8 (0..4)` и `count × (x,y,w,h i16)` логических.
- **Роль** машины определяет её плата: подключена U1 — это ПК1, иначе U2 — ПК2. Обращённая сторона на ПК1 = `pc2_side`, на ПК2 = противоположная.
- **Ключи `QSettings`:** `screen/machine_id` (случайный u64, один раз), `screen/version`, `screen/origin`, `screen/enabled`, `screen/fullscreen_guard`, `screen/resistance`, `screen/pc2_side`, `screen/pc2_offset`.
- **Строки UI** — через `self.tr` с русским исходником, как на странице общего буфера, затем `tools/update_translations.py`. **Комментарии:** `screen/` и `ui/` — по-русски, `device/` — по-английски.
- **Тесты:**
  - только `.venv/Scripts/python.exe -m pytest`, решает код возврата;
  - UI-тесты нажимают настоящие виджеты;
  - ни один тест не открывает настоящий COM-порт (автофикстура `no_real_u2_link` в `configurator/tests/ui/conftest.py`).
- **Владельцы обработчиков.** Каждый `QObject`, чей метод подключён к сигналу, имеет Qt-родителя (память «PySide6 убивает обработчики без владельца»).
- **Проверка мутациями** — в Task 13.

## Review Focus

- **Курсор в зазоре между мониторами лесенкой** после отключения монитора. Агент перечитывает мониторы и не должен вычислить «касание» по старой геометрии. Тест Task 9 меняет мониторы фальшивого probe между опросами.
- **HANDOFF, пришедший, пока машина считает себя неактивной** (ROUTE_STATE ещё не дошёл). Курсор всё равно ставится: `HANDOFF` обслуживается в любом состоянии. Тест Task 9.
- **Push посреди записи конфигурации на ПК1.** `HANDOFF` не должен ронять `write_config` с `SEQUENCE_MISMATCH`. Тест Task 2.
- **Выключение общего буфера на ПК2 при включённом расширении.** Порт U2 не должен закрыться. Тест Task 3.
- **Правка раскладки на обоих ПК почти одновременно.** Обе стороны сходятся к одной версии. Тест Task 10.

## Карта файлов

| Файл | Что делает | Задача |
| --- | --- | --- |
| `configurator/src/duo_input/device/emulator.py` | уведомления, push, `EXCHANGE_LAYOUT`, `GET_EDGE_STATS` | 1 |
| `configurator/src/duo_input/device/service.py` | API края у `DeviceService` | 2 |
| `configurator/src/duo_input/device/endpoint_service.py`, `device/shared_endpoint.py` (новый), `app.py` | API края у `EndpointService`, общий порт U2 | 3 |
| `configurator/src/duo_input/screen/__init__.py`, `screen/geometry.py` (новые) | геометрия | 4 |
| `configurator/src/duo_input/screen/layout.py` (новый) | раскладка, блоб, настройки | 5 |
| `configurator/src/duo_input/screen/probe.py`, `screen/probe_windows.py` (новые) | ОС Windows | 6 |
| `configurator/src/duo_input/screen/probe_macos.py` (новый) | ОС macOS | 7 |
| `configurator/src/duo_input/screen/edge_link.py` (новый) | связь с платой | 8 |
| `configurator/src/duo_input/screen/edge_agent.py` (новый) | автомат агента | 9 |
| `configurator/src/duo_input/screen/runtime.py` (новый) | сборка подсистемы | 10 |
| `configurator/src/duo_input/ui/screen_page.py` (новый) | страница | 11 |
| `configurator/src/duo_input/ui/main_window.py`, `app.py`, `configurator/resources/translations/*` | проводка | 12 |
| `docs/superpowers/records/2026-10-04-screen-extension-agent.md` (новый) | итог | 13 |

---

### Task 1: Эмулятор U1 понимает расширение экрана

**Files:**
- Modify: `configurator/src/duo_input/device/emulator.py`
- Test: `configurator/tests/test_device_emulator.py`

**Interfaces:**
- Consumes: `duo_input.device.edge_messages` (план 2, Task 3).
- Produces (у `U1Emulator`):
  - атрибуты `edge_contacts: list[EdgeContact]`, `handoff_ready_ids: list[int]`, `edge_stats_payload: bytes` (42 байта);
  - свойство `local_layout -> bytes`;
  - методы `set_peer_layout(blob: bytes)`, `queue_handoff(Handoff)`, `queue_route_state(RouteState)`.
  - Очередь push выдаётся в начале ответа на следующий `feed` (любой, в том числе `b""`) и только если `SCREEN_EXTENSION` согласована. Уведомления не получают ответа и не трогают номер последовательности.

- [ ] **Step 1: Тесты.** Добавить в `configurator/tests/test_device_emulator.py`:

```python
import struct

from duo_input.device.edge_messages import (
    EdgeContact,
    Handoff,
    RouteState,
    decode_edge_stats,
    decode_handoff,
    encode_edge_contact,
    encode_handoff_ready,
)
from duo_input.device.emulator import U1Emulator
from duo_input.generated.protocol import (
    Capability,
    CdcMessageType,
    EdgeSide,
    ErrorCode,
    KeyboardRoute,
    MouseRoute,
    RouteReason,
)
from duo_input.protocol.frame import CdcFrame, decode_cdc_frame, encode_cdc_frame


def _hello(emulator, capabilities=None, sequence=0):
    caps = sum(int(c) for c in Capability) if capabilities is None else capabilities
    emulator.open()
    return emulator.exchange(CdcFrame(CdcMessageType.HELLO, sequence, struct.pack("<I", caps)))


def _frames(wire: bytes) -> list[CdcFrame]:
    frames, start = [], 0
    while True:
        end = wire.find(b"\0", start)
        if end < 0:
            return frames
        frames.append(decode_cdc_frame(wire[start : end + 1]))
        start = end + 1


_CONTACT = EdgeContact(gen=1, active=True, side=EdgeSide.RIGHT, neighbor=0, t=0x8000, threshold=120)


def test_edge_notifications_are_recorded_and_never_answered():
    emulator = U1Emulator()
    _hello(emulator)
    reply = emulator.feed(encode_cdc_frame(CdcFrame(CdcMessageType.EDGE_CONTACT, 0, encode_edge_contact(_CONTACT))))
    assert reply == b""
    reply = emulator.feed(encode_cdc_frame(CdcFrame(CdcMessageType.HANDOFF_READY, 0, encode_handoff_ready(7))))
    assert reply == b""
    assert emulator.edge_contacts == [_CONTACT]
    assert emulator.handoff_ready_ids == [7]
    # The next real request continues the sequence as if nothing came between.
    status = emulator.exchange(CdcFrame(CdcMessageType.GET_STATUS, 1, b""))
    assert status.payload[0] == ErrorCode.OK


def test_edge_notifications_before_the_capability_are_dropped():
    emulator = U1Emulator()
    _hello(emulator, capabilities=0)
    emulator.feed(encode_cdc_frame(CdcFrame(CdcMessageType.EDGE_CONTACT, 0, encode_edge_contact(_CONTACT))))
    assert emulator.edge_contacts == []


def test_pushes_lead_the_next_reply_at_sequence_zero():
    emulator = U1Emulator()
    _hello(emulator)
    emulator.queue_route_state(RouteState(MouseRoute.PC2, KeyboardRoute.PC2, RouteReason.EDGE))
    emulator.queue_handoff(Handoff(id=5, side=EdgeSide.LEFT, t=0x4000))
    frames = _frames(emulator.feed(b""))
    assert [f.type for f in frames] == [CdcMessageType.ROUTE_STATE, CdcMessageType.HANDOFF]
    assert all(f.sequence == 0 for f in frames)
    assert decode_handoff(bytes(frames[1].payload)).id == 5
    assert emulator.feed(b"") == b""


def test_the_layout_exchange_and_edge_stats():
    emulator = U1Emulator()
    _hello(emulator)
    emulator.set_peer_layout(b"\x09\x08")
    reply = emulator.exchange(CdcFrame(CdcMessageType.EXCHANGE_LAYOUT, 1, b"\x01\x02\x03"))
    assert reply.payload == bytes([ErrorCode.OK, 9, 8])
    assert emulator.local_layout == b"\x01\x02\x03"
    too_big = emulator.exchange(CdcFrame(CdcMessageType.EXCHANGE_LAYOUT, 2, bytes(52)))
    assert too_big.payload[0] == ErrorCode.BAD_SIZE
    emulator.edge_stats_payload = struct.pack("<10IH", *range(1, 11), 77)
    stats = emulator.exchange(CdcFrame(CdcMessageType.GET_EDGE_STATS, 3, b""))
    assert decode_edge_stats(bytes(stats.payload[1:])).max_ready_ms == 77
```

- [ ] **Step 2: Убедиться, что падает.**
  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/test_device_emulator.py -q -k "edge or push or layout"`
  Expected: FAIL (`AttributeError: edge_contacts`).

- [ ] **Step 3: Реализация.** В `emulator.py`:
  - импорт:

```python
from .edge_messages import (
    LAYOUT_BLOB_MAX_SIZE,
    EdgeContact,
    Handoff,
    RouteState,
    decode_edge_contact,
    decode_handoff_ready,
    encode_handoff,
    encode_route_state,
)
```

  - в `_FIXED_REQUEST_SIZES` добавить `CdcMessageType.GET_EDGE_STATS: 0,`; в `_REQUIRED_CAPABILITY` добавить `CdcMessageType.EXCHANGE_LAYOUT: Capability.SCREEN_EXTENSION,` и `CdcMessageType.GET_EDGE_STATS: Capability.SCREEN_EXTENSION,`;
  - константа модуля: `_EDGE_NOTIFICATIONS = (CdcMessageType.EDGE_CONTACT, CdcMessageType.HANDOFF_READY)`;
  - в `__init__` (рядом с `_local_addresses`):

```python
        # The screen extension, as config_service.cpp keeps it: notifications
        # are recorded, never answered; pushes wait for the next feed.
        self.edge_contacts: list[EdgeContact] = []
        self.handoff_ready_ids: list[int] = []
        self.edge_stats_payload = bytes(42)
        self._layout_local = b""
        self._layout_peer = b""
        self._pushes: list[bytes] = []
```

  - методы:

```python
    @property
    def local_layout(self) -> bytes:
        return self._layout_local

    def set_peer_layout(self, blob: bytes) -> None:
        self._layout_peer = bytes(blob)

    def queue_handoff(self, handoff: Handoff) -> None:
        self._pushes.append(encode_cdc_frame(CdcFrame(CdcMessageType.HANDOFF, 0, encode_handoff(handoff))))

    def queue_route_state(self, state: RouteState) -> None:
        self._pushes.append(
            encode_cdc_frame(CdcFrame(CdcMessageType.ROUTE_STATE, 0, encode_route_state(state)))
        )

    def _edge_agreed(self) -> bool:
        return bool((self._negotiated_capabilities or 0) & int(Capability.SCREEN_EXTENSION))

    def _on_edge_notification(self, frame: CdcFrame) -> None:
        if not self._edge_agreed():
            return
        try:
            if frame.type is CdcMessageType.EDGE_CONTACT:
                self.edge_contacts.append(decode_edge_contact(bytes(frame.payload)))
            else:
                self.handoff_ready_ids.append(decode_handoff_ready(bytes(frame.payload)))
        except PayloadError:
            return

    def _handle_exchange_layout(self, payload: bytes) -> bytes:
        if len(payload) > LAYOUT_BLOB_MAX_SIZE:
            return bytes((ErrorCode.BAD_SIZE,))
        if payload:
            self._layout_local = bytes(payload)
        return bytes((ErrorCode.OK,)) + self._layout_peer

    def _handle_get_edge_stats(self, payload: bytes) -> bytes:
        return bytes((ErrorCode.OK,)) + self.edge_stats_payload
```

  - в `feed`, сразу после `responses = bytearray()`:

```python
        if self._pushes and self._edge_agreed():
            for push in self._pushes:
                responses.extend(push)
        self._pushes.clear()
```

  - в цикле `feed`, сразу после успешного `frame, major = self._decode_request(request_wire)`:

```python
            if frame.type in _EDGE_NOTIFICATIONS:
                self._on_edge_notification(frame)
                continue
```

  - в `_dispatch` расширить кортеж «идут в другую сторону»: `(CdcMessageType.DEVICE_INFO, CdcMessageType.CAPTURE_EVENT, CdcMessageType.HANDOFF, CdcMessageType.ROUTE_STATE)`.

  Замечание: при создании эмулятора `_negotiated_capabilities` равен `None`, поэтому `_edge_agreed` использует `or 0`.

- [ ] **Step 4: Прогнать.**
  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/test_device_emulator.py configurator/tests/device -q; echo "exit=$?"`
  Expected: `exit=0`.

- [ ] **Step 5: Commit.**

```bash
git add configurator/src/duo_input/device/emulator.py configurator/tests/test_device_emulator.py
git commit -m "test(emulator): screen-extension notifications, pushes, layout and stats"
```

---

### Task 2: `DeviceService` — уведомления, push, тихий обмен раскладкой

**Files:**
- Modify: `configurator/src/duo_input/device/service.py`
- Test: `configurator/tests/device/test_device_service.py`

**Interfaces:**
- Produces (у `DeviceService`):
  - сигналы `handoff_received = Signal(object)` (`Handoff`), `route_state_received = Signal(object)` (`RouteState`), `peer_layout_received = Signal(bytes)`;
  - свойство `screen_extension_ready -> bool`;
  - методы `send_edge_contact(EdgeContact) -> bool`, `send_handoff_ready(int) -> bool`, `exchange_layout(bytes) -> bool`.

- [ ] **Step 1: Тесты.** Изучить начало `configurator/tests/device/test_device_service.py`: как там подключают эмулятор и ждут `READY`. Добавить тесты в том же стиле:

```python
from duo_input.device.edge_messages import EdgeContact, Handoff, RouteState
from duo_input.generated.protocol import EdgeSide, KeyboardRoute, MouseRoute, RouteReason


def _ready_service(qtbot, emulator):
    service = DeviceService(timeout_ms=500)
    with qtbot.waitSignal(service.operation_succeeded, timeout=2000):
        service.connect_device(emulator)
    return service


def test_edge_notifications_go_out_without_a_sequence_number(qtbot):
    emulator = U1Emulator()
    service = _ready_service(qtbot, emulator)
    assert service.screen_extension_ready
    contact = EdgeContact(1, True, EdgeSide.RIGHT, 0, 0x8000, 120)
    assert service.send_edge_contact(contact) is True
    assert service.send_handoff_ready(9) is True
    qtbot.waitUntil(lambda: emulator.edge_contacts == [contact] and emulator.handoff_ready_ids == [9])
    # A real operation afterwards is still in sequence.
    with qtbot.waitSignal(service.operation_succeeded, timeout=2000):
        service.get_diagnostics()


def test_a_push_during_an_operation_neither_fails_it_nor_is_lost(qtbot):
    emulator = U1Emulator()
    service = _ready_service(qtbot, emulator)
    handoffs, routes, failures = [], [], []
    service.handoff_received.connect(handoffs.append)
    service.route_state_received.connect(routes.append)
    service.operation_failed.connect(failures.append)
    emulator.queue_handoff(Handoff(4, EdgeSide.LEFT, 100))
    emulator.queue_route_state(RouteState(MouseRoute.PC1, KeyboardRoute.PC1, RouteReason.EDGE))
    # The pushes lead the reply to this request, as a real board can send them.
    with qtbot.waitSignal(service.operation_succeeded, timeout=2000):
        service.get_diagnostics()
    assert failures == []
    assert [h.id for h in handoffs] == [4]
    assert routes[0].reason == RouteReason.EDGE


def test_the_layout_exchange_is_a_quiet_operation(qtbot):
    emulator = U1Emulator()
    emulator.set_peer_layout(b"\x05\x06")
    service = _ready_service(qtbot, emulator)
    received, states = [], []
    service.peer_layout_received.connect(received.append)
    service.state_changed.connect(states.append)
    assert service.exchange_layout(b"\x01") is True
    qtbot.waitUntil(lambda: received == [b"\x05\x06"])
    assert emulator.local_layout == b"\x01"
    assert states == []


def test_an_operator_request_during_the_layout_exchange_waits_for_it(qtbot):
    emulator = U1Emulator()
    service = _ready_service(qtbot, emulator)
    failures = []
    service.operation_failed.connect(failures.append)
    assert service.exchange_layout(b"\x01") is True
    with qtbot.waitSignal(service.operation_succeeded, timeout=2000):
        service.get_diagnostics()
    assert failures == []


def test_nothing_is_sent_to_a_board_without_the_capability(qtbot, monkeypatch):
    import duo_input.device.emulator as emulator_module
    from duo_input.generated.protocol import Capability

    monkeypatch.setattr(
        emulator_module,
        "DEVICE_CAPABILITIES",
        emulator_module.DEVICE_CAPABILITIES & ~int(Capability.SCREEN_EXTENSION),
    )
    emulator = U1Emulator()
    service = _ready_service(qtbot, emulator)
    assert service.screen_extension_ready is False
    assert service.send_edge_contact(EdgeContact(1, True, EdgeSide.RIGHT, 0, 0, 1)) is False
    assert service.exchange_layout(b"\x01") is False
```

  Если в этом файле уже есть общий помощник подключения, использовать его вместо `_ready_service`.

- [ ] **Step 2: Убедиться, что падает.**
  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/device/test_device_service.py -q -k "edge or push or layout or capability"`
  Expected: FAIL.

- [ ] **Step 3: Реализация.** В `service.py`:
  - импорты: `import logging` и

```python
from .edge_messages import (
    EdgeContact,
    decode_handoff,
    decode_route_state,
    encode_edge_contact,
    encode_handoff_ready,
)
```

  - после констант операций:

```python
_EXCHANGE_LAYOUT = "exchange_layout"
#: Background chores: they never change ``state`` and never report through
#: ``operation_succeeded``/``operation_failed``; an operator's request that
#: arrives during one waits for it.
_QUIET = (_EXCHANGE_ADDRESSES, _EXCHANGE_LAYOUT)
#: Device-initiated and outside the request count (sequence 0).
_EDGE_PUSHES = (CdcMessageType.HANDOFF, CdcMessageType.ROUTE_STATE)

logger = logging.getLogger("duo_input.device.service")
```

  - сигналы в классе после `peer_addresses_received`:

```python
    handoff_received = Signal(object)
    route_state_received = Signal(object)
    peer_layout_received = Signal(bytes)
```

  - заменить три проверки `== _EXCHANGE_ADDRESSES` (в `_defer_behind_exchange`, `_on_link_lost`, `_finish_failure`) на `in _QUIET`;
  - новые методы после `_run_deferred`:

```python
    @property
    def screen_extension_ready(self) -> bool:
        info = self._device_info
        return (
            self.is_connected
            and info is not None
            and bool(info.capabilities & int(Capability.SCREEN_EXTENSION))
        )

    def send_edge_contact(self, contact: EdgeContact) -> bool:
        return self._notify(CdcMessageType.EDGE_CONTACT, encode_edge_contact(contact))

    def send_handoff_ready(self, identifier: int) -> bool:
        return self._notify(CdcMessageType.HANDOFF_READY, encode_handoff_ready(identifier))

    def _notify(self, frame_type: CdcMessageType, payload: bytes) -> bool:
        """A notification: sequence 0, no reply, outside the request queue.

        Written straight to the link whatever is pending. The board handles it
        ahead of its sequence check, so it cannot disturb a request in flight,
        and waiting behind a configuration write would make it useless.
        """
        if not self.screen_extension_ready:
            return False
        self._link.send(encode_cdc_frame(CdcFrame(frame_type, 0, payload)))
        return True

    def exchange_layout(self, blob: bytes) -> bool:
        """Hand the board this computer's layout blob and take the other's.

        A quiet chore, exactly like ``exchange_addresses``.
        """
        if self._link is None or not self._link.is_open:
            return False
        if self._operation is not None or self._pending is not None:
            return False
        if not self.screen_extension_ready:
            return False
        self._operation = _EXCHANGE_LAYOUT
        self._request(
            CdcMessageType.EXCHANGE_LAYOUT,
            CdcMessageType.EXCHANGE_LAYOUT,
            bytes(blob),
            self._on_layout_exchanged,
        )
        return True

    def _on_layout_exchanged(self, payload: bytes) -> None:
        self._operation = None
        self._run_deferred()
        self.peer_layout_received.emit(bytes(payload[1:]))

    def _on_edge_push(self, frame: CdcFrame) -> None:
        try:
            if frame.type is CdcMessageType.HANDOFF:
                self.handoff_received.emit(decode_handoff(bytes(frame.payload)))
            else:
                self.route_state_received.emit(decode_route_state(bytes(frame.payload)))
        except PayloadError as error:
            logger.warning("edge_push_dropped type=%s reason=%s", frame.type.name, error)
```

  - в начале `_handle_frame`, перед проверкой `CAPTURE_EVENT`:

```python
        if frame.type in _EDGE_PUSHES:
            # Outside the request count: it answers nothing pending and must
            # not be matched against what is.
            self._on_edge_push(frame)
            return
```

- [ ] **Step 4: Прогнать.**
  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/device configurator/tests/ui -q; echo "exit=$?"`
  Expected: `exit=0`.

- [ ] **Step 5: Commit.**

```bash
git add configurator/src/duo_input/device/service.py configurator/tests/device/test_device_service.py
git commit -m "feat(device): edge notifications, pushes and a quiet layout exchange on U1"
```

---

### Task 3: `EndpointService` и общий порт U2

**Files:**
- Modify: `configurator/src/duo_input/device/endpoint_service.py`
- Create: `configurator/src/duo_input/device/shared_endpoint.py`
- Modify: `configurator/src/duo_input/app.py` (`_ClipboardRuntime`, `configure_runtime`)
- Test: `configurator/tests/device/test_endpoint_service.py`, `configurator/tests/device/test_shared_endpoint.py` (новый), `configurator/tests/ui/test_runtime_wiring.py`

**Interfaces:**
- Produces (у `EndpointService`):
  - сигналы `handoff_received`, `route_state_received`, `peer_layout_received` (как у `DeviceService`);
  - свойство `screen_extension_ready`;
  - методы `ensure_open() -> bool`, `send_edge_contact`, `send_handoff_ready`, `exchange_layout`.
  - HELLO просит `ADDRESS_EXCHANGE | SCREEN_EXTENSION`. `unsupported` = не дано ни то, ни другое.
- Produces: `class SharedEndpoint(QObject)` с методами `acquire(owner) -> EndpointService` и `release(owner) -> None`. Последний `release` вызывает `stop()` и `deleteLater()`.

- [ ] **Step 1: Тесты `EndpointService`.** В `configurator/tests/device/test_endpoint_service.py`:
  - тест `test_a_board_without_the_capability_is_left_alone` поправить: убирать из `DEVICE_CAPABILITIES` **обе** возможности:

```python
        emulator_module.DEVICE_CAPABILITIES
        & ~int(Capability.ADDRESS_EXCHANGE)
        & ~int(Capability.SCREEN_EXTENSION),
```

  - добавить:

```python
from duo_input.device.edge_messages import EdgeContact, Handoff, RouteState
from duo_input.generated.protocol import (
    Capability,
    EdgeSide,
    KeyboardRoute,
    MouseRoute,
    RouteReason,
)


def test_ensure_open_negotiates_the_screen_extension(qtbot):
    emulator = U1Emulator()
    service, links = _service_with(emulator, qtbot)
    assert service.ensure_open() is True
    qtbot.waitUntil(lambda: service.screen_extension_ready)
    assert len(links) == 1
    assert service.ensure_open() is True
    assert len(links) == 1


def test_u2_notifications_and_pushes(qtbot):
    emulator = U1Emulator()
    service, links = _service_with(emulator, qtbot)
    service.ensure_open()
    qtbot.waitUntil(lambda: service.screen_extension_ready)
    contact = EdgeContact(2, True, EdgeSide.LEFT, 0, 10, 40)
    assert service.send_edge_contact(contact)
    assert service.send_handoff_ready(3)
    qtbot.waitUntil(lambda: emulator.edge_contacts == [contact] and emulator.handoff_ready_ids == [3])

    handoffs, routes = [], []
    service.handoff_received.connect(handoffs.append)
    service.route_state_received.connect(routes.append)
    emulator.queue_handoff(Handoff(8, EdgeSide.LEFT, 1))
    emulator.queue_route_state(RouteState(MouseRoute.PC2, KeyboardRoute.PC2, RouteReason.EDGE))
    links[0].poll()
    qtbot.waitUntil(lambda: [h.id for h in handoffs] == [8] and len(routes) == 1)


def test_u2_layout_exchange(qtbot):
    emulator = U1Emulator()
    emulator.set_peer_layout(b"\x07")
    service, _ = _service_with(emulator, qtbot)
    received = []
    service.peer_layout_received.connect(received.append)
    service.ensure_open()
    qtbot.waitUntil(lambda: service.screen_extension_ready)
    assert service.exchange_layout(b"\x02\x03")
    qtbot.waitUntil(lambda: received == [b"\x07"])
    assert emulator.local_layout == b"\x02\x03"


def test_a_board_with_only_the_screen_extension_is_still_used(qtbot, monkeypatch):
    import duo_input.device.emulator as emulator_module

    monkeypatch.setattr(
        emulator_module,
        "DEVICE_CAPABILITIES",
        emulator_module.DEVICE_CAPABILITIES & ~int(Capability.ADDRESS_EXCHANGE),
    )
    emulator = U1Emulator()
    service, _ = _service_with(emulator, qtbot)
    service.ensure_open()
    qtbot.waitUntil(lambda: service.screen_extension_ready)
    assert service.unsupported is False
    assert service.exchange_addresses(["10.0.0.2"]) is False
```

- [ ] **Step 2: Тесты `SharedEndpoint`.** `configurator/tests/device/test_shared_endpoint.py`:

```python
from PySide6.QtCore import QObject

from duo_input.device.shared_endpoint import SharedEndpoint


class _FakeEndpoint(QObject):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.stops = 0

    def stop(self) -> None:
        self.stops += 1


def test_one_service_for_every_owner_and_closed_only_by_the_last(qapp):
    shared = SharedEndpoint(factory=_FakeEndpoint)
    clipboard, screen = object(), object()
    first = shared.acquire(clipboard)
    assert shared.acquire(screen) is first
    shared.release(clipboard)
    assert first.stops == 0
    shared.release(screen)
    assert first.stops == 1
    # A new owner after that gets a fresh service.
    assert shared.acquire(clipboard) is not first


def test_releasing_twice_or_an_unknown_owner_is_harmless(qapp):
    shared = SharedEndpoint(factory=_FakeEndpoint)
    owner = object()
    service = shared.acquire(owner)
    shared.release(owner)
    shared.release(owner)
    shared.release(object())
    assert service.stops == 1
```

- [ ] **Step 3: Тест проводки.** В `configurator/tests/ui/test_runtime_wiring.py` добавить:

```python
def test_switching_sharing_off_keeps_u2_open_for_another_owner(qtbot, qapp, tmp_path, monkeypatch):
    """The U2 port is one per application. The clipboard giving it up must
    not close it under the screen extension."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)
    shared = runtime._shared_endpoint
    other = object()
    endpoint = shared.acquire(other)
    stops: list[int] = []
    monkeypatch.setattr(endpoint, "stop", lambda: stops.append(1))

    window.clipboard_page.sharing_checkbox.setChecked(False)

    assert stops == []
    shared.release(other)
    assert stops == [1]
```

  Существующий `test_stop_calls_stop_on_both_exchange_and_endpoint` в этой задаче остаётся зелёным без правок: пока буфер — единственный владелец, `release` вызывает `stop()`. В Task 12 у порта появится второй владелец (расширение экрана), и этот тест меняется там.

- [ ] **Step 4: Убедиться, что падает.**
  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/device configurator/tests/ui/test_runtime_wiring.py -q -k "endpoint or shared or u2"`
  Expected: FAIL.

- [ ] **Step 5: `SharedEndpoint`.** `configurator/src/duo_input/device/shared_endpoint.py`:

```python
"""One U2 port for the whole application.

A serial port opens once. The clipboard's address exchange and the screen
extension both talk to U2, so neither may own the EndpointService outright:
each acquires it, and the last one to let go closes it.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject

from .endpoint_service import EndpointService


class SharedEndpoint(QObject):
    def __init__(
        self,
        factory: Callable[..., QObject] = EndpointService,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._factory = factory
        self._service = None
        self._owners: list[int] = []

    def acquire(self, owner: object):
        if self._service is None:
            self._service = self._factory(parent=self)
        if id(owner) not in self._owners:
            self._owners.append(id(owner))
        return self._service

    def release(self, owner: object) -> None:
        if id(owner) not in self._owners:
            return
        self._owners.remove(id(owner))
        if self._owners or self._service is None:
            return
        service, self._service = self._service, None
        service.stop()
        service.deleteLater()


__all__ = ["SharedEndpoint"]
```

- [ ] **Step 6: `EndpointService`.** В `endpoint_service.py`:
  - импорты:

```python
from .edge_messages import (
    EdgeContact,
    decode_handoff,
    decode_route_state,
    encode_edge_contact,
    encode_handoff_ready,
)
```

  - константы модуля:

```python
#: What PC2's program asks U2 for. Either alone makes the board worth keeping.
_REQUESTED = int(Capability.ADDRESS_EXCHANGE) | int(Capability.SCREEN_EXTENSION)
_PUSHES = (CdcMessageType.HANDOFF, CdcMessageType.ROUTE_STATE)
```

  - сигналы класса: `handoff_received = Signal(object)`, `route_state_received = Signal(object)`, `peer_layout_received = Signal(bytes)`;
  - в `__init__`: `self._granted = 0`;
  - в `_open` HELLO: `struct.pack("<I", _REQUESTED)`;
  - в `_close`: `self._granted = 0`;
  - заменить `_on_hello`:

```python
    def _on_hello(self, payload: bytes) -> None:
        info = parse_device_info(payload)
        granted = info.capabilities & _REQUESTED
        if info.protocol_major != PROTOCOL_VERSION_MAJOR or not granted:
            logger.warning("U2 supports neither address exchange nor the screen extension")
            self._unsupported = True
            self._close("")
            return
        self._granted = granted
        self._ready = True
        local, self._queued_local = self._queued_local, None
        if local is not None and granted & int(Capability.ADDRESS_EXCHANGE):
            self._send(CdcMessageType.EXCHANGE_ADDRESSES, encode_host_addresses(local))
```

  - в `exchange_addresses` после проверки `if not self._ready: return False` добавить:

```python
        if not self._granted & int(Capability.ADDRESS_EXCHANGE):
            return False
```

  - новые методы:

```python
    @property
    def screen_extension_ready(self) -> bool:
        return self._ready and bool(self._granted & int(Capability.SCREEN_EXTENSION))

    def ensure_open(self) -> bool:
        """Open U2 and negotiate if that has not happened. False: no board."""
        if self._unsupported:
            return False
        if self._link is None:
            return self._open()
        return True

    def send_edge_contact(self, contact: EdgeContact) -> bool:
        return self._notify(CdcMessageType.EDGE_CONTACT, encode_edge_contact(contact))

    def send_handoff_ready(self, identifier: int) -> bool:
        return self._notify(CdcMessageType.HANDOFF_READY, encode_handoff_ready(identifier))

    def _notify(self, frame_type: CdcMessageType, payload: bytes) -> bool:
        if not self.screen_extension_ready:
            return False
        self._link.send(encode_cdc_frame(CdcFrame(frame_type, 0, payload)))
        return True

    def exchange_layout(self, blob: bytes) -> bool:
        if not self.screen_extension_ready or self._pending is not None:
            return False
        self._send(CdcMessageType.EXCHANGE_LAYOUT, bytes(blob))
        return True
```

  - заменить `_on_frame`:

```python
    def _on_frame(self, frame: CdcFrame) -> None:
        if frame.type in _PUSHES:
            # Sequence 0, said without being asked: never matched against
            # what is pending.
            try:
                if frame.type is CdcMessageType.HANDOFF:
                    self.handoff_received.emit(decode_handoff(bytes(frame.payload)))
                else:
                    self.route_state_received.emit(decode_route_state(bytes(frame.payload)))
            except PayloadError as error:
                logger.warning("u2_push_dropped reason=%s", error)
            return
        if self._pending is None or frame.sequence != self._pending[0]:
            return
        if frame.type is not self._pending[1]:
            self._close(f"unexpected {frame.type.name}")
            return
        self._timer.stop()
        self._pending = None
        payload = bytes(frame.payload)
        try:
            if frame.type is CdcMessageType.DEVICE_INFO:
                self._on_hello(payload)
            elif frame.type is CdcMessageType.EXCHANGE_LAYOUT:
                if reply_error(payload) is ErrorCode.OK:
                    self.peer_layout_received.emit(payload[1:])
            else:
                self._on_exchange(payload)
        except PayloadError as error:
            self._close(str(error))
```

  - докстринг модуля поправить: U2 теперь говорит ещё и о крае экрана.

- [ ] **Step 7: Общий порт в `app.py`.**
  - импорт `from duo_input.device.shared_endpoint import SharedEndpoint`;
  - `_ClipboardRuntime.__init__` получает параметр `shared_endpoint: SharedEndpoint` и сохраняет `self._shared_endpoint = shared_endpoint`;
  - в `_start` заменить `self._endpoint = EndpointService(parent=self)` на `self._endpoint = self._shared_endpoint.acquire(self)`;
  - в `_stop` заменить блок

```python
        endpoint, self._endpoint = self._endpoint, None
        if endpoint is not None:
            endpoint.stop()
            endpoint.deleteLater()
```

  на

```python
        # Released, not stopped: the port may have another owner (the screen
        # extension), and the last owner to let go is the one that closes it.
        if self._endpoint is not None:
            self._endpoint = None
            self._shared_endpoint.release(self)
```

  - в `configure_runtime` перед созданием `_ClipboardRuntime`:

```python
    # One U2 port for the whole application - see SharedEndpoint.
    shared_endpoint = SharedEndpoint(parent=application)
    runtime = _ClipboardRuntime(application, window, settings, shared_endpoint)
```

- [ ] **Step 8: Прогнать.**
  Run: `.venv/Scripts/python.exe -m pytest configurator/tests -q; echo "exit=$?"`
  Expected: `exit=0`.

- [ ] **Step 9: Commit.**

```bash
git add configurator/src/duo_input/device/endpoint_service.py configurator/src/duo_input/device/shared_endpoint.py configurator/src/duo_input/app.py configurator/tests/device/test_endpoint_service.py configurator/tests/device/test_shared_endpoint.py configurator/tests/ui/test_runtime_wiring.py
git commit -m "feat(device): U2 speaks the screen edge; one U2 port shared by its owners"
```

---

### Task 4: Геометрия края

**Files:**
- Create: `configurator/src/duo_input/screen/__init__.py` (пустой докстринг), `configurator/src/duo_input/screen/geometry.py`
- Create: `configurator/tests/screen/__init__.py` (пустой), `configurator/tests/screen/test_geometry.py`

**Interfaces:**
- Produces (`duo_input.screen.geometry`):
  - `T_MAX = 0xFFFF`, `CORNER_ZONE = 10.0`, `ENTRY_INSET = 2.0`;
  - `@dataclass(frozen=True) Display(x: float, y: float, width: float, height: float, scale: float = 1.0)` со свойствами `right`, `bottom`;
  - `@dataclass(frozen=True) Segment(side: EdgeSide, line: float, start: float, end: float, scale: float)` со свойством `length`;
  - `facing_edge(displays, side) -> list[Segment]`;
  - `contact_t(segments, x, y) -> int | None` — `None` вне края или в угловой зоне;
  - `point_at(segments, t, inset=ENTRY_INSET) -> tuple[float, float] | None`;
  - `opposite(side) -> EdgeSide`;
  - `nearest_inside(displays, x, y) -> tuple[float, float]`.

Координаты — те, в которых ОС отдаёт курсор: Windows — физические пиксели виртуального экрана, macOS — точки CG. `line` — последняя координата **внутри** монитора на этой стороне: для правой стороны `right - 1`, для левой `x`, для верхней `y`, для нижней `bottom - 1`. Длины вдоль края — в логических единицах (натив / масштаб).

- [ ] **Step 1: Тесты.** `configurator/tests/screen/test_geometry.py`:

```python
import pytest

from duo_input.generated.protocol import EdgeSide
from duo_input.screen.geometry import (
    T_MAX,
    Display,
    contact_t,
    facing_edge,
    nearest_inside,
    opposite,
    point_at,
)

ONE = [Display(0, 0, 2560, 1440)]


def test_a_single_monitor_has_one_segment_per_side():
    right = facing_edge(ONE, EdgeSide.RIGHT)
    assert len(right) == 1
    assert right[0].line == 2559
    assert (right[0].start, right[0].end) == (0, 1440)


def test_the_example_from_the_spec_maps_half_height_to_half_height():
    t = contact_t(facing_edge(ONE, EdgeSide.RIGHT), 2559, 720)
    assert t == round(T_MAX * 720 / 1440)
    other = [Display(0, 0, 1920, 1080)]
    x, y = point_at(facing_edge(other, EdgeSide.LEFT), t)
    assert x == 2
    assert y == pytest.approx(540, abs=1)


def test_scale_does_not_change_the_fraction():
    hidpi = [Display(0, 0, 1920, 1080, scale=1.5)]
    x, y = point_at(facing_edge(hidpi, EdgeSide.LEFT), T_MAX // 2)
    assert y == pytest.approx(540, abs=1)
    assert x == pytest.approx(3)  # two logical units at 150 %


def test_the_shared_edge_of_side_by_side_monitors_is_not_outer():
    pair = [Display(0, 0, 1920, 1080), Display(1920, 0, 1920, 1080)]
    right = facing_edge(pair, EdgeSide.RIGHT)
    assert [s.line for s in right] == [3839]
    assert contact_t(right, 1919, 500) is None


def test_stacked_monitors_make_one_long_edge_by_logical_length():
    # A 2880x1800 laptop at 200 % (1440x900 logical) above a 1080p at 100 %.
    stack = [Display(0, 0, 2880, 1800, scale=2.0), Display(0, 1800, 1920, 1080)]
    right = facing_edge(stack, EdgeSide.RIGHT)
    assert len(right) == 2
    total = sum(s.length for s in right)
    assert total == pytest.approx(900 + 1080)
    # The junction is 900 / 1980 of the way down.
    t = contact_t(right, 1919, 1800 + 540)
    assert t == round(T_MAX * (900 + 540) / 1980)


def test_a_staggered_edge_skips_the_part_another_monitor_covers():
    # B sits to the right of A, lower: A's right edge is outer only above B.
    stagger = [Display(0, 0, 1920, 1080), Display(1920, 540, 1920, 1080)]
    right_of_a = [s for s in facing_edge(stagger, EdgeSide.RIGHT) if s.line == 1919]
    assert len(right_of_a) == 1
    assert (right_of_a[0].start, right_of_a[0].end) == (0, 540)


def test_corners_are_not_contacts():
    right = facing_edge(ONE, EdgeSide.RIGHT)
    assert contact_t(right, 2559, 5) is None
    assert contact_t(right, 2559, 1436) is None
    assert contact_t(right, 2559, 15) is not None


def test_a_pointer_off_the_edge_line_is_not_a_contact():
    right = facing_edge(ONE, EdgeSide.RIGHT)
    assert contact_t(right, 2557, 720) is None


@pytest.mark.parametrize("side", list(EdgeSide))
def test_every_side_round_trips(side):
    segments = facing_edge(ONE, side)
    x, y = point_at(segments, T_MAX // 3, inset=0)
    assert contact_t(segments, x, y) == pytest.approx(T_MAX // 3, abs=40)


def test_the_entry_point_is_inside_and_off_the_edge():
    x, y = point_at(facing_edge(ONE, EdgeSide.TOP), 0)
    assert y == 2
    assert 0 <= x < 2560


def test_no_monitors_no_edge():
    assert facing_edge([], EdgeSide.RIGHT) == []
    assert point_at([], 100) is None


def test_opposite_sides():
    assert opposite(EdgeSide.LEFT) == EdgeSide.RIGHT
    assert opposite(EdgeSide.BOTTOM) == EdgeSide.TOP


def test_a_point_outside_every_monitor_is_brought_to_the_nearest():
    pair = [Display(0, 0, 1920, 1080), Display(1920, 540, 1920, 1080)]
    assert nearest_inside(pair, 2500, 100) == (2500, 540)
    assert nearest_inside(pair, 100, 100) == (100, 100)
```

- [ ] **Step 2: Убедиться, что падает.**
  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/screen/test_geometry.py -q`
  Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Реализация.** `configurator/src/duo_input/screen/__init__.py`:

```python
"""Расширение экрана: курсор переходит на второй компьютер через край."""
```

  `configurator/src/duo_input/screen/geometry.py`:

```python
"""Края экранов одной машины и перевод позиции вдоль края в долю t и обратно.

Чистая геометрия, без ОС. Координаты - те, в которых ОС отдаёт курсор:
Windows - физические пиксели виртуального экрана, macOS - точки CG. Длины
вдоль края считаются в логических единицах (натив / масштаб), чтобы монитор
4K@150% и 1080p@100% весили по своему видимому размеру.

Обращённый край стороны - внешние отрезки мониторов на этой стороне: шаг на
одну единицу наружу не попадает ни в один монитор той же машины. t - доля
длины дуги вдоль этих отрезков, 0..T_MAX; разрывы между отрезками
выбрасываются. Так край одной машины целиком ложится на край другой, и
второй машине не нужна чужая геометрия - только сторона и t.
"""

from __future__ import annotations

from dataclasses import dataclass

from duo_input.generated.protocol import EdgeSide

T_MAX = 0xFFFF
#: Возле концов отрезка касание не сообщается: туда бросают курсор за
#: «Пуском», горячими углами и кнопкой «закрыть» развёрнутого окна.
CORNER_ZONE = 10.0
#: Насколько внутрь от края ставится курсор при входе - чтобы он не
#: оказался сразу в касании с краем, через который пришёл.
ENTRY_INSET = 2.0

_VERTICAL = (EdgeSide.LEFT, EdgeSide.RIGHT)


@dataclass(frozen=True)
class Display:
    x: float
    y: float
    width: float
    height: float
    scale: float = 1.0

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height


@dataclass(frozen=True)
class Segment:
    side: EdgeSide
    #: Последняя координата внутри монитора на этой стороне.
    line: float
    #: Интервал вдоль края, [start, end), в нативных единицах.
    start: float
    end: float
    scale: float

    @property
    def length(self) -> float:
        return (self.end - self.start) / self.scale


def opposite(side: EdgeSide) -> EdgeSide:
    return {
        EdgeSide.LEFT: EdgeSide.RIGHT,
        EdgeSide.RIGHT: EdgeSide.LEFT,
        EdgeSide.TOP: EdgeSide.BOTTOM,
        EdgeSide.BOTTOM: EdgeSide.TOP,
    }[EdgeSide(side)]


def _subtract(intervals: list[tuple[float, float]], cut: tuple[float, float]) -> list[tuple[float, float]]:
    result = []
    for start, end in intervals:
        if cut[1] <= start or cut[0] >= end:
            result.append((start, end))
            continue
        if cut[0] > start:
            result.append((start, cut[0]))
        if cut[1] < end:
            result.append((cut[1], end))
    return result


def _edge_of(display: Display, side: EdgeSide) -> tuple[float, float, tuple[float, float]]:
    """(линия внутри, координата первой точки снаружи, интервал вдоль края)."""
    if side == EdgeSide.RIGHT:
        return display.right - 1, display.right, (display.y, display.bottom)
    if side == EdgeSide.LEFT:
        return display.x, display.x - 1, (display.y, display.bottom)
    if side == EdgeSide.BOTTOM:
        return display.bottom - 1, display.bottom, (display.x, display.right)
    return display.y, display.y - 1, (display.x, display.right)


def _covers(other: Display, side: EdgeSide, outside: float) -> tuple[float, float] | None:
    """Какую часть края другой монитор закрывает снаружи, если закрывает."""
    if side in _VERTICAL:
        if other.x <= outside < other.right:
            return other.y, other.bottom
        return None
    if other.y <= outside < other.bottom:
        return other.x, other.right
    return None


def facing_edge(displays: list[Display], side: EdgeSide) -> list[Segment]:
    side = EdgeSide(side)
    segments: list[Segment] = []
    for index, display in enumerate(displays):
        line, outside, span = _edge_of(display, side)
        intervals = [span]
        for other_index, other in enumerate(displays):
            if other_index == index:
                continue
            covered = _covers(other, side, outside)
            if covered is not None:
                intervals = _subtract(intervals, covered)
        segments.extend(
            Segment(side, line, start, end, display.scale) for start, end in intervals if end > start
        )
    segments.sort(key=lambda segment: (segment.start, segment.line))
    return segments


def _along_across(side: EdgeSide, x: float, y: float) -> tuple[float, float]:
    return (y, x) if side in _VERTICAL else (x, y)


def contact_t(segments: list[Segment], x: float, y: float) -> int | None:
    """t, если курсор лежит на обращённом крае вне угловых зон; иначе None."""
    if not segments:
        return None
    total = sum(segment.length for segment in segments)
    if total <= 0:
        return None
    offset = 0.0
    for segment in segments:
        along, across = _along_across(segment.side, x, y)
        if abs(across - segment.line) < 1 and segment.start <= along < segment.end:
            into = (along - segment.start) / segment.scale
            if into < CORNER_ZONE or segment.length - into <= CORNER_ZONE:
                return None
            return round((offset + into) / total * T_MAX)
        offset += segment.length
    return None


def point_at(
    segments: list[Segment], t: int, inset: float = ENTRY_INSET
) -> tuple[float, float] | None:
    """Точка на крае для доли t, на ``inset`` логических единиц внутрь."""
    if not segments:
        return None
    total = sum(segment.length for segment in segments)
    target = max(0.0, min(1.0, t / T_MAX)) * total
    offset = 0.0
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if target <= offset + segment.length or last:
            into = max(0.0, min(target - offset, segment.length))
            along = min(segment.start + into * segment.scale, segment.end - 1)
            inward = inset * segment.scale
            if segment.side in (EdgeSide.RIGHT, EdgeSide.BOTTOM):
                across = segment.line - inward
            else:
                across = segment.line + inward
            return (across, along) if segment.side in _VERTICAL else (along, across)
        offset += segment.length
    return None


def nearest_inside(displays: list[Display], x: float, y: float) -> tuple[float, float]:
    """Сама точка, если она на каком-то мониторе; иначе ближайшая точка на мониторе."""
    best: tuple[float, float] | None = None
    best_distance = float("inf")
    for display in displays:
        cx = min(max(x, display.x), display.right - 1)
        cy = min(max(y, display.y), display.bottom - 1)
        distance = (cx - x) ** 2 + (cy - y) ** 2
        if distance < best_distance:
            best, best_distance = (cx, cy), distance
    return best if best is not None else (x, y)


__all__ = [
    "CORNER_ZONE",
    "ENTRY_INSET",
    "T_MAX",
    "Display",
    "Segment",
    "contact_t",
    "facing_edge",
    "nearest_inside",
    "opposite",
    "point_at",
]
```

- [ ] **Step 4: Прогнать.**
  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/screen -q; echo "exit=$?"`
  Expected: `exit=0`.

- [ ] **Step 5: Commit.**

```bash
git add configurator/src/duo_input/screen configurator/tests/screen
git commit -m "feat(screen): facing edges and the edge fraction t"
```

---

### Task 5: Раскладка, блоб и настройки

**Files:**
- Create: `configurator/src/duo_input/screen/layout.py`
- Create: `configurator/tests/screen/test_layout.py`

**Interfaces:**
- Consumes: `Display`, `opposite` (Task 4); `LAYOUT_BLOB_MAX_SIZE` (план 2); `PayloadError`.
- Produces (`duo_input.screen.layout`):
  - `THRESHOLDS = (1, 40, 120, 300)`, `OS_WINDOWS = 1`, `OS_MACOS = 2`;
  - `@dataclass(frozen=True) SharedLayout(version: int, origin: int, enabled: bool, fullscreen_guard: bool, resistance: int, pc2_side: EdgeSide, pc2_offset: int)`;
  - `@dataclass(frozen=True) DesktopSummary(os: int, displays: tuple[tuple[int, int, int, int], ...])`;
  - `encode_blob(layout, desktop) -> bytes`, `decode_blob(blob) -> tuple[SharedLayout, DesktopSummary]`;
  - `newer(a, b) -> bool`;
  - `summarize(displays, os_code) -> DesktopSummary`;
  - `machine_id(settings) -> int`, `load(settings) -> SharedLayout`, `save(settings, layout)`;
  - `edited(layout, settings, **changes) -> SharedLayout`;
  - `facing_side(layout, is_pc1: bool) -> EdgeSide`, `peer_side(layout, is_pc1: bool) -> EdgeSide`;
  - `side_for_peer(peer_is_on: EdgeSide, is_pc1: bool) -> EdgeSide` — перевод выбора на странице («сосед справа») в `pc2_side`.

- [ ] **Step 1: Тесты.** `configurator/tests/screen/test_layout.py`:

```python
import pytest
from PySide6.QtCore import QSettings

from duo_input.device.transactions import PayloadError
from duo_input.generated.protocol import EdgeSide
from duo_input.screen.geometry import Display
from duo_input.screen.layout import (
    OS_WINDOWS,
    DesktopSummary,
    SharedLayout,
    decode_blob,
    edited,
    encode_blob,
    facing_side,
    load,
    machine_id,
    newer,
    peer_side,
    save,
    side_for_peer,
    summarize,
)


def _settings(tmp_path):
    return QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)


LAYOUT = SharedLayout(7, 0x1122334455667788, True, True, 2, EdgeSide.RIGHT, -40)
DESKTOP = DesktopSummary(OS_WINDOWS, ((0, 0, 1920, 1080), (1920, 0, 1280, 1024)))


def test_a_blob_round_trips_and_fits_one_spi_frame():
    blob = encode_blob(LAYOUT, DESKTOP)
    assert len(blob) == 17 + 2 + 8 * 2
    assert decode_blob(blob) == (LAYOUT, DESKTOP)
    four = DesktopSummary(OS_WINDOWS, tuple((i, 0, 10, 10) for i in range(4)))
    assert len(encode_blob(LAYOUT, four)) == 51


@pytest.mark.parametrize("blob", [b"", bytes(16), bytes(17) + b"\x01\x05" + bytes(40)])
def test_broken_blobs_are_refused(blob):
    with pytest.raises(PayloadError):
        decode_blob(blob)


def test_newer_compares_version_then_origin():
    a = SharedLayout(3, 1, False, True, 2, EdgeSide.RIGHT, 0)
    assert newer(edited(a, None, version=4), a)
    assert newer(SharedLayout(3, 2, False, True, 2, EdgeSide.RIGHT, 0), a)
    assert not newer(a, a)


def test_summaries_are_logical_relative_and_at_most_four():
    displays = [Display(-1920, 0, 1920, 1080), Display(0, 0, 3840, 2160, 2.0)] + [
        Display(3840 + i * 100, 0, 100, 100) for i in range(4)
    ]
    summary = summarize(displays, OS_WINDOWS)
    assert len(summary.displays) == 4
    assert summary.displays[0] == (0, 0, 1920, 1080)
    assert summary.displays[1] == (1920, 0, 1920, 1080)


def test_settings_round_trip_and_the_machine_id_is_stable(tmp_path):
    settings = _settings(tmp_path)
    first = machine_id(settings)
    assert machine_id(settings) == first and first != 0
    default = load(settings)
    assert default.enabled is False and default.resistance == 2 and default.pc2_side == EdgeSide.RIGHT
    save(settings, LAYOUT)
    assert load(settings) == LAYOUT


def test_an_edit_takes_the_next_version_and_this_machines_origin(tmp_path):
    settings = _settings(tmp_path)
    changed = edited(LAYOUT, settings, enabled=False)
    assert changed.version == LAYOUT.version + 1
    assert changed.origin == machine_id(settings)
    assert changed.enabled is False


def test_each_computer_faces_the_other():
    layout = SharedLayout(1, 1, True, True, 2, EdgeSide.TOP, 0)
    assert facing_side(layout, is_pc1=True) == EdgeSide.TOP
    assert facing_side(layout, is_pc1=False) == EdgeSide.BOTTOM
    assert peer_side(layout, is_pc1=False) == EdgeSide.BOTTOM
    assert side_for_peer(EdgeSide.LEFT, is_pc1=True) == EdgeSide.LEFT
    assert side_for_peer(EdgeSide.LEFT, is_pc1=False) == EdgeSide.RIGHT
```

- [ ] **Step 2: Убедиться, что падает.**
  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/screen/test_layout.py -q`
  Expected: FAIL.

- [ ] **Step 3: Реализация.** `configurator/src/duo_input/screen/layout.py`:

```python
"""Общая раскладка пары и блоб, которым компьютеры обмениваются через платы.

Раскладка одна на пару: где ПК2 относительно ПК1, включено ли, какое
сопротивление края. Побеждает старшая пара (version, origin): правка на любом
компьютере берёт следующую версию и свой идентификатор, и обе стороны сходятся
к одному. Описание мониторов у каждой машины своё и нужно только для рисования.
Платы блоб не читают.
"""

from __future__ import annotations

import random
import struct
from dataclasses import dataclass, replace

from PySide6.QtCore import QSettings

from duo_input.device.edge_messages import LAYOUT_BLOB_MAX_SIZE
from duo_input.device.transactions import PayloadError
from duo_input.generated.protocol import EdgeSide

from .geometry import Display, opposite

THRESHOLDS = (1, 40, 120, 300)
OS_WINDOWS = 1
OS_MACOS = 2
MAX_DISPLAYS = 4

_HEAD = struct.Struct("<IQBBBh")
_DESKTOP_HEAD = struct.Struct("<BB")
_RECT = struct.Struct("<hhhh")
_FLAG_ENABLED = 0x01
_FLAG_FULLSCREEN = 0x02


@dataclass(frozen=True)
class SharedLayout:
    version: int
    origin: int
    enabled: bool
    fullscreen_guard: bool
    resistance: int
    pc2_side: EdgeSide
    pc2_offset: int


@dataclass(frozen=True)
class DesktopSummary:
    os: int
    displays: tuple[tuple[int, int, int, int], ...]


def encode_blob(layout: SharedLayout, desktop: DesktopSummary) -> bytes:
    flags = (_FLAG_ENABLED if layout.enabled else 0) | (_FLAG_FULLSCREEN if layout.fullscreen_guard else 0)
    rects = desktop.displays[:MAX_DISPLAYS]
    blob = _HEAD.pack(
        layout.version & 0xFFFFFFFF,
        layout.origin & 0xFFFFFFFFFFFFFFFF,
        flags,
        layout.resistance,
        int(layout.pc2_side),
        max(-32768, min(32767, layout.pc2_offset)),
    )
    blob += _DESKTOP_HEAD.pack(desktop.os, len(rects))
    blob += b"".join(_RECT.pack(*(max(-32768, min(32767, v)) for v in rect)) for rect in rects)
    assert len(blob) <= LAYOUT_BLOB_MAX_SIZE
    return blob


def decode_blob(blob: bytes) -> tuple[SharedLayout, DesktopSummary]:
    if len(blob) < _HEAD.size + _DESKTOP_HEAD.size:
        raise PayloadError(f"layout blob is {len(blob)} bytes")
    version, origin, flags, resistance, side, offset = _HEAD.unpack_from(blob)
    if side not in {int(s) for s in EdgeSide} or resistance >= len(THRESHOLDS):
        raise PayloadError("layout blob names no side or an unknown resistance")
    os_code, count = _DESKTOP_HEAD.unpack_from(blob, _HEAD.size)
    if count > MAX_DISPLAYS or len(blob) != _HEAD.size + _DESKTOP_HEAD.size + count * _RECT.size:
        raise PayloadError(f"layout blob claims {count} displays in {len(blob)} bytes")
    base = _HEAD.size + _DESKTOP_HEAD.size
    rects = tuple(_RECT.unpack_from(blob, base + i * _RECT.size) for i in range(count))
    layout = SharedLayout(
        version, origin, bool(flags & _FLAG_ENABLED), bool(flags & _FLAG_FULLSCREEN),
        resistance, EdgeSide(side), offset,
    )
    return layout, DesktopSummary(os_code, rects)


def newer(candidate: SharedLayout, current: SharedLayout) -> bool:
    return (candidate.version, candidate.origin) > (current.version, current.origin)


def summarize(displays: list[Display], os_code: int) -> DesktopSummary:
    if not displays:
        return DesktopSummary(os_code, ())
    left = min(d.x / d.scale for d in displays)
    top = min(d.y / d.scale for d in displays)
    rects = tuple(
        (
            round(d.x / d.scale - left),
            round(d.y / d.scale - top),
            round(d.width / d.scale),
            round(d.height / d.scale),
        )
        for d in displays[:MAX_DISPLAYS]
    )
    return DesktopSummary(os_code, rects)


def machine_id(settings: QSettings) -> int:
    value = int(settings.value("screen/machine_id", 0, type=int) or 0)
    if value == 0:
        value = random.getrandbits(63) | 1
        settings.setValue("screen/machine_id", value)
    return value


def load(settings: QSettings) -> SharedLayout:
    side = int(settings.value("screen/pc2_side", int(EdgeSide.RIGHT), type=int))
    return SharedLayout(
        version=int(settings.value("screen/version", 0, type=int)),
        origin=int(settings.value("screen/origin", 0, type=int)),
        enabled=bool(settings.value("screen/enabled", False, type=bool)),
        fullscreen_guard=bool(settings.value("screen/fullscreen_guard", True, type=bool)),
        resistance=int(settings.value("screen/resistance", 2, type=int)),
        pc2_side=EdgeSide(side) if side in {int(s) for s in EdgeSide} else EdgeSide.RIGHT,
        pc2_offset=int(settings.value("screen/pc2_offset", 0, type=int)),
    )


def save(settings: QSettings, layout: SharedLayout) -> None:
    settings.setValue("screen/version", layout.version)
    settings.setValue("screen/origin", layout.origin)
    settings.setValue("screen/enabled", layout.enabled)
    settings.setValue("screen/fullscreen_guard", layout.fullscreen_guard)
    settings.setValue("screen/resistance", layout.resistance)
    settings.setValue("screen/pc2_side", int(layout.pc2_side))
    settings.setValue("screen/pc2_offset", layout.pc2_offset)


def edited(layout: SharedLayout, settings: QSettings | None, **changes) -> SharedLayout:
    """Правка с этой машины: следующая версия и свой идентификатор."""
    origin = machine_id(settings) if settings is not None else layout.origin
    return replace(layout, version=layout.version + 1, origin=origin, **changes)


def facing_side(layout: SharedLayout, is_pc1: bool) -> EdgeSide:
    """Край этой машины, ведущий к соседу."""
    return layout.pc2_side if is_pc1 else opposite(layout.pc2_side)


def peer_side(layout: SharedLayout, is_pc1: bool) -> EdgeSide:
    """С какой стороны от этой машины нарисовать соседа."""
    return facing_side(layout, is_pc1)


def side_for_peer(peer_is_on: EdgeSide, is_pc1: bool) -> EdgeSide:
    """«Сосед справа от меня» - в терминах pc2_side."""
    return EdgeSide(peer_is_on) if is_pc1 else opposite(peer_is_on)


__all__ = [
    "MAX_DISPLAYS",
    "OS_MACOS",
    "OS_WINDOWS",
    "THRESHOLDS",
    "DesktopSummary",
    "SharedLayout",
    "decode_blob",
    "edited",
    "encode_blob",
    "facing_side",
    "load",
    "machine_id",
    "newer",
    "peer_side",
    "save",
    "side_for_peer",
    "summarize",
]
```

  `QSettings.value(..., type=int)` для 64-битного `machine_id`: `getrandbits(63)` держит значение в пределах знакового int64, который `QSettings` хранит без потерь.

- [ ] **Step 4: Прогнать.**
  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/screen -q; echo "exit=$?"`
  Expected: `exit=0`.

- [ ] **Step 5: Commit.**

```bash
git add configurator/src/duo_input/screen/layout.py configurator/tests/screen/test_layout.py
git commit -m "feat(screen): shared layout, its blob and its settings"
```

---

### Task 6: `DesktopProbe` и реализация для Windows

**Files:**
- Create: `configurator/src/duo_input/screen/probe.py`, `configurator/src/duo_input/screen/probe_windows.py`
- Create: `configurator/tests/screen/fake_probe.py`, `configurator/tests/screen/test_probe_windows.py`
- Modify: `configurator/tests/clipboard/test_boundaries.py` (`_CTYPES_ALLOWED_OUTSIDE_TRANSFER`)

**Interfaces:**
- Consumes: `Display` (Task 4); ответы W1–W3 из записки спайков.
- Produces:
  - `class DesktopProbe(Protocol)` с методами `os_code() -> int`, `displays() -> list[Display]`, `cursor() -> tuple[float, float] | None`, `warp(x, y) -> bool`, `suppressed(fullscreen_guard: bool) -> str | None`, `ready() -> str | None` (причина, по которой probe непригоден, например `"dpi_unaware"`; иначе `None`);
  - коды подавления: `"clip"`, `"hidden"`, `"fullscreen"`, `"unreadable"`;
  - `create_probe() -> DesktopProbe | None`;
  - `class FakeProbe` в тестах: изменяемые поля `displays_list`, `position`, `suppression`, `warps: list`, `warp_ok: bool`, `not_ready`.

- [ ] **Step 1: Фальшивый probe для всех тестов агента.** `configurator/tests/screen/fake_probe.py`:

```python
"""Probe без ОС: мониторы, курсор и подавление задаёт тест."""

from duo_input.screen.geometry import Display


class FakeProbe:
    def __init__(self, displays=None, position=(100.0, 100.0)) -> None:
        self.displays_list: list[Display] = list(displays or [Display(0, 0, 1920, 1080)])
        self.position: tuple[float, float] | None = position
        self.suppression: str | None = None
        self.not_ready: str | None = None
        self.warps: list[tuple[float, float]] = []
        self.warp_ok = True

    def os_code(self) -> int:
        return 1

    def displays(self):
        return list(self.displays_list)

    def cursor(self):
        return self.position

    def warp(self, x: float, y: float) -> bool:
        self.warps.append((x, y))
        if self.warp_ok:
            self.position = (x, y)
        return self.warp_ok

    def suppressed(self, fullscreen_guard: bool):
        return self.suppression

    def ready(self):
        return self.not_ready
```

- [ ] **Step 2: Тесты Windows-probe** (выполняются только на Windows). `configurator/tests/screen/test_probe_windows.py`:

```python
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows API")


def test_the_real_desktop_has_monitors_and_a_readable_cursor():
    from duo_input.screen.probe_windows import WindowsProbe

    probe = WindowsProbe()
    displays = probe.displays()
    assert displays, "no monitors enumerated"
    assert all(d.width > 0 and d.height > 0 and d.scale >= 1.0 for d in displays)
    position = probe.cursor()
    assert position is not None
    assert any(d.x <= position[0] < d.right and d.y <= position[1] < d.bottom for d in displays)


def test_a_warp_to_where_the_cursor_already_is_succeeds():
    from duo_input.screen.probe_windows import WindowsProbe

    probe = WindowsProbe()
    x, y = probe.cursor()
    assert probe.warp(x, y) is True
    assert probe.cursor() == (x, y)


def test_the_suppression_check_runs_and_returns_a_known_answer():
    from duo_input.screen.probe_windows import WindowsProbe

    assert WindowsProbe().suppressed(fullscreen_guard=True) in (None, "clip", "hidden", "fullscreen", "unreadable")
```

- [ ] **Step 3: Разрешить ctypes.** В `configurator/tests/clipboard/test_boundaries.py` в `_CTYPES_ALLOWED_OUTSIDE_TRANSFER` добавить строку и комментарий над кортежем:

```python
#: screen/probe_windows.py: позиция курсора, мониторы и их DPI, установка
#: курсора - user32/shcore без обёрток в стандартной библиотеке; ctypes только
#: в этом файле, всё остальное в screen/ работает через DesktopProbe.
```

```python
    "duo_input/screen/probe_windows.py",
```

- [ ] **Step 4: Убедиться, что падает.**
  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/screen/test_probe_windows.py -q`
  Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 5: Интерфейс.** `configurator/src/duo_input/screen/probe.py`:

```python
"""Что агенту нужно от операционной системы - и ничего больше.

Узкий интерфейс, чтобы весь остальной код расширения экрана проверялся без ОС:
тесты подставляют фальшивый probe. Координаты - те, в которых ОС отдаёт
курсор (см. geometry.py).
"""

from __future__ import annotations

import sys
from typing import Protocol

from .geometry import Display


class DesktopProbe(Protocol):
    def os_code(self) -> int: ...

    def displays(self) -> list[Display]: ...

    def cursor(self) -> tuple[float, float] | None:
        """None - позицию прочитать нельзя (экран блокировки, защищённый стол)."""

    def warp(self, x: float, y: float) -> bool: ...

    def suppressed(self, fullscreen_guard: bool) -> str | None:
        """Причина не сообщать касание сейчас: clip, hidden, fullscreen."""

    def ready(self) -> str | None:
        """Причина, по которой probe непригоден вовсе, или None."""


def create_probe() -> DesktopProbe | None:
    if sys.platform == "win32":
        from .probe_windows import WindowsProbe

        return WindowsProbe()
    if sys.platform == "darwin":
        from .probe_macos import MacProbe

        return MacProbe()
    return None


__all__ = ["DesktopProbe", "create_probe"]
```

- [ ] **Step 6: Реализация Windows.** `configurator/src/duo_input/screen/probe_windows.py`:

```python
"""DesktopProbe для Windows: user32, shcore и shell32 через ctypes.

Координаты - физические пиксели виртуального экрана. Так их отдаёт
GetCursorPos процессу с осознанностью Per-Monitor-V2 (Qt 6 включает её сам);
без неё Windows виртуализирует координаты, и края «плывут» на мониторах с
масштабом. Поэтому ready() проверяет режим и отказывается работать без него.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from .geometry import Display

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_shcore = ctypes.WinDLL("shcore")
_shell32 = ctypes.WinDLL("shell32")

_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
)


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class _CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hCursor", wintypes.HANDLE),
        ("ptScreenPos", wintypes.POINT),
    ]


_CURSOR_SHOWING = 0x00000001
_MDT_EFFECTIVE_DPI = 0
_SM_XVIRTUALSCREEN, _SM_YVIRTUALSCREEN = 76, 77
_SM_CXVIRTUALSCREEN, _SM_CYVIRTUALSCREEN = 78, 79
_QUNS_BUSY = 2
_QUNS_RUNNING_D3D_FULL_SCREEN = 3
_QUNS_PRESENTATION_MODE = 4
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

_user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
_user32.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]


class WindowsProbe:
    def os_code(self) -> int:
        return 1

    def ready(self) -> str | None:
        context = _user32.GetThreadDpiAwarenessContext()
        if not _user32.AreDpiAwarenessContextsEqual(context, _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return "dpi_unaware"
        return None

    def displays(self) -> list[Display]:
        found: list[Display] = []

        def collect(monitor, _hdc, _rect, _data):
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(info)
            if not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return True
            dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
            scale = 1.0
            if _shcore.GetDpiForMonitor(monitor, _MDT_EFFECTIVE_DPI, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
                scale = max(1.0, dpi_x.value / 96.0)
            r = info.rcMonitor
            found.append(Display(r.left, r.top, r.right - r.left, r.bottom - r.top, scale))
            return True

        callback = _MONITORENUMPROC(collect)
        _user32.EnumDisplayMonitors(None, None, callback, 0)
        return found

    def cursor(self) -> tuple[float, float] | None:
        point = wintypes.POINT()
        if not _user32.GetCursorPos(ctypes.byref(point)):
            return None
        return float(point.x), float(point.y)

    def warp(self, x: float, y: float) -> bool:
        return bool(_user32.SetCursorPos(int(round(x)), int(round(y))))

    def suppressed(self, fullscreen_guard: bool) -> str | None:
        if self.cursor() is None:
            return "unreadable"
        clip = wintypes.RECT()
        if _user32.GetClipCursor(ctypes.byref(clip)):
            left = _user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
            top = _user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
            width = _user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
            height = _user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)
            if (clip.left, clip.top, clip.right, clip.bottom) != (left, top, left + width, top + height):
                return "clip"
        info = _CURSORINFO()
        info.cbSize = ctypes.sizeof(info)
        if _user32.GetCursorInfo(ctypes.byref(info)) and not info.flags & _CURSOR_SHOWING:
            return "hidden"
        if fullscreen_guard:
            state = ctypes.c_int()
            if _shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0 and state.value in (
                _QUNS_BUSY,
                _QUNS_RUNNING_D3D_FULL_SCREEN,
                _QUNS_PRESENTATION_MODE,
            ):
                return "fullscreen"
        return None


__all__ = ["WindowsProbe"]
```

  **По результатам спайка:**
  - Если W1 = «нет» (собранный exe не PMv2), в `app.py` до создания `QApplication` добавить вызов `ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))` **в отдельной функции в `screen/probe_windows.py`** (`ensure_per_monitor_v2()`) и вызвать её из `main()`. Тест: после вызова `WindowsProbe().ready() is None`.
  - Если W2/W3 показали, что `GetCursorPos` на защищённом столе возвращает успех с замороженной позицией, `suppressed` дополнительно проверяет `OpenInputDesktop` (это уже не ctypes-вопрос плана). Записать решение в отчёт задачи.

- [ ] **Step 7: Прогнать.**
  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/screen configurator/tests/clipboard/test_boundaries.py -q; echo "exit=$?"`
  Expected: `exit=0`. На Windows три теста probe выполняются и проходят.

- [ ] **Step 8: Commit.**

```bash
git add configurator/src/duo_input/screen/probe.py configurator/src/duo_input/screen/probe_windows.py configurator/tests/screen/fake_probe.py configurator/tests/screen/test_probe_windows.py configurator/tests/clipboard/test_boundaries.py
git commit -m "feat(screen): desktop probe and its Windows implementation"
```

---

### Task 7: Реализация для macOS

**Files:**
- Create: `configurator/src/duo_input/screen/probe_macos.py`, `configurator/tests/screen/test_probe_macos.py`

**Interfaces:**
- Consumes: ответы M1–M6 из записки спайков. **Если M1 или M2 отрицательны, задачу не начинать:** вопрос возвращается к человеку, запасной путь (абсолютный HID на U2) требует отдельного дизайна.
- Produces: `class MacProbe` (тот же интерфейс `DesktopProbe`, `os_code() == 2`).

- [ ] **Step 1: Тесты** (только на macOS). `configurator/tests/screen/test_probe_macos.py`:

```python
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS API")


def test_screens_are_in_cg_points_with_their_scale():
    from duo_input.screen.probe_macos import MacProbe

    displays = MacProbe().displays()
    assert displays
    assert displays[0].x == 0 and displays[0].y == 0  # the main screen is the origin
    assert all(d.scale in (1.0, 2.0, 3.0) for d in displays)


def test_the_cursor_is_on_some_screen_and_a_warp_in_place_succeeds():
    from duo_input.screen.probe_macos import MacProbe

    probe = MacProbe()
    x, y = probe.cursor()
    assert any(d.x <= x <= d.right and d.y <= y <= d.bottom for d in probe.displays())
    assert probe.warp(x, y) is True
```

- [ ] **Step 2: Реализация.** `configurator/src/duo_input/screen/probe_macos.py`:

```python
"""DesktopProbe для macOS: AppKit и Quartz через pyobjc.

Координаты - глобальные точки CG: начало в левом верхнем углу главного экрана,
ось Y вниз. NSScreen и NSEvent.mouseLocation отдают координаты Cocoa (ось Y
вверх от низа главного экрана), поэтому они переворачиваются здесь, в одном
месте. Retina прозрачна: точки уже логические, масштаб - backingScaleFactor.

Разрешений не нужно: курсор читается и ставится без перехвата и инъекции
событий (спайк M1). После warp macOS по умолчанию глушит движение мыши на
~250 мс; интервал обнуляется при создании (спайк M2).
"""

from __future__ import annotations

import Quartz
from AppKit import NSEvent, NSScreen, NSWorkspace

from .geometry import Display


class MacProbe:
    def __init__(self) -> None:
        source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateCombinedSessionState)
        Quartz.CGEventSourceSetLocalEventsSuppressionInterval(source, 0.0)
        self._source = source

    def os_code(self) -> int:
        return 2

    def ready(self) -> str | None:
        return None

    def _main_height(self) -> float:
        return float(NSScreen.screens()[0].frame().size.height)

    def displays(self) -> list[Display]:
        main_height = self._main_height()
        result = []
        for screen in NSScreen.screens():
            frame = screen.frame()
            top = main_height - (frame.origin.y + frame.size.height)
            result.append(
                Display(
                    float(frame.origin.x),
                    float(top),
                    float(frame.size.width),
                    float(frame.size.height),
                    float(screen.backingScaleFactor()),
                )
            )
        return result

    def cursor(self) -> tuple[float, float] | None:
        location = NSEvent.mouseLocation()
        return float(location.x), self._main_height() - float(location.y)

    def warp(self, x: float, y: float) -> bool:
        error = Quartz.CGWarpMouseCursorPosition((x, y))
        Quartz.CGAssociateMouseAndMouseCursorPosition(True)
        return error == 0

    def suppressed(self, fullscreen_guard: bool) -> str | None:
        if not Quartz.CGCursorIsVisible():
            return "hidden"
        if fullscreen_guard and self._frontmost_is_fullscreen():
            return "fullscreen"
        return None

    def _frontmost_is_fullscreen(self) -> bool:
        application = NSWorkspace.sharedWorkspace().frontmostApplication()
        if application is None:
            return False
        pid = application.processIdentifier()
        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        )
        screens = [(d.x, d.y, d.width, d.height) for d in self.displays()]
        for window in windows or []:
            if window.get("kCGWindowOwnerPID") != pid or window.get("kCGWindowLayer") != 0:
                continue
            bounds = window.get("kCGWindowBounds") or {}
            rect = (bounds.get("X"), bounds.get("Y"), bounds.get("Width"), bounds.get("Height"))
            if rect in screens:
                return True
        return False


__all__ = ["MacProbe"]
```

  **По результатам спайка:**
  - Если M4 = «детект не работает», убрать проверку `CGCursorIsVisible` из `suppressed` и записать это в отчёт.
  - Если M3 показал смещение краёв на половину точки, отразить это в `geometry.contact_t`: допуск `abs(across - line) < 1` остаётся, а `Display` для Mac строится с `width - 0.5`. Записать фактическое решение.

- [ ] **Step 3: Прогнать на Mac (человек или агент на Mac-машине).**
  Run (на Mac): `.venv/bin/python -m pytest configurator/tests/screen -q; echo "exit=$?"`
  Expected: `exit=0`. На Windows тесты Mac пропускаются; проверить, что импорт `probe_macos` нигде не происходит вне `create_probe`.

- [ ] **Step 4: Commit.**

```bash
git add configurator/src/duo_input/screen/probe_macos.py configurator/tests/screen/test_probe_macos.py
git commit -m "feat(screen): desktop probe for macOS"
```

---

### Task 8: `BoardEdgeLink` — своя плата, U1 или U2

**Files:**
- Create: `configurator/src/duo_input/screen/edge_link.py`, `configurator/tests/screen/test_edge_link.py`

**Interfaces:**
- Consumes: API края `DeviceService` (Task 2) и `EndpointService` (Task 3).
- Produces: `class BoardEdgeLink(QObject)`:
  - сигналы `handoff_received(object)`, `route_received(object)`, `peer_blob_received(bytes)`;
  - `role() -> str | None` (`"pc1"`, `"pc2"` или `None`);
  - `board_state() -> str` (`"ready"`, `"no_board"`, `"unsupported"`);
  - `send_contact(EdgeContact) -> bool`, `send_ready(int) -> bool`, `exchange_layout(bytes) -> bool`;
  - `ensure_open() -> None`.
  - Правило: подключена U1 — работает только U1 (ПК1), U2 не ищется. Иначе работает U2.

- [ ] **Step 1: Тесты.**

```python
from PySide6.QtCore import QObject, Signal

from duo_input.device.edge_messages import EdgeContact, Handoff
from duo_input.generated.protocol import EdgeSide
from duo_input.screen.edge_link import BoardEdgeLink


class _Board(QObject):
    handoff_received = Signal(object)
    route_state_received = Signal(object)
    peer_layout_received = Signal(bytes)

    def __init__(self, connected=False, ready=False):
        super().__init__()
        self.is_connected = connected
        self.screen_extension_ready = ready
        self.unsupported = False
        self.sent: list = []
        self.opened = 0

    def send_edge_contact(self, contact):
        self.sent.append(contact)
        return self.screen_extension_ready

    def send_handoff_ready(self, identifier):
        self.sent.append(identifier)
        return self.screen_extension_ready

    def exchange_layout(self, blob):
        self.sent.append(blob)
        return self.screen_extension_ready

    def ensure_open(self):
        self.opened += 1
        return True


def test_a_connected_u1_makes_this_pc1_and_u2_is_left_alone(qapp):
    u1, u2 = _Board(connected=True, ready=True), _Board(ready=True)
    link = BoardEdgeLink(u1, u2)
    assert link.role() == "pc1"
    link.ensure_open()
    assert u2.opened == 0
    contact = EdgeContact(1, True, EdgeSide.RIGHT, 0, 1, 1)
    assert link.send_contact(contact)
    assert u1.sent == [contact] and u2.sent == []


def test_without_u1_this_is_pc2_through_u2(qapp):
    u1, u2 = _Board(), _Board(ready=True)
    link = BoardEdgeLink(u1, u2)
    link.ensure_open()
    assert u2.opened == 1
    assert link.role() == "pc2"
    assert link.send_ready(4)
    assert u2.sent == [4]


def test_no_board_no_role(qapp):
    link = BoardEdgeLink(_Board(), _Board())
    assert link.role() is None
    assert link.board_state() == "no_board"
    assert link.send_contact(EdgeContact(1, True, EdgeSide.RIGHT, 0, 1, 1)) is False


def test_a_board_without_the_extension_is_reported(qapp):
    link = BoardEdgeLink(_Board(connected=True, ready=False), _Board())
    assert link.role() is None
    assert link.board_state() == "unsupported"


def test_pushes_from_either_board_come_out_of_the_link(qapp):
    u1, u2 = _Board(connected=True, ready=True), _Board(ready=True)
    link = BoardEdgeLink(u1, u2)
    seen = []
    link.handoff_received.connect(seen.append)
    u1.handoff_received.emit(Handoff(1, EdgeSide.LEFT, 0))
    u2.handoff_received.emit(Handoff(2, EdgeSide.LEFT, 0))
    assert [h.id for h in seen] == [1, 2]
```

- [ ] **Step 2: Убедиться, что падает**, затем **Step 3: реализация** `configurator/src/duo_input/screen/edge_link.py`:

```python
"""Своя плата для расширения экрана: U1 на ПК1, U2 на ПК2.

Роль определяет то, какая плата подключена: подключена U1 - этот компьютер ПК1,
и U2 даже не ищется (стенд с обеими платами на одном компьютере - тоже ПК1).
Иначе - ПК2 через U2.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class BoardEdgeLink(QObject):
    handoff_received = Signal(object)
    route_received = Signal(object)
    peer_blob_received = Signal(bytes)

    def __init__(self, device_service, endpoint_service, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._u1 = device_service
        self._u2 = endpoint_service
        for board in (device_service, endpoint_service):
            board.handoff_received.connect(self.handoff_received)
            board.route_state_received.connect(self.route_received)
            board.peer_layout_received.connect(self.peer_blob_received)

    def _board(self):
        if self._u1.is_connected:
            return self._u1 if self._u1.screen_extension_ready else None
        return self._u2 if self._u2.screen_extension_ready else None

    def role(self) -> str | None:
        board = self._board()
        if board is None:
            return None
        return "pc1" if board is self._u1 else "pc2"

    def board_state(self) -> str:
        if self._board() is not None:
            return "ready"
        if self._u1.is_connected or getattr(self._u2, "unsupported", False):
            return "unsupported"
        return "no_board"

    def ensure_open(self) -> None:
        if not self._u1.is_connected:
            self._u2.ensure_open()

    def send_contact(self, contact) -> bool:
        board = self._board()
        return board is not None and board.send_edge_contact(contact)

    def send_ready(self, identifier: int) -> bool:
        board = self._board()
        return board is not None and board.send_handoff_ready(identifier)

    def exchange_layout(self, blob: bytes) -> bool:
        board = self._board()
        return board is not None and board.exchange_layout(blob)


__all__ = ["BoardEdgeLink"]
```

- [ ] **Step 4: Прогнать**: `.venv/Scripts/python.exe -m pytest configurator/tests/screen -q; echo "exit=$?"` → `exit=0`.

- [ ] **Step 5: Commit.**

```bash
git add configurator/src/duo_input/screen/edge_link.py configurator/tests/screen/test_edge_link.py
git commit -m "feat(screen): the board link - U1 on PC1, U2 on PC2"
```

---

### Task 9: `EdgeAgent` — автомат агента

**Files:**
- Create: `configurator/src/duo_input/screen/edge_agent.py`, `configurator/tests/screen/test_edge_agent.py`

**Interfaces:**
- Consumes: `geometry` (Task 4), `DesktopProbe`/`FakeProbe` (Task 6), `BoardEdgeLink` (Task 8), `EdgeContact`/`Handoff`/`RouteState` (план 2).
- Produces: `class EdgeAgent(QObject)`:
  - сигнал `status_changed(str)`;
  - `__init__(probe, link, clock=time.monotonic, parent=None)`;
  - `configure(enabled: bool, facing: EdgeSide | None, threshold: int, fullscreen_guard: bool)`;
  - `start()`, `stop()`, `poll()`;
  - слоты `on_route(RouteState)`, `on_handoff(Handoff)`, `refresh_displays()`;
  - свойство `status`.
  - Коды статуса: `"disabled"`, `"no_board"`, `"unsupported"`, `"probe:<reason>"`, `"idle"` (мышь не здесь), `"working"`, `"contact"`, а также причины подавления `"clip"`, `"hidden"`, `"fullscreen"`, `"unreadable"`.

- [ ] **Step 1: Тесты.** `configurator/tests/screen/test_edge_agent.py`:

```python
from PySide6.QtCore import QObject, Signal

from duo_input.device.edge_messages import Handoff, RouteState
from duo_input.generated.protocol import EdgeSide, KeyboardRoute, MouseRoute, RouteReason
from duo_input.screen.edge_agent import CONTACT_REFRESH_S, EdgeAgent
from duo_input.screen.geometry import T_MAX, Display

from .fake_probe import FakeProbe


class _Link(QObject):
    handoff_received = Signal(object)
    route_received = Signal(object)
    peer_blob_received = Signal(bytes)

    def __init__(self, role="pc1"):
        super().__init__()
        self._role = role
        self.contacts = []
        self.readies = []

    def role(self):
        return self._role

    def board_state(self):
        return "ready" if self._role else "no_board"

    def ensure_open(self):
        pass

    def send_contact(self, contact):
        self.contacts.append(contact)
        return True

    def send_ready(self, identifier):
        self.readies.append(identifier)
        return True

    def exchange_layout(self, blob):
        return True


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


ON_PC1 = RouteState(MouseRoute.PC1, KeyboardRoute.PC1, RouteReason.BOOT)
ON_PC2 = RouteState(MouseRoute.PC2, KeyboardRoute.PC2, RouteReason.EDGE)


def _agent(qapp, role="pc1", position=(1919.0, 540.0)):
    probe = FakeProbe([Display(0, 0, 1920, 1080)], position)
    link = _Link(role)
    clock = _Clock()
    agent = EdgeAgent(probe, link, clock=clock)
    agent.configure(enabled=True, facing=EdgeSide.RIGHT, threshold=120, fullscreen_guard=True)
    agent.on_route(ON_PC1 if role == "pc1" else ON_PC2)
    return agent, probe, link, clock


def test_a_pointer_on_the_facing_edge_reports_a_contact(qapp):
    agent, _, link, _ = _agent(qapp)
    agent.poll()
    assert len(link.contacts) == 1
    contact = link.contacts[0]
    assert contact.active and contact.side == EdgeSide.RIGHT and contact.threshold == 120
    assert contact.t == round(T_MAX * 540 / 1080)
    assert agent.status == "contact"


def test_the_contact_is_refreshed_and_not_flooded(qapp):
    agent, _, link, clock = _agent(qapp)
    agent.poll()
    clock.now += 0.008
    agent.poll()
    assert len(link.contacts) == 1
    clock.now += CONTACT_REFRESH_S
    agent.poll()
    assert len(link.contacts) == 2
    assert link.contacts[1].gen == link.contacts[0].gen


def test_leaving_the_edge_releases_and_returning_is_a_new_episode(qapp):
    agent, probe, link, clock = _agent(qapp)
    agent.poll()
    probe.position = (1000.0, 540.0)
    clock.now += 0.01
    agent.poll()
    assert link.contacts[-1].active is False
    probe.position = (1919.0, 300.0)
    clock.now += 0.01
    agent.poll()
    assert link.contacts[-1].active is True
    assert link.contacts[-1].gen == link.contacts[0].gen + 1


def test_nothing_is_reported_while_the_mouse_is_on_the_other_computer(qapp):
    agent, _, link, _ = _agent(qapp)
    agent.on_route(ON_PC2)
    agent.poll()
    assert link.contacts == []
    assert agent.status == "idle"


def test_a_suppressed_desktop_reports_nothing_and_says_why(qapp):
    agent, probe, link, _ = _agent(qapp)
    probe.suppression = "fullscreen"
    agent.poll()
    assert link.contacts == []
    assert agent.status == "fullscreen"


def test_a_handoff_places_the_pointer_and_answers_even_before_the_route_arrives(qapp):
    agent, probe, link, _ = _agent(qapp, role="pc2", position=(500.0, 500.0))
    agent.on_route(ON_PC1)  # this computer still thinks the mouse is elsewhere
    agent.on_handoff(Handoff(7, EdgeSide.LEFT, T_MAX // 2))
    assert probe.warps == [(2.0, probe.warps[0][1])]
    assert abs(probe.warps[0][1] - 540) <= 1
    assert link.readies == [7]


def test_a_failed_warp_is_not_answered(qapp):
    agent, probe, link, _ = _agent(qapp, role="pc2")
    probe.warp_ok = False
    agent.on_handoff(Handoff(7, EdgeSide.LEFT, 0))
    assert link.readies == []


def test_monitors_that_change_are_read_again(qapp):
    agent, probe, link, clock = _agent(qapp, position=(3839.0, 540.0))
    agent.poll()
    assert link.contacts == []  # 3839 is off the only monitor
    probe.displays_list = [Display(0, 0, 1920, 1080), Display(1920, 0, 1920, 1080)]
    agent.refresh_displays()
    clock.now += 0.01
    agent.poll()
    assert link.contacts and link.contacts[-1].active


def test_disabled_says_so_and_releases_a_held_contact(qapp):
    agent, _, link, _ = _agent(qapp)
    agent.poll()
    agent.configure(enabled=False, facing=EdgeSide.RIGHT, threshold=120, fullscreen_guard=True)
    agent.poll()
    assert link.contacts[-1].active is False
    assert agent.status == "disabled"


def test_no_board_is_a_status_not_an_error(qapp):
    agent, _, link, _ = _agent(qapp)
    link._role = None
    agent.poll()
    assert agent.status == "no_board"
```

- [ ] **Step 2: Убедиться, что падает**, затем **Step 3: реализация** `configurator/src/duo_input/screen/edge_agent.py`:

```python
"""Агент края: где курсор этой машины и что он делает у края.

Агент не решает, переходить ли, - это делает плата U1 по сырым отсчётам мыши.
Он только сообщает касание: курсор лежит на крае, ведущем к соседу, вне угловых
зон, и машина не подавлена (захват курсора, скрытый курсор, полный экран).
Касание повторяется, пока длится, - плата забывает его через 250 мс без
повтора, так что умерший агент не может перевести мышь.

HANDOFF обслуживается в любом состоянии, даже если машина ещё не знает, что
мышь пришла к ней: ROUTE_STATE может догнать его позже.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from duo_input.device.edge_messages import EdgeContact, Handoff, RouteState
from duo_input.generated.protocol import EdgeSide, MouseRoute

from .geometry import contact_t, facing_edge, nearest_inside, point_at

POLL_MS = 8
CONTACT_REFRESH_S = 0.1
T_THROTTLE_S = 1 / 60
DISPLAY_REFRESH_S = 2.0

logger = logging.getLogger("duo_input.screen.agent")


class EdgeAgent(QObject):
    status_changed = Signal(str)

    def __init__(
        self,
        probe,
        link,
        clock: Callable[[], float] = time.monotonic,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._probe = probe
        self._link = link
        self._clock = clock
        self._enabled = False
        self._facing: EdgeSide | None = None
        self._threshold = 120
        self._fullscreen_guard = True
        self._route: RouteState | None = None
        self._displays = probe.displays()
        self._displays_at = clock()
        self._segments = []
        self._gen = 0
        self._contact = False
        self._last_t = -1
        self._last_sent = -1.0
        self._status = ""
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.poll)
        link.handoff_received.connect(self.on_handoff)
        link.route_received.connect(self.on_route)

    @property
    def status(self) -> str:
        return self._status

    def configure(self, enabled: bool, facing: EdgeSide | None, threshold: int, fullscreen_guard: bool) -> None:
        self._enabled = enabled
        self._facing = facing
        self._threshold = max(1, int(threshold))
        self._fullscreen_guard = fullscreen_guard
        self._segments = facing_edge(self._displays, facing) if facing is not None else []

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._release()

    def refresh_displays(self) -> None:
        self._displays = self._probe.displays()
        self._displays_at = self._clock()
        self._segments = facing_edge(self._displays, self._facing) if self._facing is not None else []
        position = self._probe.cursor()
        if position is not None and self._displays:
            inside = nearest_inside(self._displays, *position)
            if inside != position:
                # Монитор, на котором был курсор, исчез: вернуть курсор на экран.
                self._probe.warp(*inside)

    def on_route(self, route: RouteState) -> None:
        self._route = route

    def on_handoff(self, handoff: Handoff) -> None:
        segments = facing_edge(self._probe.displays(), EdgeSide(handoff.side))
        point = point_at(segments, handoff.t)
        if point is None:
            logger.warning("handoff_unplaced id=%s reason=no_edge", handoff.id)
            return
        if not self._probe.warp(*point):
            logger.warning("handoff_unplaced id=%s reason=warp_failed", handoff.id)
            return
        logger.info("handoff_placed id=%s side=%s t=%s at=%s", handoff.id, handoff.side, handoff.t, point)
        # Курсор теперь не на крае: следующее касание - новый эпизод.
        self._contact = False
        self._link.send_ready(handoff.id)

    # --------------------------------------------------------------- опрос

    def poll(self) -> None:
        now = self._clock()
        if now - self._displays_at >= DISPLAY_REFRESH_S:
            self.refresh_displays()
        role = self._link.role()
        if not self._enabled or self._facing is None:
            self._release()
            self._set_status("disabled")
            return
        if role is None:
            self._release()
            self._set_status(self._link.board_state())
            return
        not_ready = self._probe.ready()
        if not_ready is not None:
            self._release()
            self._set_status(f"probe:{not_ready}")
            return
        if not self._mouse_is_here(role):
            self._release()
            self._set_status("idle")
            return
        reason = self._probe.suppressed(self._fullscreen_guard)
        position = self._probe.cursor() if reason is None else None
        if reason is not None or position is None:
            self._release()
            self._set_status(reason or "unreadable")
            return
        t = contact_t(self._segments, *position)
        if t is None:
            self._release()
            self._set_status("working")
            return
        if not self._contact:
            self._gen = (self._gen + 1) & 0xFFFF
            self._contact = True
            self._send(t, now)
        elif now - self._last_sent >= CONTACT_REFRESH_S or (
            t != self._last_t and now - self._last_sent >= T_THROTTLE_S
        ):
            self._send(t, now)
        self._set_status("contact")

    def _mouse_is_here(self, role: str) -> bool:
        if self._route is None:
            return False
        mine = MouseRoute.PC1 if role == "pc1" else MouseRoute.PC2
        return self._route.mouse == mine

    def _send(self, t: int, now: float) -> None:
        self._link.send_contact(
            EdgeContact(self._gen, True, int(self._facing), 0, t, self._threshold)
        )
        self._last_t = t
        self._last_sent = now

    def _release(self) -> None:
        if not self._contact:
            return
        self._contact = False
        side = int(self._facing) if self._facing is not None else 0
        self._link.send_contact(EdgeContact(self._gen, False, side, 0, 0, 0))

    def _set_status(self, status: str) -> None:
        if status != self._status:
            self._status = status
            self.status_changed.emit(status)


__all__ = ["CONTACT_REFRESH_S", "POLL_MS", "EdgeAgent"]
```

- [ ] **Step 4: Прогнать**: `.venv/Scripts/python.exe -m pytest configurator/tests/screen -q; echo "exit=$?"` → `exit=0`.

- [ ] **Step 5: Commit.**

```bash
git add configurator/src/duo_input/screen/edge_agent.py configurator/tests/screen/test_edge_agent.py
git commit -m "feat(screen): the edge agent"
```

---

### Task 10: `ScreenRuntime` — раскладка, обмен, статус

**Files:**
- Create: `configurator/src/duo_input/screen/runtime.py`, `configurator/tests/screen/test_runtime.py`

**Interfaces:**
- Consumes: `layout` (Task 5), `EdgeAgent` (Task 9), `BoardEdgeLink` (Task 8).
- Produces: `class ScreenRuntime(QObject)`:
  - сигналы `layout_changed(object)` (`SharedLayout`), `desktops_changed(object, object)` (своё `DesktopSummary`, соседа `DesktopSummary | None`), `status_changed(str)`;
  - `__init__(settings, link, probe, clock=time.monotonic, parent=None)`;
  - `start()`, `stop()`, `exchange()` (публично для тестов и таймера);
  - `set_enabled(bool)`, `set_peer_side(EdgeSide)` (сторона соседа относительно **этой** машины), `set_resistance(int)`, `set_fullscreen_guard(bool)`;
  - свойства `layout`, `is_pc1`, `status`, `agent`.
  - Статус: статус агента; поверх него `"peer_missing"`, если включено, роль есть, а блоба соседа нет 15 с.

- [ ] **Step 1: Тесты.**

```python
from PySide6.QtCore import QObject, QSettings, Signal

from duo_input.generated.protocol import EdgeSide
from duo_input.screen.geometry import Display
from duo_input.screen.layout import OS_WINDOWS, DesktopSummary, SharedLayout, decode_blob, encode_blob
from duo_input.screen.runtime import PEER_SILENCE_S, ScreenRuntime

from .fake_probe import FakeProbe


class _Link(QObject):
    handoff_received = Signal(object)
    route_received = Signal(object)
    peer_blob_received = Signal(bytes)

    def __init__(self, role="pc1"):
        super().__init__()
        self._role = role
        self.blobs: list[bytes] = []

    def role(self):
        return self._role

    def board_state(self):
        return "ready"

    def ensure_open(self):
        pass

    def send_contact(self, contact):
        return True

    def send_ready(self, identifier):
        return True

    def exchange_layout(self, blob):
        self.blobs.append(bytes(blob))
        return True


class _Clock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now


def _runtime(tmp_path, role="pc1"):
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    link = _Link(role)
    clock = _Clock()
    runtime = ScreenRuntime(settings, link, FakeProbe([Display(0, 0, 1920, 1080)]), clock=clock)
    return runtime, link, settings, clock


def test_an_edit_is_saved_versioned_and_sent_at_once(qapp, tmp_path):
    runtime, link, settings, _ = _runtime(tmp_path)
    runtime.set_enabled(True)
    layout, desktop = decode_blob(link.blobs[-1])
    assert layout.enabled and layout.version == 1
    assert desktop.displays == ((0, 0, 1920, 1080),)
    assert bool(settings.value("screen/enabled", type=bool))


def test_pc2_choosing_the_peer_on_its_left_means_pc2_is_right_of_pc1(qapp, tmp_path):
    runtime, link, _, _ = _runtime(tmp_path, role="pc2")
    runtime.set_peer_side(EdgeSide.LEFT)
    assert runtime.layout.pc2_side == EdgeSide.RIGHT


def test_a_newer_peer_layout_is_adopted_and_an_older_one_is_not(qapp, tmp_path):
    runtime, _, _, _ = _runtime(tmp_path)
    seen = []
    runtime.layout_changed.connect(seen.append)
    peer = SharedLayout(5, 99, True, False, 3, EdgeSide.TOP, 0)
    runtime._link.peer_blob_received.emit(encode_blob(peer, DesktopSummary(OS_WINDOWS, ((0, 0, 1280, 800),))))
    assert runtime.layout == peer
    older = SharedLayout(4, 99, False, False, 0, EdgeSide.LEFT, 0)
    runtime._link.peer_blob_received.emit(encode_blob(older, DesktopSummary(OS_WINDOWS, ())))
    assert runtime.layout == peer
    assert seen == [peer]


def test_two_simultaneous_edits_converge(qapp, tmp_path):
    a, a_link, _, _ = _runtime(tmp_path / "a")
    b, b_link, _, _ = _runtime(tmp_path / "b", role="pc2")
    a.set_resistance(0)
    b.set_resistance(3)
    a._link.peer_blob_received.emit(b_link.blobs[-1])
    b._link.peer_blob_received.emit(a_link.blobs[-1])
    assert a.layout == b.layout


def test_the_peer_desktop_is_announced(qapp, tmp_path):
    runtime, _, _, _ = _runtime(tmp_path)
    seen = []
    runtime.desktops_changed.connect(lambda mine, peer: seen.append(peer))
    peer_desktop = DesktopSummary(OS_WINDOWS, ((0, 0, 1280, 800),))
    runtime._link.peer_blob_received.emit(encode_blob(runtime.layout, peer_desktop))
    assert seen[-1] == peer_desktop


def test_a_silent_peer_is_reported(qapp, tmp_path):
    runtime, _, _, clock = _runtime(tmp_path)
    runtime.set_enabled(True)
    runtime.agent.on_route(type("R", (), {"mouse": 2})())  # the mouse is elsewhere
    clock.now += PEER_SILENCE_S + 1
    runtime.exchange()
    assert runtime.status == "peer_missing"


def test_a_role_learned_later_redraws_the_layout_from_the_other_side(qapp, tmp_path):
    runtime, _, _, _ = _runtime(tmp_path, role="pc2")
    seen = []
    runtime.layout_changed.connect(seen.append)
    runtime.exchange()
    assert seen == [runtime.layout]
    assert runtime.is_pc1 is False


def test_a_busy_board_is_asked_again_soon(qapp, tmp_path):
    runtime, link, _, _ = _runtime(tmp_path)
    link.exchange_layout = lambda blob: False
    runtime.exchange()
    assert runtime._retry.isActive()


def test_the_agent_is_configured_from_the_layout_and_role(qapp, tmp_path):
    runtime, _, _, _ = _runtime(tmp_path, role="pc2")
    runtime.set_enabled(True)
    runtime.set_resistance(1)
    runtime.exchange()
    assert runtime.agent._facing == EdgeSide.LEFT  # PC2 faces back toward PC1
    assert runtime.agent._threshold == 40
```

- [ ] **Step 2: Убедиться, что падает**, затем **Step 3: реализация** `configurator/src/duo_input/screen/runtime.py`:

```python
"""Подсистема «Расширение экрана» целиком: раскладка, обмен, агент, статус.

Раскладка живёт в QSettings этой машины и путешествует блобом через платы
раз в пять секунд и сразу после правки. Побеждает старшая (version, origin).
Агент настраивается заново при каждой смене раскладки или роли.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QSettings, QTimer, Signal

from duo_input.device.transactions import PayloadError
from duo_input.generated.protocol import EdgeSide

from . import layout as layouts
from .edge_agent import EdgeAgent

EXCHANGE_MS = 5000
RETRY_MS = 500
PEER_SILENCE_S = 15.0


class ScreenRuntime(QObject):
    layout_changed = Signal(object)
    desktops_changed = Signal(object, object)
    status_changed = Signal(str)

    def __init__(
        self,
        settings: QSettings,
        link,
        probe,
        clock: Callable[[], float] = time.monotonic,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._link = link
        self._probe = probe
        self._clock = clock
        layouts.machine_id(settings)
        self._layout = layouts.load(settings)
        self._peer_desktop: layouts.DesktopSummary | None = None
        self._peer_seen_at: float | None = None
        self._role: str | None = None
        self._status = ""
        self.agent = EdgeAgent(probe, link, clock=clock, parent=self)
        self.agent.status_changed.connect(self._on_agent_status)
        link.peer_blob_received.connect(self._on_peer_blob)
        self._timer = QTimer(self)
        self._timer.setInterval(EXCHANGE_MS)
        self._timer.timeout.connect(self.exchange)
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.setInterval(RETRY_MS)
        self._retry.timeout.connect(self.exchange)
        self._configure_agent()

    # ------------------------------------------------------------ свойства

    @property
    def layout(self) -> layouts.SharedLayout:
        return self._layout

    @property
    def is_pc1(self) -> bool:
        return self._role != "pc2"

    @property
    def status(self) -> str:
        return self._status

    def my_desktop(self) -> layouts.DesktopSummary:
        return layouts.summarize(self._probe.displays(), self._probe.os_code())

    # --------------------------------------------------------- жизненный цикл

    def start(self) -> None:
        self._timer.start()
        self.agent.start()
        self.exchange()

    def stop(self) -> None:
        self._timer.stop()
        self._retry.stop()
        self.agent.stop()

    # ----------------------------------------------------------------- правки

    def set_enabled(self, enabled: bool) -> None:
        self._edit(enabled=bool(enabled))

    def set_peer_side(self, peer_is_on: EdgeSide) -> None:
        # «Слева от меня» зависит от того, кто «я»: роль уточняется до расчёта.
        self._update_role()
        self._edit(pc2_side=layouts.side_for_peer(EdgeSide(peer_is_on), self.is_pc1))

    def set_resistance(self, level: int) -> None:
        self._edit(resistance=max(0, min(len(layouts.THRESHOLDS) - 1, int(level))))

    def set_fullscreen_guard(self, enabled: bool) -> None:
        self._edit(fullscreen_guard=bool(enabled))

    def _edit(self, **changes) -> None:
        self._adopt(layouts.edited(self._layout, self._settings, **changes))
        self.exchange()

    def _adopt(self, layout: layouts.SharedLayout) -> None:
        if layout == self._layout:
            return
        self._layout = layout
        layouts.save(self._settings, layout)
        self._configure_agent()
        self.layout_changed.emit(layout)

    # ----------------------------------------------------------------- обмен

    def _update_role(self) -> None:
        role = self._link.role()
        if role == self._role:
            return
        self._role = role
        self._configure_agent()
        # Та же раскладка с другой стороны: «сосед справа» на ПК1 - это
        # «сосед слева» на ПК2. Страница должна перерисоваться.
        self.layout_changed.emit(self._layout)

    def exchange(self) -> None:
        self._link.ensure_open()
        self._update_role()
        mine = self.my_desktop()
        self.desktops_changed.emit(mine, self._peer_desktop)
        if self._role is not None:
            if not self._link.exchange_layout(layouts.encode_blob(self._layout, mine)):
                # Сервис платы занят (например, читает конфигурацию сразу
                # после подключения). Повтор скоро, а не через пять секунд.
                self._retry.start()
        self._refresh_status()

    def _on_peer_blob(self, blob: bytes) -> None:
        if not blob:
            return
        try:
            layout, desktop = layouts.decode_blob(bytes(blob))
        except PayloadError:
            return
        self._peer_seen_at = self._clock()
        if desktop != self._peer_desktop:
            self._peer_desktop = desktop
            self.desktops_changed.emit(self.my_desktop(), desktop)
        if layouts.newer(layout, self._layout):
            self._adopt(layout)
        self._refresh_status()

    # ----------------------------------------------------------------- статус

    def _configure_agent(self) -> None:
        facing = layouts.facing_side(self._layout, self.is_pc1) if self._role is not None else None
        self.agent.configure(
            enabled=self._layout.enabled,
            facing=facing,
            threshold=layouts.THRESHOLDS[self._layout.resistance],
            fullscreen_guard=self._layout.fullscreen_guard,
        )

    def _on_agent_status(self, _status: str) -> None:
        self._refresh_status()

    def _refresh_status(self) -> None:
        status = self.agent.status
        silent = self._peer_seen_at is None or self._clock() - self._peer_seen_at > PEER_SILENCE_S
        if self._layout.enabled and self._role is not None and silent and status in ("working", "idle", "contact", ""):
            status = "peer_missing"
        if status != self._status:
            self._status = status
            self.status_changed.emit(status)


__all__ = ["EXCHANGE_MS", "PEER_SILENCE_S", "ScreenRuntime"]
```

  В тесте `test_a_silent_peer_is_reported` агенту передаётся объект с `mouse = 2`: у агента статус станет `idle` после первого `poll`, но здесь `poll` не вызывается. Статус агента пустой, а пустой статус покрыт условием `status in (…, "")`.

- [ ] **Step 4: Прогнать**: `.venv/Scripts/python.exe -m pytest configurator/tests/screen -q; echo "exit=$?"` → `exit=0`.

- [ ] **Step 5: Commit.**

```bash
git add configurator/src/duo_input/screen/runtime.py configurator/tests/screen/test_runtime.py
git commit -m "feat(screen): runtime - shared layout over the boards, agent, status"
```

---

### Task 11: Страница «Расширение экрана»

**Files:**
- Create: `configurator/src/duo_input/ui/screen_page.py`, `configurator/tests/ui/test_screen_page.py`

**Interfaces:**
- Consumes: `DesktopSummary`, `EdgeSide`.
- Produces: `class ScreenPage(QWidget)`:
  - сигналы `enabled_toggled(bool)`, `peer_side_chosen(object)` (`EdgeSide`, сторона соседа относительно этой машины), `resistance_chosen(int)`, `fullscreen_toggled(bool)`, `mouse_page_requested()`;
  - виджеты `enabled_checkbox`, `canvas` (`LayoutCanvas`), `side_combo`, `resistance_buttons: list[QPushButton]` (4 шт., checkable, эксклюзивные), `fullscreen_checkbox`, `status_label`, `keyboard_link` (`QPushButton`, плоская ссылка);
  - слоты `set_layout_view(enabled: bool, peer_side: EdgeSide, resistance: int, fullscreen_guard: bool)`, `set_desktops(mine, peer)`, `set_status(code: str)`.
  - Все программные установки делаются под `blockSignals`.
- Produces: `class LayoutCanvas(QWidget)` с сигналом `side_chosen(object)` и методами `set_view(mine, peer, peer_side)`, `peer_rect() -> QRectF`, `own_rect() -> QRectF`.

- [ ] **Step 1: Тесты.** `configurator/tests/ui/test_screen_page.py`:

```python
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from duo_input.generated.protocol import EdgeSide
from duo_input.screen.layout import OS_MACOS, OS_WINDOWS, DesktopSummary
from duo_input.ui.screen_page import STATUS_TEXT, ScreenPage

MINE = DesktopSummary(OS_WINDOWS, ((0, 0, 1920, 1080),))
PEER = DesktopSummary(OS_MACOS, ((0, 0, 1440, 900),))


def _page(qtbot):
    page = ScreenPage()
    qtbot.addWidget(page)
    page.resize(700, 600)
    page.show()
    page.set_desktops(MINE, PEER)
    page.set_layout_view(False, EdgeSide.RIGHT, 2, True)
    return page


def _send(widget, kind, point, buttons):
    event = QMouseEvent(
        kind, QPointF(point), QPointF(widget.mapToGlobal(point.toPoint())),
        Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def test_the_checkbox_announces_the_switch(qtbot):
    page = _page(qtbot)
    with qtbot.waitSignal(page.enabled_toggled) as blocker:
        page.enabled_checkbox.click()
    assert blocker.args == [True]


def test_showing_a_layout_announces_nothing(qtbot):
    page = _page(qtbot)
    seen = []
    page.enabled_toggled.connect(seen.append)
    page.peer_side_chosen.connect(seen.append)
    page.resistance_chosen.connect(seen.append)
    page.set_layout_view(True, EdgeSide.LEFT, 0, False)
    assert seen == []
    assert page.enabled_checkbox.isChecked()
    assert page.side_combo.currentData() == EdgeSide.LEFT
    assert page.resistance_buttons[0].isChecked()


def test_the_side_list_chooses_a_side(qtbot):
    page = _page(qtbot)
    with qtbot.waitSignal(page.peer_side_chosen) as blocker:
        page.side_combo.setCurrentIndex(page.side_combo.findData(EdgeSide.TOP))
    assert blocker.args == [EdgeSide.TOP]


def test_dragging_the_other_computer_to_the_left_chooses_left(qtbot):
    page = _page(qtbot)
    canvas = page.canvas
    start = canvas.peer_rect().center()
    end = QPointF(canvas.own_rect().left() - 40, canvas.own_rect().center().y())
    with qtbot.waitSignal(page.peer_side_chosen) as blocker:
        _send(canvas, QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton)
        _send(canvas, QEvent.Type.MouseMove, end, Qt.MouseButton.LeftButton)
        _send(canvas, QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.NoButton)
    assert blocker.args == [EdgeSide.LEFT]


def test_a_resistance_button_chooses_its_level(qtbot):
    page = _page(qtbot)
    with qtbot.waitSignal(page.resistance_chosen) as blocker:
        page.resistance_buttons[3].click()
    assert blocker.args == [3]


def test_the_fullscreen_checkbox_announces_itself(qtbot):
    page = _page(qtbot)
    with qtbot.waitSignal(page.fullscreen_toggled) as blocker:
        page.fullscreen_checkbox.click()
    assert blocker.args == [False]


def test_every_status_has_words(qtbot):
    page = _page(qtbot)
    for code in ("working", "contact", "idle", "disabled", "no_board", "unsupported",
                 "peer_missing", "probe:dpi_unaware", "fullscreen", "hidden", "clip", "unreadable"):
        page.set_status(code)
        assert page.status_label.text()
        assert code in STATUS_TEXT or code.startswith("probe:")


def test_the_keyboard_hint_leads_to_the_mouse_page(qtbot):
    page = _page(qtbot)
    with qtbot.waitSignal(page.mouse_page_requested):
        page.keyboard_link.click()
```

- [ ] **Step 2: Убедиться, что падает**, затем **Step 3: реализация** `configurator/src/duo_input/ui/screen_page.py`:

```python
"""Страница «Расширение экрана»: включить, расположить соседа, сопротивление.

Настройки действуют сразу и общие для пары - их не надо записывать в
устройство, поэтому они не на странице «Мышь». Схема рисует эту машину и
соседа по описаниям мониторов; соседа можно перетащить к любой стороне,
выпадающий список делает то же самое с клавиатуры.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from duo_input.generated.protocol import EdgeSide
from duo_input.screen.layout import OS_MACOS, DesktopSummary

#: Коды статуса агента и подсистемы - в слова для оператора.
STATUS_TEXT = {
    "working": "● Работает.",
    "contact": "● Работает: курсор у края.",
    "idle": "● Работает. Мышь сейчас на другом компьютере.",
    "disabled": "Выключено.",
    "no_board": "Плата не подключена.",
    "unsupported": "Прошивка платы не поддерживает расширение экрана — обновите обе платы.",
    "peer_missing": "На втором компьютере не запущен Duo Input.",
    "fullscreen": "Пауза: полноэкранное приложение.",
    "hidden": "Пауза: приложение спрятало курсор.",
    "clip": "Пауза: приложение заперло курсор.",
    "unreadable": "Пауза: экран заблокирован.",
}
_PROBE_TEXT = {
    "dpi_unaware": "Windows: процесс не в режиме Per-Monitor DPI — расширение экрана недоступно.",
}
_SIDES = (
    (EdgeSide.RIGHT, "справа"),
    (EdgeSide.LEFT, "слева"),
    (EdgeSide.TOP, "сверху"),
    (EdgeSide.BOTTOM, "снизу"),
)
_RESISTANCE = ("Нет", "Низкое", "Среднее", "Высокое")


def _bounds(desktop: DesktopSummary | None) -> tuple[float, float]:
    if desktop is None or not desktop.displays:
        return 1920.0, 1080.0
    width = max(x + w for x, _, w, _ in desktop.displays)
    height = max(y + h for _, y, _, h in desktop.displays)
    return float(width), float(height)


class LayoutCanvas(QWidget):
    side_chosen = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.setAccessibleName(self.tr("Расположение компьютеров"))
        self._mine: DesktopSummary | None = None
        self._peer: DesktopSummary | None = None
        self._side = EdgeSide.RIGHT
        self._drag_offset: QPointF | None = None
        self._drag_at: QPointF | None = None

    def set_view(self, mine, peer, peer_side) -> None:
        self._mine, self._peer, self._side = mine, peer, EdgeSide(peer_side)
        self._drag_offset = self._drag_at = None
        self.update()

    def _scale(self) -> float:
        mw, mh = _bounds(self._mine)
        pw, ph = _bounds(self._peer)
        horizontal = self._side in (EdgeSide.LEFT, EdgeSide.RIGHT)
        need_w = mw + pw if horizontal else max(mw, pw)
        need_h = max(mh, ph) if horizontal else mh + ph
        return 0.8 * min(self.width() / need_w, self.height() / need_h)

    def own_rect(self) -> QRectF:
        s = self._scale()
        mw, mh = _bounds(self._mine)
        pw, ph = _bounds(self._peer)
        horizontal = self._side in (EdgeSide.LEFT, EdgeSide.RIGHT)
        total_w = (mw + pw) * s if horizontal else max(mw, pw) * s
        total_h = max(mh, ph) * s if horizontal else (mh + ph) * s
        left = (self.width() - total_w) / 2
        top = (self.height() - total_h) / 2
        if self._side == EdgeSide.LEFT:
            left += pw * s
        if self._side == EdgeSide.TOP:
            top += ph * s
        return QRectF(left, top, mw * s, mh * s)

    def peer_rect(self) -> QRectF:
        s = self._scale()
        pw, ph = _bounds(self._peer)
        own = self.own_rect()
        if self._drag_at is not None and self._drag_offset is not None:
            corner = self._drag_at - self._drag_offset
            return QRectF(corner.x(), corner.y(), pw * s, ph * s)
        if self._side == EdgeSide.RIGHT:
            return QRectF(own.right(), own.center().y() - ph * s / 2, pw * s, ph * s)
        if self._side == EdgeSide.LEFT:
            return QRectF(own.left() - pw * s, own.center().y() - ph * s / 2, pw * s, ph * s)
        if self._side == EdgeSide.TOP:
            return QRectF(own.center().x() - pw * s / 2, own.top() - ph * s, pw * s, ph * s)
        return QRectF(own.center().x() - pw * s / 2, own.bottom(), pw * s, ph * s)

    def mousePressEvent(self, event) -> None:
        if self.peer_rect().contains(event.position()):
            self._drag_offset = event.position() - self.peer_rect().topLeft()
            self._drag_at = event.position()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None:
            self._drag_at = event.position()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_offset is None:
            return
        self._drag_at = event.position()
        centre = self.peer_rect().center() - self.own_rect().center()
        if abs(centre.x()) >= abs(centre.y()):
            side = EdgeSide.RIGHT if centre.x() > 0 else EdgeSide.LEFT
        else:
            side = EdgeSide.BOTTOM if centre.y() > 0 else EdgeSide.TOP
        self._drag_offset = self._drag_at = None
        self._side = side
        self.update()
        self.side_chosen.emit(side)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        text = self.palette().color(self.foregroundRole())
        self._draw(painter, self.own_rect(), self._mine, self.tr("Этот компьютер"), text, solid=True)
        self._draw(
            painter, self.peer_rect(), self._peer, self.tr("Второй компьютер"), text, solid=self._peer is not None
        )

    def _draw(self, painter, rect, desktop, title, colour, solid) -> None:
        pen = QPen(colour, 1.5, Qt.PenStyle.SolidLine if solid else Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(QColor(colour.red(), colour.green(), colour.blue(), 18))
        painter.drawRect(rect)
        if desktop is not None and desktop.displays:
            width, height = _bounds(desktop)
            for x, y, w, h in desktop.displays:
                painter.drawRect(
                    QRectF(
                        rect.left() + x / width * rect.width() + 3,
                        rect.top() + y / height * rect.height() + 3,
                        w / width * rect.width() - 6,
                        h / height * rect.height() - 6,
                    )
                )
        label = title
        if desktop is not None and desktop.displays:
            _, _, w, h = desktop.displays[0]
            label += f"\n{w}×{h}" + ("\nmacOS" if desktop.os == OS_MACOS else "\nWindows")
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)


class ScreenPage(QWidget):
    enabled_toggled = Signal(bool)
    peer_side_chosen = Signal(object)
    resistance_chosen = Signal(int)
    fullscreen_toggled = Signal(bool)
    mouse_page_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.enabled_checkbox = QCheckBox(self.tr("Расширение экрана"), self)
        self.enabled_checkbox.toggled.connect(self.enabled_toggled)
        explanation = QLabel(
            self.tr(
                "Курсор переходит на второй компьютер через край экрана, как на второй "
                "монитор. Хоткей продолжает работать. Настройки общие для обоих компьютеров."
            ),
            self,
        )
        explanation.setWordWrap(True)

        self.canvas = LayoutCanvas(self)
        self.canvas.side_chosen.connect(self._on_canvas_side)
        hint = QLabel(self.tr("Перетащите второй компьютер к нужной стороне. Край отображается на край целиком."), self)
        hint.setWordWrap(True)

        self.side_combo = QComboBox(self)
        self.side_combo.setAccessibleName(self.tr("Где второй компьютер"))
        for side, name in _SIDES:
            self.side_combo.addItem(self.tr(name), side)
        self.side_combo.currentIndexChanged.connect(self._on_combo)
        side_row = QHBoxLayout()
        side_row.addWidget(QLabel(self.tr("Второй компьютер:"), self))
        side_row.addWidget(self.side_combo)
        side_row.addStretch(1)

        self.resistance_buttons: list[QPushButton] = []
        self._resistance_group = QButtonGroup(self)
        self._resistance_group.setExclusive(True)
        resistance_row = QHBoxLayout()
        resistance_row.addWidget(QLabel(self.tr("Сопротивление края:"), self))
        for level, name in enumerate(_RESISTANCE):
            button = QPushButton(self.tr(name), self)
            button.setCheckable(True)
            self._resistance_group.addButton(button, level)
            self.resistance_buttons.append(button)
            resistance_row.addWidget(button)
        resistance_row.addStretch(1)
        self._resistance_group.idClicked.connect(self.resistance_chosen)

        self.fullscreen_checkbox = QCheckBox(self.tr("Не переходить из полноэкранных приложений и игр"), self)
        self.fullscreen_checkbox.toggled.connect(self.fullscreen_toggled)

        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        self.keyboard_link = QPushButton(
            self.tr("Клавиатура переходит вместе с мышью, если включено «Переключать клавиатуру и мышь вместе» на странице «Мышь»"),
            self,
        )
        self.keyboard_link.setFlat(True)
        self.keyboard_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.keyboard_link.clicked.connect(self.mouse_page_requested)

        box = QGroupBox(self.tr("Расположение"), self)
        box_layout = QVBoxLayout(box)
        box_layout.addWidget(self.canvas, 1)
        box_layout.addWidget(hint)
        box_layout.addLayout(side_row)
        box_layout.addLayout(resistance_row)
        box_layout.addWidget(self.fullscreen_checkbox)

        layout = QVBoxLayout(self)
        layout.addWidget(self.enabled_checkbox)
        layout.addWidget(explanation)
        layout.addWidget(box, 1)
        layout.addWidget(self.status_label)
        layout.addWidget(self.keyboard_link)
        self._mine = None
        self._peer = None
        self._side = EdgeSide.RIGHT

    # ------------------------------------------------------------ показ

    def set_layout_view(self, enabled: bool, peer_side: EdgeSide, resistance: int, fullscreen_guard: bool) -> None:
        self._side = EdgeSide(peer_side)
        for widget, apply in (
            (self.enabled_checkbox, lambda: self.enabled_checkbox.setChecked(enabled)),
            (self.side_combo, lambda: self.side_combo.setCurrentIndex(self.side_combo.findData(self._side))),
            (self.fullscreen_checkbox, lambda: self.fullscreen_checkbox.setChecked(fullscreen_guard)),
        ):
            widget.blockSignals(True)
            apply()
            widget.blockSignals(False)
        self._resistance_group.blockSignals(True)
        self.resistance_buttons[max(0, min(3, resistance))].setChecked(True)
        self._resistance_group.blockSignals(False)
        self.canvas.set_view(self._mine, self._peer, self._side)

    def set_desktops(self, mine: DesktopSummary | None, peer: DesktopSummary | None) -> None:
        self._mine, self._peer = mine, peer
        self.canvas.set_view(mine, peer, self._side)

    def set_status(self, code: str) -> None:
        if code.startswith("probe:"):
            text = _PROBE_TEXT.get(code.split(":", 1)[1], self.tr("Расширение экрана недоступно на этой системе."))
        else:
            text = STATUS_TEXT.get(code, code)
        self.status_label.setText(self.tr(text))

    # ------------------------------------------------------------ выбор

    def _on_combo(self, index: int) -> None:
        side = self.side_combo.itemData(index)
        if side is not None:
            self._side = EdgeSide(side)
            self.canvas.set_view(self._mine, self._peer, self._side)
            self.peer_side_chosen.emit(self._side)

    def _on_canvas_side(self, side) -> None:
        self._side = EdgeSide(side)
        self.side_combo.blockSignals(True)
        self.side_combo.setCurrentIndex(self.side_combo.findData(self._side))
        self.side_combo.blockSignals(False)
        self.peer_side_chosen.emit(self._side)


__all__ = ["STATUS_TEXT", "LayoutCanvas", "ScreenPage"]
```

  `self.tr(text)` для строк из словаря `STATUS_TEXT` не попадёт в извлечение переводов автоматически. Обернуть значения словаря в `QT_TRANSLATE_NOOP("ScreenPage", "…")` (из `PySide6.QtCore`), если `tools/update_translations.py` на этом настаивает. Проверить по выводу скрипта в Task 12.

- [ ] **Step 4: Прогнать**: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_screen_page.py -q; echo "exit=$?"` → `exit=0`.

- [ ] **Step 5: Commit.**

```bash
git add configurator/src/duo_input/ui/screen_page.py configurator/tests/ui/test_screen_page.py
git commit -m "feat(ui): screen extension page with a draggable layout"
```

---

### Task 12: Проводка в окно и приложение, сквозные тесты

**Files:**
- Modify: `configurator/src/duo_input/ui/main_window.py`, `configurator/src/duo_input/app.py`
- Modify: переводы через `tools/update_translations.py`
- Create: `configurator/tests/ui/test_screen_wiring.py`

**Interfaces:**
- Consumes: всё выше.
- Produces: `MainWindow.PAGE_SCREEN` (после `PAGE_CLIPBOARD`), `MainWindow.screen_page`; в `app.py` — `configure_screen(application, window, settings, shared_endpoint, probe=None) -> ScreenRuntime | None`.

- [ ] **Step 1: Сквозные тесты.** `configurator/tests/ui/test_screen_wiring.py`:

```python
"""Продакшн-путь: эмулятор платы → сервис → агент → probe и обратно."""

from PySide6.QtCore import QSettings

from duo_input.app import build_main_window, configure_screen
from duo_input.device.edge_messages import Handoff, RouteState
from duo_input.device.emulator import U1Emulator
from duo_input.device.shared_endpoint import SharedEndpoint
from duo_input.generated.protocol import EdgeSide, KeyboardRoute, MouseRoute, RouteReason
from duo_input.screen.geometry import T_MAX, Display

from ..screen.fake_probe import FakeProbe


def _settings(tmp_path, enabled=True):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("screen/enabled", enabled)
    return settings


def _connected_window(qtbot, settings, emulator):
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    with qtbot.waitSignal(window.service.operation_succeeded, timeout=3000):
        window.service.connect_device(emulator)
    return window


def test_the_page_exists_and_reflects_the_saved_setting(qtbot, qapp, tmp_path):
    settings = _settings(tmp_path)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    runtime = configure_screen(qapp, window, settings, SharedEndpoint(parent=qapp), probe=FakeProbe())
    try:
        assert window.screen_page.enabled_checkbox.isChecked()
        window.show_page(window.PAGE_SCREEN)
        assert window.pages.currentWidget() is window.screen_page
    finally:
        runtime.stop()


def test_pc1_reports_its_contact_through_u1(qtbot, qapp, tmp_path):
    emulator = U1Emulator()
    settings = _settings(tmp_path)
    window = _connected_window(qtbot, settings, emulator)
    probe = FakeProbe([Display(0, 0, 1920, 1080)], (1919.0, 540.0))
    runtime = configure_screen(qapp, window, settings, SharedEndpoint(parent=qapp), probe=probe)
    try:
        emulator.queue_route_state(RouteState(MouseRoute.PC1, KeyboardRoute.PC1, RouteReason.BOOT))
        window.service._link.poll()
        qtbot.waitUntil(lambda: any(c.active for c in emulator.edge_contacts), timeout=2000)
        assert emulator.edge_contacts[-1].side == EdgeSide.RIGHT
    finally:
        runtime.stop()


def test_a_handoff_from_u1_places_the_pointer_and_is_answered(qtbot, qapp, tmp_path):
    emulator = U1Emulator()
    settings = _settings(tmp_path)
    window = _connected_window(qtbot, settings, emulator)
    probe = FakeProbe([Display(0, 0, 1920, 1080)], (500.0, 500.0))
    runtime = configure_screen(qapp, window, settings, SharedEndpoint(parent=qapp), probe=probe)
    try:
        emulator.queue_handoff(Handoff(11, EdgeSide.RIGHT, T_MAX // 2))
        window.service._link.poll()
        qtbot.waitUntil(lambda: emulator.handoff_ready_ids == [11], timeout=2000)
        x, y = probe.warps[-1]
        assert x == 1919 - 2 and abs(y - 540) <= 1
    finally:
        runtime.stop()


def test_the_layout_reaches_the_board_and_a_peer_change_reaches_the_page(qtbot, qapp, tmp_path):
    from duo_input.screen.layout import OS_MACOS, DesktopSummary, SharedLayout, encode_blob

    emulator = U1Emulator()
    peer = SharedLayout(9, 5, True, True, 1, EdgeSide.LEFT, 0)
    emulator.set_peer_layout(encode_blob(peer, DesktopSummary(OS_MACOS, ((0, 0, 1440, 900),))))
    settings = _settings(tmp_path)
    window = _connected_window(qtbot, settings, emulator)
    runtime = configure_screen(qapp, window, settings, SharedEndpoint(parent=qapp), probe=FakeProbe())
    try:
        qtbot.waitUntil(lambda: runtime.layout == peer, timeout=3000)
        assert emulator.local_layout
        assert window.screen_page.side_combo.currentData() == EdgeSide.LEFT
        assert window.screen_page.resistance_buttons[1].isChecked()
    finally:
        runtime.stop()


def test_the_page_drives_the_runtime_through_real_widgets(qtbot, qapp, tmp_path):
    settings = _settings(tmp_path, enabled=False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    runtime = configure_screen(qapp, window, settings, SharedEndpoint(parent=qapp), probe=FakeProbe())
    try:
        window.screen_page.enabled_checkbox.click()
        assert runtime.layout.enabled is True
        window.screen_page.resistance_buttons[3].click()
        assert runtime.layout.resistance == 3
        window.screen_page.side_combo.setCurrentIndex(window.screen_page.side_combo.findData(EdgeSide.TOP))
        assert runtime.layout.pc2_side == EdgeSide.TOP  # this is PC1 (no board: default role)
        window.screen_page.keyboard_link.click()
        assert window.pages.currentWidget() is window.mouse
    finally:
        runtime.stop()


def test_without_a_probe_on_this_system_the_page_says_so(qtbot, qapp, tmp_path, monkeypatch):
    import duo_input.app as app_module

    monkeypatch.setattr(app_module, "create_probe", lambda: None)
    settings = _settings(tmp_path)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    assert configure_screen(qapp, window, settings, SharedEndpoint(parent=qapp)) is None
    assert window.screen_page.status_label.text()
    assert not window.screen_page.enabled_checkbox.isEnabled()
```

- [ ] **Step 2: Убедиться, что падает.**
  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_screen_wiring.py -q`
  Expected: FAIL.

- [ ] **Step 3: Окно.** В `main_window.py`:
  - импорт `from .screen_page import ScreenPage`;
  - распаковку констант сделать на 9 элементов: `PAGE_CLIPBOARD, PAGE_SCREEN, PAGE_DIAGNOSTICS, PAGE_SETTINGS) = range(9)`, а в `PAGE_ORDER` вставить `PAGE_SCREEN` после `PAGE_CLIPBOARD`;
  - в `_build_ui` после `self.clipboard_page = ClipboardPage(self.pages)` добавить `self.screen_page = ScreenPage(self.pages)`, а в `sections` после строки общего буфера — `(self.tr("Расширение экрана"), self.screen_page),`;
  - после цикла `for page in (self.bindings, self.mouse)` добавить:

```python
        self.screen_page.mouse_page_requested.connect(
            lambda: self.show_page(self.PAGE_MOUSE)
        )
```

- [ ] **Step 4: Приложение.** В `app.py`:
  - импорты: `from duo_input.screen.edge_link import BoardEdgeLink`, `from duo_input.screen.probe import create_probe`, `from duo_input.screen.runtime import ScreenRuntime`, `from duo_input.screen.layout import peer_side`, `from PySide6.QtGui import QGuiApplication` (если ещё не импортирован);
  - функция:

```python
def configure_screen(
    application: QApplication,
    window: MainWindow,
    settings: QSettings,
    shared_endpoint: SharedEndpoint,
    probe=None,
) -> ScreenRuntime | None:
    """Подключить «Расширение экрана». Без probe для этой ОС страница говорит почему."""
    page = window.screen_page
    probe = probe if probe is not None else create_probe()
    if probe is None:
        page.enabled_checkbox.setEnabled(False)
        page.set_status("probe:unsupported_os")
        return None
    owner = QObject(application)
    endpoint = shared_endpoint.acquire(owner)
    link = BoardEdgeLink(window.service, endpoint, parent=owner)
    runtime = ScreenRuntime(settings, link, probe, parent=owner)

    def show_layout(layout) -> None:
        page.set_layout_view(
            layout.enabled, peer_side(layout, runtime.is_pc1), layout.resistance, layout.fullscreen_guard
        )

    runtime.layout_changed.connect(show_layout)
    runtime.desktops_changed.connect(page.set_desktops)
    runtime.status_changed.connect(page.set_status)
    page.enabled_toggled.connect(runtime.set_enabled)
    page.peer_side_chosen.connect(runtime.set_peer_side)
    page.resistance_chosen.connect(runtime.set_resistance)
    page.fullscreen_toggled.connect(runtime.set_fullscreen_guard)

    # Мониторы меняются - агент перечитывает их сразу, а не через две секунды.
    def watch(screen) -> None:
        screen.geometryChanged.connect(runtime.agent.refresh_displays)

    for screen in QGuiApplication.screens():
        watch(screen)
    application.screenAdded.connect(watch)
    application.screenAdded.connect(runtime.agent.refresh_displays)
    application.screenRemoved.connect(runtime.agent.refresh_displays)

    def shutdown() -> None:
        runtime.stop()
        shared_endpoint.release(owner)

    application.aboutToQuit.connect(shutdown)
    show_layout(runtime.layout)
    runtime.start()
    return runtime
```

  - в `configure_runtime` после строки с `_ClipboardRuntime(...)`: `configure_screen(application, window, settings, shared_endpoint)`.

  `owner` — `QObject` с родителем `application`. Он держит связку живой (память о слабых ссылках PySide6) и служит ключом владения портом U2.

- [ ] **Step 4a: Тест, который теперь проверяет другое.** С этого шага `configure_runtime` создаёт и рантайм расширения экрана, поэтому у порта U2 два владельца. В `configurator/tests/ui/test_runtime_wiring.py` заменить `test_stop_calls_stop_on_both_exchange_and_endpoint` на:

```python
def test_stop_stops_the_exchange_and_gives_up_the_endpoint(qtbot, qapp, tmp_path, monkeypatch):
    """Switching sharing off stops the exchange and releases the U2 port.
    The port itself stays open: the screen extension owns it too."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)

    exchange = runtime.address_exchange
    endpoint = runtime._endpoint
    exchange_stops: list[int] = []
    endpoint_stops: list[int] = []
    monkeypatch.setattr(exchange, "stop", lambda: exchange_stops.append(1))
    monkeypatch.setattr(endpoint, "stop", lambda: endpoint_stops.append(1))

    window.clipboard_page.sharing_checkbox.setChecked(False)

    assert exchange_stops == [1]
    assert endpoint_stops == []
    assert runtime.address_exchange is None
    assert runtime._endpoint is None
```

  Тест `test_switching_sharing_off_keeps_u2_open_for_another_owner` из Task 3 остаётся как есть.

- [ ] **Step 5: Переводы.**
  Run: `.venv/Scripts/python.exe tools/update_translations.py`
  Expected: новые строки страницы попали в `.ts`. Если скрипт не видит строки из `STATUS_TEXT`, обернуть их в `QT_TRANSLATE_NOOP` (см. Task 11) и повторить. Тест локализации (`test_localization.py`) должен остаться зелёным.

- [ ] **Step 6: Прогнать всё.**
  Run: `.venv/Scripts/python.exe -m pytest configurator/tests tests -q; echo "exit=$?"`
  Expected: `exit=0`. Если `test_main_window_pages.py` или `screenshot_smoke.py` перечисляют страницы явно, добавить туда «Расширение экрана».

- [ ] **Step 7: Commit.**

```bash
git add configurator/src/duo_input/ui/main_window.py configurator/src/duo_input/app.py configurator/resources configurator/tests/ui/test_screen_wiring.py configurator/tests/ui/test_runtime_wiring.py
git commit -m "feat(app): wire the screen extension page and runtime"
```

  Путь к переводам взять из вывода `tools/update_translations.py`; если они лежат не в `configurator/resources`, добавить фактический путь.

---

### Task 13: Мутации, полные наборы, сборка, приёмка на железе

**Files:** изменений кода нет. Итог записывается в `docs/superpowers/records/2026-10-04-screen-extension-agent.md`.

- [ ] **Step 1: Мутации.** Каждую строку удалить или обратить, прогнать указанный тест и вернуть строку.

| Мутация | Где | Должен упасть |
| --- | --- | --- |
| убрать вычитание `_subtract` (все края внешние) | `geometry.py` | `test_the_shared_edge_of_side_by_side_monitors_is_not_outer` |
| деление на `scale` в `Segment.length` → без деления | `geometry.py` | `test_stacked_monitors_make_one_long_edge_by_logical_length` |
| убрать проверку `CORNER_ZONE` | `geometry.py` | `test_corners_are_not_contacts` |
| `inset` → 0 в `point_at` | `geometry.py` | `test_the_example_from_the_spec_maps_half_height_to_half_height` |
| `newer`: сравнить только `version` | `layout.py` | `test_two_simultaneous_edits_converge` |
| `side_for_peer` → всегда `peer_is_on` | `layout.py` | `test_each_computer_faces_the_other` |
| убрать `if not self._mouse_is_here(role)` | `edge_agent.py` | `test_nothing_is_reported_while_the_mouse_is_on_the_other_computer` |
| убрать повтор по `CONTACT_REFRESH_S` | `edge_agent.py` | `test_the_contact_is_refreshed_and_not_flooded` |
| убрать `gen + 1` для нового эпизода | `edge_agent.py` | `test_leaving_the_edge_releases_and_returning_is_a_new_episode` |
| `send_ready` до проверки `warp` | `edge_agent.py` | `test_a_failed_warp_is_not_answered` |
| убрать ранний возврат push в `DeviceService._handle_frame` | `service.py` | `test_a_push_during_an_operation_neither_fails_it_nor_is_lost` |
| `_notify` через `self._sequence.next()` | `service.py` | `test_edge_notifications_go_out_without_a_sequence_number` |
| убрать ветку push в `EndpointService._on_frame` | `endpoint_service.py` | `test_u2_notifications_and_pushes` |
| `release` останавливает при любом владельце | `shared_endpoint.py` | `test_one_service_for_every_owner_and_closed_only_by_the_last` |
| `BoardEdgeLink` ищет U2 при подключённой U1 | `edge_link.py` | `test_a_connected_u1_makes_this_pc1_and_u2_is_left_alone` |
| убрать `newer(...)` в `_on_peer_blob` | `runtime.py` | `test_a_newer_peer_layout_is_adopted_and_an_older_one_is_not` |
| убрать `blockSignals` в `set_layout_view` | `screen_page.py` | `test_showing_a_layout_announces_nothing` |
| убрать подключение `page.enabled_toggled` | `app.py` | `test_the_page_drives_the_runtime_through_real_widgets` |
| убрать `runtime.start()` | `app.py` | `test_pc1_reports_its_contact_through_u1` |

- [ ] **Step 2: Полные наборы и сборка exe.**
  Run:

  ```
  .venv/Scripts/python.exe -m pytest configurator/tests tests -q; echo "exit=$?"
  powershell -File tools/build_release.ps1
  ```

  Expected: `exit=0`, сборка проходит. Запустить собранный exe, открыть «Расширение экрана» и проверить, что статус не `probe:dpi_unaware` (память «sys.executable в Nuitka — фантом»: собранный exe проверяется отдельно).

- [ ] **Step 3: Приёмка Win↔Win (человек; агент готовит инструкцию и читает журналы обеих машин).** Прошивки из плана 2 уже на платах. На обоих ПК — свежая сборка. Включить «Расширение экрана», ПК2 справа, сопротивление «Среднее», синхрон включён.
  1. Переходы ПК1→ПК2 и обратно на всех четырёх сторонах (менять сторону на странице). Курсор на середине высоты появляется на середине. Рывков нет.
  2. `GET_EDGE_STATS` (через `tools/edge_bench.py` нельзя — он займёт порт; использовать вкладку «Диагностика» или временно закрыть приложение и запустить `edge_bench.py --seconds 1`): `handoff_timeouts = 0` за 200 переходов, медиана `total_ready_ms / handoffs_ready` ≤ 12 мс, `max_ready_ms` ≤ 30 мс.
  3. Работа 2 минуты у края (полоса прокрутки, «закрыть») — 0 случайных переходов.
  4. Бросок в угол — перехода нет.
  5. Перетаскивание окна к краю — перехода нет.
  6. Хоткей во время толчка в край — ровно одно переключение.
  7. Приложение на ПК2 закрыто — переход за ≤ 80 мс, мышь работает; страница ПК1 через 15 с пишет «На втором компьютере не запущен Duo Input».
  8. Провод U1↔U2 выдернут — перехода нет, хоткей работает.
  9. ПК2 спит — перехода нет.
  10. Отключить внешний монитор, подключить снова; ноутбук в док-станцию и обратно.
  11. Экран блокировки на каждой стороне.
  12. Полноэкранная игра и относительный режим (где есть игра): перехода нет, статус «Пауза: …».
  13. RDP в окне и на весь экран.
  14. Зажатый Shift → переход по краю → Shift на старом ПК отпущен.
  15. Правка стороны на ПК2 — через ≤ 5 с ПК1 показывает ту же раскладку.
  16. Выключить общий буфер на ПК2 — расширение экрана продолжает работать.

- [ ] **Step 4: Приёмка Win↔Mac.** Повторить пункты 1–7, 10–13 и 15 с Mac за U2, затем с Mac за U1. Отдельно проверить отсутствие запросов разрешений macOS и отсутствие залипания движения после входа на Mac (M2).

- [ ] **Step 5: Записать итог и закоммитить.** В `docs/superpowers/records/2026-10-04-screen-extension-agent.md`: числа наборов, таблица мутаций, каждый пункт приёмки с наблюдением, задержки, расхождения и что с ними сделано.

```bash
git add docs/superpowers/records/2026-10-04-screen-extension-agent.md
git commit -m "docs(screen-extension): agent verification record"
```
