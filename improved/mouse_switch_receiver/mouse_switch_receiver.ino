#include <Wire.h>
#include <Mouse.h>

#include "mouse_switch_protocol.h"

constexpr uint8_t kI2cAddress = 0x02;
constexpr unsigned long kLinkTimeoutMs = 100;
constexpr bool kDebugEnabled = false;

volatile uint8_t pendingBytes[kPacketSize];
volatile bool packetPending = false;

unsigned long lastValidPacketMs = 0;
bool leftPressed = false;
bool rightPressed = false;

void receiveEvent(int byteCount) {
  if (byteCount != kPacketSize) {
    while (Wire.available()) {
      Wire.read();
    }
    return;
  }

  uint8_t bytesRead = 0;
  while (bytesRead < kPacketSize && Wire.available()) {
    const int nextByte = Wire.read();
    if (nextByte < 0) {
      break;
    }
    pendingBytes[bytesRead++] = static_cast<uint8_t>(nextByte);
  }

  if (bytesRead == kPacketSize) {
    packetPending = true;
  }
}

void setButtonStates(bool wantLeft, bool wantRight) {
  if (wantLeft != leftPressed) {
    if (wantLeft) {
      Mouse.press(MOUSE_LEFT);
    } else {
      Mouse.release(MOUSE_LEFT);
    }
    leftPressed = wantLeft;
  }

  if (wantRight != rightPressed) {
    if (wantRight) {
      Mouse.press(MOUSE_RIGHT);
    } else {
      Mouse.release(MOUSE_RIGHT);
    }
    rightPressed = wantRight;
  }
}

void setup() {
  Mouse.begin();
  Wire.begin(kI2cAddress);
  Wire.onReceive(receiveEvent);
}

void loop() {
  uint8_t packetBytes[kPacketSize];
  bool havePendingPacket = false;

  noInterrupts();
  if (packetPending) {
    for (uint8_t i = 0; i < kPacketSize; ++i) {
      packetBytes[i] = pendingBytes[i];
    }
    packetPending = false;
    havePendingPacket = true;
  }
  interrupts();

  if (havePendingPacket) {
    const MousePacket packet = {
        packetBytes[0], packetBytes[1], static_cast<int8_t>(packetBytes[2]),
        static_cast<int8_t>(packetBytes[3]), static_cast<int8_t>(packetBytes[4]),
        packetBytes[5], packetBytes[6]};

    if (packetIsValid(packet)) {
      lastValidPacketMs = millis();

      if (packet.type == kReportPacketType) {
        Mouse.move(packet.dx, packet.dy, packet.wheel);
        setButtonStates((packet.buttons & 0x01) != 0,
                        (packet.buttons & 0x02) != 0);
      } else {
        setButtonStates(false, false);
      }
    }
  }

  if (millis() - lastValidPacketMs > kLinkTimeoutMs) {
    setButtonStates(false, false);
  }
}
