#include "pio_usb/hid_setup.hpp"
#include "test_support.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

using duo_input::u1::input::DeviceKind;
using duo_input::u1::input::SourceIdentity;
using duo_input::u1::input::hid::KeyboardFieldKind;
using duo_input::u1::pio_usb::classify_hid;

namespace {

constexpr std::uint8_t kProtocolNone = 0;
constexpr std::uint8_t kProtocolKeyboard = 1;
constexpr std::uint8_t kProtocolMouse = 2;

std::vector<std::uint8_t> read_binary(const std::string& path) {
    std::ifstream stream(path, std::ios::binary);
    return {std::istreambuf_iterator<char>{stream}, std::istreambuf_iterator<char>{}};
}

std::vector<std::uint8_t> read_hex(const std::string& path) {
    std::ifstream stream(path, std::ios::binary);
    const std::string text{std::istreambuf_iterator<char>{stream},
                           std::istreambuf_iterator<char>{}};
    if (!stream.is_open() || text.empty()) {
        return {};
    }
    auto nibble = [](char value) -> int {
        if (value >= '0' && value <= '9') {
            return value - '0';
        }
        if (value >= 'A' && value <= 'F') {
            return value - 'A' + 10;
        }
        return -1;
    };
    std::vector<std::uint8_t> bytes;
    for (std::size_t at = 0; at + 1 < text.size(); at += 2) {
        if (text[at] == '\r' || text[at] == '\n') {
            break;
        }
        const int high = nibble(text[at]);
        const int low = nibble(text[at + 1]);
        if (high < 0 || low < 0) {
            return {};
        }
        bytes.push_back(static_cast<std::uint8_t>((high << 4) | low));
    }
    return bytes;
}

bool all_zero(const std::uint8_t* bytes, std::size_t size) {
    for (std::size_t index = 0; index < size; ++index) {
        if (bytes[index] != 0) {
            return false;
        }
    }
    return true;
}

std::vector<std::uint8_t> report_id_keyboard() {
    return {
        0x05, 0x0C, 0x85, 0x01,
        0x09, 0x01, 0x75, 0x08, 0x95, 0x04, 0x81, 0x02,
        0x05, 0x07, 0x85, 0x02,
        0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00, 0x25, 0x01,
        0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
        0x19, 0x00, 0x29, 0x65, 0x15, 0x00, 0x25, 0x65,
        0x75, 0x08, 0x95, 0x06, 0x81, 0x00,
    };
}

std::vector<std::uint8_t> five_button_wheel_mouse() {
    return {
        0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x09, 0x01, 0xA1, 0x00,
        0x05, 0x09, 0x19, 0x01, 0x29, 0x05, 0x15, 0x00, 0x25, 0x01,
        0x95, 0x05, 0x75, 0x01, 0x81, 0x02,
        0x95, 0x01, 0x75, 0x03, 0x81, 0x03,
        0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 0x09, 0x38,
        0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x03, 0x81, 0x06,
        0xC0, 0xC0,
    };
}

std::vector<std::uint8_t> vendor_only_hid() {
    return {
        0x06, 0x00, 0xFF,  // Usage Page (Vendor 0xFF00)
        0x09, 0x01,        // Usage (1)
        0x15, 0x00, 0x25, 0xFF,
        0x75, 0x08, 0x95, 0x08,
        0x81, 0x02,
    };
}

std::vector<std::uint8_t> consumer_control() {
    return {
        0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01,
        0x15, 0x00, 0x26, 0xFF, 0x03,
        0x19, 0x00, 0x2A, 0xFF, 0x03,
        0x75, 0x10, 0x95, 0x01, 0x81, 0x00, 0xC0,
    };
}

}  // namespace

TEST_CASE(empty_callback_descriptor_is_absent_and_uses_only_matching_boot_protocol) {
    for (const auto protocol : {kProtocolKeyboard, kProtocolMouse}) {
        SourceIdentity identity;
        CHECK(classify_hid(protocol, nullptr, 0, identity));
        CHECK_EQ(identity.kind, protocol == kProtocolKeyboard ? DeviceKind::Keyboard
                                                              : DeviceKind::Mouse);
        CHECK(all_zero(identity.descriptor_hash, sizeof(identity.descriptor_hash)));
    }

    const std::uint8_t ignored = 0xAA;
    SourceIdentity identity;
    CHECK_FALSE(classify_hid(kProtocolNone, &ignored, 0, identity));
    CHECK_EQ(identity.kind, DeviceKind::Unknown);
    CHECK(all_zero(identity.descriptor_hash, sizeof(identity.descriptor_hash)));
}

