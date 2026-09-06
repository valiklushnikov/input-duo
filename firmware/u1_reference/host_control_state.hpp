#pragma once

#include <cstddef>
#include <cstdint>

#include "source_adapter.hpp"

namespace duo_input::u1::reference {

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
                        std::size_t capacity) {
        if (!active_ || dev_addr != dev_addr_) {
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

private:
    bool active_ = false;
    std::uint8_t dev_addr_ = 0;
    std::uint8_t instance_ = 0;
};

}  // namespace duo_input::u1::reference
