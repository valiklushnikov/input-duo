cmake_minimum_required(VERSION 3.21)

option(DUO_NATIVE_TESTS "Build native firmware tests" OFF)
option(DUO_FUZZ_TESTS "Build Clang libFuzzer parser targets" OFF)

if(DUO_NATIVE_TESTS AND DUO_FUZZ_TESTS)
    message(FATAL_ERROR
        "DUO_NATIVE_TESTS and DUO_FUZZ_TESTS cannot be enabled together; use separate build "
        "directories for native corpus smoke and Clang libFuzzer campaigns.")
endif()

project(duo_input LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_subdirectory(firmware/common)

if(DUO_NATIVE_TESTS OR DUO_FUZZ_TESTS)
    include(CTest)
endif()

if(DUO_NATIVE_TESTS)
    add_subdirectory(tests/firmware_native)
endif()

if(DUO_NATIVE_TESTS OR DUO_FUZZ_TESTS)
    add_subdirectory(tests/fuzz)
endif()