TEST_CASE(report_id_keyboard_uses_its_descriptor_layout_even_under_mouse_protocol) {
    const auto descriptor = report_id_keyboard();
    SourceIdentity identity;

    CHECK(classify_hid(kProtocolMouse, descriptor.data(), descriptor.size(), identity));
    CHECK_EQ(identity.kind, DeviceKind::Keyboard);
    CHECK(identity.keyboard_layout.report_id);
    CHECK_EQ(identity.keyboard_layout.report_id_value, std::uint8_t{2});
    CHECK_EQ(identity.keyboard_layout.key_kind, KeyboardFieldKind::Array);
    CHECK_EQ(identity.keyboard_layout.key_element_count, std::uint8_t{6});
    CHECK_EQ(identity.keyboard_layout.minimum_body_bytes, std::uint8_t{7});
    CHECK_EQ(identity.mouse_layout.minimum_body_bytes, std::uint8_t{0});
}

TEST_CASE(five_button_wheel_mouse_keeps_the_neutral_descriptor_layout) {
    const auto descriptor = five_button_wheel_mouse();
    SourceIdentity identity;

    CHECK(classify_hid(kProtocolKeyboard, descriptor.data(), descriptor.size(), identity));
    CHECK_EQ(identity.kind, DeviceKind::Mouse);
    CHECK_EQ(identity.mouse_layout.buttons.bits, std::uint8_t{5});
    CHECK_EQ(identity.mouse_layout.x.offset, std::uint8_t{1});
    CHECK_EQ(identity.mouse_layout.y.offset, std::uint8_t{2});
    CHECK(identity.mouse_layout.wheel.present);
    CHECK_EQ(identity.mouse_layout.wheel.offset, std::uint8_t{3});
    CHECK_EQ(identity.mouse_layout.minimum_body_bytes, std::uint8_t{3});
    CHECK_EQ(identity.keyboard_layout.key_kind, KeyboardFieldKind::None);
}

TEST_CASE(captured_aula_descriptor_is_classified_as_its_five_slot_keyboard_shape) {
    const auto descriptor = read_hex(std::string{DUO_TEST_VECTOR_DIR} +
                                     "/usb_descriptors/aula_f75_keyboard_report.hex");
    CHECK_EQ(descriptor.size(), std::size_t{77});
    SourceIdentity identity;

    CHECK(classify_hid(kProtocolKeyboard, descriptor.data(), descriptor.size(), identity));
    CHECK_EQ(identity.kind, DeviceKind::Keyboard);
    CHECK_EQ(identity.keyboard_layout.key_element_count, std::uint8_t{5});
    CHECK_EQ(identity.keyboard_layout.minimum_body_bytes, std::uint8_t{7});
}

TEST_CASE(valid_unsupported_descriptors_fall_back_only_for_a_matching_boot_protocol) {
    for (const auto descriptor : {vendor_only_hid(), consumer_control()}) {
        SourceIdentity ignored;
        CHECK_FALSE(classify_hid(kProtocolNone, descriptor.data(), descriptor.size(), ignored));
        CHECK_EQ(ignored.kind, DeviceKind::Unknown);

        SourceIdentity keyboard;
        CHECK(classify_hid(kProtocolKeyboard, descriptor.data(), descriptor.size(), keyboard));
        CHECK_EQ(keyboard.kind, DeviceKind::Keyboard);
        CHECK_EQ(keyboard.keyboard_layout.key_element_count, std::uint8_t{6});
    }
}

TEST_CASE(malformed_descriptor_can_fall_back_but_cannot_leave_partial_layout_state) {
    const std::uint8_t malformed[] = {0x75};
    SourceIdentity identity;
    identity.kind = DeviceKind::Mouse;
    identity.vendor_id = 0x1234;
    identity.product_id = 0x5678;
    identity.mouse_layout = duo_input::u1::input::hid::boot_mouse_layout();

    CHECK_FALSE(classify_hid(kProtocolNone, malformed, sizeof(malformed), identity));
    CHECK_EQ(identity.kind, DeviceKind::Unknown);
    CHECK_EQ(identity.vendor_id, std::uint16_t{0});
    CHECK_EQ(identity.product_id, std::uint16_t{0});
    CHECK_EQ(identity.mouse_layout.minimum_body_bytes, std::uint8_t{0});
    CHECK_FALSE(all_zero(identity.descriptor_hash, sizeof(identity.descriptor_hash)));
}

