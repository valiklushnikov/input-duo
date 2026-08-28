#include "pico_flash.hpp"

#include <cstring>

#include "hardware/flash.h"
#include "hardware/sync.h"
#include "pico/multicore.h"

#include "storage/flash_layout.hpp"

namespace duo_input::u1 {
namespace {

/// Where the flash appears in the address space.
const std::uint8_t* const kXipBase = reinterpret_cast<const std::uint8_t*>(XIP_BASE);

/// True once the second core is running and has armed itself to be stopped.
///
/// It used to be a build flag, which was right while the only thing on Core 1
/// was an opt-in test pattern. Core 1 now carries the whole input runtime in
/// every build, so the answer is a fact about this run rather than about how
/// it was compiled - and the flag being off while a core was executing was an
/// erase running against a chip somebody else was fetching code from.
volatile bool g_core1_running = false;

bool core1_is_running() { return g_core1_running; }

}  // namespace

void set_core1_running(bool running) { g_core1_running = running; }

bool PicoFlash::erase(std::uint32_t offset, std::size_t size) {
    if (offset % storage::kSectorSize != 0 || size % storage::kSectorSize != 0) {
        return false;
    }
    if (offset < storage::kConfigAOffset || offset + size > storage::kFlashSize) {
        // Refusing to erase the firmware is not a nicety: the code performing
        // the erase is the code being erased.
        return false;
    }

    if (core1_is_running()) {
        multicore_lockout_start_blocking();
    }
    const std::uint32_t interrupts = save_and_disable_interrupts();
    flash_range_erase(offset, size);
    restore_interrupts(interrupts);
    if (core1_is_running()) {
        multicore_lockout_end_blocking();
    }
    return true;
}

bool PicoFlash::program(std::uint32_t offset, const std::uint8_t* data, std::size_t size) {
    if (offset % storage::kPageSize != 0 || size % storage::kPageSize != 0) {
        // The A/B store writes whole pages; anything else would mean rewriting
        // a page that already holds data, which flash cannot do.
        return false;
    }
    if (offset < storage::kConfigAOffset || offset + size > storage::kFlashSize) {
        return false;
    }

    if (core1_is_running()) {
        multicore_lockout_start_blocking();
    }
    const std::uint32_t interrupts = save_and_disable_interrupts();
    flash_range_program(offset, data, size);
    restore_interrupts(interrupts);
    if (core1_is_running()) {
        multicore_lockout_end_blocking();
    }
    return true;
}

bool PicoFlash::read(std::uint32_t offset, std::uint8_t* data, std::size_t size) const {
    if (offset + size > storage::kFlashSize) {
        return false;
    }
    std::memcpy(data, kXipBase + offset, size);
    return true;
}

const std::uint8_t* PicoFlash::direct(std::uint32_t offset) const {
    if (offset >= storage::kFlashSize) {
        return nullptr;
    }
    return kXipBase + offset;
}

}  // namespace duo_input::u1
