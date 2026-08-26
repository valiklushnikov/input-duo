#pragma once

// What travels over the SPI link between U1 and U2, and how it is tracked.
//
// Every message on this link carries an *absolute* state rather than a change:
// the full set of held keys, the full button mask. That is deliberate. A link
// that carries changes has to be perfect, because one lost frame leaves the
// two ends disagreeing forever - and this link runs four wires across a
// connector that someone can knock. Carrying absolute state means a lost frame
// costs one millisecond of staleness and nothing else, and a duplicate frame
// costs nothing at all.
//
// The framing itself - magic, CRC, length - is protocol/frame.hpp, shared with
// the CDC link. This file is only about what the payloads mean.

#include <cstdint>

#include "hid/types.hpp"
#include "protocol/bytes.hpp"
#include "protocol/generated.hpp"

namespace duo_input::link {

/// What one frame's sequence number says about the frames before it.
enum class SequenceVerdict : std::uint8_t {
    /// The next one, or the first one. Nothing was missed.
    Fresh,
    /// Exactly the one already seen. Applying it again changes nothing.
    Duplicate,
    /// Something in between never arrived, or the far end restarted.
    Gap,
};

/// Follows the sequence numbers on one direction of the link.
///
/// It never refuses a frame. Refusing would mean discarding a state that is
/// more current than the one being held, which is the opposite of useful; the
/// verdict exists so gaps can be counted and reported, not so frames can be
/// dropped.
class SequenceTracker {
public:
    SequenceVerdict observe(std::uint16_t sequence);
    void reset();

    std::uint32_t gap_count() const { return gaps_; }
    std::uint32_t duplicate_count() const { return duplicates_; }

private:
    bool started_ = false;
    std::uint16_t last_ = 0;
    std::uint32_t gaps_ = 0;
    std::uint32_t duplicates_ = 0;
};

// --------------------------------------------------------------- payloads

/// Bytes a KBD_STATE payload occupies: modifiers, count, six usages.
inline constexpr std::size_t kKeyboardStateSize = 8;

/// Bytes a MOUSE_DELTA payload occupies: buttons, dx, dy, wheel, pan.
inline constexpr std::size_t kMouseDeltaSize = 7;

/// Bytes a CONSUMER_STATE payload occupies: one 16-bit usage.
inline constexpr std::size_t kConsumerStateSize = 2;

/// Write one keyboard state into ``output``. Returns false if it will not fit.
bool encode_keyboard_state(const hid::KeyboardSnapshot& keyboard,
                           protocol::MutableByteView output, std::size_t& written);

/// Read one keyboard state. Returns false on any payload this cannot be.
bool decode_keyboard_state(protocol::ByteView payload, hid::KeyboardSnapshot& keyboard);

/// Write one mouse report into ``output``.
bool encode_mouse_delta(const hid::MouseSnapshot& mouse, protocol::MutableByteView output,
                        std::size_t& written);

/// Read one mouse report.
bool decode_mouse_delta(protocol::ByteView payload, hid::MouseSnapshot& mouse);

/// Write one consumer usage.
bool encode_consumer_state(std::uint16_t usage, protocol::MutableByteView output,
                           std::size_t& written);

/// Read one consumer usage.
bool decode_consumer_state(protocol::ByteView payload, std::uint16_t& usage);

/// Could U2 have sent this? A valid CRC alone does not mean it did.
///
/// U2 sends exactly one kind of message. Everything else in the enum travels
/// the other way, so a frame carrying one of those is U1's own transmission
/// returned to it - which is what a short between the outgoing and incoming
/// lines produces, complete with the CRC U1 computed itself. Treating that as
/// an answer would report a healthy link to a board that is not running, and
/// the keys it was holding would never be released.
bool is_endpoint_reply(protocol::SpiMessageType type);

}  // namespace duo_input::link
