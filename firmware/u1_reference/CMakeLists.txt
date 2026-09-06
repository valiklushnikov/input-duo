# U1 reference target. main.c keeps the upstream host_hid_to_device_cdc
# lifecycle unchanged - clock, settle, launch Core 1, tuh_init there, tud_init
# on Core 0 - while the callbacks live in host_callbacks.cpp and hand bounded
# records to callback_queue.cpp instead of formatting CDC inside the USB host
# callback. See docs/superpowers/plans/2026-09-06-pio-usb-reference-first-rebuild.md.
if(NOT TARGET pico_pio_usb)
    add_subdirectory(
        "${PICO_PIO_USB_PATH}"
        "${CMAKE_CURRENT_BINARY_DIR}/pico_pio_usb"
    )
endif()

add_executable(duo_u1_reference)

target_sources(duo_u1_reference PRIVATE
    main.c
    callback_queue.cpp
    host_callbacks.cpp
    usb_descriptors.c
    ${PICO_TINYUSB_PATH}/src/portable/raspberrypi/pio_usb/dcd_pio_usb.c
    ${PICO_TINYUSB_PATH}/src/portable/raspberrypi/pio_usb/hcd_pio_usb.c
)

target_link_options(duo_u1_reference PRIVATE -Xlinker --print-memory-usage)
target_compile_options(duo_u1_reference PRIVATE -Wall -Wextra)
target_compile_definitions(duo_u1_reference PRIVATE PIO_USB_USE_TINYUSB)
target_include_directories(duo_u1_reference PRIVATE ${CMAKE_CURRENT_LIST_DIR})

target_link_libraries(duo_u1_reference PRIVATE
    pico_stdlib
    pico_pio_usb
    tinyusb_device
    tinyusb_host
)

pico_add_extra_outputs(duo_u1_reference)
