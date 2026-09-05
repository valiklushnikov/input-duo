// Core 1's stack pointer, for the one diagnostic that needs it.
//
// This is __get_MSP() and nothing else. It is NOT taken from CMSIS: the pinned
// SDK's cmsis_gcc_m.h declares four objects of a locally-defined type at file
// scope, which is a hard error in C++ without -fpermissive, so including that
// header from this tree's .cpp files does not compile. One MRS is not worth
// relaxing a warning flag across the image.
//
// A separate translation unit rather than an inline, so the native test build
// links its own definition from tests/firmware_native/fakes/tinyusb_host.cpp
// and every other file - including the caller - is byte for byte the file that
// ships. The cost is one call frame: the value reported is a handful of bytes
// LOWER than the caller's own stack pointer, which is the conservative
// direction for a field that exists to find an overflow.
//
// pico-sdk never selects the process stack pointer, on either core, so MSP is
// the stack pointer in both thread mode and the SOF alarm's handler mode -
// which is why one reading covers both contexts the caller samples from.

#include <cstdint>

extern "C" std::uint32_t duo_core1_stack_pointer(void) {
    std::uint32_t stack_pointer = 0;
    __asm__ volatile("mrs %0, msp" : "=r"(stack_pointer));
    return stack_pointer;
}
