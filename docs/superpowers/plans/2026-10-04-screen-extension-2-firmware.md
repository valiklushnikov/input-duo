# Расширение экрана, план 2 из 3 — протокол и прошивка

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** U1 переводит мышь (и клавиатуру по синхрону) на соседний компьютер, когда компьютер, на котором сейчас мышь, сообщает касание края, а сырая мышь продолжает давить наружу. Пока соседний компьютер ставит курсор, движение к нему придерживается. Всё это проверяется нативными тестами и стендовым скриптом, без агента.

**Architecture:**
- Новые CDC-сообщения 25–30 и SPI-сообщения 10–13. Их кодеки лежат в `firmware/common/link/edge.{hpp,cpp}`, а зеркальный кодек на Python — в `configurator/src/duo_input/device/edge_messages.py`.
- **Ядро 1 U1.** `mapping::EdgeArbiter` меряет давление и проверяет 7 условий; `Core1Runtime` вызывает существующий `move_route` и публикует `CommandKind::RouteMoved` в очередь вывода.
- **Ядро 0 U1.** `OutputRuntime` держит ворота (`link::HandoffGate`) и состояние маршрутов. `EdgeService` собирает касания (CDC от ПК1, SPI от ПК2), передаёт их на ядро 1 и рассылает `HANDOFF` и `ROUTE_STATE`. `SpiMaster` повторяет `LINK_EDGE_STATE` и блоб раскладки.
- **U2.** `EdgeRelay` хранит касание ПК2 с TTL, `last_ready_id` и состояние от U1. `choose_reply` решает, что везти в ответе SPI. `AddressService` принимает новые CDC-сообщения и шлёт push.

**Tech Stack:** C++17 (Pico SDK, TinyUSB), CMake/Ninja, нативный тест-раннер `tests/firmware_native` (`TEST_CASE`/`CHECK`), Python 3.12, PySide6 (QtSerialPort для стенда), pytest.

**Spec:** `docs/superpowers/specs/2026-10-04-screen-extension-design.md`. Раздел «Уточнения после чтения кода» в конце спеки имеет приоритет над основным текстом.

## Global Constraints

- **Идентификаторы** меняются только в `protocol/schema.json`; всё остальное генерирует `tools/generate_protocol.py`:
  - `capabilities.SCREEN_EXTENSION = 16384`;
  - `cdc_messages`: `EDGE_CONTACT = 25`, `HANDOFF_READY = 26`, `EXCHANGE_LAYOUT = 27`, `HANDOFF = 28`, `ROUTE_STATE = 29`, `GET_EDGE_STATS = 30`;
  - `spi_messages`: `LINK_EDGE_STATE = 10`, `ENDPOINT_EDGE_STATE = 11`, `LAYOUT_STATE = 12`, `ENDPOINT_LAYOUT_STATE = 13`;
  - `edge_sides`: `LEFT 1, RIGHT 2, TOP 3, BOTTOM 4`;
  - `route_reasons`: `BOOT 0, BINDING 1, EDGE 2, MACRO 3, PROFILE 4`.
- **`PROTOCOL_VERSION_MINOR` не меняется.** Поддержку определяет возможность `SCREEN_EXTENSION`.
- **Сообщения края вне номеров последовательности.** `EDGE_CONTACT`, `HANDOFF_READY`, `HANDOFF` и `ROUTE_STATE` идут с `sequence = 0`, не получают ответа и не трогают состояние номеров и кеш повторов.
- **Размеры полезной нагрузки:**

  | Сообщение | Байт |
  | --- | --- |
  | `EdgeContact` | 9 |
  | `Handoff` | 5 |
  | `RouteState` | 3 |
  | `LinkEdgeState` | 9 |
  | `EndpointEdgeState` | 12 |
  | `HANDOFF_READY` | 2 |
  | ответ `GET_EDGE_STATS` | 1 + 42 |
  | блоб раскладки | не больше 51 |

  Все многобайтовые поля — little-endian.
- **Время:**

  | Константа | Значение |
  | --- | --- |
  | TTL касания | 250 мс |
  | Кулдаун | 250 мс |
  | Разрыв давления | 200 мс |
  | Тайм-аут ворот | 80 мс |
  | Тайм-аутов до пропуска ожидания | 3 |
  | Повтор `LINK_EDGE_STATE` | 20 мс, вместо `HEARTBEAT` |
  | Повтор `ENDPOINT_EDGE_STATE` | 20 мс |
  | Повтор `ROUTE_STATE` | 1000 мс |
  | Окно «U2 умеет край» | 500 мс |
  | Интервал блоба U1 | 250 мс |
  | Слот блоба U2 | раз в 64 ответа, сдвиг 32 |

- **Стек ядра 0 и ядра 1 на U1 — по 2 КБ.** Новые локальные буферы в функциях цикла — не больше 64 байт.
- **Python-тесты** — только `.venv/Scripts/python.exe -m pytest`, и решает код возврата, а не зелёная сводка.
- **Нативные тесты** — только после `cmake --build --preset native --clean-first`: Ninja не отслеживает заголовки.
- **Прошивки:** U1 — пресет `pico-pio-usb-release` (это продакшн-бэкенд; `pico-release` — CH375). U2 — из `pico-release`. Эталонная сборка `pico-pio-usb-reference-release` должна собираться.
- **Комментарии** — на языке окружающего кода: прошивка, `device/` и `tools/` по-английски.
- **Мутации.** Каждая защита, добавленная задачей, проверяется в Task 14.

## Review Focus

- **Мышь, отпущенная воротами, не должна прыгнуть дальше, чем рука прошла.** Если `take_snapshot` под закрытыми воротами потребит дельты, накопленное потеряется. Если ворота забудут открыться, мышь пропадёт. Тесты Task 5 закрепляют оба направления.
- **Порядок «`RouteMoved` раньше первой дельты».** Дельту, перешедшую порог, ядро 1 отправляет уже после `RouteMoved`. Тест Task 6 сверяет индексы команд в очереди.
- **Уведомление посреди записи конфига.** `EDGE_CONTACT`, пришедший между `WRITE_CHUNK`, не должен ломать номер следующего запроса и кеш повторов. Тест Task 8.
- **Умерший агент ПК2.** U2 перестаёт сообщать касание активным через 250 мс, хотя продолжает его повторять. Тест Task 11.
- **Тишина U2 при работающем ПК1.** Если U2 не прислала `ENDPOINT_EDGE_STATE` за 500 мс, переход на ПК2 запрещён (старая прошивка U2, оборванный провод). Тест Task 7.

## Карта файлов

| Файл | Что делает | Задача |
| --- | --- | --- |
| `protocol/schema.json`, `tools/generate_protocol.py`, `firmware/common/protocol/frame.cpp` | идентификаторы | 1 |
| `docs/protocol/compatibility.md` | описание сообщений | 1 |
| `firmware/common/link/edge.{hpp,cpp}` (новый) | кодеки, константы, `LayoutBook` | 2 |
| `configurator/src/duo_input/device/edge_messages.py` (новый) | Python-кодек | 3 |
| `firmware/u1_main/mapping/edge_arbiter.{hpp,cpp}` (новый) | давление и условия | 4 |
| `firmware/common/link/handoff_gate.{hpp,cpp}` (новый), `firmware/common/runtime/output_command.hpp`, `firmware/u1_main/output_runtime.{hpp,cpp}` | ворота | 5 |
| `firmware/u1_main/mapping/engine.{hpp,cpp}`, `firmware/u1_main/core1_runtime.{hpp,cpp}` | переход на ядре 1 | 6 |
| `firmware/u1_main/edge_service.{hpp,cpp}` (новый) | ядро 0 U1 | 7 |
| `firmware/u1_main/config_service.{hpp,cpp}` | CDC U1 | 8 |
| `firmware/u1_main/spi_master{.hpp,.cpp,_poll.cpp}`, `firmware/common/link/spi_protocol.cpp` | SPI U1 | 9 |
| `firmware/u1_main/main.cpp`, `firmware/u1_main/CMakeLists.txt`, `firmware/u1_reference/CMakeLists.txt` | проводка U1 | 10 |
| `firmware/u2_endpoint/edge_relay.{hpp,cpp}`, `firmware/u2_endpoint/reply_choice.{hpp,cpp}` (новые) | логика U2 | 11 |
| `firmware/u2_endpoint/{address_service.*,spi_slave.*,main.cpp,CMakeLists.txt}` | проводка U2 | 12 |
| `tools/edge_bench.py`, `tools/test_edge_bench.py` (новые) | стенд | 13 |
| `docs/superpowers/records/2026-10-04-screen-extension-firmware.md` (новый) | итог | 14 |

---

### Task 1: Идентификаторы протокола

**Files:**
- Modify: `protocol/schema.json`, `tools/generate_protocol.py`, `firmware/common/protocol/frame.cpp`, `docs/protocol/compatibility.md`
- Regenerate: `firmware/common/protocol/generated.hpp`, `configurator/src/duo_input/generated/protocol.py`
- Test: `configurator/tests/test_generated_protocol.py`, `tests/firmware_native/test_frames.cpp`

**Interfaces:**
- Produces (C++, namespace `duo_input::protocol`):
  - `CdcMessageType::{EDGE_CONTACT, HANDOFF_READY, EXCHANGE_LAYOUT, HANDOFF, ROUTE_STATE, GET_EDGE_STATS}`;
  - `SpiMessageType::{LINK_EDGE_STATE, ENDPOINT_EDGE_STATE, LAYOUT_STATE, ENDPOINT_LAYOUT_STATE}`;
  - `Capability::SCREEN_EXTENSION`;
  - `enum class EdgeSide : std::uint8_t { LEFT, RIGHT, TOP, BOTTOM }`;
  - `enum class RouteReason : std::uint8_t { BOOT, BINDING, EDGE, MACRO, PROFILE }`.
- Produces (Python, `duo_input.generated.protocol`): одноимённые члены `CdcMessageType`, `SpiMessageType`, `Capability`, а также `EdgeSide(IntEnum)` и `RouteReason(IntEnum)`.

- [ ] **Step 1: Написать падающий Python-тест.** Добавить в `configurator/tests/test_generated_protocol.py`:

```python
def test_the_screen_extension_identifiers_are_generated():
    from duo_input.generated import protocol

    assert protocol.Capability.SCREEN_EXTENSION == 16384
    assert protocol.CdcMessageType.EDGE_CONTACT == 25
    assert protocol.CdcMessageType.HANDOFF_READY == 26
    assert protocol.CdcMessageType.EXCHANGE_LAYOUT == 27
    assert protocol.CdcMessageType.HANDOFF == 28
    assert protocol.CdcMessageType.ROUTE_STATE == 29
    assert protocol.CdcMessageType.GET_EDGE_STATS == 30
    assert protocol.SpiMessageType.LINK_EDGE_STATE == 10
    assert protocol.SpiMessageType.ENDPOINT_EDGE_STATE == 11
    assert protocol.SpiMessageType.LAYOUT_STATE == 12
    assert protocol.SpiMessageType.ENDPOINT_LAYOUT_STATE == 13
    assert [side.value for side in protocol.EdgeSide] == [1, 2, 3, 4]
    assert protocol.EdgeSide.LEFT == 1 and protocol.EdgeSide.BOTTOM == 4
    assert protocol.RouteReason.BOOT == 0
    assert protocol.RouteReason.EDGE == 2
    assert protocol.RouteReason.PROFILE == 4
```

- [ ] **Step 2: Написать падающий нативный тест.** Добавить в `tests/firmware_native/test_frames.cpp`, рядом с существующими тестами SPI-кадров:

```cpp
TEST_CASE(screen_extension_frames_are_known_in_both_transports) {
    using duo_input::protocol::ByteView;
    using duo_input::protocol::DecodeResult;
    using duo_input::protocol::MutableByteView;
    using duo_input::protocol::SpiFrame;
    using duo_input::protocol::SpiMessageType;
    for (SpiMessageType type :
         {SpiMessageType::LINK_EDGE_STATE, SpiMessageType::ENDPOINT_EDGE_STATE,
          SpiMessageType::LAYOUT_STATE, SpiMessageType::ENDPOINT_LAYOUT_STATE}) {
        const std::uint8_t payload[3] = {1, 2, 0};
        SpiFrame frame;
        frame.type = type;
        frame.payload = ByteView{payload, sizeof(payload)};
        std::uint8_t wire[64] = {};
        std::size_t written = 0;
        CHECK(duo_input::protocol::encode_spi_frame(frame, MutableByteView{wire, sizeof(wire)},
                                                    written));
        DecodeResult result;
        CHECK(duo_input::protocol::decode_spi_frame(ByteView{wire, sizeof(wire)}, result));
        CHECK(result.spi.type == type);
    }

    using duo_input::protocol::CdcFrame;
    using duo_input::protocol::CdcMessageType;
    for (CdcMessageType type :
         {CdcMessageType::EDGE_CONTACT, CdcMessageType::HANDOFF_READY,
          CdcMessageType::EXCHANGE_LAYOUT, CdcMessageType::HANDOFF, CdcMessageType::ROUTE_STATE,
          CdcMessageType::GET_EDGE_STATS}) {
        const std::uint8_t payload[2] = {7, 0};
        CdcFrame frame;
        frame.type = type;
        frame.payload = ByteView{payload, sizeof(payload)};
        std::uint8_t wire[64] = {};
        std::uint8_t scratch[64] = {};
        std::size_t written = 0;
        CHECK(duo_input::protocol::encode_cdc_frame(frame, MutableByteView{wire, sizeof(wire)},
                                                    MutableByteView{scratch, sizeof(scratch)},
                                                    written));
        std::uint8_t decoded[64] = {};
        DecodeResult result;
        CHECK(duo_input::protocol::decode_cdc_frame(ByteView{wire, written},
                                                    MutableByteView{decoded, sizeof(decoded)},
                                                    result));
        CHECK(result.cdc.type == type);
    }
}
```

- [ ] **Step 3: Убедиться, что тесты падают.**
  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/test_generated_protocol.py -q`
  Expected: FAIL, `AttributeError: SCREEN_EXTENSION`. Нативный тест не компилируется — это тоже падение.

- [ ] **Step 4: Схема.** В `protocol/schema.json`:
  - `capabilities`: добавить `"SCREEN_EXTENSION": 16384`;
  - `cdc_messages`: добавить `"EDGE_CONTACT": 25`, `"EXCHANGE_LAYOUT": 27`, `"GET_EDGE_STATS": 30`, `"HANDOFF": 28`, `"HANDOFF_READY": 26`, `"ROUTE_STATE": 29` (в алфавитном порядке ключей, как в файле);
  - `spi_messages`: добавить `"ENDPOINT_EDGE_STATE": 11`, `"ENDPOINT_LAYOUT_STATE": 13`, `"LAYOUT_STATE": 12`, `"LINK_EDGE_STATE": 10`;
  - новые разделы верхнего уровня, после `input_backends`:

```json
  "edge_sides": {
    "BOTTOM": 4,
    "LEFT": 1,
    "RIGHT": 2,
    "TOP": 3
  },
  "route_reasons": {
    "BINDING": 1,
    "BOOT": 0,
    "EDGE": 2,
    "MACRO": 3,
    "PROFILE": 4
  },
```

- [ ] **Step 5: Генератор.** В `tools/generate_protocol.py` в **оба** кортежа перечислений (в `render_cpp` после `("InputBackend", "input_backends"),` и в `render_python` после него же) добавить:

```python
        # Which edge of a screen a pointer leaves or enters by, and why the
        # routes last moved. Both travel in the screen-extension messages.
        ("EdgeSide", "edge_sides"),
        ("RouteReason", "route_reasons"),
```

- [ ] **Step 6: Перегенерировать.**
  Run: `.venv/Scripts/python.exe tools/generate_protocol.py`, затем `.venv/Scripts/python.exe tools/generate_protocol.py --check`
  Expected: второй запуск завершается с кодом 0. Открыть `generated.hpp` и убедиться, что `enum class EdgeSide` и `enum class RouteReason` присутствуют, а члены `EdgeSide` упорядочены по значению 1..4. Если генератор сортирует по имени, Python-тест `[side.value for side in protocol.EdgeSide] == [1, 2, 3, 4]` упадёт. Тогда в тесте заменить проверку на `sorted(side.value for side in protocol.EdgeSide) == [1, 2, 3, 4]`: порядок объявления для протокола не важен.

- [ ] **Step 7: `is_known`.** В `firmware/common/protocol/frame.cpp`:
  - в `is_known(CdcMessageType)` добавить в список `return true` метки `EDGE_CONTACT`, `EXCHANGE_LAYOUT`, `GET_EDGE_STATS`, `HANDOFF`, `HANDOFF_READY`, `ROUTE_STATE`;
  - в `is_known(SpiMessageType)` добавить `ENDPOINT_EDGE_STATE`, `ENDPOINT_LAYOUT_STATE`, `LAYOUT_STATE`, `LINK_EDGE_STATE`.

- [ ] **Step 8: Документация.** В `docs/protocol/compatibility.md` после раздела `EXCHANGE_ADDRESSES` добавить:

```markdown
## Screen extension (capability SCREEN_EXTENSION, 0x4000)

The pointer crosses to the other computer through the edge of the screen. Each computer's agent
tells its own board where its pointer presses against the edge that leads to the other computer;
U1 decides, from the raw mouse, whether the operator is pushing through, and moves the routes the
same way a route binding does. The computer the pointer arrives at is told where along its edge
to put it.

### Outside the request sequence

`EDGE_CONTACT` (0x19), `HANDOFF_READY` (0x1A), `HANDOFF` (0x1C) and `ROUTE_STATE` (0x1D) carry
sequence 0, are never answered, and neither advance nor check the request sequence nor touch the
retry cache. A board that did not grant `SCREEN_EXTENSION` drops the first two silently and never
sends the last two. `EXCHANGE_LAYOUT` (0x1B) and `GET_EDGE_STATS` (0x1E) are ordinary requests.

| Message | Direction | Payload |
| --- | --- | --- |
| EDGE_CONTACT | host → board | `gen u16, active u8 (0/1), side u8, neighbor u8, t u16, threshold u16` |
| HANDOFF_READY | host → board | `id u16` |
| HANDOFF | board → host | `id u16, side u8, t u16` — the edge the pointer enters by |
| ROUTE_STATE | board → host | `mouse u8, keyboard u8, reason u8` — on change and every second |
| EXCHANGE_LAYOUT | request/reply | request: this host's blob (0..51 bytes, empty = only ask); reply: `[error][peer blob]` |
| GET_EDGE_STATS | request/reply | reply: `[error]`, ten u32 counters, `max_ready_ms u16` |

`side` is `edge_sides` (LEFT 1, RIGHT 2, TOP 3, BOTTOM 4); for an inactive contact it may be 0.
An active contact needs a valid side and a nonzero threshold. `t` is the position along the edge,
0..65535. `reason` is `route_reasons`.

### On the SPI link

| Type | Direction | Payload |
| --- | --- | --- |
| LINK_EDGE_STATE (0x0A) | U1 → U2 | `RouteState`, `handoff_active u8`, `Handoff` — on change, and every 20 ms in place of HEARTBEAT |
| ENDPOINT_EDGE_STATE (0x0B) | U2 → U1 | PC2's `EdgeContact`, `last_ready_id u16`, `flags u8` (bit 0: U2's USB mounted and not suspended) |
| LAYOUT_STATE (0x0C) | U1 → U2 | PC1's layout blob, every 250 ms |
| ENDPOINT_LAYOUT_STATE (0x0D) | U2 → U1 | PC2's layout blob, once in 64 replies |

The boards never read the blobs. U2 stops reporting PC2's contact as active 250 ms after PC2's
agent last refreshed it.
```

- [ ] **Step 9: Прогнать тесты.**
  Run:

  ```
  .venv/Scripts/python.exe -m pytest configurator/tests tests -q; echo "exit=$?"
  cmake --preset native
  cmake --build --preset native --clean-first
  ctest --preset native
  ```

  Expected: `exit=0`, всё зелёное. Если тест паритета эмулятора или полного списка возможностей укажет на новые значения, добавить их туда, куда он указывает. Эмулятор (`device/emulator.py`) пока новые типы не обслуживает: это задача плана 3, а в этом плане приложение их не шлёт.

- [ ] **Step 10: Commit.**

```bash
git add protocol/schema.json tools/generate_protocol.py firmware/common/protocol configurator/src/duo_input/generated docs/protocol/compatibility.md configurator/tests/test_generated_protocol.py tests/firmware_native/test_frames.cpp
git commit -m "feat(protocol): screen extension identifiers"
```

---

### Task 2: Кодеки края и `LayoutBook` в общей прошивке

**Files:**
- Create: `firmware/common/link/edge.hpp`, `firmware/common/link/edge.cpp`
- Modify: `firmware/common/CMakeLists.txt` (добавить `link/edge.cpp` в `duo_common`)
- Create: `tests/firmware_native/test_edge_messages.cpp`
- Modify: `tests/firmware_native/CMakeLists.txt`

**Interfaces:**
- Consumes: `protocol::EdgeSide`, `protocol::RouteReason`, `protocol::MouseRoute`, `protocol::KeyboardRoute` (Task 1).
- Produces (namespace `duo_input::link`, полный заголовок ниже): константы времени; `EdgeContact`, `Handoff`, `RouteState`, `LinkEdgeState`, `EndpointEdgeState`, `LayoutBlob`, `LayoutBook`; `encode_*`/`decode_*`; `decode_handoff_ready`; `opposite`; размеры `kEdgeContactSize`, `kHandoffSize`, `kRouteStateSize`, `kLinkEdgeStateSize`, `kEndpointEdgeStateSize`, `kHandoffReadySize`, `kEdgeStatsSize`, `kLayoutBlobMaxSize`; флаг `kEndpointUsbReady`.

- [ ] **Step 1: Написать падающие тесты.** Создать `tests/firmware_native/test_edge_messages.cpp`:

```cpp
// The wire shape of everything the two boards and the two computers say about
// the edge of the screen. Python's device/edge_messages.py checks the same
// byte strings, so the two cannot drift apart.

#include <cstring>

#include "link/edge.hpp"
#include "test_support.hpp"

using duo_input::link::EdgeContact;
using duo_input::link::EndpointEdgeState;
using duo_input::link::Handoff;
using duo_input::link::LayoutBook;
using duo_input::link::LinkEdgeState;
using duo_input::link::RouteState;
using duo_input::protocol::ByteView;
using duo_input::protocol::EdgeSide;
using duo_input::protocol::KeyboardRoute;
using duo_input::protocol::MouseRoute;
using duo_input::protocol::MutableByteView;
using duo_input::protocol::RouteReason;

namespace {

EdgeContact right_edge_contact() {
    EdgeContact contact;
    contact.gen = 0x0102;
    contact.active = true;
    contact.side = EdgeSide::RIGHT;
    contact.neighbor = 0;
    contact.t = 0x8000;
    contact.threshold = 120;
    return contact;
}

}  // namespace

TEST_CASE(a_contact_is_nine_bytes_in_a_fixed_order) {
    std::uint8_t wire[16] = {};
    std::size_t written = 0;
    CHECK(duo_input::link::encode_edge_contact(right_edge_contact(),
                                               MutableByteView{wire, sizeof(wire)}, written));
    CHECK_EQ(written, 9u);
    const std::uint8_t expected[9] = {0x02, 0x01, 1, 2, 0, 0x00, 0x80, 120, 0};
    CHECK(std::memcmp(wire, expected, 9) == 0);

    EdgeContact back;
    CHECK(duo_input::link::decode_edge_contact(ByteView{wire, written}, back));
    CHECK_EQ(back.gen, 0x0102u);
    CHECK(back.active);
    CHECK(back.side == EdgeSide::RIGHT);
    CHECK_EQ(back.t, 0x8000u);
    CHECK_EQ(back.threshold, 120u);
}

TEST_CASE(an_inactive_contact_may_name_no_side) {
    const std::uint8_t wire[9] = {5, 0, 0, 0, 0, 0, 0, 0, 0};
    EdgeContact back;
    CHECK(duo_input::link::decode_edge_contact(ByteView{wire, sizeof(wire)}, back));
    CHECK_FALSE(back.active);
    CHECK_EQ(back.gen, 5u);
}

TEST_CASE(an_active_contact_with_no_side_is_refused) {
    const std::uint8_t wire[9] = {5, 0, 1, 0, 0, 0, 0, 120, 0};
    EdgeContact back;
    CHECK_FALSE(duo_input::link::decode_edge_contact(ByteView{wire, sizeof(wire)}, back));
}

TEST_CASE(an_active_contact_with_a_side_past_bottom_is_refused) {
    const std::uint8_t wire[9] = {5, 0, 1, 5, 0, 0, 0, 120, 0};
    EdgeContact back;
    CHECK_FALSE(duo_input::link::decode_edge_contact(ByteView{wire, sizeof(wire)}, back));
}

TEST_CASE(an_active_contact_with_no_threshold_is_refused) {
    // A threshold of zero would cross on a pointer merely resting at the edge.
    const std::uint8_t wire[9] = {5, 0, 1, 2, 0, 0, 0, 0, 0};
    EdgeContact back;
    CHECK_FALSE(duo_input::link::decode_edge_contact(ByteView{wire, sizeof(wire)}, back));
}

TEST_CASE(an_active_byte_other_than_zero_or_one_is_refused) {
    const std::uint8_t wire[9] = {5, 0, 2, 2, 0, 0, 0, 120, 0};
    EdgeContact back;
    CHECK_FALSE(duo_input::link::decode_edge_contact(ByteView{wire, sizeof(wire)}, back));
}

TEST_CASE(a_contact_of_the_wrong_length_is_refused_and_changes_nothing) {
    EdgeContact back = right_edge_contact();
    const std::uint8_t wire[8] = {9, 0, 1, 1, 0, 0, 0, 1};
    CHECK_FALSE(duo_input::link::decode_edge_contact(ByteView{wire, sizeof(wire)}, back));
    CHECK_EQ(back.gen, 0x0102u);
}

TEST_CASE(a_handoff_round_trips_and_refuses_a_bad_side) {
    Handoff handoff;
    handoff.id = 0x0304;
    handoff.side = EdgeSide::LEFT;
    handoff.t = 0x4000;
    std::uint8_t wire[8] = {};
    std::size_t written = 0;
    CHECK(duo_input::link::encode_handoff(handoff, MutableByteView{wire, sizeof(wire)}, written));
    CHECK_EQ(written, 5u);
    const std::uint8_t expected[5] = {0x04, 0x03, 1, 0x00, 0x40};
    CHECK(std::memcmp(wire, expected, 5) == 0);

    Handoff back;
    CHECK(duo_input::link::decode_handoff(ByteView{wire, written}, back));
    CHECK_EQ(back.id, 0x0304u);
    CHECK(back.side == EdgeSide::LEFT);

    wire[2] = 0;
    CHECK_FALSE(duo_input::link::decode_handoff(ByteView{wire, written}, back));
}

TEST_CASE(a_route_state_round_trips_and_refuses_impossible_routes) {
    RouteState state;
    state.mouse = MouseRoute::PC2;
    state.keyboard = KeyboardRoute::BOTH;
    state.reason = RouteReason::EDGE;
    std::uint8_t wire[4] = {};
    std::size_t written = 0;
    CHECK(duo_input::link::encode_route_state(state, MutableByteView{wire, sizeof(wire)},
                                              written));
    CHECK_EQ(written, 3u);
    const std::uint8_t expected[3] = {2, 3, 2};
    CHECK(std::memcmp(wire, expected, 3) == 0);

    RouteState back;
    CHECK(duo_input::link::decode_route_state(ByteView{wire, written}, back));
    CHECK(back.keyboard == KeyboardRoute::BOTH);

    const std::uint8_t mouse_on_both[3] = {3, 1, 0};
    CHECK_FALSE(duo_input::link::decode_route_state(ByteView{mouse_on_both, 3}, back));
    const std::uint8_t keyboard_four[3] = {1, 4, 0};
    CHECK_FALSE(duo_input::link::decode_route_state(ByteView{keyboard_four, 3}, back));
    const std::uint8_t reason_nine[3] = {1, 1, 9};
    CHECK_FALSE(duo_input::link::decode_route_state(ByteView{reason_nine, 3}, back));
}

TEST_CASE(a_link_edge_state_carries_the_handoff_only_while_it_is_pending) {
    LinkEdgeState state;
    state.route.mouse = MouseRoute::PC2;
    state.route.keyboard = KeyboardRoute::PC2;
    state.route.reason = RouteReason::EDGE;
    state.handoff_active = true;
    state.handoff.id = 9;
    state.handoff.side = EdgeSide::LEFT;
    state.handoff.t = 100;
    std::uint8_t wire[16] = {};
    std::size_t written = 0;
    CHECK(duo_input::link::encode_link_edge_state(state, MutableByteView{wire, sizeof(wire)},
                                                  written));
    CHECK_EQ(written, 9u);
    const std::uint8_t expected[9] = {2, 2, 2, 1, 9, 0, 1, 100, 0};
    CHECK(std::memcmp(wire, expected, 9) == 0);

    LinkEdgeState back;
    CHECK(duo_input::link::decode_link_edge_state(ByteView{wire, written}, back));
    CHECK(back.handoff_active);
    CHECK_EQ(back.handoff.id, 9u);

    // Not pending: the handoff fields are not read, so a zero side is fine.
    const std::uint8_t idle[9] = {1, 1, 0, 0, 0, 0, 0, 0, 0};
    CHECK(duo_input::link::decode_link_edge_state(ByteView{idle, sizeof(idle)}, back));
    CHECK_FALSE(back.handoff_active);
}

