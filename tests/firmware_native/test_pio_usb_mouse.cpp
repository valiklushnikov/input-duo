// A TinyUSB mouse's reports, from a fake host callback to a queued command -
// proving the PIO backend feeds the same InputPipeline the CH375 adapter
// always has, and that the Keychron M3 receiver's side-button quirk
// (input/pipeline.cpp's AuxiliaryReport path, unchanged by this file) gets
// its transport association from this backend without a TinyUSB type ever
// crossing the neutral source boundary.
//
// The mutation this file is really written against: an unrelated composite
// device whose second interface happens to be shaped like a keyboard must
// keep typing real keys, never invent mouse button 4. Only the receiver's
// own vendor/product gates the transport association in device_registry.cpp;
// the report-shape check in pipeline.cpp is what still decides whether a
// given report from that channel means anything, and this file does not
// touch it.

#include "fakes/tinyusb_host.hpp"
#include "input/pipeline.hpp"
#include "pio_usb/backend.hpp"
#include "pio_usb/device_registry.hpp"
#include "test_support.hpp"

#include <cstdint>
#include <vector>

using duo::test::tinyusb_host::kProtocolKeyboard;
using duo::test::tinyusb_host::kProtocolMouse;
using duo::test::tinyusb_host::kProtocolNone;
using duo_input::u1::input::IInputHandler;
using duo_input::u1::input::InputEvent;
using duo_input::u1::input::InputEventKind;
using duo_input::u1::input::InputPipeline;
using duo_input::u1::input::SourceEvent;
using duo_input::u1::input::SourceIdentity;
using duo_input::u1::pio_usb::DeviceRegistry;
using duo_input::u1::pio_usb::LogicalRole;
using duo_input::u1::pio_usb::PioUsbBackend;

