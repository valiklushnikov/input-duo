// Why this device last restarted, and how often that has happened.
//
// The reset reason is read once, at startup, before anything else can obscure
// it. The watchdog count lives in a scratch register rather than in flash:
// those registers survive a reset but not a power cycle, which is exactly the
// lifetime the number is meaningful over, and writing it to flash would wear
// the part out recording events in order to report them.

#include "diagnostics_service.hpp"

#include "hardware/watchdog.h"
#include "pico/bootrom.h"

namespace duo_input::u1 {
namespace {

/// Scratch register 7 is not used by the SDK's own bootloader handshake.
constexpr unsigned kWatchdogCountScratch = 7;

/// Marks the count as ours rather than whatever the register held at power-on.
constexpr std::uint32_t kCountTag = 0xD10C0000;
constexpr std::uint32_t kCountMask = 0x0000FFFF;

}  // namespace

diagnostics::ResetRecord read_reset_record() {
    diagnostics::ResetRecord record;

    const std::uint32_t stored = watchdog_hw->scratch[kWatchdogCountScratch];
    const bool tagged = (stored & ~kCountMask) == kCountTag;
    record.watchdog_count = tagged ? (stored & kCountMask) : 0;

    if (watchdog_caused_reboot()) {
        record.reason = diagnostics::ResetReason::Watchdog;
        // Saturating: a device resetting sixty-five thousand times has a
        // problem the exact number will not help anyone diagnose.
        if (record.watchdog_count < kCountMask) {
            ++record.watchdog_count;
        }
    } else if (tagged) {
        // The register survived, so this was a reset rather than power being
        // applied - a debugger, or a request to reboot.
        record.reason = diagnostics::ResetReason::Requested;
    } else {
        record.reason = diagnostics::ResetReason::PowerOn;
    }

    watchdog_hw->scratch[kWatchdogCountScratch] = kCountTag | record.watchdog_count;
    return record;
}

void reboot_into_bootloader() {
    reset_usb_boot(0, 0);
}

}  // namespace duo_input::u1
