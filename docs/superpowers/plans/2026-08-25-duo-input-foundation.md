# Duo Input Protocol and Configuration Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Создать единый тестируемый фундамент протоколов CDC/SPI и бинарной конфигурации, используемый прошивками U1/U2 и Windows-конфигуратором.

**Architecture:** Один JSON schema-файл является source-of-truth числовых идентификаторов и лимитов. Генератор создаёт C++ header и Python module; независимые кодеки используют общие golden vectors. Python-эмулятор U1 позволяет разрабатывать конфигуратор до готовности железа.

**Tech Stack:** C++17, CMake 3.22+, CTest, Python 3.12, pytest 8+, standard-library JSON/dataclasses, SHA-256, COBS, CRC-16/CRC-32.

**Spec:** `docs/superpowers/specs/2026-08-25-duo-input-firmware-configurator-design.md`

## Global Constraints

- Конфигурация доступна только через U1/ПК1; U2 не имеет CDC.
- CDC payload ≤1024 байт; config chunks ровно ≤512 байт; SPI frame ровно 64 байта.
- Binary config package ≤360 КиБ; 8 профилей; ≤128 bindings/profile; ≤32 macros/profile; ≤64 steps/macro.
- Major-версии несовместимы; minor-версии работают только по пересечению capabilities.
- Парсеры обязаны проверять длину до доступа к payload; неизвестные type/flags отклоняются.
- В production-коде протоколов не должно быть числовых идентификаторов, продублированных вручную между C++ и Python.
- Каждый task выполняется через red-green-refactor и заканчивается отдельным коммитом.

## Locked File Structure

```text
protocol/schema.json                         числовые IDs, версии и лимиты
tools/generate_protocol.py                   schema → C++/Python
firmware/common/protocol/generated.hpp       generated C++ constants
firmware/common/protocol/{cobs,crc,frame}.*  transport-independent codecs
firmware/common/config/{format,validator}.*  binary config reader
configurator/src/duo_input/generated/protocol.py
configurator/src/duo_input/protocol/{cobs,crc,frame}.py
configurator/src/duo_input/domain/config_binary.py
configurator/src/duo_input/device/emulator.py
tests/firmware_native/                       host C++ tests
configurator/tests/                          Python tests
tests/vectors/                               shared JSON golden vectors
```

---

### Task 1: Native and Python test skeletons

**Files:**
- Create: `CMakeLists.txt`
- Create: `firmware/common/CMakeLists.txt`
- Create: `tests/firmware_native/CMakeLists.txt`
- Create: `tests/firmware_native/test_support.hpp`
- Create: `tests/firmware_native/test_main.cpp`
- Create: `tests/firmware_native/test_smoke.cpp`
- Create: `configurator/pyproject.toml`
- Create: `configurator/src/duo_input/__init__.py`
- Create: `configurator/tests/test_smoke.py`

**Interfaces:**
- Produces: CMake target `duo_common`, CTest test `firmware_native`, Python package `duo_input`, pytest configuration.

- [ ] **Step 1: Write failing smoke tests**

```cpp
// tests/firmware_native/test_smoke.cpp
#include "test_support.hpp"
TEST_CASE(smoke_arithmetic) { CHECK_EQ(2 + 2, 4); }
```

```python
def test_package_version_exists():
    import duo_input
    assert duo_input.__version__ == "0.1.0"
```

- [ ] **Step 2: Verify red state**

Run:

```powershell
cmake -S . -B build/native -DDUO_NATIVE_TESTS=ON
cmake --build build/native
python -m pytest configurator/tests/test_smoke.py -q
```

Expected: C++ configure/build fails because the native test target is absent; Python fails because `duo_input` is not installable.

- [ ] **Step 3: Add minimal build/package files**

Set C++17, enable CTest only under `DUO_NATIVE_TESTS`, and implement a dependency-free registrar in `test_support.hpp`/`test_main.cpp` providing `TEST_CASE(name)`, `CHECK(expr)`, `CHECK_FALSE(expr)` and `CHECK_EQ(actual, expected)`. The single `main()` iterates registered cases and returns nonzero on any failure. Also create:

```python
# configurator/src/duo_input/__init__.py
__version__ = "0.1.0"
```

`configurator/pyproject.toml` must use `setuptools`, `package-dir = {"" = "src"}`, Python `>=3.12,<3.13`, and pytest `pythonpath = ["src"]`.

- [ ] **Step 4: Verify green state**

Run `ctest --test-dir build/native --output-on-failure` and `python -m pytest configurator/tests -q`.

Expected: both smoke suites pass.

- [ ] **Step 5: Commit**

```powershell
git add CMakeLists.txt firmware/common tests/firmware_native configurator
git commit -m "build: add duo input native and Python test skeletons"
```

