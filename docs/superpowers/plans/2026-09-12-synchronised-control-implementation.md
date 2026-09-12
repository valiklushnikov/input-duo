# Synchronised Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Одна настройка устройства делает клавиатуру и мышь единой точкой управления: любая смена маршрута переводит оба устройства на один компьютер.

**Architecture:** Бит в заголовке бинарного конфига доходит до `BindingEngine`, и вся сцепка живёт в одной его функции — `move_route`, единственном месте, где маршрут реально едет. Через неё же проходят шаги макроса, поэтому они подчиняются синхрону без отдельного кода. `Routes` остаётся хранилищем состояния и о синхроне не знает: он не знает, что зажато, а сцепка обязана отпустить покидаемый компьютер.

**Tech Stack:** C++17 (прошивка RP2040, тесты — самописный `TEST_CASE`/`CHECK` фреймворк + CMake/CTest), Python 3 + PySide6 (конфигуратор), pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-09-12-synchronised-control-design.md`

## Global Constraints

- Схема протокола: `schema_version` 1.1 → **1.2**. `protocol_version` **не меняется**.
- Имя флага в схеме: **`SYNCHRONISED_CONTROL`**, значение **1** (бит 0 байта 6 заголовка пакета).
- Внутреннее имя в Python: `synchronised_control`. В C++: `synchronised_control` / `ConfigFlag::SYNCHRONISED_CONTROL`.
- Версия файла проекта: `PROJECT_SCHEMA_VERSION` 1.1 → **"1.2"**.
- Подпись чекбокса — дословно: заголовок **«Переключать клавиатуру и мышь вместе»**, пояснение **«При переключении клавиатуры или мыши оба устройства будут направлены на один компьютер. Настройка действует для всех профилей.»** В коде строки пишутся по-английски внутри `self.tr(...)`, перевод идёт через `tools/update_translations.py`.
- Инвариант, который держат тесты: `synchronised_control ∧ keyboard_route != BOTH ⟹ keyboard_route == mouse_route`, на **выходе** каждой завершённой операции.
- `firmware/common/protocol/generated.hpp` и `configurator/src/duo_input/generated/protocol.py` **никогда не правятся руками** — только `python tools/generate_protocol.py`.
- Нативные тесты после правки любого заголовка собираются с `--clean-first`: Ninja не отслеживает заголовки, и инкрементальный прогон проверит устаревшие объектные файлы.
- **Не трогать** `tests/vectors/config_vectors/*.bin`. Эти векторы уже отстают на один минор схемы (`minor=0` в файле при схеме 1.1), ничто не сверяет их байты с генератором, и перегенерация — отдельный вопрос, не относящийся к этой работе.

---

### Task 1: Флаг существует в схеме и читается прошивкой

**Files:**
- Modify: `protocol/schema.json`
- Modify: `tools/generate_protocol.py:85-101` (C++ таблица секций), `:143-155` (Python таблица секций)
- Modify: `protocol/config_format.md:37-58` (таблица заголовка), `:154` (абзац про отвергаемые поля)
- Modify: `firmware/common/config/validator.hpp:94-106` (`ConfigView`)
- Modify: `firmware/common/config/validator.cpp:276` (проверка заголовка), `:412-419` (методы `ConfigView`)
- Modify: `firmware/u1_main/config_profiles.hpp:50` (рядом с `active_profile_id()`), `firmware/u1_main/config_profiles.cpp:13-34` (`load`)
- Test: `tests/firmware_native/test_config_validator.cpp`, `tests/firmware_native/test_stored_profiles.cpp`, `configurator/tests/test_generated_protocol.py`
- Generated (не править руками): `firmware/common/protocol/generated.hpp`, `configurator/src/duo_input/generated/protocol.py`

**Interfaces:**
- Consumes: ничего.
- Produces: `duo_input::protocol::ConfigFlag::SYNCHRONISED_CONTROL` (C++, `enum class ConfigFlag : std::uint8_t`); `duo_input.generated.protocol.ConfigFlag.SYNCHRONISED_CONTROL` (Python, `IntFlag`); `bool duo_input::config::ConfigView::synchronised_control() const`; `bool duo_input::u1::StoredProfiles::synchronised_control() const`.

- [ ] **Step 1: Добавить раздел в схему**

В `protocol/schema.json` вставить новый раздел (ключи верхнего уровня отсортированы по алфавиту, так что `config_flags` идёт сразу после `capabilities`):

```json
  "config_flags": {
    "SYNCHRONISED_CONTROL": 1
  },
```

И поднять минор схемы:

```json
  "schema_version": {
    "major": 1,
    "minor": 2
  },
```

- [ ] **Step 2: Научить генератор рендерить флаги**

В `tools/generate_protocol.py`, в `render_cpp`, сразу после строки с `Capability`:

```python
    lines.extend(_cpp_enum("Capability", "std::uint32_t", _items(schema, "capabilities")))
    lines.append("")
    # Header flags of the binary configuration package. A flag enum, not a
    # value enum: the byte carries a set, and a reader that does not know a bit
    # must reject the package rather than run a configuration it half
    # understands.
    lines.extend(_cpp_enum("ConfigFlag", "std::uint8_t", _items(schema, "config_flags")))
```

В `render_python`, сразу после строки с `Capability`:

```python
    lines.extend(_python_enum("Capability", "IntFlag", _items(schema, "capabilities")))
    lines.append("")
    lines.extend(_python_enum("ConfigFlag", "IntFlag", _items(schema, "config_flags")))
```

- [ ] **Step 3: Перегенерировать и убедиться, что генератор чист**

```bash
python tools/generate_protocol.py
python tools/generate_protocol.py --check
```

Ожидается: второй запуск печатает пустой вывод и возвращает 0. Проверить глазами, что в `firmware/common/protocol/generated.hpp` появилось `enum class ConfigFlag : std::uint8_t { SYNCHRONISED_CONTROL = 0x01, };`, а в `configurator/src/duo_input/generated/protocol.py` — `class ConfigFlag(IntFlag): SYNCHRONISED_CONTROL = 0x01`, и что `SCHEMA_VERSION_MINOR` в обоих файлах стал `2`.

- [ ] **Step 4: Написать падающий тест генератора**

В конец `configurator/tests/test_generated_protocol.py`:

```python
def test_config_flags_are_generated_from_the_schema():
    schema = json.loads(Path("protocol/schema.json").read_text("utf-8"))

    assert protocol.ConfigFlag.SYNCHRONISED_CONTROL.value == schema["config_flags"]["SYNCHRONISED_CONTROL"]
    # A set of bits, not a list of values: two flags must be combinable, and
    # the reader below tests membership rather than equality.
    assert isinstance(protocol.ConfigFlag.SYNCHRONISED_CONTROL, protocol.ConfigFlag)
    assert protocol.SCHEMA_VERSION_MINOR == schema["schema_version"]["minor"]
```

- [ ] **Step 5: Запустить тест генератора**

```bash
python -m pytest configurator/tests/test_generated_protocol.py -q
```

Ожидается: PASS (шаг 3 уже перегенерировал файлы).

- [ ] **Step 6: Написать падающие тесты валидатора**

В `tests/firmware_native/test_config_validator.cpp`, рядом с прочими кейсами (файл уже определяет `read_vector`, `repair_crc` и `rejects` в анонимном namespace):

```cpp
TEST_CASE(config_validator_accepts_the_synchronised_control_flag_and_exposes_it) {
    std::vector<std::uint8_t> bytes = read_vector("valid_minimal.bin");
    const auto plain = validate_config({bytes.data(), bytes.size()});
    CHECK(plain);
    CHECK_FALSE(plain.view().synchronised_control());

    bytes[6] = static_cast<std::uint8_t>(duo_input::protocol::ConfigFlag::SYNCHRONISED_CONTROL);
    repair_crc(bytes);

    const auto flagged = validate_config({bytes.data(), bytes.size()});
    CHECK(flagged);
    CHECK(flagged.view().synchronised_control());
}

TEST_CASE(config_validator_rejects_header_flags_it_does_not_know) {
    // A bit this build has never heard of means the package was written by a
    // newer configurator, and what that bit asks for is unknown. Running the
    // rest of the configuration anyway is running something nobody chose.
    for (std::uint8_t bit : {0x02U, 0x04U, 0x80U}) {
        std::vector<std::uint8_t> bytes = read_vector("valid_minimal.bin");
        bytes[6] = bit;
        repair_crc(bytes);
        CHECK(rejects(bytes));
    }
}
```

Добавить в шапку файла `#include "protocol/generated.hpp"`, если его там ещё нет.

- [ ] **Step 7: Запустить тесты валидатора и убедиться, что они падают**

```bash
cmake --build --preset native --clean-first
```

Ожидается: ошибка компиляции — `'synchronised_control': is not a member of 'duo_input::config::ConfigView'`.

- [ ] **Step 8: Реализовать чтение флага в валидаторе**

В `firmware/common/config/validator.hpp`, в `class ConfigView`, рядом с `active_profile_id()`:

```cpp
class ConfigView {
public:
    std::uint8_t active_profile_id() const;
    /// Does this configuration ask for the keyboard and the mouse to travel
    /// together?
    bool synchronised_control() const;
    std::size_t profile_count() const;
```

В `firmware/common/config/validator.cpp`, в анонимном namespace рядом с прочими константами файла:

```cpp
/// Every header flag this build understands. A package carrying anything else
/// was written by a newer configurator, and what the unknown bit asks for
/// cannot be guessed at - so the whole package is refused rather than run
/// without it.
constexpr std::uint8_t kKnownConfigFlags =
    static_cast<std::uint8_t>(protocol::ConfigFlag::SYNCHRONISED_CONTROL);
```

Заменить проверку байта 6 в `validate_config` (строка 276): было

```cpp
        input.data[6] != 0U || input.data[7] != 0U || read_u32(input, 8U) != input.size) {
```

стало

```cpp
        (input.data[6] & static_cast<std::uint8_t>(~kKnownConfigFlags)) != 0U ||
        input.data[7] != 0U || read_u32(input, 8U) != input.size) {
```

Рядом с `ConfigView::active_profile_id()` (строка 412):

```cpp
bool ConfigView::synchronised_control() const {
    return bytes_.data != nullptr &&
           (bytes_.data[6U] & static_cast<std::uint8_t>(protocol::ConfigFlag::SYNCHRONISED_CONTROL)) != 0U;
}
```

- [ ] **Step 9: Запустить тесты валидатора**

```bash
cmake --build --preset native --clean-first
ctest --preset native -R config_validator --output-on-failure
```

Ожидается: PASS, включая оба новых кейса и все существующие.

- [ ] **Step 10: Написать падающий тест `StoredProfiles`**

В `tests/firmware_native/test_stored_profiles.cpp`:

```cpp
TEST_CASE(stored_profiles_report_whether_the_configuration_asks_for_synchronised_control) {
    std::vector<std::uint8_t> bytes = read_vector("valid_minimal.bin");
    StoredProfiles profiles;
    CHECK(profiles.load({bytes.data(), bytes.size()}));
    CHECK_FALSE(profiles.synchronised_control());

    bytes[6] = static_cast<std::uint8_t>(duo_input::protocol::ConfigFlag::SYNCHRONISED_CONTROL);
    repair_crc(bytes);
    CHECK(profiles.load({bytes.data(), bytes.size()}));
    CHECK(profiles.synchronised_control());

    // A package that is not a configuration leaves nothing behind, the flag
    // included: a half-loaded configuration that still switches both devices
    // is worse than none.
    CHECK_FALSE(profiles.load({nullptr, 0}));
    CHECK_FALSE(profiles.synchronised_control());
}
```

Если в этом файле нет собственных `read_vector`/`repair_crc`, скопировать их из `tests/firmware_native/test_config_validator.cpp:53` и `:83` в анонимный namespace этого файла — он тоже читает векторы.

- [ ] **Step 11: Запустить и убедиться, что падает**

```bash
cmake --build --preset native --clean-first
```

Ожидается: ошибка компиляции — `synchronised_control` не член `StoredProfiles`.

- [ ] **Step 12: Реализовать в `StoredProfiles`**

В `firmware/u1_main/config_profiles.hpp`, сразу после `active_profile_id()`:

```cpp
    /// Whether the stored configuration asks for the keyboard and the mouse to
    /// travel together. False for anything that did not load.
    bool synchronised_control() const { return synchronised_control_; }
```

и в приватной секции класса, рядом с `active_profile_id_`:

```cpp
    bool synchronised_control_ = false;
```

В `firmware/u1_main/config_profiles.cpp`, в `load`: в блоке сброса добавить строку

```cpp
    active_profile_id_ = 0;
    synchronised_control_ = false;
```

и после успешной валидации, рядом с чтением активного профиля:

```cpp
    active_profile_id_ = result.view().active_profile_id();
    synchronised_control_ = result.view().synchronised_control();
```

- [ ] **Step 13: Запустить нативный набор целиком**

```bash
cmake --build --preset native --clean-first
ctest --preset native --output-on-failure
```

Ожидается: все тесты PASS.

- [ ] **Step 14: Обновить документ формата**

В `protocol/config_format.md`, в таблице «Package header (64 bytes)», строку

```
| 6 | 1 | flags, zero |
```

заменить на

```
| 6 | 1 | flags: bit 0 `SYNCHRONISED_CONTROL`, bits 1..7 zero |
```

И дополнить абзац на строке 154, где перечислено отвергаемое: вместо «incorrect magic/major/flags/reserved/CRC» написать «incorrect magic/major/CRC, reserved bytes that are not zero, or header flags this build does not know». Добавить под таблицей абзац:

> `SYNCHRONISED_CONTROL` makes every route change move the keyboard and the
> mouse to the same computer. A reader that does not know a flag rejects the
> package: a refusal is visible, while running the configuration without the
> flag would look like working hardware that switches only one device.

- [ ] **Step 15: Мутационная проверка обоих guard-ов**

Временно заменить в `validator.cpp` маску на `0xFF` (то есть разрешить любые биты) и убедиться, что `config_validator` краснеет на `config_validator_rejects_header_flags_it_does_not_know`. Затем вернуть маску. Повторить: временно захардкодить `ConfigView::synchronised_control()` в `return false;` и убедиться, что краснеет `config_validator_accepts_the_synchronised_control_flag_and_exposes_it`. Вернуть реализацию и переcобрать.

```bash
cmake --build --preset native --clean-first
ctest --preset native -R "config_validator|stored_profiles" --output-on-failure
```

Ожидается: после возврата обеих правок — PASS.

- [ ] **Step 16: Commit**

```bash
git add protocol/schema.json protocol/config_format.md tools/generate_protocol.py \
  firmware/common/protocol/generated.hpp configurator/src/duo_input/generated/protocol.py \
  firmware/common/config/validator.hpp firmware/common/config/validator.cpp \
  firmware/u1_main/config_profiles.hpp firmware/u1_main/config_profiles.cpp \
  tests/firmware_native/test_config_validator.cpp tests/firmware_native/test_stored_profiles.cpp \
  configurator/tests/test_generated_protocol.py
git commit -m "Carry a synchronised-control flag in the configuration header

The header's flags byte was reserved and required to be zero. Bit 0 now
says the keyboard and the mouse travel together; every other bit still
rejects the package, because a bit this build cannot read asks for
something it cannot do.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Клавиатура выходит из `BOTH` туда, где мышь

**Files:**
- Modify: `firmware/u1_main/mapping/routes.hpp:29-36`, `firmware/u1_main/mapping/routes.cpp:34-42`
- Test: `tests/firmware_native/test_binding_engine.cpp`

**Interfaces:**
- Consumes: ничего.
- Produces: `static config::MouseRoute Routes::mouse_beside(config::KeyboardRoute)` и `static config::KeyboardRoute Routes::keyboard_beside(config::MouseRoute)` — перевод маршрута одного устройства в маршрут другого устройства на том же компьютере. Задача 3 и задача 4 вызывают оба.

- [ ] **Step 1: Написать падающие тесты**

В `tests/firmware_native/test_binding_engine.cpp`, рядом с `toggling_moves_the_keyboard_and_toggling_again_moves_it_back`:

```cpp
TEST_CASE(toggling_out_of_both_lands_where_the_pointer_is) {
    // The cursor is the only thing telling the operator which computer they
    // are working on. A keyboard leaving BOTH for anywhere else lands on the
    // machine they are not looking at.
    for (auto mouse : {MouseRoute::PC1, MouseRoute::PC2}) {
        BindingEngine engine;
        engine.set_bindings({bound(0x3E, BindingMode::REPLACE, ActionKind::SET_KEYBOARD_ROUTE,
                                   static_cast<std::uint8_t>(KeyboardRoute::BOTH)),
                             bound(0x3F, BindingMode::REPLACE, ActionKind::SET_MOUSE_ROUTE,
                                   static_cast<std::uint8_t>(mouse)),
                             bound(0x40, BindingMode::REPLACE, ActionKind::TOGGLE_KEYBOARD_ROUTE)});

        engine.handle(key(InputEventKind::KeyDown, 0x3F));
        engine.handle(key(InputEventKind::KeyDown, 0x3E));
        CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(KeyboardRoute::BOTH));

        engine.handle(key(InputEventKind::KeyDown, 0x40));

        const KeyboardRoute expected =
            mouse == MouseRoute::PC1 ? KeyboardRoute::PC1 : KeyboardRoute::PC2;
        CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(expected));
    }
}

TEST_CASE(routes_translate_between_the_two_devices_on_one_computer) {
    using duo_input::u1::mapping::Routes;
    CHECK_EQ(static_cast<int>(Routes::mouse_beside(KeyboardRoute::PC1)),
             static_cast<int>(MouseRoute::PC1));
    CHECK_EQ(static_cast<int>(Routes::mouse_beside(KeyboardRoute::PC2)),
             static_cast<int>(MouseRoute::PC2));
    CHECK_EQ(static_cast<int>(Routes::keyboard_beside(MouseRoute::PC1)),
             static_cast<int>(KeyboardRoute::PC1));
    CHECK_EQ(static_cast<int>(Routes::keyboard_beside(MouseRoute::PC2)),
             static_cast<int>(KeyboardRoute::PC2));
}
```

Добавить в шапку файла `#include "mapping/routes.hpp"`, если его там ещё нет.

- [ ] **Step 2: Запустить и убедиться, что падает**

```bash
cmake --build --preset native --clean-first
```

Ожидается: ошибка компиляции — `mouse_beside` не член `Routes`.

- [ ] **Step 3: Реализовать перевод и новый выход из `BOTH`**

В `firmware/u1_main/mapping/routes.hpp`, в публичную секцию, сразу после `mouse_route_is_valid`:

```cpp
    /// The mouse route serving the same computer as this keyboard route, and
    /// the reverse. BOTH has no answer here and must not be asked: it is the
    /// one keyboard route no pointer can follow.
    static config::MouseRoute mouse_beside(config::KeyboardRoute route);
    static config::KeyboardRoute keyboard_beside(config::MouseRoute route);
```

В `firmware/u1_main/mapping/routes.cpp` добавить реализации рядом с `mouse_route_is_valid`:

```cpp
config::MouseRoute Routes::mouse_beside(config::KeyboardRoute route) {
    return route == config::KeyboardRoute::PC2 ? config::MouseRoute::PC2
                                               : config::MouseRoute::PC1;
}

config::KeyboardRoute Routes::keyboard_beside(config::MouseRoute route) {
    return route == config::MouseRoute::PC2 ? config::KeyboardRoute::PC2
                                            : config::KeyboardRoute::PC1;
}
```

И заменить `toggle_keyboard` целиком:

```cpp
void Routes::toggle_keyboard() {
    // Out of BOTH, to the computer the pointer is already on. The cursor is
    // the only thing telling the operator which machine they are working on,
    // and a keyboard that lands anywhere else lands where they are not
    // looking. Under synchronised control this also brings the pair back
    // together without dragging the pointer across a screen.
    if (keyboard_ == config::KeyboardRoute::BOTH) {
        keyboard_ = keyboard_beside(mouse_);
        return;
    }
    keyboard_ = keyboard_ == config::KeyboardRoute::PC1 ? config::KeyboardRoute::PC2
                                                        : config::KeyboardRoute::PC1;
}
```

- [ ] **Step 4: Запустить тесты**

```bash
cmake --build --preset native --clean-first
ctest --preset native --output-on-failure
```

Ожидается: все PASS. Если покраснел существующий кейс, ожидавший выхода из `BOTH` на PC1 при мыши на PC2 — он и есть закрепление старого поведения; обновить его под новое правило и записать это в сообщении коммита.

- [ ] **Step 5: Мутационная проверка**

Временно вернуть в `toggle_keyboard` старое тело (без ветки `BOTH`) и убедиться, что `toggling_out_of_both_lands_where_the_pointer_is` краснеет на итерации `MouseRoute::PC2`. Вернуть новое тело.

```bash
cmake --build --preset native --clean-first
ctest --preset native -R binding_engine --output-on-failure
```

- [ ] **Step 6: Commit**

```bash
git add firmware/u1_main/mapping/routes.hpp firmware/u1_main/mapping/routes.cpp \
  tests/firmware_native/test_binding_engine.cpp
git commit -m "Send the keyboard out of BOTH to the computer the pointer is on

The code left BOTH for PC1 and the comment beside it said the far
machine; no test held either, so the behaviour lived in prose that
disagreed with itself. The cursor is the only thing the operator can
see, so the keyboard follows it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Сцепка маршрутов в движке

**Files:**
- Modify: `firmware/u1_main/mapping/engine.hpp:68-90` (публичная секция `BindingEngine`), `:120-128` (приватные поля)
- Modify: `firmware/u1_main/mapping/engine.cpp:314-353` (`move_route`)
- Test: `tests/firmware_native/test_binding_engine.cpp`

**Interfaces:**
- Consumes: `Routes::mouse_beside`, `Routes::keyboard_beside` из задачи 2.
- Produces: `void BindingEngine::set_synchronised_control(bool)` и `bool BindingEngine::synchronised_control() const` — задача 4 вызывает оба.

- [ ] **Step 1: Написать падающий табличный тест переходов**

В `tests/firmware_native/test_binding_engine.cpp`:

```cpp
namespace {

/// Drive an engine to a given pair of routes, then apply one switch.
///
/// The bindings are fixed: 0x3E sets the keyboard, 0x3F sets the mouse, 0x40
/// toggles the keyboard and 0x41 toggles the mouse.
BindingEngine synchronised_engine(KeyboardRoute keyboard, MouseRoute mouse) {
    BindingEngine engine;
    engine.set_bindings({bound(0x3E, BindingMode::REPLACE, ActionKind::SET_KEYBOARD_ROUTE,
                               static_cast<std::uint8_t>(keyboard)),
                         bound(0x3F, BindingMode::REPLACE, ActionKind::SET_MOUSE_ROUTE,
                               static_cast<std::uint8_t>(mouse)),
                         bound(0x40, BindingMode::REPLACE, ActionKind::TOGGLE_KEYBOARD_ROUTE),
                         bound(0x41, BindingMode::REPLACE, ActionKind::TOGGLE_MOUSE_ROUTE)});
    // Placed while synchronisation is off, so the starting pair is exactly
    // what the table asks for - including the two diverged rows, which is the
    // state switching the mode on over parted routes leaves behind.
    engine.set_synchronised_control(false);
    engine.handle(key(InputEventKind::KeyDown, 0x3F));
    engine.handle(key(InputEventKind::KeyUp, 0x3F));
    engine.handle(key(InputEventKind::KeyDown, 0x3E));
    engine.handle(key(InputEventKind::KeyUp, 0x3E));
    engine.set_synchronised_control(true);
    return engine;
}

}  // namespace

TEST_CASE(synchronised_switching_puts_both_devices_on_one_computer) {
    struct Row {
        KeyboardRoute keyboard;
        MouseRoute mouse;
        std::uint16_t press;
        KeyboardRoute expect_keyboard;
        MouseRoute expect_mouse;
    };
    // The acceptance table from the design, row for row.
    const Row rows[] = {
        {KeyboardRoute::PC1, MouseRoute::PC1, 0x40, KeyboardRoute::PC2, MouseRoute::PC2},
        {KeyboardRoute::PC1, MouseRoute::PC1, 0x41, KeyboardRoute::PC2, MouseRoute::PC2},
        {KeyboardRoute::PC1, MouseRoute::PC2, 0x40, KeyboardRoute::PC2, MouseRoute::PC2},
        {KeyboardRoute::PC2, MouseRoute::PC1, 0x41, KeyboardRoute::PC2, MouseRoute::PC2},
        {KeyboardRoute::BOTH, MouseRoute::PC1, 0x40, KeyboardRoute::PC1, MouseRoute::PC1},
        {KeyboardRoute::BOTH, MouseRoute::PC2, 0x40, KeyboardRoute::PC2, MouseRoute::PC2},
        {KeyboardRoute::BOTH, MouseRoute::PC1, 0x41, KeyboardRoute::PC2, MouseRoute::PC2},
    };

    for (const Row& row : rows) {
        BindingEngine engine = synchronised_engine(row.keyboard, row.mouse);
        engine.handle(key(InputEventKind::KeyDown, row.press));

        CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(row.expect_keyboard));
        CHECK_EQ(static_cast<int>(engine.mouse_route()), static_cast<int>(row.expect_mouse));
    }
}

TEST_CASE(synchronised_control_leaves_the_mouse_alone_when_the_keyboard_goes_to_both) {
    // BOTH is the one pause. There is no mouse route that could follow the
    // keyboard there, and inventing one would put the pointer on two computers
    // where it follows neither.
    BindingEngine engine = synchronised_engine(KeyboardRoute::PC1, MouseRoute::PC1);
    engine.set_bindings({bound(0x42, BindingMode::REPLACE, ActionKind::SET_KEYBOARD_ROUTE,
                               static_cast<std::uint8_t>(KeyboardRoute::BOTH))});

    engine.handle(key(InputEventKind::KeyDown, 0x42));

    CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(KeyboardRoute::BOTH));
    CHECK_EQ(static_cast<int>(engine.mouse_route()), static_cast<int>(MouseRoute::PC1));
}

TEST_CASE(a_held_mouse_button_is_released_when_the_keyboard_switch_takes_the_mouse_along) {
    // The whole reason this lives in the engine and not in Routes. A button
    // still under a finger when the pointer moves would stay down on the
    // computer being left, and that computer never hears about it again.
    BindingEngine engine = synchronised_engine(KeyboardRoute::PC1, MouseRoute::PC1);

    InputEvent button;
    button.kind = InputEventKind::MouseButtonDown;
    button.code = 1;
    button.source_index = 0;
    CHECK(engine.handle(button).count == 1);

    const Outcome outcome = engine.handle(key(InputEventKind::KeyDown, 0x40));

    CHECK(count_of(outcome, ActionRequestKind::ReleaseTarget) >= 1);
    CHECK_EQ(static_cast<int>(engine.mouse_route()), static_cast<int>(MouseRoute::PC2));

    // The release of a button the far side never saw must not arrive there.
    InputEvent release = button;
    release.kind = InputEventKind::MouseButtonUp;
    CHECK_EQ(count_of(engine.handle(release), ActionRequestKind::SendInput), 0);
}

TEST_CASE(a_refused_route_moves_neither_device) {
    // Validity is settled before anything is released. A refused route that
    // released the old computer first would let go of keys for a switch that
    // never happened.
    BindingEngine engine = synchronised_engine(KeyboardRoute::PC1, MouseRoute::PC1);
    engine.set_bindings({bound(0x43, BindingMode::REPLACE, ActionKind::SET_MOUSE_ROUTE,
                               static_cast<std::uint8_t>(KeyboardRoute::BOTH))});

    engine.handle(key(InputEventKind::KeyDown, 0x43));

    CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(KeyboardRoute::PC1));
    CHECK_EQ(static_cast<int>(engine.mouse_route()), static_cast<int>(MouseRoute::PC1));
}

TEST_CASE(a_macro_step_moves_both_devices_under_synchronised_control) {
    // Macro steps reach move_route through set_keyboard_route, so one rule
    // covers the operator's buttons and the scripts alike.
    BindingEngine engine = synchronised_engine(KeyboardRoute::PC1, MouseRoute::PC1);

    engine.set_keyboard_route(KeyboardRoute::PC2);

    CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(KeyboardRoute::PC2));
    CHECK_EQ(static_cast<int>(engine.mouse_route()), static_cast<int>(MouseRoute::PC2));
}

TEST_CASE(switching_is_independent_again_when_synchronised_control_is_off) {
    BindingEngine engine = synchronised_engine(KeyboardRoute::PC1, MouseRoute::PC1);
    engine.set_synchronised_control(false);

    engine.handle(key(InputEventKind::KeyDown, 0x40));

    CHECK_EQ(static_cast<int>(engine.keyboard_route()), static_cast<int>(KeyboardRoute::PC2));
    CHECK_EQ(static_cast<int>(engine.mouse_route()), static_cast<int>(MouseRoute::PC1));
}
```

- [ ] **Step 2: Запустить и убедиться, что падает**

```bash
cmake --build --preset native --clean-first
```

Ожидается: ошибка компиляции — `set_synchronised_control` не член `BindingEngine`.

- [ ] **Step 3: Объявить флаг в движке**

В `firmware/u1_main/mapping/engine.hpp`, в публичную секцию `BindingEngine`, сразу после `set_sources`:

```cpp
    /// One logical point of control: every route change moves the keyboard and
    /// the mouse to the same computer. Set from the stored configuration
    /// before a profile is installed - a profile applied under the old setting
    /// would be applied under the wrong rule.
    void set_synchronised_control(bool synchronised) { synchronised_ = synchronised; }
    bool synchronised_control() const { return synchronised_; }
```

и в приватную секцию, рядом с `modifiers_`:

```cpp
    /// Whether the two devices travel together. See ``move_route``.
    bool synchronised_ = false;
```

- [ ] **Step 4: Сцепить маршруты в `move_route`**

В `firmware/u1_main/mapping/engine.cpp` заменить тело `move_route` целиком на:

```cpp
bool BindingEngine::move_route(Outcome& outcome, bool keyboard, bool toggle,
                               std::uint8_t parameter) {
    // Whether the move is allowed at all is settled first. A refused route
    // must release nothing: letting go of a computer's keys because somebody
    // asked for an impossible route would be a fault of its own.
    const bool allowed =
        toggle ? true
               : (keyboard
                      ? Routes::keyboard_route_is_valid(
                            static_cast<config::KeyboardRoute>(parameter))
                      : Routes::mouse_route_is_valid(static_cast<config::MouseRoute>(parameter)));
    if (!allowed) {
        return false;
    }

    // Does the other device travel too? Only where the leader lands on one
    // computer. A toggle always does; a set does unless it is the keyboard
    // being sent to BOTH, which no pointer can follow - so the mouse stays
    // where it is and synchronisation is paused until the next ordinary
    // switch brings the pair back together.
    const bool leader_lands_on_one_computer =
        toggle || !keyboard ||
        static_cast<config::KeyboardRoute>(parameter) != config::KeyboardRoute::BOTH;
    const bool follower_travels = synchronised_ && leader_lands_on_one_computer;
    const bool moving_keyboard = keyboard || follower_travels;
    const bool moving_mouse = !keyboard || follower_travels;

    Outcome releases;
    release_reached(releases, moving_keyboard, moving_mouse);
    if (outcome.count + releases.count > kMaxActionsPerEvent) return false;

    // Released before the route moves, while "where this reaches" still means
    // the computer being left behind. That machine will never hear about these
    // keys again - and under synchronised control that covers the mouse
    // buttons too, which is why this belongs here and not in Routes.
    release_reached(outcome, moving_keyboard, moving_mouse);
    orphan(moving_keyboard, moving_mouse);

    if (keyboard) {
        if (toggle) {
            routes_.toggle_keyboard();
        } else {
            routes_.set_keyboard(static_cast<config::KeyboardRoute>(parameter));
        }
    } else {
        if (toggle) {
            routes_.toggle_mouse();
        } else {
            routes_.set_mouse(static_cast<config::MouseRoute>(parameter));
        }
    }

    // The follower is placed on the computer the leader reached, rather than
    // toggled in its own right. Two independent toggles would preserve a
    // divergence instead of ending it - and the routes can be diverged, by
    // switching this setting on after they have parted.
    if (follower_travels) {
        if (keyboard) {
            routes_.set_mouse(Routes::mouse_beside(routes_.keyboard()));
        } else {
            routes_.set_keyboard(Routes::keyboard_beside(routes_.mouse()));
        }
    }
    return true;
}
```

- [ ] **Step 5: Запустить тесты движка**

```bash
cmake --build --preset native --clean-first
ctest --preset native -R binding_engine --output-on-failure
```

Ожидается: PASS, включая все шесть новых кейсов и все существующие.

- [ ] **Step 6: Мутационная проверка трёх решений**

Прогнать три мутации по очереди, каждый раз собирая с `--clean-first` и возвращая исходный код:

1. Заменить `if (follower_travels)` на `if (false)` → должен покраснеть `synchronised_switching_puts_both_devices_on_one_computer`.
2. Заменить `leader_lands_on_one_computer` на `true` → должен покраснеть `synchronised_control_leaves_the_mouse_alone_when_the_keyboard_goes_to_both`.
3. Вернуть `release_reached(outcome, keyboard, !keyboard)` и `orphan(keyboard, !keyboard)` → должен покраснеть `a_held_mouse_button_is_released_when_the_keyboard_switch_takes_the_mouse_along`.

```bash
cmake --build --preset native --clean-first
ctest --preset native -R binding_engine --output-on-failure
```

Ожидается: после возврата всех трёх — PASS.

- [ ] **Step 7: Запустить нативный набор целиком**

```bash
cmake --build --preset native --clean-first
ctest --preset native --output-on-failure
```

Ожидается: все PASS.

- [ ] **Step 8: Commit**

```bash
git add firmware/u1_main/mapping/engine.hpp firmware/u1_main/mapping/engine.cpp \
  tests/firmware_native/test_binding_engine.cpp
git commit -m "Move both devices together when synchronised control is on

One place changes a route, so one place carries the rule - and it is the
place that knows what is held, which is what makes the release of a
mouse button safe when a keyboard switch takes the pointer along.

The follower is placed on the computer the leader reached rather than
toggled in its own right: two independent toggles would preserve a
divergence instead of ending it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Флаг доходит до движка, профиль сводит маршруты

**Files:**
- Modify: `firmware/u1_main/core1_runtime.hpp:121-145` (публичная секция профилей)
- Modify: `firmware/u1_main/core1_runtime.cpp:388-426` (`set_profile_now`)
- Modify: `firmware/u1_main/main.cpp:339-350` (`adopt_configuration`), `:437-439` (старт Core 1)
- Test: `tests/firmware_native/test_core1_runtime.cpp`

**Interfaces:**
- Consumes: `BindingEngine::set_synchronised_control`, `BindingEngine::synchronised_control` (задача 3); `Routes::mouse_beside` (задача 2); `StoredProfiles::synchronised_control` (задача 1).
- Produces: `void Core1Runtime::set_synchronised_control(bool)`.

- [ ] **Step 1: Написать падающие тесты активации профиля**

В `tests/firmware_native/test_core1_runtime.cpp` (файл уже определяет `TwoProfiles` с полями `keyboard_zero`/`mouse_zero`/`keyboard_one`/`mouse_one`):

```cpp
TEST_CASE(a_profile_with_parted_routes_is_brought_together_under_synchronised_control) {
    RecordingSink sink;
    TwoProfiles profiles;
    // Stored apart: the configurator does not forbid it, so the device is what
    // settles it - and it settles it before either route is applied.
    profiles.keyboard_one = duo_input::config::KeyboardRoute::PC2;
    profiles.mouse_one = duo_input::config::MouseRoute::PC1;
    Core1Runtime runtime(sink, profiles);
    runtime.set_synchronised_control(true);

    runtime.set_profile_now(1);

    CHECK_EQ(static_cast<int>(runtime.keyboard_route()), static_cast<int>(Route::Pc2));
    CHECK_EQ(static_cast<int>(runtime.mouse_route()), static_cast<int>(Route::Pc2));
}

TEST_CASE(a_profile_on_both_keeps_its_own_mouse_route_under_synchronised_control) {
    RecordingSink sink;
    TwoProfiles profiles;
    profiles.keyboard_one = duo_input::config::KeyboardRoute::BOTH;
    profiles.mouse_one = duo_input::config::MouseRoute::PC2;
    Core1Runtime runtime(sink, profiles);
    runtime.set_synchronised_control(true);

    runtime.set_profile_now(1);

    CHECK_EQ(static_cast<int>(runtime.mouse_route()), static_cast<int>(Route::Pc2));
}

TEST_CASE(a_profile_keeps_parted_routes_when_synchronised_control_is_off) {
    RecordingSink sink;
    TwoProfiles profiles;
    profiles.keyboard_one = duo_input::config::KeyboardRoute::PC2;
    profiles.mouse_one = duo_input::config::MouseRoute::PC1;
    Core1Runtime runtime(sink, profiles);

    runtime.set_profile_now(1);

    CHECK_EQ(static_cast<int>(runtime.keyboard_route()), static_cast<int>(Route::Pc2));
    CHECK_EQ(static_cast<int>(runtime.mouse_route()), static_cast<int>(Route::Pc1));
}
```

`RecordingSink` определён в этом файле (`tests/firmware_native/test_core1_runtime.cpp:86`), `Route` и `keyboard_route()`/`mouse_route()` — существующий публичный API `Core1Runtime` (`core1_runtime.cpp:59-61`).

- [ ] **Step 2: Запустить и убедиться, что падает**

```bash
cmake --build --preset native --clean-first
```

Ожидается: ошибка компиляции — `set_synchronised_control` не член `Core1Runtime`.

- [ ] **Step 3: Пробросить флаг в рантайм**

В `firmware/u1_main/core1_runtime.hpp`, в публичную секцию рядом с `set_profile_now`:

```cpp
    /// Whether the keyboard and the mouse travel together. Comes from the
    /// stored configuration, and must be set before a profile is installed:
    /// installing one under the old setting applies the profile's routes under
    /// the wrong rule.
    void set_synchronised_control(bool synchronised) { engine_.set_synchronised_control(synchronised); }
```

- [ ] **Step 4: Свести профильные маршруты до применения**

В `firmware/u1_main/core1_runtime.cpp`, в `set_profile_now`, заменить блок применения маршрутов (строки 414-423) на:

```cpp
    config::KeyboardRoute keyboard = engine_.keyboard_route();
    config::MouseRoute mouse = engine_.mouse_route();
    if (profiles_.routes_for(profile, keyboard, mouse)) {
        // Under synchronised control a profile names one computer, not two
        // routes that happen to be stored side by side. Settled here, before
        // either is applied: applying the keyboard first carries the mouse
        // with it, and applying the profile's own mouse route afterwards would
        // part them again - the second move undoing the first.
        //
        // BOTH keeps the profile's own mouse route. There is no mouse route
        // that could follow the keyboard there.
        if (engine_.synchronised_control() && keyboard != config::KeyboardRoute::BOTH) {
            mouse = mapping::Routes::mouse_beside(keyboard);
        }
        if (keyboard != engine_.keyboard_route()) {
            apply(engine_.set_keyboard_route(keyboard), now_ms);
        }
        if (mouse != engine_.mouse_route()) {
            apply(engine_.set_mouse_route(mouse), now_ms);
        }
    }
```

Если `mapping/routes.hpp` не подключён в этом файле, добавить `#include "mapping/routes.hpp"` в его шапку.

- [ ] **Step 5: Запустить тесты рантайма**

```bash
cmake --build --preset native --clean-first
ctest --preset native -R core1_runtime --output-on-failure
```

Ожидается: PASS, включая три новых кейса.

- [ ] **Step 6: Подать флаг из конфигурации в `main.cpp`**

В `firmware/u1_main/main.cpp`, в `adopt_configuration`, между загрузкой и установкой профиля:

```cpp
bool adopt_configuration(duo_input::protocol::ByteView package) {
    g_runtime.release_all();
    const bool loaded = g_profiles.load(package);
    // Before the profile, not after: set_profile_now applies the profile's
    // stored routes, and whether those two routes mean one computer or two is
    // exactly what this setting decides.
    g_runtime.set_synchronised_control(loaded && g_profiles.synchronised_control());
    // A package that is not a configuration leaves StoredProfiles empty, which
    // is also what a factory reset asks for. Profile zero is what an empty
    // configuration answers to.
    const std::uint8_t profile = loaded ? g_profiles.active_profile_id() : 0;
    g_runtime.set_profile_now(profile);
    install_macros(profile);
    return loaded;
}
```

И на старте Core 1 (строка 437), перед `set_profile_now`:

```cpp
    g_runtime.set_synchronised_control(g_profiles.synchronised_control());
    std::uint8_t installed = g_profiles.active_profile_id();
    g_runtime.set_profile_now(installed);
    install_macros(installed);
```

Оба вызова безопасны как обычные: `adopt_configuration` исполняется либо до запуска Core 1, либо на самом Core 1 через `g_config_handoff` (`main.cpp:362-370`, `:465`), а этот блок — код самого Core 1. Межъядерной синхронизации здесь не нужно, и добавлять её не надо.

- [ ] **Step 7: Проверить сборку прошивки**

```bash
cmake --build --preset native --clean-first
ctest --preset native --output-on-failure
cmake --build --preset pico-pio-usb-release --clean-first
```

Ожидается: нативный набор зелёный, прошивка компонуется без ошибок.

- [ ] **Step 8: Мутационная проверка сведения**

Временно убрать условие сведения (строку `mouse = mapping::Routes::mouse_beside(keyboard);`) и убедиться, что краснеет `a_profile_with_parted_routes_is_brought_together_under_synchronised_control`. Затем временно убрать `&& keyboard != config::KeyboardRoute::BOTH` и убедиться, что краснеет `a_profile_on_both_keeps_its_own_mouse_route_under_synchronised_control`. Вернуть обе строки.

```bash
cmake --build --preset native --clean-first
ctest --preset native -R core1_runtime --output-on-failure
```

- [ ] **Step 9: Commit**

```bash
git add firmware/u1_main/core1_runtime.hpp firmware/u1_main/core1_runtime.cpp \
  firmware/u1_main/main.cpp tests/firmware_native/test_core1_runtime.cpp
git commit -m "Apply a profile as one computer under synchronised control

A profile stores both routes, and they can be stored apart. Applying
them one after the other would have the second move undo the first, so
the pair is settled before either is applied.

The setting reaches the engine before the profile does: a profile
installed under the old setting is a profile applied under the wrong
rule.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Флаг в бинарном формате конфигуратора

**Files:**
- Modify: `configurator/src/duo_input/domain/models.py:77-81` (`DeviceConfig`)
- Modify: `configurator/src/duo_input/domain/config_binary.py:24` (импорты), `:138-152` (`_validate_model`), `:346-349` (упаковка заголовка), `:385-394` (распаковка), `:530` (сборка `DeviceConfig`)
- Test: `configurator/tests/test_config_binary.py`

**Interfaces:**
- Consumes: `duo_input.generated.protocol.ConfigFlag` из задачи 1.
- Produces: `DeviceConfig.synchronised_control: bool` (поле со значением по умолчанию `False`); `config_binary.KNOWN_CONFIG_FLAGS: int`. Задача 6 читает поле, задача 7 — нет.

- [ ] **Step 1: Написать падающие тесты**

В `configurator/tests/test_config_binary.py`:

```python
def test_the_synchronised_control_flag_survives_a_round_trip():
    from dataclasses import replace

    from duo_input.domain.config_binary import compile_device_config, decode_device_config
    from duo_input.generated.protocol import ConfigFlag

    config = minimal_config()
    assert decode_device_config(compile_device_config(config)).synchronised_control is False

    synchronised = replace(config, synchronised_control=True)
    package = compile_device_config(synchronised)

    assert package[6] == ConfigFlag.SYNCHRONISED_CONTROL
    assert decode_device_config(package).synchronised_control is True


def test_header_flags_this_build_does_not_know_are_rejected():
    import zlib

    from duo_input.domain.config_binary import ConfigError, compile_device_config, decode_device_config

    package = bytearray(compile_device_config(minimal_config()))
    package[6] = 0x02
    package[12:16] = b"\0" * 4
    package[12:16] = zlib.crc32(bytes(package)).to_bytes(4, "little")

    with pytest.raises(ConfigError):
        decode_device_config(bytes(package))
```

`minimal_config()` уже определён в этом файле (`configurator/tests/test_config_binary.py:45`) и возвращает восьмипрофильную конфигурацию — импортировать ничего не нужно.

- [ ] **Step 2: Запустить и убедиться, что падает**

```bash
python -m pytest configurator/tests/test_config_binary.py -q -k synchronised
```

Ожидается: FAIL — `DeviceConfig.__init__() got an unexpected keyword argument 'synchronised_control'`.

- [ ] **Step 3: Добавить поле в модель**

В `configurator/src/duo_input/domain/models.py`:

```python
@dataclass(frozen=True)
class DeviceConfig:
    active_profile_id: int
    profiles: tuple[Profile, ...]
    #: Every route change moves the keyboard and the mouse to one computer.
    synchronised_control: bool = False
```

Значение по умолчанию обязательно: `DeviceConfig` конструируется позиционно в `config_binary.py:530` и в `text_compiler.py:73`.

- [ ] **Step 4: Упаковать и распаковать бит**

В `configurator/src/duo_input/domain/config_binary.py` добавить `ConfigFlag` в список импортируемого из `duo_input.generated.protocol` (блок на строке 24) и рядом с константами файла:

```python
#: Every header flag this build understands. A package carrying anything else
#: was written by a newer configurator, and the bit's meaning cannot be
#: guessed - so the package is refused rather than read without it.
KNOWN_CONFIG_FLAGS = int(ConfigFlag.SYNCHRONISED_CONTROL)
```

В `_validate_model`, сразу после проверки `config.profiles`:

```python
    if not isinstance(config.synchronised_control, bool):
        raise ConfigError("synchronised control must be a bool")
```

В `compile_device_config` заменить упаковку заголовка (строка 346):

```python
    flags = int(ConfigFlag.SYNCHRONISED_CONTROL) if config.synchronised_control else 0
    _HEADER.pack_into(
        package, 0, MAGIC, SCHEMA_VERSION_MAJOR, SCHEMA_VERSION_MINOR, flags, 0,
        total_length, 0, PROFILES, config.active_profile_id, PROFILE_SIZE, 0,
        HEADER_SIZE, string_start, len(string_blob), data_start, len(data), b"\0" * 24,
    )
```

В `decode_device_config` заменить проверку флагов (строка 393):

```python
    if flags & ~KNOWN_CONFIG_FLAGS:
        raise ConfigError("unknown header flags")
    if reserved != 0 or header_reserved != 0 or reserved_tail != b"\0" * 24:
        raise ConfigError("reserved bytes must be zero")
```

и сборку результата (строка 530):

```python
    result = DeviceConfig(
        active_profile_id,
        tuple(profiles),
        bool(flags & ConfigFlag.SYNCHRONISED_CONTROL),
    )
```

- [ ] **Step 5: Запустить тесты**

```bash
python -m pytest configurator/tests/test_config_binary.py configurator/tests/test_config_reader.py -q
```

Ожидается: PASS.

- [ ] **Step 6: Мутационная проверка**

Временно заменить `if flags & ~KNOWN_CONFIG_FLAGS:` на `if False:` — должен покраснеть `test_header_flags_this_build_does_not_know_are_rejected`. Затем временно заменить `flags = ... if config.synchronised_control else 0` на `flags = 0` — должен покраснеть `test_the_synchronised_control_flag_survives_a_round_trip`. Вернуть обе строки и перезапустить.

- [ ] **Step 7: Прогнать весь Python-набор**

```bash
python -m pytest configurator/tests tests -q
```

Ожидается: PASS. Симметрия с прошивкой проверяется тем, что оба набора зелены на одном и том же байте 6.

- [ ] **Step 8: Commit**

```bash
git add configurator/src/duo_input/domain/models.py \
  configurator/src/duo_input/domain/config_binary.py \
  configurator/tests/test_config_binary.py
git commit -m "Read and write the synchronised-control flag in the package header

Symmetrical with the firmware validator, down to which bits are refused:
a header flag neither side knows rejects the package on both.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Флаг в файле проекта, с миграцией

**Files:**
- Modify: `configurator/src/duo_input/domain/models.py:83-89` (`DeviceProject`)
- Modify: `configurator/src/duo_input/domain/project_store.py:33-35` (версия и таблица миграций), `:88-112` (`_migrate_document`), `:123-127` (миграции), `:129-135` (`_project_to_json`), `:194-202` (`_project_from_json`)
- Modify: `configurator/src/duo_input/domain/text_compiler.py:72-76` (`_resolved_config`)
- Modify: `configurator/src/duo_input/domain/config_reader.py:38-45` (`binary_to_project`)
- Test: `configurator/tests/domain/test_project_store.py`, `configurator/tests/domain/test_text_compiler.py`, `configurator/tests/test_config_reader.py`

**Interfaces:**
- Consumes: `DeviceConfig.synchronised_control` (задача 5).
- Produces: `DeviceProject.synchronised_control: bool` (по умолчанию `False`). Задача 7 читает и пишет его через команду сессии.

- [ ] **Step 1: Написать падающие тесты проекта**

В `configurator/tests/domain/test_project_store.py`:

```python
def test_synchronised_control_survives_saving_and_loading(tmp_path):
    from dataclasses import replace

    from duo_input.domain.project_store import load_project, save_project_atomic
    from duo_input.ui.models.project_session import default_project

    path = tmp_path / "sync.duoinput.json"
    save_project_atomic(replace(default_project(), synchronised_control=True), path)

    assert load_project(path).synchronised_control is True


def test_a_project_written_before_the_setting_existed_loads_with_it_off(tmp_path):
    import json

    from duo_input.domain.project_store import load_project, save_project_atomic
    from duo_input.ui.models.project_session import default_project

    path = tmp_path / "old.duoinput.json"
    save_project_atomic(default_project(), path)
    document = json.loads(path.read_text("utf-8"))
    # What a 1.1 project on disk actually looks like: the key is absent, not
    # false. A reader that demands it would refuse to open a file that was
    # valid when it was written.
    document["schema_version"] = "1.1"
    document.pop("synchronised_control", None)
    path.write_text(json.dumps(document), encoding="utf-8")

    assert load_project(path).synchronised_control is False
```

- [ ] **Step 2: Запустить и убедиться, что падает**

```bash
python -m pytest configurator/tests/domain/test_project_store.py -q -k synchronised
```

Ожидается: FAIL — `DeviceProject.__init__() got an unexpected keyword argument 'synchronised_control'`.

- [ ] **Step 3: Добавить поле в проект**

В `configurator/src/duo_input/domain/models.py`:

```python
@dataclass(frozen=True)
class DeviceProject:
    """The editable, versioned source project kept on the host computer."""

    schema_version: str
    active_profile_id: int
    profiles: tuple[Profile, ...]
    #: Every route change moves the keyboard and the mouse to one computer.
    synchronised_control: bool = False
```

- [ ] **Step 4: Поднять версию проекта и добавить миграцию**

В `configurator/src/duo_input/domain/project_store.py`:

```python
PROJECT_SCHEMA_VERSION = "1.2"
_SUPPORTED_SCHEMA_MAJOR = 1
_SUPPORTED_OLDER_MINORS = {0, 1}
```

В `_migrate_document`, в цикле миграций:

```python
    while minor < current_minor:
        if minor == 0:
            migrated = _migrate_1_0_to_1_1(migrated)
            minor = 1
        elif minor == 1:
            migrated = _migrate_1_1_to_1_2(migrated)
            minor = 2
        else:  # pragma: no cover - guarded by supported migration table
            raise ProjectVersionError(f"unsupported project schema version {version}")
```

Рядом с `_migrate_1_0_to_1_1`:

```python
def _migrate_1_1_to_1_2(document: dict[str, Any]) -> dict[str, Any]:
    # 1.1 had no such setting, and a project written then meant the devices
    # switched apart - which is what its absence says here.
    migrated = dict(document)
    migrated["synchronised_control"] = False
    migrated["schema_version"] = PROJECT_SCHEMA_VERSION
    return migrated
```

Обратить внимание: `_migrate_1_0_to_1_1` сейчас ставит `PROJECT_SCHEMA_VERSION` напрямую, что с двумя миграциями подряд стало бы враньём о проделанном шаге. Заменить его тело на:

```python
def _migrate_1_0_to_1_1(document: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(document)
    migrated["schema_version"] = "1.1"
    return migrated
```

- [ ] **Step 5: Сериализовать поле**

В `_project_to_json`:

```python
def _project_to_json(project: DeviceProject) -> dict[str, Any]:
    return {
        "active_profile_id": project.active_profile_id,
        "profiles": [_profile_to_json(profile) for profile in project.profiles],
        "schema_version": PROJECT_SCHEMA_VERSION,
        "synchronised_control": project.synchronised_control,
    }
```

В `_project_from_json`:

```python
            profiles=tuple(_profile_from_json(value) for value in _required(document, "profiles", list)),
            synchronised_control=bool(document.get("synchronised_control", False)),
```

- [ ] **Step 6: Донести поле до бинаря и обратно**

В `configurator/src/duo_input/domain/text_compiler.py`:

```python
def _resolved_config(project: DeviceProject) -> DeviceConfig:
    return DeviceConfig(
        active_profile_id=project.active_profile_id,
        profiles=tuple(_resolved_profile(profile) for profile in project.profiles),
        synchronised_control=project.synchronised_control,
    )
```

В `configurator/src/duo_input/domain/config_reader.py`:

```python
    return DeviceProject(
        schema_version=PROJECT_SCHEMA_VERSION,
        active_profile_id=config.active_profile_id,
        profiles=config.profiles,
        synchronised_control=config.synchronised_control,
    )
```

- [ ] **Step 7: Написать тест сквозного пути и запустить всё**

В `configurator/tests/domain/test_text_compiler.py`:

```python
def test_synchronised_control_reaches_the_compiled_package():
    from dataclasses import replace

    from duo_input.domain.config_reader import binary_to_project
    from duo_input.domain.text_compiler import compile_project_to_binary
    from duo_input.ui.models.project_session import default_project

    package = compile_project_to_binary(replace(default_project(), synchronised_control=True))

    assert binary_to_project(package).synchronised_control is True
```

```bash
python -m pytest configurator/tests tests -q
```

Ожидается: PASS.

- [ ] **Step 8: Мутационная проверка миграции**

Временно убрать ветку `elif minor == 1:` из `_migrate_document` — должен покраснеть `test_a_project_written_before_the_setting_existed_loads_with_it_off`. Временно убрать `synchronised_control=project.synchronised_control` из `_resolved_config` — должен покраснеть `test_synchronised_control_reaches_the_compiled_package`. Вернуть обе правки.

- [ ] **Step 9: Commit**

```bash
git add configurator/src/duo_input/domain/models.py \
  configurator/src/duo_input/domain/project_store.py \
  configurator/src/duo_input/domain/text_compiler.py \
  configurator/src/duo_input/domain/config_reader.py \
  configurator/tests/domain/test_project_store.py \
  configurator/tests/domain/test_text_compiler.py
git commit -m "Keep synchronised control in the project file

Project schema 1.2. A 1.1 project has no such key, and its absence means
the devices switched apart - which is what it meant when the file was
written.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Чекбокс в интерфейсе

**Files:**
- Modify: `configurator/src/duo_input/ui/models/project_session.py:114-122` (рядом с `SetActiveProfile`)
- Modify: `configurator/src/duo_input/ui/mouse.py:78-124` (конструктор страницы), новый метод `_build_synchronised`
- Modify: `configurator/src/duo_input/ui/profiles.py:245-268` (`_refresh_editor`)
- Test: `configurator/tests/ui/test_mouse_switch.py`, `configurator/tests/ui/test_project_commands.py`, `configurator/tests/ui/test_profiles.py`
- Modify: файлы переводов через `python tools/update_translations.py`

**Interfaces:**
- Consumes: `DeviceProject.synchronised_control` (задача 6).
- Produces: `SetSynchronisedControl(enabled: bool)` — команда сессии; `MouseSwitchPage.synchronised_check` — сам `QCheckBox`, к которому обращаются тесты.

- [ ] **Step 1: Написать падающий тест команды**

В `configurator/tests/ui/test_project_commands.py`:

```python
def test_set_synchronised_control_changes_only_that_setting():
    from duo_input.ui.models.project_session import SetSynchronisedControl, default_project

    project = default_project()
    changed = SetSynchronisedControl(True).apply_to(project)

    assert changed.synchronised_control is True
    assert changed.profiles == project.profiles
    assert changed.active_profile_id == project.active_profile_id
    assert SetSynchronisedControl(False).apply_to(changed).synchronised_control is False
```

- [ ] **Step 2: Запустить и убедиться, что падает**

```bash
python -m pytest configurator/tests/ui/test_project_commands.py -q -k synchronised
```

Ожидается: FAIL — `ImportError: cannot import name 'SetSynchronisedControl'`.

- [ ] **Step 3: Добавить команду**

В `configurator/src/duo_input/ui/models/project_session.py`, сразу после `SetActiveProfile`:

```python
@dataclass(frozen=True)
class SetSynchronisedControl:
    """Switch the keyboard and the mouse between travelling together or apart."""

    enabled: bool

    def apply_to(self, project: DeviceProject) -> DeviceProject:
        return replace(project, synchronised_control=self.enabled)
```

- [ ] **Step 4: Запустить тест команды**

```bash
python -m pytest configurator/tests/ui/test_project_commands.py -q -k synchronised
```

Ожидается: PASS.

- [ ] **Step 5: Написать падающие тесты страницы**

В `configurator/tests/ui/test_mouse_switch.py`:

```python
def test_the_page_offers_a_synchronised_control_switch(page):
    assert page.synchronised_check.isChecked() is False
    assert "together" in page.synchronised_check.text().lower()


def test_clicking_the_real_checkbox_asks_for_synchronised_control(page, qtbot):
    # Through the widget, not the model. A test that sets the field directly
    # passes with the handler entirely disconnected.
    with qtbot.waitSignal(page.command_requested) as blocker:
        QTest.mouseClick(
            page.synchronised_check,
            Qt.MouseButton.LeftButton,
            pos=QPoint(6, page.synchronised_check.height() // 2),
        )

    command = blocker.args[0]
    assert isinstance(command, SetSynchronisedControl)
    assert command.enabled is True


def test_the_checkbox_shows_what_the_project_already_says(page):
    from dataclasses import replace

    from duo_input.ui.models.project_session import default_project

    page.set_session(
        ProjectSession(project=replace(default_project(), synchronised_control=True))
    )

    assert page.synchronised_check.isChecked() is True


def test_reloading_the_session_does_not_ask_for_a_change(page, qtbot):
    # Setting the checkbox from the project must not echo back as an edit: a
    # toggled() that fires on every load rewrites the project on open.
    from dataclasses import replace

    from duo_input.ui.models.project_session import default_project

    session = ProjectSession(project=replace(default_project(), synchronised_control=True))

    with qtbot.assertNotEmitted(page.command_requested):
        page.set_session(session)
```

Добавить в импорты файла `SetSynchronisedControl` из `duo_input.ui.models.project_session`. `ProjectSession` — frozen dataclass (`project_session.py:453`), поэтому сессия с другим проектом **конструируется**, а не правится на месте: `ProjectSession(project=replace(default_project(), synchronised_control=True))`.

- [ ] **Step 6: Запустить и убедиться, что падает**

```bash
python -m pytest configurator/tests/ui/test_mouse_switch.py -q -k synchronised
```

Ожидается: FAIL — `AttributeError: 'MouseSwitchPage' object has no attribute 'synchronised_check'`.

- [ ] **Step 7: Добавить чекбокс на страницу**

В `configurator/src/duo_input/ui/mouse.py` добавить `QCheckBox` в импорты из `PySide6.QtWidgets` и `SetSynchronisedControl` в импорт из `duo_input.ui.models.project_session`. В конструкторе, в `body_layout`, перед редактором:

```python
        body_layout.addWidget(self._build_synchronised())
        body_layout.addWidget(self._build_editor())
```

И новый метод рядом с `_build_editor`:

```python
    def _build_synchronised(self) -> QWidget:
        box = QGroupBox(self.tr("Both devices at once"), self)
        box.setMaximumWidth(820)
        layout = QVBoxLayout(box)
        layout.setSpacing(SPACE_SM)

        self.synchronised_check = QCheckBox(
            self.tr("Switch the keyboard and the mouse together"), box
        )
        self.synchronised_check.setAccessibleName(
            self.tr("Switch the keyboard and the mouse together")
        )
        self.synchronised_check.toggled.connect(self._on_synchronised_toggled)
        layout.addWidget(self.synchronised_check)

        # The fields around this one belong to a profile. Without saying so,
        # this reads as another of them.
        hint = QLabel(
            self.tr(
                "Switching either the keyboard or the mouse sends both devices to "
                "the same computer. This setting applies to every profile."
            ),
            box,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return box

    def _on_synchronised_toggled(self, enabled: bool) -> None:
        # _updating is raised while the page is being filled in from a project;
        # without it, loading a project that has this on would emit an edit
        # that rewrites the project on open.
        if self._updating:
            return
        self.command_requested.emit(SetSynchronisedControl(enabled))
```

Добавить `QLabel` в импорты из `PySide6.QtWidgets`, если его там нет.

- [ ] **Step 8: Заполнять чекбокс из проекта**

В `MouseSwitchPage._refresh` (метод, который обновляет страницу по сессии; `grep -n "def _refresh" configurator/src/duo_input/ui/mouse.py`) внутри уже существующего участка, где `self._updating = True`, добавить:

```python
        self.synchronised_check.setChecked(self._session.project.synchronised_control)
```

Если `_refresh` не поднимает `self._updating`, обернуть именно эту строку:

```python
        self._updating = True
        self.synchronised_check.setChecked(self._session.project.synchronised_control)
        self._updating = False
```

- [ ] **Step 9: Запустить тесты страницы**

```bash
python -m pytest configurator/tests/ui/test_mouse_switch.py -q
```

Ожидается: PASS, включая все четыре новых кейса и все существующие.

- [ ] **Step 10: Написать падающий тест пометки на странице профилей**

В `configurator/tests/ui/test_profiles.py`:

```python
def test_profile_routes_say_when_the_device_will_bring_them_together(qtbot):
    from dataclasses import replace

    from duo_input.ui.models.project_session import ProjectSession
    from duo_input.ui.profiles import ProfilesPage

    from duo_input.ui.models.project_session import default_project

    page = ProfilesPage()
    qtbot.addWidget(page)
    page.set_session(
        ProjectSession(project=replace(default_project(), synchronised_control=True))
    )

    # The device settles the pair when the profile is activated, so a page
    # showing "PC1 / PC2" with nothing else would be showing routes the
    # operator will never have.
    assert "together" in page.routes_label.text().lower()
```

Виджет — `ProfilesPage.routes_label`, заполняется в `_refresh_editor` (`profiles.py:253-256`). Строка в нём состоит из трёх частей: маршрут клавиатуры, маршрут мыши и раскладка.

- [ ] **Step 11: Запустить и убедиться, что падает**

```bash
python -m pytest configurator/tests/ui/test_profiles.py -q -k together
```

Ожидается: FAIL — в тексте нет пометки.

- [ ] **Step 12: Добавить пометку**

В `configurator/src/duo_input/ui/profiles.py`, в `_refresh_editor`, заменить формирование строки маршрутов на:

```python
            routes = (
                f"{profile.keyboard_route.name} / {profile.mouse_route.name}"
                f" / {profile.text_layout.name}"
            )
            if self._session.project.synchronised_control:
                # The device brings the pair together when this profile is
                # activated. Showing the stored pair alone would promise routes
                # the operator will never actually have.
                routes = self.tr("{0} — brought together on activation").format(routes)
            self.routes_label.setText(routes)
```

Это заменяет существующий вызов `self.routes_label.setText(f"...")` целиком; отступ — внутри `try:` блока метода.

- [ ] **Step 13: Запустить весь UI-набор**

```bash
python -m pytest configurator/tests/ui -q
```

Ожидается: PASS.

- [ ] **Step 14: Мутационная проверка обработчика**

Временно закомментировать строку `self.synchronised_check.toggled.connect(self._on_synchronised_toggled)` — должен покраснеть `test_clicking_the_real_checkbox_asks_for_synchronised_control`. Затем временно убрать ранний `return` по `self._updating` — должен покраснеть `test_reloading_the_session_does_not_ask_for_a_change`. Вернуть обе правки.

```bash
python -m pytest configurator/tests/ui/test_mouse_switch.py -q
```

- [ ] **Step 15: Обновить переводы**

```bash
python tools/update_translations.py
```

Перевести новые строки на русский дословно по разделу Global Constraints: «Переключать клавиатуру и мышь вместе», «При переключении клавиатуры или мыши оба устройства будут направлены на один компьютер. Настройка действует для всех профилей.», заголовок группы — «Оба устройства сразу», пометка профиля — «{0} — сводятся при активации». Прогнать `python -m pytest configurator/tests/ui/test_localization.py -q`.

- [ ] **Step 16: Commit**

```bash
git add configurator/src/duo_input/ui/models/project_session.py \
  configurator/src/duo_input/ui/mouse.py configurator/src/duo_input/ui/profiles.py \
  configurator/tests/ui/test_mouse_switch.py configurator/tests/ui/test_project_commands.py \
  configurator/tests/ui/test_profiles.py
git add -A configurator/src/duo_input/i18n.py configurator/**/*.ts 2>/dev/null || true
git commit -m "Offer synchronised control on the mouse switching page