namespace {

constexpr std::uint8_t kMouseAddress = 2;
constexpr std::uint8_t kMouseInstance = 0;
constexpr std::uint16_t kVendorId = 0x1234;
constexpr std::uint16_t kProductId = 0x5678;

// Boot mouse reports: buttons, dX, dY, wheel, pan - the shape boot_mouse_layout()
// reads and the only one available before any descriptor is fetched.
constexpr std::uint8_t kMouseButton1Down[] = {0x01, 0x00, 0x00};
constexpr std::uint8_t kMouseNoButtons[] = {0x00, 0x00, 0x00};
constexpr std::uint8_t kAllFiveButtonsDown[] = {0x1F, 0x00, 0x00};
constexpr std::uint8_t kMouseMoveOnly[] = {0x00, 0x0A, 0xEC};       // dx=10, dy=-20
constexpr std::uint8_t kMouseWheelOnly[] = {0x00, 0x00, 0x00, 0x02};
constexpr std::uint8_t kMousePanOnly[] = {0x00, 0x00, 0x00, 0x00, 0x01};
constexpr std::uint8_t kMoveAndWheelTogether[] = {0x00, 0x0A, 0xEC, 0x02};

/// A mouse that leads every report with a Report ID: identifier, one button
/// byte, sixteen-bit X and Y, one wheel byte. Reused verbatim from
/// test_report_descriptor.cpp's report_id_wheel_mouse() - the shape
/// classify_hid's descriptor path parses into a MouseReportLayout with every
/// field sitting behind the identifier.
std::vector<std::uint8_t> report_id_wheel_mouse_descriptor() {
    return {
        0x05, 0x01,        // Usage Page (Generic Desktop)
        0x09, 0x02,        // Usage (Mouse)
        0xA1, 0x01,        // Collection (Application)
        0x85, 0x01,        //   Report ID (1)
        0x09, 0x01,        //   Usage (Pointer)
        0xA1, 0x00,        //   Collection (Physical)
        0x05, 0x09,        //     Usage Page (Button)
        0x19, 0x01,        //     Usage Minimum (Button 1)
        0x29, 0x05,        //     Usage Maximum (Button 5)
        0x15, 0x00,        //     Logical Minimum (0)
        0x25, 0x01,        //     Logical Maximum (1)
        0x75, 0x01,        //     Report Size (1)
        0x95, 0x05,        //     Report Count (5)
        0x81, 0x02,        //     Input (Data,Var,Abs)
        0x75, 0x03,        //     Report Size (3)
        0x95, 0x01,        //     Report Count (1)
        0x81, 0x01,        //     Input (Cnst) - padding to a byte
        0x05, 0x01,        //     Usage Page (Generic Desktop)
        0x09, 0x30,        //     Usage (X)
        0x09, 0x31,        //     Usage (Y)
        0x16, 0x01, 0xF8,  //     Logical Minimum (-2047)
        0x26, 0xFF, 0x07,  //     Logical Maximum (2047)
        0x75, 0x10,        //     Report Size (16)
        0x95, 0x02,        //     Report Count (2)
        0x81, 0x06,        //     Input (Data,Var,Rel)
        0x09, 0x38,        //     Usage (Wheel)
        0x15, 0x81,        //     Logical Minimum (-127)
        0x25, 0x7F,        //     Logical Maximum (127)
        0x75, 0x08,        //     Report Size (8)
        0x95, 0x01,        //     Report Count (1)
        0x81, 0x06,        //     Input (Data,Var,Rel)
        0xC0,              //   End Collection
        0xC0,              // End Collection
    };
}

/// Report ID 1: the middle button (index 2) down, X = -10 and Y = +79.
/// Reused verbatim from test_normalizers.cpp's
/// buttons_and_axes_still_land_when_the_layout_has_a_report_id. Read at boot
/// offsets, byte 0 - the identifier, 0x01 - would be button index 0 down
/// instead: the assertion below is on which one actually lands.
constexpr std::uint8_t kReportIdButtonAndMove[] = {0x01, 0x04, 0xF6, 0xFF, 0x4F, 0x00, 0x00};

/// Captured byte-for-byte from the attached mouse
/// (test_report_descriptor.cpp's
/// the_bench_mouses_real_packed_twelve_bit_descriptor_is_accepted). Report ID
/// 1, buttons, two packed signed 12-bit axes, wheel and AC Pan.
std::vector<std::uint8_t> packed_twelve_bit_mouse_descriptor() {
    return {
        0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x85, 0x01, 0x09, 0x01,
        0xA1, 0x00, 0x05, 0x09, 0x19, 0x01, 0x29, 0x05, 0x15, 0x00,
        0x25, 0x01, 0x95, 0x05, 0x75, 0x01, 0x81, 0x02, 0x95, 0x01,
        0x75, 0x03, 0x81, 0x03, 0x05, 0x01, 0x16, 0x01, 0xF8, 0x26,
        0xFF, 0x07, 0x75, 0x0C, 0x95, 0x02, 0x09, 0x30, 0x09, 0x31,
        0x81, 0x06, 0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x01,
        0x09, 0x38, 0x81, 0x06, 0xC0, 0x05, 0x0C, 0x0A, 0x38, 0x02,
        0x95, 0x01, 0x81, 0x06, 0xC0,
    };
}

/// Body bits: X=0x123, Y=-0x123 (0xEDD as signed 12-bit), then wheel -1 and
/// pan +1. Reused verbatim from test_normalizers.cpp's
/// the_bench_mouses_packed_twelve_bit_axes_leave_the_wheel_at_its_declared_byte.
constexpr std::uint8_t kPackedTwelveBitReport[] = {0x01, 0x00, 0x23, 0xD1, 0xED, 0xFF, 0x01};

// Captured from Keychron M3 receiver 3434:D030, interface 2 / endpoint 1.
// Reused verbatim from test_input_pipeline.cpp - the brief requires these
// exact traces, not new ones.
constexpr std::uint16_t kKeychronVendorId = 0x3434;
constexpr std::uint16_t kKeychronProductId = 0xD030;
constexpr std::uint8_t kKeychronSidePress[] = {0x01, 0x01, 0x00, 0x4F,
                                               0x00, 0x00, 0x00, 0x00, 0x03};
constexpr std::uint8_t kKeychronSideRelease[] = {0x01, 0x00, 0x00, 0x00,
                                                 0x00, 0x00, 0x00, 0x00, 0x03};

/// A boot keyboard report: modifiers, reserved, six usage slots.
constexpr std::uint8_t kKeyA[] = {0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00};
constexpr std::uint8_t kNoKeys[] = {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00};

struct Recorder final : IInputHandler {
    std::vector<InputEvent> events;

