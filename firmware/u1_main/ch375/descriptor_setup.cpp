#include "ch375/descriptor_setup.hpp"

namespace duo_input::u1::ch375 {
namespace {

/// Reasons an attempt ended, in the space the chip's own statuses leave free.
/// 00-3F belongs to the chip; these sit above it so the two cannot be confused.
constexpr std::uint8_t kEndedRunning = 0xFF;
constexpr std::uint8_t kEndedTimeout = 0xFD;
constexpr std::uint8_t kEndedUnreadable = 0xFC;
constexpr std::uint8_t kEndedUnsupported = 0xFB;
/// The device answered the protocol request with something other than success
/// - a STALL, usually, which is how a device says it does not do that.
constexpr std::uint8_t kEndedProtocolRefused = 0xFA;
/// Nothing came back from the protocol request at all.
constexpr std::uint8_t kEndedProtocolSilent = 0xF9;

/// HID 1.11 section 7.2.5. The controller has no command for this one.
constexpr std::uint8_t kRequestSetProtocol = 0x0B;
/// USB 2.0 9.3.1: host to device, class request, addressed to an interface.
constexpr std::uint8_t kRequestTypeInterfaceOut = 0x21;
/// HID 1.11 7.2.5: wValue 0 asks for boot protocol, 1 for report protocol.
constexpr std::uint16_t kProtocolBoot = 0;

/// A descriptor has to fit the controller's control buffer, which is 64 bytes
/// (DS1 5.13). A longer one arrives cut short, and the parser refuses what
/// does not add up rather than guessing at the rest.
constexpr std::size_t kDescriptorBuffer = kMaxBlockSize;

}  // namespace

void DescriptorSetup::begin(std::uint32_t now_us) {
    // Nothing is read here, and in particular no status.
    //
    // A status the chip is holding belongs to whatever happened before this
    // device was reset, and reading it as this one's answer would configure a
    // device that has already gone - but the reader is the caller above, not
    // this. There is exactly one, which is why poll() is handed a status
    // rather than fetching one. This used to read it too, which made two
    // readers of the same byte and cost a whole reply timeout on a chip that
    // held its line down and answered nothing.
    capabilities_ = HidCapabilities{};
    last_parse_error_ = ParseError::None;
    boot_protocol_selected_ = false;
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

/// End the attempt with a usable device, whatever the last step made of it.
///
/// The status byte is kept rather than flattened to success, because "it is
/// up" and "it is up and still in its own report protocol" are different
/// devices and only the byte says which.
SetupProgress DescriptorSetup::finish(std::uint8_t status) {
    step_ = Step::Idle;
    last_status_ = status;
    return SetupProgress::Done;
}

SetupProgress DescriptorSetup::select_boot_protocol(std::uint32_t now_us) {
    if (!capabilities_.boot_protocol) {
        // The interface does not declare the boot subclass, so there is no
        // boot report behind it to select. Asking anyway spends a control
        // transfer to be told no, and a device is free to answer worse than
        // no.
        return finish(static_cast<std::uint8_t>(InterruptStatus::Success));
    }

    ControlRequest request;
    request.request_type = kRequestTypeInterfaceOut;
    request.request = kRequestSetProtocol;
    request.value = kProtocolBoot;
    // The interface the parser chose, not zero. A composite device has
    // several, and the one being read is often not the first.
    request.index = capabilities_.interface_number;
    request.length = 0;

    if (!transport_.begin_control_request(request)) {
        // Refused before anything went on the wire. The device is configured
        // and usable; it is simply still speaking its own protocol.
        return finish(kEndedProtocolRefused);
    }
    started_us_ = now_us;
    step_ = Step::RequestingBootProtocol;
    return SetupProgress::Busy;
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
            if (choosing_protocol()) {
                // Every other step is something the device must do before it
                // can be used. This one is a preference, so silence ends the
                // request and not the device: starting over would re-enumerate
                // a working mouse for ever, and it would go quiet at the same
                // step every time.
                return finish(kEndedProtocolSilent);
            }
            return fail(kEndedTimeout);
        }
        return SetupProgress::Busy;
    }

    if (status != InterruptStatus::Success) {
        if (choosing_protocol()) {
            // A STALL here is a device saying it does not do that, which it is
            // entitled to. Nothing was left half done: a refused SETUP has no
            // status stage to send, and a control endpoint clears its own
            // stall on the next setup packet (USB 2.0 8.5.3).
            return finish(kEndedProtocolRefused);
        }
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
            return select_boot_protocol(now_us);

        case Step::RequestingBootProtocol:
            // The device took the setup packet. It has not acted on it yet:
            // that happens when the transfer completes, which is the status
            // stage and not this.
            transport_.finish_control_request();
            started_us_ = now_us;
            step_ = Step::FinishingBootProtocol;
            return SetupProgress::Busy;

        case Step::FinishingBootProtocol:
            // Now it is in boot protocol, which is the report format every
            // normalizer here was written against.
            boot_protocol_selected_ = true;
            return finish(static_cast<std::uint8_t>(InterruptStatus::Success));

        case Step::Idle:
            break;
    }
    return SetupProgress::Failed;
}

}  // namespace duo_input::u1::ch375
