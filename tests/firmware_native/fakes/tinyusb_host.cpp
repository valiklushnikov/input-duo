#include "fakes/tinyusb_host.hpp"

#include <array>

namespace {

struct Device {
    bool present = false;
    std::uint8_t address = 0;
    std::uint16_t vendor_id = 0;
    std::uint16_t product_id = 0;
};

struct Interface {
    bool present = false;
    std::uint8_t address = 0;
    std::uint8_t instance = 0;
    std::uint8_t protocol = 0;
};

struct ReceiveCall {
    std::uint8_t address = 0;
    std::uint8_t instance = 0;
};

std::array<Device, 16> devices{};
std::array<Interface, 32> interfaces{};
std::array<ReceiveCall, 64> receive_calls{};
std::size_t receive_calls_used = 0;
bool receive_result = true;
bool configure_result = true;
bool initialize_result = true;
std::size_t host_tasks = 0;
std::uint32_t clock_khz = 0;
std::uint8_t pin_dp = 0xff;

}  // namespace

namespace duo::test::tinyusb_host {

void reset() {
    devices = {};
    interfaces = {};
    receive_calls = {};
    receive_calls_used = 0;
    receive_result = true;
    configure_result = true;
    initialize_result = true;
    host_tasks = 0;
    clock_khz = 0;
    pin_dp = 0xff;
}

void add_hub(std::uint8_t dev_addr, std::uint16_t vendor_id,
             std::uint16_t product_id) {
    // TinyUSB owns the hub entry internally and intentionally suppresses
    // the application tuh_mount_cb for it. The fake retains its address for
    // metadata parity but callers must not dispatch an application callback.
    add_device(dev_addr, vendor_id, product_id);
}

void add_device(std::uint8_t dev_addr, std::uint16_t vendor_id,
                std::uint16_t product_id) {
    for (Device& device : devices) {
        if (!device.present || device.address == dev_addr) {
            device = Device{true, dev_addr, vendor_id, product_id};
            return;
        }
    }
}

void set_protocol(std::uint8_t dev_addr, std::uint8_t instance,
                  std::uint8_t protocol) {
    for (Interface& interface : interfaces) {
        if (!interface.present || (interface.address == dev_addr &&
                                   interface.instance == instance)) {
            interface = Interface{true, dev_addr, instance, protocol};
            return;
        }
    }
}

void set_receive_result(bool result) { receive_result = result; }

void set_host_initialization_result(bool configure, bool initialize) {
    configure_result = configure;
    initialize_result = initialize;
}

std::size_t receive_count() { return receive_calls_used; }

std::size_t receive_count(std::uint8_t dev_addr, std::uint8_t instance) {
    std::size_t count = 0;
    for (std::size_t index = 0; index < receive_calls_used; ++index) {
        if (receive_calls[index].address == dev_addr &&
            receive_calls[index].instance == instance) {
            ++count;
        }
    }
    return count;
}

std::size_t host_task_count() { return host_tasks; }

std::uint32_t system_clock_khz() { return clock_khz; }

std::uint8_t configured_pin_dp() { return pin_dp; }

}  // namespace duo::test::tinyusb_host

extern "C" bool tuh_vid_pid_get(std::uint8_t dev_addr, std::uint16_t* vendor_id,
                                std::uint16_t* product_id) {
    for (const Device& device : devices) {
        if (device.present && device.address == dev_addr) {
            *vendor_id = device.vendor_id;
            *product_id = device.product_id;
            return true;
        }
    }
    return false;
}

extern "C" std::uint8_t tuh_hid_interface_protocol(std::uint8_t dev_addr,
                                                    std::uint8_t instance) {
    for (const Interface& interface : interfaces) {
        if (interface.present && interface.address == dev_addr &&
            interface.instance == instance) {
            return interface.protocol;
        }
    }
    return duo::test::tinyusb_host::kProtocolNone;
}

extern "C" bool tuh_hid_receive_report(std::uint8_t dev_addr,
                                       std::uint8_t instance) {
    if (receive_calls_used < receive_calls.size()) {
        receive_calls[receive_calls_used++] = ReceiveCall{dev_addr, instance};
    }
    return receive_result;
}

extern "C" bool set_sys_clock_khz(std::uint32_t requested_khz, bool required) {
    (void)required;
    clock_khz = requested_khz;
    return true;
}

extern "C" bool tuh_configure(std::uint8_t rhport, std::uint8_t cfg_id,
                                const void* config) {
    (void)rhport;
    (void)cfg_id;
    pin_dp = *static_cast<const std::uint8_t*>(config);
    return configure_result;
}

extern "C" bool tuh_init(std::uint8_t rhport) {
    (void)rhport;
    return initialize_result;
}

extern "C" void tuh_task(void) { ++host_tasks; }