TEST_CASE(an_endpoint_edge_state_is_twelve_bytes) {
    EndpointEdgeState state;
    state.contact = right_edge_contact();
    state.last_ready_id = 0x0506;
    state.flags = duo_input::link::kEndpointUsbReady;
    std::uint8_t wire[16] = {};
    std::size_t written = 0;
    CHECK(duo_input::link::encode_endpoint_edge_state(
        state, MutableByteView{wire, sizeof(wire)}, written));
    CHECK_EQ(written, 12u);
    CHECK_EQ(wire[9], 0x06u);
    CHECK_EQ(wire[10], 0x05u);
    CHECK_EQ(wire[11], 0x01u);

    EndpointEdgeState back;
    CHECK(duo_input::link::decode_endpoint_edge_state(ByteView{wire, written}, back));
    CHECK_EQ(back.last_ready_id, 0x0506u);
    CHECK_EQ(back.contact.threshold, 120u);
}

TEST_CASE(handoff_ready_is_exactly_two_bytes) {
    const std::uint8_t two[2] = {0x34, 0x12};
    std::uint16_t id = 0;
    CHECK(duo_input::link::decode_handoff_ready(ByteView{two, 2}, id));
    CHECK_EQ(id, 0x1234u);
    const std::uint8_t three[3] = {1, 0, 0};
    CHECK_FALSE(duo_input::link::decode_handoff_ready(ByteView{three, 3}, id));
}

TEST_CASE(each_side_has_an_opposite) {
    CHECK(duo_input::link::opposite(EdgeSide::LEFT) == EdgeSide::RIGHT);
    CHECK(duo_input::link::opposite(EdgeSide::RIGHT) == EdgeSide::LEFT);
    CHECK(duo_input::link::opposite(EdgeSide::TOP) == EdgeSide::BOTTOM);
    CHECK(duo_input::link::opposite(EdgeSide::BOTTOM) == EdgeSide::TOP);
}

TEST_CASE(a_layout_book_keeps_a_blob_up_to_fifty_one_bytes) {
    LayoutBook book;
    std::uint8_t blob[52] = {};
    for (std::size_t i = 0; i < sizeof(blob); ++i) blob[i] = static_cast<std::uint8_t>(i + 1);

    CHECK_FALSE(book.set_local(ByteView{blob, 0}));
    CHECK_FALSE(book.has_local());
    CHECK_FALSE(book.set_local(ByteView{blob, 52}));
    CHECK_FALSE(book.has_local());
    CHECK(book.set_local(ByteView{blob, 51}));
    CHECK(book.has_local());
    CHECK_EQ(book.local().size, 51u);
    CHECK_EQ(book.local().bytes[50], 51u);

    CHECK(book.accept_peer(ByteView{blob, 3}));
    CHECK_EQ(book.peer().size, 3u);
    CHECK_FALSE(book.accept_peer(ByteView{blob, 52}));
    CHECK_EQ(book.peer().size, 3u);
}

TEST_CASE(a_layout_book_is_due_on_its_interval_once_it_has_a_blob) {
    LayoutBook book;
    CHECK_FALSE(book.local_due(0));
    const std::uint8_t blob[2] = {1, 2};
    book.set_local(ByteView{blob, 2});
    CHECK(book.local_due(0));
    book.mark_local_sent(0);
    CHECK_FALSE(book.local_due(duo_input::link::kLayoutIntervalMs - 1));
    CHECK(book.local_due(duo_input::link::kLayoutIntervalMs));
}

TEST_CASE(a_layout_reply_slot_comes_half_a_cycle_out_of_step) {
    LayoutBook book;
    const std::uint8_t blob[2] = {1, 2};
    book.set_local(ByteView{blob, 2});
    std::uint32_t first = 0;
    for (std::uint32_t reply = 1; reply <= 200; ++reply) {
        if (book.take_reply_slot()) {
            first = reply;
            break;
        }
    }
    // The address book's first slot is reply 64; this one must not share it.
    CHECK_EQ(first, duo_input::link::kEndpointLayoutEvery / 2);
}
```

- [ ] **Step 2: Зарегистрировать тест.** В `tests/firmware_native/CMakeLists.txt` после блока `duo_host_addresses_test`:

```cmake
add_executable(duo_edge_messages_test
    test_main.cpp
    test_edge_messages.cpp
)

target_link_libraries(duo_edge_messages_test PRIVATE duo_common)

if(MSVC)
    target_compile_options(duo_edge_messages_test PRIVATE /EHsc)
endif()

add_test(NAME edge_messages COMMAND duo_edge_messages_test)
```

- [ ] **Step 3: Убедиться, что тест не собирается.**
  Run: `cmake --build --preset native --clean-first`
  Expected: FAIL, `link/edge.hpp: No such file`.

- [ ] **Step 4: Заголовок.** Создать `firmware/common/link/edge.hpp`:

```cpp
#pragma once

// What the two boards and the two computers say to each other about the edge
// of the screen.
//
// A computer tells its own board where its pointer is pressing against the
// edge that leads to the other computer. U1 decides from the raw mouse whether
// the operator is pushing through, moves the routes, and the computer the
// pointer arrives at is told where along its edge to put it. This file is the
// wire shape of those messages and the timing they are held to; deciding is
// u1_main/mapping/edge_arbiter.hpp and holding the pointer back is
// link/handoff_gate.hpp.
//
// Like everything else on the SPI link, the board-to-board messages carry an
// absolute state and are repeated: a lost frame costs a repeat, never a
// disagreement.

#include <cstddef>
#include <cstdint>

#include "protocol/bytes.hpp"
#include "protocol/generated.hpp"

namespace duo_input::link {

/// A contact its computer has not refreshed for this long is no contact. The
/// agent refreshes every 100 ms, so this survives two lost refreshes.
inline constexpr std::uint32_t kEdgeContactTtlMs = 250;
/// After any route change, no edge crossing for this long.
inline constexpr std::uint32_t kEdgeCooldownMs = 250;
/// Pushing outward has to be continuous: a pause this long starts it over.
inline constexpr std::uint32_t kEdgePressureGapMs = 200;
/// How long U1 holds the pointer back for the arriving computer to place it.
inline constexpr std::uint32_t kHandoffTimeoutMs = 80;
/// After this many unanswered handoffs in a row to one computer, U1 stops
/// waiting for it until it answers one.
inline constexpr std::uint8_t kHandoffMissesBeforeSkip = 3;
/// U2 repeats ENDPOINT_EDGE_STATE at least this often.
inline constexpr std::uint32_t kEndpointEdgeRefreshMs = 20;
/// The boards repeat ROUTE_STATE to their computer at least this often.
inline constexpr std::uint32_t kRouteStatePushMs = 1000;
/// U1 lets the pointer cross to PC2 only if U2 sent ENDPOINT_EDGE_STATE this
/// recently - which an older U2, or a severed link, never does.
inline constexpr std::uint32_t kEdgeCapableWindowMs = 500;
/// U1 repeats its computer's layout blob this often.
inline constexpr std::uint32_t kLayoutIntervalMs = 250;
/// U2 answers with its computer's layout blob once in this many replies.
inline constexpr std::uint32_t kEndpointLayoutEvery = 64;

/// One computer's pointer against one edge.
struct EdgeContact {
    /// Changes every time the pointer arrives at an edge afresh.
    std::uint16_t gen = 0;
    bool active = false;
    protocol::EdgeSide side = protocol::EdgeSide::RIGHT;
    /// Which computer that edge leads to. Reserved: the firmware always takes
    /// it to mean "the other one".
    std::uint8_t neighbor = 0;
    /// Where along the edge, 0..65535.
    std::uint16_t t = 0;
    /// Outward mouse counts that make a crossing.
    std::uint16_t threshold = 0;
};

inline constexpr std::size_t kEdgeContactSize = 9;

/// Where the pointer is to appear on the computer it is arriving at.
struct Handoff {
    std::uint16_t id = 0;
    /// The edge it comes in by.
    protocol::EdgeSide side = protocol::EdgeSide::LEFT;
    std::uint16_t t = 0;
};

inline constexpr std::size_t kHandoffSize = 5;

/// Where input is going, and why it last moved.
struct RouteState {
    protocol::MouseRoute mouse = protocol::MouseRoute::PC1;
    protocol::KeyboardRoute keyboard = protocol::KeyboardRoute::PC1;
    protocol::RouteReason reason = protocol::RouteReason::BOOT;
};

inline constexpr std::size_t kRouteStateSize = 3;

/// U1 to U2: the routes, and the handoff being waited on if there is one.
struct LinkEdgeState {
    RouteState route{};
    bool handoff_active = false;
    Handoff handoff{};
};

inline constexpr std::size_t kLinkEdgeStateSize = kRouteStateSize + 1 + kHandoffSize;

/// ENDPOINT_EDGE_STATE flag: U2's USB is configured and not suspended.
inline constexpr std::uint8_t kEndpointUsbReady = 0x01;

/// U2 to U1: PC2's contact, the last handoff PC2 placed, and U2's USB.
struct EndpointEdgeState {
    EdgeContact contact{};
    std::uint16_t last_ready_id = 0;
    std::uint8_t flags = 0;
};

inline constexpr std::size_t kEndpointEdgeStateSize = kEdgeContactSize + 2 + 1;

inline constexpr std::size_t kHandoffReadySize = 2;

/// GET_EDGE_STATS after its error byte: ten u32 counters and one u16.
inline constexpr std::size_t kEdgeStatsSize = 10 * 4 + 2;

bool encode_edge_contact(const EdgeContact& contact, protocol::MutableByteView output,
                         std::size_t& written);
/// Refuses a wrong length, an active byte other than 0 or 1, and - for an
/// active contact - a side outside LEFT..BOTTOM or a zero threshold.
bool decode_edge_contact(protocol::ByteView payload, EdgeContact& contact);

bool encode_handoff(const Handoff& handoff, protocol::MutableByteView output,
                    std::size_t& written);
bool decode_handoff(protocol::ByteView payload, Handoff& handoff);

bool encode_route_state(const RouteState& state, protocol::MutableByteView output,
                        std::size_t& written);
bool decode_route_state(protocol::ByteView payload, RouteState& state);

bool encode_link_edge_state(const LinkEdgeState& state, protocol::MutableByteView output,
                            std::size_t& written);
bool decode_link_edge_state(protocol::ByteView payload, LinkEdgeState& state);

bool encode_endpoint_edge_state(const EndpointEdgeState& state,
                                protocol::MutableByteView output, std::size_t& written);
bool decode_endpoint_edge_state(protocol::ByteView payload, EndpointEdgeState& state);

bool decode_handoff_ready(protocol::ByteView payload, std::uint16_t& id);

/// The edge a pointer comes in by, given the edge it left by.
protocol::EdgeSide opposite(protocol::EdgeSide side);

// ------------------------------------------------------------ layout blob

/// The largest blob a computer may hand its board: what one SPI frame's
/// payload holds, less one byte of slack.
inline constexpr std::size_t kLayoutBlobMaxSize = 51;

struct LayoutBlob {
    std::uint8_t size = 0;
    std::uint8_t bytes[kLayoutBlobMaxSize] = {};
};

/// Both computers' layout blobs, as one board holds them.
///
/// The boards never read them: what is in them is between the two computers,
/// and keeping it opaque here means the layout can change shape without a
/// firmware update. Nothing is kept in flash.
class LayoutBook {
public:
    /// False, and nothing kept, for an empty or oversized blob.
    bool set_local(protocol::ByteView blob);
    bool has_local() const { return local_.size != 0; }
    const LayoutBlob& local() const { return local_; }
    const LayoutBlob& peer() const { return peer_; }
    /// False, and the previous one kept, for an oversized blob.
    bool accept_peer(protocol::ByteView blob);

    /// Master side: time to send the local blob again.
    bool local_due(std::uint32_t now_ms) const;
    void mark_local_sent(std::uint32_t now_ms);

    /// Slave side: called once per reply; true when this reply should carry
    /// the local blob. Half a cycle out of step with AddressBook's slot, so
    /// the two never want the same reply.
    bool take_reply_slot();

private:
    LayoutBlob local_{};
    LayoutBlob peer_{};
    bool ever_sent_ = false;
    std::uint32_t last_sent_ms_ = 0;
    std::uint32_t replies_ = kEndpointLayoutEvery / 2;
};

}  // namespace duo_input::link
```

- [ ] **Step 5: Реализация.** Создать `firmware/common/link/edge.cpp`:

```cpp
#include "link/edge.hpp"

#include <cstring>

namespace duo_input::link {
namespace {

void put_u16(std::uint8_t* out, std::uint16_t value) {
    out[0] = static_cast<std::uint8_t>(value);
    out[1] = static_cast<std::uint8_t>(value >> 8);
}

std::uint16_t take_u16(const std::uint8_t* in) {
    return static_cast<std::uint16_t>(in[0] | (in[1] << 8));
}

bool fits(protocol::MutableByteView output, std::size_t size) {
    return output.data != nullptr && output.size >= size;
}

bool exactly(protocol::ByteView payload, std::size_t size) {
    return payload.data != nullptr && payload.size == size;
}

bool valid_side(std::uint8_t side) {
    return side >= static_cast<std::uint8_t>(protocol::EdgeSide::LEFT) &&
           side <= static_cast<std::uint8_t>(protocol::EdgeSide::BOTTOM);
}

void write_contact(const EdgeContact& contact, std::uint8_t* out) {
    put_u16(out, contact.gen);
    out[2] = contact.active ? 1 : 0;
    out[3] = static_cast<std::uint8_t>(contact.side);
    out[4] = contact.neighbor;
    put_u16(out + 5, contact.t);
    put_u16(out + 7, contact.threshold);
}

bool read_contact(const std::uint8_t* in, EdgeContact& contact) {
    if (in[2] > 1) {
        return false;
    }
    const bool active = in[2] == 1;
    // An active contact is a push waiting to happen, so everything a push is
    // measured against has to be there. A threshold of zero would cross on a
    // pointer merely resting at the edge.
    if (active && (!valid_side(in[3]) || take_u16(in + 7) == 0)) {
        return false;
    }
    EdgeContact decoded;
    decoded.gen = take_u16(in);
    decoded.active = active;
    decoded.side = valid_side(in[3]) ? static_cast<protocol::EdgeSide>(in[3])
                                     : protocol::EdgeSide::RIGHT;
    decoded.neighbor = in[4];
    decoded.t = take_u16(in + 5);
    decoded.threshold = take_u16(in + 7);
    contact = decoded;
    return true;
}

void write_handoff(const Handoff& handoff, std::uint8_t* out) {
    put_u16(out, handoff.id);
    out[2] = static_cast<std::uint8_t>(handoff.side);
    put_u16(out + 3, handoff.t);
}

bool read_handoff(const std::uint8_t* in, Handoff& handoff) {
    if (!valid_side(in[2])) {
        return false;
    }
    handoff.id = take_u16(in);
    handoff.side = static_cast<protocol::EdgeSide>(in[2]);
    handoff.t = take_u16(in + 3);
    return true;
}

void write_route(const RouteState& state, std::uint8_t* out) {
    out[0] = static_cast<std::uint8_t>(state.mouse);
    out[1] = static_cast<std::uint8_t>(state.keyboard);
    out[2] = static_cast<std::uint8_t>(state.reason);
}

bool read_route(const std::uint8_t* in, RouteState& state) {
    const bool mouse_ok = in[0] == static_cast<std::uint8_t>(protocol::MouseRoute::PC1) ||
                          in[0] == static_cast<std::uint8_t>(protocol::MouseRoute::PC2);
    const bool keyboard_ok =
        in[1] >= static_cast<std::uint8_t>(protocol::KeyboardRoute::PC1) &&
        in[1] <= static_cast<std::uint8_t>(protocol::KeyboardRoute::BOTH);
    const bool reason_ok = in[2] <= static_cast<std::uint8_t>(protocol::RouteReason::PROFILE);
    if (!mouse_ok || !keyboard_ok || !reason_ok) {
        return false;
    }
    state.mouse = static_cast<protocol::MouseRoute>(in[0]);
    state.keyboard = static_cast<protocol::KeyboardRoute>(in[1]);
    state.reason = static_cast<protocol::RouteReason>(in[2]);
    return true;
}

}  // namespace

bool encode_edge_contact(const EdgeContact& contact, protocol::MutableByteView output,
                         std::size_t& written) {
    written = 0;
    if (!fits(output, kEdgeContactSize)) return false;
    write_contact(contact, output.data);
    written = kEdgeContactSize;
    return true;
}

bool decode_edge_contact(protocol::ByteView payload, EdgeContact& contact) {
    return exactly(payload, kEdgeContactSize) && read_contact(payload.data, contact);
}

bool encode_handoff(const Handoff& handoff, protocol::MutableByteView output,
                    std::size_t& written) {
    written = 0;
    if (!fits(output, kHandoffSize)) return false;
    write_handoff(handoff, output.data);
    written = kHandoffSize;
    return true;
}

bool decode_handoff(protocol::ByteView payload, Handoff& handoff) {
    Handoff decoded;
    if (!exactly(payload, kHandoffSize) || !read_handoff(payload.data, decoded)) return false;
    handoff = decoded;
    return true;
}

bool encode_route_state(const RouteState& state, protocol::MutableByteView output,
                        std::size_t& written) {
    written = 0;
    if (!fits(output, kRouteStateSize)) return false;
    write_route(state, output.data);
    written = kRouteStateSize;
    return true;
}

bool decode_route_state(protocol::ByteView payload, RouteState& state) {
    RouteState decoded;
    if (!exactly(payload, kRouteStateSize) || !read_route(payload.data, decoded)) return false;
    state = decoded;
    return true;
}

bool encode_link_edge_state(const LinkEdgeState& state, protocol::MutableByteView output,
                            std::size_t& written) {
    written = 0;
    if (!fits(output, kLinkEdgeStateSize)) return false;
    write_route(state.route, output.data);
    output.data[kRouteStateSize] = state.handoff_active ? 1 : 0;
    write_handoff(state.handoff, output.data + kRouteStateSize + 1);
    written = kLinkEdgeStateSize;
    return true;
}

bool decode_link_edge_state(protocol::ByteView payload, LinkEdgeState& state) {
    if (!exactly(payload, kLinkEdgeStateSize)) return false;
    LinkEdgeState decoded;
    if (!read_route(payload.data, decoded.route)) return false;
    const std::uint8_t active = payload.data[kRouteStateSize];
    if (active > 1) return false;
    decoded.handoff_active = active == 1;
    // Only a pending handoff is read: an idle one carries whatever the last
    // one left behind, including a side of zero at boot.
    if (decoded.handoff_active &&
        !read_handoff(payload.data + kRouteStateSize + 1, decoded.handoff)) {
        return false;
    }
    state = decoded;
    return true;
}

bool encode_endpoint_edge_state(const EndpointEdgeState& state,
                                protocol::MutableByteView output, std::size_t& written) {
    written = 0;
    if (!fits(output, kEndpointEdgeStateSize)) return false;
    write_contact(state.contact, output.data);
    put_u16(output.data + kEdgeContactSize, state.last_ready_id);
    output.data[kEdgeContactSize + 2] = state.flags;
    written = kEndpointEdgeStateSize;
    return true;
}

bool decode_endpoint_edge_state(protocol::ByteView payload, EndpointEdgeState& state) {
    if (!exactly(payload, kEndpointEdgeStateSize)) return false;
    EndpointEdgeState decoded;
    if (!read_contact(payload.data, decoded.contact)) return false;
    decoded.last_ready_id = take_u16(payload.data + kEdgeContactSize);
    decoded.flags = payload.data[kEdgeContactSize + 2];
    state = decoded;
    return true;
}

bool decode_handoff_ready(protocol::ByteView payload, std::uint16_t& id) {
    if (!exactly(payload, kHandoffReadySize)) return false;
    id = take_u16(payload.data);
    return true;
}

protocol::EdgeSide opposite(protocol::EdgeSide side) {
    switch (side) {
        case protocol::EdgeSide::LEFT:
            return protocol::EdgeSide::RIGHT;
        case protocol::EdgeSide::RIGHT:
            return protocol::EdgeSide::LEFT;
        case protocol::EdgeSide::TOP:
            return protocol::EdgeSide::BOTTOM;
        case protocol::EdgeSide::BOTTOM:
            return protocol::EdgeSide::TOP;
    }
    return protocol::EdgeSide::LEFT;
}

// ------------------------------------------------------------ layout book

namespace {

bool copy_blob(protocol::ByteView blob, LayoutBlob& into) {
    if (blob.data == nullptr || blob.size > kLayoutBlobMaxSize) return false;
    into.size = static_cast<std::uint8_t>(blob.size);
    std::memcpy(into.bytes, blob.data, blob.size);
    return true;
}

}  // namespace

bool LayoutBook::set_local(protocol::ByteView blob) {
    if (blob.size == 0) return false;
    return copy_blob(blob, local_);
}

bool LayoutBook::accept_peer(protocol::ByteView blob) { return copy_blob(blob, peer_); }

bool LayoutBook::local_due(std::uint32_t now_ms) const {
    return has_local() && (!ever_sent_ || now_ms - last_sent_ms_ >= kLayoutIntervalMs);
}

void LayoutBook::mark_local_sent(std::uint32_t now_ms) {
    ever_sent_ = true;
    last_sent_ms_ = now_ms;
}

bool LayoutBook::take_reply_slot() {
    if (!has_local()) return false;
    if (++replies_ < kEndpointLayoutEvery) return false;
    replies_ = 0;
    return true;
}

}  // namespace duo_input::link
```

  Замечание: `take_reply_slot` стартует с `replies_ = 32`, поэтому первый `true` приходит на 32-м вызове. Это и проверяет тест `a_layout_reply_slot_comes_half_a_cycle_out_of_step`.

- [ ] **Step 6: Подключить к `duo_common`.** В `firmware/common/CMakeLists.txt` добавить `link/edge.cpp` после `link/host_addresses.cpp`.

- [ ] **Step 7: Прогнать.**
  Run: `cmake --build --preset native --clean-first` и `ctest --preset native -R edge_messages --output-on-failure`
  Expected: PASS.

- [ ] **Step 8: Commit.**

```bash
git add firmware/common/link/edge.hpp firmware/common/link/edge.cpp firmware/common/CMakeLists.txt tests/firmware_native/test_edge_messages.cpp tests/firmware_native/CMakeLists.txt
git commit -m "feat(firmware): screen-edge message codecs and layout book"
```

---

### Task 3: Python-кодек сообщений края

**Files:**
- Create: `configurator/src/duo_input/device/edge_messages.py`
- Create: `configurator/tests/device/test_edge_messages.py`

**Interfaces:**
- Consumes: `EdgeSide`, `RouteReason`, `MouseRoute`, `KeyboardRoute` из `duo_input.generated.protocol`; `PayloadError` из `duo_input.device.transactions`.
- Produces (`duo_input.device.edge_messages`):
  - `@dataclass(frozen=True) EdgeContact(gen: int, active: bool, side: int, neighbor: int, t: int, threshold: int)`;
  - `Handoff(id: int, side: int, t: int)`;
  - `RouteState(mouse: int, keyboard: int, reason: int)`;
  - `EdgeStats(crossings, handoffs_ready, handoff_timeouts, handoffs_skipped, refused_button, refused_target_dead, refused_cooldown, contacts_inactive, contact_queue_drops, total_ready_ms, max_ready_ms)`;
  - `encode_edge_contact(EdgeContact) -> bytes`, `decode_edge_contact(bytes) -> EdgeContact`;
  - `encode_handoff`, `decode_handoff`;
  - `encode_route_state`, `decode_route_state`;
  - `encode_handoff_ready(int) -> bytes`, `decode_handoff_ready(bytes) -> int`;
  - `decode_edge_stats(bytes) -> EdgeStats` — разбирает ответ **после** байта ошибки;
  - `LAYOUT_BLOB_MAX_SIZE = 51`, `T_MAX = 0xFFFF`.

- [ ] **Step 1: Написать падающие тесты.** `configurator/tests/device/test_edge_messages.py` (байтовые строки совпадают с `test_edge_messages.cpp`):

```python
import pytest

from duo_input.device.edge_messages import (
    LAYOUT_BLOB_MAX_SIZE,
    EdgeContact,
    Handoff,
    RouteState,
    decode_edge_contact,
    decode_edge_stats,
    decode_handoff,
    decode_handoff_ready,
    decode_route_state,
    encode_edge_contact,
    encode_handoff,
    encode_handoff_ready,
    encode_route_state,
)
from duo_input.device.transactions import PayloadError
from duo_input.generated.protocol import EdgeSide, KeyboardRoute, MouseRoute, RouteReason


def test_a_contact_matches_the_firmware_bytes():
    contact = EdgeContact(gen=0x0102, active=True, side=EdgeSide.RIGHT, neighbor=0, t=0x8000, threshold=120)
    wire = encode_edge_contact(contact)
    assert wire == bytes([0x02, 0x01, 1, 2, 0, 0x00, 0x80, 120, 0])
    assert decode_edge_contact(wire) == contact


def test_an_inactive_contact_may_name_no_side():
    contact = decode_edge_contact(bytes([5, 0, 0, 0, 0, 0, 0, 0, 0]))
    assert contact.active is False
    assert contact.gen == 5


@pytest.mark.parametrize(
    "wire",
    [
        bytes([5, 0, 1, 0, 0, 0, 0, 120, 0]),  # active, no side
        bytes([5, 0, 1, 5, 0, 0, 0, 120, 0]),  # side past BOTTOM
        bytes([5, 0, 1, 2, 0, 0, 0, 0, 0]),  # no threshold
        bytes([5, 0, 2, 2, 0, 0, 0, 120, 0]),  # active byte 2
        bytes([9, 0, 1, 1, 0, 0, 0, 1]),  # eight bytes
    ],
)
def test_contacts_the_firmware_refuses_are_refused(wire):
    with pytest.raises(PayloadError):
        decode_edge_contact(wire)


def test_encoding_refuses_an_active_contact_the_firmware_would_drop():
    with pytest.raises(PayloadError):
        encode_edge_contact(EdgeContact(gen=1, active=True, side=EdgeSide.RIGHT, neighbor=0, t=0, threshold=0))


def test_a_handoff_matches_the_firmware_bytes():
    wire = bytes([0x04, 0x03, 1, 0x00, 0x40])
    handoff = decode_handoff(wire)
    assert handoff == Handoff(id=0x0304, side=EdgeSide.LEFT, t=0x4000)
    assert encode_handoff(handoff) == wire
    with pytest.raises(PayloadError):
        decode_handoff(bytes([0x04, 0x03, 0, 0x00, 0x40]))


def test_a_route_state_matches_the_firmware_bytes():
    state = RouteState(mouse=MouseRoute.PC2, keyboard=KeyboardRoute.BOTH, reason=RouteReason.EDGE)
    assert encode_route_state(state) == bytes([2, 3, 2])
    assert decode_route_state(bytes([2, 3, 2])) == state
    for wire in (bytes([3, 1, 0]), bytes([1, 4, 0]), bytes([1, 1, 9])):
        with pytest.raises(PayloadError):
            decode_route_state(wire)


def test_handoff_ready_is_two_bytes():
    assert encode_handoff_ready(0x1234) == bytes([0x34, 0x12])
    assert decode_handoff_ready(bytes([0x34, 0x12])) == 0x1234
    with pytest.raises(PayloadError):
        decode_handoff_ready(bytes([1, 0, 0]))


def test_edge_stats_are_ten_counters_and_a_maximum():
    payload = b"".join(n.to_bytes(4, "little") for n in range(1, 11)) + (77).to_bytes(2, "little")
    stats = decode_edge_stats(payload)
    assert stats.crossings == 1
    assert stats.handoffs_ready == 2
    assert stats.handoff_timeouts == 3
    assert stats.handoffs_skipped == 4
    assert stats.refused_button == 5
    assert stats.refused_target_dead == 6
    assert stats.refused_cooldown == 7
    assert stats.contacts_inactive == 8
    assert stats.contact_queue_drops == 9
    assert stats.total_ready_ms == 10
    assert stats.max_ready_ms == 77
    with pytest.raises(PayloadError):
        decode_edge_stats(payload[:-1])


def test_the_layout_blob_limit_matches_the_firmware():
    assert LAYOUT_BLOB_MAX_SIZE == 51
```

- [ ] **Step 2: Убедиться, что падает.**
  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/device/test_edge_messages.py -q`
  Expected: FAIL, `ModuleNotFoundError: duo_input.device.edge_messages`.

- [ ] **Step 3: Реализация.** `configurator/src/duo_input/device/edge_messages.py`:

