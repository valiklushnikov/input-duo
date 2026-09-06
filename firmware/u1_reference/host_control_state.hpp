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

//: Written over the whole request buffer before every on-wire attempt. The
//: buffer is static and reused, so after a replug it still holds the previous
//: attempt's bytes; a transfer that reports a length while writing nothing
//: would then compare clean and print MATCH - the exact failure under
//: investigation, reported as its own opposite. 0xA5 cannot begin a valid
//: report descriptor and is not a golden byte, so `prefix=A5A5...` reads
//: unambiguously as "nothing was received".
inline constexpr std::uint8_t kDescriptorPoisonByte = 0xA5;

template <std::size_t Size>
inline void poison_descriptor_buffer(std::array<std::uint8_t, Size>& buffer) {
    buffer.fill(kDescriptorPoisonByte);
}

struct DescriptorDiagnosticResult {
    enum class Kind : std::uint8_t { Match, Mismatch, Failure };
    Kind kind = Kind::Failure;
    std::uint32_t actual_len = 0;
    //: kReferenceNoDifference when every compared byte agreed.
    std::uint16_t first_difference = kReferenceNoDifference;
};

inline DescriptorDiagnosticResult descriptor_diagnostic_complete(
    bool transfer_succeeded, std::uint32_t actual_len, const std::uint8_t* bytes,
    std::size_t capacity) {
    DescriptorDiagnosticResult result{};
    result.actual_len = actual_len;
    if (!transfer_succeeded || bytes == nullptr || actual_len > capacity) {
        return result;
    }
    // Compare for real, however short the completion was. Reporting
    // min(actual_len, 64) as a first difference without looking at a byte
    // asserts that those bytes were golden, which is the one thing this
    // measurement exists to find out.
    const std::size_t compared = static_cast<std::size_t>(
        std::min<std::uint32_t>(actual_len, kAulaDescriptorPacketBytes));
    for (std::size_t index = 0; index < compared; ++index) {
        if (bytes[index] != kAulaDescriptorPrefix[index]) {
            result.kind = DescriptorDiagnosticResult::Kind::Mismatch;
            result.first_difference = static_cast<std::uint16_t>(index);
            return result;
        }
    }
    // Every compared byte agreed. Only a full packet is a match; anything
    // shorter is a mismatch with no first difference to name.
    result.kind = actual_len == kAulaDescriptorPacketBytes
                      ? DescriptorDiagnosticResult::Kind::Match
                      : DescriptorDiagnosticResult::Kind::Mismatch;
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

    // The token is mandatory and has no bypass. start() pre-increments from
    // zero, so zero is never a live token: a completion carrying it is one
    // whose user_data was lost on the way, and treating that as "no token
    // supplied" would let exactly that mistake pass unnoticed.
    Completion complete(std::uint8_t dev_addr,
                        bool transfer_succeeded,
                        std::uint32_t actual_len,
                        bool still_mounted,
                        std::size_t capacity,
                        std::uint32_t lifetime_token) {
        if (!active_ || dev_addr != dev_addr_ ||
            lifetime_token != lifetime_token_) {
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

    // One bounded line for a measurement that gave up before reaching the
    // wire. Silence there cannot be told apart from a board that was never
    // flashed, and this project has paid for that confusion before.
    void skipped(ReferenceDescriptorReason reason,
                 std::uint8_t dev_addr,
                 std::uint8_t instance) {
        ReferenceDescriptorDiagnostic skip{};
        skip.kind = ReferenceDescriptorDiagnosticKind::Skip;
        skip.reason = reason;
        skip.dev_addr = dev_addr;
        skip.instance = instance;
        reference_descriptor_diagnostic_push(skip);
    }

    bool abandon_if_unmounted(bool mounted) {
        const std::uint8_t dev_addr = transfer_.dev_addr();
        const std::uint8_t instance = transfer_.instance();
        if (!transfer_.abandon_if_unmounted(mounted)) {
            return false;
        }
        skipped(ReferenceDescriptorReason::Unmounted, dev_addr, instance);
        return true;
    }

    // Cancels application state before offering the UMOUNT record to the
    // bounded callback queue. Even if capture is refused, a replug cannot
    // inherit the old offer, interface ownership, or transfer generation.
    bool capture_unmount(std::uint8_t dev_addr,
                         std::uint8_t instance,
                         std::uint32_t now_us) {
        const ReferenceCallbackRecord unmount =
            reference_make_unmount(dev_addr, instance, now_us);
        if (transfer_.abandon(dev_addr, instance)) {
            skipped(ReferenceDescriptorReason::Unmounted, dev_addr, instance);
        }
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
        // The request buffer's leading bytes, on every outcome and whatever
        // actual_len says. Bounded by the buffer, never by the reported
        // length: a length of zero is precisely the case where the bytes
        // matter most, and a null buffer copies nothing.
        if (bytes != nullptr) {
            diagnostic.prefix_size = static_cast<std::uint8_t>(
                std::min<std::size_t>(capacity, diagnostic.prefix.size()));
            std::copy_n(bytes, diagnostic.prefix_size,
                        diagnostic.prefix.begin());
        }

        if (completion == DescriptorTransferState::Completion::Failure) {
            diagnostic.kind = ReferenceDescriptorDiagnosticKind::Failure;
            diagnostic.reason =
                !transfer_succeeded ? ReferenceDescriptorReason::Transfer
                : !still_mounted    ? ReferenceDescriptorReason::Gone
                                    : ReferenceDescriptorReason::TooLong;
            reference_descriptor_diagnostic_push(diagnostic);
            return completion;
        }

        const DescriptorDiagnosticResult result = descriptor_diagnostic_complete(
            transfer_succeeded, actual_len, bytes, capacity);
        if (result.kind == DescriptorDiagnosticResult::Kind::Failure) {
            // An internal fault, not a device measurement. Reporting it as a
            // mismatch would put a firmware bug on the device's record.
            diagnostic.kind = ReferenceDescriptorDiagnosticKind::Failure;
            diagnostic.reason = ReferenceDescriptorReason::NoBuffer;
            reference_descriptor_diagnostic_push(diagnostic);
            return DescriptorTransferState::Completion::Failure;
        }
        diagnostic.first_difference = result.first_difference;
        diagnostic.kind =
            result.kind == DescriptorDiagnosticResult::Kind::Match
                ? ReferenceDescriptorDiagnosticKind::Match
                : ReferenceDescriptorDiagnosticKind::Mismatch;
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
