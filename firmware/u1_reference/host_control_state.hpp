#pragma once

#include <cstddef>
#include <cstdint>
#include <array>
#include <algorithm>

#include "source_adapter.hpp"

namespace duo_input::u1::reference {

inline constexpr std::size_t kAulaDescriptorPacketBytes = 64;
// Measurement oracle only: golden bytes 0..63 from the checked-in Aula
// descriptor vector. This never reaches the HID parser or routing path.
inline constexpr std::array<std::uint8_t, kAulaDescriptorPacketBytes>
    kAulaDescriptorPrefix = {
        0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x05, 0x08, 0x19, 0x01,
        0x29, 0x03, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x03,
        0x91, 0x02, 0x95, 0x05, 0x91, 0x01, 0x05, 0x07, 0x19, 0xE0,
        0x29, 0xE7, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x08,
        0x81, 0x02, 0x75, 0x08, 0x95, 0x01, 0x81, 0x01, 0x05, 0x07,
        0x19, 0x00, 0x2A, 0xFF, 0x00, 0x15, 0x00, 0x26, 0xFF, 0x00,
        0x75, 0x08, 0x95, 0x05,
    };

struct DescriptorDiagnosticResult {
    enum class Kind : std::uint8_t { Match, Mismatch, Failure };
    Kind kind = Kind::Failure;
    std::uint32_t actual_len = 0;
    std::uint16_t first_difference = 0;
};

inline DescriptorDiagnosticResult descriptor_diagnostic_complete(
    bool transfer_succeeded, std::uint32_t actual_len, const std::uint8_t* bytes,
    std::size_t capacity) {
    DescriptorDiagnosticResult result{};
    result.actual_len = actual_len;
    if (!transfer_succeeded || bytes == nullptr || actual_len > capacity) {
        return result;
    }
    result.kind = DescriptorDiagnosticResult::Kind::Mismatch;
    result.first_difference = static_cast<std::uint16_t>(
        std::min<std::uint32_t>(actual_len, kAulaDescriptorPacketBytes));
    if (actual_len != kAulaDescriptorPacketBytes) {
        return result;
    }
    for (std::size_t index = 0; index < kAulaDescriptorPrefix.size(); ++index) {
        if (bytes[index] != kAulaDescriptorPrefix[index]) {
            result.first_difference = static_cast<std::uint16_t>(index);
            return result;
        }
    }
    result.kind = DescriptorDiagnosticResult::Kind::Match;
    return result;
}

class ProtocolRequestHold {
public:
    enum class Action : std::uint8_t { None, Offer, Dropped };

    bool hold(const ReferenceSourceAdapter::ProtocolRequest& request) {
        if (active_) {
            return false;
        }
        request_ = request;
        active_ = true;
        return true;
    }

    Action action(bool mounted) {
        if (!active_) {
            return Action::None;
        }
        if (!mounted) {
            active_ = false;
            return Action::Dropped;
        }
        return Action::Offer;
    }

    void accepted() { active_ = false; }
    bool active() const { return active_; }
    const ReferenceSourceAdapter::ProtocolRequest& request() const {
        return request_;
    }

private:
    bool active_ = false;
    ReferenceSourceAdapter::ProtocolRequest request_{};
};

class DescriptorTransferState {
public:
    enum class Completion : std::uint8_t { Ignored, Success, Failure };

    bool start(std::uint8_t dev_addr, std::uint8_t instance) {
        if (active_) {
            return false;
        }
        active_ = true;
        dev_addr_ = dev_addr;
        instance_ = instance;
        ++lifetime_token_;
        return true;
    }

    bool abandon_if_unmounted(bool mounted) {
        if (!active_ || mounted) {
            return false;
        }
        active_ = false;
        return true;
    }

    bool abandon(std::uint8_t dev_addr, std::uint8_t instance) {
        if (!active_ || dev_addr != dev_addr_ || instance != instance_) {
            return false;
        }
        active_ = false;
        return true;
    }

    Completion complete(std::uint8_t dev_addr,
                        bool transfer_succeeded,
                        std::uint32_t actual_len,
                        bool still_mounted,
                        std::size_t capacity,
                        std::uint32_t lifetime_token = 0) {
        if (!active_ || dev_addr != dev_addr_ ||
            (lifetime_token != 0 && lifetime_token != lifetime_token_)) {
            return Completion::Ignored;
        }
        active_ = false;
        return transfer_succeeded && actual_len != 0 &&
                       actual_len <= capacity && still_mounted
                   ? Completion::Success
                   : Completion::Failure;
    }

    void refused() { active_ = false; }
    bool active() const { return active_; }
    std::uint8_t dev_addr() const { return dev_addr_; }
    std::uint8_t instance() const { return instance_; }
    std::uint32_t lifetime_token() const { return lifetime_token_; }

private:
    bool active_ = false;
    std::uint8_t dev_addr_ = 0;
    std::uint8_t instance_ = 0;
    std::uint32_t lifetime_token_ = 0;
};

}  // namespace duo_input::u1::reference
