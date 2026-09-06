# U1 reference target.
#
# main.cpp keeps the upstream host_hid_to_device_cdc lifecycle unchanged -
# clock, settle, launch Core 1, tuh_init there, device stack on Core 0 - while
# the callbacks live in host_callbacks.cpp and hand bounded records to
# callback_queue.cpp instead of formatting CDC inside a USB host callback.
# source_adapter.cpp decides what those records mean at the neutral boundary.
#
# Everything below that line is the production input path, linked rather than
# reimplemented: the same pipeline, normalizers, mapping, macros, runtime and
# output the CH375 and PIO USB builds use, so a report that means one thing
# there cannot mean another here.
#
# Deliberately NOT linked at this slice: SPI and the core bridge, the
# configuration service, flash and the A/B store, the watchdog, and the current
# pio_usb/backend.cpp. Task 4 admits routing, Task 5 configuration. Adding them
# early is exactly what this rebuild exists to avoid.
#
# See docs/superpowers/plans/2026-09-06-pio-usb-reference-first-rebuild.md.
if(NOT TARGET pico_pio_usb)
    add_subdirectory(
        "${PICO_PIO_USB_PATH}"
        "${CMAKE_CURRENT_BINARY_DIR}/pico_pio_usb"
    )
endif()

set(_duo_u1_main_dir "${CMAKE_CURRENT_LIST_DIR}/../u1_main")

add_executable(duo_u1_reference)

target_sources(duo_u1_reference PRIVATE
    main.cpp
    callback_queue.cpp
    host_callbacks.cpp
    source_adapter.cpp

    # The device this firmware presents to PC1, and the input path behind it.
    ${_duo_u1_main_dir}/usb_descriptors.cpp
    ${_duo_u1_main_dir}/usb_service.cpp
    ${_duo_u1_main_dir}/output_runtime.cpp
    ${_duo_u1_main_dir}/config_profiles.cpp
    ${_duo_u1_main_dir}/core1_runtime.cpp
    ${_duo_u1_main_dir}/input/pipeline.cpp
    ${_duo_u1_main_dir}/input/keyboard_normalizer.cpp
    ${_duo_u1_main_dir}/input/mouse_normalizer.cpp
    ${_duo_u1_main_dir}/input/hid/report_descriptor.cpp
    ${_duo_u1_main_dir}/mapping/capture.cpp
    ${_duo_u1_main_dir}/mapping/engine.cpp
    ${_duo_u1_main_dir}/mapping/routes.cpp
    ${_duo_u1_main_dir}/macros/scheduler.cpp
    ${_duo_u1_main_dir}/pio_usb/hid_setup.cpp

    ${PICO_TINYUSB_PATH}/src/portable/raspberrypi/pio_usb/dcd_pio_usb.c
    ${PICO_TINYUSB_PATH}/src/portable/raspberrypi/pio_usb/hcd_pio_usb.c
)

target_link_options(duo_u1_reference PRIVATE -Xlinker --print-memory-usage)
target_compile_options(duo_u1_reference PRIVATE -Wall -Wextra)
target_compile_definitions(duo_u1_reference PRIVATE PIO_USB_USE_TINYUSB)

# tusb_config.h lives beside these sources, so TinyUSB has to be told to look
# here for it - and here rather than in u1_main, because the host half of that
# file is what this target is built to hold still.
target_include_directories(duo_u1_reference PRIVATE
    ${CMAKE_CURRENT_LIST_DIR}
    ${_duo_u1_main_dir}
)

target_link_libraries(duo_u1_reference PRIVATE
    duo_common
    pico_stdlib
    pico_pio_usb
    pico_multicore
    pico_unique_id
    hardware_clocks
    tinyusb_device
    tinyusb_host
)

# USB belongs to TinyUSB, and a stray printf on UART is a pin this board does
# not have to spare.
pico_enable_stdio_usb(duo_u1_reference 0)
pico_enable_stdio_uart(duo_u1_reference 0)

pico_add_extra_outputs(duo_u1_reference)
