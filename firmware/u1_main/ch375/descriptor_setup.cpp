#include "ch375/descriptor_setup.hpp"

#include "crypto/sha256.hpp"
#include <new>

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
/// Why a device is on boot protocol rather than its own. Not failures - each
/// of these is a device that works, without a wheel.
/// The interface declared no report descriptor at all.
constexpr std::uint8_t kNoReportDescriptor = 0xF8;
/// It declared one longer than there is room to collect.
constexpr std::uint8_t kReportDescriptorTooLong = 0xF7;
/// The device refused the request, or the chip would not carry it.
constexpr std::uint8_t kReportDescriptorRefused = 0xF6;
/// The bytes arrived and did not describe a mouse this firmware can read.
constexpr std::uint8_t kReportDescriptorUnusable = 0xF5;
/// A packet was asked for and could not be collected off the chip.
constexpr std::uint8_t kReportDescriptorUnreadable = 0xF4;
/// The device has gone silent on this request too many times to keep asking.
constexpr std::uint8_t kReportDescriptorGivenUp = 0xF3;
/// The device was read through its own report descriptor. Not boot.
constexpr std::uint8_t kReportDescriptorUsed = 0xF2;
/// A keyboard descriptor was retained for evidence before selecting boot.
constexpr std::uint8_t kKeyboardReportDescriptorCaptured = 0xF1;

/// HID 1.11 section 7.2.5. The controller has no command for this one.
constexpr std::uint8_t kRequestSetProtocol = 0x0B;
/// USB 2.0 9.3.1: host to device, class request, addressed to an interface.
constexpr std::uint8_t kRequestTypeInterfaceOut = 0x21;
/// HID 1.11 7.2.5: wValue 0 asks for boot protocol, 1 for report protocol.
constexpr std::uint16_t kProtocolBoot = 0;

/// USB 2.0 9.4.3. The controller's own GET_DESCRIPTOR command only knows the
/// device and configuration descriptors (DS2 1.11), so this one is assembled
/// as a setup packet like SET_PROTOCOL is.
constexpr std::uint8_t kRequestGetDescriptor = 0x06;
/// USB 2.0 9.3.1: device to host, standard request, addressed to an interface.
/// The recipient matters - a report descriptor belongs to an interface, and
/// asked of the device a composite has no way to know which one is meant.
constexpr std::uint8_t kRequestTypeInterfaceIn = 0x81;
/// HID 1.11 7.1.1. wValue is the type in the high byte and the index in the
/// low one, and there is only ever one report descriptor per interface.
constexpr std::uint16_t kDescriptorReport = 0x2200;
/// USB 2.0 9.3.1: device to host, standard request, addressed to the device.
constexpr std::uint8_t kRequestTypeDeviceIn = 0x80;
/// The configuration descriptor, index zero (USB 2.0 9.4.3).
constexpr std::uint16_t kDescriptorConfiguration = 0x0200;
/// The fixed part that carries wTotalLength (USB 2.0 9.6.3).
constexpr std::uint16_t kConfigurationHeaderBytes = 9;

/// Where bMaxPacketSize0 sits in a device descriptor (USB 2.0 9.6.1).
constexpr std::size_t kMaxPacketSizeOffset = 7;

/// A descriptor has to fit the controller's control buffer, which is 64 bytes
/// (DS1 5.13). A longer one arrives cut short, and the parser refuses what
/// does not add up rather than guessing at the rest.
constexpr std::size_t kDescriptorBuffer = kMaxBlockSize;

void boot_report_set(DeviceKind kind, input::hid::HidReportSet& set) {
    // Fill the setup object's report storage in place. Returning a whole set
    // would reserve it in poll's frame throughout descriptor parsing/hashing.
    new (&set) input::hid::HidReportSet{};
    set.count = 1;
    set.entries[0].role = kind == DeviceKind::Mouse
                              ? input::hid::ReportRole::Mouse
                              : input::hid::ReportRole::Keyboard;
    set.entries[0].keyboard = input::hid::boot_keyboard_layout();
    set.entries[0].mouse = input::hid::boot_mouse_layout();
}

