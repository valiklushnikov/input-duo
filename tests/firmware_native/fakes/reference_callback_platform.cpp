// Native substitutes for external CDC and controller trace hardware. The HID
// callbacks, queue and adapter remain production implementations in this test.
#include <cstdint>
#include "callback_queue.hpp"
#include "tusb.h"

extern "C" bool reference_descriptor_unmounted(std::uint8_t address,
                                                std::uint8_t instance,
                                                std::uint32_t now) {
    return reference_capture(reference_make_unmount(address, instance, now));
}
extern "C" bool pio_usb_host_ctrl_trace_take(
    std::uint32_t*, std::uint8_t*, std::uint8_t*, std::uint8_t*, std::uint8_t*,
    std::uint16_t*, std::uint16_t*, std::uint16_t*, std::uint16_t*,
    std::uint8_t*, std::uint8_t*) { return false; }
extern "C" std::uint32_t pio_usb_host_ctrl_trace_lost() { return 0; }
extern "C" std::uint32_t tud_cdc_write_available() { return 0; }
extern "C" std::uint32_t tud_cdc_write(const void*, std::uint32_t) { return 0; }
extern "C" std::uint32_t tud_cdc_write_flush() { return 0; }
