# Обмен IP-адресами через платы U1↔U2 — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** приложение на каждом ПК узнаёт IPv4-адреса второго ПК через SPI-провод U1↔U2 и подставляет их в строку «Адрес второго компьютера». Строка становится редактируемым списком; ручной ввод главнее автоопределения и сохраняется между запусками.

**Architecture:**
- Одна CDC-команда `EXCHANGE_ADDRESSES`: запрос несёт свои адреса, ответ — адреса соседа.
- Два SPI-типа: `HOST_ADDRESSES` (U1→U2) и `ENDPOINT_ADDRESSES` (U2→U1). Оба несут общий формат `HostAddresses`.
- На U1 команду обслуживает существующий `ConfigService`. На U2 — новый минимальный `AddressService` за новым CDC-интерфейсом.
- В приложении действуют два бэкенда. На ПК1 это тихая операция `DeviceService`, на ПК2 — новый `EndpointService`. Их объединяет `AddressExchange`, чей результат попадает в `ClipboardCoordinator` (перебор кандидатов) и в `ClipboardPage` (`QComboBox`).

**Tech Stack:** C++17 (Pico SDK, TinyUSB), CMake/Ninja, собственный нативный тест-раннер (`tests/firmware_native`), Python 3.12, PySide6, pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-09-25-peer-address-exchange-design.md`. Раздел «Уточнения после чтения кода» в конце спецификации имеет приоритет над её основным текстом.

## Global Constraints

- **Только IPv4.** В списке не больше 8 адресов. Wire-формат `HostAddresses`: `u8 count` (0..8) + `count × 4` октета в сетевом порядке. Длина строго `1 + 4·count`, адрес `0.0.0.0` недопустим.
- **Новые идентификаторы** (менять только в `protocol/schema.json`, остальное генерируется):
  - `capabilities.ADDRESS_EXCHANGE = 8192`;
  - `cdc_messages.EXCHANGE_ADDRESSES = 24`;
  - `spi_messages.HOST_ADDRESSES = 8`;
  - `spi_messages.ENDPOINT_ADDRESSES = 9`.
- **`PROTOCOL_VERSION_MINOR` не меняется.** Поддержку обмена определяет возможность `ADDRESS_EXCHANGE`.
- **Периоды:**
  - U1 повторяет `HOST_ADDRESSES` каждые 250 мс (`kHostAddressesIntervalMs`);
  - U2 отвечает `ENDPOINT_ADDRESSES` раз в 64 ответа (`kEndpointAddressesEvery`);
  - приложение обменивается адресами каждые 5000 мс (`EXCHANGE_INTERVAL_MS`).
- **Ключ `QSettings`:** `clipboard/manual_address`. Пустая строка означает режим «Авто».
- **Стек ядра 0 на U1 — 2 КБ.** Новые локальные буферы в функциях, которые вызывает `main`, должны быть не больше 64 байт.
- **Python-тесты** запускаются только через `.venv/Scripts/python.exe -m pytest`. Голый `python` не имеет pytest и завершается с кодом 0 даже при падении.
- **Нативная прошивка** собирается только с `--clean-first`: Ninja не отслеживает заголовки.
- **Комментарии** следуют языку окружающего кода: прошивка и `device/` — по-английски, `clipboard/` и `ui/` — по-русски.
- **Проверка мутациями.** Каждая защита, добавленная задачей, проверяется в Task 15: её удаление должно ронять хотя бы один тест.

## Карта файлов

| Файл | Что делает |
| --- | --- |
| `protocol/schema.json` | новые идентификаторы (Task 1) |
| `firmware/common/protocol/frame.cpp` | новые типы в `is_known` (Task 1) |
| `firmware/common/link/host_addresses.{hpp,cpp}` (новый) | кодек `HostAddresses` и `AddressBook` (Task 2) |
| `firmware/u1_main/config_service.{hpp,cpp}` | обработчик `EXCHANGE_ADDRESSES` (Task 3) |
| `firmware/u1_main/spi_master{.hpp,.cpp,_poll.cpp}`, `firmware/common/link/spi_protocol.cpp`, `firmware/u1_main/main.cpp` | SPI-обмен на U1 (Task 4) |
| `firmware/u2_endpoint/address_service.{hpp,cpp}` (новый) | CDC-сервис U2 (Task 5) |
| `firmware/u2_endpoint/{spi_slave.*,main.cpp,usb_descriptors.cpp,tusb_config.h,usb_service.cpp,CMakeLists.txt}`, `firmware/common/hid/report_ids.hpp` | CDC на U2 и SPI-обмен (Task 6) |
| `configurator/src/duo_input/device/host_addresses.py` (новый) | Python-кодек (Task 7) |
| `configurator/src/duo_input/device/emulator.py` | обработчик в эмуляторе (Task 7) |
| `configurator/src/duo_input/device/service.py` | тихая операция `exchange_addresses` (Task 8) |
| `configurator/src/duo_input/device/discovery.py`, `.../device/endpoint_service.py` (новый) | ПК2 ↔ U2 (Task 9) |
| `configurator/src/duo_input/clipboard/local_addresses.py` (новый) | сбор и фильтр своих адресов (Task 10) |
| `configurator/src/duo_input/clipboard/address_exchange.py` (новый) | периодический обмен (Task 11) |
| `configurator/src/duo_input/clipboard/coordinator.py` | перебор кандидатов (Task 12) |
| `configurator/src/duo_input/ui/clipboard_page.py` | `QComboBox` (Task 13) |
| `configurator/src/duo_input/app.py` | проводка и сохранение (Task 14) |
| `docs/protocol/compatibility.md` | описание команды (Task 1) |

---

### Task 1: Идентификаторы протокола

**Files:**
- Modify: `protocol/schema.json`
- Regenerate: `firmware/common/protocol/generated.hpp`, `configurator/src/duo_input/generated/protocol.py`
- Modify: `firmware/common/protocol/frame.cpp` (`is_known` для CDC и SPI)
- Modify: `docs/protocol/compatibility.md`
- Test: `configurator/tests/test_generated_protocol.py`, `tests/firmware_native/test_frames.cpp`

**Interfaces:**
- Produces:
  - `protocol::CdcMessageType::EXCHANGE_ADDRESSES`;
  - `protocol::SpiMessageType::HOST_ADDRESSES`;
  - `protocol::SpiMessageType::ENDPOINT_ADDRESSES`;
  - `protocol::Capability::ADDRESS_EXCHANGE`;
  - Python: `CdcMessageType.EXCHANGE_ADDRESSES`, `SpiMessageType.HOST_ADDRESSES`, `SpiMessageType.ENDPOINT_ADDRESSES`, `Capability.ADDRESS_EXCHANGE`.

- [ ] **Step 1: Написать падающий Python-тест.** Добавить в `configurator/tests/test_generated_protocol.py`:

```python
def test_the_address_exchange_identifiers_are_generated():
    from duo_input.generated import protocol

    assert protocol.CdcMessageType.EXCHANGE_ADDRESSES == 24
    assert protocol.SpiMessageType.HOST_ADDRESSES == 8
    assert protocol.SpiMessageType.ENDPOINT_ADDRESSES == 9
    assert protocol.Capability.ADDRESS_EXCHANGE == 8192
```

- [ ] **Step 2: Написать падающий нативный тест.** Добавить в `tests/firmware_native/test_frames.cpp`, рядом с существующими тестами SPI-кадров, не меняя их:

```cpp
TEST_CASE(address_frames_are_known_in_both_directions) {
    using duo_input::protocol::SpiFrame;
    using duo_input::protocol::SpiMessageType;
    for (SpiMessageType type :
         {SpiMessageType::HOST_ADDRESSES, SpiMessageType::ENDPOINT_ADDRESSES}) {
        const std::uint8_t payload[5] = {1, 192, 168, 1, 7};
        SpiFrame frame;
        frame.type = type;
        frame.payload = duo_input::protocol::ByteView{payload, sizeof(payload)};
        std::uint8_t wire[64] = {};
        std::size_t written = 0;
        CHECK(duo_input::protocol::encode_spi_frame(
            frame, duo_input::protocol::MutableByteView{wire, sizeof(wire)}, written));
        duo_input::protocol::DecodeResult result;
        CHECK(duo_input::protocol::decode_spi_frame(
            duo_input::protocol::ByteView{wire, sizeof(wire)}, result));
        CHECK(result.spi.type == type);
    }
}
```

- [ ] **Step 3: Убедиться, что оба теста падают.**

  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/test_generated_protocol.py -q`
  Expected: FAIL, `AttributeError: EXCHANGE_ADDRESSES`.

  Нативный тест не компилируется, пока нет идентификаторов, и это тоже считается падением.

- [ ] **Step 4: Добавить идентификаторы в `protocol/schema.json`.**
  - В `capabilities`: `"ADDRESS_EXCHANGE": 8192,` (в алфавитном порядке, первым).
  - В `cdc_messages`: `"EXCHANGE_ADDRESSES": 24,` (после `DEVICE_INFO`).
  - В `spi_messages`: `"ENDPOINT_ADDRESSES": 9,` и `"HOST_ADDRESSES": 8,`.

- [ ] **Step 5: Перегенерировать.**

  Run: `.venv/Scripts/python.exe tools/generate_protocol.py`, затем `.venv/Scripts/python.exe tools/generate_protocol.py --check`.
  Expected: второй запуск завершается с кодом 0.

- [ ] **Step 6: Добавить типы в `firmware/common/protocol/frame.cpp`.**
  - В `is_known(CdcMessageType)` добавить `case CdcMessageType::EXCHANGE_ADDRESSES:` в список, возвращающий `true`.
  - В `is_known(SpiMessageType)` добавить `case SpiMessageType::ENDPOINT_ADDRESSES:` и `case SpiMessageType::HOST_ADDRESSES:`.

- [ ] **Step 7: Не дать эмулятору упасть на новом типе.** В `configurator/src/duo_input/device/emulator.py` `_dispatch` ищет `_handle_exchange_addresses` через `getattr`. Настоящий обработчик появится в Task 7, а пока добавить в `_REQUIRED_CAPABILITY`:

```python
    CdcMessageType.EXCHANGE_ADDRESSES: Capability.ADDRESS_EXCHANGE,
```

  И временную заглушку в класс:

```python
    def _handle_exchange_addresses(self, payload: bytes) -> bytes:
        return bytes((ErrorCode.INVALID_REQUEST,))
```

- [ ] **Step 8: Описать команду в `docs/protocol/compatibility.md`.** Добавить раздел в конец файла:

```markdown
## EXCHANGE_ADDRESSES (0x18), capability ADDRESS_EXCHANGE (0x2000)

Запрос: `HostAddresses` хоста. Ответ: `[error u8][HostAddresses соседа]`.

`HostAddresses` = `count u8` (0..8), затем `count` IPv4-адресов по 4 байта в
сетевом порядке; длина строго `1 + 4·count`; `0.0.0.0` недопустим. Неверный
список → `INVALID_REQUEST`, сохранённый список не меняется.

По SPI тот же формат идёт как `HOST_ADDRESSES` (0x08, U1→U2, раз в 250 мс,
если хост U1 уже дал список) и `ENDPOINT_ADDRESSES` (0x09, U2→U1, раз в 64
ответа, если хост U2 уже дал список). Платы хранят оба списка только в RAM.
```

- [ ] **Step 9: Прогнать тесты.**

  Run:

  ```
  .venv/Scripts/python.exe -m pytest configurator/tests tests -q
  cmake --preset native
  cmake --build --preset native --clean-first
  ctest --preset native
  ```

  Expected: всё зелёное. Если какой-то тест сверяет полный список возможностей или типов (например, паритет эмулятора с прошивкой), он укажет место, куда тоже нужно добавить новые значения. Добавить их туда же.

- [ ] **Step 10: Commit.**

```bash
git add protocol/schema.json firmware/common/protocol configurator/src/duo_input/generated configurator/src/duo_input/device/emulator.py docs/protocol/compatibility.md configurator/tests/test_generated_protocol.py tests/firmware_native/test_frames.cpp
git commit -m "feat(protocol): address exchange identifiers"
```

---

### Task 2: `HostAddresses` и `AddressBook` в общей прошивке

**Files:**
- Create: `firmware/common/link/host_addresses.hpp`, `firmware/common/link/host_addresses.cpp`
- Modify: `firmware/common/CMakeLists.txt` (добавить `link/host_addresses.cpp` в `duo_common`)
- Create: `tests/firmware_native/test_host_addresses.cpp`
- Modify: `tests/firmware_native/CMakeLists.txt`

**Interfaces:**
- Produces (namespace `duo_input::link`):
  - `kMaxHostAddresses = 8`, `kHostAddressesMaxSize = 33`, `kHostAddressesIntervalMs = 250`, `kEndpointAddressesEvery = 64`;
  - `struct HostAddresses { std::uint8_t count; std::uint8_t octets[8][4]; }`;
  - `bool encode_host_addresses(const HostAddresses&, protocol::MutableByteView, std::size_t& written)`;
  - `bool decode_host_addresses(protocol::ByteView, HostAddresses&)`;
  - `class AddressBook` с методами `set_local`, `has_local`, `local`, `peer`, `accept_peer(ByteView) -> bool`, `local_due(now_ms) -> bool`, `mark_local_sent(now_ms)`, `take_reply_slot() -> bool`.

- [ ] **Step 1: Написать падающие тесты.** Файл `tests/firmware_native/test_host_addresses.cpp`:

```cpp
// The list of addresses a computer can be reached at, as the two boards carry
// it between the two computers - and when each board repeats its own.

#include "link/host_addresses.hpp"
#include "test_support.hpp"

using duo_input::link::AddressBook;
using duo_input::link::HostAddresses;
using duo_input::link::decode_host_addresses;
using duo_input::link::encode_host_addresses;
using duo_input::link::kEndpointAddressesEvery;
using duo_input::link::kHostAddressesIntervalMs;
using duo_input::protocol::ByteView;
using duo_input::protocol::MutableByteView;

namespace {

HostAddresses two_addresses() {
    HostAddresses list;
    list.count = 2;
    const std::uint8_t first[4] = {192, 168, 1, 7};
    const std::uint8_t second[4] = {10, 0, 0, 2};
    for (int i = 0; i < 4; ++i) {
        list.octets[0][i] = first[i];
        list.octets[1][i] = second[i];
    }
    return list;
}

}  // namespace

TEST_CASE(a_list_survives_the_round_trip) {
    std::uint8_t wire[33] = {};
    std::size_t written = 0;
    CHECK(encode_host_addresses(two_addresses(), MutableByteView{wire, sizeof(wire)}, written));
    CHECK_EQ(written, 9u);
    CHECK_EQ(wire[0], 2u);
    CHECK_EQ(wire[1], 192u);

    HostAddresses back;
    CHECK(decode_host_addresses(ByteView{wire, written}, back));
    CHECK_EQ(back.count, 2u);
    CHECK_EQ(back.octets[1][0], 10u);
    CHECK_EQ(back.octets[1][3], 2u);
}

TEST_CASE(an_empty_list_is_one_byte) {
    std::uint8_t wire[33] = {};
    std::size_t written = 0;
    CHECK(encode_host_addresses(HostAddresses{}, MutableByteView{wire, sizeof(wire)}, written));
    CHECK_EQ(written, 1u);
    HostAddresses back;
    CHECK(decode_host_addresses(ByteView{wire, 1}, back));
    CHECK_EQ(back.count, 0u);
}

TEST_CASE(more_than_eight_addresses_is_refused) {
    std::uint8_t wire[1 + 4 * 9] = {9};
    for (std::size_t i = 1; i < sizeof(wire); ++i) wire[i] = 1;
    HostAddresses back;
    CHECK_FALSE(decode_host_addresses(ByteView{wire, sizeof(wire)}, back));
}

TEST_CASE(a_length_that_disagrees_with_the_count_is_refused) {
    const std::uint8_t short_by_one[] = {1, 192, 168, 1};
    const std::uint8_t long_by_one[] = {1, 192, 168, 1, 7, 0};
    HostAddresses back;
    CHECK_FALSE(decode_host_addresses(ByteView{short_by_one, sizeof(short_by_one)}, back));
    CHECK_FALSE(decode_host_addresses(ByteView{long_by_one, sizeof(long_by_one)}, back));
    CHECK_FALSE(decode_host_addresses(ByteView{nullptr, 0}, back));
}

TEST_CASE(the_unspecified_address_is_refused) {
    const std::uint8_t zero[] = {1, 0, 0, 0, 0};
    HostAddresses back;
    CHECK_FALSE(decode_host_addresses(ByteView{zero, sizeof(zero)}, back));
}

TEST_CASE(a_refused_peer_list_leaves_the_previous_one) {
    AddressBook book;
    std::uint8_t wire[33] = {};
    std::size_t written = 0;
    encode_host_addresses(two_addresses(), MutableByteView{wire, sizeof(wire)}, written);
    CHECK(book.accept_peer(ByteView{wire, written}));

    const std::uint8_t broken[] = {3, 1, 2};
    CHECK_FALSE(book.accept_peer(ByteView{broken, sizeof(broken)}));
    CHECK_EQ(book.peer().count, 2u);
}

TEST_CASE(nothing_is_due_before_the_host_has_said_anything) {
    AddressBook book;
    CHECK_FALSE(book.local_due(0));
    CHECK_FALSE(book.local_due(100000));
    for (std::uint32_t i = 0; i < 2 * kEndpointAddressesEvery; ++i) {
        CHECK_FALSE(book.take_reply_slot());
    }
}

TEST_CASE(the_master_repeats_the_list_on_its_interval) {
    AddressBook book;
    book.set_local(two_addresses());
    CHECK(book.local_due(5));
    book.mark_local_sent(5);
    CHECK_FALSE(book.local_due(5 + kHostAddressesIntervalMs - 1));
    CHECK(book.local_due(5 + kHostAddressesIntervalMs));
}

TEST_CASE(an_empty_list_from_the_host_is_still_repeated) {
    // "No addresses" is an answer: the far side must stop showing old ones.
    AddressBook book;
    book.set_local(HostAddresses{});
    CHECK(book.local_due(0));
}

TEST_CASE(the_endpoint_answers_with_its_list_once_in_so_many_replies) {
    AddressBook book;
    book.set_local(two_addresses());
    std::uint32_t slots = 0;
    for (std::uint32_t i = 0; i < 3 * kEndpointAddressesEvery; ++i) {
        if (book.take_reply_slot()) ++slots;
    }
    CHECK_EQ(slots, 3u);
}
```

  В `tests/firmware_native/CMakeLists.txt` добавить по образцу `duo_hid_state_manager_test`:

```cmake
add_executable(duo_host_addresses_test
    test_main.cpp
    test_host_addresses.cpp
)

target_link_libraries(duo_host_addresses_test PRIVATE duo_common)

if(MSVC)
    target_compile_options(duo_host_addresses_test PRIVATE /EHsc)
endif()

add_test(NAME host_addresses COMMAND duo_host_addresses_test)
```

- [ ] **Step 2: Убедиться, что тест падает.**

  Run: `cmake --build --preset native --clean-first`
  Expected: ошибка компиляции `link/host_addresses.hpp: No such file`.

- [ ] **Step 3: Реализовать.** Файл `firmware/common/link/host_addresses.hpp`:

```cpp
#pragma once

// Where a computer can be reached, as the two boards pass it between the two
// computers.
//
// Each computer tells its own board its IPv4 addresses; the boards swap those
// lists over the SPI link; each computer then asks its board for the other
// one's. The list travels whole, every time, like everything else on that
// link: a lost frame costs a repeat, never a disagreement.
//
// Nothing here is stored in flash. A board that lost power knows nothing
// until its computer tells it again, which it does every few seconds.

#include <cstddef>
#include <cstdint>

#include "protocol/bytes.hpp"

namespace duo_input::link {

inline constexpr std::size_t kMaxHostAddresses = 8;

/// The count byte and eight addresses of four bytes.
inline constexpr std::size_t kHostAddressesMaxSize = 1 + 4 * kMaxHostAddresses;

/// U1 repeats its computer's list this often. Well inside a human's patience,
/// and one frame in two hundred and fifty on a busy link.
inline constexpr std::uint32_t kHostAddressesIntervalMs = 250;

/// U2 answers with its computer's list instead of its status once in this
/// many replies. U1 clocks at least one transfer every 20 ms, so this is at
/// most every 1.3 s.
inline constexpr std::uint32_t kEndpointAddressesEvery = 64;

struct HostAddresses {
    std::uint8_t count = 0;
    /// Network order: octets[i][0] is the first number of the dotted form.
    std::uint8_t octets[kMaxHostAddresses][4] = {};
};

bool encode_host_addresses(const HostAddresses& addresses, protocol::MutableByteView output,
                           std::size_t& written);

/// Refuses anything that is not exactly one count byte and that many
/// addresses, more than kMaxHostAddresses, or 0.0.0.0.
bool decode_host_addresses(protocol::ByteView payload, HostAddresses& addresses);

/// Both lists one board holds, and when it is due to repeat its own.
class AddressBook {
public:
    void set_local(const HostAddresses& addresses);
    bool has_local() const { return has_local_; }
    const HostAddresses& local() const { return local_; }

    const HostAddresses& peer() const { return peer_; }

    /// Take the far side's list from a frame. A payload that does not decode
    /// leaves the list that was there.
    bool accept_peer(protocol::ByteView payload);

    /// Master side: time to send the local list again.
    bool local_due(std::uint32_t now_ms) const;
    void mark_local_sent(std::uint32_t now_ms);

    /// Slave side: called once per reply; true when this reply should carry
    /// the local list instead of the status.
    bool take_reply_slot();

private:
    HostAddresses local_{};
    HostAddresses peer_{};
    bool has_local_ = false;
    bool ever_sent_ = false;
    std::uint32_t last_sent_ms_ = 0;
    std::uint32_t replies_ = 0;
};

}  // namespace duo_input::link
```

  Файл `firmware/common/link/host_addresses.cpp`:

```cpp
#include "link/host_addresses.hpp"

namespace duo_input::link {

bool encode_host_addresses(const HostAddresses& addresses, protocol::MutableByteView output,
                           std::size_t& written) {
    written = 0;
    if (addresses.count > kMaxHostAddresses) {
        return false;
    }
    const std::size_t size = 1 + 4 * static_cast<std::size_t>(addresses.count);
    if (output.data == nullptr || output.size < size) {
        return false;
    }
    output.data[0] = addresses.count;
    for (std::size_t index = 0; index < addresses.count; ++index) {
        for (std::size_t octet = 0; octet < 4; ++octet) {
            output.data[1 + 4 * index + octet] = addresses.octets[index][octet];
        }
    }
    written = size;
    return true;
}

bool decode_host_addresses(protocol::ByteView payload, HostAddresses& addresses) {
    if (payload.data == nullptr || payload.size == 0) {
        return false;
    }
    const std::uint8_t count = payload.data[0];
    if (count > kMaxHostAddresses ||
        payload.size != 1 + 4 * static_cast<std::size_t>(count)) {
        return false;
    }
    HostAddresses decoded;
    decoded.count = count;
    for (std::size_t index = 0; index < count; ++index) {
        bool all_zero = true;
        for (std::size_t octet = 0; octet < 4; ++octet) {
            const std::uint8_t value = payload.data[1 + 4 * index + octet];
            decoded.octets[index][octet] = value;
            all_zero = all_zero && value == 0;
        }
        if (all_zero) {
            // Nobody can be called at 0.0.0.0. A list carrying it was built
            // by something that did not know an address, and pretending it
            // did would send a connection nowhere.
            return false;
        }
    }
    addresses = decoded;
    return true;
}

void AddressBook::set_local(const HostAddresses& addresses) {
    local_ = addresses;
    has_local_ = true;
}

bool AddressBook::accept_peer(protocol::ByteView payload) {
    HostAddresses decoded;
    if (!decode_host_addresses(payload, decoded)) {
        return false;
    }
    peer_ = decoded;
    return true;
}

bool AddressBook::local_due(std::uint32_t now_ms) const {
    return has_local_ && (!ever_sent_ || now_ms - last_sent_ms_ >= kHostAddressesIntervalMs);
}

void AddressBook::mark_local_sent(std::uint32_t now_ms) {
    ever_sent_ = true;
    last_sent_ms_ = now_ms;
}

bool AddressBook::take_reply_slot() {
    if (!has_local_) {
        return false;
    }
    if (++replies_ < kEndpointAddressesEvery) {
        return false;
    }
    replies_ = 0;
    return true;
}

}  // namespace duo_input::link
```

  В `firmware/common/CMakeLists.txt` добавить строку `link/host_addresses.cpp` перед `link/spi_protocol.cpp`.

- [ ] **Step 4: Прогнать тесты.**

  Run: `cmake --build --preset native --clean-first` и `ctest --preset native -R host_addresses`
  Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add firmware/common/link/host_addresses.hpp firmware/common/link/host_addresses.cpp firmware/common/CMakeLists.txt tests/firmware_native/test_host_addresses.cpp tests/firmware_native/CMakeLists.txt
git commit -m "feat(firmware): HostAddresses codec and AddressBook"
```

---

### Task 3: `EXCHANGE_ADDRESSES` на U1

**Files:**
- Modify: `firmware/u1_main/config_service.hpp`, `firmware/u1_main/config_service.cpp`
- Test: `tests/firmware_native/test_config_service.cpp`

**Interfaces:**
- Consumes: `link::AddressBook`, `link::encode_host_addresses`, `link::decode_host_addresses` (Task 2).
- Produces: `void ConfigService::set_address_book(link::AddressBook* book)`.

- [ ] **Step 1: Написать падающие тесты.** В `tests/firmware_native/test_config_service.cpp`:
  - добавить `#include "link/host_addresses.hpp"`;
  - в `struct Link` добавить член `duo_input::link::AddressBook book;` **перед** `ConfigService service{...}` и конструктор `Link() { service.set_address_book(&book); }`;
  - в конец файла добавить тесты:

```cpp
// ------------------------------------------------------- address exchange

TEST_CASE(the_device_offers_address_exchange) {
    Link link;
    std::uint8_t request[4] = {0xFF, 0xFF, 0xFF, 0xFF};
    const CdcFrame reply = link.send(CdcMessageType::HELLO, request, sizeof(request));
    const std::uint32_t granted = u32_at(reply, 3);
    CHECK((granted & static_cast<std::uint32_t>(
                         duo_input::protocol::Capability::ADDRESS_EXCHANGE)) != 0u);
}

TEST_CASE(an_exchange_keeps_the_hosts_list_and_returns_the_peers) {
    Link link;
    link.hello();
    const std::uint8_t peer[] = {1, 10, 0, 0, 2};
    CHECK(link.book.accept_peer(duo_input::protocol::ByteView{peer, sizeof(peer)}));

    const std::uint8_t mine[] = {1, 192, 168, 1, 7};
    const CdcFrame reply = link.send(CdcMessageType::EXCHANGE_ADDRESSES, mine, sizeof(mine));

    CHECK(reply.type == CdcMessageType::EXCHANGE_ADDRESSES);
    CHECK_EQ(error_of(reply), CdcError::Ok);
    CHECK_EQ(reply.payload.size, 6u);
    CHECK_EQ(reply.payload.data[1], 1u);
    CHECK_EQ(reply.payload.data[2], 10u);
    CHECK(link.book.has_local());
    CHECK_EQ(link.book.local().octets[0][0], 192u);
}

TEST_CASE(an_exchange_with_a_broken_list_changes_nothing) {
    Link link;
    link.hello();
    const std::uint8_t broken[] = {2, 192, 168, 1, 7};
    const CdcFrame reply = link.send(CdcMessageType::EXCHANGE_ADDRESSES, broken, sizeof(broken));
    CHECK_EQ(error_of(reply), CdcError::InvalidRequest);
    CHECK_FALSE(link.book.has_local());
}

TEST_CASE(an_exchange_needs_the_capability_to_have_been_agreed) {
    Link link;
    std::uint8_t nothing[4] = {0, 0, 0, 0};
    link.send(CdcMessageType::HELLO, nothing, sizeof(nothing));
    const std::uint8_t mine[] = {1, 192, 168, 1, 7};
    const CdcFrame reply = link.send(CdcMessageType::EXCHANGE_ADDRESSES, mine, sizeof(mine));
    CHECK_EQ(error_of(reply), CdcError::UnsupportedCapability);
    CHECK_FALSE(link.book.has_local());
}
```

- [ ] **Step 2: Убедиться, что тесты падают.**

  Run: `cmake --build --preset native --clean-first`
  Expected: ошибка компиляции, потому что `set_address_book` не объявлен.

- [ ] **Step 3: Реализовать.**
  - В `config_service.hpp` добавить `#include "link/host_addresses.hpp"`.
  - В публичную часть `ConfigService`, рядом с `set_link_state`, добавить:

```cpp
    /// Where EXCHANGE_ADDRESSES keeps this computer's list and finds the
    /// other one's. main() and SpiMaster share the same book; without one the
    /// command is refused as unsupported.
    void set_address_book(link::AddressBook* book) { addresses_ = book; }
```

  - В приватные члены добавить `link::AddressBook* addresses_ = nullptr;`.

  В `config_service.cpp`:
  - В `device_capabilities()` добавить `| static_cast<std::uint32_t>(protocol::Capability::ADDRESS_EXCHANGE)`.
  - В `expected_request_size` добавить `case CdcMessageType::EXCHANGE_ADDRESSES:` к ветке `return -1;` рядом с `PING`.
  - В `required_capability` добавить:

```cpp
        case CdcMessageType::EXCHANGE_ADDRESSES:
            return static_cast<std::uint32_t>(protocol::Capability::ADDRESS_EXCHANGE);
```

  - В `dispatch` перед `default:` добавить:

```cpp
        case CdcMessageType::EXCHANGE_ADDRESSES: {
            if (addresses_ == nullptr) {
                reply_error(frame, CdcError::UnsupportedCapability);
                return;
            }
            link::HostAddresses local;
            if (!link::decode_host_addresses(frame.payload, local)) {
                // The list that was there stays: a malformed request is not a
                // reason for the other computer to stop seeing this one.
                reply_error(frame, CdcError::InvalidRequest);
                return;
            }
            addresses_->set_local(local);
            payload[0] = static_cast<std::uint8_t>(CdcError::Ok);
            std::size_t written = 0;
            link::encode_host_addresses(
                addresses_->peer(),
                protocol::MutableByteView{payload + 1, ProtocolLimits::CDC_MAX_PAYLOAD - 1},
                written);
            reply(frame.type, frame.sequence, payload, 1 + written);
            return;
        }
```

- [ ] **Step 4: Прогнать тесты.**

  Run: `cmake --build --preset native --clean-first` и `ctest --preset native`
  Expected: всё зелёное, включая старые тесты `config_service`.

- [ ] **Step 5: Commit.**

```bash
git add firmware/u1_main/config_service.hpp firmware/u1_main/config_service.cpp tests/firmware_native/test_config_service.cpp
git commit -m "feat(u1): EXCHANGE_ADDRESSES over CDC"
```

---

### Task 4: SPI-обмен на U1

**Files:**
- Modify:
  - `firmware/u1_main/spi_master.hpp`;
  - `firmware/u1_main/spi_master_poll.cpp`;
  - `firmware/u1_main/spi_master.cpp`;
  - `firmware/common/link/spi_protocol.cpp` (`is_endpoint_reply`);
  - `firmware/u1_main/main.cpp`.
- Test: `tests/firmware_native/test_spi_master_poll.cpp`

**Interfaces:**
- Consumes: `link::AddressBook` (Task 2).
- Produces:
  - `void SpiMaster::set_address_book(link::AddressBook*)`;
  - `void SpiMaster::apply_reply(const protocol::SpiFrame&)` (публичный, вызывается из `consume_reply`).

- [ ] **Step 1: Написать падающие тесты.** Добавить в конец `tests/firmware_native/test_spi_master_poll.cpp`, добавив `#include "link/host_addresses.hpp"`:

```cpp
// -------------------------------------------------------- address exchange

namespace {

duo_input::link::HostAddresses one_address() {
    duo_input::link::HostAddresses list;
    list.count = 1;
    list.octets[0][0] = 192;
    list.octets[0][1] = 168;
    list.octets[0][2] = 1;
    list.octets[0][3] = 7;
    return list;
}

}  // namespace

TEST_CASE(no_address_frame_goes_out_until_the_host_gave_a_list) {
    HidStateManager outputs;
    SpiMaster link;
    duo_input::link::AddressBook book;
    link.set_address_book(&book);
    prime(link, outputs);
    for (std::uint32_t now = 1; now < 1000; ++now) {
        link.poll(now, outputs);
    }
    CHECK_EQ(duo::test::spi_link().count_of(SpiMessageType::HOST_ADDRESSES), 0u);
}

TEST_CASE(the_hosts_list_goes_out_on_its_interval_even_under_continuous_motion) {
    HidStateManager outputs;
    SpiMaster link;
    duo_input::link::AddressBook book;
    link.set_address_book(&book);
    prime(link, outputs);
    book.set_local(one_address());

    // A mouse that never stops: HEARTBEAT would never be due, so the list
    // must not depend on it.
    for (std::uint32_t now = 1; now <= 1000; ++now) {
        outputs.mouse_delta(Target::Pc2, 1, 0, 0, 0);
        link.poll(now, outputs);
    }
    const std::size_t sent = duo::test::spi_link().count_of(SpiMessageType::HOST_ADDRESSES);
    CHECK(sent >= 4u);
    CHECK(sent <= 5u);
}

TEST_CASE(motion_held_back_by_an_address_frame_is_sent_on_the_next_pass) {
    HidStateManager outputs;
    SpiMaster link;
    duo_input::link::AddressBook book;
    link.set_address_book(&book);
    prime(link, outputs);
    book.set_local(one_address());

    outputs.mouse_delta(Target::Pc2, 5, 0, 0, 0);
    CHECK(link.poll(1, outputs));
    CHECK_EQ(duo::test::spi_link().count_of(SpiMessageType::HOST_ADDRESSES), 1u);
    CHECK(link.poll(2, outputs));
    CHECK_EQ(total_dx(), 5);
}

TEST_CASE(an_endpoint_address_reply_fills_the_peer_list) {
    SpiMaster link;
    duo_input::link::AddressBook book;
    link.set_address_book(&book);
    const std::uint8_t payload[] = {1, 10, 0, 0, 2};
    duo_input::protocol::SpiFrame frame;
    frame.type = SpiMessageType::ENDPOINT_ADDRESSES;
    frame.payload = duo_input::protocol::ByteView{payload, sizeof(payload)};

    link.apply_reply(frame);

    CHECK(link.status().answered);
    CHECK_EQ(book.peer().count, 1u);
    CHECK_EQ(book.peer().octets[0][0], 10u);
}

TEST_CASE(an_address_reply_is_an_endpoint_reply_and_the_hosts_own_is_not) {
    // U1 tells an echo on the wires from U2's answer by type alone. Sharing a
    // type between the two directions would let an echo pass as U2.
    CHECK(duo_input::link::is_endpoint_reply(SpiMessageType::ENDPOINT_ADDRESSES));
    CHECK_FALSE(duo_input::link::is_endpoint_reply(SpiMessageType::HOST_ADDRESSES));
}
```

- [ ] **Step 2: Убедиться, что тесты падают.**

  Run: `cmake --build --preset native --clean-first`
  Expected: ошибка компиляции, потому что `set_address_book` и `apply_reply` не объявлены.

- [ ] **Step 3: Реализовать.**

  В `spi_master.hpp`:
  - добавить `#include "link/host_addresses.hpp"`;
  - в публичную часть, после `send_release_all`, добавить:

```cpp
    /// The book this link fills from U2's replies and whose local list it
    /// repeats to U2. Shared with ConfigService; nullptr means no exchange.
    void set_address_book(link::AddressBook* book) { addresses_ = book; }

    /// Everything a decoded, genuine U2 reply means. consume_reply() calls
    /// this once the frame has passed its CRC and the echo check; public so
    /// the decision can be exercised without the SPI block.
    void apply_reply(const protocol::SpiFrame& frame);
```

  - в приватные члены добавить `link::AddressBook* addresses_ = nullptr;`.

  В `spi_master_poll.cpp`:
  - добавить `#include "link/host_addresses.hpp"`;
  - в начало `poll_snapshot`, сразу после `told_keyboard = false;`, вставить:

```cpp
    // The computer's own addresses, repeated on their interval. Checked first
    // because a link that is never idle - a mouse that never stops - would
    // otherwise never reach a slot for them. Returning here holds the
    // keyboard state and the movement exactly as a busy transfer does; they
    // go out on the next pass.
    if (addresses_ != nullptr && addresses_->local_due(now_ms)) {
        std::uint8_t list[link::kHostAddressesMaxSize];
        std::size_t size = 0;
        if (link::encode_host_addresses(addresses_->local(),
                                        protocol::MutableByteView{list, sizeof(list)}, size) &&
            send(protocol::SpiMessageType::HOST_ADDRESSES, protocol::ByteView{list, size},
                 now_ms)) {
            addresses_->mark_local_sent(now_ms);
            return true;
        }
    }
```

  - в конец файла, перед закрывающим namespace, добавить `apply_reply` с логикой, **перенесённой** из `consume_reply`:

```cpp
void SpiMaster::apply_reply(const protocol::SpiFrame& frame) {
    replies_.observe(frame.sequence);
    status_.answered = true;
    if (frame.type == protocol::SpiMessageType::ENDPOINT_ADDRESSES) {
        if (addresses_ != nullptr) {
            addresses_->accept_peer(frame.payload);
        }
        return;
    }
    if (frame.type == protocol::SpiMessageType::ENDPOINT_STATUS && frame.payload.size >= 1) {
        status_.mounted = frame.payload.data[0] != 0;
    }
    if (frame.type == protocol::SpiMessageType::ENDPOINT_STATUS && frame.payload.size >= 4) {
        // Read separately from the mount flag, so a U2 that reports only the
        // flag stays readable instead of being rejected over a field it never
        // claimed to send.
        status_.endpoint_drops = frame.payload.data[1];
        status_.endpoint_release_ms = static_cast<std::uint16_t>(
            frame.payload.data[2] | (frame.payload.data[3] << 8));
    }
}
```

  В `spi_master.cpp` (`consume_reply`) заменить всё после блока проверки `is_endpoint_reply` (от `replies_.observe(...)` до конца функции) одной строкой `apply_reply(result.spi);`.

  В `firmware/common/link/spi_protocol.cpp`:

```cpp
bool is_endpoint_reply(protocol::SpiMessageType type) {
    return type == protocol::SpiMessageType::ENDPOINT_STATUS ||
           type == protocol::SpiMessageType::ENDPOINT_ADDRESSES;
}
```

  В `firmware/u1_main/main.cpp`:
  - добавить `#include "link/host_addresses.hpp"`;
  - после строки `static duo_input::u1::ConfigService config(...)` добавить:

```cpp
    // One book, two readers: the CDC side takes this computer's addresses and
    // hands back the other's; the link repeats the first and fills the second.
    // Both run in this loop, on this core, so nothing crosses a core here.
    static duo_input::link::AddressBook addresses;
    link.set_address_book(&addresses);
    config.set_address_book(&addresses);
```

- [ ] **Step 4: Прогнать нативные тесты.**

  Run: `cmake --build --preset native --clean-first` и `ctest --preset native`
  Expected: всё зелёное, в том числе прежние `spi_master_poll` и `macro_publication`.

- [ ] **Step 5: Собрать прошивку U1.** Нужно убедиться, что `main.cpp` и `spi_master.cpp` компилируются под Pico.

  Run: `cmake --preset pico-release` и `cmake --build --preset pico-release --clean-first`
  Expected: сборка успешна.

- [ ] **Step 6: Commit.**

```bash
git add firmware/u1_main/spi_master.hpp firmware/u1_main/spi_master_poll.cpp firmware/u1_main/spi_master.cpp firmware/common/link/spi_protocol.cpp firmware/u1_main/main.cpp tests/firmware_native/test_spi_master_poll.cpp
git commit -m "feat(u1): carry host addresses over the SPI link"
```

---

### Task 5: CDC-сервис адресов на U2

**Files:**
- Create: `firmware/u2_endpoint/address_service.hpp`, `firmware/u2_endpoint/address_service.cpp`
- Create: `tests/firmware_native/test_address_service.cpp`
- Modify: `tests/firmware_native/CMakeLists.txt`

**Interfaces:**
- Consumes: `link::AddressBook` (Task 2), `protocol::encode_cdc_frame` / `decode_cdc_frame`.
- Produces (namespace `duo_input::u2`):
  - `class ByteSink { virtual void write(const std::uint8_t*, std::size_t) = 0; }`;
  - `class AddressService { AddressService(link::AddressBook&, ByteSink&); void on_cdc_bytes(const std::uint8_t*, std::size_t); void on_disconnect(); }`.
- Ответ на `HELLO` — `DEVICE_INFO` в формате U1 из 44 байт: `error`, `major`, `minor`, `caps u32`, `generation u32 = 0`, `profile = 0`, `32 × 0`. Python `parse_device_info` его понимает.

- [ ] **Step 1: Написать падающие тесты.** Файл `tests/firmware_native/test_address_service.cpp`:

```cpp
// What U2 says to the one program that talks to it: who it is, and the other
// computer's addresses in exchange for this one's.

#include <cstring>
#include <vector>

#include "address_service.hpp"
#include "link/host_addresses.hpp"
#include "protocol/frame.hpp"
#include "test_support.hpp"

using duo_input::protocol::CdcFrame;
using duo_input::protocol::CdcMessageType;
using duo_input::protocol::CdcError;
using duo_input::protocol::ProtocolLimits;

namespace {

class Recorder : public duo_input::u2::ByteSink {
public:
    void write(const std::uint8_t* data, std::size_t size) override {
        last.assign(data, data + size);
        ++writes;
    }
    std::vector<std::uint8_t> last;
    int writes = 0;
};

struct Board {
    duo_input::link::AddressBook book;
    Recorder sink;
    duo_input::u2::AddressService service{book, sink};
    std::uint16_t sequence = 0;
    std::uint8_t scratch[ProtocolLimits::CDC_MAX_PAYLOAD] = {};
    duo_input::protocol::DecodeResult result{};

    CdcFrame send(CdcMessageType type, const std::uint8_t* payload, std::size_t size) {
        CdcFrame request;
        request.type = type;
        request.sequence = sequence++;
        request.payload = duo_input::protocol::ByteView{payload, size};
        std::uint8_t wire[256];
        std::uint8_t encode_scratch[256];
        std::size_t written = 0;
        duo_input::protocol::encode_cdc_frame(
            request, duo_input::protocol::MutableByteView{wire, sizeof(wire)},
            duo_input::protocol::MutableByteView{encode_scratch, sizeof(encode_scratch)},
            written);
        sink.writes = 0;
        service.on_cdc_bytes(wire, written);
        if (sink.writes == 0) {
            return CdcFrame{};
        }
        duo_input::protocol::decode_cdc_frame(
            duo_input::protocol::ByteView{sink.last.data(), sink.last.size()},
            duo_input::protocol::MutableByteView{scratch, sizeof(scratch)}, result);
        return result.cdc;
    }

    CdcFrame hello(std::uint32_t requested = 0xFFFFFFFFu) {
        const std::uint8_t request[4] = {
            static_cast<std::uint8_t>(requested), static_cast<std::uint8_t>(requested >> 8),
            static_cast<std::uint8_t>(requested >> 16), static_cast<std::uint8_t>(requested >> 24)};
        return send(CdcMessageType::HELLO, request, sizeof(request));
    }
};

std::uint32_t caps_of(const CdcFrame& frame) {
    const std::uint8_t* p = frame.payload.data + 3;
    return static_cast<std::uint32_t>(p[0]) | (static_cast<std::uint32_t>(p[1]) << 8) |
           (static_cast<std::uint32_t>(p[2]) << 16) | (static_cast<std::uint32_t>(p[3]) << 24);
}

}  // namespace

TEST_CASE(u2_answers_hello_with_a_device_info_the_configurator_can_parse) {
    Board board;
    const CdcFrame reply = board.hello();
    CHECK(reply.type == CdcMessageType::DEVICE_INFO);
    CHECK_EQ(reply.payload.size, 44u);
    CHECK_EQ(reply.payload.data[0], static_cast<std::uint8_t>(CdcError::Ok));
    CHECK_EQ(reply.payload.data[1], duo_input::protocol::PROTOCOL_VERSION_MAJOR);
    CHECK_EQ(caps_of(reply),
             static_cast<std::uint32_t>(duo_input::protocol::Capability::ADDRESS_EXCHANGE));
}

TEST_CASE(u2_exchanges_addresses_after_hello) {
    Board board;
    const std::uint8_t peer[] = {1, 192, 168, 1, 7};
    CHECK(board.book.accept_peer(duo_input::protocol::ByteView{peer, sizeof(peer)}));
    board.hello();

    const std::uint8_t mine[] = {1, 10, 0, 0, 2};
    const CdcFrame reply = board.send(CdcMessageType::EXCHANGE_ADDRESSES, mine, sizeof(mine));

    CHECK(reply.type == CdcMessageType::EXCHANGE_ADDRESSES);
    CHECK_EQ(reply.payload.size, 6u);
    CHECK_EQ(reply.payload.data[0], static_cast<std::uint8_t>(CdcError::Ok));
    CHECK_EQ(reply.payload.data[2], 192u);
    CHECK(board.book.has_local());
    CHECK_EQ(board.book.local().octets[0][0], 10u);
}

TEST_CASE(u2_refuses_an_exchange_before_hello) {
    Board board;
    const std::uint8_t mine[] = {1, 10, 0, 0, 2};
    const CdcFrame reply = board.send(CdcMessageType::EXCHANGE_ADDRESSES, mine, sizeof(mine));
    CHECK_EQ(reply.payload.data[0], static_cast<std::uint8_t>(CdcError::BadState));
    CHECK_FALSE(board.book.has_local());
}

TEST_CASE(u2_refuses_an_exchange_the_host_did_not_ask_for) {
    Board board;
    board.hello(0);
    const std::uint8_t mine[] = {1, 10, 0, 0, 2};
    const CdcFrame reply = board.send(CdcMessageType::EXCHANGE_ADDRESSES, mine, sizeof(mine));
    CHECK_EQ(reply.payload.data[0], static_cast<std::uint8_t>(CdcError::UnsupportedCapability));
}

TEST_CASE(u2_refuses_a_broken_list_and_keeps_the_old_one) {
    Board board;
    board.hello();
    const std::uint8_t good[] = {1, 10, 0, 0, 2};
    board.send(CdcMessageType::EXCHANGE_ADDRESSES, good, sizeof(good));
    const std::uint8_t broken[] = {2, 10, 0, 0, 3};
    const CdcFrame reply = board.send(CdcMessageType::EXCHANGE_ADDRESSES, broken, sizeof(broken));
    CHECK_EQ(reply.payload.data[0], static_cast<std::uint8_t>(CdcError::InvalidRequest));
    CHECK_EQ(board.book.local().octets[0][3], 2u);
}

TEST_CASE(u2_says_plainly_that_it_is_not_a_configurable_board) {
    Board board;
    board.hello();
    const CdcFrame reply = board.send(CdcMessageType::GET_STATUS, nullptr, 0);
    CHECK(reply.type == CdcMessageType::GET_STATUS);
    CHECK_EQ(reply.payload.data[0], static_cast<std::uint8_t>(CdcError::UnsupportedCapability));
}

TEST_CASE(u2_answers_ping_before_hello) {
    Board board;
    const std::uint8_t probe[] = {7, 8};
    const CdcFrame reply = board.send(CdcMessageType::PING, probe, sizeof(probe));
    CHECK(reply.type == CdcMessageType::PING);
    CHECK_EQ(reply.payload.size, 3u);
    CHECK_EQ(reply.payload.data[2], 8u);
}

TEST_CASE(u2_ignores_garbage_and_recovers_on_the_next_frame) {
    Board board;
    std::uint8_t noise[300];
    std::memset(noise, 0x55, sizeof(noise));
    board.service.on_cdc_bytes(noise, sizeof(noise));
    const std::uint8_t delimiter = 0;
    board.service.on_cdc_bytes(&delimiter, 1);
    CHECK_EQ(board.sink.writes, 0);
    const CdcFrame reply = board.hello();
    CHECK(reply.type == CdcMessageType::DEVICE_INFO);
}
```

  В `tests/firmware_native/CMakeLists.txt` добавить по образцу `duo_frame_resync_test`:

```cmake
add_executable(duo_address_service_test
    test_main.cpp
    test_address_service.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u2_endpoint/address_service.cpp
)

target_link_libraries(duo_address_service_test PRIVATE duo_common)
target_include_directories(duo_address_service_test PRIVATE
    ${CMAKE_SOURCE_DIR}/firmware/u2_endpoint
)

if(MSVC)
    target_compile_options(duo_address_service_test PRIVATE /EHsc)
endif()

add_test(NAME address_service COMMAND duo_address_service_test)
```

- [ ] **Step 2: Убедиться, что тест падает.**

  Run: `cmake --build --preset native --clean-first`
  Expected: ошибка компиляции `address_service.hpp: No such file`.

- [ ] **Step 3: Реализовать.** Файл `firmware/u2_endpoint/address_service.hpp`:

```cpp
#pragma once

// The CDC side of U2: the one thing PC2's program has to say to its board.
//
// U2 holds no configuration and never will; this port exists so PC2 can hand
// over its own addresses and read PC1's. It speaks the same framing as U1 so
// the configurator reuses its transport, and it answers everything else with
// UNSUPPORTED_CAPABILITY rather than silence: a program pointed at the wrong
// board should be told so, not left to time out.

#include <cstddef>
#include <cstdint>

#include "link/host_addresses.hpp"
#include "protocol/frame.hpp"
#include "protocol/generated.hpp"

namespace duo_input::u2 {

class ByteSink {
public:
    virtual ~ByteSink() = default;
    virtual void write(const std::uint8_t* data, std::size_t size) = 0;
};

class AddressService {
public:
    AddressService(link::AddressBook& book, ByteSink& sink) : book_(book), sink_(sink) {}

    void on_cdc_bytes(const std::uint8_t* data, std::size_t size);

    /// The host went away. The next one starts with HELLO.
    void on_disconnect();

private:
    /// Largest request this service answers, framed: HELLO or a full list,
    /// with room for COBS overhead. Anything longer is discarded to the next
    /// delimiter.
    static constexpr std::size_t kMaxWire = 96;
    static constexpr std::size_t kMaxPayload = 64;

    void handle_frame(const std::uint8_t* wire, std::size_t size);
    void reply(protocol::CdcMessageType type, std::uint16_t sequence,
               const std::uint8_t* payload, std::size_t size);

    link::AddressBook& book_;
    ByteSink& sink_;
    bool negotiated_ = false;
    std::uint32_t capabilities_ = 0;
    std::uint8_t pending_[kMaxWire] = {};
    std::size_t pending_size_ = 0;
    bool overflowed_ = false;
    std::uint8_t decoded_[protocol::ProtocolLimits::CDC_MAX_PAYLOAD] = {};
    std::uint8_t payload_[kMaxPayload] = {};
    std::uint8_t out_[2 * kMaxWire] = {};
    std::uint8_t scratch_[2 * kMaxWire] = {};
};

}  // namespace duo_input::u2
```

  Файл `firmware/u2_endpoint/address_service.cpp`:

```cpp
#include "address_service.hpp"

#include <cstring>

namespace duo_input::u2 {
namespace {

using protocol::CdcError;
using protocol::CdcMessageType;

constexpr std::uint8_t kFrameDelimiter = 0;

constexpr std::uint32_t kCapabilities =
    static_cast<std::uint32_t>(protocol::Capability::ADDRESS_EXCHANGE);

void put_u32(std::uint8_t* out, std::uint32_t value) {
    out[0] = static_cast<std::uint8_t>(value);
    out[1] = static_cast<std::uint8_t>(value >> 8);
    out[2] = static_cast<std::uint8_t>(value >> 16);
    out[3] = static_cast<std::uint8_t>(value >> 24);
}

}  // namespace

void AddressService::on_cdc_bytes(const std::uint8_t* data, std::size_t size) {
    for (std::size_t index = 0; index < size; ++index) {
        const std::uint8_t byte = data[index];
        if (byte == kFrameDelimiter) {
            if (!overflowed_ && pending_size_ > 0) {
                pending_[pending_size_++] = byte;
                handle_frame(pending_, pending_size_);
            }
            pending_size_ = 0;
            overflowed_ = false;
            continue;
        }
        if (pending_size_ + 1 < sizeof(pending_)) {
            pending_[pending_size_++] = byte;
        } else {
            overflowed_ = true;
        }
    }
}

void AddressService::on_disconnect() {
    pending_size_ = 0;
    overflowed_ = false;
    negotiated_ = false;
    capabilities_ = 0;
}

void AddressService::handle_frame(const std::uint8_t* wire, std::size_t size) {
    protocol::DecodeResult result;
    if (!protocol::decode_cdc_frame(protocol::ByteView{wire, size},
                                    protocol::MutableByteView{decoded_, sizeof(decoded_)},
                                    result)) {
        return;
    }
    const protocol::CdcFrame& frame = result.cdc;

    switch (frame.type) {
        case CdcMessageType::HELLO: {
            payload_[0] = static_cast<std::uint8_t>(CdcError::Ok);
            std::uint32_t granted = 0;
            if (frame.payload.size == 4) {
                const std::uint8_t* p = frame.payload.data;
                const std::uint32_t requested =
                    static_cast<std::uint32_t>(p[0]) | (static_cast<std::uint32_t>(p[1]) << 8) |
                    (static_cast<std::uint32_t>(p[2]) << 16) |
                    (static_cast<std::uint32_t>(p[3]) << 24);
                granted = requested & kCapabilities;
                negotiated_ = true;
                capabilities_ = granted;
            } else {
                payload_[0] = static_cast<std::uint8_t>(CdcError::InvalidRequest);
            }
            // The same 44 bytes U1 sends, so the configurator's parser reads
            // it unchanged: no configuration, so no generation, no profile and
            // an all-zero digest.
            payload_[1] = protocol::PROTOCOL_VERSION_MAJOR;
            payload_[2] = protocol::PROTOCOL_VERSION_MINOR;
            put_u32(payload_ + 3, granted);
            put_u32(payload_ + 7, 0);
            payload_[11] = 0;
            std::memset(payload_ + 12, 0, 32);
            reply(CdcMessageType::DEVICE_INFO, frame.sequence, payload_, 44);
            return;
        }
        case CdcMessageType::PING: {
            payload_[0] = static_cast<std::uint8_t>(CdcError::Ok);
            std::size_t size_out = 1;
            if (frame.payload.size < kMaxPayload) {
                std::memcpy(payload_ + 1, frame.payload.data, frame.payload.size);
                size_out += frame.payload.size;
            }
            reply(frame.type, frame.sequence, payload_, size_out);
            return;
        }
        case CdcMessageType::EXCHANGE_ADDRESSES: {
            if (!negotiated_) {
                payload_[0] = static_cast<std::uint8_t>(CdcError::BadState);
                reply(frame.type, frame.sequence, payload_, 1);
                return;
            }
            if ((capabilities_ & kCapabilities) == 0) {
                payload_[0] = static_cast<std::uint8_t>(CdcError::UnsupportedCapability);
                reply(frame.type, frame.sequence, payload_, 1);
                return;
            }
            link::HostAddresses local;
            if (!link::decode_host_addresses(frame.payload, local)) {
                payload_[0] = static_cast<std::uint8_t>(CdcError::InvalidRequest);
                reply(frame.type, frame.sequence, payload_, 1);
                return;
            }
            book_.set_local(local);
            payload_[0] = static_cast<std::uint8_t>(CdcError::Ok);
            std::size_t written = 0;
            link::encode_host_addresses(
                book_.peer(), protocol::MutableByteView{payload_ + 1, kMaxPayload - 1}, written);
            reply(frame.type, frame.sequence, payload_, 1 + written);
            return;
        }
        default:
            payload_[0] = static_cast<std::uint8_t>(CdcError::UnsupportedCapability);
            reply(frame.type, frame.sequence, payload_, 1);
            return;
    }
}

void AddressService::reply(CdcMessageType type, std::uint16_t sequence,
                           const std::uint8_t* payload, std::size_t size) {
    protocol::CdcFrame frame;
    frame.type = type;
    frame.sequence = sequence;
    frame.payload = protocol::ByteView{payload, size};
    std::size_t written = 0;
    if (protocol::encode_cdc_frame(frame, protocol::MutableByteView{out_, sizeof(out_)},
                                   protocol::MutableByteView{scratch_, sizeof(scratch_)},
                                   written)) {
        sink_.write(out_, written);
    }
}

}  // namespace duo_input::u2
```

  Разделитель включается в передаваемый кадр так же, как в `ConfigService::on_cdc_bytes` на U1 (`config_service.cpp:177-195`): там байт сначала дописывается в `pending_`, и только потом кадр уходит в `handle_frame`.

- [ ] **Step 4: Прогнать тесты.**

  Run: `cmake --build --preset native --clean-first` и `ctest --preset native -R address_service`
  Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add firmware/u2_endpoint/address_service.hpp firmware/u2_endpoint/address_service.cpp tests/firmware_native/test_address_service.cpp tests/firmware_native/CMakeLists.txt
git commit -m "feat(u2): CDC address service"
```

---

### Task 6: CDC-интерфейс и SPI-обмен на U2

**Files:**
- Modify:
  - `firmware/common/hid/report_ids.hpp` (`U2Interface`);
  - `firmware/u2_endpoint/usb_descriptors.cpp`;
  - `firmware/u2_endpoint/tusb_config.h`;
  - `firmware/u2_endpoint/usb_service.cpp`;
  - `firmware/u2_endpoint/spi_slave.hpp`, `firmware/u2_endpoint/spi_slave.cpp`;
  - `firmware/u2_endpoint/main.cpp`;
  - `firmware/u2_endpoint/CMakeLists.txt`.
- Test: `tests/build/test_usb_descriptors.py`

**Interfaces:**
- Consumes: `AddressService`, `ByteSink` (Task 5); `AddressBook` (Task 2).
- Produces: `void SpiSlave::set_address_book(link::AddressBook*)`.

- [ ] **Step 1: Изменить тест дескрипторов.** В `tests/build/test_usb_descriptors.py` заменить `test_u2_offers_the_same_hid_and_no_cdc` на:

```python
def test_u2_offers_the_same_hid_and_one_cdc_for_address_exchange(u2):
    classes = [interface["bInterfaceClass"] for interface in u2["configuration"]["interfaces"]]
    # U2 has no configuration; its serial port exists only so PC2's program can
    # swap addresses with PC1's. The HID part must stay exactly U1's - see
    # test_both_boards_describe_identical_input_devices.
    assert classes == [CLASS_HID, CLASS_HID, CLASS_HID, CLASS_CDC_CONTROL, CLASS_CDC_DATA]
    assert u2["configuration"]["bNumInterfaces"] == 5
```

- [ ] **Step 2: Убедиться, что тест падает.** Собрать текущую прошивку и запустить тест.

  Run: `cmake --build --preset pico-release --clean-first`, затем `.venv/Scripts/python.exe -m pytest tests/build/test_usb_descriptors.py -q`
  Expected: FAIL на новом тесте.

- [ ] **Step 3: Изменить USB-часть U2.**

  В `firmware/common/hid/report_ids.hpp`:

```cpp
enum class U2Interface : std::uint8_t {
    Keyboard = 0,
    Mouse = 1,
    Consumer = 2,
    CdcControl = 3,
    CdcData = 4,
    Count = 5,
};
```

  В `firmware/u2_endpoint/tusb_config.h`:
  - заменить `#define CFG_TUD_CDC 0` на `#define CFG_TUD_CDC 1`;
  - добавить:

```c
// Address exchange only: requests and replies are well under 128 bytes.
#define CFG_TUD_CDC_RX_BUFSIZE 256
#define CFG_TUD_CDC_TX_BUFSIZE 256
#define CFG_TUD_CDC_EP_BUFSIZE 64
```

  В `firmware/u2_endpoint/usb_descriptors.cpp`:
  - обновить шапочный комментарий: три HID-интерфейса, как у U1, плюс CDC только для обмена адресами;
  - `DUO_U2_CONFIG_TOTAL_LEN` → `(TUD_CONFIG_DESC_LEN + 3 * TUD_HID_DESC_LEN + TUD_CDC_DESC_LEN)`;
  - в `desc_configuration` после consumer добавить:

```cpp
    TUD_CDC_DESCRIPTOR(static_cast<std::uint8_t>(hid::U2Interface::CdcControl), 7,
                       hid::kEpCdcNotifyIn, 8, hid::kEpCdcDataOut, hid::kEpCdcDataIn, 64),
```

  - в `string_table` добавить `"Duo Input Address Exchange",  // 7`.

  В `firmware/u2_endpoint/usb_service.cpp` добавить `#include "pico/bootrom.h"` и сброс в загрузчик на 1200 бод. Скопировать `tud_cdc_line_state_cb` из `firmware/u1_main/usb_service.cpp:117-127` вместе с комментарием.

  В `firmware/u2_endpoint/CMakeLists.txt`:
  - добавить `address_service.cpp` в `add_executable`;
  - добавить `pico_bootrom` в `target_link_libraries`;
  - шапочный комментарий заменить на `# U2: the second computer's endpoint. Its only CDC traffic is the address exchange; everything it emits as HID arrives over SPI from U1.`

- [ ] **Step 4: Подключить SPI-обмен в `SpiSlave`.**

  В `spi_slave.hpp`:
  - добавить `#include "link/host_addresses.hpp"`;
  - в публичную часть добавить `void set_address_book(link::AddressBook* book) { addresses_ = book; }`;
  - в приватные члены добавить `link::AddressBook* addresses_ = nullptr;`.

  В `spi_slave.cpp` в `prime()` перед текущим построением `ENDPOINT_STATUS` вставить:

```cpp
    // Once in kEndpointAddressesEvery replies, and only once PC2 has said
    // where it is, this reply carries PC2's addresses instead of the status.
    // The status loses nothing it cannot afford: it is absolute and repeats
    // on the very next transfer.
    if (addresses_ != nullptr && addresses_->take_reply_slot()) {
        protocol::SpiFrame frame;
        frame.type = protocol::SpiMessageType::ENDPOINT_ADDRESSES;
        frame.sequence = reply_sequence_++;
        std::uint8_t list[link::kHostAddressesMaxSize];
        std::size_t size = 0;
        link::encode_host_addresses(addresses_->local(),
                                    protocol::MutableByteView{list, sizeof(list)}, size);
        frame.payload = protocol::ByteView{list, size};
        std::size_t written = 0;
        std::memset(tx_, 0, sizeof(tx_));
        protocol::encode_spi_frame(frame, protocol::MutableByteView{tx_, sizeof(tx_)}, written);
        return;
    }
```

  **Проверить при реализации.** Блок `#if DUO_SPI_DEBUG` в конце `prime()` переписывает `tx_` счётным шаблоном. Ранний `return` не должен его обходить. Если обходит, вынести этот блок в начало `prime()` с собственным `return` или обернуть новую ветку в `#if !DUO_SPI_DEBUG`.

- [ ] **Step 5: Подключить в `main.cpp` U2.**
  - Добавить `#include "tusb.h"`, `#include "address_service.hpp"`, `#include "link/host_addresses.hpp"`.
  - В `apply()` добавить ветку до `default:`. Для этого добавить параметр `duo_input::link::AddressBook& addresses` и обновить единственный вызов `apply(frame, outputs)` на `apply(frame, outputs, addresses)`:

```cpp
        case duo_input::protocol::SpiMessageType::HOST_ADDRESSES:
            // PC1's addresses, for PC2's program to read over CDC.
            addresses.accept_peer(payload);
            return;
```

  - Перед `int main()`, в анонимном namespace, добавить писатель:

```cpp
class CdcWriter : public duo_input::u2::ByteSink {
public:
    void write(const std::uint8_t* data, std::size_t size) override {
        // Same rule as U1: written whether or not DTR is up, because
        // QSerialPort does not raise it on open.
        tud_cdc_write(data, static_cast<std::uint32_t>(size));
        tud_cdc_write_flush();
    }
};
```

  - В `main()` после создания `link` добавить:

```cpp
    static duo_input::link::AddressBook addresses;
    static CdcWriter cdc_writer;
    static duo_input::u2::AddressService address_service(addresses, cdc_writer);
    link.set_address_book(&addresses);
```

  - В цикле после `usb.task();` добавить:

```cpp
        if (tud_cdc_available()) {
            std::uint8_t incoming[64];
            const std::uint32_t read = tud_cdc_read(incoming, sizeof(incoming));
            address_service.on_cdc_bytes(incoming, read);
        }
```

  - В ветке `if (mounted != was_mounted)` добавить `if (!mounted) { address_service.on_disconnect(); }`.

- [ ] **Step 6: Собрать и прогнать тесты дескрипторов.**

  Run: `cmake --build --preset pico-release --clean-first`, затем `.venv/Scripts/python.exe -m pytest tests/build -q`
  Expected: PASS, в том числе `test_both_boards_describe_identical_input_devices`.

  Также проверить PIO-сборку: `cmake --preset pico-pio-usb-release` и `cmake --build --preset pico-pio-usb-release --clean-first`, если на машине настроен toolchain (`.deps/`). Если не настроен, отметить в отчёте, что этот шаг пропущен.

- [ ] **Step 7: Прогнать нативные тесты** (они компилируют `report_ids.hpp`).

  Run: `cmake --build --preset native --clean-first` и `ctest --preset native`
  Expected: всё зелёное.

- [ ] **Step 8: Commit.**

```bash
git add firmware/common/hid/report_ids.hpp firmware/u2_endpoint tests/build/test_usb_descriptors.py
git commit -m "feat(u2): CDC interface and SPI address exchange"
```

---

### Task 7: Python-кодек и эмулятор

**Files:**
- Create: `configurator/src/duo_input/device/host_addresses.py`
- Modify: `configurator/src/duo_input/device/emulator.py`
- Test: `configurator/tests/device/test_host_addresses.py` (новый), `configurator/tests/test_device_emulator.py`

**Interfaces:**
- Produces:
  - `MAX_HOST_ADDRESSES = 8`;
  - `encode_host_addresses(addresses: list[str]) -> bytes`: берёт первые 8, пропускает неразборчивые и `0.0.0.0`;
  - `decode_host_addresses(payload: bytes) -> list[str]`: при нарушении формата бросает `PayloadError` из `duo_input.device.transactions`;
  - в `U1Emulator`: `set_peer_addresses(list[str])` и свойство `local_addresses -> list[str]`.

- [ ] **Step 1: Написать падающие тесты.** Файл `configurator/tests/device/test_host_addresses.py`:

```python
"""The wire form of a list of IPv4 addresses, the same on CDC and SPI."""

from __future__ import annotations

import pytest

from duo_input.device.host_addresses import (
    MAX_HOST_ADDRESSES,
    decode_host_addresses,
    encode_host_addresses,
)
from duo_input.device.transactions import PayloadError


def test_a_list_round_trips():
    wire = encode_host_addresses(["192.168.1.7", "10.0.0.2"])
    assert wire == bytes([2, 192, 168, 1, 7, 10, 0, 0, 2])
    assert decode_host_addresses(wire) == ["192.168.1.7", "10.0.0.2"]


def test_an_empty_list_is_one_byte():
    assert encode_host_addresses([]) == b"\x00"
    assert decode_host_addresses(b"\x00") == []


def test_only_the_first_eight_are_sent():
    many = [f"10.0.0.{n}" for n in range(1, 12)]
    assert decode_host_addresses(encode_host_addresses(many)) == many[:MAX_HOST_ADDRESSES]


def test_unusable_entries_are_left_out_rather_than_sent():
    assert encode_host_addresses(["nonsense", "0.0.0.0", "::1", "192.168.1.7"]) == bytes(
        [1, 192, 168, 1, 7]
    )


@pytest.mark.parametrize(
    "payload",
    [b"", bytes([1, 192, 168, 1]), bytes([1, 192, 168, 1, 7, 0]), bytes([9]) + bytes(36), bytes([1, 0, 0, 0, 0])],
)
def test_a_malformed_list_is_refused(payload):
    with pytest.raises(PayloadError):
        decode_host_addresses(payload)
```

  В `configurator/tests/test_device_emulator.py` добавить (посмотреть там, как уже устроены обмены с эмулятором через `exchange(CdcFrame(...))`, и следовать этому):

```python
def test_the_emulator_exchanges_addresses_like_the_board():
    from duo_input.device.host_addresses import decode_host_addresses, encode_host_addresses
    from duo_input.generated.protocol import CdcMessageType
    from duo_input.protocol.frame import CdcFrame
    import struct

    emulator = U1Emulator()
    emulator.exchange(CdcFrame(CdcMessageType.HELLO, 0, struct.pack("<I", 0xFFFFFFFF)))
    emulator.set_peer_addresses(["10.0.0.2"])

    reply = emulator.exchange(
        CdcFrame(CdcMessageType.EXCHANGE_ADDRESSES, 1, encode_host_addresses(["192.168.1.7"]))
    )

    assert reply.type is CdcMessageType.EXCHANGE_ADDRESSES
    assert reply.payload[0] == 0
    assert decode_host_addresses(bytes(reply.payload[1:])) == ["10.0.0.2"]
    assert emulator.local_addresses == ["192.168.1.7"]
```

- [ ] **Step 2: Убедиться, что тесты падают.**

  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/device/test_host_addresses.py configurator/tests/test_device_emulator.py -q`
  Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Реализовать.** Файл `configurator/src/duo_input/device/host_addresses.py`:

```python
"""The wire form of a list of IPv4 addresses.

The same bytes travel in EXCHANGE_ADDRESSES over CDC and in HOST_ADDRESSES /
ENDPOINT_ADDRESSES over SPI: a count byte, then that many four-byte addresses
in network order. firmware/common/link/host_addresses.cpp is the other half,
and the two refuse exactly the same payloads.
"""

from __future__ import annotations

import ipaddress

from .transactions import PayloadError

MAX_HOST_ADDRESSES = 8


def _usable(address: str) -> ipaddress.IPv4Address | None:
    try:
        parsed = ipaddress.IPv4Address(address)
    except ValueError:
        return None
    return None if parsed.is_unspecified else parsed


def encode_host_addresses(addresses: list[str]) -> bytes:
    """Encode up to eight usable IPv4 addresses; anything else is left out."""
    usable = [parsed for parsed in map(_usable, addresses) if parsed is not None]
    usable = usable[:MAX_HOST_ADDRESSES]
    return bytes([len(usable)]) + b"".join(parsed.packed for parsed in usable)


def decode_host_addresses(payload: bytes) -> list[str]:
    if not payload:
        raise PayloadError("address list is empty")
    count = payload[0]
    if count > MAX_HOST_ADDRESSES:
        raise PayloadError(f"address list claims {count} entries")
    if len(payload) != 1 + 4 * count:
        raise PayloadError(f"address list of {count} is {len(payload)} bytes")
    addresses = []
    for index in range(count):
        parsed = ipaddress.IPv4Address(payload[1 + 4 * index : 5 + 4 * index])
        if parsed.is_unspecified:
            raise PayloadError("address list carries 0.0.0.0")
        addresses.append(str(parsed))
    return addresses


__all__ = ["MAX_HOST_ADDRESSES", "decode_host_addresses", "encode_host_addresses"]
```

  В `emulator.py`:
  - в `__init__` добавить `self._local_addresses: list[str] = []` и `self._peer_addresses: list[str] = []`;
  - добавить публичные `set_peer_addresses(self, addresses: list[str]) -> None` и свойство `local_addresses`;
  - заменить заглушку из Task 1:

```python
    def _handle_exchange_addresses(self, payload: bytes) -> bytes:
        try:
            local = decode_host_addresses(payload)
        except PayloadError:
            return bytes((ErrorCode.INVALID_REQUEST,))
        self._local_addresses = local
        return bytes((ErrorCode.OK,)) + encode_host_addresses(self._peer_addresses)
```

  (импорты: `from .host_addresses import decode_host_addresses, encode_host_addresses` и `from .transactions import PayloadError`; проверить, что `PayloadError` ещё не импортирован).

- [ ] **Step 4: Прогнать тесты.**

  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/device configurator/tests/test_device_emulator.py -q`
  Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add configurator/src/duo_input/device/host_addresses.py configurator/src/duo_input/device/emulator.py configurator/tests/device/test_host_addresses.py configurator/tests/test_device_emulator.py
git commit -m "feat(configurator): host address codec and emulator support"
```

---

### Task 8: Тихая операция `DeviceService.exchange_addresses`

**Files:**
- Modify: `configurator/src/duo_input/device/service.py`
- Test: `configurator/tests/device/test_device_service.py`

**Interfaces:**
- Consumes: `encode_host_addresses`, `decode_host_addresses` (Task 7).
- Produces:
  - `DeviceService.exchange_addresses(local: list[str]) -> bool`;
  - сигнал `DeviceService.peer_addresses_received = Signal(list)`.
- Контракт:
  - Возвращает `False` и ничего не шлёт, если нет связи, идёт другая операция или плата не подтвердила `ADDRESS_EXCHANGE`.
  - Не меняет `state` и не испускает `operation_succeeded` / `operation_failed`.
  - Пользовательская операция, начатая во время обмена, откладывается и запускается сразу после его завершения.

- [ ] **Step 1: Написать падающие тесты.** В `test_device_service.py` уже есть фикстуры `service` (с `timeout_ms=5000`) и `emulator` и хелпер `_connect(qtbot, service, transport)`. `SynchronousTransportLink` отвечает через `QTimer.singleShot(0)`, поэтому между запросом и ответом управление возвращается в цикл событий, и тест на откладывание действительно ловит момент «обмен ещё идёт». Добавить в конец файла:

```python
@pytest.fixture
def connected(qtbot, service, emulator):
    _connect(qtbot, service, emulator)
    return service, emulator


def test_an_exchange_returns_the_peers_addresses_quietly(qtbot, connected):
    service, emulator = connected
    emulator.set_peer_addresses(["10.0.0.2"])
    states, succeeded, failed, received = [], [], [], []
    service.state_changed.connect(states.append)
    service.operation_succeeded.connect(succeeded.append)
    service.operation_failed.connect(failed.append)
    service.peer_addresses_received.connect(received.append)

    assert service.exchange_addresses(["192.168.1.7"]) is True
    qtbot.waitUntil(lambda: received == [["10.0.0.2"]])

    assert emulator.local_addresses == ["192.168.1.7"]
    assert states == [] and succeeded == [] and failed == []


def test_no_exchange_is_attempted_without_a_link():
    from duo_input.device.service import DeviceService

    assert DeviceService().exchange_addresses(["192.168.1.7"]) is False


def test_no_exchange_is_attempted_while_another_operation_runs(qtbot, connected):
    service, _ = connected
    service.get_diagnostics()
    assert service.exchange_addresses(["192.168.1.7"]) is False


def test_a_user_operation_started_during_an_exchange_waits_instead_of_failing(qtbot, connected):
    service, _ = connected
    failed, succeeded = [], []
    service.operation_failed.connect(failed.append)
    service.operation_succeeded.connect(succeeded.append)

    assert service.exchange_addresses(["192.168.1.7"]) is True
    service.get_diagnostics()  # в тот же миг, пока обмен ещё не ответил
    assert len(service._deferred_calls) == 1

    qtbot.waitUntil(lambda: len(succeeded) == 1)
    assert failed == []
    assert succeeded[0].operation == "get_diagnostics"


def test_a_board_without_the_capability_is_not_asked(qtbot, connected, monkeypatch):
    service, _ = connected
    from dataclasses import replace
    from duo_input.generated.protocol import Capability

    info = service.device_info
    monkeypatch.setattr(
        service,
        "_device_info",
        replace(info, capabilities=info.capabilities & ~int(Capability.ADDRESS_EXCHANGE)),
    )
    assert service.exchange_addresses(["192.168.1.7"]) is False


def test_a_failed_exchange_is_silent_and_frees_the_service(qtbot, connected):
    service, emulator = connected
    failed = []
    service.operation_failed.connect(failed.append)
    emulator.inject_timeout()

    assert service.exchange_addresses(["192.168.1.7"]) is True
    # Ответа не будет; по таймауту сервиса (5 с) обмен молча снимается.
    qtbot.waitUntil(lambda: service.exchange_addresses(["192.168.1.7"]) is True, timeout=8000)
    assert failed == []
```

  (`DeviceInfo` — `@dataclass(frozen=True)`, поэтому `dataclasses.replace` подходит.)

- [ ] **Step 2: Убедиться, что тесты падают.**

  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/device/test_device_service.py -q -k "exchange or during"`
  Expected: FAIL (`AttributeError: exchange_addresses`).

- [ ] **Step 3: Реализовать в `service.py`.**
  - Добавить импорт `from .host_addresses import decode_host_addresses, encode_host_addresses`.
  - Добавить константу `_EXCHANGE_ADDRESSES = "exchange_addresses"`.
  - Добавить сигнал `peer_addresses_received = Signal(list)`.
  - В `__init__` добавить `self._deferred_calls: list = []`.
  - Добавить публичный метод:

```python
    def exchange_addresses(self, local: list[str]) -> bool:
        """Hand the board this computer's addresses and ask for the other's.

        A background chore, not an operation the operator started: it runs
        only when the service is idle, never changes ``state`` and never
        reports through ``operation_succeeded``/``operation_failed``. The
        answer arrives as ``peer_addresses_received``; a failure is dropped,
        because the next tick asks again.
        """
        if self._link is None or not self._link.is_open:
            return False
        if self._operation is not None or self._pending is not None:
            return False
        info = self._device_info
        if info is None or not info.capabilities & int(Capability.ADDRESS_EXCHANGE):
            return False
        self._operation = _EXCHANGE_ADDRESSES
        self._request(
            CdcMessageType.EXCHANGE_ADDRESSES,
            CdcMessageType.EXCHANGE_ADDRESSES,
            encode_host_addresses(local),
            self._on_addresses_exchanged,
        )
        return True

    def _on_addresses_exchanged(self, payload: bytes) -> None:
        peers = decode_host_addresses(payload[1:])
        self._operation = None
        self._run_deferred()
        self.peer_addresses_received.emit(peers)

    def _defer_behind_exchange(self, call) -> bool:
        """An operator's request that arrives mid-exchange waits for it.

        The exchange is a few milliseconds long; refusing the operator with
        BUSY over a chore they never asked for would be a failure they could
        not explain.
        """
        if self._operation != _EXCHANGE_ADDRESSES:
            return False
        self._deferred_calls.append(call)
        return True

    def _run_deferred(self) -> None:
        calls, self._deferred_calls = self._deferred_calls, []
        for call in calls:
            call()
```

  - В начало каждого из методов `read_config`, `write_config`, `begin_capture`, `test_macro`, `stop_and_release_all`, `get_diagnostics` добавить строку отложенного вызова с аргументами метода:
    - `read_config`: `if self._defer_behind_exchange(self.read_config): return`;
    - `write_config`: `if self._defer_behind_exchange(lambda: self.write_config(package)): return`;
    - `test_macro`: `if self._defer_behind_exchange(lambda: self.test_macro(profile_id, macro_id)): return`;
    - остальные — так же, как `read_config`.
  - В `_finish_failure` в самое начало добавить:

```python
        if failure.operation == _EXCHANGE_ADDRESSES:
            self._operation = None
            self._run_deferred()
            return
```

  - В `_on_link_lost` после `operation = self._operation` добавить:

```python
        if operation == _EXCHANGE_ADDRESSES:
            operation = None
```

    В конце метода вызвать `self._run_deferred()`, чтобы отложенные операции получили честный `NOT_CONNECTED`.
  - В `disconnect_device` добавить `self._deferred_calls.clear()`.

- [ ] **Step 4: Прогнать тесты.**

  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/device configurator/tests/ui -q`
  Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add configurator/src/duo_input/device/service.py configurator/tests/device
git commit -m "feat(configurator): quiet address exchange on DeviceService"
```

---

### Task 9: `EndpointService` для U2 на ПК2

**Files:**
- Modify: `configurator/src/duo_input/device/discovery.py` (`matches_u2`, `find_u2_ports`)
- Create: `configurator/src/duo_input/device/endpoint_service.py`
- Test: `configurator/tests/device/test_discovery.py`, `configurator/tests/device/test_endpoint_service.py` (новый)

