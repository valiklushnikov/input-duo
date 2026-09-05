#pragma once

#include <cstddef>
#include <cstdint>

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

}  // extern "C"
