// Reading a USB configuration descriptor, carefully.
//
// This is bytes from a stranger. The descriptor arrives from whatever someone
// plugged in, its records carry their own lengths, and those lengths are the
// only thing saying where the next record starts. A parser that believes them
// walks wherever it is pointed - which for firmware means out of the buffer
// and into whatever is next in memory.
//
// So every length is checked against what is left, not against what the
// descriptor claims it has, and a descriptor that does not add up is rejected
// whole rather than partly understood. Half a keyboard is not a keyboard.
//
// What is being looked for is narrow on purpose: one HID interface that this
// firmware can actually route - a boot keyboard or a boot mouse - and the
// interrupt endpoint its reports arrive on. Everything else is refused by
// name, so an unsupported device says so instead of half working.

#include "ch375/hid_parser.hpp"
#include "test_support.hpp"

#include <cstdio>
#include <string>
#include <vector>

using duo_input::u1::ch375::DeviceKind;
using duo_input::u1::ch375::HidCapabilities;
using duo_input::u1::ch375::ParseError;
using duo_input::u1::ch375::parse_configuration;

namespace {

std::vector<std::uint8_t> load(const char* name) {
    std::string path = DUO_HID_DESCRIPTORS_PATH;
    path += "/";
    path += name;

    std::vector<std::uint8_t> bytes;
    std::FILE* file = std::fopen(path.c_str(), "rb");
    if (file == nullptr) {
        return bytes;
    }
    std::uint8_t chunk[256];
    std::size_t read = 0;
    while ((read = std::fread(chunk, 1, sizeof(chunk), file)) > 0) {
        bytes.insert(bytes.end(), chunk, chunk + read);
    }
    std::fclose(file);
    return bytes;
}

duo_input::protocol::ByteView view(const std::vector<std::uint8_t>& bytes) {
    return duo_input::protocol::ByteView{bytes.data(), bytes.size()};
}

}  // namespace

// ------------------------------------------------------------ the corpus

TEST_CASE(the_descriptor_corpus_is_present) {
    // A missing vector file would otherwise make every test below pass by
    // rejecting an empty buffer.
    CHECK(!load("boot_keyboard.bin").empty());
    CHECK(!load("boot_mouse.bin").empty());
    CHECK(!load("consumer_composite.bin").empty());
    CHECK(!load("hub.bin").empty());
}

// ---------------------------------------------------------- what is wanted

TEST_CASE(a_boot_keyboard_is_recognised) {
    const std::vector<std::uint8_t> bytes = load("boot_keyboard.bin");
    HidCapabilities found;

    CHECK_EQ(static_cast<int>(parse_configuration(view(bytes), found)),
             static_cast<int>(ParseError::None));
    CHECK_EQ(static_cast<int>(found.kind), static_cast<int>(DeviceKind::Keyboard));
}

TEST_CASE(a_boot_keyboards_endpoint_is_the_one_it_declared) {
    const std::vector<std::uint8_t> bytes = load("boot_keyboard.bin");
    HidCapabilities found;
    parse_configuration(view(bytes), found);

    // 0x81 is endpoint 1, IN. The number is what a token is addressed to, so
    // taking it from the descriptor is the whole point of reading one.
    CHECK_EQ(found.endpoint, 1u);
    CHECK_EQ(found.max_packet, 8u);
    CHECK_EQ(found.interface_number, 0u);
}

TEST_CASE(a_boot_mouse_is_recognised) {
    const std::vector<std::uint8_t> bytes = load("boot_mouse.bin");
    HidCapabilities found;

    CHECK_EQ(static_cast<int>(parse_configuration(view(bytes), found)),
             static_cast<int>(ParseError::None));
    CHECK_EQ(static_cast<int>(found.kind), static_cast<int>(DeviceKind::Mouse));
    CHECK_EQ(found.endpoint, 2u);
    CHECK_EQ(found.max_packet, 4u);
}

TEST_CASE(a_mouse_without_the_boot_subclass_is_still_a_mouse) {
    // Plenty of mice declare the mouse protocol without claiming boot support.
    // Refusing those would refuse most of the wired mice on sale.
    const std::vector<std::uint8_t> bytes = load("mouse_5_button.bin");
    HidCapabilities found;

    CHECK_EQ(static_cast<int>(parse_configuration(view(bytes), found)),
             static_cast<int>(ParseError::None));
    CHECK_EQ(static_cast<int>(found.kind), static_cast<int>(DeviceKind::Mouse));
    CHECK(!found.boot_protocol);
}

TEST_CASE(the_boot_subclass_is_reported_when_it_is_there) {
    const std::vector<std::uint8_t> bytes = load("boot_mouse.bin");
    HidCapabilities found;
    parse_configuration(view(bytes), found);

    // Worth knowing: a boot device can be put into a report format this
    // firmware already understands, without reading its report descriptor.
    CHECK(found.boot_protocol);
}

