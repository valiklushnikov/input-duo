cmake_minimum_required(VERSION 3.21)

project(duo_input LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(DUO_NATIVE_TESTS "Build native firmware tests" OFF)

add_subdirectory(firmware/common)

if(DUO_NATIVE_TESTS)
    include(CTest)
    add_subdirectory(tests/firmware_native)
endif()