```python
"""The wire shape of the screen-extension messages.

firmware/common/link/edge.cpp is the other half, and the two refuse exactly
the same payloads: tests/firmware_native/test_edge_messages.cpp and
configurator/tests/device/test_edge_messages.py check the same byte strings.
All multibyte fields are little-endian.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from duo_input.generated.protocol import EdgeSide, KeyboardRoute, MouseRoute, RouteReason

from .transactions import PayloadError

LAYOUT_BLOB_MAX_SIZE = 51
T_MAX = 0xFFFF

_CONTACT = struct.Struct("<HBBBHH")
_HANDOFF = struct.Struct("<HBH")
_ROUTE = struct.Struct("<BBB")
_READY = struct.Struct("<H")
_STATS = struct.Struct("<10IH")

_SIDES = {int(side) for side in EdgeSide}


@dataclass(frozen=True)
class EdgeContact:
    gen: int
    active: bool
    side: int
    neighbor: int
    t: int
    threshold: int


@dataclass(frozen=True)
class Handoff:
    id: int
    side: int
    t: int


@dataclass(frozen=True)
class RouteState:
    mouse: int
    keyboard: int
    reason: int


@dataclass(frozen=True)
class EdgeStats:
    crossings: int
    handoffs_ready: int
    handoff_timeouts: int
    handoffs_skipped: int
    refused_button: int
    refused_target_dead: int
    refused_cooldown: int
    contacts_inactive: int
    contact_queue_drops: int
    total_ready_ms: int
    max_ready_ms: int


def _check_contact(active: bool, side: int, threshold: int) -> None:
    if active and (side not in _SIDES or threshold == 0):
        raise PayloadError("an active contact needs a side and a threshold")


def encode_edge_contact(contact: EdgeContact) -> bytes:
    _check_contact(contact.active, int(contact.side), contact.threshold)
    return _CONTACT.pack(
        contact.gen, 1 if contact.active else 0, int(contact.side), contact.neighbor, contact.t, contact.threshold
    )


def decode_edge_contact(payload: bytes) -> EdgeContact:
    if len(payload) != _CONTACT.size:
        raise PayloadError(f"edge contact is {len(payload)} bytes")
    gen, active, side, neighbor, t, threshold = _CONTACT.unpack(payload)
    if active > 1:
        raise PayloadError(f"edge contact active byte is {active}")
    _check_contact(active == 1, side, threshold)
    return EdgeContact(gen, active == 1, side, neighbor, t, threshold)


def encode_handoff(handoff: Handoff) -> bytes:
    if int(handoff.side) not in _SIDES:
        raise PayloadError("a handoff needs a side")
    return _HANDOFF.pack(handoff.id, int(handoff.side), handoff.t)


def decode_handoff(payload: bytes) -> Handoff:
    if len(payload) != _HANDOFF.size:
        raise PayloadError(f"handoff is {len(payload)} bytes")
    identifier, side, t = _HANDOFF.unpack(payload)
    if side not in _SIDES:
        raise PayloadError(f"handoff side is {side}")
    return Handoff(identifier, side, t)


def encode_route_state(state: RouteState) -> bytes:
    return _ROUTE.pack(int(state.mouse), int(state.keyboard), int(state.reason))


def decode_route_state(payload: bytes) -> RouteState:
    if len(payload) != _ROUTE.size:
        raise PayloadError(f"route state is {len(payload)} bytes")
    mouse, keyboard, reason = _ROUTE.unpack(payload)
    if mouse not in (MouseRoute.PC1, MouseRoute.PC2):
        raise PayloadError(f"mouse route is {mouse}")
    if keyboard not in {int(route) for route in KeyboardRoute}:
        raise PayloadError(f"keyboard route is {keyboard}")
    if reason not in {int(item) for item in RouteReason}:
        raise PayloadError(f"route reason is {reason}")
    return RouteState(mouse, keyboard, reason)


def encode_handoff_ready(identifier: int) -> bytes:
    return _READY.pack(identifier)


def decode_handoff_ready(payload: bytes) -> int:
    if len(payload) != _READY.size:
        raise PayloadError(f"handoff ready is {len(payload)} bytes")
    return _READY.unpack(payload)[0]


def decode_edge_stats(payload: bytes) -> EdgeStats:
    """The GET_EDGE_STATS reply after its error byte."""
    if len(payload) != _STATS.size:
        raise PayloadError(f"edge stats are {len(payload)} bytes")
    return EdgeStats(*_STATS.unpack(payload))


__all__ = [
    "LAYOUT_BLOB_MAX_SIZE",
    "T_MAX",
    "EdgeContact",
    "EdgeStats",
    "Handoff",
    "RouteState",
    "decode_edge_contact",
    "decode_edge_stats",
    "decode_handoff",
    "decode_handoff_ready",
    "decode_route_state",
    "encode_edge_contact",
    "encode_handoff",
    "encode_handoff_ready",
    "encode_route_state",
]
```

- [ ] **Step 4: Прогнать.**
  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/device/test_edge_messages.py -q; echo "exit=$?"`
  Expected: `exit=0`.

- [ ] **Step 5: Commit.**

```bash
git add configurator/src/duo_input/device/edge_messages.py configurator/tests/device/test_edge_messages.py
git commit -m "feat(configurator): screen-edge message codec"
```

---

### Task 4: `EdgeArbiter` — давление и условия перехода

**Files:**
- Create: `firmware/u1_main/mapping/edge_arbiter.hpp`, `firmware/u1_main/mapping/edge_arbiter.cpp`
- Create: `tests/firmware_native/test_edge_arbiter.cpp`
- Modify: `tests/firmware_native/CMakeLists.txt`

**Interfaces:**
- Consumes: `link::EdgeContact`, `link::opposite`, константы `link::kEdgeContactTtlMs`, `kEdgeCooldownMs`, `kEdgePressureGapMs` (Task 2); `hid::Target`, `hid::kTargetCount`.
- Produces (namespace `duo_input::u1::mapping`):
  - `struct EdgeDecision { bool cross; hid::Target to; protocol::EdgeSide entry_side; std::uint16_t t; }`;
  - `struct EdgeCounters { std::uint32_t crossings, refused_button, refused_target_dead, refused_cooldown, contacts_inactive; }`;
  - `class EdgeArbiter` с методами `set_contact(hid::Target source, const link::EdgeContact&, std::uint32_t received_ms, hid::Target mouse_on)`, `set_target_alive(hid::Target, bool)`, `route_changed(std::uint32_t now_ms)`, `EdgeDecision on_motion(hid::Target mouse_on, std::int32_t dx, std::int32_t dy, bool button_held, std::uint32_t now_ms)`, `const EdgeCounters& counters() const`.

- [ ] **Step 1: Написать падающие тесты.** `tests/firmware_native/test_edge_arbiter.cpp`:

```cpp
// Whether the operator is pushing the pointer through the edge of a screen.
//
// Each test is one row of the design's seven conditions, or one property of
// the push itself. The push is counted in raw mouse counts, one report a
// millisecond, the way Core 1 feeds it.

#include "link/edge.hpp"
#include "mapping/edge_arbiter.hpp"
#include "test_support.hpp"

using duo_input::hid::Target;
using duo_input::link::EdgeContact;
using duo_input::protocol::EdgeSide;
using duo_input::u1::mapping::EdgeArbiter;
using duo_input::u1::mapping::EdgeDecision;

namespace {

EdgeContact touching(EdgeSide side, std::uint16_t gen = 1, std::uint16_t threshold = 100,
                     std::uint16_t t = 0x8000) {
    EdgeContact contact;
    contact.gen = gen;
    contact.active = true;
    contact.side = side;
    contact.t = t;
    contact.threshold = threshold;
    return contact;
}

/// Both computers able to take the pointer, PC1's pointer against its right
/// edge since ``now``.
EdgeArbiter pressed_right(std::uint32_t now) {
    EdgeArbiter arbiter;
    arbiter.set_target_alive(Target::Pc1, true);
    arbiter.set_target_alive(Target::Pc2, true);
    arbiter.set_contact(Target::Pc1, touching(EdgeSide::RIGHT), now, Target::Pc1);
    return arbiter;
}

/// ``reports`` reports of (dx, dy), one a millisecond from ``now``. Returns
/// the first crossing, or no crossing.
EdgeDecision push(EdgeArbiter& arbiter, int reports, int dx, int dy, std::uint32_t& now,
                  Target on = Target::Pc1, bool button = false) {
    for (int i = 0; i < reports; ++i) {
        const EdgeDecision decision = arbiter.on_motion(on, dx, dy, button, now);
        ++now;
        if (decision.cross) {
            return decision;
        }
    }
    return EdgeDecision{};
}

}  // namespace

TEST_CASE(a_push_that_reaches_the_threshold_crosses_to_the_other_computer) {
    std::uint32_t now = 1000;
    EdgeArbiter arbiter = pressed_right(now);
    // Nine reports of ten is ninety: short of a hundred.
    CHECK_FALSE(push(arbiter, 9, 10, 0, now).cross);
    const EdgeDecision decision = push(arbiter, 1, 10, 0, now);
    CHECK(decision.cross);
    CHECK(decision.to == Target::Pc2);
    CHECK(decision.entry_side == EdgeSide::LEFT);
    CHECK_EQ(decision.t, 0x8000u);
    CHECK_EQ(arbiter.counters().crossings, 1u);
}

TEST_CASE(moving_inward_starts_the_push_over) {
    std::uint32_t now = 1000;
    EdgeArbiter arbiter = pressed_right(now);
    CHECK_FALSE(push(arbiter, 6, 10, 0, now).cross);
    CHECK_FALSE(push(arbiter, 1, -1, 0, now).cross);
    // Sixty more would have made a hundred and twenty without the reset.
    CHECK_FALSE(push(arbiter, 6, 10, 0, now).cross);
    CHECK(push(arbiter, 4, 10, 0, now).cross);
}

TEST_CASE(a_pause_longer_than_the_gap_starts_the_push_over) {
    std::uint32_t now = 1000;
    EdgeArbiter arbiter = pressed_right(now);
    CHECK_FALSE(push(arbiter, 6, 10, 0, now).cross);
    now += duo_input::link::kEdgePressureGapMs + 1;
    // A refresh keeps the contact itself alive across the pause.
    arbiter.set_contact(Target::Pc1, touching(EdgeSide::RIGHT), now, Target::Pc1);
    CHECK_FALSE(push(arbiter, 6, 10, 0, now).cross);
}

TEST_CASE(movement_along_the_edge_does_not_start_it_over) {
    std::uint32_t now = 1000;
    EdgeArbiter arbiter = pressed_right(now);
    CHECK_FALSE(push(arbiter, 6, 10, 0, now).cross);
    CHECK_FALSE(push(arbiter, 3, 0, 15, now).cross);
    CHECK(push(arbiter, 4, 10, 5, now).cross);
}

TEST_CASE(no_contact_no_crossing) {
    std::uint32_t now = 1000;
    EdgeArbiter arbiter;
    arbiter.set_target_alive(Target::Pc2, true);
    CHECK_FALSE(push(arbiter, 50, 10, 0, now).cross);
}

TEST_CASE(an_inactive_contact_does_not_cross) {
    std::uint32_t now = 1000;
    EdgeArbiter arbiter = pressed_right(now);
    EdgeContact released = touching(EdgeSide::RIGHT);
    released.active = false;
    arbiter.set_contact(Target::Pc1, released, now, Target::Pc1);
    CHECK_FALSE(push(arbiter, 50, 10, 0, now).cross);
}

TEST_CASE(a_contact_from_the_computer_the_mouse_is_not_on_does_not_cross) {
    std::uint32_t now = 1000;
    EdgeArbiter arbiter;
    arbiter.set_target_alive(Target::Pc1, true);
    arbiter.set_target_alive(Target::Pc2, true);
    // PC2's pointer was left at its left edge; the mouse is on PC1.
    arbiter.set_contact(Target::Pc2, touching(EdgeSide::LEFT), now, Target::Pc1);
    CHECK_EQ(arbiter.counters().contacts_inactive, 1u);
    CHECK_FALSE(push(arbiter, 50, -10, 0, now).cross);
    // A refresh of the same episode is not counted again.
    arbiter.set_contact(Target::Pc2, touching(EdgeSide::LEFT), now, Target::Pc1);
    CHECK_EQ(arbiter.counters().contacts_inactive, 1u);
}

TEST_CASE(a_contact_nobody_refreshed_does_not_cross) {
    std::uint32_t now = 1000;
    EdgeArbiter arbiter = pressed_right(now);
    now += duo_input::link::kEdgeContactTtlMs + 1;
    CHECK_FALSE(push(arbiter, 50, 10, 0, now).cross);
}

TEST_CASE(a_contact_spent_by_a_route_change_does_not_cross_again) {
    std::uint32_t now = 1000;
    EdgeArbiter arbiter = pressed_right(now);
    arbiter.route_changed(now);
    now += duo_input::link::kEdgeCooldownMs + 1;
    // Refreshed, but the same episode: the pointer never left the edge.
    arbiter.set_contact(Target::Pc1, touching(EdgeSide::RIGHT, 1), now, Target::Pc1);
    CHECK_FALSE(push(arbiter, 50, 10, 0, now).cross);
    // It left and came back.
    arbiter.set_contact(Target::Pc1, touching(EdgeSide::RIGHT, 2), now, Target::Pc1);
    CHECK(push(arbiter, 10, 10, 0, now).cross);
}

TEST_CASE(a_held_button_refuses_and_is_counted) {
    std::uint32_t now = 1000;
    EdgeArbiter arbiter = pressed_right(now);
    CHECK_FALSE(push(arbiter, 10, 10, 0, now, Target::Pc1, true).cross);
    CHECK_EQ(arbiter.counters().refused_button, 1u);
    CHECK_EQ(arbiter.counters().crossings, 0u);
}

TEST_CASE(a_computer_that_cannot_take_the_pointer_refuses_and_is_counted) {
    std::uint32_t now = 1000;
    EdgeArbiter arbiter = pressed_right(now);
    arbiter.set_target_alive(Target::Pc2, false);
    CHECK_FALSE(push(arbiter, 10, 10, 0, now).cross);
    CHECK_EQ(arbiter.counters().refused_target_dead, 1u);
}

TEST_CASE(the_cooldown_after_any_route_change_refuses_and_is_counted) {
    std::uint32_t now = 1000;
    EdgeArbiter arbiter = pressed_right(now);
    arbiter.route_changed(now);
    // A fresh episode straight away: only the cooldown stands in the way.
    arbiter.set_contact(Target::Pc1, touching(EdgeSide::RIGHT, 2), now, Target::Pc1);
    CHECK_FALSE(push(arbiter, 10, 10, 0, now).cross);
    CHECK_EQ(arbiter.counters().refused_cooldown, 1u);
    now = 1000 + duo_input::link::kEdgeCooldownMs;
    arbiter.set_contact(Target::Pc1, touching(EdgeSide::RIGHT, 2), now, Target::Pc1);
    CHECK(push(arbiter, 10, 10, 0, now).cross);
}

TEST_CASE(every_side_is_pushed_outward_and_entered_opposite) {
    struct Row {
        EdgeSide side;
        int dx;
        int dy;
        EdgeSide entry;
    };
    const Row rows[] = {
        {EdgeSide::LEFT, -10, 0, EdgeSide::RIGHT},
        {EdgeSide::RIGHT, 10, 0, EdgeSide::LEFT},
        {EdgeSide::TOP, 0, -10, EdgeSide::BOTTOM},
        {EdgeSide::BOTTOM, 0, 10, EdgeSide::TOP},
    };
    for (const Row& row : rows) {
        std::uint32_t now = 1000;
        EdgeArbiter arbiter;
        arbiter.set_target_alive(Target::Pc2, true);
        arbiter.set_contact(Target::Pc1, touching(row.side), now, Target::Pc1);
        // The wrong way first: away from the edge never crosses.
        CHECK_FALSE(push(arbiter, 20, -row.dx, -row.dy, now).cross);
        const EdgeDecision decision = push(arbiter, 10, row.dx, row.dy, now);
        CHECK(decision.cross);
        CHECK(decision.entry_side == row.entry);
    }
}

TEST_CASE(a_threshold_of_one_crosses_on_the_first_outward_count) {
    std::uint32_t now = 1000;
    EdgeArbiter arbiter;
    arbiter.set_target_alive(Target::Pc2, true);
    arbiter.set_contact(Target::Pc1, touching(EdgeSide::RIGHT, 1, 1), now, Target::Pc1);
    CHECK(push(arbiter, 1, 1, 0, now).cross);
}

TEST_CASE(a_crossing_from_pc2_goes_to_pc1) {
    std::uint32_t now = 1000;
    EdgeArbiter arbiter;
    arbiter.set_target_alive(Target::Pc1, true);
    arbiter.set_contact(Target::Pc2, touching(EdgeSide::LEFT, 1, 50), now, Target::Pc2);
    const EdgeDecision decision = push(arbiter, 5, -10, 0, now, Target::Pc2);
    CHECK(decision.cross);
    CHECK(decision.to == Target::Pc1);
    CHECK(decision.entry_side == EdgeSide::RIGHT);
}
```

- [ ] **Step 2: Зарегистрировать.** В `tests/firmware_native/CMakeLists.txt` после `duo_edge_messages_test`:

```cmake
add_executable(duo_edge_arbiter_test
    test_main.cpp
    test_edge_arbiter.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u1_main/mapping/edge_arbiter.cpp
)

target_link_libraries(duo_edge_arbiter_test PRIVATE duo_common)
target_include_directories(duo_edge_arbiter_test PRIVATE
    ${CMAKE_SOURCE_DIR}/firmware/u1_main
)

if(MSVC)
    target_compile_options(duo_edge_arbiter_test PRIVATE /EHsc)
endif()

add_test(NAME edge_arbiter COMMAND duo_edge_arbiter_test)
```

  И в `add_library(duo_mapping_sources STATIC …)` добавить строку `${CMAKE_SOURCE_DIR}/firmware/u1_main/mapping/edge_arbiter.cpp`. Это нужно всем целям, которые компилируют `core1_runtime.cpp` (Task 6).

- [ ] **Step 3: Убедиться, что не собирается.**
  Run: `cmake --build --preset native --clean-first`
  Expected: FAIL, нет `mapping/edge_arbiter.hpp`.

- [ ] **Step 4: Заголовок.** `firmware/u1_main/mapping/edge_arbiter.hpp`:

```cpp
#pragma once

// Whether the operator is pushing the pointer through the edge of a screen.
//
// The computer knows where its pointer is and which edge leads to the other
// computer; it says so in a contact. This board knows what the mouse is
// actually doing, before any acceleration the computer applies - and a pointer
// held against the edge stops moving on screen while the hand keeps going.
// That continued push, counted in raw mouse counts, is the whole signal: a
// pointer resting at an edge pushes nothing, and one thrown at a button near
// the edge stops pushing the moment it gets there.
//
// Pure: no clock of its own, no hardware, no other core. Core1Runtime feeds it
// and acts on what it says.

#include <cstdint>

#include "hid/types.hpp"
#include "link/edge.hpp"

namespace duo_input::u1::mapping {

struct EdgeDecision {
    bool cross = false;
    /// Where the mouse goes.
    hid::Target to = hid::Target::Pc1;
    /// Which edge of that computer the pointer comes in by.
    protocol::EdgeSide entry_side = protocol::EdgeSide::LEFT;
    /// Where along it.
    std::uint16_t t = 0;
};

/// What the edge has done, for GET_EDGE_STATS. A refusal is counted only for a
/// push that reached its threshold: a push that never got there is somebody
/// working near the edge, not a crossing that was refused.
struct EdgeCounters {
    std::uint32_t crossings = 0;
    std::uint32_t refused_button = 0;
    std::uint32_t refused_target_dead = 0;
    std::uint32_t refused_cooldown = 0;
    /// New contacts from a computer the mouse was not on.
    std::uint32_t contacts_inactive = 0;
};

class EdgeArbiter {
public:
    /// A contact from ``source``'s computer, as its board heard it at
    /// ``received_ms``. ``mouse_on`` is only for the count.
    void set_contact(hid::Target source, const link::EdgeContact& contact,
                     std::uint32_t received_ms, hid::Target mouse_on);

    /// Whether ``target`` could take the pointer now.
    void set_target_alive(hid::Target target, bool alive);

    /// A route moved, for any reason - a crossing, a hotkey, a macro, a
    /// profile. Starts the cooldown and spends every contact standing now.
    void route_changed(std::uint32_t now_ms);

    /// One mouse report on its way to ``mouse_on``.
    EdgeDecision on_motion(hid::Target mouse_on, std::int32_t dx, std::int32_t dy,
                           bool button_held, std::uint32_t now_ms);

    const EdgeCounters& counters() const { return counters_; }

private:
    struct Slot {
        link::EdgeContact contact{};
        std::uint32_t received_ms = 0;
        bool present = false;
        /// The generation this computer's contact had when a route last moved.
        /// That episode is over, however long the pointer stays where it is.
        std::uint16_t spent_gen = 0;
        bool has_spent = false;
    };

    void forget_push() {
        pressure_ = 0;
        pressing_ = false;
    }

    Slot slots_[hid::kTargetCount];
    bool alive_[hid::kTargetCount] = {};

    std::uint32_t pressure_ = 0;
    bool pressing_ = false;
    std::uint16_t pressing_gen_ = 0;
    hid::Target pressing_source_ = hid::Target::Pc1;
    std::uint32_t last_outward_ms_ = 0;

    bool cooling_ = false;
    std::uint32_t cooldown_until_ms_ = 0;

    EdgeCounters counters_{};
};

}  // namespace duo_input::u1::mapping
```

- [ ] **Step 5: Реализация.** `firmware/u1_main/mapping/edge_arbiter.cpp`:

```cpp
#include "mapping/edge_arbiter.hpp"

namespace duo_input::u1::mapping {
namespace {

std::size_t index_of(hid::Target target) { return static_cast<std::size_t>(target); }

hid::Target other(hid::Target target) {
    return target == hid::Target::Pc1 ? hid::Target::Pc2 : hid::Target::Pc1;
}

/// How far this report moved the hand out through ``side``. Negative is
/// inward.
std::int32_t outward(protocol::EdgeSide side, std::int32_t dx, std::int32_t dy) {
    switch (side) {
        case protocol::EdgeSide::LEFT:
            return -dx;
        case protocol::EdgeSide::RIGHT:
            return dx;
        case protocol::EdgeSide::TOP:
            return -dy;
        case protocol::EdgeSide::BOTTOM:
            return dy;
    }
    return 0;
}

/// Has more than ``limit`` passed since ``since``? Subtraction, because the
/// millisecond counter wraps.
bool older_than(std::uint32_t now_ms, std::uint32_t since_ms, std::uint32_t limit_ms) {
    return static_cast<std::int32_t>(now_ms - since_ms) > static_cast<std::int32_t>(limit_ms);
}

}  // namespace

void EdgeArbiter::set_contact(hid::Target source, const link::EdgeContact& contact,
                              std::uint32_t received_ms, hid::Target mouse_on) {
    Slot& slot = slots_[index_of(source)];
    const bool new_episode = contact.active && (!slot.present || !slot.contact.active ||
                                                slot.contact.gen != contact.gen);
    if (new_episode && source != mouse_on) {
        ++counters_.contacts_inactive;
    }
    slot.contact = contact;
    slot.received_ms = received_ms;
    slot.present = true;
}

void EdgeArbiter::set_target_alive(hid::Target target, bool alive) {
    alive_[index_of(target)] = alive;
}

void EdgeArbiter::route_changed(std::uint32_t now_ms) {
    cooling_ = true;
    cooldown_until_ms_ = now_ms + link::kEdgeCooldownMs;
    for (Slot& slot : slots_) {
        if (slot.present) {
            slot.spent_gen = slot.contact.gen;
            slot.has_spent = true;
        }
    }
    forget_push();
}

EdgeDecision EdgeArbiter::on_motion(hid::Target mouse_on, std::int32_t dx, std::int32_t dy,
                                    bool button_held, std::uint32_t now_ms) {
    const Slot& slot = slots_[index_of(mouse_on)];

    // A contact, from the computer the mouse is on, refreshed recently, and
    // not one a route change already spent. Without all four there is no edge
    // to push against.
    if (!slot.present || !slot.contact.active ||
        older_than(now_ms, slot.received_ms, link::kEdgeContactTtlMs) ||
        (slot.has_spent && slot.contact.gen == slot.spent_gen)) {
        forget_push();
        return EdgeDecision{};
    }

    if (!pressing_ || pressing_gen_ != slot.contact.gen || pressing_source_ != mouse_on) {
        pressure_ = 0;
        pressing_ = true;
        pressing_gen_ = slot.contact.gen;
        pressing_source_ = mouse_on;
        last_outward_ms_ = now_ms;
    }

    const std::int32_t push = outward(slot.contact.side, dx, dy);
    if (push < 0) {
        // Back from the edge: whatever the hand meant, it was not to go through.
        pressure_ = 0;
        return EdgeDecision{};
    }
    if (older_than(now_ms, last_outward_ms_, link::kEdgePressureGapMs)) {
        pressure_ = 0;
    }
    if (push == 0) {
        return EdgeDecision{};
    }
    pressure_ += static_cast<std::uint32_t>(push);
    last_outward_ms_ = now_ms;
    if (pressure_ < slot.contact.threshold) {
        return EdgeDecision{};
    }

    // The push is through. What follows decides whether it may be, and a
    // refused push starts over rather than crossing the instant the refusal
    // lifts - a button let go mid-drag must not throw the pointer across.
    pressure_ = 0;
    const hid::Target to = other(mouse_on);
    if (button_held) {
        ++counters_.refused_button;
        return EdgeDecision{};
    }
    if (!alive_[index_of(to)]) {
        ++counters_.refused_target_dead;
        return EdgeDecision{};
    }
    if (cooling_ && static_cast<std::int32_t>(now_ms - cooldown_until_ms_) < 0) {
        ++counters_.refused_cooldown;
        return EdgeDecision{};
    }

    ++counters_.crossings;
    EdgeDecision decision;
    decision.cross = true;
    decision.to = to;
    decision.entry_side = link::opposite(slot.contact.side);
    decision.t = slot.contact.t;
    return decision;
}

}  // namespace duo_input::u1::mapping
```

- [ ] **Step 6: Прогнать.**
  Run: `cmake --build --preset native --clean-first` и `ctest --preset native -R edge_arbiter --output-on-failure`
  Expected: PASS.

- [ ] **Step 7: Commit.**

```bash
git add firmware/u1_main/mapping/edge_arbiter.hpp firmware/u1_main/mapping/edge_arbiter.cpp tests/firmware_native/test_edge_arbiter.cpp tests/firmware_native/CMakeLists.txt
git commit -m "feat(u1): edge arbiter - outward pressure and the crossing conditions"
```

---

### Task 5: Ворота передачи — `RouteMoved`, `HandoffGate`, `OutputRuntime`

**Files:**
- Modify: `firmware/common/runtime/output_command.hpp`
- Create: `firmware/common/link/handoff_gate.hpp`, `firmware/common/link/handoff_gate.cpp`; добавить `link/handoff_gate.cpp` в `firmware/common/CMakeLists.txt`
- Modify: `firmware/u1_main/output_runtime.hpp`, `firmware/u1_main/output_runtime.cpp`
- Create: `tests/firmware_native/test_handoff_gate.cpp`; Modify: `tests/firmware_native/test_output_runtime.cpp`, `tests/firmware_native/CMakeLists.txt`

**Interfaces:**
- Consumes: `link::kHandoffTimeoutMs`, `link::kHandoffMissesBeforeSkip`, `link::LinkEdgeState`, `link::RouteState`, `link::Handoff` (Task 2).
- Produces:
  - `runtime::CommandKind::RouteMoved` и новые поля `OutputCommand`: `std::uint16_t handoff_id`, `std::uint16_t position`, `std::uint8_t side`, `std::uint8_t reason`. Для `RouteMoved` поле `route` — куда теперь идёт мышь (`Pc1`/`Pc2`), `code` — маршрут клавиатуры (`protocol::KeyboardRoute`), `reason` — `protocol::RouteReason`, а `handoff_id != 0` означает переход по краю.
  - `link::HandoffStats { begun, ready, timeouts, skipped, total_ready_ms; std::uint16_t max_ready_ms; }`.
  - `class link::HandoffGate` с методами `begin(hid::Target, std::uint16_t id, std::uint32_t now_ms)`, `cancel()`, `ready(hid::Target from, std::uint16_t id, std::uint32_t now_ms)`, `tick(std::uint32_t now_ms)`, `bool closed(hid::Target) const`, `bool pending() const`, `std::uint16_t id() const`, `hid::Target target() const`, `std::uint32_t version() const`, `const HandoffStats& stats() const`.
  - Методы `OutputRuntime`: `void handoff_ready(hid::Target from, std::uint16_t id, std::uint32_t now_ms)`, `link::LinkEdgeState edge_state() const`, `std::uint32_t edge_version() const`, `const link::HandoffStats& handoff_stats() const`.

- [ ] **Step 1: Тесты `HandoffGate`.** `tests/firmware_native/test_handoff_gate.cpp`:

```cpp
// Holding the pointer back while the computer it arrives at places it - and
// never for long.

#include "link/edge.hpp"
#include "link/handoff_gate.hpp"
#include "test_support.hpp"

using duo_input::hid::Target;
using duo_input::link::HandoffGate;
using duo_input::link::kHandoffMissesBeforeSkip;
using duo_input::link::kHandoffTimeoutMs;

TEST_CASE(a_begun_handoff_holds_only_the_arriving_computer) {
    HandoffGate gate;
    gate.begin(Target::Pc2, 7, 100);
    CHECK(gate.closed(Target::Pc2));
    CHECK_FALSE(gate.closed(Target::Pc1));
    CHECK(gate.pending());
    CHECK_EQ(gate.id(), 7u);
}

TEST_CASE(the_right_answer_opens_it_and_is_timed) {
    HandoffGate gate;
    gate.begin(Target::Pc2, 7, 100);
    gate.ready(Target::Pc1, 7, 103);  // the wrong computer
    gate.ready(Target::Pc2, 6, 104);  // an old handoff
    CHECK(gate.closed(Target::Pc2));
    gate.ready(Target::Pc2, 7, 109);
    CHECK_FALSE(gate.closed(Target::Pc2));
    CHECK_FALSE(gate.pending());
    CHECK_EQ(gate.stats().ready, 1u);
    CHECK_EQ(gate.stats().total_ready_ms, 9u);
    CHECK_EQ(gate.stats().max_ready_ms, 9u);
    // U2 repeats its last answer on every reply. Once is counted.
    gate.ready(Target::Pc2, 7, 120);
    CHECK_EQ(gate.stats().ready, 1u);
}

TEST_CASE(silence_opens_it_after_the_timeout) {
    HandoffGate gate;
    gate.begin(Target::Pc2, 7, 100);
    gate.tick(100 + kHandoffTimeoutMs - 1);
    CHECK(gate.closed(Target::Pc2));
    gate.tick(100 + kHandoffTimeoutMs);
    CHECK_FALSE(gate.closed(Target::Pc2));
    CHECK_FALSE(gate.pending());
    CHECK_EQ(gate.stats().timeouts, 1u);
}

