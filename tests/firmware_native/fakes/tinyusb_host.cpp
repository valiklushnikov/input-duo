#include "fakes/tinyusb_host.hpp"
#include "tusb.h"

// The same header firmware/u1_main/pio_usb/tinyusb_host_callbacks.cpp
// includes; in this build it resolves to fakes/hardware/timer.h, whose
// time_us_32() is the settable function defined at the bottom of this file
// rather than the SDK's inline register read.
#include "hardware/timer.h"

// The same two headers firmware/u1_main/pio_usb/backend.cpp includes; in this
// build they resolve to fakes/hardware/clocks.h and fakes/pio_usb.h, whose
// clock_get_hz(), pio_usb_host_get_frame_number() and pio_usb_root_port are
// the settable stand-ins defined at the bottom of this file.
#include "hardware/clocks.h"
#include "host/hcd.h"
#include "pio_usb.h"

#include <array>

namespace {

// What the firmware's recovery reads and writes. Both are real TinyUSB entry
// points defined in usbh.c; here they are the settable stand-in and the
// recorder, so a test can see the event the recovery queued and the values it
// addressed it with.
hcd_devtree_info_t device_zero_topology{};
std::vector<duo::test::tinyusb_host::HostEvent> queued_host_events;

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
    std::uint8_t interface_number = 0;
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
std::uint32_t now_us = 0;
bool host_already_active = false;
bool host_inited = false;
std::uint32_t system_clock_hz = 0;
std::uint32_t system_clock_hz_at_configure = 0;
std::uint32_t sof_frames = 0;
bool hub_mounted = false;
std::array<bool, 16> device_mounted{};
std::uint32_t stack_pointer = 0;
duo::test::tinyusb_host::TuhTaskEffect tuh_task_effect =
    duo::test::tinyusb_host::TuhTaskEffect::None;

}  // namespace

// The real symbol pio_usb.c defines, which backend.cpp declares extern and
// reads its root-port bits out of. Defined here so the native build links
// exactly the declaration the firmware build links.
extern "C" root_port_t pio_usb_root_port[PIO_USB_ROOT_PORT_CNT];
root_port_t pio_usb_root_port[PIO_USB_ROOT_PORT_CNT]{};
extern "C" endpoint_t pio_usb_ep_pool[PIO_USB_EP_POOL_CNT];
endpoint_t pio_usb_ep_pool[PIO_USB_EP_POOL_CNT]{};

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
    now_us = 0;
    host_already_active = false;
    host_inited = false;
    // Backend fixtures begin where the real Core 1 begins: main() has already
    // selected and settled the reference host's 120 MHz clock.
    system_clock_hz = 120000000u;
    system_clock_hz_at_configure = 0;
    sof_frames = 0;
    hub_mounted = false;
    device_mounted = {};
    stack_pointer = 0;
    tuh_task_effect = TuhTaskEffect::None;
    pio_usb_root_port[0] = root_port_t{};
    pio_usb_root_port[1] = root_port_t{};
    for (endpoint_t& endpoint : pio_usb_ep_pool) {
        endpoint = endpoint_t{};
    }
    device_zero_topology = hcd_devtree_info_t{};
    queued_host_events.clear();
}

void add_hub(std::uint16_t vendor_id, std::uint16_t product_id) {
    // TinyUSB owns the hub entry internally and intentionally suppresses
    // the application tuh_mount_cb for it. The fake retains its address for
    // metadata parity but callers must not dispatch an application callback.
    add_device(kHubAddress, vendor_id, product_id);
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
            interface = Interface{true, dev_addr, instance, protocol, instance};
            return;
        }
    }
}

void set_receive_result(bool result) { receive_result = result; }

void set_interface_number(std::uint8_t dev_addr, std::uint8_t instance,
                          std::uint8_t interface_number) {
    for (Interface& interface : interfaces) {
        if (interface.present && interface.address == dev_addr &&
            interface.instance == instance) {
            interface.interface_number = interface_number;
            return;
        }
    }
}