### Task 2: Protocol source-of-truth generator

**Files:**
- Create: `protocol/schema.json`
- Create: `tools/generate_protocol.py`
- Create: `firmware/common/protocol/generated.hpp`
- Create: `configurator/src/duo_input/generated/__init__.py`
- Create: `configurator/src/duo_input/generated/protocol.py`
- Create: `configurator/tests/test_generated_protocol.py`

**Interfaces:**
- Produces: `ProtocolLimits`, `CdcMessageType`, `SpiMessageType`, `MacroStepType`, `Capability` in both languages.
- Produces: command `python tools/generate_protocol.py --check` returning nonzero on stale generated files.

- [ ] **Step 1: Write schema parity test**

```python
def test_generated_values_match_schema():
    schema = json.loads(Path("protocol/schema.json").read_text("utf-8"))
    assert protocol.CDC_MAX_PAYLOAD == schema["limits"]["cdc_max_payload"] == 1024
    assert protocol.SPI_FRAME_SIZE == 64
    assert protocol.CdcMessageType.HELLO.value == schema["cdc_messages"]["HELLO"]
```

- [ ] **Step 2: Verify failure**

Run `python -m pytest configurator/tests/test_generated_protocol.py -q`.

Expected: FAIL because schema and generated module do not exist.

- [ ] **Step 3: Implement deterministic generation**

`schema.json` must define protocol/schema major/minor, all message IDs from the spec, config limits, capability bits and macro step IDs. Generator must sort keys, include `// generated; do not edit` / `# generated; do not edit`, and write only when bytes differ.

Core API:

```python
def render_cpp(schema: dict[str, object]) -> str: ...
def render_python(schema: dict[str, object]) -> str: ...
def main(argv: list[str] | None = None) -> int: ...
```

- [ ] **Step 4: Generate and verify parity**

Run:

```powershell
python tools/generate_protocol.py
python tools/generate_protocol.py --check
python -m pytest configurator/tests/test_generated_protocol.py -q
cmake --build build/native
ctest --test-dir build/native --output-on-failure
```

Expected: all commands pass and second generator invocation produces no diff.

- [ ] **Step 5: Commit**

```powershell
git add protocol tools firmware/common/protocol configurator/src/duo_input/generated configurator/tests/test_generated_protocol.py
git commit -m "feat: generate shared protocol identifiers"
```

### Task 3: Shared COBS and CRC golden vectors

**Files:**
- Create: `tests/vectors/transport_vectors.json`
- Create: `firmware/common/protocol/bytes.hpp`
- Create: `firmware/common/protocol/cobs.hpp`
- Create: `firmware/common/protocol/cobs.cpp`
- Create: `firmware/common/protocol/crc.hpp`
- Create: `firmware/common/protocol/crc.cpp`
- Create: `tests/firmware_native/test_transport.cpp`
- Create: `configurator/src/duo_input/protocol/__init__.py`
- Create: `configurator/src/duo_input/protocol/cobs.py`
- Create: `configurator/src/duo_input/protocol/crc.py`
- Create: `configurator/tests/test_transport_vectors.py`

**Interfaces:**
- Produces C++: `bool cobs_encode(ByteView, MutableByteView, size_t&)`, `bool cobs_decode(...)`, `uint16_t crc16_ccitt(ByteView)`, `uint32_t crc32_ieee(ByteView)` where views are pointer+length structs compatible with C++17.
- Produces Python: `cobs_encode(data: bytes) -> bytes`, `cobs_decode(data: bytes) -> bytes`, `crc16_ccitt(data: bytes) -> int`, `crc32_ieee(data: bytes) -> int`.

- [ ] **Step 1: Add vectors and failing consumers**

Include empty input, embedded zeroes, 254 nonzero bytes, malformed zero code, truncated block, `"123456789"` CRC-16 `0x29B1` and CRC-32 `0xCBF43926`.

```python
@pytest.mark.parametrize("case", load_vectors()["cobs"])
def test_cobs_vector(case):
    raw = bytes.fromhex(case["raw"])
    assert cobs_encode(raw).hex() == case["encoded"]
    assert cobs_decode(bytes.fromhex(case["encoded"])) == raw
```

- [ ] **Step 2: Run both suites and confirm missing-symbol failures**

Run `cmake --build build/native; ctest --test-dir build/native -R transport --output-on-failure` and `python -m pytest configurator/tests/test_transport_vectors.py -q`.

- [ ] **Step 3: Implement allocation-free C++ and bounded Python codecs**

C++ `bytes.hpp` defines only `ByteView {const uint8_t* data; size_t size;}` and `MutableByteView {uint8_t* data; size_t size;}`. Codec functions accept caller-owned buffers and reject insufficient output capacity. Python decoder must raise `ValueError("invalid COBS frame")` for malformed input.