TEST_CASE(any_other_route_move_cancels_it) {
    HandoffGate gate;
    gate.begin(Target::Pc2, 7, 100);
    gate.cancel();
    CHECK_FALSE(gate.closed(Target::Pc2));
    CHECK_FALSE(gate.pending());
    CHECK_EQ(gate.stats().timeouts, 0u);
}

TEST_CASE(a_computer_that_never_answers_stops_being_waited_for) {
    HandoffGate gate;
    std::uint32_t now = 100;
    for (std::uint16_t id = 1; id <= kHandoffMissesBeforeSkip; ++id) {
        gate.begin(Target::Pc2, id, now);
        CHECK(gate.closed(Target::Pc2));
        now += kHandoffTimeoutMs;
        gate.tick(now);
    }
    gate.begin(Target::Pc2, 10, now);
    CHECK_FALSE(gate.closed(Target::Pc2));
    // Still announced: if somebody is there after all, they place the pointer.
    CHECK(gate.pending());
    CHECK_EQ(gate.stats().skipped, 1u);

    // They answer it. From now on they are waited for again.
    gate.ready(Target::Pc2, 10, now + 2);
    gate.begin(Target::Pc2, 11, now + 300);
    CHECK(gate.closed(Target::Pc2));
}

TEST_CASE(a_late_answer_still_proves_somebody_is_listening) {
    HandoffGate gate;
    std::uint32_t now = 100;
    for (std::uint16_t id = 1; id < kHandoffMissesBeforeSkip; ++id) {
        gate.begin(Target::Pc2, id, now);
        now += kHandoffTimeoutMs;
        gate.tick(now);
    }
    gate.begin(Target::Pc2, 9, now);
    now += kHandoffTimeoutMs;
    gate.tick(now);
    gate.ready(Target::Pc2, 9, now + 5);  // after the timeout
    gate.begin(Target::Pc2, 10, now + 300);
    CHECK(gate.closed(Target::Pc2));
}

TEST_CASE(the_version_moves_whenever_pending_or_the_id_does) {
    HandoffGate gate;
    const std::uint32_t before = gate.version();
    gate.begin(Target::Pc2, 1, 0);
    const std::uint32_t begun = gate.version();
    CHECK(begun != before);
    gate.ready(Target::Pc2, 1, 3);
    CHECK(gate.version() != begun);
}
```

- [ ] **Step 2: Тесты ворот в `OutputRuntime`.** Добавить в конец `tests/firmware_native/test_output_runtime.cpp`:

```cpp
// --------------------------------------------------------------- handoff gate

namespace {

OutputCommand route_moved(Route mouse, std::uint16_t handoff_id, std::uint16_t position = 0x8000) {
    OutputCommand command;
    command.kind = CommandKind::RouteMoved;
    command.route = mouse;
    command.code = static_cast<std::uint8_t>(duo_input::protocol::KeyboardRoute::PC2);
    command.reason = static_cast<std::uint8_t>(
        handoff_id != 0 ? duo_input::protocol::RouteReason::EDGE
                        : duo_input::protocol::RouteReason::BINDING);
    command.handoff_id = handoff_id;
    command.side = static_cast<std::uint8_t>(duo_input::protocol::EdgeSide::LEFT);
    command.position = position;
    return command;
}

OutputCommand buttons(Route route, std::uint8_t mask) {
    OutputCommand command;
    command.kind = CommandKind::MouseButtons;
    command.route = route;
    command.code = mask;
    return command;
}

}  // namespace

TEST_CASE(movement_for_the_arriving_computer_waits_for_its_handoff_and_is_not_lost) {
    OutputRuntime runtime;
    CHECK(runtime.submit(route_moved(Route::Pc2, 7)));
    CHECK(runtime.submit(movement(Route::Pc2, 5)));
    CHECK(runtime.submit(movement(Route::Pc2, 3)));
    runtime.drain(100, 100000);

    CHECK_EQ(runtime.snapshot(Target::Pc2).mouse.delta_x, 0);
    // A publisher that asks for it anyway is given nothing and takes nothing.
    CHECK_EQ(runtime.take_snapshot(Target::Pc2).mouse.delta_x, 0);

    runtime.handoff_ready(Target::Pc2, 7, 105);
    CHECK_EQ(runtime.snapshot(Target::Pc2).mouse.delta_x, 8);
    CHECK_EQ(runtime.take_snapshot(Target::Pc2).mouse.delta_x, 8);
    CHECK_EQ(runtime.snapshot(Target::Pc2).mouse.delta_x, 0);
}

TEST_CASE(buttons_are_held_back_with_the_movement) {
    OutputRuntime runtime;
    runtime.submit(route_moved(Route::Pc2, 7));
    runtime.submit(buttons(Route::Pc2, 1));
    runtime.drain(100, 100000);
    // A click before the pointer is placed would land where the pointer was.
    CHECK_EQ(runtime.snapshot(Target::Pc2).mouse.buttons, 0u);
    runtime.handoff_ready(Target::Pc2, 7, 104);
    CHECK_EQ(runtime.snapshot(Target::Pc2).mouse.buttons, 1u);
}

TEST_CASE(the_gate_opens_by_itself_and_the_held_movement_goes_out) {
    OutputRuntime runtime;
    runtime.submit(route_moved(Route::Pc2, 7));
    runtime.submit(movement(Route::Pc2, 4));
    runtime.drain(100, 100000);
    runtime.drain(100 + duo_input::link::kHandoffTimeoutMs - 1, 0);
    CHECK_EQ(runtime.snapshot(Target::Pc2).mouse.delta_x, 0);
    runtime.drain(100 + duo_input::link::kHandoffTimeoutMs, 0);
    CHECK_EQ(runtime.snapshot(Target::Pc2).mouse.delta_x, 4);
    CHECK_EQ(runtime.handoff_stats().timeouts, 1u);
}

TEST_CASE(the_computer_being_left_is_never_held) {
    OutputRuntime runtime;
    runtime.submit(route_moved(Route::Pc2, 7));
    runtime.submit(movement(Route::Pc1, 6));
    runtime.drain(100, 100000);
    CHECK_EQ(runtime.snapshot(Target::Pc1).mouse.delta_x, 6);
}

TEST_CASE(a_route_move_that_is_not_a_crossing_cancels_the_wait) {
    OutputRuntime runtime;
    runtime.submit(route_moved(Route::Pc2, 7));
    runtime.submit(movement(Route::Pc2, 2));
    runtime.drain(100, 100000);
    runtime.submit(route_moved(Route::Pc2, 0));
    runtime.drain(101, 101000);
    CHECK_EQ(runtime.snapshot(Target::Pc2).mouse.delta_x, 2);
    CHECK_FALSE(runtime.edge_state().handoff_active);
}

TEST_CASE(the_edge_state_says_where_input_goes_and_what_is_pending) {
    OutputRuntime runtime;
    const std::uint32_t at_boot = runtime.edge_version();
    runtime.submit(route_moved(Route::Pc2, 7, 0x1234));
    runtime.drain(100, 100000);
    const duo_input::link::LinkEdgeState state = runtime.edge_state();
    CHECK(state.route.mouse == duo_input::protocol::MouseRoute::PC2);
    CHECK(state.route.keyboard == duo_input::protocol::KeyboardRoute::PC2);
    CHECK(state.route.reason == duo_input::protocol::RouteReason::EDGE);
    CHECK(state.handoff_active);
    CHECK_EQ(state.handoff.id, 7u);
    CHECK(state.handoff.side == duo_input::protocol::EdgeSide::LEFT);
    CHECK_EQ(state.handoff.t, 0x1234u);
    const std::uint32_t moved = runtime.edge_version();
    CHECK(moved != at_boot);

    runtime.handoff_ready(Target::Pc2, 7, 103);
    CHECK_FALSE(runtime.edge_state().handoff_active);
    CHECK(runtime.edge_version() != moved);
}
```

- [ ] **Step 3: Зарегистрировать тест ворот.** В `tests/firmware_native/CMakeLists.txt`:

```cmake
add_executable(duo_handoff_gate_test
    test_main.cpp
    test_handoff_gate.cpp
)

target_link_libraries(duo_handoff_gate_test PRIVATE duo_common)

if(MSVC)
    target_compile_options(duo_handoff_gate_test PRIVATE /EHsc)
endif()

add_test(NAME handoff_gate COMMAND duo_handoff_gate_test)
```

- [ ] **Step 4: Убедиться, что не собирается.**
  Run: `cmake --build --preset native --clean-first`
  Expected: FAIL, нет `link/handoff_gate.hpp`, `CommandKind::RouteMoved`.

- [ ] **Step 5: `OutputCommand`.** В `firmware/common/runtime/output_command.hpp`:
  - в `enum class CommandKind` после `ReleaseAll,` добавить:

```cpp
    /// The routes moved. ``route`` is where the mouse goes now, ``code`` the
    /// keyboard route, ``reason`` why. A nonzero ``handoff_id`` makes it an
    /// edge crossing: the mouse is held back from that computer until it says
    /// it has placed its pointer. Zero cancels any such wait.
    RouteMoved,
```

  - в `struct OutputCommand` после поля `origin_us` добавить:

```cpp
    /// RouteMoved: the edge crossing this move is, or zero when it is not one.
    std::uint16_t handoff_id = 0;
    /// RouteMoved with a handoff: where along the entry edge, 0..65535.
    std::uint16_t position = 0;
    /// RouteMoved with a handoff: the entry edge, as protocol::EdgeSide.
    std::uint8_t side = 0;
    /// RouteMoved: why, as protocol::RouteReason.
    std::uint8_t reason = 0;
```

- [ ] **Step 6: `HandoffGate`.** `firmware/common/link/handoff_gate.hpp`:

```cpp
#pragma once

// Holding the pointer back while the computer it is arriving at places it.
//
// When the mouse crosses an edge, the computer it arrives at has to put its
// pointer where the other one left off. Until it says it has, movement sent to
// it would move the pointer from wherever it happened to be - so the movement
// is held instead, added up, and sent as one report once the pointer is in
// place. It is never held for long: if the computer does not answer within
// kHandoffTimeoutMs the gate opens anyway. The mouse is already routed there;
// the worst a silent computer costs is a pointer that starts from where it
// was, which is exactly what a hotkey switch does today.
//
// A computer that leaves kHandoffMissesBeforeSkip handoffs in a row unanswered
// has nobody listening, and is not waited for again until it answers one -
// eighty milliseconds of a dead mouse on every crossing would be a fault of
// its own.
//
// U1's Core 0 only. Pure: the clock is passed in.

#include <cstdint>

#include "hid/types.hpp"

namespace duo_input::link {

struct HandoffStats {
    std::uint32_t begun = 0;
    /// Answered while the gate was closed.
    std::uint32_t ready = 0;
    std::uint32_t timeouts = 0;
    /// Begun without closing, because that computer had stopped answering.
    std::uint32_t skipped = 0;
    std::uint32_t total_ready_ms = 0;
    std::uint16_t max_ready_ms = 0;
};

class HandoffGate {
public:
    /// The mouse has just been routed to ``target`` by an edge crossing.
    void begin(hid::Target target, std::uint16_t id, std::uint32_t now_ms);
    /// The route moved for some other reason. Whatever was pending is over.
    void cancel();
    /// ``from`` says it has placed handoff ``id``. Repeats are harmless.
    void ready(hid::Target from, std::uint16_t id, std::uint32_t now_ms);
    /// Opens the gate once the wait has run out.
    void tick(std::uint32_t now_ms);

    /// Is movement for ``target`` being held back?
    bool closed(hid::Target target) const { return closed_ && target == target_; }
    /// Is a handoff waiting to be placed - held, or begun while skipping?
    bool pending() const { return pending_; }
    std::uint16_t id() const { return id_; }
    hid::Target target() const { return target_; }
    /// Changes whenever pending() or id() does.
    std::uint32_t version() const { return version_; }
    const HandoffStats& stats() const { return stats_; }

private:
    void finish();

    bool pending_ = false;
    bool closed_ = false;
    hid::Target target_ = hid::Target::Pc1;
    std::uint16_t id_ = 0;
    std::uint32_t since_ms_ = 0;
    std::uint8_t misses_[hid::kTargetCount] = {};
    std::uint32_t version_ = 0;
    HandoffStats stats_{};
};

}  // namespace duo_input::link
```

  `firmware/common/link/handoff_gate.cpp`:

```cpp
#include "link/handoff_gate.hpp"

#include "link/edge.hpp"

namespace duo_input::link {
namespace {

std::size_t index_of(hid::Target target) { return static_cast<std::size_t>(target); }

}  // namespace

void HandoffGate::begin(hid::Target target, std::uint16_t id, std::uint32_t now_ms) {
    pending_ = true;
    target_ = target;
    id_ = id;
    since_ms_ = now_ms;
    ++stats_.begun;
    const bool skipping = misses_[index_of(target)] >= kHandoffMissesBeforeSkip;
    closed_ = !skipping;
    if (skipping) {
        ++stats_.skipped;
    }
    ++version_;
}

void HandoffGate::cancel() {
    if (pending_) {
        finish();
    }
}

void HandoffGate::ready(hid::Target from, std::uint16_t id, std::uint32_t now_ms) {
    if (id == 0 || from != target_ || id != id_) {
        return;
    }
    // Late or not, an answer proves somebody is placing pointers over there.
    misses_[index_of(from)] = 0;
    if (!pending_) {
        return;
    }
    if (closed_) {
        const std::uint32_t waited = now_ms - since_ms_;
        ++stats_.ready;
        stats_.total_ready_ms += waited;
        const std::uint16_t clipped =
            waited > 0xFFFFu ? static_cast<std::uint16_t>(0xFFFF) : static_cast<std::uint16_t>(waited);
        if (clipped > stats_.max_ready_ms) {
            stats_.max_ready_ms = clipped;
        }
    }
    finish();
}

void HandoffGate::tick(std::uint32_t now_ms) {
    if (!pending_ ||
        static_cast<std::int32_t>(now_ms - since_ms_) < static_cast<std::int32_t>(kHandoffTimeoutMs)) {
        return;
    }
    if (closed_) {
        ++stats_.timeouts;
        std::uint8_t& misses = misses_[index_of(target_)];
        if (misses != 0xFF) {
            ++misses;
        }
    }
    finish();
}

void HandoffGate::finish() {
    pending_ = false;
    closed_ = false;
    ++version_;
}

}  // namespace duo_input::link
```

  Добавить `link/handoff_gate.cpp` в `firmware/common/CMakeLists.txt` после `link/edge.cpp`.

- [ ] **Step 7: `OutputRuntime`.** В `firmware/u1_main/output_runtime.hpp`:
  - добавить includes `#include "link/edge.hpp"` и `#include "link/handoff_gate.hpp"`;
  - в публичную часть, после `void release_all();`:

```cpp
    /// A computer says it has placed handoff ``id``. From PC1's CDC or from
    /// U2's replies; repeats are harmless.
    void handoff_ready(hid::Target from, std::uint16_t id, std::uint32_t now_ms) {
        gate_.ready(from, id, now_ms);
    }

    /// Where input goes, why, and the handoff being waited on - what U2 and
    /// both computers are told.
    link::LinkEdgeState edge_state() const;

    /// Changes whenever edge_state() does. Both terms only ever grow.
    std::uint32_t edge_version() const { return route_version_ + gate_.version(); }

    const link::HandoffStats& handoff_stats() const { return gate_.stats(); }
```

  - в приватную часть:

```cpp
    /// Holds the arriving computer's mouse while it places its pointer.
    link::HandoffGate gate_;
    link::RouteState route_{};
    link::Handoff handoff_{};
    std::uint32_t route_version_ = 0;
    /// The clock of the current drain, for a RouteMoved applied inside it.
    std::uint32_t now_ms_ = 0;
```

  В `firmware/u1_main/output_runtime.cpp`:
  - первые строки `drain()`, до проверки `refused`:

```cpp
    now_ms_ = now_ms;
    // Before anything is applied: a handoff whose time has run out releases
    // the movement it was holding in this pass, not the next.
    gate_.tick(now_ms);
```

  - в `process()`, в `switch` перед `default: break;`:

```cpp
        case CommandKind::RouteMoved: {
            const hid::Target mouse =
                command.route == Route::Pc2 ? hid::Target::Pc2 : hid::Target::Pc1;
            route_.mouse = mouse == hid::Target::Pc2 ? protocol::MouseRoute::PC2
                                                     : protocol::MouseRoute::PC1;
            route_.keyboard = static_cast<protocol::KeyboardRoute>(command.code);
            route_.reason = static_cast<protocol::RouteReason>(command.reason);
            ++route_version_;
            if (command.handoff_id != 0) {
                handoff_.id = command.handoff_id;
                handoff_.side = static_cast<protocol::EdgeSide>(command.side);
                handoff_.t = command.position;
                gate_.begin(mouse, command.handoff_id, now_ms_);
            } else {
                gate_.cancel();
            }
            return;
        }
```

  - заменить `snapshot` и `take_snapshot`:

```cpp
hid::TargetSnapshot OutputRuntime::snapshot(hid::Target target) const {
    hid::TargetSnapshot snapshot = outputs_.snapshot(target);
    if (gate_.closed(target)) {
        // Nothing has happened to this computer's mouse yet, as far as anyone
        // sending to it can tell: its pointer is still being placed.
        snapshot.mouse = hid::MouseSnapshot{};
    }
    return snapshot;
}

hid::TargetSnapshot OutputRuntime::take_snapshot(hid::Target target) {
    if (gate_.closed(target)) {
        // Held, not taken: the movement goes out in one report once the
        // pointer is in place, and taking it now would throw it away.
        return snapshot(target);
    }
    return outputs_.take_snapshot(target);
}
```

  - добавить реализацию:

```cpp
link::LinkEdgeState OutputRuntime::edge_state() const {
    link::LinkEdgeState state;
    state.route = route_;
    state.handoff_active = gate_.pending();
    state.handoff = handoff_;
    return state;
}
```

  `touches_keyboard` не меняется: `RouteMoved` попадает в `default` и ничего не ждёт.

- [ ] **Step 8: Прогнать.**
  Run: `cmake --build --preset native --clean-first` и `ctest --preset native -R "handoff_gate|output_runtime|macro_reports|macro_publication" --output-on-failure`
  Expected: PASS.

- [ ] **Step 9: Commit.**

```bash
git add firmware/common/runtime/output_command.hpp firmware/common/link/handoff_gate.hpp firmware/common/link/handoff_gate.cpp firmware/common/CMakeLists.txt firmware/u1_main/output_runtime.hpp firmware/u1_main/output_runtime.cpp tests/firmware_native/test_handoff_gate.cpp tests/firmware_native/test_output_runtime.cpp tests/firmware_native/CMakeLists.txt
git commit -m "feat(u1): hold the arriving computer's mouse until it places its pointer"
```

---

### Task 6: Переход по краю на ядре 1

**Files:**
- Modify: `firmware/u1_main/mapping/engine.hpp`, `firmware/u1_main/mapping/engine.cpp`
- Modify: `firmware/u1_main/core1_runtime.hpp`, `firmware/u1_main/core1_runtime.cpp`
- Test: `tests/firmware_native/test_binding_engine.cpp`, `tests/firmware_native/test_core1_runtime.cpp`

**Interfaces:**
- Consumes: `mapping::EdgeArbiter`, `EdgeDecision`, `EdgeCounters` (Task 4); `CommandKind::RouteMoved` и поля команды (Task 5); `link::EdgeContact` (Task 2).
- Produces:
  - `bool BindingEngine::any_mouse_button_held() const`;
  - `bool Core1Runtime::post_edge_contact(hid::Target source, const link::EdgeContact& contact, std::uint32_t received_ms)` — вызывает ядро 0;
  - `void Core1Runtime::set_edge_alive(bool pc1, bool pc2)` — вызывает ядро 0;
  - `mapping::EdgeCounters Core1Runtime::edge_counters() const` — можно читать с ядра 0.
  - **Поведение:** каждая реальная смена маршрута (привязка, макрос, профиль, край) публикует один `RouteMoved`. Загрузка, оставившая маршруты на PC1/PC1, не публикует ничего: ядро 0 стартует там же, с причиной `BOOT`. Для края `RouteMoved` идёт после команд отпускания и раньше дельты, перешедшей порог.

- [ ] **Step 1: Тест движка.** Добавить в `tests/firmware_native/test_binding_engine.cpp` (имена `InputEvent`/`InputEventKind`/`BindingEngine` там уже импортированы; если нет — добавить `using` по образцу шапки файла):

```cpp
TEST_CASE(any_held_mouse_button_counts_including_one_left_behind_by_a_route_change) {
    BindingEngine engine;
    CHECK_FALSE(engine.any_mouse_button_held());

    InputEvent down;
    down.kind = InputEventKind::MouseButtonDown;
    down.code = 0;
    (void)engine.handle(down);
    CHECK(engine.any_mouse_button_held());

    // Orphaned by a switch: still under the finger, still a drag in progress.
    (void)engine.set_mouse_route(duo_input::config::MouseRoute::PC2);
    CHECK(engine.any_mouse_button_held());

    InputEvent up = down;
    up.kind = InputEventKind::MouseButtonUp;
    (void)engine.handle(up);
    CHECK_FALSE(engine.any_mouse_button_held());
}
```

- [ ] **Step 2: Тесты ядра 1.** Добавить в конец `tests/firmware_native/test_core1_runtime.cpp`:

```cpp
// ------------------------------------------------------------ the screen edge

namespace {

duo_input::link::EdgeContact right_edge(std::uint16_t gen = 1, std::uint16_t threshold = 20) {
    duo_input::link::EdgeContact contact;
    contact.gen = gen;
    contact.active = true;
    contact.side = duo_input::protocol::EdgeSide::RIGHT;
    contact.t = 0x4000;
    contact.threshold = threshold;
    return contact;
}

/// Where the last RouteMoved and the given command kinds sit in the queue.
int index_of_last(const RecordingSink& sink, CommandKind kind) {
    for (int i = static_cast<int>(sink.commands.size()) - 1; i >= 0; --i) {
        if (sink.commands[static_cast<std::size_t>(i)].kind == kind) return i;
    }
    return -1;
}

const OutputCommand* last_route_moved(const RecordingSink& sink) {
    const int at = index_of_last(sink, CommandKind::RouteMoved);
    return at < 0 ? nullptr : &sink.commands[static_cast<std::size_t>(at)];
}

/// PC1's pointer against its right edge, both computers alive, delivered to
/// Core 1 the way its loop delivers it: posted, then a tick.
void touch_right_edge(Core1Runtime& runtime, std::uint32_t now, std::uint16_t gen = 1) {
    CHECK(runtime.post_edge_contact(duo_input::hid::Target::Pc1, right_edge(gen), now));
    runtime.set_edge_alive(true, true);
    runtime.tick(now);
}

}  // namespace

TEST_CASE(a_boot_that_leaves_the_routes_where_core0_starts_says_nothing) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    CHECK_EQ(sink.count_of(CommandKind::RouteMoved), 0);
}

TEST_CASE(a_boot_profile_that_starts_elsewhere_is_published) {
    RecordingSink sink;
    TwoProfiles profiles;
    profiles.mouse_zero = duo_input::config::MouseRoute::PC2;
    profiles.keyboard_zero = duo_input::config::KeyboardRoute::PC2;
    Core1Runtime runtime(sink, profiles);
    CHECK_EQ(sink.count_of(CommandKind::RouteMoved), 1);
    const OutputCommand* boot = last_route_moved(sink);
    CHECK(boot != nullptr);
    CHECK(boot->route == duo_input::runtime::Route::Pc2);
    CHECK_EQ(boot->code, static_cast<std::uint8_t>(duo_input::config::KeyboardRoute::PC2));
    CHECK(boot->reason == static_cast<std::uint8_t>(duo_input::protocol::RouteReason::PROFILE));
    CHECK_EQ(boot->handoff_id, 0u);
}

TEST_CASE(a_push_through_the_edge_moves_the_mouse_and_opens_a_handoff) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    touch_right_edge(runtime, 1000);
    sink.commands.clear();

    runtime.handle_input(motion(15, 0), 1001);
    CHECK_EQ(sink.count_of(CommandKind::RouteMoved), 0);
    runtime.handle_input(motion(10, 0), 1002);

    const OutputCommand* moved = last_route_moved(sink);
    CHECK(moved != nullptr);
    CHECK(moved->route == duo_input::runtime::Route::Pc2);
    CHECK(moved->handoff_id != 0u);
    CHECK(moved->side == static_cast<std::uint8_t>(duo_input::protocol::EdgeSide::LEFT));
    CHECK_EQ(moved->position, 0x4000u);
    CHECK(moved->reason == static_cast<std::uint8_t>(duo_input::protocol::RouteReason::EDGE));

    // PC1 is let go of first, then the gate closes, then the push that went
    // through is the first thing PC2 is sent.
    const int release = index_of_last(sink, CommandKind::ReleaseRoute);
    const int route = index_of_last(sink, CommandKind::RouteMoved);
    const int delta = index_of_last(sink, CommandKind::MouseDelta);
    CHECK(release >= 0 && release < route);
    CHECK(route < delta);
    CHECK(sink.commands[static_cast<std::size_t>(delta)].route == duo_input::runtime::Route::Pc2);
}

TEST_CASE(the_keyboard_follows_an_edge_crossing_only_under_synchronised_control) {
    for (const bool synchronised : {false, true}) {
        RecordingSink sink;
        TwoProfiles profiles;
        Core1Runtime runtime(sink, profiles);
        runtime.set_synchronised_control(synchronised);
        touch_right_edge(runtime, 1000);
        runtime.handle_input(motion(25, 0), 1001);
        const OutputCommand* moved = last_route_moved(sink);
        CHECK(moved != nullptr);
        const auto expected = synchronised ? duo_input::config::KeyboardRoute::PC2
                                           : duo_input::config::KeyboardRoute::PC1;
        CHECK_EQ(moved->code, static_cast<std::uint8_t>(expected));
        CHECK(runtime.engine().keyboard_route() == expected);
    }
}

TEST_CASE(a_held_button_keeps_the_pointer_on_its_screen) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    touch_right_edge(runtime, 1000);
    runtime.handle_input(button(InputEventKind::MouseButtonDown, 0), 1000);
    sink.commands.clear();
    runtime.handle_input(motion(50, 0), 1001);
    CHECK_EQ(sink.count_of(CommandKind::RouteMoved), 0);
    CHECK(runtime.engine().mouse_route() == duo_input::config::MouseRoute::PC1);
    runtime.tick(1002);  // the counters Core 0 reads are published at the tick
    CHECK_EQ(runtime.edge_counters().refused_button, 1u);
}

TEST_CASE(each_crossing_gets_its_own_handoff_id) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    touch_right_edge(runtime, 1000);
    runtime.handle_input(motion(25, 0), 1001);
    const std::uint16_t first = last_route_moved(sink)->handoff_id;

    // Back from PC2's left edge, a new episode after the cooldown.
    duo_input::link::EdgeContact left = right_edge(1);
    left.side = duo_input::protocol::EdgeSide::LEFT;
    CHECK(runtime.post_edge_contact(duo_input::hid::Target::Pc2, left, 1400));
    runtime.tick(1400);
    runtime.handle_input(motion(-25, 0), 1401);
    const OutputCommand* back = last_route_moved(sink);
    CHECK(back->route == duo_input::runtime::Route::Pc1);
    CHECK(back->handoff_id != 0u);
    CHECK(back->handoff_id != first);
}

TEST_CASE(a_binding_route_change_is_published_without_a_handoff) {
    RecordingSink sink;
    TwoProfiles profiles;
    Binding toggle;
    toggle.trigger = TriggerKind::KEYBOARD_USAGE;
    toggle.code = 0x42;  // F9
    toggle.mode = BindingMode::REPLACE;
    toggle.action = ActionKind::TOGGLE_MOUSE_ROUTE;
    profiles.profile_zero.push_back(toggle);
    Core1Runtime runtime(sink, profiles);
    sink.commands.clear();

    runtime.handle_input(key(InputEventKind::KeyDown, 0x42), 10);
    const OutputCommand* moved = last_route_moved(sink);
    CHECK(moved != nullptr);
    CHECK(moved->route == duo_input::runtime::Route::Pc2);
    CHECK_EQ(moved->handoff_id, 0u);
    CHECK(moved->reason == static_cast<std::uint8_t>(duo_input::protocol::RouteReason::BINDING));

    // A keystroke that moves nothing publishes nothing.
    sink.commands.clear();
    runtime.handle_input(key(InputEventKind::KeyUp, 0x42), 11);
    runtime.handle_input(key(InputEventKind::KeyDown, 0x04), 12);
    CHECK_EQ(sink.count_of(CommandKind::RouteMoved), 0);
}

TEST_CASE(a_hotkey_starts_the_cooldown_for_the_edge) {
    RecordingSink sink;
    TwoProfiles profiles;
    Binding toggle;
    toggle.trigger = TriggerKind::KEYBOARD_USAGE;
    toggle.code = 0x42;
    toggle.mode = BindingMode::REPLACE;
    toggle.action = ActionKind::TOGGLE_MOUSE_ROUTE;
    profiles.profile_zero.push_back(toggle);
    Core1Runtime runtime(sink, profiles);
    runtime.set_edge_alive(true, true);

    runtime.handle_input(key(InputEventKind::KeyDown, 0x42), 1000);  // to PC2
    duo_input::link::EdgeContact left = right_edge(4);
    left.side = duo_input::protocol::EdgeSide::LEFT;
    CHECK(runtime.post_edge_contact(duo_input::hid::Target::Pc2, left, 1010));
    runtime.tick(1010);
    sink.commands.clear();
    runtime.handle_input(motion(-25, 0), 1011);
    CHECK_EQ(sink.count_of(CommandKind::RouteMoved), 0);
    runtime.tick(1012);
    CHECK_EQ(runtime.edge_counters().refused_cooldown, 1u);
}

TEST_CASE(a_contact_reaches_core1_only_at_its_tick) {
    RecordingSink sink;
    TwoProfiles profiles;
    Core1Runtime runtime(sink, profiles);
    CHECK(runtime.post_edge_contact(duo_input::hid::Target::Pc1, right_edge(), 1000));
    runtime.set_edge_alive(true, true);
    sink.commands.clear();
    runtime.handle_input(motion(25, 0), 1001);
    CHECK_EQ(sink.count_of(CommandKind::RouteMoved), 0);
    runtime.tick(1002);
    runtime.handle_input(motion(25, 0), 1003);
    CHECK_EQ(sink.count_of(CommandKind::RouteMoved), 1);
}
```

  Если `key`, `button` или `motion` в этом файле объявлены с другими именами, использовать те, что есть: они определены в начале файла.