**Interfaces:**
- Consumes: `FrameAssembler`, `SequenceGenerator`, `parse_device_info`, `reply_error`, `PayloadError` из `transactions`; `encode_cdc_frame`, `CdcFrame`; кодек из Task 7.
- Produces:
  - `matches_u2(port_info) -> bool`, `find_u2_ports(port_infos=None) -> tuple[PortCandidate, ...]`;
  - `EndpointService(link_factory: Callable[[], object | None] | None = None, timeout_ms: int = 2000, parent=None)`;
  - `.exchange_addresses(local: list[str]) -> bool`, `.stop()`;
  - сигнал `peer_addresses_received = Signal(list)`;
  - свойство `unsupported -> bool`.
  - `link_factory` возвращает незакрытую связь (`QSerialPortTransport` или `SynchronousTransportLink`) либо `None`, если U2 не найдена. По умолчанию открывает первый порт из `find_u2_ports()`.

- [ ] **Step 1: Написать падающие тесты.** В `test_discovery.py` добавить тест по образцу уже существующего теста `matches_u1`, который фейковым `port_info` с PID `U2_IDENTITY` проверяет:
  - `matches_u2(...) is True`;
  - `matches_u1(...) is False`;
  - `find_u2_ports([...])` возвращает один кандидат.

  Файл `configurator/tests/device/test_endpoint_service.py`:

```python
"""ПК2 разговаривает со своей U2 ровно об одном - об адресах."""

from __future__ import annotations

from duo_input.device.emulator import U1Emulator
from duo_input.device.endpoint_service import EndpointService
from duo_input.device.qt_transport import SynchronousTransportLink


def _service_with(emulator, qtbot):
    links = []

    def factory():
        link = SynchronousTransportLink(emulator)
        links.append(link)
        return link

    service = EndpointService(link_factory=factory, timeout_ms=500)
    return service, links


def test_the_first_call_opens_the_board_and_the_answer_follows(qtbot):
    # U1Emulator говорит тем же кадрированием и отвечает на HELLO и
    # EXCHANGE_ADDRESSES так же, как U2 - для этого сервиса разницы нет.
    emulator = U1Emulator()
    emulator.set_peer_addresses(["192.168.1.7"])
    service, links = _service_with(emulator, qtbot)
    received = []
    service.peer_addresses_received.connect(received.append)

    service.exchange_addresses(["10.0.0.2"])

    qtbot.waitUntil(lambda: received == [["192.168.1.7"]])
    assert emulator.local_addresses == ["10.0.0.2"]
    assert len(links) == 1


def test_no_board_means_no_attempt_and_no_error(qtbot):
    service = EndpointService(link_factory=lambda: None)
    assert service.exchange_addresses(["10.0.0.2"]) is False


def test_a_board_without_the_capability_is_left_alone(qtbot, monkeypatch):
    import duo_input.device.emulator as emulator_module
    from duo_input.generated.protocol import Capability

    monkeypatch.setattr(
        emulator_module,
        "DEVICE_CAPABILITIES",
        emulator_module.DEVICE_CAPABILITIES & ~int(Capability.ADDRESS_EXCHANGE),
    )
    emulator = U1Emulator()
    service, links = _service_with(emulator, qtbot)

    service.exchange_addresses(["10.0.0.2"])
    qtbot.waitUntil(lambda: service.unsupported)

    assert service.exchange_addresses(["10.0.0.2"]) is False
    assert len(links) == 1


def test_a_lost_board_is_reopened_on_the_next_call(qtbot):
    emulator = U1Emulator()
    service, links = _service_with(emulator, qtbot)
    received = []
    service.peer_addresses_received.connect(received.append)
    service.exchange_addresses(["10.0.0.2"])
    qtbot.waitUntil(lambda: len(received) == 1)

    links[0].link_lost.emit("unplugged")
    service.exchange_addresses(["10.0.0.2"])

    qtbot.waitUntil(lambda: len(received) == 2)
    assert len(links) == 2
```

- [ ] **Step 2: Убедиться, что тесты падают.**

  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/device -q`
  Expected: FAIL (`ModuleNotFoundError: endpoint_service`, `ImportError: matches_u2`).

- [ ] **Step 3: Реализовать.**

  В `discovery.py` рядом с U1-функциями добавить `matches_u2` и `find_u2_ports` (тела как у U1, но с `U2_IDENTITY`) и дописать оба имени в `__all__`.

  Файл `configurator/src/duo_input/device/endpoint_service.py`:

```python
"""PC2's conversation with U2: this computer's addresses for PC1's.

U2 answers nothing else - it holds no configuration - so this is not a
DeviceService and never appears in the main window. It opens U2 when asked,
negotiates once, and from then on each ``exchange_addresses`` is one request
and one reply. A board that does not grant ADDRESS_EXCHANGE is closed and
never asked again; one that disappears is reopened on the next call.
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from duo_input.generated.protocol import PROTOCOL_VERSION_MAJOR, Capability, CdcMessageType
from duo_input.protocol.frame import CdcFrame, encode_cdc_frame

from .host_addresses import decode_host_addresses, encode_host_addresses
from .transactions import (
    ErrorCode,
    FrameAssembler,
    FrameOverflowError,
    PayloadError,
    SequenceGenerator,
    parse_device_info,
    reply_error,
)

logger = logging.getLogger("duo_input.device.endpoint")


def default_link_factory():
    from .discovery import find_u2_ports
    from .qt_transport import QSerialPortTransport

    ports = find_u2_ports()
    return QSerialPortTransport(ports[0].port_name) if ports else None


class EndpointService(QObject):
    peer_addresses_received = Signal(list)

    def __init__(
        self,
        link_factory: Callable[[], object | None] | None = None,
        timeout_ms: int = 2000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._factory = link_factory or default_link_factory
        self._link = None
        self._ready = False
        self._unsupported = False
        self._pending: tuple[int, CdcMessageType] | None = None
        self._queued_local: list[str] | None = None
        self._assembler = FrameAssembler()
        self._sequence = SequenceGenerator()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_ms)
        self._timer.timeout.connect(lambda: self._close("no answer"))

    @property
    def unsupported(self) -> bool:
        return self._unsupported

    def exchange_addresses(self, local: list[str]) -> bool:
        if self._unsupported or self._pending is not None:
            return False
        if self._link is None:
            if not self._open():
                return False
            self._queued_local = list(local)
            return True
        if not self._ready:
            return False
        self._send(CdcMessageType.EXCHANGE_ADDRESSES, encode_host_addresses(local))
        return True

    def stop(self) -> None:
        self._close("")

    # ------------------------------------------------------------------ link

    def _open(self) -> bool:
        link = self._factory()
        if link is None:
            return False
        link.bytes_received.connect(self._on_bytes)
        link.link_lost.connect(self._close)
        if not link.open():
            link.bytes_received.disconnect(self._on_bytes)
            link.link_lost.disconnect(self._close)
            return False
        self._link = link
        self._ready = False
        self._assembler.clear()
        self._sequence = SequenceGenerator()
        self._send(CdcMessageType.HELLO, struct.pack("<I", int(Capability.ADDRESS_EXCHANGE)))
        return True

    def _close(self, reason: str = "") -> None:
        self._timer.stop()
        self._pending = None
        self._ready = False
        link, self._link = self._link, None
        if link is None:
            return
        if reason:
            logger.info("u2_link_closed reason=%s", reason)
        for signal, slot in ((link.bytes_received, self._on_bytes), (link.link_lost, self._close)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):  # pragma: no cover - already detached
                pass
        link.close()

    def _send(self, request: CdcMessageType, payload: bytes) -> None:
        sequence = self._sequence.next()
        reply = CdcMessageType.DEVICE_INFO if request is CdcMessageType.HELLO else request
        self._pending = (sequence, reply)
        self._timer.start()
        self._link.send(encode_cdc_frame(CdcFrame(request, sequence, payload)))

    def _on_bytes(self, data: bytes) -> None:
        try:
            scan = self._assembler.push(bytes(data))
        except FrameOverflowError:
            self._close("frame overflow")
            return
        for frame in scan.frames:
            self._on_frame(frame)
            if self._link is None:
                return

    def _on_frame(self, frame: CdcFrame) -> None:
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
            else:
                self._on_exchange(payload)
        except PayloadError as error:
            self._close(str(error))

    def _on_hello(self, payload: bytes) -> None:
        info = parse_device_info(payload)
        granted = info.capabilities & int(Capability.ADDRESS_EXCHANGE)
        if info.protocol_major != PROTOCOL_VERSION_MAJOR or not granted:
            logger.warning("плата U2 не поддерживает обмен адресами")
            self._unsupported = True
            self._close("")
            return
        self._ready = True
        local, self._queued_local = self._queued_local, None
        if local is not None:
            self._send(CdcMessageType.EXCHANGE_ADDRESSES, encode_host_addresses(local))

    def _on_exchange(self, payload: bytes) -> None:
        if reply_error(payload) is not ErrorCode.OK:
            return
        self.peer_addresses_received.emit(decode_host_addresses(payload[1:]))


__all__ = ["EndpointService", "default_link_factory"]
```

  **Проверить при реализации.** `DeviceInfo` у `parse_device_info` использует поля `protocol_major` и `capabilities` (так их читает `DeviceService._on_hello`). Если имена другие, взять имена из `transactions.py`.

- [ ] **Step 4: Прогнать тесты.**

  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/device -q`
  Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add configurator/src/duo_input/device/discovery.py configurator/src/duo_input/device/endpoint_service.py configurator/tests/device
git commit -m "feat(configurator): EndpointService talks to U2 about addresses"
```

---

### Task 10: Сбор и фильтр своих адресов

**Files:**
- Create: `configurator/src/duo_input/clipboard/local_addresses.py`
- Test: `configurator/tests/clipboard/test_local_addresses.py`

**Interfaces:**
- Produces:
  - `InterfaceEntry(name: str, kind: str, up: bool, running: bool, loopback: bool, addresses: tuple[str, ...])`, где `kind` принимает значения `"ethernet"`, `"wifi"`, `"virtual"`, `"other"`;
  - `select_addresses(entries: Iterable[InterfaceEntry]) -> list[str]` — чистая функция;
  - `local_ipv4_addresses() -> list[str]` — адаптер над `QNetworkInterface`.

- [ ] **Step 1: Написать падающие тесты.**

```python
"""Какие свои адреса стоит сообщать второму компьютеру, а какие нет."""

from __future__ import annotations

from duo_input.clipboard.local_addresses import InterfaceEntry, select_addresses


def _nic(name, kind, *addresses, up=True, running=True, loopback=False):
    return InterfaceEntry(name, kind, up, running, loopback, tuple(addresses))


def test_ethernet_comes_before_wifi_and_wifi_before_the_rest():
    entries = [
        _nic("Tailscale", "other", "100.64.0.5"),
        _nic("Wi-Fi", "wifi", "192.168.1.20"),
        _nic("Ethernet", "ethernet", "192.168.1.10"),
    ]
    assert select_addresses(entries) == ["192.168.1.10", "192.168.1.20", "100.64.0.5"]


def test_loopback_link_local_and_unspecified_are_never_offered():
    entries = [
        _nic("lo", "other", "127.0.0.1", loopback=True),
        _nic("Ethernet", "ethernet", "169.254.3.4", "0.0.0.0", "127.0.0.2", "192.168.1.10"),
    ]
    assert select_addresses(entries) == ["192.168.1.10"]


def test_virtual_adapters_are_left_out_even_when_windows_calls_them_ethernet():
    entries = [
        _nic("vEthernet (WSL)", "ethernet", "172.20.0.1"),
        _nic("VirtualBox Host-Only Network", "ethernet", "192.168.56.1"),
        _nic("VMware Network Adapter VMnet8", "ethernet", "192.168.80.1"),
        _nic("WSL", "ethernet", "172.21.0.1"),
        _nic("docker0", "virtual", "172.17.0.1"),
        _nic("Ethernet", "ethernet", "192.168.1.10"),
    ]
    assert select_addresses(entries) == ["192.168.1.10"]


def test_an_interface_that_is_down_or_not_running_is_skipped():
    entries = [
        _nic("Ethernet", "ethernet", "192.168.1.10", up=False),
        _nic("Ethernet 2", "ethernet", "192.168.1.11", running=False),
        _nic("Wi-Fi", "wifi", "192.168.1.20"),
    ]
    assert select_addresses(entries) == ["192.168.1.20"]


def test_ipv6_is_ignored_and_duplicates_collapse_and_eight_is_the_limit():
    entries = [_nic("Ethernet", "ethernet", "fe80::1", "192.168.1.10", "192.168.1.10")]
    entries += [_nic(f"Wi-Fi {n}", "wifi", f"10.0.0.{n}") for n in range(1, 12)]
    result = select_addresses(entries)
    assert result[0] == "192.168.1.10"
    assert len(result) == 8
    assert len(set(result)) == 8
```

- [ ] **Step 2: Убедиться, что тесты падают.**

  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/clipboard/test_local_addresses.py -q`
  Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Реализовать.**

```python
"""Свои IPv4-адреса, которые имеет смысл сообщить второму компьютеру.

Отфильтровано всё, по чему второй компьютер заведомо не дозвонится:
loopback, link-local (169.254.x - адрес, который Windows выдаёт сама себе, когда
DHCP не ответил), неподнятые интерфейсы и виртуальные адаптеры. Qt на Windows
называет адаптеры Hyper-V/WSL/VirtualBox/VMware обычным Ethernet, поэтому их
отсекаем по имени. Порядок - проводная сеть, затем Wi-Fi, затем остальное
(VPN вроде Tailscale): второй компьютер пробует адреса именно в этом порядке.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from dataclasses import dataclass

from duo_input.device.host_addresses import MAX_HOST_ADDRESSES

_VIRTUAL_NAME_PREFIXES = ("vethernet", "virtualbox", "vmware", "wsl")
_ORDER = {"ethernet": 0, "wifi": 1, "other": 2}


@dataclass(frozen=True)
class InterfaceEntry:
    name: str
    kind: str
    up: bool
    running: bool
    loopback: bool
    addresses: tuple[str, ...]


def _reachable(address: str) -> bool:
    try:
        parsed = ipaddress.IPv4Address(address)
    except ValueError:
        return False
    return not (parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified)


def select_addresses(entries: Iterable[InterfaceEntry]) -> list[str]:
    usable = [
        entry
        for entry in entries
        if entry.up
        and entry.running
        and not entry.loopback
        and entry.kind != "virtual"
        and not entry.name.lower().startswith(_VIRTUAL_NAME_PREFIXES)
    ]
    # sorted() устойчива: внутри группы остаётся порядок перечисления.
    usable.sort(key=lambda entry: _ORDER.get(entry.kind, 2))
    result: list[str] = []
    for entry in usable:
        for address in entry.addresses:
            if _reachable(address) and address not in result:
                result.append(address)
    return result[:MAX_HOST_ADDRESSES]


def local_ipv4_addresses() -> list[str]:
    from PySide6.QtNetwork import QAbstractSocket, QNetworkInterface

    kinds = {
        QNetworkInterface.InterfaceType.Ethernet: "ethernet",
        QNetworkInterface.InterfaceType.Wifi: "wifi",
        QNetworkInterface.InterfaceType.Virtual: "virtual",
    }
    flags = QNetworkInterface.InterfaceFlag
    entries = []
    for interface in QNetworkInterface.allInterfaces():
        state = interface.flags()
        entries.append(
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
            )
        )
    return select_addresses(entries)


__all__ = ["InterfaceEntry", "local_ipv4_addresses", "select_addresses"]
```

- [ ] **Step 4: Прогнать тесты.** Затем один раз вызвать адаптер на этой машине и посмотреть, что он возвращает.

  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/clipboard/test_local_addresses.py -q`
  Run: `.venv/Scripts/python.exe -c "from duo_input.clipboard.local_addresses import local_ipv4_addresses as f; print(f())"` из `configurator/src` (или с `PYTHONPATH=configurator/src`).
  Expected: тесты PASS. Адаптер печатает список, совпадающий с IPv4 из `ipconfig` без виртуальных адаптеров. Результат записать в отчёт задачи.

- [ ] **Step 5: Commit.**

```bash
git add configurator/src/duo_input/clipboard/local_addresses.py configurator/tests/clipboard/test_local_addresses.py
git commit -m "feat(clipboard): collect and filter this computer's IPv4 addresses"
```

