cmake_minimum_required(VERSION 3.21)

option(DUO_NATIVE_TESTS "Build native firmware tests" OFF)
option(DUO_FUZZ_TESTS "Build Clang libFuzzer parser targets" OFF)
option(DUO_PICO_FIRMWARE "Build the RP2040 U1 and U2 firmware images" OFF)

if(DUO_NATIVE_TESTS AND DUO_FUZZ_TESTS)
    message(FATAL_ERROR
        "DUO_NATIVE_TESTS and DUO_FUZZ_TESTS cannot be enabled together; use separate build "
        "directories for native corpus smoke and Clang libFuzzer campaigns.")
endif()

# The firmware images cross-compile for ARM; the tests build for the host. One
# build directory cannot do both, and a build that quietly did half of each
# would be worse than one that refuses.
if(DUO_PICO_FIRMWARE AND (DUO_NATIVE_TESTS OR DUO_FUZZ_TESTS))
    message(FATAL_ERROR
        "DUO_PICO_FIRMWARE cross-compiles for RP2040 and cannot share a build directory with "
        "the host test targets; configure them separately.")
endif()

# The SDK must be pulled in before project(), because it installs the ARM
# toolchain this project is then configured against.
if(DUO_PICO_FIRMWARE)
    # Waveshare RP2040-Zero, not a Raspberry Pi Pico. Same RP2040 and the same
    # 2 MB of flash, so the A/B layout is unaffected - but it has no plain LED
    # on GP25, only an addressable WS2812 on GP16. Building for the wrong board
    # compiles indicator code that drives a pad this board does not route
    # anywhere, which is worse than having no indicator: it looks like a signal.
    set(PICO_BOARD waveshare_rp2040_zero CACHE STRING "Target board")
    include(cmake/pico_sdk_import.cmake)
endif()

# C and ASM are not optional here: the Pico SDK, TinyUSB and the RP2040 boot
# stage are C and assembly, and CMake cannot compile them without the language
# enabled even though every file this project owns is C++.
project(duo_input LANGUAGES C CXX ASM)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(DUO_PICO_FIRMWARE)
    pico_sdk_init()
endif()

add_subdirectory(firmware/common)

if(DUO_PICO_FIRMWARE)
    add_subdirectory(firmware)
endif()

if(DUO_NATIVE_TESTS OR DUO_FUZZ_TESTS)
    include(CTest)
endif()

if(DUO_NATIVE_TESTS)
    add_subdirectory(tests/firmware_native)
endif()

if(DUO_NATIVE_TESTS OR DUO_FUZZ_TESTS)
    add_subdirectory(tests/fuzz)
endif()