void set_now_us(std::uint32_t value) { now_us = value; }

void set_host_initialization_result(bool configure, bool initialize) {
    configure_result = configure;
    initialize_result = initialize;
}

void set_host_already_active(bool active) { host_already_active = active; }

void set_host_inited(bool inited) { host_inited = inited; }

void set_system_clock_hz(std::uint32_t hz) { system_clock_hz = hz; }

void set_sof_frame_count(std::uint32_t frames) { sof_frames = frames; }

void set_endpoint(std::size_t index, std::uint16_t size, std::uint8_t failed_count) {
    if (index < PIO_USB_EP_POOL_CNT) {
        pio_usb_ep_pool[index].size = size;
        pio_usb_ep_pool[index].failed_count = failed_count;
    }
}

void set_endpoint_identity(std::size_t index, std::uint16_t size,
                           std::uint8_t dev_addr, std::uint8_t ep_num) {
    if (index < PIO_USB_EP_POOL_CNT) {
        pio_usb_ep_pool[index].size = size;
        pio_usb_ep_pool[index].dev_addr = dev_addr;
        pio_usb_ep_pool[index].ep_num = ep_num;
    }
}

void set_endpoint_transfer(std::size_t index, const EndpointTransfer& transfer) {
    if (index < PIO_USB_EP_POOL_CNT) {
        pio_usb_ep_pool[index].has_transfer = transfer.has_transfer;
        pio_usb_ep_pool[index].is_tx = transfer.is_tx;
        pio_usb_ep_pool[index].data_id = transfer.data_id;
        pio_usb_ep_pool[index].need_pre = transfer.need_pre;
        pio_usb_ep_pool[index].stalled = transfer.stalled;
        pio_usb_ep_pool[index].transfer_aborted = transfer.transfer_aborted;
    }
}

void set_tuh_task_effect(TuhTaskEffect effect) { tuh_task_effect = effect; }

void set_device_zero_topology(std::uint8_t rhport, std::uint8_t hub_addr,
                              std::uint8_t hub_port) {
    device_zero_topology = {rhport, hub_addr, hub_port, 0};
}

const std::vector<HostEvent>& host_events() { return queued_host_events; }

void set_hub_mounted(bool mounted) { hub_mounted = mounted; }

void set_device_mounted(std::uint8_t dev_addr, bool mounted) {
    if (dev_addr < device_mounted.size()) {
        device_mounted[dev_addr] = mounted;
    }
}

void forget_devices() { devices = {}; }

void set_stack_pointer(std::uint32_t value) { stack_pointer = value; }

std::uint32_t clock_hz_at_configure() { return system_clock_hz_at_configure; }

bool host_already_active_now() { return host_already_active; }