ReportDescriptorError rejected_error(const input::hid::HidReportSet& set,
                                     input::hid::ReportRole role,
                                     ReportDescriptorError missing) {
    for (std::size_t index = 0; index < set.rejected_count; ++index) {
        if (set.rejected[index].role == role) {
            return set.rejected[index].reason;
        }
    }
    return missing;
}

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
    // A layout belongs to the device that declared it. Kept across an attempt
    // it would be applied to whatever is plugged in next, which is a mouse
    // read at another mouse's offsets.
    mouse_layout_ = boot_mouse_layout();
    have_mouse_layout_ = false;
    keyboard_layout_ = boot_keyboard_layout();
    have_keyboard_layout_ = false;
    report_set_ = input::hid::HidReportSet{};
    report_error_ = ReportDescriptorError::None;
    report_status_ = 0;
    control_packet_ = 8;
    configuration_wanted_ = 0;
    configuration_received_ = 0;
    configuration_toggle_data1_ = true;
    report_wanted_ = 0;
    report_received_ = 0;
    report_toggle_data1_ = true;
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
    // Once, here, rather than on every read of the accessor: this runs on the
    // core that reads peripherals, and a SHA-256 per pass round its loop would
    // be paid a thousand times a second to answer a question asked once.
    if (report_received_ != 0) {
        duo_input::crypto::sha256(report_buffer_, report_received_, report_hash_);
    }
    // Whatever the device did about its report descriptor, it is up. The next
    // device to arrive on this channel gets the full three tries again.
    report_silences_ = 0;
    return SetupProgress::Done;
}

SetupProgress DescriptorSetup::request_configuration_header(std::uint32_t now_us) {
    ControlRequest request;
    request.request_type = kRequestTypeDeviceIn;
    request.request = kRequestGetDescriptor;
    request.value = kDescriptorConfiguration;
    request.index = 0;
    request.length = kConfigurationHeaderBytes;
    if (!transport_.begin_control_request(request)) {
        return fail(kEndedUnreadable);
    }
    configuration_wanted_ = kConfigurationHeaderBytes;
    configuration_received_ = 0;
    configuration_toggle_data1_ = true;
    started_us_ = now_us;
    step_ = Step::RequestingConfigurationHeader;
    return SetupProgress::Busy;
}

SetupProgress DescriptorSetup::request_long_configuration(std::uint32_t now_us) {
    if (configuration_received_ < kConfigurationHeaderBytes) {
        last_parse_error_ = ParseError::Truncated;
        return fail(kEndedUnsupported);
    }
    const std::uint16_t total = static_cast<std::uint16_t>(
        configuration_buffer_[2] |
        (static_cast<std::uint16_t>(configuration_buffer_[3]) << 8));
    if (total < kConfigurationHeaderBytes || total > kMaxConfigurationDescriptorBytes) {
        last_parse_error_ = ParseError::Truncated;
        return fail(kEndedUnsupported);
    }

    ControlRequest request;
    request.request_type = kRequestTypeDeviceIn;
    request.request = kRequestGetDescriptor;
    request.value = kDescriptorConfiguration;
    request.index = 0;
    request.length = total;
    if (!transport_.begin_control_request(request)) {
        return fail(kEndedUnreadable);
    }
    configuration_wanted_ = total;
    configuration_received_ = 0;
    configuration_toggle_data1_ = true;
    started_us_ = now_us;
    step_ = Step::RequestingConfiguration;
    return SetupProgress::Busy;
}

SetupProgress DescriptorSetup::collect_configuration(std::uint32_t now_us,
                                                     Step finished_step) {
    std::uint8_t packet[kDescriptorBuffer];
    std::size_t size = 0;
    if (!transport_.read_block(packet, sizeof(packet), size)) {
        return fail(kEndedUnreadable);
    }
    const std::size_t room = kMaxConfigurationDescriptorBytes - configuration_received_;
    const std::size_t keep = size < room ? size : room;
    for (std::size_t index = 0; index < keep; ++index) {
        configuration_buffer_[configuration_received_ + index] = packet[index];
    }
    configuration_received_ = static_cast<std::uint16_t>(configuration_received_ + keep);
    configuration_toggle_data1_ = !configuration_toggle_data1_;

    const bool short_packet = size < control_packet_;
    if (!short_packet && configuration_received_ < configuration_wanted_ && keep == size) {
        transport_.request_control_data(configuration_toggle_data1_);
        started_us_ = now_us;
        return SetupProgress::Busy;
    }
    if (!transport_.finish_control_read()) {
        step_ = finished_step;
        return finished_step == Step::FinishingConfigurationHeader
                   ? request_long_configuration(now_us)
                   : parse_long_configuration();
    }
    started_us_ = now_us;
    step_ = finished_step;
    return SetupProgress::Busy;
}