- [ ] **Step 4: Run vector suites plus generator check**

Expected: identical vector results in C++ and Python; `python tools/generate_protocol.py --check` passes.

- [ ] **Step 5: Commit**

```powershell
git add tests/vectors firmware/common/protocol tests/firmware_native configurator/src/duo_input/protocol configurator/tests
git commit -m "feat: add shared transport codecs"
```

### Task 4: CDC and SPI frame codecs

**Files:**
- Create: `firmware/common/protocol/frame.hpp`
- Create: `firmware/common/protocol/frame.cpp`
- Create: `tests/firmware_native/test_frames.cpp`
- Create: `configurator/src/duo_input/protocol/frame.py`
- Create: `configurator/tests/test_frames.py`
- Create: `tests/vectors/frame_vectors.json`

**Interfaces:**
- Produces C++: `DecodeResult decode_cdc_frame(...)`, `DecodeResult decode_spi_frame(...)`, `bool encode_*`.
- Produces Python: immutable `CdcFrame`, `SpiFrame`, `encode_cdc_frame`, `decode_cdc_frame`, `encode_spi_frame`, `decode_spi_frame`.

- [ ] **Step 1: Write negative-first frame tests**

```python
def test_cdc_rejects_crc_damage(valid_cdc_bytes):
    damaged = valid_cdc_bytes[:-2] + bytes([valid_cdc_bytes[-2] ^ 1]) + valid_cdc_bytes[-1:]
    with pytest.raises(FrameError, match="CRC"):
        decode_cdc_frame(damaged)

def test_spi_is_exactly_64_bytes():
    assert len(encode_spi_frame(SpiFrame(type=SpiMessageType.HEARTBEAT, sequence=7, payload=b""))) == 64
```

- [ ] **Step 2: Verify failures for missing codecs**

Run Python and native frame tests; expected missing imports/symbols.

- [ ] **Step 3: Implement strict codecs**

Define explicit little-endian packing. Reject wrong magic, major, flags, declared length, CRC, nonzero SPI padding and CDC payload >1024. Preserve unknown minor only when required capability bits are supported.

- [ ] **Step 4: Run shared vectors and malformed corpus**

Run all native/Python tests. Add one byte truncation test for every possible CDC frame boundary and lengths 0, 1, 1024, 1025.

- [ ] **Step 5: Commit**

```powershell
git add firmware/common/protocol tests/firmware_native tests/vectors configurator/src/duo_input/protocol configurator/tests
git commit -m "feat: encode strict CDC and SPI frames"
```

### Task 5: Binary configuration compiler and C++ validator

**Files:**
- Create: `protocol/config_format.md`
- Create: `configurator/src/duo_input/domain/models.py`
- Create: `configurator/src/duo_input/domain/config_binary.py`
- Create: `configurator/tests/test_config_binary.py`
- Create: `firmware/common/config/format.hpp`
- Create: `firmware/common/config/validator.hpp`
- Create: `firmware/common/config/validator.cpp`
- Create: `tests/firmware_native/test_config_validator.cpp`
- Create: `tests/vectors/config_vectors/valid_minimal.bin`
- Create: `tests/vectors/config_vectors/valid_full.bin`

**Interfaces:**
- Produces Python: `compile_device_config(config: DeviceConfig) -> bytes`, `decode_device_config(data: bytes) -> DeviceConfig`.
- Produces C++: `ValidationResult validate_config(ByteView)`, `ConfigView` with bounded iterators.

- [ ] **Step 1: Write round-trip and limit failures**

```python
def test_minimal_config_round_trip(minimal_config):
    encoded = compile_device_config(minimal_config)
    assert decode_device_config(encoded) == minimal_config

def test_rejects_129th_binding(profile_factory):
    cfg = profile_factory(binding_count=129)
    with pytest.raises(ConfigError, match="128"):
        compile_device_config(cfg)
```

C++ test must validate Python-produced minimal/full vectors and reject a vector with every length field independently corrupted.

- [ ] **Step 2: Confirm red state in both languages**

Run `python -m pytest configurator/tests/test_config_binary.py -q` and native `config_validator` test.

- [ ] **Step 3: Implement versioned offset-table format**

Use fixed little-endian headers plus checked offset/length tables; never store raw C structs. Encode names/text as UTF-8. Store compiled HID text sequences in binary while source Unicode remains only in JSON. Enforce all spec limits and 360 KiB total.

- [ ] **Step 4: Generate vectors and cross-validate**

Run compiler to regenerate the two committed vectors, then run all suites. Expected: `decode(encode(x)) == x`; C++ accepts both valid vectors and rejects all corruptions.

