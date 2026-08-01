#ifndef MOUSE_SWITCH_PS2_LOGIC_H
#define MOUSE_SWITCH_PS2_LOGIC_H

#include <stdint.h>

constexpr int16_t decodePs2Axis(uint8_t raw, bool signSet) {
  return signSet ? static_cast<int16_t>(raw) - 256
                 : static_cast<int16_t>(raw);
}

constexpr int8_t hidDisplacementChunk(int16_t displacement) {
  return displacement > 127
             ? 127
             : (displacement < -127 ? -127
                                    : static_cast<int8_t>(displacement));
}

constexpr int16_t negatePs2Axis(int16_t value) {
  return static_cast<int16_t>(-value);
}

constexpr int8_t decodePs2Wheel(uint8_t deviceId, uint8_t raw) {
  return deviceId == 0x04
             ? ((raw & 0x08) != 0
                    ? static_cast<int8_t>((raw & 0x0F) - 16)
                    : static_cast<int8_t>(raw & 0x0F))
             : static_cast<int8_t>(raw);
}

constexpr bool shouldAttemptExplorerUpgrade(uint8_t deviceId) {
  return deviceId == 0x03;
}

constexpr bool shouldReconnectAfterPs2Failure(uint8_t consecutiveFailures,
                                              uint8_t failureLimit) {
  return failureLimit != 0 && consecutiveFailures >= failureLimit;
}

#endif