void set_root_port(bool initialized, bool connected, bool suspended,
                   bool is_fullspeed) {
    pio_usb_root_port[0].initialized = initialized;
    pio_usb_root_port[0].connected = connected;
    pio_usb_root_port[0].suspended = suspended;
    pio_usb_root_port[0].is_fullspeed = is_fullspeed;
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

extern "C" bool tuh_hid_itf_get_info(std::uint8_t dev_addr,
                                    std::uint8_t instance, tuh_itf_info_t* info) {
    for (const Interface& interface : interfaces) {
        if (interface.present && interface.address == dev_addr &&
            interface.instance == instance) {
            *info = {dev_addr, {9, 4, interface.interface_number, 0, 1, 3,
                               static_cast<std::uint8_t>(interface.protocol != 0),
                               interface.protocol, 0}};
            return true;
        }
    }
    return false;
}

extern "C" bool set_sys_clock_khz(std::uint32_t requested_khz, bool required) {
    (void)required;
    clock_khz = requested_khz;
    // The real call moves clk_sys, and the two clock fields in the diagnostics
    // reply exist precisely to show a value captured before it against one read
    // after it. A fake that left clock_get_hz() alone would let a test pass
    // while the firmware reported the same number twice.
    system_clock_hz = requested_khz * 1000u;
    return true;
}

extern "C" std::uint32_t clock_get_hz(std::uint32_t clock) {
    (void)clock;
    return system_clock_hz;
}

extern "C" std::uint32_t pio_usb_host_get_frame_number(void) { return sof_frames; }

extern "C" bool tuh_configure(std::uint8_t rhport, std::uint8_t cfg_id,
                                const void* config) {
    (void)rhport;
    (void)cfg_id;
    pin_dp = *static_cast<const std::uint8_t*>(config);
    // The clock the real hcd_configure/hcd_init pair would compute every PIO
    // divider from. Recorded here so a test can assert the host is brought up
    // AFTER the system clock moves, rather than leaving that ordering to a
    // comment - it is the whole reason clk_hz_now is the divider clock.
    system_clock_hz_at_configure = system_clock_hz;
    return configure_result;
}

extern "C" bool tuh_init(std::uint8_t rhport) {
    (void)rhport;
    // Faithful to .deps/tinyusb/src/host/usbh.c:415: tuh_rhport_init sets
    // _usbh_controller = rhport once it is past the already-active check, and
    // it does so BEFORE hcd_init and regardless of whether hcd_init then
    // fails. So after any tuh_init that got that far, tuh_rhport_is_active()
    // reads true.
    //
    // Without this the fake could not tell "sampled before tuh_init" from
    // "sampled after" - and that ordering is the entire smoking gun. A sample
    // taken after this call reads 1 on a CORRECT image, which would send an
    // operator chasing a regression that is not there.
    host_already_active = true;
    return initialize_result;
}

extern "C" bool tuh_rhport_is_active(std::uint8_t rhport) {
    (void)rhport;
    return host_already_active;
}

extern "C" bool tuh_inited(void) { return host_inited; }

extern "C" bool tuh_mounted(std::uint8_t dev_addr) {
    if (dev_addr == duo::test::tinyusb_host::kHubAddress) {
        return hub_mounted;
    }
    return dev_addr < device_mounted.size() && device_mounted[dev_addr];
}

// firmware/u1_main/pio_usb/core1_stack_pointer.cpp is the shipping definition
// and reads MSP; here it is the value a test last set, so a case can put
// Core 1's stack wherever it needs it without an ARM core to read one from.
extern "C" std::uint32_t duo_core1_stack_pointer(void) { return stack_pointer; }

extern "C" void tuh_task(void) {
    ++host_tasks;
    if (tuh_task_effect == duo::test::tinyusb_host::TuhTaskEffect::StartAddressZeroTransferOnce) {
        // This transfer begins while the host stack is being serviced, not in
        // the pre-service pool observation. The call-order test must observe
        // the resulting one-pass delay before starting its 2 s watchdog.
        endpoint_t& endpoint = pio_usb_ep_pool[2];
        endpoint.size = 8u;
        endpoint.dev_addr = 0u;
        endpoint.ep_num = 0u;
        endpoint.has_transfer = true;
        endpoint.is_tx = true;
        endpoint.data_id = 1u;
        tuh_task_effect = duo::test::tinyusb_host::TuhTaskEffect::None;
    }
}

// Deliberately not derived from host_tasks or from any per-pass counter: the
// point of the capture timestamp is that it is read where the report arrives,
// so the fake lets a test move it between two callbacks that share one pass.
extern "C" std::uint32_t time_us_32(void) { return now_us; }

extern "C" void hcd_devtree_get_info(std::uint8_t dev_addr,
                                     hcd_devtree_info_t* devtree_info) {
    // usbh.c's own behaviour: an unaddressed device has no entry in the device
    // table, so the answer comes from _dev0 - the port being enumerated.
    (void)dev_addr;
    *devtree_info = device_zero_topology;
}

extern "C" void hcd_event_handler(hcd_event_t const* event, bool in_isr) {
    queued_host_events.push_back(duo::test::tinyusb_host::HostEvent{
        event->rhport, event->event_id, event->connection.hub_addr,
        event->connection.hub_port, in_isr});
}
