// What U2 tells a computer it is.
//
// The same three HID interfaces as U1, plus one CDC pair used only so PC2's
// program can swap IPv4 addresses with PC1's. U2 still holds no configuration
// of its own - the serial port answers nothing but HELLO/PING/EXCHANGE_ADDRESSES,
// and everything else with UNSUPPORTED_CAPABILITY.
//
// The HID report descriptors are byte-identical to U1's, and a test asserts
// that: the two computers must see the same keyboard and the same mouse, or a
// macro recorded against one would type something else on the other.
//
// The symbols here are extracted from the built ELF by
// tools/dump_usb_descriptors.py and checked by tests/build/test_usb_descriptors.py,
// so what this file says and what the board actually enumerates as cannot
// drift apart.

#include <cstdio>
#include <cstring>

#include "pico/unique_id.h"
#include "tusb.h"

#include "hid/report_ids.hpp"
#include "hid/usb_identity.hpp"

namespace hid = duo_input::hid;
namespace usb = duo_input::usb;

// --------------------------------------------------------------- device

extern "C" {

extern const tusb_desc_device_t desc_device;
const tusb_desc_device_t desc_device = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,

    // Three HID interfaces and nothing else, so the class is decided per
    // interface rather than at the device level.
    .bDeviceClass = 0x00,
    .bDeviceSubClass = 0x00,
    .bDeviceProtocol = 0x00,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,

    .idVendor = usb::kVendorId,
    .idProduct = usb::kU2ProductId,
    .bcdDevice = usb::kDeviceReleaseBcd,

    .iManufacturer = 0x01,
    .iProduct = 0x02,
    .iSerialNumber = 0x03,

    .bNumConfigurations = 0x01,
};

const std::uint8_t* tud_descriptor_device_cb(void) {
    return reinterpret_cast<const std::uint8_t*>(&desc_device);
}

// ----------------------------------------------------- report descriptors

// One report per interface, and therefore no report IDs. TUD_HID_REPORT_DESC_*
// omit the report ID entirely when given no argument.
extern const std::uint8_t desc_hid_keyboard_report[];
const std::uint8_t desc_hid_keyboard_report[] = {TUD_HID_REPORT_DESC_KEYBOARD()};
extern const std::uint8_t desc_hid_mouse_report[];
const std::uint8_t desc_hid_mouse_report[] = {TUD_HID_REPORT_DESC_MOUSE()};
extern const std::uint8_t desc_hid_consumer_report[];
const std::uint8_t desc_hid_consumer_report[] = {TUD_HID_REPORT_DESC_CONSUMER()};

const std::uint8_t* tud_hid_descriptor_report_cb(std::uint8_t instance) {
    switch (static_cast<hid::U2Interface>(instance)) {
        case hid::U2Interface::Keyboard:
            return desc_hid_keyboard_report;
        case hid::U2Interface::Mouse:
            return desc_hid_mouse_report;
        case hid::U2Interface::Consumer:
            return desc_hid_consumer_report;
        default:
            return nullptr;
    }
}

// ---------------------------------------------------- configuration

#define DUO_U2_CONFIG_TOTAL_LEN (TUD_CONFIG_DESC_LEN + 3 * TUD_HID_DESC_LEN + TUD_CDC_DESC_LEN)

extern const std::uint8_t desc_configuration[];
const std::uint8_t desc_configuration[] = {
    // 100 mA: the board draws its own power plus whatever the CH375B side
    // needs, and asking for more than is used is how a hub refuses a device.
    TUD_CONFIG_DESCRIPTOR(1, static_cast<std::uint8_t>(hid::U2Interface::Count), 0,
                          DUO_U2_CONFIG_TOTAL_LEN,
                          TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP * 0, 100),

    // Boot keyboard. HID_ITF_PROTOCOL_KEYBOARD is what makes it one.
    TUD_HID_DESCRIPTOR(static_cast<std::uint8_t>(hid::U2Interface::Keyboard), 4,
                       HID_ITF_PROTOCOL_KEYBOARD, sizeof(desc_hid_keyboard_report),
                       hid::kEpKeyboardIn, hid::kKeyboardReportSize,
                       hid::kInputPollIntervalMs),

    // The mouse has five buttons and a pan wheel, which a boot mouse cannot
    // describe, so it is reported-protocol only.
    TUD_HID_DESCRIPTOR(static_cast<std::uint8_t>(hid::U2Interface::Mouse), 5,
                       HID_ITF_PROTOCOL_NONE, sizeof(desc_hid_mouse_report),
                       hid::kEpMouseIn, hid::kMouseReportSize, hid::kInputPollIntervalMs),

    TUD_HID_DESCRIPTOR(static_cast<std::uint8_t>(hid::U2Interface::Consumer), 6,
                       HID_ITF_PROTOCOL_NONE, sizeof(desc_hid_consumer_report),
                       hid::kEpConsumerIn, hid::kConsumerReportSize,
                       hid::kConsumerPollIntervalMs),

    TUD_CDC_DESCRIPTOR(static_cast<std::uint8_t>(hid::U2Interface::CdcControl), 7,
                       hid::kEpCdcNotifyIn, 8, hid::kEpCdcDataOut, hid::kEpCdcDataIn, 64),
};

const std::uint8_t* tud_descriptor_configuration_cb(std::uint8_t index) {
    (void)index;
    return desc_configuration;
}

// ---------------------------------------------------------------- strings

namespace {

// Long enough for the prefix plus the RP2040's 16 hexadecimal chip-ID digits.
char serial_number[32];

const char* const string_table[] = {
    reinterpret_cast<const char*>("\x09\x04"),  // 0: English (United States)
    usb::kManufacturer,                         // 1
    usb::kU2Product,                            // 2
    serial_number,                              // 3
    "Duo Input Keyboard",                       // 4
    "Duo Input Mouse",                          // 5
    "Duo Input Consumer Control",               // 6
    "Duo Input Address Exchange",               // 7
};

std::uint16_t string_descriptor[33];

void build_serial_number() {
    if (serial_number[0] != '\0') {
        return;
    }
    pico_unique_board_id_t board_id;
    pico_get_unique_board_id(&board_id);

    std::size_t written = std::snprintf(serial_number, sizeof(serial_number), "%s",
                                        usb::kU2SerialPrefix);
    for (std::size_t byte = 0; byte < PICO_UNIQUE_BOARD_ID_SIZE_BYTES; ++byte) {
        written += std::snprintf(serial_number + written, sizeof(serial_number) - written,
                                 "%02X", board_id.id[byte]);
    }
}

}  // namespace

const std::uint16_t* tud_descriptor_string_cb(std::uint8_t index, std::uint16_t langid) {
    (void)langid;
    if (index >= sizeof(string_table) / sizeof(string_table[0])) {
        return nullptr;
    }

    std::uint8_t characters = 0;
    if (index == 0) {
        std::memcpy(&string_descriptor[1], string_table[0], 2);
        characters = 1;
    } else {
        if (index == 3) {
            build_serial_number();
        }
        const char* text = string_table[index];
        const std::size_t length = std::strlen(text);
        const std::size_t capacity = sizeof(string_descriptor) / sizeof(string_descriptor[0]) - 1;
        characters = static_cast<std::uint8_t>(length < capacity ? length : capacity);
        for (std::uint8_t position = 0; position < characters; ++position) {
            string_descriptor[1 + position] = static_cast<std::uint16_t>(text[position]);
        }
    }

    string_descriptor[0] = static_cast<std::uint16_t>((TUSB_DESC_STRING << 8) |
                                                      (2 * characters + 2));
    return string_descriptor;
}

}  // extern "C"