    void on_input(const InputEvent& event, std::uint32_t) override { events.push_back(event); }

    int count(InputEventKind kind) const {
        int seen = 0;
        for (const InputEvent& event : events) {
            if (event.kind == kind) {
                ++seen;
            }
        }
        return seen;
    }

    int of(InputEventKind kind, std::uint16_t code) const {
        int seen = 0;
        for (const InputEvent& event : events) {
            if (event.kind == kind && event.code == code) {
                ++seen;
            }
        }
        return seen;
    }

    const InputEvent* first(InputEventKind kind) const {
        for (const InputEvent& event : events) {
            if (event.kind == kind) {
                return &event;
            }
        }
        return nullptr;
    }
};

/// A single mouse behind PioUsbBackend, plus the real InputPipeline and
/// Recorder every drained event is fed through - the same shape main.cpp's
/// Core 1 loop drives it in.
struct MouseRig {
    PioUsbBackend backend;
    Recorder recorder;
    InputPipeline pipeline{recorder};
    std::uint32_t now_us = 1000;

    MouseRig() {
        duo::test::tinyusb_host::reset();
        backend.begin();
    }

    void mount(const std::uint8_t* descriptor, std::size_t descriptor_size,
              std::uint8_t protocol = kProtocolNone) {
        duo::test::tinyusb_host::add_device(kMouseAddress, kVendorId, kProductId);
        tuh_mount_cb(kMouseAddress);
        duo::test::tinyusb_host::set_protocol(kMouseAddress, kMouseInstance, protocol);
        tuh_hid_mount_cb(kMouseAddress, kMouseInstance, descriptor,
                        static_cast<std::uint16_t>(descriptor_size));
        pump();
    }

    void mount_boot_mouse() { mount(nullptr, 0, kProtocolMouse); }

    void report(const std::uint8_t* bytes, std::size_t size, std::uint32_t captured_us = 0) {
        duo::test::tinyusb_host::set_now_us(captured_us);
        tuh_hid_report_received_cb(kMouseAddress, kMouseInstance, bytes,
                                   static_cast<std::uint16_t>(size));
        pump();
    }

    void pump() {
        now_us += 1000;
        backend.task(now_us);
        SourceEvent event;
        SourceIdentity identity;
        while (backend.take_event(event, identity)) {
            pipeline.on_event(event, identity, now_us / 1000);
        }
    }
};

/// One physical composite device behind PioUsbBackend: a mouse-shaped
/// primary interface (instance 0) and a keyboard-shaped second interface
/// (instance 1) - the exact shape of the Keychron M3 receiver, whose
/// vendor/product decides whether that second interface is granted the
/// Keyboard role or associated with the mouse's own channel instead. Both
/// pipelines are wired so either routing is observable.
struct CompositeMouseRig {
    PioUsbBackend backend;
    Recorder keyboard_recorder;
    Recorder mouse_recorder;
    InputPipeline keyboard_pipeline{keyboard_recorder};
    InputPipeline mouse_pipeline{mouse_recorder};
    std::uint32_t now_us = 1000;
    std::uint8_t dev_addr;

    CompositeMouseRig(std::uint8_t address, std::uint16_t vendor_id, std::uint16_t product_id)
        : dev_addr(address) {
        duo::test::tinyusb_host::reset();
        backend.begin();

        duo::test::tinyusb_host::add_device(dev_addr, vendor_id, product_id);
        tuh_mount_cb(dev_addr);
        duo::test::tinyusb_host::set_protocol(dev_addr, 0, kProtocolMouse);
        tuh_hid_mount_cb(dev_addr, 0, nullptr, 0);
        duo::test::tinyusb_host::set_protocol(dev_addr, 1, kProtocolKeyboard);
        tuh_hid_mount_cb(dev_addr, 1, nullptr, 0);

        pump();
    }