The checkbox sits where somebody arrives asking how switching works, and
says out loud that it is not a profile setting - every field around it
is one.

The profiles page now says when the device will bring a profile's stored
routes together, rather than showing a pair the operator will never have.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Финальная проверка и приёмка

**Files:**
- Create: `docs/superpowers/records/2026-09-12-synchronised-control-acceptance.md`

**Interfaces:**
- Consumes: всё из задач 1-7.
- Produces: запись о приёмке.

- [ ] **Step 1: Прогнать нативный набор начисто**

```bash
cmake --build --preset native --clean-first
ctest --preset native --output-on-failure
```

Записать точное число пройденных тестов из вывода `ctest`.

- [ ] **Step 2: Прогнать Python-наборы целиком**

```bash
python -m pytest configurator/tests tests -q
```

Записать точное число пройденных тестов.

- [ ] **Step 3: Проверить, что генератор чист**

```bash
python tools/generate_protocol.py --check
```

Ожидается: пустой вывод, код возврата 0.

- [ ] **Step 4: Собрать прошивку**

```bash
cmake --build --preset pico-pio-usb-release --clean-first
```

Ожидается: сборка без ошибок, `.uf2` в `build/pico-pio-usb-release`.

- [ ] **Step 5: Прошить и проверить таблицу переходов на железе**

