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

#include "protocol/frame.hpp"
#include "protocol/generated.hpp"
#include "storage/ab_store.hpp"

namespace duo_input::u1 {

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
    std::uint8_t active_profile() const { return active_profile_; }
    void set_active_profile(std::uint8_t profile) { active_profile_ = profile; }

    /// Whether a capture is running.
    ///
    /// Always false today: the device does not advertise the CAPTURE
    /// capability, because it has no peripheral to capture from until the
    /// CH375B exists. The field is reported in GET_STATUS regardless, so the
    /// host reads a truthful answer rather than a missing one.
    bool capture_active() const { return capture_active_; }

    const CdcDiagnostics& diagnostics() const { return diagnostics_; }

#if DUO_SPI_DEBUG
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
    bool release_all_requested_ = false;
    bool factory_confirmed_ = false;
    bool factory_armed_ = false;

    CdcDiagnostics diagnostics_{};
    LinkState link_state_{};

#if DUO_SPI_DEBUG
    std::uint8_t link_debug_[48] = {};
    std::size_t link_debug_size_ = 0;
#endif
};

}  // namespace duo_input::u1
