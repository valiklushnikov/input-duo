#pragma once

// The CDC side of U1: what the configurator talks to.
//
// One request in, one reply out, same sequence number. There is never more
// than one request in flight and the device never speaks first, except for
// capture events - so the whole thing is a function from a frame to a frame,
// plus the staging state a write transaction carries between them.
//
// Two rules shape everything here. A request whose sequence is not the next
// one is refused rather than acted on, because a configurator that lost a
// reply and a configurator that is confused look identical from this side.
// And a request byte-identical to the last one gets the last reply again,
// because that is what a lost reply looks like and re-erasing a slot for it
// would be worse than useless.

#include <cstddef>
#include <cstdint>

#include "mapping/capture.hpp"
#include "protocol/frame.hpp"
#include "protocol/generated.hpp"
#include "storage/ab_store.hpp"

namespace duo_input::u1 {

/// What the host last asked the capture to do.
///
/// The capture itself runs on Core 1. This class only records what was asked
/// for and the main loop carries it across, because a USB callback writing the
/// other core's state is how a keyboard ends up swallowing keys nobody meant
/// it to.
enum class CaptureRequest : std::uint8_t {
    None,
    Begin,
    Cancel,
};

/// Errors the protocol defines. Mirrors the emulator, which is the reference.
enum class CdcError : std::uint8_t {
    Ok = 0,
    InvalidRequest = 1,
    IncompatibleMajor = 2,
    UnsupportedCapability = 3,
    BadSequence = 4,
    Busy = 5,
    BadState = 6,
    BadSize = 7,
    BadChunk = 8,
    BadHash = 9,
    InvalidConfig = 10,
    PhysicalConfirmationRequired = 11,
};

/// Counters the host can ask for.
struct CdcDiagnostics {
    std::uint32_t bad_crc = 0;
    std::uint32_t disconnect = 0;
    std::uint32_t timeout = 0;
    std::uint32_t bad_sequence = 0;
    std::uint32_t aborted_staging = 0;
};

/// Where a reply goes. The USB service supplies one.
class CdcSink {
public:
    virtual ~CdcSink() = default;
    virtual void write(const std::uint8_t* data, std::size_t size) = 0;
};

/// Largest wire frame: header, maximum payload, CRC, COBS overhead, delimiter.
inline constexpr std::size_t kMaxWireFrame = 1100;

/// What the link to U2 is doing, as the host needs to see it.
///
/// The counters beside this one all count failures, and a link that never
/// started produces none of them - it was possible for every reading this
/// device offered to be zero while the second board was not there at all.
/// These say what is happening rather than what went wrong.
struct LinkState {
    /// U2 replied to the last frame with something only U2 could have sent.
    bool answered = false;
    /// U2 says its own USB is up.
    bool mounted = false;
    std::uint32_t frames_sent = 0;
    std::uint32_t crc_errors = 0;
    /// Frames U1 received that only U1 could have sent - see spi_master.hpp.
    std::uint32_t echoed_frames = 0;
    /// How many times U2 has released everything because U1 went quiet.
    std::uint8_t endpoint_drops = 0;
    /// The silence that caused U2's most recent release, in milliseconds.
    std::uint16_t endpoint_release_ms = 0;
};

class ConfigService {
public:
    ConfigService(storage::AbStore& store, CdcSink& sink) : store_(store), sink_(sink) {}

    /// Feed bytes as they arrive from the CDC endpoint.
    ///
    /// Frames are delimited by a zero byte, so partial reads are normal and
    /// the leftover is kept until the rest turns up.
    void on_cdc_bytes(const std::uint8_t* data, std::size_t size);

    /// Forget any partial frame and any write in progress.
    ///
    /// Called when the host goes away. An abandoned staging slot has no header
    /// so it is already nothing, but the counter should say it happened.
    void on_disconnect();

    /// Publish what the link is doing, for GET_DIAGNOSTICS to report.
    void set_link_state(const LinkState& state) { link_state_ = state; }

    const LinkState& link_state() const { return link_state_; }

    /// Which profile the device is running.
    ///
    /// Set by the main loop when Core 1 confirms a swap, not when the host
    /// asks for one. Reporting a profile before the bindings behind it changed
    /// would have the configurator show one thing while the keyboard does
    /// another for as long as the swap takes.
    std::uint8_t active_profile() const { return active_profile_; }
    void set_active_profile(std::uint8_t profile) { active_profile_ = profile; }

