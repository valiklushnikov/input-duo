#pragma once

// Everything that crosses between Core 0's CDC service and Core 1's runtime.
//
// It lives here rather than in main's loop because this is the third defect
// found by reading main.cpp instead of by a failing test: the answer to a
// capture was thrown away by publishing the runtime's state before taking the
// event, and nothing anywhere checked that a host's profile acknowledgement
// reached confirm_profile_applied while a binding's reached
// publish_local_profile. main.cpp cannot be built on a desktop; this can.
//
// Neither side reaches into the other anywhere else. One of them is a USB
// callback and the other is halfway through a binding table.

#include <atomic>
#include <cstdint>

#include "config_service.hpp"
#include "core1_runtime.hpp"
#include "protocol/bytes.hpp"

namespace duo_input::u1 {

/// Carry one pass of requests to Core 1 and one pass of answers back.
///
/// Called from Core 0 only, once per turn round its loop.
///
/// A template on the runtime, and only for that reason: what this function
/// has to get right is the order in which it reads two values another core is
/// writing, and the interleaving that matters - Core 1 finishing a capture
/// between Core 0's two reads - cannot be staged against the real runtime from
/// one thread. A stand-in that answers "no capture is running" and hands over
/// a captured trigger in the same pass can, and does.
template <typename Runtime>
void pump_core_bridge(ConfigService& config, Runtime& runtime) {
    switch (config.take_capture_request()) {
        case CaptureRequest::Begin:
            runtime.request_capture_begin();
            break;
        case CaptureRequest::Cancel:
            runtime.request_capture_cancel();
            break;
        case CaptureRequest::None:
            break;
    }

    // The state first and the event second - the opposite order to the one
    // Core 1 publishes them in, and that opposition is the whole point.
    //
    // Core 1 stores the captured trigger and only then stores that the capture
    // is over. So if this read says the capture is over, the store that said
    // so happened after the store of the trigger, and the take below is
    // certain to find it. Reading them the other way round leaves a window in
    // which Core 0 sees neither: no event, and a capture that has ended. It
    // publishes "not running", and the answer the operator gave is refused on
    // the next pass for want of a capture to answer - the original defect, in
    // a gap about four instructions wide.
    const bool active = runtime.capture_active();

    mapping::CapturedTrigger captured;
    if (runtime.take_capture_event(captured)) {
        // Answering is what ends the capture, and emit_capture_event ends it
        // itself. Publishing as well would be publishing a state that was read
        // before the answer was taken - which, when the answer arrived between
        // the two, is exactly the "no capture" that throws the answer away.
        config.emit_capture_event(captured);
    } else {
        config.set_capture_active(active);
    }

    std::uint8_t profile = 0;
    if (config.take_profile_request(profile)) {
        runtime.request_profile(profile);
    }

    bool requested_by_host = false;
    if (runtime.take_profile_ack(profile, requested_by_host)) {
        // Only now is it true: Core 1 has stopped its macros and let go of
        // what was held under the old profile's meaning.
        //
        // Which of the two it goes to matters. A host's acknowledgement has an
        // outstanding SET_ACTIVE_PROFILE behind it and is matched against it,
        // so an acknowledgement for some other profile cannot answer the
        // request the host is waiting on. A local one - a binding, a macro
        // step - has no request to match, and that same check would discard
        // it, leaving GET_STATUS reporting a profile the device stopped
        // running.
        if (requested_by_host) {
            config.confirm_profile_applied(profile);
        } else {
            config.publish_local_profile(profile);
        }
    }

    // Core 1 counts what the queue refused. This is the only way out of it,
    // and a nonzero count means what a computer is holding no longer matches
    // what the operator did.
    config.set_dropped_commands(runtime.dropped_commands());
}

/// The one configuration change Core 0 has staged and Core 1 has not adopted.
///
/// A committed A/B slot becomes the spare slot on the following write, so Core
/// 0 cannot acknowledge WRITE_COMMIT until Core 1 has stopped reading the old
/// slot and every macro pointer has been rebuilt against the new one.
///
/// The mechanism is a handshake and not a lockout. `multicore_lockout` pauses
/// the other core at an arbitrary instruction - inside `set_profile_now`, or
/// inside the loop that installs macro definitions - and rebuilding those
/// structures underneath a core that then resumes its own half-finished
/// rebuild produces a binding table interleaved from two profiles. Here Core 1
/// does the work at a point in its own loop that it chose, and the
/// acknowledgement means the work is finished rather than merely interrupted.
///
/// Core 0 writes the package and the request counter. Core 1 writes the
/// outcome and the acknowledgement counter. Neither writes the other's, so
/// there is nothing to lock and nothing that can be read half-written.
class ConfigHandoff {
public:
    /// Core 0: publish a package for Core 1 to adopt. Returns the ticket to
    /// wait on. A package of nothing is a factory reset - Core 1 is being told
    /// to stop reading flash that is about to be erased.
    std::uint32_t post(protocol::ByteView package);

    /// Core 0: has Core 1 finished that ticket?
    bool finished(std::uint32_t ticket) const;

    /// Core 0: whether the package Core 1 adopted was a configuration.
    /// Only meaningful once ``finished`` says so.
    bool succeeded() const { return loaded_; }

    /// Core 1: take the package to adopt, if there is one waiting.
    bool take(protocol::ByteView& package);

    /// Core 1: report the outcome of the ticket just taken.
    void complete(bool loaded);

private:
    protocol::ByteView package_{nullptr, 0};
    /// Written by Core 0 only.
    std::atomic<std::uint32_t> requested_{0};
    /// Written by Core 1 only.
    std::atomic<std::uint32_t> acknowledged_{0};
    /// Core 1's own; Core 0 never touches it.
    std::uint32_t taken_ = 0;
    /// Written by Core 1 before the acknowledgement, read by Core 0 after it.
    bool loaded_ = false;
};

}  // namespace duo_input::u1
