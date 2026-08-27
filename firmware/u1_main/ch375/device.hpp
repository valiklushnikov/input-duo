#pragma once

// One CH375 and whatever is plugged into it.
//
// The chip is asynchronous and this firmware is not allowed to wait for it.
// Every step therefore happens across ticks: one call does a bounded piece of
// work and returns, so a keyboard whose controller has stopped answering costs
// that one device its input and nothing else. U1 still has to service USB, the
// link to U2 and the watchdog on the same loop, and a device that could hold
// the loop would take all three down with it.
//
// Two of these run side by side, one per CH375. They share no state at all,
// which is the point: the mouse dying must be invisible to the keyboard.
//
// Failure is expected rather than exceptional. Controllers answer nonsense,
// stop answering, and come back; devices are unplugged mid-transaction. All of
// that lands in RecoverWait, which counts down and tries again, and none of it
// is allowed to end with keys held down on the far side.

#include <cstddef>
#include <cstdint>

#include "ch375/commands.hpp"
#include "ch375/transport.hpp"

namespace duo_input::u1::ch375 {

enum class Ch375State : std::uint8_t {
    /// Nothing is plugged in, and the chip is watching for something to be.
    Absent,
    /// The USB bus is being held in reset.
    Resetting,
    /// The chip is in host mode and generating frames.
    HostMode,
    /// The device is being configured.
    Enumerating,
    /// Configured; its reports are being read.
    Ready,
    /// Something failed. Counting down to another attempt.
    RecoverWait,
    /// Retrying has stopped being worth it.
    Fault,
};

enum class Ch375EventKind : std::uint8_t {
    None,
    /// A device was plugged in. It is not usable yet.
    Attached,
    /// A device went away. Whatever it was holding must be released.
    Detached,
    /// A device is configured and its reports are coming.
    Ready,
    /// This controller has given up.
    Fault,
    /// A report arrived from the device.
    Report,
};

struct Ch375Event {
    Ch375EventKind kind = Ch375EventKind::None;
    std::uint8_t report[kMaxBlockSize] = {};
    std::size_t report_size = 0;
};

enum class SetupProgress : std::uint8_t {
    /// Still working. Call again next tick.
    Busy,
    /// The device is configured.
    Done,
    /// It cannot be configured. Recover and try again.
    Failed,
};

/// Configuring a freshly attached device.
///
/// Separated from the lifecycle because the two change for different reasons:
/// the lifecycle is about a controller and a cable, and this is about USB
/// descriptors. Task 3 of the plan implements the real one.
class IDeviceSetup {
public:
    virtual ~IDeviceSetup() = default;

    /// Start over on a device that has just been reset.
    virtual void begin(std::uint32_t now_us) = 0;

    /// Do a bounded piece of the work.
    virtual SetupProgress poll(std::uint32_t now_us) = 0;

    /// Which endpoint the device's reports arrive on. Valid after Done.
    virtual std::uint8_t interrupt_endpoint() const = 0;
};

/// How long the USB bus is held in reset before the device is configured.
///
/// DS1 section 5.9 says to enter mode 7 after a device is plugged in and then
/// switch to mode 6. USB requires the reset to last at least 10 ms; this is
/// comfortably past that and still imperceptible.
inline constexpr std::uint32_t kBusResetHoldUs = 20000;

/// How long to wait after a failure before trying the whole sequence again.
///
/// Retrying flat out would hammer a controller that is already unhappy and
/// fill the loop with work that cannot succeed, while a longer wait would
/// leave someone staring at a keyboard that does nothing.
inline constexpr std::uint32_t kRecoverDelayUs = 1000000;

/// How often a configured device is asked whether it has anything to say.
///
/// A USB interrupt endpoint on a keyboard is polled about every 8 ms by a real
/// host, and faster gains nothing a person can feel.
inline constexpr std::uint32_t kReportPollUs = 8000;

/// How long a working device may go without its controller answering.
///
/// A controller that stops answering while a device is up is the dangerous
/// case: nothing can be read from the device any more, and something on the
/// far side may be holding a key down with no way to learn otherwise. After
/// this long the device is declared gone and released, which is the same rule
/// U2 keeps about the link to U1 and for the same reason.
///
/// Long enough not to trip on jitter, short enough that nobody finishes a word
/// in it.
inline constexpr std::uint32_t kDeviceLostUs = 250000;

/// How often to ask whether something has been plugged in.
///
/// The chip announces arrivals by itself, so this is a backstop rather than
/// the main route: it catches a device that was already attached when the
/// power came on, and one whose announcement was lost while the chip was
/// unwell. A tenth of a second is imperceptible to whoever just plugged a
/// keyboard in.
inline constexpr std::uint32_t kConnectPollUs = 100000;

/// How many events can be held before the oldest is dropped.
inline constexpr std::size_t kEventQueueDepth = 8;

class Ch375Device {
public:
    Ch375Device(Ch375Transport& transport, IDeviceSetup& setup)
        : transport_(transport), setup_(setup) {}

    /// Do a bounded piece of work.
    void tick(std::uint32_t now_us);

    Ch375State state() const { return state_; }

    /// Take the oldest event, if there is one.
    bool take_event(Ch375Event& event);

    /// Configure the attached device again from scratch.
    ///
    /// Does nothing when there is no device: there would be nothing to
    /// configure, and pretending otherwise would reset a bus with nothing on
    /// it every time the configurator asked.
    void request_reenumeration();

private:
    void enter(Ch375State state, std::uint32_t now_us);
    void publish(Ch375EventKind kind);
    void publish_report(const std::uint8_t* data, std::size_t size);
    void fail(std::uint32_t now_us);
    void handle_detach(std::uint32_t now_us);

    /// Read the chip's interrupt status if it is asking. False if it is not.
    bool poll_interrupt(InterruptStatus& status);

    Ch375Transport& transport_;
    IDeviceSetup& setup_;

    Ch375State state_ = Ch375State::Absent;
    bool chip_ready_ = false;
    bool announced_ready_ = false;
    std::uint32_t entered_us_ = 0;
    std::uint32_t last_poll_us_ = 0;
    std::uint32_t last_connect_poll_us_ = 0;
    std::uint32_t last_answer_us_ = 0;
    std::uint8_t endpoint_ = 0;

    Ch375Event events_[kEventQueueDepth];
    std::size_t head_ = 0;
    std::size_t count_ = 0;
};

}  // namespace duo_input::u1::ch375