- [ ] **Step 3: Убедиться, что не собирается.**
  Run: `cmake --build --preset native --clean-first`
  Expected: FAIL, нет `any_mouse_button_held`, `post_edge_contact`.

- [ ] **Step 4: Движок.** В `engine.hpp` после `mouse_route()`:

```cpp
    /// Is any mouse button physically down - including one a route change
    /// left orphaned, and one a binding swallowed? A finger on a button is a
    /// drag in progress, and the edge of the screen must not end it.
    bool any_mouse_button_held() const;
```

  В `engine.cpp` перед `set_keyboard_route`:

```cpp
bool BindingEngine::any_mouse_button_held() const {
    for (std::size_t index = 0; index < held_count_; ++index) {
        if (held_[index].kind == InputEventKind::MouseButtonDown) {
            return true;
        }
    }
    return false;
}
```

- [ ] **Step 5: Заголовок `Core1Runtime`.** В `core1_runtime.hpp`:
  - includes: `#include "hid/types.hpp"`, `#include "link/edge.hpp"`, `#include "mapping/edge_arbiter.hpp"`, `#include "protocol/generated.hpp"`;
  - в публичную часть после `request_release_all()`:

```cpp
    // --- the edge of the screen, fed from Core 0

    /// A contact from ``source``'s computer, as its board heard it. Core 0
    /// calls this; Core 1 takes it at its next tick. False when the queue
    /// between them is full - the computer repeats itself within 100 ms.
    bool post_edge_contact(hid::Target source, const link::EdgeContact& contact,
                           std::uint32_t received_ms);

    /// Which computers could take the pointer now. Core 0, every pass.
    void set_edge_alive(bool pc1, bool pc2) {
        edge_alive_.store(static_cast<std::uint8_t>((pc1 ? 1u : 0u) | (pc2 ? 2u : 0u)),
                          std::memory_order_release);
    }

    /// What the edge has done, for GET_EDGE_STATS. Safe from Core 0.
    mapping::EdgeCounters edge_counters() const;
```

  - в приватную часть:

```cpp
    struct EdgeUpdate {
        hid::Target source = hid::Target::Pc1;
        link::EdgeContact contact{};
        std::uint32_t received_ms = 0;
    };

    /// Core 1's copies of what Core 0 posted, taken once per tick.
    void take_edge_updates();
    void cross_edge(const mapping::EdgeDecision& decision, std::uint32_t now_ms);
    /// Tell Core 0 where input goes, if that changed - or, for an edge
    /// crossing, always: the gate has to close.
    void publish_routes(protocol::RouteReason reason, std::uint32_t now_ms,
                        const mapping::EdgeDecision* crossing = nullptr);
    hid::Target mouse_target() const;

    mapping::EdgeArbiter edge_;
    /// Core 0 is the only producer, Core 1 the only consumer.
    duo_input::runtime::SpscQueue<EdgeUpdate, 8> edge_updates_;
    /// Bit 0 PC1, bit 1 PC2. Written by Core 0.
    std::atomic<std::uint8_t> edge_alive_{0};
    /// Never zero once used: zero on the wire means "no handoff".
    std::uint16_t handoff_id_ = 0;
    /// What Core 0 was last told. OutputRuntime starts at PC1/PC1 as well, so
    /// a boot that leaves the routes there has nothing to say.
    std::uint8_t published_mouse_ = static_cast<std::uint8_t>(config::MouseRoute::PC1);
    std::uint8_t published_keyboard_ = static_cast<std::uint8_t>(config::KeyboardRoute::PC1);

    /// Written by Core 1 after each tick, read by Core 0.
    struct PublishedEdgeCounters {
        std::atomic<std::uint32_t> crossings{0};
        std::atomic<std::uint32_t> refused_button{0};
        std::atomic<std::uint32_t> refused_target_dead{0};
        std::atomic<std::uint32_t> refused_cooldown{0};
        std::atomic<std::uint32_t> contacts_inactive{0};
    };
    PublishedEdgeCounters edge_published_;
```

  Поля ставятся сразу после `macros::MacroScheduler macros_;`. Конструктор вызывает `set_profile_now(0)` (и через него `publish_routes`) в своём теле, когда все члены уже созданы, так что порядок объявления здесь ни на что не влияет; место выбрано по смыслу.

- [ ] **Step 6: Реализация `Core1Runtime`.** В `core1_runtime.cpp`:
  - в `handle_input` заменить последнюю строку `apply(engine_.handle(event), now_ms);` на:

```cpp
    if (event.kind == InputEventKind::MouseMove) {
        const mapping::EdgeDecision decision = edge_.on_motion(
            mouse_target(), event.x, event.y, engine_.any_mouse_button_held(), now_ms);
        if (decision.cross) {
            // Before the movement itself, so the push that went through is the
            // first thing the arriving computer is sent - after its gate.
            cross_edge(decision, now_ms);
        }
    }

    apply(engine_.handle(event), now_ms);
    publish_routes(protocol::RouteReason::BINDING, now_ms);
```

  - в `drain_macros`, в ветках `SetKeyboardRoute` и `SetMouseRoute`, после `apply(...)` добавить `publish_routes(protocol::RouteReason::MACRO, now_ms);`;
  - в конце `set_profile_now`, перед `active_profile_.store(...)`, добавить `publish_routes(protocol::RouteReason::PROFILE, now_ms);`;
  - первой строкой `tick()` добавить `take_edge_updates();`;
  - новые функции:

```cpp
bool Core1Runtime::post_edge_contact(hid::Target source, const link::EdgeContact& contact,
                                     std::uint32_t received_ms) {
    EdgeUpdate update;
    update.source = source;
    update.contact = contact;
    update.received_ms = received_ms;
    return edge_updates_.push(update);
}

hid::Target Core1Runtime::mouse_target() const {
    return engine_.mouse_route() == config::MouseRoute::PC2 ? hid::Target::Pc2
                                                            : hid::Target::Pc1;
}

void Core1Runtime::take_edge_updates() {
    const std::uint8_t alive = edge_alive_.load(std::memory_order_acquire);
    edge_.set_target_alive(hid::Target::Pc1, (alive & 1u) != 0);
    edge_.set_target_alive(hid::Target::Pc2, (alive & 2u) != 0);

    EdgeUpdate update;
    while (edge_updates_.pop(update)) {
        edge_.set_contact(update.source, update.contact, update.received_ms, mouse_target());
    }

    const mapping::EdgeCounters& counters = edge_.counters();
    edge_published_.crossings.store(counters.crossings, std::memory_order_relaxed);
    edge_published_.refused_button.store(counters.refused_button, std::memory_order_relaxed);
    edge_published_.refused_target_dead.store(counters.refused_target_dead,
                                              std::memory_order_relaxed);
    edge_published_.refused_cooldown.store(counters.refused_cooldown,
                                           std::memory_order_relaxed);
    edge_published_.contacts_inactive.store(counters.contacts_inactive,
                                            std::memory_order_relaxed);
}

mapping::EdgeCounters Core1Runtime::edge_counters() const {
    mapping::EdgeCounters counters;
    counters.crossings = edge_published_.crossings.load(std::memory_order_relaxed);
    counters.refused_button = edge_published_.refused_button.load(std::memory_order_relaxed);
    counters.refused_target_dead =
        edge_published_.refused_target_dead.load(std::memory_order_relaxed);
    counters.refused_cooldown =
        edge_published_.refused_cooldown.load(std::memory_order_relaxed);
    counters.contacts_inactive =
        edge_published_.contacts_inactive.load(std::memory_order_relaxed);
    return counters;
}

void Core1Runtime::cross_edge(const mapping::EdgeDecision& decision, std::uint32_t now_ms) {
    const config::MouseRoute wanted = decision.to == hid::Target::Pc2 ? config::MouseRoute::PC2
                                                                      : config::MouseRoute::PC1;
    // The very move a mouse-route binding makes: it lets go of what was held
    // on the computer being left, and brings the keyboard along under
    // synchronised control. The edge is one more way of asking, not a second
    // way of routing.
    apply(engine_.set_mouse_route(wanted), now_ms);
    if (engine_.mouse_route() != wanted) {
        return;
    }
    publish_routes(protocol::RouteReason::EDGE, now_ms, &decision);
}

void Core1Runtime::publish_routes(protocol::RouteReason reason, std::uint32_t now_ms,
                                  const mapping::EdgeDecision* crossing) {
    const auto mouse = static_cast<std::uint8_t>(engine_.mouse_route());
    const auto keyboard = static_cast<std::uint8_t>(engine_.keyboard_route());
    if (crossing == nullptr && mouse == published_mouse_ && keyboard == published_keyboard_) {
        return;
    }
    published_mouse_ = mouse;
    published_keyboard_ = keyboard;

    OutputCommand command;
    command.kind = CommandKind::RouteMoved;
    command.route = mouse_route();
    command.code = keyboard;
    command.reason = static_cast<std::uint8_t>(reason);
    if (crossing != nullptr) {
        if (++handoff_id_ == 0) {
            handoff_id_ = 1;
        }
        command.handoff_id = handoff_id_;
        command.side = static_cast<std::uint8_t>(crossing->entry_side);
        command.position = crossing->t;
    }
    submit(command);
    edge_.route_changed(now_ms);
}
```

- [ ] **Step 7: Прогнать всё нативное.**
  Run: `cmake --build --preset native --clean-first` и `ctest --preset native --output-on-failure`
  Expected: PASS. Если старый тест считает команды и теперь видит лишний `RouteMoved` (например, «после свопа профиля в очереди ровно N команд»), поправить тест: ожидаемое число растёт на один `RouteMoved` там, где маршрут действительно сменился. Каждую такую правку перечислить в сообщении коммита.

- [ ] **Step 8: Commit.**

```bash
git add firmware/u1_main/mapping/engine.hpp firmware/u1_main/mapping/engine.cpp firmware/u1_main/core1_runtime.hpp firmware/u1_main/core1_runtime.cpp tests/firmware_native/test_binding_engine.cpp tests/firmware_native/test_core1_runtime.cpp
git commit -m "feat(u1): cross the screen edge on Core 1 through move_route"
```

---

### Task 7: `EdgeService` — ядро 0 U1

**Files:**
- Create: `firmware/u1_main/edge_service.hpp`, `firmware/u1_main/edge_service.cpp`
- Create: `tests/firmware_native/test_edge_service.cpp`
- Modify: `tests/firmware_native/CMakeLists.txt`

**Interfaces:**
- Consumes: `link::*` (Task 2).
- Produces (namespace `duo_input::u1`):
  - интерфейсы `IEdgeCore1 { post_edge_contact; set_edge_alive; }`, `IEdgeOutput { handoff_ready; edge_state; edge_version; }`, `IEdgePush { push_handoff(const link::Handoff&); push_route_state(const link::RouteState&); }`, `IEdgeHost { on_pc1_contact(const link::EdgeContact&); on_pc1_ready(std::uint16_t); }`, `IEndpointEdgeSink { on_endpoint_edge_state(const link::EndpointEdgeState&); }`;
  - `struct EdgeOutbox { link::LinkEdgeState state; std::uint32_t version; bool valid; }`;
  - `class EdgeService : public IEdgeHost, public IEndpointEdgeSink` с методами `EdgeService(IEdgeCore1&, IEdgeOutput&, IEdgePush&)`, `void service(std::uint32_t now_ms, bool pc1_usb_ready, bool link_answered)`, `const EdgeOutbox& outbox() const`, `std::uint32_t contact_queue_drops() const`.

- [ ] **Step 1: Тесты.** `tests/firmware_native/test_edge_service.cpp`:

```cpp
// U1's Core 0 side of the screen edge, with both cores and PC1's CDC faked.

#include <vector>

#include "edge_service.hpp"
#include "link/edge.hpp"
#include "test_support.hpp"

using duo_input::hid::Target;
using duo_input::link::EdgeContact;
using duo_input::link::EndpointEdgeState;
using duo_input::link::Handoff;
using duo_input::link::LinkEdgeState;
using duo_input::link::RouteState;
using duo_input::protocol::EdgeSide;
using duo_input::protocol::MouseRoute;
using duo_input::u1::EdgeService;

namespace {

struct FakeCore1 final : duo_input::u1::IEdgeCore1 {
    struct Posted {
        Target source;
        EdgeContact contact;
        std::uint32_t at;
    };
    std::vector<Posted> posted;
    bool accept = true;
    bool pc1_alive = false;
    bool pc2_alive = false;
    bool post_edge_contact(Target source, const EdgeContact& contact,
                           std::uint32_t received_ms) override {
        if (!accept) return false;
        posted.push_back({source, contact, received_ms});
        return true;
    }
    void set_edge_alive(bool pc1, bool pc2) override {
        pc1_alive = pc1;
        pc2_alive = pc2;
    }
};

struct FakeOutput final : duo_input::u1::IEdgeOutput {
    struct Ready {
        Target from;
        std::uint16_t id;
    };
    std::vector<Ready> ready;
    LinkEdgeState state{};
    std::uint32_t version = 0;
    void handoff_ready(Target from, std::uint16_t id, std::uint32_t) override {
        ready.push_back({from, id});
    }
    LinkEdgeState edge_state() const override { return state; }
    std::uint32_t edge_version() const override { return version; }
};

struct FakePush final : duo_input::u1::IEdgePush {
    std::vector<Handoff> handoffs;
    std::vector<RouteState> routes;
    void push_handoff(const Handoff& handoff) override { handoffs.push_back(handoff); }
    void push_route_state(const RouteState& state) override { routes.push_back(state); }
};

struct Rig {
    FakeCore1 core1;
    FakeOutput output;
    FakePush push;
    EdgeService edge{core1, output, push};
};

EdgeContact contact(std::uint16_t gen, bool active = true) {
    EdgeContact c;
    c.gen = gen;
    c.active = active;
    c.side = EdgeSide::LEFT;
    c.t = 10;
    c.threshold = 100;
    return c;
}

EndpointEdgeState from_u2(std::uint16_t gen, std::uint16_t ready = 0,
                          std::uint8_t flags = duo_input::link::kEndpointUsbReady) {
    EndpointEdgeState state;
    state.contact = contact(gen);
    state.last_ready_id = ready;
    state.flags = flags;
    return state;
}

}  // namespace

TEST_CASE(a_pc1_contact_goes_to_core1_stamped_with_this_pass) {
    Rig rig;
    rig.edge.service(500, true, true);
    rig.edge.on_pc1_contact(contact(3));
    CHECK_EQ(rig.core1.posted.size(), 1u);
    CHECK(rig.core1.posted[0].source == Target::Pc1);
    CHECK_EQ(rig.core1.posted[0].at, 500u);
    CHECK_EQ(rig.core1.posted[0].contact.gen, 3u);
}

TEST_CASE(a_pc1_answer_reaches_the_gate_as_pc1s) {
    Rig rig;
    rig.edge.service(500, true, true);
    rig.edge.on_pc1_ready(9);
    CHECK_EQ(rig.output.ready.size(), 1u);
    CHECK(rig.output.ready[0].from == Target::Pc1);
    CHECK_EQ(rig.output.ready[0].id, 9u);
}

TEST_CASE(pc2s_repeated_contact_is_passed_on_when_it_changes_and_to_keep_it_fresh) {
    Rig rig;
    rig.edge.service(1000, true, true);
    rig.edge.on_endpoint_edge_state(from_u2(1));
    rig.edge.service(1010, true, true);
    rig.edge.on_endpoint_edge_state(from_u2(1));
    CHECK_EQ(rig.core1.posted.size(), 1u);  // unchanged, too soon
    rig.edge.service(1050, true, true);
    rig.edge.on_endpoint_edge_state(from_u2(1));
    CHECK_EQ(rig.core1.posted.size(), 2u);  // unchanged, but 50 ms on
    rig.edge.on_endpoint_edge_state(from_u2(2));
    CHECK_EQ(rig.core1.posted.size(), 3u);  // changed
    CHECK(rig.core1.posted[2].source == Target::Pc2);
}

TEST_CASE(pc2s_last_answer_reaches_the_gate_as_pc2s) {
    Rig rig;
    rig.edge.service(1000, true, true);
    rig.edge.on_endpoint_edge_state(from_u2(1, 12));
    CHECK_EQ(rig.output.ready.size(), 1u);
    CHECK(rig.output.ready[0].from == Target::Pc2);
    CHECK_EQ(rig.output.ready[0].id, 12u);
}

TEST_CASE(pc2_can_take_the_pointer_only_while_u2_speaks_the_edge_and_its_usb_is_up) {
    Rig rig;
    rig.edge.service(1000, true, true);
    CHECK(rig.core1.pc1_alive);
    CHECK_FALSE(rig.core1.pc2_alive);  // never heard ENDPOINT_EDGE_STATE

    rig.edge.on_endpoint_edge_state(from_u2(1));
    rig.edge.service(1001, true, true);
    CHECK(rig.core1.pc2_alive);

    rig.edge.service(1001, true, false);  // link not answering
    CHECK_FALSE(rig.core1.pc2_alive);

    rig.edge.on_endpoint_edge_state(from_u2(1, 0, 0));  // USB suspended
    rig.edge.service(1002, true, true);
    CHECK_FALSE(rig.core1.pc2_alive);

    rig.edge.on_endpoint_edge_state(from_u2(1));
    rig.edge.service(1002 + duo_input::link::kEdgeCapableWindowMs + 1, true, true);
    CHECK_FALSE(rig.core1.pc2_alive);  // gone quiet
}

TEST_CASE(pc1_is_told_where_to_put_its_pointer_once_per_handoff) {
    Rig rig;
    rig.output.state.route.mouse = MouseRoute::PC1;
    rig.output.state.handoff_active = true;
    rig.output.state.handoff.id = 4;
    rig.output.state.handoff.side = EdgeSide::RIGHT;
    rig.output.version = 1;
    rig.edge.service(100, true, true);
    rig.edge.service(101, true, true);
    CHECK_EQ(rig.push.handoffs.size(), 1u);
    CHECK_EQ(rig.push.handoffs[0].id, 4u);
}

TEST_CASE(pc1_is_not_told_about_a_handoff_to_pc2) {
    Rig rig;
    rig.output.state.route.mouse = MouseRoute::PC2;
    rig.output.state.handoff_active = true;
    rig.output.state.handoff.id = 4;
    rig.edge.service(100, true, true);
    CHECK(rig.push.handoffs.empty());
}

TEST_CASE(the_route_state_is_pushed_when_it_changes_and_every_second) {
    Rig rig;
    rig.edge.service(100, true, true);
    CHECK_EQ(rig.push.routes.size(), 1u);
    rig.edge.service(200, true, true);
    CHECK_EQ(rig.push.routes.size(), 1u);
    rig.output.state.route.mouse = MouseRoute::PC2;
    rig.edge.service(201, true, true);
    CHECK_EQ(rig.push.routes.size(), 2u);
    rig.edge.service(201 + duo_input::link::kRouteStatePushMs, true, true);
    CHECK_EQ(rig.push.routes.size(), 3u);
}

TEST_CASE(the_outbox_mirrors_the_output_state_and_version) {
    Rig rig;
    CHECK_FALSE(rig.edge.outbox().valid);
    rig.output.version = 7;
    rig.output.state.handoff.id = 3;
    rig.edge.service(100, true, true);
    CHECK(rig.edge.outbox().valid);
    CHECK_EQ(rig.edge.outbox().version, 7u);
    CHECK_EQ(rig.edge.outbox().state.handoff.id, 3u);
}

TEST_CASE(a_full_queue_to_core1_is_counted) {
    Rig rig;
    rig.core1.accept = false;
    rig.edge.service(100, true, true);
    rig.edge.on_pc1_contact(contact(1));
    CHECK_EQ(rig.edge.contact_queue_drops(), 1u);
}
```

- [ ] **Step 2: Зарегистрировать.**

```cmake
add_executable(duo_edge_service_test
    test_main.cpp
    test_edge_service.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u1_main/edge_service.cpp
)

target_link_libraries(duo_edge_service_test PRIVATE duo_common)
target_include_directories(duo_edge_service_test PRIVATE
    ${CMAKE_SOURCE_DIR}/firmware/u1_main
)

if(MSVC)
    target_compile_options(duo_edge_service_test PRIVATE /EHsc)
endif()

add_test(NAME edge_service COMMAND duo_edge_service_test)
```

- [ ] **Step 3: Убедиться, что не собирается.**
  Run: `cmake --build --preset native --clean-first`
  Expected: FAIL.

- [ ] **Step 4: Заголовок.** `firmware/u1_main/edge_service.hpp`:

```cpp
#pragma once

// U1's Core 0 side of the screen edge: what the two computers say about their
// pointers, carried to Core 1, and what Core 1 decided, carried back out.
//
// PC1's agent speaks over CDC, through ConfigService. PC2's speaks over CDC to
// U2, which repeats it over SPI, through SpiMaster. Both arrive here, are
// stamped with the pass this board heard them in, and go to Core 1 - which
// alone decides whether the pointer crosses. What it decided comes back as the
// routes and a pending handoff in OutputRuntime; this tells PC1 over CDC, and
// leaves an outbox SpiMaster repeats to U2.
//
// Everything it talks to is behind an interface, so it runs on a desktop with
// both cores and the CDC faked.

#include <cstdint>

#include "hid/types.hpp"
#include "link/edge.hpp"

namespace duo_input::u1 {

/// Core 1's half: where contacts go and where liveness is published.
class IEdgeCore1 {
public:
    virtual ~IEdgeCore1() = default;
    virtual bool post_edge_contact(hid::Target source, const link::EdgeContact& contact,
                                   std::uint32_t received_ms) = 0;
    virtual void set_edge_alive(bool pc1, bool pc2) = 0;
};

/// The output side: who answers handoffs, and what the routes are.
class IEdgeOutput {
public:
    virtual ~IEdgeOutput() = default;
    virtual void handoff_ready(hid::Target from, std::uint16_t id, std::uint32_t now_ms) = 0;
    virtual link::LinkEdgeState edge_state() const = 0;
    virtual std::uint32_t edge_version() const = 0;
};

/// PC1's CDC, for the two things the board says without being asked.
class IEdgePush {
public:
    virtual ~IEdgePush() = default;
    virtual void push_handoff(const link::Handoff& handoff) = 0;
    virtual void push_route_state(const link::RouteState& state) = 0;
};

/// Where ConfigService hands PC1's two notifications.
class IEdgeHost {
public:
    virtual ~IEdgeHost() = default;
    virtual void on_pc1_contact(const link::EdgeContact& contact) = 0;
    virtual void on_pc1_ready(std::uint16_t id) = 0;
};

/// Where SpiMaster hands U2's ENDPOINT_EDGE_STATE.
class IEndpointEdgeSink {
public:
    virtual ~IEndpointEdgeSink() = default;
    virtual void on_endpoint_edge_state(const link::EndpointEdgeState& state) = 0;
};

/// What SpiMaster repeats to U2: the latest edge state and its version.
struct EdgeOutbox {
    link::LinkEdgeState state{};
    std::uint32_t version = 0;
    bool valid = false;
};

class EdgeService final : public IEdgeHost, public IEndpointEdgeSink {
public:
    EdgeService(IEdgeCore1& core1, IEdgeOutput& output, IEdgePush& push)
        : core1_(core1), output_(output), push_(push) {}

    void on_pc1_contact(const link::EdgeContact& contact) override;
    void on_pc1_ready(std::uint16_t id) override;
    void on_endpoint_edge_state(const link::EndpointEdgeState& state) override;

    /// Once per pass of Core 0's loop, after the drain and before the link.
    void service(std::uint32_t now_ms, bool pc1_usb_ready, bool link_answered);

    const EdgeOutbox& outbox() const { return outbox_; }
    std::uint32_t contact_queue_drops() const { return drops_; }

private:
    void post(hid::Target source, const link::EdgeContact& contact);

    IEdgeCore1& core1_;
    IEdgeOutput& output_;
    IEdgePush& push_;

    /// The time of the current pass; it stamps whatever arrives during it.
    std::uint32_t now_ms_ = 0;
    std::uint32_t drops_ = 0;

    bool endpoint_seen_ = false;
    std::uint32_t endpoint_seen_ms_ = 0;
    bool endpoint_usb_ready_ = false;
    link::EdgeContact last_pc2_contact_{};
    bool posted_pc2_ = false;
    std::uint32_t last_pc2_post_ms_ = 0;

    EdgeOutbox outbox_{};
    std::uint16_t pushed_handoff_id_ = 0;
    link::RouteState pushed_route_{};
    bool pushed_any_route_ = false;
    std::uint32_t last_route_push_ms_ = 0;
};

}  // namespace duo_input::u1
```

- [ ] **Step 5: Реализация.** `firmware/u1_main/edge_service.cpp`:

```cpp
#include "edge_service.hpp"

namespace duo_input::u1 {
namespace {

/// U2 repeats PC2's contact every 10-20 ms. Passed to Core 1 when it changes,
/// and otherwise at least this often, so Core 1's TTL measures PC2's agent
/// and not this board's thrift. U2 itself stops calling a contact active once
/// PC2's agent has stopped refreshing it.
constexpr std::uint32_t kPc2RepostMs = 50;

bool same_contact(const link::EdgeContact& a, const link::EdgeContact& b) {
    return a.gen == b.gen && a.active == b.active && a.side == b.side &&
           a.neighbor == b.neighbor && a.t == b.t && a.threshold == b.threshold;
}

bool since(std::uint32_t now_ms, std::uint32_t then_ms, std::uint32_t at_least_ms) {
    return static_cast<std::int32_t>(now_ms - then_ms) >= static_cast<std::int32_t>(at_least_ms);
}

}  // namespace

void EdgeService::post(hid::Target source, const link::EdgeContact& contact) {
    if (!core1_.post_edge_contact(source, contact, now_ms_)) {
        ++drops_;
    }
}

void EdgeService::on_pc1_contact(const link::EdgeContact& contact) {
    post(hid::Target::Pc1, contact);
}

void EdgeService::on_pc1_ready(std::uint16_t id) {
    output_.handoff_ready(hid::Target::Pc1, id, now_ms_);
}

void EdgeService::on_endpoint_edge_state(const link::EndpointEdgeState& state) {
    endpoint_seen_ = true;
    endpoint_seen_ms_ = now_ms_;
    endpoint_usb_ready_ = (state.flags & link::kEndpointUsbReady) != 0;
    output_.handoff_ready(hid::Target::Pc2, state.last_ready_id, now_ms_);

    if (!posted_pc2_ || !same_contact(state.contact, last_pc2_contact_) ||
        since(now_ms_, last_pc2_post_ms_, kPc2RepostMs)) {
        post(hid::Target::Pc2, state.contact);
        last_pc2_contact_ = state.contact;
        posted_pc2_ = true;
        last_pc2_post_ms_ = now_ms_;
    }
}

void EdgeService::service(std::uint32_t now_ms, bool pc1_usb_ready, bool link_answered) {
    now_ms_ = now_ms;

    // PC2 can take the pointer only through a U2 that speaks the edge - an
    // older one never sends ENDPOINT_EDGE_STATE - over a link that answers,
    // with a USB that is up and awake. A sleeping PC2 is still mounted.
    const bool heard = endpoint_seen_ &&
                       !since(now_ms, endpoint_seen_ms_, link::kEdgeCapableWindowMs + 1);
    core1_.set_edge_alive(pc1_usb_ready, link_answered && heard && endpoint_usb_ready_);

    const link::LinkEdgeState state = output_.edge_state();
    outbox_.state = state;
    outbox_.version = output_.edge_version();
    outbox_.valid = true;

    // PC1 is told where to put its pointer when PC1 is where it is arriving.
    // Once per handoff: a second warp after the first would yank a pointer
    // the operator is already moving.
    if (state.handoff_active && state.route.mouse == protocol::MouseRoute::PC1 &&
        state.handoff.id != pushed_handoff_id_) {
        push_.push_handoff(state.handoff);
        pushed_handoff_id_ = state.handoff.id;
    }

    const bool changed = !pushed_any_route_ || state.route.mouse != pushed_route_.mouse ||
                         state.route.keyboard != pushed_route_.keyboard ||
                         state.route.reason != pushed_route_.reason;
    if (changed || since(now_ms, last_route_push_ms_, link::kRouteStatePushMs)) {
        push_.push_route_state(state.route);
        pushed_route_ = state.route;
        pushed_any_route_ = true;
        last_route_push_ms_ = now_ms;
    }
}

}  // namespace duo_input::u1
```

- [ ] **Step 6: Прогнать.**
  Run: `cmake --build --preset native --clean-first` и `ctest --preset native -R edge_service --output-on-failure`
  Expected: PASS.

- [ ] **Step 7: Commit.**

```bash
git add firmware/u1_main/edge_service.hpp firmware/u1_main/edge_service.cpp tests/firmware_native/test_edge_service.cpp tests/firmware_native/CMakeLists.txt
git commit -m "feat(u1): Core 0 edge service between both computers and Core 1"
```

---

### Task 8: CDC U1 — уведомления вне номеров, push, `EXCHANGE_LAYOUT`, `GET_EDGE_STATS`

