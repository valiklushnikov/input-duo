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

#include <cstddef>
#include <cstdint>

#include "input/events.hpp"
#include "input/keyboard_normalizer.hpp"
#include "input/mouse_normalizer.hpp"
#include "input/source.hpp"
#include "protocol/bytes.hpp"

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
    /// Told once, when the source has been configured. Each layout is the one
    /// that source's report descriptor declared, or boot protocol's for a
    /// device that would not give one up.
    ///
    /// Both go in on every Ready, whichever kind it is. A pipeline is reused
    /// across devices, and a layout left behind from the last one is read into
    /// the next: a keyboard whose reports are all dropped because they do not
    /// carry an identifier the keyboard before it used.
    void set_kind(DeviceKind kind, const hid::KeyboardReportLayout& keyboard_layout,
                  const hid::MouseReportLayout& mouse_layout);

    /// A report arrived from the device.
    void on_report(protocol::ByteView report, std::uint32_t now_ms);

    /// The device went away.
    void on_detached(std::uint32_t now_ms);

    /// Turn one source event into whatever it means. Backend-neutral: a
    /// SourceEvent and SourceIdentity carry everything the pipeline reads,
    /// whichever controller produced them.
    void on_event(const SourceEvent& event, const SourceIdentity& identity,
                  std::uint32_t now_ms);

    DeviceKind kind() const { return kind_; }
    /// How many reports were dropped because the pipeline did not know what
    /// kind of device they came from.
    std::uint32_t unclaimed_reports() const { return unclaimed_; }
#if DUO_CH375_PROBE
    /// What the keyboard normalizer made of the reports it was handed.
    const KeyboardNormalizer& keyboard_normalizer() const { return keyboard_; }
#endif

private:
    void emit(const InputEvent* events, std::size_t count, std::uint32_t now_ms);

    IInputHandler& handler_;
    DeviceKind kind_ = DeviceKind::Unknown;
    KeyboardNormalizer keyboard_;
    MouseNormalizer mouse_;
    std::uint32_t unclaimed_ = 0;
};

}  // namespace duo_input::u1::input
