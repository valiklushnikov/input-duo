#pragma once

// Bringing a device up by hand, one control transfer at a time.
//
// The controller offers to do all of this in a single command, AUTO_SETUP, and
// that command cannot finish the job. It gives the device an address without
// saying which - while the host has to be told the same address separately
// (DS2 1.5) - and it never reports which endpoint the reports will arrive on.
// On hardware that showed as a device which enumerated successfully and then
// answered a hundred and twenty polls with nothing whatsoever.
//
// So the steps are taken here, in the order they depend on each other:
//
//   read the device descriptor, while everything is still on address zero
//   give the device an address of its own
//   tell the controller the same address, or it goes on calling the old one
//   read the configuration descriptor, at the new address
//   choose a configuration, which is what makes the endpoints work
//   ask a boot-capable interface to use boot protocol
//
// It is longer than one command, and at the end the address, the endpoint and
// the report format are known rather than assumed.
//
// That last step is the one the controller cannot do at all: it has commands
// for SET_ADDRESS, SET_CONFIGURATION and GET_DESCRIPTOR and for nothing else,
// so SET_PROTOCOL is assembled as a setup packet and issued by hand. Without
// it a device stays in its own report protocol and sends its native report -
// which for the mouse on this bench means a Report ID in front of everything,
// read as the buttons, with the buttons read as dx and dx as dy. A click on
// every movement and a pointer that only goes up and down.
//
// A device is allowed to refuse it, and one that does is still brought up. It
// says what it says in its own protocol; that is a mouse this firmware reads
// badly, and better than no mouse at all.
//
// Nothing here waits without a deadline. A device that stops answering part
// way through ends the attempt, not the loop: U1 services USB, the link to U2
// and a watchdog on the same pass, and none of them can wait for it.

#include <cstdint>

#include "ch375/device.hpp"
#include "ch375/hid_parser.hpp"
#include "ch375/transport.hpp"

namespace duo_input::u1::ch375 {

/// The address given to whatever is attached.
///
/// One device per controller, so one address is enough. Anything but zero
/// would do; zero is where everything starts and only one device can be there.
inline constexpr std::uint8_t kAssignedAddress = 2;

class DescriptorSetup final : public IDeviceSetup {
public:
    explicit DescriptorSetup(Ch375Transport& transport) : transport_(transport) {}

    void begin(std::uint32_t now_us) override;
    SetupProgress poll(std::uint32_t now_us, bool interrupted, InterruptStatus status) override;
    std::uint8_t interrupt_endpoint() const override { return capabilities_.endpoint; }

    DeviceKind kind() const { return capabilities_.kind; }
    std::uint16_t max_packet() const override { return capabilities_.max_packet; }
    /// Does the interface descriptor *advertise* boot support?
    ///
    /// Only that. It says nothing about which protocol the device is actually
    /// in, and reading it as if it did is what put a click on every movement
    /// of the mouse on the bench for weeks.
    bool boot_protocol() const { return capabilities_.boot_protocol; }

    /// Was the device actually put into boot protocol?
    bool boot_protocol_selected() const { return boot_protocol_selected_; }

    /// Why the last attempt ended, for a bring-up build to report.
    std::uint8_t last_status() const { return last_status_; }
    ParseError last_parse_error() const { return last_parse_error_; }
    std::uint16_t attempts() const { return attempts_; }

private:
    enum class Step : std::uint8_t {
        Idle,
        ReadingDeviceDescriptor,
        SettingAddress,
        ReadingConfiguration,
        ChoosingConfiguration,
        /// The SET_PROTOCOL setup packet has gone; its interrupt is awaited.
        RequestingBootProtocol,
        /// The status stage has gone. Only when it lands has the device acted.
        FinishingBootProtocol,
    };

    SetupProgress fail(std::uint8_t status);
    SetupProgress finish(std::uint8_t status);
    /// Ask a boot-capable interface to switch, or finish without asking.
    SetupProgress select_boot_protocol(std::uint32_t now_us);
    /// True while the outcome of the protocol request is still outstanding.
    bool choosing_protocol() const {
        return step_ == Step::RequestingBootProtocol || step_ == Step::FinishingBootProtocol;
    }
    void ask_for_descriptor(DescriptorType type, std::uint32_t now_us);

    Ch375Transport& transport_;
    HidCapabilities capabilities_{};
    Step step_ = Step::Idle;
    std::uint32_t started_us_ = 0;
    std::uint8_t last_status_ = 0;
    ParseError last_parse_error_ = ParseError::None;
    bool boot_protocol_selected_ = false;
    std::uint16_t attempts_ = 0;
};

}  // namespace duo_input::u1::ch375