TEST_CASE(a_descriptor_with_both_supported_roles_is_rejected_as_ambiguous) {
    auto descriptor = report_id_keyboard();
    const auto mouse = five_button_wheel_mouse();
    descriptor.insert(descriptor.end(), mouse.begin(), mouse.end());
    SourceIdentity identity;

    CHECK_FALSE(classify_hid(kProtocolKeyboard, descriptor.data(), descriptor.size(), identity));
    CHECK_EQ(identity.kind, DeviceKind::Unknown);
    CHECK_EQ(identity.keyboard_layout.key_kind, KeyboardFieldKind::None);
    CHECK_EQ(identity.mouse_layout.minimum_body_bytes, std::uint8_t{0});
    CHECK_FALSE(all_zero(identity.descriptor_hash, sizeof(identity.descriptor_hash)));
}

TEST_CASE(exact_hash_distinguishes_prefix_equal_descriptors_and_never_hashes_absence) {
    constexpr std::uint8_t short_descriptor[] = {'a', 'b', 'c'};
    constexpr std::uint8_t long_descriptor[] = {'a', 'b', 'c', 'd'};
    constexpr std::array<std::uint8_t, 32> short_hash = {
        0xba, 0x78, 0x16, 0xbf, 0x8f, 0x01, 0xcf, 0xea,
        0x41, 0x41, 0x40, 0xde, 0x5d, 0xae, 0x22, 0x23,
        0xb0, 0x03, 0x61, 0xa3, 0x96, 0x17, 0x7a, 0x9c,
        0xb4, 0x10, 0xff, 0x61, 0xf2, 0x00, 0x15, 0xad,
    };
    constexpr std::array<std::uint8_t, 32> long_hash = {
        0x88, 0xd4, 0x26, 0x6f, 0xd4, 0xe6, 0x33, 0x8d,
        0x13, 0xb8, 0x45, 0xfc, 0xf2, 0x89, 0x57, 0x9d,
        0x20, 0x9c, 0x89, 0x78, 0x23, 0xb9, 0x21, 0x7d,
        0xa3, 0xe1, 0x61, 0x93, 0x6f, 0x03, 0x15, 0x89,
    };
    SourceIdentity short_identity;
    SourceIdentity long_identity;

    CHECK(classify_hid(kProtocolKeyboard, short_descriptor, sizeof(short_descriptor),
                       short_identity));
    CHECK(classify_hid(kProtocolKeyboard, long_descriptor, sizeof(long_descriptor),
                       long_identity));
    CHECK(std::memcmp(short_identity.descriptor_hash, short_hash.data(), short_hash.size()) == 0);
    CHECK(std::memcmp(long_identity.descriptor_hash, long_hash.data(), long_hash.size()) == 0);
    CHECK(std::memcmp(short_identity.descriptor_hash, long_identity.descriptor_hash,
                      sizeof(short_identity.descriptor_hash)) != 0);
}

TEST_CASE(every_existing_descriptor_fixture_is_bounded_and_not_mistaken_for_a_report_layout) {
    constexpr const char* fixtures[] = {
        "boot_keyboard.bin",
        "boot_mouse.bin",
        "consumer_composite.bin",
        "hub.bin",
        "impossible_report_size.bin",
        "mouse_5_button.bin",
        "truncated_item.bin",
        "vendor_only.bin",
    };
    for (const char* name : fixtures) {
        const auto descriptor = read_binary(std::string{DUO_TEST_VECTOR_DIR} +
                                            "/hid_descriptors/" + name);
        CHECK_FALSE(descriptor.empty());
        SourceIdentity identity;
        CHECK_FALSE(classify_hid(kProtocolNone, descriptor.data(), descriptor.size(), identity));
        CHECK_EQ(identity.kind, DeviceKind::Unknown);
        CHECK_FALSE(all_zero(identity.descriptor_hash, sizeof(identity.descriptor_hash)));
    }
}
