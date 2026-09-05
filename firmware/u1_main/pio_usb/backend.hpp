#pragma once

// U1's native USB host: one XL334P4 hub on RHPort 1, replacing both CH375
// channels. TinyUSB's device stack keeps RHPort 0 exactly as it always has -
// see tusb_config.h - and this never touches tud_task(), the CDC link to the
// configurator, or anything USB-descriptor-shaped on Core 0's side.
//
// It is not silent on Core 0 otherwise, though: begin() raises clk_sys to
// 120 MHz, and on this SDK (PICO_CLOCK_ADJUST_PERI_CLOCK_WITH_SYS_CLOCK is 0
// and nothing here overrides it) that reparents clk_peri from clk_sys onto
// PLL_USB at 48 MHz as a side effect - clk_peri is not this class's own
// clock, it belongs to every PL022/UART/etc. peripheral on the chip, which on
// this board means the SPI link Core 0 uses to talk to U2. See
// SpiMaster::refresh_baudrate() and clock_settled() below: Core 0 must not
// run a real SPI transfer until begin() has finished, or it does so at
// whatever SCK the stale prescalers produce against the new clock.
//
// This class is Core 1's only door into the host stack, the same shape
// Ch375SourceAdapter is Core 1's only door into a CH375 channel: begin()
// once, task() every pass, take_event() drained in a loop until it returns
// false. TinyUSB's own host callbacks - tinyusb_host_callbacks.cpp - run
// inside that task() call and copy bounded data into storage this class
// owns; nothing downstream ever sees a pointer into memory a callback still
// owns.
//
// The callback-facing registry is fixed-capacity. It assigns boot-protocol
// roles, owns one receive at a time per mounted HID interface, and surfaces
// Ready, Report, Detached and host/capture faults through take_event() in the
// shape Ch375SourceAdapter::convert() already produces from CH375's side.
//
// Recovery after a detach, a stall or a reconnect lives in that registry -
// bounded retry with backoff, generation counters, ordered teardown across a
// whole hub - but this file DRIVES it: task() hands the registry the pass's
// own clock reading and calls retry_pending_arms() every pass, right after
// process_pending(), so a backoff armed in one pass is retried in a later one
// without anything blocking in between. Nothing here decides what recovery
// means; it decides when the registry gets a chance to do it.

#include <atomic>
#include <cstdint>

#include "input/source.hpp"
#include "pio_usb/device_registry.hpp"

namespace duo_input::u1::pio_usb {

/// One-way Core 1 -> Core 0 publication for the shared clock change.
class ClockChangeBarrier {
public:
    void publish_settled() noexcept {
        settled_.store(1u, std::memory_order_release);
    }

    bool settled() const noexcept {
        return settled_.load(std::memory_order_acquire) != 0;
    }

private:
    std::atomic<std::uint32_t> settled_{0};
};

/// Keeps Core 0 non-blocking while ordering baud restore before any transfer.
class LinkStartupGate {
public:
    template <typename RestoreBaud, typename Transfer>
    bool run_if_ready(bool clock_settled, RestoreBaud&& restore_baud, Transfer&& transfer) {
        if (!clock_settled) {
            return false;
        }
        if (!baud_restored_) {
            restore_baud();
            baud_restored_ = true;
        }
        transfer();
        return true;
    }

private:
    bool baud_restored_ = false;
};

class PioUsbBackend {
public:
    /// Bring the host stack up. Call once, from Core 1, before task().
    ///
    /// Sets the RP2040 system clock to 120 MHz - Pico-PIO-USB's bit timing
    /// requires it, and TinyUSB's device side on RHPort 0 does not care,
    /// since it runs off the chip's dedicated USB PLL rather than clk_sys -
    /// then configures Pico-PIO-USB's D+ pin as GP0 (D- is the adjacent GP1;
    /// Pico-PIO-USB derives it from D+ itself) and calls tuh_init on RHPort
    /// 1. clock_settled() reads true once this returns; nothing else about
    /// this class touches Core 0, but the clock change itself does - see the
    /// file comment above.
    void begin();

    /// Whether begin() has finished changing the shared system clock.
    ///
    /// Core 0 must not run a real transfer over the SPI link to U2 before
    /// this is true; see the file comment above and
    /// SpiMaster::refresh_baudrate(). The acquire pairs with begin()'s
    /// release publication, so observing true happens after the clock change.
    bool clock_settled() const;

    /// Service the host stack for one pass. Called every time round Core 1's
    /// loop, unconditionally, the same as Ch375Device::tick.
    void task(std::uint32_t now_us);

    /// Take one queued event out of backend-owned storage, in the shape
    /// Ch375SourceAdapter::convert() already produces from CH375's side.
    /// Returns false once nothing is queued; ``event`` and ``identity`` are
    /// unchanged when it does.
    bool take_event(input::SourceEvent& event, input::SourceIdentity& identity);

    /// Which of this board's two logical ports a resolved DeviceKind
    /// belongs on: 0 for the keyboard, 1 for the mouse. -1 for Unknown,
    /// which nothing routes - the same thing CH375's own per-channel
    /// adapters already do by construction.
    static int logical_port(input::DeviceKind kind);

private:
    ClockChangeBarrier clock_change_;
    DeviceRegistry registry_;
    bool host_ready_ = false;
};

}  // namespace duo_input::u1::pio_usb
