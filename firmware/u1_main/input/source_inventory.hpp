#pragma once

#include <cstddef>
#include <cstdint>

namespace duo_input::u1::input {
inline constexpr std::size_t kSourceCapacity = 8;
inline constexpr std::size_t kProductNameBytes = 48;
struct SourceInfo {
    std::uint16_t vendor_id = 0;
    std::uint16_t product_id = 0;
    std::uint8_t interface_number = 0;
    std::uint8_t kind = 0;
    std::uint8_t device_address = 0;
    char product_name[kProductNameBytes] = {};
    std::uint32_t reports = 0;
    std::uint32_t decoded_events = 0;
    std::uint8_t last_report_size = 0;
    std::uint8_t last_report[9] = {};
    std::uint8_t layout_source = 0;
    std::uint8_t report_id = 0;
    std::uint8_t minimum_body_bytes = 0;
    std::uint8_t keyboard_error = 0xFF;
    std::uint8_t consumer_error = 0xFF;
};
struct SourceInventory {
    std::uint8_t count = 0;
    std::uint32_t rejected_interfaces = 0;
    SourceInfo sources[kSourceCapacity] = {};
};
}  // namespace duo_input::u1::input