SetupProgress DescriptorSetup::parse_long_configuration() {
    last_parse_error_ = parse_configuration(
        protocol::ByteView{configuration_buffer_, configuration_received_}, capabilities_);
    if (last_parse_error_ != ParseError::None) {
        capabilities_ = HidCapabilities{};
        return fail(kEndedUnsupported);
    }
    transport_.set_configuration(1);
    started_us_ = transport_.now_us();
    step_ = Step::ChoosingConfiguration;
    return SetupProgress::Busy;
}

/// Ask the selected HID interface for its report descriptor.
SetupProgress DescriptorSetup::request_report_descriptor(std::uint32_t now_us) {
    if (capabilities_.kind != DeviceKind::Mouse &&
        capabilities_.kind != DeviceKind::Keyboard) {
        return select_boot_protocol(now_us);
    }
    if (report_silences_ >= kReportDescriptorAttempts) {
        // Asked and unanswered every time. Asking again would reset the bus
        // again, for ever, on a device that works.
        report_status_ = kReportDescriptorGivenUp;
        return select_boot_protocol(now_us);
    }

    const std::uint16_t length = capabilities_.report_descriptor_length;
    if (length == 0) {
        // The interface named no report descriptor. HID requires one, and a
        // device is under no obligation to be correct.
        report_status_ = kNoReportDescriptor;
        return select_boot_protocol(now_us);
    }
    if (length > kMaxReportDescriptorBytes) {
        // Refused by name rather than collected in part. Half a descriptor
        // parses as a different device, and a different device is the wrong
        // offsets on every report it will ever send.
        report_status_ = kReportDescriptorTooLong;
        return select_boot_protocol(now_us);
    }

    ControlRequest request;
    request.request_type = kRequestTypeInterfaceIn;
    request.request = kRequestGetDescriptor;
    request.value = kDescriptorReport;
    // The interface the parser chose. A composite has several and only one of
    // them is the mouse.
    request.index = capabilities_.interface_number;
    request.length = length;

    if (!transport_.begin_control_request(request)) {
        // Refused before anything went on the wire, so nothing is outstanding.
        report_status_ = kReportDescriptorRefused;
        return select_boot_protocol(now_us);
    }
    report_wanted_ = length;
    report_received_ = 0;
    // A control transfer's data stage starts at DATA1 and alternates
    // (USB 2.0 8.6). The chip tracks neither toggle, so both are set by hand.
    report_toggle_data1_ = true;
    started_us_ = now_us;
    step_ = Step::RequestingReportDescriptor;
    return SetupProgress::Busy;
}

/// Take one packet off the chip, then ask for the next or end the transfer.
SetupProgress DescriptorSetup::collect_report_descriptor(std::uint32_t now_us) {
    std::uint8_t packet[kDescriptorBuffer];
    std::size_t size = 0;
    if (!transport_.read_block(packet, sizeof(packet), size)) {
        // read_block has already drained what the chip was sending. Walking
        // away without that would leave those bytes to be read as the answer
        // to the next command, and the one after it, for ever.
        return abandon_report_descriptor(now_us, kReportDescriptorUnreadable);
    }

    const std::size_t room = kMaxReportDescriptorBytes - report_received_;
    const std::size_t keep = size < room ? size : room;
    for (std::size_t index = 0; index < keep; ++index) {
        report_buffer_[report_received_ + index] = packet[index];
    }
    report_received_ = static_cast<std::uint16_t>(report_received_ + keep);
    report_toggle_data1_ = !report_toggle_data1_;

    // A packet shorter than the endpoint's maximum is the end of the data,
    // whatever the device promised in wLength (USB 2.0 8.5.3.2). So is having
    // collected everything that was asked for. Either way the next thing is
    // the status stage and not another IN.
    const bool short_packet = size < control_packet_;
    if (!short_packet && report_received_ < report_wanted_ && keep == size) {
        transport_.request_control_data(report_toggle_data1_);
        started_us_ = now_us;
        return SetupProgress::Busy;
    }

    if (!transport_.finish_control_read()) {
        // The empty packet was refused before its token went out, so nothing
        // is outstanding and the bytes in hand are still good.
        return apply_report_descriptor(now_us);
    }
    started_us_ = now_us;
    step_ = Step::FinishingReportDescriptor;
    return SetupProgress::Busy;
}

