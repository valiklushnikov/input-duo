add_executable(duo_firmware_native_test
    test_main.cpp
    test_smoke.cpp
)

target_link_libraries(duo_firmware_native_test PRIVATE duo_common)

if(MSVC)
    target_compile_options(duo_firmware_native_test PRIVATE /EHsc)
endif()

add_test(NAME firmware_native COMMAND duo_firmware_native_test)