    /// Whether a capture is running, as Core 1 last reported it.
    ///
    /// Published by the main loop rather than owned here, for the same reason
    /// the link state is: a capture ends on its own after ten seconds and this
    /// class would otherwise go on telling the host one is running.
    bool capture_active() const { return capture_active_; }
    void set_capture_active(bool active) { capture_active_ = active; }

    const CdcDiagnostics& diagnostics() const { return diagnostics_; }

#if DUO_SPI_DEBUG || DUO_CH375_PROBE
    /// Bytes reported in place of the ordinary diagnostics, for bring-up only.
    ///
    /// This replaces a frozen protocol reply with something the configurator
    /// cannot parse, which is why it exists only behind a build flag and never
    /// ships. It is here because the alternative was guessing at a silent
    /// four-wire link.
    void set_link_debug(const std::uint8_t* bytes, std::size_t size);
#endif

    /// Record that someone at the device held SW2 for five seconds.
    ///
    /// The confirmation authorises exactly one factory reset and is spent by
    /// it. A standing confirmation would let a program erase the operator's
    /// work repeatedly on the strength of one button press, and a new session
    /// forgets it entirely: the person who pressed the button and the program
    /// now connected are not necessarily the same person.
    void confirm_factory_reset() { factory_confirmed_ = true; }

    /// Set when the host asked for everything to be released.
    ///
    /// Read and cleared by the main loop: this class must not reach into the
    /// output state, which belongs to Core 0's runtime.
    bool take_release_all_request();

    /// What the host last asked of the capture, taken once.
    ///
    /// Read twice, a Begin would start a second capture nobody asked for and
    /// the operator's next keystroke would vanish into it.
    CaptureRequest take_capture_request();

    /// The profile the host asked for, if it asked. Taken once.
    bool take_profile_request(std::uint8_t& profile);

    /// Send the trigger the operator pressed, unprompted.
    ///
    /// The host is waiting on this and nothing else, so it goes out on the
    /// sequence the next request would have used and moves the count on - a
    /// configurator that carried on from its own last request would be refused
    /// from here to the end of the session. The capture ends with it: one
    /// question, one answer.
    ///
    /// Ignored when no capture is running. Speaking out of turn costs the host
    /// its sequence over a trigger it never asked for.
    void emit_capture_event(const mapping::CapturedTrigger& trigger);

private:
    void handle_frame(const std::uint8_t* wire, std::size_t size);
    void dispatch(const protocol::CdcFrame& frame);
    void reply(protocol::CdcMessageType type, std::uint16_t sequence,
               const std::uint8_t* payload, std::size_t size);
    void reply_error(const protocol::CdcFrame& frame, CdcError error);

    std::size_t device_info_payload(CdcError error, std::uint32_t capabilities,
                                    std::uint8_t* out) const;
    std::size_t status_payload(CdcError error, std::uint8_t* out) const;
    std::size_t config_info_payload(CdcError error, std::uint8_t* out);
    std::size_t diagnostics_payload(CdcError error, std::uint8_t* out) const;

    storage::AbStore& store_;
    CdcSink& sink_;

    // Assembly of an incoming frame.
    std::uint8_t pending_[kMaxWireFrame] = {};
    std::size_t pending_size_ = 0;

    // The last exchange, kept so a repeated request gets a repeated reply.
    std::uint8_t last_request_[kMaxWireFrame] = {};
    std::size_t last_request_size_ = 0;
    std::uint8_t last_response_[kMaxWireFrame] = {};
    std::size_t last_response_size_ = 0;
    std::uint16_t last_sequence_ = 0;
    bool have_sequence_ = false;

    std::uint32_t negotiated_capabilities_ = 0;
    bool negotiated_ = false;

    /// Where the next chunk of a write must start. The protocol requires
    /// strictly ascending chunks so both ends agree on what has arrived.
    std::uint32_t expected_offset_ = 0;

    std::uint8_t active_profile_ = 1;
    bool capture_active_ = false;
    CaptureRequest capture_request_ = CaptureRequest::None;
    std::uint8_t requested_profile_ = 0;
    bool profile_requested_ = false;
    bool release_all_requested_ = false;
    bool factory_confirmed_ = false;
    bool factory_armed_ = false;

    CdcDiagnostics diagnostics_{};
    LinkState link_state_{};

#if DUO_SPI_DEBUG || DUO_CH375_PROBE
    std::uint8_t link_debug_[900] = {};
    std::size_t link_debug_size_ = 0;
#endif
};

}  // namespace duo_input::u1