/// Read what arrived, and keep it only if all of it can be named in bytes.
SetupProgress DescriptorSetup::apply_report_descriptor(std::uint32_t now_us) {
    if (report_received_ == 0) {
        // The transfer itself completed, but there is no descriptor to ask
        // the parser about.  Keep that distinct from bytes which arrived and
        // did not describe a mouse: the distinction is essential on a board
        // where the only post-mortem evidence is this status and byte count.
        report_error_ = ReportDescriptorError::None;
        return abandon_report_descriptor(now_us, kReportDescriptorUnreadable);
    }

    report_error_ = input::hid::parse_hid_report_set(
        protocol::ByteView{report_buffer_, report_received_}, report_set_);
    if (report_error_ != ReportDescriptorError::None) {
        return abandon_report_descriptor(now_us, kReportDescriptorUnusable);
    }
    if (report_set_.count == 0) {
        report_error_ = capabilities_.kind == DeviceKind::Mouse
                            ? rejected_error(report_set_, input::hid::ReportRole::Mouse,
                                             ReportDescriptorError::NoMouseReport)
                            : rejected_error(report_set_, input::hid::ReportRole::Keyboard,
                                             ReportDescriptorError::NoKeyboardReport);
        return abandon_report_descriptor(now_us, kReportDescriptorUnusable);
    }

    const input::hid::HidReportEntry& first = report_set_.entries[0];
    if (first.role == input::hid::ReportRole::Mouse) {
        mouse_layout_ = first.mouse;
        have_mouse_layout_ = true;
    } else {
        keyboard_layout_ = first.keyboard;
        have_keyboard_layout_ = true;
    }
    // The accepted report set describes report protocol interface-wide. No
    // individual Report ID gets to select a different protocol.
    return finish(kReportDescriptorUsed);
}

SetupProgress DescriptorSetup::abandon_report_descriptor(std::uint32_t now_us,
                                                        std::uint8_t status) {
    report_status_ = status;
    // Parsed candidates describe report protocol. Once that descriptor is
    // rejected they cannot be published as the active wire format, including
    // when the subsequent boot-protocol request is refused or silent.
    report_set_ = input::hid::HidReportSet{};
    // Do not erase bytes already collected.  A zero byte count means nothing
    // arrived; a non-zero count plus kReportDescriptorUnusable means the
    // parser rejected actual evidence.  begin() resets the count before the
    // next device, so preserving it here cannot leak into another attempt.
    if (capabilities_.kind == DeviceKind::Keyboard) {
        return fallback_keyboard_to_boot(now_us);
    }
    return select_boot_protocol(now_us);
}

