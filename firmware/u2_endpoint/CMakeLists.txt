# U2: the second computer's endpoint. Its only CDC traffic is the address
# exchange; everything it emits as HID arrives over SPI from U1.
add_executable(duo_u2_endpoint
    main.cpp
    usb_descriptors.cpp
    usb_service.cpp
    spi_slave.cpp
    frame_resync.cpp
    link_drop_log.cpp
    link_watchdog.cpp
    address_service.cpp
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
    hardware_dma
    hardware_watchdog
    pico_bootrom
)

pico_enable_stdio_usb(duo_u2_endpoint 0)
pico_enable_stdio_uart(duo_u2_endpoint 0)

# Bring-up only; see firmware/u2_endpoint/spi_slave.cpp.
option(DUO_SPI_DEBUG "Send a counting pattern instead of frames" OFF)
option(DUO_WIRE_WALK "Mirror the parity of the incoming lines, nothing else" OFF)
target_compile_definitions(duo_u2_endpoint PRIVATE
    DUO_SPI_DEBUG=$<BOOL:${DUO_SPI_DEBUG}>
    DUO_WIRE_WALK=$<BOOL:${DUO_WIRE_WALK}>
)

pico_add_extra_outputs(duo_u2_endpoint)