Прошить U1, **выполнить цикл питания** (без него хост ничего не увидит, и это не свидетельство о прошивке). В конфигураторе включить чекбокс, записать конфигурацию, и пройти все восемь строк таблицы из раздела «Приёмка на железе» спецификации, отмечая фактический результат каждой.

- [ ] **Step 6: Проверить два физических сценария**

1. Зажать `Shift` на ПК1 → переключиться на ПК2 → убедиться, что на ПК1 `Shift` отпущен (набрать там текст и проверить регистр).
2. Зажать левую кнопку мыши на ПК1 → переключиться **клавиатурной** кнопкой на ПК2 → убедиться, что на ПК1 кнопка отпущена (выделение не тянется).

- [ ] **Step 7: Проверить сценарий, ради которого всё делалось**

`Ctrl+C` на ПК1 → одно нажатие переключения → `Ctrl+V` на ПК2. Содержимое должно вставиться, и второго переключения не потребоваться.

- [ ] **Step 8: Записать приёмку**

Создать `docs/superpowers/records/2026-09-12-synchronised-control-acceptance.md` по образцу существующих записей в `docs/superpowers/records/`: дата, версия прошивки, точные числа пройденных тестов из шагов 1-2, таблица из восьми строк с фактическими результатами, исход обоих физических сценариев и сценария с буфером обмена. Любое расхождение с ожидаемым — не «мелочь на потом», а незакрытая задача: записать его и вернуться к соответствующей задаче плана.

