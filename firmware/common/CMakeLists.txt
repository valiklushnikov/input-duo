add_library(duo_common STATIC
    protocol/cobs.cpp
    protocol/crc.cpp
    protocol/frame.cpp
)

target_include_directories(duo_common PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
