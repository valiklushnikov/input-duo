#include "config_service.hpp"

#include <cstring>

#include "config/validator.hpp"
#include "crypto/sha256.hpp"

namespace duo_input::u1 {
namespace {

using protocol::CdcFrame;
using protocol::CdcMessageType;
using protocol::ProtocolLimits;

constexpr std::uint8_t kFrameDelimiter = 0;

/// Everything this build can actually do.
///
/// Sent in DEVICE_INFO and masked with what the host asked for, so both ends
/// agree on the subset in use. CAPTURE is here now that Core 1 has peripherals
/// to capture from; TEST_MACRO is still absent, because nothing yet runs a
/// macro on the host's say-so and a device that advertised it would leave the
/// configurator waiting for something that is not coming. FACTORY_RESET is
/// present, because the device can do it - it simply insists on someone being
/// at the device.
constexpr std::uint32_t device_capabilities() {
    return static_cast<std::uint32_t>(protocol::Capability::CAPTURE) |
           static_cast<std::uint32_t>(protocol::Capability::KEYBOARD_HID) |
           static_cast<std::uint32_t>(protocol::Capability::MOUSE_HID) |
           static_cast<std::uint32_t>(protocol::Capability::CONSUMER_HID) |
           static_cast<std::uint32_t>(protocol::Capability::CONFIG_READ) |
           static_cast<std::uint32_t>(protocol::Capability::CONFIG_WRITE) |
           static_cast<std::uint32_t>(protocol::Capability::DIAGNOSTICS) |
           static_cast<std::uint32_t>(protocol::Capability::ROUTE_CONTROL) |
           static_cast<std::uint32_t>(protocol::Capability::SPI_ENDPOINT) |
           static_cast<std::uint32_t>(protocol::Capability::FACTORY_RESET);
}

void put_u32(std::uint8_t* out, std::uint32_t value) {
    out[0] = static_cast<std::uint8_t>(value);
    out[1] = static_cast<std::uint8_t>(value >> 8);
    out[2] = static_cast<std::uint8_t>(value >> 16);
    out[3] = static_cast<std::uint8_t>(value >> 24);
}

/// One histogram on the wire: how many, the worst, then every bucket.
///
/// The count is here so a reader can tell "nothing was measured" from "nothing
/// was slow" - a device that saw no input at all would otherwise look like the
/// fastest device ever built.
std::size_t write_latency(std::uint8_t* out,
                          const diagnostics::LatencyHistogram& histogram) {
    put_u32(out, histogram.count());
    put_u32(out + 4, histogram.max_us());
    for (std::size_t index = 0; index < diagnostics::kLatencyBucketCount; ++index) {
        put_u32(out + 8 + 4 * index, histogram.bucket(index));
    }
    return 8 + 4 * diagnostics::kLatencyBucketCount;
}

void put_u16(std::uint8_t* out, std::uint16_t value) {
    out[0] = static_cast<std::uint8_t>(value);
    out[1] = static_cast<std::uint8_t>(value >> 8);
}

/// One peripheral port on the wire.
std::size_t write_peripheral(std::uint8_t* out, const PeripheralPort& port) {
    out[0] = port.attached ? 1 : 0;
    out[1] = port.ready ? 1 : 0;
    out[2] = port.kind;
    put_u16(out + 3, port.vendor_id);
    put_u16(out + 5, port.product_id);
    out[7] = port.buttons;
    put_u16(out + 8, port.report_descriptor_bytes);
    std::memcpy(out + 10, port.descriptor_hash, sizeof(port.descriptor_hash));
    return kPeripheralPortBytes;
}

std::uint32_t take_u32(const std::uint8_t* data) {
    return static_cast<std::uint32_t>(data[0]) |
           (static_cast<std::uint32_t>(data[1]) << 8) |
           (static_cast<std::uint32_t>(data[2]) << 16) |
           (static_cast<std::uint32_t>(data[3]) << 24);
}

std::uint16_t take_u16(const std::uint8_t* data) {
    return static_cast<std::uint16_t>(data[0] | (data[1] << 8));
}

/// The exact payload length each request must carry, or -1 for variable.
int expected_request_size(CdcMessageType type) {
    switch (type) {
        case CdcMessageType::HELLO:
            return 4;
        case CdcMessageType::READ_CONFIG_CHUNK:
            return 6;
        case CdcMessageType::WRITE_BEGIN:
            return 36;
        case CdcMessageType::SET_ACTIVE_PROFILE:
            return 1;
        case CdcMessageType::TEST_MACRO:
            return 2;
        case CdcMessageType::WRITE_CHUNK:
        case CdcMessageType::PING:
            return -1;
        case CdcMessageType::GET_STATUS:
        case CdcMessageType::GET_ACTIVE_CONFIG_INFO:
        case CdcMessageType::READ_CONFIG_BEGIN:
        case CdcMessageType::WRITE_VERIFY:
        case CdcMessageType::WRITE_COMMIT:
        case CdcMessageType::WRITE_ABORT:
        case CdcMessageType::CAPTURE_BEGIN:
        case CdcMessageType::CAPTURE_END:
        case CdcMessageType::STOP_AND_RELEASE_ALL:
        case CdcMessageType::GET_DIAGNOSTICS:
        case CdcMessageType::FACTORY_RESET_ARM:
        case CdcMessageType::FACTORY_RESET_COMMIT:
            return 0;
        default:
            return -1;
    }
}

bool payload_shape_is_valid(const CdcFrame& frame) {
    if (frame.type == CdcMessageType::WRITE_CHUNK) {
        return frame.payload.size >= 5 &&
               frame.payload.size <= 4 + ProtocolLimits::CONFIG_CHUNK_MAX_BYTES;
    }
    const int expected = expected_request_size(frame.type);
    return expected < 0 || frame.payload.size == static_cast<std::size_t>(expected);
}

/// Which capability a request needs before it may be answered.
std::uint32_t required_capability(CdcMessageType type) {
    switch (type) {
        case CdcMessageType::GET_ACTIVE_CONFIG_INFO:
        case CdcMessageType::READ_CONFIG_BEGIN:
        case CdcMessageType::READ_CONFIG_CHUNK:
            return static_cast<std::uint32_t>(protocol::Capability::CONFIG_READ);
        case CdcMessageType::WRITE_BEGIN:
        case CdcMessageType::WRITE_CHUNK:
        case CdcMessageType::WRITE_VERIFY:
        case CdcMessageType::WRITE_COMMIT:
        case CdcMessageType::WRITE_ABORT:
            return static_cast<std::uint32_t>(protocol::Capability::CONFIG_WRITE);
        case CdcMessageType::SET_ACTIVE_PROFILE:
            return static_cast<std::uint32_t>(protocol::Capability::ROUTE_CONTROL);
        case CdcMessageType::CAPTURE_BEGIN:
        case CdcMessageType::CAPTURE_END:
            return static_cast<std::uint32_t>(protocol::Capability::CAPTURE);
        case CdcMessageType::TEST_MACRO:
            return static_cast<std::uint32_t>(protocol::Capability::TEST_MACRO);
        case CdcMessageType::GET_DIAGNOSTICS:
            return static_cast<std::uint32_t>(protocol::Capability::DIAGNOSTICS);
        case CdcMessageType::FACTORY_RESET_ARM:
        case CdcMessageType::FACTORY_RESET_COMMIT:
            return static_cast<std::uint32_t>(protocol::Capability::FACTORY_RESET);
        default:
            return 0;
    }
}

}  // namespace

// -------------------------------------------------------------- assembly

void ConfigService::on_cdc_bytes(const std::uint8_t* data, std::size_t size) {
    for (std::size_t index = 0; index < size; ++index) {
        const std::uint8_t byte = data[index];
        if (pending_size_ < sizeof(pending_)) {
            pending_[pending_size_++] = byte;
        } else if (byte != kFrameDelimiter) {
            // Beyond any legal frame. Keep discarding until the delimiter, so
            // the next real frame starts clean rather than inheriting this.
            continue;
        }

        if (byte == kFrameDelimiter) {
            if (pending_size_ > 1 && pending_size_ <= sizeof(pending_)) {
                handle_frame(pending_, pending_size_);
            }
            pending_size_ = 0;
        }
    }
}

void ConfigService::on_disconnect() {
    pending_size_ = 0;
    last_request_size_ = 0;
    last_response_size_ = 0;
    have_sequence_ = false;
    negotiated_ = false;
    negotiated_capabilities_ = 0;
    if (capture_active_) {
        // A configurator that crashed mid-question would otherwise leave a
        // keyboard silently eating its own input until the timeout runs out.
        capture_active_ = false;
        capture_request_ = CaptureRequest::Cancel;
    }
    if (store_.staging()) {
        // The slot has no header, so it is already nothing. The counter is the
        // part worth keeping: a host that keeps vanishing mid-write is a fact
        // about the cable, and the operator should be able to see it.
        store_.abort();
        ++diagnostics_.aborted_staging;
    }
    ++diagnostics_.disconnect;
}

void ConfigService::handle_frame(const std::uint8_t* wire, std::size_t size) {
    protocol::DecodeResult result;
    // decoded_, not a local: see the comment on it. frame.payload points into
    // this buffer for the whole of dispatch below.
    if (!protocol::decode_cdc_frame(protocol::ByteView{wire, size},
                                    protocol::MutableByteView{decoded_, sizeof(decoded_)},
                                    result)) {
        // Damaged on the wire. There is nothing to answer, and answering the
        // sequence we guessed at would be worse than silence.
        ++diagnostics_.bad_crc;
        return;
    }

    const CdcFrame& frame = result.cdc;

    // A byte-identical repeat means the host never saw the reply, so it gets
    // the same reply rather than having the request run twice - re-running a
    // WRITE_BEGIN would erase a slot for the second time.
    //
    // A handshake is exempt. A new session's HELLO is byte-identical to the
    // previous session's, so treating it as a repeat would serve a cached
    // reply and skip starting the session over - leaving the sequence count,
    // the negotiated capabilities and any physical confirmation belonging to
    // whoever was connected before.
    if (frame.type != CdcMessageType::HELLO && size == last_request_size_ &&
        std::memcmp(wire, last_request_, size) == 0) {
        sink_.write(last_response_, last_response_size_);
        return;
    }

    std::memcpy(last_request_, wire, size);
    last_request_size_ = size;

    // A handshake starts a session, so it is accepted at whatever sequence it
    // carries and resets the count. A configurator that closed the port and
    // opened it again begins at zero, having no memory of the last session
    // either; refusing that left a real board permanently unreachable to the
    // very program meant to configure it.
    if (frame.type == CdcMessageType::HELLO) {
        have_sequence_ = false;
        factory_confirmed_ = false;
        factory_armed_ = false;
        if (capture_active_ || capture_request_ == CaptureRequest::Begin) {
            // HELLO starts a new owner of the CDC session. A question left by
            // the previous owner must not keep swallowing this one's keys.
            capture_active_ = false;
            capture_request_ = CaptureRequest::Cancel;
        }
        if (store_.staging()) {
            // Closing a serial port does not unmount USB, so a configurator
            // that quit mid-write leaves the transaction open. A new session
            // cannot continue someone else's write, and leaving it running
            // means answering Busy to every later attempt until the device is
            // unplugged.
            store_.abort();
            ++diagnostics_.aborted_staging;
        }
    }

    if (have_sequence_ &&
        frame.sequence != static_cast<std::uint16_t>(last_sequence_ + 1)) {
        // A host that lost a reply and a host that is confused look identical
        // from here. Refusing costs one round trip; guessing could erase a
        // slot on the strength of a stale request.
        ++diagnostics_.bad_sequence;
        reply_error(frame, CdcError::BadSequence);
        last_sequence_ = frame.sequence;
        have_sequence_ = true;
        return;
    }

    dispatch(frame);
    last_sequence_ = frame.sequence;
    have_sequence_ = true;
}

// --------------------------------------------------------------- replies

void ConfigService::reply(CdcMessageType type, std::uint16_t sequence,
                          const std::uint8_t* payload, std::size_t size) {
    CdcFrame frame;
    frame.type = type;
    frame.sequence = sequence;
    frame.payload = protocol::ByteView{payload, size};

    std::size_t written = 0;
    if (!protocol::encode_cdc_frame(
            frame, protocol::MutableByteView{last_response_, sizeof(last_response_)},
            protocol::MutableByteView{encode_scratch_, sizeof(encode_scratch_)}, written)) {
        last_response_size_ = 0;
        return;
    }
    last_response_size_ = written;
    sink_.write(last_response_, written);
}

std::size_t ConfigService::device_info_payload(CdcError error, std::uint32_t capabilities,
                                               std::uint8_t* out) const {
    const storage::ScanResult found = const_cast<storage::AbStore&>(store_).scan();
    out[0] = static_cast<std::uint8_t>(error);
    out[1] = protocol::PROTOCOL_VERSION_MAJOR;
    out[2] = protocol::PROTOCOL_VERSION_MINOR;
    put_u32(out + 3, capabilities);
    put_u32(out + 7, found.has_active ? found.active_slot().generation : 0);
    out[11] = active_profile_;
    if (found.has_active) {
        std::memcpy(out + 12, found.active_slot().digest, crypto::kSha256DigestSize);
    } else {
        std::memset(out + 12, 0, crypto::kSha256DigestSize);
    }
    return 12 + crypto::kSha256DigestSize;
}

std::size_t ConfigService::status_payload(CdcError error, std::uint8_t* out) const {
    out[0] = static_cast<std::uint8_t>(error);
    out[1] = active_profile_;
    out[2] = capture_active_ ? 1 : 0;
    out[3] = store_.staging() ? 1 : 0;
    // release_all_count, which is what compatibility.md, the emulator and the
    // host's DeviceStatus all say this field is. aborted_staging used to sit
    // here: the same width, so nothing anywhere errored - the host simply read
    // a different number under the right name.
    put_u32(out + 4, diagnostics_.release_all_count);
    return 8;
}

std::size_t ConfigService::config_info_payload(CdcError error, std::uint8_t* out) {
    const storage::ScanResult found = store_.scan();
    out[0] = static_cast<std::uint8_t>(error);
    put_u32(out + 1, found.has_active ? found.active_slot().generation : 0);
    put_u32(out + 5, found.has_active ? found.active_slot().size : 0);
    if (found.has_active) {
        std::memcpy(out + 9, found.active_slot().digest, crypto::kSha256DigestSize);
    } else {
        std::memset(out + 9, 0, crypto::kSha256DigestSize);
    }
    return 9 + crypto::kSha256DigestSize;
}

#if DUO_SPI_DEBUG || DUO_CH375_PROBE
void ConfigService::set_link_debug(const std::uint8_t* bytes, std::size_t size) {
    link_debug_size_ = size < sizeof(link_debug_) ? size : sizeof(link_debug_);
    std::memcpy(link_debug_, bytes, link_debug_size_);
}
#endif

std::size_t ConfigService::diagnostics_payload(CdcError error, std::uint8_t* out) const {
#if DUO_SPI_DEBUG || DUO_CH375_PROBE
    out[0] = static_cast<std::uint8_t>(error);
    std::memcpy(out + 1, link_debug_, link_debug_size_);
    return 1 + link_debug_size_;
#else
    out[0] = static_cast<std::uint8_t>(error);
    put_u32(out + 1, diagnostics_.bad_crc);
    put_u32(out + 5, diagnostics_.disconnect);
    put_u32(out + 9, diagnostics_.timeout);
    put_u32(out + 13, diagnostics_.bad_sequence);
    put_u32(out + 17, diagnostics_.aborted_staging);

    // Appended after the counters the host already knew about, so an older
    // configurator reading the first 21 bytes still reads them correctly.
    out[21] = link_state_.answered ? 1 : 0;
    out[22] = link_state_.mounted ? 1 : 0;
    put_u32(out + 23, link_state_.frames_sent);
    put_u32(out + 27, link_state_.crc_errors);
    put_u32(out + 31, link_state_.echoed_frames);
    out[35] = link_state_.endpoint_drops;
    out[36] = static_cast<std::uint8_t>(link_state_.endpoint_release_ms & 0xFF);
    out[37] = static_cast<std::uint8_t>(link_state_.endpoint_release_ms >> 8);

    // Appended last, for the same reason the link state was appended before
    // it: a host that stops reading at byte 38 still reads everything it knew
    // about. This is the only outward sign that Core 1 had something to say
    // and the queue would not take it, which means a key press, a release or
    // a macro step never reached the computer it was meant for.
    put_u32(out + 38, dropped_commands_);

    // One more byte, appended for the same reason as everything above it: a
    // host that stops at byte 42 still reads what it knew. The count says
    // input was lost at some point since boot; this says the output runtime is
    // refusing commands *now*, which is the difference between a burst that
    // has passed and one that is still going on.
    out[42] = static_cast<std::uint8_t>(runtime_fault_);

    // And the latency block, appended for the same reason as everything above
    // it. It leads with its own bucket edges rather than relying on the host
    // holding a matching copy: two copies of eight numbers agree right up to
    // the moment somebody edits one of them, and the failure would be a report
    // that quietly attributes samples to the wrong bucket.
    std::size_t at = 43;
    out[at++] = static_cast<std::uint8_t>(diagnostics::kLatencyBucketCount);
    for (std::size_t index = 0; index + 1 < diagnostics::kLatencyBucketCount; ++index) {
        put_u32(out + at, diagnostics::kLatencyBucketEdgesUs[index]);
        at += 4;
    }
    at += write_latency(out + at, keyboard_latency_);
    at += write_latency(out + at, mouse_latency_);

    // And what is on the two peripheral ports. Both always, empty or not: a
    // port with nothing on it is a fact about the run, and a reader inferring
    // it from a shorter reply would be inferring it from the same absence that
    // an older firmware produces.
    at += write_peripheral(out + at, keyboard_port_);
    at += write_peripheral(out + at, mouse_port_);
    return at;
#endif
}

void ConfigService::reply_error(const CdcFrame& frame, CdcError error) {
    std::uint8_t* const payload = error_payload_;
    std::size_t size = 1;
    payload[0] = static_cast<std::uint8_t>(error);
    CdcMessageType type = frame.type;

    // Several replies carry their fields whether or not the request succeeded,
    // so the host can always read them. Only the leading error byte changes.
    switch (frame.type) {
        case CdcMessageType::HELLO:
            type = CdcMessageType::DEVICE_INFO;
            size = device_info_payload(error, 0, payload);
            break;
        case CdcMessageType::GET_STATUS:
            size = status_payload(error, payload);
            break;
        case CdcMessageType::GET_ACTIVE_CONFIG_INFO:
        case CdcMessageType::READ_CONFIG_BEGIN:
            size = config_info_payload(error, payload);
            break;
        case CdcMessageType::GET_DIAGNOSTICS:
            size = diagnostics_payload(error, payload);
            break;
        case CdcMessageType::WRITE_CHUNK:
            put_u32(payload + 1, expected_offset_);
            size = 5;
            break;
        case CdcMessageType::READ_CONFIG_CHUNK:
            if (frame.payload.size >= 4) {
                std::memcpy(payload + 1, frame.payload.data, 4);
            } else {
                std::memset(payload + 1, 0, 4);
            }
            size = 5;
            break;
        case CdcMessageType::PING:
            if (frame.payload.size < ProtocolLimits::CDC_MAX_PAYLOAD) {
                std::memcpy(payload + 1, frame.payload.data, frame.payload.size);
                size = 1 + frame.payload.size;
            }
            break;
        default:
            break;
    }
    reply(type, frame.sequence, payload, size);
}

// -------------------------------------------------------------- dispatch

void ConfigService::dispatch(const CdcFrame& frame) {
    std::uint8_t* const payload = dispatch_payload_;

    if (frame.type == CdcMessageType::HELLO) {
        if (!payload_shape_is_valid(frame)) {
            reply_error(frame, CdcError::InvalidRequest);
            return;
        }
        negotiated_capabilities_ = take_u32(frame.payload.data) & device_capabilities();
        negotiated_ = true;
        const std::size_t size =
            device_info_payload(CdcError::Ok, negotiated_capabilities_, payload);
        reply(CdcMessageType::DEVICE_INFO, frame.sequence, payload, size);
        return;
    }

    // DEVICE_INFO and CAPTURE_EVENT travel the other way. A host sending one
    // is confused, and answering as if it were a request would encourage it.
    if (frame.type == CdcMessageType::DEVICE_INFO ||
        frame.type == CdcMessageType::CAPTURE_EVENT) {
        payload[0] = static_cast<std::uint8_t>(CdcError::InvalidRequest);
        reply(frame.type, frame.sequence, payload, 1);
        return;
    }

    if (!payload_shape_is_valid(frame)) {
        reply_error(frame, CdcError::InvalidRequest);
        return;
    }

    // PING and STOP_AND_RELEASE_ALL work before any negotiation. The first is
    // how a host checks the link is alive at all; the second must never depend
    // on the link being in a good mood, because it is the way out of a macro
    // that is holding keys down.
    const bool always_allowed = frame.type == CdcMessageType::PING ||
                                frame.type == CdcMessageType::STOP_AND_RELEASE_ALL;
    if (!always_allowed) {
        if (!negotiated_) {
            reply_error(frame, CdcError::BadState);
            return;
        }
        const std::uint32_t needed = required_capability(frame.type);
        if (needed != 0 && (negotiated_capabilities_ & needed) == 0) {
            reply_error(frame, CdcError::UnsupportedCapability);
            return;
        }
    }

    switch (frame.type) {
        case CdcMessageType::PING: {
            payload[0] = static_cast<std::uint8_t>(CdcError::Ok);
            std::memcpy(payload + 1, frame.payload.data, frame.payload.size);
            reply(frame.type, frame.sequence, payload, 1 + frame.payload.size);
            return;
        }
        case CdcMessageType::GET_STATUS: {
            const std::size_t size = status_payload(CdcError::Ok, payload);
            reply(frame.type, frame.sequence, payload, size);
            return;
        }
        case CdcMessageType::GET_ACTIVE_CONFIG_INFO:
        case CdcMessageType::READ_CONFIG_BEGIN: {
            const std::size_t size = config_info_payload(CdcError::Ok, payload);
            reply(frame.type, frame.sequence, payload, size);
            return;
        }
        case CdcMessageType::GET_DIAGNOSTICS: {
            const std::size_t size = diagnostics_payload(CdcError::Ok, payload);
            reply(frame.type, frame.sequence, payload, size);
            return;
        }
        case CdcMessageType::READ_CONFIG_CHUNK: {
            const std::uint32_t offset = take_u32(frame.payload.data);
            const std::uint16_t requested = take_u16(frame.payload.data + 4);
            if (requested > ProtocolLimits::CONFIG_CHUNK_MAX_BYTES) {
                reply_error(frame, CdcError::BadSize);
                return;
            }
            const storage::ScanResult found = store_.scan();
            if (!found.has_active) {
                reply_error(frame, CdcError::BadState);
                return;
            }
            if (offset > found.active_slot().size) {
                reply_error(frame, CdcError::BadChunk);
                return;
            }
            const std::uint32_t available = found.active_slot().size - offset;
            const std::uint32_t take = requested < available ? requested : available;
            payload[0] = static_cast<std::uint8_t>(CdcError::Ok);
            put_u32(payload + 1, offset);
            if (take != 0 &&
                store_.read_active(offset, payload + 5, take) != storage::StoreError::None) {
                reply_error(frame, CdcError::BadState);
                return;
            }
            reply(frame.type, frame.sequence, payload, 5 + take);
            return;
        }
        case CdcMessageType::WRITE_BEGIN: {
            if (store_.staging()) {
                reply_error(frame, CdcError::Busy);
                return;
            }
            const std::uint32_t size = take_u32(frame.payload.data);
            // The slot is larger than the protocol's maximum package, which is
            // not a licence to accept one: a package the configurator cannot
            // read back is worse than one the device refuses to take.
            if (size == 0 || size > ProtocolLimits::BINARY_CONFIG_MAX_BYTES) {
                reply_error(frame, CdcError::BadSize);
                return;
            }
            std::uint8_t digest[crypto::kSha256DigestSize];
            std::memcpy(digest, frame.payload.data + 4, sizeof(digest));
            if (store_.begin(size, digest) != storage::StoreError::None) {
                reply_error(frame, CdcError::BadState);
                return;
            }
            expected_offset_ = 0;
            payload[0] = static_cast<std::uint8_t>(CdcError::Ok);
            reply(frame.type, frame.sequence, payload, 1);
            return;
        }
        case CdcMessageType::WRITE_CHUNK: {
            if (!store_.staging()) {
                payload[0] = static_cast<std::uint8_t>(CdcError::BadState);
                put_u32(payload + 1, 0);
                reply(frame.type, frame.sequence, payload, 5);
                return;
            }
            const std::uint32_t offset = take_u32(frame.payload.data);
            const std::size_t length = frame.payload.size - 4;
            // Strictly in order. Out-of-order chunks are legal for the store
            // but not for this protocol: accepting them would leave the host
            // and the device disagreeing about what has arrived.
            const bool in_order = offset == expected_offset_;
            const storage::StoreError error =
                in_order ? store_.write_chunk(offset, frame.payload.data + 4, length)
                         : storage::StoreError::OutOfRange;
            if (error != storage::StoreError::None) {
                payload[0] = static_cast<std::uint8_t>(CdcError::BadChunk);
                put_u32(payload + 1, expected_offset_);
                reply(frame.type, frame.sequence, payload, 5);
                return;
            }
            expected_offset_ = offset + static_cast<std::uint32_t>(length);
            payload[0] = static_cast<std::uint8_t>(CdcError::Ok);
            put_u32(payload + 1, expected_offset_);
            reply(frame.type, frame.sequence, payload, 5);
            return;
        }
        case CdcMessageType::WRITE_VERIFY: {
            if (!store_.staging()) {
                reply_error(frame, CdcError::BadState);
                return;
            }
            if (expected_offset_ != store_.staging_size()) {
                reply_error(frame, CdcError::BadSize);
                return;
            }
            const storage::StoreError error = store_.verify();
            if (error == storage::StoreError::DigestMismatch) {
                store_.abort();
                ++diagnostics_.aborted_staging;
                reply_error(frame, CdcError::BadHash);
                return;
            }
            if (error != storage::StoreError::None) {
                reply_error(frame, CdcError::BadState);
                return;
            }
            // The bytes are intact; whether they are a configuration is a
            // separate question, and one the device must answer before it
            // agrees to run them.
            const protocol::ByteView view =
                store_.payload_view(store_.staging_slot(), store_.staging_size());
            if (view.data != nullptr && !config::validate_config(view)) {
                store_.abort();
                ++diagnostics_.aborted_staging;
                reply_error(frame, CdcError::InvalidConfig);
                return;
            }
            payload[0] = static_cast<std::uint8_t>(CdcError::Ok);
            reply(frame.type, frame.sequence, payload, 1);
            return;
        }
        case CdcMessageType::WRITE_COMMIT: {
            if (store_.commit() != storage::StoreError::None) {
                reply_error(frame, CdcError::BadState);
                return;
            }
            const storage::ScanResult found = store_.scan();
            const protocol::ByteView view =
                store_.payload_view(found.active, found.active_slot().size);
            const config::ValidationResult validated = config::validate_config(view);
            if (!validated || !runtime_.activate(view)) {
                // The new slot is durable, but the runtime still owns views of
                // the previous slot.  Do not acknowledge a state in which the
                // next WRITE_BEGIN may erase memory Core 1 is still reading.
                reply_error(frame, CdcError::BadState);
                return;
            }
            active_profile_ = validated.view().active_profile_id();
            payload[0] = static_cast<std::uint8_t>(CdcError::Ok);
            reply(frame.type, frame.sequence, payload, 1);
            return;
        }
        case CdcMessageType::WRITE_ABORT: {
            if (!store_.staging()) {
                reply_error(frame, CdcError::BadState);
                return;
            }
            store_.abort();
            ++diagnostics_.aborted_staging;
            payload[0] = static_cast<std::uint8_t>(CdcError::Ok);
            reply(frame.type, frame.sequence, payload, 1);
            return;
        }
        case CdcMessageType::SET_ACTIVE_PROFILE: {
            const storage::ScanResult found = store_.scan();
            if (!found.has_active) {
                reply_error(frame, CdcError::BadState);
                return;
            }
            const protocol::ByteView view =
                store_.payload_view(found.active, found.active_slot().size);
            bool known = false;
            if (view.data != nullptr) {
                const config::ValidationResult validated = config::validate_config(view);
                if (validated) {
                    for (std::size_t index = 0; index < validated.view().profile_count();
                         ++index) {
                        config::ProfileView profile;
                        if (validated.view().profile_at(index, profile) &&
                            profile.id() == frame.payload.data[0]) {
                            known = true;
                            break;
                        }
                    }
                }
            }
            if (!known) {
                reply_error(frame, CdcError::InvalidRequest);
                return;
            }
            // Asked for, not done. Core 1 has a macro to stop and held keys to
            // let go of before the bindings change underneath them, and it is
            // the one that reports back which profile is actually running.
            requested_profile_ = frame.payload.data[0];
            profile_requested_ = true;
            pending_profile_ = frame.payload.data[0];
            profile_confirmation_pending_ = true;
            payload[0] = static_cast<std::uint8_t>(CdcError::Ok);
            reply(frame.type, frame.sequence, payload, 1);
            return;
        }
        case CdcMessageType::CAPTURE_BEGIN: {
            if (capture_active_) {
                // One question at a time. Two captures running would answer
                // one of them with the other's keypress.
                reply_error(frame, CdcError::Busy);
                return;
            }
            capture_active_ = true;
            capture_request_ = CaptureRequest::Begin;
            payload[0] = static_cast<std::uint8_t>(CdcError::Ok);
            reply(frame.type, frame.sequence, payload, 1);
            return;
        }
        case CdcMessageType::CAPTURE_END: {
            if (!capture_active_) {
                reply_error(frame, CdcError::BadState);
                return;
            }
            capture_active_ = false;
            capture_request_ = CaptureRequest::Cancel;
            payload[0] = static_cast<std::uint8_t>(CdcError::Ok);
            reply(frame.type, frame.sequence, payload, 1);
            return;
        }
        case CdcMessageType::STOP_AND_RELEASE_ALL: {
            release_all_requested_ = true;
            ++diagnostics_.release_all_count;
            if (capture_active_ || capture_request_ == CaptureRequest::Begin) {
                // The way out of anything, including a question the operator
                // can no longer answer.
                capture_active_ = false;
                capture_request_ = CaptureRequest::Cancel;
            }
            if (store_.staging()) {
                // The way out of anything includes a write nobody is going to
                // finish. compatibility.md says this clears staging state and
                // the emulator has always done so; the firmware did not, so a
                // configurator that used the safety command to get unstuck was
                // still answered Busy by hardware and Ok by the emulator the
                // host's tests run against. Nothing is at risk: the staged
                // slot has no header, so it is already nothing.
                store_.abort();
                ++diagnostics_.aborted_staging;
            }
            payload[0] = static_cast<std::uint8_t>(CdcError::Ok);
            reply(frame.type, frame.sequence, payload, 1);
            return;
        }
        case CdcMessageType::FACTORY_RESET_ARM: {
            // Erasing the operator's work is not something a program alone may
            // do; somebody has to be at the device.
            if (!factory_confirmed_) {
                reply_error(frame, CdcError::PhysicalConfirmationRequired);
                return;
            }
            factory_armed_ = true;
            payload[0] = static_cast<std::uint8_t>(CdcError::Ok);
            reply(frame.type, frame.sequence, payload, 1);
            return;
        }
        case CdcMessageType::FACTORY_RESET_COMMIT: {
            if (!factory_confirmed_) {
                reply_error(frame, CdcError::PhysicalConfirmationRequired);
                return;
            }
            if (!factory_armed_) {
                reply_error(frame, CdcError::BadState);
                return;
            }
            if (capture_active_ || capture_request_ == CaptureRequest::Begin) {
                // Everything the capture was for is about to be erased, and
                // the emulator - which the configurator was written against -
                // ends the capture here too. Cancelled before the erase rather
                // than after it, so a reset that fails part way still leaves
                // the keyboard answering to its operator.
                capture_active_ = false;
                capture_request_ = CaptureRequest::Cancel;
            }
            if (!runtime_.clear()) {
                factory_confirmed_ = false;
                factory_armed_ = false;
                reply_error(frame, CdcError::BadState);
                return;
            }
            const storage::StoreError error = store_.erase_everything();
            // Spent either way: a failed erase does not leave a standing
            // permission to try again unattended.
            factory_confirmed_ = false;
            factory_armed_ = false;
            if (error != storage::StoreError::None) {
                reply_error(frame, CdcError::BadState);
                return;
            }
            active_profile_ = 1;
            payload[0] = static_cast<std::uint8_t>(CdcError::Ok);
            reply(frame.type, frame.sequence, payload, 1);
            return;
        }
        default:
            reply_error(frame, CdcError::InvalidRequest);
            return;
    }
}

bool ConfigService::take_release_all_request() {
    const bool requested = release_all_requested_;
    release_all_requested_ = false;
    return requested;
}

CaptureRequest ConfigService::take_capture_request() {
    const CaptureRequest requested = capture_request_;
    capture_request_ = CaptureRequest::None;
    return requested;
}

bool ConfigService::take_profile_request(std::uint8_t& profile) {
    if (!profile_requested_) {
        return false;
    }
    profile_requested_ = false;
    profile = requested_profile_;
    return true;
}

bool ConfigService::confirm_profile_applied(std::uint8_t profile) {
    if (!profile_confirmation_pending_ || profile != pending_profile_) {
        return false;
    }
    profile_confirmation_pending_ = false;
    active_profile_ = profile;
    return true;
}

void ConfigService::emit_capture_event(const mapping::CapturedTrigger& trigger) {
    if (!capture_active_) {
        return;
    }

    const bool keyboard = trigger.kind == config::TriggerKind::KEYBOARD_USAGE;
    const bool mouse = trigger.kind == config::TriggerKind::MOUSE_BUTTON;
    if ((!keyboard && !mouse) || trigger.code == 0 ||
        (mouse && (trigger.code > 5 || trigger.modifiers != 0))) {
        // The configurator would reject this payload. Keep the capture alive
        // so the operator can answer again instead of silently losing it.
        return;
    }
    capture_active_ = false;

    // Exactly three bytes, in the order the host unpacks them.
    std::uint8_t payload[3];
    payload[0] = static_cast<std::uint8_t>(trigger.kind);
    payload[1] = trigger.code;
    payload[2] = trigger.modifiers;

    // A session that has not seen a request yet has no count to continue, so
    // the device starts one - which is what a first request would have done.
    const std::uint16_t sequence =
        have_sequence_ ? static_cast<std::uint16_t>(last_sequence_ + 1) : 0;
    reply(CdcMessageType::CAPTURE_EVENT, sequence, payload, sizeof(payload));
    last_sequence_ = sequence;
    have_sequence_ = true;

    // The cached exchange no longer describes the last thing on the wire, and
    // serving it to a retry would answer a request with somebody else's reply.
    last_request_size_ = 0;
    last_response_size_ = 0;
}

}  // namespace duo_input::u1
