# U2: the second computer's endpoint. It has no CDC and no configuration of
# its own; everything it emits arrives over SPI from U1.
add_executable(duo_u2_endpoint
    main.cpp
    usb_descriptors.cpp
    usb_service.cpp
)

# tusb_config.h lives beside the sources, so TinyUSB has to be told to look
# here for it.
target_include_directories(duo_u2_endpoint PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

target_link_libraries(duo_u2_endpoint PRIVATE
    duo_common
    pico_stdlib
    tinyusb_device
    pico_unique_id
    hardware_spi
    hardware_watchdog
)

pico_enable_stdio_usb(duo_u2_endpoint 0)
pico_enable_stdio_uart(duo_u2_endpoint 0)

pico_add_extra_outputs(duo_u2_endpoint)
