#pragma once

// U1's native USB host: one XL334P4 hub on RHPort 1, replacing both CH375
// channels. TinyUSB's device stack keeps RHPort 0 exactly as it always has -
// see tusb_config.h - so nothing here touches Core 0's tud_task(), the CDC
// link to the configurator, or the SPI link to U2.
//
// This class is Core 1's only door into the host stack, the same shape
// Ch375SourceAdapter is Core 1's only door into a CH375 channel: begin()
// once, task() every pass, take_event() drained in a loop until it returns
// false. TinyUSB's own host callbacks - tinyusb_host_callbacks.cpp - run
// inside that task() call and copy bounded data into storage this class
// owns; nothing downstream ever sees a pointer into memory a callback still
// owns.
//
// This task's callbacks are empty and take_event() always returns false:
// the host stack comes up, runs and can be asked for events, but nothing yet
// arms a device's reports or fills the storage take_event() would drain.
// That storage, the callbacks that fill it and take_event()'s real answer
// arrive in tasks 6 through 8 of this migration.

#include <cstdint>

#include "input/source.hpp"

namespace duo_input::u1::pio_usb {

class PioUsbBackend {
public:
    /// Bring the host stack up. Call once, from Core 1, before task().
    ///
    /// Sets the RP2040 system clock to 120 MHz - Pico-PIO-USB's bit timing
    /// requires it and TinyUSB's device side on RHPort 0 does not care, since
    /// it runs off the chip's dedicated USB PLL rather than clk_sys - then
    /// configures Pico-PIO-USB's D+ pin as GP0 (D- is the adjacent GP1;
    /// Pico-PIO-USB derives it from D+ itself) and calls tuh_init on RHPort
    /// 1.
    void begin();

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
};

}  // namespace duo_input::u1::pio_usb