- [ ] **Step 5: Commit**

```powershell
git add protocol/config_format.md firmware/common/config tests/firmware_native tests/vectors/config_vectors configurator/src/duo_input/domain configurator/tests
git commit -m "feat: define validated binary configuration"
```

### Task 6: U1 CDC emulator and compatibility contract

**Files:**
- Create: `configurator/src/duo_input/device/transport.py`
- Create: `configurator/src/duo_input/device/emulator.py`
- Create: `configurator/tests/test_device_emulator.py`
- Create: `docs/protocol/compatibility.md`

**Interfaces:**
- Produces: `AbstractByteTransport.open/close/write`, `U1Emulator.feed(bytes) -> bytes`, stateful handlers for HELLO, status, read/write transaction, capture and stop.

- [ ] **Step 1: Write transaction tests**

```python
def test_power_loss_before_commit_keeps_old_config(emulator, config_a, config_b):
    emulator.install_active(config_a)
    begin_and_send_all_chunks(emulator, config_b)
    emulator.simulate_power_cycle()
    assert emulator.active_hash == hashlib.sha256(config_a).digest()

def test_major_mismatch_blocks_write(emulator):
    reply = emulator.exchange(hello_frame(major=99))
    assert reply.error == ErrorCode.INCOMPATIBLE_MAJOR
```

- [ ] **Step 2: Verify failures**

Run `python -m pytest configurator/tests/test_device_emulator.py -q`.

- [ ] **Step 3: Implement deterministic emulator**

Use in-memory slots A/B, generation counters, SHA-256, strict sequence matching and injectable timeout/CRC/disconnect faults. No Qt dependency is allowed in the emulator.

- [ ] **Step 4: Run full foundation suite**

```powershell
python tools/generate_protocol.py --check
cmake --build build/native
ctest --test-dir build/native --output-on-failure
python -m pytest configurator/tests -q
git diff --check
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add configurator/src/duo_input/device configurator/tests/test_device_emulator.py docs/protocol/compatibility.md
git commit -m "test: add deterministic U1 protocol emulator"
```

### Task 7: Parser fuzz harnesses

**Files:**
- Create: `tests/fuzz/CMakeLists.txt`
- Create: `tests/fuzz/fuzz_cdc_frame.cpp`
- Create: `tests/fuzz/fuzz_spi_frame.cpp`
- Create: `tests/fuzz/fuzz_config.cpp`
- Create: `tests/fuzz/corpus/`
- Create: `docs/testing/fuzzing.md`

**Interfaces:**
- Produces libFuzzer entry points `LLVMFuzzerTestOneInput` for CDC, SPI and binary config parsers.

- [ ] **Step 1: Add one crashing seed test around an unchecked length mutation**

Seed each corpus with its valid minimal vector plus variants whose declared length is `0`, maximum, maximum+1 and `0xFFFF`. Before parser hardening is complete, run under AddressSanitizer and record any failure as the red state.

- [ ] **Step 2: Build fuzz targets with Clang sanitizers**

```powershell
cmake -S . -B build/fuzz -DDUO_FUZZ_TESTS=ON -DCMAKE_CXX_COMPILER=clang++
cmake --build build/fuzz --parallel
```

Expected: three fuzz executables build; any sanitizer crash is a failing test.

- [ ] **Step 3: Enforce total/non-throwing parser behavior**

Every entry point calls only the public parser with `ByteView{data,size}` and asserts: accepted results expose views fully inside input; rejected results perform no callback/HID action. Fix parser bounds at the parser, never in the fuzz harness.

- [ ] **Step 4: Run bounded fuzz campaign**

```powershell
build/fuzz/fuzz_cdc_frame.exe -max_total_time=60 tests/fuzz/corpus/cdc
build/fuzz/fuzz_spi_frame.exe -max_total_time=60 tests/fuzz/corpus/spi
build/fuzz/fuzz_config.exe -max_total_time=120 tests/fuzz/corpus/config
```

Expected: no crash, timeout, OOM or sanitizer report. Commit any coverage-increasing corpus files under 4 KiB.

- [ ] **Step 5: Commit**

```powershell
git add tests/fuzz docs/testing/fuzzing.md firmware/common
git commit -m "test: fuzz protocol and configuration parsers"
```

## Plan Completion Gate

- `python tools/generate_protocol.py --check` passes.
- Native and Python suites consume the same committed vectors.
- No manually duplicated protocol IDs exist outside generated files/tests.
- Emulator demonstrates transactional config recovery and version rejection.
- CDC, SPI and binary config fuzz targets complete bounded sanitizer runs without findings.
- Only after this gate begin the firmware and configurator plans.
