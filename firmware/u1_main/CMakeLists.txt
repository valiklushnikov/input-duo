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
    buttons.cpp
    ch375_probe.cpp
    ch375/pio_transport.cpp
    ch375/device.cpp
    ch375/enumerator.cpp
    ch375/descriptor_setup.cpp
    ch375/hid_parser.cpp
    diagnostics_service.cpp
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
    hardware_pio
    hardware_spi
    hardware_flash
    hardware_sync
    hardware_watchdog
)

# No stdio on either interface. USB is owned by TinyUSB, and a stray printf on
# UART is a pin this board does not have to spare.
pico_generate_pio_header(duo_u1_main ${CMAKE_CURRENT_LIST_DIR}/ch375/ch375_serial.pio)

pico_enable_stdio_usb(duo_u1_main 0)
pico_enable_stdio_uart(duo_u1_main 0)

# The generated-input pattern is opt-in and off by default. A device that can
# type on its own must not ship because someone forgot a flag.
option(DUO_TEST_PATTERN "Generate synthetic input on Core 1" OFF)
# Bring-up only: replaces the diagnostics reply with raw link bytes, which the
# configurator cannot parse. Never enabled in a release build.
option(DUO_SPI_DEBUG "Report raw SPI link bytes over CDC" OFF)
# Bring-up only: bit-bangs the CH375 serial lines before anything else claims
# them and reports what answered, so a soldering mistake can be found before a
# driver exists to blame. Never enabled in a release build.
option(DUO_CH375_PROBE "Probe both CH375s over CDC at start-up" OFF)
target_compile_definitions(duo_u1_main PRIVATE
    DUO_TEST_PATTERN=$<BOOL:${DUO_TEST_PATTERN}>
    DUO_SPI_DEBUG=$<BOOL:${DUO_SPI_DEBUG}>
    DUO_CH375_PROBE=$<BOOL:${DUO_CH375_PROBE}>
)

pico_add_extra_outputs(duo_u1_main)
