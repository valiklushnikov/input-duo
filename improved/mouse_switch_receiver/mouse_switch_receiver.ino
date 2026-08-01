#include <Wire.h>
#include <Mouse.h>

#include "mouse_switch_packet_queue.h"
#include "mouse_switch_protocol.h"

constexpr uint8_t kI2cAddress = 0x02;
constexpr unsigned long kLinkTimeoutMs = 100;
constexpr bool kDebugEnabled = false;

volatile uint8_t packetQueue[kPacketQueueCapacity][kPacketSize];
volatile uint8_t packetQueueHead = 0;
volatile uint8_t packetQueueTail = 0;
volatile uint8_t packetQueueCount = 0;

unsigned long lastValidPacketMs = 0;
bool leftPressed = false;
bool rightPressed = false;

PacketQueueState currentPacketQueueState() {
  return PacketQueueState{
      packetQueueHead, packetQueueTail, packetQueueCount};
}

void drainWireInput() {
  while (Wire.available()) {
    Wire.read();
  }
}

void receiveEvent(int byteCount) {
  if (!packetLengthIsValid(static_cast<size_t>(byteCount))) {
    drainWireInput();
    return;
  }

  const PacketQueueState state = currentPacketQueueState();
  if (!packetQueueCanEnqueue(state)) {
    drainWireInput();
    return;
  }

  const uint8_t enqueueSlot = packetQueueEnqueueSlot(state);
  uint8_t bytesRead = 0;
  while (bytesRead < kPacketSize && Wire.available()) {
    const int nextByte = Wire.read();
    if (nextByte < 0) {
      break;
    }
    packetQueue[enqueueSlot][bytesRead++] = static_cast<uint8_t>(nextByte);
  }

  if (bytesRead == kPacketSize) {
    const PacketQueueState nextState = packetQueueAfterEnqueue(state);
    packetQueueTail = nextState.tail;
    packetQueueCount = nextState.count;
  } else {
    drainWireInput();
  }
}

void requestEvent() {
  const PacketQueueState state = currentPacketQueueState();
  Wire.write(packetQueueFreeSlots(state));
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
  Wire.onRequest(requestEvent);
}

void loop() {
  uint8_t packetBytes[kPacketSize];
  bool havePendingPacket = false;

  noInterrupts();
  const PacketQueueState state = currentPacketQueueState();
  if (packetQueueCanDequeue(state)) {
    const uint8_t dequeueSlot = packetQueueDequeueSlot(state);
    for (uint8_t i = 0; i < kPacketSize; ++i) {
      packetBytes[i] = packetQueue[dequeueSlot][i];
    }
    const PacketQueueState nextState = packetQueueAfterDequeue(state);
    packetQueueHead = nextState.head;
    packetQueueCount = nextState.count;
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
        setButtonStates((packet.buttons & 0x01) != 0,
                        (packet.buttons & 0x02) != 0);
        Mouse.move(packet.dx, packet.dy, packet.wheel);
      } else {
        setButtonStates(false, false);
      }
    }
  }

  if (millis() - lastValidPacketMs > kLinkTimeoutMs) {
    setButtonStates(false, false);
  }
}