TEST_CASE(a_composite_device_is_searched_past_its_first_interface) {
    // The first HID interface here is consumer controls. A parser that stops
    // at the first one it meets picks an interface this firmware cannot route
    // and calls the keyboard behind it unsupported.
    const std::vector<std::uint8_t> bytes = load("consumer_composite.bin");
    HidCapabilities found;

    CHECK_EQ(static_cast<int>(parse_configuration(view(bytes), found)),
             static_cast<int>(ParseError::None));
    CHECK_EQ(static_cast<int>(found.kind), static_cast<int>(DeviceKind::Keyboard));
    CHECK_EQ(found.interface_number, 1u);
    CHECK_EQ(found.endpoint, 1u);
}

// --------------------------------------------------------- what is refused

TEST_CASE(a_descriptor_that_stops_mid_record_is_refused) {
    // The last record says it is longer than what is left. Believing it walks
    // off the end of the buffer, and these bytes came from a stranger.
    const std::vector<std::uint8_t> bytes = load("truncated_item.bin");
    HidCapabilities found;

    CHECK_EQ(static_cast<int>(parse_configuration(view(bytes), found)),
             static_cast<int>(ParseError::Truncated));
}

TEST_CASE(an_endpoint_bigger_than_the_chip_can_read_is_refused) {
    // The controller's buffer is 64 bytes (DS1 5.13). An endpoint promising
    // more cannot be read, and accepting it would mean reports silently cut
    // in half - which is worse than refusing the device.
    const std::vector<std::uint8_t> bytes = load("impossible_report_size.bin");
    HidCapabilities found;

    CHECK_EQ(static_cast<int>(parse_configuration(view(bytes), found)),
             static_cast<int>(ParseError::PacketTooLarge));
}

TEST_CASE(a_vendor_specific_device_is_refused_by_name) {
    const std::vector<std::uint8_t> bytes = load("vendor_only.bin");
    HidCapabilities found;

    CHECK_EQ(static_cast<int>(parse_configuration(view(bytes), found)),
             static_cast<int>(ParseError::NoUsableInterface));
}

TEST_CASE(a_hub_is_refused_by_name) {
    // Hubs are outside what this device promises, and saying so is better than
    // enumerating one and appearing to work until someone plugs in two things.
    const std::vector<std::uint8_t> bytes = load("hub.bin");
    HidCapabilities found;

    CHECK_EQ(static_cast<int>(parse_configuration(view(bytes), found)),
             static_cast<int>(ParseError::NoUsableInterface));
}

// ------------------------------------------------------- malformed input

TEST_CASE(an_empty_descriptor_is_refused) {
    HidCapabilities found;

    CHECK_EQ(static_cast<int>(parse_configuration(duo_input::protocol::ByteView{nullptr, 0}, found)),
             static_cast<int>(ParseError::Truncated));
}

TEST_CASE(a_record_claiming_zero_length_is_refused) {
    // Zero would leave the walk standing still, reading the same record until
    // the power goes off.
    const std::uint8_t bytes[] = {9, 0x02, 12, 0, 1, 1, 0, 0x80, 50, 0, 0x04, 0};
    HidCapabilities found;

    CHECK_EQ(static_cast<int>(
                 parse_configuration(duo_input::protocol::ByteView{bytes, sizeof(bytes)}, found)),
             static_cast<int>(ParseError::Truncated));
}

TEST_CASE(a_descriptor_that_is_not_a_configuration_is_refused) {
    // A device descriptor, offered where a configuration belongs.
    const std::uint8_t bytes[] = {18, 0x01, 0x00, 0x02, 0, 0, 0, 8};
    HidCapabilities found;

    CHECK_EQ(static_cast<int>(
                 parse_configuration(duo_input::protocol::ByteView{bytes, sizeof(bytes)}, found)),
             static_cast<int>(ParseError::NotAConfiguration));
}

TEST_CASE(a_hid_interface_with_no_interrupt_endpoint_is_refused) {
    // Class and protocol say keyboard, but there is nowhere for reports to
    // arrive from. Accepting it would give a device that enumerates and then
    // says nothing forever.
    const std::uint8_t bytes[] = {
        9, 0x02, 25, 0, 1, 1, 0, 0x80, 50,                  // configuration
        9, 0x04, 0, 0, 1, 0x03, 0x01, 0x01, 0,              // HID keyboard interface
        7, 0x05, 0x01, 0x02, 64, 0, 0,                      // a bulk OUT endpoint
    };
    HidCapabilities found;

    CHECK_EQ(static_cast<int>(
                 parse_configuration(duo_input::protocol::ByteView{bytes, sizeof(bytes)}, found)),
             static_cast<int>(ParseError::NoUsableInterface));
}

TEST_CASE(a_total_length_longer_than_the_buffer_is_refused) {
    // The header claims more than arrived. The chip's control buffer is 64
    // bytes, so a long descriptor genuinely does arrive cut short, and the
    // claim is the only warning there is.
    const std::uint8_t bytes[] = {9, 0x02, 200, 0, 1, 1, 0, 0x80, 50};
    HidCapabilities found;

    CHECK_EQ(static_cast<int>(
                 parse_configuration(duo_input::protocol::ByteView{bytes, sizeof(bytes)}, found)),
             static_cast<int>(ParseError::Truncated));
}
