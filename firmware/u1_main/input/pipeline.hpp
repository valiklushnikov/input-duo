#pragma once

// One peripheral, from a raw report to an input event.
//
// A controller says a device attached, was configured, sent this many bytes,
// went away. This turns that into presses and releases, which is all anything
// above here needs to know.
//
// The part worth care is the going away. A device unplugged mid-keystroke is
// holding keys on a computer that will keep holding them: the peripheral is
// gone, so no release will ever arrive from it, and the far machine has no way
// to find out. Somebody yanks a USB cable and their other computer types until
// it is rebooted. So a disconnect synthesises the releases the device did not
// send, which is the only moment anything here invents input.

#include <atomic>
#include <cstddef>
#include <cstdint>

#include "ch375/device.hpp"
#include "ch375/hid_parser.hpp"
#include "ch375/report_descriptor.hpp"
#include "input/events.hpp"
#include "input/keyboard_normalizer.hpp"
#include "input/mouse_normalizer.hpp"

namespace duo_input::u1::input {

/// Where the events go. The Core 1 runtime on hardware.
class IInputHandler {
public:
    virtual ~IInputHandler() = default;
    virtual void on_input(const InputEvent& event, std::uint32_t now_ms) = 0;
};

class InputPipeline {
public:
    explicit InputPipeline(IInputHandler& handler) : handler_(handler) {}

    /// What this device turned out to be, and where it keeps its fields.
    ///
    /// Told once, when the device has been configured. The layout is the one
    /// its report descriptor declared, or boot protocol's for a device that
    /// would not give one up - see ch375/descriptor_setup.hpp. It is ignored
    /// for a keyboard, whose boot report is fixed by the specification.
    void set_kind(ch375::DeviceKind kind, const ch375::MouseReportLayout& mouse_layout);

    /// A report arrived from the device.
    void on_report(protocol::ByteView report, std::uint32_t now_ms);
    void on_auxiliary_report(std::uint8_t endpoint, protocol::ByteView report,
                             std::uint32_t now_ms);

    /// The device went away.
    void on_detached(std::uint32_t now_ms);

    /// Turn one controller event into whatever it means.
    void on_event(const ch375::Ch375Event& event, ch375::DeviceKind kind,
                  const ch375::MouseReportLayout& mouse_layout, std::uint32_t now_ms,
                  std::uint16_t vendor_id = 0, std::uint16_t product_id = 0);

    ch375::DeviceKind kind() const { return kind_; }
    /// How many reports were dropped because the pipeline did not know what
    /// kind of device they came from.
    std::uint32_t unclaimed_reports() const { return unclaimed_; }
#if DUO_CH375_PROBE
    std::uint32_t keychron_side_presses() const {
        return keychron_side_presses_.load(std::memory_order_relaxed);
    }
    std::uint32_t keychron_side_releases() const {
        return keychron_side_releases_.load(std::memory_order_relaxed);
    }
    bool keychron_side_held() const {
        return keychron_side_button_held_.load(std::memory_order_relaxed);
    }
#endif

private:
    void emit(const InputEvent* events, std::size_t count, std::uint32_t now_ms);

    IInputHandler& handler_;
    ch375::DeviceKind kind_ = ch375::DeviceKind::Unknown;
    KeyboardNormalizer keyboard_;
    MouseNormalizer mouse_;
    bool keychron_receiver_ = false;
    std::atomic<bool> keychron_side_button_held_{false};
#if DUO_CH375_PROBE
    std::atomic<std::uint32_t> keychron_side_presses_{0};
    std::atomic<std::uint32_t> keychron_side_releases_{0};
#endif
    std::uint32_t unclaimed_ = 0;
};

}  // namespace duo_input::u1::input
