add_executable(duo_firmware_native_test
    test_main.cpp
    test_smoke.cpp
    test_transport.cpp
    test_frames.cpp
)

target_link_libraries(duo_firmware_native_test PRIVATE duo_common)
target_compile_definitions(duo_firmware_native_test PRIVATE
    DUO_TRANSPORT_VECTORS_PATH="${CMAKE_SOURCE_DIR}/tests/vectors/transport_vectors.json"
    DUO_FRAME_VECTORS_PATH="${CMAKE_SOURCE_DIR}/tests/vectors/frame_vectors.json"
)

if(MSVC)
    target_compile_options(duo_firmware_native_test PRIVATE /EHsc)
endif()

add_test(NAME firmware_native COMMAND duo_firmware_native_test)
add_test(NAME transport COMMAND duo_firmware_native_test)

add_executable(duo_config_validator_test
    test_main.cpp
    test_config_validator.cpp
)

target_link_libraries(duo_config_validator_test PRIVATE duo_common)
target_compile_definitions(duo_config_validator_test PRIVATE
    DUO_CONFIG_VECTORS_PATH="${CMAKE_SOURCE_DIR}/tests/vectors/config_vectors"
)

if(MSVC)
    target_compile_options(duo_config_validator_test PRIVATE /EHsc)
endif()

add_test(NAME config_validator COMMAND duo_config_validator_test)