**Files:**
- Modify: `firmware/u1_main/config_service.hpp`, `firmware/u1_main/config_service.cpp`
- Test: `tests/firmware_native/test_config_service.cpp`

**Interfaces:**
- Consumes: `IEdgeHost`, `IEdgePush` (Task 7); `link::LayoutBook`, кодеки (Task 2).
- Produces:
  - `class ConfigService : public IEdgePush` с методами `push_handoff`, `push_route_state`;
  - `void set_edge_host(IEdgeHost*)`, `void set_layout_book(link::LayoutBook*)`, `void set_edge_stats(const EdgeStats&)`;
  - `struct EdgeStats` (в `config_service.hpp`, поля в порядке провода): `crossings, handoffs_ready, handoff_timeouts, handoffs_skipped, refused_button, refused_target_dead, refused_cooldown, contacts_inactive, contact_queue_drops, total_ready_ms` (`u32`), `max_ready_ms` (`u16`).
  - **Поведение:** `SCREEN_EXTENSION` предлагается в HELLO, только если заданы и обработчик края, и книга раскладки.

- [ ] **Step 1: Тесты.** Добавить в конец `tests/firmware_native/test_config_service.cpp`:

```cpp
// --------------------------------------------------------- screen extension

namespace {

struct EdgeHostRecorder final : duo_input::u1::IEdgeHost {
    std::vector<duo_input::link::EdgeContact> contacts;
    std::vector<std::uint16_t> ready;
    void on_pc1_contact(const duo_input::link::EdgeContact& contact) override {
        contacts.push_back(contact);
    }
    void on_pc1_ready(std::uint16_t id) override { ready.push_back(id); }
};

/// A Link with the screen extension wired in, the way main() wires it.
struct EdgeLink {
    Link link;
    EdgeHostRecorder edge;
    duo_input::link::LayoutBook layout;
    EdgeLink() {
        link.service.set_edge_host(&edge);
        link.service.set_layout_book(&layout);
    }

    /// Sequence 0, nothing expected back, and the request count untouched.
    void notify(CdcMessageType type, const std::uint8_t* payload, std::size_t size) {
        CdcFrame frame;
        frame.type = type;
        frame.sequence = 0;
        frame.payload = duo_input::protocol::ByteView{payload, size};
        std::uint8_t wire[128];
        std::uint8_t scratch[128];
        std::size_t written = 0;
        duo_input::protocol::encode_cdc_frame(
            frame, duo_input::protocol::MutableByteView{wire, sizeof(wire)},
            duo_input::protocol::MutableByteView{scratch, sizeof(scratch)}, written);
        link.replies.clear();
        link.service.on_cdc_bytes(wire, written);
    }
};

const std::uint8_t kContact[9] = {1, 0, 1, 2, 0, 0, 0x80, 120, 0};

}  // namespace

TEST_CASE(screen_extension_is_offered_only_where_it_is_wired) {
    Link bare;
    std::uint8_t all[4] = {0xFF, 0xFF, 0xFF, 0xFF};
    const std::uint32_t without =
        u32_at(bare.send(CdcMessageType::HELLO, all, sizeof(all)), 3);
    CHECK((without & static_cast<std::uint32_t>(
                         duo_input::protocol::Capability::SCREEN_EXTENSION)) == 0u);

    EdgeLink wired;
    const std::uint32_t with =
        u32_at(wired.link.send(CdcMessageType::HELLO, all, sizeof(all)), 3);
    CHECK((with & static_cast<std::uint32_t>(
                      duo_input::protocol::Capability::SCREEN_EXTENSION)) != 0u);
}

TEST_CASE(a_contact_is_delivered_and_never_answered) {
    EdgeLink rig;
    rig.link.hello();
    rig.notify(CdcMessageType::EDGE_CONTACT, kContact, sizeof(kContact));
    CHECK_EQ(rig.link.replies.count(), 0u);
    CHECK_EQ(rig.edge.contacts.size(), 1u);
    CHECK(rig.edge.contacts[0].active);
    CHECK_EQ(rig.edge.contacts[0].threshold, 120u);

    const std::uint8_t ready[2] = {7, 0};
    rig.notify(CdcMessageType::HANDOFF_READY, ready, sizeof(ready));
    CHECK_EQ(rig.link.replies.count(), 0u);
    CHECK_EQ(rig.edge.ready.size(), 1u);
    CHECK_EQ(rig.edge.ready[0], 7u);
}

TEST_CASE(notifications_between_requests_leave_the_sequence_alone) {
    EdgeLink rig;
    rig.link.hello();
    CHECK_EQ(error_of(rig.link.send(CdcMessageType::GET_STATUS)), CdcError::Ok);
    rig.notify(CdcMessageType::EDGE_CONTACT, kContact, sizeof(kContact));
    rig.notify(CdcMessageType::EDGE_CONTACT, kContact, sizeof(kContact));
    // The next request continues the count as if nothing had come between.
    CHECK_EQ(error_of(rig.link.send(CdcMessageType::GET_STATUS)), CdcError::Ok);
}

TEST_CASE(a_notification_does_not_replace_the_cached_reply_a_retry_gets) {
    EdgeLink rig;
    rig.link.hello();
    // One real request, its wire kept so it can be retried byte for byte.
    CdcFrame request;
    request.type = CdcMessageType::GET_STATUS;
    request.sequence = rig.link.sequence++;
    std::uint8_t wire[64];
    std::uint8_t scratch[64];
    std::size_t written = 0;
    duo_input::protocol::encode_cdc_frame(
        request, duo_input::protocol::MutableByteView{wire, sizeof(wire)},
        duo_input::protocol::MutableByteView{scratch, sizeof(scratch)}, written);
    rig.link.replies.clear();
    rig.link.service.on_cdc_bytes(wire, written);
    const std::vector<std::uint8_t> first = rig.link.replies.last();

    rig.notify(CdcMessageType::EDGE_CONTACT, kContact, sizeof(kContact));

    rig.link.replies.clear();
    rig.link.service.on_cdc_bytes(wire, written);
    CHECK(rig.link.replies.count() == 1u);
    CHECK(rig.link.replies.last() == first);
}

TEST_CASE(notifications_before_the_capability_is_agreed_are_dropped) {
    EdgeLink rig;
    rig.notify(CdcMessageType::EDGE_CONTACT, kContact, sizeof(kContact));
    CHECK(rig.edge.contacts.empty());
    std::uint8_t nothing[4] = {0, 0, 0, 0};
    rig.link.send(CdcMessageType::HELLO, nothing, sizeof(nothing));
    rig.notify(CdcMessageType::EDGE_CONTACT, kContact, sizeof(kContact));
    CHECK(rig.edge.contacts.empty());
    CHECK_EQ(rig.link.replies.count(), 0u);
}

TEST_CASE(a_malformed_contact_is_dropped) {
    EdgeLink rig;
    rig.link.hello();
    const std::uint8_t no_threshold[9] = {1, 0, 1, 2, 0, 0, 0x80, 0, 0};
    rig.notify(CdcMessageType::EDGE_CONTACT, no_threshold, sizeof(no_threshold));
    CHECK(rig.edge.contacts.empty());
}

TEST_CASE(a_push_goes_out_at_sequence_zero_only_once_agreed) {
    EdgeLink rig;
    duo_input::link::Handoff handoff;
    handoff.id = 5;
    handoff.side = duo_input::protocol::EdgeSide::RIGHT;
    handoff.t = 0x100;
    rig.link.service.push_handoff(handoff);
    CHECK_EQ(rig.link.replies.count(), 0u);

    rig.link.hello();
    rig.link.replies.clear();
    rig.link.service.push_handoff(handoff);
    CHECK_EQ(rig.link.replies.count(), 1u);
    const CdcFrame pushed = rig.link.decode_last();
    CHECK(pushed.type == CdcMessageType::HANDOFF);
    CHECK_EQ(pushed.sequence, 0u);
    CHECK_EQ(pushed.payload.size, 5u);
    CHECK_EQ(pushed.payload.data[0], 5u);

    duo_input::link::RouteState route;
    route.mouse = duo_input::protocol::MouseRoute::PC2;
    rig.link.service.push_route_state(route);
    CHECK(rig.link.decode_last().type == CdcMessageType::ROUTE_STATE);

    // And the request after it is still in sequence.
    CHECK_EQ(error_of(rig.link.send(CdcMessageType::GET_STATUS)), CdcError::Ok);
}

TEST_CASE(a_host_sending_what_only_the_device_sends_is_refused) {
    EdgeLink rig;
    rig.link.hello();
    const std::uint8_t five[5] = {1, 0, 1, 0, 0};
    CHECK_EQ(error_of(rig.link.send(CdcMessageType::HANDOFF, five, sizeof(five))),
             CdcError::InvalidRequest);
}

TEST_CASE(the_layout_exchange_keeps_the_hosts_blob_and_returns_the_peers) {
    EdgeLink rig;
    rig.link.hello();
    const std::uint8_t peer[3] = {9, 8, 7};
    CHECK(rig.layout.accept_peer(duo_input::protocol::ByteView{peer, sizeof(peer)}));
    const std::uint8_t mine[4] = {1, 2, 3, 4};
    const CdcFrame reply = rig.link.send(CdcMessageType::EXCHANGE_LAYOUT, mine, sizeof(mine));
    CHECK_EQ(error_of(reply), CdcError::Ok);
    CHECK_EQ(reply.payload.size, 4u);
    CHECK_EQ(reply.payload.data[1], 9u);
    CHECK_EQ(rig.layout.local().size, 4u);

    // Empty: only asking. What was kept stays.
    const CdcFrame ask = rig.link.send(CdcMessageType::EXCHANGE_LAYOUT, nullptr, 0);
    CHECK_EQ(error_of(ask), CdcError::Ok);
    CHECK_EQ(rig.layout.local().size, 4u);

    std::uint8_t too_big[52] = {};
    CHECK_EQ(error_of(rig.link.send(CdcMessageType::EXCHANGE_LAYOUT, too_big, sizeof(too_big))),
             CdcError::BadSize);
    CHECK_EQ(rig.layout.local().size, 4u);
}

TEST_CASE(edge_stats_are_reported_in_wire_order) {
    EdgeLink rig;
    rig.link.hello();
    duo_input::u1::EdgeStats stats;
    stats.crossings = 1;
    stats.handoffs_ready = 2;
    stats.handoff_timeouts = 3;
    stats.handoffs_skipped = 4;
    stats.refused_button = 5;
    stats.refused_target_dead = 6;
    stats.refused_cooldown = 7;
    stats.contacts_inactive = 8;
    stats.contact_queue_drops = 9;
    stats.total_ready_ms = 10;
    stats.max_ready_ms = 77;
    rig.link.service.set_edge_stats(stats);
    const CdcFrame reply = rig.link.send(CdcMessageType::GET_EDGE_STATS);
    CHECK_EQ(error_of(reply), CdcError::Ok);
    CHECK_EQ(reply.payload.size, 43u);
    CHECK_EQ(u32_at(reply, 1), 1u);
    CHECK_EQ(u32_at(reply, 37), 10u);
    CHECK_EQ(reply.payload.data[41], 77u);
}

TEST_CASE(edge_requests_need_the_capability) {
    EdgeLink rig;
    std::uint8_t nothing[4] = {0, 0, 0, 0};
    rig.link.send(CdcMessageType::HELLO, nothing, sizeof(nothing));
    CHECK_EQ(error_of(rig.link.send(CdcMessageType::GET_EDGE_STATS)),
             CdcError::UnsupportedCapability);
    const std::uint8_t mine[1] = {1};
    CHECK_EQ(error_of(rig.link.send(CdcMessageType::EXCHANGE_LAYOUT, mine, 1)),
             CdcError::UnsupportedCapability);
}
```

  Тестовой цели `duo_config_service_test` не нужны новые `.cpp`: `config_service.cpp` использует только заголовок `edge_service.hpp` и `duo_common`.

- [ ] **Step 2: Убедиться, что не собирается.**
  Run: `cmake --build --preset native --clean-first`
  Expected: FAIL.

- [ ] **Step 3: Заголовок.** В `firmware/u1_main/config_service.hpp`:
  - includes: `#include "edge_service.hpp"`, `#include "link/edge.hpp"`;
  - перед `class ConfigService` добавить:

```cpp
/// GET_EDGE_STATS, gathered on Core 0 from both cores. Wire order.
struct EdgeStats {
    std::uint32_t crossings = 0;
    std::uint32_t handoffs_ready = 0;
    std::uint32_t handoff_timeouts = 0;
    std::uint32_t handoffs_skipped = 0;
    std::uint32_t refused_button = 0;
    std::uint32_t refused_target_dead = 0;
    std::uint32_t refused_cooldown = 0;
    std::uint32_t contacts_inactive = 0;
    std::uint32_t contact_queue_drops = 0;
    std::uint32_t total_ready_ms = 0;
    std::uint16_t max_ready_ms = 0;
};
```

  - объявление класса поменять на `class ConfigService final : public IEdgePush {` (если сейчас класс не `final` и от него ничего не наследуется, оставить без `final`, то есть `class ConfigService : public IEdgePush {`);
  - в публичную часть рядом с `set_address_book`:

```cpp
    /// The screen extension. Both must be wired for SCREEN_EXTENSION to be
    /// offered at all: a board that cannot act on a contact must not invite
    /// the agent to send one.
    void set_edge_host(IEdgeHost* host) { edge_host_ = host; }
    void set_layout_book(link::LayoutBook* book) { layout_ = book; }
    void set_edge_stats(const EdgeStats& stats) { edge_stats_ = stats; }

    /// Said without being asked, at sequence 0 and outside the request count.
    /// Nothing goes to a host that has not agreed SCREEN_EXTENSION.
    void push_handoff(const link::Handoff& handoff) override;
    void push_route_state(const link::RouteState& state) override;
```

  - в приватную часть:

```cpp
    bool edge_agreed() const;
    std::uint32_t offered_capabilities() const;
    void handle_edge_notification(const protocol::CdcFrame& frame);
    void push_event(protocol::CdcMessageType type, const std::uint8_t* payload,
                    std::size_t size);

    IEdgeHost* edge_host_ = nullptr;
    link::LayoutBook* layout_ = nullptr;
    EdgeStats edge_stats_{};
    /// Pushes have their own buffers: last_response_ is what a retry gets,
    /// and a push must never become it.
    std::uint8_t event_wire_[64] = {};
    std::uint8_t event_scratch_[64] = {};
```

- [ ] **Step 4: Реализация.** В `firmware/u1_main/config_service.cpp`:
  - в `device_capabilities()` добавить `| static_cast<std::uint32_t>(protocol::Capability::SCREEN_EXTENSION)`;
  - в `expected_request_size`: `EXCHANGE_LAYOUT` — в группу `return -1;`, `GET_EDGE_STATS` — в группу `return 0;`;
  - в `required_capability` перед `default`:

```cpp
        case CdcMessageType::EXCHANGE_LAYOUT:
        case CdcMessageType::GET_EDGE_STATS:
            return static_cast<std::uint32_t>(protocol::Capability::SCREEN_EXTENSION);
```

  - в `handle_frame` сразу после `const CdcFrame& frame = result.cdc;`:

```cpp
    // The two edge notifications live outside the request sequence. They are
    // sent many times a second by a host that is also writing configurations
    // and reading diagnostics, and if they took sequence numbers the next
    // real request would be refused as out of order. They are not answered,
    // not cached, and change nothing a request depends on.
    if (frame.type == CdcMessageType::EDGE_CONTACT ||
        frame.type == CdcMessageType::HANDOFF_READY) {
        handle_edge_notification(frame);
        return;
    }
```

  - в `dispatch` в обработке HELLO заменить `& device_capabilities()` на `& offered_capabilities()`;
  - в `dispatch` в проверке «travel the other way» расширить условие:

```cpp
    if (frame.type == CdcMessageType::DEVICE_INFO ||
        frame.type == CdcMessageType::CAPTURE_EVENT ||
        frame.type == CdcMessageType::HANDOFF ||
        frame.type == CdcMessageType::ROUTE_STATE) {
```

  - в `switch` в `dispatch` перед `default:`:

```cpp
        case CdcMessageType::EXCHANGE_LAYOUT: {
            if (layout_ == nullptr) {
                reply_error(frame, CdcError::UnsupportedCapability);
                return;
            }
            if (frame.payload.size > link::kLayoutBlobMaxSize) {
                reply_error(frame, CdcError::BadSize);
                return;
            }
            // Empty is a host only asking; what it gave before stays.
            if (frame.payload.size != 0) {
                layout_->set_local(frame.payload);
            }
            const link::LayoutBlob& peer = layout_->peer();
            payload[0] = static_cast<std::uint8_t>(CdcError::Ok);
            std::memcpy(payload + 1, peer.bytes, peer.size);
            reply(frame.type, frame.sequence, payload, 1 + peer.size);
            return;
        }
        case CdcMessageType::GET_EDGE_STATS: {
            payload[0] = static_cast<std::uint8_t>(CdcError::Ok);
            const std::uint32_t counters[10] = {
                edge_stats_.crossings,           edge_stats_.handoffs_ready,
                edge_stats_.handoff_timeouts,    edge_stats_.handoffs_skipped,
                edge_stats_.refused_button,      edge_stats_.refused_target_dead,
                edge_stats_.refused_cooldown,    edge_stats_.contacts_inactive,
                edge_stats_.contact_queue_drops, edge_stats_.total_ready_ms,
            };
            for (std::size_t index = 0; index < 10; ++index) {
                put_u32(payload + 1 + 4 * index, counters[index]);
            }
            put_u16(payload + 41, edge_stats_.max_ready_ms);
            reply(frame.type, frame.sequence, payload, 1 + link::kEdgeStatsSize);
            return;
        }
```

  - новые методы в конце файла (до закрывающей скобки namespace):

```cpp
std::uint32_t ConfigService::offered_capabilities() const {
    const std::uint32_t edge = static_cast<std::uint32_t>(protocol::Capability::SCREEN_EXTENSION);
    const bool wired = edge_host_ != nullptr && layout_ != nullptr;
    return wired ? device_capabilities() : (device_capabilities() & ~edge);
}

bool ConfigService::edge_agreed() const {
    return negotiated_ &&
           (negotiated_capabilities_ &
            static_cast<std::uint32_t>(protocol::Capability::SCREEN_EXTENSION)) != 0;
}

void ConfigService::handle_edge_notification(const CdcFrame& frame) {
    if (!edge_agreed() || edge_host_ == nullptr) {
        return;
    }
    if (frame.type == CdcMessageType::EDGE_CONTACT) {
        link::EdgeContact contact;
        if (link::decode_edge_contact(frame.payload, contact)) {
            edge_host_->on_pc1_contact(contact);
        }
        return;
    }
    std::uint16_t id = 0;
    if (link::decode_handoff_ready(frame.payload, id)) {
        edge_host_->on_pc1_ready(id);
    }
}

void ConfigService::push_event(CdcMessageType type, const std::uint8_t* payload,
                               std::size_t size) {
    if (!edge_agreed()) {
        return;
    }
    CdcFrame frame;
    frame.type = type;
    frame.sequence = 0;
    frame.payload = protocol::ByteView{payload, size};
    std::size_t written = 0;
    if (protocol::encode_cdc_frame(
            frame, protocol::MutableByteView{event_wire_, sizeof(event_wire_)},
            protocol::MutableByteView{event_scratch_, sizeof(event_scratch_)}, written)) {
        sink_.write(event_wire_, written);
    }
}

void ConfigService::push_handoff(const link::Handoff& handoff) {
    std::uint8_t payload[link::kHandoffSize];
    std::size_t written = 0;
    if (link::encode_handoff(handoff, protocol::MutableByteView{payload, sizeof(payload)},
                             written)) {
        push_event(CdcMessageType::HANDOFF, payload, written);
    }
}

void ConfigService::push_route_state(const link::RouteState& state) {
    std::uint8_t payload[link::kRouteStateSize];
    std::size_t written = 0;
    if (link::encode_route_state(state, protocol::MutableByteView{payload, sizeof(payload)},
                                 written)) {
        push_event(CdcMessageType::ROUTE_STATE, payload, written);
    }
}
```

  Если `put_u16` объявлен в анонимном namespace ниже места использования в `dispatch`, переместить объявление выше или использовать `put_u32` для младших двух байт так, как это уже делает файл.

- [ ] **Step 5: Прогнать.**
  Run: `cmake --build --preset native --clean-first` и `ctest --preset native -R "config_service|core_bridge" --output-on-failure`
  Expected: PASS. Если существующий тест сверяет полный набор возможностей из HELLO равенством, он упадёт на `SCREEN_EXTENSION` только для `EdgeLink`. Для голого `Link` возможность не предлагается, и существующие тесты должны остаться зелёными. Если упал тест на голом `Link`, значит неверен `offered_capabilities`: чинить код, а не тест.

- [ ] **Step 6: Commit.**

```bash
git add firmware/u1_main/config_service.hpp firmware/u1_main/config_service.cpp tests/firmware_native/test_config_service.cpp
git commit -m "feat(u1): edge notifications outside the CDC sequence, pushes, layout and stats"
```

---

### Task 9: SPI U1 — `LINK_EDGE_STATE`, блоб, ответы U2

**Files:**
- Modify: `firmware/u1_main/spi_master.hpp`, `firmware/u1_main/spi_master_poll.cpp`
- Modify: `firmware/common/link/spi_protocol.cpp` (`is_endpoint_reply`)
- Test: `tests/firmware_native/test_spi_master_poll.cpp`

**Interfaces:**
- Consumes: `EdgeOutbox`, `IEndpointEdgeSink` (Task 7); `link::LayoutBook`, кодеки (Task 2).
- Produces: `void SpiMaster::set_edge(const EdgeOutbox* outbox, IEndpointEdgeSink* sink)`, `void SpiMaster::set_layout_book(link::LayoutBook* book)`.

- [ ] **Step 1: Тесты.** Добавить в конец `tests/firmware_native/test_spi_master_poll.cpp`:

```cpp
// ---------------------------------------------------------- screen extension

namespace {

struct EndpointRecorder final : duo_input::u1::IEndpointEdgeSink {
    int calls = 0;
    duo_input::link::EndpointEdgeState last{};
    void on_endpoint_edge_state(const duo_input::link::EndpointEdgeState& state) override {
        ++calls;
        last = state;
    }
};

}  // namespace

TEST_CASE(a_changed_edge_state_goes_out_ahead_of_movement) {
    HidStateManager outputs;
    SpiMaster link;
    prime(link, outputs);
    duo_input::u1::EdgeOutbox outbox;
    outbox.valid = true;
    outbox.version = 1;
    outbox.state.handoff_active = true;
    outbox.state.handoff.id = 3;
    outbox.state.handoff.side = duo_input::protocol::EdgeSide::LEFT;
    link.set_edge(&outbox, nullptr);

    outputs.mouse_delta(Target::Pc2, 4, 0, 0, 0);
    CHECK(link.poll(1, outputs));
    CHECK_EQ(duo::test::spi_link().count_of(SpiMessageType::LINK_EDGE_STATE), 1u);
    CHECK_EQ(duo::test::spi_link().count_of(SpiMessageType::MOUSE_DELTA), 0u);
    CHECK(link.poll(2, outputs));
    CHECK_EQ(total_dx(), 4);
    CHECK_EQ(duo::test::spi_link().count_of(SpiMessageType::LINK_EDGE_STATE), 1u);
}

TEST_CASE(an_unchanged_edge_state_repeats_in_place_of_the_heartbeat) {
    HidStateManager outputs;
    SpiMaster link;
    prime(link, outputs);
    duo_input::u1::EdgeOutbox outbox;
    outbox.valid = true;
    outbox.version = 1;
    link.set_edge(&outbox, nullptr);
    CHECK(link.poll(1, outputs));  // the change
    CHECK_FALSE(link.poll(2, outputs));
    CHECK(link.poll(1 + duo_input::u1::kHeartbeatIntervalMs, outputs));
    CHECK_EQ(duo::test::spi_link().count_of(SpiMessageType::LINK_EDGE_STATE), 2u);
    CHECK_EQ(duo::test::spi_link().count_of(SpiMessageType::HEARTBEAT), 0u);
}

TEST_CASE(the_layout_blob_goes_out_on_its_interval) {
    HidStateManager outputs;
    SpiMaster link;
    prime(link, outputs);
    duo_input::link::LayoutBook layout;
    link.set_layout_book(&layout);
    CHECK_FALSE(link.poll(1, outputs));
    const std::uint8_t blob[3] = {1, 2, 3};
    layout.set_local(duo_input::protocol::ByteView{blob, sizeof(blob)});
    CHECK(link.poll(2, outputs));
    CHECK_EQ(duo::test::spi_link().count_of(SpiMessageType::LAYOUT_STATE), 1u);
    CHECK_EQ(duo::test::spi_link().payload_size[duo::test::spi_link().count - 1], 3u);
}

TEST_CASE(u2s_edge_state_reaches_the_edge_service) {
    SpiMaster link;
    EndpointRecorder sink;
    link.set_edge(nullptr, &sink);
    const std::uint8_t payload[12] = {1, 0, 1, 1, 0, 0, 0x40, 50, 0, 9, 0, 1};
    duo_input::protocol::SpiFrame frame;
    frame.type = SpiMessageType::ENDPOINT_EDGE_STATE;
    frame.payload = duo_input::protocol::ByteView{payload, sizeof(payload)};
    link.apply_reply(frame);
    CHECK_EQ(sink.calls, 1);
    CHECK_EQ(sink.last.last_ready_id, 9u);
    CHECK(sink.last.contact.active);
}

TEST_CASE(u2s_layout_blob_fills_the_peer) {
    SpiMaster link;
    duo_input::link::LayoutBook layout;
    link.set_layout_book(&layout);
    const std::uint8_t blob[2] = {5, 6};
    duo_input::protocol::SpiFrame frame;
    frame.type = SpiMessageType::ENDPOINT_LAYOUT_STATE;
    frame.payload = duo_input::protocol::ByteView{blob, sizeof(blob)};
    link.apply_reply(frame);
    CHECK_EQ(layout.peer().size, 2u);
}

TEST_CASE(only_u2s_edge_frames_are_endpoint_replies) {
    using duo_input::link::is_endpoint_reply;
    CHECK(is_endpoint_reply(SpiMessageType::ENDPOINT_EDGE_STATE));
    CHECK(is_endpoint_reply(SpiMessageType::ENDPOINT_LAYOUT_STATE));
    CHECK_FALSE(is_endpoint_reply(SpiMessageType::LINK_EDGE_STATE));
    CHECK_FALSE(is_endpoint_reply(SpiMessageType::LAYOUT_STATE));
}
```

- [ ] **Step 2: Убедиться, что не собирается.**
  Run: `cmake --build --preset native --clean-first`
  Expected: FAIL.

- [ ] **Step 3: `SpiMaster`.** В `spi_master.hpp`:
  - includes: `#include "edge_service.hpp"`, `#include "link/edge.hpp"`;
  - в публичную часть после `set_address_book`:

```cpp
    /// The screen edge: what U1 repeats to U2, and where U2's answers go.
    /// Either may be null.
    void set_edge(const EdgeOutbox* outbox, IEndpointEdgeSink* sink) {
        edge_outbox_ = outbox;
        edge_sink_ = sink;
    }

    /// The layout blobs this link carries both ways. Null means none.
    void set_layout_book(link::LayoutBook* book) { layout_ = book; }
```

  - в приватную часть:

```cpp
    bool send_edge_state(std::uint32_t now_ms);

    const EdgeOutbox* edge_outbox_ = nullptr;
    IEndpointEdgeSink* edge_sink_ = nullptr;
    link::LayoutBook* layout_ = nullptr;
    std::uint32_t edge_sent_version_ = 0;
    bool edge_ever_sent_ = false;
```

- [ ] **Step 4: `poll_snapshot` и `apply_reply`.** В `spi_master_poll.cpp`:
  - добавить `#include "link/edge.hpp"`;
  - в начале `poll_snapshot`, после `told_keyboard = false;`:

```cpp
    // The routes or a handoff changed. Ahead of everything else, addresses
    // included: while a handoff is pending the pointer is being held back on
    // this frame's account, and every pass it waits is a pass of a dead mouse
    // on the computer it is arriving at.
    if (edge_outbox_ != nullptr && edge_outbox_->valid &&
        (!edge_ever_sent_ || edge_outbox_->version != edge_sent_version_) &&
        send_edge_state(now_ms)) {
        return true;
    }
```

  - сразу после ветки адресов (после её закрывающей `}`):

```cpp
    // This computer's layout blob, on its own interval, for the same reason
    // the addresses have one: a link that is never idle never reaches a slot.
    if (layout_ != nullptr && layout_->local_due(now_ms)) {
        const link::LayoutBlob& blob = layout_->local();
        if (send(protocol::SpiMessageType::LAYOUT_STATE, protocol::ByteView{blob.bytes, blob.size},
                 now_ms)) {
            layout_->mark_local_sent(now_ms);
            return true;
        }
    }
```

  - заменить хвост с heartbeat на:

```cpp
    const bool heartbeat_due =
        !ever_sent_ || (now_ms - last_sent_ms_) >= kHeartbeatIntervalMs;
    if (heartbeat_due) {
        // With the screen extension running, the repeat of the edge state is
        // the heartbeat: it says the same "still here" and keeps U2's copy of
        // the routes fresh as well.
        if (edge_outbox_ != nullptr && edge_outbox_->valid) {
            return send_edge_state(now_ms);
        }
        return send(protocol::SpiMessageType::HEARTBEAT, protocol::ByteView{nullptr, 0},
                    now_ms);
    }
    return false;
}

bool SpiMaster::send_edge_state(std::uint32_t now_ms) {
    std::uint8_t payload[link::kLinkEdgeStateSize];
    std::size_t written = 0;
    if (!link::encode_link_edge_state(edge_outbox_->state,
                                      protocol::MutableByteView{payload, sizeof(payload)},
                                      written) ||
        !send(protocol::SpiMessageType::LINK_EDGE_STATE, protocol::ByteView{payload, written},
              now_ms)) {
        return false;
    }
    edge_sent_version_ = edge_outbox_->version;
    edge_ever_sent_ = true;
    return true;
}
```

  - в `apply_reply` после ветки `ENDPOINT_ADDRESSES`:

