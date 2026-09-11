#include "flash_park.hpp"

// FlashPark is inline so its Core-1 half can be used from an SRAM-only loop
// without introducing an accidental call back into XIP flash.
