#include "ch375/descriptor_setup.hpp"

namespace duo_input::u1::ch375 {
namespace {

/// Reasons an attempt ended, in the space the chip's own statuses leave free.
/// 00-3F belongs to the chip; these sit above it so the two cannot be confused.
constexpr std::uint8_t kEndedRunning = 0xFF;
constexpr std::uint8_t kEndedTimeout = 0xFD;
constexpr std::uint8_t kEndedUnreadable = 0xFC;
constexpr std::uint8_t kEndedUnsupported = 0xFB;

/// A descriptor has to fit the controller's control buffer, which is 64 bytes
/// (DS1 5.13). A longer one arrives cut short, and the parser refuses what
/// does not add up rather than guessing at the rest.
constexpr std::size_t kDescriptorBuffer = kMaxBlockSize;

}  // namespace

void DescriptorSetup::begin(std::uint32_t now_us) {
    // Anything the chip is still holding belongs to the device that was here
    // before. Read as this one's answer it would configure a device that has
    // already gone.
    transport_.drain_pending_status();

    capabilities_ = HidCapabilities{};
    last_parse_error_ = ParseError::None;
    last_status_ = kEndedRunning;
    started_us_ = now_us;
    ++attempts_;

    // Everything starts on address zero, which is where a device answers until
    // it is given one of its own.
    transport_.set_usb_address(0);
    ask_for_descriptor(DescriptorType::Device, now_us);
    step_ = Step::ReadingDeviceDescriptor;
}

void DescriptorSetup::ask_for_descriptor(DescriptorType type, std::uint32_t now_us) {
    started_us_ = now_us;
    transport_.get_descriptor(type);
}

SetupProgress DescriptorSetup::fail(std::uint8_t status) {
    step_ = Step::Idle;
    last_status_ = status;
    return SetupProgress::Failed;
}

SetupProgress DescriptorSetup::poll(std::uint32_t now_us, bool interrupted,
                                    InterruptStatus status) {
    if (step_ == Step::Idle) {
        // Never begun, or already finished. Reporting Busy would leave a
        // caller waiting for something nobody set in motion.
        return SetupProgress::Failed;
    }

    if (!interrupted) {
        // Subtraction, not comparison: the microsecond clock wraps every 71
        // minutes, and a deadline compared directly reads as no wait at all on
        // one side of the wrap and an hour on the other.
        if (now_us - started_us_ >= kSetupTimeoutUs) {
            return fail(kEndedTimeout);
        }
        return SetupProgress::Busy;
    }

    if (status != InterruptStatus::Success) {
        // The byte says which device response caused it - NAK, STALL, or
        // nothing at all - and the caller can report it. None of them makes
        // trying again pointless, so this is a failure, not a fault.
        return fail(static_cast<std::uint8_t>(status));
    }

    switch (step_) {
        case Step::ReadingDeviceDescriptor: {
            std::uint8_t buffer[kDescriptorBuffer];
            std::size_t size = 0;
            if (!transport_.read_block(buffer, sizeof(buffer), size) || size < 8) {
                return fail(kEndedUnreadable);
            }
            // Nothing inside it is needed to go on. What it establishes is
            // that the device answered on address zero, which means it is
            // listening and can be moved somewhere of its own.
            transport_.set_address(kAssignedAddress);
            started_us_ = now_us;
            step_ = Step::SettingAddress;
            return SetupProgress::Busy;
        }

        case Step::SettingAddress:
            // The device has moved. If the controller is not moved with it,
            // every later transfer is addressed to somewhere nobody answers -
            // which looks exactly like a device that is not there (DS2 1.5).
            transport_.set_usb_address(kAssignedAddress);
            ask_for_descriptor(DescriptorType::Configuration, now_us);
            step_ = Step::ReadingConfiguration;
            return SetupProgress::Busy;

        case Step::ReadingConfiguration: {
            std::uint8_t buffer[kDescriptorBuffer];
            std::size_t size = 0;
            if (!transport_.read_block(buffer, sizeof(buffer), size) || size == 0) {
                return fail(kEndedUnreadable);
            }
            last_parse_error_ =
                parse_configuration(protocol::ByteView{buffer, size}, capabilities_);
            if (last_parse_error_ != ParseError::None) {
                // A device this firmware has nothing to route, or a descriptor
                // that does not add up. Saying so is better than configuring
                // it and appearing to work.
                capabilities_ = HidCapabilities{};
                return fail(kEndedUnsupported);
            }
            // Configuration 1. Every device this firmware supports has exactly
            // one, and a device that is addressed but unconfigured has no
            // working endpoints at all.
            transport_.set_configuration(1);
            started_us_ = now_us;
            step_ = Step::ChoosingConfiguration;
            return SetupProgress::Busy;
        }

        case Step::ChoosingConfiguration:
            step_ = Step::Idle;
            last_status_ = static_cast<std::uint8_t>(InterruptStatus::Success);
            return SetupProgress::Done;

        case Step::Idle:
            break;
    }
    return SetupProgress::Failed;
}

}  // namespace duo_input::u1::ch375
