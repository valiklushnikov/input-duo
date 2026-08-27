#pragma once

// Getting a freshly attached device ready to talk.
//
// The CH375 will do the standard opening moves itself: AUTO_SETUP is
// GET_DESCR, SET_ADDRESS and SET_CONFIGURATION in one command (DS2 1.13).
// That is enough for the ordinary wired keyboard and mouse this device
// promises, and it is deliberately all that happens here.
//
// What it cannot do is say which endpoint the device's reports arrive on,
// because it does not report what it read. Endpoint 1 is assumed, which is
// where nearly every wired keyboard and mouse puts its interrupt IN endpoint -
// and "nearly every" is exactly the kind of assumption the next task in the
// plan removes, by reading the descriptors instead.
//
// Nothing here waits without a deadline. The chip answers by raising an
// interrupt, and one that never comes must end the attempt rather than the
// loop: U1 services USB, the link to U2 and a watchdog on the same pass.

#include <cstdint>

#include "ch375/device.hpp"
#include "ch375/transport.hpp"

namespace duo_input::u1::ch375 {

/// How long to let one attempt at configuring a device run.
///
/// The control transfers inside AUTO_SETUP take single-digit milliseconds at
/// full speed. Long enough not to abandon a slow device, short enough that a
/// port with nothing on it costs a fraction of a second rather than a pause
/// somebody notices.
inline constexpr std::uint32_t kSetupTimeoutUs = 200000;

class AutoSetupEnumerator final : public IDeviceSetup {
public:
    explicit AutoSetupEnumerator(Ch375Transport& transport) : transport_(transport) {}

    void begin(std::uint32_t now_us) override;
    SetupProgress poll(std::uint32_t now_us, bool interrupted, InterruptStatus status) override;
    std::uint8_t interrupt_endpoint() const override { return endpoint_; }

    /// The status byte the last attempt ended on, and how many attempts there
    /// have been.
    ///
    /// Kept rather than reduced to success or failure, because the byte is the
    /// diagnosis: DS1 5.12 puts the device's own response in its low four bits
    /// - NAK, STALL, or a timeout meaning nothing answered at all. Those want
    /// different repairs and look identical from outside.
    std::uint8_t last_status() const { return last_status_; }
    std::uint16_t attempts() const { return attempts_; }

private:
    Ch375Transport& transport_;
    std::uint32_t started_us_ = 0;
    bool running_ = false;
    std::uint8_t endpoint_ = 0;
    std::uint8_t last_status_ = 0;
    std::uint16_t attempts_ = 0;
};

}  // namespace duo_input::u1::ch375