/// A keyboard whose own description cannot be used, put back on the fixed one.
///
/// Only a boot-capable interface has a fixed report behind it. Without one
/// there is no layout left to read this device at: boot's offsets would be a
/// guess about a report the keyboard never agreed to send, and a guess here is
/// keystrokes nobody made arriving on somebody's computer.
///
/// Mice do not come through here. A mouse with no usable descriptor still
/// moves the pointer on boot protocol whether or not the interface says so,
/// and refusing one would take away a device that worked.
///
/// The layout needs no resetting: begin() sets it to boot's for every attempt,
/// and the only thing that replaces it is a descriptor that parsed - which
/// finishes immediately and never reaches here.
SetupProgress DescriptorSetup::fallback_keyboard_to_boot(std::uint32_t now_us) {
    if (!capabilities_.boot_protocol) {
        return fail(kEndedUnsupported);
    }
    return select_boot_protocol(now_us);
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
            if (fetching_report_descriptor()) {
                // Silence, and silence alone, is not recoverable in place: a
                // token was issued and never answered, so the chip may still
                // complete it and raise its interrupt - and that interrupt
                // would be read as the answer to whatever is asked next.
                // Ending the attempt is what gets ABORT_NAK issued and the bus
                // reset before anything else is said (device.cpp, Enumerating).
                //
                // Counted, so a device that is silent every time stops being
                // asked and comes up on boot rather than re-enumerating for
                // ever.
                ++report_silences_;
                report_status_ = kReportDescriptorRefused;
                return fail(kEndedTimeout);
            }
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
        if (step_ == Step::ReadingConfiguration && status == InterruptStatus::BufferOver) {
            // The dedicated GET_DESCRIPTOR command owns one 64-byte buffer.
            // Ask endpoint zero ourselves so each USB packet can be drained
            // before the next one arrives.
            return request_configuration_header(now_us);
        }
        if (fetching_report_descriptor()) {
            // A STALL is a device saying it will not answer that, which it is
            // entitled to. Unlike a timeout it is an answer: the interrupt has
            // been read and nothing is left outstanding, so the next control
            // transfer can go out safely - and the next one is the request for
            // boot protocol, which is where such a device belongs.
            return abandon_report_descriptor(now_us, kReportDescriptorRefused);
        }
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
            // One field is worth keeping: how much endpoint zero carries in a
            // single packet (USB 2.0 9.6.1). A report descriptor is longer
            // than that on most mice, so it arrives in several, and knowing
            // the size is what says which packet is the last one. Only the
            // four legal values are believed; anything else would either end
            // the transfer early or ask for a packet that never comes.
            // idVendor and idProduct (USB 2.0 9.6.1, offsets 8 and 10). The
            // only place this firmware ever learns what the peripheral is, and
            // the only thing that can name a row of the compatibility matrix.
            if (size >= 12) {
                vendor_id_ = static_cast<std::uint16_t>(buffer[8] | (buffer[9] << 8));
                product_id_ = static_cast<std::uint16_t>(buffer[10] | (buffer[11] << 8));
            }
            const std::uint8_t declared = buffer[kMaxPacketSizeOffset];
            if (declared == 8 || declared == 16 || declared == 32 || declared == 64) {
                control_packet_ = declared;
            }
            // Nothing else inside it is needed to go on. What it establishes
            // is that the device answered on address zero, which means it is
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

        case Step::RequestingConfigurationHeader:
            transport_.request_control_data(configuration_toggle_data1_);
            started_us_ = now_us;
            step_ = Step::ReadingConfigurationHeader;
            return SetupProgress::Busy;

        case Step::ReadingConfigurationHeader:
            return collect_configuration(now_us, Step::FinishingConfigurationHeader);

        case Step::FinishingConfigurationHeader:
            return request_long_configuration(now_us);

        case Step::RequestingConfiguration:
            transport_.request_control_data(configuration_toggle_data1_);
            started_us_ = now_us;
            step_ = Step::ReadingLongConfiguration;
            return SetupProgress::Busy;

        case Step::ReadingLongConfiguration:
            return collect_configuration(now_us, Step::FinishingLongConfiguration);

        case Step::FinishingLongConfiguration:
            return parse_long_configuration();

        case Step::ChoosingConfiguration:
            return request_report_descriptor(now_us);

        case Step::RequestingReportDescriptor:
            // The device took the setup packet. None of the descriptor has
            // arrived yet - that is the data stage, one IN transaction at a
            // time, each one a tick of its own so that a hundred-byte
            // descriptor never becomes a spin on a core that owes the other
            // channel a poll every eight milliseconds.
            transport_.request_control_data(report_toggle_data1_);
            started_us_ = now_us;
            step_ = Step::ReadingReportDescriptor;
            return SetupProgress::Busy;

        case Step::ReadingReportDescriptor:
            return collect_report_descriptor(now_us);

        case Step::FinishingReportDescriptor:
            // The transfer is closed. Whatever the status stage came to, the
            // bytes are already in hand.
            return apply_report_descriptor(now_us);

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
            boot_report_set(capabilities_.kind, report_set_);
            keyboard_layout_ = report_set_.entries[0].keyboard;
            mouse_layout_ = report_set_.entries[0].mouse;
            return finish(static_cast<std::uint8_t>(InterruptStatus::Success));

        case Step::Idle:
            break;
    }
    return SetupProgress::Failed;
}

}  // namespace duo_input::u1::ch375
