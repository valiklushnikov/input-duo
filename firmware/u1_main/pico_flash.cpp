#include "pico_flash.hpp"

#include <cstring>

#include "hardware/flash.h"
#include "hardware/sync.h"
#include "pico/multicore.h"

#if defined(DUO_INPUT_BACKEND_PIO_USB) || defined(DUO_INPUT_BACKEND_PIO_USB_REFERENCE)
#include "pio_usb.h"
#endif

#include "flash_park.hpp"
#include "storage/flash_layout.hpp"

namespace duo_input::u1 {
namespace {

/// Where the flash appears in the address space.
const std::uint8_t* const kXipBase = reinterpret_cast<const std::uint8_t*>(XIP_BASE);

/// True immediately before Core 0 launches the second core.
///
/// It used to be a build flag, which was right while the only thing on Core 1
/// was an opt-in test pattern. Core 1 now carries the whole input runtime in
/// every build, so the answer is a fact about this run rather than about how
/// it was compiled - and the flag being off while a core was executing was an
/// erase running against a chip somebody else was fetching code from. Core 0
/// owns both this publication and launch, closing the startup check/use race.
std::atomic<bool> g_core1_running{false};
FlashPark g_flash_park;

}  // namespace

void set_core1_running(bool running) { g_core1_running.store(running, std::memory_order_release); }

bool core1_running() { return g_core1_running.load(std::memory_order_acquire); }

bool begin_core1_flash_window() {
    if (!core1_running()) {
        return true;
    }
    if (!g_flash_park.request()) {
        return false;
    }
    __sev();
    while (!g_flash_park.parked()) {
        tight_loop_contents();
    }
    return true;
}

void end_core1_flash_window() {
    // The park state, rather than a second read of g_core1_running, is the
    // operation token. This also makes a false->true publication between
    // begin/end harmless instead of creating an unacknowledgeable release.
    if (!g_flash_park.release()) {
        return;
    }
    __sev();
    while (!g_flash_park.idle()) {
        tight_loop_contents();
    }
}

bool core1_flash_window_requested() { return g_flash_park.requested(); }

void __no_inline_not_in_flash_func(service_core1_flash_window)() {
    const std::uint32_t interrupts = save_and_disable_interrupts();
    if (!g_flash_park.begin_park()) {
        restore_interrupts(interrupts);
        return;
    }

    while (!g_flash_park.release_requested()) {
#if defined(DUO_INPUT_BACKEND_PIO_USB) || defined(DUO_INPUT_BACKEND_PIO_USB_REFERENCE)
        pio_usb_host_flash_keepalive();
#else
        __asm volatile("nop");
#endif
    }
    restore_interrupts(interrupts);
}

void finish_core1_flash_window() {
    g_flash_park.finish_park();
    __sev();
}

bool PicoFlash::erase(std::uint32_t offset, std::size_t size) {
    if (offset % storage::kSectorSize != 0 || size % storage::kSectorSize != 0) {
        return false;
    }
    if (offset < storage::kConfigAOffset || offset + size > storage::kFlashSize) {
        // Refusing to erase the firmware is not a nicety: the code performing
        // the erase is the code being erased.
        return false;
    }

    if (!begin_core1_flash_window()) {
        return false;
    }
    const std::uint32_t interrupts = save_and_disable_interrupts();
    flash_range_erase(offset, size);
    restore_interrupts(interrupts);
    end_core1_flash_window();
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

    if (!begin_core1_flash_window()) {
        return false;
    }
    const std::uint32_t interrupts = save_and_disable_interrupts();
    flash_range_program(offset, data, size);
    restore_interrupts(interrupts);
    end_core1_flash_window();
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
