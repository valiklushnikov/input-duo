// TinyUSB's own HID host callbacks, invoked from inside tuh_task() while
// PioUsbBackend::task() runs it on Core 1.
//
// All three callbacks are explicit, empty compile-only stubs. In particular,
// neither mount nor report-received arms interrupt-IN traffic. Task 6 owns
// the first arm, every re-arm, and the registry needed to bound that work.
// report-received must remain non-weak because TinyUSB links against it.

#include <cstdint>

extern "C" {

void tuh_hid_mount_cb(std::uint8_t dev_addr, std::uint8_t instance,
                      std::uint8_t const* report_desc, std::uint16_t desc_len) {
    (void)dev_addr;
    (void)instance;
    (void)report_desc;
    (void)desc_len;
}

void tuh_hid_umount_cb(std::uint8_t dev_addr, std::uint8_t instance) {
    (void)dev_addr;
    (void)instance;
}

void tuh_hid_report_received_cb(std::uint8_t dev_addr, std::uint8_t instance,
                                std::uint8_t const* report, std::uint16_t len) {
    (void)dev_addr;
    (void)instance;
    (void)report;
    (void)len;
}

}  // extern "C"