```cpp
    if (frame.type == protocol::SpiMessageType::ENDPOINT_EDGE_STATE) {
        link::EndpointEdgeState state;
        if (edge_sink_ != nullptr && link::decode_endpoint_edge_state(frame.payload, state)) {
            edge_sink_->on_endpoint_edge_state(state);
        }
        return;
    }
    if (frame.type == protocol::SpiMessageType::ENDPOINT_LAYOUT_STATE) {
        if (layout_ != nullptr) {
            layout_->accept_peer(frame.payload);
        }
        return;
    }
```

- [ ] **Step 5: `is_endpoint_reply`.** В `firmware/common/link/spi_protocol.cpp`:

```cpp
bool is_endpoint_reply(protocol::SpiMessageType type) {
    return type == protocol::SpiMessageType::ENDPOINT_STATUS ||
           type == protocol::SpiMessageType::ENDPOINT_ADDRESSES ||
           type == protocol::SpiMessageType::ENDPOINT_EDGE_STATE ||
           type == protocol::SpiMessageType::ENDPOINT_LAYOUT_STATE;
}
```

  Комментарий над объявлением в `spi_protocol.hpp` («U2 sends exactly one kind of message») поправить: «U2 sends only the ENDPOINT_* messages».

- [ ] **Step 6: Прогнать.**
  Run: `cmake --build --preset native --clean-first` и `ctest --preset native --output-on-failure`
  Expected: PASS.

- [ ] **Step 7: Commit.**

```bash
git add firmware/u1_main/spi_master.hpp firmware/u1_main/spi_master_poll.cpp firmware/common/link/spi_protocol.cpp firmware/common/link/spi_protocol.hpp tests/firmware_native/test_spi_master_poll.cpp
git commit -m "feat(u1): carry the edge state and layout blob over the SPI link"
```

---

### Task 10: Проводка U1 и сборки

**Files:**
- Modify: `firmware/u1_main/main.cpp`, `firmware/u1_main/CMakeLists.txt`, `firmware/u1_reference/CMakeLists.txt`

**Interfaces:**
- Consumes: всё из Tasks 5–9.

- [ ] **Step 1: CMake.**
  - `firmware/u1_main/CMakeLists.txt`: в `_duo_u1_sources` добавить `edge_service.cpp` (после `core_bridge.cpp`) и `mapping/edge_arbiter.cpp` (после `mapping/engine.cpp`).
  - `firmware/u1_reference/CMakeLists.txt`: в `target_sources` добавить `${_duo_u1_main_dir}/mapping/edge_arbiter.cpp` рядом с остальными `mapping/*.cpp`. Если их там нет, то рядом с `core1_runtime.cpp`. Сюда нужен только арбитр: эталонная сборка компилирует `core1_runtime.cpp`, но `EdgeService` не подключает и `SCREEN_EXTENSION` не предлагает.

- [ ] **Step 2: Адаптеры и объекты в `main.cpp`.** Перед `int main()` (рядом с `CdcWriter`) добавить:

```cpp
/// Core 1's side of the screen edge, as EdgeService sees it.
class Core1Edge final : public duo_input::u1::IEdgeCore1 {
public:
    bool post_edge_contact(duo_input::hid::Target source,
                           const duo_input::link::EdgeContact& contact,
                           std::uint32_t received_ms) override {
        return g_runtime.post_edge_contact(source, contact, received_ms);
    }
    void set_edge_alive(bool pc1, bool pc2) override { g_runtime.set_edge_alive(pc1, pc2); }
};

/// The output runtime's side of it.
class OutputEdge final : public duo_input::u1::IEdgeOutput {
public:
    void handoff_ready(duo_input::hid::Target from, std::uint16_t id,
                       std::uint32_t now_ms) override {
        g_outputs.handoff_ready(from, id, now_ms);
    }
    duo_input::link::LinkEdgeState edge_state() const override { return g_outputs.edge_state(); }
    std::uint32_t edge_version() const override { return g_outputs.edge_version(); }
};
```

  `g_outputs` и `g_runtime` должны быть объявлены выше этих классов. Если `g_outputs` объявлен ниже, перенести адаптеры ниже его объявления.

- [ ] **Step 3: Проводка в `main()`.** Сразу после строк `link.set_address_book(&addresses); config.set_address_book(&addresses);`:

```cpp
    // The screen edge. PC1's notifications reach EdgeService through the CDC
    // service, PC2's through the link; what Core 1 decides goes back out
    // through both. The layout blobs ride the same link as the addresses.
    static duo_input::link::LayoutBook layout;
    link.set_layout_book(&layout);
    config.set_layout_book(&layout);
    static Core1Edge core1_edge;
    static OutputEdge output_edge;
    static duo_input::u1::EdgeService edge(core1_edge, output_edge, config);
    config.set_edge_host(&edge);
    link.set_edge(&edge.outbox(), &edge);
```

- [ ] **Step 4: Цикл.** В `while (true)` сразу после `g_outputs.drain(now_ms, time_us_32());`:

```cpp
        // After the drain, which is where a RouteMoved from Core 1 lands, and
        // before the publish and the poll, so a handoff begun in this pass is
        // told to PC1 and queued for U2 in this pass too.
        edge.service(now_ms, usb.mounted() && !usb.suspended(), link.status().answered);
        {
            duo_input::u1::EdgeStats stats;
            const duo_input::u1::mapping::EdgeCounters core1 = g_runtime.edge_counters();
            const duo_input::link::HandoffStats& gate = g_outputs.handoff_stats();
            stats.crossings = core1.crossings;
            stats.handoffs_ready = gate.ready;
            stats.handoff_timeouts = gate.timeouts;
            stats.handoffs_skipped = gate.skipped;
            stats.refused_button = core1.refused_button;
            stats.refused_target_dead = core1.refused_target_dead;
            stats.refused_cooldown = core1.refused_cooldown;
            stats.contacts_inactive = core1.contacts_inactive;
            stats.contact_queue_drops = edge.contact_queue_drops();
            stats.total_ready_ms = gate.total_ready_ms;
            stats.max_ready_ms = gate.max_ready_ms;
            config.set_edge_stats(stats);
        }
```

  Добавить include `#include "edge_service.hpp"` к остальным include в начале файла.

- [ ] **Step 5: Собрать обе прошивки U1 и эталонную.**
  Run:

  ```
  cmake --preset pico-pio-usb-release
  cmake --build --preset pico-pio-usb-release --clean-first
  cmake --preset pico-pio-usb-reference-release
  cmake --build --preset pico-pio-usb-reference-release --clean-first
  cmake --preset pico-release
  cmake --build --preset pico-release --clean-first
  ```

  Expected: все три собираются без ошибок и предупреждений о новых файлах. Если toolchain PIO не настроен (`.deps/` нет), выполнить `tools/bootstrap_pio_usb_toolchain.ps1` по `docs/release/firmware-build.md`. Если и это невозможно, отметить пропуск в отчёте задачи.

- [ ] **Step 6: Прогнать сборочные Python-тесты.**
  Run: `.venv/Scripts/python.exe -m pytest tests/build -q; echo "exit=$?"`
  Expected: `exit=0`. Тест контракта `test_pio_usb_firmware_contract.py` сверяет состав ELF. Если он требует перечислить новые символы, добавить их туда, куда он указывает.

- [ ] **Step 7: Commit.**

```bash
git add firmware/u1_main/main.cpp firmware/u1_main/CMakeLists.txt firmware/u1_reference/CMakeLists.txt
git commit -m "feat(u1): wire the screen edge into the Core 0 loop"
```

---

### Task 11: U2 — `EdgeRelay` и выбор ответа

**Files:**
- Create: `firmware/u2_endpoint/edge_relay.hpp`, `firmware/u2_endpoint/edge_relay.cpp`, `firmware/u2_endpoint/reply_choice.hpp`, `firmware/u2_endpoint/reply_choice.cpp`
- Create: `tests/firmware_native/test_edge_relay.cpp`
- Modify: `tests/firmware_native/CMakeLists.txt`

**Interfaces:**
- Consumes: кодеки и константы Task 2; `link::AddressBook`.
- Produces (namespace `duo_input::u2`):
  - `class EdgeRelay` с методами `set_contact(const link::EdgeContact&, std::uint32_t now_ms)`, `set_ready(std::uint16_t id)`, `set_usb_ready(bool)`, `bool take_reply(std::uint32_t now_ms, link::EndpointEdgeState& out)`, `void other_reply()`, `void accept_link_state(const link::LinkEdgeState&)`, `bool take_handoff(link::Handoff& out)`, `bool take_route_state(std::uint32_t now_ms, link::RouteState& out)`;
  - `enum class ReplyKind { EdgeState, Addresses, Layout, Status }`;
  - `ReplyKind choose_reply(EdgeRelay* relay, link::AddressBook* addresses, link::LayoutBook* layout, std::uint32_t now_ms, link::EndpointEdgeState& edge)`.

- [ ] **Step 1: Тесты.** `tests/firmware_native/test_edge_relay.cpp`:

```cpp
// U2 relays and decides nothing - except when PC2's agent has gone quiet.

#include "edge_relay.hpp"
#include "link/edge.hpp"
#include "link/host_addresses.hpp"
#include "reply_choice.hpp"
#include "test_support.hpp"

using duo_input::link::EdgeContact;
using duo_input::link::EndpointEdgeState;
using duo_input::link::Handoff;
using duo_input::link::LinkEdgeState;
using duo_input::link::RouteState;
using duo_input::protocol::EdgeSide;
using duo_input::protocol::MouseRoute;
using duo_input::u2::EdgeRelay;
using duo_input::u2::ReplyKind;
using duo_input::u2::choose_reply;

namespace {

EdgeContact pressing(std::uint16_t gen = 1) {
    EdgeContact contact;
    contact.gen = gen;
    contact.active = true;
    contact.side = EdgeSide::LEFT;
    contact.t = 5;
    contact.threshold = 100;
    return contact;
}

LinkEdgeState handing_to_pc2(std::uint16_t id) {
    LinkEdgeState state;
    state.route.mouse = MouseRoute::PC2;
    state.handoff_active = true;
    state.handoff.id = id;
    state.handoff.side = EdgeSide::LEFT;
    state.handoff.t = 7;
    return state;
}

}  // namespace

TEST_CASE(a_new_contact_is_the_next_reply) {
    EdgeRelay relay;
    EndpointEdgeState state;
    CHECK(relay.take_reply(0, state));  // the first one always goes
    relay.set_contact(pressing(), 10);
    CHECK(relay.take_reply(11, state));
    CHECK(state.contact.active);
    CHECK_EQ(state.contact.gen, 1u);
}

TEST_CASE(an_unchanged_state_repeats_but_never_twice_in_a_row) {
    EdgeRelay relay;
    EndpointEdgeState state;
    CHECK(relay.take_reply(0, state));
    CHECK_FALSE(relay.take_reply(duo_input::link::kEndpointEdgeRefreshMs, state));
    relay.other_reply();
    CHECK(relay.take_reply(duo_input::link::kEndpointEdgeRefreshMs, state));
}

TEST_CASE(a_contact_its_agent_stopped_refreshing_is_reported_released) {
    EdgeRelay relay;
    EndpointEdgeState state;
    relay.set_contact(pressing(), 100);
    CHECK(relay.take_reply(100, state));
    CHECK(state.contact.active);
    relay.other_reply();
    CHECK(relay.take_reply(100 + duo_input::link::kEdgeContactTtlMs + 1, state));
    CHECK_FALSE(state.contact.active);
}

TEST_CASE(the_reply_carries_the_last_answer_and_the_usb) {
    EdgeRelay relay;
    EndpointEdgeState state;
    relay.set_ready(42);
    relay.set_usb_ready(true);
    CHECK(relay.take_reply(0, state));
    CHECK_EQ(state.last_ready_id, 42u);
    CHECK_EQ(state.flags, duo_input::link::kEndpointUsbReady);
}

TEST_CASE(pc2_is_told_about_a_handoff_to_it_once) {
    EdgeRelay relay;
    Handoff handoff;
    CHECK_FALSE(relay.take_handoff(handoff));
    relay.accept_link_state(handing_to_pc2(3));
    CHECK(relay.take_handoff(handoff));
    CHECK_EQ(handoff.id, 3u);
    relay.accept_link_state(handing_to_pc2(3));
    CHECK_FALSE(relay.take_handoff(handoff));
    relay.accept_link_state(handing_to_pc2(4));
    CHECK(relay.take_handoff(handoff));
}

TEST_CASE(pc2_is_not_told_about_a_handoff_to_pc1_or_one_already_over) {
    EdgeRelay relay;
    Handoff handoff;
    LinkEdgeState to_pc1 = handing_to_pc2(3);
    to_pc1.route.mouse = MouseRoute::PC1;
    relay.accept_link_state(to_pc1);
    CHECK_FALSE(relay.take_handoff(handoff));
    LinkEdgeState over = handing_to_pc2(4);
    over.handoff_active = false;
    relay.accept_link_state(over);
    CHECK_FALSE(relay.take_handoff(handoff));
}

TEST_CASE(the_routes_are_pushed_on_change_and_every_second) {
    EdgeRelay relay;
    RouteState route;
    CHECK_FALSE(relay.take_route_state(0, route));  // nothing from U1 yet
    relay.accept_link_state(LinkEdgeState{});
    CHECK(relay.take_route_state(0, route));
    CHECK_FALSE(relay.take_route_state(10, route));
    relay.accept_link_state(handing_to_pc2(1));
    CHECK(relay.take_route_state(11, route));
    CHECK(route.mouse == MouseRoute::PC2);
    CHECK(relay.take_route_state(11 + duo_input::link::kRouteStatePushMs, route));
}

TEST_CASE(choose_reply_puts_a_due_edge_state_first) {
    EdgeRelay relay;
    duo_input::link::AddressBook addresses;
    duo_input::link::LayoutBook layout;
    EndpointEdgeState edge;
    CHECK(choose_reply(&relay, &addresses, &layout, 0, edge) == ReplyKind::EdgeState);
    CHECK(choose_reply(&relay, &addresses, &layout, 1, edge) == ReplyKind::Status);
}

TEST_CASE(choose_reply_gives_the_layout_its_own_slot) {
    duo_input::link::AddressBook addresses;
    duo_input::link::LayoutBook layout;
    const std::uint8_t blob[2] = {1, 2};
    layout.set_local(duo_input::protocol::ByteView{blob, 2});
    EndpointEdgeState edge;
    int layouts = 0;
    for (int reply = 0; reply < 64; ++reply) {
        if (choose_reply(nullptr, &addresses, &layout, 0, edge) == ReplyKind::Layout) ++layouts;
    }
    CHECK_EQ(layouts, 1);
}
```

- [ ] **Step 2: Зарегистрировать.**

```cmake
add_executable(duo_edge_relay_test
    test_main.cpp
    test_edge_relay.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u2_endpoint/edge_relay.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u2_endpoint/reply_choice.cpp
)

target_link_libraries(duo_edge_relay_test PRIVATE duo_common)
target_include_directories(duo_edge_relay_test PRIVATE
    ${CMAKE_SOURCE_DIR}/firmware/u2_endpoint
)

if(MSVC)
    target_compile_options(duo_edge_relay_test PRIVATE /EHsc)
endif()

add_test(NAME edge_relay COMMAND duo_edge_relay_test)
```

- [ ] **Step 3: Убедиться, что не собирается.**
  Run: `cmake --build --preset native --clean-first`
  Expected: FAIL.

- [ ] **Step 4: `EdgeRelay`.** `firmware/u2_endpoint/edge_relay.hpp`:

```cpp
#pragma once

// U2's part in the screen edge: a relay, and very nearly nothing more.
//
// PC2's program tells U2 where its pointer is pressing; U2 repeats that to U1,
// which decides. U1 tells U2 where input goes and which handoff it is waiting
// on; U2 tells PC2's program. U2 decides nothing - but it is the only one
// that knows when PC2's program last spoke, so it is the one that stops
// calling a contact active once nobody is refreshing it. Without that, a
// program that died with its pointer at the edge would be repeated to U1
// forever.
//
// Pure: the clock is passed in.

#include <cstdint>

#include "link/edge.hpp"

namespace duo_input::u2 {

class EdgeRelay {
public:
    // --- from PC2's program
    void set_contact(const link::EdgeContact& contact, std::uint32_t now_ms);
    void set_ready(std::uint16_t id) { ready_id_ = id; }
    void set_usb_ready(bool ready) { usb_ready_ = ready; }

    // --- towards U1, one reply at a time
    /// Should the next reply be ENDPOINT_EDGE_STATE? Yes when anything in it
    /// changed, or when the last one went out kEndpointEdgeRefreshMs ago and
    /// the reply before this one was something else - so ENDPOINT_STATUS,
    /// which carries the mount flag, still gets its turn on a quiet link.
    bool take_reply(std::uint32_t now_ms, link::EndpointEdgeState& state);
    /// The reply being primed is something else.
    void other_reply() { last_was_edge_ = false; }

    // --- from U1
    void accept_link_state(const link::LinkEdgeState& state);

    // --- towards PC2's program
    /// A handoff to PC2 it has not been told about yet.
    bool take_handoff(link::Handoff& handoff);
    /// The routes, when they changed or kRouteStatePushMs after last time.
    bool take_route_state(std::uint32_t now_ms, link::RouteState& state);

private:
    link::EndpointEdgeState current(std::uint32_t now_ms) const;

    link::EdgeContact contact_{};
    std::uint32_t contact_ms_ = 0;
    std::uint16_t ready_id_ = 0;
    bool usb_ready_ = false;

    link::EndpointEdgeState sent_{};
    bool ever_sent_ = false;
    std::uint32_t sent_ms_ = 0;
    bool last_was_edge_ = false;

    link::LinkEdgeState link_{};
    bool has_link_ = false;
    std::uint16_t told_handoff_id_ = 0;
    link::RouteState told_route_{};
    bool told_any_route_ = false;
    std::uint32_t told_route_ms_ = 0;
};

}  // namespace duo_input::u2
```

  `firmware/u2_endpoint/edge_relay.cpp`:

```cpp
#include "edge_relay.hpp"

namespace duo_input::u2 {
namespace {

bool same(const link::EndpointEdgeState& a, const link::EndpointEdgeState& b) {
    return a.contact.gen == b.contact.gen && a.contact.active == b.contact.active &&
           a.contact.side == b.contact.side && a.contact.neighbor == b.contact.neighbor &&
           a.contact.t == b.contact.t && a.contact.threshold == b.contact.threshold &&
           a.last_ready_id == b.last_ready_id && a.flags == b.flags;
}

bool since(std::uint32_t now_ms, std::uint32_t then_ms, std::uint32_t at_least_ms) {
    return static_cast<std::int32_t>(now_ms - then_ms) >= static_cast<std::int32_t>(at_least_ms);
}

}  // namespace

void EdgeRelay::set_contact(const link::EdgeContact& contact, std::uint32_t now_ms) {
    contact_ = contact;
    contact_ms_ = now_ms;
}

link::EndpointEdgeState EdgeRelay::current(std::uint32_t now_ms) const {
    link::EndpointEdgeState state;
    state.contact = contact_;
    if (state.contact.active && since(now_ms, contact_ms_, link::kEdgeContactTtlMs + 1)) {
        state.contact.active = false;
    }
    state.last_ready_id = ready_id_;
    state.flags = usb_ready_ ? link::kEndpointUsbReady : 0;
    return state;
}

bool EdgeRelay::take_reply(std::uint32_t now_ms, link::EndpointEdgeState& state) {
    const link::EndpointEdgeState now = current(now_ms);
    const bool changed = !ever_sent_ || !same(now, sent_);
    const bool refresh = !last_was_edge_ && since(now_ms, sent_ms_, link::kEndpointEdgeRefreshMs);
    if (!changed && !refresh) {
        return false;
    }
    sent_ = now;
    ever_sent_ = true;
    sent_ms_ = now_ms;
    last_was_edge_ = true;
    state = now;
    return true;
}

void EdgeRelay::accept_link_state(const link::LinkEdgeState& state) {
    link_ = state;
    has_link_ = true;
}

bool EdgeRelay::take_handoff(link::Handoff& handoff) {
    if (!has_link_ || !link_.handoff_active ||
        link_.route.mouse != protocol::MouseRoute::PC2 ||
        link_.handoff.id == told_handoff_id_) {
        return false;
    }
    handoff = link_.handoff;
    told_handoff_id_ = link_.handoff.id;
    return true;
}

bool EdgeRelay::take_route_state(std::uint32_t now_ms, link::RouteState& state) {
    if (!has_link_) {
        return false;
    }
    const link::RouteState& route = link_.route;
    const bool changed = !told_any_route_ || route.mouse != told_route_.mouse ||
                         route.keyboard != told_route_.keyboard ||
                         route.reason != told_route_.reason;
    if (!changed && !since(now_ms, told_route_ms_, link::kRouteStatePushMs)) {
        return false;
    }
    told_route_ = route;
    told_any_route_ = true;
    told_route_ms_ = now_ms;
    state = route;
    return true;
}

}  // namespace duo_input::u2
```

  Замечание: тест `a_new_contact_is_the_next_reply` проверяет, что первый вызов `take_reply` всегда `true` (`!ever_sent_`), а второй с новым касанием — тоже `true`, потому что состояние изменилось. Это совместимо с правилом «не два повтора подряд»: изменение важнее.

- [ ] **Step 5: `choose_reply`.** `firmware/u2_endpoint/reply_choice.hpp`:

```cpp
#pragma once

// What U2's next SPI reply carries, decided before the transfer that carries
// it - which is the only time a slave can decide anything. A function of its
// own, apart from the DMA, so the order can be checked on a desktop.

#include <cstdint>

#include "edge_relay.hpp"
#include "link/edge.hpp"
#include "link/host_addresses.hpp"

namespace duo_input::u2 {

enum class ReplyKind : std::uint8_t {
    EdgeState,
    Addresses,
    Layout,
    Status,
};

/// An edge state that is due, then PC2's addresses in their slot, then its
/// layout blob in its slot, then the status. ``edge`` is filled for
/// EdgeState. Any pointer may be null.
ReplyKind choose_reply(EdgeRelay* relay, link::AddressBook* addresses, link::LayoutBook* layout,
                       std::uint32_t now_ms, link::EndpointEdgeState& edge);

}  // namespace duo_input::u2
```

  `firmware/u2_endpoint/reply_choice.cpp`:

```cpp
#include "reply_choice.hpp"

namespace duo_input::u2 {

ReplyKind choose_reply(EdgeRelay* relay, link::AddressBook* addresses, link::LayoutBook* layout,
                       std::uint32_t now_ms, link::EndpointEdgeState& edge) {
    if (relay != nullptr) {
        if (relay->take_reply(now_ms, edge)) {
            return ReplyKind::EdgeState;
        }
        relay->other_reply();
    }
    if (addresses != nullptr && addresses->take_reply_slot()) {
        return ReplyKind::Addresses;
    }
    if (layout != nullptr && layout->take_reply_slot()) {
        return ReplyKind::Layout;
    }
    return ReplyKind::Status;
}

}  // namespace duo_input::u2
```

- [ ] **Step 6: Прогнать.**
  Run: `cmake --build --preset native --clean-first` и `ctest --preset native -R edge_relay --output-on-failure`
  Expected: PASS.

- [ ] **Step 7: Commit.**

```bash
git add firmware/u2_endpoint/edge_relay.hpp firmware/u2_endpoint/edge_relay.cpp firmware/u2_endpoint/reply_choice.hpp firmware/u2_endpoint/reply_choice.cpp tests/firmware_native/test_edge_relay.cpp tests/firmware_native/CMakeLists.txt
git commit -m "feat(u2): edge relay with the contact TTL, and the reply choice"
```

---

### Task 12: U2 — CDC, `prime()`, цикл

**Files:**
- Modify: `firmware/u2_endpoint/address_service.hpp`, `firmware/u2_endpoint/address_service.cpp`
- Modify: `firmware/u2_endpoint/spi_slave.hpp`, `firmware/u2_endpoint/spi_slave.cpp`
- Modify: `firmware/u2_endpoint/main.cpp`, `firmware/u2_endpoint/CMakeLists.txt`
- Test: `tests/firmware_native/test_address_service.cpp`; Modify: `tests/firmware_native/CMakeLists.txt` (цель `duo_address_service_test` получает `edge_relay.cpp`)

**Interfaces:**
- Consumes: `EdgeRelay`, `choose_reply` (Task 11); `link::LayoutBook`, кодеки (Task 2).
- Produces:
  - `AddressService::set_edge(EdgeRelay* relay, link::LayoutBook* layout)`, `set_now(std::uint32_t now_ms)`, `push_handoff(const link::Handoff&)`, `push_route_state(const link::RouteState&)`;
  - `SpiSlave::set_edge(EdgeRelay* relay, link::LayoutBook* layout)`.
  - **Поведение:** U2 предлагает `SCREEN_EXTENSION`, только если вызван `set_edge` с обоими указателями.

- [ ] **Step 1: Тесты.** Добавить в конец `tests/firmware_native/test_address_service.cpp`:

```cpp
// ------------------------------------------------------- screen extension

namespace {

struct EdgeBoard {
    Board board;
    duo_input::u2::EdgeRelay relay;
    duo_input::link::LayoutBook layout;
    EdgeBoard() { board.service.set_edge(&relay, &layout); }

    void notify(CdcMessageType type, const std::uint8_t* payload, std::size_t size) {
        CdcFrame frame;
        frame.type = type;
        frame.sequence = 0;
        frame.payload = duo_input::protocol::ByteView{payload, size};
        std::uint8_t wire[128];
        std::uint8_t scratch[128];
        std::size_t written = 0;
        duo_input::protocol::encode_cdc_frame(
            frame, duo_input::protocol::MutableByteView{wire, sizeof(wire)},
            duo_input::protocol::MutableByteView{scratch, sizeof(scratch)}, written);
        board.sink.writes = 0;
        board.service.on_cdc_bytes(wire, written);
    }
};

const std::uint8_t kU2Contact[9] = {3, 0, 1, 1, 0, 0, 0x40, 90, 0};

}  // namespace

TEST_CASE(u2_offers_screen_extension_only_when_wired) {
    Board bare;
    CHECK((caps_of(bare.hello()) &
           static_cast<std::uint32_t>(duo_input::protocol::Capability::SCREEN_EXTENSION)) == 0u);
    EdgeBoard wired;
    CHECK((caps_of(wired.board.hello()) &
           static_cast<std::uint32_t>(duo_input::protocol::Capability::SCREEN_EXTENSION)) != 0u);
}

TEST_CASE(u2_relays_pc2s_contact_and_answer_without_replying) {
    EdgeBoard rig;
    rig.board.hello();
    rig.board.service.set_now(500);
    rig.notify(CdcMessageType::EDGE_CONTACT, kU2Contact, sizeof(kU2Contact));
    CHECK_EQ(rig.board.sink.writes, 0);
    const std::uint8_t ready[2] = {8, 0};
    rig.notify(CdcMessageType::HANDOFF_READY, ready, sizeof(ready));
    CHECK_EQ(rig.board.sink.writes, 0);

    duo_input::link::EndpointEdgeState state;
    CHECK(rig.relay.take_reply(500, state));
    CHECK(state.contact.active);
    CHECK_EQ(state.contact.gen, 3u);
    CHECK_EQ(state.last_ready_id, 8u);
}

TEST_CASE(u2_drops_edge_notifications_before_the_capability) {
    EdgeBoard rig;
    rig.notify(CdcMessageType::EDGE_CONTACT, kU2Contact, sizeof(kU2Contact));
    duo_input::link::EndpointEdgeState state;
    CHECK(rig.relay.take_reply(0, state));
    CHECK_FALSE(state.contact.active);
}

TEST_CASE(u2_exchanges_layout_blobs) {
    EdgeBoard rig;
    rig.board.hello();
    const std::uint8_t peer[2] = {4, 5};
    rig.layout.accept_peer(duo_input::protocol::ByteView{peer, 2});
    const std::uint8_t mine[3] = {1, 2, 3};
    const CdcFrame reply = rig.board.send(CdcMessageType::EXCHANGE_LAYOUT, mine, sizeof(mine));
    CHECK(reply.type == CdcMessageType::EXCHANGE_LAYOUT);
    CHECK_EQ(reply.payload.data[0], static_cast<std::uint8_t>(CdcError::Ok));
    CHECK_EQ(reply.payload.size, 3u);
    CHECK_EQ(rig.layout.local().size, 3u);
    std::uint8_t too_big[52] = {};
    const CdcFrame refused = rig.board.send(CdcMessageType::EXCHANGE_LAYOUT, too_big, 52);
    CHECK_EQ(refused.payload.data[0], static_cast<std::uint8_t>(CdcError::BadSize));
}

TEST_CASE(u2_pushes_at_sequence_zero_once_agreed) {
    EdgeBoard rig;
    duo_input::link::Handoff handoff;
    handoff.id = 2;
    handoff.side = duo_input::protocol::EdgeSide::LEFT;
    rig.board.sink.writes = 0;
    rig.board.service.push_handoff(handoff);
    CHECK_EQ(rig.board.sink.writes, 0);
    rig.board.hello();
    rig.board.sink.writes = 0;
    rig.board.service.push_handoff(handoff);
    CHECK_EQ(rig.board.sink.writes, 1);
    duo_input::protocol::decode_cdc_frame(
        duo_input::protocol::ByteView{rig.board.sink.last.data(), rig.board.sink.last.size()},
        duo_input::protocol::MutableByteView{rig.board.scratch, sizeof(rig.board.scratch)},
        rig.board.result);
    CHECK(rig.board.result.cdc.type == CdcMessageType::HANDOFF);
    CHECK_EQ(rig.board.result.cdc.sequence, 0u);
}
```

  В `tests/firmware_native/CMakeLists.txt` в цель `duo_address_service_test` добавить `${CMAKE_SOURCE_DIR}/firmware/u2_endpoint/edge_relay.cpp`.

