#include "mouse_switch_protocol.h"

static_assert(kPacketSize == 7, "Unexpected packet size");
static_assert(checksumBytes(0xA1, 0x7F, 0x80, 0x7F, 0xFF, 0x03) == 0xDD,
              "Checksum contract changed");
static_assert(isMiddlePressEdge(true, false), "Press edge must switch");
static_assert(!isMiddlePressEdge(true, true), "Held button must not switch again");
static_assert(!isMiddlePressEdge(false, true), "Release must not switch");

void setup() {}

void loop() {}
