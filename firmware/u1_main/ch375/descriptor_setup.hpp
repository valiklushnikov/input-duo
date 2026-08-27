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
//
// It is longer than one command, and at the end the address and the endpoint
// are known rather than assumed.
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
    std::uint16_t max_packet() const { return capabilities_.max_packet; }
    bool boot_protocol() const { return capabilities_.boot_protocol; }

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
    };

    SetupProgress fail(std::uint8_t status);
    void ask_for_descriptor(DescriptorType type, std::uint32_t now_us);

    Ch375Transport& transport_;
    HidCapabilities capabilities_{};
    Step step_ = Step::Idle;
    std::uint32_t started_us_ = 0;
    std::uint8_t last_status_ = 0;
    ParseError last_parse_error_ = ParseError::None;
    std::uint16_t attempts_ = 0;
};

}  // namespace duo_input::u1::ch375