- [ ] **Step 2: Убедиться, что не собирается.**
  Run: `cmake --build --preset native --clean-first`
  Expected: FAIL.

- [ ] **Step 3: `AddressService`.** В `address_service.hpp`:
  - includes: `#include "edge_relay.hpp"`, `#include "link/edge.hpp"`;
  - в публичную часть:

```cpp
    /// The screen extension. Both must be given for SCREEN_EXTENSION to be
    /// offered.
    void set_edge(EdgeRelay* relay, link::LayoutBook* layout) {
        relay_ = relay;
        layout_ = layout;
    }

    /// The time of the current pass, which stamps PC2's contact.
    void set_now(std::uint32_t now_ms) { now_ms_ = now_ms; }

    /// Said without being asked, at sequence 0. Nothing is sent to a host
    /// that has not agreed SCREEN_EXTENSION.
    void push_handoff(const link::Handoff& handoff);
    void push_route_state(const link::RouteState& state);
```

  - в приватную часть:

```cpp
    std::uint32_t offered() const;
    bool edge_agreed() const;
    void handle_edge_notification(const protocol::CdcFrame& frame);

    EdgeRelay* relay_ = nullptr;
    link::LayoutBook* layout_ = nullptr;
    std::uint32_t now_ms_ = 0;
```

  В `address_service.cpp`:
  - убрать `constexpr std::uint32_t kCapabilities = …;` из анонимного namespace и добавить:

```cpp
constexpr std::uint32_t kAddressExchange =
    static_cast<std::uint32_t>(protocol::Capability::ADDRESS_EXCHANGE);
constexpr std::uint32_t kScreenExtension =
    static_cast<std::uint32_t>(protocol::Capability::SCREEN_EXTENSION);
```

  - в HELLO `granted = requested & kCapabilities;` → `granted = requested & offered();`;
  - в `EXCHANGE_ADDRESSES` обе проверки `kCapabilities` заменить на `kAddressExchange`;
  - в начале `handle_frame` сразу после `const protocol::CdcFrame& frame = result.cdc;`:

```cpp
    // As on U1: the two edge notifications are outside the request sequence,
    // never answered, and dropped by a board that did not agree to them.
    if (frame.type == CdcMessageType::EDGE_CONTACT ||
        frame.type == CdcMessageType::HANDOFF_READY) {
        handle_edge_notification(frame);
        return;
    }
```

  - в `switch` перед `default:`:

```cpp
        case CdcMessageType::EXCHANGE_LAYOUT: {
            if (!negotiated_) {
                payload_[0] = static_cast<std::uint8_t>(CdcError::BadState);
                reply(frame.type, frame.sequence, payload_, 1);
                return;
            }
            if (!edge_agreed() || layout_ == nullptr) {
                payload_[0] = static_cast<std::uint8_t>(CdcError::UnsupportedCapability);
                reply(frame.type, frame.sequence, payload_, 1);
                return;
            }
            if (frame.payload.size > link::kLayoutBlobMaxSize) {
                payload_[0] = static_cast<std::uint8_t>(CdcError::BadSize);
                reply(frame.type, frame.sequence, payload_, 1);
                return;
            }
            if (frame.payload.size != 0) {
                layout_->set_local(frame.payload);
            }
            const link::LayoutBlob& peer = layout_->peer();
            payload_[0] = static_cast<std::uint8_t>(CdcError::Ok);
            std::memcpy(payload_ + 1, peer.bytes, peer.size);
            reply(frame.type, frame.sequence, payload_, 1 + peer.size);
            return;
        }
```

  - новые функции:

```cpp
std::uint32_t AddressService::offered() const {
    return kAddressExchange |
           ((relay_ != nullptr && layout_ != nullptr) ? kScreenExtension : 0u);
}

bool AddressService::edge_agreed() const {
    return negotiated_ && (capabilities_ & kScreenExtension) != 0;
}

void AddressService::handle_edge_notification(const protocol::CdcFrame& frame) {
    if (!edge_agreed() || relay_ == nullptr) {
        return;
    }
    if (frame.type == CdcMessageType::EDGE_CONTACT) {
        link::EdgeContact contact;
        if (link::decode_edge_contact(frame.payload, contact)) {
            relay_->set_contact(contact, now_ms_);
        }
        return;
    }
    std::uint16_t id = 0;
    if (link::decode_handoff_ready(frame.payload, id)) {
        relay_->set_ready(id);
    }
}

void AddressService::push_handoff(const link::Handoff& handoff) {
    if (!edge_agreed()) return;
    std::uint8_t payload[link::kHandoffSize];
    std::size_t written = 0;
    if (link::encode_handoff(handoff, protocol::MutableByteView{payload, sizeof(payload)},
                             written)) {
        reply(CdcMessageType::HANDOFF, 0, payload, written);
    }
}

void AddressService::push_route_state(const link::RouteState& state) {
    if (!edge_agreed()) return;
    std::uint8_t payload[link::kRouteStateSize];
    std::size_t written = 0;
    if (link::encode_route_state(state, protocol::MutableByteView{payload, sizeof(payload)},
                                 written)) {
        reply(CdcMessageType::ROUTE_STATE, 0, payload, written);
    }
}
```

  `reply()` у U2 не кеширует ответы, поэтому для push её можно использовать.

- [ ] **Step 4: `SpiSlave`.** В `spi_slave.hpp`:
  - includes: `#include "edge_relay.hpp"`, `#include "link/edge.hpp"`;
  - в публичную часть после `set_address_book`:

```cpp
    /// The screen edge and the layout blobs. Either may be null.
    void set_edge(EdgeRelay* relay, link::LayoutBook* layout) {
        relay_ = relay;
        layout_ = layout;
    }
```

  - в приватную часть: `void prime_frame(protocol::SpiMessageType type, protocol::ByteView payload);`, `EdgeRelay* relay_ = nullptr;`, `link::LayoutBook* layout_ = nullptr;`, `std::uint32_t now_ms_ = 0;`.

  В `spi_slave.cpp`:
  - добавить `#include "reply_choice.hpp"`;
  - в начале `take_valid_frame`: `now_ms_ = now_ms;`;
  - заменить блок `#if !DUO_SPI_DEBUG … #endif` в начале `prime()` на:

```cpp
#if !DUO_SPI_DEBUG
    // Kept out of DUO_SPI_DEBUG builds: that mode's whole point is a counting
    // pattern the far end can check byte for byte, and returning early here
    // would skip it silently. The order is choose_reply's, tested there.
    link::EndpointEdgeState edge;
    switch (choose_reply(relay_, addresses_, layout_, now_ms_, edge)) {
        case ReplyKind::EdgeState: {
            std::uint8_t payload[link::kEndpointEdgeStateSize];
            std::size_t size = 0;
            link::encode_endpoint_edge_state(
                edge, protocol::MutableByteView{payload, sizeof(payload)}, size);
            prime_frame(protocol::SpiMessageType::ENDPOINT_EDGE_STATE,
                        protocol::ByteView{payload, size});
            return;
        }
        case ReplyKind::Addresses: {
            std::uint8_t list[link::kHostAddressesMaxSize];
            std::size_t size = 0;
            link::encode_host_addresses(addresses_->local(),
                                        protocol::MutableByteView{list, sizeof(list)}, size);
            prime_frame(protocol::SpiMessageType::ENDPOINT_ADDRESSES,
                        protocol::ByteView{list, size});
            return;
        }
        case ReplyKind::Layout: {
            const link::LayoutBlob& blob = layout_->local();
            prime_frame(protocol::SpiMessageType::ENDPOINT_LAYOUT_STATE,
                        protocol::ByteView{blob.bytes, blob.size});
            return;
        }
        case ReplyKind::Status:
            break;
    }
#endif
```

  - добавить функцию:

```cpp
void SpiSlave::prime_frame(protocol::SpiMessageType type, protocol::ByteView payload) {
    protocol::SpiFrame frame;
    frame.type = type;
    frame.sequence = reply_sequence_++;
    frame.payload = payload;
    std::size_t written = 0;
    std::memset(tx_, 0, sizeof(tx_));
    protocol::encode_spi_frame(frame, protocol::MutableByteView{tx_, sizeof(tx_)}, written);
}
```

- [ ] **Step 5: Цикл U2.** В `firmware/u2_endpoint/main.cpp`:
  - includes: `#include "edge_relay.hpp"`, `#include "link/edge.hpp"`;
  - сигнатура `apply(...)` получает ещё `duo_input::u2::EdgeRelay& relay, duo_input::link::LayoutBook& layout`, а в `switch` добавляются ветки:

```cpp
        case duo_input::protocol::SpiMessageType::LINK_EDGE_STATE: {
            link::LinkEdgeState state;
            if (link::decode_link_edge_state(payload, state)) {
                relay.accept_link_state(state);
            }
            return;
        }
        case duo_input::protocol::SpiMessageType::LAYOUT_STATE:
            layout.accept_peer(payload);
            return;
```

  - после `static duo_input::u2::AddressService address_service(...)` и `link.set_address_book(&addresses);`:

```cpp
    static duo_input::u2::EdgeRelay relay;
    static duo_input::link::LayoutBook layout;
    address_service.set_edge(&relay, &layout);
    link.set_edge(&relay, &layout);
```

  - в начале тела `while (true)` вычислять `now_ms` (перенести строку `const std::uint32_t now_ms = to_ms_since_boot(get_absolute_time());` с её нынешнего места в начало цикла) и сразу вызывать `address_service.set_now(now_ms);`;
  - вызов `apply(frame, outputs, addresses);` заменить на `apply(frame, outputs, addresses, relay, layout);`;
  - после `link.set_status(status);`:

```cpp
        relay.set_usb_ready(mounted && !usb.suspended());
```

  - после цикла `while (link.take_valid_frame(...))`:

```cpp
        // What U1 said about the routes, passed on to PC2's program.
        duo_input::link::Handoff handoff;
        if (relay.take_handoff(handoff)) {
            address_service.push_handoff(handoff);
        }
        duo_input::link::RouteState route;
        if (relay.take_route_state(now_ms, route)) {
            address_service.push_route_state(route);
        }
```

  - в `firmware/u2_endpoint/CMakeLists.txt` добавить `edge_relay.cpp` и `reply_choice.cpp` в `add_executable(duo_u2_endpoint …)`.

- [ ] **Step 6: Прогнать нативные тесты и собрать U2.**
  Run:

  ```
  cmake --build --preset native --clean-first
  ctest --preset native --output-on-failure
  cmake --build --preset pico-release --clean-first
  .venv/Scripts/python.exe -m pytest tests/build -q; echo "exit=$?"
  ```

  Expected: всё зелёное, `exit=0`. Тест дескрипторов U2 должен остаться зелёным: HID-часть не менялась.

- [ ] **Step 7: Commit.**

```bash
git add firmware/u2_endpoint tests/firmware_native/test_address_service.cpp tests/firmware_native/CMakeLists.txt
git commit -m "feat(u2): relay the screen edge over CDC and SPI"
```

---

### Task 13: Стенд `tools/edge_bench.py`

**Files:**
- Create: `tools/edge_bench.py`, `tools/test_edge_bench.py`

**Interfaces:**
- Consumes: `duo_input.device.edge_messages` (Task 3); `duo_input.device.discovery.find_u1_ports`/`find_u2_ports`; `duo_input.device.transactions.FrameAssembler`; `duo_input.protocol.frame.CdcFrame`, `encode_cdc_frame`; `PySide6.QtSerialPort.QSerialPort`.
- Produces: скрипт стенда и чистую функцию `contacts_for(mouse_on_pc2: bool, side: int, gens: tuple[int, int], threshold: int, t: int) -> tuple[EdgeContact, EdgeContact]` (касание ПК1, касание ПК2).

Стенд нужен для приёмки прошивки без агента. Обе платы подключены к одному ноутбуку (так стоит стенд: память «Прошивать платы самому»), мышь — в U1. Скрипт изображает агентов обеих машин: «курсор всегда упёрт в край» на той машине, где сейчас мышь.

- [ ] **Step 1: Тест чистой функции.** `tools/test_edge_bench.py`:

```python
from edge_bench import contacts_for

from duo_input.generated.protocol import EdgeSide


def test_only_the_computer_with_the_mouse_presses_and_the_other_faces_back():
    pc1, pc2 = contacts_for(False, EdgeSide.RIGHT, (3, 7), 120, 0x8000)
    assert pc1.active and pc1.side == EdgeSide.RIGHT and pc1.gen == 3
    assert pc1.threshold == 120 and pc1.t == 0x8000
    assert not pc2.active and pc2.gen == 7

    pc1, pc2 = contacts_for(True, EdgeSide.RIGHT, (3, 7), 120, 0x8000)
    assert not pc1.active
    assert pc2.active and pc2.side == EdgeSide.LEFT and pc2.gen == 7
```

  Тесты в `tools/` запускаются из каталога `tools` (`tools/test_fp_*.py` устроены так же). Если `conftest`/`pytest.ini` не добавляет `tools` и `configurator/src` в путь, запускать с `PYTHONPATH=tools;configurator/src`.

- [ ] **Step 2: Убедиться, что падает.**
  Run: `$env:PYTHONPATH="tools;configurator/src"; .venv/Scripts/python.exe -m pytest tools/test_edge_bench.py -q`
  Expected: FAIL, `ModuleNotFoundError: edge_bench`.

- [ ] **Step 3: Скрипт.** `tools/edge_bench.py`:

```python
"""Screen-extension bench: both boards on one computer, no agent.

Opens U1's and U2's CDC ports, agrees SCREEN_EXTENSION with both, and plays
the agent of both computers at once: the pointer is always pressed against the
edge on whichever computer the mouse is on. Push the mouse plugged into U1
towards that edge and U1 should move it to the other side; every ROUTE_STATE
and HANDOFF is printed with a timestamp, and each HANDOFF is answered with
HANDOFF_READY unless --no-ready. GET_EDGE_STATS is printed at the end.

    .venv/Scripts/python.exe tools/edge_bench.py --side right --seconds 60
    .venv/Scripts/python.exe tools/edge_bench.py --no-ready
    .venv/Scripts/python.exe tools/edge_bench.py --contact-seconds 5

Run with PYTHONPATH=configurator/src.
"""

from __future__ import annotations

import argparse
import struct
import sys
import time

from PySide6.QtCore import QCoreApplication
from PySide6.QtSerialPort import QSerialPort

from duo_input.device.discovery import find_u1_ports, find_u2_ports
from duo_input.device.edge_messages import (
    EdgeContact,
    decode_edge_stats,
    decode_handoff,
    decode_route_state,
    encode_edge_contact,
    encode_handoff_ready,
)
from duo_input.device.transactions import FrameAssembler
from duo_input.generated.protocol import Capability, CdcMessageType, EdgeSide, MouseRoute
from duo_input.protocol.frame import CdcFrame, encode_cdc_frame

_OPPOSITE = {
    EdgeSide.LEFT: EdgeSide.RIGHT,
    EdgeSide.RIGHT: EdgeSide.LEFT,
    EdgeSide.TOP: EdgeSide.BOTTOM,
    EdgeSide.BOTTOM: EdgeSide.TOP,
}


def contacts_for(
    mouse_on_pc2: bool, side: int, gens: tuple[int, int], threshold: int, t: int
) -> tuple[EdgeContact, EdgeContact]:
    """PC1's and PC2's contacts: only the computer with the mouse presses."""
    pc1 = EdgeContact(gens[0], not mouse_on_pc2, int(side), 0, t, threshold)
    pc2 = EdgeContact(gens[1], mouse_on_pc2, int(_OPPOSITE[EdgeSide(side)]), 0, t, threshold)
    return pc1, pc2


class Board:
    def __init__(self, name: str, port_name: str) -> None:
        self.name = name
        self.port = QSerialPort(port_name)
        self.port.setBaudRate(115200)
        if not self.port.open(QSerialPort.OpenModeFlag.ReadWrite):
            sys.exit(f"{name}: cannot open {port_name}: {self.port.errorString()}")
        self.assembler = FrameAssembler()
        self.sequence = 0

    def write(self, frame_type: CdcMessageType, payload: bytes, sequence: int) -> None:
        self.port.write(encode_cdc_frame(CdcFrame(frame_type, sequence, payload)))
        self.port.flush()

    def request(self, frame_type: CdcMessageType, payload: bytes = b"") -> None:
        self.write(frame_type, payload, self.sequence)
        self.sequence = (self.sequence + 1) & 0xFFFF

    def notify(self, frame_type: CdcMessageType, payload: bytes) -> None:
        self.write(frame_type, payload, 0)

    def frames(self, wait_ms: int) -> list[CdcFrame]:
        if self.port.waitForReadyRead(wait_ms):
            return list(self.assembler.push(bytes(self.port.readAll())).frames)
        return []


def stamp(start: float) -> str:
    return f"{(time.monotonic() - start) * 1000:9.1f} ms"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--side", choices=["left", "right", "top", "bottom"], default="right",
                        help="the edge of PC1 that leads to PC2")
    parser.add_argument("--threshold", type=int, default=120)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--contact-seconds", type=float, default=None,
                        help="stop refreshing contacts after this long (TTL check)")
    parser.add_argument("--no-ready", action="store_true", help="never answer HANDOFF")
    args = parser.parse_args(argv)

    QCoreApplication(sys.argv[:1])
    u1_ports, u2_ports = find_u1_ports(), find_u2_ports()
    if not u1_ports or not u2_ports:
        sys.exit(f"need both boards: U1 {len(u1_ports)} port(s), U2 {len(u2_ports)} port(s)")
    u1 = Board("U1", u1_ports[0].port_name)
    u2 = Board("U2", u2_ports[0].port_name)

    hello = struct.pack("<I", int(Capability.SCREEN_EXTENSION))
    for board in (u1, u2):
        board.request(CdcMessageType.HELLO, hello)
        granted = None
        deadline = time.monotonic() + 2
        while granted is None and time.monotonic() < deadline:
            for frame in board.frames(50):
                if frame.type is CdcMessageType.DEVICE_INFO:
                    granted = struct.unpack_from("<I", frame.payload, 3)[0]
        if granted is None or not granted & int(Capability.SCREEN_EXTENSION):
            sys.exit(f"{board.name} did not grant SCREEN_EXTENSION (granted={granted})")
        print(f"{board.name}: SCREEN_EXTENSION granted")

    side = EdgeSide[args.side.upper()]
    start = time.monotonic()
    mouse_on_pc2 = False
    gens = [1, 1]
    last_contact = 0.0
    pending_route = None

    while time.monotonic() - start < args.seconds:
        now = time.monotonic()
        refreshing = args.contact_seconds is None or now - start < args.contact_seconds
        if refreshing and now - last_contact >= 0.1:
            pc1, pc2 = contacts_for(mouse_on_pc2, side, (gens[0], gens[1]), args.threshold, 0x8000)
            u1.notify(CdcMessageType.EDGE_CONTACT, encode_edge_contact(pc1))
            u2.notify(CdcMessageType.EDGE_CONTACT, encode_edge_contact(pc2))
            last_contact = now

        for board in (u1, u2):
            for frame in board.frames(5):
                if frame.type is CdcMessageType.HANDOFF:
                    handoff = decode_handoff(bytes(frame.payload))
                    print(f"{stamp(start)}  {board.name} HANDOFF id={handoff.id} side={EdgeSide(handoff.side).name} t={handoff.t}")
                    if not args.no_ready:
                        board.notify(CdcMessageType.HANDOFF_READY, encode_handoff_ready(handoff.id))
                elif frame.type is CdcMessageType.ROUTE_STATE and board is u1:
                    route = decode_route_state(bytes(frame.payload))
                    if route != pending_route:
                        print(f"{stamp(start)}  ROUTE mouse={route.mouse} keyboard={route.keyboard} reason={route.reason}")
                        pending_route = route
                    now_on_pc2 = route.mouse == MouseRoute.PC2
                    if now_on_pc2 != mouse_on_pc2:
                        # The pointer was placed away from the edge and came back:
                        # a new episode on both sides, as a real agent would report.
                        mouse_on_pc2 = now_on_pc2
                        gens = [gens[0] + 1, gens[1] + 1]
                        last_contact = 0.0

    u1.request(CdcMessageType.GET_EDGE_STATS)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        for frame in u1.frames(50):
            if frame.type is CdcMessageType.GET_EDGE_STATS:
                print(f"error={frame.payload[0]} {decode_edge_stats(bytes(frame.payload[1:]))}")
                return 0
    print("no GET_EDGE_STATS reply")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Прогнать тест.**
  Run: `$env:PYTHONPATH="tools;configurator/src"; .venv/Scripts/python.exe -m pytest tools/test_edge_bench.py -q; echo "exit=$?"`
  Expected: `exit=0`.

- [ ] **Step 5: Commit.**

```bash
git add tools/edge_bench.py tools/test_edge_bench.py
git commit -m "test(tools): screen-extension bench that plays both agents"
```

---

### Task 14: Мутации, полные наборы, железо

**Files:** изменений кода нет. Итог записывается в `docs/superpowers/records/2026-10-04-screen-extension-firmware.md`.

- [ ] **Step 1: Проверить защиты мутациями.** Каждую строку таблицы удалить или обратить, пересобрать с `--clean-first`, прогнать указанный тест и вернуть строку (`git checkout -- <file>`). Если тест не упал, дописать тест и повторить.

| Мутация | Где | Должен упасть |
| --- | --- | --- |
| убрать проверку `threshold == 0` | `edge.cpp` `read_contact` | `an_active_contact_with_no_threshold_is_refused` |
| убрать проверку `in[2] > 1` | `edge.cpp` `read_contact` | `an_active_byte_other_than_zero_or_one_is_refused` |
| убрать `!slot.contact.active` | `edge_arbiter.cpp` | `an_inactive_contact_does_not_cross` |
| убрать проверку TTL | `edge_arbiter.cpp` | `a_contact_nobody_refreshed_does_not_cross` |
| убрать проверку `spent_gen` | `edge_arbiter.cpp` | `a_contact_spent_by_a_route_change_does_not_cross_again` |
| убрать `if (push < 0) { pressure_ = 0; …}` | `edge_arbiter.cpp` | `moving_inward_starts_the_push_over` |
| убрать сброс по `kEdgePressureGapMs` | `edge_arbiter.cpp` | `a_pause_longer_than_the_gap_starts_the_push_over` |
| убрать `if (button_held)` | `edge_arbiter.cpp` | `a_held_button_refuses_and_is_counted` |
| убрать `if (!alive_[…])` | `edge_arbiter.cpp` | `a_computer_that_cannot_take_the_pointer_refuses_and_is_counted` |
| убрать проверку кулдауна | `edge_arbiter.cpp` | `the_cooldown_after_any_route_change_refuses_and_is_counted` |
| `closed_ = !skipping` → `closed_ = true` | `handoff_gate.cpp` | `a_computer_that_never_answers_stops_being_waited_for` |
| убрать `gate_.tick(now_ms)` | `output_runtime.cpp` `drain` | `the_gate_opens_by_itself_and_the_held_movement_goes_out` |
| в `take_snapshot` убрать ветку `gate_.closed` | `output_runtime.cpp` | `movement_for_the_arriving_computer_waits_for_its_handoff_and_is_not_lost` |
| в `snapshot` убрать обнуление мыши | `output_runtime.cpp` | `buttons_are_held_back_with_the_movement` |
| `cross_edge` вызвать после `apply(engine_.handle(event))` | `core1_runtime.cpp` | `a_push_through_the_edge_moves_the_mouse_and_opens_a_handoff` |
| убрать `edge_.route_changed(now_ms)` в `publish_routes` | `core1_runtime.cpp` | `a_hotkey_starts_the_cooldown_for_the_edge` |
| убрать ранний `return` «маршрут не изменился» в `publish_routes` | `core1_runtime.cpp` | `a_boot_that_leaves_the_routes_where_core0_starts_says_nothing` |
| убрать `take_edge_updates()` из `tick` | `core1_runtime.cpp` | `a_contact_reaches_core1_only_at_its_tick` |
| убрать `endpoint_usb_ready_` из живости | `edge_service.cpp` | `pc2_can_take_the_pointer_only_while_u2_speaks_the_edge_and_its_usb_is_up` |
| убрать проверку `pushed_handoff_id_` | `edge_service.cpp` | `pc1_is_told_where_to_put_its_pointer_once_per_handoff` |
| убрать ранний возврат уведомлений в `handle_frame` | `config_service.cpp` | `notifications_between_requests_leave_the_sequence_alone` |
| `push_event` писать через `reply()` | `config_service.cpp` | `a_notification_does_not_replace_the_cached_reply_a_retry_gets` или `a_push_goes_out_at_sequence_zero_only_once_agreed` |
| `offered_capabilities` → `device_capabilities()` | `config_service.cpp` | `screen_extension_is_offered_only_where_it_is_wired` |
| убрать ветку `LINK_EDGE_STATE` в начале `poll_snapshot` | `spi_master_poll.cpp` | `a_changed_edge_state_goes_out_ahead_of_movement` |
| убрать `ENDPOINT_EDGE_STATE` из `is_endpoint_reply` | `spi_protocol.cpp` | `only_u2s_edge_frames_are_endpoint_replies` |
| убрать TTL в `EdgeRelay::current` | `edge_relay.cpp` | `a_contact_its_agent_stopped_refreshing_is_reported_released` |
| убрать `!last_was_edge_` из `refresh` | `edge_relay.cpp` | `an_unchanged_state_repeats_but_never_twice_in_a_row` |
| убрать проверку `route.mouse != PC2` | `edge_relay.cpp` `take_handoff` | `pc2_is_not_told_about_a_handoff_to_pc1_or_one_already_over` |
| убрать `edge_agreed()` в U2 `handle_edge_notification` | `address_service.cpp` | `u2_drops_edge_notifications_before_the_capability` |

- [ ] **Step 2: Полные наборы.**
  Run:

  ```
  .venv/Scripts/python.exe -m pytest configurator/tests tests -q; echo "exit=$?"
  $env:PYTHONPATH="tools;configurator/src"; .venv/Scripts/python.exe -m pytest tools -q; echo "exit=$?"
  cmake --build --preset native --clean-first
  ctest --preset native
  cmake --build --preset pico-pio-usb-release --clean-first
  cmake --build --preset pico-pio-usb-reference-release --clean-first
  cmake --build --preset pico-release --clean-first
  .venv/Scripts/python.exe tools/generate_protocol.py --check
  ```

  Expected: оба `exit=0`, всё зелёное. Записать итоговые числа.

- [ ] **Step 3: Прошить обе платы.** Прошивает агент сам (память «Прошивать платы самому»). Перед прошивкой проверить VID/PID (память «Опознавай платы по VID/PID»).
  - U1 — образом `build/pico-pio-usb-release/firmware/u1_main/*.uf2` (память «U1 — это PIO USB»), сброс на 1200 бод (память «Flashing without BOOTSEL»).
  - U2 — образом `build/pico-release/firmware/u2_endpoint/*.uf2`, тоже сбросом на 1200 бод: у U2 уже есть CDC.
  - После прошивки — цикл питания обеих плат (память «После прошивки U1 нужен цикл питания»). Проверить `ready=True` в диагностике U1.

- [ ] **Step 4: Стенд (выполняет человек, агент готовит команды и читает вывод).** Мышь подключена к U1, обе платы — к одному ноутбуку. Для каждого пункта записать вывод `edge_bench.py`.
  1. `tools/edge_bench.py --side right --seconds 60`: толкнуть мышь вправо. Ожидается `ROUTE mouse=2 reason=2`, `U2 HANDOFF id=… side=LEFT`. Затем толкнуть влево: `U1 HANDOFF … side=RIGHT`, `ROUTE mouse=1`. Повторить 50 раз. По `GET_EDGE_STATS`: `crossings ≈ 100`, `handoffs_ready = crossings`, `handoff_timeouts = 0`, `max_ready_ms` записать.
  2. Двигать мышь вдоль края и мелко «упираться» без толчка 1 минуту — переходов 0.
  3. Зажать левую кнопку и толкнуть — перехода нет, `refused_button` растёт.
  4. `--no-ready`: переходы происходят, первые три дают `handoff_timeouts`, дальше растёт `handoffs_skipped`, и мышь на новой стороне двигается сразу, без паузы.
  5. `--contact-seconds 5`: через 5 с толчки больше не переводят мышь (TTL касания).
  6. Хоткей переключения мыши во время толчка — ровно одно переключение (`reason=1`), сразу после него толчок 250 мс не срабатывает.
  7. Выдернуть USB U2 из ноутбука (U2 без питания от ПК2) — толчок с ПК1 не переводит мышь, `refused_target_dead` растёт.
  8. С включённым в конфигураторе «Переключать клавиатуру и мышь вместе» `ROUTE keyboard` следует за `mouse`; с выключенным — остаётся 1.

- [ ] **Step 5: Записать итог и закоммитить.** В `docs/superpowers/records/2026-10-04-screen-extension-firmware.md`: числа наборов, таблицу мутаций с отметкой «упал/дописан тест», вывод стенда по каждому пункту, `max_ready_ms` и среднее (`total_ready_ms / handoffs_ready`), найденные расхождения.

```bash
git add docs/superpowers/records/2026-10-04-screen-extension-firmware.md
git commit -m "docs(screen-extension): firmware verification record"
```
