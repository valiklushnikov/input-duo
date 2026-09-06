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
        return transfer_succeeded && actual_len <= capacity && still_mounted
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

// The host callback lifecycle for the one-packet descriptor measurement.
// TinyUSB's wrapper delegates here so native tests exercise the same
// cancellation, stale-completion, and diagnostic-queue path as the firmware.
// All work is fixed-size and non-blocking; CDC formatting stays on Core 0.
class DescriptorDiagnosticCoordinator {
public:
    explicit DescriptorDiagnosticCoordinator(ReferenceSourceAdapter& adapter)
        : adapter_(adapter) {}

    bool start(std::uint8_t dev_addr, std::uint8_t instance) {
        return transfer_.start(dev_addr, instance);
    }

    void request_accepted() {
        adapter_.descriptor_request_accepted();
        ReferenceDescriptorDiagnostic started{};
        started.kind = ReferenceDescriptorDiagnosticKind::Start;
        started.dev_addr = transfer_.dev_addr();
        started.instance = transfer_.instance();
        reference_descriptor_diagnostic_push(started);
    }

    void refused() { transfer_.refused(); }

    bool abandon_if_unmounted(bool mounted) {
        return transfer_.abandon_if_unmounted(mounted);
    }

    // Cancels application state before offering the UMOUNT record to the
    // bounded callback queue. Even if capture is refused, a replug cannot
    // inherit the old offer, interface ownership, or transfer generation.
    bool capture_unmount(std::uint8_t dev_addr,
                         std::uint8_t instance,
                         std::uint32_t now_us) {
        const ReferenceCallbackRecord unmount =
            reference_make_unmount(dev_addr, instance, now_us);
        transfer_.abandon(dev_addr, instance);
        adapter_.consume(unmount, now_us);
        return reference_capture(unmount);
    }

    DescriptorTransferState::Completion complete(
        std::uint8_t dev_addr,
        bool transfer_succeeded,
        std::uint32_t actual_len,
        bool still_mounted,
        const std::uint8_t* bytes,
        std::size_t capacity,
        std::uint32_t lifetime_token) {
        const std::uint8_t diagnostic_dev_addr = transfer_.dev_addr();
        const std::uint8_t diagnostic_instance = transfer_.instance();
        const DescriptorTransferState::Completion completion =
            transfer_.complete(dev_addr, transfer_succeeded, actual_len,
                               still_mounted, capacity, lifetime_token);
        if (completion == DescriptorTransferState::Completion::Ignored) {
            return completion;
        }

        ReferenceDescriptorDiagnostic diagnostic{};
        diagnostic.dev_addr = diagnostic_dev_addr;
        diagnostic.instance = diagnostic_instance;
        diagnostic.actual_len = static_cast<std::uint16_t>(actual_len);
        if (completion == DescriptorTransferState::Completion::Failure) {
            diagnostic.kind = ReferenceDescriptorDiagnosticKind::Failure;
            reference_descriptor_diagnostic_push(diagnostic);
            return completion;
        }

        const DescriptorDiagnosticResult result = descriptor_diagnostic_complete(
            transfer_succeeded, actual_len, bytes, capacity);
        diagnostic.first_difference = result.first_difference;
        diagnostic.kind =
            result.kind == DescriptorDiagnosticResult::Kind::Match
                ? ReferenceDescriptorDiagnosticKind::Match
                : ReferenceDescriptorDiagnosticKind::Mismatch;
        diagnostic.prefix_size = static_cast<std::uint8_t>(
            std::min<std::size_t>(actual_len, diagnostic.prefix.size()));
        std::copy_n(bytes, diagnostic.prefix_size, diagnostic.prefix.begin());
        reference_descriptor_diagnostic_push(diagnostic);
        return completion;
    }

    bool active() const { return transfer_.active(); }
    std::uint8_t dev_addr() const { return transfer_.dev_addr(); }
    std::uint8_t instance() const { return transfer_.instance(); }
    std::uint32_t lifetime_token() const {
        return transfer_.lifetime_token();
    }

private:
    ReferenceSourceAdapter& adapter_;
    DescriptorTransferState transfer_{};
};

}  // namespace duo_input::u1::reference
