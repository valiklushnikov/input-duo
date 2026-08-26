# U1: the board the operator's keyboard, mouse and configurator all talk to.
add_executable(duo_u1_main
    main.cpp
    usb_descriptors.cpp
    usb_service.cpp
    spi_master.cpp
)

# tusb_config.h lives beside the sources, so TinyUSB has to be told to look
# here for it.
target_include_directories(duo_u1_main PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

target_link_libraries(duo_u1_main PRIVATE
    duo_common
    pico_stdlib
    tinyusb_device
    pico_multicore
    pico_unique_id
    hardware_spi
    hardware_flash
    hardware_sync
    hardware_watchdog
)

# No stdio on either interface. USB is owned by TinyUSB, and a stray printf on
# UART is a pin this board does not have to spare.
pico_enable_stdio_usb(duo_u1_main 0)
pico_enable_stdio_uart(duo_u1_main 0)

pico_add_extra_outputs(duo_u1_main)
