# U2: the second computer's endpoint. It has no CDC and no configuration of
# its own; everything it emits arrives over SPI from U1.
add_executable(duo_u2_endpoint main.cpp)

target_link_libraries(duo_u2_endpoint PRIVATE
    duo_common
    pico_stdlib
    pico_unique_id
    hardware_spi
    hardware_watchdog
)

pico_enable_stdio_usb(duo_u2_endpoint 0)
pico_enable_stdio_uart(duo_u2_endpoint 0)

pico_add_extra_outputs(duo_u2_endpoint)