    ~CompositeMouseRig() { duo_input::u1::pio_usb::set_callback_registry(nullptr); }

    void deliver_mouse(const std::uint8_t* bytes, std::size_t size, std::uint32_t captured_us) {
        duo::test::tinyusb_host::set_now_us(captured_us);
        tuh_hid_report_received_cb(dev_addr, 0, bytes, static_cast<std::uint16_t>(size));
    }

    void deliver_auxiliary(const std::uint8_t* bytes, std::size_t size,
                           std::uint32_t captured_us) {
        duo::test::tinyusb_host::set_now_us(captured_us);
        tuh_hid_report_received_cb(dev_addr, 1, bytes, static_cast<std::uint16_t>(size));
    }

    void detach() { tuh_umount_cb(dev_addr); }

    void task() {
        now_us += 1000;
        backend.task(now_us);
    }

    void drain() {
        SourceEvent event;
        SourceIdentity identity;
        while (backend.take_event(event, identity)) {
            switch (PioUsbBackend::logical_port(identity.kind)) {
                case 0:
                    keyboard_pipeline.on_event(event, identity, now_us / 1000);
                    break;
                case 1:
                    mouse_pipeline.on_event(event, identity, now_us / 1000);
                    break;
                default:
                    break;
            }
        }
    }

    void pump() {
        task();
        drain();
    }
};

constexpr std::uint8_t kSeparateKeyboardAddress = 4;
constexpr std::uint16_t kSeparateKeyboardVendorId = 0x1111;
constexpr std::uint16_t kSeparateKeyboardProductId = 0x2222;
constexpr std::uint8_t kKeychronAddress = 5;

/// A genuine keyboard plus the Keychron receiver, together - the three
/// independently-arming role-bearing interfaces V1's queue reservation must
/// each be able to release: Keyboard, Mouse and the receiver's Auxiliary
/// channel.
struct ThreeSourceRig {
    PioUsbBackend backend;
    Recorder keyboard_recorder;
    Recorder mouse_recorder;
    InputPipeline keyboard_pipeline{keyboard_recorder};
    InputPipeline mouse_pipeline{mouse_recorder};
    std::uint32_t now_us = 1000;

    ThreeSourceRig() {
        duo::test::tinyusb_host::reset();
        backend.begin();

        duo::test::tinyusb_host::add_device(kSeparateKeyboardAddress, kSeparateKeyboardVendorId,
                                            kSeparateKeyboardProductId);
        tuh_mount_cb(kSeparateKeyboardAddress);
        duo::test::tinyusb_host::set_protocol(kSeparateKeyboardAddress, 0, kProtocolKeyboard);
        tuh_hid_mount_cb(kSeparateKeyboardAddress, 0, nullptr, 0);

        duo::test::tinyusb_host::add_device(kKeychronAddress, kKeychronVendorId,
                                            kKeychronProductId);
        tuh_mount_cb(kKeychronAddress);
        duo::test::tinyusb_host::set_protocol(kKeychronAddress, 0, kProtocolMouse);
        tuh_hid_mount_cb(kKeychronAddress, 0, nullptr, 0);
        duo::test::tinyusb_host::set_protocol(kKeychronAddress, 1, kProtocolKeyboard);
        tuh_hid_mount_cb(kKeychronAddress, 1, nullptr, 0);

        pump();
    }

    ~ThreeSourceRig() { duo_input::u1::pio_usb::set_callback_registry(nullptr); }