---

### Task 11: `AddressExchange` — периодический обмен

**Files:**
- Create: `configurator/src/duo_input/clipboard/address_exchange.py`
- Test: `configurator/tests/clipboard/test_address_exchange.py`

**Interfaces:**
- Consumes: всё, у чего есть `exchange_addresses(list[str]) -> bool` и `peer_addresses_received: Signal(list)` (`DeviceService`, `EndpointService`).
- Produces:
  - `EXCHANGE_INTERVAL_MS = 5000`;
  - `AddressExchange(backends: list, local_addresses: Callable[[], list[str]], parent=None)` с методами `.start()`, `.stop()`, `.tick()` и свойством `.peer_addresses -> list[str]`;
  - сигнал `peer_addresses_changed = Signal(list)`.

- [ ] **Step 1: Написать падающие тесты.**

```python
"""Обмен адресами по таймеру: свои - плате, чужие - дальше, только при изменении."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from duo_input.clipboard.address_exchange import EXCHANGE_INTERVAL_MS, AddressExchange


class _Backend(QObject):
    peer_addresses_received = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[list[str]] = []

    def exchange_addresses(self, local: list[str]) -> bool:
        self.sent.append(list(local))
        return True


def test_every_tick_hands_every_backend_the_current_local_list(qapp):
    first, second = _Backend(), _Backend()
    local = [["192.168.1.10"]]
    exchange = AddressExchange([first, second], lambda: local[0])

    exchange.tick()
    local[0] = ["192.168.1.11"]
    exchange.tick()

    assert first.sent == [["192.168.1.10"], ["192.168.1.11"]]
    assert second.sent == first.sent


def test_the_peer_list_is_announced_only_when_it_changes(qapp):
    backend = _Backend()
    exchange = AddressExchange([backend], lambda: [])
    seen = []
    exchange.peer_addresses_changed.connect(seen.append)

    backend.peer_addresses_received.emit(["10.0.0.2"])
    backend.peer_addresses_received.emit(["10.0.0.2"])
    backend.peer_addresses_received.emit(["10.0.0.3"])

    assert seen == [["10.0.0.2"], ["10.0.0.3"]]
    assert exchange.peer_addresses == ["10.0.0.3"]


def test_start_exchanges_at_once_and_then_on_the_interval(qtbot):
    backend = _Backend()
    exchange = AddressExchange([backend], lambda: ["192.168.1.10"])
    exchange.start()
    try:
        assert len(backend.sent) == 1
        assert exchange._timer.interval() == EXCHANGE_INTERVAL_MS
        assert exchange._timer.isActive()
    finally:
        exchange.stop()
    assert not exchange._timer.isActive()
```

- [ ] **Step 2: Убедиться, что тесты падают.**

  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/clipboard/test_address_exchange.py -q`
  Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Реализовать.**

```python
"""Периодический обмен адресами с платой (см. спецификацию обмена адресами).

Раз в EXCHANGE_INTERVAL_MS каждому бэкенду (DeviceService на ПК1 - через U1,
EndpointService на ПК2 - через U2) отдаётся текущий список своих адресов;
ответ - адреса второго компьютера. Бэкенд, которому сейчас не до обмена
(нет платы, идёт запись конфигурации), просто пропускает такт: следующий
через пять секунд. Дальше по цепочке список уходит только при изменении.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal

EXCHANGE_INTERVAL_MS = 5000


class AddressExchange(QObject):
    peer_addresses_changed = Signal(list)

    def __init__(
        self,
        backends: list,
        local_addresses: Callable[[], list[str]],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._backends = list(backends)
        self._local = local_addresses
        self._peer: list[str] = []
        for backend in self._backends:
            backend.peer_addresses_received.connect(self._on_peer)
        self._timer = QTimer(self)
        self._timer.setInterval(EXCHANGE_INTERVAL_MS)
        self._timer.timeout.connect(self.tick)

    @property
    def peer_addresses(self) -> list[str]:
        return list(self._peer)

    def start(self) -> None:
        self._timer.start()
        self.tick()

    def stop(self) -> None:
        self._timer.stop()
        for backend in self._backends:
            try:
                backend.peer_addresses_received.disconnect(self._on_peer)
            except (RuntimeError, TypeError):  # pragma: no cover - уже отключено
                pass

    def tick(self) -> None:
        local = self._local()
        for backend in self._backends:
            backend.exchange_addresses(local)

    def _on_peer(self, addresses: list) -> None:
        addresses = [str(address) for address in addresses]
        if addresses == self._peer:
            return
        self._peer = addresses
        self.peer_addresses_changed.emit(list(addresses))


__all__ = ["EXCHANGE_INTERVAL_MS", "AddressExchange"]
```

- [ ] **Step 4: Прогнать тесты.**

  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/clipboard/test_address_exchange.py -q`
  Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add configurator/src/duo_input/clipboard/address_exchange.py configurator/tests/clipboard/test_address_exchange.py
git commit -m "feat(clipboard): periodic address exchange"
```

---

### Task 12: Перебор кандидатов в `ClipboardCoordinator`

**Files:**
- Modify: `configurator/src/duo_input/clipboard/coordinator.py`
- Test: `configurator/tests/clipboard/test_coordinator.py`

**Interfaces:**
- Produces:
  - `ClipboardCoordinator.set_board_addresses(addresses: list[str]) -> None`;
  - `ClipboardCoordinator.restore_manual_address(address: str) -> None` — ставит адрес без попытки соединения;
  - сигнал `ClipboardCoordinator.address_in_use = Signal(str)`.
- `_address()` удаляется, его место занимает `_candidates() -> list[str]`.

- [ ] **Step 1: Написать падающие тесты.** Добавить в конец `test_coordinator.py`. Вспомогательная `_RecordingLink` уже повторяется в файле. Добавить одну общую на уровне модуля после `_FakeTimer`:

```python
class _DialLink(QObject):
    """PeerLink, который только записывает, куда его попросили позвонить."""

    connected = Signal(str)
    disconnected = Signal(str)
    message_received = Signal(object)
    made: list["_DialLink"] = []

    def __init__(self, identity, parent=None) -> None:
        super().__init__(parent)
        self.address = ""
        self.peer_address = ""
        _DialLink.made.append(self)

    def connect_to(self, address, port, expected_fingerprint) -> None:
        self.address = address
        self.peer_address = address

    def send(self, message) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture
def dial(monkeypatch):
    _DialLink.made = []
    monkeypatch.setattr(coordinator_module, "PeerLink", _DialLink)
    return _DialLink.made
```

  И тесты:

```python
# ---------------------------------------------------------------------- адреса от платы


def test_the_last_good_address_is_tried_first_then_the_boards(tmp_path, dial):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator._retry = _FakeTimer()
    # 192.168.1.5 - сохранённый last_address; в списке платы он повторяется.
    coordinator.set_board_addresses(["10.0.0.2", "192.168.1.5", "10.0.0.3"])

    for _ in range(3):
        coordinator._try_connect()  # то, что сделал бы сработавший _retry
        dial[-1].disconnected.emit("refused")

    assert [link.address for link in dial] == ["192.168.1.5", "10.0.0.2", "10.0.0.3"]


def test_the_next_candidate_is_tried_without_the_backoff(tmp_path, dial):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    timer = _FakeTimer()
    coordinator._retry = timer
    coordinator.set_board_addresses(["10.0.0.2"])

    coordinator._try_connect()
    dial[-1].disconnected.emit("refused")

    assert timer.starts == [0]
    assert coordinator._attempt == 0


def test_the_backoff_starts_only_after_the_whole_list_failed(tmp_path, dial):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    timer = _FakeTimer()
    coordinator._retry = timer
    coordinator.set_board_addresses(["10.0.0.2"])

    coordinator._try_connect()
    dial[-1].disconnected.emit("refused")
    coordinator._try_connect()
    dial[-1].disconnected.emit("refused")

    assert timer.starts == [0, reconnect_delay_ms(0)]
    assert coordinator._candidate_index == 0


def test_a_manual_address_is_the_only_candidate(tmp_path, dial):
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator.set_board_addresses(["10.0.0.2"])
    coordinator.restore_manual_address("192.168.7.7")

    assert coordinator._candidates() == ["192.168.7.7"]
    assert dial == []  # restore только запоминает, не звонит


def test_a_connection_remembers_and_announces_the_address_that_worked(tmp_path, dial):
    coordinator, trust = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    coordinator.set_board_addresses(["10.0.0.2"])
    announced = []
    coordinator.address_in_use.connect(announced.append)
    coordinator._candidate_index = 1

    coordinator._try_connect()
    dial[-1].connected.emit("f" * 64)

    try:
        assert announced == ["10.0.0.2"]
        assert trust.peer().last_address == "10.0.0.2"
        assert coordinator._candidate_index == 0
    finally:
        coordinator.stop()


def test_an_incoming_ipv4_mapped_address_is_stored_plain(tmp_path, qapp):
    coordinator, trust = _make_coordinator(tmp_path, peer_origin_id=SMALLEST_ORIGIN_ID)
    announced = []
    coordinator.address_in_use.connect(announced.append)

    coordinator._on_incoming_link(_FakeLink("f" * 64, peer_address="::ffff:192.168.1.44"))

    try:
        assert announced == ["192.168.1.44"]
        assert trust.peer().last_address == "192.168.1.44"
    finally:
        coordinator.stop()


def test_no_candidates_at_all_falls_back_to_searching(tmp_path, dial, monkeypatch):
    # TrustStore не хранит пира без адреса, поэтому «кандидатов нет» задаётся
    # напрямую: так выглядит пир, чей адрес неизвестен, а плата молчит.
    coordinator, _ = _make_coordinator(tmp_path, peer_origin_id=LARGEST_ORIGIN_ID)
    monkeypatch.setattr(coordinator, "_candidates", lambda: [])
    try:
        coordinator._try_connect()

        assert coordinator.state is LinkState.SEARCHING
        assert dial == []
    finally:
        coordinator.stop()
```

- [ ] **Step 2: Убедиться, что тесты падают.**

  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/clipboard/test_coordinator.py -q`
  Expected: новые тесты FAIL (`AttributeError: set_board_addresses`), старые PASS.

- [ ] **Step 3: Реализовать в `coordinator.py`.**
  - Добавить сигнал рядом с остальными: `address_in_use = Signal(str)`.
  - В `__init__` после `self._manual_address = ""` добавить:

```python
        self._board_addresses: list[str] = []
        # Какой по счёту кандидат набирается сейчас и идёт ли набор вообще:
        # неудача набора ведёт к следующему кандидату без паузы, а разрыв
        # живой связи - к обычной паузе переподключения.
        self._candidate_index = 0
        self._dialing = False
```

  - Добавить методы после `set_manual_address`:

```python
    def restore_manual_address(self, address: str) -> None:
        """Сохранённый ручной адрес при запуске - без немедленного звонка."""
        self._manual_address = address.strip()

    def set_board_addresses(self, addresses: list[str]) -> None:
        """Адреса второго компьютера, которые сообщила плата.

        Живую связь это не трогает: список нужен только следующему набору.
        """
        self._board_addresses = [address for address in addresses if address]
```

  - Заменить `_address()` на:

```python
    def _candidates(self) -> list[str]:
        """Куда звонить, по порядку: ручной адрес - единственный кандидат;
        иначе последний удачный, затем адреса от платы, без повторов."""
        if self._manual_address:
            return [self._manual_address]
        peer = self.peer
        result: list[str] = []
        for address in ([peer.last_address] if peer else []) + self._board_addresses:
            if address and address not in result:
                result.append(address)
        return result
```

  - В `_try_connect` заменить блок с `address = self._address()` на:

```python
        candidates = self._candidates()
        if not candidates:
            self._start_looking()
            return
```

    Перед созданием `PeerLink` (после ветки «Звонит меньший») добавить:

```python
        address = candidates[self._candidate_index % len(candidates)]
        self._dialing = True
```

  - В `_drop` после блока `if protocol_mismatch: ... return` и **до** `if not self._manual_address:` вставить:

```python
        if self._dialing:
            self._dialing = False
            if self._candidate_index + 1 < len(self._candidates()):
                # Этот адрес не ответил - следующий пробуем сразу: пауза
                # переподключения нужна после неудачи всего списка, а не
                # каждого адреса в нём.
                self._candidate_index += 1
                self._set_state(LinkState.DISCONNECTED)
                self._retry.start(0)
                return
        self._candidate_index = 0
```

  - В начало `_on_connected` (после `self._link = link`) добавить:

```python
        self._dialing = False
        self._candidate_index = 0
        address = _plain_ipv4(getattr(link, "peer_address", "") or "")
        if address:
            peer = self.peer
            if peer is not None and peer.last_address != address:
                self._trust.update_address(address)
            self.address_in_use.emit(address)
```

  - Добавить функцию модуля перед классом:

```python
def _plain_ipv4(address: str) -> str:
    """Входящий сокет на двухстековом слушателе даёт "::ffff:1.2.3.4" -
    звонить обратно и показывать человеку нужно "1.2.3.4"."""
    prefix = "::ffff:"
    return address[len(prefix):] if address.lower().startswith(prefix) else address
```

  - В `stop()` и `forget_peer()` сбросить `self._dialing = False` и `self._candidate_index = 0` там, где уже останавливаются таймеры.

- [ ] **Step 4: Прогнать тесты.** Обязательно весь файл: старые тесты (`test_a_failed_last_known_address_starts_discovery_...`, `test_setting_a_manual_address_...`) проверяют соседние ветки `_drop`.

  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/clipboard -q`
  Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add configurator/src/duo_input/clipboard/coordinator.py configurator/tests/clipboard/test_coordinator.py
git commit -m "feat(clipboard): dial board-reported candidates in order"
```

---

### Task 13: Строка адреса — редактируемый `QComboBox`

**Files:**
- Modify: `configurator/src/duo_input/ui/clipboard_page.py`
- Modify: `configurator/src/duo_input/resources/translations/duo_input_ru.ts`, `configurator/src/duo_input/resources/translations/duo_input_en.ts` (через `tools/update_translations.py`)
- Test: `configurator/tests/ui/test_clipboard_page.py`

**Interfaces:**
- Produces:
  - `ClipboardPage.address_combo: QComboBox` (editable) вместо `address_field`;
  - сигнал `address_changed = Signal(str)` сохраняется: испускается на Enter / потере фокуса и на выборе из списка;
  - `set_board_addresses(addresses: list[str])`;
  - `set_manual_address(address: str)`;
  - `show_address_in_use(address: str)`;
  - свойство `is_manual -> bool`.

- [ ] **Step 1: Написать падающие тесты.** Все тесты работают через настоящий виджет, как требует память проекта «тест должен нажимать настоящую кнопку». Текст только ASCII, потому что `keyClicks` падает на кириллице:

```python
from PySide6.QtCore import Qt


def _page(qtbot):
    page = ClipboardPage()
    qtbot.addWidget(page)
    page.show()
    return page


def test_typing_an_address_and_pressing_enter_reports_it_and_makes_it_manual(qtbot):
    page = _page(qtbot)
    seen = []
    page.address_changed.connect(seen.append)

    qtbot.keyClicks(page.address_combo.lineEdit(), "192.168.1.42")
    qtbot.keyClick(page.address_combo.lineEdit(), Qt.Key.Key_Return)

    assert seen[-1] == "192.168.1.42"
    assert page.is_manual is True


def test_board_addresses_fill_the_drop_down_without_reporting_anything(qtbot):
    page = _page(qtbot)
    seen = []
    page.address_changed.connect(seen.append)

    page.set_board_addresses(["192.168.1.7", "10.0.0.2"])

    items = [page.address_combo.itemText(i) for i in range(page.address_combo.count())]
    assert items == ["192.168.1.7", "10.0.0.2"]
    assert seen == []
    assert page.is_manual is False


def test_choosing_from_the_list_is_a_manual_choice(qtbot):
    page = _page(qtbot)
    page.set_board_addresses(["192.168.1.7", "10.0.0.2"])
    seen = []
    page.address_changed.connect(seen.append)

    page.address_combo.activated.emit(1)  # то, что шлёт QComboBox при выборе мышью

    assert seen == ["10.0.0.2"]
    assert page.is_manual is True


def test_clearing_the_field_returns_to_automatic(qtbot):
    page = _page(qtbot)
    page.set_manual_address("192.168.1.42")
    seen = []
    page.address_changed.connect(seen.append)

    page.address_combo.lineEdit().selectAll()
    qtbot.keyClick(page.address_combo.lineEdit(), Qt.Key.Key_Delete)
    qtbot.keyClick(page.address_combo.lineEdit(), Qt.Key.Key_Return)

    assert seen[-1] == ""
    assert page.is_manual is False


def test_the_address_in_use_is_shown_in_automatic_mode_only(qtbot):
    page = _page(qtbot)
    seen = []
    page.address_changed.connect(seen.append)

    page.show_address_in_use("10.0.0.2")
    assert page.address_combo.currentText() == "10.0.0.2"
    assert page.is_manual is False
    assert seen == []

    page.set_manual_address("192.168.1.42")
    page.show_address_in_use("10.0.0.2")
    assert page.address_combo.currentText() == "192.168.1.42"


def test_refilling_the_list_keeps_what_is_typed(qtbot):
    page = _page(qtbot)
    page.set_manual_address("192.168.1.42")
    page.set_board_addresses(["10.0.0.2"])
    assert page.address_combo.currentText() == "192.168.1.42"
```

- [ ] **Step 2: Убедиться, что тесты падают.**

  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_clipboard_page.py -q`
  Expected: FAIL (`AttributeError: address_combo`).

- [ ] **Step 3: Реализовать в `clipboard_page.py`.**
  - В импорт добавить `QComboBox`.
  - Блок создания `self.address_field` заменить на:

```python
        # Одна строка на оба случая: выпадающий список - адреса, которые
        # сообщила плата; ввод - ручной адрес, который главнее них, пока его
        # не сотрут. Программная подстановка идёт под blockSignals и ручным
        # вводом не считается.
        self._manual = False
        self.address_combo = QComboBox(self)
        self.address_combo.setEditable(True)
        self.address_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.address_combo.lineEdit().setPlaceholderText(
            self.tr("Адрес второго компьютера, если поиск не нашёл")
        )
        self.address_combo.lineEdit().editingFinished.connect(self._on_address_edited)
        self.address_combo.activated.connect(self._on_address_chosen)
```

  - В `peer_layout.addWidget(self.address_field)` заменить виджет на `self.address_combo`.
  - Добавить методы перед `set_peer`:

```python
    @property
    def is_manual(self) -> bool:
        return self._manual

    def set_board_addresses(self, addresses: list[str]) -> None:
        text = self.address_combo.currentText()
        self.address_combo.blockSignals(True)
        self.address_combo.clear()
        self.address_combo.addItems(addresses)
        self.address_combo.setEditText(text)
        self.address_combo.blockSignals(False)

    def set_manual_address(self, address: str) -> None:
        self._manual = bool(address)
        self._show(address, self.tr("введён вручную") if address else "")

    def show_address_in_use(self, address: str) -> None:
        if self._manual:
            return
        self._show(address, self.tr("найден автоматически"))

    def _show(self, address: str, source: str) -> None:
        self.address_combo.blockSignals(True)
        self.address_combo.setEditText(address)
        self.address_combo.blockSignals(False)
        self.address_combo.setToolTip(source)

    def _on_address_edited(self) -> None:
        text = self.address_combo.currentText().strip()
        if not self._manual and not text:
            return  # пустая строка в режиме «Авто» - ничего не изменилось
        self._manual = bool(text)
        self.address_combo.setToolTip(self.tr("введён вручную") if text else "")
        self.address_changed.emit(text)

    def _on_address_chosen(self, index: int) -> None:
        text = self.address_combo.itemText(index).strip()
        self._manual = bool(text)
        self.address_combo.setToolTip(self.tr("введён вручную") if text else "")
        self.address_changed.emit(text)
```

  - Во всех тестах, которые обращаются к `address_field` (`configurator/tests/ui/test_runtime_wiring.py:473-474, 561-562`), заменить обращение на `address_combo.lineEdit()`. Строка `address_field.setText(...)` становится `address_combo.lineEdit().setText(...)`, а `address_field.editingFinished.emit()` — `address_combo.lineEdit().editingFinished.emit()`. Проверить остальные места поиском: `grep -rn address_field configurator`.
  - Обновить переводы: `.venv/Scripts/python.exe tools/update_translations.py` (сначала прочитать, как он запускается). Для новых строк «введён вручную» и «найден автоматически» в `duo_input_en.ts` задать переводы `entered manually` и `found automatically`.

- [ ] **Step 4: Прогнать тесты.**

  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui -q`
  Expected: PASS, включая `test_localization.py`.

- [ ] **Step 5: Commit.**

```bash
git add configurator/src/duo_input/ui/clipboard_page.py configurator/src/duo_input/resources/translations configurator/tests/ui
git commit -m "feat(ui): address field becomes an editable list of board addresses"
```

---

### Task 14: Проводка в приложении, сохранение и сквозной тест

**Files:**
- Modify: `configurator/src/duo_input/app.py`
- Test: `configurator/tests/ui/test_runtime_wiring.py`

**Interfaces:**
- Consumes:
  - `AddressExchange` (Task 11);
  - `EndpointService` (Task 9);
  - `DeviceService.exchange_addresses` (Task 8);
  - `local_ipv4_addresses` (Task 10);
  - методы координатора (Task 12);
  - методы страницы (Task 13).
- Produces: `_ClipboardRuntime.address_exchange: AddressExchange | None`, а также метод `_ClipboardRuntime.set_manual_address(address: str)`.

- [ ] **Step 1: Написать падающие тесты.** Файл `test_runtime_wiring.py` уже содержит хелперы `_settings(tmp_path, enabled)` и `configure_runtime`. Добавить тесты:

```python
def test_a_manual_address_survives_a_restart(qtbot, qapp, tmp_path, monkeypatch):
    """Исходная жалоба: после пересборки строка адреса пустела."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    coordinator = configure_runtime(qapp, window, settings)
    line = window.clipboard_page.address_combo.lineEdit()
    qtbot.keyClicks(line, "192.168.1.42")
    qtbot.keyClick(line, Qt.Key.Key_Return)
    coordinator.service._backend.stop()
    coordinator.stop()

    again = build_main_window(settings=settings)
    qtbot.addWidget(again)
    restored = configure_runtime(qapp, again, settings)
    try:
        assert again.clipboard_page.address_combo.currentText() == "192.168.1.42"
        assert again.clipboard_page.is_manual is True
        assert restored._manual_address == "192.168.1.42"
    finally:
        restored.service._backend.stop()
        restored.stop()


