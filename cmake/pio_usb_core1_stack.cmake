# Core 1 descriptor discovery runs underneath the USB SOF IRQ. Check the
# actual SDK stack allocation at link time, including when a board overrides
# PICO_CORE1_STACK_SIZE. See docs/testing/pio-usb-core1-stack-budget.md.
function(duo_configure_pio_usb_core1_stack target)
    target_compile_definitions(${target} PRIVATE PICO_CORE1_STACK_SIZE=4096)
    # Preserve per-function evidence alongside the ELF for stack-budget audits.
    target_compile_options(${target} PRIVATE -fstack-usage)
    set(guard "${CMAKE_SOURCE_DIR}/cmake/pio_usb_core1_stack_guard.ld")
    target_link_options(${target} PRIVATE "LINKER:-T,${guard}")
    set_property(TARGET ${target} APPEND PROPERTY LINK_DEPENDS "${guard}")
endfunction()
