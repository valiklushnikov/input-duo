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

/// What begin() found and what the raw root port is doing, below TinyUSB.
///
/// Every counter the registry keeps starts one level too high to answer the
/// question that cost a whole bench session: a host that never initialised and
/// a host that initialised and saw nothing produce the same twelve zeros, and
/// so does a Core 1 that stopped inside begin(). None of these fields go
/// through the registry, the callbacks or TinyUSB's device model. They are
/// read straight off the host stack's own flags and Pico-PIO-USB's root port,
/// which is the only layer that still says something when everything above it
/// is silent.
///
/// All fixed width, all copied by value into the CDC reply on Core 0.
struct HostObservability {
    /// The four kHostInit* bits below.
    std::uint8_t init_flags = 0;
    /// clk_sys as begin() found it, BEFORE set_sys_clock_khz(120000).
    ///
    /// NOT the clock the PIO dividers were computed from, and nothing may read
    /// it as one. begin() raises the clock first and brings the host up
    /// afterwards, so on a correct image this is 125 MHz (the RP2040 default
    /// main() never changes) while the dividers are computed at 120 MHz. The
    /// two differing is what a healthy board looks like.
    ///
    /// It is also the same 125 MHz on the broken image, where the host was
    /// already up before Core 1 ran, so the pair has no discriminating power
    /// of its own. What it says is narrower and still worth having: Core 1
    /// reached set_sys_clock_khz and the clock moved.
    std::uint32_t clk_hz_at_begin = 0;
    /// clk_sys on Core 0, sampled once per main-loop pass.
    ///
    /// THIS is the divider clock when kHostInitHostAlreadyActive is clear:
    /// Pico-PIO-USB computes every divider once inside pio_usb_host_init and
    /// never recomputes one, and begin() calls tuh_init after its own clock
    /// change - an ordering
    /// tests/firmware_native/test_pio_usb_device_registry.cpp guards rather
    /// than leaves to this comment. When that bit is SET the host came up
    /// somewhere else, on a clock this reply never saw, and no clock reading
    /// here is evidence about the dividers.
    std::uint32_t clk_hz_now = 0;
    /// pio_usb_host_get_frame_number(): the free-running SOF count.
    ///
    /// Below TinyUSB and below the registry. Read twice a second apart it
    /// says whether U1 is driving the bus at all, and at what rate. Sampled
    /// on Core 0 rather than cached by a Core 1 pass, deliberately: a frozen
    /// core1_passes beside a climbing frame count is a Core 1 that died under
    /// a host that did not.
    std::uint32_t sof_frame_count = 0;
    /// The four kRootPort* bits below, sampled on Core 0.
    std::uint8_t root_port_state = 0;
    /// How many times the root port has gone from disconnected to connected.
    ///
    /// Whether U1 ever saw the hub's D+ pull-up, independently of whether any
    /// transaction on it ever succeeded. Saturates rather than wrapping: a
    /// count that rolled over to zero would read as "never attached".
    ///
    /// A LOWER BOUND, not a total. Nothing below TinyUSB reports an attach
    /// edge, so task() polls the level once a pass; an attach and detach that
    /// both fall between two passes is not counted. Zero here is therefore
    /// strong evidence that nothing attached and not proof of it.
    std::uint16_t root_port_connects = 0;
    /// Passes of Core 1's loop. This project's established proof that Core 1
    /// stopped is this counter unchanged across two reads twenty seconds
    /// apart, so it must be readable that way and must not saturate low.
    std::uint32_t core1_passes = 0;
};

/// tuh_rhport_is_active(1) as begin() found it, before it changed anything.
///
/// The smoking gun. Set means something initialised the host stack before
/// Core 1 reached begin() - which is what usb_service.cpp's argument-less
/// tusb_init() used to do on Core 0, at the wrong clock, before
/// tuh_configure(). It must read clear on a correct image.
inline constexpr std::uint8_t kHostInitHostAlreadyActive = 1u << 0;
/// What tuh_configure() returned.
inline constexpr std::uint8_t kHostInitConfigured = 1u << 1;
/// What tuh_init() returned. True on its own proves nothing: tuh_init on an
/// already-active rhport returns true without doing anything, which is why
/// kHostInitHostAlreadyActive is read first and reported beside it.
inline constexpr std::uint8_t kHostInitInitialized = 1u << 2;
/// tuh_inited() after both calls.
inline constexpr std::uint8_t kHostInitInited = 1u << 3;

/// PIO_USB_ROOT_PORT(0)->initialized.
inline constexpr std::uint8_t kRootPortInitialized = 1u << 0;
/// PIO_USB_ROOT_PORT(0)->connected.
inline constexpr std::uint8_t kRootPortConnected = 1u << 1;
/// PIO_USB_ROOT_PORT(0)->suspended.
inline constexpr std::uint8_t kRootPortSuspended = 1u << 2;
/// PIO_USB_ROOT_PORT(0)->is_fullspeed.
inline constexpr std::uint8_t kRootPortFullSpeed = 1u << 3;

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

    /// Read-only view of what the registry knows, for diagnostics.
    ///
    /// Const on purpose: the only caller is Core 0's main loop building a
    /// GET_DIAGNOSTICS reply, and it must be able to read what is mounted
    /// without being able to arm, fault or forget anything. The translation
    /// into the wire's PeripheralPort shape stays in main.cpp, exactly where
    /// the CH375 path already does it - the registry does not know what a CDC
    /// frame is and the CDC service does not know what a TinyUSB interface is.
    const DeviceRegistry& registry() const { return registry_; }

    /// What the host stack and the raw root port are doing right now.
    ///
    /// Called from Core 0's main loop, once a pass, for the same reason
    /// registry() is read there: the reply must be able to read this core's
    /// state without being able to change any of it. The result is handed to
    /// ConfigService and held until a GET_DIAGNOSTICS arrives, so a reading
    /// can be up to one Core 0 pass old - which is far below the second the
    /// "read it twice" procedures need, and does not affect either of them.
    ///
    /// The live halves (clk_sys, the SOF count, the root port's own flags) are
    /// sampled HERE, on Core 0, rather than cached by Core 1's task(), so they
    /// still answer when Core 1 has stopped - which is precisely the case they
    /// exist to tell apart.
    ///
    /// Bounded, non-allocating and non-blocking: four volatile reads and a
    /// register read. It takes no lock, so a root-port flag can change under
    /// it; nothing here is a decision, only a reading.
    HostObservability observe() const;

private:
    ClockChangeBarrier clock_change_;
    DeviceRegistry registry_;
    bool host_ready_ = false;

    // Written once by begin() on Core 1 and read by Core 0 afterwards, the
    // same discipline the registry's own counters already use: plain fixed
    // width words, published before clock_change_.publish_settled().
    std::uint8_t host_init_flags_ = 0;
    std::uint32_t clk_hz_at_begin_ = 0;

    // The attach edge has to be polled - nothing below TinyUSB reports one -
    // so task() samples it every pass. Both are Core 1's alone; Core 0 only
    // ever reads the counter.
    std::uint16_t root_port_connects_ = 0;
    bool root_port_was_connected_ = false;

    // Incremented once per task() call, and task() is called exactly once per
    // pass of core1_entry's loop, unconditionally - ahead of the host_ready_
    // early return, so a backend that refused to start still proves the core
    // itself is turning.
    std::uint32_t core1_passes_ = 0;
};

}  // namespace duo_input::u1::pio_usb