    void deliver_keyboard(const std::uint8_t* bytes, std::size_t size, std::uint32_t us) {
        duo::test::tinyusb_host::set_now_us(us);
        tuh_hid_report_received_cb(kSeparateKeyboardAddress, 0, bytes,
                                   static_cast<std::uint16_t>(size));
    }
    void deliver_mouse(const std::uint8_t* bytes, std::size_t size, std::uint32_t us) {
        duo::test::tinyusb_host::set_now_us(us);
        tuh_hid_report_received_cb(kKeychronAddress, 0, bytes, static_cast<std::uint16_t>(size));
    }
    void deliver_auxiliary(const std::uint8_t* bytes, std::size_t size, std::uint32_t us) {
        duo::test::tinyusb_host::set_now_us(us);
        tuh_hid_report_received_cb(kKeychronAddress, 1, bytes, static_cast<std::uint16_t>(size));
    }

    void task() {
        now_us += 1000;
        backend.task(now_us);
    }

    void drain() {
        SourceEvent event;
        SourceIdentity identity;
        while (backend.take_event(event, identity)) {
            switch (PioUsbBackend::logical_port(identity.kind)) {
                case 0:
                    keyboard_pipeline.on_event(event, identity, now_us / 1000);
                    break;
                case 1:
                    mouse_pipeline.on_event(event, identity, now_us / 1000);
                    break;
                default:
                    break;
            }
        }
    }

