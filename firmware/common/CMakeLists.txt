add_library(duo_common STATIC
    config/validator.cpp
    crypto/sha256.cpp
    hid/state_manager.cpp
    link/spi_protocol.cpp
    protocol/cobs.cpp
    protocol/crc.cpp
    protocol/frame.cpp
    storage/ab_store.cpp
)

target_include_directories(duo_common PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
