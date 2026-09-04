// TinyUSB's own HID host callbacks, invoked from inside tuh_task() while
// PioUsbBackend::task() runs it on Core 1.
//
// Each callback performs only bounded metadata lookup/copy and queueing. The
// ordinary Core 1 pass processes those records and owns every receive arm;
// no parser, normalizer, pipeline or route is called from this file.

#include <cstdint>

#include "pio_usb/device_registry.hpp"

extern "C" bool tuh_vid_pid_get(std::uint8_t dev_addr, std::uint16_t* vendor_id,
                                std::uint16_t* product_id);
extern "C" std::uint8_t tuh_hid_interface_protocol(std::uint8_t dev_addr,
                                                    std::uint8_t instance);

namespace duo_input::u1::pio_usb {
namespace {
DeviceRegistry* callback_registry = nullptr;
}

void set_callback_registry(DeviceRegistry* registry) noexcept {
    callback_registry = registry;
}
}  // namespace duo_input::u1::pio_usb

extern "C" {

void tuh_mount_cb(std::uint8_t dev_addr) {
    auto* registry = duo_input::u1::pio_usb::callback_registry;
    if (registry == nullptr) {
        return;
    }
    std::uint16_t vendor_id = 0;
    std::uint16_t product_id = 0;
    (void)tuh_vid_pid_get(dev_addr, &vendor_id, &product_id);
    registry->capture_device_mount(dev_addr, vendor_id, product_id);
}

void tuh_umount_cb(std::uint8_t dev_addr) {
    auto* registry = duo_input::u1::pio_usb::callback_registry;
    if (registry != nullptr) {
        registry->capture_unmount(dev_addr);
    }
}

void tuh_hid_mount_cb(std::uint8_t dev_addr, std::uint8_t instance,
                      std::uint8_t const* report_desc, std::uint16_t desc_len) {
    auto* registry = duo_input::u1::pio_usb::callback_registry;
    if (registry == nullptr) {
        return;
    }
    std::uint16_t vendor_id = 0;
    std::uint16_t product_id = 0;
    (void)tuh_vid_pid_get(dev_addr, &vendor_id, &product_id);
    registry->capture_hid_mount(dev_addr, instance, vendor_id, product_id,
                                tuh_hid_interface_protocol(dev_addr, instance),
                                report_desc, desc_len);
}

void tuh_hid_umount_cb(std::uint8_t dev_addr, std::uint8_t instance) {
    (void)instance;
    auto* registry = duo_input::u1::pio_usb::callback_registry;
    if (registry != nullptr) {
        registry->capture_unmount(dev_addr);
    }
}

void tuh_hid_report_received_cb(std::uint8_t dev_addr, std::uint8_t instance,
                                std::uint8_t const* report, std::uint16_t len) {
    auto* registry = duo_input::u1::pio_usb::callback_registry;
    if (registry != nullptr) {
        registry->capture_report(dev_addr, instance, report, len);
    }
}

}  // extern "C"
