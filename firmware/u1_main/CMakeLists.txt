# U1: the board the operator's keyboard, mouse and configurator all talk to.
add_executable(duo_u1_main
    main.cpp
    usb_descriptors.cpp
    usb_service.cpp
    spi_master.cpp
    output_runtime.cpp
    test_pattern.cpp
    config_service.cpp
    pico_flash.cpp
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

# The generated-input pattern is opt-in and off by default. A device that can
# type on its own must not ship because someone forgot a flag.
option(DUO_TEST_PATTERN "Generate synthetic input on Core 1" OFF)
target_compile_definitions(duo_u1_main PRIVATE
    DUO_TEST_PATTERN=$<BOOL:${DUO_TEST_PATTERN}>
)

pico_add_extra_outputs(duo_u1_main)
