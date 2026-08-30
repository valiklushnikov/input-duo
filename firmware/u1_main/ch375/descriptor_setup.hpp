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
//   read a mouse's report descriptor, if it said it has one
//   ask a boot-capable interface to use boot protocol, if that failed
//
// It is longer than one command, and at the end the address, the endpoint and
// the report format are known rather than assumed.
//
// The last two steps are alternatives, and which one runs decides whether the
// mouse has a wheel. Boot protocol's report is three bytes - buttons, dX, dY -
// and there is no wheel in it, so a device forced into boot has one silently
// taken away. The report descriptor is the only thing that says where a wheel
// is, or whether an identifier leads every report; a device that gives one up
// is left in its own protocol and read through what it declared.
//
// The order is deliberate. Forcing boot is what made the mouse on this bench
// work at all - before it, its native report was read one byte out of place,
// with the identifier taken for the buttons - so boot stays the fallback and
// the descriptor has to prove itself first. Everything that can go wrong with
// the fetch ends at boot: an interface that declares no report descriptor, one
// longer than there is room for, a device that refuses the request, and a
// descriptor that arrives but cannot be represented by the bounded layout.
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
#include "ch375/report_descriptor.hpp"
#include "ch375/transport.hpp"

namespace duo_input::u1::ch375 {

/// The address given to whatever is attached.
///
/// One device per controller, so one address is enough. Anything but zero
/// would do; zero is where everything starts and only one device can be there.
inline constexpr std::uint8_t kAssignedAddress = 2;

/// The longest report descriptor this firmware will collect.
///
/// A mouse's is fifty to a hundred bytes; the five-button one in the corpus
/// declares ninety-four. Gaming mice with a dozen collections run longer, and
/// a device that declares more than this is refused by name and brought up on
/// boot protocol rather than half read - a descriptor cut short parses as a
/// different device, and a different device is the wrong offsets.
///
/// Two of these exist, one per channel, and they are members rather than
/// stack: Core 1 has two kilobytes under it.
inline constexpr std::size_t kMaxReportDescriptorBytes = 256;

/// How many times in a row a device may go silent on the report-descriptor
/// request before it is simply not asked again.
///
/// Silence is the one failure that cannot be recovered from in place: the
/// transaction is still outstanding, so the attempt has to end and the bus be
/// reset, and a device that is silent every time would re-enumerate for ever.
/// After this many it comes up on boot protocol, which is exactly where it
/// would have been if this step had never existed.
inline constexpr std::uint16_t kReportDescriptorAttempts = 3;

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

    /// Did this device describe its own report layout?
    ///
    /// False for every keyboard, for a mouse that declares no report
    /// descriptor, and for one whose descriptor could not be fetched or could
    /// not be parsed. All of those are on boot protocol.
    bool has_mouse_layout() const { return have_mouse_layout_; }

    /// Where this mouse keeps its fields - its own layout when one was read,
    /// and boot protocol's when it was not.
    ///
    /// Always answerable, so a caller cannot forget to check first and hand
    /// the normalizer nothing.
    const MouseReportLayout& mouse_layout() const { return mouse_layout_; }

    /// Why the report descriptor was not used, for a bring-up build to report.
    std::uint8_t last_report_descriptor_status() const { return report_status_; }
    ReportDescriptorError last_report_descriptor_error() const { return report_error_; }
    /// How many bytes the interface said its report descriptor contains.
    std::uint16_t report_descriptor_wanted() const { return report_wanted_; }
    /// How many bytes of it arrived.
    std::uint16_t report_descriptor_bytes() const { return report_received_; }
    /// The bytes themselves, retained for the probe build's post-mortem.
    protocol::ByteView report_descriptor() const {
        return protocol::ByteView{report_buffer_, report_received_};
    }

    /// What the device said it is, from its own device descriptor.
    ///
    /// Read on every enumeration and kept, because a peripheral plugged into
    /// U1's own USB port is invisible to the computer at the other end of the
    /// CDC link: nothing else on either side can say which device a row of a
    /// compatibility matrix is about. Zero until a device has answered.
    std::uint16_t vendor_id() const { return vendor_id_; }
    std::uint16_t product_id() const { return product_id_; }

    /// SHA-256 of the report descriptor this device gave up, or zeros.
    ///
    /// What tells two devices sharing a VID and PID apart, and what catches a
    /// peripheral whose own firmware changed between one run of the matrix and
    /// the next. All zeros means no report descriptor was read - a keyboard,
    /// or a mouse that declined - rather than the hash of an empty buffer,
    /// which is a constant every such device would share.
    const std::uint8_t* report_descriptor_hash() const { return report_hash_; }

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
        /// The GET_DESCRIPTOR setup packet has gone; its interrupt is awaited.
        RequestingReportDescriptor,
        /// An IN token has gone; the packet it fetches is awaited.
        ReadingReportDescriptor,
        /// The empty status packet has gone, which ends the transfer.
        FinishingReportDescriptor,
        /// The SET_PROTOCOL setup packet has gone; its interrupt is awaited.
        RequestingBootProtocol,
        /// The status stage has gone. Only when it lands has the device acted.
        FinishingBootProtocol,
    };

    SetupProgress fail(std::uint8_t status);
    SetupProgress finish(std::uint8_t status);
    /// Ask a mouse for its report descriptor, or go straight to boot.
    SetupProgress request_report_descriptor(std::uint32_t now_us);
    /// Collect one packet of it, and ask for the next or end the transfer.
    SetupProgress collect_report_descriptor(std::uint32_t now_us);
    /// Parse what arrived, and keep the layout or fall back to boot.
    SetupProgress apply_report_descriptor(std::uint32_t now_us);
    /// Give up on the descriptor and take the path that was already working.
    SetupProgress abandon_report_descriptor(std::uint32_t now_us, std::uint8_t status);
    /// Ask a boot-capable interface to switch, or finish without asking.
    SetupProgress select_boot_protocol(std::uint32_t now_us);
    /// True while the outcome of the protocol request is still outstanding.
    bool choosing_protocol() const {
        return step_ == Step::RequestingBootProtocol || step_ == Step::FinishingBootProtocol;
    }
    /// True while the report descriptor is still being fetched.
    bool fetching_report_descriptor() const {
        return step_ == Step::RequestingReportDescriptor ||
               step_ == Step::ReadingReportDescriptor ||
               step_ == Step::FinishingReportDescriptor;
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

    // --- the report descriptor ---------------------------------------------
    MouseReportLayout mouse_layout_ = boot_mouse_layout();
    bool have_mouse_layout_ = false;
    ReportDescriptorError report_error_ = ReportDescriptorError::None;
    std::uint8_t report_status_ = 0;
    /// How many times running the device has said nothing to the request.
    std::uint16_t report_silences_ = 0;
    /// What the device descriptor said endpoint zero can carry in one packet
    /// (USB 2.0 9.6.1). Eight until it has said, because reading a full packet
    /// as a short one ends the transfer early and reading a short one as full
    /// asks for a packet that never comes.
    std::uint8_t control_packet_ = 8;
    std::uint16_t report_wanted_ = 0;
    std::uint16_t report_received_ = 0;
    /// Computed once, when a setup ends. Recomputing it per pass would be a
    /// SHA-256 every millisecond to answer a question nobody asked twice.
    std::uint8_t report_hash_[32] = {};
    std::uint16_t vendor_id_ = 0;
    std::uint16_t product_id_ = 0;
    bool report_toggle_data1_ = true;
    std::uint8_t report_buffer_[kMaxReportDescriptorBytes] = {};
};

}  // namespace duo_input::u1::ch375