def test_addresses_from_the_board_reach_the_page_and_the_coordinator(
    qtbot, qapp, tmp_path, monkeypatch
):
    """Сквозной путь: эмулятор U1 -> DeviceService -> AddressExchange ->
    координатор и строка. Без него каждая часть протестирована, а цепочка -
    нет (см. память «Tested but never called»)."""
    from duo_input.device.emulator import U1Emulator
    from duo_input.device.qt_transport import SynchronousTransportLink

    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(app_module, "local_ipv4_addresses", lambda: ["192.168.1.10"])
    emulator = U1Emulator()
    emulator.set_peer_addresses(["10.0.0.2"])
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    window.service.connect_device(SynchronousTransportLink(emulator))
    qtbot.waitUntil(lambda: window.service.is_connected and window.service.device_info is not None)

    coordinator = configure_runtime(qapp, window, settings)
    try:
        qtbot.waitUntil(
            lambda: [
                window.clipboard_page.address_combo.itemText(i)
                for i in range(window.clipboard_page.address_combo.count())
            ]
            == ["10.0.0.2"],
            timeout=3000,
        )
        assert coordinator._board_addresses == ["10.0.0.2"]
        assert emulator.local_addresses == ["192.168.1.10"]
    finally:
        coordinator.service._backend.stop()
        coordinator.stop()
```

  **Проверить при реализации.** Если `build_main_window` без `transport_factory` сам пытается открыть реальный порт, передать `transport_factory=lambda: None`, как это делают соседние тесты. Добавить `from PySide6.QtCore import Qt`, если его нет в импортах.

- [ ] **Step 2: Убедиться, что тесты падают.**

  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_runtime_wiring.py -q -k "survives or reach_the_page"`
  Expected: FAIL.

- [ ] **Step 3: Реализовать в `app.py`.**
  - Импорты:

```python
from duo_input.clipboard.address_exchange import AddressExchange
from duo_input.clipboard.local_addresses import local_ipv4_addresses
from duo_input.device.endpoint_service import EndpointService
```

  - В `_ClipboardRuntime.__init__` добавить `self.address_exchange: AddressExchange | None = None`.
  - Добавить метод:

```python
    def set_manual_address(self, address: str) -> None:
        """Ручной адрес со страницы: сохранить (переживает перезапуск и
        пересборку) и отдать координатору."""
        self._settings.setValue("clipboard/manual_address", address)
        if self.coordinator is not None:
            self.coordinator.set_manual_address(address)
```

  - В `_start`:
    - заменить строку `window.clipboard_page.address_changed.connect(coordinator.set_manual_address)` на `window.clipboard_page.address_changed.connect(self.set_manual_address)`;
    - перед `coordinator.start()` вставить:

```python
        manual = str(self._settings.value("clipboard/manual_address", "", type=str) or "")
        coordinator.restore_manual_address(manual)
        window.clipboard_page.set_manual_address(manual)
        coordinator.address_in_use.connect(window.clipboard_page.show_address_in_use)

        # ПК1 спрашивает через U1 (порт уже держит DeviceService), ПК2 - через
        # свою U2. На каждом компьютере отвечает ровно один из двух: второй
        # просто не находит своей платы и пропускает такт.
        self._endpoint = EndpointService(parent=self)
        exchange = AddressExchange(
            [window.service, self._endpoint], lambda: local_ipv4_addresses(), self
        )
        exchange.peer_addresses_changed.connect(coordinator.set_board_addresses)
        exchange.peer_addresses_changed.connect(window.clipboard_page.set_board_addresses)
        self.address_exchange = exchange
```

    - после `coordinator.start()` добавить `exchange.start()`.

    `lambda: local_ipv4_addresses()`, а не сама функция, нужен для того, чтобы `monkeypatch` в тесте подменял имя модуля `app` во время вызова.
  - В `_stop` рядом с отключением `address_changed`:
    - заменить `page.address_changed.disconnect(coordinator.set_manual_address)` на `page.address_changed.disconnect(self.set_manual_address)`;
    - добавить `coordinator.address_in_use.disconnect(page.show_address_in_use)`;
    - добавить:

```python
        exchange, self.address_exchange = self.address_exchange, None
        if exchange is not None:
            exchange.stop()
            exchange.deleteLater()
        endpoint, self._endpoint = getattr(self, "_endpoint", None), None
        if endpoint is not None:
            endpoint.stop()
            endpoint.deleteLater()
```

  - В `__init__` добавить `self._endpoint: EndpointService | None = None` (тогда `getattr` в `_stop` не нужен, заменить на `self._endpoint`).

- [ ] **Step 4: Прогнать весь UI-набор.** Затем, по памяти проекта «QApplication.quit() залипает», прогнать наборы по порядку.

  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui -q`
  Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer configurator/tests/ui -q`
  Expected: PASS. Сверить код возврата (`echo $?` = 0), а не только зелёную сводку: см. память «Зелёная сводка при ненулевом коде».

- [ ] **Step 5: Commit.**

```bash
git add configurator/src/duo_input/app.py configurator/tests/ui/test_runtime_wiring.py
git commit -m "feat(app): wire the address exchange and persist the manual address"
```

---

### Task 15: Проверка мутациями, полные наборы, железо

**Files:** изменений кода нет. Итог записывается в `docs/superpowers/records/2026-09-25-peer-address-exchange.md`.

- [ ] **Step 1: Проверить защиты мутациями.** Каждую строку из таблицы удалить или обратить, прогнать указанный тест и вернуть строку (`git checkout -- <file>`). Если тест не упал, дописать тест и повторить.

| Мутация | Где | Должен упасть |
| --- | --- | --- |
| убрать `count > kMaxHostAddresses` | `host_addresses.cpp` decode | `more_than_eight_addresses_is_refused` |
| убрать проверку длины | `host_addresses.cpp` decode | `a_length_that_disagrees_with_the_count_is_refused` |
| убрать проверку `all_zero` | `host_addresses.cpp` decode | `the_unspecified_address_is_refused` |
| `has_local_ &&` → `true &&` | `AddressBook::local_due` | `nothing_is_due_before_the_host_has_said_anything` |
| удалить ветку адресов в `poll_snapshot` | `spi_master_poll.cpp` | `the_hosts_list_goes_out_on_its_interval_...` |
| `ENDPOINT_ADDRESSES` убрать из `is_endpoint_reply` | `spi_protocol.cpp` | `an_address_reply_is_an_endpoint_reply_...` |
| убрать `required_capability` для `EXCHANGE_ADDRESSES` | `config_service.cpp` | `an_exchange_needs_the_capability_...` |
| убрать `if (!negotiated_)` | `address_service.cpp` | `u2_refuses_an_exchange_before_hello` |
| убрать `is_link_local` из `_reachable` | `local_addresses.py` | `test_loopback_link_local_...` |
| убрать фильтр по префиксу имени | `local_addresses.py` | `test_virtual_adapters_...` |
| убрать `_defer_behind_exchange` из `get_diagnostics` | `service.py` | `test_a_user_operation_started_during_an_exchange_...` |
| убрать ветку `_dialing` в `_drop` | `coordinator.py` | `test_the_next_candidate_is_tried_without_the_backoff` |
| убрать `blockSignals` в `_show` | `clipboard_page.py` | `test_the_address_in_use_is_shown_in_automatic_mode_only` |
| убрать `setValue("clipboard/manual_address", ...)` | `app.py` | `test_a_manual_address_survives_a_restart` |
| убрать `exchange.start()` | `app.py` | `test_addresses_from_the_board_reach_the_page_...` |

- [ ] **Step 2: Прогнать полные наборы.**

  Run:

  ```
  .venv/Scripts/python.exe -m pytest configurator/tests tests -q; echo "exit=$?"
  cmake --build --preset native --clean-first
  ctest --preset native
  cmake --build --preset pico-release --clean-first
  .venv/Scripts/python.exe tools/generate_protocol.py --check
  ```

  Expected: `exit=0` и всё зелёное. Записать итоговые числа в отчёт.

- [ ] **Step 3: Проверить на железе.** Выполняет человек. Агент готовит инструкцию и ждёт результата.
  1. Прошить U1 и U2 образами `build/pico-release/firmware/{u1_main,u2_endpoint}/*.uf2`. U1 прошивается без BOOTSEL сбросом на 1200 бод. U2 в этот раз ещё через BOOTSEL: CDC у неё появится только с новой прошивкой.
  2. Сделать цикл питания обеих плат (память «После прошивки U1 нужен цикл питания»).
  3. На ПК2 проверить в Диспетчере устройств, что появился COM-порт `Duo Input U2` (VID/PID `1209:D102`). Если Windows закешировала прежний дескриптор без CDC, удалить устройство в Диспетчере вместе с драйвером и переподключить.
  4. Запустить приложение на обоих ПК с включённым общим буфером.
  5. В течение 10 с в выпадающем списке строки адреса на каждом ПК должны появиться IPv4 другого ПК. Сверить с `ipconfig` на нём.
  6. Ввести на ПК1 заведомо чужой адрес, закрыть и снова открыть приложение. Адрес должен остаться, подсказка должна показывать «введён вручную».
  7. Очистить строку и нажать Enter. Связь должна восстановиться по автоадресу, а строка — показать его.
  8. Переключить ПК2 на другую Wi-Fi-сеть или переподключить кабель. Через ≤ 10 с список на ПК1 должен обновиться.
  9. Двигать мышь на ПК2 непрерывно 30 с. Рывков быть не должно: `HOST_ADDRESSES` задерживает ввод на один проход раз в 250 мс.

- [ ] **Step 4: Записать итог и закоммитить.** Результаты шагов 1–3 записать в `docs/superpowers/records/2026-09-25-peer-address-exchange.md`: цифры наборов, мутации, наблюдения с железа, найденные расхождения. Затем:

```bash
git add docs/superpowers/records/2026-09-25-peer-address-exchange.md
git commit -m "docs(address-exchange): verification record"
```
