# U1: the board the operator's keyboard, mouse and configurator all talk to.
#
# Sources split three ways: common to both backends, CH375-only, and PIO
# USB-only. DUO_INPUT_BACKEND selects exactly one of the latter two - never
# both - so the CH375 image never links a byte of TinyUSB host code and the
# PIO USB image never links a byte of CH375 code. tests/build/
# test_pio_usb_firmware_contract.py checks that split against the linked
# ELF, not just against this file.
set(_duo_u1_sources
    main.cpp
    usb_descriptors.cpp
    usb_service.cpp
    spi_master.cpp
    spi_master_poll.cpp
    output_runtime.cpp
    config_service.cpp
    config_profiles.cpp
    core1_runtime.cpp
    core_bridge.cpp
    input/pipeline.cpp
    input/keyboard_normalizer.cpp
    input/mouse_normalizer.cpp
    mapping/capture.cpp
    mapping/engine.cpp
    mapping/routes.cpp
    macros/scheduler.cpp
    pico_flash.cpp
    buttons.cpp
    input/hid/report_descriptor.cpp
    diagnostics_service.cpp
)

if(DUO_INPUT_BACKEND STREQUAL "CH375")
    list(APPEND _duo_u1_sources
        input/ch375_source_adapter.cpp
        ch375_probe.cpp
        ch375/pio_transport.cpp
        ch375/device.cpp
        ch375/enumerator.cpp
        ch375/descriptor_setup.cpp
        ch375/hid_parser.cpp
    )
else()
    list(APPEND _duo_u1_sources
        pio_usb/backend.cpp
        pio_usb/tinyusb_host_callbacks.cpp
    )
endif()

add_executable(duo_u1_main ${_duo_u1_sources})

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

if(DUO_INPUT_BACKEND STREQUAL "CH375")
    # No stdio on either interface. USB is owned by TinyUSB, and a stray
    # printf on UART is a pin this board does not have to spare.
    pico_generate_pio_header(duo_u1_main ${CMAKE_CURRENT_LIST_DIR}/ch375/ch375_serial.pio)
else()
    # TinyUSB's own host stack, plus Pico-PIO-USB as RHPort 1's host
    # controller driver. Both targets come from the verified .deps/ clones
    # cmake/pio_usb_toolchain_lock.cmake pinned - tinyusb_pico_pio_usb is
    # created by TinyUSB's own hw/bsp/rp2040/family.cmake once
    # PICO_PIO_USB_PATH is set, which that lock file already does.
    target_link_libraries(duo_u1_main PRIVATE
        tinyusb_host
        tinyusb_pico_pio_usb
        hardware_clocks
    )
endif()

pico_enable_stdio_usb(duo_u1_main 0)
pico_enable_stdio_uart(duo_u1_main 0)

# Bring-up only: replaces the diagnostics reply with raw link bytes, which the
# configurator cannot parse. Never enabled in a release build.
option(DUO_SPI_DEBUG "Report raw SPI link bytes over CDC" OFF)
# Bring-up only: bit-bangs the CH375 serial lines before anything else claims
# them and reports what answered, so a soldering mistake can be found before a
# driver exists to blame. Never enabled in a release build. The PIO USB
# backend's sources never reference it - main.cpp's probe code is compiled
# only under DUO_INPUT_BACKEND_CH375 - so asking for it there is refused
# rather than silently having no effect.
option(DUO_CH375_PROBE "Probe both CH375s over CDC at start-up" OFF)
if(NOT DUO_INPUT_BACKEND STREQUAL "CH375" AND DUO_CH375_PROBE)
    message(FATAL_ERROR
        "DUO_CH375_PROBE probes hardware the PIO USB backend does not build "
        "against; configure with -DDUO_INPUT_BACKEND=CH375 to use it.")
endif()
target_compile_definitions(duo_u1_main PRIVATE
    DUO_SPI_DEBUG=$<BOOL:${DUO_SPI_DEBUG}>
    # Safe to pass straight through: the FATAL_ERROR above already refuses a
    # configure where this would be ON for anything but the CH375 backend.
    DUO_CH375_PROBE=$<BOOL:${DUO_CH375_PROBE}>
)

pico_add_extra_outputs(duo_u1_main)