- [ ] **Step 9: Commit**

```bash
git add docs/superpowers/records/2026-09-12-synchronised-control-acceptance.md
git commit -m "Record synchronised control acceptance

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Замечания для исполнителя

**Порядок задач нежёсткий только между 5 и 6.** Всё остальное — цепочка: задача 3 использует `Routes::mouse_beside` из задачи 2, задача 4 — `set_synchronised_control` из задачи 3 и `StoredProfiles::synchronised_control` из задачи 1, задача 7 — поле проекта из задачи 6.

**Прошивка полностью работоспособна после задачи 4.** Устройство с флагом в конфиге уже переключает оба устройства; задачи 5-7 дают способ этот флаг поставить из интерфейса. Если работа прервётся между 4 и 5, дерево остаётся зелёным и осмысленным.

**Три места, где легко ошибиться:**

1. **Порядок в `main.cpp`** — `set_synchronised_control` строго до `set_profile_now`. Иначе профиль применится по старому правилу, и первое же включение синхрона даст разведённую пару до первого переключения.
2. **`_updating` в чекбоксе** — без него загрузка проекта с включённой настройкой сама эмитит правку и переписывает проект при открытии. Тест `test_reloading_the_session_does_not_ask_for_a_change` держит именно это.
3. **Ведомое устройство ставится, а не тоглится** — в `move_route` последний блок обязан быть `set_mouse(mouse_beside(...))`, а не `toggle_mouse()`. Разница видна только на двух разведённых строках таблицы, и ровно они есть в табличном тесте.
