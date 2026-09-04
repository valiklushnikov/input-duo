// TinyUSB's own HID host callbacks, invoked from inside tuh_task() while
// PioUsbBackend::task() runs it on Core 1.
//
// Empty and bounded on purpose. This task brings the host stack up and lets
// it enumerate, but nothing here copies a report anywhere, parses a
// descriptor, or reaches into InputPipeline. tuh_hid_mount_cb only arms
// reception for a boot keyboard or mouse interface - the interrupt-IN
// request every TinyUSB HID host has to issue itself before any report ever
// arrives, the host-side equivalent of CH375's own poll - and
// tuh_hid_report_received_cb only re-arms it once a report lands. Both are
// single, O(1) calls into TinyUSB's own API, not routing work: the report
// they arm reception for is read into TinyUSB's own transfer buffer and
// left there, unread, the moment this callback returns. Tasks 6-8 replace
// that discard with a copy into PioUsbBackend's own bounded storage, which
// is what take_event() will then have something to drain.
//
// tuh_hid_report_received_cb is not declared weak in TinyUSB's own header
// (unlike the mount and unmount callbacks below it), so it has to be defined
// here or the HID host class fails to link at all - which is also why its
// presence in the linked ELF is part of this task's build-contract test:
// an image that boots without it is not a possible outcome, only a broken
// build.

#include <cstdint>

#include "tusb.h"

namespace {

bool is_boot_hid(std::uint8_t protocol) {
    return protocol == HID_ITF_PROTOCOL_KEYBOARD || protocol == HID_ITF_PROTOCOL_MOUSE;
}

}  // namespace

extern "C" {

void tuh_hid_mount_cb(std::uint8_t dev_addr, std::uint8_t instance,
                      std::uint8_t const* report_desc, std::uint16_t desc_len) {
    (void)report_desc;
    (void)desc_len;
    // Anything that is not a boot keyboard or mouse is not a device this
    // firmware reads - the same narrowing CH375's own enumeration already
    // does before it ever asks for a report.
    if (is_boot_hid(tuh_hid_interface_protocol(dev_addr, instance))) {
        tuh_hid_receive_report(dev_addr, instance);
    }
}

void tuh_hid_umount_cb(std::uint8_t dev_addr, std::uint8_t instance) {
    (void)dev_addr;
    (void)instance;
    // Nothing is held on this device's behalf yet - no bounded storage keyed
    // on dev_addr/instance exists until task 6 - so there is nothing to
    // release here. A later task's Detached event is what release_all()
    // downstream will actually depend on.
}

void tuh_hid_report_received_cb(std::uint8_t dev_addr, std::uint8_t instance,
                                std::uint8_t const* report, std::uint16_t len) {
    (void)report;
    (void)len;
    // Keeps the interrupt-IN reception loop alive without ever looking at
    // what arrived.
    if (is_boot_hid(tuh_hid_interface_protocol(dev_addr, instance))) {
        tuh_hid_receive_report(dev_addr, instance);
    }
}

}  // extern "C"
