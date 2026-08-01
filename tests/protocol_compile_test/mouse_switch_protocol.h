#ifndef MOUSE_SWITCH_PROTOCOL_H
#define MOUSE_SWITCH_PROTOCOL_H

#include <stdint.h>

constexpr uint8_t kReportPacketType = 0xA1;
constexpr uint8_t kReleasePacketType = 0xA0;
constexpr uint8_t kAllowedButtonMask = 0x03;
constexpr uint8_t kPacketSize = 7;

#pragma pack(push, 1)
struct MousePacket {
  uint8_t type;
  uint8_t sequence;
  int8_t dx;
  int8_t dy;
  int8_t wheel;
  uint8_t buttons;
  uint8_t checksum;
};
#pragma pack(pop)

static_assert(sizeof(MousePacket) == kPacketSize, "MousePacket must be seven bytes");

constexpr uint8_t checksumBytes(uint8_t b0, uint8_t b1, uint8_t b2,
                                uint8_t b3, uint8_t b4, uint8_t b5) {
  return static_cast<uint8_t>(b0 ^ b1 ^ b2 ^ b3 ^ b4 ^ b5);
}

inline uint8_t packetChecksum(const MousePacket& packet) {
  return checksumBytes(packet.type, packet.sequence,
                       static_cast<uint8_t>(packet.dx),
                       static_cast<uint8_t>(packet.dy),
                       static_cast<uint8_t>(packet.wheel), packet.buttons);
}

inline bool packetIsValid(const MousePacket& packet) {
  if (packet.type != kReportPacketType && packet.type != kReleasePacketType) {
    return false;
  }

  if ((packet.buttons & static_cast<uint8_t>(~kAllowedButtonMask)) != 0) {
    return false;
  }

  if (packet.type == kReleasePacketType &&
      (packet.dx != 0 || packet.dy != 0 || packet.wheel != 0 || packet.buttons != 0)) {
    return false;
  }

  return packet.checksum == packetChecksum(packet);
}

inline MousePacket makeReportPacket(uint8_t sequence, int8_t dx, int8_t dy,
                                    int8_t wheel, uint8_t buttons) {
  MousePacket packet = {kReportPacketType, sequence, dx, dy, wheel,
                        static_cast<uint8_t>(buttons & kAllowedButtonMask), 0};
  packet.checksum = packetChecksum(packet);
  return packet;
}

inline MousePacket makeReleasePacket(uint8_t sequence) {
  MousePacket packet = {kReleasePacketType, sequence, 0, 0, 0, 0, 0};
  packet.checksum = packetChecksum(packet);
  return packet;
}

constexpr bool isMiddlePressEdge(bool currentPressed, bool previousPressed) {
  return currentPressed && !previousPressed;
}

#endif
