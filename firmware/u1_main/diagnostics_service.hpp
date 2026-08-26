#pragma once

#include "diagnostics/counters.hpp"

namespace duo_input::u1 {

/// Read why the device restarted, and count it if the watchdog did it.
///
/// Call once, early: later code cannot distinguish a watchdog reset from a
/// power cycle once the hardware flags have been cleared.
diagnostics::ResetRecord read_reset_record();

/// Restart into the ROM bootloader, so a new image can be written.
void reboot_into_bootloader();

}  // namespace duo_input::u1
