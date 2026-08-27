#include "ch375/enumerator.hpp"

namespace duo_input::u1::ch375 {
namespace {

/// Where nearly every wired keyboard and mouse puts its interrupt IN endpoint.
constexpr std::uint8_t kAssumedInterruptEndpoint = 1;

}  // namespace

void AutoSetupEnumerator::begin(std::uint32_t now_us) {
    // Anything still queued belongs to the device that was here before. Read
    // as this one's answer it would configure a device that is already gone.
    transport_.drain_pending_status();

    transport_.auto_setup();
    started_us_ = now_us;
    running_ = true;
    endpoint_ = 0;
    ++attempts_;
    // 0xFF is not a status the chip can produce, so it stands for "this
    // attempt has not ended yet" without needing a second flag.
    last_status_ = 0xFF;
}

SetupProgress AutoSetupEnumerator::poll(std::uint32_t now_us, bool interrupted,
                                        InterruptStatus status) {
    if (!running_) {
        // Never started, or already finished. Reporting Busy here would leave
        // a caller waiting for something nobody set in motion.
        return SetupProgress::Failed;
    }

    if (interrupted) {
        running_ = false;
        last_status_ = static_cast<std::uint8_t>(status);
        if (status != InterruptStatus::Success) {
            // The byte says why - which device response caused it - and the
            // caller can count it. What it does not say is anything that
            // makes trying again pointless, so this is a failure, not a fault.
            return SetupProgress::Failed;
        }
        endpoint_ = kAssumedInterruptEndpoint;
        return SetupProgress::Done;
    }

    // Subtraction, not comparison: the microsecond clock wraps every 71
    // minutes, and a deadline compared directly reads as no wait at all on one
    // side of the wrap and an hour on the other.
    if (now_us - started_us_ >= kSetupTimeoutUs) {
        running_ = false;
        // The chip never raised an interrupt. 0xFD marks that apart from a
        // failure it did report.
        last_status_ = 0xFD;
        return SetupProgress::Failed;
    }
    return SetupProgress::Busy;
}

}  // namespace duo_input::u1::ch375
