#pragma once

// The real flash behind the A/B store.
//
// Two things make writing flash on an RP2040 delicate. The code doing the
// writing normally executes from that same flash, so the SDK's routines run
// from RAM and interrupts must be off for the duration - a USB interrupt
// landing mid-erase would try to fetch code from a chip that is busy erasing
// itself. And the second core must not be executing from flash either.
//
// Erases take milliseconds, during which USB is not serviced. That is
// survivable because a host tolerates a device that is briefly quiet, and
// because a configuration write is a deliberate, occasional act.

#include <cstdint>

#include "storage/ab_store.hpp"

namespace duo_input::u1 {

class PicoFlash : public storage::FlashBackend {
public:
    bool erase(std::uint32_t offset, std::size_t size) override;
    bool program(std::uint32_t offset, const std::uint8_t* data, std::size_t size) override;
    bool read(std::uint32_t offset, std::uint8_t* data, std::size_t size) const override;

    /// Flash is memory-mapped, so the stored bytes can be read where they lie.
    const std::uint8_t* direct(std::uint32_t offset) const override;
};

}  // namespace duo_input::u1
