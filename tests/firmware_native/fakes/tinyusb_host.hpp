#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

#include "pio_usb.h"

namespace duo::test::tinyusb_host {

inline constexpr std::uint8_t kProtocolNone = 0;
inline constexpr std::uint8_t kProtocolKeyboard = 1;
inline constexpr std::uint8_t kProtocolMouse = 2;
inline constexpr std::uint8_t kFirstDownstreamAddress = 1;
inline constexpr std::uint8_t kLastDownstreamAddress = 4;
inline constexpr std::uint8_t kHubAddress = 5;

void reset();
void add_hub(std::uint16_t vendor_id, std::uint16_t product_id);
void add_device(std::uint8_t dev_addr, std::uint16_t vendor_id,
                std::uint16_t product_id);
void set_protocol(std::uint8_t dev_addr, std::uint8_t instance,
                  std::uint8_t protocol);
void set_receive_result(bool result);
void set_host_initialization_result(bool configure_result, bool initialize_result);
/// Whether tuh_rhport_is_active() reports the host stack already up.
///
/// The defect this exists to detect: something other than
/// PioUsbBackend::begin() initialised the host first, after which
/// tuh_configure and tuh_init are no-ops that still return true. A test that
/// sets this is reproducing that board, not a hypothetical one.
///
/// tuh_init() in this fake SETS it, the way usbh.c:415 does, so a sample taken
/// after the bring-up reads true and a sample taken before it does not. That
/// is what makes the ordering testable rather than merely commented.
void set_host_already_active(bool active);
/// What tuh_inited() reports after begin()'s calls.
void set_host_inited(bool inited);
/// What clock_get_hz(clk_sys) returns from here on.
///
/// set_sys_clock_khz() in this fake moves it, so a test can watch begin()
/// capture one value before the call and Core 0 read another after it - which
/// is the whole point of the pair of clock fields in the reply.
void set_system_clock_hz(std::uint32_t hz);
/// What pio_usb_host_get_frame_number() returns from here on.
void set_sof_frame_count(std::uint32_t frames);
/// Populate one Pico-PIO-USB endpoint-pool entry with the two fields the
/// backend samples for its bounded wire-progress high-water marks.
void set_endpoint(std::size_t index, std::uint16_t size, std::uint8_t failed_count);
/// Populate one endpoint-pool entry with the identity ep_slot_map encodes.
///
/// ``ep_num`` carries the direction bit exactly as the library stores it, so a
/// test writes 0x81 for interrupt-IN endpoint 1 and 0x00 for a control
/// endpoint - including the address-0 control endpoint, whose address,
/// direction and number are all zero and which is the case the open bit
/// exists for.
void set_endpoint_identity(std::size_t index, std::uint16_t size,
                           std::uint8_t dev_addr, std::uint8_t ep_num);
/// Live Pico-PIO-USB fields behind the transfer-state diagnostic byte.
struct EndpointTransfer {
    bool has_transfer = false;
    bool is_tx = false;
    std::uint8_t data_id = 0;
    bool need_pre = false;
    bool stalled = false;
    bool transfer_aborted = false;
};
void set_endpoint_transfer(std::size_t index, const EndpointTransfer& transfer);
/// A controlled consequence of the next fake ``tuh_task()`` call.
///
/// The service-order guard needs a transfer that starts *inside* host service:
/// sampling it before service must begin its watchdog window on the next pass,
/// while sampling after service would incorrectly charge time spent in TinyUSB
/// to that new transfer.
enum class TuhTaskEffect : std::uint8_t {
    None,
    StartAddressZeroTransferOnce,
};
void set_tuh_task_effect(TuhTaskEffect effect);
/// What hcd_devtree_get_info(0, ...) reports - the port the host stack is
/// enumerating. The recovery has to address its synthetic attach at exactly
/// this port, because any other value takes tuh_task's "defer" branch instead
/// of its "duplicated attach" one.
void set_device_zero_topology(std::uint8_t rhport, std::uint8_t hub_addr,
                              std::uint8_t hub_port);
/// One event the firmware handed to hcd_event_handler.
struct HostEvent {
    std::uint8_t rhport = 0;
    std::uint8_t event_id = 0;
    std::uint8_t hub_addr = 0;
    std::uint8_t hub_port = 0;
    bool in_isr = false;
};
const std::vector<HostEvent>& host_events();
void set_hub_mounted(bool mounted);
/// What tuh_mounted() reports for one downstream address.
void set_device_mounted(std::uint8_t dev_addr, bool mounted);
/// Drop every device the fake knows, without touching anything else.
///
/// Models TinyUSB forgetting an address between two passes, which is what the
/// sticky half of enum_progress_mask has to survive.
void forget_devices();
/// What __get_MSP() returns from here on - this build's stand-in for Core 1's
/// stack pointer at the moment a host event is queued.
void set_stack_pointer(std::uint32_t value);
/// The four root-port flags PioUsbBackend::observe() packs into one byte.
void set_root_port(bool initialized, bool connected, bool suspended,
                   bool is_fullspeed);
/// What the fake's time_us_32() returns from here on - the test's own stand-in
/// for the clock tuh_hid_report_received_cb reads at capture. It persists
/// until changed (reset() clears it to zero), so a test can hand two reports
/// delivered inside one Core 1 pass two different capture times and see each
/// arrive on its own SourceEvent.
void set_now_us(std::uint32_t value);

std::size_t receive_count();
std::size_t receive_count(std::uint8_t dev_addr, std::uint8_t instance);
std::size_t host_task_count();
std::uint32_t system_clock_khz();
/// clock_get_hz(clk_sys) as it stood when tuh_configure() was called - the
/// clock the real Pico-PIO-USB would compute every PIO divider from.
std::uint32_t clock_hz_at_configure();
/// What tuh_rhport_is_active() would report right now, so a test can tell a
/// backend that sampled before bringing the host up from one that never
/// brought it up at all.
bool host_already_active_now();
std::uint8_t configured_pin_dp();

}  // namespace duo::test::tinyusb_host

extern "C" {

void tuh_mount_cb(std::uint8_t dev_addr);
void tuh_umount_cb(std::uint8_t dev_addr);
void tuh_hid_mount_cb(std::uint8_t dev_addr, std::uint8_t instance,
                      std::uint8_t const* report_desc, std::uint16_t desc_len);
void tuh_hid_umount_cb(std::uint8_t dev_addr, std::uint8_t instance);
void tuh_hid_report_received_cb(std::uint8_t dev_addr, std::uint8_t instance,
                                std::uint8_t const* report, std::uint16_t len);

/// TinyUSB's weak host event hook, which the firmware under test defines.
///
/// Declared beside the other firmware-owned callbacks because a test drives it
/// the same way: usbh.c's queue_event() calls it for every event the host
/// stack queues, with in_isr saying which of Core 1's two contexts it is in.
void tuh_event_hook_cb(std::uint8_t rhport, std::uint32_t eventid, bool in_isr);

}  // extern "C"