    void pump() {
        task();
        drain();
    }
};

constexpr std::uint8_t kCompetingMouseAddress = 6;
constexpr std::uint16_t kCompetingMouseVendorId = 0xAAAA;
constexpr std::uint16_t kCompetingMouseProductId = 0xBBBB;
constexpr std::uint8_t kRealKeyboardAddress = 7;
constexpr std::uint16_t kRealKeyboardVendorId = 0xCCCC;
constexpr std::uint16_t kRealKeyboardProductId = 0xDDDD;

/// A generic boot-shaped keyboard report: Report ID 1 is an unrelated
/// four-byte consumer collection, Report ID 2 is eight modifier bits, a
/// reserved byte and six one-byte usage slots - bytes reused verbatim from
/// test_pio_usb_hid_setup.cpp's report_id_keyboard(), already proven there to
/// classify as DeviceKind::Keyboard with report_id_value 2. This is NOT a
/// capture of the Keychron receiver's actual interface 2 - that descriptor
/// is not present anywhere in this repository; see the fix report for why.
std::vector<std::uint8_t> generic_keyboard_descriptor() {
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

/// generic_keyboard_descriptor()'s bytes with a mouse-shaped report (reused
/// verbatim from test_pio_usb_hid_setup.cpp's five_button_wheel_mouse())
/// appended. classify_report_descriptor finds both a keyboard report and a
/// mouse report in one descriptor and refuses it as
/// ReportDescriptorRole::Ambiguous - the shape a device whose real
/// descriptor this firmware cannot read cleanly would present. Not a claim
/// about what the Keychron receiver's actual interface 2 contains.
std::vector<std::uint8_t> ambiguous_descriptor() {
    auto bytes = generic_keyboard_descriptor();
    const std::vector<std::uint8_t> mouse = {
        0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x09, 0x01, 0xA1, 0x00,
        0x05, 0x09, 0x19, 0x01, 0x29, 0x05, 0x15, 0x00, 0x25, 0x01,
        0x95, 0x05, 0x75, 0x01, 0x81, 0x02,
        0x95, 0x01, 0x75, 0x03, 0x81, 0x03,
        0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 0x09, 0x38,
        0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x03, 0x81, 0x06,
        0xC0, 0xC0,
    };
    bytes.insert(bytes.end(), mouse.begin(), mouse.end());
    return bytes;
}

/// Direct DeviceRegistry access, no PioUsbBackend in between - needed because
/// the tests below assert on which LogicalRole an interface actually won,
/// not only on what a pipeline did with its reports. Otherwise the same
/// shape as every other rig in this file: two pipelines, drained through
/// PioUsbBackend::logical_port the same way main.cpp's Core 1 loop does.
struct RegistryTopologyRig {
    DeviceRegistry registry;
    Recorder keyboard_recorder;
    Recorder mouse_recorder;
    InputPipeline keyboard_pipeline{keyboard_recorder};
    InputPipeline mouse_pipeline{mouse_recorder};

    RegistryTopologyRig() {
        duo::test::tinyusb_host::reset();
        duo_input::u1::pio_usb::set_callback_registry(&registry);
    }

    ~RegistryTopologyRig() { duo_input::u1::pio_usb::set_callback_registry(nullptr); }

    /// Mount one HID interface and drain whatever it produced - a Ready, if
    /// it won a role - into the pipeline that role belongs to.
    void mount(std::uint8_t dev_addr, std::uint16_t vendor_id, std::uint16_t product_id,
              std::uint8_t instance, std::uint8_t protocol,
              const std::uint8_t* descriptor = nullptr, std::size_t descriptor_size = 0) {
        duo::test::tinyusb_host::add_device(dev_addr, vendor_id, product_id);
        duo::test::tinyusb_host::set_protocol(dev_addr, instance, protocol);
        tuh_hid_mount_cb(dev_addr, instance, descriptor,
                        static_cast<std::uint16_t>(descriptor_size));
        registry.process_pending(0);
        drain();
    }

    void deliver(std::uint8_t dev_addr, std::uint8_t instance, const std::uint8_t* bytes,
                std::size_t size, std::uint32_t captured_us) {
        duo::test::tinyusb_host::set_now_us(captured_us);
        tuh_hid_report_received_cb(dev_addr, instance, bytes, static_cast<std::uint16_t>(size));
        registry.process_pending(0);
        drain();
    }

    void drain() {
        SourceEvent event;
        SourceIdentity identity;
        while (registry.take_event(event, identity)) {
            switch (PioUsbBackend::logical_port(identity.kind)) {
                case 0:
                    keyboard_pipeline.on_event(event, identity, 1);
                    break;
                case 1:
                    mouse_pipeline.on_event(event, identity, 1);
                    break;
                default:
                    break;
            }
        }
    }
};

}  // namespace

TEST_CASE(a_boot_mouse_button_press_and_release_become_mousebuttondown_and_up) {
    MouseRig rig;
    rig.mount_boot_mouse();

    rig.report(kMouseButton1Down, sizeof(kMouseButton1Down));
    CHECK_EQ(rig.recorder.of(InputEventKind::MouseButtonDown, 0), 1);

    rig.report(kMouseNoButtons, sizeof(kMouseNoButtons));
    CHECK_EQ(rig.recorder.of(InputEventKind::MouseButtonUp, 0), 1);
}

TEST_CASE(mouse_movement_is_carried_as_dx_dy) {
    MouseRig rig;
    rig.mount_boot_mouse();

    rig.report(kMouseMoveOnly, sizeof(kMouseMoveOnly));
    const InputEvent* move = rig.recorder.first(InputEventKind::MouseMove);
    CHECK(move != nullptr);
    if (move != nullptr) {
        CHECK_EQ(move->x, static_cast<std::int16_t>(10));
        CHECK_EQ(move->y, static_cast<std::int16_t>(-20));
    }
    CHECK_EQ(rig.recorder.count(InputEventKind::MouseButtonDown), 0);
}

TEST_CASE(a_vertical_wheel_notch_is_its_own_event) {
    MouseRig rig;
    rig.mount_boot_mouse();

    rig.report(kMouseWheelOnly, sizeof(kMouseWheelOnly));
    const InputEvent* wheel = rig.recorder.first(InputEventKind::Wheel);
    CHECK(wheel != nullptr);
    if (wheel != nullptr) {
        CHECK_EQ(wheel->wheel, static_cast<std::int8_t>(2));
        CHECK_EQ(wheel->pan, static_cast<std::int8_t>(0));
    }
    CHECK_EQ(rig.recorder.count(InputEventKind::MouseMove), 0);
}

TEST_CASE(a_horizontal_pan_notch_is_its_own_event) {
    MouseRig rig;
    rig.mount_boot_mouse();

    rig.report(kMousePanOnly, sizeof(kMousePanOnly));
    const InputEvent* wheel = rig.recorder.first(InputEventKind::Wheel);
    CHECK(wheel != nullptr);
    if (wheel != nullptr) {
        CHECK_EQ(wheel->wheel, static_cast<std::int8_t>(0));
        CHECK_EQ(wheel->pan, static_cast<std::int8_t>(1));
    }
}

TEST_CASE(all_five_buttons_are_carried) {
    MouseRig rig;
    rig.mount_boot_mouse();

    rig.report(kAllFiveButtonsDown, sizeof(kAllFiveButtonsDown));
    for (std::uint16_t code = 0; code < 5; ++code) {
        CHECK_EQ(rig.recorder.of(InputEventKind::MouseButtonDown, code), 1);
    }
    CHECK_EQ(rig.recorder.count(InputEventKind::MouseButtonDown), 5);

    rig.report(kMouseNoButtons, sizeof(kMouseNoButtons));
    CHECK_EQ(rig.recorder.count(InputEventKind::MouseButtonUp), 5);
}

TEST_CASE(movement_and_a_wheel_notch_from_one_report_both_land) {
    MouseRig rig;
    rig.mount_boot_mouse();

    rig.report(kMoveAndWheelTogether, sizeof(kMoveAndWheelTogether));
    const InputEvent* move = rig.recorder.first(InputEventKind::MouseMove);
    const InputEvent* wheel = rig.recorder.first(InputEventKind::Wheel);
    CHECK(move != nullptr);
    CHECK(wheel != nullptr);
    if (move != nullptr) {
        CHECK_EQ(move->x, static_cast<std::int16_t>(10));
        CHECK_EQ(move->y, static_cast<std::int16_t>(-20));
    }
    if (wheel != nullptr) {
        CHECK_EQ(wheel->wheel, static_cast<std::int8_t>(2));
    }
    CHECK_EQ(rig.recorder.count(InputEventKind::MouseButtonDown), 0);
}

TEST_CASE(a_report_id_mouse_is_read_through_its_own_descriptor_layout_not_boot_offsets) {
    MouseRig rig;
    const auto descriptor = report_id_wheel_mouse_descriptor();
    rig.mount(descriptor.data(), descriptor.size(), kProtocolNone);

    rig.report(kReportIdButtonAndMove, sizeof(kReportIdButtonAndMove));

    // Button index 2, not index 0 - the boot-offset misreading of the
    // identifier byte itself (0x01) that a layout precedence bug would
    // produce.
    CHECK_EQ(rig.recorder.of(InputEventKind::MouseButtonDown, 2), 1);
    CHECK_EQ(rig.recorder.of(InputEventKind::MouseButtonDown, 0), 0);
    CHECK_EQ(rig.recorder.count(InputEventKind::MouseButtonDown), 1);

    const InputEvent* move = rig.recorder.first(InputEventKind::MouseMove);
    CHECK(move != nullptr);
    if (move != nullptr) {
        CHECK_EQ(move->x, static_cast<std::int16_t>(-10));
        CHECK_EQ(move->y, static_cast<std::int16_t>(79));
    }
}

TEST_CASE(packed_twelve_bit_axes_round_trip_through_the_pio_backend) {
    MouseRig rig;
    const auto descriptor = packed_twelve_bit_mouse_descriptor();
    rig.mount(descriptor.data(), descriptor.size(), kProtocolNone);

    rig.report(kPackedTwelveBitReport, sizeof(kPackedTwelveBitReport));

    const InputEvent* move = rig.recorder.first(InputEventKind::MouseMove);
    const InputEvent* wheel = rig.recorder.first(InputEventKind::Wheel);
    CHECK(move != nullptr);
    CHECK(wheel != nullptr);
    if (move != nullptr) {
        CHECK_EQ(move->x, static_cast<std::int16_t>(0x123));
        CHECK_EQ(move->y, static_cast<std::int16_t>(-0x123));
    }
    if (wheel != nullptr) {
        CHECK_EQ(wheel->wheel, static_cast<std::int8_t>(-1));
        CHECK_EQ(wheel->pan, static_cast<std::int8_t>(1));
    }
}
