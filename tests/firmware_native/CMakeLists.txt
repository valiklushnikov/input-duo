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

add_executable(duo_ch375_transport_test
    test_main.cpp
    test_ch375_transport.cpp
    fakes/scripted_ch375.cpp
)

target_link_libraries(duo_ch375_transport_test PRIVATE duo_common)
target_include_directories(duo_ch375_transport_test PRIVATE
    ${CMAKE_SOURCE_DIR}/firmware/u1_main
    ${CMAKE_CURRENT_SOURCE_DIR}
)

if(MSVC)
    target_compile_options(duo_ch375_transport_test PRIVATE /EHsc)
endif()

add_test(NAME ch375_transport COMMAND duo_ch375_transport_test)

add_executable(duo_ch375_device_test
    test_main.cpp
    test_ch375_device.cpp
    fakes/scripted_ch375.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u1_main/ch375/device.cpp
)

target_link_libraries(duo_ch375_device_test PRIVATE duo_common)
target_include_directories(duo_ch375_device_test PRIVATE
    ${CMAKE_SOURCE_DIR}/firmware/u1_main
    ${CMAKE_CURRENT_SOURCE_DIR}
)

if(MSVC)
    target_compile_options(duo_ch375_device_test PRIVATE /EHsc)
endif()

add_test(NAME ch375_device COMMAND duo_ch375_device_test)

add_executable(duo_ch375_baud_scan_test
    test_main.cpp
    test_ch375_baud_scan.cpp
)

target_link_libraries(duo_ch375_baud_scan_test PRIVATE duo_common)

if(MSVC)
    target_compile_options(duo_ch375_baud_scan_test PRIVATE /EHsc)
endif()

add_test(NAME ch375_baud_scan COMMAND duo_ch375_baud_scan_test)

add_executable(duo_ch375_enumerator_test
    test_main.cpp
    test_ch375_enumerator.cpp
    fakes/scripted_ch375.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u1_main/ch375/device.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u1_main/ch375/enumerator.cpp
)

target_link_libraries(duo_ch375_enumerator_test PRIVATE duo_common)
target_include_directories(duo_ch375_enumerator_test PRIVATE
    ${CMAKE_SOURCE_DIR}/firmware/u1_main
    ${CMAKE_CURRENT_SOURCE_DIR}
)

if(MSVC)
    target_compile_options(duo_ch375_enumerator_test PRIVATE /EHsc)
endif()

add_test(NAME ch375_enumerator COMMAND duo_ch375_enumerator_test)

add_executable(duo_hid_parser_test
    test_main.cpp
    test_hid_parser.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u1_main/ch375/hid_parser.cpp
)

target_link_libraries(duo_hid_parser_test PRIVATE duo_common)
target_include_directories(duo_hid_parser_test PRIVATE
    ${CMAKE_SOURCE_DIR}/firmware/u1_main
    ${CMAKE_CURRENT_SOURCE_DIR}
)
target_compile_definitions(duo_hid_parser_test PRIVATE
    DUO_HID_DESCRIPTORS_PATH="${CMAKE_SOURCE_DIR}/tests/vectors/hid_descriptors"
)

if(MSVC)
    target_compile_options(duo_hid_parser_test PRIVATE /EHsc)
endif()

add_test(NAME hid_parser COMMAND duo_hid_parser_test)

add_executable(duo_ch375_descriptor_setup_test
    test_main.cpp
    test_ch375_descriptor_setup.cpp
    fakes/scripted_ch375.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u1_main/ch375/device.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u1_main/ch375/enumerator.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u1_main/ch375/descriptor_setup.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u1_main/ch375/hid_parser.cpp
)

target_link_libraries(duo_ch375_descriptor_setup_test PRIVATE duo_common)
target_include_directories(duo_ch375_descriptor_setup_test PRIVATE
    ${CMAKE_SOURCE_DIR}/firmware/u1_main
    ${CMAKE_CURRENT_SOURCE_DIR}
)

if(MSVC)
    target_compile_options(duo_ch375_descriptor_setup_test PRIVATE /EHsc)
endif()

add_test(NAME ch375_descriptor_setup COMMAND duo_ch375_descriptor_setup_test)

add_executable(duo_normalizers_test
    test_main.cpp
    test_normalizers.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u1_main/input/keyboard_normalizer.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u1_main/input/mouse_normalizer.cpp
)

target_link_libraries(duo_normalizers_test PRIVATE duo_common)
target_include_directories(duo_normalizers_test PRIVATE
    ${CMAKE_SOURCE_DIR}/firmware/u1_main
    ${CMAKE_CURRENT_SOURCE_DIR}
)

if(MSVC)
    target_compile_options(duo_normalizers_test PRIVATE /EHsc)
endif()

add_test(NAME normalizers COMMAND duo_normalizers_test)

add_executable(duo_binding_engine_test
    test_main.cpp
    test_binding_engine.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u1_main/mapping/engine.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u1_main/mapping/routes.cpp
)

target_link_libraries(duo_binding_engine_test PRIVATE duo_common)
target_include_directories(duo_binding_engine_test PRIVATE
    ${CMAKE_SOURCE_DIR}/firmware/u1_main
    ${CMAKE_CURRENT_SOURCE_DIR}
)

if(MSVC)
    target_compile_options(duo_binding_engine_test PRIVATE /EHsc)
endif()

add_test(NAME binding_engine COMMAND duo_binding_engine_test)

add_executable(duo_macro_scheduler_test
    test_main.cpp
    test_macro_scheduler.cpp
    ${CMAKE_SOURCE_DIR}/firmware/u1_main/macros/scheduler.cpp
)

target_link_libraries(duo_macro_scheduler_test PRIVATE duo_common)
target_include_directories(duo_macro_scheduler_test PRIVATE
    ${CMAKE_SOURCE_DIR}/firmware/u1_main
    ${CMAKE_CURRENT_SOURCE_DIR}
)

if(MSVC)
    target_compile_options(duo_macro_scheduler_test PRIVATE /EHsc)
endif()

add_test(NAME macro_scheduler COMMAND duo_macro_scheduler_test)
