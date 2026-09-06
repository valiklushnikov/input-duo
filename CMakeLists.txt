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

    # Which physical USB host path U1 uses. CH375 is the currently shipping
    # implementation; PIO_USB is the native Pico-PIO-USB/TinyUSB host under
    # migration, while PIO_USB_REFERENCE is the frozen upstream golden target
    # used to rebuild that integration one measured layer at a time.
    # An unrecognised value fails configuration outright: silently building
    # one backend when a different one was asked for is worse than refusing
    # to build at all.
    set(DUO_INPUT_BACKEND "CH375" CACHE STRING "U1 USB host backend")
    set(_duo_input_backends CH375 PIO_USB PIO_USB_REFERENCE)
    set_property(CACHE DUO_INPUT_BACKEND PROPERTY STRINGS ${_duo_input_backends})
    if(NOT DUO_INPUT_BACKEND IN_LIST _duo_input_backends)
        message(FATAL_ERROR "Unknown DUO_INPUT_BACKEND '${DUO_INPUT_BACKEND}'")
    endif()

    if(NOT DUO_INPUT_BACKEND STREQUAL "CH375")
        # Locates and verifies its own Pico SDK / TinyUSB / Pico-PIO-USB
        # clones under .deps/, independently of PICO_SDK_PATH. The CH375
        # branch below is untouched: it keeps resolving PICO_SDK_PATH from
        # the environment exactly as it always has.
        include(cmake/pio_usb_toolchain_lock.cmake)
    endif()

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
    if(DUO_INPUT_BACKEND STREQUAL "CH375")
        add_compile_definitions(DUO_INPUT_BACKEND_CH375=1)
    elseif(DUO_INPUT_BACKEND STREQUAL "PIO_USB")
        add_compile_definitions(DUO_INPUT_BACKEND_PIO_USB=1)
    else()
        add_compile_definitions(DUO_INPUT_BACKEND_PIO_USB_REFERENCE=1)
    endif()
    pico_sdk_init()
endif()

add_subdirectory(firmware/common)

if(DUO_PICO_FIRMWARE)
    # The PIO USB backend's own C++ sources (firmware/u1_main/pio_usb/...)
    # arrive in a later task of this migration. Until then, a PIO_USB
    # configure stops here, deliberately, once the toolchain above has
    # already been located and its revisions verified - so a broken
    # toolchain and "not implemented yet" produce two different, legible
    # failures instead of one confusing one.
    if(DUO_INPUT_BACKEND STREQUAL "PIO_USB" AND
       NOT EXISTS "${CMAKE_SOURCE_DIR}/firmware/u1_main/pio_usb/backend.cpp")
        message(FATAL_ERROR
            "DUO_INPUT_BACKEND=PIO_USB: the toolchain is verified, but "
            "firmware/u1_main/pio_usb/backend.cpp does not exist yet. It "
            "arrives in a later task of the PIO USB migration "
            "(docs/superpowers/plans/2026-09-03-pio-usb-hub-v1-implementation.md); "
            "this is the expected stopping point until then.")
    endif()
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
