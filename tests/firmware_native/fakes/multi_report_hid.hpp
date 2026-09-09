#pragma once

// Captured descriptor/side-button packets, plus synthetic consumer and bitmap
// packets for the other declared reports, shared by native integration tests.
#include "input/source.hpp"
#include "test_support.hpp"

#include <filesystem>
#include <fstream>
#include <vector>

namespace duo::test::multi_report_hid {

inline constexpr std::uint8_t kSidePress[] = {0x01, 0x01, 0x00, 0x4F,
                                            0x00, 0x00, 0x00, 0x00, 0x03};
inline constexpr std::uint8_t kSideRelease[] = {0x01, 0x00, 0x00, 0x00,
                                              0x00, 0x00, 0x00, 0x00, 0x03};
inline constexpr std::uint8_t kConsumerPress[] = {0x02, 0xE9, 0x00};
inline constexpr std::uint8_t kConsumerRelease[] = {0x02, 0x00, 0x00};
// ID 12: modifier byte followed by the bitmap starting at usage zero.
inline constexpr std::uint8_t kBitmapPress[21] = {0x0C, 0x02, 0x10};
inline constexpr std::uint8_t kBitmapRelease[21] = {0x0C};

inline std::vector<std::uint8_t> descriptor() {
    const auto path = std::filesystem::path(__FILE__).parent_path().parent_path().parent_path() /
                      "vectors" / "usb_descriptors" /
                      "keychron_3434_d030_interface_2_report.hex";
    std::ifstream input(path);
    std::vector<std::uint8_t> bytes;
    char high = 0, low = 0;
    const auto digit = [](char value) {
        return value >= '0' && value <= '9' ? value - '0' : value - 'A' + 10;
    };
    while (input >> high >> low) {
        bytes.push_back(static_cast<std::uint8_t>((digit(high) << 4) | digit(low)));
    }
    CHECK_EQ(bytes.size(), 164u);
    return bytes;
}

inline duo_input::u1::input::SourceIdentity identity() {
    namespace hid = duo_input::u1::input::hid;
    const auto bytes = descriptor();
    duo_input::u1::input::SourceIdentity out;
    out.vendor_id = 0x3434;
    out.product_id = 0xD030;
    out.interface_number = 2;
    CHECK_EQ(hid::parse_hid_report_set({bytes.data(), bytes.size()}, out.report_set),
             hid::ReportDescriptorError::None);
    CHECK_EQ(out.report_set.count, 3);
    // Deliberately keep the old single-layout consumer selection: dispatch
    // must consume the report set even when legacy identity fields disagree.
    out.kind = duo_input::u1::input::DeviceKind::Consumer;
    out.keyboard_layout = out.report_set.entries[1].keyboard;
    return out;
}

// Hand-built old fixtures now fill the same one-entry contract as boot setup.
inline void set_single_report(duo_input::u1::input::SourceIdentity& identity) {
    namespace input = duo_input::u1::input;
    identity.report_set = {};
    if (identity.kind == input::DeviceKind::Unknown) return;
    auto& entry = identity.report_set.entries[0];
    identity.report_set.count = 1;
    if (identity.kind == input::DeviceKind::Mouse) {
        entry.role = input::hid::ReportRole::Mouse;
        entry.mouse = identity.mouse_layout;
        identity.report_set.uses_report_ids = entry.mouse.report_id;
        entry.report_id = entry.mouse.report_id_value;
    } else {
        entry.role = identity.kind == input::DeviceKind::Consumer
                         ? input::hid::ReportRole::Consumer : input::hid::ReportRole::Keyboard;
        entry.keyboard = identity.keyboard_layout;
        identity.report_set.uses_report_ids = entry.keyboard.report_id;
        entry.report_id = entry.keyboard.report_id_value;
    }
}

}  // namespace duo::test::multi_report_hid
