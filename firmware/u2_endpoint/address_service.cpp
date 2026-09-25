#include "address_service.hpp"

#include <cstring>

namespace duo_input::u2 {
namespace {

using protocol::CdcError;
using protocol::CdcMessageType;

constexpr std::uint8_t kFrameDelimiter = 0;

constexpr std::uint32_t kCapabilities =
    static_cast<std::uint32_t>(protocol::Capability::ADDRESS_EXCHANGE);

void put_u32(std::uint8_t* out, std::uint32_t value) {
    out[0] = static_cast<std::uint8_t>(value);
    out[1] = static_cast<std::uint8_t>(value >> 8);
    out[2] = static_cast<std::uint8_t>(value >> 16);
    out[3] = static_cast<std::uint8_t>(value >> 24);
}

}  // namespace

void AddressService::on_cdc_bytes(const std::uint8_t* data, std::size_t size) {
    for (std::size_t index = 0; index < size; ++index) {
        const std::uint8_t byte = data[index];
        if (byte == kFrameDelimiter) {
            if (pending_size_ > 0) {
                pending_[pending_size_++] = byte;
                handle_frame(pending_, pending_size_);
            }
            pending_size_ = 0;
            continue;
        }
        if (pending_size_ + 1 < sizeof(pending_)) {
            pending_[pending_size_++] = byte;
        } else {
            // Beyond any legal frame: once pending_ is full it freezes right
            // here and stops accepting bytes, so nothing marks this run as
            // overflowed any more - there is nothing left for that mark to
            // change. The frozen prefix is what reaches handle_frame() at the
            // next delimiter, decoded and rejected there (a truncated frame
            // fails length/CRC validation), so any real frame embedded inside
            // this overlong run is lost by design: this buffer holds no
            // complete copy of it to recover.
        }
    }
}

void AddressService::on_disconnect() {
    pending_size_ = 0;
    negotiated_ = false;
    capabilities_ = 0;
}

void AddressService::handle_frame(const std::uint8_t* wire, std::size_t size) {
    protocol::DecodeResult result;
    if (!protocol::decode_cdc_frame(protocol::ByteView{wire, size},
                                    protocol::MutableByteView{decoded_, sizeof(decoded_)},
                                    result)) {
        return;
    }
    const protocol::CdcFrame& frame = result.cdc;

    switch (frame.type) {
        case CdcMessageType::HELLO: {
            payload_[0] = static_cast<std::uint8_t>(CdcError::Ok);
            std::uint32_t granted = 0;
            if (frame.payload.size == 4) {
                const std::uint8_t* p = frame.payload.data;
                const std::uint32_t requested =
                    static_cast<std::uint32_t>(p[0]) | (static_cast<std::uint32_t>(p[1]) << 8) |
                    (static_cast<std::uint32_t>(p[2]) << 16) |
                    (static_cast<std::uint32_t>(p[3]) << 24);
                granted = requested & kCapabilities;
                negotiated_ = true;
                capabilities_ = granted;
            } else {
                payload_[0] = static_cast<std::uint8_t>(CdcError::InvalidRequest);
            }
            // The same 44 bytes U1 sends, so the configurator's parser reads
            // it unchanged: no configuration, so no generation, no profile and
            // an all-zero digest.
            payload_[1] = protocol::PROTOCOL_VERSION_MAJOR;
            payload_[2] = protocol::PROTOCOL_VERSION_MINOR;
            put_u32(payload_ + 3, granted);
            put_u32(payload_ + 7, 0);
            payload_[11] = 0;
            std::memset(payload_ + 12, 0, 32);
            reply(CdcMessageType::DEVICE_INFO, frame.sequence, payload_, 44);
            return;
        }
        case CdcMessageType::PING: {
            payload_[0] = static_cast<std::uint8_t>(CdcError::Ok);
            std::size_t size_out = 1;
            if (frame.payload.size < kMaxPayload) {
                std::memcpy(payload_ + 1, frame.payload.data, frame.payload.size);
                size_out += frame.payload.size;
            }
            reply(frame.type, frame.sequence, payload_, size_out);
            return;
        }
        case CdcMessageType::EXCHANGE_ADDRESSES: {
            if (!negotiated_) {
                payload_[0] = static_cast<std::uint8_t>(CdcError::BadState);
                reply(frame.type, frame.sequence, payload_, 1);
                return;
            }
            // capabilities_ is already the HELLO-time intersection with
            // kCapabilities (see below), so this is exactly "did the host ask
            // for ADDRESS_EXCHANGE" - masking again here makes that explicit
            // rather than relying on the caller to remember.
            if ((capabilities_ & kCapabilities) == 0) {
                payload_[0] = static_cast<std::uint8_t>(CdcError::UnsupportedCapability);
                reply(frame.type, frame.sequence, payload_, 1);
                return;
            }
            link::HostAddresses local;
            if (!link::decode_host_addresses(frame.payload, local)) {
                payload_[0] = static_cast<std::uint8_t>(CdcError::InvalidRequest);
                reply(frame.type, frame.sequence, payload_, 1);
                return;
            }
            book_.set_local(local);
            payload_[0] = static_cast<std::uint8_t>(CdcError::Ok);
            std::size_t written = 0;
            link::encode_host_addresses(
                book_.peer(), protocol::MutableByteView{payload_ + 1, kMaxPayload - 1}, written);
            reply(frame.type, frame.sequence, payload_, 1 + written);
            return;
        }
        default:
            payload_[0] = static_cast<std::uint8_t>(CdcError::UnsupportedCapability);
            reply(frame.type, frame.sequence, payload_, 1);
            return;
    }
}

void AddressService::reply(CdcMessageType type, std::uint16_t sequence,
                           const std::uint8_t* payload, std::size_t size) {
    protocol::CdcFrame frame;
    frame.type = type;
    frame.sequence = sequence;
    frame.payload = protocol::ByteView{payload, size};
    std::size_t written = 0;
    if (protocol::encode_cdc_frame(frame, protocol::MutableByteView{out_, sizeof(out_)},
                                   protocol::MutableByteView{scratch_, sizeof(scratch_)},
                                   written)) {
        sink_.write(out_, written);
    }
}

}  // namespace duo_input::u2
