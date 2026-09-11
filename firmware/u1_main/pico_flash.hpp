#pragma once

// The real flash behind the A/B store.
//
// Two things make writing flash on an RP2040 delicate. The code doing the
// writing normally executes from that same flash, so the SDK's routines run
// from RAM and interrupts must be off for the duration - a USB interrupt
// landing mid-erase would try to fetch code from a chip that is busy erasing
// itself. And the second core must not be executing from flash either.
//
// Erases take longer than USB suspend detection. Core 1 therefore enters a
// cooperative SRAM-only window instead of the SDK lockout, and the PIO USB
// build continues emitting SOF while XIP is unavailable.

#include <cstdint>

#include "storage/ab_store.hpp"

namespace duo_input::u1 {

/// Publish that Core 0 is about to launch the second core.
///
/// Core 0 owns both this publication and multicore_launch_core1(), so a false
/// read cannot race a newly launched XIP reader. Once published, every flash
/// operation must use the cooperative park window.
void set_core1_running(bool running);

/// Whether launch has been published. Before that point no second core can
/// start until Core 0 returns to the launch sequence.
bool core1_running();

/// Core 0: ask Core 1 to enter its SRAM-only flash window and wait for it.
bool begin_core1_flash_window();

/// Core 0: release the window and wait until Core 1 has resumed normal work.
void end_core1_flash_window();

/// Core 1: whether Core 0 is waiting for the safe loop.
bool core1_flash_window_requested();

/// Core 1: acknowledge and remain in SRAM until Core 0 releases the window.
void service_core1_flash_window();

/// Core 1: publish that normal timer/host work has been restored.
void finish_core1_flash_window();

class PicoFlash : public storage::FlashBackend {
public:
    bool erase(std::uint32_t offset, std::size_t size) override;
    bool program(std::uint32_t offset, const std::uint8_t* data, std::size_t size) override;
    bool read(std::uint32_t offset, std::uint8_t* data, std::size_t size) const override;

    /// Flash is memory-mapped, so the stored bytes can be read where they lie.
    const std::uint8_t* direct(std::uint32_t offset) const override;
};

}  // namespace duo_input::u1
