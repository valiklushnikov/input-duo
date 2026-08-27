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

add_executable(duo_hid_state_manager_test
    test_main.cpp
    test_hid_state_manager.cpp
)

target_link_libraries(duo_hid_state_manager_test PRIVATE duo_common)

if(MSVC)
    target_compile_options(duo_hid_state_manager_test PRIVATE /EHsc)
endif()

add_test(NAME hid_state_manager COMMAND duo_hid_state_manager_test)

add_executable(duo_link_watchdog_test
    test_main.cpp
    test_link_watchdog.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u2_endpoint/link_watchdog.cpp
)

target_link_libraries(duo_link_watchdog_test PRIVATE duo_common)
target_include_directories(duo_link_watchdog_test PRIVATE
    ${CMAKE_SOURCE_DIR}/firmware/u2_endpoint
)

if(MSVC)
    target_compile_options(duo_link_watchdog_test PRIVATE /EHsc)
endif()

add_test(NAME link_watchdog COMMAND duo_link_watchdog_test)

add_executable(duo_frame_resync_test
    test_main.cpp
    test_frame_resync.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u2_endpoint/frame_resync.cpp
)

target_link_libraries(duo_frame_resync_test PRIVATE duo_common)
target_include_directories(duo_frame_resync_test PRIVATE
    ${CMAKE_SOURCE_DIR}/firmware/u2_endpoint
)

if(MSVC)
    target_compile_options(duo_frame_resync_test PRIVATE /EHsc)
endif()

add_test(NAME frame_resync COMMAND duo_frame_resync_test)

add_executable(duo_link_drop_log_test
    test_main.cpp
    test_link_drop_log.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u2_endpoint/link_drop_log.cpp
)

target_link_libraries(duo_link_drop_log_test PRIVATE duo_common)
target_include_directories(duo_link_drop_log_test PRIVATE
    ${CMAKE_SOURCE_DIR}/firmware/u2_endpoint
)

if(MSVC)
    target_compile_options(duo_link_drop_log_test PRIVATE /EHsc)
endif()

add_test(NAME link_drop_log COMMAND duo_link_drop_log_test)

add_executable(duo_output_runtime_test
    test_main.cpp
    test_output_runtime.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u1_main/output_runtime.cpp
)

target_link_libraries(duo_output_runtime_test PRIVATE duo_common)
target_include_directories(duo_output_runtime_test PRIVATE
    ${CMAKE_SOURCE_DIR}/firmware/u1_main
)

if(MSVC)
    target_compile_options(duo_output_runtime_test PRIVATE /EHsc)
endif()

add_test(NAME output_runtime COMMAND duo_output_runtime_test)

add_executable(duo_sha256_test
    test_main.cpp
    test_sha256.cpp
)

target_link_libraries(duo_sha256_test PRIVATE duo_common)

if(MSVC)
    target_compile_options(duo_sha256_test PRIVATE /EHsc)
endif()

add_test(NAME sha256 COMMAND duo_sha256_test)

add_executable(duo_ab_store_test
    test_main.cpp
    test_ab_store.cpp
)

target_link_libraries(duo_ab_store_test PRIVATE duo_common)

if(MSVC)
    target_compile_options(duo_ab_store_test PRIVATE /EHsc)
endif()

add_test(NAME ab_store COMMAND duo_ab_store_test)

add_executable(duo_config_service_test
    test_main.cpp
    test_config_service.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u1_main/config_service.cpp
)

target_link_libraries(duo_config_service_test PRIVATE duo_common)
target_include_directories(duo_config_service_test PRIVATE
    ${CMAKE_SOURCE_DIR}/firmware/u1_main
)

if(MSVC)
    target_compile_options(duo_config_service_test PRIVATE /EHsc)
endif()

add_test(NAME config_service COMMAND duo_config_service_test)

add_executable(duo_buttons_test
    test_main.cpp
    test_buttons.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u1_main/buttons.cpp
)

target_link_libraries(duo_buttons_test PRIVATE duo_common)
target_include_directories(duo_buttons_test PRIVATE
    ${CMAKE_SOURCE_DIR}/firmware/u1_main
)

if(MSVC)
    target_compile_options(duo_buttons_test PRIVATE /EHsc)
endif()

add_test(NAME buttons COMMAND duo_buttons_test)
